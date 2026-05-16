"""HDS-v3 messenger RAG endpoint.

POST /v3/respond — invoke the HDS-v3 Responder for a single message.
Returns the AI reply plus diagnostic metadata (tokens, cost, chunks used).
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.extractors.hds_v3.responder import HDSv3Responder
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3", tags=["hds_v3"])


class RespondRequest(BaseModel):
    company_id: uuid.UUID
    message: str = Field(..., min_length=1, max_length=4000)
    customer_id: str | None = Field(default=None, max_length=200)


class RespondResponse(BaseModel):
    success: bool
    reply: str | None = None
    persona_version: int | None = None
    chunks_used: int = 0
    memory_summary_len: int = 0
    memory_recent_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    error: str | None = None


@router.post("/respond", response_model=RespondResponse)
async def respond(req: RespondRequest) -> RespondResponse:
    responder = HDSv3Responder()
    async with AsyncSessionLocal() as session:
        try:
            result = await responder.respond(
                session=session,
                company_id=req.company_id,
                message=req.message,
                customer_id=req.customer_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("v3/respond failed")
            raise HTTPException(status_code=500, detail=f"internal: {str(e)[:200]}")

    return RespondResponse(
        success=result.success,
        reply=result.reply_text,
        persona_version=result.persona_version,
        chunks_used=result.chunks_used,
        memory_summary_len=result.memory_summary_len,
        memory_recent_count=result.memory_recent_count,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        duration_sec=result.duration_sec,
        error=result.error,
    )
