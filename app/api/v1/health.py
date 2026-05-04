from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "inboxai-brain",
        "environment": settings.ENVIRONMENT,
    }
