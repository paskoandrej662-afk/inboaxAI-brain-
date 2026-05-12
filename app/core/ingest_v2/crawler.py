"""CrawlerV2 — hybrid page discovery pre Universal Ingestion Engine v2 (Phase 2A).

Stratégia: seed + sitemap.xml + robots.txt + homepage links (+ plytky BFS),
URL normalizacia, same-domain filter, robots Disallow filter, priority scoring,
deduplikacia, cap na `max_pages`. Zero LLM.

Defensive: ziadna metoda neraisne pri zlych vstupoch; sietove operacie maju 5s
timeout a na chybu vracaju prazdny vysledok (crawling sa nezablokuje).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Priority patterns — matchuju sa proti path (lowercase) cieloveho URL.
# ---------------------------------------------------------------------------
HIGH_PRIORITY_PATTERNS: list[tuple[str, float]] = [
    (r"/(kontakt|contact|kontakty)(/|$)", 1.0),
    (r"/(o-nas|about|about-us|kto-sme|kto-sme-my)(/|$)", 0.95),
    (r"/(sluzby|services|nabídka|nabidka)(/|$)", 0.95),
    (r"/(produkty|products|tovar|katalog|sortiment)(/|$)", 0.95),
    (r"/(cennik|cenník|ceny|pricing|cena)(/|$)", 0.95),
    (r"/(galeria|gallery|fotogaleria|fotky|photos)(/|$)", 0.85),
    (r"/(faq|najcastejsie-otazky|casto-kladene-otazky|otazky)(/|$)", 0.90),
    (r"/(certifikaty|certificates|certifikat)(/|$)", 0.85),
    (r"/(menu|ponuka|jedalniček|jedalnicek)(/|$)", 0.95),
    (r"/(portfolio|portfolió|referencie|reference)(/|$)", 0.90),
    (r"/(reviews|hodnotenia|recenzie)(/|$)", 0.80),
    (r"/(team|tim|nas-tim|naš-tím)(/|$)", 0.75),
]

LOW_PRIORITY_PATTERNS: list[tuple[str, float]] = [
    (r"/(gdpr|privacy|cookies|ochrana-osobnych-udajov|ochrana-udajov|sukromie)(/|$)", 0.05),
    (r"/(obchodne-podmienky|terms|terms-of-service|tos|vop)(/|$)", 0.05),
    (r"/(login|prihlasenie|registracia|register)(/|$)", 0.10),
    (r"/(cart|kosik|kosík|checkout|objednavka)(/|$)", 0.10),
    (r"/(tag|tags)/", 0.15),
    (r"/(category|kategoria|kategória)/[^/]+/page/\d+", 0.10),
    (r"/(author|autor)/", 0.15),
    (r"/feed/?$", 0.05),
    (r"\.(jpg|jpeg|png|gif|svg|webp|pdf|zip|doc|docx)$", 0.00),  # binarne subory
]

DEFAULT_PRIORITY = 0.40
HOMEPAGE_PRIORITY = 0.85

# Tracking query params, ktore zahadzujeme pri normalizacii.
_TRACKING_PARAMS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "yclid", "igshid",
    "mc_cid", "mc_eid", "_ga", "_gl", "ref", "ref_src",
}

_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)

# Pre-kompilovane priority patterny.
_HIGH_COMPILED = [(re.compile(p, re.IGNORECASE), s) for p, s in HIGH_PRIORITY_PATTERNS]
_LOW_COMPILED = [(re.compile(p, re.IGNORECASE), s) for p, s in LOW_PRIORITY_PATTERNS]


@dataclass
class DiscoveredPage:
    url: str  # normalized
    discovery_method: str  # 'seed','sitemap','homepage_link','bfs','rendered_link','robots'
    depth: int  # 0 pre seed, 1 pre priame linky zo seedu
    parent_url: str | None
    priority_score: float  # 0.0-1.0


class CrawlerV2:
    def __init__(
        self,
        renderer: "Optional[object]" = None,
        max_pages: int = 12,
        max_depth: int = 2,
        user_agent: str = "InboxAI-Ingest/1.0",
    ) -> None:
        self.renderer = renderer
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.user_agent = user_agent

    # ------------------------------------------------------------------ public
    async def discover_pages(self, start_url: str) -> list[DiscoveredPage]:
        """Hybrid discovery: seed + sitemap + robots + homepage links (+ plytky BFS).

        Vysledok je deduplikovany, zoradeny (seed prvy, potom podla priority desc)
        a orezany na `max_pages`.
        """
        start_norm = self._normalize_url(start_url, start_url) or start_url.rstrip("/")
        discovered: dict[str, DiscoveredPage] = {}

        # --- seed -------------------------------------------------------------
        discovered[start_norm] = DiscoveredPage(
            url=start_norm,
            discovery_method="seed",
            depth=0,
            parent_url=None,
            priority_score=HOMEPAGE_PRIORITY,
        )

        # --- robots.txt -------------------------------------------------------
        robots = await self._fetch_robots(start_url)
        disallow: list[str] = robots.get("disallow", [])
        robots_sitemaps: list[str] = robots.get("sitemaps", [])

        # --- sitemap ----------------------------------------------------------
        try:
            sitemap_urls = await self._fetch_sitemap_urls(start_url, extra_sitemaps=robots_sitemaps)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("sitemap discovery failed for %s: %s", start_url, e)
            sitemap_urls = []
        for raw_url, hint in sitemap_urls:
            n = self._normalize_url(raw_url, start_url)
            if not n or n in discovered:
                continue
            if self._is_disallowed(n, disallow):
                continue
            base_pr = self._priority_for_url(n)
            discovered[n] = DiscoveredPage(
                url=n,
                discovery_method="sitemap",
                depth=1,
                parent_url=start_norm,
                priority_score=max(base_pr, hint),
            )

        # --- homepage links ---------------------------------------------------
        try:
            homepage_links = await self._extract_homepage_links(start_url)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("homepage link extraction failed for %s: %s", start_url, e)
            homepage_links = []
        for href in homepage_links:
            n = self._normalize_url(href, start_url)
            if not n or n in discovered:
                continue
            if self._is_disallowed(n, disallow):
                continue
            discovered[n] = DiscoveredPage(
                url=n,
                discovery_method="homepage_link",
                depth=1,
                parent_url=start_norm,
                priority_score=self._priority_for_url(n),
            )

        # --- plytky BFS (depth 2) — len ak mame renderer a max_depth >= 2 -----
        if self.max_depth >= 2 and self.renderer is not None:
            depth1 = sorted(
                (p for p in discovered.values() if p.depth == 1 and p.discovery_method == "homepage_link"),
                key=lambda p: p.priority_score,
                reverse=True,
            )
            # Expandujeme len zopar najlepsich, aby sme nezahltili crawl.
            bfs_parents = depth1[: max(0, min(3, self.max_pages))]
            for parent in bfs_parents:
                if len(discovered) >= self.max_pages * 3:
                    break
                try:
                    rendered = await self.renderer.render_page(parent.url)  # type: ignore[attr-defined]
                except Exception as e:  # pragma: no cover - defensive
                    logger.debug("bfs render failed for %s: %s", parent.url, e)
                    continue
                if getattr(rendered, "render_status", None) != "success":
                    continue
                try:
                    soup = BeautifulSoup(getattr(rendered, "html", "") or "", "html.parser")
                except Exception:
                    continue
                for a in soup.find_all("a", href=True):
                    n = self._normalize_url(a.get("href"), parent.url)
                    if not n or n in discovered:
                        continue
                    if self._is_disallowed(n, disallow):
                        continue
                    discovered[n] = DiscoveredPage(
                        url=n,
                        discovery_method="bfs",
                        depth=2,
                        parent_url=parent.url,
                        priority_score=self._priority_for_url(n),
                    )

        # --- sort + cap -------------------------------------------------------
        seed_page = discovered.get(start_norm)
        others = [p for p in discovered.values() if not seed_page or p.url != seed_page.url]
        others.sort(key=lambda p: (p.priority_score, -p.depth), reverse=True)
        result: list[DiscoveredPage] = ([seed_page] if seed_page else []) + others
        return result[: self.max_pages]

    # ------------------------------------------------------------------ network
    async def _fetch_sitemap_urls(
        self, start_url: str, extra_sitemaps: list[str] | None = None
    ) -> list[tuple[str, float]]:
        """Skusi /sitemap.xml, /sitemap_index.xml a sitemapy z robots.txt.

        Vrati zoznam (url, priority_hint). priority_hint je momentalne 0.5 pre vsetky
        (jemny boost — stranky v sitemap su "kanonicke"). Na chybu vracia [].
        """
        try:
            parsed = urlparse(start_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return []
        candidates = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
        for sm in extra_sitemaps or []:
            if sm and sm not in candidates:
                candidates.append(sm)

        found: dict[str, float] = {}
        try:
            async with httpx.AsyncClient(
                timeout=5.0, follow_redirects=True, headers={"User-Agent": self.user_agent}
            ) as client:
                visited_sitemaps: set[str] = set()
                queue = list(candidates)
                # Max 6 sitemap fetchov (vratane sitemap-index expanzii).
                while queue and len(visited_sitemaps) < 6:
                    sm_url = queue.pop(0)
                    if sm_url in visited_sitemaps:
                        continue
                    visited_sitemaps.add(sm_url)
                    try:
                        resp = await client.get(sm_url)
                    except Exception:
                        continue
                    if resp.status_code != 200 or not resp.text:
                        continue
                    text = resp.text
                    locs = _SITEMAP_LOC_RE.findall(text)
                    is_index = "<sitemapindex" in text.lower()
                    if is_index:
                        for loc in locs:
                            if loc not in visited_sitemaps and loc not in queue:
                                queue.append(loc)
                    else:
                        for loc in locs:
                            if loc not in found:
                                found[loc] = 0.5
                            if len(found) >= 500:
                                break
                    if len(found) >= 500:
                        break
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("sitemap fetch error for %s: %s", start_url, e)
            return list(found.items())
        return list(found.items())

    async def _fetch_robots(self, start_url: str) -> dict:
        """Stiahne robots.txt. Vrati {'disallow': [...], 'sitemaps': [...]}.

        Na akukolvek chybu vracia prazdny dict (nesmie zablokovat crawling).
        """
        try:
            parsed = urlparse(start_url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        except Exception:
            return {}
        try:
            async with httpx.AsyncClient(
                timeout=5.0, follow_redirects=True, headers={"User-Agent": self.user_agent}
            ) as client:
                resp = await client.get(robots_url)
        except Exception:
            return {}
        if resp.status_code != 200 or not resp.text:
            return {}

        disallow: list[str] = []
        sitemaps: list[str] = []
        applies = True  # platí pre aktualny User-agent blok
        for line in resp.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                # Sledujeme len '*' a nas vlastny UA prefix.
                ua = value.lower()
                applies = ua == "*" or ua in self.user_agent.lower() or self.user_agent.lower().startswith(ua)
            elif key == "disallow" and applies:
                if value:
                    disallow.append(value)
            elif key == "sitemap":
                if value:
                    sitemaps.append(value)
        return {"disallow": disallow, "sitemaps": sitemaps}

    async def _extract_homepage_links(self, start_url: str) -> list[str]:
        """Vyrenderuje homepage cez self.renderer a vrati vsetky <a href>."""
        if self.renderer is None:
            return []
        try:
            rendered = await self.renderer.render_page(start_url)  # type: ignore[attr-defined]
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("renderer.render_page failed for %s: %s", start_url, e)
            return []
        if getattr(rendered, "render_status", None) != "success":
            return []
        try:
            soup = BeautifulSoup(getattr(rendered, "html", "") or "", "html.parser")
        except Exception:
            return []
        return [a.get("href") for a in soup.find_all("a", href=True) if a.get("href")]

    # ------------------------------------------------------------------ helpers
    def _normalize_url(self, url: str, base: str) -> str | None:
        """Resolve relative, lowercase scheme+netloc, drop www., strip fragment +
        trailing slash + tracking params. Vrati None ak je URL invalidne alebo
        z ineho domenoveho mena.
        """
        if not url or not isinstance(url, str):
            return None
        url = url.strip()
        if not url:
            return None
        low = url.lower()
        if low.startswith(("mailto:", "tel:", "javascript:", "data:", "sms:", "callto:")):
            return None
        try:
            absolute = urljoin(base, url)
            parsed = urlparse(absolute)
        except Exception:
            return None
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            return None
        netloc = (parsed.netloc or "").lower()
        if not netloc:
            return None
        host = netloc[4:] if netloc.startswith("www.") else netloc

        # Same-domain check (www. ekvivalencia).
        try:
            base_netloc = (urlparse(base).netloc or "").lower()
            base_host = base_netloc[4:] if base_netloc.startswith("www.") else base_netloc
        except Exception:
            base_host = ""
        if base_host and host != base_host:
            return None

        # Path — case-sensitive zachovaj; strip trailing slash okrem root.
        path = parsed.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/") or "/"

        # Query — odstran tracking params.
        query = ""
        if parsed.query:
            kept: list[tuple[str, str]] = []
            try:
                for k, v in parse_qsl(parsed.query, keep_blank_values=True):
                    kl = k.lower()
                    if kl.startswith("utm_") or kl in _TRACKING_PARAMS:
                        continue
                    kept.append((k, v))
            except Exception:
                kept = []
            if kept:
                query = urlencode(kept)

        rebuilt = f"{scheme}://{host}{path}"
        if query:
            rebuilt += f"?{query}"
        return rebuilt

    def _priority_for_url(self, url: str) -> float:
        """Vrati priority 0.0-1.0. Homepage 0.85, inak HIGH/LOW patterny, default 0.40."""
        try:
            parsed = urlparse(url)
            path = parsed.path or "/"
        except Exception:
            return DEFAULT_PRIORITY
        if path in ("", "/"):
            return HOMEPAGE_PRIORITY
        path_l = path.lower()
        for rx, score in _HIGH_COMPILED:
            if rx.search(path_l):
                return score
        for rx, score in _LOW_COMPILED:
            if rx.search(path_l):
                return score
        return DEFAULT_PRIORITY

    def _is_disallowed(self, url: str, robots_disallow: list[str]) -> bool:
        """True ak path URL spada pod niektory robots.txt Disallow prefix."""
        if not robots_disallow:
            return False
        try:
            path = urlparse(url).path or "/"
        except Exception:
            return False
        for rule in robots_disallow:
            if not rule:
                continue
            prefix = rule.split("*", 1)[0]
            if not prefix:
                continue
            if path.startswith(prefix):
                return True
        return False
