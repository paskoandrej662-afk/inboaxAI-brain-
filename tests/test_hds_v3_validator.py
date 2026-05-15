"""Tests for hds_v3 Validator — anti-halucinacia checks."""
from __future__ import annotations

from app.core.extractors.hds_v3.parser import ParseResult
from app.core.extractors.hds_v3.types import HdsExtractedFact, HdsFAQ
from app.core.extractors.hds_v3.validator import Validator
from app.core.extractors.types import ExtractedProduct


def test_validator_drops_product_with_empty_name():
    v = Validator()
    parse = ParseResult(
        products=[
            ExtractedProduct(name="Real Product", price_eur=100.0),
            ExtractedProduct(name="", price_eur=50.0),
            ExtractedProduct(name=" ", price_eur=60.0),
            ExtractedProduct(name="neuvedene", price_eur=70.0),
        ]
    )
    result = v.validate(parse)
    assert len(result.products) == 1
    assert result.products[0].name == "Real Product"


def test_validator_drops_contact_with_invalid_phone():
    v = Validator()
    parse = ParseResult(
        contacts=[
            HdsExtractedFact(
                type="contact",
                content="John (owner): 0907 043 467 a@b.sk",
                meta={"phone": "0907 043 467", "email": "a@b.sk"},
            ),
            HdsExtractedFact(
                type="contact",
                content="Hallucination: 1234",
                meta={"phone": "1234", "email": None},
            ),
        ]
    )
    result = v.validate(parse)
    assert len(result.contacts) == 1
    assert result.contacts[0].meta["phone"] == "0907 043 467"


def test_validator_drops_contact_with_invalid_email():
    v = Validator()
    parse = ParseResult(
        contacts=[
            HdsExtractedFact(
                type="contact",
                content="Bad: garbage-email",
                meta={"phone": None, "email": "not-an-email"},
            ),
            HdsExtractedFact(
                type="contact",
                content="Good: a@b.sk",
                meta={"phone": None, "email": "a@b.sk"},
            ),
        ]
    )
    result = v.validate(parse)
    assert len(result.contacts) == 1
    assert result.contacts[0].meta["email"] == "a@b.sk"


def test_validator_drops_address_without_numbers():
    v = Validator()
    parse = ParseResult(
        contacts=[
            HdsExtractedFact(
                type="address",
                content="Sidlo: Babín 420, 02952",
                meta={"address_type": "sidlo"},
            ),
            HdsExtractedFact(
                type="address",
                content="Sidlo: Bratislava",
                meta={"address_type": "sidlo"},
            ),
        ]
    )
    result = v.validate(parse)
    assert len(result.contacts) == 1
    assert "Babín" in result.contacts[0].content


def test_validator_keeps_dohodou_price():
    """Products with price_eur=None but price_text='dohodou' must survive."""
    v = Validator()
    parse = ParseResult(
        products=[
            ExtractedProduct(
                name="Custom service",
                price_eur=None,
                price_text="dohodou",
            )
        ]
    )
    result = v.validate(parse)
    assert len(result.products) == 1
    assert result.products[0].price_text == "dohodou"


def test_validator_drops_faq_too_short():
    v = Validator()
    parse = ParseResult(
        faqs=[
            HdsFAQ(question="Aké sú podmienky?", answer="Hrad rozložený na rovnej ploche."),
            HdsFAQ(question="A?", answer="Yes"),  # too short
            HdsFAQ(question="", answer="Empty Q"),
            HdsFAQ(question="Real question?", answer=""),
        ]
    )
    result = v.validate(parse)
    assert len(result.faqs) == 1
    assert result.faqs[0].question.startswith("Aké")
