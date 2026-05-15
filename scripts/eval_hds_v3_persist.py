"""Real E2E eval: ingest + persist HDS-v3 to DB, then verify via SELECT.

Usage:
    PYTHONPATH=. python3 scripts/eval_hds_v3_persist.py

Requires:
    GEMINI_API_KEY, DATABASE_URL (Supabase Postgres async URL)

Estimated cost: ~$0.02-0.03 (extraction + persona Gemini calls).

This script is intentionally read+write against the live Supabase DB.
It runs against the dedicated TEST_COMPANY_ID so re-runs are idempotent
in shape (UPSERT facts, dedup FAQs, version-bumped persona).
"""
from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


TEST_COMPANY_ID = UUID("a1d921f7-3e08-4efd-8769-cf6517d0a29d")
TEST_URL = "https://skakaciehradyorava.sk/"


async def main() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: missing env var GEMINI_API_KEY")
        return 1

    from app.config import settings

    db_url = settings.effective_database_url
    if "localhost" in db_url and not os.environ.get("DATABASE_URL"):
        print(
            "ERROR: no production DB URL resolved "
            "(set DATABASE_URL or DATABASE_URL_SUPABASE)"
        )
        return 1

    from sqlalchemy import func, select

    from app.core.extractors.hds_v3.engine import HDSv3Engine
    from app.db import AsyncSessionLocal
    from app.models.brain_faqs import BrainFaq
    from app.models.brain_facts import BrainFact
    from app.models.brain_personas import BrainPersonaDocument

    print("=== HDS-v3 Persist E2E Eval ===")
    print(f"URL:     {TEST_URL}")
    print(f"Company: {TEST_COMPANY_ID}")

    engine = HDSv3Engine()
    async with AsyncSessionLocal() as session:
        result = await engine.ingest_and_persist(
            base_url=TEST_URL,
            company_id=TEST_COMPANY_ID,
            session=session,
        )

        print("\n=== INGEST RESULT ===")
        for k, v in result.items():
            if k != "persist":
                print(f"  {k}: {v}")

        print("\n=== PERSIST RESULT ===")
        for k, v in (result.get("persist") or {}).items():
            print(f"  {k}: {v}")

    async with AsyncSessionLocal() as session:
        facts_count = await session.scalar(
            select(func.count(BrainFact.id)).where(
                BrainFact.company_id == TEST_COMPANY_ID
            )
        )
        faqs_count = await session.scalar(
            select(func.count(BrainFaq.id)).where(
                BrainFaq.company_id == TEST_COMPANY_ID
            )
        )
        persona = await session.scalar(
            select(BrainPersonaDocument)
            .where(BrainPersonaDocument.company_id == TEST_COMPANY_ID)
            .order_by(BrainPersonaDocument.version.desc())
            .limit(1)
        )

    print("\n=== DB STATE ===")
    print(f"  brain_facts rows:    {facts_count}")
    print(f"  brain_faqs rows:     {faqs_count}")
    print(
        f"  latest persona ver:  {persona.version if persona else 'NONE'}"
    )
    print(
        f"  persona word_count:  {persona.word_count if persona else 'N/A'}"
    )
    print(
        f"  persona cost_usd:    "
        f"{float(persona.gemini_cost_usd) if persona and persona.gemini_cost_usd else 'N/A'}"
    )

    assert result.get("success"), f"Ingest failed: {result.get('error')}"
    persist = result.get("persist") or {}
    assert persist.get("error") is None, f"Persistence failed: {persist.get('error')}"
    assert (
        persist["facts_inserted"] + persist["facts_updated"] >= 10
    ), f"Too few facts written: {persist}"
    assert persist["persona_inserted"], "Persona not inserted"
    assert (facts_count or 0) >= 10, "DB facts count too low"
    assert persona is not None and (persona.word_count or 0) >= 1000, (
        "Persona missing or short"
    )
    print("\nOK: all assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
