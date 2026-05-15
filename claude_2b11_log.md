# 2B-11 — HDS-v3 Parser + Validator + Dedup + Persona (Commit 3 zo 5)

## Cieľ
Zoberieme raw markdown z Gemini Commitu 2 a:
1. PARSER — markdown → ExtractedProduct[] + HdsExtractedFact[] (contacts/facts) + HdsFAQ[]
2. VALIDATOR — anti-halucinácia regex (SK telefón, email, číslo v adrese)
3. DEDUP — produkt "Tiger" v 3 batchoch → 1 záznam
4. PERSONA GENERATOR — extra Gemini call vytvorí 2000-slov operating manual
5. ENGINE — orchestrátor full pipeline

Persistence do DB (knowledge_hub integration) → Commit 3.1 (oddelený od tohto).

## Base commit
`0d0ff99` (HDS-v3 Commit 2.1 — url_context patch, 206 testov).

## Scope (10 nových + 3 modifikované)

### NEW
- `migrations/versions/a3f9c2d51842_hds_v3_brain_personas_doc.py` — Alembic migrácia
- `app/models/brain_personas.py` — `BrainPersonaDocument` ORM model (BigInt PK, company_id UUID indexed, version, persona_text, word_count, source_urls JSONB, gemini_cost_usd, tokens, meta, created_at)
- `app/core/extractors/hds_v3/parser.py` — `MarkdownParser` + `ParseResult`
- `app/core/extractors/hds_v3/validator.py` — `Validator`
- `app/core/extractors/hds_v3/dedup.py` — `Deduplicator`
- `app/core/extractors/hds_v3/persona_generator.py` — `PersonaGenerator` (separate Gemini call, temp=0.3, max 8192 tokens)
- `app/core/extractors/hds_v3/engine.py` — `HDSv3Engine` (full pipeline orchestrator)
- `tests/test_hds_v3_parser.py` — 8 testov
- `tests/test_hds_v3_validator.py` — 6 testov
- `tests/test_hds_v3_dedup.py` — 4 testy
- `scripts/eval_hds_v3_full.py` — real-API E2E eval

### MODIFIED
- `app/core/extractors/hds_v3/types.py` — pridané `HdsExtractedFact` (type/content/meta) + `HdsFAQ` dataclasses
- `app/core/extractors/hds_v3/__init__.py` — re-export nových typov

### NETKNUTÉ
- `crawler.py`, `gemini_client.py`, `prompts.py` (Commit 2)
- `knowledge_hub.py`, `vision.py`, `merger.py`, `verification.py`
- Phase 2A engine (`app/core/ingest_v2/**`)
- HDS-Lite (`app/core/extractors/hds/**`)
- Existujúce `brain_persona` (singular) tabuľka — to je tone config, nie operating manual
- `app/api/**`, `app/workers/**`, `requirements.txt` (nový SDK už nainštalovaný v Commite 2)

## Architektúra

### Adaptácia oproti zadaniu
Zadanie navrhovalo `ExtractedFact(type, content, meta)` import z `app/core/extractors/types.py`. Tento typ TAM NEEXISTUJE — existujúci je `ExtractedBusinessFact(key, value)`. Preto som zaviedol dva domain-specific dataclasses v `hds_v3/types.py`:
- `HdsExtractedFact` — `type` (contact|address|social|info|geo) + `content` + `source_url` + `meta` (dict)
- `HdsFAQ` — `question` + `answer` + `source_url`

`ExtractedProduct` zostáva existujúci (z `app.core.extractors.types`) — pole `price_unit` (nie `unit`), `source_type` označený ako `"hds_v3"`. Engine.py neskôr (Commit 3.1) namapuje HdsExtractedFact → ExtractedBusinessFact pri persistovaní cez knowledge_hub.

### MarkdownParser
**Diacritic-tolerant header matching**: prompts.py vytvára ASCII-only headers (`STRANKA`, `IDENTIFIKACIA`, `PRODUKTY / SLUZBY`), ale Gemini niekedy odpovedá s diakritikou. Riešenie:
- `_strip_diacritics()` cez NFKD + filter combining marks
- Header regexy match-ujú ASCII projekciu, ale URL/values sa extrahujú z pôvodného textu (zachová diakritiku v `Babín 420`)

