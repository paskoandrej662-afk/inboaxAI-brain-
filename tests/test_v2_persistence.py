"""Strukturalne (signature) testy pre `app/core/ingest_v2/persistence.py` — bez DB."""
import inspect

from app.core.ingest_v2 import persistence as p


def test_create_job_signature():
    params = list(inspect.signature(p.create_job).parameters.keys())
    assert "session" in params
    assert "company_id" in params
    assert "source_url" in params


def test_save_page_signature():
    params = list(inspect.signature(p.save_page).parameters.keys())
    for r in ("session", "job_id", "company_id", "discovered", "render", "raw_data"):
        assert r in params


def test_save_blocks_signature():
    assert "blocks" in inspect.signature(p.save_blocks).parameters


def test_finalize_job_signature():
    sig = inspect.signature(p.finalize_job)
    for r in ("session", "job_id", "status", "pages_visited", "blocks_found"):
        assert r in sig.parameters
