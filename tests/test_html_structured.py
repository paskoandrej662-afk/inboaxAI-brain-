from app.core.extractors.html_structured import (
    extract_contact_patterns,
    extract_image_refs,
    extract_jsonld_business,
    extract_jsonld_faq,
    extract_jsonld_products,
    extract_meta,
    extract_opengraph,
)


def test_jsonld_product_basic():
    """Schema.org Product with name, price, image extracted as ExtractedProduct(jsonld, conf=0.95, verified=True)."""
    html = '''<html><body><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Skákací Hrad Disney","description":"9 detí, 8x5m","image":"https://example.com/disney.jpg","offers":{"@type":"Offer","price":"160","priceCurrency":"EUR"}}
    </script></body></html>'''
    products = extract_jsonld_products(html, "https://example.com/")
    assert len(products) == 1
    assert products[0].name == "Skákací Hrad Disney"
    assert products[0].price_eur == 160.0
    assert products[0].image_url == "https://example.com/disney.jpg"
    assert products[0].source_type == "jsonld"
    assert products[0].confidence == 0.95
    assert products[0].verified is True


def test_jsonld_product_no_offers():
    """Product without offers: price stays None, but product is still extracted."""
    html = '''<script type="application/ld+json">{"@type":"Product","name":"X"}</script>'''
    products = extract_jsonld_products(html, "https://x.sk/")
    assert len(products) == 1
    assert products[0].price_text is None


def test_jsonld_faq_basic():
    html = '''<script type="application/ld+json">
    {"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"Aká je cena?","acceptedAnswer":{"@type":"Answer","text":"160 EUR"}}]}
    </script>'''
    faqs = extract_jsonld_faq(html, "https://x.sk/faq")
    assert len(faqs) == 1
    assert faqs[0].question == "Aká je cena?"
    assert faqs[0].answer == "160 EUR"


def test_jsonld_localbusiness():
    html = '''<script type="application/ld+json">
    {"@type":"LocalBusiness","name":"Skákačky Orava","telephone":"+421 911 815 051","email":"info@skakaciehradyorava.sk","address":{"streetAddress":"Babín 420","postalCode":"029 52","addressLocality":"Orava"}}
    </script>'''
    facts = extract_jsonld_business(html, "https://x.sk/")
    keys = [f.key for f in facts]
    assert 'phone' in keys
    assert 'email' in keys
    assert 'address' in keys
    assert 'company_name' in keys


def test_contact_phone_sk():
    text = "Volajte nám 0911 815 051 alebo +421 905 123 456"
    facts = extract_contact_patterns(text, "https://x.sk/")
    phones = [f for f in facts if f.key == 'phone']
    assert len(phones) == 2


def test_contact_email():
    text = "Kontakt: info@firma.sk, Eva: eva.novakova@firma.com"
    facts = extract_contact_patterns(text, "https://x.sk/")
    emails = [f for f in facts if f.key == 'email']
    assert len(emails) == 2


def test_image_refs_relative_to_absolute():
    html = '<img src="/foo.jpg" alt="Foo" /><img src="https://other.com/bar.jpg">'
    images = extract_image_refs(html, "https://example.sk/page")
    urls = [img.url for img in images]
    assert "https://example.sk/foo.jpg" in urls
    assert "https://other.com/bar.jpg" in urls


def test_jsonld_empty_block_skipped():
    """Empty or invalid JSON-LD blocks must not crash."""
    html = '<script type="application/ld+json"></script><script type="application/ld+json">not json</script>'
    products = extract_jsonld_products(html, "https://x.sk/")
    assert products == []
