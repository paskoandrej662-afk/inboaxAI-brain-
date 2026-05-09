web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: arq app.workers.ingest_worker.IngestWorker
