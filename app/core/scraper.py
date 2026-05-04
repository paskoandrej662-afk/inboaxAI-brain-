from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import httpx
import tldextract
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

USER_AGENT = "InboxAI Brain/0.1 (+https://inboxai.example/bot)"
DEFAULT_TIMEOUT = 15.0
MAX_PARALLEL = 5
SKIP_PATH_FRAGMENTS = (
    "/admin",
    "/wp-admin",
    "/wp-login",
    "/login",
    "/logout",
    "/signin",
    "/signup",
    "/register",
    "/cart",
    "/checkout",
    "/feed",
    "/rss",
    "/tag/",
    "/tags/",
    "/author/",
)
SKIP_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".mp4",
    ".mp3",
    ".webm",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".css",
    ".js",
    ".json",
    ".xml",
)


@dataclass
class ScrapedPage:
    url: str
    html: str = ""
    status_code: int = 0
    content_type: str = ""
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None


def _same_registered_domain(a: str, b: str) -> bool:
    ea = tldextract.extract(a)
    eb = tldextract.extract(b)
    if not ea.registered_domain or not eb.registered_domain:
        return False
    return ea.registered_domain.lower() == eb.registered_domain.lower()


def _is_skippable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    path = parsed.path.lower()
    if any(frag in path for frag in SKIP_PATH_FRAGMENTS):
        return True
    if any(path.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    return False


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    # Strip fragment, keep query
    base = f"{parsed.scheme}://{parsed.netloc}{path}"
    if parsed.query:
        base = f"{base}?{parsed.query}"
    # Drop trailing slash on non-root paths for dedupe consistency
    if path != "/" and base.endswith("/"):
        base = base[:-1]
    return base


async def fetch_url(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = DEFAULT_TIMEOUT,
) -> ScrapedPage | None:
    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower() and resp.status_code < 400:
            logger.info("scraper: skipping non-HTML %s (%s)", url, ctype)
            return ScrapedPage(
                url=str(resp.url),
                status_code=resp.status_code,
                content_type=ctype,
                error="non-html-content",
            )
        return ScrapedPage(
            url=str(resp.url),
            html=resp.text,
            status_code=resp.status_code,
            content_type=ctype,
        )
    except (httpx.RequestError, httpx.HTTPError) as exc:
        logger.warning("scraper: fetch error %s: %s", url, exc)
        return ScrapedPage(url=url, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("scraper: unexpected error %s", url)
        return ScrapedPage(url=url, error=str(exc))


async def _try_robots(client: httpx.AsyncClient, base: str) -> RobotFileParser | None:
    robots_url = urljoin(base, "/robots.txt")
    try:
        resp = await client.get(robots_url, timeout=10.0, follow_redirects=True)
        if resp.status_code >= 400:
            return None
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp
    except Exception:
        return None


async def _parse_sitemap(
    client: httpx.AsyncClient, sitemap_url: str, depth: int = 0
) -> list[str]:
    if depth > 3:
        return []
    try:
        resp = await client.get(sitemap_url, timeout=15.0, follow_redirects=True)
        if resp.status_code >= 400:
            return []
        text = resp.text
    except Exception:
        return []

    urls: list[str] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    # Strip namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    if root.tag.endswith("sitemapindex"):
        for sm in root.findall(f"{ns}sitemap"):
            loc_el = sm.find(f"{ns}loc")
            if loc_el is not None and loc_el.text:
                urls.extend(await _parse_sitemap(client, loc_el.text.strip(), depth + 1))
    elif root.tag.endswith("urlset"):
        for u in root.findall(f"{ns}url"):
            loc_el = u.find(f"{ns}loc")
            if loc_el is not None and loc_el.text:
                urls.append(loc_el.text.strip())
    return urls


async def discover_pages(start_url: str, max_pages: int = 30) -> list[str]:
    """Discover pages via sitemap.xml; fallback to BFS crawl from homepage."""
    parsed = urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "sk,cs;q=0.9,en;q=0.8"}

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        # Try sitemaps first
        sitemap_candidates = [
            urljoin(base, "/sitemap.xml"),
            urljoin(base, "/sitemap_index.xml"),
            urljoin(base, "/sitemap-index.xml"),
        ]

        # robots.txt may list Sitemap: lines
        try:
            rresp = await client.get(urljoin(base, "/robots.txt"), timeout=10.0)
            if rresp.status_code < 400:
                for line in rresp.text.splitlines():
                    line = line.strip()
                    if line.lower().startswith("sitemap:"):
                        url = line.split(":", 1)[1].strip()
                        if url and url not in sitemap_candidates:
                            sitemap_candidates.append(url)
        except Exception:
            pass

        sitemap_urls: list[str] = []
        for sm in sitemap_candidates:
            urls = await _parse_sitemap(client, sm)
            if urls:
                sitemap_urls.extend(urls)
                if len(sitemap_urls) >= max_pages * 3:
                    break

        if sitemap_urls:
            seen: set[str] = set()
            picked: list[str] = []
            # Always prefer the start URL itself
            for candidate in [start_url, *sitemap_urls]:
                norm = _normalize_url(candidate)
                if norm in seen:
                    continue
                if not _same_registered_domain(norm, start_url):
                    continue
                if _is_skippable(norm):
                    continue
                seen.add(norm)
                picked.append(norm)
                if len(picked) >= max_pages:
                    break
            if picked:
                return picked

        # Fallback: BFS crawl from homepage
        return await _bfs_crawl(client, start_url, max_pages)


async def _bfs_crawl(
    client: httpx.AsyncClient, start_url: str, max_pages: int
) -> list[str]:
    seen: set[str] = set()
    queue: list[str] = [_normalize_url(start_url)]
    seen.add(queue[0])
    out: list[str] = []

    while queue and len(out) < max_pages:
        url = queue.pop(0)
        if _is_skippable(url):
            continue
        try:
            resp = await client.get(url, timeout=10.0)
        except Exception:
            continue
        if resp.status_code >= 400:
            continue
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower():
            continue
        out.append(url)

        try:
            tree = HTMLParser(resp.text)
        except Exception:
            continue
        for a in tree.css("a[href]"):
            href = a.attributes.get("href")
            if not href:
                continue
            absolute = urljoin(url, href)
            norm = _normalize_url(absolute)
            if norm in seen:
                continue
            if not _same_registered_domain(norm, start_url):
                continue
            if _is_skippable(norm):
                continue
            seen.add(norm)
            queue.append(norm)

    return out


async def scrape_site(
    start_url: str,
    max_pages: int = 30,
    time_budget_s: float = 90.0,
) -> list[ScrapedPage]:
    """Discover URLs and fetch them with a concurrency limit + time budget."""
    started = time.monotonic()
    urls = await discover_pages(start_url, max_pages=max_pages)
    if not urls:
        urls = [start_url]

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "sk,cs;q=0.9,en;q=0.8"}
    sem = asyncio.Semaphore(MAX_PARALLEL)
    pages: list[ScrapedPage] = []

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        rp = await _try_robots(client, start_url)

        async def _bound(u: str) -> ScrapedPage | None:
            if rp is not None:
                try:
                    if not rp.can_fetch(USER_AGENT, u):
                        return ScrapedPage(url=u, error="blocked-by-robots")
                except Exception:
                    pass
            async with sem:
                if time.monotonic() - started > time_budget_s:
                    return ScrapedPage(url=u, error="time-budget-exceeded")
                return await fetch_url(u, client)

        results = await asyncio.gather(*[_bound(u) for u in urls], return_exceptions=False)
        for r in results:
            if r is not None:
                pages.append(r)

    return pages
