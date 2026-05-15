"""ImageValidator tests — pre_filter is pure, HEAD path is exercised via
an injected `httpx.AsyncClient` so we don't hit the network."""
from __future__ import annotations

import httpx
import pytest

from app.core.extractors.hds_v3.image_extractor import MediaItem
from app.core.extractors.hds_v3.image_validator import ImageValidator


def _img(
    src: str,
    *,
    filename: str | None = None,
    width: int = 800,
    height: int = 600,
) -> MediaItem:
    return MediaItem(
        position=0,
        item_type="image",
        src=src,
        filename=filename if filename is not None else src.rsplit("/", 1)[-1],
        width=width,
        height=height,
    )


def test_pre_filter_drops_logo():
    v = ImageValidator()
    assert v.pre_filter(_img("https://x.sk/site-logo.png")) is False
    assert v.pre_filter(_img("https://x.sk/uploads/logo-header.jpg")) is False


def test_pre_filter_drops_icon():
    v = ImageValidator()
    assert v.pre_filter(_img("https://x.sk/icon-facebook.png")) is False
    assert v.pre_filter(_img("https://x.sk/favicon.ico")) is False


def test_pre_filter_drops_small_dimensions():
    v = ImageValidator()
    assert v.pre_filter(_img("https://x.sk/product.jpg", width=120, height=80)) is False


def test_pre_filter_drops_svg():
    v = ImageValidator()
    assert v.pre_filter(_img("https://x.sk/illustration.svg")) is False
    assert v.pre_filter(_img("https://x.sk/path/IMG.SVG")) is False


def test_pre_filter_passes_product_image():
    v = ImageValidator()
    assert (
        v.pre_filter(
            _img(
                "https://x.sk/wp-content/uploads/2023/tiger-product.jpg",
                width=1200,
                height=900,
            )
        )
        is True
    )


def test_pre_filter_drops_extreme_aspect_ratio():
    v = ImageValidator()
    # 1200x100 → ratio 12 → banner
    assert v.pre_filter(_img("https://x.sk/photo.jpg", width=1200, height=100)) is False


# ---------------------------------------------------------------------------
# HEAD validation — exercised via httpx.MockTransport so no real network.
# ---------------------------------------------------------------------------
class _FakeTransport(httpx.MockTransport):
    pass


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_head_validate_accepts_image_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg", "content-length": "120000"},
        )

    v = ImageValidator()
    async with _client(handler) as client:
        ok = await v.head_validate("https://x.sk/p.jpg", client=client)
    assert ok is True


@pytest.mark.asyncio
async def test_head_validate_rejects_non_image_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "1000"},
        )

    v = ImageValidator()
    async with _client(handler) as client:
        ok = await v.head_validate("https://x.sk/p.jpg", client=client)
    assert ok is False
