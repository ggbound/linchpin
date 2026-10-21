"""Atlas B1 — context compaction tests (PRD FR-2.2).

Covers:
- estimate_tokens (CJK-aware heuristic)
- get_context_window (ModelConfig → env → default fallback chain)
- render_transcript (lightweight transcript rendering)
- maybe_compact (threshold gate, boundary selection, nested coverage,
  provider failure tolerance, missing-DB tolerance)
- build_context replay (summary injection, covered-skip, latest-wins)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.compaction import (
    DEFAULT_CONTEXT_WINDOW,
    estimate_tokens,
    get_context_window,
    maybe_compact,
    render_transcript,
)
from app.models import Agent, ModelConfig
from app.orchestrator import build_context
from app.providers import ContentBlock, ModelResponse, ProviderError

_SESSION_ID = str(uuid.uuid4())
_AGENT_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(context_window: int | None = None) -> Agent:
    return Agent(
        id=_AGENT_ID,
        name="test-agent",
        version=1,
        model=ModelConfig(
            provider="openrouter",
            id="anthropic/claude-sonnet-4",
            context_window=context_window,
        ),
        system="You are helpful.",
        tools=[],
        mcp_servers=[],
        created_at=datetime.now(timezone.utc),
    )


def _make_session_row(context_estimate: int = 0) -> dict:
    return {
        "id": uuid.UUID(_SESSION_ID),
        "context_estimate": context_estimate,
        "status": "running",
    }


def _event(seq: int, event_type: str, payload: dict) -> dict:
    return {"seq": seq, "type": event_type, "payload": payload}


def _covered_events() -> list[dict]:
    """A small history: two complete turns, the last one with a tool pair."""
    return [
        _event(1, "user.message", {"content": "hello"}),
        _event(2, "agent.message", {"content": "hi there"}),
        _event(3, "user.message", {"content": "run the tool"}),
        _event(4, "agent.tool_use", {"tool_use_id": "t1", "name": "bash", "input": {"cmd": "ls"}}),
        _event(5, "agent.tool_result", {"tool_use_id": "t1", "result": "a.txt"}),
        _event(6, "agent.message", {"content": "done"}),
    ]


def _mock_provider(summary: str = "Summary of early turns") -> MagicMock:
    provider = MagicMock()
    provider.send = AsyncMock(return_value=ModelResponse(
        content=[ContentBlock(type="text", text=summary)],
        stop_reason="end_turn",
        usage={"input_tokens": 100, "output_tokens": 50,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    ))
    return provider


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_is_zero(self):
        assert estimate_tokens("") == 0

    def test_ascii_is_quarter(self):
        # 8 ASCII chars → 2 tokens
        assert estimate_tokens("abcdefgh") == 2

    def test_cjk_is_one_each(self):
        assert estimate_tokens("你好世界") == 4

    def test_mixed(self):
        # 4 CJK + 8 ASCII → 4 + 2
        assert estimate_tokens("你好世界abcdefgh") == 6


# ---------------------------------------------------------------------------
# get_context_window
# ---------------------------------------------------------------------------


class TestGetContextWindow:
    def test_model_config_wins(self):
        agent = _make_agent(context_window=4000)
        with patch.dict(os.environ, {"LINCHPIN_CONTEXT_WINDOW": "999999"}):
            assert get_context_window(agent) == 4000

    def test_env_fallback(self):
        agent = _make_agent(context_window=None)
        with patch.dict(os.environ, {"LINCHPIN_CONTEXT_WINDOW": "8000"}):
            assert get_context_window(agent) == 8000

    def test_default_when_neither(self):
        agent = _make_agent(context_window=None)
        with patch.dict(os.environ, {"LINCHPIN_CONTEXT_WINDOW": ""}):
            assert get_context_window(agent) == DEFAULT_CONTEXT_WINDOW


# ---------------------------------------------------------------------------
# render_transcript
# ---------------------------------------------------------------------------


class TestRenderTranscript:
    def test_renders_core_events_and_skips_thinking(self):
        events = [
            _event(1, "user.message", {"content": "hi"}),
            _event(2, "agent.thinking", {"thinking": "private"}),
            _event(3, "agent.message", {"content": "hello"}),
        ]
        text = render_transcript(events)
        assert "[1] user: hi" in text
        assert "[3] assistant: hello" in text
        assert "private" not in text

    def test_tool_pair_rendering(self):
        events = [
            _event(4, "agent.tool_use", {"tool_use_id": "t1", "name": "bash", "input": {"cmd": "ls"}}),
            _event(5, "agent.tool_result", {"tool_use_id": "t1", "result": "a.txt"}),
            _event(6, "agent.tool_result", {"tool_use_id": "t2", "error": "boom"}),
        ]
        text = render_transcript(events)
        assert "tool_use bash" in text
        assert "t1: a.txt" in text
        assert "t2: error: boom" in text

    def test_json_string_payload_tolerated(self):
        text = render_transcript([
            {"seq": 1, "type": "user.message", "payload": '{"content": "hi"}'},
        ])
        assert "[1] user: hi" in text


# ---------------------------------------------------------------------------
# maybe_compact
# ---------------------------------------------------------------------------


class TestMaybeCompact:
    @pytest.mark.asyncio
    async def test_below_threshold_no_op(self):
        """Estimate under threshold → no provider call, no event."""
        provider = _mock_provider()
        with patch("app.compaction.fetch_one", new=AsyncMock(return_value=_make_session_row(1000))), \
             patch("app.compaction.fetch_all", new=AsyncMock(return_value=_covered_events())), \
             patch("app.compaction.append_event", new=AsyncMock()) as mock_append:
            result = await maybe_compact(_SESSION_ID, _make_agent(context_window=100_000), provider)
        assert result is False
        provider.send.assert_not_called()
        mock_append.assert_not_called()

    @pytest.mark.asyncio
    async def test_triggers_and_persists(self):
        """Estimate ≥ 80% of window → summary generated, compacted event
        written up to the turn boundary before the last user.message."""
        provider = _mock_provider()
        agent = _make_agent(context_window=4000)  # threshold = 3200
        with patch("app.compaction.fetch_one", new=AsyncMock(return_value=_make_session_row(3600))), \
             patch("app.compaction.fetch_all", new=AsyncMock(return_value=_covered_events())), \
             patch("app.compaction.append_event", new=AsyncMock()) as mock_append, \
             patch("app.compaction.execute", new=AsyncMock()) as mock_execute:
            result = await maybe_compact(_SESSION_ID, agent, provider, api_key="sk-test")

        assert result is True
        provider.send.assert_awaited_once()
        # Last uncovered user.message is seq 3 → boundary = 2.
        mock_append.assert_awaited_once()
        event_type, payload = mock_append.await_args.args[1], mock_append.await_args.args[2]
        assert event_type == "session.compacted"
        assert payload["summary"] == "Summary of early turns"
        assert payload["covered_seq_start"] == 1
        assert payload["covered_seq_end"] == 2
        assert payload["model"] == {"provider": "openrouter", "id": "anthropic/claude-sonnet-4"}
        assert payload["usage"]["output_tokens"] == 50
        # api_key flows through to the provider call
        assert provider.send.await_args.kwargs.get("api_key") == "sk-test"
        # Estimate recalibrated after compaction
        mock_execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_min_covered_events_skip(self):
        """Covered interval with a single event → skip (churn guard)."""
        provider = _mock_provider()
        events = [
            _event(1, "user.message", {"content": "only one turn boundary"}),
            _event(2, "user.message", {"content": "next turn"}),
        ]
        with patch("app.compaction.fetch_one", new=AsyncMock(return_value=_make_session_row(5000))), \
             patch("app.compaction.fetch_all", new=AsyncMock(return_value=events)), \
             patch("app.compaction.append_event", new=AsyncMock()) as mock_append:
            result = await maybe_compact(_SESSION_ID, _make_agent(context_window=4000), provider)
        assert result is False
        provider.send.assert_not_called()
        mock_append.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_uncovered_user_message_skip(self):
        """No uncovered user.message → no safe boundary → skip."""
        provider = _mock_provider()
        events = [_event(1, "agent.message", {"content": "orphan assistant turn"})]
        with patch("app.compaction.fetch_one", new=AsyncMock(return_value=_make_session_row(5000))), \
             patch("app.compaction.fetch_all", new=AsyncMock(return_value=events)), \
             patch("app.compaction.append_event", new=AsyncMock()) as mock_append:
            result = await maybe_compact(_SESSION_ID, _make_agent(context_window=4000), provider)
        assert result is False
        mock_append.assert_not_called()

    @pytest.mark.asyncio
    async def test_nested_resummarization_uses_previous_summary(self):
        """A second compaction re-summarizes the previous summary together
        with the newly covered events (latest-wins nested coverage)."""
        provider = _mock_provider("Second summary")
        agent = _make_agent(context_window=4000)
        events = [
            _event(1, "session.compacted", {
                "summary": "First summary of turns 1-2",
                "covered_seq_start": 1,
                "covered_seq_end": 2,
            }),
            _event(3, "user.message", {"content": "continue"}),
            _event(4, "agent.message", {"content": "ok"}),
            _event(5, "user.message", {"content": "more"}),
            _event(6, "agent.message", {"content": "more ok"}),
            _event(7, "user.message", {"content": "latest"}),
        ]
        with patch("app.compaction.fetch_one", new=AsyncMock(return_value=_make_session_row(5000))), \
             patch("app.compaction.fetch_all", new=AsyncMock(return_value=events)), \
             patch("app.compaction.append_event", new=AsyncMock()) as mock_append:
            result = await maybe_compact(_SESSION_ID, agent, provider)

        assert result is True
        # boundary = last uncovered user.message (seq 7) − 1 = 6
        event_type, payload = mock_append.await_args.args[1], mock_append.await_args.args[2]
        assert payload["covered_seq_start"] == 3
        assert payload["covered_seq_end"] == 6
        # The provider prompt must contain the previous summary.
        sent_messages = provider.send.await_args.args[0]
        user_text = sent_messages[-1]["content"]
        assert "First summary of turns 1-2" in user_text

    @pytest.mark.asyncio
    async def test_provider_failure_returns_false(self):
        """Provider failure → compaction aborts, nothing persisted."""
        provider = MagicMock()
        provider.send = AsyncMock(side_effect=ProviderError("boom"))
        with patch("app.compaction.fetch_one", new=AsyncMock(return_value=_make_session_row(5000))), \
             patch("app.compaction.fetch_all", new=AsyncMock(return_value=_covered_events())), \
             patch("app.compaction.append_event", new=AsyncMock()) as mock_append:
            result = await maybe_compact(_SESSION_ID, _make_agent(context_window=4000), provider)
        assert result is False
        mock_append.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_db_pool_returns_false(self):
        """No DB pool (unit-test reality for run_session) → RuntimeError is
        swallowed and compaction degrades to a no-op."""
        with patch("app.compaction.fetch_one", side_effect=RuntimeError("pool not initialized")):
            result = await maybe_compact(_SESSION_ID, _make_agent(), _mock_provider())
        assert result is False


# ---------------------------------------------------------------------------
# build_context — compaction replay
# ---------------------------------------------------------------------------


@pytest.fixture()
def _memory_block():
    with patch("app.orchestrator._build_memory_system_block", new=AsyncMock(return_value="")):
        yield


class TestBuildContextReplay:
    @pytest.mark.asyncio
    async def test_summary_injected_and_covered_skipped(self, _memory_block):
        events = [
            _event(1, "user.message", {"content": "old question"}),
            _event(2, "agent.message", {"content": "old answer"}),
            _event(3, "session.compacted", {
                "summary": "earlier stuff",
                "covered_seq_start": 1,
                "covered_seq_end": 2,
            }),
            _event(4, "user.message", {"content": "next"}),
            _event(5, "agent.message", {"content": "answer"}),
        ]
        with patch("app.orchestrator.fetch_all", new=AsyncMock(return_value=events)):
            messages = await build_context(_SESSION_ID, _make_agent())

        assert messages[0] == {"role": "system", "content": "You are helpful."}
        assert messages[1] == {
            "role": "system",
            "content": "<session_summary>\nearlier stuff\n</session_summary>",
        }
        # Covered events (seq 1-2) skipped; the compacted event itself and
        # everything after replay normally.
        assert {"role": "user", "content": "next"} in messages
        assert {"role": "assistant", "content": "answer"} in messages
        assert "old question" not in json.dumps(messages)
        assert len(messages) == 4

    @pytest.mark.asyncio
    async def test_latest_compaction_wins(self, _memory_block):
        """Two compacted events → the one with the largest covered_seq_end
        is effective; the older one is folded away."""
        events = [
            _event(1, "user.message", {"content": "ancient"}),
            _event(2, "session.compacted", {
                "summary": "old summary", "covered_seq_start": 1, "covered_seq_end": 1,
            }),
            _event(3, "user.message", {"content": "mid"}),
            _event(4, "agent.message", {"content": "mid answer"}),
            _event(5, "session.compacted", {
                "summary": "new summary", "covered_seq_start": 1, "covered_seq_end": 4,
            }),
            _event(6, "user.message", {"content": "fresh"}),
        ]
        with patch("app.orchestrator.fetch_all", new=AsyncMock(return_value=events)):
            messages = await build_context(_SESSION_ID, _make_agent())

        summaries = [m for m in messages if "session_summary" in str(m.get("content", ""))]
        assert len(summaries) == 1
        assert "new summary" in summaries[0]["content"]
        assert "old summary" not in json.dumps(messages)
        # Only the fresh user message replays.
        assert {"role": "user", "content": "fresh"} in messages
        assert "mid answer" not in json.dumps(messages)

    @pytest.mark.asyncio
    async def test_json_string_compacted_payload(self, _memory_block):
        """asyncpg returns jsonb as str by default — replay must tolerate it."""
        events = [
            _event(1, "user.message", {"content": "old"}),
            {"seq": 2, "type": "session.compacted",
             "payload": '{"summary": "str payload", "covered_seq_start": 1, "covered_seq_end": 1}'},
            _event(3, "user.message", {"content": "new"}),
        ]
        with patch("app.orchestrator.fetch_all", new=AsyncMock(return_value=events)):
            messages = await build_context(_SESSION_ID, _make_agent())

        assert any("str payload" in str(m.get("content", "")) for m in messages)
        assert {"role": "user", "content": "old"} not in messages
