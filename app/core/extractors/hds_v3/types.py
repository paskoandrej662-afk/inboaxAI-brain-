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
