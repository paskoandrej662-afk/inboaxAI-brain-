import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_detect_includes_flip_box():
    """_detect_ui_patterns now includes 'flip_box' key."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': False, 'carousel_js': False,
        'swiper_api_available': False,
        'accordion': False, 'lazy_load': False,
        'flip_box': True, 'flip_box_count': 14,
        'complexity_score': 0.1,
    })
    result = await pool._detect_ui_patterns(fake_page)
    assert 'flip_box' in result
    assert result['flip_box'] is True


@pytest.mark.asyncio
async def test_flip_box_triggers_expansion():
    """LOW complexity + flip_box detected → _expand_flip_boxes called."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': False, 'carousel_js': False,
        'swiper_api_available': False,
        'accordion': False, 'lazy_load': False,
        'flip_box': True,
        'complexity_score': 0.1,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=0)
    pool._try_swiper_js_api = AsyncMock(return_value=0)
    pool._cycle_native_scroll = AsyncMock(return_value=0)
    pool._dom_fallback_expand_carousels = AsyncMock(return_value=0)
    pool._expand_flip_boxes = AsyncMock(return_value=14)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    assert result['flip_boxes_expanded'] == 14
    pool._expand_flip_boxes.assert_called_once()


@pytest.mark.asyncio
async def test_high_complexity_skips_flip_box():
    """HIGH complexity skips flip box expansion (under accordion gate)."""
    from app.core.browser import BrowserPool
    pool = BrowserPool()
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={
        'carousel_native': False, 'carousel_js': False,
        'swiper_api_available': False,
        'accordion': False, 'lazy_load': False,
        'flip_box': True,
        'complexity_score': 0.85,
    })
    fake_page.wait_for_timeout = AsyncMock()
    pool._trigger_lazy_load = AsyncMock()
    pool._expand_accordions_strict = AsyncMock(return_value=99)
    pool._expand_flip_boxes = AsyncMock(return_value=99)

    result = await pool._apply_ui_expansion(fake_page, "https://x.sk/")

    assert result['flip_boxes_expanded'] == 0
    pool._expand_flip_boxes.assert_not_called()


@pytest.mark.asyncio
async def test_expand_flip_boxes_uses_correct_elementor_classnames():
    """Verify _expand_flip_boxes JS targets the real Elementor classes (not BEM modifier syntax).

    Real Elementor naming uses single underscore __front and __back, not double underscore
    with modifier --front / --back.
    """
    from app.core.browser import BrowserPool
    pool = BrowserPool()

    # Inspect the source of the method to make sure it uses correct selectors
    import inspect
    source = inspect.getsource(pool._expand_flip_boxes)

    # Must reference correct Elementor classes
    assert '.elementor-flip-box__front' in source, \
        "_expand_flip_boxes must target .elementor-flip-box__front (Elementor real naming)"
    assert '.elementor-flip-box__back' in source, \
        "_expand_flip_boxes must target .elementor-flip-box__back (Elementor real naming)"

    # Must NOT use the wrong BEM modifier syntax
    assert '__layer--front' not in source, \
        "Old wrong selector __layer--front should be removed"
    assert '__layer--back' not in source, \
        "Old wrong selector __layer--back should be removed"


@pytest.mark.asyncio
async def test_expand_flip_boxes_uses_important_for_critical_styles():
    """Verify that critical transform/visibility styles use setProperty(..., 'important')
    to override Elementor's CSS specificity.
    """
    from app.core.browser import BrowserPool
    import inspect
    pool = BrowserPool()
    source = inspect.getsource(pool._expand_flip_boxes)

    # Critical styles must use setProperty with 'important' flag
    assert "setProperty('transform', 'none', 'important')" in source, \
        "transform must be set with !important to beat Elementor CSS"
    assert "setProperty('display'" in source and "'important'" in source, \
        "display must use setProperty with !important"