**Page splitting** cez `=== STRANKA N: URL ===` regex. Pre každú stránku rozdelenie do sekcií cez `## HEADER` a normalizáciu mena (strip `(ak je na tejto stranke)` parenthetical).

**Tabuľkový parser** (`_parse_markdown_table`): skip header riadku, separator riadok `|---|---|` označuje začiatok dát. Defenzívne — riadky bez `|` ignorované.

**Polia**:
- `_parse_identification`: regex `Nazov firmy:`, `ICO:`, `DIC:` line-by-line, hodnoty z pôvodného textu
- `_parse_kontakty`: tabuľka (Meno|Pozícia|Telefón|Email) + line scan pre `Sidlo:`, `Prevadzka:`, `Facebook:`, `Instagram:`
- `_parse_produkty`: tabuľka (Názov|Cena|Jednotka|Popis|Atribúty); `_parse_price` extrahuje numeric pred `€`; `_parse_attributes` regex `Key: Value, Key: Value` (UNICODE-aware capital letter pattern)
- `_parse_faq`: tabuľka (Otázka|Odpoveď)
- Voľné sekcie (CENOVE PODMIENKY, GEOGRAFIA, …) → `HdsExtractedFact(type="info")` ak nie sú all-`neuvedene`

### Validator
- Phone regex: `^(\+421 \d{2,3} \d{3} \d{3,4}|0\d{3} \d{3} \d{3})$` — SK telefón
- Email regex: štandardný RFC-like
- Address: musí obsahovať aspoň jednu číslicu (filter "Sídlo: Bratislava" halucinácie)
- Product: drop ak `name` prázdne/neuvedene, drop ak `price_eur < 0 || > 1_000_000`
- Product s `price_text="dohodou"` a `price_eur=None` PREJDE — soft pricing podporovaný
- FAQ: drop ak question < 5 chars alebo answer < 3 chars

### Deduplicator
- Products: kľúč = NFKD + lowercase + diacritic strip (Tiger ≡ TIGER ≡ tigér); pri konflikte ponechaj kompletnejší záznam (completeness score = price+description+unit+|attrs|)
- Contacts: kľúč podľa typu — phone (digits only), email (lower), addr (normalized content), social (URL lower)
- FAQs: kľúč = normalizovaná otázka [:60]

### PersonaGenerator
- Oddelený Gemini call (žiadne tools — len text-to-text)
- Temperature 0.3 (vs 0.1 pri extrakcii) — viac kreativity pri synthéze
- Combined markdown zo všetkých úspešných batchov v jednom prompte
- 8 povinných sekcií: PROFIL / SLUŽBY / ŠTÝL / KONVERZAČNÉ PATTERNY / PREDAJNÉ STRATÉGIE / HRANICE / ESKALÁCIA / EMOCIONÁLNE PATTERNY
- Cieľová dĺžka ~2000 slov (max_output_tokens=8192)
- ASCII-only prompt template (žiadna diakritika v prompte → minimum tokens)
- Cost: ~$0.01-0.015 per persona

### HDSv3Engine
- Pipeline: Crawl → Gemini → (Parse → Validate → Dedup) ∥ Persona → results
- Parse/Persona bežia paralelne cez `asyncio.gather` (~30% time saving)
- DB persistence vynechaná v tomto commite — `engine._last_parsed` + `engine._last_persona` ako attributes pre testovanie
- Persist hook (TBD Commit 3.1): bude volať `knowledge_hub.persist_hds_v3_result(parsed, persona, company_id)`

## DB migrácia
- Revision: `a3f9c2d51842`, down_revision: `6eb8936e7f1a` (head pred Commitom 3)
- Tabuľka `brain_personas` (plural) — odlišná od existujúcej `brain_persona` (singular, style config)
- Stĺpce: id BIGSERIAL, company_id UUID indexed, version int default 1, persona_text TEXT, word_count int, source_urls JSONB, gemini_cost_usd NUMERIC(10,6), tokens_in/tokens_out int, meta JSONB default '{}', created_at TZTZ default NOW()
- Index `ix_brain_personas_company_id_version` na `(company_id, version DESC)` pre rýchly fetch najnovšieho
- Žiadny FK na `brain_companies` — táto tabuľka v repe **neexistuje** (existujúce `brain_*` tabuľky mali iba UUID company_id bez FK)
- Apply: `alembic upgrade head` → INFO Running upgrade 6eb8936e7f1a -> a3f9c2d51842

