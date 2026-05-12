"""Pydantic v2 typy pre Universal Ingestion Engine v2 (Phase 2A).

Tieto typy su zdielany kontrakt medzi crawler-om, block detektorom, budget managerom
a (neskor) Phase 2B klasifikatorom. Ziaden z nich nesmie zavisiet od DB modelov.
"""
from __future__ import annotations

import uuid
from datetime import datetime  # noqa: F401  (vyhradene pre buduce typy)
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumy
# ---------------------------------------------------------------------------
class IngestMode(str, Enum):
    STANDARD = "standard"
    DEEP = "deep"
    QUICK = "quick"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RenderStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    ERROR = "error"
    SKIPPED = "skipped"


class DiscoveryMethod(str, Enum):
    SITEMAP = "sitemap"
    HOMEPAGE_LINK = "homepage_link"
    BFS = "bfs"
    RENDERED_LINK = "rendered_link"
    SEED = "seed"
    ROBOTS = "robots"


class SourceType(str, Enum):
    DOM = "dom"
    JSONLD = "jsonld"
    MICRODATA = "microdata"
    META = "meta"
    OGTAG = "ogtag"
    REGEX = "regex"
    HEURISTIC_BLOCK = "heuristic_block"
    LLM = "llm"
    INFERENCE = "inference"


class BlockTypeHint(str, Enum):
    CANDIDATE_CARD = "candidate_card"
    SECTION_CANDIDATE = "section_candidate"
    REPEATED_CARD_CANDIDATE = "repeated_card_candidate"
    TABLE_CANDIDATE = "table_candidate"
    FAQ_CANDIDATE = "faq_candidate"
    CONTACT_CANDIDATE = "contact_candidate"
    GALLERY_CANDIDATE = "gallery_candidate"
    ARTICLE_CANDIDATE = "article_candidate"
    PRICING_CANDIDATE = "pricing_candidate"
    FOOTER_CANDIDATE = "footer_candidate"
    HEADER_NAV_CANDIDATE = "header_nav_candidate"
    HERO_CANDIDATE = "hero_candidate"
    ABOUT_CANDIDATE = "about_candidate"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Pydantic modely
# ---------------------------------------------------------------------------
class EvidenceRecord(BaseModel):
    """Univerzalny obal nad dokazom. Kazdy extrahovany fakt musi jeden niest."""

    source_url: str
    source_type: SourceType
    selector: Optional[str] = None
    evidence_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    extraction_method: str
    page_id: Optional[uuid.UUID] = None
    block_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(use_enum_values=True)


class BlockSignals(BaseModel):
    """Signaly nazbierane pri detekcii blokov. Riadi Phase 2B klasifikaciu."""

    has_price: bool = False
    price_count: int = 0
    price_patterns: list[str] = Field(default_factory=list)
    has_image: bool = False
    image_count: int = 0
    has_cta: bool = False
    cta_texts: list[str] = Field(default_factory=list)
    has_contact: bool = False
    has_date: bool = False
    has_question: bool = False
    repeated_structure_count: int = 0
    section_heading: Optional[str] = None
    class_tokens: list[str] = Field(default_factory=list)
    link_count: int = 0
    text_length: int = 0


class ImageCandidate(BaseModel):
    """Obrazok najdeny na stranke — pred stiahnutim/dedup (to riesi Phase 2C)."""

    src: str
    resolved_url: str
    srcset: list[str] = Field(default_factory=list)
    alt: Optional[str] = None
    title: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    selector: Optional[str] = None
    nearby_text: Optional[str] = None
    section_heading: Optional[str] = None
    parent_link: Optional[str] = None
    is_lazy: bool = False
    source_attr: str = "src"  # 'src','srcset','data-src','data-lazy-src','background-image'
    candidate_role: Literal["product", "gallery", "logo", "icon", "background", "unknown"] = "unknown"


class ContactPatterns(BaseModel):
    """Regex-detegovane kontaktne udaje z viditelneho textu stranky."""

    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    ico: list[str] = Field(default_factory=list)
    dic: list[str] = Field(default_factory=list)
    ic_dph: list[str] = Field(default_factory=list)
    iban: list[str] = Field(default_factory=list)
    social_links: list[str] = Field(default_factory=list)
    map_links: list[str] = Field(default_factory=list)
    addresses_candidates: list[str] = Field(default_factory=list)


class HeadingItem(BaseModel):
    level: int = Field(ge=1, le=6)
    text: str
    selector: Optional[str] = None


class LinkItem(BaseModel):
    href: str
    text: str
    internal: bool


class RawPageData(BaseModel):
    """Strukturovana surova extrakcia (Layer A). Ide do company_pages.raw_data JSONB."""

    headings: list[HeadingItem] = Field(default_factory=list)
    links: list[LinkItem] = Field(default_factory=list)
    images: list[ImageCandidate] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)  # zoznam 2D tabuliek
    lists: list[list[str]] = Field(default_factory=list)
    forms: list[dict] = Field(default_factory=list)
    json_ld: list[dict] = Field(default_factory=list)
    microdata: list[dict] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)
    open_graph: dict[str, str] = Field(default_factory=dict)
    pdfs: list[str] = Field(default_factory=list)
    social_links: list[str] = Field(default_factory=list)
    contact_patterns: ContactPatterns = Field(default_factory=ContactPatterns)


class BudgetLimits(BaseModel):
    """Resource + EUR limity pre jeden ingestion job."""

    max_pages: int = 12
    max_depth: int = 2
    max_runtime_seconds: int = 180
    max_render_ms_total: int = 120000
    max_html_bytes_total: int = 15_000_000
    max_images_candidates: int = 300
    max_blocks: int = 1000
    target_eur: float = 0.70
    soft_limit_eur: float = 1.00
    hard_limit_eur: float = 1.20


class BudgetStatus(BaseModel):
    """Aktualny stav budgetu pocas jobu."""

    spent_eur: float = 0.0
    pages_used: int = 0
    blocks_used: int = 0
    images_used: int = 0
    render_ms_used: int = 0
    html_bytes_used: int = 0
    runtime_seconds: float = 0.0
    soft_limit_hit: bool = False
    hard_limit_hit: bool = False
    page_limit_hit: bool = False
    runtime_limit_hit: bool = False
    operations_count: int = 0
