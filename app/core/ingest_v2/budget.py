"""BudgetManager — sleduje resource + EUR spotrebu pocas jedneho ingestion jobu."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.core.ingest_v2.types import BudgetLimits, BudgetStatus

logger = logging.getLogger(__name__)


@dataclass
class _BudgetOperation:
    operation: str
    estimated_eur: float
    actual_eur: float
    duration_ms: int
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    bytes_in: Optional[int] = None
    bytes_out: Optional[int] = None
    timestamp: float = field(default_factory=time.time)


class BudgetManager:
    """Tracks resource + EUR consumption during an ingestion job.

    Used by orchestrator to decide whether to continue with expensive operations
    (vision calls, image downloads) or to degrade gracefully.
    """

    def __init__(self, limits: BudgetLimits | None = None) -> None:
        self.limits = limits or BudgetLimits()
        self.spent_eur = 0.0
        self.pages_used = 0
        self.blocks_used = 0
        self.images_used = 0
        self.render_ms_used = 0
        self.html_bytes_used = 0
        self.operations: list[_BudgetOperation] = []
        self._started_at: float = time.time()

    def runtime_seconds(self) -> float:
        return time.time() - self._started_at

    def can_render_page(
        self, estimated_ms: int = 5000, estimated_html_bytes: int = 200_000
    ) -> tuple[bool, str]:
        """Check whether crawler can render another page."""
        if self.pages_used >= self.limits.max_pages:
            return False, "page_limit_reached"
        if self.runtime_seconds() >= self.limits.max_runtime_seconds:
            return False, "runtime_limit_reached"
        if self.render_ms_used + estimated_ms > self.limits.max_render_ms_total:
            return False, "render_ms_limit_reached"
        if self.html_bytes_used + estimated_html_bytes > self.limits.max_html_bytes_total:
            return False, "html_bytes_limit_reached"
        return True, "ok"

    def can_spend(self, estimated_eur: float, operation: str = "unknown") -> tuple[bool, str]:
        """Check whether an LLM/AI operation can run within budget."""
        projected = self.spent_eur + estimated_eur
        if projected > self.limits.hard_limit_eur:
            return False, "hard_limit_would_exceed"
        # Soft limit only blocks "expensive" operations
        expensive_ops = {"vision_call", "image_describe", "company_profile"}
        if operation in expensive_ops and projected > self.limits.soft_limit_eur:
            return False, "soft_limit_for_expensive"
        return True, "ok"

    def can_store_block(self) -> tuple[bool, str]:
        if self.blocks_used >= self.limits.max_blocks:
            return False, "block_limit_reached"
        return True, "ok"

    def can_collect_image(self) -> tuple[bool, str]:
        if self.images_used >= self.limits.max_images_candidates:
            return False, "image_candidate_limit_reached"
        return True, "ok"

    def record_render(self, page_count: int = 1, render_ms: int = 0, html_bytes: int = 0) -> None:
        self.pages_used += page_count
        self.render_ms_used += render_ms
        self.html_bytes_used += html_bytes

    def record_blocks(self, count: int) -> None:
        self.blocks_used += count

    def record_images(self, count: int) -> None:
        self.images_used += count

    def record_operation(
        self,
        operation: str,
        actual_eur: float = 0.0,
        duration_ms: int = 0,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        bytes_in: int | None = None,
        bytes_out: int | None = None,
    ) -> None:
        self.spent_eur += actual_eur
        self.operations.append(
            _BudgetOperation(
                operation=operation,
                estimated_eur=0.0,
                actual_eur=actual_eur,
                duration_ms=duration_ms,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
            )
        )

    def status(self) -> BudgetStatus:
        return BudgetStatus(
            spent_eur=round(self.spent_eur, 6),
            pages_used=self.pages_used,
            blocks_used=self.blocks_used,
            images_used=self.images_used,
            render_ms_used=self.render_ms_used,
            html_bytes_used=self.html_bytes_used,
            runtime_seconds=round(self.runtime_seconds(), 2),
            soft_limit_hit=self.spent_eur >= self.limits.soft_limit_eur,
            hard_limit_hit=self.spent_eur >= self.limits.hard_limit_eur,
            page_limit_hit=self.pages_used >= self.limits.max_pages,
            runtime_limit_hit=self.runtime_seconds() >= self.limits.max_runtime_seconds,
            operations_count=len(self.operations),
        )
