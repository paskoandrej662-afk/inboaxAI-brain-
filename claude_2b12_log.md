# 2B-12 — HDS-v3 Commit 4: Product image extraction (DOM-agnostic)

## Cieľ
Priradiť každému `ExtractedProduct` z HDS-v3 pipeline:
- `primary_image_url` — najvyššia-confidence URL fotky produktu
- `image_urls` (zoznam až 4 sekundárnych) — ďalšie kandidátne fotky

Univerzálny algoritmus, ktorý funguje na akomkoľvek webe (WordPress,
Webflow, custom HTML), pretože inšpektuje **lineárne poradie** stránky
(images + text nodes) — nie DOM tree ani CSS klasy.

Anti-halucinácia: pri confidence < 0.5 obrázok **neuložíme**.

## Base commit
`b82d491` (HDS-v3 Commit 3.1 — DB persistence + Supabase migration, 232 testov, 14 produktov + persona v DB).

## Scope

### NEW (7 súborov)
- `app/core/extractors/hds_v3/image_extractor.py` — `MediaItem` + `ImageExtractor`
- `app/core/extractors/hds_v3/image_validator.py` — `ImageValidator` (pre-filter + HEAD)
- `app/core/extractors/hds_v3/image_matcher.py` — `ImageMatcher` (4 signály, group_by_product)
- `tests/test_hds_v3_image_extractor.py` — 4 testy
- `tests/test_hds_v3_image_matcher.py` — 10 testov
- `tests/test_hds_v3_image_validator.py` — 8 testov (6 pre_filter + 2 HEAD)
- `scripts/eval_hds_v3_images.py` — real-API E2E

### MODIFIED
- `app/core/extractors/hds_v3/types.py` — pridaný `PageCrawlResult`
- `app/core/extractors/hds_v3/crawler.py` — pridaná metóda `crawl_media_streams(pages)`
- `app/core/extractors/hds_v3/engine.py` — `asyncio.gather(extract_pages, crawl_media_streams)` paralel + `_match_images` step + `images_matched`/`images_total_candidates` v result dict
- `app/core/extractors/hds_v3/persistence.py` — pridané `primary_image_url` + `image_urls` do products `value` JSONB
- `app/core/extractors/types.py` — pridané `image_urls: list[str]` field na `ExtractedProduct`

### NETKNUTÉ
- gemini_client/parser/validator/dedup/persona_generator (Commit 2-3 logika)
- DB schema (URLs idú do JSONB, žiadna nová migration)
- knowledge_hub.py, vision.py, browser.py

## Architektúra

### Pipeline po Commit 4
```
HDSCrawler.discover()                                # 1. sitemap + fallback links
    ↓
asyncio.gather(                                      # 2. parallel:
    GeminiClient.extract_pages(),                    #    a) url_context → markdown
    HDSCrawler.crawl_media_streams(),                #    b) BrowserPool → MediaItem streams
)
    ↓
parse + validate + dedup ∥ persona_generate          # 3. (unchanged)
    ↓
_match_images(parsed, media_pages):                  # 4. NEW
    ImageValidator.pre_filter           # drop junk via regex+dimensions
    ImageMatcher.match_images           # text proximity + signals
    ImageValidator.head_validate (gathered)   # HEAD per unique URL
    ImageMatcher.group_by_product       # primary + ≤4 secondary
    → set prod.image_url + prod.image_urls
    ↓
HDSv3Persistence.persist()                           # 5. UPSERT brain_facts.value JSONB
                                                      #    obsahuje primary_image_url + image_urls
```

### Image matching scoring (max 1.45 raw, capped to 1.0)
| Signal | Boost | Reason |
|---|---|---|
| Proximity (0..1) | base | distance to nearest text mention of product, window=10 |
| Filename match | +0.20 | `tiger-product.jpg` for product "Tiger" |
| URL path match | +0.15 | `wp-content/uploads/tiger/img.jpg` (skipped if filename hit) |
| Alt text match | +0.10 | `<img alt="Tiger product photo">` |

Score < 0.5 → match je **zahodený** (anti-halucinácia). Po zoskupení per product:
- primary = match s najvyššou confidence
- secondary = ďalšie 4 unikátne URL podľa confidence

### MediaItem stream + DOM-agnostic principle
Stream je lineárny zoznam `MediaItem(position, item_type, …)` kde
`item_type ∈ {"image", "text"}`. Žiadne assumption o `<div class="product-card">`,
Elementor widget hierarchii, Shopify Liquid templates. Pre WordPress aj
Webflow aj custom HTML platí, že produkt + jeho obrázok sú "blízko seba"
v document order.

Production extrakcia stream-u beží cez Playwright `page.evaluate` (handles
`naturalWidth`, `window.getComputedStyle` pre CSS-class hidden imgs).
Offline / test extrakcia beží cez BeautifulSoup `extract_stream_from_html`
(deterministická, žiadne Playwright dependency v testoch).

### Adaptácia oproti zadaniu
- **Brief navrhoval `page.evaluate` ako jedinú entry**: pridal som druhú
  entry `extract_stream_from_html(html)` aby testy mohli pracovať bez
  Playwright runtime-u. Production volá Playwright path, testy BS4 path —
  rovnaký `list[MediaItem]` contract.
