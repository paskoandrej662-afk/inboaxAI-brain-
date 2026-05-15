"""Anti-halucinacia validator for HDS-v3 extracted data."""
from __future__ import annotations

import logging
import re

from app.core.extractors.hds_v3.parser import ParseResult
from app.core.extractors.hds_v3.types import HdsExtractedFact, HdsFAQ
from app.core.extractors.types import ExtractedProduct

logger = logging.getLogger(__name__)


class Validator:
    """Validate extracted data against regex rules + sanity checks.

    Anti-halucinacia: drop items that look fabricated or malformed.
    Conservative — when in doubt, keep (downstream merger handles dups).
    """

    PHONE_RE = re.compile(
        r"^(\+421\s?\d{2,3}\s?\d{3}\s?\d{3,4}|0\d{3}\s?\d{3}\s?\d{3})$"
    )
    EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

    NEUVEDENE_RE = re.compile(r"\bneuveden", re.IGNORECASE)

    def validate(self, parse: ParseResult) -> ParseResult:
        """Filter out invalid items. Mutates and returns parse."""
        before = (len(parse.products), len(parse.contacts), len(parse.faqs))

        parse.products = [p for p in parse.products if self._validate_product(p)]
        parse.contacts = [c for c in parse.contacts if self._validate_contact(c)]
        parse.faqs = [f for f in parse.faqs if self._validate_faq(f)]

        after = (len(parse.products), len(parse.contacts), len(parse.faqs))
        logger.info(
            "Validation: products %d->%d, contacts %d->%d, faqs %d->%d",
            before[0], after[0], before[1], after[1], before[2], after[2],
        )
        return parse

    def _validate_product(self, p: ExtractedProduct) -> bool:
        if not p.name or len(p.name.strip()) < 2:
            return False
        if self.NEUVEDENE_RE.search(p.name):
            return False
        if p.price_eur is not None and (p.price_eur < 0 or p.price_eur > 1_000_000):
            logger.warning(
                "Drop product with insane price: %s = %s", p.name, p.price_eur
            )
            return False
        return True

    def _validate_contact(self, c: HdsExtractedFact) -> bool:
        meta = c.meta or {}

        if c.type == "contact":
            phone = meta.get("phone") or ""
            email = meta.get("email") or ""
            has_phone = phone and not self.NEUVEDENE_RE.search(phone)
            has_email = email and not self.NEUVEDENE_RE.search(email)

            if has_phone:
                normalized = re.sub(r"[\s\-]+", " ", phone.strip())
                if not self.PHONE_RE.match(normalized):
                    logger.warning("Drop contact with invalid phone: %s", phone)
                    return False
            if has_email:
                if not self.EMAIL_RE.match(email.strip()):
                    logger.warning("Drop contact with invalid email: %s", email)
                    return False
            if not has_phone and not has_email:
                return False
            return True

        if c.type == "address":
            content = c.content or ""
            if not re.search(r"\d", content):
                logger.warning(
                    "Drop address without numbers (probably halucinacia): %s", content
                )
                return False
            return True

        if c.type == "social":
            url = (c.meta or {}).get("url") or c.content
            if not url or "." not in url:
                return False
            return True

        # info/geo facts — pass through if non-empty
        return bool(c.content and c.content.strip())

    def _validate_faq(self, f: HdsFAQ) -> bool:
        if not f.question or len(f.question.strip()) < 5:
            return False
        if not f.answer or len(f.answer.strip()) < 3:
            return False
        return True
