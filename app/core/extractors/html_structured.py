from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.core.extractors.types import (
    ExtractedBusinessFact,
    ExtractedFaqItem,
    ExtractedImage,
    ExtractedProduct,
)

logger = logging.getLogger(__name__)

# Regex patterny pre SK/CZ kontaktne data
PHONE_RE = re.compile(r'(?:\+421|\+420|00421|00420|0)\s?\d{2,3}\s?\d{3}\s?\d{3}\b')
EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
ICO_RE = re.compile(r'\bI[ČC]O\s*[:\s]+(\d{8})\b')
IBAN_RE = re.compile(r'\b[A-Z]{2}\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{0,4}\b')
ADDRESS_RE = re.compile(
    r'([A-ZŠČŤŽÝÁÍÉÚÔŇĎĽ][A-Za-zšščťžýáíéúôňďľ]+(?:\s+[A-Za-zšščťžýáíéúôňďľ]+)*\s+\d+[a-zA-Z]?)\s*,?\s*(\d{3}\s?\d{2})\s+([A-ZŠČŤŽÝÁÍÉÚÔŇĎĽ][A-Za-zšščťžýáíéúôňďľ]+)'
)


def _parse_jsonld_blocks(html: str) -> list[dict]:
    """Najdi vsetky <script type='application/ld+json'> bloky.

    Skipni bloky ktore nie su validny JSON. Vrati zoznam dict objektov
    (rozbal aj @graph arrays).
    """
    try:
        soup = BeautifulSoup(html, 'html.parser')
    except Exception as e:
        logger.warning("html parse failed: %s", e)
        return []

    blocks: list[dict] = []
    for script in soup.find_all('script', {'type': 'application/ld+json'}):
        try:
            text = script.string or script.get_text()
            if not text or not text.strip():
                continue
            data = json.loads(text)
            if isinstance(data, dict) and '@graph' in data:
                graph = data['@graph']
                if isinstance(graph, list):
                    for item in graph:
                        if isinstance(item, dict):
                            blocks.append(item)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        blocks.append(item)
            elif isinstance(data, dict):
                blocks.append(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.debug("jsonld parse skip: %s", e)
    return blocks


def _matches_type(obj: dict, target_types: tuple[str, ...]) -> bool:
    """JSON-LD @type moze byt string alebo list."""
    t = obj.get('@type')
    if isinstance(t, list):
        return any(s in target_types for s in t)
    return t in target_types


def _extract_offer_price(offer: Any) -> tuple[str | None, float | None, str | None]:
    """Z 'offers' (Offer alebo AggregateOffer) vytiahni (price_text, price_eur, currency)."""
    if not isinstance(offer, dict):
        return None, None, None
    price = offer.get('price') or offer.get('lowPrice')
    currency = offer.get('priceCurrency')
    if price is None:
        return None, None, None
    try:
        price_eur = float(price) if currency in ('EUR', None) else None
    except (ValueError, TypeError):
        price_eur = None
    price_text = f"{price} {currency}" if currency else str(price)
    return price_text, price_eur, currency


# ───────────────────────────────────────────────────
# Verejne API
# ───────────────────────────────────────────────────


def extract_jsonld_products(html: str, source_url: str) -> list[ExtractedProduct]:
    """Najdi vsetky schema.org Product/Service entries.

    Confidence=0.95, source_type='jsonld', verified=True.
    """
    out: list[ExtractedProduct] = []
    try:
        for obj in _parse_jsonld_blocks(html):
            if not isinstance(obj, dict):
                continue
            if not _matches_type(obj, ('Product', 'Service', 'IndividualProduct')):
                continue
            name = obj.get('name')
            if not name:
                continue
            description = obj.get('description')
            image = obj.get('image')
            if isinstance(image, list):
                image_url = image[0] if image else None
            elif isinstance(image, dict):
                image_url = image.get('url')
            else:
                image_url = image

            offers = obj.get('offers')
            price_text, price_eur, _currency = None, None, None
            if isinstance(offers, list) and offers:
                price_text, price_eur, _currency = _extract_offer_price(offers[0])
            elif isinstance(offers, dict):
                price_text, price_eur, _currency = _extract_offer_price(offers)

            attributes: dict[str, str] = {}
            for key in ('brand', 'sku', 'gtin', 'category', 'color', 'material', 'size'):
                val = obj.get(key)
                if isinstance(val, str):
                    attributes[key] = val
                elif isinstance(val, dict) and 'name' in val:
                    attributes[key] = str(val['name'])

            out.append(ExtractedProduct(
                name=str(name).strip(),
                description=str(description).strip() if description else None,
                price_text=price_text,
                price_eur=price_eur,
                price_unit=None,
                attributes=attributes,
                image_url=str(image_url) if image_url else None,
                source_url=source_url,
                source_type='jsonld',
                confidence=0.95,
                verified=True,
            ))
    except Exception as e:
        logger.warning("extract_jsonld_products failed for %s: %s", source_url, e)
    return out


def extract_jsonld_faq(html: str, source_url: str) -> list[ExtractedFaqItem]:
    """Najdi schema.org FAQPage entries."""
    out: list[ExtractedFaqItem] = []
    try:
        for obj in _parse_jsonld_blocks(html):
            if not isinstance(obj, dict):
                continue
            if not _matches_type(obj, ('FAQPage',)):
                continue
            main = obj.get('mainEntity') or []
            if not isinstance(main, list):
                main = [main]
            for q in main:
                if not isinstance(q, dict):
                    continue
                question = q.get('name')
                answer_obj = q.get('acceptedAnswer')
                answer = None
                if isinstance(answer_obj, dict):
                    answer = answer_obj.get('text')
                if question and answer:
                    out.append(ExtractedFaqItem(
                        question=str(question).strip(),
                        answer=str(answer).strip(),
                        source_url=source_url,
                        source_type='jsonld',
                        confidence=0.95,
                    ))
    except Exception as e:
        logger.warning("extract_jsonld_faq failed for %s: %s", source_url, e)
    return out


def extract_jsonld_business(html: str, source_url: str) -> list[ExtractedBusinessFact]:
    """Najdi schema.org LocalBusiness/Organization/Restaurant/Store entries."""
    out: list[ExtractedBusinessFact] = []
    try:
        target = (
            'LocalBusiness', 'Organization', 'Restaurant', 'Store',
            'HairSalon', 'BeautySalon', 'AutoRepair', 'Hotel', 'LodgingBusiness',
        )
        for obj in _parse_jsonld_blocks(html):
            if not isinstance(obj, dict):
                continue
            if not _matches_type(obj, target):
                continue

            tel = obj.get('telephone')
            if tel:
                out.append(ExtractedBusinessFact(
                    key='phone', value=str(tel).strip(),
                    source_url=source_url, source_type='jsonld', confidence=0.95,
                ))

            email = obj.get('email')
            if email:
                out.append(ExtractedBusinessFact(
                    key='email', value=str(email).strip(),
                    source_url=source_url, source_type='jsonld', confidence=0.95,
                ))

            addr = obj.get('address')
            if isinstance(addr, dict):
                parts = [
                    addr.get('streetAddress'),
                    addr.get('postalCode'),
                    addr.get('addressLocality'),
                ]
                addr_str = ' '.join(p for p in parts if p)
                if addr_str.strip():
                    out.append(ExtractedBusinessFact(
                        key='address', value=addr_str.strip(),
                        source_url=source_url, source_type='jsonld', confidence=0.95,
                    ))
            elif isinstance(addr, str) and addr.strip():
                out.append(ExtractedBusinessFact(
                    key='address', value=addr.strip(),
                    source_url=source_url, source_type='jsonld', confidence=0.95,
                ))

            hours = obj.get('openingHours') or obj.get('openingHoursSpecification')
            if hours:
                if isinstance(hours, list):
                    hours_str = '; '.join(str(h) for h in hours if h)
                else:
                    hours_str = str(hours)
                if hours_str.strip():
                    out.append(ExtractedBusinessFact(
                        key='hours', value=hours_str.strip()[:500],
                        source_url=source_url, source_type='jsonld', confidence=0.95,
                    ))

            name = obj.get('name')
            if name:
                out.append(ExtractedBusinessFact(
                    key='company_name', value=str(name).strip(),
                    source_url=source_url, source_type='jsonld', confidence=0.95,
                ))

            same_as = obj.get('sameAs')
            if isinstance(same_as, list):
                for url in same_as:
                    if not isinstance(url, str):
                        continue
                    key = 'other'
                    if 'facebook.com' in url:
                        key = 'social_facebook'
                    elif 'instagram.com' in url:
                        key = 'social_instagram'
                    elif 'tiktok.com' in url:
                        key = 'social_tiktok'
                    out.append(ExtractedBusinessFact(
                        key=key, value=url,
                        source_url=source_url, source_type='jsonld', confidence=0.95,
                    ))
    except Exception as e:
        logger.warning("extract_jsonld_business failed for %s: %s", source_url, e)
    return out


def extract_opengraph(html: str, source_url: str) -> dict[str, str]:
    """Vrati dict s og:title, og:description, og:image, og:type, og:site_name."""
    out: dict[str, str] = {}
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for meta in soup.find_all('meta'):
            prop = meta.get('property') or meta.get('name')
            if prop and prop.startswith('og:'):
                content = meta.get('content')
                if content:
                    out[prop] = content.strip()
    except Exception as e:
        logger.warning("extract_opengraph failed for %s: %s", source_url, e)
    return out


def extract_meta(html: str, source_url: str) -> dict[str, str]:
    """Vrati description, keywords, canonical, robots z meta tagov."""
    out: dict[str, str] = {}
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for meta in soup.find_all('meta'):
            name = meta.get('name')
            if name in ('description', 'keywords', 'robots', 'author'):
                content = meta.get('content')
                if content:
                    out[name] = content.strip()
        canonical = soup.find('link', {'rel': 'canonical'})
        if canonical and canonical.get('href'):
            out['canonical'] = canonical['href']
    except Exception as e:
        logger.warning("extract_meta failed for %s: %s", source_url, e)
    return out


def extract_contact_patterns(text: str, source_url: str) -> list[ExtractedBusinessFact]:
    """Regex extraction zo strankoveho textu: telefony SK/CZ, emaily, IČO, IBAN, adresy."""
    out: list[ExtractedBusinessFact] = []
    seen: set[tuple[str, str]] = set()

    try:
        for m in PHONE_RE.finditer(text):
            val = m.group(0).strip()
            normalized = re.sub(r'\s+', '', val)
            key = ('phone', normalized)
            if key not in seen:
                seen.add(key)
                out.append(ExtractedBusinessFact(
                    key='phone', value=val,
                    source_url=source_url, source_type='regex', confidence=0.85,
                ))

        for m in EMAIL_RE.finditer(text):
            val = m.group(0).strip().lower()
            key = ('email', val)
            if key not in seen:
                seen.add(key)
                out.append(ExtractedBusinessFact(
                    key='email', value=val,
                    source_url=source_url, source_type='regex', confidence=0.85,
                ))

        for m in ICO_RE.finditer(text):
            val = m.group(1).strip()
            key = ('ico', val)
            if key not in seen:
                seen.add(key)
                out.append(ExtractedBusinessFact(
                    key='ico', value=val,
                    source_url=source_url, source_type='regex', confidence=0.85,
                ))

        for m in IBAN_RE.finditer(text):
            val = re.sub(r'\s+', '', m.group(0)).strip()
            key = ('iban', val)
            if key not in seen:
                seen.add(key)
                out.append(ExtractedBusinessFact(
                    key='iban', value=val,
                    source_url=source_url, source_type='regex', confidence=0.85,
                ))

        for m in ADDRESS_RE.finditer(text):
            val = ' '.join(m.groups()).strip()
            key = ('address', val.lower())
            if key not in seen:
                seen.add(key)
                out.append(ExtractedBusinessFact(
                    key='address', value=val,
                    source_url=source_url, source_type='regex', confidence=0.75,
                ))
    except Exception as e:
        logger.warning("extract_contact_patterns failed for %s: %s", source_url, e)

    return out


def extract_image_refs(html: str, base_url: str) -> list[ExtractedImage]:
    """Vsetky <img src=...> + og:image. Resolve relative URLs voci base_url."""
    out: list[ExtractedImage] = []
    seen: set[str] = set()

    try:
        soup = BeautifulSoup(html, 'html.parser')
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if not src:
                continue
            abs_url = urljoin(base_url, src)
            if abs_url in seen:
                continue
            seen.add(abs_url)
            alt = img.get('alt') or None

            width = None
            height = None
            w_attr = img.get('width', '')
            h_attr = img.get('height', '')
            try:
                if isinstance(w_attr, str) and w_attr.isdigit():
                    width = int(w_attr)
            except (ValueError, TypeError):
                width = None
            try:
                if isinstance(h_attr, str) and h_attr.isdigit():
                    height = int(h_attr)
            except (ValueError, TypeError):
                height = None

            out.append(ExtractedImage(
                url=abs_url, alt_text=alt, width=width, height=height,
                source_url=base_url,
            ))

        og = extract_opengraph(html, base_url)
        if 'og:image' in og:
            og_url = urljoin(base_url, og['og:image'])
            if og_url not in seen:
                seen.add(og_url)
                out.append(ExtractedImage(url=og_url, source_url=base_url))
    except Exception as e:
        logger.warning("extract_image_refs failed for %s: %s", base_url, e)

    return out
