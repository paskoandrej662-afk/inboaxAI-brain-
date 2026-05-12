# PROMPT 2A-1 — Universal Ingestion Engine v2 — pracovny log

Start: 2026-05-11 — Status: **DOKONCENE, vsetky verifikacie passed.**

## Pociatocny pruzkum (recon)

- Python: 3.12, FastAPI 0.115.0, SQLAlchemy 2.0.36 (asyncio), asyncpg 0.30.0, pydantic 2.9.2, alembic 1.14.0 — pydantic v2 potvrdeny.
- Alembic head v repe: `eba2b4ef7fe9` (subor `migrations/versions/eba2b4ef7fe9_coach_audit_logs_proposal_columns.py`).
- `migrations/env.py`: `target_metadata = Base.metadata` z `app.models` (relevantne len pre autogenerate — ten nespustame).
- Naming convention migracii: `<hash>_<slug>.py`, hlavicka `revision: str = '...'`, `down_revision: Union[str, None] = '...'`.
- Modely: `app/models/base.py` ma `Base(DeclarativeBase)` + `TimestampMixin`. Vzor `app/models/brain_chunks.py` / `audit_logs.py` — `Mapped[...]`, `mapped_column`, `UUID(as_uuid=True)`, `server_default=text("gen_random_uuid()")`, `JSONB`, `Index(...)` v `__table_args__`. `company_id` UUID NOT NULL bez FK.
- Existujucich testov (baseline): **45 collected**.

---

## ULOHA 1 — Alembic migracia

- Command: `ALEMBIC_DATABASE_URL=... alembic -c alembic.ini revision -m "phase2a raw layer (company_pages, raw_page_blocks, ingestion_jobs, ingestion_costs)"`
  (dummy `ALEMBIC_DATABASE_URL` aby env.py nevyzadoval produkcnu DB — nic sa nepripajalo k DB)
- Vytvoreny subor: `migrations/versions/6eb8936e7f1a_phase2a_raw_layer_company_pages_raw_.py`
- **revision: `6eb8936e7f1a`**, **down_revision: `eba2b4ef7fe9`** (auto-set, overene).
- Telo `upgrade()` / `downgrade()` prepisane:
  - `op.create_table('ingestion_jobs', ...)` — id uuid PK gen_random_uuid(), company_id uuid NOT NULL (bez FK), source_url, mode (CHECK standard/deep/quick, default 'standard'), status (CHECK queued/running/completed/partial/failed, default 'queued'), progress, budget_eur numeric(10,4) def 1.20, cost_total_eur numeric(10,6) def 0, pages_visited/succeeded/failed, blocks_found, errors jsonb '[]', warnings jsonb '[]', result_summary jsonb null, started_at/ended_at timestamptz null, created_at/updated_at timestamptz def now().
    Indexy: `ix_ingestion_jobs_company_status_created` (company_id, status, created_at DESC); `ix_ingestion_jobs_active` (status, created_at) WHERE status IN ('queued','running') — partial.
  - `op.create_table('company_pages', ...)` — id, company_id, job_id (logical FK, bez DB FK), url, url_normalized, final_url, title, http_status, render_status (CHECK pending/success/timeout/blocked/error/skipped def 'pending'), render_method (CHECK playwright_headless/httpx/sitemap_only def 'playwright_headless'), render_ms, retry_count, error_message, discovery_method (CHECK sitemap/homepage_link/bfs/rendered_link/seed/robots def 'bfs'), priority_score numeric(3,2) def 0.50, depth, parent_url, html text null, html_storage_path null, content_hash, visible_text, dom_size, text_length, screenshot_path, raw_data jsonb '{}', fetched_at, created_at.
    Indexy: `uq_company_pages_company_url_normalized` UNIQUE (company_id, url_normalized); `ix_company_pages_job_id`; `ix_company_pages_company_render_status`; `ix_company_pages_company_priority` (company_id, priority_score DESC).
  - `op.create_table('raw_page_blocks', ...)` — id, job_id, company_id, page_id (logical FK), source_url, block_type def 'candidate', block_type_hint null, selector, dom_path, parent_selector, section_heading, text, html, text_hash, headings jsonb '[]', images jsonb '[]', links jsonb '[]', signals jsonb '{}', position_index def 0, depth def 0, extraction_method def 'heuristic_block', confidence numeric(3,2) def 0.50, status def 'raw', created_at.
    Indexy: `ix_raw_page_blocks_page_id`; `ix_raw_page_blocks_job_id`; `ix_raw_page_blocks_company_type_hint` (company_id, block_type_hint).
  - `op.create_table('ingestion_costs', ...)` — id, job_id, operation NOT NULL, model null, input/output/cache_read/cache_creation tokens, bytes_in/out, duration_ms, est_cost_eur numeric(10,6) def 0, hard_limit_hit boolean def false, created_at.
    Index: `ix_ingestion_costs_job_created` (job_id, created_at).
  - `op.execute(...)` — `CREATE OR REPLACE FUNCTION ingestion_jobs_set_updated_at()` + `CREATE TRIGGER ingestion_jobs_updated_at_trg BEFORE UPDATE ON ingestion_jobs FOR EACH ROW EXECUTE FUNCTION ingestion_jobs_set_updated_at()`.
  - `downgrade()`: drop trigger + function (IF EXISTS), potom drop indexov a tabuliek v opacnom poradi (costs, blocks, pages, jobs).
