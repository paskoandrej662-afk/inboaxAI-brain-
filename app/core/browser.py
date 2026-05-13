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

            # === UI EXPANSION LAYER ===
            try:
                expansion_result = await self._apply_ui_expansion(page, url)
                logger.debug("ui_expansion result for %s: %s", url, expansion_result)
            except Exception as e:
                logger.warning("ui_expansion failed for %s: %s — pokracujem", url, e)

            # Force-show scroll-triggered animations (AOS, scroll-reveal, atd.)
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

            # Pockaj kym sa vsetko ustali
            await page.wait_for_timeout(800)

            # Re-measure scrollHeight AFTER expansion (carousels can change height)
            try:
                scroll_height = await page.evaluate("() => document.documentElement.scrollHeight")
                scroll_height = int(scroll_height) if scroll_height else segment_height
            except Exception:
                scroll_height = segment_height
            # === END UI EXPANSION LAYER ===

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

    # ==================================================================
    # UI EXPANSION LAYER — fixed execution chain + complexity safety gate
    # ==================================================================

    async def _detect_ui_patterns(self, page) -> dict:
        """Detect UI patterns on the page. Returns dict with required keys.
        Defensive: returns safe defaults on error.
        """
        try:
            return await page.evaluate("""
                () => {
                    // Native scroll carousels (CSS overflow-x)
                    const native_scroll = Array.from(document.querySelectorAll('*'))
                        .filter(el => {
                            const cs = window.getComputedStyle(el);
                            return (cs.overflowX === 'scroll' || cs.overflowX === 'auto')
                                && el.scrollWidth > el.clientWidth
                                && el.clientWidth > 200;
                        });

                    // JS carousels (Swiper/Slick/Owl)
                    const swiper_count = document.querySelectorAll('.swiper, .swiper-container').length;
                    const slick_count = document.querySelectorAll('.slick-slider, .slick-list').length;
                    const owl_count = document.querySelectorAll('.owl-carousel').length;
                    const has_js_carousel = swiper_count + slick_count + owl_count > 0;

                    // Elementor Flip Box (front+back layer widget; back hidden via CSS 3D rotation)
                    const flip_box_count = document.querySelectorAll('.elementor-flip-box').length;
                    const has_flip_box = flip_box_count > 0;

                    // Swiper instance accessible via DOM property?
                    let swiper_api_available = false;
                    try {
                        const containers = document.querySelectorAll('.swiper, .swiper-container');
                        for (const c of containers) {
                            if (c.swiper && typeof c.swiper.slideTo === 'function') {
                                swiper_api_available = true;
                                break;
                            }
                        }
                    } catch(e) {}

                    // Accordions (strict)
                    const details_count = document.querySelectorAll('details').length;
                    const role_button_collapsed = document.querySelectorAll('[role="button"][aria-expanded="false"]').length;

                    // Lazy load
                    const data_src_count = document.querySelectorAll('img[data-src], img[data-lazy-src], img[data-original], img[loading="lazy"]').length;

                    // Complexity score — uses SPA/framework signals + canvas.
                    // NOTE: swiper_count is NOT penalized — e-commerce sites have many carousels
                    // and are our PRIMARY use-case for expansion (not exclusion).
                    let complexity = 0.0;
                    const canvas_count = document.querySelectorAll('canvas').length;
                    if (canvas_count > 0) complexity += 0.4 * Math.min(1.0, canvas_count / 3);
                    if (document.querySelector('[data-reactroot], #__next, #root[data-server-rendered]')) complexity += 0.25;
                    if (document.querySelector('[ng-app], [ng-version]')) complexity += 0.25;
                    if (document.querySelector('[data-v-app]')) complexity += 0.15;

                    return {
                        carousel_native: native_scroll.length > 0,
                        carousel_js: has_js_carousel,
                        swiper_api_available: swiper_api_available,
                        accordion: details_count + role_button_collapsed > 0,
                        lazy_load: data_src_count > 0,
                        flip_box: has_flip_box,
                        flip_box_count: flip_box_count,
                        complexity_score: Math.min(1.0, complexity),
                    };
                }
            """)
        except Exception as e:
            logger.debug("_detect_ui_patterns error: %s", e)
            return {
                'carousel_native': False, 'carousel_js': False,
                'swiper_api_available': False,
                'accordion': False, 'lazy_load': False,
                'flip_box': False,
                'complexity_score': 0.5,
            }

    async def _trigger_lazy_load(self, page) -> None:
        """Simple: scroll to bottom, wait, scroll to top, wait. No loops."""
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
        except Exception as e:
            logger.debug("_trigger_lazy_load error: %s", e)

    async def _expand_accordions_strict(self, page) -> int:
        """Strict accordion expansion. Only:
        - <details> elements → .open = true
        - [role='button'][aria-expanded='false'] → click (skipping nav/menu/modal)
        """
        try:
            count = await page.evaluate("""
                () => {
                    let count = 0;
                    document.querySelectorAll('details').forEach(d => {
                        if (!d.open) { d.open = true; count++; }
                    });
                    let clicked = 0;
                    document.querySelectorAll('[role="button"][aria-expanded="false"]').forEach(el => {
                        if (clicked >= 20) return;
                        const cls = (el.className || '').toLowerCase();
                        if (cls.includes('nav') || cls.includes('menu') || cls.includes('modal')
                            || cls.includes('dialog') || cls.includes('dropdown')) return;
                        try { el.click(); clicked++; count++; } catch(e) {}
                    });
                    return count;
                }
            """)
            if count and count > 0:
                await page.wait_for_timeout(300)
            return int(count) if count else 0
        except Exception as e:
            logger.debug("_expand_accordions_strict error: %s", e)
            return 0

    async def _try_swiper_js_api(self, page) -> int:
        """Cycle through all slides using Swiper's own JS API. Clean approach.
        Returns number of Swiper instances cycled. 0 if API not available or failed.
        """
        try:
            return await page.evaluate("""
                async () => {
                    let cycled = 0;
                    const containers = document.querySelectorAll('.swiper, .swiper-container');
                    for (const c of containers) {
                        const inst = c.swiper;
                        if (inst && typeof inst.slideTo === 'function' && inst.slides && inst.slides.length > 1) {
                            try {
                                const total = inst.slides.length;
                                for (let i = 0; i < total; i++) {
                                    inst.slideTo(i, 0);
                                    await new Promise(r => setTimeout(r, 150));
                                }
                                inst.slideTo(0, 0);
                                cycled++;
                            } catch(e) {}
                        }
                    }
                    return cycled;
                }
            """) or 0
        except Exception as e:
            logger.debug("_try_swiper_js_api error: %s", e)
            return 0

    async def _cycle_native_scroll(self, page) -> int:
        """For CSS overflow-x carousels, scrollLeft 0→33%→66%→100%→0. No DOM mutation."""
        try:
            return await page.evaluate("""
                async () => {
                    const carousels = Array.from(document.querySelectorAll('*'))
                        .filter(el => {
                            const cs = window.getComputedStyle(el);
                            return (cs.overflowX === 'scroll' || cs.overflowX === 'auto')
                                && el.scrollWidth > el.clientWidth
                                && el.clientWidth > 200;
                        });
                    for (const c of carousels) {
                        const max = c.scrollWidth - c.clientWidth;
                        for (const s of [0, max * 0.33, max * 0.66, max]) {
                            c.scrollLeft = s;
                            await new Promise(r => setTimeout(r, 200));
                        }
                        c.scrollLeft = 0;
                    }
                    return carousels.length;
                }
            """) or 0
        except Exception as e:
            logger.debug("_cycle_native_scroll error: %s", e)
            return 0

    async def _dom_fallback_expand_carousels(self, page) -> int:
        """LAST-RESORT carousel expansion via DOM mutation. Called when:
        - carousel detected (JS or native)
        - prior steps (Swiper API, native scroll) did NOT cycle anything
        - allow_dom_mutation == True (set by safety gate)

        Targets ONLY known carousel slide classes: .swiper-slide, .slick-slide, .owl-item.
        """
        try:
            return await page.evaluate("""
                () => {
                    let modified = 0;

                    // Swiper
                    document.querySelectorAll('.swiper-wrapper').forEach(w => {
                        w.style.transform = 'none';
                        w.style.flexWrap = 'wrap';
                        w.style.height = 'auto';
                    });
                    document.querySelectorAll('.swiper-slide').forEach(s => {
                        s.style.opacity = '1';
                        s.style.visibility = 'visible';
                        s.style.flexShrink = '0';
                        s.style.minWidth = '200px';
                        modified++;
                    });

                    // Slick
                    document.querySelectorAll('.slick-track').forEach(t => {
                        t.style.transform = 'none';
                        t.style.width = 'auto';
                    });
                    document.querySelectorAll('.slick-list').forEach(l => {
                        l.style.overflow = 'visible';
                        l.style.height = 'auto';
                    });
                    document.querySelectorAll('.slick-slide').forEach(s => {
                        s.style.opacity = '1';
                        s.style.visibility = 'visible';
                        s.style.display = 'inline-block';
                        s.classList.remove('slick-cloned');
                        modified++;
                    });

                    // Owl
                    document.querySelectorAll('.owl-stage').forEach(s => {
                        s.style.transform = 'none';
                        s.style.width = 'auto';
                    });
                    document.querySelectorAll('.owl-item').forEach(i => {
                        i.style.opacity = '1';
                        i.style.visibility = 'visible';
                        i.style.minWidth = '200px';
                        i.style.display = 'inline-block';
                        modified++;
                    });

                    return modified;
                }
            """) or 0
        except Exception as e:
            logger.debug("_dom_fallback_expand_carousels error: %s", e)
            return 0

    async def _expand_flip_boxes(self, page) -> int:
        """For Elementor Flip Box widgets: force-show the back layer (where details + prices live).

        Flip Box has 2 layers (front=image+title, back=description+price).
        Default: only front visible. Back is rotated 180deg in CSS 3D.

        Strategy: stack both layers visibly OR hide front + show back.

        Returns number of flip boxes modified.
        """
        try:
            return await page.evaluate("""
                () => {
                    let modified = 0;

                    // Strategy: force the .elementor-flip-box parent to display both layers
                    // by stacking them vertically (flex column) instead of CSS 3D rotation
                    document.querySelectorAll('.elementor-flip-box').forEach(box => {
                        box.style.transformStyle = 'flat';
                        box.style.perspective = 'none';
                        modified++;
                    });

                    // Front layer: keep visible (it has the image)
                    document.querySelectorAll('.elementor-flip-box__layer--front').forEach(front => {
                        front.style.transform = 'none';
                        front.style.backfaceVisibility = 'visible';
                        front.style.position = 'relative';
                        front.style.opacity = '1';
                    });

                    // Back layer: force-show below the front (where prices and details live)
                    document.querySelectorAll('.elementor-flip-box__layer--back').forEach(back => {
                        back.style.transform = 'none';
                        back.style.backfaceVisibility = 'visible';
                        back.style.position = 'relative';  // not absolute
                        back.style.opacity = '1';
                        back.style.visibility = 'visible';
                        back.style.height = 'auto';
                        back.style.minHeight = '150px';
                        back.style.display = 'block';
                    });

                    // Inner content of back layer: force visibility
                    document.querySelectorAll('.elementor-flip-box__layer__inner').forEach(inner => {
                        inner.style.opacity = '1';
                        inner.style.visibility = 'visible';
                    });

                    document.querySelectorAll('.elementor-flip-box__layer__overlay').forEach(overlay => {
                        overlay.style.opacity = '1';
                        overlay.style.visibility = 'visible';
                    });

                    document.querySelectorAll('.elementor-flip-box__layer__description').forEach(desc => {
                        desc.style.opacity = '1';
                        desc.style.visibility = 'visible';
                        desc.style.display = 'block';
                    });

                    return modified;
                }
            """) or 0
        except Exception as e:
            logger.debug("_expand_flip_boxes error: %s", e)
            return 0

    async def _apply_ui_expansion(self, page, url: str) -> dict:
        """Main UI expansion. FIXED execution chain. Complexity = safety gate only.

        Pipeline (always same order):
          1. Lazy load (always safe)
          2. Accordion (skip if HIGH complexity)
          3. Carousel chain:
             a) Swiper JS API
             b) Native scroll (CSS overflow-x)
             c) DOM fallback (only if prior 2 didn't cycle AND complexity < 0.3)

        DOM fallback triggers when:
          - Any carousel detected (JS or native)
          - Prior carousel steps did NOT cycle anything (i.e., 0 cycled)
          - complexity < 0.3 (allow_dom_mutation)
        """
        patterns = await self._detect_ui_patterns(page)
        complexity = patterns.get('complexity_score', 0.0)

        # Safety gates
        allow_dom_mutation = complexity < 0.3
        allow_carousel_handling = complexity < 0.7
        allow_accordion = complexity < 0.7

        logger.info(
            "ui_expansion %s: complexity=%.2f native=%s js=%s api=%s accordion=%s lazy=%s allow_dom=%s",
            url, complexity,
            patterns.get('carousel_native'), patterns.get('carousel_js'),
            patterns.get('swiper_api_available'), patterns.get('accordion'),
            patterns.get('lazy_load'), allow_dom_mutation,
        )

        applied = {
            'complexity': complexity,
            'allow_dom_mutation': allow_dom_mutation,
            'lazy_triggered': False,
            'accordions_expanded': 0,
            'flip_boxes_expanded': 0,
            'swiper_api_cycled': 0,
            'native_scroll_cycled': 0,
            'dom_fallback_modified': 0,
        }

        # Step 1: ALWAYS safe — lazy load
        if patterns.get('lazy_load'):
            await self._trigger_lazy_load(page)
            applied['lazy_triggered'] = True

        # Step 2: Accordion (if complexity allows)
        if allow_accordion and patterns.get('accordion'):
            applied['accordions_expanded'] = await self._expand_accordions_strict(page)

        # Step 2.5: Elementor Flip Box (always safe, just CSS — counts as accordion-like)
        if allow_accordion and patterns.get('flip_box'):
            applied['flip_boxes_expanded'] = await self._expand_flip_boxes(page)

        # Step 3: Carousel handling — FIXED chain
        if allow_carousel_handling:
            # 3a. Swiper JS API
            if patterns.get('carousel_js') and patterns.get('swiper_api_available'):
                applied['swiper_api_cycled'] = await self._try_swiper_js_api(page)

            # 3b. Native scroll carousels (always safe, no DOM mutation)
            if patterns.get('carousel_native'):
                applied['native_scroll_cycled'] = await self._cycle_native_scroll(page)

            # 3c. DOM fallback — only if (a) and (b) didn't cycle AND DOM mutation allowed
            # Trigger when ANY carousel is detected (JS or native) and no prior step worked
            any_carousel_detected = patterns.get('carousel_js') or patterns.get('carousel_native')
            no_successful_cycle = (
                applied['swiper_api_cycled'] == 0
                and applied['native_scroll_cycled'] == 0
            )
            if any_carousel_detected and no_successful_cycle and allow_dom_mutation:
                applied['dom_fallback_modified'] = await self._dom_fallback_expand_carousels(page)

        # Final settle
        await page.wait_for_timeout(600)

        return applied
