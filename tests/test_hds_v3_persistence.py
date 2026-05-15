"""Tests for HDS-v3 persistence — offline, mocked AsyncSession.

Strategy: AsyncMock spec=AsyncSession; intercept .add() to capture ORM
objects (FAQs, persona), and .execute() to record SQL statements / params
(facts upserts via raw SQL). Validate per-test that the correct rows
would be sent.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.parser import ParseResult
from app.core.extractors.hds_v3.persistence import HDSv3Persistence
from app.core.extractors.hds_v3.types import HdsExtractedFact, HdsFAQ
from app.core.extractors.types import ExtractedProduct


# ---------------------------------------------------------------------------
# Mock session helper
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows=None, scalar_value=None, scalars_list=None):
        self._rows = rows or []
        self._scalar = scalar_value
        self._scalars_list = scalars_list or []

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def scalars(self):
        ret = MagicMock()
        ret.all = MagicMock(return_value=list(self._scalars_list))
        return ret


def _make_session(
    existing_fact_keys: list[tuple[str, str | None]] | None = None,
    existing_faq_questions: list[str] | None = None,
    max_persona_version: int = 0,
):
    session = AsyncMock(spec=AsyncSession)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    executed_sqls: list[tuple[str, dict]] = []

    async def execute(statement, params=None):
        sql = str(statement)
        executed_sqls.append((sql, params or {}))
        sql_lower = sql.lower()
        if "select key, subject from brain_facts" in sql_lower:
            return FakeResult(rows=existing_fact_keys or [])
        if "select question from brain_faqs" in sql_lower:
            return FakeResult(scalars_list=existing_faq_questions or [])
        if "max(brain_personas.version)" in sql_lower or "coalesce" in sql_lower:
            return FakeResult(scalar_value=max_persona_version)
        return FakeResult()

    session.execute = AsyncMock(side_effect=execute)
    session._executed_sqls = executed_sqls  # type: ignore[attr-defined]
    return session


def _company_id() -> UUID:
    return UUID("a1d921f7-3e08-4efd-8769-cf6517d0a29d")


def _fact_upsert_params(session) -> list[dict]:
    out = []
    for sql, params in session._executed_sqls:
        if "INSERT INTO brain_facts" in sql:
            out.append(params)
    return out


# ---------------------------------------------------------------------------
# 1. Company metadata
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_inserts_company_metadata():
    parse = ParseResult(
        company_name="Skákacie hrady Orava s.r.o.",
        company_ico="12345678",
        company_dic="SK2020123456",
    )
    session = _make_session()
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona={},
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["facts_inserted"] == 3
    keys = {p["key"] for p in _fact_upsert_params(session)}
    assert {"company_name", "ico", "dic"} <= keys


# ---------------------------------------------------------------------------
# 2. Products
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_inserts_products():
    parse = ParseResult(
        products=[
            ExtractedProduct(
                name="Tiger",
                description="Skákací hrad pre deti",
                price_text="160€/Deň",
                price_eur=160.0,
                price_unit="Deň",
                attributes={"kapacita": "9 deti"},
                source_url="https://x.sk/produkty",
                source_type="hds_v3",
            ),
            ExtractedProduct(
                name="Rozprávkovo",
                description=None,
                price_text="180€",
                price_eur=180.0,
                price_unit="Deň",
                attributes={},
                source_url="https://x.sk/produkty",
                source_type="hds_v3",
            ),
        ]
    )
    session = _make_session()
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona={},
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["facts_inserted"] == 2
    product_rows = [
        p for p in _fact_upsert_params(session) if p["key"] == "product"
    ]
    assert len(product_rows) == 2
    # value payload carries price_eur + attributes
    value0 = json.loads(product_rows[0]["value"])
    assert value0["name"] == "Tiger"
    assert value0["price_eur"] == 160.0
    assert value0["attributes"] == {"kapacita": "9 deti"}


# ---------------------------------------------------------------------------
# 3. Contacts
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_inserts_contacts():
    parse = ParseResult(
        contacts=[
            HdsExtractedFact(
                type="contact",
                content="Ján Novák: 0907 043 467 info@x.sk",
                source_url="https://x.sk/kontakt",
                meta={"phone": "0907 043 467", "email": "info@x.sk"},
            ),
            HdsExtractedFact(
                type="address",
                content="Sídlo: Babín 420, 02952",
                source_url="https://x.sk/kontakt",
                meta={"address_type": "sidlo", "value": "Babín 420, 02952"},
            ),
        ]
    )
    session = _make_session()
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona={},
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["facts_inserted"] == 2
    keys = [p["key"] for p in _fact_upsert_params(session)]
    assert "contact_contact" in keys
    assert "contact_address" in keys


# ---------------------------------------------------------------------------
# 4. FAQs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_inserts_faqs():
    parse = ParseResult(
        faqs=[
            HdsFAQ(
                question="Aká je doprava?",
                answer="Od Babína 15km zadarmo.",
                source_url="https://x.sk/faq",
            ),
            HdsFAQ(
                question="Je obsluha v cene?",
                answer="Áno.",
                source_url="https://x.sk/faq",
            ),
        ]
    )
    session = _make_session()
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona={},
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["faqs_inserted"] == 2
    assert result["faqs_skipped_duplicates"] == 0
    # Two BrainFaq instances added
    added = [c.args[0] for c in session.add.call_args_list]
    assert len(added) == 2
    assert all(type(a).__name__ == "BrainFaq" for a in added)


# ---------------------------------------------------------------------------
# 5. Facts UPSERT — pre-existing keys counted as updates (no supersede column)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_upserts_existing_facts_as_updates():
    parse = ParseResult(
        company_name="Skákacie hrady Orava s.r.o.",
        products=[
            ExtractedProduct(
                name="Tiger",
                description=None,
                price_text=None,
                price_eur=160.0,
                price_unit="Deň",
                attributes={},
                source_url="https://x.sk/p",
                source_type="hds_v3",
            ),
        ],
    )
    # Pretend brain_facts already has rows for company_name + Tiger
    existing = [
        ("company_name", "skakacie hrady orava s.r.o."),
        ("product", "tiger"),
    ]
    session = _make_session(existing_fact_keys=existing)
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona={},
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["facts_inserted"] == 0
    assert result["facts_updated"] == 2


# ---------------------------------------------------------------------------
# 6. FAQ dedup against existing rows
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_skips_existing_faqs():
    parse = ParseResult(
        faqs=[
            HdsFAQ(
                question="Aká je doprava?",
                answer="15 km zadarmo.",
                source_url="https://x.sk/faq",
            ),
            HdsFAQ(
                question="Nová otázka?",
                answer="Nová odpoveď.",
                source_url="https://x.sk/faq",
            ),
            HdsFAQ(
                question="AKÁ JE DOPRAVA?",  # case-insensitive dup
                answer="Iná odpoveď.",
                source_url="https://x.sk/faq",
            ),
        ]
    )
    session = _make_session(existing_faq_questions=["Aká je doprava?"])
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona={},
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["faqs_inserted"] == 1
    assert result["faqs_skipped_duplicates"] == 2


# ---------------------------------------------------------------------------
# 7. Persona — first insert gets version 1
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_inserts_persona_version_1_first_time():
    parse = ParseResult()
    persona = {
        "success": True,
        "persona_text": "PROFIL FIRMY: Skákacie hrady Orava...",
        "word_count": 1500,
        "tokens_in": 5000,
        "tokens_out": 1500,
        "cost_usd": 0.01082,
        "source_urls": ["https://x.sk/", "https://x.sk/produkty"],
    }
    session = _make_session(max_persona_version=0)
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona=persona,
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["persona_inserted"] is True
    assert result["persona_version"] == 1
    added = [c.args[0] for c in session.add.call_args_list]
    persona_docs = [a for a in added if type(a).__name__ == "BrainPersonaDocument"]
    assert len(persona_docs) == 1
    assert persona_docs[0].version == 1
    assert persona_docs[0].word_count == 1500


# ---------------------------------------------------------------------------
# 8. Persona — re-ingest bumps version
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persist_bumps_persona_version_on_re_ingest():
    parse = ParseResult()
    persona = {
        "success": True,
        "persona_text": "v3 persona text",
        "word_count": 1600,
        "tokens_in": 4500,
        "tokens_out": 1600,
        "cost_usd": 0.012,
        "source_urls": ["https://x.sk/"],
    }
    session = _make_session(max_persona_version=2)
    persistence = HDSv3Persistence()
    result = await persistence.persist(
        session=session,
        company_id=_company_id(),
        parse=parse,
        persona=persona,
        source_url="https://x.sk/",
    )
    assert result["error"] is None
    assert result["persona_version"] == 3
    added = [c.args[0] for c in session.add.call_args_list]
    persona_docs = [a for a in added if type(a).__name__ == "BrainPersonaDocument"]
    assert persona_docs[0].version == 3
