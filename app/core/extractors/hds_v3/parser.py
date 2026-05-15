"""Parse Gemini markdown output to ExtractedProduct / HdsExtractedFact / HdsFAQ.

Gemini emits ASCII-form headers (per prompts.py), but values within sections
preserve diacritics. Header matching is diacritic-tolerant; values pass through
verbatim.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from app.core.extractors.hds_v3.types import (
    GeminiBatchResult,
    HdsExtractedFact,
    HdsFAQ,
)
from app.core.extractors.types import ExtractedProduct

logger = logging.getLogger(__name__)


def _strip_diacritics(text: str) -> str:
    """NFKD + remove combining marks (for diacritic-tolerant header match)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


@dataclass
class ParseResult:
    """Output of parser for one or more Gemini batches."""

    products: list[ExtractedProduct] = field(default_factory=list)
    contacts: list[HdsExtractedFact] = field(default_factory=list)
    facts: list[HdsExtractedFact] = field(default_factory=list)
    faqs: list[HdsFAQ] = field(default_factory=list)
    company_name: Optional[str] = None
    company_ico: Optional[str] = None
    company_dic: Optional[str] = None
    parsing_errors: list[str] = field(default_factory=list)


class MarkdownParser:
    """Parse Gemini batch markdown output.

    Markdown structure (per prompts.py BATCH_EXTRACTION_PROMPT):
        === STRANKA 1: https://x.sk/ ===
        ## IDENTIFIKACIA FIRMY (ak je na tejto stranke)
        - Nazov firmy: X
        ## KONTAKTY (ak su na tejto stranke)
        | Meno | Pozicia | Telefon | Email |
        ## PRODUKTY / SLUZBY (ak su na tejto stranke)
        | Nazov | Cena | Jednotka | Popis | Atributy |
        ## FAQ (ak su na tejto stranke)
        | Otazka | Odpoved |
    """

    PAGE_HEADER_RE = re.compile(r"===\s*STRANKA\s*\d+\s*:\s*(.+?)\s*===")
    SECTION_HEADER_RE = re.compile(r"^##\s+(.+?)$", re.MULTILINE)
    NEUVEDENE_RE = re.compile(r"\bneuveden", re.IGNORECASE)

    def parse_batches(self, batches: list[GeminiBatchResult]) -> ParseResult:
        """Parse all batches into single ParseResult."""
        result = ParseResult()

        for batch in batches:
            if not batch.success or not batch.markdown:
                continue
            try:
                self._parse_single_markdown(batch.markdown, result)
            except Exception as e:  # noqa: BLE001
                logger.exception("Failed to parse batch markdown")
                result.parsing_errors.append(str(e)[:200])

        return result

    def _parse_single_markdown(self, markdown: str, result: ParseResult) -> None:
        pages = self._split_into_pages(markdown)
        for page_url, page_content in pages.items():
            self._parse_page(page_url, page_content, result)

    def _split_into_pages(self, markdown: str) -> dict[str, str]:
        """Split markdown by === STRANKA N: URL === headers (diacritic-tolerant)."""
        ascii_markdown = _strip_diacritics(markdown)
        matches = list(self.PAGE_HEADER_RE.finditer(ascii_markdown))

        if not matches:
            return {"unknown": markdown}

        pages: dict[str, str] = {}
        for i, match in enumerate(matches):
            url = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            pages[url] = markdown[start:end]
        return pages

    def _normalize_section_name(self, name: str) -> str:
        """ASCII upper + collapse whitespace for matching."""
        return re.sub(r"\s+", " ", _strip_diacritics(name).upper().strip())

    def _split_into_sections(self, content: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        matches = list(self.SECTION_HEADER_RE.finditer(content))
        for i, match in enumerate(matches):
            raw_name = match.group(1).strip()
            # Strip parenthetical hints like "(ak je na tejto stranke)"
            base_name = re.sub(r"\s*\([^)]*\)\s*$", "", raw_name)
            key = self._normalize_section_name(base_name)
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            sections[key] = content[start:end].strip()
        return sections

    def _parse_page(self, page_url: str, content: str, result: ParseResult) -> None:
        sections = self._split_into_sections(content)

        ident = sections.get("IDENTIFIKACIA FIRMY", "")
        if ident:
            self._parse_identification(ident, result)

        kontakty = sections.get("KONTAKTY", "")
        if kontakty:
            self._parse_kontakty(kontakty, page_url, result)

        produkty = (
            sections.get("PRODUKTY / SLUZBY", "")
            or sections.get("PRODUKTY/SLUZBY", "")
            or sections.get("PRODUKTY", "")
        )
        if produkty:
            self._parse_produkty(produkty, page_url, result)

        faq_section = sections.get("FAQ", "")
        if faq_section:
            self._parse_faq(faq_section, page_url, result)

        for section_name in (
            "CENOVE PODMIENKY",
            "PROCES OBJEDNAVKY",
            "TECHNICKE PODMIENKY",
            "GEOGRAFIA",
            "SPECIALNE POZNAMKY",
        ):
            section_text = sections.get(section_name, "")
            if section_text.strip() and not self._is_all_neuvedene(section_text):
                result.facts.append(
                    HdsExtractedFact(
                        type="info",
                        content=f"{section_name}: {section_text.strip()}"[:2000],
                        source_url=page_url,
                        meta={"section": section_name},
                    )
                )

    def _is_all_neuvedene(self, text: str) -> bool:
        """Detect section that is purely 'neuvedene' placeholders."""
        cleaned = re.sub(r"[^a-záčďéíľĺňóšťúýžA-Z0-9]+", " ", text).strip().lower()
        if not cleaned:
            return True
        words = cleaned.split()
        if not words:
            return True
        meaningful = [
            w for w in words if "neuveden" not in w and w not in {"a", "i", "o", "u"}
        ]
        return len(meaningful) < 2

    def _parse_identification(self, text: str, result: ParseResult) -> None:
        # Diacritic-tolerant key match using ascii projection
        ascii_text = _strip_diacritics(text)
        for line in text.split("\n"):
            ascii_line = _strip_diacritics(line)
            m_name = re.search(r"Nazov firmy:\s*(.+?)$", ascii_line)
            if m_name:
                # Re-extract from original line to preserve diacritics in value
                colon_idx = line.find(":")
                if colon_idx >= 0:
                    value = line[colon_idx + 1 :].strip()
                    if value and not self.NEUVEDENE_RE.search(value) and not result.company_name:
                        result.company_name = value

            m_ico = re.search(r"ICO:\s*(\d+)", ascii_line)
            if m_ico and not result.company_ico:
                result.company_ico = m_ico.group(1)

            m_dic = re.search(r"DIC:\s*(\S+)", ascii_line)
            if m_dic and not result.company_dic:
                value = m_dic.group(1)
                if not self.NEUVEDENE_RE.search(value):
                    result.company_dic = value

        # Keep ascii_text for clarity / future expansion
        _ = ascii_text

    def _parse_kontakty(
        self, text: str, page_url: str, result: ParseResult
    ) -> None:
        # People table
        rows = self._parse_markdown_table(text)
        for row in rows:
            if len(row) < 4:
                continue
            name, position, phone, email = row[0], row[1], row[2], row[3]
            has_phone = phone and not self.NEUVEDENE_RE.search(phone)
            has_email = email and not self.NEUVEDENE_RE.search(email)
            if not has_phone and not has_email:
                continue
            result.contacts.append(
                HdsExtractedFact(
                    type="contact",
                    content=f"{name} ({position}): {phone} {email}".strip(),
                    source_url=page_url,
                    meta={
                        "name": name if not self.NEUVEDENE_RE.search(name) else None,
                        "position": position
                        if not self.NEUVEDENE_RE.search(position)
                        else None,
                        "phone": phone if has_phone else None,
                        "email": email if has_email else None,
                    },
                )
            )

        # Addresses
        for line in text.split("\n"):
            ascii_line = _strip_diacritics(line)
            for label, addr_type in (
                ("Sidlo:", "sidlo"),
                ("Prevadzka:", "prevadzka"),
            ):
                if ascii_line.lstrip("- ").startswith(label):
                    value = line.split(":", 1)[1].strip() if ":" in line else ""
                    if value and not self.NEUVEDENE_RE.search(value):
                        result.contacts.append(
                            HdsExtractedFact(
                                type="address",
                                content=f"{label[:-1]}: {value}",
                                source_url=page_url,
                                meta={"address_type": addr_type, "value": value},
                            )
                        )

        # Social
        for line in text.split("\n"):
            ascii_line = _strip_diacritics(line)
            for label, net in (
                ("Facebook:", "facebook"),
                ("Instagram:", "instagram"),
            ):
                if ascii_line.lstrip("- ").startswith(label):
                    value = line.split(":", 1)[1].strip() if ":" in line else ""
                    if (
                        value
                        and not self.NEUVEDENE_RE.search(value)
                        and ("http" in value.lower() or "." in value)
                    ):
                        result.contacts.append(
                            HdsExtractedFact(
                                type="social",
                                content=f"{net}: {value}",
                                source_url=page_url,
                                meta={"network": net, "url": value},
                            )
                        )

    def _parse_produkty(
        self, text: str, page_url: str, result: ParseResult
    ) -> None:
        rows = self._parse_markdown_table(text)
        for row in rows:
            if len(row) < 2:
                continue

            name = row[0].strip()
            if not name or self.NEUVEDENE_RE.search(name):
                continue

            price_text = row[1] if len(row) > 1 else ""
            unit = row[2] if len(row) > 2 else ""
            popis = row[3] if len(row) > 3 else ""
            attributes_text = row[4] if len(row) > 4 else ""

            price_eur = self._parse_price(price_text)
            attrs = self._parse_attributes(attributes_text)

            description = popis.strip() if popis and not self.NEUVEDENE_RE.search(popis) else None
            price_unit = unit.strip() if unit and not self.NEUVEDENE_RE.search(unit) else None
            price_text_clean = (
                price_text.strip()
                if price_text and not self.NEUVEDENE_RE.search(price_text)
                else None
            )

            result.products.append(
                ExtractedProduct(
                    name=name,
                    description=description,
                    price_text=price_text_clean,
                    price_eur=price_eur,
                    price_unit=price_unit,
                    attributes=attrs,
                    source_url=page_url,
                    source_type="hds_v3",
                )
            )

    def _parse_faq(
        self, text: str, page_url: str, result: ParseResult
    ) -> None:
        rows = self._parse_markdown_table(text)
        for row in rows:
            if len(row) < 2:
                continue
            question, answer = row[0].strip(), row[1].strip()
            if not question or not answer:
                continue
            if self.NEUVEDENE_RE.search(question) or self.NEUVEDENE_RE.search(answer):
                continue
            result.faqs.append(
                HdsFAQ(question=question, answer=answer, source_url=page_url)
            )

    def _parse_markdown_table(self, text: str) -> list[list[str]]:
        """Parse markdown table. Skip header row and separator row."""
        rows: list[list[str]] = []
        seen_separator = False
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                seen_separator = True
                continue
            if not seen_separator:
                continue  # skip header before separator
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if cells:
                rows.append(cells)
        return rows

    def _parse_price(self, text: str) -> Optional[float]:
        """Extract numeric price from text like '160€/Deň' or 'od 80€'."""
        if not text or self.NEUVEDENE_RE.search(text):
            return None
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*€", text)
        if match:
            try:
                return float(match.group(1).replace(",", "."))
            except ValueError:
                return None
        return None

    def _parse_attributes(self, text: str) -> dict[str, str]:
        """Parse 'Kapacita: 9 deti, Rozmery: 8x6m, Vek: 2-14' into dict."""
        if not text or self.NEUVEDENE_RE.search(text[:30]):
            return {}
        attrs: dict[str, str] = {}
        # Match "Key: Value" segments separated by commas
        # Key starts with a capital, value goes until next ", Key:" or end
        pattern = re.compile(
            r"([A-ZČŠŽÁÉÍÓÚÝÄĎĹĽŇŔŤÔ][\w\sáčďéíľĺňóšťúýžäĺľôŕťÁČĎÉÍĽĹŇÓŠŤÚÝŽÄÔŔ]{1,40}?)"
            r"\s*:\s*"
            r"([^,]+(?:,\s*\d[^,]*)*)",
            re.UNICODE,
        )
        for match in pattern.finditer(text):
            key = match.group(1).strip().lower()
            value = match.group(2).strip().rstrip(",")
            if value and not self.NEUVEDENE_RE.search(value):
                attrs[key] = value
        return attrs
