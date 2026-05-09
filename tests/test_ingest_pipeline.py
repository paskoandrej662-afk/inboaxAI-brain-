import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.knowledge_hub import IngestResult, _ingest_url_legacy, ingest_url


def test_ingest_url_signature_intact():
    """ingest_url signature MUSI ostat: (company_id, url, job_id, max_pages=30) -> IngestResult."""
    sig = inspect.signature(ingest_url)
    params = list(sig.parameters.keys())
    assert "company_id" in params
    assert "url" in params
    assert "job_id" in params
    assert "max_pages" in params
    assert sig.parameters["max_pages"].default == 30


def test_ingest_result_backward_compat_fields():
    """IngestResult musi obsahovat povodne polia + nove optional polia."""
    r = IngestResult()
    assert r.chunks_inserted == 0
    assert r.chunks_skipped == 0
    assert r.chunks_superseded == 0
    assert r.facts_inserted == 0
    assert r.faqs_inserted == 0
    assert r.pages_scraped == 0
    assert r.pages_failed == 0
    assert r.errors == []
    # New fields
    assert r.pages_visited == 0
    assert r.pages_succeeded == 0
    assert r.products_inserted == 0
    assert r.images_inserted == 0
    assert r.total_llm_cost_usd == 0.0
    assert r.extraction_run_id is None


def test_ingest_result_to_dict_keeps_legacy_keys():
    r = IngestResult(chunks_inserted=3, facts_inserted=2, pages_scraped=5)
    d = r.to_dict()
    assert d["chunks"] == 3
    assert d["facts"] == 2
    assert d["pages_scraped"] == 5
    assert "errors" in d


@pytest.mark.asyncio
async def test_ingest_url_legacy_fallback_when_vision_disabled():
    """Ak VISION_INGEST_ENABLED=False, ingest_url volá legacy path."""
    fake_result = IngestResult()
    with patch("app.core.knowledge_hub.settings") as mock_settings:
        mock_settings.VISION_INGEST_ENABLED = False
        with patch(
            "app.core.knowledge_hub._ingest_url_legacy",
            new=AsyncMock(return_value=fake_result),
        ) as mock_legacy:
            result = await ingest_url(
                uuid.uuid4(), "https://x.sk", "job-1", max_pages=5
            )
            mock_legacy.assert_called_once()
            assert result is fake_result


@pytest.mark.asyncio
async def test_ingest_url_dispatches_to_vision_when_enabled():
    """Ak VISION_INGEST_ENABLED=True, ingest_url volá vision pipeline."""
    fake_result = IngestResult()
    with patch("app.core.knowledge_hub.settings") as mock_settings:
        mock_settings.VISION_INGEST_ENABLED = True
        with patch(
            "app.core.knowledge_hub._ingest_url_vision",
            new=AsyncMock(return_value=fake_result),
        ) as mock_vision:
            result = await ingest_url(
                uuid.uuid4(), "https://x.sk", "job-2", max_pages=5
            )
            mock_vision.assert_called_once()
            assert result is fake_result


def test_legacy_function_is_importable():
    """_ingest_url_legacy musi byt importovatelne ako module-level funkcia."""
    assert callable(_ingest_url_legacy)
    sig = inspect.signature(_ingest_url_legacy)
    assert list(sig.parameters.keys())[:4] == [
        "company_id",
        "url",
        "job_id",
        "max_pages",
    ]
