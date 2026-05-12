"""FastAPI router pre Universal Ingestion Engine v2 (Phase 2A).

Endpointy:
  POST /v2/ingest-company           — spusti novy ingest job (beh v BackgroundTasks)
  GET  /v2/jobs/{job_id}            — stav jobu
  GET  /v2/jobs/{job_id}/raw-summary — agregovane pocty (debug)
  GET  /v2/companies/{company_id}/pages — zoznam strankok firmy

POZN: orchestrator sa v 2A-3 vola synchronne cez FastAPI BackgroundTasks
(ziaden Redis queue / Arq enqueue) — pre testovanie pred plnym deployom.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.ingest_v2 import persistence as p
from app.core.ingest_v2.orchestrator import ingest_company_v2
from app.db import AsyncSessionLocal
from app.models.ingest_v2 import CompanyPage, IngestionJob, RawPageBlock

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2", tags=["ingest_v2"])


class IngestRequest(BaseModel):
    url: str
    company_id: uuid.UUID
    mode: str = Field(default="standard", pattern="^(standard|quick|deep)$")
    budget_eur: float = Field(default=1.20, ge=0.05, le=10.0)


class IngestResponse(BaseModel):
    job_id: str
    status: str


async def _run_ingest_background(
    job_id: uuid.UUID, company_id: uuid.UUID, url: str, mode: str, budget_eur: float
) -> None:
    """Wrapper pre background task — zachyti vsetky vynimky a oznaci job ako failed."""
    try:
        await ingest_company_v2(job_id, company_id, url, mode, budget_eur)
    except Exception as e:
        logger.exception("background ingest_v2 crashed: %s", e)
        try:
            async with AsyncSessionLocal() as session:
                await p.finalize_job(
                    session, job_id, "failed",
                    pages_visited=0, pages_succeeded=0, pages_failed=0,
                    blocks_found=0, cost_total_eur=0.0,
                    errors=[{"stage": "orchestrator_crash", "error": str(e)[:500]}],
                )
                await session.commit()
        except Exception:
            pass


@router.post("/ingest-company", response_model=IngestResponse, status_code=202)
async def ingest_company(req: IngestRequest, background_tasks: BackgroundTasks) -> IngestResponse:
    """Spusti novy ingest job. Vrati job_id okamzite, beh prebehne v pozadi."""
    async with AsyncSessionLocal() as session:
        job = await p.create_job(session, req.company_id, req.url, req.mode, req.budget_eur)
        await session.commit()
        job_id = job.id

    background_tasks.add_task(
        _run_ingest_background, job_id, req.company_id, req.url, req.mode, req.budget_eur
    )
    return IngestResponse(job_id=str(job_id), status="queued")


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as session:
        job = await session.get(IngestionJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            "id": str(job.id),
            "company_id": str(job.company_id),
            "source_url": job.source_url,
            "mode": job.mode,
            "status": job.status,
            "progress": job.progress,
            "budget_eur": float(job.budget_eur),
            "cost_total_eur": float(job.cost_total_eur),
            "pages_visited": job.pages_visited,
            "pages_succeeded": job.pages_succeeded,
            "pages_failed": job.pages_failed,
            "blocks_found": job.blocks_found,
            "errors": job.errors,
            "warnings": job.warnings,
            "result_summary": job.result_summary,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "ended_at": job.ended_at.isoformat() if job.ended_at else None,
        }


@router.get("/companies/{company_id}/pages")
async def list_pages(company_id: uuid.UUID, limit: int = 50) -> dict:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CompanyPage)
            .where(CompanyPage.company_id == company_id)
            .order_by(CompanyPage.priority_score.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        pages = result.scalars().all()
        return {
            "pages": [
                {
                    "id": str(pg.id),
                    "url": pg.url,
                    "title": pg.title,
                    "render_status": pg.render_status,
                    "render_ms": pg.render_ms,
                    "priority_score": float(pg.priority_score),
                    "discovery_method": pg.discovery_method,
                    "text_length": pg.text_length,
                    "dom_size": pg.dom_size,
                    "fetched_at": pg.fetched_at.isoformat() if pg.fetched_at else None,
                }
                for pg in pages
            ]
        }


@router.get("/jobs/{job_id}/raw-summary")
async def raw_summary(job_id: uuid.UUID) -> dict:
    """Agregovane pocty pre debugovanie jobu."""
    async with AsyncSessionLocal() as session:
        pages_count = (
            await session.execute(
                select(func.count()).select_from(CompanyPage).where(CompanyPage.job_id == job_id)
            )
        ).scalar() or 0
        blocks_count = (
            await session.execute(
                select(func.count()).select_from(RawPageBlock).where(RawPageBlock.job_id == job_id)
            )
        ).scalar() or 0
        hist_rows = (
            await session.execute(
                select(RawPageBlock.block_type_hint, func.count())
                .where(RawPageBlock.job_id == job_id)
                .group_by(RawPageBlock.block_type_hint)
            )
        ).all()
        hist = {row[0] or "unknown": row[1] for row in hist_rows}

        pages = (
            await session.execute(select(CompanyPage).where(CompanyPage.job_id == job_id))
        ).scalars().all()
        all_images = 0
        all_links = 0
        all_emails: set[str] = set()
        all_phones: set[str] = set()
        all_pdfs: set[str] = set()
        json_ld_count = 0
        for pg in pages:
            rd = pg.raw_data or {}
            all_images += len(rd.get("images", []))
            all_links += len(rd.get("links", []))
            cp = rd.get("contact_patterns", {}) or {}
            for e in cp.get("emails", []):
                all_emails.add(e)
            for ph in cp.get("phones", []):
                all_phones.add(ph)
            for pdf in rd.get("pdfs", []):
                all_pdfs.add(pdf)
            json_ld_count += len(rd.get("json_ld", []))

        return {
            "pages": pages_count,
            "blocks": blocks_count,
            "block_type_histogram": hist,
            "images": all_images,
            "links": all_links,
            "emails": sorted(all_emails),
            "phones": sorted(all_phones),
            "pdfs": sorted(all_pdfs),
            "json_ld_count": json_ld_count,
        }
