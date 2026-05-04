from __future__ import annotations

import logging
import uuid

from arq.connections import RedisSettings

from app.config import settings
from app.core.knowledge_hub import ingest_url

logger = logging.getLogger(__name__)


async def ingest_task(ctx: dict, company_id: str, url: str, job_id: str, max_pages: int = 30) -> dict:
    logger.info("worker: starting ingest job_id=%s url=%s", job_id, url)
    result = await ingest_url(uuid.UUID(company_id), url, job_id, max_pages=max_pages)
    logger.info(
        "worker: finished ingest job_id=%s chunks=%s facts=%s faqs=%s",
        job_id,
        result.chunks_inserted,
        result.facts_inserted,
        result.faqs_inserted,
    )
    return result.to_dict()


async def startup(ctx: dict) -> None:
    logger.info("ingest worker: startup")


async def shutdown(ctx: dict) -> None:
    logger.info("ingest worker: shutdown")


class IngestWorker:
    functions = [ingest_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 2
    job_timeout = 600
    keep_result = 3600
    on_startup = startup
    on_shutdown = shutdown
