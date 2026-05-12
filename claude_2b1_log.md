# Phase 2B-1 — Tiled (multi-viewport) screenshot pipeline pre Phase 1 vision

CIEL: pridať tiled rendering + tiled vision pre dlhé stránky (>3000 px), zachovať single-screenshot
cestu pre krátke stránky, reuse merger.py + verification.py.

SCOPE:
- MOD: app/core/browser.py (render_page_tiled + TiledPageResult)
- MOD: app/core/extractors/vision.py (extract_page_with_tiled_vision + _extract_one_tile)
- MOD: app/core/knowledge_hub.py (_ingest_url_vision — short vs tiled decision)
- NEW: tests/test_tiled_vision.py (4 testy)

---

## KROK 0 — Záloha pôvodného stavu

### Pôvodná _ingest_url_vision (knowledge_hub.py, riadky 283-449 — časť ktorá sa mení)

```python
async def _ingest_url_vision(
    company_id: uuid.UUID, url: str, job_id: str, max_pages: int = 30
) -> IngestResult:
    """Vision-based ingest using BrowserPool + html_structured + classifier + vision + verification + merger."""
    from app.core.browser import BrowserPool
    from app.core.extractors.classifier import classify_page
    from app.core.extractors.html_structured import (
        extract_contact_patterns, extract_image_refs, extract_jsonld_business,
        extract_jsonld_faq, extract_jsonld_products,
    )
    from app.core.extractors.merger import (merge_facts, merge_faqs, merge_images, merge_products)
    from app.core.extractors.verification import verify_fact, verify_product
    from app.core.extractors.vision import extract_page_with_vision

    result = IngestResult()
    try:
        await _update_progress(job_id, 0, "running")
        browser = BrowserPool()
        try:
            await browser.start()
        except Exception as e:
            return await _ingest_url_legacy(company_id, url, job_id, max_pages)
        try:
            pages = await _discover_pages_hybrid(url, browser, max_pages)
        except Exception as e:
            pages = [url]
        if not pages: pages = [url]
        total = len(pages)
        ...
        try:
            for i, page_url in enumerate(pages):
                try:
                    await _update_progress(job_id, int((i / total) * 90))
                    rendered = await browser.render_page(page_url, take_screenshot=True)
                    if rendered.error: ... continue
                    result.pages_visited += 1
                    content = extract_text(rendered.html, rendered.final_url)
                    raw_text = content.text or ""
                    title = content.title or ""
                    section_hint = content.section
                    page_type, value_score = await classify_page(page_url, title, raw_text[:600])
                    if page_type == "other_low_value" and value_score < 0.2: continue
                    html_products = extract_jsonld_products(...)
                    html_faqs = extract_jsonld_faq(...)
                    html_business = extract_jsonld_business(...) + extract_contact_patterns(...)
                    html_images = extract_image_refs(...)
                    use_vision = (rendered.screenshot_png is not None and page_type in {...}
                                  and total_cost_usd < settings.INGEST_LLM_COST_CAP_USD)
                    vp = vf = vfq = vi = []; summary = ""; cost = 0.0
                    if use_vision:
                        vp, vf, vfq, vi, summary, cost = await extract_page_with_vision(rendered, page_type, raw_text)
                        total_cost_usd += cost
                        vp = [verify_product(p, raw_text) for p in vp]
                        vf = [verify_fact(f, raw_text) for f in vf if verify_fact(f, raw_text) is not None]
                    page_products = merge_products(html_products, vp)
                    page_facts = merge_facts(html_business, vf)
                    page_faqs = merge_faqs(html_faqs, vfq)
                    all_products.extend(page_products); all_facts.extend(page_facts); all_faqs.extend(page_faqs)
                    all_images.extend(html_images + vi)
                    if raw_text: all_chunks_text_tuples.append((raw_text, rendered.final_url, section_hint))
                    result.pages_succeeded += 1
                except Exception as e: ...
        finally:
            await browser.close()
        # cross-page dedupe + persistence (NEMENIT)
```

Pôvodná render_page() v browser.py + extract_page_with_vision() vo vision.py — ostávajú NETKNUTÉ.

---

## PROGRESS

- [x] KROK 1: browser.py — `TiledPageResult` dataclass (po `RenderedPage`) + `render_page_tiled()` metóda
      (na konci `BrowserPool`, po `discover_links`). Adaptívne segmenty <3000=1 / 3000-7000=3 /
      7000-12000=5 / >12000=7. Overlap 22% (`segment_height * 0.22`). Viewport 1280×2200. Cookie
      dismiss + lazy-load scroll + AOS/scroll-reveal force-show + collapsibles expand. Plne defenzívne
      (try/except všade, na chybe vráti `error` + prázdne segmenty). `render_page()` NETKNUTÁ.
- [x] KROK 2: vision.py — pridané importy hore (`asyncio`, `TiledPageResult`, `merge_*`); na konci
      `_extract_one_tile()` (1 tile → products/facts/faqs/images/summary/cost, `use_cache=True`) +
      `extract_page_with_tiled_vision()` (parallel `asyncio.gather` so `Semaphore(3)`,
      `return_exceptions=True`, merge cez tiles existujúcim mergerom). `extract_page_with_vision()` NETKNUTÁ.
- [x] KROK 3: knowledge_hub.py `_ingest_url_vision` — lazy import doplnený o `extract_page_with_tiled_vision`.
      Loop body prepísaný: (1) `render_page(take_screenshot=False)` → HTML/text/klasifikácia,
      (2) HTML structured, (3) `use_vision` decision (bez `screenshot_png` checku),
      (4) `render_page_tiled()` → ak segments → `extract_page_with_tiled_vision`, else fallback
      `render_page(take_screenshot=True)` + `extract_page_with_vision`. Pôvodný dvojitý render+extract
      ODSTRÁNENÝ. `verify_product` + `verify_fact` ostávajú POVINNÉ. `section_hint` zachovaný v chunk tuple.
      Cross-page merge, `finally: browser.close()`, persist — NETKNUTÉ.
- [x] KROK 4: tests/test_tiled_vision.py — 4 testy: real browser lifecycle (example.com → 1 segment),
      empty segments → empty, merge across 2 tiles (Tiger dedupe → 3 unikátne), failed tile → ostatné OK.
- [x] KROK 5: verifikácia — SYNTAX OK; IMPORTS OK (routes: 20); test_tiled_vision.py 4 passed;
      full regression 119 passed (115 baseline + 4 nové).

## VÝSLEDOK: DONE ✅
- Modifikované: app/core/browser.py, app/core/extractors/vision.py, app/core/knowledge_hub.py
- Pridané: tests/test_tiled_vision.py (4 testy)
- Žiadne nové dependencies, migrations/models/ingest_v2 nedotknuté.
- Git: bez commitu (na želanie).
