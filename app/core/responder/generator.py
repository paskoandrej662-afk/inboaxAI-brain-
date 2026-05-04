from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.llm.anthropic_client import call_sonnet
from app.core.responder.retrieval import RetrievalContext

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.5

RESPOND_TOOL: dict[str, Any] = {
    "name": "respond",
    "description": (
        "Pošli odpoveď zákazníkovi. Vždy uveď confidence skóre a zoznam [n] indexov "
        "KB chunks ktoré si reálne použil pri tvorbe odpovede."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "response": {
                "type": "string",
                "description": "Text odpovede pre zákazníka v správnom jazyku a štýle.",
            },
            "confidence": {
                "type": "number",
                "description": "Tvoja istota odpovede 0.0–1.0",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "used_chunk_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Zoznam [n] indexov chunkov z KB ktoré si použil. Prázdny zoznam ak žiadne.",
            },
        },
        "required": ["response", "confidence", "used_chunk_indices"],
    },
}

FALLBACK_RESPONSE = (
    "Prepáčte, momentálne mám technický problém. Skúste to prosím o chvíľu znovu, "
    "alebo Vás čo najskôr kontaktuje kolega."
)


@dataclass
class ResponderOutput:
    response: str
    confidence: float
    used_chunk_ids: list[uuid.UUID] = field(default_factory=list)
    used_chunk_indices: list[int] = field(default_factory=list)
    cited_sources: list[str] = field(default_factory=list)
    needs_human: bool = False
    flags: dict[str, Any] = field(default_factory=dict)
    raw_text: str | None = None


def _extract_tool_use(message: Any) -> dict | None:
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and block.name == "respond":
            return block.input  # type: ignore[no-any-return]
    return None


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


async def generate_response(
    context: RetrievalContext,
    query: str,
    history: list[dict] | None,
    system_prompt: str,
) -> ResponderOutput:
    msgs: list[dict[str, Any]] = []
    if history:
        for h in history[-10:]:
            role = h.get("role")
            content = (h.get("content") or "").strip()
            if not content:
                continue
            if role in ("user", "assistant"):
                msgs.append({"role": role, "content": content[:2000]})
    msgs.append({"role": "user", "content": query[:4000]})

    flags: dict[str, Any] = {}
    if context.no_kb_match:
        flags["no_kb_match"] = True

    try:
        message = await call_sonnet(
            system=system_prompt,
            messages=msgs,
            max_tokens=500,
            temperature=0.3,
            tools=[RESPOND_TOOL],
            tool_choice={"type": "tool", "name": "respond"},
        )
    except Exception as exc:
        logger.exception("generator: sonnet call failed: %s", exc)
        flags["llm_error"] = str(exc)[:200]
        return ResponderOutput(
            response=FALLBACK_RESPONSE,
            confidence=0.0,
            needs_human=True,
            flags=flags,
        )

    tool_input = _extract_tool_use(message)
    raw_text = _extract_text(message)

    if not tool_input:
        # Sonnet didn't emit tool use — fall back to text
        logger.warning("generator: no tool_use in response; using text fallback")
        flags["no_tool_use"] = True
        text = raw_text or FALLBACK_RESPONSE
        return ResponderOutput(
            response=text,
            confidence=0.3,
            needs_human=True,
            flags=flags,
            raw_text=raw_text,
        )

    response_text = str(tool_input.get("response", "")).strip() or FALLBACK_RESPONSE
    try:
        confidence = float(tool_input.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    raw_indices = tool_input.get("used_chunk_indices") or []
    indices: list[int] = []
    for x in raw_indices:
        try:
            indices.append(int(x))
        except (TypeError, ValueError):
            continue

    # Map [n] (1-based) back to chunk IDs; ignore bad indices but flag.
    used_ids: list[uuid.UUID] = []
    cited: list[str] = []
    invalid_idx: list[int] = []
    for n in indices:
        if 1 <= n <= len(context.chunks):
            ch = context.chunks[n - 1]
            used_ids.append(ch.id)
            if ch.source_url:
                cited.append(ch.source_url)
        else:
            invalid_idx.append(n)
    if invalid_idx:
        flags["invalid_chunk_indices"] = invalid_idx
        # Penalize confidence for hallucinated citation
        confidence = min(confidence, 0.4)

    # Dedupe sources preserving order
    seen: set[str] = set()
    cited_unique: list[str] = []
    for u in cited:
        if u not in seen:
            seen.add(u)
            cited_unique.append(u)

    needs_human = confidence < LOW_CONFIDENCE_THRESHOLD or context.no_kb_match
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        flags["low_confidence"] = True

    return ResponderOutput(
        response=response_text,
        confidence=confidence,
        used_chunk_ids=used_ids,
        used_chunk_indices=indices,
        cited_sources=cited_unique,
        needs_human=needs_human,
        flags=flags,
        raw_text=raw_text or None,
    )
