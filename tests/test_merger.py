from app.core.extractors.merger import (
    merge_facts,
    merge_faqs,
    merge_images,
    merge_products,
)
from app.core.extractors.types import (
    ExtractedBusinessFact,
    ExtractedFaqItem,
    ExtractedImage,
    ExtractedProduct,
)


def test_merge_products_jsonld_wins_over_vision():
    jsonld = ExtractedProduct(
        name="Disney", price_text="160€", price_eur=160.0,
        source_type="jsonld", confidence=0.95, verified=True,
    )
    vision = ExtractedProduct(
        name="Disney", price_text="55€", price_eur=55.0,
        source_type="vision", confidence=0.7, verified=True,
    )
    merged = merge_products([jsonld], [vision])
    assert len(merged) == 1
    assert merged[0].price_text == "160€"


def test_merge_products_unverified_vision_lower():
    v_verified = ExtractedProduct(
        name="X", price_text="100€",
        source_type="vision", confidence=0.7, verified=True,
    )
    v_unverified = ExtractedProduct(
        name="X", price_text="999€",
        source_type="vision", confidence=0.4, verified=False,
    )
    merged = merge_products([v_unverified], [v_verified])
    assert merged[0].price_text == "100€"


def test_merge_products_attributes_merged():
    a = ExtractedProduct(
        name="X", attributes={"kapacita": "9 detí"},
        source_type="vision", verified=True,
    )
    b = ExtractedProduct(
        name="X", attributes={"rozmery": "8x5m"},
        source_type="vision", verified=True,
    )
    merged = merge_products([a], [b])
    assert merged[0].attributes == {"kapacita": "9 detí", "rozmery": "8x5m"} or \
           merged[0].attributes == {"rozmery": "8x5m", "kapacita": "9 detí"}


def test_merge_facts_phone_multi_value():
    a = ExtractedBusinessFact(key="phone", value="0911 111 111", source_type="regex")
    b = ExtractedBusinessFact(key="phone", value="0922 222 222", source_type="vision")
    merged = merge_facts([a], [b])
    phones = [f for f in merged if f.key == "phone"]
    assert len(phones) == 2


def test_merge_facts_address_single_value():
    a = ExtractedBusinessFact(
        key="address", value="Babín 420, 029 52", source_type="jsonld",
    )
    b = ExtractedBusinessFact(
        key="address", value="Wrong Address", source_type="vision",
    )
    merged = merge_facts([a], [b])
    addrs = [f for f in merged if f.key == "address"]
    assert len(addrs) == 1
    assert addrs[0].value == "Babín 420, 029 52"


def test_merge_faqs_dedupe_question():
    a = ExtractedFaqItem(question="Aká je cena?", answer="A1", source_type="jsonld")
    b = ExtractedFaqItem(question="Aká je cena?", answer="A2", source_type="vision")
    merged = merge_faqs([a], [b])
    assert len(merged) == 1
    assert merged[0].source_type == "jsonld"


def test_merge_images_dedupe_url():
    a = ExtractedImage(url="https://x.sk/foo.jpg", alt_text="A")
    b = ExtractedImage(url="https://x.sk/foo.jpg", alt_text="B")
    c = ExtractedImage(url="https://x.sk/bar.jpg")
    merged = merge_images([a, b], [c])
    assert len(merged) == 2
