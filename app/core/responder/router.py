from __future__ import annotations

import logging
from typing import Literal

from app.core.llm.anthropic_client import call_haiku

logger = logging.getLogger(__name__)

Route = Literal["qa", "booking", "handoff", "smalltalk", "unknown"]
VALID_ROUTES: tuple[Route, ...] = ("qa", "booking", "handoff", "smalltalk", "unknown")

_SYSTEM = """Si router. Klasifikuj nasledujúcu správu zákazníka do jednej kategórie:
- qa: zákazník sa pýta na fakty o firme (cena, hodiny, služby, kontakt, lokalita, produkty)
- booking: chce si rezervovať termín, dohodnúť stretnutie, alebo objednať službu
- handoff: chce hovoriť s človekom/manažérom, má sťažnosť, je nespokojný, chce vrátiť peniaze
- smalltalk: pozdrav, ďakovanie, neutrálna konverzácia bez konkrétnej požiadavky
- unknown: správa je nejasná, prázdna, alebo nedáva zmysel

Príklady:
"Aké máte ceny strihania?" -> qa
"Otvárate cez víkend?" -> qa
"Kde sa nachádzate?" -> qa
"Chcem si rezervovať termín na zajtra o 10:00" -> booking
"Môžem sa objednať?" -> booking
"Chcem hovoriť s majiteľom, toto je hrôza!" -> handoff
"Mám sťažnosť na vašu službu" -> handoff
"Dobrý deň, ako sa máte?" -> smalltalk
"Ďakujem, pekný deň" -> smalltalk
"asdf" -> unknown

Odpovedz IBA jedným slovom: qa, booking, handoff, smalltalk, alebo unknown."""


def _normalize(label: str) -> Route:
    label = label.strip().lower().rstrip(".:! ").split()[0] if label.strip() else ""
    # strip punctuation
    label = "".join(ch for ch in label if ch.isalpha())
    if label in VALID_ROUTES:
        return label  # type: ignore[return-value]
    return "qa"  # safest default


async def classify_route(query: str, history: list[dict] | None = None) -> Route:
    if not query or not query.strip():
        return "unknown"

    user_msg = query.strip()
    # We keep history light — last user/assistant turn is enough context for routing
    msgs: list[dict] = []
    if history:
        for h in history[-2:]:
            role = h.get("role")
            content = h.get("content", "")
            if role in ("user", "assistant") and content:
                msgs.append({"role": role, "content": str(content)[:500]})
    msgs.append({"role": "user", "content": user_msg[:1000]})

    try:
        raw = await call_haiku(_SYSTEM, msgs, max_tokens=10, temperature=0.0)
    except Exception as exc:
        logger.warning("router: haiku failed, falling back to qa: %s", exc)
        return "qa"

    return _normalize(raw)
