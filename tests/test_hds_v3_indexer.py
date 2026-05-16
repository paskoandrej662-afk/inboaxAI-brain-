"""Tests for HDS-v3 Indexer — offline, mocked AsyncSession + embedder."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.indexer import HDSv3Indexer


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)


def _company_id() -> UUID:
    return UUID("a1d921f7-3e08-4efd-8769-cf6517d0a29d")


def _make_session(
    fact_rows=None,
    faq_rows=None,
    delete_rowcount=0,
):
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()

    executed: list[tuple[str, dict]] = []

    async def execute(statement, params=None):
        sql = str(statement)
        executed.append((sql, params or {}))
        sql_lower = sql.lower()
        if "from brain_facts" in sql_lower:
            return FakeResult(rows=fact_rows or [])
        if "from brain_faqs" in sql_lower:
            return FakeResult(rows=faq_rows or [])
        if "delete from brain_chunks" in sql_lower:
            return FakeResult(rowcount=delete_rowcount)
        return FakeResult()

    session.execute = AsyncMock(side_effect=execute)
    session._executed = executed  # type: ignore[attr-defined]
    return session


def _fake_embedder(dim: int = 1536):
    embedder = MagicMock()

    async def _embed(texts):
        return [[0.1] * dim for _ in texts]

    embedder.embed_batch = _embed
    return embedder


@pytest.mark.asyncio
async def test_reindex_with_no_facts_returns_error():
    session = _make_session(fact_rows=[], faq_rows=[])
    indexer = HDSv3Indexer(embedder=_fake_embedder())

    result = await indexer.reindex(session, _company_id())

    assert result["success"] is False
    assert result["error"] == "no_facts_or_faqs"
    assert result["facts_seen"] == 0
    assert result["faqs_seen"] == 0
    # No insert or delete should have happened
    inserts = [s for s, _ in session._executed if "insert into brain_chunks" in s.lower()]
    assert inserts == []


@pytest.mark.asyncio
async def test_reindex_creates_chunks_for_facts():
    fact_rows = [
        (
            "product",
            "tiger",
            {
                "value": "Tiger",
                "name": "Tiger",
                "price_eur": 80.0,
                "price_unit": "deň",
                "description": "Skákací hrad pre väčšie deti",
                "attributes": {"vek": "5-12"},
            },
            "https://x.sk/p/tiger",
        ),
        (
            "contact_phone",
            "0900 123 456",
            {"value": "0900 123 456", "type": "phone"},
            "https://x.sk/kontakt",
        ),
    ]
    faq_rows = [
        ("Aká je cena?", "80€/deň", "https://x.sk/faq"),
    ]
    session = _make_session(fact_rows=fact_rows, faq_rows=faq_rows)
    indexer = HDSv3Indexer(embedder=_fake_embedder())

    result = await indexer.reindex(session, _company_id())

    assert result["success"] is True, result
    assert result["facts_seen"] == 2
    assert result["faqs_seen"] == 1
    assert result["inserted"] == 3
    inserts = [
        (s, p) for s, p in session._executed if "insert into brain_chunks" in s.lower()
    ]
    assert len(inserts) == 3
    # Validate product chunk text
    product_chunk = next(p for _, p in inserts if "Produkt" in p["text"])
    assert "Tiger" in product_chunk["text"]
    assert "80.00€" in product_chunk["text"]
    assert "Popis" in product_chunk["text"]
    # FAQ chunk format
    faq_chunk = next(p for _, p in inserts if p["section"] == "faq")
    assert "Otázka" in faq_chunk["text"]
    assert "Odpoveď" in faq_chunk["text"]
    # All inserts share source_type = hds_v3_chunk
    assert all(p["source_type"] == "hds_v3_chunk" for _, p in inserts)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reindex_deletes_old_chunks():
    fact_rows = [
        ("info_address", "bratislava", {"value": "Bratislava"}, None),
    ]
    session = _make_session(
        fact_rows=fact_rows,
        faq_rows=[],
        delete_rowcount=12,
    )
    indexer = HDSv3Indexer(embedder=_fake_embedder())

    result = await indexer.reindex(session, _company_id())

    assert result["success"] is True
    assert result["deleted"] == 12
    deletes = [
        (s, p)
        for s, p in session._executed
        if "delete from brain_chunks" in s.lower()
    ]
    assert len(deletes) == 1
    assert deletes[0][1]["stype"] == "hds_v3_chunk"
