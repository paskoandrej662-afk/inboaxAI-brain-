from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from app.core.coach.proposal_generator import DiffEntry, Proposal, ToolCall
from app.db import get_redis_client

logger = logging.getLogger(__name__)

PROPOSAL_TTL_SECONDS = 600


def _proposal_to_dict(p: Proposal) -> dict[str, Any]:
    return {
        "proposal_id": p.proposal_id,
        "company_id": p.company_id,
        "tool_calls": [{"name": tc.name, "args": tc.args} for tc in p.tool_calls],
        "preview_text": p.preview_text,
        "preview_diff": [dataclasses.asdict(d) for d in p.preview_diff],
        "needs_clarification": p.needs_clarification,
        "clarification": p.clarification,
        "proposal_hash": p.proposal_hash,
        "intent": p.intent,
        "created_at": p.created_at,
        "raw_response": p.raw_response,
        "token_usage": p.token_usage,
    }


def _dict_to_proposal(d: dict[str, Any]) -> Proposal:
    return Proposal(
        proposal_id=d["proposal_id"],
        company_id=d["company_id"],
        tool_calls=[ToolCall(name=tc["name"], args=tc.get("args") or {}) for tc in d.get("tool_calls", [])],
        preview_text=d.get("preview_text", ""),
        preview_diff=[DiffEntry(**de) for de in d.get("preview_diff", [])],
        needs_clarification=bool(d.get("needs_clarification", False)),
        clarification=d.get("clarification"),
        proposal_hash=d.get("proposal_hash", ""),
        intent=d.get("intent", ""),
        created_at=d.get("created_at", ""),
        raw_response=d.get("raw_response"),
        token_usage=d.get("token_usage") or {},
    )


def _key(proposal_id: str) -> str:
    return f"coach:proposal:{proposal_id}"


def _company_set_key(company_id: str) -> str:
    return f"coach:proposals:{company_id}"


async def store_proposal(proposal: Proposal) -> None:
    client = await get_redis_client()
    payload = json.dumps(_proposal_to_dict(proposal), ensure_ascii=False)
    pipe = client.pipeline()
    pipe.set(_key(proposal.proposal_id), payload, ex=PROPOSAL_TTL_SECONDS)
    pipe.sadd(_company_set_key(proposal.company_id), proposal.proposal_id)
    pipe.expire(_company_set_key(proposal.company_id), PROPOSAL_TTL_SECONDS * 2)
    await pipe.execute()


async def get_proposal(proposal_id: str) -> Proposal | None:
    client = await get_redis_client()
    raw = await client.get(_key(proposal_id))
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return _dict_to_proposal(d)
    except Exception as exc:
        logger.warning("proposal_store: parse failed for %s: %s", proposal_id, exc)
        return None


async def delete_proposal(proposal_id: str, company_id: str | None = None) -> None:
    client = await get_redis_client()
    pipe = client.pipeline()
    pipe.delete(_key(proposal_id))
    if company_id:
        pipe.srem(_company_set_key(company_id), proposal_id)
    await pipe.execute()


async def list_company_proposals(company_id: str) -> list[str]:
    client = await get_redis_client()
    members = await client.smembers(_company_set_key(company_id))
    return sorted(members or [])
