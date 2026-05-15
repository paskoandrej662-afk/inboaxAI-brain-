"""HDSCrawler — najde podstranky webu pre HDS-v3 pipeline.

Strategy:
  1. Skus sitemap.xml (najlepsi pripad — vacsina serioznych webov ho ma)
  2. Fallback: render homepage v Playwright + extract <a href> links
  3. Filter (same-domain, no admin/binary), prioritize (4 tier), dedupe
  4. Limit MAX_PAGES (30) per web

Defensive: kazda metoda catch errors, log warning, vrat empty result. CrawlResult
ma vzdy zmysluplny `success` flag a (ak je success=False) `error` retazec.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from app.core.browser import BrowserPool
from app.core.extractors.hds_v3.image_extractor import ImageExtractor
from app.core.extractors.hds_v3.types import (
    CrawlResult,
    DiscoveredPage,
    PageCrawlResult,
    PagePriority,
)

logger = logging.getLogger(__name__)


# Tracking query params ktore zahadzujeme pri normalizacii.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "yclid", "igshid",
    "mc_cid", "mc_eid", "_ga", "_gl", "ref", "ref_src",
}


class HDSCrawler:
    """Crawler ktory najde podstranky webu pre HDS-v3 pipeline.

    Verejne API: `await crawler.discover(base_url) -> CrawlResult`.
    """

    MAX_PAGES = 30
    SITEMAP_TIMEOUT_SEC = 10
    RENDER_TIMEOUT_SEC = 30

    # Tier 0 MUST-HAVE patterns — vzdy zahrnute, neorezava sa cap-om.
    TIER_0_PATTERNS = ["/kontakt", "/contact", "/contacts"]

    # Tier 1 URL patterns (case-insensitive substring na ceste).
    TIER_1_PATTERNS = [
        "/produkty", "/products",
        "/sluzby", "/services",
        "/cennik", "/cennik-sluzieb", "/pricing", "/cenik",
    ]

    # Tier 2.
    TIER_2_PATTERNS = [
        "/o-nas", "/about", "/about-us",
        "/faq", "/najcastejsie-otazky", "/casto-kladene-otazky",
    ]

    # Tier 3.
    TIER_3_PATTERNS = [
        "/galeria", "/gallery",
        "/projekty", "/projects",
        "/referencie", "/references",
        "/blog", "/novinky", "/news",
    ]

    # Career/recruitment URL patterns — typicky job postings ktore Gemini nepotrebuje.
    RECRUITMENT_PATTERNS = [
        "/praca", "/kariera", "/career", "/jobs", "/job",
        "/pracuj-", "/zamestnan-", "/zamestnanost",
        "/strojnik", "/elektrikar", "/zvarac", "/lezec",
        "/vodic", "/montaze-", "/pomocny-pracovnik",
        "/nevies-kam", "/oferta-pracy",
    ]

    # Marketing / junk patterns (firemne akcie, vianoce, vernostne karty).
    MARKETING_PATTERNS = [
        "/vianocny-", "/vianoce-",
        "/firemny-den", "/firemne-",
        "/ocenenie-", "/cena-",
        "/bonusova-karta", "/vernostny-",
        "/akcia", "/zlava",
    ]

    # Excluded substrings — ak URL path/extension obsahuje, vyhodit.
    EXCLUDED_PATTERNS = [
        "/wp-admin", "/admin",
        "/login", "/prihlasit", "/registracia",
        "/cart", "/kosik",
        "/checkout", "/objednavka",
        "/account", "/ucet", "/profil",
        "/wp-content", "/wp-includes",
        "/feed", "/rss", "/atom",
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".zip", ".doc", ".docx", ".xls", ".xlsx",
        ".mp4", ".mp3", ".avi",
    ]

    # Sitemap kandidatne cesty (skusame v poradi).
    _SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap1.xml"]

    # XML namespace pre <urlset>/<sitemapindex> (vacsina sitemap-ov pouziva tento).
    _SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

    # --------------------------------------------------------------- public API
    async def discover(self, base_url: str) -> CrawlResult:
        """Hlavny vstupny bod. Vrat CrawlResult so zoznamom DiscoveredPage."""
        start = time.monotonic()
        normalized_base = self._normalize_base_url(base_url)
        if not normalized_base:
            return CrawlResult(
                success=False,
                base_url=base_url,
                error="invalid_base_url",
                duration_sec=time.monotonic() - start,
            )

        # 1) Sitemap-first strategia.
        sitemap_urls: list[str] = []
        try:
            sitemap_urls = await self._try_sitemap(normalized_base)
        except Exception as e:  # extra paranoia (try_sitemap je uz defensive)
            logger.warning("HDSCrawler: sitemap probe raised: %s", e)
            sitemap_urls = []

        sitemap_found = bool(sitemap_urls)

        # Vzdy zaradime homepage ako seed.
        seeds: list[tuple[str, str]] = [(normalized_base, "manual_seed")]

        if sitemap_found:
            for u in sitemap_urls:
                seeds.append((u, "sitemap"))
        else:
            # 2) Fallback: homepage links.
            try:
                home_links = await self._crawl_homepage_links(normalized_base)
            except Exception as e:
                logger.warning("HDSCrawler: homepage fallback raised: %s", e)
                home_links = []
            for u in home_links:
                seeds.append((u, "homepage_link"))

        # 3) Filter + dedupe (zachova via labels).
        filtered = self._filter_urls_with_via(seeds, normalized_base)

        # 4) Prioritize + per-tier caps.
        pages = self._prioritize(filtered, normalized_base)

        # 5) Manual /kontakt probe — ak chyba (typicky fallback z homepage links).
        has_kontakt = any("/kontakt" in p.url.lower() or "/contact" in p.url.lower() for p in pages)
        if not has_kontakt:
            probed = await self._probe_kontakt(normalized_base)
            if probed:
                pages.insert(0, probed)  # TIER_0 ide na zaciatok

        # 6) Final cap MAX_PAGES. TIER_0 by mal byt na zaciatku — orezavame iba chvost.
        if len(pages) > self.MAX_PAGES:
            pages = pages[: self.MAX_PAGES]

        return CrawlResult(
            success=True,
            base_url=normalized_base,
            pages=pages,
            total_discovered=len(pages),
            sitemap_found=sitemap_found,
            duration_sec=time.monotonic() - start,
        )

    # --------------------------------------------------------------- media streams
    async def crawl_media_streams(
        self, pages: list[DiscoveredPage]
    ) -> list[PageCrawlResult]:
        """Render each page via BrowserPool and extract its media stream.

        Returns one `PageCrawlResult` per input page (in input order). On
        per-page failure we still emit a result with empty `media_stream`
        and the error set, so callers can correlate by URL.
        """
        results: list[PageCrawlResult] = []
        if not pages:
            return results

        pool = BrowserPool()
        try:
            await pool.start()
        except Exception as e:  # noqa: BLE001
            logger.warning("crawl_media_streams: pool.start failed: %s", e)
            try:
                await pool.close()
            except Exception:  # noqa: BLE001
                pass
            return [PageCrawlResult(url=p.url, error="browser_pool_failed") for p in pages]

        extractor = ImageExtractor()
        try:
            for page in pages:
                try:
                    rendered = await pool.render_page(page.url, take_screenshot=False)
                    if rendered.error or not rendered.html:
                        results.append(
                            PageCrawlResult(
                                url=page.url,
                                error=rendered.error or "empty_html",
                            )
                        )
                        continue
                    stream = extractor.extract_stream_from_html(
                        rendered.html, base_url=rendered.final_url or page.url
                    )
                    results.append(PageCrawlResult(url=page.url, media_stream=stream))
                except Exception as e:  # noqa: BLE001
                    logger.warning("crawl_media_streams: page %s failed: %s", page.url, e)
                    results.append(PageCrawlResult(url=page.url, error=str(e)[:200]))
        finally:
            try:
                await pool.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("crawl_media_streams: pool.close warn: %s", e)
        return results

    # --------------------------------------------------------------- manual probe
    async def _probe_kontakt(self, base_url: str) -> Optional[DiscoveredPage]:
        """Skus HEAD na {base_url}/kontakt; pri 200 OK vrat DiscoveredPage(TIER_0).

        /kontakt je kriticky pre Gemini extraction (telefon, adresa, email),
        ale ked sitemap zlyha a homepage navigacia ho neukazuje, padne nam zo zoznamu.
        Defensive: na chybu len log warning + vrat None.
        """
        kontakt_url = base_url.rstrip("/") + "/kontakt"
        try:
            async with httpx.AsyncClient(
                timeout=5,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (HDS-v3 Crawler)"},
            ) as client:
                # HEAD niektore servery odmietaju (405) — fallback GET ale len status check
                resp = await client.head(kontakt_url)
                if resp.status_code in (405, 501):
                    resp = await client.get(kontakt_url)
                if 200 <= resp.status_code < 300:
                    return DiscoveredPage(
                        url=str(resp.url) if resp.url else kontakt_url,
                        priority=PagePriority.TIER_0_ESSENTIAL,
                        discovered_via="manual_seed",
                    )
        except Exception as e:
            logger.warning("HDSCrawler: manual /kontakt probe failed: %s", e)
        return None

    # --------------------------------------------------------------- sitemap
    async def _try_sitemap(self, base_url: str) -> list[str]:
        """Skus standardne sitemap lokacie. Vrati zoznam URL z <loc> tagov.

        Podpora pre nested sitemap_index (rekurzia 1 level — sitemap_index.xml
        odkazuje na sub-sitemapy, tych obsah scrapneme).
        """
        urls: list[str] = []
        try:
            async with httpx.AsyncClient(
                timeout=self.SITEMAP_TIMEOUT_SEC,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (HDS-v3 Crawler)"},
            ) as client:
                for path in self._SITEMAP_PATHS:
                    sitemap_url = urljoin(base_url, path)
                    try:
                        resp = await client.get(sitemap_url)
                    except Exception as e:
                        logger.debug("sitemap probe %s failed: %s", sitemap_url, e)
                        continue
                    if resp.status_code != 200 or not resp.text:
                        continue

                    parsed_urls, sub_sitemaps = self._parse_sitemap_xml(resp.text)
                    urls.extend(parsed_urls)

                    # Rekurzia jeden level pre sitemap_index.
                    for sub in sub_sitemaps[:10]:  # cap na 10 sub-sitemap
                        try:
                            sub_resp = await client.get(sub)
                        except Exception as e:
                            logger.debug("sub-sitemap %s failed: %s", sub, e)
                            continue
                        if sub_resp.status_code != 200 or not sub_resp.text:
                            continue
                        sub_urls, _ = self._parse_sitemap_xml(sub_resp.text)
                        urls.extend(sub_urls)

                    if urls:
                        return urls  # prvy uspesny sitemap staci
        except Exception as e:
            logger.warning("HDSCrawler._try_sitemap fatal: %s", e)
            return []
        return urls

    def _parse_sitemap_xml(self, xml_text: str) -> tuple[list[str], list[str]]:
        """Spracuj sitemap XML. Vrat (urls, sub_sitemap_urls).

        Podporuje:
          - <urlset>/<url>/<loc> — listing podstranok
          - <sitemapindex>/<sitemap>/<loc> — nested sitemap-y
        Defensive: pri malformed XML vrati ([], []).
        """
        urls: list[str] = []
        sub_sitemaps: list[str] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.debug("sitemap parse error: %s", e)
            return urls, sub_sitemaps

        # Tag s namespace alebo bez (niektore sitemapy ho omitnu).
        tag = root.tag.lower()
        ns = self._SITEMAP_NS

        # Skuska s namespace, fallback bez.
        url_elems = root.findall(f"{ns}url") or root.findall("url")
        sitemap_elems = root.findall(f"{ns}sitemap") or root.findall("sitemap")

        for u in url_elems:
            loc = u.find(f"{ns}loc")
            if loc is None:
                loc = u.find("loc")
            if loc is not None and loc.text:
                urls.append(loc.text.strip())

        for s in sitemap_elems:
            loc = s.find(f"{ns}loc")
            if loc is None:
                loc = s.find("loc")
            if loc is not None and loc.text:
                sub_sitemaps.append(loc.text.strip())

        # Ak je root sitemapindex, tagy `url` budu prazdne — to je OK,
        # caller spravi rekurziu cez sub_sitemaps.
        if not urls and not sub_sitemaps and "sitemapindex" not in tag and "urlset" not in tag:
            # Fallback regex pre extra-malformed sitemapy.
            urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, re.IGNORECASE)

        return urls, sub_sitemaps

    # --------------------------------------------------------------- homepage
    async def _crawl_homepage_links(self, base_url: str) -> list[str]:
        """Render homepage v Playwright + extract <a href> links.

        Pouziva existujuci BrowserPool (browser.py). Defensive: pri chybe vrat [].
        """
        pool = BrowserPool()
        try:
            await pool.start()
        except Exception as e:
            logger.warning("HDSCrawler: BrowserPool.start failed: %s", e)
            try:
                await pool.close()
            except Exception:
                pass
            return []

        try:
            links = await pool.discover_links(base_url)
            return [h for h in links if isinstance(h, str) and h.startswith("http")]
        except Exception as e:
            logger.warning("HDSCrawler: discover_links failed: %s", e)
            return []
        finally:
            try:
                await pool.close()
            except Exception as e:
                logger.debug("HDSCrawler: pool.close warn: %s", e)

    # --------------------------------------------------------------- filtering
    def _normalize_base_url(self, raw: str) -> Optional[str]:
        """Normalize base URL: zaisti scheme, strip fragment, ponechaj trailing slash."""
        if not raw or not isinstance(raw, str):
            return None
        raw = raw.strip()
        if not raw:
            return None
        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        try:
            parsed = urlparse(raw)
        except Exception:
            return None
        if not parsed.netloc:
            return None
        # Lowercase host, no fragment, no query.
        netloc = parsed.netloc.lower()
        # Zachovaj trailing slash na ceste pre base.
        path = parsed.path or "/"
        return urlunparse((parsed.scheme, netloc, path, "", "", ""))

    def _normalize_url(self, url: str, base_url: str) -> Optional[str]:
        """Filter rules pre jednotlivu URL.

        - Same domain only (registrable host musi sediet s base)
        - Strip fragment
        - Strip tracking query params; zachovaj non-tracking query
        - Normalize: lowercase host, remove trailing slash (okrem root '/')
        - Vrati None ak URL je excluded / external / invalid.
        """
        if not url or not isinstance(url, str):
            return None
        try:
            parsed = urlparse(url)
        except Exception:
            return None

        if parsed.scheme not in ("http", "https"):
            return None
        if not parsed.netloc:
            return None

        try:
            base_parsed = urlparse(base_url)
        except Exception:
            return None

        # Same-domain check (case-insensitive, ignore www. prefix).
        host = parsed.netloc.lower()
        base_host = base_parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if base_host.startswith("www."):
            base_host = base_host[4:]
        if host != base_host:
            return None

        path_lower = (parsed.path or "/").lower()

        # Excluded patterns (admin/binary/auth).
        for pat in self.EXCLUDED_PATTERNS:
            if pat in path_lower:
                return None

        # Recruitment / career inzeraty.
        for pat in self.RECRUITMENT_PATTERNS:
            if pat in path_lower:
                return None

        # Marketing / junk (vianoce, firemne akcie, vernostne karty).
        for pat in self.MARKETING_PATTERNS:
            if pat in path_lower:
                return None

        # Strip tracking params, zachovaj zvysok.
        query_pairs = parse_qsl(parsed.query, keep_blank_values=False)
        kept = [(k, v) for k, v in query_pairs if k.lower() not in _TRACKING_PARAMS]
        new_query = urlencode(kept)

        # Normalize path: trailing slash strip okrem root.
        norm_path = parsed.path or "/"
        if len(norm_path) > 1 and norm_path.endswith("/"):
            norm_path = norm_path.rstrip("/")

        # Canonical host = base host (kolapsuje www.x.sk a x.sk varianty na 1 URL).
        canonical_netloc = base_parsed.netloc.lower()

        return urlunparse((parsed.scheme, canonical_netloc, norm_path, "", new_query, ""))

    def _filter_urls_with_via(
        self,
        urls_with_via: list[tuple[str, str]],
        base_url: str,
    ) -> list[tuple[str, str]]:
        """Filter + dedupe (case-insensitive). Zachova `via` z prveho vyskytu."""
        seen: dict[str, tuple[str, str]] = {}
        for raw_url, via in urls_with_via:
            norm = self._normalize_url(raw_url, base_url)
            if norm is None:
                continue
            key = norm.lower()
            if key in seen:
                continue
            seen[key] = (norm, via)
        return list(seen.values())

    def _filter_urls(self, urls: list[str], base_url: str) -> list[str]:
        """Convenience wrapper bez 'via' (pre testy)."""
        seen: dict[str, str] = {}
        for raw_url in urls:
            norm = self._normalize_url(raw_url, base_url)
            if norm is None:
                continue
            key = norm.lower()
            if key in seen:
                continue
            seen[key] = norm
        return list(seen.values())

    # --------------------------------------------------------------- prioritize
    def _classify(self, url: str, base_url: str) -> PagePriority:
        """Vrat PagePriority pre danu URL podla pattern matchu.

        TIER_0 (/kontakt) sa kontroluje FIRST a presahuje vsetko ostatne.
        """
        try:
            parsed = urlparse(url)
            base_parsed = urlparse(base_url)
        except Exception:
            return PagePriority.TIER_4_OTHER

        path = (parsed.path or "/").lower()
        base_path = (base_parsed.path or "/").lower()

        # TIER_0 (essential) — /kontakt | /contact — FIRST check.
        for pat in self.TIER_0_PATTERNS:
            if pat in path:
                return PagePriority.TIER_0_ESSENTIAL

        # Homepage (presny match s base) → TIER_1.
        if path in ("", "/", base_path):
            return PagePriority.TIER_1_CRITICAL

        for pat in self.TIER_1_PATTERNS:
            if pat in path:
                return PagePriority.TIER_1_CRITICAL
        for pat in self.TIER_2_PATTERNS:
            if pat in path:
                return PagePriority.TIER_2_IMPORTANT
        for pat in self.TIER_3_PATTERNS:
            if pat in path:
                return PagePriority.TIER_3_USEFUL
        return PagePriority.TIER_4_OTHER

    # Per-tier caps (anti-TIER_4-spam, anti-sluzby-explozia).
    _TIER_CAPS = {
        # TIER_0: unlimited (always included, typicky 1-2 URL)
        PagePriority.TIER_1_CRITICAL: 20,
        PagePriority.TIER_2_IMPORTANT: 5,
        PagePriority.TIER_3_USEFUL: 5,
        PagePriority.TIER_4_OTHER: 3,
    }

    def _prioritize(
        self,
        urls_with_via: list[tuple[str, str]],
        base_url: str,
    ) -> list[DiscoveredPage]:
        """Priraď prioritu kazdej URL a aplikuj per-tier caps.

        Poradie: TIER_0 (unlimited) → TIER_1 (max 20) → TIER_2 (5) → TIER_3 (5) → TIER_4 (3).
        V ramci tieru zachova discovery order.
        """
        pages: list[DiscoveredPage] = []
        for url, via in urls_with_via:
            priority = self._classify(url, base_url)
            pages.append(DiscoveredPage(url=url, priority=priority, discovered_via=via))

        tier_0 = [p for p in pages if p.priority is PagePriority.TIER_0_ESSENTIAL]
        tier_1 = [p for p in pages if p.priority is PagePriority.TIER_1_CRITICAL]
        tier_2 = [p for p in pages if p.priority is PagePriority.TIER_2_IMPORTANT]
        tier_3 = [p for p in pages if p.priority is PagePriority.TIER_3_USEFUL]
        tier_4 = [p for p in pages if p.priority is PagePriority.TIER_4_OTHER]

        result = (
            tier_0
            + tier_1[: self._TIER_CAPS[PagePriority.TIER_1_CRITICAL]]
            + tier_2[: self._TIER_CAPS[PagePriority.TIER_2_IMPORTANT]]
            + tier_3[: self._TIER_CAPS[PagePriority.TIER_3_USEFUL]]
            + tier_4[: self._TIER_CAPS[PagePriority.TIER_4_OTHER]]
        )
        return result
