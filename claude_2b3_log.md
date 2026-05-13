# Phase 2B-3 — Elementor Flip Box expansion

**Status:** DONE ✅
**Date:** 2026-05-13
**Trigger:** E2E test on skakaciehradyorava.sk — 14 products detected, only 2 had prices (regression: previously 3). Root cause: Elementor Flip Box widgets are not Swiper/Slick/Owl nor CSS overflow-x carousels, so the existing UI Expansion Layer skipped them entirely. Back layer of each flip box (containing prices + details) is rotated 180° via CSS 3D transform and stays invisible to both the screenshot pipeline and the HTML scraper.

## What changed

**Modified files:** `app/core/browser.py` (3 small in-place edits + 1 new method).

### 1. `_detect_ui_patterns` — JS evaluate
- Added `flip_box_count = document.querySelectorAll('.elementor-flip-box').length`.
- Added `flip_box` (bool) and `flip_box_count` (int) to the returned object.
- Mirrored the new `'flip_box': False` key in the Python `except` defensive defaults.

### 2. New method `_expand_flip_boxes(page) -> int`
Pure CSS unflip — does NOT rewrite DOM, only sets inline styles:
- `.elementor-flip-box` parent: `transformStyle: flat`, `perspective: none` (kills the 3D context).
- `.elementor-flip-box__layer--front`: `transform: none`, `position: relative`, opacity 1 (image stays visible above).
- `.elementor-flip-box__layer--back`: `transform: none`, `position: relative` (not absolute), `display: block`, `height: auto`, `minHeight: 150px`, `opacity/visibility: visible` — back layer now stacks below the front in normal flow, exposing price + bullet list to both screenshot and DOM-scrape.
- `.elementor-flip-box__layer__inner`, `__overlay`, `__description`: force opacity/visibility/display visible.

Returns count of `.elementor-flip-box` parents modified. Defensive: any JS exception returns 0.

### 3. `_apply_ui_expansion` — integration
- Added `'flip_boxes_expanded': 0` to the `applied` dict.
- New step **2.5** between accordion expansion and the carousel chain:
  ```python
  if allow_accordion and patterns.get('flip_box'):
      applied['flip_boxes_expanded'] = await self._expand_flip_boxes(page)
  ```
- Gated behind `allow_accordion` (complexity < 0.7) rather than `allow_dom_mutation` (< 0.3), because this is pure inline-style application — no removal/rebuild of nodes, no destructive rewrite.

## Tests

**Added:** `tests/test_flip_box.py` (3 tests).
1. `test_detect_includes_flip_box` — patterns dict surfaces the new `flip_box` key.
2. `test_flip_box_triggers_expansion` — LOW complexity + `flip_box: True` → `_expand_flip_boxes` called once, result reflects modified count.
3. `test_high_complexity_skips_flip_box` — complexity 0.85 keeps the gate closed; the method is NOT called.

**Test run:** 130 passed (127 baseline + 3 new), ~35s.

## Verification
- `SYNTAX OK` (ast parse).
- `IMPORTS OK, routes: 20`.
- `tests/test_flip_box.py`: 3/3 pass.
- Full suite: `130 passed in 35.07s`.

## Untouched
- `render_page()` / `render_page_tiled()` outer structure — no edits.
- `_apply_ui_expansion` carousel chain, complexity logic, safety gates — unchanged.
- `vision.py`, `knowledge_hub.py`, `extractors/**`, Phase 2A engine (`app/core/ingest_v2/**`).
- `requirements.txt`, `migrations/**`, `models/**`.
- No git commit/push performed (per instruction).

## Expected impact
- skakaciehradyorava.sk: 14 products with 14 prices (vs 2 before), back-layer bullet details ("Kapacita / Rozmery / Výška / Odporúčaný vek") now visible to both the HTML extractor and the screenshot pipeline.
- No latency cost for non-flip-box sites (gate skips when `flip_box: False`).
- For flip-box pages: +1 evaluate call (~50ms), no extra wait — already inside the 600ms final settle.

## Next
- Re-run E2E on skakaciehradyorava.sk and verify price coverage.
- Monitor any Elementor-heavy sites for unintended layout reflow (the front+back stacking changes page height; screenshot pipeline re-measures `scrollHeight` after expansion, so segment count adapts automatically).
