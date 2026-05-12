"""Arq worker pre Phase 2 (Universal Ingestion Engine v2).

POZOR: tento worker je PRIPRAVENY ale NESPUSTANY — Railway worker service stale
behi na `app.workers.ingest_worker.IngestWorker` (Phase 1). Tento subor je tu pre
buducu aktivaciu (staci prepnut Railway worker command na `IngestV2Worker`).
"""
from __future__ import annotations

import logging
import uuid

from arq.connections import RedisSettings

from app.config import settings
from app.core.ingest_v2.orchestrator import ingest_company_v2

logger = logging.getLogger(__name__)


async def ingest_v2_task(
    ctx: dict,
    job_id: str,
    company_id: str,
    source_url: str,
    mode: str = "standard",
    budget_eur: float = 1.20,
) -> dict:
    """Arq task wrapper okolo Phase 2 orchestratora. ZATIAL NEAKTIVNY v Railway workeri."""
    logger.info("ingest_v2_task: job_id=%s url=%s mode=%s", job_id, source_url, mode)
    return await ingest_company_v2(
        job_id=uuid.UUID(job_id),
        company_id=uuid.UUID(company_id),
        source_url=source_url,
        mode=mode,
        budget_eur=budget_eur,
    )


async def startup(ctx: dict) -> None:
    logger.info("ingest_v2 worker: startup")


async def shutdown(ctx: dict) -> None:
    logger.info("ingest_v2 worker: shutdown")


class IngestV2Worker:
    """Arq Settings trieda pre Phase 2 worker. ZATIAL NEAKTIVNA — Railway worker
    service stale spusta `ingest_worker.IngestWorker`. Tu pre buducu aktivaciu."""

    functions = [ingest_v2_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    job_timeout = 600  # 10 min
    max_jobs = 2
    keep_result = 3600
    on_startup = startup
    on_shutdown = shutdown
