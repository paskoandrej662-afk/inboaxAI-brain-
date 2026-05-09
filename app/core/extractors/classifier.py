from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

from app.core.llm.anthropic_client import call_haiku

logger = logging.getLogger(__name__)

# (regex, page_type, value_score)
URL_HEURISTICS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r'/(faq|najcastejsie-otazky|casto-kladene-otazky|otazky-a-odpovede)(/|$)', re.IGNORECASE), 'faq', 0.95),
    (re.compile(r'/(kontakt|contact|kontakty)(/|$)', re.IGNORECASE), 'contact', 0.9),
    (re.compile(r'/(o-nas|about|about-us|kto-sme)(/|$)', re.IGNORECASE), 'about', 0.85),
    (re.compile(r'/(galeria|gallery|fotogaleria|fotky)(/|$)', re.IGNORECASE), 'gallery', 0.7),
    (re.compile(r'/(blog|novinky|aktuality|clanky)(/|$)', re.IGNORECASE), 'blog_post', 0.6),
    (re.compile(r'/(cennik|ceny|pricing|cena)(/|$)', re.IGNORECASE), 'service_pricing', 0.9),
    (re.compile(r'/(produkty|products|tovar|katalog)(/|$)', re.IGNORECASE), 'product_listing', 0.85),
    (re.compile(r'/(sluzby|services)(/|$)', re.IGNORECASE), 'product_listing', 0.85),
    (re.compile(r'/(produkt|product)/[^/]+', re.IGNORECASE), 'product_detail', 0.85),
    (re.compile(r'/(legal|terms|gdpr|cookies|ochrana-osobnych-udajov|obchodne-podmienky|podmienky)(/|$)', re.IGNORECASE), 'other_low_value', 0.95),
]

VALID_TYPES = (
    'product_listing', 'product_detail', 'service_pricing', 'faq',
    'contact', 'about', 'home', 'gallery', 'blog_post', 'other_low_value',
)


def heuristic_classify(url: str) -> tuple[str, float] | None:
    """Skús URL heuristiky. Vráti (page_type, value_score) alebo None."""
    try:
        parsed = urlparse(url)
        path = parsed.path or '/'
        if path in ('', '/', '/index', '/index.html', '/index.php', '/home'):
            return ('home', 0.85)
        for pattern, ptype, score in URL_HEURISTICS:
            if pattern.search(path):
                return (ptype, score)
    except Exception as e:
        logger.warning("heuristic_classify failed for %s: %s", url, e)
    return None


CLASSIFIER_SYSTEM = """Si klasifikator typu webovej stranky pre slovensky business web. Pozri si URL, title a kratky text excerpt a urci typ stranky.

Mozne typy:
- product_listing: zoznam produktov/sluzieb
- product_detail: detail jedneho produktu
- service_pricing: cennik sluzieb
- faq: casto kladene otazky
- contact: kontakt, telefony, adresa
- about: o nas, popis firmy
- home: hlavna domovska stranka
- gallery: galeria fotiek
- blog_post: clanok / novinka
- other_low_value: cookies, GDPR, terms, prazdna stranka, junk

Tvoja odpoved JE IBA validny JSON v tomto formate:
{"type": "<type>", "value_score": 0.0_az_1.0}

value_score: 0 = junk (cookie banner), 1 = critical info (cennik, kontakt). Pre product_*/faq/service_pricing typicky 0.8-0.95. Pre other_low_value typicky 0.0-0.2.

Bez ziadneho dalsieho textu. IBA JSON.
"""


async def classify_page(url: str, title: str, text_excerpt: str) -> tuple[str, float]:
    """Vrati (page_type, value_score). Najprv URL heuristics, fallback Haiku LLM."""
    h = heuristic_classify(url)
    if h is not None:
        return h

    user_msg = f"URL: {url}\nTitle: {title}\n\nText (prvych 600 znakov):\n{text_excerpt[:600]}"
    try:
        response = await call_haiku(
            system=CLASSIFIER_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=100,
            temperature=0.0,
        )
        text = response.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            text = '\n'.join(l for l in lines if not l.startswith('```')).strip()
        data = json.loads(text)
        ptype = str(data.get('type', 'other_low_value'))
        score = float(data.get('value_score', 0.3))
        if ptype not in VALID_TYPES:
            ptype = 'other_low_value'
        score = max(0.0, min(1.0, score))
        return (ptype, score)
    except Exception as e:
        logger.warning("classify_page LLM failed for %s: %s", url, e)
        return ('other_low_value', 0.3)
