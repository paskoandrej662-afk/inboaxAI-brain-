"""Testy pre HDS-v3 crawler (app/core/extractors/hds_v3/crawler.py).

Offline only — ziadne realne network/playwright cally. Sitemap parser + filtre +
priority logika sa testuju priamo na statickom inpute; async cesty su mockovane.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.extractors.hds_v3.crawler import HDSCrawler
from app.core.extractors.hds_v3.types import PagePriority


def _crawler() -> HDSCrawler:
    return HDSCrawler()


# ============================================================ priority / classify
def test_priority_homepage_is_tier_1():
    """Homepage URL (sam base_url) ma byt TIER_1_CRITICAL."""
    c = _crawler()
    base = "https://example.sk/"
    assert c._classify(base, base) is PagePriority.TIER_1_CRITICAL
    # Aj bez trailing slash by mal byt homepage.
    assert c._classify("https://example.sk", base) is PagePriority.TIER_1_CRITICAL


def test_priority_kontakt_is_tier_0():
    """URL obsahujuca '/kontakt' alebo '/contact' → TIER_0_ESSENTIAL (must-have)."""
    c = _crawler()
    base = "https://example.sk/"
    assert c._classify("https://example.sk/kontakt", base) is PagePriority.TIER_0_ESSENTIAL
    assert c._classify("https://example.sk/contact-us", base) is PagePriority.TIER_0_ESSENTIAL
    assert c._classify("https://example.sk/contacts", base) is PagePriority.TIER_0_ESSENTIAL


def test_priority_produkty_cennik_is_tier_1():
    """URL '/produkty', '/sluzby', '/cennik' → TIER_1."""
    c = _crawler()
    base = "https://example.sk/"
    assert c._classify("https://example.sk/produkty", base) is PagePriority.TIER_1_CRITICAL
    assert c._classify("https://example.sk/sluzby", base) is PagePriority.TIER_1_CRITICAL
    assert c._classify("https://example.sk/cennik", base) is PagePriority.TIER_1_CRITICAL


def test_priority_o_nas_is_tier_2():
    """URL '/o-nas' alebo '/about' → TIER_2."""
    c = _crawler()
    base = "https://example.sk/"
    assert c._classify("https://example.sk/o-nas", base) is PagePriority.TIER_2_IMPORTANT
    assert c._classify("https://example.sk/about", base) is PagePriority.TIER_2_IMPORTANT
    assert c._classify("https://example.sk/faq", base) is PagePriority.TIER_2_IMPORTANT


def test_priority_blog_is_tier_3():
    """URL '/blog/clanok-1' → TIER_3."""
    c = _crawler()
    base = "https://example.sk/"
    assert c._classify("https://example.sk/blog/clanok-1", base) is PagePriority.TIER_3_USEFUL
    assert c._classify("https://example.sk/galeria", base) is PagePriority.TIER_3_USEFUL
    assert c._classify("https://example.sk/novinky", base) is PagePriority.TIER_3_USEFUL


def test_priority_unknown_is_tier_4():
    """Neznama URL → TIER_4_OTHER."""
    c = _crawler()
    base = "https://example.sk/"
    assert c._classify("https://example.sk/nejaka-divna-cesta", base) is PagePriority.TIER_4_OTHER


# ============================================================ filtering
def test_filter_removes_external_urls():
    """Z mixed listu vyhodit externe domeny."""
    c = _crawler()
    urls = [
        "https://example.sk/kontakt",
        "https://google.com/search",
        "https://facebook.com/example",
        "https://example.sk/produkty",
        "https://other.com/x",
    ]
    out = c._filter_urls(urls, "https://example.sk/")
    assert len(out) == 2
    assert all("example.sk" in u for u in out)


def test_filter_removes_pdf_jpg():
    """.pdf, .jpg, .png URLs vyhodit."""
    c = _crawler()
    urls = [
        "https://example.sk/dok/cennik.pdf",
        "https://example.sk/img/logo.jpg",
        "https://example.sk/foto/galeria.png",
        "https://example.sk/produkty",
        "https://example.sk/file.zip",
    ]
    out = c._filter_urls(urls, "https://example.sk/")
    assert out == ["https://example.sk/produkty"]


def test_filter_removes_admin_login():
    """/wp-admin, /login, /cart URLs vyhodit."""
    c = _crawler()
    urls = [
        "https://example.sk/wp-admin/index.php",
        "https://example.sk/login",
        "https://example.sk/cart",
        "https://example.sk/kosik",
        "https://example.sk/wp-content/uploads/x.jpg",
        "https://example.sk/produkty",
    ]
    out = c._filter_urls(urls, "https://example.sk/")
    assert out == ["https://example.sk/produkty"]


def test_dedupe_normalizes_trailing_slash():
    """site.sk/kontakt a site.sk/kontakt/ → 1 URL."""
    c = _crawler()
    urls = [
        "https://example.sk/kontakt",
        "https://example.sk/kontakt/",
        "https://example.sk/Kontakt",  # case match doesn't matter for path, but treat as same after lower-key dedupe
        "https://example.sk/kontakt?utm_source=fb",
    ]
    out = c._filter_urls(urls, "https://example.sk/")
    # Po normalizacii (lower host, strip trailing /, strip utm) by mali kolabovat
    # na rovnaky alebo malo zaznamov. Cesta sa nelowercasuje — '/Kontakt' a '/kontakt'
    # su technicky rozne URL podla RFC, ale nas dedupe pouziva .lower() na key.
    assert len(out) == 1
    assert out[0].endswith("/kontakt") or out[0].endswith("/Kontakt")


def test_filter_strips_tracking_params():
    """utm_*, fbclid sa odstranuju; ine query params sa zachovaju."""
    c = _crawler()
    urls = ["https://example.sk/produkt?id=7&utm_source=fb&fbclid=abc"]
    out = c._filter_urls(urls, "https://example.sk/")
    assert len(out) == 1
    assert "utm_source" not in out[0]
    assert "fbclid" not in out[0]
    assert "id=7" in out[0]


# ============================================================ sitemap parser
def test_sitemap_parser_handles_basic_xml():
    """Mock fetched sitemap XML s 3 <loc> tagmi → 3 URL."""
    c = _crawler()
    xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.sk/</loc></url>
  <url><loc>https://example.sk/kontakt</loc></url>
  <url><loc>https://example.sk/produkty</loc></url>
</urlset>"""
    urls, sub = c._parse_sitemap_xml(xml_text)
    assert len(urls) == 3
    assert "https://example.sk/kontakt" in urls
    assert sub == []


