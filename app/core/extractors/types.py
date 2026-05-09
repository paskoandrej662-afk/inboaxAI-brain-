from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PAGE_TYPES = (
    "product_listing",
    "product_detail",
    "service_pricing",
    "faq",
    "contact",
    "about",
    "home",
    "gallery",
    "blog_post",
    "other_low_value",
)


@dataclass
class ExtractedProduct:
    name: str
    description: str | None = None
    price_text: str | None = None
    price_eur: float | None = None
    price_unit: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    image_url: str | None = None
    source_url: str = ""
    source_block_text: str = ""
    source_type: str = "vision"
    confidence: float = 0.5
    verified: bool = False


@dataclass
class ExtractedBusinessFact:
    key: str
    value: str
    source_url: str = ""
    source_type: str = "vision"
    confidence: float = 0.5


@dataclass
class ExtractedFaqItem:
    question: str
    answer: str
    source_url: str = ""
    source_type: str = "vision"
    confidence: float = 0.5


@dataclass
class ExtractedImage:
    url: str
    alt_text: str | None = None
    description: str | None = None
    width: int | None = None
    height: int | None = None
    near_product_name: str | None = None
    source_url: str = ""


@dataclass
class PageExtraction:
    url: str
    page_type: str
    value_score: float
    products: list[ExtractedProduct] = field(default_factory=list)
    business_facts: list[ExtractedBusinessFact] = field(default_factory=list)
    faqs: list[ExtractedFaqItem] = field(default_factory=list)
    images: list[ExtractedImage] = field(default_factory=list)
    page_summary: str = ""
    raw_text: str = ""
    errors: list[str] = field(default_factory=list)
    llm_cost_usd: float = 0.0


@dataclass
class IngestRunResult:
    pages_visited: int = 0
    pages_succeeded: int = 0
    pages_failed: int = 0
    products_extracted: int = 0
    facts_extracted: int = 0
    faqs_extracted: int = 0
    images_described: int = 0
    chunks_created: int = 0
    total_llm_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    extraction_run_id: str | None = None
