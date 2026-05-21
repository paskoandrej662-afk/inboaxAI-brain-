"""Real E2E eval: HDS-v3 responder over a full ingest → reindex → 10 queries.

Usage:
    PYTHONPATH=. python3 scripts/eval_hds_v3_respond.py

Requires:
    GEMINI_API_KEY, DATABASE_URL (async Postgres URL with pgvector).

What it does (single conversation, one customer):
    1. Run engine.ingest_and_persist() once. This crawls skakacky, extracts
       facts/faqs/persona, persists them, AND reindexes brain_chunks (the
       reindex-after-persist wiring added in Commit 5.5). Skipped if the
       company already has chunks, unless EVAL_FORCE_INGEST=1.
    2. Fire 10 sequential customer messages at HDSv3Responder.respond(),
       reusing the same customer_id so ConversationMemory accumulates.
       Background memory updates are drained between turns so each turn
       sees the prior turn's persisted state.
    3. Log per-query reply/cost/chunks, and detect at which turn the
       rolling summary first appears (memory summarization fired).

Cost: ~$0.02-0.03 ingest (if run) + ~10 cheap Gemini Flash replies.
"""
from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


TEST_COMPANY_ID = UUID("a1d921f7-3e08-4efd-8769-cf6517d0a29d")
TEST_URL = "https://skakaciehradyorava.sk/"
TEST_CUSTOMER_ID = "eval-respond-5-5"

# 10 queries — a realistic SK conversation. The last turn probes memory
# recall, so it only answers well if earlier turns were summarized/kept.
QUERIES: list[str] = [
    "Ahoj, plánujem detskú oslavu pre syna. Akú atrakciu by ste odporučili?",
    "Koľko stojí prenájom takého skákacieho hradu na jeden deň?",
    "Máte aj šmykľavky alebo iné atrakcie, nielen skákacie hrady?",
    "Doručujete aj do Dolného Kubína a okolia?",
    "Aký veľký priestor potrebujem pre váš najväčší hrad?",
    "Je v cene zahrnutá aj doprava, montáž a demontáž?",
    "Do akého veku detí sú vaše hrady vhodné?",
    "Chcel by som to rezervovať na sobotu. Ako prebieha rezervácia?",
    "Aké sú vaše kontaktné údaje, kam vám môžem zavolať?",
    "Ešte si prosím spomeň — na čo som sa pýtal úplne na začiatku?",
]


def _fmt_money(v: float) -> str:
    return f"${v:.4f}"


async def _drain_background_tasks() -> None:
    """Await fire-and-forget tasks the responder scheduled on this loop.

    The responder writes ConversationMemory in a background task so the
    HTTP response isn't blocked. In the eval we must let those finish
    before the next turn loads memory, otherwise summarization never
    appears to advance.
    """
    me = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _chunk_count(session, company_id: UUID) -> int:
    from sqlalchemy import text as sa_text

    res = await session.execute(
        sa_text(
            "SELECT count(*) FROM brain_chunks "
            "WHERE company_id = :c AND source_type = 'hds_v3_chunk'"
        ),
        {"c": str(company_id)},
    )
    return int(res.scalar() or 0)


