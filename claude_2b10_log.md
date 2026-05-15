# 2B-10 — HDS-v3 Gemini Integration (Commit 2 zo 4)

## Cieľ
Vziať `List[DiscoveredPage]` z Commitu 1 (HDSCrawler), rozdeliť do trojíc,
poslať paralelne do Gemini Flash 2.5 s Google Search Grounding,
vrátiť raw markdown výstupy. **Parser + DB persistence prídu v Commite 3.**

## Base commit
f2b548e (HDS-v3 Commit 1 — Crawler hotový, 192 testov v repe).

## Scope
- NEW: `app/core/extractors/hds_v3/batcher.py` (Batcher trieda — chunk 1-3 URL)
- NEW: `app/core/extractors/hds_v3/gemini_client.py` (GeminiClient async)
- NEW: `app/core/extractors/hds_v3/prompts.py` (BATCH_EXTRACTION_SYSTEM + build_batch_prompt)
- NEW: `tests/test_hds_v3_batcher.py` (5 testov)
- NEW: `tests/test_hds_v3_gemini_client.py` (6 testov, mocked)
- NEW: `scripts/eval_hds_v3_gemini.py` (real Gemini API smoke eval)
- NEW: `scripts/test_hds_v3_full.py` (end-to-end Crawler→Gemini)
- MODIFIED: `app/core/extractors/hds_v3/types.py` — pridané `GeminiBatchResult` + `GeminiExtractionResult` dataclasses (existujúce CrawlResult/DiscoveredPage/PagePriority netknuté)
- MODIFIED: `app/core/extractors/hds_v3/__init__.py` — re-export nových typov
- MODIFIED: `requirements.txt` — pridané `google-genai>=0.3.0`

## Pravidla rešpektované
- Žiadna zmena v `crawler.py` (Commit 1 hotový)
- Žiadna zmena v `knowledge_hub.py`, `vision.py`, `merger.py`, `verification.py`
- Žiadna zmena v Phase 2A engine (`app/core/ingest_v2/**`), `migrations/**`, `models/**`
- Žiadny nový endpoint, žiadny nový worker job — len knižničný kód a testy
- Defenzívne — exceptions z `asyncio.gather(return_exceptions=True)` zaznamenané, parciálny úspech podporovaný (`success = successful_batches > 0`)
- Real API skripty (`eval_hds_v3_gemini.py`, `test_hds_v3_full.py`) **nepúšťané v CI** — manuálne pred push-om

## Architektúra

### Batcher (`batcher.py`)
- `BATCH_SIZE = 3`
- `make_batches(pages)`: list comprehension `pages[i:i+3]` zachová poradie z crawlera (TIER_0 first)
- Edge: empty list → `[]`; single page → `[[page]]`; 7 pages → `[3, 3, 1]`

### Prompts (`prompts.py`)
- `BATCH_EXTRACTION_SYSTEM`: 6 pravidiel — žiadne halucinácie, "neuvedené" pri chýbajúcich dátach, soft prices ("dohodou") zachovať verbatim
- `build_batch_prompt(urls)`: hardguard `ValueError` ak `len(urls) ∉ [1,3]`; vyplní URL list + per-page sekciu template (identifikácia firmy, kontakty, produkty/služby tabuľka, cenové podmienky, FAQ, referencie, geografia)
- ASCII-only (žiadne diakritické znaky v kóde) pre konzistenciu — Gemini si poradí

### GeminiClient (`gemini_client.py`)
- SDK: `google-genai==2.3.0` (oficiálny Google Python SDK)
- Model: `gemini-2.5-flash`
- Tool: `google_search=GoogleSearch()` (free do 1500 query/day)
- Pricing constants (May 2026): `INPUT_TOKEN_PRICE_PER_1M=0.30`, `OUTPUT_TOKEN_PRICE_PER_1M=2.50`
- Concurrency: `asyncio.Semaphore(MAX_CONCURRENT_BATCHES=5)`
- Retry: `MAX_RETRIES=3` s exponential backoff `[1, 3, 9]` sec; per-batch timeout `BATCH_TIMEOUT_SEC=60`
- `extract_pages(base_url, pages) -> GeminiExtractionResult`:
  1. early return ak `pages` empty (`error="no_pages_to_exract"`)
  2. `Batcher.make_batches(pages)`
  3. `asyncio.gather(*tasks, return_exceptions=True)` — paralelne, exceptions caught
  4. agregát: success/failed counts, cost, tokens, duration
  5. `success = successful_batches > 0` (parciálne úspechy stačia)
- `_call_batch(batch)`:
  - acquire semaphore
  - build_batch_prompt (ValueError catch → early return)
  - retry loop: `asyncio.wait_for(self._gemini_call(...), timeout=60)`
  - úspech → text extract, usage_metadata → cost calc, return
  - timeout/exception → log warning, sleep(backoff), retry
