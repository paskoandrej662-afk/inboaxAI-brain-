from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text

from app.db import AsyncSessionLocal, cache_get, cache_set

logger = logging.getLogger(__name__)

PERSONA_TTL = 300
FACTS_TTL = 300
DEFAULT_TOP_K = 5
SIMILARITY_THRESHOLD = 0.3


@dataclass
class RetrievedChunk:
    id: uuid.UUID
    text: str
    source_url: str | None
    section: str | None
    similarity: float


@dataclass
class RetrievedFact:
    key: str
    subject: str | None
    value: dict[str, Any]
    evidence: str | None
    source_url: str | None


@dataclass
class Persona:
    company_id: uuid.UUID
    tone: str = "friendly"
    addressing: str = "tykanie"
    language: str = "sk"
    emoji_use: str = "sometimes"
    length_preference: str = "medium"
    rules: list[str] = field(default_factory=list)
    negative_facts: list[str] = field(default_factory=list)


@dataclass
class RetrievalContext:
    company_id: uuid.UUID
    chunks: list[RetrievedChunk]
    facts: list[RetrievedFact]
    persona: Persona
    media_triggers: list[Any] = field(default_factory=list)
    no_kb_match: bool = False


def _vec_literal(vector: list[float]) -> str:
    """Convert a Python list to pgvector textual format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


async def _load_persona(company_id: uuid.UUID) -> Persona:
    cache_key = f"persona:{company_id}"
    cached = await cache_get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            return Persona(
                company_id=company_id,
                tone=data.get("tone", "friendly"),
                addressing=data.get("addressing", "tykanie"),
                language=data.get("language", "sk"),
                emoji_use=data.get("emoji_use", "sometimes"),
                length_preference=data.get("length_preference", "medium"),
                rules=data.get("rules") or [],
                negative_facts=data.get("negative_facts") or [],
            )
        except Exception as exc:
            logger.warning("persona cache parse failed: %s", exc)

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT tone, addressing, language, emoji_use, length_preference, rules, negative_facts "
                    "FROM brain_persona WHERE company_id = :cid LIMIT 1"
                ),
                {"cid": str(company_id)},
            )
        ).first()

    if row is None:
        persona = Persona(company_id=company_id)
    else:
        persona = Persona(
            company_id=company_id,
            tone=row[0] or "friendly",
            addressing=row[1] or "tykanie",
            language=row[2] or "sk",
            emoji_use=row[3] or "sometimes",
            length_preference=row[4] or "medium",
            rules=list(row[5]) if row[5] else [],
            negative_facts=list(row[6]) if row[6] else [],
        )

    await cache_set(
        cache_key,
        json.dumps(
            {
                "tone": persona.tone,
                "addressing": persona.addressing,
                "language": persona.language,
                "emoji_use": persona.emoji_use,
                "length_preference": persona.length_preference,
                "rules": persona.rules,
                "negative_facts": persona.negative_facts,
            },
            ensure_ascii=False,
        ),
        PERSONA_TTL,
    )
    return persona


async def _load_facts(company_id: uuid.UUID) -> list[RetrievedFact]:
    cache_key = f"facts:{company_id}"
    cached = await cache_get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            return [
                RetrievedFact(
                    key=f["key"],
                    subject=f.get("subject"),
                    value=f.get("value") or {},
                    evidence=f.get("evidence"),
                    source_url=f.get("source_url"),
                )
                for f in data
            ]
        except Exception as exc:
            logger.warning("facts cache parse failed: %s", exc)

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                sa_text(
                    "SELECT key, subject, value, evidence, source_url, confidence "
                    "FROM brain_facts WHERE company_id = :cid "
                    "ORDER BY confidence DESC, updated_at DESC LIMIT 50"
                ),
                {"cid": str(company_id)},
            )
        ).all()

    facts = [
        RetrievedFact(
            key=r[0],
            subject=r[1],
            value=r[2] if isinstance(r[2], dict) else (json.loads(r[2]) if r[2] else {}),
            evidence=r[3],
            source_url=r[4],
        )
        for r in rows
    ]
    await cache_set(
        cache_key,
        json.dumps(
            [
                {
                    "key": f.key,
                    "subject": f.subject,
                    "value": f.value,
                    "evidence": f.evidence,
                    "source_url": f.source_url,
                }
                for f in facts
            ],
            ensure_ascii=False,
        ),
        FACTS_TTL,
    )
    return facts


async def _vector_search(
    company_id: uuid.UUID, query_vec: list[float], top_k: int
) -> list[RetrievedChunk]:
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


async def retrieve_context(
    company_id: uuid.UUID,
    query: str,
    query_embedding: list[float],
    top_k: int = DEFAULT_TOP_K,
) -> RetrievalContext:
    persona = await _load_persona(company_id)
    facts = await _load_facts(company_id)
    chunks = await _vector_search(company_id, query_embedding, top_k)

    no_kb_match = not chunks or all(c.similarity < SIMILARITY_THRESHOLD for c in chunks)
    if no_kb_match:
        logger.info(
            "retrieval: no KB match for company=%s query=%r (top_sim=%.3f)",
            company_id,
            query[:80],
            chunks[0].similarity if chunks else 0.0,
        )

    return RetrievalContext(
        company_id=company_id,
        chunks=chunks,
        facts=facts,
        persona=persona,
        no_kb_match=no_kb_match,
    )
