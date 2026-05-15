"""End-to-end test: Crawler -> Batcher -> Gemini.

Costs real Gemini calls: ~$0.10-0.30 per run.

Usage:
    PYTHONPATH=. python3 scripts/test_hds_v3_full.py https://skakaciehradyorava.sk/
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(url: str):
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY required")
        sys.exit(1)

    from app.core.extractors.hds_v3.crawler import HDSCrawler
    from app.core.extractors.hds_v3.gemini_client import GeminiClient

    print("=== STEP 1: Crawler ===")
    crawler = HDSCrawler()
    crawl = await crawler.discover(url)
    print(f"Found {crawl.total_discovered} pages in {crawl.duration_sec:.1f}s")
    for p in crawl.pages[:5]:
        print(f"  [{p.priority.name}] {p.url}")
    if crawl.total_discovered > 5:
        print(f"  ... +{crawl.total_discovered - 5} more")

    print("\n=== STEP 2: Gemini Extraction ===")
    client = GeminiClient()
    extract = await client.extract_pages(url, crawl.pages)

    print(f"Batches: {extract.total_batches}")
    print(f"OK: {extract.successful_batches}")
    print(f"Failed: {extract.failed_batches}")
    print(f"Cost: ${extract.total_cost_usd:.4f}")
    print(f"Duration: {extract.total_duration_sec:.1f}s")
    print(
        f"Tokens in/out: {extract.total_input_tokens} / {extract.total_output_tokens}"
    )

    safe_name = url.replace("://", "_").replace("/", "_")
    out_path = f"/tmp/hds_v3_output_{safe_name}.md"
    with open(out_path, "w") as f:
        for i, batch in enumerate(extract.batches):
            f.write(f"\n\n=== BATCH {i + 1} (URLs: {batch.urls}) ===\n\n")
            f.write(batch.markdown)

    print(f"\nFull markdown saved: {out_path}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://skakaciehradyorava.sk/"
    asyncio.run(main(url))
