from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import text as sa_text

from app.core.llm.anthropic_client import call_haiku
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

SUMMARY_FRESHNESS_THRESHOLD = 10
SUMMARY_SYSTEM_PROMPT = (
    "Stručne zhrň túto konverzáciu pre AI asistenta. "
    "Zachovaj fakty (mená, čísla, ceny, dátumy). 2-3 vety."
)


def conversation_id_for(customer_id: str, channel: str) -> str:
    return f"{customer_id}:{channel}"


async def _fetch_existing_summary(
    company_id: uuid.UUID, conversation_id: str
) -> Optional[tuple[str, int]]:
    try:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    sa_text(
                        """
                        SELECT summary, message_count_at_summary
                        FROM brain_summaries
                        WHERE company_id = :cid AND conversation_id = :conv
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """
                    ),
                    {"cid": str(company_id), "conv": conversation_id},
                )
            ).first()
        if row is None:
            return None
        return (row[0], int(row[1] or 0))
    except Exception as exc:
        logger.warning("brain_summaries fetch failed: %s", exc)
        return None


async def _store_summary(
    company_id: uuid.UUID,
    conversation_id: str,
    customer_id_uuid: uuid.UUID | None,
    summary: str,
    message_count: int,
) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_text(
                    """
                    INSERT INTO brain_summaries
                        (company_id, conversation_id, customer_id, summary, message_count_at_summary)
                    VALUES (:cid, :conv, :cust, :summary, :mc)
                    """
                ),
                {
                    "cid": str(company_id),
                    "conv": conversation_id,
                    "cust": str(customer_id_uuid) if customer_id_uuid else None,
                    "summary": summary[:8000],
                    "mc": message_count,
                },
            )
            await session.commit()
    except Exception as exc:
        logger.warning("brain_summaries insert failed: %s", exc)


async def generate_summary(messages: list[dict]) -> Optional[str]:
    """Summarise conversation via Haiku. Returns None on failure."""
    if not messages:
        return None
    convo_lines: list[str] = []
    for m in messages:
        role = (m.get("role") or "").lower()
        content = (m.get("content") or "").strip()
        if not content:
            continue
        speaker = "Zákazník" if role == "user" else "Asistent"
        convo_lines.append(f"{speaker}: {content[:600]}")
    if not convo_lines:
        return None

    user_block = "\n".join(convo_lines)
    try:
        return await call_haiku(
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_block}],
            max_tokens=300,
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning("summary generation failed: %s", exc)
        return None


async def get_or_create_summary(
    company_id: uuid.UUID,
    conversation_id: str,
    messages: list[dict],
    customer_id_uuid: uuid.UUID | None = None,
) -> Optional[str]:
    """Return cached summary if fresh enough, else generate a new one.

    `messages` is the full historical conversation (chronological).
    Considered fresh when `message_count_at_summary >= len(messages) - SUMMARY_FRESHNESS_THRESHOLD`.
    """
    current_count = len(messages)
    existing = await _fetch_existing_summary(company_id, conversation_id)
    if existing is not None:
        summary, count_at = existing
        if count_at >= current_count - SUMMARY_FRESHNESS_THRESHOLD:
            return summary

    summary = await generate_summary(messages)
    if not summary:
        return None

    await _store_summary(
        company_id=company_id,
        conversation_id=conversation_id,
        customer_id_uuid=customer_id_uuid,
        summary=summary,
        message_count=current_count,
    )
    return summary
