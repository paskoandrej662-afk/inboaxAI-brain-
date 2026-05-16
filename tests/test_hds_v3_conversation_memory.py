"""Tests for HDS-v3 ConversationMemory — offline, all LLM calls mocked."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractors.hds_v3.conversation_memory import (
    ConversationMemory,
    ConversationState,
)


class FakeRow:
    def __init__(self, summary, msgs, count):
        self._t = (summary, msgs, count)

    def __getitem__(self, i):
        return self._t[i]


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


def _company_id() -> UUID:
    return UUID("a1d921f7-3e08-4efd-8769-cf6517d0a29d")


def _make_session(initial_row=None):
    """Build a fake AsyncSession.

    Mutable state holds the current row, so UPSERTs and re-fetches stay
    consistent across calls within a single test.
    """
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    state = {"row": initial_row}
    executed: list[tuple[str, dict]] = []

    async def execute(statement, params=None):
        sql = str(statement)
        executed.append((sql, params or {}))
        sql_lower = sql.lower()
        if "select summary_text" in sql_lower:
            row = state["row"]
            if row is None:
                return FakeResult(row=None)
            return FakeResult(
                row=FakeRow(
                    row.get("summary_text"),
                    row.get("last_messages") or [],
                    row.get("message_count") or 0,
                )
            )
        if "insert into brain_customer_memory" in sql_lower:
            msgs = params.get("msgs")
            if isinstance(msgs, str):
                try:
                    msgs = json.loads(msgs)
                except Exception:
                    msgs = []
            state["row"] = {
                "summary_text": params.get("summary"),
                "last_messages": msgs,
                "message_count": params.get("count"),
            }
            return FakeResult()
        return FakeResult()

    session.execute = AsyncMock(side_effect=execute)
    session._executed = executed  # type: ignore[attr-defined]
    session._state = state  # type: ignore[attr-defined]
    return session


@pytest.mark.asyncio
async def test_load_returns_empty_state_when_no_row():
    memory = ConversationMemory(api_key="test-key")
    session = _make_session(initial_row=None)

    state = await memory.load(session, _company_id(), "ext_001")

    assert isinstance(state, ConversationState)
    assert state.summary_text is None
    assert state.recent_messages == []
    assert state.message_count == 0


@pytest.mark.asyncio
async def test_append_below_threshold_keeps_all_raw():
    memory = ConversationMemory(api_key="test-key")
    memory._summarize = AsyncMock(side_effect=AssertionError("should not be called"))
    session = _make_session(initial_row=None)

    # 4 new messages, threshold = 7 -> no summarization
    new = [
        {"role": "user", "text": "ahoj"},
        {"role": "assistant", "text": "ahoj!"},
        {"role": "user", "text": "máte tigra?"},
        {"role": "assistant", "text": "áno"},
    ]
    await memory.append_and_maybe_summarize(
        session, _company_id(), "ext_001", new
    )

    state = await memory.load(session, _company_id(), "ext_001")
    assert state.summary_text is None
    assert len(state.recent_messages) == 4
    assert state.message_count == 4
    memory._summarize.assert_not_called()


@pytest.mark.asyncio
async def test_append_above_threshold_triggers_summarize():
    memory = ConversationMemory(api_key="test-key")
    memory._summarize = AsyncMock(return_value="Zákazník sa zaujíma o tigra.")
    session = _make_session(initial_row=None)

    new = [
        {"role": "user", "text": f"msg-{i}"} for i in range(8)
    ]
    await memory.append_and_maybe_summarize(
        session, _company_id(), "ext_001", new
    )

    state = await memory.load(session, _company_id(), "ext_001")
    assert state.summary_text == "Zákazník sa zaujíma o tigra."
    assert len(state.recent_messages) == ConversationMemory.KEEP_RECENT
    assert state.message_count == 8
    memory._summarize.assert_awaited_once()
    # Ensure to_summarize = combined[:-KEEP_RECENT] -> 3 messages
    args, _ = memory._summarize.call_args
    _, to_summarize = args
    assert len(to_summarize) == 8 - ConversationMemory.KEEP_RECENT


@pytest.mark.asyncio
async def test_message_count_increments():
    memory = ConversationMemory(api_key="test-key")
    memory._summarize = AsyncMock(return_value="summary")
    session = _make_session(initial_row=None)

    # First batch (3 messages, below threshold)
    await memory.append_and_maybe_summarize(
        session,
        _company_id(),
        "ext_001",
        [{"role": "user", "text": f"a-{i}"} for i in range(3)],
    )
    state1 = await memory.load(session, _company_id(), "ext_001")
    assert state1.message_count == 3

    # Second batch (2 more, still below threshold)
    await memory.append_and_maybe_summarize(
        session,
        _company_id(),
        "ext_001",
        [{"role": "user", "text": f"b-{i}"} for i in range(2)],
    )
    state2 = await memory.load(session, _company_id(), "ext_001")
    assert state2.message_count == 5

    # Third batch pushes count above threshold
    await memory.append_and_maybe_summarize(
        session,
        _company_id(),
        "ext_001",
        [{"role": "user", "text": f"c-{i}"} for i in range(4)],
    )
    state3 = await memory.load(session, _company_id(), "ext_001")
    assert state3.message_count == 9


@pytest.mark.asyncio
async def test_summary_failure_keeps_old_summary():
    memory = ConversationMemory(api_key="test-key")
    memory._summarize = AsyncMock(side_effect=RuntimeError("LLM down"))
    session = _make_session(
        initial_row={
            "summary_text": "stary suhrn",
            "last_messages": [{"role": "user", "text": "x"}],
            "message_count": 1,
        }
    )

    # Force threshold trip: 1 existing + 8 new = 9 > 7
    new = [{"role": "user", "text": f"m-{i}"} for i in range(8)]
    await memory.append_and_maybe_summarize(
        session, _company_id(), "ext_001", new
    )

    state = await memory.load(session, _company_id(), "ext_001")
    # Summary preserved despite LLM failure
    assert state.summary_text == "stary suhrn"
    # Recent trimmed to KEEP_RECENT
    assert len(state.recent_messages) == ConversationMemory.KEEP_RECENT
    assert state.message_count == 9
