"""Gemini Flash + Google Search Grounding client.

Uses official google-genai SDK with grounding tool enabled.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from google import genai
from google.genai import types as genai_types

from app.core.extractors.hds_v3.batcher import Batcher
from app.core.extractors.hds_v3.prompts import (
    BATCH_EXTRACTION_SYSTEM,
    build_batch_prompt,
)
from app.core.extractors.hds_v3.types import (
    DiscoveredPage,
    GeminiBatchResult,
    GeminiExtractionResult,
)

logger = logging.getLogger(__name__)


class GeminiClient:
    """Client for Gemini Flash + Google Search Grounding.

    Pricing (Gemini 2.5 Flash, May 2026):
      - Input:  $0.30 per 1M tokens
      - Output: $2.50 per 1M tokens
      - Grounding: free up to 1500 queries/day, then $14 per 1k
    """

    MODEL_NAME = "gemini-2.5-flash"
    MAX_CONCURRENT_BATCHES = 5
    MAX_RETRIES = 3
    RETRY_BACKOFF_SEC = [1, 3, 9]
    BATCH_TIMEOUT_SEC = 60

    INPUT_TOKEN_PRICE_PER_1M = 0.30
    OUTPUT_TOKEN_PRICE_PER_1M = 2.50

    def __init__(self, api_key: Optional[str] = None):
        """Initialize. api_key from ENV GEMINI_API_KEY if not provided."""
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not set (in env or constructor)")
        self.client = genai.Client(api_key=key)
        self.semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_BATCHES)

    async def extract_pages(
        self,
        base_url: str,
        pages: list[DiscoveredPage],
    ) -> GeminiExtractionResult:
        """Main entry point. Batches pages and calls Gemini in parallel."""
        start = time.time()
        result = GeminiExtractionResult(success=False, base_url=base_url)

        if not pages:
            result.error = "no_pages_to_extract"
            return result

        batcher = Batcher()
        batches = batcher.make_batches(pages)
        result.total_batches = len(batches)

        tasks = [self._call_batch(batch) for batch in batches]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for br in batch_results:
            if isinstance(br, Exception):
                logger.error("Batch failed with exception: %s", br)
                result.failed_batches += 1
                continue
            result.batches.append(br)
            if br.success:
                result.successful_batches += 1
            else:
                result.failed_batches += 1
            result.total_cost_usd += br.cost_usd
            result.total_input_tokens += br.input_tokens
            result.total_output_tokens += br.output_tokens

        result.success = result.successful_batches > 0
        result.total_duration_sec = time.time() - start

        if not result.success:
            result.error = "all_batches_failed"

        logger.info(
            "Gemini extraction for %s: batches=%d ok=%d failed=%d cost=$%.4f dur=%.1fs",
            base_url,
            result.total_batches,
            result.successful_batches,
            result.failed_batches,
            result.total_cost_usd,
            result.total_duration_sec,
        )
        return result

    async def _call_batch(self, batch: list[DiscoveredPage]) -> GeminiBatchResult:
        """Call Gemini API for a single batch. Retry on failure."""
        async with self.semaphore:
            urls = [p.url for p in batch]
            result = GeminiBatchResult(success=False, urls=urls)

            try:
                user_prompt = build_batch_prompt(urls)
            except ValueError as e:
                result.error = f"prompt_build_error: {e}"
                return result

            start = time.time()
            for attempt in range(self.MAX_RETRIES):
                result.retry_count = attempt
                start = time.time()

                try:
                    response = await asyncio.wait_for(
                        self._gemini_call(user_prompt),
                        timeout=self.BATCH_TIMEOUT_SEC,
                    )

                    text = getattr(response, "text", None) or ""
                    if not text.strip():
                        raise ValueError("Gemini returned empty response")

                    result.markdown = text
                    result.success = True
                    result.error = None

                    um = getattr(response, "usage_metadata", None)
                    if um is not None:
                        result.input_tokens = (
                            getattr(um, "prompt_token_count", 0) or 0
                        )
                        result.output_tokens = (
                            getattr(um, "candidates_token_count", 0) or 0
                        )
                        result.cost_usd = (
                            result.input_tokens
                            * self.INPUT_TOKEN_PRICE_PER_1M
                            / 1_000_000
                            + result.output_tokens
                            * self.OUTPUT_TOKEN_PRICE_PER_1M
                            / 1_000_000
                        )

                    result.duration_sec = time.time() - start
                    return result

                except asyncio.TimeoutError:
                    logger.warning(
                        "Batch timeout (attempt %d): %s", attempt + 1, urls
                    )
                    result.error = f"timeout_attempt_{attempt + 1}"
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "Batch error (attempt %d): %s - %s",
                        attempt + 1,
                        urls,
                        e,
                    )
                    result.error = (
                        f"error_attempt_{attempt + 1}: {str(e)[:200]}"
                    )

                if attempt < self.MAX_RETRIES - 1:
                    backoff = self.RETRY_BACKOFF_SEC[
                        min(attempt, len(self.RETRY_BACKOFF_SEC) - 1)
                    ]
                    await asyncio.sleep(backoff)

            result.duration_sec = time.time() - start
            return result

    async def _gemini_call(self, user_prompt: str):
        """Make the actual Gemini API call with grounding enabled."""
        return await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.MODEL_NAME,
            contents=user_prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=BATCH_EXTRACTION_SYSTEM,
                tools=[
                    genai_types.Tool(
                        google_search=genai_types.GoogleSearch()
                    )
                ],
                temperature=0.1,
                max_output_tokens=8192,
            ),
        )
