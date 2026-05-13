from __future__ import annotations

import logging
import re

from app.core.extractors.types import ExtractedBusinessFact, ExtractedProduct

logger = logging.getLogger(__name__)

NUMBER_RE = re.compile(r'\d+[.,]?\d*')
DIGITS_ONLY_RE = re.compile(r'\D')

# Soft-pricing keywords — non-numeric prices (e.g., "dohodou", "individualne").
# If price_text contains one of these AND the same keyword appears in page text,
# verification passes without numeric match.
SOFT_PRICE_KEYWORDS = (
    'dohod',          # dohodou, dohodov, dohode
    'na vyziadanie',
    'na poziadanie',
    'individual',     # individualne, individualny
    'na vyzadanie',
)


def _normalize_for_match(s: str) -> str:
    """Lowercase, remove all whitespace + nbsp."""
    return (
        s.lower()
        .replace(' ', '')
        .replace(' ', '')
        .replace('\t', '')
        .replace('\n', '')
    )


def verify_product(p: ExtractedProduct, page_text: str) -> ExtractedProduct:
    """Set p.verified=True ak su extrahovane ceny/cisla v page_text.

    Pre source_type='jsonld' bypass — JSON-LD je structured ground truth.
    """
    if p.source_type == 'jsonld':
        p.verified = True
        return p

    text_norm = _normalize_for_match(page_text)

    # Verify price_text
    if p.price_text:
        price_norm = _normalize_for_match(p.price_text)
        price_text_lower = p.price_text.lower()
        text_lower = page_text.lower()
        soft_match = next(
            (kw for kw in SOFT_PRICE_KEYWORDS
             if kw in price_text_lower and kw in text_lower),
            None,
        )
        if soft_match:
            pass  # OK — soft pricing keyword verbatim in both price_text and page
        elif price_norm and price_norm in text_norm:
            pass  # OK — full price found verbatim
        else:
            nums = NUMBER_RE.findall(p.price_text)
            if not nums or not any(_normalize_for_match(n) in text_norm for n in nums):
                logger.warning(
                    "VERIFY FAIL: product '%s' price '%s' NOT in page text — stripping",
                    p.name, p.price_text,
                )
                p.price_text = None
                p.price_eur = None
                p.confidence = max(0.0, p.confidence - 0.3)

    # Soft check meno produktu
    if p.name:
        name_words = [w for w in re.split(r'\s+', p.name.lower()) if len(w) > 2]
        if name_words:
            text_lower = page_text.lower()
            matches = sum(1 for w in name_words if w in text_lower)
            if matches / len(name_words) < 0.5:
                p.confidence = max(0.0, p.confidence - 0.2)

    # Numeric attributes — kapacita, rozmery atd.
    for k, v in list(p.attributes.items()):
        if isinstance(v, str):
            nums = NUMBER_RE.findall(v)
            if nums:
                if not any(_normalize_for_match(n) in text_norm for n in nums):
                    logger.warning(
                        "VERIFY FAIL: product '%s' attribute %s='%s' has numbers not in page",
                        p.name, k, v,
                    )
                    p.confidence = max(0.0, p.confidence - 0.05)

    if p.confidence >= 0.4:
        p.verified = True

    return p


def verify_fact(
    f: ExtractedBusinessFact, page_text: str
) -> ExtractedBusinessFact | None:
    """Vrati None ak fact zlyhava verification.

    Trusted source_types ('jsonld', 'regex') prejdu bez kontroly.
    """
    if f.source_type in ('jsonld', 'regex'):
        return f

    if f.key == 'phone':
        digits_in_value = re.sub(r'\D', '', f.value)
        digits_in_text = re.sub(r'\D', '', page_text)
        if digits_in_value and len(digits_in_value) >= 7 and digits_in_value in digits_in_text:
            return f
        logger.warning("VERIFY FAIL phone: '%s' digits not in page text", f.value)
        return None

    if f.key == 'email':
        if f.value.lower() in page_text.lower():
            return f
        logger.warning("VERIFY FAIL email: '%s' not in page text", f.value)
        return None

    if f.key == 'ico':
        if f.value in page_text:
            return f
        return None

    # Soft check pre hours / address / social atd.
    words = [w for w in re.split(r'\s+', f.value.lower()) if len(w) > 2]
    if not words:
        return f
    text_lower = page_text.lower()
    matches = sum(1 for w in words if w in text_lower)
    ratio = matches / len(words)
    if ratio < 0.5:
        logger.warning(
            "VERIFY SOFT FAIL: fact %s='%s' (%d/%d words match)",
            f.key, f.value, matches, len(words),
        )
        f.confidence = max(0.0, f.confidence - 0.3)
        if f.confidence < 0.3:
            return None
    return f
