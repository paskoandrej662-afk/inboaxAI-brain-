"""Renderer testy pre `app/core/ingest_v2/renderer.py`.

Vyzaduju Chromium (playwright) a (idealne) siet. Renderer je defensive — pri
sietovej chybe vracia `render_status in ('error','timeout','blocked')` namiesto
vynimky, takze testy prejdu aj offline.
"""
from __future__ import annotations

import pytest

from app.core.ingest_v2.renderer import RendererV2


@pytest.mark.asyncio
async def test_renderer_lifecycle():
    r = RendererV2()
    try:
        await r.start()
        result = await r.render_page("https://example.com/", take_screenshot=False)
        assert result.url == "https://example.com/"
        # Online → success; offline → graceful error.
        if result.render_status == 'success':
            assert result.title or result.visible_text
            assert result.render_ms > 0
            assert isinstance(result.html, str) and result.html
        else:
            assert result.render_status in ('error', 'timeout', 'blocked')
            assert result.error_message is not None
    finally:
        await r.close()


@pytest.mark.asyncio
async def test_renderer_handles_nonexistent_gracefully():
    r = RendererV2(timeout_ms=8000)
    try:
        await r.start()
        result = await r.render_page("https://example.com/nonexistent-xyz-123")
        assert result.render_status in ('success', 'error', 'timeout', 'blocked')
        assert isinstance(result.html, str)
    finally:
        await r.close()
