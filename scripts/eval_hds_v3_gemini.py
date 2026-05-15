"""Smoke eval: real Gemini API call on a single batch.

Cost: ~$0.02 per run. Skipped if GEMINI_API_KEY not set.

Run manually BEFORE pushing changes to gemini_client/prompts/schema:
    PYTHONPATH=. python3 scripts/eval_hds_v3_gemini.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("GEMINI_API_KEY"):
    print("WARN  GEMINI_API_KEY not set - skipping eval")
    sys.exit(0)


async def main():
    from app.core.extractors.hds_v3.gemini_client import GeminiClient
    from app.core.extractors.hds_v3.types import DiscoveredPage, PagePriority

    test_pages = [
        DiscoveredPage(
            url="https://skakaciehradyorava.sk/",
            priority=PagePriority.TIER_1_CRITICAL,
            discovered_via="test",
        ),
        DiscoveredPage(
            url="https://skakaciehradyorava.sk/najcastejsie-otazky/",
            priority=PagePriority.TIER_2_IMPORTANT,
            discovered_via="test",
        ),
        DiscoveredPage(
            url="https://skakaciehradyorava.sk/galeria/",
            priority=PagePriority.TIER_3_USEFUL,
            discovered_via="test",
        ),
    ]

    client = GeminiClient()
    result = await client.extract_pages(
        "https://skakaciehradyorava.sk/", test_pages
    )

    print("\n=== EVAL RESULT ===")
    print(f"Success: {result.success}")
    print(f"Total batches: {result.total_batches}")
    print(f"Successful: {result.successful_batches}")
    print(f"Failed: {result.failed_batches}")
    print(f"Cost: ${result.total_cost_usd:.4f}")
    print(f"Duration: {result.total_duration_sec:.1f}s")
    print(f"Total tokens in: {result.total_input_tokens}")
    print(f"Total tokens out: {result.total_output_tokens}")

    if result.batches:
        print("\n=== First batch markdown (first 2000 chars) ===")
        print(result.batches[0].markdown[:2000])
        print("...")

    all_text = "\n".join(b.markdown for b in result.batches)
    contains_product = any(
        term.lower() in all_text.lower()
        for term in ["Tiger", "Rozpravkovo", "Aladin", "Disney", "hrad"]
    )

    if result.success and contains_product:
        print("\nPASS - Gemini returned markdown mentioning expected products")
        sys.exit(0)
    else:
        print("\nFAIL - Gemini did not return expected content")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
