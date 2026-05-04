from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text as sa_text

from app.core.llm.anthropic_client import HAIKU_MODEL, SONNET_MODEL
from app.core.llm.openai_client import embed
from app.core.responder.generator import (
    FALLBACK_RESPONSE,
    ResponderOutput,
    generate_response,
)
from app.core.responder.handoff import detect_handoff_signals, detect_prompt_injection
from app.core.responder.prompt_builder import build_system_prompt
from app.core.responder.retrieval import RetrievalContext, retrieve_context
from app.core.responder.router import classify_route
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "text-embedding-3-small"


@dataclass
class ResponderResult:
    response: str
    confidence: float
    cited_sources: list[str]
    route: str
    needs_human: bool
    latency_ms: int
    flags: dict[str, Any] = field(default_factory=dict)


def _safe_default_persona_company_name(company_id: uuid.UUID) -> str:
    return "Vaša firma"


async def _route_or_fallback(query: str, history: list[dict] | None) -> str:
    try:
        return await classify_route(query, history or [])
    except Exception as exc:
        logger.warning("router failed, defaulting to qa: %s", exc)
        return "qa"


async def _embed_or_fallback(query: str) -> list[float] | None:
    try:
        return await embed(query)
    except Exception as exc:
        logger.warning("embed failed: %s", exc)
        return None


async def _empty_context(company_id: uuid.UUID) -> RetrievalContext:
    """Return a minimal context when embedding fails (so we still call generator)."""
    from app.core.responder.retrieval import Persona

    return RetrievalContext(
        company_id=company_id,
        chunks=[],
        facts=[],
        persona=Persona(company_id=company_id),
        no_kb_match=True,
    )


async def _insert_audit_log(
    company_id: uuid.UUID,
    channel: str | None,
    customer_id: str | None,
    query: str,
    route: str,
    retrieved_chunk_ids: list[uuid.UUID],
    retrieved_facts_summary: list[dict],
    output: ResponderOutput,
    latency_ms: int,
) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_text(
                    """
                    INSERT INTO audit_logs (
                        company_id, channel, customer_id, query, route,
                        retrieved_chunk_ids, retrieved_facts, response, confidence,
                        cited_sources, used_chunk_ids, latency_ms, model_versions,
                        needs_human, flags
                    ) VALUES (
                        :company_id, :channel, :customer_id, :query, :route,
                        :retrieved_chunk_ids, CAST(:retrieved_facts AS jsonb), :response, :confidence,
                        :cited_sources, :used_chunk_ids, :latency_ms, CAST(:model_versions AS jsonb),
                        :needs_human, CAST(:flags AS jsonb)
                    )
                    """
                ),
                {
                    "company_id": str(company_id),
                    "channel": channel,
                    "customer_id": customer_id,
                    "query": query[:8000],
                    "route": route,
                    "retrieved_chunk_ids": [str(i) for i in retrieved_chunk_ids],
                    "retrieved_facts": json.dumps(
                        retrieved_facts_summary, ensure_ascii=False
                    ),
                    "response": output.response[:8000],
                    "confidence": output.confidence,
                    "cited_sources": output.cited_sources,
                    "used_chunk_ids": [str(i) for i in output.used_chunk_ids],
                    "latency_ms": latency_ms,
                    "model_versions": json.dumps(
                        {
                            "router": HAIKU_MODEL,
                            "generator": SONNET_MODEL,
                            "embedding": EMBEDDING_MODEL_NAME,
                        }
                    ),
                    "needs_human": output.needs_human,
                    "flags": json.dumps(output.flags, ensure_ascii=False),
                },
            )
            await session.commit()
    except Exception as exc:
        logger.exception("audit_log insert failed: %s", exc)


async def respond(
    company_id: uuid.UUID,
    query: str,
    history: list[dict] | None,
    channel: str | None,
    customer_id: str | None,
) -> ResponderResult:
    """Full pipeline: route + embed (parallel) → retrieve → prompt → generate → audit."""
    started = time.monotonic()

    injection_detected = detect_prompt_injection(query)
    if injection_detected:
        logger.info(
            "prompt injection signal for company=%s customer=%s query=%r",
            company_id,
            customer_id,
            query[:120],
        )

    # 1. Parallel: route classifier + query embedding
    route_task = asyncio.create_task(_route_or_fallback(query, history))
    embed_task = asyncio.create_task(_embed_or_fallback(query))
    route, query_vec = await asyncio.gather(route_task, embed_task)

    # 2. Retrieval (depends on embedding)
    if query_vec is None:
        context = await _empty_context(company_id)
    else:
        try:
            context = await retrieve_context(company_id, query, query_vec, top_k=4)
        except Exception as exc:
            logger.exception("retrieval failed: %s", exc)
            context = await _empty_context(company_id)

    # 3. Prompt
    system_prompt = build_system_prompt(
        persona=context.persona,
        chunks=context.chunks,
        facts=context.facts,
        history=history,
        current_time=datetime.now(timezone.utc),
        company_name=_safe_default_persona_company_name(company_id),
        route=route,
    )

    # 4. Generation
    output = await generate_response(context, query, history, system_prompt)

    # 5. Handoff overlay
    if detect_handoff_signals(query, route):
        output.needs_human = True
        output.flags["handoff_signal"] = True
    if injection_detected:
        output.flags["prompt_injection_detected"] = True
    if context.no_kb_match and "no_kb_match" not in output.flags:
        output.flags["no_kb_match"] = True

    # If the model produced no grounded citations on a factual route, flag for human.
    if route in ("qa", "unknown") and not output.used_chunk_ids:
        output.needs_human = True
        output.flags.setdefault("no_grounded_citation", True)

    latency_ms = int((time.monotonic() - started) * 1000)

    # 6. Audit log (best-effort)
    facts_summary = [
        {
            "key": f.key,
            "subject": f.subject,
            "source_url": f.source_url,
        }
        for f in context.facts[:20]
    ]
    await _insert_audit_log(
        company_id=company_id,
        channel=channel,
        customer_id=customer_id,
        query=query,
        route=route,
        retrieved_chunk_ids=[c.id for c in context.chunks],
        retrieved_facts_summary=facts_summary,
        output=output,
        latency_ms=latency_ms,
    )

    return ResponderResult(
        response=output.response,
        confidence=output.confidence,
        cited_sources=output.cited_sources,
        route=route,
        needs_human=output.needs_human,
        latency_ms=latency_ms,
        flags=output.flags,
    )


__all__ = ["respond", "ResponderResult", "FALLBACK_RESPONSE"]
