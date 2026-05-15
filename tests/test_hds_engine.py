"""Testy pre HDS engine — end-to-end s mockovanym Sonnet vision."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from bs4 import BeautifulSoup

from app.core.extractors.hds.cluster_detector import find_siblings
from app.core.extractors.hds.engine import run_hds_extraction
from app.core.extractors.hds.field_extractor import extract_fields
from app.core.extractors.hds.lca_finder import find_lca
from app.core.extractors.hds.types import Seed


FIXTURE = (
    Path(__file__).parent / "fixtures" / "skakacky_homepage.html"
).read_text(encoding="utf-8")


def test_fixture_loads():
    assert "Tiger" in FIXTURE
    assert "Rozprávkovo" in FIXTURE
    assert "elementor-flip-box" in FIXTURE


def test_lca_to_cluster_to_fields_pipeline():
    """Cely deterministicky pipeline na fixture — bez vision callu."""
    soup = BeautifulSoup(FIXTURE, "html.parser")
    seed = Seed(name="Tiger", price="160€")
    lca = find_lca(soup, seed)
    assert lca is not None
    assert lca.name in ("div", "section", "article", "li")

    siblings = find_siblings(lca)
    # Fixture ma 14 kariet
    assert len(siblings) == 14

    cards_with_name = 0
    cards_with_price = 0
    cards_with_kapacita = 0
    for s in siblings:
        fields = extract_fields(s)
        if fields.get("name"):
            cards_with_name += 1
        if fields.get("price_eur") is not None or fields.get("price_text"):
            cards_with_price += 1
        if "kapacita" in fields.get("attributes", {}):
            cards_with_kapacita += 1

    # Vsetky karty maju nazov
    assert cards_with_name == 14
    # Aspon 13/14 maju cenu (Klauni = na vyziadanie → price_text)
    assert cards_with_price >= 13
    # Aspon 10/14 maju kapacitu (niektore atrakcie ju nemaju)
    assert cards_with_kapacita >= 10


@pytest.mark.asyncio
async def test_engine_full_pipeline_mocked_seeds():
    """Integration test: mockuj Sonnet vision, over ze pipeline extrahuje 14 kariet."""
    mock_seeds = (
        [
            Seed(name="Tiger", price="160€"),
            Seed(name="Rozprávkovo", price="150€"),
            Seed(name="Avengers", price="170€"),
        ],
        0.03,
    )

    # Mock screenshot bytes (placeholder — neuvazujeme realnym vision call)
    fake_screenshot = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with patch(
        "app.core.extractors.hds.engine.find_seeds",
        AsyncMock(return_value=mock_seeds),
    ):
        result = await run_hds_extraction(
            html=FIXTURE,
            screenshot_bytes=fake_screenshot,
            page=None,
            page_url="https://skakaciehradyorava.sk/",
        )

    assert result.seeds_found == 3
    assert result.lcas_found >= 1
    assert result.candidate_count == 14
    # Page=None → visibility no-op → vsetky karty prejdu
    assert result.after_visibility == 14
    # Min 10 high-confidence (name + price + recurring → score 1.0)
    assert result.after_confidence >= 10
    # Arbitration sa pre uncertain karty zavolala iba ked je 0.4-0.7
    # V nasom pripade by mali byt vsetky high-confidence (1.0) → no arbitration
    assert result.arbitration_called == 0
    assert result.success is True
    assert len(result.cards) >= 10


@pytest.mark.asyncio
async def test_engine_no_seeds_returns_fallback():
    """Ak vision nevrati ziadne seedy, pipeline vrat success=False."""
    fake_screenshot = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with patch(
        "app.core.extractors.hds.engine.find_seeds",
        AsyncMock(return_value=([], 0.0)),
    ):
        result = await run_hds_extraction(
            html=FIXTURE,
            screenshot_bytes=fake_screenshot,
            page=None,
            page_url="https://skakaciehradyorava.sk/",
        )

    assert result.success is False
    assert result.fallback_reason == "no_seeds_from_vision"
    assert result.seeds_found == 0


@pytest.mark.asyncio
async def test_engine_no_lca_returns_fallback():
    """Ak seed.name nie je v DOM, LCA failne."""
    mock_seeds = ([Seed(name="NonExistentProduct12345", price="999€")], 0.01)
    fake_screenshot = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with patch(
        "app.core.extractors.hds.engine.find_seeds",
        AsyncMock(return_value=mock_seeds),
    ):
        result = await run_hds_extraction(
            html=FIXTURE,
            screenshot_bytes=fake_screenshot,
            page=None,
            page_url="https://skakaciehradyorava.sk/",
        )

    assert result.success is False
    assert result.fallback_reason == "no_lca_found"


@pytest.mark.asyncio
async def test_engine_empty_html_fails_gracefully():
    """Empty HTML → defensive failure."""
    mock_seeds = ([Seed(name="Tiger", price="160€")], 0.01)
    fake_screenshot = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with patch(
        "app.core.extractors.hds.engine.find_seeds",
        AsyncMock(return_value=mock_seeds),
    ):
        result = await run_hds_extraction(
            html="",
            screenshot_bytes=fake_screenshot,
            page=None,
            page_url="https://example.com/",
        )

    assert result.success is False
    assert result.fallback_reason is not None
