"""Offline testy pre `app/core/ingest_v2/block_detection.py` — ziaden LLM, ziadna siet."""
from app.core.ingest_v2.block_detection import detect_blocks
from app.core.ingest_v2.types import BlockTypeHint


def test_detect_empty_html():
    assert detect_blocks("") == []


def test_detect_section():
    html = "<section><h2>O nas</h2><p>Sme firma s 20 rocnou tradiciou.</p></section>"
    blocks = detect_blocks(html)
    assert len(blocks) >= 1
    assert any(
        b.block_type_hint in (BlockTypeHint.SECTION_CANDIDATE.value, BlockTypeHint.ABOUT_CANDIDATE.value)
        for b in blocks
    )


def test_detect_repeated_cards():
    html = (
        '<div class="grid">'
        '<div class="card"><h3>P1</h3><span>160 €</span></div>'
        '<div class="card"><h3>P2</h3><span>180 €</span></div>'
        '<div class="card"><h3>P3</h3><span>200 €</span></div>'
        "</div>"
    )
    blocks = detect_blocks(html)
    repeated = [b for b in blocks if b.block_type_hint == BlockTypeHint.REPEATED_CARD_CANDIDATE.value]
    assert len(repeated) >= 3


def test_detect_faq_details():
    html = "<details><summary>Aka je cena?</summary><p>160 EUR za den</p></details>"
    blocks = detect_blocks(html)
    assert any(b.block_type_hint == BlockTypeHint.FAQ_CANDIDATE.value for b in blocks)


def test_detect_footer():
    html = "<footer><p>Copyright 2026</p><p>info@firma.sk</p></footer>"
    blocks = detect_blocks(html)
    assert any(b.block_type_hint == BlockTypeHint.FOOTER_CANDIDATE.value for b in blocks)


def test_detect_signals_price():
    html = "<section><h2>Cennik</h2><div>Sluzba 160 EUR</div></section>"
    blocks = detect_blocks(html)
    found = [b for b in blocks if b.signals.has_price]
    assert len(found) >= 1
