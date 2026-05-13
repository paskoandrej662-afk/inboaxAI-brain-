# Phase 2B-6 — Vision prompt: text as primary source

**Status:** DONE ✅
**Date:** 2026-05-13
**Trigger:** Live debug after 2B-5 confirmed `raw_text` (HTML extract) contains all 14 hrady with prices/attributes/phone (10710 chars). But the vision prompt in `_extract_one_tile` only forwarded `raw_text_excerpt[:2500]` to Sonnet — roughly the first 3 hrady — and Sonnet was treating the screenshot as ground truth. With the flip-box transform still rotated (per 2B-5 partial), the screenshot can mislead Sonnet on back-layer content. Fix: enlarge the text window and tell Sonnet the text is primary.

## What changed

**Modified files:** `app/core/extractors/vision.py` — only the `user_text` construction inside `_extract_one_tile`.

### Diff (in spirit)
- `raw_text_excerpt[:2500]` → `raw_text_excerpt[:12000]` (≈4.8× more context — enough for the full 14-hrady page).
- Old "kontext z celej stranky" framing replaced with explicit hierarchy:
  - "DOLEZITE — Primarny zdroj pravdy je TEXT z HTML, nie screenshot."
  - "Ak vidis konflikt medzi screenshotom a textom: VERIME TEXTU."
  - "Pouzij screenshot iba aby si pochopil rozlozenie a vztahy."
- Block delimiters `=== HTML EXTRACTED TEXT (primary source) === / === END TEXT ===` so Sonnet recognizes the boundary.

### Cost / latency impact
- +~10000 input characters per tile → ~2500 extra input tokens. With prompt caching ON for `system + tool_schema` (unchanged), the text portion is NOT cacheable (varies per page) but is small in absolute cost vs the image.
- No new API calls, no new round-trips.

## Tests

**Added:** `test_extract_one_tile_uses_text_primary_prompt` in `tests/test_tiled_vision.py`. Uses `inspect.getsource` to assert:
- `[:12000]` is present (catches regression to the old 2500 limit).
- A "primary source" / "Primarny zdroj" instruction is present.

**Existing tiled-vision tests:** all 4 still pass (they mock the Sonnet response, prompt text is opaque to them).

## Verification

- **Syntax:** `SYNTAX OK`.
- **Imports:** `APP OK, routes: 20`.
- **Tiled vision tests:** 5/5 pass.
- **Full suite:** `133 passed in 11.51s` (132 baseline + 1 new).
- **Debug script `/tmp/debug_2b6.py`:** confirmed `[:12000]` and primary-source disclaimer are in the live source.

## Untouched
- `merger.py`, `verification.py`, `browser.py`, `knowledge_hub.py`.
- Phase 2A engine.
- `migrations/**`, `models/**`, `requirements.txt`.
- Prompt caching strategy (still `use_cache=True` on system+tool), `max_tokens=2500`, `timeout_s=90.0` — all unchanged.
- No git commit/push.

## Net effect on the data pipeline
- HTML extract → `raw_text` (10710 chars on skakacky) → first 12000 chars now reach Sonnet alongside the tile screenshot, with an explicit "text wins" rule.
- For flip-box pages where screenshot still shows mirrored back layers (2B-5 partial), Sonnet now has both signals and is told which to trust → halucinations should drop. `verify_product` still gates anything Sonnet invents from the screenshot, so we have belt + suspenders.
- For non-flip-box pages: behavior is the same except Sonnet has more context — should only help.

## Next
- E2E re-run on skakaciehradyorava.sk: expect 14 hrady with prices + Kapacita/Rozmery/Výška/Vek persisted via vision pipeline (vs 2 today). If still under-extracting, the gap is in the prompt's product list framing, not in the data window — escalate by tightening the JSON schema instructions or splitting per-tile budgets.
