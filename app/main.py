import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import health, ingest
from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.arq_redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        logger.info("arq redis pool initialized")
    except Exception as exc:
        logger.exception("failed to initialize arq redis pool: %s", exc)
        app.state.arq_redis = None
    try:
        yield
    finally:
        if app.state.arq_redis is not None:
            await app.state.arq_redis.close()
            logger.info("arq redis pool closed")


app = FastAPI(title="InboxAI Brain", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(ingest.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "InboxAI Brain", "version": "0.1.0"}
