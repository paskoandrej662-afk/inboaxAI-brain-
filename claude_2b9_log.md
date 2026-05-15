# 2B-9 — HDS-v3 Crawler (Commit 1 zo 4)

## Cieľ
Honza-style crawler ktorý nájde 5–30 podstránok webu pre HDS-v3 pipeline
(Gemini Flash + Google Search Grounding nahradí Sonnet vision).

## Base commit
6d68a3e (post-2B-8, 170 testov v repe = 137 baseline + 33 HDS-Lite).

## Scope
- NEW: `app/core/extractors/hds_v3/__init__.py` (re-export)
- NEW: `app/core/extractors/hds_v3/types.py` (DiscoveredPage, CrawlResult, PagePriority)
- NEW: `app/core/extractors/hds_v3/crawler.py` (HDSCrawler)
- NEW: `tests/test_hds_v3_crawler.py` (17 testov)
- NEW: `scripts/test_hds_v3_crawler.py` (manual run)

## Pravidla rešpektované
- BrowserPool z `app/core/browser.py` znovupoužitý (`discover_links()`); žiaden vlastný browser
- Žiadne nové dependencies — `httpx` (už v requirements) + stdlib `xml.etree.ElementTree`
- Defenzívne — každá metóda catch errors, log warning, vráti empty result
- NETKNUTÉ: vision.py, knowledge_hub.py, Phase 2A engine, HDS-Lite, requirements.txt, migrations/**, models/**

## API

```python
from app.core.extractors.hds_v3 import HDSCrawler, CrawlResult, DiscoveredPage, PagePriority
crawler = HDSCrawler()
result: CrawlResult = await crawler.discover("https://example.sk/")
# result.pages : list[DiscoveredPage] — zoradene TIER_1 → TIER_4
# result.sitemap_found : True ak sitemap.xml fungoval
# result.duration_sec : meraný čas
```

## Implementácia

### types.py
- `PagePriority` enum (TIER_1_CRITICAL=1 → TIER_4_OTHER=4)
- `DiscoveredPage(url, priority, discovered_via)` — kde via je `"sitemap"|"homepage_link"|"manual_seed"`
- `CrawlResult(success, base_url, pages, total_discovered, sitemap_found, error, duration_sec)`

### crawler.py — HDSCrawler
- `MAX_PAGES = 30`, `SITEMAP_TIMEOUT_SEC = 10`, `RENDER_TIMEOUT_SEC = 30`
- Pattern listy (TIER_1, TIER_2, TIER_3, EXCLUDED) ako class attribútes pre čitateľnosť

**Pipeline `discover()`:**
1. `_normalize_base_url` — pridá scheme, lowercase host, strip fragment/query; vráti None pri invalid
2. `_try_sitemap` — skúsi `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap1.xml` cez httpx; rekurzia 1 level pre `sitemapindex` (cap 10 sub-sitemap)
3. `_parse_sitemap_xml` — `xml.etree.ElementTree` s namespace `{...sitemap/0.9}`, fallback bez NS, fallback regex pre extra-malformed
4. Ak sitemap prázdny: `_crawl_homepage_links` cez `BrowserPool.discover_links()` (start/close v finally)
5. `_filter_urls_with_via` — same-domain (www. canonicalization), strip excluded patterns, strip tracking params (utm_*, fbclid, gclid, …), dedupe lower-key, host canonicalizovaný na base host
6. `_prioritize` — homepage = TIER_1, pattern match na path; stable sort TIER_1 → TIER_4
7. Cap `MAX_PAGES = 30`

**Edge case fixy počas implementácie:**
- `lstrip("www.")` nahradené `if host.startswith("www."): host = host[4:]` (correctness na exotic hosts)
- Dedupe pôvodne zachovával rôzne `netloc` formy (www. vs nie) — pridaná canonicalization na base netloc → `https://example.sk/x` a `https://www.example.sk/x` kolabujú na jeden URL podľa base

## Verification

```
$ python -c "import ast; [ast.parse(open(f).read()) for f in [...]]; print('SYNTAX OK')"
SYNTAX OK

$ PYTHONPATH=. python -c "from app.core.extractors.hds_v3.crawler import HDSCrawler; from app.main import app; print('APP OK, routes:', len(app.routes))"
APP OK, routes: 20

$ PYTHONPATH=. pytest tests/test_hds_v3_crawler.py -v
17 passed in 0.28s

$ PYTHONPATH=. pytest tests/ -q
187 passed in 11.82s  # 170 baseline + 17 nové
```

## Live discovery výsledky

### Test 1 — skakaciehradyorava.sk (3.06 s)
- Sitemap found: **True** (WordPress sitemap)
- Total: **7 URL**
- TIER_1: homepage (1)
- TIER_2: /najcastejsie-otazky (1)
- TIER_3: /galeria (1)
- TIER_4: /2025/03/10/ahoj-svet, /ukazkova-stranka, /category/nezaradene, /author/* (4)
- Pozn: web má len ~7 reálnych podstránok, nie chyba crawlera

### Test 2 — www.elspolno.sk (~5 s)
- Sitemap found: **False** (žiadny sitemap.xml)
- Fallback na Playwright homepage links
- Total: **30 URL** (cap dosiahnutý)
- TIER_1: 12 (/sluzby + sub-služby ako /sluzby/asfaltovanie, /sluzby/montaze-plynovodov, /sluzby/zemne-a-vykopove-prace, …)
- TIER_2: 1 (/o-nas)
- TIER_3: 1 (/projekty)
- TIER_4: 16 (kariera, bonus, projekty stránky)
- Dedupe canonicalization fixol www. duplicity (`www.elspolno.sk/pracuj-v-elspol` a `elspolno.sk/pracuj-v-elspol` → 1 URL)

### Test 3 — www.klimcik.sk (1.87 s)
- Sitemap found: **True**
- Total: **7 URL**
- TIER_1: homepage + /kontakt (2)
- TIER_2: /o-nas (1)
- TIER_4: /nakladna-doprava, /autobusova-doprava, /servis, /cerpacia-stanica (4)
- Pozn: tier_4 sa dá vylepšiť patternami pre "/doprava", "/servis" — odložené, nie kritické pre commit 1

## Tests breakdown (17 nové)

Priority/classify (5):
- test_priority_homepage_is_tier_1
- test_priority_kontakt_is_tier_1
- test_priority_o_nas_is_tier_2
- test_priority_blog_is_tier_3
- test_priority_unknown_is_tier_4

Filtering (5):
- test_filter_removes_external_urls
- test_filter_removes_pdf_jpg
- test_filter_removes_admin_login
- test_dedupe_normalizes_trailing_slash
- test_filter_strips_tracking_params

Sitemap parser (3):
- test_sitemap_parser_handles_basic_xml
- test_sitemap_parser_handles_sitemap_index
- test_sitemap_parser_malformed_returns_empty

Discover end-to-end mocked (4):
- test_limit_max_pages_30
- test_discover_uses_sitemap_when_available
- test_discover_falls_back_to_homepage_links
- test_discover_invalid_url_returns_error

## Git
Bez commitu (na želanie).

## Ďalej (Commit 2)
Gemini integration — pošle `result.pages` cez Gemini Flash + Google Search Grounding,
dostane markdown popis produktov / služieb. Vstup: `CrawlResult`. Výstup: per-page markdown.
