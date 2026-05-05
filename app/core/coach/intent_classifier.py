from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.llm.anthropic_client import call_haiku

logger = logging.getLogger(__name__)

PrimaryIntent = Literal[
    "persona_change",
    "fact_change",
    "rule_add",
    "rule_remove",
    "negative_fact_add",
    "faq_add",
    "knowledge_correction",
    "chunk_add",
    "composite",
    "meta_question",
    "unknown",
]

VALID_INTENTS: tuple[str, ...] = (
    "persona_change",
    "fact_change",
    "rule_add",
    "rule_remove",
    "negative_fact_add",
    "faq_add",
    "knowledge_correction",
    "chunk_add",
    "composite",
    "meta_question",
    "unknown",
)

VALID_TONES = {"casual", "formal", "friendly", "professional"}
VALID_ADDRESSING = {"tykanie", "vykanie"}
VALID_EMOJI = {"never", "sometimes", "often"}
VALID_LENGTH = {"short", "medium", "long"}


@dataclass
class IntentClassification:
    primary_intent: PrimaryIntent
    needs_sonnet: bool
    trivial_payload: list[dict[str, Any]] = field(default_factory=list)
    raw: str = ""


_SYSTEM = """Si intent classifier pre Coach Mode. Majiteľ firmy ti napíše prirodzeným jazykom čo chce zmeniť na svojej AI a ty určíš typ zmeny.

Vráť IBA validný JSON v tomto formáte (žiadny iný text):
{"primary_intent": "<typ>", "needs_sonnet": true|false, "trivial_payload": [...]}

Typy intentov:
- persona_change: zmena štýlu (tonalita, tykanie/vykanie, emoji, dĺžka odpovedí)
- fact_change: zmena ceny, telefónu, adresy, hodín, alebo iného faktu
- rule_add: pridanie pozitívneho pravidla správania
- rule_remove: odstránenie existujúceho pravidla
- negative_fact_add: pridanie "čo firma NEROBÍ"
- faq_add: pridanie otázky a odpovede
- knowledge_correction: niečo v KB je zastarané/nesprávne
- chunk_add: pridanie novej informácie do KB
- composite: viacero zmien naraz
- meta_question: otázka o aktuálnom stave (nie zmena)
- unknown: nejasné

needs_sonnet=false IBA pre TRIVIÁLNE persona zmeny — keď query je čisto o tone/addressing/emoji/length BEZ ďalšieho kontextu. Vtedy vyplň trivial_payload priamym tool call:
- "buď casual" → {"tool":"update_persona_field","args":{"field":"tone","value":"casual"}}
- "tykaj" → {"tool":"update_persona_field","args":{"field":"addressing","value":"tykanie"}}
- "vykaj" → {"tool":"update_persona_field","args":{"field":"addressing","value":"vykanie"}}
- "viac smajlíkov" → {"tool":"update_persona_field","args":{"field":"emoji_use","value":"often"}}
- "menej smajlíkov" / "žiadne emoji" → {"tool":"update_persona_field","args":{"field":"emoji_use","value":"never"}}
- "kratšie odpovede" → {"tool":"update_persona_field","args":{"field":"length_preference","value":"short"}}
- "dlhšie odpovede" → {"tool":"update_persona_field","args":{"field":"length_preference","value":"long"}}

Príklady:
"buď casual" → {"primary_intent":"persona_change","needs_sonnet":false,"trivial_payload":[{"tool":"update_persona_field","args":{"field":"tone","value":"casual"}}]}
"buď viac casual a začni tykať" → {"primary_intent":"persona_change","needs_sonnet":false,"trivial_payload":[{"tool":"update_persona_field","args":{"field":"tone","value":"casual"}},{"tool":"update_persona_field","args":{"field":"addressing","value":"tykanie"}}]}
"zmeň cenu strihu na 17 eur" → {"primary_intent":"fact_change","needs_sonnet":true,"trivial_payload":[]}
"pridaj pravidlo nikdy nesľubuj zľavy" → {"primary_intent":"rule_add","needs_sonnet":true,"trivial_payload":[]}
"pridaj pravidlo nikdy nesľubuj zľavy a buď stručnejší" → {"primary_intent":"composite","needs_sonnet":true,"trivial_payload":[]}
"informácia o vašich zľavách je už zastaraná" → {"primary_intent":"knowledge_correction","needs_sonnet":true,"trivial_payload":[]}
"pridaj otázku Robíte rezervácie online? s odpoveďou Áno cez náš web." → {"primary_intent":"faq_add","needs_sonnet":true,"trivial_payload":[]}
"my nerobíme farbenie vlasov" → {"primary_intent":"negative_fact_add","needs_sonnet":true,"trivial_payload":[]}
"aký je aktuálny tón odpovedí?" → {"primary_intent":"meta_question","needs_sonnet":false,"trivial_payload":[]}
"asdf" → {"primary_intent":"unknown","needs_sonnet":false,"trivial_payload":[]}

DÔLEŽITÉ: Ak persona zmena obsahuje aj iný typ (napr. fact, rule), je to "composite" a needs_sonnet=true.
"""

