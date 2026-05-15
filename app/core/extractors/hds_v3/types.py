"""Typy pre HDS-v3 crawler — DiscoveredPage, CrawlResult, PagePriority."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PagePriority(Enum):
    """Priority tiers pre objavene URL.

    TIER_0: MUST-HAVE (/kontakt) — vzdy zahrnuta, neorezava sa cap-om
    TIER_1: kriticke podstranky (/, /produkty, /sluzby, /cennik)
    TIER_2: dolezite (/o-nas, /faq)
    TIER_3: uzitocne (/galeria, /projekty, /referencie, /blog)
    TIER_4: zvysok
    """

    TIER_0_ESSENTIAL = 0
    TIER_1_CRITICAL = 1
    TIER_2_IMPORTANT = 2
    TIER_3_USEFUL = 3
    TIER_4_OTHER = 4


@dataclass
class DiscoveredPage:
    """Jedna podstranka objavena crawlerom."""

    url: str
    priority: PagePriority
    discovered_via: str  # "sitemap" | "homepage_link" | "manual_seed"


@dataclass
class CrawlResult:
    """Vystup `HDSCrawler.discover()`."""

    success: bool
    base_url: str
    pages: list[DiscoveredPage] = field(default_factory=list)
    total_discovered: int = 0
    sitemap_found: bool = False
    error: Optional[str] = None
    duration_sec: float = 0.0


@dataclass
class GeminiBatchResult:
    """Output of single Gemini API call for 1 batch (1-3 URLs).

    `markdown` is raw response — NOT parsed yet. Parser comes in Commit 3.
    """

    success: bool
    urls: list[str]
    markdown: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    error: Optional[str] = None
    retry_count: int = 0


@dataclass
class HdsExtractedFact:
    """Generic fact extracted by HDS-v3 parser.

    Distinct from app.core.extractors.types.ExtractedBusinessFact (key+value) —
    this carries `type` discriminator + free-text `content` + optional `meta`
    dict, matching the shape of the markdown sections produced by Gemini.

    Engine.py maps this to ExtractedBusinessFact when persisting via knowledge_hub.
    """

    type: str  # "contact" | "address" | "info" | "social" | "geo"
    content: str
    source_url: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class HdsFAQ:
    """FAQ item extracted by HDS-v3 parser."""

    question: str
    answer: str
    source_url: Optional[str] = None


@dataclass
class PageCrawlResult:
    """Per-page output from `HDSCrawler.crawl_media_streams`.

    `media_stream` is a linear list of images + text nodes captured from
    the rendered page (see `app.core.extractors.hds_v3.image_extractor`).
    Empty when rendering failed.
    """

    url: str
    media_stream: list = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class GeminiExtractionResult:
    """Aggregated output of all batches for a single web ingest.

    This is what Commit 3 (Parser) will consume.
    """

    success: bool
    base_url: str
    batches: list[GeminiBatchResult] = field(default_factory=list)
    total_batches: int = 0
    successful_batches: int = 0
    failed_batches: int = 0
    total_cost_usd: float = 0.0
    total_duration_sec: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: Optional[str] = None
