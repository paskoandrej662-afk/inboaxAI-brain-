# Worker service na Railway

Worker beží ako samostatný Railway service v rovnakom projekte ako web.

## Nastavenie (jednorazovo)
1. V Railway projekte klikni "+ New" → "GitHub Repo" → vyber inboaxAI-brain-
2. V Settings nového service-u nastav Custom Start Command:
   `arq app.workers.ingest_worker.IngestWorker`
3. Skopíruj všetky env premenné z web service (DATABASE_URL, REDIS_URL, OPENAI_API_KEY, ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ENVIRONMENT=production)
4. Deploy

## Overenie
V logoch worker service-u vidíš `[arq] Starting worker for X functions`. Posli ingest request cez Web UI a sleduj progress v UI.