def test_sitemap_parser_handles_sitemap_index():
    """Sitemap-index (root <sitemapindex>) → sub_sitemaps list."""
    c = _crawler()
    xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.sk/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.sk/sitemap2.xml</loc></sitemap>
</sitemapindex>"""
    urls, sub = c._parse_sitemap_xml(xml_text)
    assert urls == []
    assert len(sub) == 2
    assert "https://example.sk/sitemap1.xml" in sub


def test_sitemap_parser_malformed_returns_empty():
    """Malformed XML → ([], [])."""
    c = _crawler()
    urls, sub = c._parse_sitemap_xml("<broken><<")
    assert urls == []
    assert sub == []


# ============================================================ per-tier caps
@pytest.mark.asyncio
async def test_per_tier_caps_applied():
    """Per-tier caps: TIER_4 max 3, TIER_1 max 20, TIER_2/3 max 5; TIER_0 unlimited."""
    c = HDSCrawler()
    # Generuj mix: 30 TIER_1 (sluzby/*), 10 TIER_2 (faq/*), 10 TIER_3 (blog/*),
    # 100 TIER_4 (random paths), 2 TIER_0 (kontakt).
    fake_urls = (
        [f"https://example.sk/sluzby/x{i:02d}" for i in range(30)]
        + [f"https://example.sk/faq/q{i:02d}" for i in range(10)]
        + [f"https://example.sk/blog/p{i:02d}" for i in range(10)]
        + [f"https://example.sk/random-{i:03d}" for i in range(100)]
        + ["https://example.sk/kontakt", "https://example.sk/contact-us"]
    )

    async def fake_sitemap(self, base_url):  # noqa: ARG001
        return fake_urls

    async def fake_homepage(self, base_url):  # noqa: ARG001
        return []

    with patch.object(HDSCrawler, "_try_sitemap", fake_sitemap), \
         patch.object(HDSCrawler, "_crawl_homepage_links", fake_homepage):
        result = await c.discover("https://example.sk/")

    assert result.success is True
    tier_0 = [p for p in result.pages if p.priority is PagePriority.TIER_0_ESSENTIAL]
    tier_1 = [p for p in result.pages if p.priority is PagePriority.TIER_1_CRITICAL]
    tier_2 = [p for p in result.pages if p.priority is PagePriority.TIER_2_IMPORTANT]
    tier_3 = [p for p in result.pages if p.priority is PagePriority.TIER_3_USEFUL]
    tier_4 = [p for p in result.pages if p.priority is PagePriority.TIER_4_OTHER]
    assert len(tier_0) == 2  # unlimited (kontakt + contact-us)
    assert len(tier_1) == 20  # cap 20 (homepage + 19 sluzby)
    assert len(tier_2) == 5  # cap 5 faq
    # Pre-final-cap total: 2 + 20 + 5 + 5 + 3 = 35 → final MAX_PAGES=30 orezava chvost.
    # Order = T0 + T1 + T2 + T3 + T4 → posledne T4 (3) a 2 T3 sa odstria.
    assert len(result.pages) == HDSCrawler.MAX_PAGES
    assert len(tier_3) <= 5  # cap 5; po MAX_PAGES cape mozu odpadnut
    assert len(tier_4) <= 3  # cap 3; po MAX_PAGES cape padaju ako prve
    # TIER_0 ide na zaciatok (nesmie sa orezat cap-om).
    assert result.pages[0].priority is PagePriority.TIER_0_ESSENTIAL
    # /kontakt sa zachova ako TIER_0 aj pri 100+ kandidatov
    kontakt_urls = [p.url for p in result.pages if p.priority is PagePriority.TIER_0_ESSENTIAL]
    assert any("/kontakt" in u for u in kontakt_urls)


# ============================================================ discover end-to-end (mock)
@pytest.mark.asyncio
async def test_discover_uses_sitemap_when_available():
    """Ak sitemap najde URLs, homepage fallback sa neuzije."""
    c = HDSCrawler()
    sitemap_urls = [
        "https://example.sk/kontakt",
        "https://example.sk/produkty",
        "https://example.sk/o-nas",
    ]
    home_mock = AsyncMock()

    async def fake_sitemap(self, base_url):  # noqa: ARG001
        return sitemap_urls

    with patch.object(HDSCrawler, "_try_sitemap", fake_sitemap), \
         patch.object(HDSCrawler, "_crawl_homepage_links", home_mock):
        result = await c.discover("https://example.sk/")

    assert result.success is True
    assert result.sitemap_found is True
    # Sitemap najde 3 + homepage seed = 4 unikatne URL.
    assert result.total_discovered == 4
    home_mock.assert_not_called()
    # /kontakt sa nachadza → TIER_0 ide na prvu poziciu.
    assert result.pages[0].priority is PagePriority.TIER_0_ESSENTIAL
    assert "/kontakt" in result.pages[0].url.lower()


@pytest.mark.asyncio
async def test_discover_falls_back_to_homepage_links():
    """Ak sitemap zlyha, pouzije sa homepage_link discovery."""
    c = HDSCrawler()
    home_links = [
        "https://example.sk/kontakt",
        "https://example.sk/blog/clanok-1",
        "https://other.com/external",  # external — vyhodit
    ]

    async def fake_sitemap(self, base_url):  # noqa: ARG001
        return []

    async def fake_homepage(self, base_url):  # noqa: ARG001
        return home_links

    with patch.object(HDSCrawler, "_try_sitemap", fake_sitemap), \
         patch.object(HDSCrawler, "_crawl_homepage_links", fake_homepage):
        result = await c.discover("https://example.sk/")

    assert result.success is True
    assert result.sitemap_found is False
    # Homepage seed + 2 same-domain linky (external vyhodeny) = 3.
    urls = [p.url for p in result.pages]
    assert any("example.sk/kontakt" in u for u in urls)
    assert not any("other.com" in u for u in urls)


@pytest.mark.asyncio
async def test_discover_invalid_url_returns_error():
    """Invalid base URL → success=False, error='invalid_base_url'."""
    c = HDSCrawler()
    result = await c.discover("")
    assert result.success is False
    assert result.error == "invalid_base_url"


# ============================================================ recruitment / marketing filters
def test_recruitment_urls_filtered_out():
    """Career inzeraty (strojnik, elektrikar, pracuj-v-X, …) sa vyhadzuju."""
    c = _crawler()
    urls = [
        "https://example.sk/strojnik",
        "https://example.sk/elektrikar-na-montaze",
        "https://example.sk/zvarac-tig",
        "https://example.sk/lezec",
        "https://example.sk/pracuj-v-firme",
        "https://example.sk/kariera",
        "https://example.sk/pomocny-pracovnik",
        "https://example.sk/nevies-kam-po-skole",
        "https://example.sk/produkty",  # toto preziva
        "https://example.sk/o-nas",  # toto preziva
    ]
    out = c._filter_urls(urls, "https://example.sk/")
    assert sorted(out) == sorted(["https://example.sk/produkty", "https://example.sk/o-nas"])


def test_marketing_urls_filtered_out():
    """Marketing junk (vianocny-dar, firemny-den, bonusova-karta, …) sa vyhadzuje."""
    c = _crawler()
    urls = [
        "https://example.sk/vianocny-dar",
        "https://example.sk/firemny-den",
        "https://example.sk/bonusova-karta",
        "https://example.sk/ocenenie-bozp",
        "https://example.sk/akcia-leto",
        "https://example.sk/vernostny-program",
        "https://example.sk/kontakt",  # toto preziva
        "https://example.sk/produkty",  # toto preziva
    ]
    out = c._filter_urls(urls, "https://example.sk/")
    assert sorted(out) == sorted(["https://example.sk/kontakt", "https://example.sk/produkty"])


# ============================================================ manual /kontakt probe
@pytest.mark.asyncio
async def test_manual_kontakt_probe_when_missing():
    """Ak /kontakt chyba v zozname po crawli, HEAD probe ho ma pridat ako TIER_0."""
    c = HDSCrawler()

    async def fake_sitemap(self, base_url):  # noqa: ARG001
        return ["https://example.sk/produkty", "https://example.sk/o-nas"]  # ziadny /kontakt

    async def fake_homepage(self, base_url):  # noqa: ARG001
        return []

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.url = "https://example.sk/kontakt"

    probe_mock = AsyncMock(return_value=fake_resp)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        head = probe_mock

    with patch.object(HDSCrawler, "_try_sitemap", fake_sitemap), \
         patch.object(HDSCrawler, "_crawl_homepage_links", fake_homepage), \
         patch("app.core.extractors.hds_v3.crawler.httpx.AsyncClient", FakeClient):
        result = await c.discover("https://example.sk/")

    assert result.success is True
    # /kontakt musi byt v zozname, TIER_0, manual_seed
    kontakts = [p for p in result.pages if "/kontakt" in p.url.lower()]
    assert len(kontakts) == 1
    assert kontakts[0].priority is PagePriority.TIER_0_ESSENTIAL
    assert kontakts[0].discovered_via == "manual_seed"
    # Probe bol skutocne zavolany
    probe_mock.assert_awaited()


@pytest.mark.asyncio
async def test_manual_kontakt_probe_not_called_when_present():
    """Ak /kontakt UZ je v zozname, manualny probe sa nesmie volat (saving network)."""
    c = HDSCrawler()

    async def fake_sitemap(self, base_url):  # noqa: ARG001
        return ["https://example.sk/kontakt", "https://example.sk/produkty"]

    async def fake_homepage(self, base_url):  # noqa: ARG001
        return []

    probe_called = AsyncMock()

    with patch.object(HDSCrawler, "_try_sitemap", fake_sitemap), \
         patch.object(HDSCrawler, "_crawl_homepage_links", fake_homepage), \
         patch.object(HDSCrawler, "_probe_kontakt", probe_called):
        result = await c.discover("https://example.sk/")

    assert result.success is True
    probe_called.assert_not_called()
