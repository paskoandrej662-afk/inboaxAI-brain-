"""Phase 3 — Sibling Cluster Detection.

Z LCA elementu vytiahni tag + class_list. Najdi vsetky siblings v lca.parent
ktore maju rovnaky tag + min 50% jaccard zhodu v class_list. Vrat list of Tag
(vratane samotneho lca, v dokument-order).
"""

from __future__ import annotations

import logging
from typing import Optional

from bs4 import Tag

logger = logging.getLogger(__name__)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def find_siblings(lca: Tag) -> list[Tag]:
    """Najdi vsetkych siblings v lca.parent ktori maju rovnaky tag a >=50% class match.

    Vracia list Tag vratane samotneho lca, v dokument-order.
    Defenzivne: pri chybe vrat [lca].
    """
    try:
        if lca is None or not isinstance(lca, Tag):
            return []
        parent: Optional[Tag] = lca.parent
        if parent is None or not isinstance(parent, Tag):
            return [lca]

        target_tag = lca.name
        target_classes = set(lca.get("class") or [])

        siblings: list[Tag] = []
        for child in parent.find_all(recursive=False):
            if not isinstance(child, Tag):
                continue
            if child.name != target_tag:
                continue
            child_classes = set(child.get("class") or [])
            if not target_classes and not child_classes:
                # Oba bez tried — povaz za match (homogenny container)
                siblings.append(child)
                continue
            if _jaccard(target_classes, child_classes) >= 0.5:
                siblings.append(child)

        if not siblings:
            return [lca]
        return siblings
    except Exception as e:
        logger.debug("hds.find_siblings exception: %s", e)
        return [lca] if lca is not None else []
