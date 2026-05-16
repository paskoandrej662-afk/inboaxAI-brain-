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

    from sqlalchemy import text as sa_text

    async with AsyncSessionLocal() as session:
        by_key = await session.execute(
            sa_text(
                "SELECT key, COUNT(*) FROM brain_facts "
                "WHERE company_id = :cid GROUP BY key ORDER BY COUNT(*) DESC"
            ),
            {"cid": str(TEST_COMPANY_ID)},
        )
        rows_by_key = list(by_key)
        print("\n=== DB FACTS BY KEY (after persist) ===")
        for key, cnt in rows_by_key:
            print(f"  {key:40} {cnt}")

        product_count_res = await session.execute(
            sa_text(
                "SELECT COUNT(*) FROM brain_facts "
                "WHERE company_id = :cid AND key = 'product'"
            ),
            {"cid": str(TEST_COMPANY_ID)},
        )
        product_count = product_count_res.scalar() or 0

        with_image_res = await session.execute(
            sa_text(
                "SELECT COUNT(*) FROM brain_facts "
                "WHERE company_id = :cid AND key = 'product' "
                "AND value->>'primary_image_url' IS NOT NULL"
            ),
            {"cid": str(TEST_COMPANY_ID)},
        )
        with_image_db = with_image_res.scalar() or 0

        print(f"\n>>> PRODUCT facts in DB: {product_count}")
        print(f">>> Products WITH primary_image_url (DB): {with_image_db}")

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
    assert product_count >= 5, (
        f"FATAL: only {product_count} product rows in DB (expected >= 5). "
        "Persistence path for products is broken."
    )
    assert with_image_db >= 5, (
        f"FATAL: only {with_image_db}/{product_count} product rows carry "
        "primary_image_url in DB. In-memory shows {with_image} attached. "
        "Image matching wired up but persistence not emitting the field."
    )
    print("\nOK: all assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
