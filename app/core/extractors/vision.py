from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.browser import RenderedPage, TiledPageResult
from app.core.extractors.merger import (
    merge_facts,
    merge_faqs,
    merge_images,
    merge_products,
)
from app.core.extractors.types import (
    ExtractedBusinessFact,
    ExtractedFaqItem,
    ExtractedImage,
    ExtractedProduct,
)
from app.core.llm.anthropic_client import call_sonnet_vision

logger = logging.getLogger(__name__)


VISION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "extract_page_data",
    "description": "Extrahuj strukturovane data zo screenshotu webovej stranky.",
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
                            "description": "Nazov produktu/sluzby",
                        },
                        "description": {
                            "type": "string",
                            "description": "Strucny popis",
                        },
                        "price_text": {
                            "type": "string",
                            "description": "Cena presne tak ako na stranke, napr. '160€/Den' alebo 'od 25 EUR'",
                        },
                        "price_eur": {
                            "type": ["number", "null"],
                            "description": "Cena ako cislo v EUR ak sa da parsovat",
                        },
                        "price_unit": {
                            "type": "string",
                            "enum": [
                                "den",
                                "hodina",
                                "kus",
                                "mesiac",
                                "rok",
                                "osoba",
                                "neuvedene",
                            ],
                        },
                        "attributes": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": "Kluc-hodnota napr. {'kapacita': '9 deti', 'rozmery': '8x5m'}",
                        },
                        "image_url": {
                            "type": ["string", "null"],
                            "description": "URL primarneho obrazka produktu",
                        },
                    },
                    "required": ["name"],
                },
            },
            "business_facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "enum": [
                                "phone",
                                "email",
                                "address",
                                "hours",
                                "social_facebook",
                                "social_instagram",
                                "social_tiktok",
                                "ico",
                                "iban",
                                "payment_methods",
                                "service_area",
                                "company_name",
                                "tagline",
                                "other",
                            ],
                        },
                        "value": {"type": "string"},
                    },
                    "required": ["key", "value"],
                },
            },
            "faqs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                    },
                    "required": ["question", "answer"],
                },
            },
            "image_descriptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "image_url": {"type": "string"},
                        "description": {
                            "type": "string",
                            "description": "1-2 vety v slovencine co je na obrazku",
                        },
                        "near_product_name": {"type": ["string", "null"]},
                    },
                    "required": ["image_url", "description"],
                },
            },
            "page_summary": {
                "type": "string",
                "description": "2-3 vetne zhrnutie obsahu stranky",
            },
        },
        "required": [
            "products",
            "business_facts",
            "faqs",
            "image_descriptions",
            "page_summary",
        ],
    },
}


VISION_SYSTEM = """Si extraktor faktov pre slovensky business web. Pozri sa na screenshot stranky tak, ako by sa na nu pozeral clovek.

Tvoja uloha:
1. Identifikuj vsetky PRODUKTY/SLUZBY s nazvami, popismi, cenami a atributmi (kapacita, rozmery, cas, materil, vek...). Cena musi byt presne tak ako na stranke.
2. Identifikuj FAKTY o firme: telefon, email, adresa, otvaracie hodiny, socialne siete, ICO, oblast posobnosti.
3. Identifikuj FAQ (otazka-odpoved pary) ak nejake su.
4. Pre dolezitie obrazky napis strucny popis co je na nom.
5. Vrat 2-3 vetne zhrnutie cono sa stranka tyka.

PRAVIDLA:
- NEVYMYSLAJ udaje. Ak nieco na stranke nevidis, NEZAHRNUJ.
- Ak je ta ista cena spomenuta viackrat pri tom istom produkte, uloz ju iba raz.
- Cena bez konkretneho produktu (napr. "Akcia od 99€" v hlavicke) → uloz ako fact 'tagline', NIE ako product price.
- Atributy zachovaj v slovencine ako kluce: kapacita, rozmery, vyska, vek, materil, farba, atd.
- Pouzi tool extract_page_data s vyplnenym JSON, ziadny iny text.
"""


IMAGE_DESCRIBE_SYSTEM = """Si popisovač produktovych fotiek. Vrat 1-2 vety v slovencine popisujuce co je na obrazku — produkty, farby, prostredie, ludia. Ak vidis text, precitaj ho. Ziadny fluff, faktický popis.
"""


