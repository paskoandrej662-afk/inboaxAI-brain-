from __future__ import annotations

import asyncio
import logging

from openai import APIError, AsyncOpenAI, RateLimitError

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
MAX_BATCH = 100
MAX_RETRIES = 3


async def embed_batch(texts: list[str], client: AsyncOpenAI) -> list[list[float]]:
    """Embed up to MAX_BATCH texts per call. Returns one vector per input, in order."""
    if not texts:
        return []

    out: list[list[float]] = []
    for start in range(0, len(texts), MAX_BATCH):
        batch = texts[start : start + MAX_BATCH]
        # Defensive: replace empty strings (the API rejects them)
        cleaned = [t if t.strip() else "(empty)" for t in batch]

        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=cleaned,
                )
                vectors = [item.embedding for item in resp.data]
                if len(vectors) != len(cleaned):
                    raise RuntimeError(
                        f"embedding count mismatch: got {len(vectors)} for {len(cleaned)} inputs"
                    )
                out.extend(vectors)
                last_exc = None
                break
            except RateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "embeddings: rate limit (attempt %s/%s): %s",
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                )
                await asyncio.sleep(delay)
                delay *= 2
            except APIError as exc:
                last_exc = exc
                logger.warning(
                    "embeddings: API error (attempt %s/%s): %s",
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                )
                await asyncio.sleep(delay)
                delay *= 2
        if last_exc is not None:
            raise last_exc

    return out


async def embed_single(text: str, client: AsyncOpenAI) -> list[float]:
    vectors = await embed_batch([text], client)
    return vectors[0]
