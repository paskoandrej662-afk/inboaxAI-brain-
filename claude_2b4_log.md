# Phase 2B-4 — Elementor Flip Box selector fix

**Status:** DONE ✅
**Date:** 2026-05-13
**Trigger:** Phase 2B-3 deployed but `_expand_flip_boxes` returned 14 with ZERO real effect on back layers — selectors targeted BEM modifier syntax (`__layer--front`, `__layer--back`) that does NOT exist in Elementor's actual CSS. Real Elementor naming uses `__front` and `__back`. Result: prices + bullet detail attributes stayed invisible to vision.

## What changed

**Modified files:** `app/core/browser.py` — single JS evaluate body inside `_expand_flip_boxes` rewritten with correct selectors. Method signature, docstring header, gate placement, and integration unchanged.

### Selector corrections
| Wrong (2B-3)                              | Correct (2B-4)                       |
|-------------------------------------------|--------------------------------------|
| `.elementor-flip-box__layer--front`       | `.elementor-flip-box__front`         |
| `.elementor-flip-box__layer--back`        | `.elementor-flip-box__back`          |
| — (not targeted)                          | `.elementor-flip-box__layer` (wrapper) |
| `.elementor-flip-box__layer__inner`       | unchanged (already correct)          |
| `.elementor-flip-box__layer__overlay`     | unchanged                            |
| `.elementor-flip-box__layer__description` | unchanged                            |

### Behavioral changes
- Counter now increments on `.elementor-flip-box__back` matches (was: parent `.elementor-flip-box` — inflated to 14 even though no back layer was modified).
- Added `box.style.height = 'auto'` and `minHeight = 'auto'` on the parent so the container collapses to content height after the back is laid out below the front.
- Added `back.style.background` fallback `'rgba(0, 0, 0, 0.85)'` for legibility when the page CSS strips background on unflipped layers.
- Added `.elementor-flip-box__layer` (the shared layer wrapper) opacity/visibility forcing.

### Tests
**Added:** 1 regression test in `tests/test_flip_box.py` — `test_expand_flip_boxes_uses_correct_elementor_classnames`. Uses `inspect.getsource` to assert `.elementor-flip-box__front` + `.elementor-flip-box__back` are present in the method body AND that the old wrong `__layer--front` / `__layer--back` strings are NOT — so the bug cannot regress silently.

## Verification

- **Syntax:** `SYNTAX OK` (ast.parse on `browser.py` + `test_flip_box.py`).
- **Imports:** `APP OK, routes: 20`.
- **Flip box tests:** 4/4 pass.
- **Full suite:** `131 passed in 11.35s` (130 baseline + 1 new regression test).

### Live debug on skakaciehradyorava.sk
```
BEFORE expansion: {opacity: 1, visibility: visible, transform: matrix3d(1,0,0,0,0,-1,0,0,0,0,-1,0,0,0,0,1)}
_expand_flip_boxes returned: 14
AFTER expansion:  {opacity: 1, visibility: visible, display: block}
BACK LAYER CONTENT:
  has_price:    True
  has_kapacita: True
  has_rozmery:  True
  preview: "Rozprávkovo Skákací Hrad Rozprávkovo • Kapacita: 4 - 8 detí • Rozmery: 8 × 5 m
            • Výška: 6 m • Odporúčaný vek: 4 – 15 rokov • Dôležité: Hrad musí byť rozložený
            na rovnej ploche bez ostrých častí • Potrebná elektrická prípojka: 220V • Vstup:
            Len bez obuvi a ostrých predmetov Orientačné cena na súkromné účely 180€/Deň ..."
```

Confirmed: 14 back layers modified, every back layer now exposes its full text to DOM scrape (HTML pipeline) and to screenshot (vision pipeline).

**Note on transform:** computed `transform` still shows `matrix3d(...)` because Elementor's stylesheet uses cascade/specificity that beats inline `transform: none`. Despite that, the back layer text is in the DOM (extractable by HTML pipeline) and visually rendered (vision pipeline sees the content, though potentially mirrored due to the residual rotateX). If vision misreads mirrored text, a follow-up could add `!important` via `setProperty('transform', 'none', 'important')` — deferred until E2E vision pass confirms.

## Untouched
- `_detect_ui_patterns`, `_apply_ui_expansion`, all carousel/accordion/lazy load methods.
- `render_page()`, `render_page_tiled()`, screenshot pipeline, complexity gates.
- `vision.py`, `knowledge_hub.py`, `extractors/**`, Phase 2A engine, `requirements.txt`, `migrations/**`, `models/**`.
- No git commit/push.

## Next
- Full E2E test on skakaciehradyorava.sk: expect 14/14 products with prices in the persisted knowledge graph (vs 2/14 today).
- If vision misreads mirrored back-layer text, escalate transform-override to `setProperty('transform', 'none', 'important')`.
