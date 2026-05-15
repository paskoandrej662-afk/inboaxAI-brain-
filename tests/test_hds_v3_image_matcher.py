"""ImageMatcher tests — pure dataclass-based, no I/O."""
from __future__ import annotations

from app.core.extractors.hds_v3.image_extractor import MediaItem
from app.core.extractors.hds_v3.image_matcher import ImageMatch, ImageMatcher


def _text(pos: int, text: str) -> MediaItem:
    return MediaItem(position=pos, item_type="text", text=text)


def _image(
    pos: int,
    src: str = "https://x.sk/img.jpg",
    alt: str = "",
    filename: str | None = None,
) -> MediaItem:
    return MediaItem(
        position=pos,
        item_type="image",
        src=src,
        filename=filename if filename is not None else src.rsplit("/", 1)[-1],
        alt=alt,
        width=800,
        height=600,
    )


def test_match_image_adjacent_to_product_name_high_confidence():
    stream = [
        _text(0, "Skákací hrad"),
        _text(1, "Tiger 160€/Deň"),
        _image(2, src="https://x.sk/img-abc.jpg"),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Tiger"])
    assert len(matches) == 1
    assert matches[0].product_name == "Tiger"
    # Distance 1 → proximity score 0.9; no boosts → ≥ MIN_CONFIDENCE_KEEP
    assert matches[0].confidence >= 0.5


def test_match_image_far_from_product_low_confidence():
    stream = [
        _text(0, "Tiger 160€"),
        # 25 positions of filler text — well outside PROXIMITY_WINDOW (10)
        *[_text(i, f"filler {i}") for i in range(1, 26)],
        _image(30, src="https://x.sk/generic.jpg"),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Tiger"])
    # Outside proximity window, no filename/url/alt boost → dropped
    assert matches == []


def test_no_match_when_no_product_text_nearby():
    stream = [
        _text(0, "Welcome to our site"),
        _image(1, src="https://x.sk/generic.jpg", alt="welcome banner"),
        _text(2, "Contact us"),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Tiger"])
    assert matches == []


def test_filename_boost_increases_confidence():
    # Image is at distance 8 from text mention (proximity = 0.2). Without
    # boost it would be dropped; with filename boost (+0.2) still under
    # 0.5, but with URL boost too... actually we want to show filename
    # alone bumps the score. Pair filename match with proximity ≥ 0.4.
    stream = [
        _text(0, "Tiger"),
        *[_text(i, f"filler") for i in range(1, 7)],
        _image(7, src="https://x.sk/tiger-product.jpg"),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Tiger"])
    assert len(matches) == 1
    m = matches[0]
    assert m.product_name == "Tiger"
    assert "filename" in m.signals
    assert m.signals["filename"]["boost"] == matcher.FILENAME_BOOST


def test_url_path_boost():
    # Filename does NOT contain product; URL path does → URL boost.
    stream = [
        _text(0, "Tiger"),
        _image(
            1,
            src="https://x.sk/wp-content/uploads/tiger/img-001.jpg",
            filename="img-001.jpg",
        ),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Tiger"])
    assert len(matches) == 1
    m = matches[0]
    assert "url_path" in m.signals
    assert "filename" not in m.signals


def test_alt_text_boost():
    # No proximity, no filename, no URL match — just alt.
    stream = [
        _text(0, "Tiger"),
        _image(1, src="https://x.sk/i.jpg", filename="i.jpg", alt="Tiger product photo"),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Tiger"])
    assert len(matches) == 1
    assert "alt" in matches[0].signals


def test_normalization_handles_diacritics():
    stream = [
        _text(0, "Rozprávkovo"),
        _image(1, src="https://x.sk/rozpravkovo.jpg"),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Rozprávkovo"])
    assert len(matches) == 1
    assert matches[0].product_name == "Rozprávkovo"


def test_group_by_product_sorts_by_confidence():
    matches = [
        ImageMatch(product_name="Tiger", image_url="b.jpg", confidence=0.7),
        ImageMatch(product_name="Tiger", image_url="a.jpg", confidence=0.95),
        ImageMatch(product_name="Tiger", image_url="c.jpg", confidence=0.6),
    ]
    grouped = ImageMatcher().group_by_product(matches)
    assert grouped["Tiger"]["primary"] == "a.jpg"
    assert grouped["Tiger"]["secondary"] == ["b.jpg", "c.jpg"]


def test_group_by_product_caps_secondary_at_4():
    matches = [
        ImageMatch(product_name="P", image_url=f"u{i}.jpg", confidence=1.0 - i * 0.01)
        for i in range(10)
    ]
    grouped = ImageMatcher().group_by_product(matches)
    assert grouped["P"]["primary"] == "u0.jpg"
    assert len(grouped["P"]["secondary"]) == 4
    assert grouped["P"]["secondary"][0] == "u1.jpg"


def test_match_with_multiple_products_picks_closest():
    stream = [
        _text(0, "Tiger"),
        _text(1, "Rozprávkovo"),
        _image(2, src="https://x.sk/generic.jpg"),
    ]
    matcher = ImageMatcher()
    matches = matcher.match_images(stream, ["Tiger", "Rozprávkovo"])
    # Image at pos 2: Rozprávkovo is at distance 1 (closer than Tiger at 2).
    assert len(matches) == 1
    assert matches[0].product_name == "Rozprávkovo"
