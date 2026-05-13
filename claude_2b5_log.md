# Phase 2B-5 — !important inline override (PARTIAL SUCCESS)

**Status:** PARTIAL ⚠️
**Date:** 2026-05-13
**Trigger:** 2B-4 confirmed back-layer text is in DOM, but computed `transform` stayed at `matrix3d(...)` (rotateX 180deg). Goal of 2B-5: switch all critical style writes in `_expand_flip_boxes` to `setProperty(prop, value, 'important')` so inline styles beat Elementor's stylesheet rules.

## What changed

**Modified files:** `app/core/browser.py` — JS body of `_expand_flip_boxes` rewritten to use `setProperty(..., 'important')` for all critical properties.

### Critical properties switched to `setProperty(..., 'important')`
On all targeted selectors (`.elementor-flip-box`, `__front`, `__back`, `__layer`, `__layer__inner`, `__layer__overlay`, `__layer__description`):
- `transform`, `transform-style`, `perspective`
- `position`, `display`, `opacity`, `visibility`
- `height`, `min-height`, `backface-visibility`

Non-critical (`background`) left as plain `style.background = ...`.

### Tests
**Added:** `test_expand_flip_boxes_uses_important_for_critical_styles` — asserts `setProperty('transform', 'none', 'important')` and `setProperty('display', ..., 'important')` are present in the method source. Now 5 flip-box tests.

## Verification

- **Syntax:** OK.
- **Imports:** APP OK.
- **Flip-box tests:** 5/5 pass.
- **Full suite:** `132 passed in 11.16s` (131 baseline + 1 new).

### Live debug on skakaciehradyorava.sk
```
AFTER FIX:
  transform:  matrix3d(1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1)   ← still rotated
  display:    block         ← OK
  visibility: visible       ← OK
  opacity:    1             ← OK
  count:      14

Result: FAIL: STILL ROTATED — transform fix neprebil Elementor CSS
```

### Interpretation
The `!important` inline override **does work** for `display` / `visibility` / `opacity` / `position` (those properties compute correctly), but **fails for `transform`**. Possible causes:
1. Elementor uses a **CSS animation** holding `transform: rotateX(180deg)` — animations sit at the top of the cascade and beat even inline `!important`.
2. Elementor uses a `keyframes`-backed pseudo-class rule that re-applies the transform after our JS runs.
3. There is an intermediate wrapper element between `.elementor-flip-box` (parent) and `.elementor-flip-box__back` (child) that also has its own transform context we are not flattening.

### Net effect on the data pipeline
- **HTML extractor:** unaffected — back-layer text is in DOM (`textContent` already exposes "180€", "Kapacita", "Rozmery", etc.). Phase 2A scrape will see everything.
- **Vision (screenshot) pipeline:** screenshot will still show back layers vertically mirrored (rotateX 180deg). Sonnet vision can usually read mirrored text but it is a recognition cost we should not accept long-term.
- **Counts:** `_expand_flip_boxes` correctly returns 14 (one per back layer).

## Untouched
- Method signature, docstring, gate placement (`allow_accordion`, complexity < 0.7), integration point (step 2.5 of `_apply_ui_expansion`), counter location.
- All other UI Expansion methods, render pipeline, vision, knowledge hub, Phase 2A engine.
- No git commit/push.

## Next (follow-up needed — NOT done in 2B-5)
Likely fixes to try when we revisit:
1. Inspect actual stylesheet rule winning the cascade — `getMatchedCSSRules` or `window.getComputedStyle` source attribution to identify whether it is an animation, transition, or a more specific rule.
2. If it is an animation: set `animation: none !important` and `transition: none !important` first, then transform.
3. Walk the DOM tree between `.elementor-flip-box` and `.elementor-flip-box__back` and zero out transforms on every intermediate ancestor.
4. Alternative: clone the back-layer's `textContent` into a sibling `<div>` outside the flip-box container so the screenshot pipeline sees a normal upright copy.

For now, HTML extraction is fully unblocked. Decision deferred to user: ship 2B-4/2B-5 as-is and rely on HTML pipeline, or invest in the visual fix (option 1-4 above) before E2E.
