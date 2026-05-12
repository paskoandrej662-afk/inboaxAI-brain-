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


@dataclass
class TiledPageResult:
    """Vysledok multi-viewport tiled renderovania pre dlhe stranky."""
    url: str
    final_url: str
    html: str
    visible_text: str
    title: str
    scroll_height: int
    viewport_w: int
    viewport_h: int
    segments: list[bytes]  # PNG bytes, kazdy viewport_w x viewport_h
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

    async def render_page_tiled(
        self,
        url: str,
        segment_height: int = 2200,
        overlap_pct: float = 0.22,
    ) -> "TiledPageResult":
        """Renderuj stranku a zachyt viacero viewport-velkych screenshotov pri skrolovani.

        Pocet segmentov je adaptivny podla scrollHeight:
          < 3000 px   -> 1 segment (full page, ako render_page)
          3000-7000   -> 3 segmenty
          7000-12000  -> 5 segmentov
          > 12000     -> 7 segmentov (cap)

        Overlap je 22% (default) — stedry, aby zvladol sticky headery / animacie ktore
        mozu posunut obsah medzi viewportami.

        Kazdy segment je zachyteny v 1280x2200 px (NIE resizovany Anthropicom kedze
        2200 < 2400 px threshold). Sonnet vision vie precitat produktove karty v plnom detaile.

        Defenzivne: pri akejkolvek chybe vrati TiledPageResult s nastavenym `error` a prazdnymi
        segmentami. Caller by mal fallbacknut na render_page().
        """
        if not self._started or self._context is None:
            return TiledPageResult(
                url=url, final_url=url, html="", visible_text="", title="",
                scroll_height=0, viewport_w=0, viewport_h=0, segments=[],
                error="browser_not_started",
            )

        page = None
        try:
            page = await self._context.new_page()
            # Sirsi viewport pre tiled — produkty casto zaberaju 1280
            await page.set_viewport_size({"width": 1280, "height": segment_height})

            await page.goto(url, wait_until="networkidle", timeout=settings.PLAYWRIGHT_TIMEOUT_MS)

            # Robustne render predpoklady — cookie dismiss + lazy load (best-effort, defenzivne)
            try:
                # Auto-dismiss cookie bannerov (multi-language)
                for text in ["Súhlasím", "Prijať všetko", "Accept all", "OK", "Rozumiem", "Pokračovať"]:
                    try:
                        loc = page.get_by_role("button", name=text).first
                        if await loc.count() > 0:
                            await loc.click(timeout=800)
                            await page.wait_for_timeout(300)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            # Vynut lazy load skrolovanim zhora nadol
            try:
                await page.evaluate("""
                    async () => {
                        const max_h = document.documentElement.scrollHeight;
                        const step = 600;
                        for (let y = 0; y < max_h; y += step) {
                            window.scrollTo(0, y);
                            await new Promise(r => setTimeout(r, 100));
                        }
                        window.scrollTo(0, 0);
                        await new Promise(r => setTimeout(r, 300));
                    }
                """)
            except Exception:
                pass

            # Vynut zobrazenie scroll-triggered animacii (AOS, scroll-reveal, atd.)
            try:
                await page.evaluate("""
                    () => {
                        document.querySelectorAll('[data-aos]').forEach(el => {
                            el.classList.add('aos-animate');
                            el.style.opacity = '1';
                            el.style.transform = 'none';
                        });
                        document.querySelectorAll('[data-scroll], .scroll-reveal, .animate-on-scroll').forEach(el => {
                            el.classList.add('is-inview', 'is-visible', 'in-view');
                            el.style.opacity = '1';
                        });
                    }
                """)
            except Exception:
                pass

            # Rozbal collapsibles
            try:
                await page.evaluate("""
                    () => {
                        document.querySelectorAll('details').forEach(d => { d.open = true; });
                        let count = 0;
                        document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                            if (count++ < 50 && el.click) {
                                try { el.click(); } catch(e) {}
                            }
                        });
                    }
                """)
            except Exception:
                pass

            # Pockaj kym sa vsetko ustali
            await page.wait_for_timeout(800)

            html = await page.content()
            title = await page.title()
            visible_text = await page.evaluate("() => document.body.innerText || ''")
            final_url = page.url

            # Ziskaj celkovu scroll height
            try:
                scroll_height = await page.evaluate("() => document.documentElement.scrollHeight")
                scroll_height = int(scroll_height) if scroll_height else segment_height
            except Exception:
                scroll_height = segment_height

            # Adaptivne rozhodni pocet segmentov
            if scroll_height < 3000:
                num_segments = 1
            elif scroll_height < 7000:
                num_segments = 3
            elif scroll_height < 12000:
                num_segments = 5
            else:
                num_segments = 7

            segments: list[bytes] = []

            if num_segments == 1:
                # Kratka stranka: full_page screenshot ako 1 segment
                try:
                    screenshot = await page.screenshot(type="png", full_page=True)
                    if screenshot:
                        segments.append(screenshot)
                except Exception as e:
                    logger.warning("full_page screenshot failed for %s: %s", url, e)
            else:
                # Dlha stranka: rozdel na segmenty s overlapom
                overlap_px = int(segment_height * overlap_pct)
                effective_step = segment_height - overlap_px  # o kolko sa posunieme per segment

                for i in range(num_segments):
                    y_offset = i * effective_step
                    # Posledny segment: zarovnaj na spodok ak je kratsi
                    if i == num_segments - 1:
                        y_offset = max(0, scroll_height - segment_height)

                    try:
                        await page.evaluate(f"() => window.scrollTo(0, {y_offset})")
                        await page.wait_for_timeout(400)  # lazy obrazky sa nacitaju

                        screenshot = await page.screenshot(
                            type="png",
                            clip={"x": 0, "y": 0, "width": 1280, "height": segment_height},
                        )
                        if screenshot:
                            segments.append(screenshot)
                    except Exception as e:
                        logger.warning("tile segment %d failed for %s: %s", i, url, e)
                        continue

            logger.info(
                "render_page_tiled %s: scrollHeight=%d segments=%d (target=%d)",
                url, scroll_height, len(segments), num_segments,
            )
            return TiledPageResult(
                url=url, final_url=final_url, html=html,
                visible_text=visible_text, title=title,
                scroll_height=scroll_height,
                viewport_w=1280, viewport_h=segment_height,
                segments=segments,
            )
        except Exception as e:
            logger.warning("render_page_tiled failed for %s: %s", url, e)
            return TiledPageResult(
                url=url, final_url=url, html="", visible_text="", title="",
                scroll_height=0, viewport_w=0, viewport_h=0, segments=[],
                error=str(e)[:500],
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
