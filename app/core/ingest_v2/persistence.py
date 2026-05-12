"""Persistence vrstva pre Universal Ingestion Engine v2 (Phase 2A).

Async SQLAlchemy zapisy do 4 tabuliek: ingestion_jobs, company_pages,
raw_page_blocks, ingestion_costs. Konvencia: company_id ako uuid bez DB FK
(zhodne s brain_* tabulkami a `app/models/ingest_v2.py`).

Vsetky funkcie volaju `await session.flush()`; commit() necha na volajuceho
(orchestrator drzi vlastny per-page session a commituje sam).
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ingest_v2.block_detection import DetectedBlock
from app.core.ingest_v2.crawler import DiscoveredPage
from app.core.ingest_v2.renderer import RenderResult
from app.core.ingest_v2.types import RawPageData
from app.models.ingest_v2 import CompanyPage, IngestionCost, IngestionJob, RawPageBlock

logger = logging.getLogger(__name__)


def _compute_content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _normalize_url(url: str) -> str:
    """Jednoducha normalizacia URL pre unique key (crawler uz dodava normalizovane URL)."""
    if not url:
        return ""
    return url.rstrip("/").lower()


def _as_dict(obj) -> dict:
    """Pydantic v2 model -> dict; fallback na dict(obj)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    try:
        return dict(obj)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# ingestion_jobs
# ---------------------------------------------------------------------------
async def create_job(
    session: AsyncSession,
    company_id: uuid.UUID,
    source_url: str,
    mode: str = "standard",
    budget_eur: float = 1.20,
) -> IngestionJob:
    """Vytvori novy ingestion job (status=queued)."""
    job = IngestionJob(
        company_id=company_id,
        source_url=source_url,
        mode=mode,
        budget_eur=budget_eur,
        status="queued",
        progress=0,
    )
    session.add(job)
    await session.flush()
    return job


async def update_job_status(
    session: AsyncSession,
    job_id: uuid.UUID,
    status: str,
    progress: int | None = None,
) -> None:
    """Aktualizuje status jobu + volitelne progress."""
    job = await session.get(IngestionJob, job_id)
    if not job:
        return
    job.status = status
    if progress is not None:
        job.progress = progress
    if status == "running" and job.started_at is None:
        job.started_at = datetime.now(timezone.utc)
    if status in ("completed", "partial", "failed"):
        job.ended_at = datetime.now(timezone.utc)
    await session.flush()


async def finalize_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    status: str,
    pages_visited: int,
    pages_succeeded: int,
    pages_failed: int,
    blocks_found: int,
    cost_total_eur: float,
    result_summary: dict | None = None,
    errors: list | None = None,
    warnings: list | None = None,
) -> None:
    """Oznaci job ako dokonceny s finalnymi statistikami."""
    job = await session.get(IngestionJob, job_id)
    if not job:
        return
    job.status = status
    job.progress = 100
    job.pages_visited = pages_visited
    job.pages_succeeded = pages_succeeded
    job.pages_failed = pages_failed
    job.blocks_found = blocks_found
    job.cost_total_eur = cost_total_eur
    job.result_summary = result_summary or {}
    if errors:
        job.errors = errors
    if warnings:
        job.warnings = warnings
    job.ended_at = datetime.now(timezone.utc)
    await session.flush()


# ---------------------------------------------------------------------------
# company_pages
# ---------------------------------------------------------------------------
async def save_page(
    session: AsyncSession,
    job_id: uuid.UUID,
    company_id: uuid.UUID,
    discovered: DiscoveredPage,
    render: RenderResult,
    raw_data: RawPageData,
) -> CompanyPage:
    """Ulozi jednu stranku (audit trail aj pre neuspesny render)."""
    url_norm = _normalize_url(discovered.url)
    html = render.html or ""
    content_hash = _compute_content_hash(html)

    page = CompanyPage(
        company_id=company_id,
        job_id=job_id,
        url=discovered.url,
        url_normalized=url_norm,
        final_url=render.final_url,
        title=(render.title or "")[:500],
        http_status=render.http_status,
        render_status=render.render_status,
        render_method="playwright_headless",
        render_ms=render.render_ms,
        retry_count=0,
        error_message=render.error_message,
        discovery_method=discovered.discovery_method,
        priority_score=discovered.priority_score,
        depth=discovered.depth,
        parent_url=discovered.parent_url,
        # raw HTML — orezane, aby sme nezahltili DB; neskor moze ist do object storage
        html=html[:200_000] if html else "",
        content_hash=content_hash,
        visible_text=(render.visible_text or "")[:100_000],
        dom_size=render.dom_size,
        text_length=render.text_length,
        raw_data=_as_dict(raw_data),
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(page)
    await session.flush()
    return page


# ---------------------------------------------------------------------------
# raw_page_blocks
# ---------------------------------------------------------------------------
async def save_blocks(
    session: AsyncSession,
    job_id: uuid.UUID,
    company_id: uuid.UUID,
    page_id: uuid.UUID,
    source_url: str,
    blocks: list[DetectedBlock],
) -> int:
    """Bulk insert blokov. Vrati pocet vlozenych riadkov."""
    if not blocks:
        return 0
    rows: list[RawPageBlock] = []
    for b in blocks:
        rows.append(RawPageBlock(
            job_id=job_id,
            company_id=company_id,
            page_id=page_id,
            source_url=source_url,
            block_type="candidate",
            block_type_hint=b.block_type_hint,
            selector=b.selector[:500] if b.selector else None,
            dom_path=b.selector[:500] if b.selector else None,
            parent_selector=b.parent_selector[:500] if b.parent_selector else None,
            section_heading=b.section_heading,
            text=(b.text or "")[:10_000],
            html=(b.html_snippet or "")[:5_000],
            text_hash=_compute_content_hash(b.text or ""),
            headings=b.headings,
            images=b.images,
            links=b.links,
            signals=_as_dict(b.signals),
            position_index=b.position_index,
            depth=b.depth,
            extraction_method="heuristic_block",
            confidence=b.confidence,
            status="raw",
        ))
    session.add_all(rows)
    await session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# ingestion_costs
# ---------------------------------------------------------------------------
async def save_cost(
    session: AsyncSession,
    job_id: uuid.UUID,
    operation: str,
    model: str | None = None,
    duration_ms: int = 0,
    bytes_in: int | None = None,
    bytes_out: int | None = None,
    est_cost_eur: float = 0.0,
    hard_limit_hit: bool = False,
) -> None:
    """Zaznamena jeden naklad / operaciu jobu."""
    row = IngestionCost(
        job_id=job_id,
        operation=operation,
        model=model,
        duration_ms=duration_ms,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
        est_cost_eur=est_cost_eur,
        hard_limit_hit=hard_limit_hit,
    )
    session.add(row)
    await session.flush()