- **NESPUSTENE** na DB. Iba parse-check (viz Verifikacia, krok 1b: `MIGRATION PARSES`).

## ULOHA 2 — SQLAlchemy modely

- Vytvoreny `app/models/ingest_v2.py` — `IngestionJob`, `CompanyPage`, `RawPageBlock`, `IngestionCost`. Styl podla `brain_chunks.py` / `audit_logs.py`: `Mapped[...]`, `mapped_column`, `UUID(as_uuid=True)`, `server_default=text(...)`, `JSONB`, `Numeric`, `CheckConstraint` + `Index` v `__table_args__`. Stlpce 1:1 s migraciou. `company_id` bez FK.
- `app/models/__init__.py` — pridane importy a `__all__` polozky: `IngestionJob`, `CompanyPage`, `RawPageBlock`, `IngestionCost` (jediny modifikovany existujuci subor, +10 riadkov).

## ULOHA 3 — Pydantic typy

- Vytvoreny `app/core/ingest_v2/__init__.py` — `# ingest_v2 package`.
- Vytvoreny `app/core/ingest_v2/types.py` — Pydantic v2 (`BaseModel`, `Field`, `ConfigDict`).
  - Enumy: `IngestMode`, `JobStatus`, `RenderStatus`, `DiscoveryMethod`, `SourceType`, `BlockTypeHint`.
  - Modely: `EvidenceRecord` (s `model_config = ConfigDict(use_enum_values=True)`), `BlockSignals`, `ImageCandidate`, `ContactPatterns`, `HeadingItem`, `LinkItem`, `RawPageData`, `BudgetLimits`, `BudgetStatus`.

## ULOHA 4 — BudgetManager

- Vytvoreny `app/core/ingest_v2/budget.py` — `_BudgetOperation` (dataclass) + `BudgetManager` (resource + EUR tracking): `runtime_seconds`, `can_render_page`, `can_spend` (soft limit blokuje len `vision_call`/`image_describe`/`company_profile`, hard limit blokuje vsetko), `can_store_block`, `can_collect_image`, `record_render`, `record_blocks`, `record_images`, `record_operation`, `status() -> BudgetStatus`.

## ULOHA 5 — Testy

- `tests/test_v2_types.py` — 7 testov (EvidenceRecord min + confidence range, BlockSignals defaults, ImageCandidate min, RawPageData empty, BudgetLimits defaults, enums values).
- `tests/test_v2_budget.py` — 12 testov (init, can_render within, page limit, html bytes limit, can_spend within soft, hard limit blocks all, soft limit blocks only expensive, image candidate limit, block limit, record_operation increments, status reflects state, runtime limit).

## ULOHA 6 — VERIFIKACIA

### Krok 1 — Syntax check
```
$ python -c "import ast; [ast.parse(open(f).read()) for f in ['app/core/ingest_v2/__init__.py', 'app/core/ingest_v2/types.py', 'app/core/ingest_v2/budget.py', 'app/models/ingest_v2.py', 'tests/test_v2_types.py', 'tests/test_v2_budget.py']]; print('SYNTAX OK')"
SYNTAX OK
```

### Krok 1b — Migration parses (namiesto `alembic upgrade`)
```
$ python -c "import ast; ast.parse(open('migrations/versions/6eb8936e7f1a_phase2a_raw_layer_company_pages_raw_.py').read()); print('MIGRATION PARSES')"
MIGRATION PARSES
```

### Krok 2 — Import check
```
$ PYTHONPATH=. python -c "from app.core.ingest_v2.types import EvidenceRecord, SourceType, BudgetLimits, RawPageData, BlockTypeHint; from app.core.ingest_v2.budget import BudgetManager; from app.models.ingest_v2 import IngestionJob, CompanyPage, RawPageBlock, IngestionCost; print('IMPORTS OK')"
IMPORTS OK
```