- **Brief navrhoval ukladať do `attributes`** dict: namiesto toho som
  pridal first-class fields `image_url` (singular, už existoval) a
  `image_urls` (plural list, nový) na `ExtractedProduct`. Persistence
  emituje oba do `value` JSONB ako top-level kľúče (`primary_image_url`,
  `image_urls`). Toto je čistejšie než stuff cez attribútes a kompatibilné
  s existujúcim responder/RAG ktorý vie čítať `brain_facts.value->>'primary_image_url'`.
- **Brief počítal s render-on-page-by-page v crawler.py**: aktuálny
  `HDSCrawler.discover()` nerenderuje obsah (Gemini robí url_context).
  Pridal som novú metódu `crawl_media_streams(pages)` ktorá iteruje pages
  cez BrowserPool a renderuje každú zvlášť. Engine spúšťa Gemini batches
  a media streams **paralelne** cez `asyncio.gather` — nuluje to extra
  latency na rendering.

## Verifikácia

### Tests
```
PYTHONPATH=. pytest tests/test_hds_v3_image_extractor.py \
                     tests/test_hds_v3_image_matcher.py \
                     tests/test_hds_v3_image_validator.py -v
```
- 22 passed (4 + 10 + 8). Z toho HEAD validation testy bežia cez
  `httpx.MockTransport` (žiadny real network).

### Full regression
```
PYTHONPATH=. pytest tests/ -q
→ 254 passed (232 baseline + 22 nové), ~14s
```

### Real-API E2E eval
```
set -a; . ./.env; set +a
export DATABASE_URL="$DATABASE_URL_SUPABASE"
PYTHONPATH=. python3 scripts/eval_hds_v3_images.py
```

Výsledky proti `https://skakaciehradyorava.sk/`:

| Metric | Hodnota |
|---|---|
| Pages discovered | 6 |
| Batches successful | 2/2 |
| Products extracted | 14 |
| **Image candidates seen** | **124** |
| **Products with primary image** | **14/14** (100% nových produktov) |
| Persona version | v3 (3. ingest) |
| Persona words | 1176 |
| Extraction cost | $0.0198 |
| Persona cost | $0.0100 |
| **Total cost** | **$0.0298** |

Image matching našiel správne WordPress upload URLs pre každý produkt:
- `Skákací Hrad Tiger` → `…/2025/03/skakaci-hrad-tiger-prenajom-names…jpg`
- `Skákací Hrad Aladin` → `…/2025/05/skakaci-hrad-aladin-prenajom-name…jpg`
- `Skákací Hrad Rozprávkovo` → `…/2025/03/rozpravkovo-skakaci-hrad-poziciav…jpg`
- `Skákací Hrad Avengers` → `…/2025/05/nafukovaci-hrad-avenger-dolny-kub…jpg`
- `Stan na prenájom` → `…/2025/08/00886fb0-0e8e-…jpg`
- `Prenájom Autíčok` → `…/2026/04/Auticka-na-poziciavanie.jpg`
- + 8 ďalších

Stale records z predošlých runs (`Autíčka`, `Biely Skákací hrad (Domček)`)
ostávajú bez images v DB — primary key subject sa zmenil medzi ingestmi,
takže UPSERT ich neaktualizuje. Tieto sa časom prečistia keď budeme
implementovať expiry / supersede pre stale subjects (out of scope tohto
commitu).

## Changelog (6 bodov)
1. **Nové súbory** (7): `image_extractor.py`, `image_matcher.py`, `image_validator.py` + 3 test files (22 testov) + `scripts/eval_hds_v3_images.py`.
2. **Crawler integration**: `HDSCrawler.crawl_media_streams(pages) -> list[PageCrawlResult]` rendruje cez BrowserPool + extractuje `MediaItem` stream cez BS4.
3. **Engine integration**: `asyncio.gather(Gemini.extract_pages, crawler.crawl_media_streams)` paralel + nový `_match_images(parsed, media_pages)` step pred persistence.
4. **Universal text-proximity algorithm** (DOM-agnostic): 4 signály (proximity 0..1, filename +0.20, URL +0.15, alt +0.10), confidence < 0.5 zahadzujeme, primary + ≤4 secondary per product.
5. **Pre-filter + HEAD validation**: regex junk patterns (logo/icon/banner/favicon/social/avatar/...) + min 200x200 dim + max 5:1 aspect ratio + HEAD content-type/size check via injected `httpx.AsyncClient`.
6. **Real eval**: 14/14 produktov má primary_image_url, 124 → 14 deterministických matches, total $0.0298.

## Git
Bez commitu (na želanie). Pripravené na `git add` všetkých nových súborov + úprav existujúcich (engine.py, crawler.py, types.py × 2, persistence.py).

## Ďalej (Commit 5 — Messenger RAG)
- Pri prichádzajúcej message: fetch latest `brain_personas` pre `company_id`
- Persona text + relevant `brain_facts` (vrátane `primary_image_url`) + `brain_faqs` ako system prompt context pre Claude responder
- Existujúci `app/core/responder/` engine
- Pri produktovej query bot pošle aj fotku produktu (Messenger attachment API)
