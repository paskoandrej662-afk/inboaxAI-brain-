"""Tests for hds_v3 MarkdownParser — offline, no network."""
from __future__ import annotations

import pytest

from app.core.extractors.hds_v3.parser import MarkdownParser
from app.core.extractors.hds_v3.types import GeminiBatchResult


def _batch(markdown: str, urls: list[str] | None = None) -> GeminiBatchResult:
    return GeminiBatchResult(
        success=True,
        urls=urls or ["https://x.sk/"],
        markdown=markdown,
    )


SAMPLE_PAGE = """=======================================
=== STRANKA 1: https://x.sk/ ===
=======================================

## IDENTIFIKACIA FIRMY (ak je na tejto stranke)
- Nazov firmy: Skákačky Orava s.r.o.
- ICO: 12345678
- DIC: SK2020123456
- Slogan/popis: neuvedene

## KONTAKTY (ak su na tejto stranke)
Tabulka VSETKYCH kontaktov:

| Meno | Pozicia | Telefon | Email |
|------|---------|---------|-------|
| Ján Novák | majiteľ | 0907 043 467 | info@x.sk |
| neuvedene | neuvedene | 0911 815 051 | neuvedene |

Adresy:
- Sidlo: Babín 420, 02952
- Prevadzka: neuvedene

Socialne siete:
- Facebook: https://www.facebook.com/skakacky
- Instagram: neuvedene

## PRODUKTY / SLUZBY (ak su na tejto stranke)

| Nazov | Cena | Jednotka | Popis | Atributy |
|-------|------|----------|-------|----------|
| Skákací hrad Tiger | 160€ | Deň | Veľký hrad | Kapacita: 9 detí, Rozmery: 8x6m, Výška: 4m |
| Skákací hrad Rozprávkovo | 180€ | Deň | neuvedene | Kapacita: 4-8 detí, Rozmery: 8x5m |
| neuvedene | neuvedene | neuvedene | neuvedene | neuvedene |

## FAQ (ak su na tejto stranke)

| Otazka | Odpoved |
|--------|---------|
| Aké sú podmienky prenájmu? | Hrad musí byť rozložený na rovnej ploche. |
| neuvedene | neuvedene |
"""


def test_parser_extracts_company_name_from_identification():
    parser = MarkdownParser()
    result = parser.parse_batches([_batch(SAMPLE_PAGE)])
    assert result.company_name == "Skákačky Orava s.r.o."


def test_parser_extracts_ico():
    parser = MarkdownParser()
    result = parser.parse_batches([_batch(SAMPLE_PAGE)])
    assert result.company_ico == "12345678"
    assert result.company_dic == "SK2020123456"


def test_parser_extracts_products_table():
    parser = MarkdownParser()
    result = parser.parse_batches([_batch(SAMPLE_PAGE)])
    names = [p.name for p in result.products]
    assert "Skákací hrad Tiger" in names
    assert "Skákací hrad Rozprávkovo" in names
    # 'neuvedene' row should NOT become a product
    assert all(p.name and "neuveden" not in p.name.lower() for p in result.products)

    tiger = next(p for p in result.products if p.name == "Skákací hrad Tiger")
    assert tiger.price_eur == 160.0
    assert tiger.price_unit == "Deň"
    assert "kapacita" in tiger.attributes
    assert tiger.source_url == "https://x.sk/"
    assert tiger.source_type == "hds_v3"


def test_parser_extracts_contacts_table():
    parser = MarkdownParser()
    result = parser.parse_batches([_batch(SAMPLE_PAGE)])
    phones = [c.meta.get("phone") for c in result.contacts if c.type == "contact"]
    assert "0907 043 467" in phones
    assert "0911 815 051" in phones

    sidla = [c for c in result.contacts if c.type == "address" and c.meta.get("address_type") == "sidlo"]
    assert len(sidla) == 1
    assert "Babín 420" in sidla[0].content

    fbs = [c for c in result.contacts if c.type == "social" and c.meta.get("network") == "facebook"]
    assert len(fbs) == 1
    assert "facebook.com" in fbs[0].content


def test_parser_extracts_faq_table():
    parser = MarkdownParser()
    result = parser.parse_batches([_batch(SAMPLE_PAGE)])
    questions = [f.question for f in result.faqs]
    assert "Aké sú podmienky prenájmu?" in questions
    # 'neuvedene' row dropped
    assert all("neuveden" not in q.lower() for q in questions)


def test_parser_handles_missing_sections():
    """Batch without IDENTIFIKACIA / KONTAKTY / etc. — no crashes."""
    parser = MarkdownParser()
    minimal = """=== STRANKA 1: https://x.sk/empty ===

## PRODUKTY / SLUZBY (ak su na tejto stranke)

| Nazov | Cena |
|-------|------|
| Sample | 50€ |
"""
    result = parser.parse_batches([_batch(minimal)])
    assert result.company_name is None
    assert len(result.products) == 1
    assert result.products[0].price_eur == 50.0
    assert len(result.contacts) == 0


def test_parser_handles_neuvedene():
    """All-neuvedene markdown returns empty results, no crashes."""
    parser = MarkdownParser()
    md = """=== STRANKA 1: https://x.sk/ ===

## IDENTIFIKACIA FIRMY
- Nazov firmy: neuvedene
- ICO: neuvedene

## PRODUKTY / SLUZBY

| Nazov | Cena |
|-------|------|
| neuvedene | neuvedene |
"""
    result = parser.parse_batches([_batch(md)])
    assert result.company_name is None
    assert result.company_ico is None
    assert len(result.products) == 0


def test_parser_splits_multiple_pages():
    """Two pages in single markdown -> products from both attributed correctly."""
    parser = MarkdownParser()
    md = """=== STRANKA 1: https://x.sk/p1 ===

## PRODUKTY / SLUZBY

| Nazov | Cena |
|-------|------|
| Product A | 100€ |

=== STRANKA 2: https://x.sk/p2 ===

## PRODUKTY / SLUZBY

| Nazov | Cena |
|-------|------|
| Product B | 200€ |
"""
    result = parser.parse_batches([_batch(md)])
    assert len(result.products) == 2
    by_name = {p.name: p for p in result.products}
    assert by_name["Product A"].source_url == "https://x.sk/p1"
    assert by_name["Product B"].source_url == "https://x.sk/p2"
