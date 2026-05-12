# PROMPT 2A-2 — Renderer + Crawler + Raw extraction — pracovny log

Start: 2026-05-12 — Status: **DONE** ✅

## Recon

- Baseline testov v repe: **64 collected** (pytest tests/ --co -q).
- pytest 8.3.3, pytest-asyncio 0.24.0 (strict mode → testy musia mat `@pytest.mark.asyncio`).
- lxml 6.1.0, beautifulsoup4 4.12.3, playwright 1.48.0 (+ Chromium funkcny), pillow 10.4.0, httpx 0.27.2.
- Network k example.com funguje → renderer testy bezia naozaj online.
- `app/core/ingest_v2/types.py` ma: HeadingItem, LinkItem, ImageCandidate(src, resolved_url, srcset, alt, title, width, height, selector, nearby_text, section_heading, parent_link, is_lazy, source_attr, candidate_role), ContactPatterns(emails, phones, ico, dic, ic_dph, iban, social_links, map_links, addresses_candidates), RawPageData.
- `renderer.py` API (uz hotove, NEZMENENE):
  - `RenderResult` = dataclass: url, final_url, http_status, render_status, render_ms, html, visible_text, title, dom_size, text_length, screenshot_png, error_message.
  - `RendererV2(viewport_w, viewport_h, timeout_ms, locale, user_agent)` → `await start()`, `await render_page(url, take_screenshot=False, screenshot_full_page=False)`, `await close()`.
  - `render_status in ('success','timeout','blocked','error')`. Nikdy neraisne von z `render_page`.
- `crawler.py` API (uz hotove, NEZMENENE):
  - `DiscoveredPage` = dataclass: url, discovery_method, depth, parent_url, priority_score.
  - `CrawlerV2(renderer=None, max_pages=12, max_depth=2, user_agent=...)`.
  - `await discover_pages(start_url)`; sync helpery `_normalize_url(url, base) -> str|None`, `_priority_for_url(url) -> float`, `_is_disallowed(url, disallow)`.
  - `_priority_for_url`: homepage 0.85, HIGH patterny 0.75–1.0 (kontakt 1.0, cennik/sluzby/produkty/o-nas 0.95), LOW patterny 0.0–0.15 (gdpr/cookies/privacy 0.05, binarne pripony 0.0), default 0.40.
- `app/core/browser.py` (Phase 1A) — NEDOTYKAT, ostava pre legacy crawler.
- WARNING check: `renderer.py` NEimportuje `raw_extraction.py` → ziadny chybajuci import, OK.

## Co bolo dorobene v tejto session

1. **`app/core/ingest_v2/raw_extraction.py`** (NOVY) — pure funkcie, zero state, zero LLM, zero network. Kazda funkcia je defensive (nikdy neraisne, pri chybe vracia `[]`/`{}`/`""`, log debug). Predkompilovane regexy (email, SK/CZ telefon, IČO, DIČ, IČ DPH, IBAN, adresa).
   - Funkcie: `extract_json_ld` (+ `@graph` flatten), `extract_microdata`, `extract_meta`, `extract_open_graph`, `extract_headings`, `extract_links` (internal/external + dedup), `extract_images` (src/srcset/data-src/data-lazy-src/data-original, skip `data:`, section heading, icon heuristika), `extract_tables` (limity 20×100×30), `extract_lists` (30×50), `extract_forms`, `extract_pdfs`, `extract_social_links`, `extract_visible_text` (strip script/style/noscript), `extract_contact_patterns`.
2. **`tests/test_v2_raw_extraction.py`** (NOVY) — 21 offline testov.
3. **`tests/test_v2_crawler.py`** (NOVY) — 13 offline testov (`_normalize_url`, `_priority_for_url`; +3 navyse oproti promptu: mailto→None, strip tracking params, homepage priority).
4. **`tests/test_v2_renderer.py`** (NOVY) — 2 testy (lifecycle online/offline-graceful, nonexistent URL graceful). `@pytest.mark.asyncio`.

## Verifikacia (vsetky kroky PASS)

| Krok | Vysledok |
|------|----------|
| 1. Syntax check (ast.parse 6 suborov) | `SYNTAX OK` |
| 2. Imports (renderer/crawler/raw_extraction) | `IMPORTS OK` |
| 3. Offline testy (raw_extraction + crawler) | **34 passed** |
| 4. Renderer testy (network) | **2 passed** (online, real example.com) |
| 5. ALL tests regression | **100 passed** (64 baseline + 36 nove) |

Nove testy: 21 (raw_extraction) + 13 (crawler) + 2 (renderer) = **36**. Total **100**.

## Pravidla dodrzane

- Defensive: ziadna funkcia v `raw_extraction.py` neraisne (try/except all-around).
- Renderer/crawler testy pisane podla SKUTOCNEHO API (grep recon), nie podla navrhu v prompte.
- Type hints vsade, `from __future__ import annotations`.
- Slovencina v komentaroch a docstringoch.
- Phase 1 + Phase 2A-1 NEDOTKNUTE, `renderer.py` a `crawler.py` NEZMENENE (len pridane nove subory).
- Ziadny git commit/push.
