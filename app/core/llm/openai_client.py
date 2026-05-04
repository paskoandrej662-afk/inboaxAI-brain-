from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.config import settings
from app.core.embeddings import embed_batch

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def embed(text: str) -> list[float]:
    """Embed a single query string for retrieval."""
    vectors = await embed_batch([text or " "], get_client())
    return vectors[0]
