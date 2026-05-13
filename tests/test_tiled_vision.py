import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_render_page_tiled_lifecycle():
    """Real browser test on example.com (short page, expect 1 segment)."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    try:
        await pool.start()
        result = await pool.render_page_tiled("https://example.com/")
        assert result.url == "https://example.com/"
        if result.error is None:
            # example.com is very short — should be 1 segment
            assert len(result.segments) == 1
            assert result.scroll_height > 0
            assert len(result.segments[0]) > 1000  # non-trivial PNG
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_tiled_vision_empty_segments():
    """When TiledPageResult has no segments, returns empty results."""
    from app.core.browser import TiledPageResult
    from app.core.extractors.vision import extract_page_with_tiled_vision

    tiled = TiledPageResult(
        url="https://x.sk/", final_url="https://x.sk/", html="",
        visible_text="", title="", scroll_height=0,
        viewport_w=1280, viewport_h=2200, segments=[],
    )
    products, facts, faqs, images, summary, cost = await extract_page_with_tiled_vision(
        tiled, "home", ""
    )
    assert products == []
    assert facts == []
    assert faqs == []
    assert cost == 0.0


@pytest.mark.asyncio
async def test_tiled_vision_merges_across_tiles():
    """Mock vision calls on 2 tiles — products from both tiles are merged."""
    from app.core.browser import TiledPageResult
    from app.core.extractors.vision import extract_page_with_tiled_vision

    tiled = TiledPageResult(
        url="https://x.sk/", final_url="https://x.sk/", html="",
        visible_text="", title="", scroll_height=4500,
        viewport_w=1280, viewport_h=2200,
        segments=[b"\x89PNG" + b"x" * 2000, b"\x89PNG" + b"y" * 2000],
    )

    # Tile 1: Disney + Tiger
    tile1_tool = MagicMock()
    tile1_tool.type = "tool_use"
    tile1_tool.input = {
        "products": [
            {"name": "Disney", "price_text": "160€/Den", "price_eur": 160.0, "price_unit": "den", "attributes": {}, "image_url": None},
            {"name": "Tiger", "price_text": "180€/Den", "price_eur": 180.0, "price_unit": "den", "attributes": {}, "image_url": None},
        ],
        "business_facts": [],
        "faqs": [],
        "image_descriptions": [],
        "page_summary": "Tile 1"
    }
    tile1_resp = MagicMock()
    tile1_resp.content = [tile1_tool]
    tile1_resp.usage = MagicMock(input_tokens=500, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0)

    # Tile 2: Tiger (duplicate) + Pirat
    tile2_tool = MagicMock()
    tile2_tool.type = "tool_use"
    tile2_tool.input = {
        "products": [
            {"name": "Tiger", "price_text": "180€/Den", "price_eur": 180.0, "price_unit": "den", "attributes": {"vyska": "4m"}, "image_url": None},
            {"name": "Pirat", "price_text": "150€/Den", "price_eur": 150.0, "price_unit": "den", "attributes": {}, "image_url": None},
        ],
        "business_facts": [],
        "faqs": [],
        "image_descriptions": [],
        "page_summary": "Tile 2"
    }
    tile2_resp = MagicMock()
    tile2_resp.content = [tile2_tool]
    tile2_resp.usage = MagicMock(input_tokens=100, output_tokens=100, cache_read_input_tokens=400, cache_creation_input_tokens=0)

    call_count = 0
    async def mock_call(**kwargs):
        nonlocal call_count
        call_count += 1
        return tile1_resp if call_count == 1 else tile2_resp

    with patch("app.core.extractors.vision.call_sonnet_vision", side_effect=mock_call):
        products, facts, faqs, images, summary, cost = await extract_page_with_tiled_vision(
            tiled, "product_listing", "Disney Tiger Pirat raw text"
        )

    # Tiger should be merged (appears in both tiles)
    product_names = sorted([p.name for p in products])
    assert "Disney" in product_names
    assert "Tiger" in product_names
    assert "Pirat" in product_names
    assert len(products) == 3, f"expected 3 unique products, got {len(products)}: {product_names}"
    assert cost > 0.0


@pytest.mark.asyncio
async def test_tiled_vision_handles_failed_tile():
    """If one tile vision call raises, other tiles still produce results."""
    from app.core.browser import TiledPageResult
    from app.core.extractors.vision import extract_page_with_tiled_vision

    tiled = TiledPageResult(
        url="https://x.sk/", final_url="https://x.sk/", html="",
        visible_text="", title="", scroll_height=4500,
        viewport_w=1280, viewport_h=2200,
        segments=[b"\x89PNG" + b"a" * 2000, b"\x89PNG" + b"b" * 2000],
    )

    good_tool = MagicMock()
    good_tool.type = "tool_use"
    good_tool.input = {"products": [{"name": "OK", "price_text": "10€", "price_eur": 10.0, "price_unit": "kus", "attributes": {}, "image_url": None}], "business_facts": [], "faqs": [], "image_descriptions": []}
    good_resp = MagicMock()
    good_resp.content = [good_tool]
    good_resp.usage = MagicMock(input_tokens=100, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0)

    call_count = 0
    async def mock_call(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated API error")
        return good_resp

    with patch("app.core.extractors.vision.call_sonnet_vision", side_effect=mock_call):
        products, _, _, _, _, _ = await extract_page_with_tiled_vision(
            tiled, "home", "raw"
        )

    # Failed tile gave nothing, second tile gave "OK"
    assert len(products) == 1
    assert products[0].name == "OK"


