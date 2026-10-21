"""FR-6.1 — subagent declaration surface (``multi_agents``) tests.

Covers: model validation (name format / duplicates / cap), agent CRUD
wiring (create echoes + validates worker refs; PATCH snapshots + dynamic
SET), spawn_subagent tool-definition injection, the fixed ``always_ask``
policy, load_agent(_at_version) roster parsing, and the fail-closed
_spawn_subagent handler.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.models import (
    Agent,
    CreateAgentRequest,
    SubagentConfig,
    UpdateAgentRequest,
)

AUTH = {"Authorization": "Bearer test-secret-key"}

WORKER_ID = str(uuid.uuid4())
AGENT_ID = str(uuid.uuid4())
PARENT_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())

MODEL = {"provider": "openrouter", "id": "anthropic/claude-sonnet-4", "base_url": None}

VALID_PAYLOAD = {
    "name": "coordinator",
    "model": {"provider": "openrouter", "id": "anthropic/claude-sonnet-4"},
    "system": "You coordinate.",
    "tools": [],
    "mcp_servers": [],
}


def _mk_agent(**overrides) -> Agent:
    """Build a validated Agent, optionally carrying a declared roster."""
    base = dict(
        id=str(uuid.uuid4()),
        name="coordinator",
        version=3,
        model=MODEL,
        system="You coordinate.",
        tools=[],
        mcp_servers=[],
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Agent(**base)


def _agent_row(**overrides) -> dict:
    """Fake asyncpg row for the agents table (with multi_agents present)."""
    row = {
        "id": uuid.UUID(AGENT_ID),
        "name": "coordinator",
        "version": 1,
        "model": MODEL,
        "system": "You coordinate.",
        "tools": [],
        "mcp_servers": [],
        "skills": [],
        "multi_agents": [
            {
                "name": "researcher",
                "agent_id": WORKER_ID,
                "environment_id": None,
                "description": "",
            }
        ],
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "description": None,
        "metadata": {},
        "archived_at": None,
    }
    row.update(overrides)
    return row


def _session_row(**overrides) -> dict:
    row = {
        "id": uuid.UUID(PARENT_ID),
        "project_id": uuid.UUID(PROJECT_ID),
        "agent_id": uuid.uuid4(),
        "agent_version": 3,
        "environment_id": uuid.uuid4(),
        "title": "Coordinator session",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


def test_subagent_config_defaults():
    cfg = SubagentConfig(name="researcher", agent_id=WORKER_ID)
    assert cfg.environment_id is None
    assert cfg.description == ""


@pytest.mark.parametrize(
    "bad",
    ["Researcher", "1bad", "has space", "", "bad!name", "a" * 65],
)
def test_subagent_config_rejects_bad_names(bad):
    with pytest.raises(ValueError):
        SubagentConfig(name=bad, agent_id=WORKER_ID)


def test_agent_rejects_duplicate_subagent_names():
    subs = [
        SubagentConfig(name="worker", agent_id=WORKER_ID),
        SubagentConfig(name="worker", agent_id=WORKER_ID),
    ]
    with pytest.raises(ValueError, match="duplicate names"):
        _mk_agent(multi_agents=subs)


def test_agent_rejects_roster_over_cap():
    subs = [
        SubagentConfig(name=f"w{i:02d}", agent_id=WORKER_ID)
        for i in range(17)
    ]
    with pytest.raises(ValueError, match="16-per-agent cap"):
        _mk_agent(multi_agents=subs)


def test_agent_defaults_to_empty_roster():
    assert _mk_agent().multi_agents == []


def test_create_agent_request_accepts_multi_agents():
    req = CreateAgentRequest(
        **VALID_PAYLOAD,
        multi_agents=[SubagentConfig(name="researcher", agent_id=WORKER_ID)],
    )
    assert req.multi_agents[0].name == "researcher"


def test_update_agent_request_allows_none_multi_agents():
    assert UpdateAgentRequest().multi_agents is None


# ---------------------------------------------------------------------------
# POST /v1/agents — echo + worker-ref validation
# ---------------------------------------------------------------------------


@patch("app.routes.agents.fetch_one", new_callable=AsyncMock)
def test_create_agent_echoes_multi_agents(mock_fetch, client: TestClient):
    async def side(sql, *args, **kwargs):
        if "INSERT INTO agents" in sql:
            return _agent_row()
        if "FROM agents WHERE id" in sql:  # _validate_subagent_refs lookup
            return {"id": uuid.UUID(WORKER_ID), "archived_at": None}
        return None

    mock_fetch.side_effect = side

    payload = {
        **VALID_PAYLOAD,
        "multi_agents": [{"name": "researcher", "agent_id": WORKER_ID}],
    }
    resp = client.post("/v1/agents", json=payload, headers=AUTH)

    assert resp.status_code == 201
    assert resp.json()["multi_agents"] == [
        {"name": "researcher", "agent_id": WORKER_ID, "environment_id": None, "description": ""}
    ]

    insert_call = next(
        c for c in mock_fetch.await_args_list if "INSERT INTO agents" in str(c.args[0])
    )
    assert "multi_agents" in insert_call.args[0]
    # $9::jsonb — the serialized roster lands between skills and description.
    assert json.loads(insert_call.args[9]) == [
        {"name": "researcher", "agent_id": WORKER_ID, "environment_id": None, "description": ""}
    ]


@patch("app.routes.agents.fetch_one", new_callable=AsyncMock)
def test_create_agent_rejects_unknown_worker(mock_fetch, client: TestClient):
    async def side(sql, *args, **kwargs):
        if "INSERT INTO agents" in sql:
            return _agent_row()
        return None  # worker lookup misses → 422

    mock_fetch.side_effect = side

    payload = {
        **VALID_PAYLOAD,
        "multi_agents": [{"name": "researcher", "agent_id": WORKER_ID}],
    }
    resp = client.post("/v1/agents", json=payload, headers=AUTH)

    assert resp.status_code == 422
    # Unified error envelope: {"error", "message", "detail"}
    assert "not found" in resp.json()["message"]


def test_create_agent_rejects_invalid_worker_uuid(client: TestClient):
    with patch("app.routes.agents.fetch_one", new_callable=AsyncMock):
        payload = {
            **VALID_PAYLOAD,
            "multi_agents": [{"name": "w", "agent_id": "not-a-uuid"}],
        }
        resp = client.post("/v1/agents", json=payload, headers=AUTH)

    assert resp.status_code == 422
    assert "Invalid subagent agent id" in resp.json()["message"]


# ---------------------------------------------------------------------------
# PATCH /v1/agents/{id} — snapshot + dynamic SET
# ---------------------------------------------------------------------------


@patch("app.routes.agents.execute", new_callable=AsyncMock)
@patch("app.routes.agents.fetch_one", new_callable=AsyncMock)
def test_update_agent_snapshots_and_sets_multi_agents(
    mock_fetch, mock_execute, client: TestClient
):
    async def side(sql, *args, **kwargs):
        if "FOR UPDATE" in sql:
            return _agent_row(multi_agents=[])
        if "UPDATE agents SET" in sql:
            row = _agent_row(version=2)
            row["multi_agents"] = [
                {
                    "name": "researcher",
                    "agent_id": WORKER_ID,
                    "environment_id": None,
                    "description": "",
                }
            ]
            return row
        if "FROM agents WHERE id" in sql:  # _validate_subagent_refs lookup
            return {"id": uuid.UUID(WORKER_ID), "archived_at": None}
        return None

    mock_fetch.side_effect = side

    resp = client.patch(
        f"/v1/agents/{AGENT_ID}",
        json={"multi_agents": [{"name": "researcher", "agent_id": WORKER_ID}]},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json()["multi_agents"][0]["name"] == "researcher"

    snapshot_call = next(
        c for c in mock_execute.await_args_list
        if "INSERT INTO agent_versions" in str(c.args[0])
    )
    assert "multi_agents" in snapshot_call.args[0]
    # $11::jsonb — the *prior* (empty) roster is snapshotted before the SET.
    assert json.loads(snapshot_call.args[11]) == []

    update_call = next(
        c for c in mock_fetch.await_args_list if "UPDATE agents SET" in str(c.args[0])
    )
    assert "multi_agents = $3::jsonb" in update_call.args[0]  # $1 id, $2 project


# ---------------------------------------------------------------------------
# Tool-definition injection + policy
# ---------------------------------------------------------------------------


def test_tool_definitions_include_spawn_subagent():
    from app.orchestrator import build_tool_definitions

    agent = _mk_agent(
        multi_agents=[
            SubagentConfig(
                name="researcher", agent_id=WORKER_ID, description="does research"
            )
        ]
    )
    defs = build_tool_definitions(agent)

    sub = next(d for d in defs if d["name"] == "spawn_subagent")
    assert sub["input_schema"]["properties"]["name"]["enum"] == ["researcher"]
    assert "researcher (does research)" in sub["description"]
    assert sub["input_schema"]["required"] == ["name", "input_message"]


def test_tool_definitions_omit_spawn_subagent_without_roster():
    from app.orchestrator import build_tool_definitions

    defs = build_tool_definitions(_mk_agent())
    assert all(d["name"] != "spawn_subagent" for d in defs)


def test_policy_fixes_spawn_subagent_at_always_ask():
    from app.policy import PolicyEvaluator

    agent = _mk_agent(
        multi_agents=[SubagentConfig(name="w", agent_id=WORKER_ID)]
    )
    assert PolicyEvaluator(agent).evaluate("spawn_subagent") == "always_ask"


def test_policy_denies_spawn_subagent_without_roster():
    from app.policy import PolicyEvaluator

    assert PolicyEvaluator(_mk_agent()).evaluate("spawn_subagent") == "denied"


# ---------------------------------------------------------------------------
# load_agent(_at_version) roster parsing
# ---------------------------------------------------------------------------


@patch("app.orchestrator.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_load_agent_parses_multi_agents(mock_fetch):
    from app.orchestrator import load_agent

    mock_fetch.return_value = {
        "id": uuid.uuid4(),
        "name": "coordinator",
        "version": 2,
        "model": json.dumps(MODEL),
        "system": "s",
        "tools": "[]",
        "mcp_servers": "[]",
        "multi_agents": json.dumps([{"name": "w", "agent_id": WORKER_ID}]),
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }

    agent = await load_agent(str(uuid.uuid4()))

    assert agent.multi_agents[0].name == "w"


@patch("app.orchestrator.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_load_agent_defaults_roster_when_column_missing(mock_fetch):
    from app.orchestrator import load_agent

    mock_fetch.return_value = {
        "id": uuid.uuid4(),
        "name": "coordinator",
        "version": 2,
        "model": json.dumps(MODEL),
        "system": "s",
        "tools": "[]",
        "mcp_servers": "[]",
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }

    agent = await load_agent(str(uuid.uuid4()))

    assert agent.multi_agents == []


@patch("app.orchestrator.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_load_agent_at_version_parses_multi_agents(mock_fetch):
    from app.orchestrator import load_agent_at_version

    mock_fetch.return_value = {
        "agent_id": uuid.uuid4(),
        "version": 1,
        "name": "coordinator",
        "model": MODEL,
        "system": "s",
        "tools": [],
        "mcp_servers": [],
        "multi_agents": [{"name": "w", "agent_id": WORKER_ID}],
        "snapshotted_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }

    agent = await load_agent_at_version(str(uuid.uuid4()), 1)

    assert len(agent.multi_agents) == 1
    assert agent.multi_agents[0].agent_id == WORKER_ID


# ---------------------------------------------------------------------------
# _spawn_subagent handler
# ---------------------------------------------------------------------------


def _pinned_agent() -> Agent:
    return _mk_agent(
        multi_agents=[
            SubagentConfig(
                name="researcher", agent_id=WORKER_ID, description="does research"
            )
        ]
    )


def _make_fetch_side(session_row):
    async def side(sql, *args, **kwargs):
        if "FROM sessions" in sql:
            return session_row
        if "FROM projects" in sql:
            return {"org_id": uuid.uuid4()}
        return None

    return side


@patch("app.routes.sessions.spawn_thread", new_callable=AsyncMock)
@patch("app.orchestrator.load_agent_at_version", new_callable=AsyncMock)
@patch("app.tools.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_spawn_subagent_happy_path(mock_fetch, mock_load, mock_spawn):
    from app.tools import execute_builtin_tool

    session_row = _session_row()
    mock_fetch.side_effect = _make_fetch_side(session_row)
    mock_load.return_value = _pinned_agent()
    mock_spawn.return_value = SimpleNamespace(id=str(uuid.uuid4()), status="running")

    result = await execute_builtin_tool(
        "spawn_subagent",
        {"name": "researcher", "input_message": "go dig"},
        sandbox=None,
        container_id="c0",
        context={"session_id": PARENT_ID, "project_id": PROJECT_ID},
    )

    assert "error" not in result
    assert result["subagent"] == "researcher"
    assert result["status"] == "running"
    assert result["session_id"] == PARENT_ID

    body = mock_spawn.await_args.args[1]  # _SpawnThreadRequest
    assert body.agent_id == WORKER_ID
    assert body.input_message == "go dig"
    assert body.metadata["subagent_name"] == "researcher"
    assert body.metadata["parent_session_id"] == PARENT_ID
    # Declaration leaves environment unset → inherit the parent session's.
    assert body.environment_id == str(session_row["environment_id"])


@patch("app.routes.sessions.spawn_thread", new_callable=AsyncMock)
@patch("app.orchestrator.load_agent_at_version", new_callable=AsyncMock)
@patch("app.tools.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_spawn_subagent_fails_closed_on_undeclared_name(
    mock_fetch, mock_load, mock_spawn
):
    from app.tools import execute_builtin_tool

    mock_fetch.side_effect = _make_fetch_side(_session_row())
    mock_load.return_value = _pinned_agent()

    result = await execute_builtin_tool(
        "spawn_subagent",
        {"name": "intruder", "input_message": "x"},
        sandbox=None,
        container_id="c0",
        context={"session_id": PARENT_ID, "project_id": PROJECT_ID},
    )

    assert "not declared" in result["error"]
    mock_spawn.assert_not_awaited()


@patch("app.routes.sessions.spawn_thread", new_callable=AsyncMock)
@patch("app.orchestrator.load_agent_at_version", new_callable=AsyncMock)
@patch("app.tools.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_spawn_subagent_maps_depth_exceeded_to_error(
    mock_fetch, mock_load, mock_spawn
):
    from app.tools import execute_builtin_tool

    mock_fetch.side_effect = _make_fetch_side(_session_row())
    mock_load.return_value = _pinned_agent()
    mock_spawn.side_effect = HTTPException(
        status_code=422,
        detail={"error": "thread_depth_exceeded", "message": "Thread depth limit reached"},
    )

    result = await execute_builtin_tool(
        "spawn_subagent",
        {"name": "researcher", "input_message": "go dig"},
        sandbox=None,
        container_id="c0",
        context={"session_id": PARENT_ID, "project_id": PROJECT_ID},
    )

    assert "spawn_subagent failed" in result["error"]
    assert "Thread depth limit reached" in result["error"]


@patch("app.tools.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_spawn_subagent_requires_name_and_message(mock_fetch):
    from app.tools import execute_builtin_tool

    result = await execute_builtin_tool(
        "spawn_subagent",
        {"name": "researcher"},
        sandbox=None,
        container_id="c0",
        context={"session_id": PARENT_ID, "project_id": PROJECT_ID},
    )

    assert "input_message" in result["error"]


@patch("app.tools.fetch_one", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_spawn_subagent_requires_session_context(mock_fetch):
    from app.tools import execute_builtin_tool

    result = await execute_builtin_tool(
        "spawn_subagent",
        {"name": "researcher", "input_message": "go"},
        sandbox=None,
        container_id="c0",
        context=None,
    )

    assert "session context" in result["error"]
