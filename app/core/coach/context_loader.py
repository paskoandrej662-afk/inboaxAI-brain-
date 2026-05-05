from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text

from app.core.responder.retrieval import (
    Persona,
    RetrievedChunk,
    RetrievedFact,
    _load_facts,
    _load_persona,
    _vec_literal,
)
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


@dataclass
class CoachContext:
    company_id: uuid.UUID
    persona: Persona
    facts: list[RetrievedFact]
    candidate_chunks: list[RetrievedChunk]
    sections_summary: list[tuple[str, int]]
    faqs_count: int
    chunks_count: int
    rules: list[str] = field(default_factory=list)
    negative_facts: list[str] = field(default_factory=list)


async def _sections_summary(company_id: uuid.UUID) -> list[tuple[str, int]]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                sa_text(
                    """
                    SELECT COALESCE(section, 'general') AS section, count(*)
                    FROM brain_chunks
                    WHERE company_id = :cid AND superseded_at IS NULL
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                ),
                {"cid": str(company_id)},
            )
        ).all()
    return [(r[0], int(r[1])) for r in rows]


async def _faqs_count(company_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                sa_text("SELECT count(*) FROM brain_faqs WHERE company_id = :cid"),
                {"cid": str(company_id)},
            )
        ).first()
    return int(row[0]) if row else 0


async def _chunks_count(company_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT count(*) FROM brain_chunks WHERE company_id = :cid AND superseded_at IS NULL"
                ),
                {"cid": str(company_id)},
            )
        ).first()
    return int(row[0]) if row else 0


async def _candidate_chunks(
    company_id: uuid.UUID, query_vec: list[float] | None, top_k: int = 3
) -> list[RetrievedChunk]:
    if query_vec is None:
        return []
    vec_lit = _vec_literal(query_vec)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                sa_text(
                    """
                    SELECT id, text, source_url, section,
                           1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                    FROM brain_chunks
                    WHERE company_id = :cid
                      AND superseded_at IS NULL
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:vec AS vector) ASC
                    LIMIT :k
                    """
                ),
                {"vec": vec_lit, "cid": str(company_id), "k": top_k},
            )
        ).all()
    return [
        RetrievedChunk(
            id=r[0],
            text=r[1],
            source_url=r[2],
            section=r[3],
            similarity=float(r[4]) if r[4] is not None else 0.0,
        )
        for r in rows
    ]


async def load_scoped_context(
    company_id: uuid.UUID,
    query: str,
    query_embedding: list[float] | None,
    *,
    intent_needs_chunks: bool = True,
    facts_top_k: int = 10,
    chunks_top_k: int = 3,
) -> CoachContext:
    persona = await _load_persona(company_id)
    all_facts = await _load_facts(company_id)
    sections = await _sections_summary(company_id)
    faqs_n = await _faqs_count(company_id)
    chunks_n = await _chunks_count(company_id)

    candidate_chunks: list[RetrievedChunk] = []
    if intent_needs_chunks and query_embedding is not None:
        try:
            candidate_chunks = await _candidate_chunks(
                company_id, query_embedding, top_k=chunks_top_k
            )
        except Exception as exc:
            logger.warning("coach context: chunk search failed: %s", exc)

    # Filter facts by keyword overlap with query (simple lexical heuristic)
    q_lower = (query or "").lower()
    q_tokens = set(re.findall(r"\w+", q_lower)) if q_lower else set()

    def _score(f: RetrievedFact) -> int:
        s = 0
        if f.subject and f.subject.lower() in q_lower:
            s += 3
        if f.evidence:
            ev_tokens = set(re.findall(r"\w+", f.evidence.lower()))
            s += len(q_tokens & ev_tokens)
        return s

    scored = sorted(all_facts, key=_score, reverse=True)
    top_facts = scored[:facts_top_k]
    if not q_tokens:
        # Without query keywords, prefer high-confidence canonical facts
        top_facts = all_facts[:facts_top_k]

    return CoachContext(
        company_id=company_id,
        persona=persona,
        facts=top_facts,
        candidate_chunks=candidate_chunks,
        sections_summary=sections,
        faqs_count=faqs_n,
        chunks_count=chunks_n,
        rules=list(persona.rules),
        negative_facts=list(persona.negative_facts),
    )


# Lazy import to avoid circular import at module load
import re  # noqa: E402
