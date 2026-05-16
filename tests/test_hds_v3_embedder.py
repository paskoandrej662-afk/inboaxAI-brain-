"""Tests for HDS-v3 Embedder — offline, all Gemini calls mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.extractors.hds_v3.embedder import Embedder


def _fake_embed_response(vectors: list[list[float]]):
    response = MagicMock()
    response.embeddings = [MagicMock(values=v) for v in vectors]
    return response


def test_embedder_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Embedder()


@pytest.mark.asyncio
async def test_embed_text_calls_gemini():
    embedder = Embedder(api_key="test-key")
    embedder._call_embed = AsyncMock(return_value=[[0.1] * 768])

    vector = await embedder.embed_text("hello")

    embedder._call_embed.assert_awaited_once()
    assert len(vector) == Embedder.TARGET_DIM
    # zero-padded
    assert vector[:768] == [0.1] * 768
    assert all(v == 0.0 for v in vector[768:])


@pytest.mark.asyncio
async def test_embed_batch_chunks_large_lists():
    embedder = Embedder(api_key="test-key")

    calls: list[list[str]] = []

    async def _capture(texts):
        calls.append(list(texts))
        return [[0.0] * 768 for _ in texts]

    embedder._call_embed = _capture
    texts = [f"t{i}" for i in range(250)]

    vectors = await embedder.embed_batch(texts)

    assert len(vectors) == 250
    # MAX_BATCH=100 -> 100,100,50
    assert [len(c) for c in calls] == [100, 100, 50]
    assert all(len(v) == Embedder.TARGET_DIM for v in vectors)
