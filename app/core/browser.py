from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    async_playwright,
)

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RenderedPage:
    url: str
    final_url: str
    html: str
    screenshot_png: bytes | None
    viewport_w: int
    viewport_h: int
    error: str | None = None


class BrowserPool:
    """Singleton-style Playwright wrapper.

    render_page() a discover_links() su defenzivne - nikdy neraisuju, vracaju
    RenderedPage s error pole alebo prazdny zoznam.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._started: bool = False

    async def start(self) -> None:
        if self._started:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={
                "width": settings.PLAYWRIGHT_VIEWPORT_WIDTH,
                "height": settings.PLAYWRIGHT_VIEWPORT_HEIGHT,
            },
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="sk-SK",
            ignore_https_errors=True,
        )
        self._started = True
        logger.info("BrowserPool: started")

    async def close(self) -> None:
        # Idempotentny - kazdy step v try/except aby cleanup vzdy dobehol
        try:
            if self._context is not None:
                await self._context.close()
        except Exception as e:
            logger.warning("BrowserPool: context close failed: %s", e)
        finally:
            self._context = None

        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception as e:
            logger.warning("BrowserPool: browser close failed: %s", e)
        finally:
            self._browser = None

        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception as e:
            logger.warning("BrowserPool: playwright stop failed: %s", e)
        finally:
            self._playwright = None

        self._started = False
        logger.info("BrowserPool: closed")

    async def render_page(
        self, url: str, *, take_screenshot: bool = True
    ) -> RenderedPage:
        if not self._started or self._context is None:
            return RenderedPage(
                url=url,
                final_url=url,
                html="",
                screenshot_png=None,
                viewport_w=0,
                viewport_h=0,
                error="browser_not_started",
            )

        page = None
        try:
            page = await self._context.new_page()
            await page.goto(
                url,
                wait_until="networkidle",
                timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
            )
            html = await page.content()
            final_url = page.url

            screenshot: bytes | None = None
            if take_screenshot:
                try:
                    screenshot = await page.screenshot(full_page=True, type="png")
                except Exception as e:
                    logger.warning("screenshot failed for %s: %s", url, e)
                    screenshot = None

            return RenderedPage(
                url=url,
                final_url=final_url,
                html=html,
                screenshot_png=screenshot,
                viewport_w=settings.PLAYWRIGHT_VIEWPORT_WIDTH,
                viewport_h=settings.PLAYWRIGHT_VIEWPORT_HEIGHT,
            )
        except Exception as e:
            logger.warning("render_page failed for %s: %s", url, e)
            return RenderedPage(
                url=url,
                final_url=url,
                html="",
                screenshot_png=None,
                viewport_w=0,
                viewport_h=0,
                error=str(e),
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def discover_links(self, url: str) -> list[str]:
        """Vrati vsetky absolutne href z JS-renderovaneho DOM. Vrati [] na failure."""
        if not self._started or self._context is None:
            return []

        page = None
        try:
            page = await self._context.new_page()
            await page.goto(
                url,
                wait_until="networkidle",
                timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
            )
            hrefs = await page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
            )
            return [h for h in hrefs if isinstance(h, str) and h]
        except Exception as e:
            logger.warning("discover_links failed for %s: %s", url, e)
            return []
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
