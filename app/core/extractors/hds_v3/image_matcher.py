"""Match images to products by linear text proximity.

DOM-agnostic algorithm: works on any CMS (WordPress, Webflow, custom)
because it never inspects DOM structure — only the linear order of
images and text in the document.

Scoring signals per (image, product) pair:

* **Proximity** (0.0..1.0): how close the nearest text mention of the
  product is to the image, measured in stream positions.
* **Filename match** (+0.20): product name appears in the image filename.
* **URL path match** (+0.15): product name appears elsewhere in the URL
  (only when filename didn't already match — no double-count).
* **Alt text match** (+0.10): product name appears in the `alt` attr.

Confidence below `MIN_CONFIDENCE_DETERMINISTIC` (0.5) is rejected — we
never assign an image when uncertain.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from app.core.extractors.hds_v3.image_extractor import MediaItem

logger = logging.getLogger(__name__)


@dataclass
class ImageMatch:
    product_name: str
    image_url: str
    confidence: float
    filename: Optional[str] = None
    width: int = 0
    height: int = 0
    signals: dict = field(default_factory=dict)


class ImageMatcher:
    """Match images to products using linear text proximity."""

    PROXIMITY_WINDOW = 10
    MIN_CONFIDENCE_DETERMINISTIC = 0.5
    MIN_CONFIDENCE_KEEP = 0.5  # threshold below which we drop the match

    FILENAME_BOOST = 0.20
    URL_BOOST = 0.15
    ALT_BOOST = 0.10

    SECONDARY_CAP = 4

    # ------------------------------------------------------------------ util
    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""
        nfkd = unicodedata.normalize("NFKD", text)
        no_diacritics = "".join(c for c in nfkd if not unicodedata.combining(c))
        return no_diacritics.lower()

    # ------------------------------------------------------------------ main
    def match_images(
        self,
        stream: list[MediaItem],
        product_names: list[str],
    ) -> list[ImageMatch]:
        """Return matches for each image, deduplicated 1:1 (image → product)."""
        if not stream or not product_names:
            return []

        normalized_products = [
            (name, self._normalize(name))
            for name in product_names
            if name and self._normalize(name)
        ]
        if not normalized_products:
            return []

        # Precompute text item list (sorted by position).
        text_items = [
            (item.position, self._normalize(item.text or ""), item.text or "")
            for item in stream
            if item.item_type == "text" and item.text
        ]

        matches: list[ImageMatch] = []
        for item in stream:
            if item.item_type != "image" or not item.src:
                continue
            best = self._best_match_for_image(item, text_items, normalized_products)
            if best is not None:
                matches.append(best)
        return matches

    def _best_match_for_image(
        self,
        img: MediaItem,
        text_items: list[tuple[int, str, str]],
        normalized_products: list[tuple[str, str]],
    ) -> Optional[ImageMatch]:
        img_pos = img.position
        proximity = [
            (abs(pos - img_pos), norm, raw)
            for pos, norm, raw in text_items
            if abs(pos - img_pos) <= self.PROXIMITY_WINDOW
        ]

        best_score = 0.0
        best_product: Optional[str] = None
        best_signals: dict = {}

        for product_orig, product_norm in normalized_products:
            score = 0.0
            signals: dict = {}

            # Proximity signal — highest among nearby text mentions
            for distance, text_norm, raw in proximity:
                if product_norm and product_norm in text_norm:
                    proximity_score = max(
                        0.0, 1.0 - (distance / max(self.PROXIMITY_WINDOW, 1))
                    )
                    if proximity_score > score:
                        score = proximity_score
                        signals["proximity"] = {
                            "distance": distance,
                            "score": round(proximity_score, 3),
                            "matched_text": raw[:80],
                        }

            # Filename match
            filename_hit = False
            if img.filename and product_norm in self._normalize(img.filename):
                score = min(1.0, score + self.FILENAME_BOOST)
                signals["filename"] = {
                    "boost": self.FILENAME_BOOST,
                    "filename": img.filename,
                }
                filename_hit = True

            # URL path match (skip if filename already matched)
            if not filename_hit and img.src:
                url_norm = self._normalize(img.src)
                if product_norm and product_norm in url_norm:
                    score = min(1.0, score + self.URL_BOOST)
                    signals["url_path"] = {"boost": self.URL_BOOST}

            # Alt text match
            if img.alt and product_norm in self._normalize(img.alt):
                score = min(1.0, score + self.ALT_BOOST)
                signals["alt"] = {"boost": self.ALT_BOOST, "alt": img.alt}

            if score > best_score:
                best_score = score
                best_product = product_orig
                best_signals = signals

        if best_product is None or best_score < self.MIN_CONFIDENCE_KEEP:
            return None

        return ImageMatch(
            product_name=best_product,
            image_url=img.src or "",
            confidence=round(best_score, 4),
            filename=img.filename,
            width=img.width or 0,
            height=img.height or 0,
            signals=best_signals,
        )

    # ------------------------------------------------------------------ group
    def group_by_product(
        self, matches: list[ImageMatch]
    ) -> dict[str, dict]:
        """Group matches per product, return primary + secondary URLs.

        Primary = highest-confidence match. Secondary = next N matches,
        deduplicated by URL and capped at `SECONDARY_CAP`.
        """
        by_product: dict[str, list[ImageMatch]] = {}
        for m in matches:
            by_product.setdefault(m.product_name, []).append(m)

        result: dict[str, dict] = {}
        for product, ms in by_product.items():
            ms.sort(key=lambda x: (-x.confidence, x.image_url))
            primary = ms[0]
            seen = {primary.image_url}
            secondary: list[str] = []
            for m in ms[1:]:
                if m.image_url in seen:
                    continue
                seen.add(m.image_url)
                secondary.append(m.image_url)
                if len(secondary) >= self.SECONDARY_CAP:
                    break
            result[product] = {
                "primary": primary.image_url,
                "primary_confidence": primary.confidence,
                "secondary": secondary,
            }
        return result


__all__ = ["ImageMatcher", "ImageMatch"]