async def extract_page_with_vision(
    rendered: RenderedPage,
    page_type: str,
    raw_text: str,
) -> tuple[
    list[ExtractedProduct],
    list[ExtractedBusinessFact],
    list[ExtractedFaqItem],
    list[ExtractedImage],
    str,
    float,
]:
    """Extrahuje strukturovane data zo screenshot+DOM kontextu.

    Returns: (products, business_facts, faqs, image_descriptions, page_summary, llm_cost_usd)

    Defenzivne: Ak vision call zlyhá, vráti všetky prázdne zoznamy + cost=0.0
    (pipeline pokračuje s html-only daty).
    """
    if rendered.screenshot_png is None:
        logger.info("vision: skipping %s, no screenshot", rendered.url)
        return [], [], [], [], "", 0.0

    user_text = (
        f"URL: {rendered.url}\n"
        f"Typ stranky: {page_type}\n\n"
        f"Text extrahovany z HTML (kontext):\n{raw_text[:3000]}"
    )

    try:
        response = await call_sonnet_vision(
            system=VISION_SYSTEM,
            user_text=user_text,
            image_bytes=rendered.screenshot_png,
            tools=[VISION_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "extract_page_data"},
            max_tokens=3000,
            use_cache=True,
            timeout_s=90.0,
        )
    except Exception as e:
        logger.warning("vision extraction failed for %s: %s", rendered.url, e)
        return [], [], [], [], "", 0.0

    tool_use = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            tool_use = block
            break
    if tool_use is None:
        logger.warning("vision: no tool_use block in response for %s", rendered.url)
        return [], [], [], [], "", 0.0

    data = tool_use.input or {}

    products: list[ExtractedProduct] = []
    for p in data.get("products") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        try:
            price_eur = float(p["price_eur"]) if p.get("price_eur") is not None else None
        except (TypeError, ValueError):
            price_eur = None
        products.append(
            ExtractedProduct(
                name=str(p["name"]).strip(),
                description=str(p["description"]).strip() if p.get("description") else None,
                price_text=str(p["price_text"]).strip() if p.get("price_text") else None,
                price_eur=price_eur,
                price_unit=str(p["price_unit"]) if p.get("price_unit") else None,
                attributes={
                    str(k): str(v)
                    for k, v in (p.get("attributes") or {}).items()
                    if v is not None
                },
                image_url=str(p["image_url"]) if p.get("image_url") else None,
                source_url=rendered.final_url,
                source_block_text=raw_text[:500],
                source_type="vision",
                confidence=0.7,
                verified=False,
            )
        )

    facts: list[ExtractedBusinessFact] = []
    for f in data.get("business_facts") or []:
        if not isinstance(f, dict) or not f.get("key") or not f.get("value"):
            continue
        facts.append(
            ExtractedBusinessFact(
                key=str(f["key"]),
                value=str(f["value"]).strip(),
                source_url=rendered.final_url,
                source_type="vision",
                confidence=0.7,
            )
        )

    faqs: list[ExtractedFaqItem] = []
    for fq in data.get("faqs") or []:
        if not isinstance(fq, dict) or not fq.get("question") or not fq.get("answer"):
            continue
        faqs.append(
            ExtractedFaqItem(
                question=str(fq["question"]).strip(),
                answer=str(fq["answer"]).strip(),
                source_url=rendered.final_url,
                source_type="vision",
                confidence=0.7,
            )
        )

    images: list[ExtractedImage] = []
    for img in data.get("image_descriptions") or []:
        if not isinstance(img, dict) or not img.get("image_url"):
            continue
        images.append(
            ExtractedImage(
                url=str(img["image_url"]),
                description=str(img["description"]) if img.get("description") else None,
                near_product_name=str(img["near_product_name"])
                if img.get("near_product_name")
                else None,
                source_url=rendered.final_url,
            )
        )

    summary = str(data.get("page_summary") or "")

    cost = 0.0
    try:
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        regular_input = max(0, input_tokens - cache_read)
        cost = (
            (regular_input + cache_write) * 3.0 / 1_000_000
            + cache_read * 0.3 / 1_000_000
            + output_tokens * 15.0 / 1_000_000
        )
    except Exception:
        cost = 0.0

    logger.info(
        "vision extracted %s: products=%d facts=%d faqs=%d images=%d cost=$%.4f",
        rendered.url,
        len(products),
        len(facts),
        len(faqs),
        len(images),
        cost,
    )
    return products, facts, faqs, images, summary, cost


