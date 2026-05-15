"""Phase 5 — Visibility & Confidence Scoring.

score_card: pure-Python bodovanie (name+price+recurring).
filter_visible: Playwright is_visible check + dedup podla text contentu.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from app.core.extractors.hds.types import ProductCard

logger = logging.getLogger(__name__)


def score_card(card_data: dict, has_recurring_siblings: bool) -> float:
    """+0.5 ak name, +0.3 ak price (eur OR text), +0.2 ak recurring."""
    score = 0.0
    if card_data.get("name"):
        score += 0.5
    if card_data.get("price_eur") is not None or card_data.get("price_text"):
        score += 0.3
    if has_recurring_siblings:
        score += 0.2
    return min(1.0, score)


def _normalize_signature(card: ProductCard) -> str:
    """Dedup signature: NFKC(name + price_eur + price_text)."""
    parts = [
        unicodedata.normalize("NFKC", card.name or "").strip().lower(),
        f"{card.price_eur:.2f}" if card.price_eur is not None else "",
        unicodedata.normalize("NFKC", card.price_text or "").strip().lower(),
    ]
    return "|".join(parts)


async def filter_visible(cards: list[ProductCard], page: Any) -> list[ProductCard]:
    """Pre kazdu kartu over visibility cez Playwright. Dedup po visibility.

    page: Playwright Page object alebo None — ak None, visibility step skip.

    Pre visibility lookup pouzivame card.lca_element (BS4 Tag) → ziadny priamy
    most na Playwright element handle. Pretoze BS4 a Playwright sa nezdielajú
    rovnaky DOM, robime CSS-selector matching cez tag + class signature.
    Pri akejkolvek chybe povazujeme za visible (best-effort).
    """
    visible: list[ProductCard] = []

    for card in cards:
        is_vis = True  # default — best-effort
        if page is not None and card.lca_element is not None:
            try:
                lca = card.lca_element
                tag = getattr(lca, "name", None)
                classes = lca.get("class") if hasattr(lca, "get") else None
                if tag and classes:
                    selector = tag + "".join(f".{c}" for c in classes if c)
                    # is_visible() na prvom matching elemente
                    handle = await page.query_selector(selector)
                    if handle is not None:
                        try:
                            is_vis = await handle.is_visible()
                        except Exception:
                            is_vis = True
            except Exception as e:
                logger.debug("hds.filter_visible visibility check failed: %s", e)
                is_vis = True

        if is_vis:
            visible.append(card)

    # Dedup: ked 2 karty maju identical signature, zachovaj s lepsim confidence
    by_sig: dict[str, ProductCard] = {}
    for card in visible:
        sig = _normalize_signature(card)
        existing = by_sig.get(sig)
        if existing is None or card.confidence > existing.confidence:
            by_sig[sig] = card

    return list(by_sig.values())
