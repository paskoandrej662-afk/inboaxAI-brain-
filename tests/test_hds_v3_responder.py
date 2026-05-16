"""Tests for HDS-v3 Responder — offline, all LLM + DB calls mocked."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.conversation_memory import ConversationState
from app.core.extractors.hds_v3.responder import HDSv3Responder
from app.core.extractors.hds_v3.retriever import RetrievedChunk


def _company_id() -> UUID:
    return UUID("a1d921f7-3e08-4efd-8769-cf6517d0a29d")


def _make_persona(version: int = 3, text: str = "Sme firma X, skákacie hrady…"):
    p = MagicMock()
    p.version = version
    p.persona_text = text
    return p


def _make_session(persona=None):
    """Return AsyncSession mock that yields `persona` when responder loads persona."""
    session = AsyncMock(spec=AsyncSession)

    persona_result = MagicMock()
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=persona)
    persona_result.scalars = MagicMock(return_value=scalars)

    session.execute = AsyncMock(return_value=persona_result)
    return session


def _make_retriever(chunks=None):
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(return_value=chunks or [])
    return retriever


def _make_memory(state=None):
    memory = MagicMock()
    memory.load = AsyncMock(return_value=state or ConversationState())
    memory.append_and_maybe_summarize = AsyncMock()
    return memory


def _make_gemini_response(text="ahoj, dakujem za otázku!", in_tok=1200, out_tok=80):
    response = MagicMock()
    response.text = text
    response.usage_metadata = MagicMock(
        prompt_token_count=in_tok,
        candidates_token_count=out_tok,
    )
    return response


@pytest.mark.asyncio
async def test_respond_empty_message_returns_error():
    responder = HDSv3Responder(
        api_key="test-key",
        retriever=_make_retriever(),
        memory=_make_memory(),
    )
    session = _make_session(persona=_make_persona())

    result = await responder.respond(session, _company_id(), "   ")

    assert result.success is False
    assert result.error == "empty_message"


@pytest.mark.asyncio
async def test_respond_no_persona_returns_error():
    responder = HDSv3Responder(
        api_key="test-key",
        retriever=_make_retriever(),
        memory=_make_memory(),
    )
    session = _make_session(persona=None)

    result = await responder.respond(session, _company_id(), "Aká je cena?")

    assert result.success is False
    assert result.error == "no_persona_for_company"


def _fake_session_factory(session):
    """Return a zero-arg callable yielding `session` from an async-with."""
    class _Cm:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return False

    def _factory():
        return _Cm()

    return _factory


@pytest.mark.asyncio
async def test_respond_with_chunks_succeeds():
    chunks = [
        RetrievedChunk(
            text="Produkt: Tiger. Cena: 80€/deň.",
            source_url="https://x.sk/tiger",
            section="product",
            similarity=0.91,
        ),
        RetrievedChunk(
            text="Doprava zadarmo do 30 km.",
            source_url="https://x.sk/doprava",
            section="info_doprava",
            similarity=0.55,
        ),
    ]
    state = ConversationState(
        summary_text="Zákazník Ján sa zaujíma o tigra.",
        recent_messages=[
            {"role": "user", "text": "Ahoj"},
            {"role": "assistant", "text": "Ahoj Ján!"},
        ],
        message_count=2,
    )
    bg_session = AsyncMock(spec=AsyncSession)
    responder = HDSv3Responder(
        api_key="test-key",
        retriever=_make_retriever(chunks=chunks),
        memory=_make_memory(state=state),
        session_factory=_fake_session_factory(bg_session),
    )
    responder._gemini_call = AsyncMock(
        return_value=_make_gemini_response(text="Tiger stojí 80€/deň.")
    )
    session = _make_session(persona=_make_persona(version=4))

    result = await responder.respond(
        session, _company_id(), "Koľko stojí tiger?", customer_id="ext_001"
    )

    assert result.success is True
    assert result.reply_text == "Tiger stojí 80€/deň."
    assert result.persona_version == 4
    assert result.chunks_used == 2
    assert result.memory_recent_count == 2
    assert result.memory_summary_len > 0
    assert result.cost_usd > 0
    # Drain pending background memory update task.
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    responder._memory.append_and_maybe_summarize.assert_awaited_once()


@pytest.mark.asyncio
async def test_respond_cost_calculation():
    responder = HDSv3Responder(
        api_key="test-key",
        retriever=_make_retriever(),
        memory=_make_memory(),
    )
    # 1_000_000 input + 1_000_000 output -> 0.30 + 2.50 = 2.80
    responder._gemini_call = AsyncMock(
        return_value=_make_gemini_response(
            text="ok", in_tok=1_000_000, out_tok=1_000_000
        )
    )
    session = _make_session(persona=_make_persona())

    result = await responder.respond(session, _company_id(), "Hello")

    assert result.success is True
    assert result.input_tokens == 1_000_000
    assert result.output_tokens == 1_000_000
    assert abs(result.cost_usd - 2.80) < 1e-6


@pytest.mark.asyncio
async def test_respond_timeout_returns_error():
    responder = HDSv3Responder(
        api_key="test-key",
        retriever=_make_retriever(),
        memory=_make_memory(),
    )

    async def _slow(system, prompt):
        await asyncio.sleep(60)
        return _make_gemini_response()

    responder._gemini_call = _slow
    responder.TIMEOUT_SEC = 0.1
    session = _make_session(persona=_make_persona())

    result = await responder.respond(session, _company_id(), "Hello")

    assert result.success is False
    assert result.error == "gemini_timeout"