async def describe_image(
    image_bytes: bytes, alt_text: str | None = None
) -> str | None:
    """Popisi 1 obrazok pre RAG vyhľadavanie.

    image_bytes: raw image content (PNG/JPEG).
    Returns: 1-2 vety v slovencine ako string, alebo None ak vision zlyhá.
    """
    if not image_bytes or len(image_bytes) < 1024:
        return None

    user_text = f"alt='{alt_text}'" if alt_text else ""

    try:
        response = await call_sonnet_vision(
            system=IMAGE_DESCRIBE_SYSTEM,
            user_text=user_text,
            image_bytes=image_bytes,
            image_media_type="image/jpeg" if image_bytes[:3] == b"\xff\xd8\xff" else "image/png",
            max_tokens=200,
            use_cache=True,
            timeout_s=30.0,
        )
    except Exception as e:
        logger.warning("describe_image failed: %s", e)
        return None

    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    description = "".join(parts).strip()
    return description if description else None


# ───────────────────────────────────────────────────
# Tiled vision — multi-viewport screenshot extraction (Phase 2B-1)
# ───────────────────────────────────────────────────


async def _extract_one_tile(
    tile_bytes: bytes,
    tile_index: int,
    total_tiles: int,
    page_url: str,
    page_type: str,
    raw_text_excerpt: str,
) -> tuple[list, list, list, list, str, float]:
    """Extrahuj strukturovane data z jedneho tile screenshotu.

    Returns: (products, facts, faqs, images, summary, cost_usd).
    Pouziva prompt caching pre system + tool schema (saves ~60% na opakovanych volaniach).
    """
    user_text = (
        f"URL: {page_url}\n"
        f"Typ stranky: {page_type}\n"
        f"Toto je segment {tile_index + 1} z {total_tiles} (skrolovany screenshot 1280x2200 px).\n"
        f"Mozes vidiet len cast stranky — sustred sa na produkty/sluzby/fakty viditelne v tomto segmente.\n\n"
        f"HTML extracted text (kontext z celej stranky):\n{raw_text_excerpt[:2500]}"
    )

    try:
        response = await call_sonnet_vision(
            system=VISION_SYSTEM,
            user_text=user_text,
            image_bytes=tile_bytes,
            tools=[VISION_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "extract_page_data"},
            max_tokens=2500,
            use_cache=True,  # CRITICAL — prompt caching na system + tools
            timeout_s=90.0,
        )
    except Exception as e:
        logger.warning("tile %d vision call failed: %s", tile_index, e)
        return [], [], [], [], "", 0.0

    # Najdi tool_use block
    tool_use = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            tool_use = block
            break
    if tool_use is None:
        return [], [], [], [], "", 0.0

    data = tool_use.input or {}

    # Parse products
    products: list = []
    for p in (data.get("products") or []):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        try:
            price_eur = float(p["price_eur"]) if p.get("price_eur") is not None else None
        except (TypeError, ValueError):
            price_eur = None
        products.append(ExtractedProduct(
            name=str(p["name"]).strip(),
            description=str(p["description"]).strip() if p.get("description") else None,
            price_text=str(p["price_text"]).strip() if p.get("price_text") else None,
            price_eur=price_eur,
            price_unit=str(p["price_unit"]) if p.get("price_unit") else None,
            attributes={k: str(v) for k, v in (p.get("attributes") or {}).items() if v is not None},
            image_url=str(p["image_url"]) if p.get("image_url") else None,
            source_url=page_url,
            source_block_text=raw_text_excerpt[:500],
            source_type="vision",
            confidence=0.7,
            verified=False,
        ))

    # Parse facts
    facts: list = []
    for f in (data.get("business_facts") or []):
        if not isinstance(f, dict) or not f.get("key") or not f.get("value"):
            continue
        facts.append(ExtractedBusinessFact(
            key=str(f["key"]),
            value=str(f["value"]).strip(),
            source_url=page_url,
            source_type="vision",
            confidence=0.7,
        ))

    # Parse faqs
    faqs: list = []
    for fq in (data.get("faqs") or []):
        if not isinstance(fq, dict) or not fq.get("question") or not fq.get("answer"):
            continue
        faqs.append(ExtractedFaqItem(
            question=str(fq["question"]).strip(),
            answer=str(fq["answer"]).strip(),
            source_url=page_url,
            source_type="vision",
            confidence=0.7,
        ))

    # Parse images
    images: list = []
    for img in (data.get("image_descriptions") or []):
        if not isinstance(img, dict) or not img.get("image_url"):
            continue
        images.append(ExtractedImage(
            url=str(img["image_url"]),
            description=str(img["description"]) if img.get("description") else None,
            near_product_name=str(img["near_product_name"]) if img.get("near_product_name") else None,
            source_url=page_url,
        ))

    summary = str(data.get("page_summary") or "")

    # Vypocet ceny
    cost = 0.0
    try:
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        regular_input = max(0, input_tokens - cache_read)
        # Sonnet pricing: $3/M input, $15/M output, $0.30/M cache-read, $3.75/M cache-write
        cost = (
            (regular_input + cache_write * 1.25) * 3.0 / 1_000_000
            + cache_read * 0.3 / 1_000_000
            + output_tokens * 15.0 / 1_000_000
        )
    except Exception:
        cost = 0.0

    return products, facts, faqs, images, summary, cost


