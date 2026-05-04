from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.responder import FALLBACK_RESPONSE, ResponderResult, respond

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["respond"])

ALLOWED_CHANNELS = ("messenger", "instagram", "gmail", "whatsapp", "sms", "web")
TOTAL_TIMEOUT_S = 35.0


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class RespondRequest(BaseModel):
    company_id: str
    channel: str
    customer_id: str = Field(..., max_length=200)
    message: str = Field(..., max_length=4000)
    history: list[HistoryMessage] = Field(default_factory=list)

    @field_validator("company_id")
    @classmethod
    def _validate_company_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as exc:
            raise ValueError("company_id must be a valid UUID") from exc
        return v

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, v: str) -> str:
        if v not in ALLOWED_CHANNELS:
            raise ValueError(f"channel must be one of {ALLOWED_CHANNELS}")
        return v

    @field_validator("message")
    @classmethod
    def _validate_message(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("message must not be empty")
        return v

    @field_validator("history")
    @classmethod
    def _validate_history(cls, v: list[HistoryMessage]) -> list[HistoryMessage]:
        if len(v) > 10:
            raise ValueError("history must have at most 10 items")
        return v


class RespondResponse(BaseModel):
    response: str
    confidence: float
    cited_sources: list[str]
    route: str
    needs_human: bool
    latency_ms: int


@router.post("/respond", response_model=RespondResponse)
async def respond_endpoint(body: RespondRequest) -> RespondResponse:
    started = time.monotonic()
    company_id = uuid.UUID(body.company_id)
    history = [{"role": h.role, "content": h.content} for h in body.history]

    try:
        result: ResponderResult = await asyncio.wait_for(
            respond(
                company_id=company_id,
                query=body.message,
                history=history,
                channel=body.channel,
                customer_id=body.customer_id,
            ),
            timeout=TOTAL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("respond: total timeout for company=%s", company_id)
        elapsed = int((time.monotonic() - started) * 1000)
        return RespondResponse(
            response=FALLBACK_RESPONSE,
            confidence=0.0,
            cited_sources=[],
            route="qa",
            needs_human=True,
            latency_ms=elapsed,
        )
    except Exception as exc:
        logger.exception("respond: unexpected error: %s", exc)
        elapsed = int((time.monotonic() - started) * 1000)
        return RespondResponse(
            response=FALLBACK_RESPONSE,
            confidence=0.0,
            cited_sources=[],
            route="qa",
            needs_human=True,
            latency_ms=elapsed,
        )

    return RespondResponse(
        response=result.response,
        confidence=result.confidence,
        cited_sources=result.cited_sources,
        route=result.route,
        needs_human=result.needs_human,
        latency_ms=result.latency_ms,
    )
