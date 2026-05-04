# InboxAI Brain

AI engine za InboxAI platformou. Stiahne firemné weby/FB/IG/PDF, uloží ich ako vector embeddings, generuje odpovede zákazníkom cez Claude Sonnet + RAG, a umožňuje majiteľovi tunovať AI cez "Coach Mode".

Komunikuje s Next.js Web aplikáciou cez REST API. Nasadené na Railway, vyvíjané v GitHub Codespaces.

**Tech stack:** Python 3.12, FastAPI, async SQLAlchemy 2.0, asyncpg, Arq (Redis queue), pgvector, Anthropic SDK, OpenAI SDK, Supabase Storage.

## Quickstart

```bash
# 1. Skopíruj env template a vyplň keys
cp .env.example .env

# 2. Spusti lokálny Postgres (pgvector) + Redis
docker compose up -d

# 3. Nainštaluj Python závislosti
pip install -r requirements.txt --break-system-packages
pip install -r requirements-dev.txt --break-system-packages

# 4. Spusti FastAPI dev server
uvicorn app.main:app --reload

# 5. Over že beží
curl http://localhost:8000/v1/health
# → {"status":"ok","service":"inboxai-brain","environment":"development"}
```

## Project structure

```
app/
  main.py              # FastAPI app, CORS, root endpoint
  config.py            # Pydantic Settings (číta .env)
  api/
    v1/
      health.py        # GET /v1/health
  core/                # business logika (postupne)
  models/              # SQLAlchemy DB modely (Task 2)
requirements.txt       # runtime závislosti
requirements-dev.txt   # dev/test/lint závislosti
docker-compose.yml     # Postgres + Redis pre lokálny dev
Dockerfile             # build pre Railway
.env.example           # template env premenných
```

## Endpoints

- `GET /` — service info
- `GET /v1/health` — healthcheck

## Roadmap

- **Task 1 (toto):** kostra projektu — FastAPI app, config, healthcheck, Docker setup ✅
- **Task 2:** DB schema — SQLAlchemy modely + Alembic migrácie (pgvector tabuľky)
- **Task 3+:** ingestion pipeline, RAG retrieval, Coach Mode, atď.

> DB migrácie a modely zatiaľ neexistujú — pridajú sa v Task 2.
