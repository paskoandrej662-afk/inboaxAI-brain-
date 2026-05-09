from app.core.extractors.types import ExtractedBusinessFact, ExtractedProduct
from app.core.extractors.verification import verify_fact, verify_product


def test_verify_product_price_in_text():
    p = ExtractedProduct(
        name="Disney", price_text="160€/Deň", price_eur=160.0,
        source_type="vision", confidence=0.7,
    )
    page_text = "Skákací Hrad Disney - kapacita 9 detí - 160€/Deň"
    result = verify_product(p, page_text)
    assert result.price_text == "160€/Deň"
    assert result.verified is True


def test_verify_product_price_NOT_in_text():
    p = ExtractedProduct(
        name="Disney", price_text="55€", price_eur=55.0,
        source_type="vision", confidence=0.7,
    )
    page_text = "Skákací Hrad Disney - 160€/Deň"
    result = verify_product(p, page_text)
    assert result.price_text is None
    assert result.price_eur is None
    assert result.confidence < 0.7


def test_verify_product_jsonld_bypass():
    """JSON-LD products are trusted automatically."""
    p = ExtractedProduct(
        name="Phantom", price_text="999€",
        source_type="jsonld", confidence=0.95,
    )
    result = verify_product(p, "")
    assert result.verified is True
    assert result.price_text == "999€"


def test_verify_phone_digits_match():
    f = ExtractedBusinessFact(
        key="phone", value="+421 905 123 456",
        source_type="vision", confidence=0.7,
    )
    page_text = "Volajte +421905123456"
    assert verify_fact(f, page_text) is not None


def test_verify_phone_NOT_in_text():
    f = ExtractedBusinessFact(
        key="phone", value="0900 999 888",
        source_type="vision", confidence=0.7,
    )
    page_text = "Volajte 0911 815 051"
    assert verify_fact(f, page_text) is None


def test_verify_email_match():
    f = ExtractedBusinessFact(
        key="email", value="info@x.sk",
        source_type="vision", confidence=0.7,
    )
    assert verify_fact(f, "Email: info@x.sk thanks") is not None
    assert verify_fact(f, "no email here") is None


def test_verify_jsonld_bypass():
    f = ExtractedBusinessFact(
        key="phone", value="000",
        source_type="jsonld", confidence=0.95,
    )
    assert verify_fact(f, "") is not None
