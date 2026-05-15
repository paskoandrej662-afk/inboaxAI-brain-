"""HDS-v3 — Hybrid Deterministic Scraper v3.

Nova pipeline: Gemini Flash + Google Search Grounding namiesto Sonnet vision.
Tento balik obsahuje crawler (najde podstranky) a parser (markdown -> products).

Commit 1: crawler — najde podstranky webu z URL.
"""

from app.core.extractors.hds_v3.types import (
    CrawlResult,
    DiscoveredPage,
    GeminiBatchResult,
    GeminiExtractionResult,
    PagePriority,
)

__all__ = [
    "CrawlResult",
    "DiscoveredPage",
    "GeminiBatchResult",
    "GeminiExtractionResult",
    "PagePriority",
]