async def extract_page_with_tiled_vision(
    tiled: TiledPageResult,
    page_type: str,
    raw_text: str,
) -> tuple[list, list, list, list, str, float]:
    """Extrahuj strukturovane data z multi-tile renderovanej stranky.

    Vola Sonnet vision na kazdy tile PARALELNE s prompt cachingom.
    Zlucuje products/facts/faqs/images cez tiles pomocou existujuceho mergera (dedupe by name).

    Pre single-tile (kratka stranka) result: funguje identicky ako extract_page_with_vision,
    len pouziva novy pipeline.

    Returns: (merged_products, merged_facts, merged_faqs, merged_images, combined_summary, total_cost_usd).
    """
    if not tiled.segments:
        logger.info("tiled vision: no segments for %s, returning empty", tiled.url)
        return [], [], [], [], "", 0.0

    num_tiles = len(tiled.segments)
    logger.info("tiled vision extracting %s: %d tiles", tiled.url, num_tiles)

    # Paralelna extrakcia s concurrency limitom (vyhneme sa rate limitom)
    semaphore = asyncio.Semaphore(3)

    async def _extract_with_sem(idx, tile_bytes):
        async with semaphore:
            return await _extract_one_tile(
                tile_bytes, idx, num_tiles,
                tiled.final_url, page_type, raw_text,
            )

    tasks = [_extract_with_sem(i, seg) for i, seg in enumerate(tiled.segments)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Pozbieraj vysledky, preskoc exceptions
    all_products: list = []
    all_facts: list = []
    all_faqs: list = []
    all_images: list = []
    summaries: list[str] = []
    total_cost = 0.0

    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning("tile %d raised: %s", i, r)
            continue
        products, facts, faqs, images, summary, cost = r
        all_products.extend(products)
        all_facts.extend(facts)
        all_faqs.extend(faqs)
        all_images.extend(images)
        if summary:
            summaries.append(summary)
        total_cost += cost

    # Zluc cez tiles pomocou existujuceho mergera (dedupe by normalized name)
    merged_products = merge_products(all_products)
    merged_facts = merge_facts(all_facts)
    merged_faqs = merge_faqs(all_faqs)
    merged_images = merge_images(all_images)

    combined_summary = " ".join(summaries[:3]) if summaries else ""

    logger.info(
        "tiled vision done %s: %d tiles -> products=%d facts=%d faqs=%d images=%d cost=$%.4f",
        tiled.url, num_tiles, len(merged_products), len(merged_facts),
        len(merged_faqs), len(merged_images), total_cost,
    )

    return merged_products, merged_facts, merged_faqs, merged_images, combined_summary, total_cost
