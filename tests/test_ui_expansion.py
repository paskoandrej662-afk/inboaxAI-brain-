import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_detect_returns_required_keys():
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': True, 'carousel_js': False,
        'swiper_api_available': False,
        'accordion': True, 'lazy_load': False,
        'complexity_score': 0.3,
    })
    result = await pool._detect_ui_patterns(fake_page)
    for k in ('carousel_native', 'carousel_js', 'swiper_api_available', 'accordion', 'lazy_load', 'complexity_score'):
        assert k in result


@pytest.mark.asyncio
async def test_detect_defensive():
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(side_effect=RuntimeError("dead"))
    result = await pool._detect_ui_patterns(fake_page)
    assert result['complexity_score'] == 0.5
    assert result['carousel_native'] is False


@pytest.mark.asyncio
async def test_low_complexity_js_carousel_no_api_triggers_dom_fallback():
    """LOW (<0.3) + JS carousel + no API → triggers DOM fallback."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': False, 'carousel_js': True,
        'swiper_api_available': False,
        'accordion': True, 'lazy_load': True,
        'complexity_score': 0.1,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=3)
    pool._try_swiper_js_api = AsyncMock(return_value=0)
    pool._cycle_native_scroll = AsyncMock(return_value=0)
    pool._dom_fallback_expand_carousels = AsyncMock(return_value=14)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    assert result['allow_dom_mutation'] is True
    assert result['lazy_triggered'] is True
    assert result['accordions_expanded'] == 3
    assert result['dom_fallback_modified'] == 14


@pytest.mark.asyncio
async def test_low_complexity_native_only_carousel_uses_scroll():
    """LOW + only native carousel → scroll cycle handles it, NO DOM fallback."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': True, 'carousel_js': False,
        'swiper_api_available': False,
        'accordion': False, 'lazy_load': False,
        'complexity_score': 0.1,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=0)
    pool._try_swiper_js_api = AsyncMock(return_value=0)
    pool._cycle_native_scroll = AsyncMock(return_value=2)  # cycled 2 native carousels
    pool._dom_fallback_expand_carousels = AsyncMock(return_value=99)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    assert result['native_scroll_cycled'] == 2
    assert result['dom_fallback_modified'] == 0  # NOT triggered (scroll worked)
    pool._dom_fallback_expand_carousels.assert_not_called()


@pytest.mark.asyncio
async def test_low_native_carousel_no_scroll_triggers_dom_fallback():
    """LOW + native carousel detected + scroll cycled 0 → DOM fallback triggered.
    BUG #1 fix: previously DOM fallback only checked carousel_js."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': True, 'carousel_js': False,
        'swiper_api_available': False,
        'accordion': False, 'lazy_load': False,
        'complexity_score': 0.1,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=0)
    pool._try_swiper_js_api = AsyncMock(return_value=0)
    pool._cycle_native_scroll = AsyncMock(return_value=0)  # scroll didn't work
    pool._dom_fallback_expand_carousels = AsyncMock(return_value=5)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    # DOM fallback MUST fire even though carousel_js was False
    assert result['dom_fallback_modified'] == 5


@pytest.mark.asyncio
async def test_medium_complexity_no_dom_fallback():
    """MEDIUM (0.3-0.7) blocks DOM fallback even when needed."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': False, 'carousel_js': True,
        'swiper_api_available': False,
        'accordion': True, 'lazy_load': True,
        'complexity_score': 0.5,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=2)
    pool._try_swiper_js_api = AsyncMock(return_value=0)
    pool._cycle_native_scroll = AsyncMock(return_value=0)
    pool._dom_fallback_expand_carousels = AsyncMock(return_value=99)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    assert result['allow_dom_mutation'] is False
    assert result['accordions_expanded'] == 2
    assert result['dom_fallback_modified'] == 0  # blocked by gate
    pool._dom_fallback_expand_carousels.assert_not_called()


@pytest.mark.asyncio
async def test_high_complexity_only_lazy():
    """HIGH (>=0.7) skips everything except lazy load."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': True, 'carousel_js': True,
        'swiper_api_available': True,
        'accordion': True, 'lazy_load': True,
        'complexity_score': 0.85,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=99)
    pool._try_swiper_js_api = AsyncMock(return_value=99)
    pool._cycle_native_scroll = AsyncMock(return_value=99)
    pool._dom_fallback_expand_carousels = AsyncMock(return_value=99)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    assert result['lazy_triggered'] is True
    assert result['accordions_expanded'] == 0
    assert result['swiper_api_cycled'] == 0
    assert result['native_scroll_cycled'] == 0
    assert result['dom_fallback_modified'] == 0
    pool._expand_accordions_strict.assert_not_called()
    pool._try_swiper_js_api.assert_not_called()
    pool._cycle_native_scroll.assert_not_called()
    pool._dom_fallback_expand_carousels.assert_not_called()


@pytest.mark.asyncio
async def test_swiper_api_success_skips_dom_fallback():
    """If Swiper API cycles successfully, DOM fallback is NOT called even at LOW."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': False, 'carousel_js': True,
        'swiper_api_available': True,
        'accordion': False, 'lazy_load': False,
        'complexity_score': 0.2,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=0)
    pool._try_swiper_js_api = AsyncMock(return_value=3)
    pool._cycle_native_scroll = AsyncMock(return_value=0)
    pool._dom_fallback_expand_carousels = AsyncMock(return_value=99)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    assert result['swiper_api_cycled'] == 3
    assert result['dom_fallback_modified'] == 0
    pool._dom_fallback_expand_carousels.assert_not_called()
