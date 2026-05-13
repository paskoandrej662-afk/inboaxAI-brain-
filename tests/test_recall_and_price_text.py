import pytest


def test_extracted_product_accepts_price_text():
    """ExtractedProduct dataclass must accept price_text field."""
    from app.core.extractors.types import ExtractedProduct
    p = ExtractedProduct(
        name="Stan na prenajom",
        price_eur=55.0,
        price_text="55€/den + doprava dohodov",
    )
    assert p.price_text == "55€/den + doprava dohodov"
    assert p.price_eur == 55.0


def test_extracted_product_price_text_only():
    """Product with only price_text (no numeric price) is valid."""
    from app.core.extractors.types import ExtractedProduct
    p = ExtractedProduct(
        name="Atrakcia na mieru",
        price_eur=None,
        price_text="dohodou",
    )
    assert p.price_eur is None
    assert p.price_text == "dohodou"


def test_vision_tool_schema_includes_price_text():
    """VISION_TOOL_SCHEMA must declare price_text as optional product field."""
    from app.core.extractors.vision import VISION_TOOL_SCHEMA
    import json
    schema_str = json.dumps(VISION_TOOL_SCHEMA)
    assert 'price_text' in schema_str, "Schema must include price_text field"


def test_vision_system_prompt_has_recall_instruction():
    """VISION_SYSTEM must instruct Sonnet to extract ALL products including small ones."""
    from app.core.extractors.vision import VISION_SYSTEM
    text = VISION_SYSTEM.lower()
    assert 'kazd' in text or 'every' in text or 'vsetk' in text, \
        "System prompt must emphasize extracting every product"


def test_verify_product_accepts_dohodov():
    """verify_product must accept product when price_text contains 'dohodov'.

    Note: verify_product returns the (mutated) ExtractedProduct, not a bool — we assert
    .verified is True (the public flag indicating verification passed).
    """
    from app.core.extractors.verification import verify_product
    from app.core.extractors.types import ExtractedProduct

    raw_text = "Atrakcia na mieru — cena bude dohodov individualne pre kazdu objednavku"
    p = ExtractedProduct(
        name="Atrakcia na mieru",
        price_eur=None,
        price_text="dohodov",
    )
    result = verify_product(p, raw_text)
    assert result.verified is True
    # Soft-pricing keyword must NOT be stripped from price_text
    assert result.price_text == "dohodov"
