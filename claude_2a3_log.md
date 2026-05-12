# Phase 2A-3 log — Universal Ingestion Engine v2 (block detector + persistence + orchestrator + API)

Predchadzajuce: 2A-1 (DB layer + types + BudgetManager), 2A-2 (Renderer + Crawler + RawExtraction). Commit baseline: `31c566f`.

## Co bolo dorobene v tejto session

### Nove subory
1. **`app/core/ingest_v2/block_detection.py`** (NOVY) — heuristicky block detektor (Layer B), zero LLM, zero network.
   - `DetectedBlock` dataclass + `detect_blocks(html, max_blocks=200)`.
   - 3 priechody: (1) semanticke bloky `section/article/main/footer/nav/header/aside`, (2) opakovane karty (skupiny >=3 podobnych surodencov), (3) `<details>` FAQ akordeony.
   - Signaly: cena (`PRICE_RE` € / EUR / Kč / CZK), CTA keywords, kontakt regex, datum, otazka, obrazky, linky, class tokeny, opakovana struktura.
   - `_classify_block_hint`: footer/header_nav/hero/faq/contact/pricing/gallery/article/about/table/repeated_card/section/candidate_card/unknown.
   - Defensive: `detect_blocks` nikdy neraisne — pri chybe vracia ciastocny zoznam.
   - **Pozn.:** prahy minimalnej dlzky textu znizene oproti povodnemu navrhu (`_MIN_TEXT_SEMANTIC=15`, `_MIN_TEXT_REPEATED=5`), aby kratke realne bloky (footer "© 2026 info@...", karta "P1 160 €") neboli odfiltrovane.
2. **`app/core/ingest_v2/persistence.py`** (NOVY) — async SQLAlchemy zapisy do 4 tabuliek.
   - `create_job`, `update_job_status`, `finalize_job` (ingestion_jobs); `save_page` (company_pages, aj pre neuspesny render = audit trail); `save_blocks` (raw_page_blocks, bulk insert); `save_cost` (ingestion_costs).
   - Vsetky volaju `await session.flush()`; `commit()` ostava na volajucom.
   - `content_hash`/`text_hash` = sha256; HTML cap 200 kB, visible_text cap 100 kB, block text cap 10 kB, block html cap 5 kB.
3. **`app/core/ingest_v2/orchestrator.py`** (NOVY) — `ingest_company_v2(job_id, company_id, source_url, mode, budget_eur)` — main entry point.
   - Pipeline: status→running → renderer.start → crawler.discover_pages (fallback na seed) → per page: render → raw extrakcia → block detekcia → persist page+blocks (vlastny session per page, commit po flush) → progress update → finalize_job (`completed`/`partial`/`failed`).
   - BudgetLimits podla mode: quick (5 strankok / 90 s), deep (20 / 300 s), standard (default 12 / 180 s).
   - Defensive: chyby per-page → `errors`/`warnings`, job nikdy nespadne kvoli jednej stranke; `finally: renderer.close()`.
   - Zero LLM (`budget.spent_eur` ostava 0.0 → `cost_total_eur=0.0`).
4. **`app/workers/ingest_v2_worker.py`** (NOVY, PRIPRAVENY — NESPUSTANY) — `ingest_v2_task` + `IngestV2Worker` (Arq Settings, `RedisSettings.from_dsn(settings.REDIS_URL)`, `job_timeout=600`, `max_jobs=2`). Railway worker service stale behi na `ingest_worker.IngestWorker` (Phase 1).
5. **`app/api/v2/__init__.py`** (NOVY) — package marker.
6. **`app/api/v2/ingest.py`** (NOVY) — FastAPI router `prefix="/v2"`:
   - `POST /v2/ingest-company` (202, beh v `BackgroundTasks` — ziaden Redis queue / Arq enqueue, pre 2A-3 testovanie),
   - `GET /v2/jobs/{job_id}`,
   - `GET /v2/jobs/{job_id}/raw-summary` (agregaty: pocty strankok/blokov, histogram block_type_hint, obrazky/linky/emaily/telefony/pdf/json_ld),
   - `GET /v2/companies/{company_id}/pages`.
7. **`tests/test_v2_block_detection.py`** (NOVY) — 6 offline testov.
8. **`tests/test_v2_persistence.py`** (NOVY) — 4 signature/strukturalne testy (bez DB).
9. **`tests/test_v2_orchestrator.py`** (NOVY) — 1 signature test.

### Upraveny subor
- **`app/main.py`** — pridane 2 riadky: `from app.api.v2 import ingest as v2_ingest` + `app.include_router(v2_ingest.router)`. Ziadne ine zmeny.

## Verifikacia (vsetky kroky PASS)

| Krok | Vysledok |
|------|----------|
| 1. Syntax check (ast.parse 10 suborov) | `SYNTAX OK` |
| 2. Imports (block_detection / persistence / orchestrator / ingest_v2_worker / v2 router) | `IMPORTS OK` |
| 3. `from app.main import app` | `APP OK, routes count: 20` |
| 4. Nove testy (block_detection + persistence + orchestrator) | **11 passed** |
| 5. ALL tests regression | **111 passed** (100 baseline + 11 nove) |

v2 routy registrovane: `POST /v2/ingest-company`, `GET /v2/jobs/{job_id}`, `GET /v2/companies/{company_id}/pages`, `GET /v2/jobs/{job_id}/raw-summary`.

## Pravidla dodrzane
- Zero LLM, zero new dependencies.
- Pydantic v2 (`BaseModel` + `Field`), async/await + `AsyncSession`, `commit()` po `flush()`.
- Defensive: orchestrator/persistence catch errors per-page, cely job nikdy nespadne.
- Type hints, `from __future__ import annotations`, slovencina v komentaroch.
- Phase 1 (`app/workers/ingest_worker.py`, `app/api/v1/**`, `knowledge_hub`, `extractors`, `browser`, `scraper`, `db.py`, `migrations/**`, `requirements.txt`) NEDOTKNUTE.
- Phase 2A-1/2A-2 (`types.py`, `budget.py`, `renderer.py`, `crawler.py`, `raw_extraction.py`, `models/ingest_v2.py`) NEZMENENE — len pridane nove subory + 2 riadky v `main.py`.
- `ingest_v2_worker.py` len PRIPRAVENY — Railway worker service ostava na `ingest_worker.IngestWorker`.
- Ziadny git commit/push.

## TODO / dalej
- E2E test cez `POST /v2/ingest-company` manualne po deployi (DB pripojenie + Playwright Chromium na Railway).
- Phase 2B: klasifikator nad `raw_page_blocks` (LLM), Phase 2C: image describe.
