import asyncio

import pytest

from app.core.browser import BrowserPool, RenderedPage
from app.core.extractors.types import (
    PAGE_TYPES,
    ExtractedBusinessFact,
    ExtractedFaqItem,
    ExtractedImage,
    ExtractedProduct,
    IngestRunResult,
    PageExtraction,
)
from app.core.llm.anthropic_client import call_sonnet_vision


def test_imports_ok():
    assert PAGE_TYPES
    assert callable(call_sonnet_vision)
    assert BrowserPool


def test_types_instantiate():
    p = ExtractedProduct(name="Test Product")
    assert p.name == "Test Product"
    assert p.attributes == {}
    assert p.confidence == 0.5
    assert not p.verified

    f = ExtractedBusinessFact(key="phone", value="+421 905 123 456")
    assert f.confidence == 0.5

    faq = ExtractedFaqItem(question="Q?", answer="A.")
    assert faq.source_type == "vision"

    img = ExtractedImage(url="https://example.com/x.jpg")
    assert img.description is None

    page = PageExtraction(
        url="https://example.com/", page_type="home", value_score=0.8
    )
    assert page.products == []

    run = IngestRunResult()
    assert run.pages_visited == 0
    assert run.errors == []


def test_browser_pool_lifecycle():
    async def _run():
        pool = BrowserPool()
        await pool.start()
        try:
            rendered = await pool.render_page(
                "https://example.com/", take_screenshot=True
            )
            assert rendered.url == "https://example.com/"
            if rendered.error is None:
                assert len(rendered.html) > 100
                assert rendered.screenshot_png is not None
                assert len(rendered.screenshot_png) > 1000
        finally:
            await pool.close()

    asyncio.run(_run())
