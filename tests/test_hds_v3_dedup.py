"""Tests for hds_v3 Deduplicator."""
from __future__ import annotations

from app.core.extractors.hds_v3.dedup import Deduplicator
from app.core.extractors.hds_v3.parser import ParseResult
from app.core.extractors.hds_v3.types import HdsExtractedFact, HdsFAQ
from app.core.extractors.types import ExtractedProduct


def test_dedup_products_by_normalized_name():
    """Tiger / TIGER / tigér -> single product (diacritic + case insensitive)."""
    d = Deduplicator()
    parse = ParseResult(
        products=[
            ExtractedProduct(name="Skákací hrad Tiger", price_eur=160.0),
            ExtractedProduct(name="SKAKACI HRAD TIGER", price_eur=160.0),
            ExtractedProduct(name="skákací hrad tigér", price_eur=160.0),
            ExtractedProduct(name="Different product", price_eur=200.0),
        ]
    )
    result = d.deduplicate(parse)
    names_normalized = {p.name.lower() for p in result.products}
    assert len(result.products) == 2
    # Both groups must be present (any case kept)
    assert any("tiger" in n.replace("é", "e") or "tig" in n for n in names_normalized)


def test_dedup_keeps_most_complete_product():
    """When duplicate names exist, keep the entry with more attributes/price/description."""
    d = Deduplicator()
    bare = ExtractedProduct(name="Tiger", price_eur=None, attributes={})
    full = ExtractedProduct(
        name="tiger",
        price_eur=160.0,
        description="Veľký hrad",
        price_unit="Deň",
        attributes={"kapacita": "9", "rozmery": "8x6m"},
    )
    parse = ParseResult(products=[bare, full])
    result = d.deduplicate(parse)
    assert len(result.products) == 1
    assert result.products[0].price_eur == 160.0
    assert len(result.products[0].attributes) == 2


def test_dedup_contacts_by_phone():
    """Two contacts with same digit-only phone -> one record."""
    d = Deduplicator()
    parse = ParseResult(
        contacts=[
            HdsExtractedFact(
                type="contact",
                content="A: 0907 043 467",
                meta={"phone": "0907 043 467", "email": None},
            ),
            HdsExtractedFact(
                type="contact",
                content="B: 0907043467",
                meta={"phone": "0907043467", "email": None},
            ),
            HdsExtractedFact(
                type="contact",
                content="C: 0911 815 051",
                meta={"phone": "0911 815 051", "email": None},
            ),
        ]
    )
    result = d.deduplicate(parse)
    phones = {(c.meta or {}).get("phone") for c in result.contacts}
    assert len(result.contacts) == 2  # 2 unique digit-strings
    assert "0911 815 051" in phones


def test_dedup_faqs_by_question():
    d = Deduplicator()
    parse = ParseResult(
        faqs=[
            HdsFAQ(question="Aké sú podmienky?", answer="A"),
            HdsFAQ(question="Aké sú podmienky?", answer="B"),
            HdsFAQ(question="AKE SU PODMIENKY?", answer="C"),
            HdsFAQ(question="Iná otázka?", answer="D"),
        ]
    )
    result = d.deduplicate(parse)
    assert len(result.faqs) == 2