async def main() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        # GEMINI_API_KEY isn't a declared Settings field (pydantic ignores
        # it), so pull it straight from .env if present.
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("GEMINI_API_KEY="):
                        os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip()
                        break
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: missing GEMINI_API_KEY")
        return 1

    from app.config import settings

    db_url = settings.effective_database_url
    print("=== HDS-v3 Responder E2E Eval ===")
    print(f"DB:       {db_url.split('@')[-1]}")
    print(f"Company:  {TEST_COMPANY_ID}")
    print(f"Customer: {TEST_CUSTOMER_ID}")

    from app.core.extractors.hds_v3.engine import HDSv3Engine
    from app.core.extractors.hds_v3.responder import HDSv3Responder
    from app.db import AsyncSessionLocal

    force_ingest = os.environ.get("EVAL_FORCE_INGEST") == "1"

    # ---- Step 1: ensure data + chunks (full ingest → persist → reindex) ----
    async with AsyncSessionLocal() as session:
        existing_chunks = await _chunk_count(session, TEST_COMPANY_ID)

    if existing_chunks == 0 or force_ingest:
        print(
            f"\n--- Running full ingest_and_persist "
            f"(existing chunks={existing_chunks}, force={force_ingest}) ---"
        )
        engine = HDSv3Engine()
        async with AsyncSessionLocal() as session:
            ing = await engine.ingest_and_persist(
                base_url=TEST_URL,
                company_id=TEST_COMPANY_ID,
                session=session,
            )
        if not ing.get("success"):
            print(f"ERROR: ingest failed: {ing.get('error')}")
            return 1
        persist = ing.get("persist") or {}
        reindex = ing.get("reindex") or {}
        print(
            f"  ingest: products={ing['products']} facts={ing['facts']} "
            f"faqs={ing['faqs']} persona_words={ing['persona_words']} "
            f"cost={_fmt_money(ing['total_cost_usd'])}"
        )
        print(f"  persist: {persist}")
        print(f"  reindex: {reindex}")
        if reindex.get("error"):
            print(f"ERROR: reindex failed: {reindex['error']}")
            return 1
    else:
        print(f"\n--- Reusing existing {existing_chunks} chunks (skip ingest) ---")

    # ---- Reset this customer's memory so summarization is observable ----
    from sqlalchemy import text as sa_text

    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_text(
                "DELETE FROM brain_customer_memory "
                "WHERE company_id = :c AND external_id = :e"
            ),
            {"c": str(TEST_COMPANY_ID), "e": TEST_CUSTOMER_ID},
        )
        await session.commit()

    # ---- Step 2 + 3: 10-turn conversation ----
    responder = HDSv3Responder(session_factory=AsyncSessionLocal)

    total_cost = 0.0
    total_in = 0
    total_out = 0
    failures = 0
    summary_first_seen_at: int | None = None

    print("\n=== CONVERSATION (10 turns) ===")
    for i, q in enumerate(QUERIES, start=1):
        async with AsyncSessionLocal() as session:
            res = await responder.respond(
                session=session,
                company_id=TEST_COMPANY_ID,
                message=q,
                customer_id=TEST_CUSTOMER_ID,
            )
        # Let the background memory update commit before the next turn reads.
        await _drain_background_tasks()

        if not res.success:
            failures += 1
            print(f"\n[Q{i}] {q}\n  !! FAILED: {res.error}")
            continue

        total_cost += res.cost_usd
        total_in += res.input_tokens
        total_out += res.output_tokens

        # Inspect persisted memory after this turn.
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                sa_text(
                    "SELECT summary_text, message_count "
                    "FROM brain_customer_memory "
                    "WHERE company_id = :c AND external_id = :e"
                ),
                {"c": str(TEST_COMPANY_ID), "e": TEST_CUSTOMER_ID},
            )
            mem = row.fetchone()
        summary_text = mem[0] if mem else None
        msg_count = mem[1] if mem else 0
        if summary_text and summary_first_seen_at is None:
            summary_first_seen_at = i

        reply = (res.reply_text or "").replace("\n", " ")
        if len(reply) > 220:
            reply = reply[:217] + "..."
        print(f"\n[Q{i}] {q}")
        print(f"  reply: {reply}")
        print(
            f"  chunks={res.chunks_used} "
            f"in={res.input_tokens} out={res.output_tokens} "
            f"cost={_fmt_money(res.cost_usd)} dur={res.duration_sec:.1f}s "
            f"| mem_msgs={msg_count} summary={'YES' if summary_text else 'no'}"
        )

    answered = len(QUERIES) - failures
    avg_cost = (total_cost / answered) if answered else 0.0

    print("\n=== SUMMARY ===")
    print(f"  Queries answered:    {answered}/{len(QUERIES)}")
    print(f"  Total cost:          {_fmt_money(total_cost)}")
    print(f"  Avg per query:       {_fmt_money(avg_cost)}")
    print(f"  Total tokens:        in={total_in} out={total_out}")
    if summary_first_seen_at is not None:
        print(f"  Memory summary first appeared at: Q{summary_first_seen_at}")
    else:
        print("  Memory summary: never triggered (check KEEP_RECENT/THRESHOLD)")

    assert failures == 0, f"{failures} queries failed"
    assert summary_first_seen_at is not None, "memory summarization never fired"
    print("\nOK: all assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
