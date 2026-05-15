"""Tests for hds_v3 GeminiClient — offline, all API calls mocked."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.extractors.hds_v3.gemini_client import GeminiClient
from app.core.extractors.hds_v3.types import DiscoveredPage, PagePriority


def _page(url: str) -> DiscoveredPage:
    return DiscoveredPage(
        url=url,
        priority=PagePriority.TIER_1_CRITICAL,
        discovered_via="test",
    )


def test_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiClient()


@pytest.mark.asyncio
async def test_extract_pages_empty_returns_error():
    client = GeminiClient(api_key="test-key")
    result = await client.extract_pages("https://x.sk/", [])
    assert result.success is False
    assert result.error == "no_pages_to_extract"


@pytest.mark.asyncio
async def test_extract_pages_calls_gemini_per_batch():
    client = GeminiClient(api_key="test-key")

    fake_response = MagicMock()
    fake_response.text = "fake markdown output"
    fake_response.usage_metadata = MagicMock(
        prompt_token_count=1000,
        candidates_token_count=500,
    )
    client._gemini_call = AsyncMock(return_value=fake_response)

    pages = [_page(f"https://x.sk/p{i}") for i in range(6)]
    result = await client.extract_pages("https://x.sk/", pages)

    assert result.success is True
    assert result.total_batches == 2
    assert result.successful_batches == 2
    assert client._gemini_call.call_count == 2
    assert result.total_cost_usd > 0


@pytest.mark.asyncio
async def test_batch_retries_on_failure():
    client = GeminiClient(api_key="test-key")

    fake_success = MagicMock()
    fake_success.text = "ok"
    fake_success.usage_metadata = MagicMock(
        prompt_token_count=100, candidates_token_count=50
    )

    call_count = 0

    async def flaky_call(prompt):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("API error")
        return fake_success

    client._gemini_call = flaky_call
    client.RETRY_BACKOFF_SEC = [0, 0, 0]

    pages = [_page("https://x.sk/")]
    result = await client.extract_pages("https://x.sk/", pages)

    assert result.success is True
    assert call_count == 3
    assert result.batches[0].retry_count == 2


@pytest.mark.asyncio
async def test_batch_fails_after_max_retries():
    client = GeminiClient(api_key="test-key")
    client._gemini_call = AsyncMock(side_effect=RuntimeError("permanent error"))
    client.RETRY_BACKOFF_SEC = [0, 0, 0]

    pages = [_page("https://x.sk/")]
    result = await client.extract_pages("https://x.sk/", pages)

    assert result.success is False
    assert result.failed_batches == 1


@pytest.mark.asyncio
async def test_cost_calculation():
    client = GeminiClient(api_key="test-key")

    fake_response = MagicMock()
    fake_response.text = "ok"
    fake_response.usage_metadata = MagicMock(
        prompt_token_count=10_000,
        candidates_token_count=5_000,
    )
    client._gemini_call = AsyncMock(return_value=fake_response)

    pages = [_page("https://x.sk/")]
    result = await client.extract_pages("https://x.sk/", pages)

    # 10k * $0.30/M + 5k * $2.50/M = 0.003 + 0.0125 = $0.0155
    assert result.total_cost_usd == pytest.approx(0.0155, abs=0.0001)


@pytest.mark.asyncio
async def test_response_text_fallback_to_candidates():
    """Ak response.text je None, fallback na candidates[0].content.parts[*].text."""
    client = GeminiClient(api_key="test-key")

    fake_part = MagicMock()
    fake_part.text = "fallback markdown content"

    fake_content = MagicMock()
    fake_content.parts = [fake_part]

    fake_candidate = MagicMock()
    fake_candidate.content = fake_content
    fake_candidate.finish_reason = "STOP"

    fake_response = MagicMock()
    fake_response.text = None
    fake_response.candidates = [fake_candidate]
    fake_response.usage_metadata = MagicMock(
        prompt_token_count=100,
        candidates_token_count=50,
    )

    client._gemini_call = AsyncMock(return_value=fake_response)

    pages = [_page("https://x.sk/")]
    result = await client.extract_pages("https://x.sk/", pages)

    assert result.success is True
    assert "fallback markdown content" in result.batches[0].markdown


def test_gemini_call_configures_both_grounding_tools():
    """Regression: _gemini_call must enable BOTH google_search AND url_context.

    url_context is required to actually fetch the URL contents (vs google_search
    which only returns cached snippets). Without url_context Gemini halucinates.
    """
    source = inspect.getsource(GeminiClient._gemini_call)
    assert "google_search=" in source
    assert "url_context=" in source
    assert "GoogleSearch()" in source
    assert "UrlContext()" in source


@pytest.mark.asyncio
async def test_response_completely_empty_raises():
    """Ak ani text ani candidates parts nie su k dispozicii, raises empty error."""
    client = GeminiClient(api_key="test-key")

    fake_response = MagicMock()
    fake_response.text = None
    fake_response.candidates = []

    client._gemini_call = AsyncMock(return_value=fake_response)
    client.RETRY_BACKOFF_SEC = [0, 0, 0]

    pages = [_page("https://x.sk/")]
    result = await client.extract_pages("https://x.sk/", pages)

    assert result.success is False
    assert "empty" in (result.batches[0].error or "").lower()
