import time

import pytest  # noqa: F401  (drzime kvoli buducim parametrizovanym testom)

from app.core.ingest_v2.budget import BudgetManager
from app.core.ingest_v2.types import BudgetLimits


def test_budget_init_default_limits():
    bm = BudgetManager()
    assert bm.limits.max_pages == 12
    assert bm.pages_used == 0
    assert bm.spent_eur == 0.0


def test_budget_can_render_within_limits():
    bm = BudgetManager()
    ok, reason = bm.can_render_page(estimated_ms=5000)
    assert ok is True
    assert reason == "ok"


def test_budget_page_limit_blocks_render():
    bm = BudgetManager(BudgetLimits(max_pages=2))
    bm.record_render(page_count=2)
    ok, reason = bm.can_render_page()
    assert ok is False
    assert reason == "page_limit_reached"


def test_budget_html_bytes_limit():
    bm = BudgetManager(BudgetLimits(max_html_bytes_total=10000))
    bm.record_render(page_count=1, html_bytes=8000)
    ok, reason = bm.can_render_page(estimated_html_bytes=3000)
    assert ok is False
    assert reason == "html_bytes_limit_reached"


def test_budget_can_spend_within_soft():
    bm = BudgetManager()
    ok, reason = bm.can_spend(0.05, "vision_call")
    assert ok is True


def test_budget_hard_limit_blocks_all():
    bm = BudgetManager(BudgetLimits(hard_limit_eur=0.50))
    bm.record_operation("vision_call", actual_eur=0.40)
    ok, reason = bm.can_spend(0.20, "vision_call")
    assert ok is False
    assert reason == "hard_limit_would_exceed"


def test_budget_soft_limit_blocks_only_expensive():
    bm = BudgetManager(BudgetLimits(soft_limit_eur=0.50, hard_limit_eur=1.0))
    bm.record_operation("render", actual_eur=0.45)
    # Cheap operation: allowed
    ok, _ = bm.can_spend(0.10, "render")
    assert ok is True
    # Expensive operation: blocked
    ok2, reason = bm.can_spend(0.10, "vision_call")
    assert ok2 is False
    assert reason == "soft_limit_for_expensive"


def test_budget_image_candidate_limit():
    bm = BudgetManager(BudgetLimits(max_images_candidates=5))
    bm.record_images(5)
    ok, reason = bm.can_collect_image()
    assert ok is False
    assert reason == "image_candidate_limit_reached"


def test_budget_block_limit():
    bm = BudgetManager(BudgetLimits(max_blocks=10))
    bm.record_blocks(10)
    ok, reason = bm.can_store_block()
    assert ok is False
    assert reason == "block_limit_reached"


def test_budget_record_operation_increments_spent():
    bm = BudgetManager()
    bm.record_operation(
        "vision_call", actual_eur=0.05, model="sonnet-4.6", input_tokens=1000, output_tokens=200
    )
    assert bm.spent_eur == 0.05
    assert len(bm.operations) == 1


def test_budget_status_reflects_state():
    bm = BudgetManager(BudgetLimits(max_pages=5, hard_limit_eur=1.0))
    bm.record_render(page_count=3, render_ms=15000, html_bytes=500000)
    bm.record_blocks(20)
    bm.record_operation("vision_call", actual_eur=0.30)
    s = bm.status()
    assert s.pages_used == 3
    assert s.blocks_used == 20
    assert s.spent_eur == 0.30
    assert s.page_limit_hit is False


def test_budget_runtime_limit():
    bm = BudgetManager(BudgetLimits(max_runtime_seconds=0))
    # Simulate that time passed
    bm._started_at = time.time() - 1.0
    ok, reason = bm.can_render_page()
    assert ok is False
    assert reason == "runtime_limit_reached"
