"""Tests for HDS-v3 Retriever — offline, mocked AsyncSession + embedder."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.retriever import (
    HDSv3Retriever,
    MIN_SIMILARITY,
    SOURCE_TYPE,
)


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)


def _company_id() -> UUID:
    return UUID("a1d921f7-3e08-4efd-8769-cf6517d0a29d")


def _make_session(rows=None):
    session = AsyncMock(spec=AsyncSession)
    executed: list[tuple[str, dict]] = []

    async def execute(statement, params=None):
        executed.append((str(statement), params or {}))
        return FakeResult(rows=rows or [])

    session.execute = AsyncMock(side_effect=execute)
    session._executed = executed  # type: ignore[attr-defined]
    return session


def _fake_embedder(dim: int = 1536):
    embedder = MagicMock()

    async def _embed(text):
        return [0.1] * dim

    embedder.embed_text = _embed
    return embedder


@pytest.mark.asyncio
async def test_retrieve_empty_query_returns_empty():
    session = _make_session()
    retriever = HDSv3Retriever(embedder=_fake_embedder())

    result = await retriever.retrieve(session, _company_id(), "   ")

    assert result == []
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrieve_filters_below_min_similarity():
    rows = [
        ("relevant chunk", "https://x.sk/a", "product", {}, 0.85),
        ("borderline", "https://x.sk/b", "info_address", {}, 0.31),
        ("noise", "https://x.sk/c", "faq", {}, MIN_SIMILARITY - 0.01),
        ("more noise", "https://x.sk/d", "faq", {}, 0.0),
    ]
    session = _make_session(rows=rows)
    retriever = HDSv3Retriever(embedder=_fake_embedder())

    result = await retriever.retrieve(session, _company_id(), "tiger")

    assert len(result) == 2
    assert {c.text for c in result} == {"relevant chunk", "borderline"}
    assert all(c.similarity >= MIN_SIMILARITY for c in result)


@pytest.mark.asyncio
async def test_retrieve_returns_top_k():
    rows = [
        (f"chunk-{i}", None, "info", {}, 0.9 - i * 0.05) for i in range(5)
    ]
    session = _make_session(rows=rows)
    retriever = HDSv3Retriever(embedder=_fake_embedder())

    result = await retriever.retrieve(
        session, _company_id(), "tiger", top_k=3
    )

    # Mock returns the rows we gave, just confirm LIMIT param was 3 and
    # all returned chunks are present (all 5 are above threshold here).
    assert len(result) == 5
    params = session._executed[0][1]
    assert params["k"] == 3


@pytest.mark.asyncio
async def test_retrieve_calls_pgvector_cosine():
    session = _make_session(rows=[])
    retriever = HDSv3Retriever(embedder=_fake_embedder())

    await retriever.retrieve(session, _company_id(), "tiger")

    sql, params = session._executed[0]
    sql_lower = sql.lower()
    assert "embedding <=>" in sql_lower
    assert "cast(:emb as vector)" in sql_lower
    assert "1 - (embedding <=>" in sql_lower
    assert params["cid"] == str(_company_id())
    assert params["stype"] == SOURCE_TYPE
    # Embedding literal is a stringified pgvector array.
    assert params["emb"].startswith("[") and params["emb"].endswith("]")
