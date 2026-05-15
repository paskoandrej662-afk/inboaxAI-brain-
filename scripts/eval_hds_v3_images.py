"""Real E2E eval — verify product image matching on Skákačky Orava.

Run:
    set -a; . ./.env; set +a
    export DATABASE_URL="$DATABASE_URL_SUPABASE"
    PYTHONPATH=. python3 scripts/eval_hds_v3_images.py

Estimated cost: ~$0.02-0.03 (extraction + persona). Image matching is
free locally (BrowserPool rendering + httpx HEAD).
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

    from sqlalchemy import select

    from app.core.extractors.hds_v3.engine import HDSv3Engine
    from app.db import AsyncSessionLocal
    from app.models.brain_facts import BrainFact

    print("=== HDS-v3 Image Matching Eval ===")
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
        print("\n=== IMAGE MATCHING ===")
        print(f"  Candidates seen:  {result.get('images_total_candidates', 0)}")
        print(f"  Products with primary: {result.get('images_matched', 0)}")

    async with AsyncSessionLocal() as session:
        stmt = (
            select(BrainFact)
            .where(BrainFact.company_id == TEST_COMPANY_ID)
            .where(BrainFact.key == "product")
            .limit(30)
        )
        facts = (await session.execute(stmt)).scalars().all()

    with_image = 0
    print("\n=== PRODUCT IMAGE STATE (top 30) ===")
    for fact in facts:
        value = fact.value or {}
        name = value.get("name") if isinstance(value, dict) else None
        primary = (
            value.get("primary_image_url") if isinstance(value, dict) else None
        )
        secondaries = value.get("image_urls") if isinstance(value, dict) else None
        if primary:
            with_image += 1
            sec_count = len(secondaries or [])
            print(f"  OK  {name:30} → {primary[:90]}  (+{sec_count} sec)")
        else:
            print(f"  --  {name:30} → (no image)")

    print(
        f"\nProducts with primary image: {with_image}/{len(facts)}"
    )

    assert result.get("success"), f"Ingest failed: {result.get('error')}"
    persist = result.get("persist") or {}
    assert persist.get("error") is None, f"Persistence failed: {persist.get('error')}"
    assert with_image >= 5, (
        f"Expected ≥5 products with primary image, got {with_image}"
    )
    print("\nOK: all assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
