"""Signature test pre `app/core/ingest_v2/orchestrator.py`."""
import inspect

from app.core.ingest_v2.orchestrator import ingest_company_v2


def test_orchestrator_signature():
    params = list(inspect.signature(ingest_company_v2).parameters.keys())
    for r in ("job_id", "company_id", "source_url", "mode", "budget_eur"):
        assert r in params