_TRIVIAL_TONE_RE = re.compile(
    r"\b(casual|formal|friendly|professional|neformaln|formaln|prateľ|profesion)",
    re.IGNORECASE,
)


def _coerce_trivial_payload(items: Any) -> list[dict[str, Any]]:
    """Sanitize Haiku's trivial_payload — only allow update_persona_field with whitelisted values."""
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        args = item.get("args") or {}
        if tool != "update_persona_field":
            continue
        field = args.get("field")
        value = args.get("value")
        if field == "tone" and value in VALID_TONES:
            out.append({"tool": tool, "args": {"field": "tone", "value": value}})
        elif field == "addressing" and value in VALID_ADDRESSING:
            out.append({"tool": tool, "args": {"field": "addressing", "value": value}})
        elif field == "emoji_use" and value in VALID_EMOJI:
            out.append({"tool": tool, "args": {"field": "emoji_use", "value": value}})
        elif field == "length_preference" and value in VALID_LENGTH:
            out.append({"tool": tool, "args": {"field": "length_preference", "value": value}})
    # Dedupe by (field) — keep last
    seen: dict[str, dict[str, Any]] = {}
    for it in out:
        seen[it["args"]["field"]] = it
    return list(seen.values())


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    # Strip code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, count=1).strip()
        text = re.sub(r"```$", "", text).strip()
    # Try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to extract first balanced object
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


async def classify_intent(query: str, history: list[dict] | None = None) -> IntentClassification:
    if not query or not query.strip():
        return IntentClassification(primary_intent="unknown", needs_sonnet=False, raw="")

    msgs: list[dict] = []
    if history:
        for h in history[-2:]:
            role = h.get("role")
            content = (h.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                msgs.append({"role": role, "content": content[:500]})
    msgs.append({"role": "user", "content": query.strip()[:1000]})

    try:
        raw = await call_haiku(_SYSTEM, msgs, max_tokens=300, temperature=0.0)
    except Exception as exc:
        logger.warning("intent_classifier: haiku failed: %s", exc)
        return IntentClassification(primary_intent="unknown", needs_sonnet=True, raw=str(exc))

    parsed = _extract_json(raw)
    if not parsed:
        logger.warning("intent_classifier: failed to parse JSON from %r", raw[:200])
        return IntentClassification(primary_intent="unknown", needs_sonnet=True, raw=raw)

    intent = parsed.get("primary_intent") or "unknown"
    if intent not in VALID_INTENTS:
        intent = "unknown"

    needs_sonnet = bool(parsed.get("needs_sonnet", True))
    trivial_payload = _coerce_trivial_payload(parsed.get("trivial_payload"))

    # Safety: if we say needs_sonnet=False but have no trivial_payload, force sonnet path.
    if not needs_sonnet and not trivial_payload and intent not in ("meta_question", "unknown"):
        needs_sonnet = True

    return IntentClassification(
        primary_intent=intent,  # type: ignore[arg-type]
        needs_sonnet=needs_sonnet,
        trivial_payload=trivial_payload,
        raw=raw,
    )
