"""Dedup extracted data across multiple Gemini batches."""
from __future__ import annotations

import logging
import re
import unicodedata

from app.core.extractors.hds_v3.parser import ParseResult
from app.core.extractors.hds_v3.types import HdsExtractedFact, HdsFAQ
from app.core.extractors.types import ExtractedProduct

logger = logging.getLogger(__name__)


class Deduplicator:
    """Dedup products by normalized name, contacts by phone/address, FAQs by question."""

    def deduplicate(self, parse: ParseResult) -> ParseResult:
        """Remove duplicates. Mutates and returns parse."""
        before = (len(parse.products), len(parse.contacts), len(parse.faqs))

        parse.products = self._dedup_products(parse.products)
        parse.contacts = self._dedup_contacts(parse.contacts)
        parse.faqs = self._dedup_faqs(parse.faqs)

        after = (len(parse.products), len(parse.contacts), len(parse.faqs))
        logger.info(
            "Dedup: products %d->%d, contacts %d->%d, faqs %d->%d",
            before[0], after[0], before[1], after[1], before[2], after[2],
        )
        return parse

    def _normalize_name(self, name: str) -> str:
        """NFKD + lowercase + strip diacritics + collapse whitespace."""
        if not name:
            return ""
        nfkd = unicodedata.normalize("NFKD", name)
        no_diacritics = "".join(c for c in nfkd if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", no_diacritics.lower().strip())

    def _product_completeness(self, p: ExtractedProduct) -> int:
        return (
            (1 if p.price_eur else 0)
            + (1 if p.description else 0)
            + (1 if p.price_unit else 0)
            + len(p.attributes or {})
        )

    def _dedup_products(
        self, products: list[ExtractedProduct]
    ) -> list[ExtractedProduct]:
        groups: dict[str, ExtractedProduct] = {}
        for p in products:
            key = self._normalize_name(p.name)
            if not key:
                continue
            if key not in groups:
                groups[key] = p
                continue
            existing = groups[key]
            if self._product_completeness(p) > self._product_completeness(existing):
                groups[key] = p
        return list(groups.values())

    def _dedup_contacts(
        self, contacts: list[HdsExtractedFact]
    ) -> list[HdsExtractedFact]:
        seen: set[tuple[str, str]] = set()
        result: list[HdsExtractedFact] = []
        for c in contacts:
            meta = c.meta or {}
            key: tuple[str, str] | None = None
            if c.type == "contact":
                phone = meta.get("phone") or ""
                if phone:
                    key = ("phone", re.sub(r"\D", "", phone))
                elif meta.get("email"):
                    key = ("email", (meta.get("email") or "").lower().strip())
            elif c.type == "address":
                key = ("addr", self._normalize_name(c.content or ""))
            elif c.type == "social":
                key = ("social", (meta.get("url") or c.content or "").lower())
            else:
                key = ("info", self._normalize_name(c.content or "")[:120])

            if key is None:
                result.append(c)
                continue
            if key in seen:
                continue
            seen.add(key)
            result.append(c)
        return result

    def _dedup_faqs(self, faqs: list[HdsFAQ]) -> list[HdsFAQ]:
        seen: set[str] = set()
        result: list[HdsFAQ] = []
        for f in faqs:
            key = self._normalize_name(f.question)[:60]
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(f)
        return result
