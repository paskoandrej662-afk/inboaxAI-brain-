# Claude 2B-2 Log — UI Expansion Layer

## Cieľ
Pridať UI Expansion Layer do `render_page_tiled` s FIXED execution chain + complexity rules ako safety gate. Riešime problém: tiled vision detegoval 14 produktov skákačiek (mená), ale 11/14 nemalo cenu/atribúty kvôli HORIZONTÁLNEMU CAROUSELU v každej karte hradu.

## Architektúra — FIXED EXECUTION CHAIN
Pipeline (vždy rovnaké poradie):
1. Detect UI patterns (`_detect_ui_patterns`)
2. Lazy load trigger (`_trigger_lazy_load`) — VŽDY safe
3. Accordion expansion (`_expand_accordions_strict`) — IBA ak complexity < 0.7
4. Carousel chain (ordered fallback):
   a) Swiper JS API (`_try_swiper_js_api`) — ak je inštancia v DOM property `el.swiper`
   b) Native scroll simulation (`_cycle_native_scroll`) — scrollLeft 0→33→66→100→0 pre CSS overflow-x
   c) DOM rewrite fallback (`_dom_fallback_expand_carousels`) — IBA ak (a) ani (b) nezachytili AND complexity < 0.3
5. Complexity = SAFETY GATE only:
   - `< 0.3` → `allow_dom_mutation = True` (DOM fallback povolený)
   - `< 0.7` → carousel handling + accordion (bez DOM fallbacku)
   - `>= 0.7` → IBA lazy load (zvyšok vision-only)

Complexity score je odvodený zo SPA/framework signálov + `<canvas>` počet. Swiper/Slick/Owl počet NIE je penalizovaný — e-commerce weby s mnohými carouselmi sú PRIMÁRNY use-case pre expansion, nie exclusion.

## Implementácia
### MODIFIKOVANÉ: `app/core/browser.py`
- Nové metódy na konci triedy `BrowserPool`:
  - `_detect_ui_patterns(page) -> dict` — JS detekcia: carousel_native, carousel_js, swiper_api_available, accordion, lazy_load, complexity_score. Defensive (na error → safe defaults, complexity 0.5).
  - `_trigger_lazy_load(page) -> None` — scroll bottom → wait → top → wait. Žiadne slučky.
  - `_expand_accordions_strict(page) -> int` — `<details>.open=true` + `[role=button][aria-expanded=false].click()` so skipom nav/menu/modal/dialog/dropdown, cap 20 klikov.
  - `_try_swiper_js_api(page) -> int` — cykluje cez `el.swiper.slideTo(i,0)` pre všetky slidy, vráti späť na 0. Vráti počet odcyklovaných inštancií.
  - `_cycle_native_scroll(page) -> int` — pre CSS overflow-x: scrollLeft 0→33%→66%→100%→0. Žiadna DOM mutácia.
  - `_dom_fallback_expand_carousels(page) -> int` — LAST-RESORT: nastaví inline styly na `.swiper-wrapper/.swiper-slide`, `.slick-track/.slick-list/.slick-slide`, `.owl-stage/.owl-item` (transform none, flex-wrap, opacity/visibility, min-width 200px, remove `.slick-cloned`). Vráti počet upravených slide elementov.
  - `_apply_ui_expansion(page, url) -> dict` — orchestrátor fixed chainu, vráti dict s `complexity`, `allow_dom_mutation`, `lazy_triggered`, `accordions_expanded`, `swiper_api_cycled`, `native_scroll_cycled`, `dom_fallback_modified`.
- `render_page_tiled`: nahradené staré inline `page.evaluate` bloky (lazy load skroll-loop, AOS animácie, collapsibles, wait 800) za:
  - `# === UI EXPANSION LAYER ===` blok: `await self._apply_ui_expansion(page, url)` (best-effort, try/except → warning + pokračuje)
  - Force-show animácií (AOS / scroll-reveal) — zachovaný
  - `wait_for_timeout(800)`
  - Re-measure `scrollHeight` PO expansion (carousely môžu zmeniť výšku)
  - `# === END UI EXPANSION LAYER ===`
  - Cookie dismiss banner blok zostal NETKNUTÝ (pred UI expansion layerom).

### PRIDANÉ: `tests/test_ui_expansion.py` (8 testov)
- `test_detect_returns_required_keys`
- `test_detect_defensive` — evaluate raise → complexity 0.5, carousel_native False
- `test_low_complexity_js_carousel_no_api_triggers_dom_fallback`
- `test_low_complexity_native_only_carousel_uses_scroll` — scroll fungoval → DOM fallback NEvolaný
- `test_low_native_carousel_no_scroll_triggers_dom_fallback` — BUG #1 fix: DOM fallback aj keď carousel_js=False
- `test_medium_complexity_no_dom_fallback` — gate blokuje DOM fallback
- `test_high_complexity_only_lazy` — iba lazy load, všetko ostatné assert_not_called
- `test_swiper_api_success_skips_dom_fallback`

### BUG FIXY (oproti pôvodnému návrhu)
- **BUG #1**: DOM fallback sa spúšťa pre AKÝKOĽVEK typ carouselu (JS aj native), keď prior steps nič neodcyklovali — nielen keď `carousel_js=True`.
- **BUG #2**: Complexity score už NEpenalizuje carousel-heavy weby (odstránený `swiper_count > 3` príspevok) — e-commerce s mnohými carouselmi je náš cieľ.

### NETKNUTÉ
`render_page()`, štruktúra `render_page_tiled` (len pridaný expansion krok pred screenshotmi), `vision.py`, `knowledge_hub.py`, `extractors/**`, Phase 2A engine (`app/core/ingest_v2/**` — vrátane `renderer.py` ktorý má vlastné `_force_lazy_load/_expand_collapsibles/_force_animations`), `requirements.txt`, `migrations/**`, `models/**`.

## Verifikácia
- `python -c "import ast; ..."` → SYNTAX OK
- `PYTHONPATH=. python -c "from app.core.browser import BrowserPool; from app.main import app; ..."` → IMPORTS OK, routes: 20
- `pytest tests/test_ui_expansion.py -v` → 8 passed
- `pytest tests/ -q` → **127 passed** (119 baseline + 8 nových) v ~40s

## Stav: DONE ✅
Bez git commitu (na želanie). Ďalej: deploy + E2E test na reálnom carousel-heavy webe (skákačky hrady), sledovať či DOM fallback zachytí 11 chýbajúcich produktov; merať latenciu (carousel cyklovanie pridáva ~1-3s per page).
