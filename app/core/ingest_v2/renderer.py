"""RendererV2 — robustny headless render pre Universal Ingestion Engine v2 (Phase 2A).

Samostatny od Phase 1A `app/core/browser.py` (ten ostava pre legacy crawler).
RendererV2 ma agresivnejsi pipeline: dismiss cookie bannerov, force lazy-load,
expand accordions / <details>, force scroll-triggered animacii. Zero LLM.

Defensive: ziadna metoda nesmie raisnut von z `render_page` — vsetky chyby sa
mapuju na `RenderResult.render_status in ('timeout','blocked','error')`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

logger = logging.getLogger(__name__)

# Moderny Chrome UA — drzime aktualny, aby weby neservirovali "upgrade your browser".
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# JS spusteny pri kazdom novom dokumente — predvyplnime cookie-consent kluce,
# aby vacsina bannerov vobec nevyskocila.
_INIT_SCRIPT = """
try {
  window.localStorage.setItem('cookie_consent', 'accepted');
  window.localStorage.setItem('gdpr_consent', 'true');
  window.localStorage.setItem('cookieConsent', 'true');
  window.localStorage.setItem('cookie-consent', 'true');
  window.localStorage.setItem('CookieConsent', 'true');
} catch (e) {}
"""


@dataclass
class RenderResult:
    """Vysledok jedneho renderu. `render_status`: 'success' | 'timeout' | 'blocked' | 'error'."""

    url: str
    final_url: str
    http_status: int | None
    render_status: str
    render_ms: int
    html: str
    visible_text: str
    title: str
    dom_size: int
    text_length: int
    screenshot_png: bytes | None
    error_message: str | None = None


class RendererV2:
    """Robustny Playwright Chromium renderer (headless)."""

    def __init__(
        self,
        viewport_w: int = 1280,
        viewport_h: int = 900,
        timeout_ms: int = 30000,
        locale: str = "sk-SK",
        user_agent: str | None = None,
    ) -> None:
        self.viewport_w = viewport_w
        self.viewport_h = viewport_h
        self.timeout_ms = timeout_ms
        self.locale = locale
        self.user_agent = user_agent or _DEFAULT_UA
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Spustí Playwright + Chromium + perzistentny context."""
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
            viewport={"width": self.viewport_w, "height": self.viewport_h},
            user_agent=self.user_agent,
            locale=self.locale,
            ignore_https_errors=True,
            extra_http_headers={"Accept-Language": "sk-SK,sk;q=0.9,en;q=0.5"},
        )
        try:
            await self._context.add_init_script(_INIT_SCRIPT)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("add_init_script failed: %s", e)

    async def close(self) -> None:
        """Idempotentne zatvorenie — kazdy krok v try/except."""
        try:
            if self._context is not None:
                await self._context.close()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("context.close failed: %s", e)
        finally:
            self._context = None
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("browser.close failed: %s", e)
        finally:
            self._browser = None
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("playwright.stop failed: %s", e)
        finally:
            self._playwright = None

    # ------------------------------------------------------------------ render
    async def render_page(
        self,
        url: str,
        take_screenshot: bool = False,
        screenshot_full_page: bool = False,
    ) -> RenderResult:
        """Robust render pipeline. Nikdy neraisne — chyby idu do `render_status`."""
        if self._context is None:
            return RenderResult(
                url=url, final_url=url, http_status=None, render_status="error",
                render_ms=0, html="", visible_text="", title="", dom_size=0,
                text_length=0, screenshot_png=None,
                error_message="renderer not started (call start() first)",
            )

        page = None
        response = None
        start_ts = time.monotonic()
        try:
            page = await self._context.new_page()
            response = await page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            http_status = response.status if response else None

            # 1. Dismiss cookie banner (multi-language, defensive).
            await self._dismiss_cookies(page)
            # 2. Force lazy-load obrazkov cez scroll cyklus.
            await self._force_lazy_load(page)
            # 3. Expand accordions, <details>, aria-expanded.
            await self._expand_collapsibles(page)
            # 4. Force-show scroll-triggered animacii (AOS a pod.).
            await self._force_animations(page)
            # 5. Finalny settle wait.
            await page.wait_for_timeout(800)

            html = await page.content()
            try:
                title = await page.title()
            except Exception:
                title = ""
            try:
                visible_text = await page.evaluate("() => document.body.innerText || ''")
            except Exception:
                visible_text = ""
            final_url = page.url

            screenshot = None
            if take_screenshot:
                try:
                    screenshot = await page.screenshot(type="png", full_page=screenshot_full_page)
                except Exception as e:
                    logger.warning("screenshot failed for %s: %s", url, e)

            render_ms = int((time.monotonic() - start_ts) * 1000)
            return RenderResult(
                url=url,
                final_url=final_url,
                http_status=http_status,
                render_status="success",
                render_ms=render_ms,
                html=html or "",
                visible_text=visible_text or "",
                title=title or "",
                dom_size=len(html or ""),
                text_length=len(visible_text or ""),
                screenshot_png=screenshot,
            )

        except PlaywrightTimeoutError as e:
            return RenderResult(
                url=url, final_url=url, http_status=None, render_status="timeout",
                render_ms=int((time.monotonic() - start_ts) * 1000),
                html="", visible_text="", title="", dom_size=0, text_length=0,
                screenshot_png=None, error_message=str(e)[:500],
            )
        except Exception as e:
            msg = str(e).lower()
            status = "blocked" if ("blocked" in msg or "403" in str(e) or "err_blocked" in msg) else "error"
            return RenderResult(
                url=url, final_url=url, http_status=None, render_status=status,
                render_ms=int((time.monotonic() - start_ts) * 1000),
                html="", visible_text="", title="", dom_size=0, text_length=0,
                screenshot_png=None, error_message=str(e)[:500],
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------ helpers
    async def _dismiss_cookies(self, page) -> None:
        """Skusi bezne cookie-banner buttony (selektory + text). Defensive, max 1 klik."""
        selectors = [
            "#onetrust-accept-btn-handler",
            "#cookieconsent-accept",
            "#cookie-accept",
            ".cookie-banner button",
            ".cc-allow",
            ".cc-accept",
            ".cc-btn-accept",
            "[data-cookieconsent='accept']",
            "[aria-label*='cookie' i] button",
            "button[id*='accept' i]",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=800)
                    await page.wait_for_timeout(300)
                    return
            except Exception:
                continue

        button_texts = [
            "Súhlasím", "Prijať všetko", "Prijať", "Rozumiem", "OK", "Pokračovať",
            "Accept all", "Accept", "I agree", "Allow all", "Got it",
            "Souhlasím", "Akceptovat", "Rozumím",  # CZ
        ]
        for text in button_texts:
            try:
                loc = page.get_by_role("button", name=text).first
                if await loc.count() > 0:
                    await loc.click(timeout=800)
                    await page.wait_for_timeout(300)
                    return
            except Exception:
                try:
                    loc = page.locator(f"button:has-text('{text}')").first
                    if await loc.count() > 0:
                        await loc.click(timeout=800)
                        await page.wait_for_timeout(300)
                        return
                except Exception:
                    continue

    async def _force_lazy_load(self, page) -> None:
        """Scroll zhora nadol → spat hore, aby sa nacitali lazy obrazky."""
        try:
            await page.evaluate(
                """
                async () => {
                    const max_h = document.documentElement.scrollHeight;
                    const step = 600;
                    for (let y = 0; y < max_h; y += step) {
                        window.scrollTo(0, y);
                        await new Promise(r => setTimeout(r, 120));
                    }
                    window.scrollTo(0, 0);
                    await new Promise(r => setTimeout(r, 200));
                }
                """
            )
        except Exception as e:
            logger.debug("force_lazy_load failed: %s", e)

    async def _expand_collapsibles(self, page) -> None:
        """Otvori vsetky <details>, klikne aria-expanded='false' (limit 50), bezne akordeony."""
        try:
            await page.evaluate(
                """
                () => {
                    document.querySelectorAll('details').forEach(d => { try { d.open = true; } catch(e) {} });
                    let count = 0;
                    document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                        if (count++ < 50 && el.click) {
                            try { el.click(); } catch(e) {}
                        }
                    });
                    ['.accordion-button.collapsed', '.faq-question.collapsed', '.accordion__button',
                     '.accordion-toggle.collapsed'].forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => {
                            try { el.click(); } catch(e) {}
                        });
                    });
                }
                """
            )
        except Exception as e:
            logger.debug("expand_collapsibles failed: %s", e)

    async def _force_animations(self, page) -> None:
        """Force-show scroll-triggered animacie (AOS, scroll-reveal, animate-on-scroll, ...)."""
        try:
            await page.evaluate(
                """
                () => {
                    document.querySelectorAll('[data-aos]').forEach(el => {
                        try {
                            el.classList.add('aos-animate');
                            el.style.opacity = '1';
                            el.style.transform = 'none';
                        } catch(e) {}
                    });
                    document.querySelectorAll('[data-scroll], .scroll-reveal, .animate-on-scroll, .reveal, .wow').forEach(el => {
                        try {
                            el.classList.add('is-inview', 'is-visible', 'in-view', 'active', 'animated');
                            el.style.opacity = '1';
                            el.style.transform = 'none';
                        } catch(e) {}
                    });
                }
                """
            )
        except Exception as e:
            logger.debug("force_animations failed: %s", e)
