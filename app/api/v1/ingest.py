from __future__ import annotations

import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

router = APIRouter(prefix="/v1", tags=["ingest"])


class IngestRequest(BaseModel):
    url: str = Field(..., description="Start URL to ingest")
    max_pages: int = Field(default=30, ge=1, le=200)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        parsed = urlparse(v.strip())
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")
        if not parsed.netloc:
            raise ValueError("URL must include a hostname")
        return v.strip()


class IngestResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    id: str
    status: str
    progress: int
    result: dict | None = None
    error: str | None = None


@router.post(
    "/companies/{company_id}/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_ingest(
    company_id: str,
    body: IngestRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    try:
        cid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="company_id must be a valid UUID")

    job_id = str(uuid.uuid4())

    await db.execute(
        sa_text(
            """
            INSERT INTO brain_jobs (id, company_id, type, status, progress)
            VALUES (:id, :cid, 'web_ingest', 'queued', 0)
            """
        ),
        {"id": job_id, "cid": str(cid)},
    )
    await db.commit()

    redis = getattr(request.app.state, "arq_redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="job queue not initialized")

    await redis.enqueue_job(
        "ingest_task",
        str(cid),
        body.url,
        job_id,
        body.max_pages,
        _job_id=f"ingest:{job_id}",
    )

    return IngestResponse(job_id=job_id, status="queued")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobStatusResponse:
    row = (
        await db.execute(
            sa_text(
                "SELECT id, status, progress, result, error FROM brain_jobs WHERE id = :id"
            ),
            {"id": job_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(
        id=row[0],
        status=row[1],
        progress=row[2],
        result=row[3],
        error=row[4],
    )
