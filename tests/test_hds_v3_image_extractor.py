"""ImageExtractor tests — offline, HTML-string-based.

The extractor's production entry uses Playwright `page.evaluate`; here
we exercise `extract_stream_from_html` which is the same contract over
BeautifulSoup. The Playwright path is identical at the result-shape
level and is exercised by the eval script.
"""
from __future__ import annotations

from app.core.extractors.hds_v3.image_extractor import ImageExtractor, MediaItem


def test_extract_stream_returns_items_in_order():
    html = """
    <html><body>
      <h1>Welcome</h1>
      <p>First paragraph</p>
      <img src="https://x.sk/tiger.jpg" alt="Tiger" />
      <p>After image</p>
      <img src="https://x.sk/rozpravkovo.jpg" alt="Rozprávkovo" />
    </body></html>
    """
    extractor = ImageExtractor()
    items = extractor.extract_stream_from_html(html)
    types = [it.item_type for it in items]
    # Document order: text, text, image, text, image
    assert types.count("image") == 2
    assert types.count("text") >= 2
    image_positions = [it.position for it in items if it.item_type == "image"]
    assert image_positions == sorted(image_positions)
    # First image is Tiger, then Rozprávkovo
    images = [it for it in items if it.item_type == "image"]
    assert images[0].src.endswith("tiger.jpg")
    assert images[1].src.endswith("rozpravkovo.jpg")


def test_extract_stream_skips_invisible_images():
    html = """
    <html><body>
      <p>visible text</p>
      <img src="https://x.sk/hidden1.jpg" style="display:none" />
      <img src="https://x.sk/hidden2.jpg" hidden />
      <div style="visibility:hidden">
        <img src="https://x.sk/inside-hidden.jpg" />
      </div>
      <img src="https://x.sk/visible.jpg" />
    </body></html>
    """
    extractor = ImageExtractor()
    items = extractor.extract_stream_from_html(html)
    image_srcs = [it.src for it in items if it.item_type == "image"]
    assert image_srcs == ["https://x.sk/visible.jpg"]


def test_extract_stream_includes_alt_and_dimensions():
    html = """
    <html><body>
      <img src="https://x.sk/p.jpg" alt="Tiger 160€" width="800" height="600" />
    </body></html>
    """
    extractor = ImageExtractor()
    items = extractor.extract_stream_from_html(html)
    images = [it for it in items if it.item_type == "image"]
    assert len(images) == 1
    img = images[0]
    assert img.alt == "Tiger 160€"
    assert img.width == 800
    assert img.height == 600
    assert img.filename == "p.jpg"


def test_extract_stream_skips_svg_and_script():
    html = """
    <html><body>
      <script>var x = 1;</script>
      <style>.a { color: red; }</style>
      <svg><circle r="10"/></svg>
      <p>real text</p>
      <img src="https://x.sk/keep.jpg" />
    </body></html>
    """
    extractor = ImageExtractor()
    items = extractor.extract_stream_from_html(html)
    # No JS / CSS / SVG content in the stream
    texts = " ".join((it.text or "").lower() for it in items if it.item_type == "text")
    assert "var x" not in texts
    assert "color: red" not in texts
    assert "<circle" not in texts
    image_srcs = [it.src for it in items if it.item_type == "image"]
    assert image_srcs == ["https://x.sk/keep.jpg"]
