"""Phase 2 — Anchor Mapping & LCA.

Pre kazdy Seed: najdi v DOM-e deepest element ktory obsahuje seed.name a
deepest element ktory obsahuje seed.price. Walk up oba paths -> spolocny rodic.
LCA musi byt container tag (div/section/article/li).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from bs4 import BeautifulSoup, Tag

from app.core.extractors.hds.types import Seed

logger = logging.getLogger(__name__)

CONTAINER_TAGS = {"div", "section", "article", "li"}


def _normalize(text: str) -> str:
    """NFKC normalizacia + lower + strip + diacritic removal pre stringove porovnania.

    Diakritika sa odstranuje (NFD decompose + odhodit combining marks) — ucel je
    permissive matching medzi vision seedom (Sonnet OCR) a DOM textom, kde sa
    forma diakritiky moze ulozit roznymi unicode codepoint-mi.
    """
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).strip().lower()
    decomposed = unicodedata.normalize("NFD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped


def _price_search_fragment(price: str) -> str:
    """Z 'price' string vytiahni hladaci fragment.

    Sonnet moze vratit '160€' alebo 'dohodou' — chceme najst najdistinktivnejsi
    sub-string. Pre numericku cenu vyber prvy '\\d+'.
    """
    norm = _normalize(price)
    # Najdi prve cislo (mozno s desatinnym znakom)
    m = re.search(r"\d+(?:[.,]\d+)?", norm)
    if m:
        return m.group(0).replace(",", ".")
    # Bez cisla — vyber prve "slovo" >= 4 znaky
    for token in re.split(r"\s+", norm):
        if len(token) >= 4:
            return token
    return norm


def _find_deepest_containing(
    soup: BeautifulSoup, needle: str
) -> Optional[Tag]:
    """Najdi najhlbsi element ktoreho text (po normalizacii) obsahuje needle.

    'Najhlbsi' v zmysle: ziadne dieta nesplnuje to iste.
    """
    needle_norm = _normalize(needle)
    if not needle_norm:
        return None

    matches: list[Tag] = []
    for el in soup.find_all(True):
        try:
            text_norm = _normalize(el.get_text(" ", strip=True))
        except Exception:
            continue
        if needle_norm in text_norm:
            matches.append(el)

    if not matches:
        return None

    # Najdi 'leaf' matches — match bez deeper match v ramci svojich potomkov
    def has_matching_descendant(el: Tag) -> bool:
        for d in el.find_all(True):
            try:
                dt = _normalize(d.get_text(" ", strip=True))
            except Exception:
                continue
            if needle_norm in dt:
                return True
        return False

    deepest = [el for el in matches if not has_matching_descendant(el)]
    if not deepest:
        # Vsetky matche mali aj deeper match — vrat posledny v dokument-order
        return matches[-1]
    # Vyber najkratsi text (typicky najprecisaensi)
    deepest.sort(key=lambda e: len(_normalize(e.get_text(" ", strip=True))))
    return deepest[0]


def _ancestors(el: Tag) -> list[Tag]:
    """Vrat list ancestor Tag-ov od el samotneho po root."""
    out: list[Tag] = []
    cur: Optional[Tag] = el
    while cur is not None and isinstance(cur, Tag):
        out.append(cur)
        cur = cur.parent
    return out


def _common_ancestor(a: Tag, b: Tag) -> Optional[Tag]:
    """LCA dvoch BS4 Tag-ov."""
    anc_a = _ancestors(a)
    set_a = set(id(x) for x in anc_a)
    cur: Optional[Tag] = b
    while cur is not None and isinstance(cur, Tag):
        if id(cur) in set_a:
            return cur
        cur = cur.parent
    return None


def find_lca(soup: BeautifulSoup, seed: Seed) -> Optional[Tag]:
    """Najdi Lowest Common Ancestor pre seed.name a seed.price.

    Vrat container tag (div/section/article/li). Ak LCA nie je container,
    walk up dokym ho najdes. Ak nenajde, return None.
    """
    try:
        name_el = _find_deepest_containing(soup, seed.name)
        if name_el is None:
            logger.debug("hds.find_lca: name not found: %r", seed.name)
            return None

        price_fragment = _price_search_fragment(seed.price)
        if not price_fragment:
            logger.debug("hds.find_lca: empty price fragment for %r", seed.price)
            return None
        price_el = _find_deepest_containing(soup, price_fragment)
        if price_el is None:
            logger.debug(
                "hds.find_lca: price fragment %r not found", price_fragment
            )
            return None

        lca = _common_ancestor(name_el, price_el)
        if lca is None:
            return None

        # Walk up dokym najdeme container tag
        cur: Optional[Tag] = lca
        while cur is not None and isinstance(cur, Tag):
            if cur.name in CONTAINER_TAGS:
                return cur
            cur = cur.parent
        return None
    except Exception as e:
        logger.debug("hds.find_lca exception: %s", e)
        return None
