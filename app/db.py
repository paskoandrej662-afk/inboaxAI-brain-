import logging
from collections.abc import AsyncIterator

import redis.asyncio as redis_async
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


async def ping() -> bool:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        return result.scalar() == 1


_redis_client: redis_async.Redis | None = None


async def get_redis_client() -> redis_async.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_async.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def close_redis_client() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def cache_get(key: str) -> str | None:
    try:
        client = await get_redis_client()
        return await client.get(key)
    except Exception as exc:
        logger.warning("cache_get failed for %s: %s", key, exc)
        return None


async def cache_set(key: str, value: str, ttl_seconds: int = 300) -> None:
    try:
        client = await get_redis_client()
        await client.set(key, value, ex=ttl_seconds)
    except Exception as exc:
        logger.warning("cache_set failed for %s: %s", key, exc)


async def cache_delete(key: str) -> None:
    try:
        client = await get_redis_client()
        await client.delete(key)
    except Exception as exc:
        logger.warning("cache_delete failed for %s: %s", key, exc)
