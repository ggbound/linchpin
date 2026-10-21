"""Atlas M4 (FR-5.6) — deferred tool loading via the tool_search meta-tool.

Covers the keyword ranking in ``search_tool_definitions`` and the
run_session integration: threshold-gated meta-tool injection, activation
bookkeeping across turns, and rejection of tools that were never
activated by a search.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import (
    Agent,
    BuiltinToolConfig,
    BuiltinToolItemConfig,
    ModelConfig,
)
from app.orchestrator import (
    TOOL_SEARCH_DEF,
    run_session,
    search_tool_definitions,
)
from app.providers import ContentBlock, ModelResponse
from tests.conftest import model_response_to_stream_chunks


_SESSION_ID = str(uuid.uuid4())
_AGENT_ID = str(uuid.uuid4())
_ENV_ID = str(uuid.uuid4())

_USAGE = {
    "input_tokens": 10,
    "output_tokens": 5,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}


def _make_agent(tools=None) -> Agent:
    return Agent(
        id=_AGENT_ID,
        name="test-agent",
        version=1,
        model=ModelConfig(provider="openrouter", id="anthropic/claude-sonnet-4"),
        system="You are helpful.",
        tools=tools or [],
        mcp_servers=[],
        created_at=datetime.now(timezone.utc),
    )


def _make_session_row(metadata=None, status="idle"):
    return {
        "id": uuid.UUID(_SESSION_ID),
        "agent_id": uuid.UUID(_AGENT_ID),
        "agent_version": None,
        "environment_id": uuid.UUID(_ENV_ID),
        "status": status,
        "container_id": "container-abc123",
        "title": None,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "archived_at": None,
        "last_event_cursor": None,
        "ttl_seconds": None,
        "stats": {"total_events": 0, "tool_calls": 0, "model_turns": 0},
        "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }


def _listen_once():
    """Return an async generator that yields one notification then stops."""
    async def _gen(channel):
        yield "new_events"
    return _gen


def _catalog_25():
    """25-tool catalog. idx 4 name+desc hit 'deploy'; idx 9 desc only;
    idx 14 is the ``bash`` tool; the rest are filler."""
    tools = []
    for i in range(25):
        if i == 4:
            tools.append({
                "name": "kubectl_apply",
                "description": "Deploy Kubernetes manifests to a cluster.",
                "input_schema": {"type": "object", "properties": {}},
            })
        elif i == 9:
            tools.append({
                "name": "misc_nine",
                "description": "Deploy helper for legacy stacks.",
                "input_schema": {"type": "object", "properties": {}},
            })
        elif i == 14:
            tools.append({
                "name": "bash",
                "description": "Run shell commands in the sandbox.",
                "input_schema": {"type": "object", "properties": {}},
            })
        else:
            tools.append({
                "name": f"misc_{i:02d}",
                "description": f"Assorted utility number {i}.",
                "input_schema": {"type": "object", "properties": {}},
            })
    return tools


def _tool_response(name: str, tool_input: dict, tool_use_id: str = "tu1") -> ModelResponse:
    return ModelResponse(
        content=[
            ContentBlock(
                type="tool_use",
                tool_use_id=tool_use_id,
                tool_name=name,
                tool_input=tool_input,
            ),
        ],
        stop_reason="tool_use",
        usage=dict(_USAGE),
    )


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(
        content=[ContentBlock(type="text", text=text)],
        stop_reason="end_turn",
        usage=dict(_USAGE),
    )


# ---------------------------------------------------------------------------
# search_tool_definitions unit tests
# ---------------------------------------------------------------------------


class TestSearchToolDefinitions:
    """Keyword ranking: name weight 2 > description weight 1, best first."""

    def test_empty_query_returns_no_matches(self):
        assert search_tool_definitions("", _catalog_25()) == []

    def test_whitespace_query_returns_no_matches(self):
        assert search_tool_definitions("   ", _catalog_25()) == []

    def test_no_match_returns_empty(self):
        assert search_tool_definitions("nonexistent_widget", _catalog_25()) == []

    def test_name_match_outranks_description_match(self):
        catalog = [
            {"name": "kubectl_apply", "description": "Deploys Kubernetes manifests."},
            {"name": "misc_deployer", "description": "Helper for legacy stacks."},
        ]
        matches = search_tool_definitions("deploy", catalog)
        # misc_deployer matches in name (+2); kubectl_apply only in
        # description (+1). Name weight wins even though kubectl_apply
        # comes first in the catalog (an idx tie-break would favor it).
        assert [t["name"] for t in matches] == ["misc_deployer", "kubectl_apply"]

    def test_description_only_match_is_returned(self):
        catalog = [{"name": "unrelated", "description": "Deploys containers."}]
        matches = search_tool_definitions("deploys", catalog)
        assert [t["name"] for t in matches] == ["unrelated"]

    def test_multi_term_or_match_is_case_insensitive(self):
        catalog = _catalog_25()
        matches = search_tool_definitions("BASH SANDBOX", catalog)
        # "bash" hits the name (+2), "sandbox" hits the description (+1).
        assert [t["name"] for t in matches] == ["bash"]

    def test_limit_truncates_results(self):
        catalog = _catalog_25()
        matches = search_tool_definitions("utility", catalog, limit=3)
        assert len(matches) == 3
        assert all("misc" in t["name"] for t in matches)

    def test_default_limit_is_10(self):
        catalog = [{"name": f"util_{i}", "description": "A utility tool."} for i in range(15)]
        matches = search_tool_definitions("util", catalog)
        assert len(matches) == 10

    def test_ties_break_by_catalog_order(self):
        catalog = [
            {"name": "beta", "description": "A helper."},
            {"name": "alpha", "description": "A helper."},
            {"name": "gamma", "description": "A helper."},
        ]
        matches = search_tool_definitions("helper", catalog)
        assert [t["name"] for t in matches] == ["beta", "alpha", "gamma"]

    def test_returns_copies_not_catalog_refs(self):
        catalog = [{"name": "bash", "description": "Run commands."}]
        matches = search_tool_definitions("bash", catalog)
        matches[0]["name"] = "mutated"
        assert catalog[0]["name"] == "bash"


# ---------------------------------------------------------------------------
# run_session integration
# ---------------------------------------------------------------------------


class TestRunSessionToolSearch:
    """Threshold-gated meta-tool injection and activation bookkeeping."""

    @pytest.mark.asyncio
    async def test_search_activates_tool_for_next_turn(self):
        agent = _make_agent(tools=[
            BuiltinToolConfig(
                type="builtin",
                configs=[
                    BuiltinToolItemConfig(name="bash", permission_policy="always_allow"),
                ],
            ),
        ])
        session_row = _make_session_row(
            metadata={"tool_search": {"enabled": True, "threshold": 20}},
        )
        catalog = _catalog_25()

        responses = [
            _tool_response("tool_search", {"query": "bash sandbox"}),
            _tool_response("bash", {"command": "ls"}, tool_use_id="tu2"),
            _text_response("Found and ran it."),
        ]
        chunks = [model_response_to_stream_chunks(r) for r in responses]
        send_calls: list[tuple] = []
        call_idx = {"n": 0}

        async def fake_send_streaming(*args, **kwargs):
            send_calls.append(args)
            call_idx["n"] += 1
            for chunk in chunks[call_idx["n"] - 1]:
                yield chunk

        mock_provider = MagicMock()
        mock_provider.send_streaming = fake_send_streaming

        appended_events = []

        async def mock_append(sid, etype, payload):
            appended_events.append((etype, payload))
            return MagicMock(session_id=sid, cursor="c1", seq=1, type=etype, payload=payload, processed_at=None)

        load_call_count = {"n": 0}

        async def mock_load_session(sid):
            load_call_count["n"] += 1
            # 3 model turns × 2 loads each; 7th load ends the loop.
            if load_call_count["n"] <= 6:
                return session_row
            return {**session_row, "status": "terminated"}

        mock_dispatch = AsyncMock(
            return_value=('{"stdout": "file.txt", "stderr": "", "exit_code": 0}', "agent.tool_result"),
        )

        sandbox = MagicMock()

        with (
            patch("app.orchestrator.load_session", side_effect=mock_load_session),
            patch("app.orchestrator.load_agent", new_callable=AsyncMock, return_value=agent),
            patch("app.orchestrator.listen", side_effect=_listen_once()),
            patch("app.orchestrator.append_event", side_effect=mock_append),
            patch("app.orchestrator.transition", new_callable=AsyncMock),
            patch("app.orchestrator.update_usage", new_callable=AsyncMock),
            patch("app.orchestrator.get_provider", return_value=mock_provider),
            patch(
                "app.orchestrator.build_tool_definitions_with_mcp",
                new_callable=AsyncMock,
                return_value=catalog,
            ),
            patch("app.orchestrator.dispatch_tool", mock_dispatch),
            patch("app.orchestrator.check_for_interrupt", new_callable=AsyncMock, return_value=False),
            patch("app.orchestrator.build_context", new_callable=AsyncMock, return_value=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Run ls"},
            ]),
        ):
            await run_session(_SESSION_ID, sandbox)

        assert call_idx["n"] == 3, f"expected 3 model calls, got {call_idx['n']}"
        assert load_call_count["n"] == 7

        # Turn 1: only the meta-tool rides on the request.
        assert [t["name"] for t in send_calls[0][2]] == ["tool_search"]

        # Turn 2: meta-tool plus the bash tool activated by the search.
        assert [t["name"] for t in send_calls[1][2]] == ["tool_search", "bash"]

        # Turn 3: activation persists.
        assert [t["name"] for t in send_calls[2][2]] == ["tool_search", "bash"]

        # The interception wrote the matches as the tool_result payload.
        tool_results = [p for et, p in appended_events if et == "agent.tool_result"]
        first_payload = json.loads(tool_results[0]["result"])
        assert [t["name"] for t in first_payload["tools"]] == ["bash"]
        assert "note" not in first_payload  # matches found → no retry note

        # bash went through real dispatch exactly once.
        mock_dispatch.assert_awaited_once()
        assert mock_dispatch.call_args[0][1].tool_name == "bash"

    @pytest.mark.asyncio
    async def test_unactivated_tool_is_rejected_before_dispatch(self):
        agent = _make_agent(tools=[
            BuiltinToolConfig(
                type="builtin",
                configs=[
                    BuiltinToolItemConfig(name="bash", permission_policy="always_allow"),
                ],
            ),
        ])
        session_row = _make_session_row(
            metadata={"tool_search": {"enabled": True, "threshold": 20}},
        )
        catalog = _catalog_25()

        responses = [
            # Model calls bash directly without searching first.
            _tool_response("bash", {"command": "ls"}),
            _text_response("Understood — I'll search first next time."),
        ]
        chunks = [model_response_to_stream_chunks(r) for r in responses]
        send_calls: list[tuple] = []
        call_idx = {"n": 0}

        async def fake_send_streaming(*args, **kwargs):
            send_calls.append(args)
            call_idx["n"] += 1
            for chunk in chunks[call_idx["n"] - 1]:
                yield chunk

        mock_provider = MagicMock()
        mock_provider.send_streaming = fake_send_streaming

        appended_events = []

        async def mock_append(sid, etype, payload):
            appended_events.append((etype, payload))
            return MagicMock(session_id=sid, cursor="c1", seq=1, type=etype, payload=payload, processed_at=None)

        load_call_count = {"n": 0}

        async def mock_load_session(sid):
            load_call_count["n"] += 1
            if load_call_count["n"] <= 4:  # 2 turns × 2 loads
                return session_row
            return {**session_row, "status": "terminated"}

        mock_dispatch = AsyncMock()

        sandbox = MagicMock()

        with (
            patch("app.orchestrator.load_session", side_effect=mock_load_session),
            patch("app.orchestrator.load_agent", new_callable=AsyncMock, return_value=agent),
            patch("app.orchestrator.listen", side_effect=_listen_once()),
            patch("app.orchestrator.append_event", side_effect=mock_append),
            patch("app.orchestrator.transition", new_callable=AsyncMock),
            patch("app.orchestrator.update_usage", new_callable=AsyncMock),
            patch("app.orchestrator.get_provider", return_value=mock_provider),
            patch(
                "app.orchestrator.build_tool_definitions_with_mcp",
                new_callable=AsyncMock,
                return_value=catalog,
            ),
            patch("app.orchestrator.dispatch_tool", mock_dispatch),
            patch("app.orchestrator.check_for_interrupt", new_callable=AsyncMock, return_value=False),
            patch("app.orchestrator.build_context", new_callable=AsyncMock, return_value=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Run ls"},
            ]),
        ):
            await run_session(_SESSION_ID, sandbox)

        assert call_idx["n"] == 2

        # The request carried only the meta-tool — bash was never activated.
        assert [t["name"] for t in send_calls[0][2]] == ["tool_search"]

        # The tool_result is the rejection error, and dispatch never ran.
        tool_results = [p for et, p in appended_events if et == "agent.tool_result"]
        assert len(tool_results) == 1
        assert "not activated" in tool_results[0]["error"]
        assert "tool_search" in tool_results[0]["error"]
        mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_catalog_below_threshold_sends_everything(self):
        agent = _make_agent()
        session_row = _make_session_row(
            metadata={"tool_search": {"enabled": True, "threshold": 20}},
        )
        small_catalog = [
            {"name": f"tool_{i}", "description": f"Tool {i}."} for i in range(5)
        ]

        send_calls: list[tuple] = []
        call_idx = {"n": 0}

        async def fake_send_streaming(*args, **kwargs):
            send_calls.append(args)
            call_idx["n"] += 1
            for chunk in model_response_to_stream_chunks(_text_response("Done.")):
                yield chunk

        mock_provider = MagicMock()
        mock_provider.send_streaming = fake_send_streaming

        load_call_count = {"n": 0}

        async def mock_load_session(sid):
            load_call_count["n"] += 1
            if load_call_count["n"] <= 2:
                return session_row
            return {**session_row, "status": "terminated"}

        sandbox = MagicMock()

        with (
            patch("app.orchestrator.load_session", side_effect=mock_load_session),
            patch("app.orchestrator.load_agent", new_callable=AsyncMock, return_value=agent),
            patch("app.orchestrator.listen", side_effect=_listen_once()),
            patch("app.orchestrator.append_event", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.orchestrator.transition", new_callable=AsyncMock),
            patch("app.orchestrator.update_usage", new_callable=AsyncMock),
            patch("app.orchestrator.get_provider", return_value=mock_provider),
            patch(
                "app.orchestrator.build_tool_definitions_with_mcp",
                new_callable=AsyncMock,
                return_value=small_catalog,
            ),
            patch("app.orchestrator.check_for_interrupt", new_callable=AsyncMock, return_value=False),
            patch("app.orchestrator.build_context", new_callable=AsyncMock, return_value=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ]),
        ):
            await run_session(_SESSION_ID, sandbox)

        assert call_idx["n"] == 1
        sent = send_calls[0][2]
        assert [t["name"] for t in sent] == [t["name"] for t in small_catalog]
        assert "tool_search" not in [t["name"] for t in sent]

    @pytest.mark.asyncio
    async def test_no_tool_search_metadata_sends_full_catalog(self):
        """25 tools but no tool_search config → search mode stays off."""
        agent = _make_agent()
        session_row = _make_session_row(metadata={})
        catalog = _catalog_25()

        send_calls: list[tuple] = []
        call_idx = {"n": 0}

        async def fake_send_streaming(*args, **kwargs):
            send_calls.append(args)
            call_idx["n"] += 1
            for chunk in model_response_to_stream_chunks(_text_response("Done.")):
                yield chunk

        mock_provider = MagicMock()
        mock_provider.send_streaming = fake_send_streaming

        load_call_count = {"n": 0}

        async def mock_load_session(sid):
            load_call_count["n"] += 1
            if load_call_count["n"] <= 2:
                return session_row
            return {**session_row, "status": "terminated"}

        sandbox = MagicMock()

        with (
            patch("app.orchestrator.load_session", side_effect=mock_load_session),
            patch("app.orchestrator.load_agent", new_callable=AsyncMock, return_value=agent),
            patch("app.orchestrator.listen", side_effect=_listen_once()),
            patch("app.orchestrator.append_event", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.orchestrator.transition", new_callable=AsyncMock),
            patch("app.orchestrator.update_usage", new_callable=AsyncMock),
            patch("app.orchestrator.get_provider", return_value=mock_provider),
            patch(
                "app.orchestrator.build_tool_definitions_with_mcp",
                new_callable=AsyncMock,
                return_value=catalog,
            ),
            patch("app.orchestrator.check_for_interrupt", new_callable=AsyncMock, return_value=False),
            patch("app.orchestrator.build_context", new_callable=AsyncMock, return_value=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ]),
        ):
            await run_session(_SESSION_ID, sandbox)

        assert call_idx["n"] == 1
        sent = send_calls[0][2]
        assert len(sent) == 25
        assert "tool_search" not in [t["name"] for t in sent]
