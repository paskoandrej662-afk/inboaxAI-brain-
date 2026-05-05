from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.coach.applier import ApplyResult, apply_proposal
from app.core.coach.context_loader import load_scoped_context
from app.core.coach.intent_classifier import IntentClassification, classify_intent
from app.core.coach.proposal_generator import (
    Proposal,
    build_proposal_from_trivial,
    generate_proposal,
)
from app.core.coach.proposal_store import delete_proposal, get_proposal, store_proposal
from app.core.coach.state_reader import (
    AuditEntry,
    CoachState,
    get_coach_history,
    get_coach_state,
)
from app.core.coach.validators import ValidationResult, validate_proposal
from app.core.llm.openai_client import embed
from app.core.responder.handoff import detect_prompt_injection

logger = logging.getLogger(__name__)


@dataclass
class ProposeResult:
    proposal: Proposal | None
    validation: ValidationResult
    intent: str
    used_sonnet: bool
    latency_ms: int
    flags: dict[str, Any] = field(default_factory=dict)


async def _embed_or_none(query: str) -> list[float] | None:
    try:
        return await embed(query)
    except Exception as exc:
        logger.warning("coach: embed failed: %s", exc)
        return None


async def propose(
    company_id: uuid.UUID,
    query: str,
    history: list[dict] | None = None,
    session_id: str | None = None,
) -> ProposeResult:
    started = time.monotonic()
    flags: dict[str, Any] = {}

    if detect_prompt_injection(query):
        flags["prompt_injection_detected"] = True

    # Stage 1: classify intent (Haiku) and embed query in parallel
    intent_task = asyncio.create_task(classify_intent(query, history or []))
    embed_task = asyncio.create_task(_embed_or_none(query))

    intent: IntentClassification = await intent_task
    flags["intent"] = intent.primary_intent

    # Stage 2: trivial path or Sonnet
    used_sonnet = False
    if not intent.needs_sonnet and intent.trivial_payload:
        ctx = await load_scoped_context(
            company_id=company_id,
            query=query,
            query_embedding=None,
            intent_needs_chunks=False,
        )
        proposal = build_proposal_from_trivial(ctx, intent)
    else:
        if intent.primary_intent == "meta_question":
            latency_ms = int((time.monotonic() - started) * 1000)
            return ProposeResult(
                proposal=None,
                validation=ValidationResult(
                    is_valid=False,
                    errors=["Toto je informačná otázka, použi GET /v1/coach/state."],
                ),
                intent=intent.primary_intent,
                used_sonnet=False,
                latency_ms=latency_ms,
                flags=flags,
            )
        if intent.primary_intent == "unknown":
            latency_ms = int((time.monotonic() - started) * 1000)
            return ProposeResult(
                proposal=None,
                validation=ValidationResult(
                    is_valid=False,
                    errors=[
                        "Nerozumiem požiadavke. Skús prosím sformulovať jasnejšie čo chceš zmeniť."
                    ],
                ),
                intent=intent.primary_intent,
                used_sonnet=False,
                latency_ms=latency_ms,
                flags=flags,
            )

        used_sonnet = True
        query_vec = await embed_task
        ctx = await load_scoped_context(
            company_id=company_id,
            query=query,
            query_embedding=query_vec,
            intent_needs_chunks=intent.primary_intent
            in ("knowledge_correction", "fact_change", "chunk_add", "composite"),
        )
        proposal = await generate_proposal(ctx, intent, query, history)

    validation = validate_proposal(proposal, str(company_id), query)
    if not validation.is_valid:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProposeResult(
            proposal=None,
            validation=validation,
            intent=intent.primary_intent,
            used_sonnet=used_sonnet,
            latency_ms=latency_ms,
            flags=flags,
        )

    sanitized = validation.sanitized_proposal or proposal

    if not sanitized.tool_calls and not sanitized.needs_clarification:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProposeResult(
            proposal=None,
            validation=ValidationResult(
                is_valid=False,
                errors=[
                    "Žiadne aplikovateľné zmeny — možno už je nastavenie také, ako chceš."
                ],
                warnings=validation.warnings,
            ),
            intent=intent.primary_intent,
            used_sonnet=used_sonnet,
            latency_ms=latency_ms,
            flags=flags,
        )

    await store_proposal(sanitized)

    latency_ms = int((time.monotonic() - started) * 1000)
    return ProposeResult(
        proposal=sanitized,
        validation=validation,
        intent=intent.primary_intent,
        used_sonnet=used_sonnet,
        latency_ms=latency_ms,
        flags=flags,
    )


__all__ = [
    "propose",
    "apply_proposal",
    "ApplyResult",
    "ProposeResult",
    "Proposal",
    "ValidationResult",
    "AuditEntry",
    "CoachState",
    "get_coach_state",
    "get_coach_history",
    "get_proposal",
    "delete_proposal",
]