## Testy
- `tests/test_hds_v3_parser.py` (8):
  - extracts_company_name_from_identification
  - extracts_ico
  - extracts_products_table (Tiger 160€ Deň + Rozprávkovo 180€, drops `neuvedene` rows)
  - extracts_contacts_table (phones, address Babín 420, FB URL)
  - extracts_faq_table
  - handles_missing_sections (PRODUKTY only, no crash)
  - handles_neuvedene (all-neuvedene → empty result)
  - splits_multiple_pages (Product A from /p1, Product B from /p2)
- `tests/test_hds_v3_validator.py` (6):
  - drops_product_with_empty_name (+ neuvedene + insanely high price)
  - drops_contact_with_invalid_phone (1234 fail, 0907 043 467 pass)
  - drops_contact_with_invalid_email (garbage-email fail, a@b.sk pass)
  - drops_address_without_numbers (Bratislava-only fails, Babín 420 passes)
  - keeps_dohodou_price (price_eur=None + price_text="dohodou" survives)
  - drops_faq_too_short (Q<5 chars or A<3 chars)
- `tests/test_hds_v3_dedup.py` (4):
  - dedup_products_by_normalized_name (TIGER/Tiger/tigér → 1)
  - dedup_keeps_most_complete_product (full > bare)
  - dedup_contacts_by_phone (0907 043 467 ≡ 0907043467)
  - dedup_faqs_by_question

**Tests: 224 passed** (206 baseline + 18 nové), ~17s.

## Real-API E2E eval výsledky
```
PYTHONPATH=. python3 scripts/eval_hds_v3_full.py
```

| Metrika | Hodnota |
|---|---|
| Pages discovered | 6 |
| Batches total/successful | 2 / 1 (TIER_4 stub WordPress URLs failed — expected) |
| Products extracted | **14** |
| Contacts | 5 (2 phone, 1 address, 2 social) |
| Facts | 15 |
| FAQs | **6** (real questions from najcastejsie-otazky page) |
| Persona generated | True |
| Persona word count | **1436 words** |
| Extraction cost | $0.01335 |
| Persona cost | $0.01082 |
| **Total cost** | **$0.0242** |

### Real produkty extracted (anti-halucinácia overené)
- Skákací Hrad Rozprávkovo 180€/Deň (kapacita 4-8 detí, 8×5m, výška 6m, vek 4-15)
- Skákací Hrad Tiger 160€/Deň (9 detí, 8×6m, výška 4m, vek 2-14)
- Skákací Hrad Indián 160€/Deň
- Balonová bublina 125€/Deň
- Biely Skákací hrad 55€/Deň
- Skákací Hrad Fantázia 160€/Deň
- Skákací Hrad Aladin 160€/Deň
- Skákací Hrad Avengers 160€/Deň
- Stan na prenájom 55€/Deň
- + 5 ďalších (Disney, Panda, Rytier, Pirát, Prenájom Autíčok)

### Reálne kontakty
- Telefón: 0907 043 467, 0911 815 051
- Email: skakaciehradyorava@gmail.com
- Adresa: Babín 420, 02952
- Facebook + Instagram URL

### Persona ukážka (prvých 8 sekcií presne podľa zadania)
- 1. PROFIL FIRMY: identifikuje rodinnú firmu na Orave (Námestovo, Dolný Kubín, Tvrdošín), sídlo Babín 420
- 2. HLAVNÉ SLUŽBY: kompletný produktový zoznam s reálnymi cenami
- 3-8. ŠTÝL, KONVERZAČNÉ PATTERNY, PREDAJNÉ STRATÉGIE, HRANICE, ESKALÁCIA, EMOCIONÁLNE PATTERNY
- Persona uložená v `/tmp/hds_v3_persona.md` pre review

## Verifikácia
- ✅ Alembic migration applied
- ✅ SYNTAX OK na všetkých nových súboroch
- ✅ APP OK, routes: 20 (nezmenené)
- ✅ Tests: 224 passed
- ✅ Real-API E2E: PASS, $0.0242 total cost, 14 products, 6 FAQs, 1436-word persona

## Git
Bez commitu (na želanie). Pripravené na `git add` všetkých nových súborov + úprav existujúcich.

