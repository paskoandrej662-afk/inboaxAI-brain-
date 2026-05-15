# 2B-8: HDS-Lite — Hybrid DOM-Segmentation Engine

## Stav
DONE ✅ — pure-Python deterministic extractor PRED existujucim Sonnet vision pipeline.
Base commit: 6d68a3e (post-2B-7, 137 testov passed).

## Trigger
Phase 2B-7 dosiahla 11/14 produktov a 7-9/14 s cenami (~64% completeness) ale
7 fáz iterácie pristup "pure LLM extraction" nedosiahlo viac ako 64% na
skakaciehradyorava.sk. Rozhodnutie: prejst na DETERMINISTIC pattern-based
engine. Sonnet poskytne "seeds", Python deterministicky harvestuje cely cluster.
Cieľ: 95%+ accuracy, 0% halucinacii.

## Architektura — 6 faz

| Phase | Modul | Co robi |
|------:|-------|---------|
| 1 | vision_seed.py | Sonnet vision → 3 sample produkty (name, price) zo screenshotu |
| 2 | lca_finder.py | BS4 najde seed.name + seed.price v DOM, walk-up → Lowest Common Ancestor (musi byt container: div/section/article/li) |
| 3 | cluster_detector.py | Z LCA tag+class_list → najde siblings v parent containeri (rovnaky tag + 50% jaccard zhoda) |
| 4 | field_extractor.py | Pure-Python regex/heuristikami: name (h1-h5), price_eur (regex `(\d+[.,]?\d*)€`, vyber max), price_text (mixed/soft), attributes (kapacita, rozmery, vyska, vek) |
| 5 | confidence.py | score_card (+0.5 name, +0.3 price, +0.2 recurring) + Playwright is_visible + dedup |
| 6 | arbitration.py | Sonnet review (text only call_sonnet) iba pre cards 0.4-0.7 confidence — vrat null ak nie je produkt |

Orchestrator: `engine.py::run_hds_extraction()` — fail-safe per phase,
pri kazdej kritickej chybe vrat `ExtractionResult(success=False, fallback_reason='...')`.

## Modifikované / vytvorené

### Nové súbory (10):
- `app/core/extractors/hds/__init__.py` (re-export Seed/ProductCard/ExtractionResult)
- `app/core/extractors/hds/types.py` (3 dataclassy)
- `app/core/extractors/hds/vision_seed.py` (Phase 1 — Sonnet seed generator s vlastnou tool schema `report_seed_products`)
- `app/core/extractors/hds/lca_finder.py` (Phase 2 — `_normalize` s NFKC + diacritic strip, `_find_deepest_containing`, `_common_ancestor`, `find_lca` walks-up dokym container tag)
- `app/core/extractors/hds/cluster_detector.py` (Phase 3 — `_jaccard` + `find_siblings` s defensive fallback na `[lca]`)
- `app/core/extractors/hds/field_extractor.py` (Phase 4 — `_extract_name` heading-first + font-size fallback, `_extract_price_eur` picks max, `_extract_price_text` soft-pricing keywords, `_extract_attributes` 4 regex patterns)
- `app/core/extractors/hds/confidence.py` (Phase 5 — `score_card` + async `filter_visible` s page=None best-effort visibility + signature-based dedup)
- `app/core/extractors/hds/arbitration.py` (Phase 6 — `arbitrate` len pre 0.4≤conf≤0.7, tool schema `arbitrate_card`)
- `app/core/extractors/hds/engine.py` (`run_hds_extraction` orchestrator s try/except per phase)

### Test súbory + fixture (5):
- `tests/test_hds_lca.py` (9 testov — diacritics, price fragment, deepest match, common ancestor, container walk-up, missing name handling)
- `tests/test_hds_cluster.py` (6 testov — jaccard, tag+class matching, non-matching tag skip, partial match, None handling)
- `tests/test_hds_field_extraction.py` (12 testov — heading-first, font-size fallback, price max, decimal, soft-price, kapacita/rozmery, mixed pricing, empty card)
- `tests/test_hds_engine.py` (6 testov — fixture load, deterministic pipeline pre 14 kariet, mocked seeds full pipeline, no_seeds fallback, no_lca fallback, empty HTML)
- `tests/fixtures/skakacky_homepage.html` (~3.5 KB statickej fixture s 14 elementor-flip-box kartami)

### Modifikované:
- `app/core/knowledge_hub.py::_ingest_url_vision` — pridana HDS primary cesta PRED `extract_page_with_tiled_vision`. Najde sa middle screenshot z `tiled.segments`, zavola `run_hds_extraction(html=tiled.html, screenshot_bytes=middle, page=None, page_url=...)`. Ak `hds_result.success and len(cards) >= 3`, konvert na `ExtractedProduct(source_type="hds")` a preskoc tiled vision call pre products. Ak HDS zlyha → existujuca tiled vision logika bezi nedotknuta. Cost z HDS sa pripocitava do `total_cost_usd`. Logger emituje line s success/cards/seeds/lcas/candidates/cost/reason metrikami.

## NEPOZMENENÉ
- `vision.py`, `merger.py`, `verification.py` — HDS card→ExtractedProduct konverzia v knowledge_hub, kompatibilne s merger/verification.
- `browser.py` (flip box code intact).
- Phase 2A engine (`app/core/ingest_v2/**`).
- `requirements.txt` — `beautifulsoup4==4.12.3` uz bol pritomny.
- migrations/**, models/**.

## Pravidla rešpektované
- HDS vracia ExtractedProduct (existujuci typ) → kompatibilne s merger/verification/DB.
- Fail-safe: kazda phase v try/except, kriticka chyba → `success=False, fallback_reason`.
- Pure Python kde mozno, Sonnet len Phase 1 (vision seed) + Phase 6 (arbitration).
- `unicodedata.normalize('NFKC')` + diacritic strip (NFD + filter combining) na vsetky string comparisons — robustny match medzi vision OCR a DOM textom.
- Defenzivne: try/except per modul, nikdy neraisne kritickú chybu.
- Type hints + slovencina v komentaroch.

## Testy
- 170 passed (137 baseline + 33 nové HDS testy), ~47s.
- SYNTAX OK na vsetkych 14 zmeneneych/novych suboroch.
- APP OK, routes: 20 (nemenené).

## Pozn. k integracii
- `page=None` v knowledge_hub HDS calle — `render_page_tiled` open/close vlastnu Playwright Page, ktora nie je v scope volajuceho. Visibility check teda funguje best-effort (vsetky karty povazovane za visible). Pri buducej integracii do `render_page_tiled` mozeme nechat Playwright page open a posunut do HDS.
- HDS sa skusi LEN ked `use_vision == True` a `tiled.segments` su naplnene — t.j. iba na vision-worthy stranky kde uz aj tak by sme vision volali.
- Diakritika v `_normalize` je strippeNV (NFD + combining-mark filter) — Sonnet vision OCR moze vratit "Skákací" alebo "Skakaci"; oboje matchne.

## Dalej (out of 2B-8 scope)
- Live test na skakaciehradyorava.sk — ocakavanie 14/14 produktov.
- Threshold-tuning: ak HDS minie >2 seeds bez LCA, mozno vyssia tolerance v _find_deepest_containing.
- Page-level vision pre facts/faqs/images stale bezi v plnom rozsahu cez tiled (HDS rieši IBA products).
