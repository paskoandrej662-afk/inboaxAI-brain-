from __future__ import annotations

import logging
import re

from app.core.extractors.types import (
    ExtractedBusinessFact,
    ExtractedFaqItem,
    ExtractedImage,
    ExtractedProduct,
)

logger = logging.getLogger(__name__)

# Source priority: jsonld > regex > vision_verified > vision_unverified > html_text
SOURCE_PRIORITY: dict[str, int] = {
    'jsonld': 100,
    'regex': 80,
    'vision': 0,  # special case — riesi sa per-product check (verified/unverified)
    'html_text': 30,
}


def _product_priority(p: ExtractedProduct) -> int:
    if p.source_type == 'vision':
        return 70 if p.verified else 50
    return SOURCE_PRIORITY.get(p.source_type, 0)


def _normalize_name(name: str) -> str:
    """Lowercase, strip, collapse whitespace, remove leading/trailing non-alphanum."""
    s = name.lower().strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'^\W+|\W+$', '', s)
    return s


def merge_products(*lists: list[ExtractedProduct]) -> list[ExtractedProduct]:
    """Group products by normalized name, pick winner by source priority, merge attributes."""
    by_name: dict[str, list[ExtractedProduct]] = {}
    for lst in lists:
        for p in lst:
            key = _normalize_name(p.name)
            if not key:
                continue
            by_name.setdefault(key, []).append(p)

    merged: list[ExtractedProduct] = []
    for _key, candidates in by_name.items():
        candidates.sort(key=_product_priority, reverse=True)
        winner = candidates[0]
        for c in candidates[1:]:
            for k, v in c.attributes.items():
                if k not in winner.attributes:
                    winner.attributes[k] = v
            if winner.price_text is None and c.price_text:
                winner.price_text = c.price_text
                winner.price_eur = c.price_eur
                winner.price_unit = c.price_unit
            elif winner.price_text and c.price_text and len(c.price_text) > len(winner.price_text):
                # Both have price_text — keep the longer one (more info, e.g. mixed pricing)
                winner.price_text = c.price_text
            if winner.image_url is None and c.image_url:
                winner.image_url = c.image_url
            if winner.description is None and c.description:
                winner.description = c.description
        merged.append(winner)
    return merged


# Multi-value vs single-value fact keys
MULTI_VALUE_KEYS = frozenset({
    'phone', 'email',
    'social_facebook', 'social_instagram', 'social_tiktok',
    'payment_methods', 'service_area',
})


def merge_facts(*lists: list[ExtractedBusinessFact]) -> list[ExtractedBusinessFact]:
    """Group by key. Multi-value keys keep multiple. Single-value pick highest priority."""
    by_key: dict[str, list[ExtractedBusinessFact]] = {}
    for lst in lists:
        for f in lst:
            by_key.setdefault(f.key, []).append(f)

    merged: list[ExtractedBusinessFact] = []
    for key, items in by_key.items():
        if key in MULTI_VALUE_KEYS:
            seen: set[str] = set()
            sorted_items = sorted(
                items,
                key=lambda x: SOURCE_PRIORITY.get(x.source_type, 0),
                reverse=True,
            )
            for it in sorted_items:
                norm = re.sub(r'\s+', '', it.value.lower())
                if norm and norm not in seen:
                    seen.add(norm)
                    merged.append(it)
        else:
            best = max(
                items,
                key=lambda x: SOURCE_PRIORITY.get(x.source_type, 0),
            )
            merged.append(best)
    return merged


def merge_faqs(*lists: list[ExtractedFaqItem]) -> list[ExtractedFaqItem]:
    """Dedupe FAQs by normalized question. Higher-priority source wins."""
    seen: set[str] = set()
    out: list[ExtractedFaqItem] = []
    all_items: list[ExtractedFaqItem] = []
    for lst in lists:
        all_items.extend(lst)
    all_items.sort(
        key=lambda x: SOURCE_PRIORITY.get(x.source_type, 0),
        reverse=True,
    )
    for f in all_items:
        norm = re.sub(r'\W+', '', f.question.lower())
        if norm and norm not in seen:
            seen.add(norm)
            out.append(f)
    return out


def merge_images(*lists: list[ExtractedImage]) -> list[ExtractedImage]:
    """Dedupe images by URL. Keep first occurrence (which has more context)."""
    seen: set[str] = set()
    out: list[ExtractedImage] = []
    for lst in lists:
        for img in lst:
            if img.url and img.url not in seen:
                seen.add(img.url)
                out.append(img)
    return out
