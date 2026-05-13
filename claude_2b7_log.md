# Phase 2B-7 — Recall fix + price_text + eval smoke

**Status:** DONE ✅
**Date:** 2026-05-13
**Base commit:** f8fa6cb (post-rollback of 2B-6; baseline 132 tests).

## Why
After 2B-3/4/5 the e2e on skakaciehradyorava.sk reached **11/14 products with 9 complete** (21% → 82% completeness). The remaining gap had two root causes:
1. **Recall** — Pirat, Biely Skakaci Hrad and Stan na prenajom have full data in `raw_text` but Sonnet skipped them in extraction. Prompt did not strongly enforce "extract every product card."
2. **Mixed/soft pricing** — Stan is "55€/Deň + doprava dohodov." Without a free-text price field, verification stripped the price; `price_eur` ended up null and the product dropped quality.

(Autickov / Bublina / Atrakcia are missing from the homepage's raw_text entirely — different page or widget; deferred to 2B-8.)

## What changed

### `app/core/extractors/vision.py`
- **`VISION_TOOL_SCHEMA.products.items.properties.price_text`**: type widened from `"string"` to `["string", "null"]`; description rewritten — now explicitly for non-numeric or mixed prices (`"dohodou"`, `"na vyziadanie"`, `"55€/den + doprava dohodov"`), with the instruction "if price is clean numeric, leave null and only set price_eur."
- **`VISION_SYSTEM`**: appended (NOT replaced) a new `DOLEZITE PRAVIDLA EXTRAKCIE` block. Tells Sonnet to extract every named product, including small cards and products with `price_text` instead of `price_eur`, and to fill BOTH for mixed pricing.
- **`_extract_one_tile`**: raw_text limit stays at `[:2500]` per spec (we explicitly do NOT repeat the 2B-6 widening here).

### `app/core/extractors/verification.py`
- **`verify_product`**: added OR branch that accepts soft-pricing language. New `SOFT_PRICE_KEYWORDS = ('dohod', 'na vyziadanie', 'na poziadanie', 'individual', 'na vyzadanie')`. If `p.price_text` contains one of these keywords AND the same keyword appears in `page_text`, verification passes without numeric match. Existing numeric verification branches are unchanged — the new check sits in front as an extra acceptance gate.

### `app/core/extractors/merger.py`
- **`merge_products`**: when two candidates for the same product both have `price_text`, keep the longer one (more information, typically the mixed-pricing variant). Existing "winner has no price_text, copy from candidate" path unchanged.

### `app/core/extractors/types.py`
- **Untouched.** `ExtractedProduct.price_text: str | None = None` already exists.

### `tests/test_recall_and_price_text.py` (new)
5 tests:
1. `test_extracted_product_accepts_price_text` — dataclass accepts mixed pricing fields.
2. `test_extracted_product_price_text_only` — `price_eur=None` + `price_text="dohodou"` valid.
3. `test_vision_tool_schema_includes_price_text` — schema declares the field.
4. `test_vision_system_prompt_has_recall_instruction` — prompt contains a "KAZD/EVERY/VSETK" instruction.
5. `test_verify_product_accepts_dohodov` — `verify_product` sets `.verified=True` and leaves `price_text="dohodov"` intact when both the field and page_text contain a soft-pricing keyword.

**Deviation from the literal prompt:** test #5 asserts `result.verified is True` (not `verify_product(...) is True`). `verify_product` returns the mutated `ExtractedProduct`, not a bool, and the in-tree caller (`knowledge_hub.py:477`) relies on the dataclass return — changing the signature would have been an unscoped breaking change. The semantic the user asked for ("verify passes") is preserved.

### `scripts/eval_vision_smoke.py` (new)
Manual smoke eval (calls real Sonnet API, ~$0.10-$1 per run). Renders a fixture HTML with 3 products (A 100€/ks, B 200€/ks, C dohodou), runs `extract_page_with_tiled_vision`, asserts ≥2 products extracted. Exits 0 on pass, 1 on fail. Wired to skip silently if `ANTHROPIC_API_KEY` is unset. Adjusted from the prompt's stub to match real signatures (`TiledPageResult` fields, 6-tuple return from `extract_page_with_tiled_vision`).

## Verification

- **Syntax:** all 6 touched/new Python files parse.
- **Imports:** `APP OK, routes: 20`.
- **New tests:** `5 passed in 0.43s`.
- **Full suite:** `137 passed in 11.48s` (132 baseline + 5 new).
- **`eval_vision_smoke.py`:** NOT run automatically — requires real Sonnet API call. Run manually pre-push if you change `vision.py`, `VISION_SYSTEM`, or `VISION_TOOL_SCHEMA`.

## Untouched
- `browser.py` (2B-3/4/5 flip-box code intact), `knowledge_hub.py`, Phase 2A engine.
- `_extract_one_tile` raw_text window stays at 2500 — explicitly preserves the rollback.
- `requirements.txt`, `migrations/**`, `models/**`.
- No git commit/push.

## Expected E2E impact (skakaciehradyorava.sk)
- Recall: prompt-side push for "extract every card" should pick up Pirat / Biely / Stan in addition to current 11.
- Stan: `price_eur=55, price_text="55€/Deň, doprava dohodov"` survives verification (price_eur numeric match + soft-keyword acceptance for the doprava part).
- Eval smoke: pre-push gate against any future regression that silently drops products to 0.

## Next
- Andrej: run `python3 scripts/eval_vision_smoke.py` once (one-time real-API cost) to baseline the smoke test and confirm Sonnet still extracts ≥2 products with the new prompt + schema.
- E2E on skakaciehradyorava.sk; expect 14/14 products, 11+ with complete pricing.
- 2B-8 candidate: source of Autickov/Bublina/Atrakcia (separate page or widget not surfaced on homepage raw_text).
