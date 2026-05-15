"""Phase 1 — Vision Discovery (Seed Generation).

Sonnet vision analyzuje 1 screenshot a vrati ~3 sample produkty s name a price
PRESNE ako ich vidi. Tieto seedy potom Python deterministicky harvestuje cez
LCA + sibling clustering. Sonnet NEVYMYSLIA — ak vidi menej, vrat menej.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.extractors.hds.types import Seed
from app.core.llm.anthropic_client import call_sonnet_vision

logger = logging.getLogger(__name__)


SEED_TOOL_SCHEMA: dict[str, Any] = {
    "name": "report_seed_products",
    "description": "Vrat 3 sample produkty viditelne na screenshote.",
    "input_schema": {
        "type": "object",
        "properties": {
            "products": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Nazov produktu PRESNE ako je na screenshote (zachovaj diakritiku).",
                        },
                        "price": {
                            "type": "string",
                            "description": "Cena PRESNE ako je na screenshote ('160€', '180€/Den', 'dohodou', 'od 100€').",
                        },
                    },
                    "required": ["name", "price"],
                },
            }
        },
        "required": ["products"],
    },
}


SEED_SYSTEM = """Si seed-generator pre HDS-Lite engine.

Tvoja jedina uloha: najdi 3 produkty viditelne na screenshote a vrat ich name + price PRESNE ako su na obrazovke.

PRAVIDLA:
- NEVYMYSLAJ. Ak vidis menej nez 3 produkty, vrat len kolko vidis.
- Nazov copy-paste z obrazku (s diakritikou).
- Cena copy-paste z obrazku (s '€', '/Den', 'dohodou' atd.).
- Vyber produkty ktore SU VIDITELNE (nepokus sa hadat zakryte alebo orezane karty).
- Vyber rozne produkty — nie 3× tu istu kartu.
- Ak nevidis ziadny produkt, vrat prazdne pole.
"""


async def find_seeds(
    screenshot_bytes: bytes, page_url: str
) -> tuple[list[Seed], float]:
    """Phase 1 — Sonnet vision identifikuje 3 sample produkty.

    Returns: (seeds, cost_usd). Pri chybe ([], 0.0).
    """
    if not screenshot_bytes:
        logger.info("hds.find_seeds: no screenshot for %s", page_url)
        return [], 0.0

    user_text = (
        f"URL: {page_url}\n\n"
        "Najdi 3 produkty na screenshote. Pre kazdy vrat name a price PRESNE ako su."
    )

    try:
        response = await call_sonnet_vision(
            system=SEED_SYSTEM,
            user_text=user_text,
            image_bytes=screenshot_bytes,
            tools=[SEED_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "report_seed_products"},
            max_tokens=600,
            use_cache=True,
            timeout_s=60.0,
        )
    except Exception as e:
        logger.warning("hds.find_seeds vision call failed for %s: %s", page_url, e)
        return [], 0.0

    tool_use = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            tool_use = block
            break
    if tool_use is None:
        logger.warning("hds.find_seeds: no tool_use block for %s", page_url)
        return [], 0.0

    data = tool_use.input or {}
    seeds: list[Seed] = []
    for p in data.get("products") or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        price = (p.get("price") or "").strip()
        if not name or not price:
            continue
        seeds.append(Seed(name=name, price=price))

    # Cena (per usage, vid' vision.py pattern)
    cost = 0.0
    try:
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        regular_input = max(0, input_tokens - cache_read)
        cost = (
            (regular_input + cache_write * 1.25) * 3.0 / 1_000_000
            + cache_read * 0.3 / 1_000_000
            + output_tokens * 15.0 / 1_000_000
        )
    except Exception:
        cost = 0.0

    logger.info(
        "hds.find_seeds %s: %d seeds, cost=$%.4f", page_url, len(seeds), cost
    )
    return seeds, cost
