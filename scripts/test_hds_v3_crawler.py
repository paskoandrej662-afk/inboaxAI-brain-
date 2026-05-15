"""Manual run: discover pages of a website pomocou HDSCrawler.

Usage:
    PYTHONPATH=. python3 scripts/test_hds_v3_crawler.py https://skakaciehradyorava.sk/
"""
from __future__ import annotations

import asyncio
import sys

from app.core.extractors.hds_v3.crawler import HDSCrawler


async def main(url: str) -> None:
    crawler = HDSCrawler()
    result = await crawler.discover(url)

    print(f"\n=== CRAWL RESULT pre {url} ===")
    print(f"Success: {result.success}")
    print(f"Sitemap found: {result.sitemap_found}")
    print(f"Total discovered: {result.total_discovered}")
    print(f"Duration: {result.duration_sec:.2f}s")

    if result.error:
        print(f"Error: {result.error}")

    print(f"\n--- Pages ({len(result.pages)}) ---")
    for i, page in enumerate(result.pages, 1):
        print(f"  {i:2d}. [{page.priority.name}] {page.url}")
        print(f"       via: {page.discovered_via}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://skakaciehradyorava.sk/"
    asyncio.run(main(target))
