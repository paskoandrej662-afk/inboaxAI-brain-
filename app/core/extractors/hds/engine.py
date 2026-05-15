"""HDS-Lite orchestrator — vola vsetky 6 faz v poradi.

Fail-safe: kazdy phase ma try/except. Pri kritickej chybe vrat
ExtractionResult(success=False, fallback_reason='...') aby caller mohol
fallbackovat na povodny vision extractor.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.extractors.hds.arbitration import arbitrate
from app.core.extractors.hds.cluster_detector import find_siblings
from app.core.extractors.hds.confidence import filter_visible, score_card
from app.core.extractors.hds.field_extractor import extract_fields
from app.core.extractors.hds.lca_finder import find_lca
from app.core.extractors.hds.types import ExtractionResult, ProductCard
from app.core.extractors.hds.vision_seed import find_seeds

logger = logging.getLogger(__name__)


async def run_hds_extraction(
    html: str,
    screenshot_bytes: bytes,
    page: Optional[Any],
    page_url: str,
) -> ExtractionResult:
    """Main HDS-Lite pipeline — 6 phases.

    Fail-safe: ak ktorakolvek phase zlyha kriticky, vrat
    ExtractionResult(success=False, fallback_reason='...').
    Caller potom moze fallback na puvodny vision extractor.
    """
    result = ExtractionResult(success=False)

    # ─────────────────────────────────────────────
    # Phase 1: Vision Seeds
    # ─────────────────────────────────────────────
    try:
        seeds, seed_cost = await find_seeds(screenshot_bytes, page_url)
    except Exception as e:
        logger.warning("hds.engine phase1 (vision seeds) failed: %s", e)
        result.fallback_reason = "phase1_exception"
        return result

    result.sonnet_cost_usd += seed_cost
    result.seeds_found = len(seeds)
    if not seeds:
        result.fallback_reason = "no_seeds_from_vision"
        return result

    # ─────────────────────────────────────────────
    # Phase 2: LCA mapping
    # ─────────────────────────────────────────────
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.warning("hds.engine: BS4 parse failed: %s", e)
        result.fallback_reason = "html_parse_failed"
        return result

    lcas: list = []
    for seed in seeds:
        try:
            lca = find_lca(soup, seed)
        except Exception as e:
            logger.debug("hds.engine find_lca exception for seed %r: %s", seed.name, e)
            continue
        if lca is not None:
            lcas.append((seed, lca))

    result.lcas_found = len(lcas)
    if not lcas:
        result.fallback_reason = "no_lca_found"
        return result

    # ─────────────────────────────────────────────
    # Phase 3: Sibling Clustering — pouzi prvy LCA ako template
    # ─────────────────────────────────────────────
    primary_seed, primary_lca = lcas[0]
    try:
        candidate_cards = find_siblings(primary_lca)
    except Exception as e:
        logger.warning("hds.engine find_siblings failed: %s", e)
        result.fallback_reason = "cluster_exception"
        return result

    result.candidate_count = len(candidate_cards)

    if len(candidate_cards) < 2:
        result.fallback_reason = "insufficient_candidates"
        return result

    has_recurring = len(candidate_cards) >= 3

    # ─────────────────────────────────────────────
    # Phase 4: Field extraction (pure Python)
    # ─────────────────────────────────────────────
    extracted: list[ProductCard] = []
    for card_tag in candidate_cards:
        try:
            fields = extract_fields(card_tag)
        except Exception as e:
            logger.debug("hds.engine extract_fields exception: %s", e)
            continue
        if not fields.get("name"):
            continue

        card = ProductCard(
            name=fields["name"],
            price_eur=fields.get("price_eur"),
            price_text=fields.get("price_text"),
            attributes=fields.get("attributes", {}),
            lca_element=card_tag,
            source_html=str(card_tag)[:2000],
        )
        card.confidence = score_card(fields, has_recurring)
        extracted.append(card)

    if not extracted:
        result.fallback_reason = "no_fields_extracted"
        return result

    # ─────────────────────────────────────────────
    # Phase 5: Visibility + dedup
    # ─────────────────────────────────────────────
    try:
        visible = await filter_visible(extracted, page)
    except Exception as e:
        logger.warning("hds.engine filter_visible failed: %s — using all extracted", e)
        visible = extracted
    result.after_visibility = len(visible)

    # ─────────────────────────────────────────────
    # Phase 6: Arbitration pre uncertain cards
    # ─────────────────────────────────────────────
    high_confidence = [c for c in visible if c.confidence > 0.7]
    uncertain = [c for c in visible if 0.4 <= c.confidence <= 0.7]
    result.after_confidence = len(high_confidence)

    arbitrated: list[ProductCard] = []
    for card in uncertain:
        try:
            new_card, arb_cost = await arbitrate(card)
        except Exception as e:
            logger.debug("hds.engine arbitrate exception: %s", e)
            new_card, arb_cost = None, 0.0
        result.sonnet_cost_usd += arb_cost
        result.arbitration_called += 1
        if new_card and new_card.confidence >= 0.7:
            arbitrated.append(new_card)

    final_cards = high_confidence + arbitrated

    if len(final_cards) < 2:
        result.fallback_reason = "too_few_after_pipeline"
        return result

    result.cards = final_cards
    result.success = True
    return result
