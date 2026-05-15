"""Batcher: rozdeli DiscoveredPage list do trojic pre Gemini volania."""
from __future__ import annotations

from app.core.extractors.hds_v3.types import DiscoveredPage


class Batcher:
    """Rozdeli pages do batchov po 3 URL.

    Strategy: maintain page order from crawler (TIER_0 first).
    Last batch may have 1-3 URLs.
    """

    BATCH_SIZE = 3

    def make_batches(
        self, pages: list[DiscoveredPage]
    ) -> list[list[DiscoveredPage]]:
        """Split pages into batches of 3 (last batch can be 1-3).

        Example:
            12 pages -> [3, 3, 3, 3]
            7 pages -> [3, 3, 1]
            2 pages -> [2]
        """
        if not pages:
            return []
        return [
            pages[i : i + self.BATCH_SIZE]
            for i in range(0, len(pages), self.BATCH_SIZE)
        ]
