"""SQLAlchemy ORM modely pre Phase 2A — Universal Ingestion Engine v2 (raw layer).

Zodpoveda alembic migracii 6eb8936e7f1a (phase2a raw layer).
Konvencia (zhodna so vsetkymi brain_* tabulkami): company_id uuid NOT NULL, BEZ FK.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestionJob(Base):
    """Jeden riadok = jedno spustenie ingestu pre danu spolocnost."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, server_default=sa_text("'standard'"), nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default=sa_text("'queued'"), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    budget_eur: Mapped[float] = mapped_column(Numeric(10, 4), server_default=sa_text("1.20"), nullable=False)
    cost_total_eur: Mapped[float] = mapped_column(Numeric(10, 6), server_default=sa_text("0"), nullable=False)
    pages_visited: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    pages_succeeded: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    pages_failed: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    blocks_found: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    errors: Mapped[list] = mapped_column(JSONB, server_default=sa_text("'[]'::jsonb"), nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, server_default=sa_text("'[]'::jsonb"), nullable=False)
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("mode IN ('standard', 'deep', 'quick')", name="ck_ingestion_jobs_mode"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed')",
            name="ck_ingestion_jobs_status",
        ),
        Index(
            "ix_ingestion_jobs_company_status_created",
            "company_id",
            "status",
            sa_text("created_at DESC"),
        ),
        Index(
            "ix_ingestion_jobs_active",
            "status",
            "created_at",
            postgresql_where=sa_text("status IN ('queued', 'running')"),
        ),
    )


class CompanyPage(Base):
    """Kazda navstivena/renderovana stranka v ramci ingestion jobu (Layer A)."""

    __tablename__ = "company_pages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    render_status: Mapped[str] = mapped_column(Text, server_default=sa_text("'pending'"), nullable=False)
    render_method: Mapped[str] = mapped_column(
        Text, server_default=sa_text("'playwright_headless'"), nullable=False
    )
    render_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_method: Mapped[str] = mapped_column(Text, server_default=sa_text("'bfs'"), nullable=False)
    priority_score: Mapped[float] = mapped_column(Numeric(3, 2), server_default=sa_text("0.50"), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    parent_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # raw HTML; v Phase 2A nechavame v DB, neskor moze ist do object storage
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # sha256 of html for change detection
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    visible_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dom_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSONB, server_default=sa_text("'{}'::jsonb"), nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "render_status IN ('pending', 'success', 'timeout', 'blocked', 'error', 'skipped')",
            name="ck_company_pages_render_status",
        ),
        CheckConstraint(
            "render_method IN ('playwright_headless', 'httpx', 'sitemap_only')",
            name="ck_company_pages_render_method",
        ),
        CheckConstraint(
            "discovery_method IN ('sitemap', 'homepage_link', 'bfs', 'rendered_link', 'seed', 'robots')",
            name="ck_company_pages_discovery_method",
        ),
        Index(
            "uq_company_pages_company_url_normalized",
            "company_id",
            "url_normalized",
            unique=True,
        ),
        Index("ix_company_pages_job_id", "job_id"),
        Index("ix_company_pages_company_render_status", "company_id", "render_status"),
        Index("ix_company_pages_company_priority", "company_id", sa_text("priority_score DESC")),
    )


class RawPageBlock(Base):
    """Heuristicky detegovany blok-kandidat na stranke. Phase 2B ho klasifikuje."""

    __tablename__ = "raw_page_blocks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # logical FK to company_pages.id (bez DB FK, brain konvencia)
    page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    block_type: Mapped[str] = mapped_column(Text, server_default=sa_text("'candidate'"), nullable=False)
    block_type_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    dom_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    section_heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # sha256 of text for dedup
    text_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    headings: Mapped[list] = mapped_column(JSONB, server_default=sa_text("'[]'::jsonb"), nullable=False)
    images: Mapped[list] = mapped_column(JSONB, server_default=sa_text("'[]'::jsonb"), nullable=False)
    links: Mapped[list] = mapped_column(JSONB, server_default=sa_text("'[]'::jsonb"), nullable=False)
    signals: Mapped[dict] = mapped_column(JSONB, server_default=sa_text("'{}'::jsonb"), nullable=False)
    position_index: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    extraction_method: Mapped[str] = mapped_column(
        Text, server_default=sa_text("'heuristic_block'"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), server_default=sa_text("0.50"), nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default=sa_text("'raw'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_raw_page_blocks_page_id", "page_id"),
        Index("ix_raw_page_blocks_job_id", "job_id"),
        Index("ix_raw_page_blocks_company_type_hint", "company_id", "block_type_hint"),
    )


class IngestionCost(Base):
    """Audit nakladov ingestion jobu — render, vision call, image describe, ... v EUR + tokeny."""

    __tablename__ = "ingestion_costs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_cost_eur: Mapped[float] = mapped_column(Numeric(10, 6), server_default=sa_text("0"), nullable=False)
    hard_limit_hit: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ingestion_costs_job_created", "job_id", "created_at"),
    )
