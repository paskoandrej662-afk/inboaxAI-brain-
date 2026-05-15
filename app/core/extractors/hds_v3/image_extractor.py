"""Extract linear media stream (images + text nodes) from a rendered page.

Two entry points keep production fidelity AND test ergonomics:

* `extract_stream(page)` — production path via Playwright `page.evaluate`.
  Uses `naturalWidth`/`window.getComputedStyle` so we can detect images
  hidden by CSS classes, not just inline `style`.
* `extract_stream_from_html(html)` — offline path via BeautifulSoup.
  Used by tests and as a fallback when Playwright is unavailable.
  Visibility check is best-effort (inline `style="display:none"` and
  the `hidden` HTML attribute), since BS4 cannot compute CSS.

Both paths produce a `list[MediaItem]` ordered by document position.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


@dataclass
class MediaItem:
    """One node in the linear document stream."""

    position: int
    item_type: str  # "image" | "text"
    src: Optional[str] = None
    filename: Optional[str] = None
    alt: Optional[str] = None
    width: int = 0
    height: int = 0
    text: Optional[str] = None


_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
_HIDDEN_STYLE_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0)\s*(?:;|$)",
    re.IGNORECASE,
)


class ImageExtractor:
    """Extract a linear stream of images + text from a rendered page."""

    MIN_TEXT_LENGTH = 3
    MAX_TEXT_PER_NODE = 200

    # ------------------------------------------------------------------ JS
    _JS_WALK = """
        () => {
            const items = [];
            let pos = 0;
            const SKIP = new Set(['script', 'style', 'noscript', 'svg', 'template']);

            function isHidden(el) {
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === 'none') return true;
                if (style.visibility === 'hidden') return true;
                if (parseFloat(style.opacity || '1') === 0) return true;
                if (el.hasAttribute('hidden')) return true;
                return false;
            }

            function walk(node) {
                if (!node) return;
                if (node.nodeType === 1) {
                    const tag = node.tagName.toLowerCase();
                    if (SKIP.has(tag)) return;
                    if (isHidden(node)) return;
                    if (tag === 'img') {
                        const src = node.currentSrc || node.src || node.getAttribute('data-src') || '';
                        if (src && /^https?:/i.test(src)) {
                            items.push({
                                pos: pos++,
                                type: 'image',
                                src: src,
                                alt: node.alt || '',
                                width: node.naturalWidth || parseInt(node.getAttribute('width') || '0', 10) || 0,
                                height: node.naturalHeight || parseInt(node.getAttribute('height') || '0', 10) || 0,
                            });
                        }
                        return;
                    }
                } else if (node.nodeType === 3) {
                    const text = (node.textContent || '').trim();
                    if (text.length >= 3) {
                        items.push({
                            pos: pos++,
                            type: 'text',
                            text: text.substring(0, 200),
                        });
                    }
                    return;
                }
                for (const child of node.childNodes) walk(child);
            }

            if (document.body) walk(document.body);
            return items;
        }
    """

    async def extract_stream(self, page) -> list[MediaItem]:
        """Production entry — walk DOM of a Playwright `Page`."""
        try:
            raw_items = await page.evaluate(self._JS_WALK)
        except Exception as e:  # noqa: BLE001
            logger.warning("ImageExtractor: page.evaluate failed: %s", e)
            return []
        return [self._raw_to_item(r) for r in (raw_items or [])]

    def extract_stream_from_html(
        self, html: str, base_url: Optional[str] = None
    ) -> list[MediaItem]:
        """Offline entry — walk DOM of an HTML string via BeautifulSoup.

        `base_url` is used to resolve relative `<img src>` URLs.
        """
        if not html:
            return []
        try:
            from bs4 import BeautifulSoup, NavigableString, Tag
        except Exception as e:  # noqa: BLE001
            logger.warning("ImageExtractor: bs4 import failed: %s", e)
            return []

        soup = BeautifulSoup(html, "html.parser")
        body = soup.body or soup
        items: list[MediaItem] = []
        pos = 0

        def hidden_by_inline(tag: "Tag") -> bool:
            if tag.has_attr("hidden"):
                return True
            style = tag.get("style") or ""
            if style and _HIDDEN_STYLE_RE.search(style):
                return True
            return False

        def walk(node):
            nonlocal pos
            if isinstance(node, NavigableString):
                parent = node.parent
                if parent is not None and getattr(parent, "name", None) in _SKIP_TAGS:
                    return
                text = str(node).strip()
                if len(text) >= self.MIN_TEXT_LENGTH:
                    items.append(
                        MediaItem(
                            position=pos,
                            item_type="text",
                            text=text[: self.MAX_TEXT_PER_NODE],
                        )
                    )
                    pos += 1
                return
            if not isinstance(node, Tag):
                return
            name = (node.name or "").lower()
            if name in _SKIP_TAGS:
                return
            if hidden_by_inline(node):
                return
            if name == "img":
                src = (
                    node.get("src")
                    or node.get("data-src")
                    or node.get("data-lazy-src")
                    or ""
                )
                if not src:
                    return
                if base_url:
                    src = urljoin(base_url, src)
                if not src.lower().startswith(("http://", "https://")):
                    return
                width = _parse_int(node.get("width"))
                height = _parse_int(node.get("height"))
                items.append(
                    MediaItem(
                        position=pos,
                        item_type="image",
                        src=src,
                        filename=_extract_filename(src),
                        alt=(node.get("alt") or "").strip(),
                        width=width,
                        height=height,
                    )
                )
                pos += 1
                return
            for child in node.children:
                walk(child)

        for child in body.children:
            walk(child)
        return items

    def _raw_to_item(self, raw: dict[str, Any]) -> MediaItem:
        kind = raw.get("type")
        if kind == "image":
            src = raw.get("src") or ""
            return MediaItem(
                position=int(raw.get("pos", 0)),
                item_type="image",
                src=src,
                filename=_extract_filename(src),
                alt=(raw.get("alt") or "").strip(),
                width=int(raw.get("width", 0) or 0),
                height=int(raw.get("height", 0) or 0),
            )
        return MediaItem(
            position=int(raw.get("pos", 0)),
            item_type="text",
            text=(raw.get("text") or "")[: ImageExtractor.MAX_TEXT_PER_NODE],
        )


def _parse_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return 0


def _extract_filename(src: str) -> str:
    if not src:
        return ""
    try:
        parsed = urlparse(src)
        return (parsed.path or "").rsplit("/", 1)[-1]
    except Exception:  # noqa: BLE001
        return src.rsplit("/", 1)[-1].split("?")[0]


__all__ = ["ImageExtractor", "MediaItem"]