- `_gemini_call(user_prompt)`: SDK je sync, wrapped v `asyncio.to_thread`; `GenerateContentConfig(system_instruction, tools=[Tool(google_search=...)], temperature=0.1, max_output_tokens=8192)`

## Defensive notes
- `usage_metadata` access cez `getattr(..., None)` + `getattr(um, 'prompt_token_count', 0) or 0` — SDK rôzne verzie majú rôzne názvy/optional fields
- `response.text` rovnako: `getattr(response, 'text', None) or ""`
- Empty text → `ValueError("Gemini returned empty response")` → retry vetva
- `RETRY_BACKOFF_SEC[min(attempt, len-1)]` — safe pre prípad zmeny `MAX_RETRIES`
- `result.error = None` po úspechu (clear z predchádzajúcich retry attemptov)

## Testy
- `tests/test_hds_v3_batcher.py` (5):
  - 12 pages → 4 batches po 3
  - 7 pages → [3, 3, 1]
  - empty → []
  - single → [[page]]
  - poradie sa zachová (flat == original)
- `tests/test_hds_v3_gemini_client.py` (6):
  - `test_client_requires_api_key` — `monkeypatch.delenv` → `ValueError("GEMINI_API_KEY")`
  - `test_extract_pages_empty_returns_error` — `pages=[]` → `success=False`, `error="no_pages_to_extract"`
  - `test_extract_pages_calls_gemini_per_batch` — 6 pages → 2 calls (mock `_gemini_call`)
  - `test_batch_retries_on_failure` — 1.+2. call zlyhá, 3. uspeje → `success=True`, `retry_count=2`
  - `test_batch_fails_after_max_retries` — vždy zlyhá → `failed_batches=1`, `success=False`
  - `test_cost_calculation` — 10k in + 5k out → `0.0155 USD` (presná pricing math)
  - Test trik: `RETRY_BACKOFF_SEC = [0, 0, 0]` na inštancii pre rýchlosť testov
  - `AsyncMock` priamo nastavený na `client._gemini_call` (bypass SDK)
- **Real-API skripty (NESPÚŠŤANÉ v CI)**:
  - `scripts/eval_hds_v3_gemini.py` — 1 batch (3 URL skakaciehradyorava.sk) ≈ $0.02, validuje že markdown obsahuje očakávané produkty (Tiger/Rozprávkovo/Aladin/hrad/Disney)
  - `scripts/test_hds_v3_full.py` — full Crawler → Gemini, výstup do `/tmp/hds_v3_output_<url>.md` pre review

## Test results
```
PYTHONPATH=. pytest tests/ -q
203 passed (192 baseline + 11 nové), ~15s
```
Plný suite (`pytest`) bez argumentu hlási collection conflict medzi `scripts/test_hds_v3_crawler.py` a `tests/test_hds_v3_crawler.py` — existoval už pred 2B-10 (oba súbory pridané v Commite 1) a netýka sa môjho kódu. Workaround: `pytest tests/`.

## SDK note
`google-genai==2.3.0` (May 2026) si pri inštalácii bumpne `httpx` z `0.27.2` na `0.28.1`. Žiadne testy nepadli, ale stojí za pozornosť pri ďalších commitoch — ak by sa niekde inde objavil break, fixnúť pinning v requirements.txt.

## API ergonomics overené
```python
from google import genai
from google.genai import types as gt
client = genai.Client(api_key=...)
client.models.generate_content(
    model="gemini-2.5-flash",
    contents=user_prompt,
    config=gt.GenerateContentConfig(
        system_instruction=...,
        tools=[gt.Tool(google_search=gt.GoogleSearch())],
        temperature=0.1,
        max_output_tokens=8192,
    ),
)
# response.text + response.usage_metadata.prompt_token_count + .candidates_token_count
```

## Netknuté
- `crawler.py` (Commit 1)
- `knowledge_hub.py`, `vision.py`, `merger.py`, `verification.py`, `browser.py`
- Phase 2A engine (`app/core/ingest_v2/**`)
- HDS-Lite (`app/core/extractors/hds/**`)
- `migrations/**`, `models/**`
- `app/api/**`, `app/workers/**`

## Git status
Bez commitu (na želanie). Pripravené na `git add` všetkých nových súborov + úprav existujúcich.

## Ďalej (Commit 3)
- Markdown parser ktorý zo `GeminiBatchResult.markdown` extrahuje:
  - business identity (názov, IČO, DIČ, adresy)
  - kontakty (osoby + telefón + email + pozícia)
  - produkty/služby (názov + cena + jednotka + atribúty)
  - FAQ, referencie, geografia
- DB persistence — mapovanie na existujúce modely v `app/models/`
- Test fixtures: real markdown z `scripts/test_hds_v3_full.py` runov

## Ďalej (Commit 4)
- Obrázky: primary_image per produkt (z Gemini citácií?) + sekundárne (galéria)
