from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.coach.proposal_generator import Proposal, ToolCall
from app.core.coach.proposal_store import delete_proposal, get_proposal
from app.core.embeddings import embed_batch
from app.core.llm.openai_client import get_client as get_openai_client
from app.db import AsyncSessionLocal, cache_delete, get_redis_client

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    success: bool
    audit_log_id: str | None = None
    applied_changes_count: int = 0
    idempotent_replay: bool = False
    error: str | None = None
    diff_after: dict[str, Any] = field(default_factory=dict)


async def _persona_snapshot(session: AsyncSession, company_id: str) -> dict[str, Any]:
    row = (
        await session.execute(
            sa_text(
                "SELECT tone, addressing, language, emoji_use, length_preference, rules, negative_facts, version "
                "FROM brain_persona WHERE company_id = :cid LIMIT 1"
            ),
            {"cid": company_id},
        )
    ).first()
    if row is None:
        return {}
    return {
        "tone": row[0],
        "addressing": row[1],
        "language": row[2],
        "emoji_use": row[3],
        "length_preference": row[4],
        "rules": list(row[5]) if row[5] else [],
        "negative_facts": list(row[6]) if row[6] else [],
        "version": row[7],
    }


async def _ensure_persona(session: AsyncSession, company_id: str) -> None:
    """Insert default persona row if none exists for this company."""
    await session.execute(
        sa_text(
            """
            INSERT INTO brain_persona (company_id)
            VALUES (:cid)
            ON CONFLICT (company_id) DO NOTHING
            """
        ),
        {"cid": company_id},
    )