## Ďalej (Commit 3.1 — Persistence)
- Integrate `engine._last_parsed` + `engine._last_persona` s `knowledge_hub.py`
- Mapovanie:
  - `parsed.products` → `BrainProduct` (alebo existujúce `brain_products` tabuľky cez merger.py)
  - `parsed.contacts/facts` → `ExtractedBusinessFact` → `brain_facts`
  - `parsed.faqs` → `brain_faqs`
  - `persona_result` → `brain_personas` (nová tabuľka) cez `BrainPersonaDocument`
- Version bump per company_id (auto-increment latest version + 1)
- Idempotent insert pre konzistenciu pri retry

## Ďalej (Commit 4 — Obrázky)
- Per-product primary_image + sekundárne (galéria)
- Gemini grounding alebo Beautiful Soup z URL fetched cez httpx
- Image hash dedup
- DB tabuľka brain_media už existuje (overené v ls models/)

## Ďalej (Commit 5 — Messenger RAG)
- Pri prichádzajúcej message: fetch latest `brain_personas` pre company_id + relevant chunks
- Persona text + facts/products/FAQs ako system prompt context pre Claude responder
- Existujúci `app/core/responder/` engine (overený v ls models/)

---

# Commit 3.1 — DB persistence (HDS-v3 → brain_facts/brain_faqs/brain_personas)

## Base commit
`c2da0f7` (HDS-v3 Commit 3 — Parser + Validator + Dedup + Persona Generator, 224 testov).

## Cieľ
Pripojiť výsledky HDS-v3 ingest pipeline (engine `_last_parsed` + `_last_persona`) do existujúcich brain tabuliek bez duplikátov a so spätnou kompatibilitou pre existujúci RAG/responder.

## Scope (3 nové súbory + 1 modifikovaný + 1 cleanup)

### NEW
- `app/core/extractors/hds_v3/persistence.py` — `HDSv3Persistence` (UPSERT facts cez raw SQL ON CONFLICT, dedup FAQs cez `_normalize_question`, persona version bump cez `SELECT MAX(version)+1`)
- `tests/test_hds_v3_persistence.py` — 8 offline testov s `AsyncMock(spec=AsyncSession)` (FakeResult side_effect na execute() rozlišuje SELECT key/subject, SELECT question, MAX(version))
- `scripts/eval_hds_v3_persist.py` — real-API E2E (ingest_and_persist → SELECT count facts/faqs/persona)

### MODIFIED
- `app/core/extractors/hds_v3/engine.py` — `HDSv3Engine.ingest_and_persist(base_url, company_id: UUID, session: AsyncSession) -> dict` (volá `ingest()` → `HDSv3Persistence.persist()` → vracia `{**ingest, "persist": persist_result}`)

### CLEANUP
- `5` — stray súbor v repo root, `rm -f 5`

### NETKNUTÉ
- crawler/gemini_client/parser/validator/dedup/persona_generator — Commit 3 logika ostáva
- `knowledge_hub.py` — vlastný legacy pipeline, sa nedotknul (HDS-v3 má vlastný persist endpoint)
- migrations — Commit 3 migration `a3f9c2d51842_hds_v3_brain_personas_doc.py` ostáva ako jedinný head

## Architektúra — adaptácia oproti zadaniu

### Žiadny `superseded_at` v brain_facts/brain_faqs
Zadanie pôvodne navrhovalo "supersede pattern" (SET superseded_at = now() pre staré riadky). Reálna schéma (`app/models/brain_facts.py`, `app/models/brain_faqs.py`) **takú kolónu nemá**. Postupoval som podľa rovnakého patternu, ktorý už používa `app/core/knowledge_hub.py:851`:
- **brain_facts**: UPSERT cez `INSERT ... ON CONFLICT (company_id, key, subject) DO UPDATE` so `value` ako JSONB `{"value": "...", "source_type": "..."}` a `confidence = GREATEST(brain_facts.confidence, EXCLUDED.confidence)`. Pre rozlíšenie nových vs. updated riadkov pred-fetchujem `SELECT key, subject` keys do setu (bookkeeping vs. xmax-trick = portable a deterministický).
- **brain_faqs**: skip-if-question-already-exists (case-insensitive `_normalize_question`).
- **brain_personas**: append-only s monotónne rastúcim `version` (max(version) + 1 per company_id).

Tým ostávajú HDS-v3 zápisy kompatibilné s existujúcim responder/RAG (čítajú `brain_facts.value->>'value'`).

