from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text

from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    id: str
    intent: str | None
    actor: str | None
    status: str | None
    proposal: dict[str, Any] | None
    diff_before: dict[str, Any] | None
    diff_after: dict[str, Any] | None
    flags: dict[str, Any] | None
    created_at: str


@dataclass
class CoachState:
    company_id: str
    persona: dict[str, Any]
    facts_count: int
    chunks_count: int
    faqs_count: int
    recent_changes: list[AuditEntry] = field(default_factory=list)


def _parse_audit_row(row: Any) -> AuditEntry:
    return AuditEntry(
        id=str(row[0]),
        intent=row[1],
        actor=row[2],
        status=row[3],
        proposal=row[4] if isinstance(row[4], dict) else None,
        diff_before=row[5] if isinstance(row[5], dict) else None,
        diff_after=row[6] if isinstance(row[6], dict) else None,
        flags=row[7] if isinstance(row[7], dict) else None,
        created_at=row[8].isoformat() if row[8] is not None else "",
    )


async def get_coach_state(company_id: uuid.UUID, recent_limit: int = 10) -> CoachState:
    cid = str(company_id)
    async with AsyncSessionLocal() as session:
        persona_row = (
            await session.execute(
                sa_text(
                    "SELECT tone, addressing, language, emoji_use, length_preference, "
                    "rules, negative_facts, version "
                    "FROM brain_persona WHERE company_id = :cid LIMIT 1"
                ),
                {"cid": cid},
            )
        ).first()
        if persona_row is None:
            persona_dict = {
                "tone": "friendly",
                "addressing": "tykanie",
                "language": "sk",
                "emoji_use": "sometimes",
                "length_preference": "medium",
                "rules": [],
                "negative_facts": [],
                "version": 0,
                "exists": False,
            }
        else:
            persona_dict = {
                "tone": persona_row[0],
                "addressing": persona_row[1],
                "language": persona_row[2],
                "emoji_use": persona_row[3],
                "length_preference": persona_row[4],
                "rules": list(persona_row[5]) if persona_row[5] else [],
                "negative_facts": list(persona_row[6]) if persona_row[6] else [],
                "version": persona_row[7],
                "exists": True,
            }

        facts_n = (
            await session.execute(
                sa_text("SELECT count(*) FROM brain_facts WHERE company_id = :cid"),
                {"cid": cid},
            )
        ).scalar_one()

        chunks_n = (
            await session.execute(
                sa_text(
                    "SELECT count(*) FROM brain_chunks "
                    "WHERE company_id = :cid AND superseded_at IS NULL"
                ),
                {"cid": cid},
            )
        ).scalar_one()

        faqs_n = (
            await session.execute(
                sa_text("SELECT count(*) FROM brain_faqs WHERE company_id = :cid"),
                {"cid": cid},
            )
        ).scalar_one()

        recent_rows = (
            await session.execute(
                sa_text(
                    """
                    SELECT id, intent, actor, status, proposal, diff_before, diff_after, flags, created_at
                    FROM audit_logs
                    WHERE company_id = :cid AND route = 'coach'
                    ORDER BY created_at DESC
                    LIMIT :n
                    """
                ),
                {"cid": cid, "n": recent_limit},
            )
        ).all()
        recent = [_parse_audit_row(r) for r in recent_rows]

    return CoachState(
        company_id=cid,
        persona=persona_dict,
        facts_count=int(facts_n or 0),
        chunks_count=int(chunks_n or 0),
        faqs_count=int(faqs_n or 0),
        recent_changes=recent,
    )


async def get_coach_history(
    company_id: uuid.UUID, limit: int = 50
) -> list[AuditEntry]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                sa_text(
                    """
                    SELECT id, intent, actor, status, proposal, diff_before, diff_after, flags, created_at
                    FROM audit_logs
                    WHERE company_id = :cid AND route = 'coach'
                    ORDER BY created_at DESC
                    LIMIT :n
                    """
                ),
                {"cid": str(company_id), "n": min(max(limit, 1), 200)},
            )
        ).all()
    return [_parse_audit_row(r) for r in rows]
