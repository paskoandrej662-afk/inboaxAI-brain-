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


def test_detect_repeated_card_uses_parent_when_small_child():
    """When inner siblings are tiny (e.g. price spans), engine should treat the
    grandparent as the card and include its full text (name + price)."""
    html = """
    <div class="grid">
      <div class="card"><img src="/p1.jpg"><h3>Skakaci Hrad Tiger</h3><span class="price">160 €</span></div>
      <div class="card"><img src="/p2.jpg"><h3>Skakaci Hrad Indian</h3><span class="price">160 €</span></div>
      <div class="card"><img src="/p3.jpg"><h3>Skakaci Hrad Pirat</h3><span class="price">150 €</span></div>
    </div>
    """
    from app.core.ingest_v2.block_detection import detect_blocks
    blocks = detect_blocks(html)
    cards_with_name = [b for b in blocks if "Tiger" in b.text or "Indian" in b.text or "Pirat" in b.text]
    assert len(cards_with_name) >= 1, f"Expected card with name in text, got: {[b.text[:50] for b in blocks]}"


def test_detect_no_duplicate_selectors():
    """Same element should not appear twice in the output."""
    html = "<section><h2>O nas</h2><p>Tu je dlhsi text o nasej firme z roku 2020.</p></section>"
    from app.core.ingest_v2.block_detection import detect_blocks
    blocks = detect_blocks(html)
    selectors = [b.selector for b in blocks]
    assert len(selectors) == len(set(selectors)), f"Duplicate selectors found: {selectors}"
