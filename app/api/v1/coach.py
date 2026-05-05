from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.core.coach import (
    apply_proposal,
    delete_proposal,
    get_coach_history,
    get_coach_state,
    get_proposal,
    propose,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/coach", tags=["coach"])


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class ProposeRequest(BaseModel):
    company_id: str
    query: str = Field(..., min_length=1, max_length=4000)
    history: list[HistoryMessage] = Field(default_factory=list)
    session_id: str | None = None

    @field_validator("company_id")
    @classmethod
    def _v_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as exc:
            raise ValueError("company_id must be a valid UUID") from exc
        return v

    @field_validator("history")
    @classmethod
    def _v_hist(cls, v: list[HistoryMessage]) -> list[HistoryMessage]:
        if len(v) > 10:
            raise ValueError("history max 10 items")
        return v


class ProposeResponse(BaseModel):
    proposal_id: str | None
    intent: str
    used_sonnet: bool
    preview_text: str
    preview_diff: list[dict[str, Any]]
    needs_clarification: bool
    clarification: dict[str, Any] | None
    errors: list[str]
    warnings: list[str]
    latency_ms: int


class ApplyRequest(BaseModel):
    proposal_id: str
    actor: str = "owner"

    @field_validator("proposal_id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as exc:
            raise ValueError("proposal_id must be a valid UUID") from exc
        return v


class ApplyResponse(BaseModel):
    success: bool
    audit_log_id: str | None
    applied_changes_count: int
    idempotent_replay: bool
    error: str | None
    diff_after: dict[str, Any]


class CancelRequest(BaseModel):
    proposal_id: str


class StateResponse(BaseModel):
    company_id: str
    persona: dict[str, Any]
    facts_count: int
    chunks_count: int
    faqs_count: int
    recent_changes: list[dict[str, Any]]


class HistoryResponse(BaseModel):
    changes: list[dict[str, Any]]


@router.post("", response_model=ProposeResponse)
async def coach_propose(body: ProposeRequest) -> ProposeResponse:
    company_id = uuid.UUID(body.company_id)
    history = [{"role": h.role, "content": h.content} for h in body.history]

    result = await propose(
        company_id=company_id,
        query=body.query,
        history=history,
        session_id=body.session_id,
    )

    if result.proposal is None:
        return ProposeResponse(
            proposal_id=None,
            intent=result.intent,
            used_sonnet=result.used_sonnet,
            preview_text="",
            preview_diff=[],
            needs_clarification=False,
            clarification=None,
            errors=result.validation.errors,
            warnings=result.validation.warnings,
            latency_ms=result.latency_ms,
        )

    p = result.proposal
    return ProposeResponse(
        proposal_id=p.proposal_id,
        intent=result.intent,
        used_sonnet=result.used_sonnet,
        preview_text=p.preview_text,
        preview_diff=[dataclasses.asdict(d) for d in p.preview_diff],
        needs_clarification=p.needs_clarification,
        clarification=p.clarification,
        errors=[],
        warnings=result.validation.warnings,
        latency_ms=result.latency_ms,
    )


@router.post("/apply", response_model=ApplyResponse)
async def coach_apply(body: ApplyRequest) -> ApplyResponse:
    result = await apply_proposal(body.proposal_id, actor=body.actor or "owner")
    return ApplyResponse(
        success=result.success,
        audit_log_id=result.audit_log_id,
        applied_changes_count=result.applied_changes_count,
        idempotent_replay=result.idempotent_replay,
        error=result.error,
        diff_after=result.diff_after,
    )


@router.post("/cancel")
async def coach_cancel(body: CancelRequest) -> dict[str, bool]:
    proposal = await get_proposal(body.proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found or expired")
    await delete_proposal(body.proposal_id, proposal.company_id)
    return {"cancelled": True}


@router.get("/state", response_model=StateResponse)
async def coach_state(company_id: str = Query(...)) -> StateResponse:
    try:
        cid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="company_id must be a UUID")
    state = await get_coach_state(cid)
    return StateResponse(
        company_id=state.company_id,
        persona=state.persona,
        facts_count=state.facts_count,
        chunks_count=state.chunks_count,
        faqs_count=state.faqs_count,
        recent_changes=[dataclasses.asdict(c) for c in state.recent_changes],
    )


@router.get("/history", response_model=HistoryResponse)
async def coach_history(
    company_id: str = Query(...), limit: int = Query(50, ge=1, le=200)
) -> HistoryResponse:
    try:
        cid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="company_id must be a UUID")
    entries = await get_coach_history(cid, limit=limit)
    return HistoryResponse(changes=[dataclasses.asdict(e) for e in entries])
