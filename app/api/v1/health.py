from fastapi import APIRouter

from app.config import settings
from app.db import ping

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "inboxai-brain",
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/db")
async def health_db() -> dict[str, str]:
    try:
        ok = await ping()
    except Exception as exc:
        return {"status": "error", "database": "disconnected", "detail": str(exc)}
    if not ok:
        return {"status": "error", "database": "disconnected"}
    return {"status": "ok", "database": "connected"}
