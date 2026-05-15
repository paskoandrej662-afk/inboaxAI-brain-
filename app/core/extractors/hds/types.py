"""Dataclassy pre HDS-Lite engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Seed:
    """Sample produkt identifikovany Sonnet vision (Phase 1)."""

    name: str
    price: str  # "160€" alebo "dohodou" alebo "180€/Den"


@dataclass
class ProductCard:
    """Vysledok HDS extrakcie pre jeden produkt."""

    name: str
    price_eur: Optional[float] = None
    price_text: Optional[str] = None
    attributes: dict = field(default_factory=dict)
    confidence: float = 0.0
    lca_element: Any = None  # BS4 element reference (debug only)
    source_html: str = ""  # pre arbitration


@dataclass
class ExtractionResult:
    """Final HDS output pre jednu stranku."""

    success: bool
    cards: list[ProductCard] = field(default_factory=list)
    seeds_found: int = 0
    lcas_found: int = 0
    candidate_count: int = 0
    after_visibility: int = 0
    after_confidence: int = 0
    arbitration_called: int = 0
    sonnet_cost_usd: float = 0.0
    fallback_reason: Optional[str] = None