### Krok 3 — Nove testy
```
$ PYTHONPATH=. pytest tests/test_v2_types.py tests/test_v2_budget.py -v
collecting ... collected 19 items

tests/test_v2_types.py::test_evidence_record_minimum PASSED              [  5%]
tests/test_v2_types.py::test_evidence_record_confidence_range PASSED     [ 10%]
tests/test_v2_types.py::test_block_signals_defaults PASSED               [ 15%]
tests/test_v2_types.py::test_image_candidate_minimum PASSED              [ 21%]
tests/test_v2_types.py::test_raw_page_data_default_empty PASSED          [ 26%]
tests/test_v2_types.py::test_budget_limits_defaults PASSED               [ 31%]
tests/test_v2_types.py::test_enums_values PASSED                         [ 36%]
tests/test_v2_budget.py::test_budget_init_default_limits PASSED          [ 42%]
tests/test_v2_budget.py::test_budget_can_render_within_limits PASSED     [ 47%]
tests/test_v2_budget.py::test_budget_page_limit_blocks_render PASSED     [ 52%]
tests/test_v2_budget.py::test_budget_html_bytes_limit PASSED            [ 57%]
tests/test_v2_budget.py::test_budget_can_spend_within_soft PASSED       [ 63%]
tests/test_v2_budget.py::test_budget_hard_limit_blocks_all PASSED       [ 68%]
tests/test_v2_budget.py::test_budget_soft_limit_blocks_only_expensive PASSED [ 73%]
tests/test_v2_budget.py::test_budget_image_candidate_limit PASSED       [ 78%]
tests/test_v2_budget.py::test_budget_block_limit PASSED                 [ 84%]
tests/test_v2_budget.py::test_budget_record_operation_increments_spent PASSED [ 89%]
tests/test_v2_budget.py::test_budget_status_reflects_state PASSED       [ 94%]
tests/test_v2_budget.py::test_budget_runtime_limit PASSED              [100%]

============================== 19 passed in 0.17s ==============================
```

### Krok 4 — VSETKY testy (regresia)
```
$ PYTHONPATH=. pytest tests/ -v
...
tests/test_vision_extractor.py::test_extract_page_no_tool_use_returns_empty PASSED [100%]

============================== 64 passed in 28.85s ==============================
```
45 (baseline) + 19 (nove) = **64 passed, 0 failed**.

### Git scope check
```
$ git status --short
 M app/models/__init__.py        <- jediny modifikovany existujuci subor (+10 riadkov, len exporty)
?? app/core/ingest_v2/           <- nove (__init__.py, types.py, budget.py)
?? app/models/ingest_v2.py       <- nove
?? migrations/versions/6eb8936e7f1a_phase2a_raw_layer_company_pages_raw_.py  <- nove
?? tests/test_v2_budget.py       <- nove
?? tests/test_v2_types.py        <- nove
?? claude_2a1_log.md, claude_status.md  <- log/status
```
Nedotknute: `app/core/knowledge_hub.py`, `app/core/extractors/**`, `app/core/browser.py`, `app/core/scraper.py`, `app/api/v1/**`, `app/workers/**`, `app/db.py`, `/workspaces/inboxai-web`. Ziadne nove dependencies. Ziaden git commit/push. Ziadne SQL na produkcnej DB.

---

## CHANGELOG (5 bodov)

1. **Vytvorene subory:**
   - `migrations/versions/6eb8936e7f1a_phase2a_raw_layer_company_pages_raw_.py` (Alembic migracia, 4 tabulky + trigger)
   - `app/models/ingest_v2.py` (SQLAlchemy modely: IngestionJob, CompanyPage, RawPageBlock, IngestionCost)
   - `app/core/ingest_v2/__init__.py`
   - `app/core/ingest_v2/types.py` (Pydantic v2 typy + enumy)
   - `app/core/ingest_v2/budget.py` (BudgetManager)
   - `tests/test_v2_types.py` (7 testov)
   - `tests/test_v2_budget.py` (12 testov)
   - Modifikovany: `app/models/__init__.py` (+10 riadkov — exporty 4 novych tried)

2. **Migracia:** subor `6eb8936e7f1a_phase2a_raw_layer_company_pages_raw_.py`, **revision hash `6eb8936e7f1a`**, down_revision `eba2b4ef7fe9`. NESPUSTENA na DB (cakajuca na manualne `alembic upgrade`).

3. **SYNTAX OK + IMPORTS OK potvrdene? ANO.** (+ navyse `MIGRATION PARSES` OK.)

4. **Nove testy: 19 passed** (7 types + 12 budget). **Celkovo: 64 passed** (45 baseline + 19 = 64, splna ≥56). 0 failed.

5. **Potvrdene — nic existujuce sa nezmenilo:** Phase 1A-1C kod (`knowledge_hub.py`, `extractors/**`, `browser.py`, `scraper.py`), `api/v1/**`, `workers/**`, `db.py` su nedotknute. Jediny modifikovany existujuci subor je `app/models/__init__.py` (len pridanie 4 importov/exportov, ziadne zmeny existujucich riadkov). Ziadne nove dependencies, ziadny git commit/push, ziadne SQL na produkcnej DB.
