"""Phase 4 — Deterministic Field Extraction.

Pre kazdy Candidate Card pure-Python regex/heuristikami extrahuj:
- name (najvyssi h1-h5, fallback najvacsi font-size text)
- price_eur (regex r'(\\d+[.,]?\\d*)\\s*€', vyber najvacsi)
- price_text (mixed/soft pricing: 'dohodou', 'na vyziadanie' atd.)
- attributes (kapacita, rozmery, vyska, odporucany_vek)

Vsetky stringy NFKC normalizovane.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from bs4 import Tag

logger = logging.getLogger(__name__)


HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5")

SOFT_PRICE_KEYWORDS = (
    "dohod",
    "na vyziadanie",
    "na požiadanie",
    "na vyžiadanie",
    "individual",
    "cena podla",
    "cena podľa",
)

PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*€")

ATTR_PATTERNS = {
    "kapacita": re.compile(
        r"kapacita\s*[:\-]?\s*([^\n\r•|]+?)(?=[\n\r•|]|cena|rozmer|v[ýy]ka|v[ýy]ška|vek|$)",
        re.IGNORECASE,
    ),
    "rozmery": re.compile(
        r"rozmery\s*[:\-]?\s*([^\n\r•|]+?)(?=[\n\r•|]|cena|kapac|v[ýy]ka|v[ýy]ška|vek|$)",
        re.IGNORECASE,
    ),
    "vyska": re.compile(
        r"v[ýy]ška\s*[:\-]?\s*([^\n\r•|]+?)(?=[\n\r•|]|cena|kapac|rozmer|vek|$)",
        re.IGNORECASE,
    ),
    "odporucany_vek": re.compile(
        r"(?:odporúčan[ýy]|vhodn[ýyé]|vek)\s*[:\-]?\s*([^\n\r•|]+?)(?=[\n\r•|]|cena|kapac|rozmer|v[ýy]ka|$)",
        re.IGNORECASE,
    ),
}


def _norm(s: str) -> str:
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", str(s)).strip()


def _extract_name(card: Tag) -> Optional[str]:
    """Prefer prvy heading h1-h5. Fallback: najvyssia inline font-size text node."""
    for tag_name in HEADING_TAGS:
        h = card.find(tag_name)
        if h is not None:
            text = _norm(h.get_text(" ", strip=True))
            if text:
                return text

    # Fallback — najdi element s najvyssim inline font-size
    candidates: list[tuple[float, str]] = []
    for el in card.find_all(True):
        if not isinstance(el, Tag):
            continue
        style = el.get("style") or ""
        m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*(px|em|rem|%)?", style, re.IGNORECASE)
        if not m:
            continue
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        text = _norm(el.get_text(" ", strip=True))
        if text:
            candidates.append((val, text))
    if candidates:
        candidates.sort(key=lambda t: t[0], reverse=True)
        return candidates[0][1]
    return None


def _extract_price_eur(text: str) -> Optional[float]:
    """Z text vsetky '\\d+€' matches; vyber najvacsiu (typicky hlavna cena)."""
    matches = PRICE_RE.findall(text)
    values: list[float] = []
    for raw in matches:
        try:
            v = float(raw.replace(",", "."))
            values.append(v)
        except ValueError:
            continue
    if not values:
        return None
    # Vyber najvacsiu hodnotu — typicky to je hlavna cena (nie napr. "od 5€")
    return max(values)


def _extract_price_text(text: str) -> Optional[str]:
    """Najde fragment ak text obsahuje soft-price keyword."""
    low = text.lower()
    for kw in SOFT_PRICE_KEYWORDS:
        idx = low.find(kw)
        if idx == -1:
            continue
        # Vyber okolie 80 znakov
        start = max(0, idx - 20)
        end = min(len(text), idx + 60)
        fragment = text[start:end].strip()
        return _norm(fragment)
    return None


def _extract_attributes(text: str) -> dict[str, str]:
    """Regex extrakcia kapacita/rozmery/vyska/odporucany_vek."""
    out: dict[str, str] = {}
    for key, pattern in ATTR_PATTERNS.items():
        m = pattern.search(text)
        if not m:
            continue
        val = _norm(m.group(1))
        if val:
            out[key] = val
    return out


def extract_fields(card: Tag) -> dict:
    """Pure-Python extraction pre jednu candidate card.

    Returns: dict s klucmi name, price_eur, price_text, attributes.
    Pri chybe vrat {} (caller skipuje kartu bez name).
    """
    try:
        full_text = _norm(card.get_text(" ", strip=True))
        if not full_text:
            return {}

        name = _extract_name(card)
        price_eur = _extract_price_eur(full_text)
        price_text = _extract_price_text(full_text) if price_eur is None else None
        # Aj ked je price_eur, mixed pricing ('55€ + doprava dohodov') zachovaj
        if price_eur is not None and price_text is None:
            mixed = _extract_price_text(full_text)
            if mixed:
                price_text = mixed
        attributes = _extract_attributes(full_text)

        return {
            "name": name,
            "price_eur": price_eur,
            "price_text": price_text,
            "attributes": attributes,
        }
    except Exception as e:
        logger.debug("hds.extract_fields exception: %s", e)
        return {}