async def _apply_update_persona_field(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    field = args["field"]
    value = args["value"]
    if field not in ("tone", "addressing", "language", "emoji_use", "length_preference"):
        raise ValueError(f"invalid persona field {field!r}")
    sql = (
        f"UPDATE brain_persona SET {field} = :value, version = version + 1, "
        f"updated_at = now() WHERE company_id = :cid"
    )
    await session.execute(sa_text(sql), {"value": value, "cid": company_id})


async def _apply_add_persona_rule(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    await session.execute(
        sa_text(
            """
            UPDATE brain_persona
            SET rules = rules || to_jsonb(CAST(:rule AS text)),
                version = version + 1,
                updated_at = now()
            WHERE company_id = :cid
            """
        ),
        {"rule": args["rule"], "cid": company_id},
    )


async def _apply_remove_persona_rule(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    idx = int(args["rule_index"])
    await session.execute(
        sa_text(
            """
            UPDATE brain_persona
            SET rules = rules - CAST(:idx AS integer),
                version = version + 1,
                updated_at = now()
            WHERE company_id = :cid
            """
        ),
        {"idx": idx, "cid": company_id},
    )


async def _apply_add_negative_fact(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    await session.execute(
        sa_text(
            """
            UPDATE brain_persona
            SET negative_facts = negative_facts || to_jsonb(CAST(:fact AS text)),
                version = version + 1,
                updated_at = now()
            WHERE company_id = :cid
            """
        ),
        {"fact": args["fact"], "cid": company_id},
    )


async def _apply_upsert_fact(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    key = args["key"]
    subject = args.get("subject")
    value = args["value"]
    evidence = args.get("evidence") or value
    await session.execute(
        sa_text(
            """
            INSERT INTO brain_facts (company_id, key, subject, value, evidence, source_url, confidence)
            VALUES (:cid, :key, :subject, CAST(:value AS jsonb), :evidence, :source_url, 1.0)
            ON CONFLICT (company_id, key, subject) DO UPDATE
            SET value = EXCLUDED.value,
                evidence = EXCLUDED.evidence,
                source_url = EXCLUDED.source_url,
                confidence = 1.0,
                updated_at = now()
            """
        ),
        {
            "cid": company_id,
            "key": key,
            "subject": subject,
            "value": json.dumps({"raw": value}, ensure_ascii=False),
            "evidence": evidence,
            "source_url": "coach:owner_update",
        },
    )


async def _apply_delete_fact(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    key = args["key"]
    subject = args.get("subject")
    if subject is None:
        await session.execute(
            sa_text(
                "DELETE FROM brain_facts WHERE company_id = :cid AND key = :k AND subject IS NULL"
            ),
            {"cid": company_id, "k": key},
        )
    else:
        await session.execute(
            sa_text(
                "DELETE FROM brain_facts WHERE company_id = :cid AND key = :k AND subject = :s"
            ),
            {"cid": company_id, "k": key, "s": subject},
        )


async def _embed_text(text: str) -> list[float]:
    client = get_openai_client()
    vectors = await embed_batch([text], client)
    return vectors[0]


def _vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


async def _apply_add_faq(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    question = args["question"]
    answer = args["answer"]
    embedding = await _embed_text(f"{question}\n{answer}")
    await session.execute(
        sa_text(
            """
            INSERT INTO brain_faqs (company_id, question, answer, source_url, embedding)
            VALUES (:cid, :q, :a, 'coach:owner_update', CAST(:vec AS vector))
            """
        ),
        {
            "cid": company_id,
            "q": question,
            "a": answer,
            "vec": _vec_literal(embedding),
        },
    )


async def _apply_mark_chunk_outdated(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> int:
    target = args["target_text"]
    embedding = await _embed_text(target)
    # Find best-match active chunk and mark it superseded.
    row = (
        await session.execute(
            sa_text(
                """
                SELECT id FROM brain_chunks
                WHERE company_id = :cid
                  AND superseded_at IS NULL
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector) ASC
                LIMIT 1
                """
            ),
            {"cid": company_id, "vec": _vec_literal(embedding)},
        )
    ).first()
    if not row:
        return 0
    await session.execute(
        sa_text("UPDATE brain_chunks SET superseded_at = now() WHERE id = :id"),
        {"id": row[0]},
    )
    return 1


async def _apply_add_chunk(
    session: AsyncSession, company_id: str, args: dict[str, Any]
) -> None:
    text = args["text"]
    section = args.get("section") or "general"
    import hashlib

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    embedding = await _embed_text(text)
    await session.execute(
        sa_text(
            """
            INSERT INTO brain_chunks (
                company_id, text, source_url, source_type, section, content_hash, embedding, meta
            ) VALUES (
                :cid, :text, 'coach:owner_update', 'coach:owner_update', :section,
                :hash, CAST(:vec AS vector), CAST('{"actor":"owner_coach"}' AS jsonb)
            )
            ON CONFLICT (company_id, content_hash) WHERE superseded_at IS NULL DO NOTHING
            """
        ),
        {
            "cid": company_id,
            "text": text,
            "section": section,
            "hash": content_hash,
            "vec": _vec_literal(embedding),
        },
    )


async def _check_idempotent(
    session: AsyncSession, company_id: str, proposal_hash: str
) -> str | None:
    """Return existing audit_log id if this proposal_hash already applied successfully."""
    row = (
        await session.execute(
            sa_text(
                "SELECT id FROM audit_logs "
                "WHERE company_id = :cid AND proposal_hash = :ph AND status = 'applied' "
                "LIMIT 1"
            ),
            {"cid": company_id, "ph": proposal_hash},
        )
    ).first()
    return str(row[0]) if row else None


async def _insert_audit(
    session: AsyncSession,
    *,
    company_id: str,
    proposal: Proposal,
    diff_before: dict[str, Any],
    status: str,
    actor: str,
    error: str | None = None,
    diff_after: dict[str, Any] | None = None,
) -> str:
    audit_id = str(uuid.uuid4())
    proposal_payload = {
        "proposal_id": proposal.proposal_id,
        "tool_calls": [{"name": tc.name, "args": tc.args} for tc in proposal.tool_calls],
        "preview_text": proposal.preview_text,
        "intent": proposal.intent,
        "token_usage": proposal.token_usage,
    }
    await session.execute(
        sa_text(
            """
            INSERT INTO audit_logs (
                id, company_id, query, intent, route, response,
                proposal_hash, proposal, diff_before, diff_after,
                actor, status, model_versions, flags
            ) VALUES (
                :id, :cid, :query, :intent, 'coach', :response,
                :phash, CAST(:proposal AS jsonb),
                CAST(:diff_before AS jsonb), CAST(:diff_after AS jsonb),
                :actor, :status,
                CAST(:mv AS jsonb), CAST(:flags AS jsonb)
            )
            """
        ),
        {
            "id": audit_id,
            "cid": company_id,
            "query": proposal.preview_text[:8000],
            "intent": f"coach_apply:{proposal.intent}",
            "response": (proposal.raw_response or proposal.preview_text)[:8000],
            "phash": proposal.proposal_hash,
            "proposal": json.dumps(proposal_payload, ensure_ascii=False),
            "diff_before": json.dumps(diff_before, ensure_ascii=False, default=str),
            "diff_after": json.dumps(diff_after or {}, ensure_ascii=False, default=str),
            "actor": actor,
            "status": status,
            "mv": json.dumps({"router": "claude-haiku-4-5", "generator": "claude-sonnet-4-6"}),
            "flags": json.dumps({"error": error} if error else {}),
        },
    )
    return audit_id


async def _invalidate_caches(company_id: str) -> None:
    await cache_delete(f"persona:{company_id}")
    await cache_delete(f"facts:{company_id}")


async def apply_proposal(
    proposal_id: str,
    *,
    actor: str = "owner",
    requested_company_id: str | None = None,
) -> ApplyResult:
    proposal = await get_proposal(proposal_id)
    if proposal is None:
        return ApplyResult(success=False, error="Proposal nenájdený alebo expirovaný (TTL 10 min).")

    if requested_company_id and proposal.company_id != requested_company_id:
        return ApplyResult(success=False, error="Cross-tenant guard: company_id mismatch.")

    company_id = proposal.company_id

    # Take pre-apply snapshot for diff_before
    async with AsyncSessionLocal() as ro_session:
        diff_before = {"persona": await _persona_snapshot(ro_session, company_id)}
        # Idempotency check
        existing_audit_id = await _check_idempotent(ro_session, company_id, proposal.proposal_hash)
        if existing_audit_id:
            await delete_proposal(proposal.proposal_id, company_id)
            return ApplyResult(
                success=True,
                audit_log_id=existing_audit_id,
                applied_changes_count=0,
                idempotent_replay=True,
                diff_after={"info": "already applied"},
            )

    applied_count = 0
    apply_error: str | None = None

    async with AsyncSessionLocal() as session:
        try:
            async with session.begin():
                # Make sure persona row exists before mutations
                await _ensure_persona(session, company_id)

                for tc in proposal.tool_calls:
                    name = tc.name
                    args = tc.args
                    if name == "update_persona_field":
                        await _apply_update_persona_field(session, company_id, args)
                    elif name == "add_persona_rule":
                        await _apply_add_persona_rule(session, company_id, args)
                    elif name == "remove_persona_rule":
                        await _apply_remove_persona_rule(session, company_id, args)
                    elif name == "add_negative_fact":
                        await _apply_add_negative_fact(session, company_id, args)
                    elif name == "upsert_fact":
                        await _apply_upsert_fact(session, company_id, args)
                    elif name == "delete_fact":
                        await _apply_delete_fact(session, company_id, args)
                    elif name == "add_faq":
                        await _apply_add_faq(session, company_id, args)
                    elif name == "mark_chunk_outdated":
                        await _apply_mark_chunk_outdated(session, company_id, args)
                    elif name == "add_chunk":
                        await _apply_add_chunk(session, company_id, args)
                    elif name == "request_clarification":
                        # never applied
                        continue
                    else:
                        raise ValueError(f"unknown tool: {name}")
                    applied_count += 1

                diff_after = {"persona": await _persona_snapshot(session, company_id)}
                audit_id = await _insert_audit(
                    session,
                    company_id=company_id,
                    proposal=proposal,
                    diff_before=diff_before,
                    diff_after=diff_after,
                    status="applied",
                    actor=actor,
                )
        except Exception as exc:
            logger.exception("apply_proposal failed: %s", exc)
            apply_error = str(exc)[:500]

    if apply_error is not None:
        # Best-effort: separate session to record the failure
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    audit_id = await _insert_audit(
                        session,
                        company_id=company_id,
                        proposal=proposal,
                        diff_before=diff_before,
                        diff_after={},
                        status="failed",
                        actor=actor,
                        error=apply_error,
                    )
        except Exception as exc2:
            logger.exception("audit_logs insert (failed branch) crashed: %s", exc2)
            audit_id = None  # type: ignore[assignment]
        return ApplyResult(
            success=False,
            audit_log_id=audit_id,
            applied_changes_count=0,
            error=apply_error,
        )

    # Cache invalidation. Keep proposal in Redis (TTL=10min) so that a
    # client retry returns idempotent_replay=True instead of "expired".
    await _invalidate_caches(company_id)

    return ApplyResult(
        success=True,
        audit_log_id=audit_id,
        applied_changes_count=applied_count,
        diff_after=diff_after,
    )
