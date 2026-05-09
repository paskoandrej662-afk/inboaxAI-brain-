from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.browser import RenderedPage
from app.core.extractors.vision import extract_page_with_vision


@pytest.mark.asyncio
async def test_extract_page_no_screenshot_returns_empty():
    rendered = RenderedPage(
        url="https://x.sk/",
        final_url="https://x.sk/",
        html="<html></html>",
        screenshot_png=None,
        viewport_w=0,
        viewport_h=0,
    )
    result = await extract_page_with_vision(rendered, "home", "")
    products, facts, faqs, images, summary, cost = result
    assert products == []
    assert facts == []
    assert faqs == []
    assert images == []
    assert summary == ""
    assert cost == 0.0


@pytest.mark.asyncio
async def test_extract_page_vision_api_error():
    """Ak vision call raises, vraciame prazdne zoznamy a pipeline pokracuje."""
    rendered = RenderedPage(
        url="https://x.sk/",
        final_url="https://x.sk/",
        html="<html></html>",
        screenshot_png=b"\x89PNG\r\n" + b"x" * 2000,
        viewport_w=1280,
        viewport_h=800,
    )
    with patch(
        "app.core.extractors.vision.call_sonnet_vision",
        new=AsyncMock(side_effect=Exception("api fail")),
    ):
        result = await extract_page_with_vision(rendered, "home", "test")
        products, facts, faqs, images, summary, cost = result
        assert products == []
        assert facts == []
        assert faqs == []
        assert images == []
        assert summary == ""
        assert cost == 0.0


@pytest.mark.asyncio
async def test_extract_page_with_mocked_vision_response():
    """Ak vision call vrati validny tool_use, parsing musi nam dat strukturovane data."""
    rendered = RenderedPage(
        url="https://x.sk/",
        final_url="https://x.sk/",
        html="<html></html>",
        screenshot_png=b"\x89PNG\r\n" + b"x" * 2000,
        viewport_w=1280,
        viewport_h=800,
    )

    fake_tool_use = MagicMock()
    fake_tool_use.type = "tool_use"
    fake_tool_use.input = {
        "products": [
            {
                "name": "Disney",
                "description": "9 deti",
                "price_text": "160€/Den",
                "price_eur": 160.0,
                "price_unit": "den",
                "attributes": {"kapacita": "9 deti"},
                "image_url": "https://x.sk/disney.jpg",
            }
        ],
        "business_facts": [{"key": "phone", "value": "+421 911 815 051"}],
        "faqs": [{"question": "Cena?", "answer": "160€"}],
        "image_descriptions": [
            {
                "image_url": "https://x.sk/disney.jpg",
                "description": "Skakaci hrad",
                "near_product_name": "Disney",
            }
        ],
        "page_summary": "Stranka o skakacich hradoch.",
    }
    fake_response = MagicMock()
    fake_response.content = [fake_tool_use]
    fake_response.usage = MagicMock(
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )

    with patch(
        "app.core.extractors.vision.call_sonnet_vision",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await extract_page_with_vision(rendered, "product_listing", "test")
        products, facts, faqs, images, summary, cost = result
        assert len(products) == 1
        assert products[0].name == "Disney"
        assert products[0].price_eur == 160.0
        assert products[0].source_type == "vision"
        assert products[0].verified is False
        assert len(facts) == 1
        assert facts[0].key == "phone"
        assert len(faqs) == 1
        assert len(images) == 1
        assert summary == "Stranka o skakacich hradoch."
        assert cost > 0


@pytest.mark.asyncio
async def test_extract_page_no_tool_use_returns_empty():
    """Ak response neobsahuje tool_use blok, vraciame prazdne."""
    rendered = RenderedPage(
        url="https://x.sk/",
        final_url="https://x.sk/",
        html="<html></html>",
        screenshot_png=b"\x89PNG\r\n" + b"x" * 2000,
        viewport_w=1280,
        viewport_h=800,
    )
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "no tool"
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_response.usage = MagicMock(
        input_tokens=10,
        output_tokens=10,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    with patch(
        "app.core.extractors.vision.call_sonnet_vision",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await extract_page_with_vision(rendered, "home", "test")
        products, facts, faqs, images, summary, cost = result
        assert products == []
        assert cost == 0.0
