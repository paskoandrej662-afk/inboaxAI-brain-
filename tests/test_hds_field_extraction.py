"""Testy pre field extractor (Phase 4)."""

from bs4 import BeautifulSoup

from app.core.extractors.hds.field_extractor import (
    _extract_attributes,
    _extract_name,
    _extract_price_eur,
    _extract_price_text,
    extract_fields,
)


def test_extract_name_prefers_heading():
    html = "<div><h3>Tiger Hrad</h3><p>Description</p></div>"
    soup = BeautifulSoup(html, "html.parser")
    card = soup.find("div")
    assert _extract_name(card) == "Tiger Hrad"


def test_extract_name_fallback_to_font_size():
    html = """
    <div>
        <span style="font-size: 24px;">Big Title</span>
        <span style="font-size: 12px;">Small text</span>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    card = soup.find("div")
    assert _extract_name(card) == "Big Title"


def test_extract_price_eur_picks_largest():
    # Najdi najvacsiu hodnotu (typicky hlavna cena)
    text = "Od 5€ alebo 160€ za den"
    assert _extract_price_eur(text) == 160.0


def test_extract_price_eur_returns_none():
    assert _extract_price_eur("Bez ceny") is None


def test_extract_price_eur_handles_decimal():
    text = "Cena 99,99€"
    assert _extract_price_eur(text) == 99.99


def test_extract_price_text_soft_price():
    text = "Tiger Hrad — Cena dohodou. Volajte."
    pt = _extract_price_text(text)
    assert pt is not None
    assert "dohod" in pt.lower()


def test_extract_price_text_none_for_clean_numeric():
    assert _extract_price_text("Cena 160€") is None


def test_extract_attributes_kapacita():
    text = "Kapacita: 9 detí. Cena 160€."
    attrs = _extract_attributes(text)
    assert "kapacita" in attrs
    assert "9" in attrs["kapacita"]


def test_extract_attributes_rozmery():
    text = "Rozmery: 8 × 6 m. Kapacita: 12 detí."
    attrs = _extract_attributes(text)
    assert "rozmery" in attrs


def test_extract_fields_full_card():
    html = """
    <div class="card">
        <h3>Skákací Hrad Tiger</h3>
        <p>Kapacita: 9 detí. Cena: 160€/Deň.</p>
        <p>Rozmery: 8 × 6 m.</p>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    card = soup.find("div")
    fields = extract_fields(card)
    assert "Tiger" in fields["name"]
    assert fields["price_eur"] == 160.0
    assert "9" in fields["attributes"].get("kapacita", "")


def test_extract_fields_mixed_pricing():
    html = """
    <div>
        <h3>Stan</h3>
        <p>55€/Den + doprava dohodov</p>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    card = soup.find("div")
    fields = extract_fields(card)
    assert fields["price_eur"] == 55.0
    assert fields["price_text"] is not None
    assert "dohod" in fields["price_text"].lower()


def test_extract_fields_empty_card():
    html = "<div></div>"
    soup = BeautifulSoup(html, "html.parser")
    card = soup.find("div")
    fields = extract_fields(card)
    assert fields == {}
