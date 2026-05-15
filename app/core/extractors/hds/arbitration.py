"""Phase 6 — AI Arbitration (Fallback).

Pre cards so score 0.4-0.7: posli HTML snippet do Sonnet a opyt sa ci je
validny produkt alebo layout. Sonnet NESMIE vymyslat data — ak chyba info,
nechaj null.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.extractors.hds.types import ProductCard
from app.core.llm.anthropic_client import call_sonnet

logger = logging.getLogger(__name__)


ARBITRATION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "arbitrate_card",
    "description": "Skontroluj HTML utruzok — produkt alebo layout?",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_product": {
                "type": "boolean",
                "description": "True ak HTML reprezentuje produkt/sluzbu, False ak layout/nav/footer.",
            },
            "name": {"type": ["string", "null"]},
            "price_eur": {"type": ["number", "null"]},
            "price_text": {"type": ["string", "null"]},
            "attributes": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["is_product"],
    },
}


ARBITRATION_SYSTEM = """Si arbiter pre HDS-Lite extractor.

Dostanes HTML utruzok jednej DOM karty. Rozhodnut:
1. Je to produkt/sluzba (ma nazov + cenu alebo atributy)? alebo layout element (nav/footer/banner)?
2. Ak produkt: doplň name, price_eur, price_text, attributes — IBA z toho co je v HTML.

PRAVIDLA:
- NEVYMYSLAJ. Ak v HTML nieto napriklad ceny, nechaj price_eur=null.
- price_text len ak je tam fraza ako 'dohodou', 'na vyziadanie', 'individual', '55€/den + doprava dohodov'.
- attributes: slovenske kluce (kapacita, rozmery, vyska, vek).
- Ak HTML obsahuje viac produktov, vyber HLAVNY (najvyraznejsi nazov).
- Vrat tool call arbitrate_card, ziadny dalsi text.
"""


async def arbitrate(card: ProductCard) -> tuple[Optional[ProductCard], float]:
    """IBA pre cards s 0.4 <= confidence < 0.7.

    Returns: (updated_card_alebo_None, cost_usd).
    """
    if not card.source_html:
        return None, 0.0

    snippet = card.source_html[:2000]
    user_text = (
        "Tu je HTML utruzok jednej DOM karty:\n\n"
        f"```html\n{snippet}\n```\n\n"
        "Je to produkt? Ak ano, vyplň name/price/attributes z HTML. NEVYMYSLAJ."
    )

    try:
        response = await call_sonnet(
            system=ARBITRATION_SYSTEM,
            messages=[{"role": "user", "content": user_text}],
            max_tokens=600,
            temperature=0.0,
            tools=[ARBITRATION_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "arbitrate_card"},
        )
    except Exception as e:
        logger.warning("hds.arbitrate call failed: %s", e)
        return None, 0.0

    tool_use = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            tool_use = block
            break
    if tool_use is None:
        return None, 0.0

    data = tool_use.input or {}
    is_product = bool(data.get("is_product"))

    # Compute cost
    cost = 0.0
    try:
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cost = (
            input_tokens * 3.0 / 1_000_000
            + output_tokens * 15.0 / 1_000_000
        )
    except Exception:
        cost = 0.0

    if not is_product:
        return None, cost

    name = data.get("name") or card.name
    if not name:
        return None, cost

    price_eur = data.get("price_eur")
    try:
        price_eur = float(price_eur) if price_eur is not None else None
    except (TypeError, ValueError):
        price_eur = None
    price_text = data.get("price_text") or card.price_text
    attributes = {
        str(k): str(v)
        for k, v in (data.get("attributes") or {}).items()
        if v is not None
    } or card.attributes

    new_conf = 0.5  # baseline po arbitration
    if name:
        new_conf += 0.2
    if price_eur is not None or price_text:
        new_conf += 0.2
    new_conf = min(1.0, new_conf)

    return (
        ProductCard(
            name=str(name).strip(),
            price_eur=price_eur,
            price_text=str(price_text).strip() if price_text else None,
            attributes=attributes,
            confidence=new_conf,
            lca_element=card.lca_element,
            source_html=card.source_html,
        ),
        cost,
    )
