"""Pre-filter + HEAD-validate images to drop non-product junk.

Two stages:

* `pre_filter(item)` — synchronous, free. Drops obvious junk by
  filename/URL pattern, dimensions, aspect ratio.
* `head_validate(url)` — async, one HEAD request. Confirms the URL
  actually serves an image of a sane size.

Patterns are intentionally conservative: when in doubt we drop, so the
matcher only sees plausibly-product imagery.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.core.extractors.hds_v3.image_extractor import MediaItem

logger = logging.getLogger(__name__)


class ImageValidator:
    """Filter out non-product images before matching."""

    # Junk substrings on filename / URL path. Tuned per typical CMS naming
    # (WordPress, Webflow, Squarespace, Joomla, Shopify, generic).
    JUNK_PATTERNS = [
        r"logo",
        r"icon",
        r"favicon",
        r"placeholder",
        r"pixel",
        r"banner",
        r"hero",
        r"footer",
        r"spinner",
        r"loading",
        r"tracking",
        r"analytics",
        r"social[_-]",
        r"\bfb[_-]",
        r"instagram[_-]",
        r"twitter[_-]",
        r"map[_-]",
        r"gps[_-]",
        r"arrow",
        r"chevron",
        r"bullet",
        r"avatar",
        r"author",
        r"sprite",
    ]
    # `header` is risky because of "header" being part of product photos;
    # drop only if not co-occurring with product-ish words.
    _HEADER_RE = re.compile(r"header(?!.*product)", re.IGNORECASE)

    MIN_WIDTH = 200
    MIN_HEIGHT = 200
    MAX_ASPECT_RATIO = 5.0  # >5:1 is almost certainly a banner / divider
    MIN_FILE_SIZE = 10_000  # 10 KB
    MAX_FILE_SIZE = 5_000_000  # 5 MB

    HEAD_TIMEOUT_SEC = 5

    def __init__(self):
        self._junk_re = re.compile("|".join(self.JUNK_PATTERNS), re.IGNORECASE)

    # ------------------------------------------------------------------ pre
    def pre_filter(self, img: MediaItem) -> bool:
        """Return True if `img` plausibly is a product image."""
        if img is None or img.item_type != "image" or not img.src:
            return False

        # SVG = icon/logo, drop unconditionally.
        if img.src.lower().endswith(".svg"):
            return False

        # Filename / URL junk patterns.
        check_text = f"{img.filename or ''} {img.src}".lower()
        if self._junk_re.search(check_text):
            return False
        if self._HEADER_RE.search(check_text):
            return False

        # Dimensions (only if DOM gave them; many lazy-loaded imgs report 0).
        if img.width and img.width < self.MIN_WIDTH:
            return False
        if img.height and img.height < self.MIN_HEIGHT:
            return False

        if img.width and img.height:
            longer = max(img.width, img.height)
            shorter = min(img.width, img.height)
            if shorter > 0 and longer / shorter > self.MAX_ASPECT_RATIO:
                return False

        return True

    # ------------------------------------------------------------------ HEAD
    async def head_validate(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> bool:
        """Confirm via HEAD request that `url` serves a reasonable image."""
        if not url:
            return False

        own_client = False
        if client is None:
            client = httpx.AsyncClient(
                timeout=self.HEAD_TIMEOUT_SEC,
                follow_redirects=True,
            )
            own_client = True
        try:
            try:
                resp = await client.head(url)
            except Exception as e:  # noqa: BLE001
                logger.debug("HEAD validate failed for %s: %s", url, e)
                return False
            if resp.status_code != 200:
                return False
            ct = (resp.headers.get("content-type") or "").lower()
            if not ct.startswith("image/"):
                return False
            try:
                size = int(resp.headers.get("content-length") or 0)
            except ValueError:
                size = 0
            if size and (size < self.MIN_FILE_SIZE or size > self.MAX_FILE_SIZE):
                return False
            return True
        finally:
            if own_client:
                await client.aclose()


__all__ = ["ImageValidator"]
