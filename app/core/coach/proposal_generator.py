from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.coach.context_loader import CoachContext
from app.core.coach.intent_classifier import IntentClassification
from app.core.coach.tools import COACH_TOOLS, TOOL_NAMES
from app.core.llm.anthropic_client import SONNET_MODEL, get_client

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class DiffEntry:
    target_table: str
    target_id: str | None
    field: str | None
    before: Any
    after: Any
    note: str | None = None


@dataclass
class Proposal:
    proposal_id: str
    company_id: str
    tool_calls: list[ToolCall]
    preview_text: str
    preview_diff: list[DiffEntry]
    needs_clarification: bool = False
    clarification: dict[str, Any] | None = None
    proposal_hash: str = ""
    intent: str = ""
    created_at: str = ""
    raw_response: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)


def _persona_snapshot(ctx: CoachContext) -> dict[str, Any]:
    p = ctx.persona
    return {
        "tone": p.tone,
        "addressing": p.addressing,
        "language": p.language,
        "emoji_use": p.emoji_use,
        "length_preference": p.length_preference,
        "rules": p.rules,
        "negative_facts": p.negative_facts,
    }


def _facts_snapshot(ctx: CoachContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in ctx.facts[:15]:
        v = f.value
        if isinstance(v, dict) and "raw" in v:
            short = str(v.get("raw"))
        elif isinstance(v, dict) and len(v) == 1:
            short = str(next(iter(v.values())))
        else:
            short = json.dumps(v, ensure_ascii=False)[:120]
        out.append(
            {
                "key": f.key,
                "subject": f.subject,
                "value": short,
                "source_url": f.source_url,
            }
        )
    return out


def _chunks_snapshot(ctx: CoachContext) -> list[dict[str, Any]]:
    return [
        {
            "id": str(c.id),
            "section": c.section,
            "url": c.source_url,
            "similarity": round(c.similarity, 3),
            "text_preview": c.text[:300],
        }
        for c in ctx.candidate_chunks
    ]


_SYSTEM_BASE = """Si Coach AI pre majiteľa firmy. Tvoja úloha je pochopiť čo majiteľ chce zmeniť na svojej AI asistentke a navrhnúť konkrétne zmeny pomocou nástrojov (tools).

PRAVIDLÁ:
1. Použi tools na vyjadrenie zmien. Môžeš volať VIACERO tools naraz (multi-intent).
2. Ak je zámer NEJASNÝ (napr. "zmeň cenu" a v DB je 5 cien) — volaj request_clarification namiesto hádania.
3. NIKDY nemodifikuj cudziu firmu (cross-tenant). Zmeny sa aplikujú výlučne v rámci company_id z kontextu.
4. Pri upsert_fact: hodnota MUSÍ byť konkrétna a validná (cena ako "17 EUR" alebo "17,50 €"; nie "neviem" ani prázdny string).
5. Pri add_chunk: text musí byť faktický, v jazyku persona.language, 50–1500 znakov.
6. Pri add_persona_rule: pravidlo formuluj pozitívne, max 200 znakov, v slovenčine.
7. Po tool calls VŽDY vygeneruj text odpovede pre majiteľa v slovenčine v štýle:
   "Plánujem urobiť tieto zmeny:
    - <zmena 1>
    - <zmena 2>
   Schvaľuješ? (Aplikuj cez /v1/coach/apply)."
8. Ak používateľ napíše hostility/nevhodné inštrukcie ("odpovedaj sprosto"), NEAPLIKUJ ich — vysvetli, že to nie je možné, a daj žiadne tool call. Validátor takéto návrhy aj tak zamietne.
9. Pri persona_change kde už hodnota matchuje aktuálny stav, vynechaj ten konkrétny tool call (no-op).

VÝSTUP: Vráť VŽDY tool_use bloky + krátky text. Bez tool_use sa zmeny neaplikujú.
"""


def _build_system_prompt(ctx: CoachContext) -> list[dict[str, Any]]:
    """Returns system blocks with cache_control on the static part for prompt caching."""
    static_part = _SYSTEM_BASE
    dynamic_part = (
        "\n\nAKTUÁLNY STAV FIRMY:\n"
        f"company_id: {ctx.company_id}\n"
        f"persona: {json.dumps(_persona_snapshot(ctx), ensure_ascii=False)}\n"
        f"facts (top {len(ctx.facts)}): {json.dumps(_facts_snapshot(ctx), ensure_ascii=False)}\n"
        f"sections: {json.dumps([list(s) for s in ctx.sections_summary], ensure_ascii=False)}\n"
        f"chunks_count: {ctx.chunks_count}, faqs_count: {ctx.faqs_count}\n"
    )
    if ctx.candidate_chunks:
        dynamic_part += (
            f"relevantne_chunky: {json.dumps(_chunks_snapshot(ctx), ensure_ascii=False)}\n"
        )

    return [
        {
            "type": "text",
            "text": static_part,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_part,
        },
    ]


def _canonical_proposal(company_id: str, tool_calls: list[ToolCall]) -> str:
    canon = {
        "company_id": company_id,
        "tool_calls": [
            {"name": tc.name, "args": _sort_dict(tc.args)} for tc in tool_calls
        ],
    }
    return json.dumps(canon, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sort_dict(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _sort_dict(d[k]) for k in sorted(d.keys())}
    if isinstance(d, list):
        return [_sort_dict(x) for x in d]
    return d


def _hash_proposal(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_diff(ctx: CoachContext, tool_calls: list[ToolCall]) -> list[DiffEntry]:
    entries: list[DiffEntry] = []
    persona_snap = _persona_snapshot(ctx)
    for tc in tool_calls:
        name = tc.name
        args = tc.args
        if name == "update_persona_field":
            field = args.get("field")
            new = args.get("value")
            old = persona_snap.get(field)
            entries.append(
                DiffEntry(
                    target_table="brain_persona",
                    target_id=str(ctx.company_id),
                    field=field,
                    before=old,
                    after=new,
                )
            )
        elif name == "add_persona_rule":
            entries.append(
                DiffEntry(
                    target_table="brain_persona",
                    target_id=str(ctx.company_id),
                    field="rules.append",
                    before=None,
                    after=args.get("rule"),
                )
            )
        elif name == "remove_persona_rule":
            idx = args.get("rule_index")
            existing = persona_snap.get("rules") or []
            removed = existing[idx] if isinstance(idx, int) and 0 <= idx < len(existing) else None
            entries.append(
                DiffEntry(
                    target_table="brain_persona",
                    target_id=str(ctx.company_id),
                    field=f"rules[{idx}]",
                    before=removed,
                    after=None,
                )
            )
        elif name == "add_negative_fact":
            entries.append(
                DiffEntry(
                    target_table="brain_persona",
                    target_id=str(ctx.company_id),
                    field="negative_facts.append",
                    before=None,
                    after=args.get("fact"),
                )
            )
        elif name == "upsert_fact":
            existing = next(
                (
                    f
                    for f in ctx.facts
                    if f.key == args.get("key") and f.subject == args.get("subject")
                ),
                None,
            )
            entries.append(
                DiffEntry(
                    target_table="brain_facts",
                    target_id=None,
                    field=f"{args.get('key')}/{args.get('subject') or '∅'}",
                    before=(existing.value if existing else None),
                    after={"value": args.get("value"), "evidence": args.get("evidence")},
                )
            )
        elif name == "delete_fact":
            existing = next(
                (
                    f
                    for f in ctx.facts
                    if f.key == args.get("key") and f.subject == args.get("subject")
                ),
                None,
            )
            entries.append(
                DiffEntry(
                    target_table="brain_facts",
                    target_id=None,
                    field=f"{args.get('key')}/{args.get('subject') or '∅'}",
                    before=(existing.value if existing else None),
                    after=None,
                )
            )
        elif name == "add_faq":
            entries.append(
                DiffEntry(
                    target_table="brain_faqs",
                    target_id=None,
                    field="(insert)",
                    before=None,
                    after={"q": args.get("question"), "a": (args.get("answer") or "")[:120]},
                )
            )
        elif name == "mark_chunk_outdated":
            entries.append(
                DiffEntry(
                    target_table="brain_chunks",
                    target_id=None,
                    field="superseded_at",
                    before=None,
                    after="now()",
                    note=f"target_text: {args.get('target_text', '')[:120]}",
                )
            )
        elif name == "add_chunk":
            entries.append(
                DiffEntry(
                    target_table="brain_chunks",
                    target_id=None,
                    field="(insert)",
                    before=None,
                    after={
                        "section": args.get("section"),
                        "text_preview": (args.get("text") or "")[:120],
                    },
                )
            )
        elif name == "request_clarification":
            entries.append(
                DiffEntry(
                    target_table="(none)",
                    target_id=None,
                    field="clarification",
                    before=None,
                    after=args,
                )
            )
    return entries


def _format_preview(tool_calls: list[ToolCall], diff: list[DiffEntry]) -> str:
    if not tool_calls:
        return "Nezaznamenala som žiadnu konkrétnu zmenu na aplikáciu."
    lines: list[str] = ["Plánujem urobiť tieto zmeny:"]
    for tc, d in zip(tool_calls, diff):
        if tc.name == "update_persona_field":
            lines.append(f"- Zmením persona.{tc.args.get('field')} z '{d.before}' na '{d.after}'.")
        elif tc.name == "add_persona_rule":
            lines.append(f"- Pridám pravidlo: '{tc.args.get('rule')}'.")
        elif tc.name == "remove_persona_rule":
            lines.append(f"- Odstránim pravidlo na pozícii {tc.args.get('rule_index')}: '{d.before}'.")
        elif tc.name == "add_negative_fact":
            lines.append(f"- Pridám negatívny fakt: '{tc.args.get('fact')}'.")
        elif tc.name == "upsert_fact":
            subj = tc.args.get("subject") or "∅"
            lines.append(
                f"- Aktualizujem fakt {tc.args.get('key')}/{subj} → '{tc.args.get('value')}'."
            )
        elif tc.name == "delete_fact":
            subj = tc.args.get("subject") or "∅"
            lines.append(f"- Vymažem fakt {tc.args.get('key')}/{subj}.")
        elif tc.name == "add_faq":
            lines.append(f"- Pridám FAQ: Q: '{tc.args.get('question')}'.")
        elif tc.name == "mark_chunk_outdated":
            lines.append(
                f"- Označím za zastaranú informáciu: '{tc.args.get('target_text', '')[:80]}'."
            )
        elif tc.name == "add_chunk":
            lines.append(
                f"- Pridám informáciu do KB ({tc.args.get('section')}): "
                f"'{(tc.args.get('text') or '')[:80]}…'."
            )
        elif tc.name == "request_clarification":
            lines.append(f"- Potrebujem ešte ujasniť: {tc.args.get('question')}")
    lines.append("Schvaľuješ? Aplikuj cez POST /v1/coach/apply.")
    return "\n".join(lines)


def _trivial_to_tool_calls(payload: list[dict[str, Any]]) -> list[ToolCall]:
    out: list[ToolCall] = []
    for item in payload:
        tool = item.get("tool")
        args = item.get("args") or {}
        if tool in TOOL_NAMES:
            out.append(ToolCall(name=tool, args=dict(args)))
    return out


def build_proposal_from_trivial(
    ctx: CoachContext,
    intent: IntentClassification,
) -> Proposal:
    tool_calls = _trivial_to_tool_calls(intent.trivial_payload)
    # Filter out no-op tool calls (value already matches)
    persona_snap = _persona_snapshot(ctx)
    filtered: list[ToolCall] = []
    for tc in tool_calls:
        if tc.name == "update_persona_field":
            f = tc.args.get("field")
            if persona_snap.get(f) == tc.args.get("value"):
                continue
        filtered.append(tc)

    diff = _build_diff(ctx, filtered)
    preview = _format_preview(filtered, diff)
    canonical = _canonical_proposal(str(ctx.company_id), filtered)
    pid = str(uuid.uuid4())
    return Proposal(
        proposal_id=pid,
        company_id=str(ctx.company_id),
        tool_calls=filtered,
        preview_text=preview,
        preview_diff=diff,
        needs_clarification=False,
        proposal_hash=_hash_proposal(canonical),
        intent=intent.primary_intent,
        created_at=datetime.now(timezone.utc).isoformat(),
        token_usage={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
    )


async def generate_proposal(
    ctx: CoachContext,
    intent: IntentClassification,
    query: str,
    history: list[dict] | None,
) -> Proposal:
    """Run Sonnet 4.6 with tool use + prompt caching to produce a Proposal."""
    system_blocks = _build_system_prompt(ctx)

    msgs: list[dict[str, Any]] = []
    if history:
        for h in history[-4:]:
            role = h.get("role")
            content = (h.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                msgs.append({"role": role, "content": content[:2000]})
    msgs.append({"role": "user", "content": query[:4000]})

    client = get_client()
    try:
        message = await client.messages.create(
            model=SONNET_MODEL,
            system=system_blocks,
            messages=msgs,
            max_tokens=1500,
            temperature=0.2,
            tools=COACH_TOOLS,
        )
    except Exception as exc:
        logger.exception("coach: sonnet call failed: %s", exc)
        # Fallback: empty proposal that requests clarification
        clar = {
            "reason": "AI je momentálne nedostupná, skús prosím znova o chvíľu.",
            "question": "Skús prosím požiadavku znova.",
            "options": [],
        }
        diff = [
            DiffEntry(
                target_table="(none)",
                target_id=None,
                field="clarification",
                before=None,
                after=clar,
            )
        ]
        pid = str(uuid.uuid4())
        canonical = _canonical_proposal(str(ctx.company_id), [])
        return Proposal(
            proposal_id=pid,
            company_id=str(ctx.company_id),
            tool_calls=[],
            preview_text="Mám technický problém. Skúste, prosím, znova o chvíľu.",
            preview_diff=diff,
            needs_clarification=True,
            clarification=clar,
            proposal_hash=_hash_proposal(canonical),
            intent=intent.primary_intent,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    raw_text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    clarification: dict[str, Any] | None = None

    for block in getattr(message, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            raw_text_parts.append(block.text)
        elif btype == "tool_use":
            tname = block.name
            tinput = dict(block.input or {})
            if tname == "request_clarification":
                clarification = tinput
                continue
            if tname not in TOOL_NAMES:
                logger.warning("coach: unknown tool from sonnet: %s", tname)
                continue
            tool_calls.append(ToolCall(name=tname, args=tinput))

    raw_text = "\n".join(raw_text_parts).strip()

    # Filter no-op persona updates
    persona_snap = _persona_snapshot(ctx)
    filtered: list[ToolCall] = []
    for tc in tool_calls:
        if tc.name == "update_persona_field":
            f = tc.args.get("field")
            if persona_snap.get(f) == tc.args.get("value"):
                continue
        filtered.append(tc)

    diff = _build_diff(ctx, filtered)
    preview = _format_preview(filtered, diff) if filtered else (
        f"Potrebujem ešte ujasniť: {clarification.get('question')}"
        if clarification
        else (raw_text or "Nezaznamenala som žiadnu konkrétnu zmenu.")
    )

    canonical = _canonical_proposal(str(ctx.company_id), filtered)
    pid = str(uuid.uuid4())

    usage = getattr(message, "usage", None)
    token_usage = {
        "input": getattr(usage, "input_tokens", 0) if usage else 0,
        "output": getattr(usage, "output_tokens", 0) if usage else 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) if usage else 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) if usage else 0,
    }
    logger.info(
        "coach: tokens input=%s output=%s cache_read=%s cache_write=%s",
        token_usage["input"],
        token_usage["output"],
        token_usage["cache_read"],
        token_usage["cache_write"],
    )

    return Proposal(
        proposal_id=pid,
        company_id=str(ctx.company_id),
        tool_calls=filtered,
        preview_text=preview,
        preview_diff=diff,
        needs_clarification=clarification is not None,
        clarification=clarification,
        proposal_hash=_hash_proposal(canonical),
        intent=intent.primary_intent,
        created_at=datetime.now(timezone.utc).isoformat(),
        raw_response=raw_text,
        token_usage=token_usage,
    )