### Mapping ParseResult → BrainFact rows
| ParseResult pole | brain_facts.key | subject seed | source_type | confidence |
|---|---|---|---|---|
| `company_name` | `company_name` | normalizovaný value | `hds_v3` | 0.9 |
| `company_ico` | `ico` | ICO digits | `hds_v3` | 0.9 |
| `company_dic` | `dic` | DIČ string | `hds_v3` | 0.9 |
| `products[i]` | `product` | product name (lowercased, NFKD) | `hds_v3_product` | 0.9 |
| `contacts[i]` | `contact_{type}` | meta.phone/email/url alebo content | `hds_v3_contact` | 0.9 |
| `facts[i]` | `info_{section}` | content[:200] | `hds_v3_info` | 0.75 |

### Mapping persona dict → BrainPersonaDocument
- `persona_text` → `persona_text`
- `word_count` → `word_count`
- `source_urls` → `source_urls` (JSONB)
- `cost_usd` → `gemini_cost_usd` (Numeric(10,6))
- `tokens_in/out` → `tokens_in/out`
- meta: `{"source": "hds_v3", "base_url": <url>}`

## Verifikácia
- ✅ Cleanup `5` súbor odstránený
- ✅ SYNTAX OK na `persistence.py`, `engine.py`, `scripts/eval_hds_v3_persist.py`
- ✅ Imports OK: `from app.core.extractors.hds_v3.persistence import HDSv3Persistence; from app.main import app`
- ✅ Unit testy: **8/8 passed** (`test_hds_v3_persistence.py`, ~0.5s)
- ✅ Full regression: **232 passed** (224 baseline + 8 nové), ~38s
- ⚠️ **Real-API E2E eval BLOCKED**: pri pokuse spustiť `scripts/eval_hds_v3_persist.py` proti Supabase sa zistilo, že tabuľka `brain_personas` na Supabase **neexistuje** — Alembic migration `a3f9c2d51842` z Commitu 3 bola aplikovaná **iba na lokálny Docker** (Supabase ostala na head `6eb8936e7f1a`). Pokus `alembic upgrade head` proti Supabase bol blokovaný auto-classifier-om (production DDL bez explicitnej autorizácie). **Akcia pre používateľa pred prvým eval-om**:
  ```bash
  set -a; . ./.env; set +a
  export ALEMBIC_DATABASE_URL="$DATABASE_URL_SUPABASE"
  alembic upgrade head   # apply a3f9c2d51842 (brain_personas)
  export DATABASE_URL="$DATABASE_URL_SUPABASE"
  PYTHONPATH=. python3 scripts/eval_hds_v3_persist.py
  ```

## Changelog (6 bodov)
1. **Nové súbory**: `app/core/extractors/hds_v3/persistence.py` (HDSv3Persistence class), `tests/test_hds_v3_persistence.py` (8 testov), `scripts/eval_hds_v3_persist.py` (real-API E2E).
2. **`HDSv3Engine.ingest_and_persist(base_url, company_id, session)`** v `app/core/extractors/hds_v3/engine.py` — orchestruje ingest + persistence v jednom volaní.
3. **UPSERT pattern pre brain_facts** (ON CONFLICT company_id+key+subject DO UPDATE), **dedup pattern pre brain_faqs** (case-insensitive question existence check) — **superseded_at column NEEXISTUJE**, prispôsobenie sa existujúcej schéme + `knowledge_hub.py` štýlu.
4. **Persona version bump** cez `SELECT COALESCE(MAX(version), 0) + 1` per company_id — prvý ingest = v1, opakovaný ingest = v2, v3, ...
5. **Cleanup stray súbor "5"** v repo root.
6. **Real-API eval status**: ZADRŽANÝ kvôli chýbajúcej brain_personas tabuľke na Supabase (Commit 3 migration neaplikovaná na produkčnú DB). Bez DB migrácie: pipeline by zlyhal pri `INSERT INTO brain_personas`. Po manuálnom `alembic upgrade head` proti Supabase je eval pripravený na spustenie.

## Git
Bez commitu (na želanie). Pripravené na `git add` `app/core/extractors/hds_v3/persistence.py`, `app/core/extractors/hds_v3/engine.py`, `tests/test_hds_v3_persistence.py`, `scripts/eval_hds_v3_persist.py`.

