"""Tests for hds_v3 Batcher — offline, no network."""
from __future__ import annotations

from app.core.extractors.hds_v3.batcher import Batcher
from app.core.extractors.hds_v3.types import DiscoveredPage, PagePriority


def _page(url: str) -> DiscoveredPage:
    return DiscoveredPage(
        url=url,
        priority=PagePriority.TIER_1_CRITICAL,
        discovered_via="test",
    )


def test_batcher_splits_12_into_4_batches():
    batcher = Batcher()
    pages = [_page(f"https://x.sk/p{i}") for i in range(12)]
    batches = batcher.make_batches(pages)
    assert len(batches) == 4
    assert all(len(b) == 3 for b in batches)


def test_batcher_handles_7_pages():
    batcher = Batcher()
    pages = [_page(f"https://x.sk/p{i}") for i in range(7)]
    batches = batcher.make_batches(pages)
    assert len(batches) == 3
    assert len(batches[0]) == 3
    assert len(batches[1]) == 3
    assert len(batches[2]) == 1


def test_batcher_handles_empty_list():
    assert Batcher().make_batches([]) == []


def test_batcher_handles_single_page():
    batcher = Batcher()
    batches = batcher.make_batches([_page("https://x.sk/")])
    assert len(batches) == 1
    assert len(batches[0]) == 1


def test_batcher_preserves_order():
    batcher = Batcher()
    pages = [_page(f"https://x.sk/p{i}") for i in range(5)]
    batches = batcher.make_batches(pages)
    flat = [p for batch in batches for p in batch]
    assert flat == pages
