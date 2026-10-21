"""Orchestrator — one async task per session driving the agent loop.

Constructs conversation context from the event log, sends to the model
provider, processes response content blocks (text, tool_use, thinking),
evaluates tool permissions via PolicyEvaluator, and manages session state
transitions.

Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 6.2, 6.3, 6.4, 6.7, 16.2, 16.4, 17.1, 17.3, 23.1, 23.2, 23.3, 23.4, 6.8
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Literal

import httpx

from app.audit import record_audit
from app.db import execute, fetch_all, fetch_one, listen, notify
# Atlas C2 — cross-replica session lease (advisory lock + heartbeat)
from app.cluster import LeaseHeldElsewhere, SessionLease, get_instance_id
from app.event_crypto import decrypt_payload, get_session_data_key
from app.events import append_event
from app.models import Agent, BuiltinToolConfig, CustomToolConfig, MCPServerConfig, ModelConfig
from app.policy import PolicyEvaluator
from app.providers import (
    ContentBlock,
    ModelResponse,
    ProviderError,
    RetryableError,
    StreamChunk,
    get_provider,
)
from app.sandbox import DockerSandbox, SandboxError
from app.tools import execute_builtin_tool
from app.credentials import CredentialResolver
from app.streaming import start_stream, append_delta, finish_stream
from app.compaction import maybe_compact
# Atlas B2 — in-process per-session interrupt flags
from app import interrupts
# Atlas B10 — OTel tracing hooks (dual-write alongside span.* events)
from app import tracing

logger = logging.getLogger("linchpin-api.orchestrator")

# Atlas C2 — session lease heartbeat cadence and staleness threshold. Each
# lease holder re-stamps sessions.orchestrator_heartbeat every
# HEARTBEAT_INTERVAL_SEC; the lease supervisor reclaims sessions whose
# heartbeat is older than LEASE_TIMEOUT_SEC.
HEARTBEAT_INTERVAL_SEC = float(os.environ.get("LINCHPIN_HEARTBEAT_INTERVAL_SEC", "10"))
LEASE_TIMEOUT_SEC = float(os.environ.get("LINCHPIN_LEASE_TIMEOUT_SEC", "60"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def load_session(session_id: str) -> dict[str, Any]:
    """Fetch a session row from the database."""
    uid = uuid.UUID(session_id)
    row = await fetch_one("SELECT * FROM sessions WHERE id = $1", uid)
    if row is None:
        raise ValueError(f"Session {session_id} not found")
    result: dict[str, Any] = dict(row)
    # Parse vault_ids from JSONB — may be a JSON string or already a list
    vault_ids_raw = result.get("vault_ids")
    if vault_ids_raw is None:
        result["vault_ids"] = []
    elif isinstance(vault_ids_raw, str):
        result["vault_ids"] = json.loads(vault_ids_raw)
    elif isinstance(vault_ids_raw, list):
        result["vault_ids"] = vault_ids_raw
    else:
        result["vault_ids"] = []
    return result


async def load_agent(agent_id: str) -> Agent:
    """Fetch an agent row from the database and parse into an Agent model."""
    uid = uuid.UUID(agent_id)
    row = await fetch_one("SELECT * FROM agents WHERE id = $1", uid)
    if row is None:
        raise ValueError(f"Agent {agent_id} not found")

    tools_raw = row["tools"]
    mcp_raw = row["mcp_servers"]
    model_raw = row["model"]

    tools = json.loads(tools_raw) if isinstance(tools_raw, str) else tools_raw
    mcp_servers = json.loads(mcp_raw) if isinstance(mcp_raw, str) else mcp_raw
    model = json.loads(model_raw) if isinstance(model_raw, str) else model_raw

    # FR-6.1 — keys-check keeps older test mocks (rows without the column)
    # loading as an empty roster.
    multi_agents: list = []
    if "multi_agents" in set(row.keys()):
        raw = row["multi_agents"]
        multi_agents = json.loads(raw) if isinstance(raw, str) else (raw or [])

    return Agent(
        id=str(row["id"]),
        name=row["name"],
        version=row["version"],
        model=ModelConfig(**model),
        system=row["system"],
        tools=tools,
        mcp_servers=mcp_servers,
        multi_agents=multi_agents,
        created_at=row["created_at"],
    )


async def load_agent_at_version(agent_id: str, version: int | None) -> Agent:
    """Fetch the agent config pinned at ``version`` (Atlas A1).

    Sessions pin ``agent_version`` at create time; each turn must load
    that snapshot — not the live agent row — so mid-session PATCHes
    don't retroactively change running or recovering sessions. Reads
    the ``agent_versions`` snapshot written on every PATCH (0006).
    Falls back to the live ``agents`` row when the snapshot is missing
    (historical rows created before versioning existed).
    """
    if version is not None:
        row = await fetch_one(
            "SELECT * FROM agent_versions WHERE agent_id = $1 AND version = $2",
            uuid.UUID(agent_id),
            int(version),
        )
        if row is not None:
            tools_raw = row["tools"]
            mcp_raw = row["mcp_servers"]
            model_raw = row["model"]
            tools = json.loads(tools_raw) if isinstance(tools_raw, str) else tools_raw
            mcp_servers = json.loads(mcp_raw) if isinstance(mcp_raw, str) else mcp_raw
            model = json.loads(model_raw) if isinstance(model_raw, str) else model_raw

            multi_agents = []
            if "multi_agents" in set(row.keys()):
                raw = row["multi_agents"]
                multi_agents = json.loads(raw) if isinstance(raw, str) else (raw or [])

            return Agent(
                id=str(row["agent_id"]),
                name=row["name"],
                version=row["version"],
                model=ModelConfig(**model),
                system=row["system"],
                tools=tools,
                mcp_servers=mcp_servers,
                multi_agents=multi_agents,
                created_at=row["snapshotted_at"],
            )
    return await load_agent(agent_id)



# Atlas A2 — automatic retry budget for retryable provider failures.
MAX_RETRIES = 3


async def transition(
    session_id: str, new_status: str, stop_reason: str | None = None
) -> None:
    """Update a session's status + stop_reason in the database (Atlas A2).

    ``stop_reason`` is written verbatim on every call (NULL clears it) so
    each transition states why the session is in its new state:
    'end_turn', 'requires_action', 'retries_exhausted', 'retrying',
    'provisioning_failed', 'provider_error', 'credential_missing', ...
    """
    uid = uuid.UUID(session_id)
    await fetch_one(
        "UPDATE sessions SET status = $1, stop_reason = $2, updated_at = now() "
        "WHERE id = $3 RETURNING id",
        new_status,
        stop_reason,
        uid,
    )


async def update_usage(session_id: str, usage: dict) -> None:
    """Increment session token usage counters (v0.2.0 item #10).

    Accumulates four counters: prompt input/output and prompt-caching
    create/read. Providers that don't report cache metrics (Ollama, the
    OpenAI Chat Completions shape) leave those at 0, which is correct.

    Defensive cast: existing v0.1 rows have a usage JSONB with only
    `input_tokens` + `output_tokens` keys. ``COALESCE((usage->>'k')::int, 0)``
    handles the missing-key case so the UPDATE doesn't error on rows
    created before this migration. New sessions ship all four keys
    pre-seeded (see ``routes/sessions.py:create_session``).
    """
    uid = uuid.UUID(session_id)
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_creation_input_tokens = usage.get("cache_creation_input_tokens", 0)
    cache_read_input_tokens = usage.get("cache_read_input_tokens", 0)
    await fetch_one(
        """
        UPDATE sessions
        SET usage = jsonb_set(
                jsonb_set(
                    jsonb_set(
                        jsonb_set(usage, '{input_tokens}',
                            to_jsonb(COALESCE((usage->>'input_tokens')::int, 0) + $1)),
                        '{output_tokens}',
                        to_jsonb(COALESCE((usage->>'output_tokens')::int, 0) + $2)),
                    '{cache_creation_input_tokens}',
                    to_jsonb(COALESCE((usage->>'cache_creation_input_tokens')::int, 0) + $3)),
                '{cache_read_input_tokens}',
                to_jsonb(COALESCE((usage->>'cache_read_input_tokens')::int, 0) + $4)),
            updated_at = now()
        WHERE id = $5
        RETURNING id
        """,
        input_tokens,
        output_tokens,
        cache_creation_input_tokens,
        cache_read_input_tokens,
        uid,
    )


# ---------------------------------------------------------------------------
# Connector helpers
# ---------------------------------------------------------------------------

# Tool type classification result
ToolKind = Literal["builtin", "mcp", "custom_http"]


def get_connector_url() -> str:
    """Return the connector base URL from env var (default: http://linchpin-connector:8001)."""
    return os.environ.get("CONNECTOR_URL", "http://linchpin-connector:8001")


# Atlas M4 (FR-5.6) — the meta-tool exposed when tool search mode is
# active. The full catalog is withheld; the model must search first, and
# matches are activated for the remainder of the session run.
TOOL_SEARCH_DEF = {
    "name": "tool_search",
    "description": (
        "Search the full tool catalog by keyword. Use this to discover "
        "tools before calling them — calling a tool that has not been "
        "activated by a previous search will fail."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Space-separated keywords matched against tool names "
                    "and descriptions."
                ),
            },
        },
        "required": ["query"],
    },
}


def search_tool_definitions(
    query: str, all_tools: list[dict[str, Any]], limit: int = 10,
) -> list[dict[str, Any]]:
    """FR-5.6 — keyword search over the assembled tool catalog.

    Space-separated terms are OR-matched case-insensitively against
    name (weight 2) and description (weight 1); matches are returned
    best-first, ties broken by catalog order. An empty query or no hit
    returns an empty list — the caller surfaces a retry note.
    """
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return []
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for idx, tool in enumerate(all_tools):
        name = str(tool.get("name", "")).lower()
        description = str(tool.get("description", "")).lower()
        score = 0.0
        for term in terms:
            if term in name:
                score += 2.0
            if term in description:
                score += 1.0
        if score > 0:
            scored.append((score, idx, tool))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [dict(item[2]) for item in scored[:limit]]


def classify_tool(tool_name: str, agent: Agent) -> ToolKind:
    """Determine whether a tool is built-in, MCP, or custom_http.

    Checks the agent's tool and mcp_servers config to classify the tool.
    - If the tool name matches an MCP server tool pattern (server_name__tool), it's MCP.
    - If the tool matches a CustomToolConfig entry, it's custom_http.
    - Otherwise, it's built-in.
    """
    # Check MCP servers — MCP tools are named as "server_name__tool_name"
    for mcp_server in agent.mcp_servers:
        if tool_name.startswith(f"{mcp_server.name}__"):
            return "mcp"

    # Check custom tools
    for tool_cfg in agent.tools:
        if isinstance(tool_cfg, CustomToolConfig) and tool_cfg.name == tool_name:
            return "custom_http"

    return "builtin"


def _find_mcp_server_for_tool(tool_name: str, agent: Agent) -> MCPServerConfig | None:
    """Find the MCP server config for a tool named 'server__tool'."""
    for mcp_server in agent.mcp_servers:
        if tool_name.startswith(f"{mcp_server.name}__"):
            return mcp_server
    return None


def _find_custom_tool_config(tool_name: str, agent: Agent) -> CustomToolConfig | None:
    """Find the CustomToolConfig for a given tool name."""
    for tool_cfg in agent.tools:
        if isinstance(tool_cfg, CustomToolConfig) and tool_cfg.name == tool_name:
            return tool_cfg
    return None


async def invoke_connector(
    session_id: str,
    tool_type: ToolKind,
    tool_name: str,
    arguments: dict,
    server_name: str | None = None,
    credentials: dict | None = None,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Forward a tool call to the linchpin-connector via POST /tools/invoke.

    Returns the parsed JSON response from the connector.
    """
    url = f"{get_connector_url()}/tools/invoke"
    payload = {
        "session_id": session_id,
        "tool_type": tool_type,
        "tool_name": tool_name,
        "arguments": arguments,
    }
    if server_name is not None:
        payload["server_name"] = server_name
    if credentials is not None:
        payload["credentials"] = credentials
    if endpoint is not None:
        payload["endpoint"] = endpoint

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        return {"error": f"Connector returned {exc.response.status_code}: {exc.response.text}", "status": "error"}
    except httpx.HTTPError as exc:
        return {"error": f"Connector request failed: {exc}", "status": "error"}


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


async def _build_memory_system_block(session_id: uuid.UUID) -> str:
    """v0.3.0 — render the ``<linchpin:memory>`` block for the system
    prompt by joining ``session_resources`` rows (type=memory_store)
    against ``memory_stores`` to grab each store's name + description.

    Returns the empty string when no memory_store resources are mounted.
    """
    from app.memory import render_memory_system_prompt_block

    rows = await fetch_all(
        """
        SELECT sr.config, ms.name, ms.description
        FROM session_resources sr
        JOIN memory_stores ms
             ON ms.id = (sr.config->>'memory_store_id')::uuid
        WHERE sr.session_id = $1
          AND sr.type = 'memory_store'
          AND sr.state = 'mounted'
        ORDER BY sr.created_at ASC
        """,
        session_id,
    )
    if not rows:
        return ""
    stores: list[dict] = []
    for row in rows:
        cfg = row["config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        stores.append({
            "name": row["name"],
            "access": cfg.get("access", "read_only"),
            "description": row["description"],
            "instructions": cfg.get("instructions"),
        })
    return render_memory_system_prompt_block(stores)


async def _resolve_file_sources(content_blocks: list) -> list:
    """v0.5.0 — resolve ``source: {type: "file", file_id}`` shapes to
    inline ``base64`` blocks by reading bytes through the FileStore.

    Provider adapters don't speak Linchpin file ids; resolving here
    keeps them simple. A missing/archived file_id is left as-is so the
    provider error surfaces, mirroring the spec's "trust the provider"
    posture for unsupported combos.
    """
    import base64

    from app.files import get_file_store

    resolved: list = []
    fs = None
    for block in content_blocks:
        if not isinstance(block, dict):
            resolved.append(block)
            continue
        source = block.get("source")
        if not isinstance(source, dict) or source.get("type") != "file":
            resolved.append(block)
            continue
        file_id = source.get("file_id")
        if not file_id:
            resolved.append(block)
            continue
        try:
            file_uid = uuid.UUID(str(file_id))
        except ValueError:
            resolved.append(block)
            continue
        row = await fetch_one(
            "SELECT content_type, storage_path FROM files "
            "WHERE id = $1 AND archived_at IS NULL",
            file_uid,
        )
        if row is None:
            resolved.append(block)
            continue
        if fs is None:
            fs = get_file_store()
        try:
            with open(fs.absolute_path(row["storage_path"]), "rb") as f:
                raw = f.read()
        except OSError:
            resolved.append(block)
            continue
        encoded = base64.b64encode(raw).decode("ascii")
        new_block = dict(block)
        new_block["source"] = {
            "type": "base64",
            "media_type": row["content_type"] or "application/octet-stream",
            "data": encoded,
        }
        resolved.append(new_block)
    return resolved


async def _build_skills_system_block(agent: Agent) -> str:
    """v0.4.0 — render the ``<linchpin:skills>`` block. Skills are
    static-at-agent-time so we read them off the agent object directly
    (the agent passed into build_context is already pinned to the
    session's agent_version snapshot) and look up each id in the
    skills table for the live name + description.

    Missing/archived skills are silently dropped — the materialization
    loop in sessions.py logs the warning at boot; the system prompt
    just omits them so the agent doesn't see ghost skills.
    """
    from app.skills import render_skills_system_prompt_block

    skill_ids = list(agent.skills or [])
    if not skill_ids:
        return ""

    rendered: list[dict[str, object]] = []
    for sid in skill_ids:
        try:
            uid = uuid.UUID(sid)
        except ValueError:
            continue
        row = await fetch_one(
            "SELECT name, description, archived_at FROM skills WHERE id = $1",
            uid,
        )
        if row is None or row["archived_at"] is not None:
            continue
        rendered.append({"name": row["name"], "description": row["description"]})
    return render_skills_system_prompt_block(rendered)


async def build_context(session_id: str, agent: Agent) -> list[dict]:
    """Build the conversation messages list from the session event log.

    Reads all events for the session and converts them into the messages
    format expected by model providers:
    - system message from agent config
    - user.message → user role
    - agent.message → assistant role (text)
    - agent.thinking → assistant role (thinking block, skipped for context)
    - agent.tool_use → assistant role (tool_use block)
    - agent.tool_result → tool result
    """
    uid = uuid.UUID(session_id)
    rows = await fetch_all(
        "SELECT * FROM events WHERE session_id = $1 ORDER BY seq ASC",
        uid,
    )

    messages: list[dict] = []

    # Atlas B1 — find the effective compaction summary: the compacted event
    # covering the most history. Everything at or below its covered_seq_end
    # (older compacted events included) is folded into that summary and
    # skipped during replay.
    covered_end = 0
    compaction_summary: str | None = None
    for row in rows:
        if row["type"] == "session.compacted":
            compacted_payload = row["payload"]
            if isinstance(compacted_payload, str):
                compacted_payload = json.loads(compacted_payload)
            row_covered_end = int(compacted_payload.get("covered_seq_end", 0) or 0)
            if row_covered_end > covered_end:
                covered_end = row_covered_end
                compaction_summary = compacted_payload.get("summary") or None

    # System message first. v0.3.0 — auto-inject the <linchpin:memory>
    # block summarizing every memory_store resource mounted into this
    # session, so the agent learns about its persistent state without
    # the caller having to manage prompts themselves.
    memory_block = await _build_memory_system_block(uid)
    skills_block = await _build_skills_system_block(agent)
    base_system = agent.system or ""
    prelude_parts = [part for part in (skills_block, memory_block) if part]
    if prelude_parts:
        prelude = "\n\n".join(prelude_parts)
        system = f"{prelude}\n\n{base_system}".rstrip() if base_system else prelude
    else:
        system = base_system
    if system:
        messages.append({"role": "system", "content": system})

    # Atlas B1 — inject the effective compaction summary as a system message
    # so the model sees a condensed history in place of the covered prefix.
    if compaction_summary:
        messages.append({
            "role": "system",
            "content": f"<session_summary>\n{compaction_summary}\n</session_summary>",
        })

    # Atlas B8 — encrypted sessions store business-content payloads as
    # {"enc":"v1","ct":...} envelopes; resolve the data key once and
    # decrypt as rows stream through. Control events (session.*) are
    # stored plaintext, so the pre-scan above needs no decryption.
    data_key = await get_session_data_key(uid)

    for row in rows:
        # Atlas B1 — skip events folded into the effective summary.
        if row["seq"] <= covered_end:
            continue
        event_type = row["type"]
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = decrypt_payload(payload, data_key)

        if event_type == "user.message":
            # v0.5.0 — pass content blocks through verbatim so providers
            # can route to multimodal-capable models. Legacy plain-string
            # callers continue to work; the orchestrator does not
            # rewrite their shape. File-source resolution happens here
            # so providers see inline bytes rather than Linchpin-specific
            # file_id references.
            # v0.5.1 — accept payload.text as well as payload.content. The
            # public SDK + README document the user-message shape as
            # {text}; the orchestrator was only reading {content}, causing
            # text-shaped messages to reach the model as empty strings.
            content = payload.get("content")
            if content is None or content == "":
                content = payload.get("text", "")
            if isinstance(content, list):
                content = await _resolve_file_sources(content)
            messages.append({"role": "user", "content": content})

        elif event_type == "user.steer":
            # Atlas B6 — replay steer events as user-role messages wrapped
            # in a <steer> tag and marked injected=true. Steers land in the
            # event log mid-turn (the A4 gate admits user.steer while
            # running); build_context is the single injection point, so
            # both the next tool-round and the post-steer continuation see
            # them in order relative to agent messages and tool results.
            steer_text = payload.get("content")
            if steer_text is None or steer_text == "":
                steer_text = payload.get("text", "")
            if isinstance(steer_text, list):
                steer_text = await _resolve_file_sources(steer_text)
            messages.append({
                "role": "user",
                "content": f'<steer injected="true">{steer_text}</steer>',
            })

        elif event_type == "agent.message":
            messages.append({"role": "assistant", "content": payload.get("content", "")})

        elif event_type == "agent.thinking":
            # Include thinking as a separate block for providers that support it
            pass  # Thinking is informational; not included in context replay

        elif event_type == "agent.tool_use":
            messages.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": payload.get("tool_use_id", ""),
                        "name": payload.get("name", ""),
                        "input": payload.get("input", {}),
                    }
                ],
            })

        elif event_type == "agent.tool_result":
            tool_use_id = payload.get("tool_use_id", "")
            if "error" in payload:
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "is_error": True,
                            "content": payload["error"],
                        }
                    ],
                })
            else:
                result = payload.get("result", "")
                if isinstance(result, dict):
                    result = json.dumps(result)
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": str(result),
                        }
                    ],
                })

        elif event_type in ("agent.mcp_tool_use", "agent.custom_tool_use"):
            # MCP and custom tool_use events map to assistant tool_use blocks
            messages.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": payload.get("tool_use_id", ""),
                        "name": payload.get("name", ""),
                        "input": payload.get("input", {}),
                    }
                ],
            })

        elif event_type == "agent.mcp_tool_result":
            # MCP tool results map to user tool_result blocks
            tool_use_id = payload.get("tool_use_id", "")
            if "error" in payload:
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "is_error": True,
                            "content": payload["error"],
                        }
                    ],
                })
            else:
                result = payload.get("result", "")
                if isinstance(result, dict):
                    result = json.dumps(result)
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": str(result),
                        }
                    ],
                })

    return messages


# ---------------------------------------------------------------------------
# Tool definitions builder
# ---------------------------------------------------------------------------


def build_tool_definitions(agent: Agent) -> list[dict]:
    """Build the tool definitions list from the agent's tool config.

    Returns a list of dicts suitable for passing to model providers.
    Includes built-in tools, custom tools, and MCP tools (discovered via connector).
    """
    tools: list[dict] = []
    for tool in agent.tools:
        if hasattr(tool, "configs"):
            # BuiltinToolConfig
            for cfg in tool.configs:
                if cfg.enabled:
                    tools.append({
                        "name": cfg.name,
                        "description": f"Built-in tool: {cfg.name}",
                        "input_schema": {"type": "object", "properties": {}},
                    })
        elif hasattr(tool, "name"):
            # CustomToolConfig
            tools.append({
                "name": tool.name,
                "description": getattr(tool, "description", ""),
                "input_schema": getattr(tool, "input_schema", {"type": "object", "properties": {}}),
            })
    # FR-6.1 — declared subagents surface a spawn_subagent tool so the
    # model can hand work off to worker threads pinned in agent config.
    if agent.multi_agents:
        roster = ", ".join(
            f"{s.name} ({s.description})" if s.description else s.name
            for s in agent.multi_agents
        )
        tools.append({
            "name": "spawn_subagent",
            "description": (
                "Spawn a worker thread handled by a declared subagent agent. "
                f"Available workers: {roster}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [s.name for s in agent.multi_agents],
                        "description": "Which declared subagent to spawn.",
                    },
                    "input_message": {
                        "type": "string",
                        "description": "Task instruction for the worker thread.",
                    },
                },
                "required": ["name", "input_message"],
            },
        })
    return tools


async def build_tool_definitions_with_mcp(
    agent: Agent, session_id: str, vault_ids: list[str] | None = None,
) -> list[dict]:
    """Build tool definitions including MCP tools discovered from the connector.

    Calls the connector's POST /tools/list for each MCP server to discover
    available tools, then prefixes them with server_name__.
    Resolves vault credentials and passes them as env vars to the MCP server.
    """
    # Start with built-in and custom tools
    tools = build_tool_definitions(agent)

    # Discover MCP tools from each configured server
    if agent.mcp_servers:
        connector_url = get_connector_url()
        for mcp_server in agent.mcp_servers:
            try:
                # Merge agent-configured env with vault credentials
                env = dict(mcp_server.env)
                if vault_ids:
                    mcp_url = env.get("MCP_SERVER_URL") or mcp_server.command or mcp_server.name
                    resolver = CredentialResolver()
                    vault_creds = await resolver.resolve_mcp_credential(vault_ids, mcp_url)
                    if vault_creds:
                        auth_type = vault_creds.get("auth_type")
                        if auth_type == "bearer_token":
                            env["AUTHORIZATION"] = f"Bearer {vault_creds.get('token', '')}"
                        elif auth_type == "oauth":
                            env["AUTHORIZATION"] = f"Bearer {vault_creds.get('access_token', '')}"

                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{connector_url}/tools/list",
                        json={
                            "session_id": session_id,
                            "server_name": mcp_server.name,
                            "command": mcp_server.command or "npx",
                            "args": mcp_server.args,
                            "env": env,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("tools"):
                            for mcp_tool in data["tools"]:
                                tool_name = mcp_tool.get("name", "")
                                tools.append({
                                    "name": f"{mcp_server.name}__{tool_name}",
                                    "description": mcp_tool.get("description", ""),
                                    "input_schema": mcp_tool.get("inputSchema", mcp_tool.get("input_schema", {"type": "object", "properties": {}})),
                                })
                            logger.info(
                                "Discovered %d tools from MCP server '%s' for session %s",
                                len(data["tools"]), mcp_server.name, session_id,
                            )
                        elif data.get("error"):
                            logger.warning(
                                "MCP tools/list error for '%s': %s",
                                mcp_server.name, data["error"],
                            )
            except Exception as exc:
                logger.warning(
                    "Failed to discover MCP tools from '%s' for session %s: %s",
                    mcp_server.name, session_id, exc,
                )

    return tools


# ---------------------------------------------------------------------------
# Confirmation waiter
# ---------------------------------------------------------------------------


async def wait_for_confirmation(session_id: str, tool_use_id: str) -> dict:
    """Block on LISTEN/NOTIFY until a user.tool_confirmation event arrives.

    Returns the confirmation payload dict.
    """
    channel = f"session_{session_id.replace('-', '_')}"
    uid = uuid.UUID(session_id)
    # Atlas B8 — data key for decrypting encrypted confirmation payloads.
    data_key = await get_session_data_key(uid)

    async for _payload in listen(channel):
        # Atlas B2 — requires_action admits user.interrupt, so the wait
        # must break when one lands. Return a deny sentinel: the caller's
        # deny branch appends an error result, and the loop-top DB check
        # then consumes the interrupt and closes the turn out.
        if interrupts.is_interrupted(session_id):
            return {"result": "deny", "deny_message": "Interrupted"}
        # Check for a tool_confirmation event matching our tool_use_id
        rows = await fetch_all(
            """
            SELECT * FROM events
            WHERE session_id = $1 AND type = 'user.tool_confirmation'
            ORDER BY seq DESC LIMIT 10
            """,
            uid,
        )
        for row in rows:
            ev_payload = row["payload"]
            if isinstance(ev_payload, str):
                ev_payload = json.loads(ev_payload)
            # Atlas B8 — user.tool_confirmation is business content and
            # may be stored encrypted; decrypt before matching.
            ev_payload = decrypt_payload(ev_payload, data_key)
            if ev_payload.get("tool_use_id") == tool_use_id:
                return ev_payload

    # Should not reach here in normal operation
    return {"result": "deny", "deny_message": "No confirmation received"}


async def wait_for_custom_tool_result(session_id: str, tool_use_id: str) -> dict:
    """Block on LISTEN/NOTIFY until a user.custom_tool_result event arrives.

    Returns the result payload dict.
    """
    channel = f"session_{session_id.replace('-', '_')}"
    uid = uuid.UUID(session_id)
    # Atlas B8 — data key for decrypting encrypted tool-result payloads.
    data_key = await get_session_data_key(uid)

    async for _payload in listen(channel):
        # Atlas B2 — same interrupt escape hatch as wait_for_confirmation:
        # break the wait, let the loop-top DB check close the turn out.
        if interrupts.is_interrupted(session_id):
            return {"result": "", "error": "Interrupted", "interrupted": True}
        rows = await fetch_all(
            """
            SELECT * FROM events
            WHERE session_id = $1 AND type = 'user.custom_tool_result'
            ORDER BY seq DESC LIMIT 10
            """,
            uid,
        )
        for row in rows:
            ev_payload = row["payload"]
            if isinstance(ev_payload, str):
                ev_payload = json.loads(ev_payload)
            # Atlas B8 — user.custom_tool_result is business content and
            # may be stored encrypted; decrypt before matching.
            ev_payload = decrypt_payload(ev_payload, data_key)
            if ev_payload.get("tool_use_id") == tool_use_id:
                return ev_payload

    return {"result": "", "error": "No custom tool result received"}


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------


async def dispatch_tool(
    session_id: str,
    block: ContentBlock,
    sandbox: DockerSandbox,
    agent: Agent,
    vault_ids: list[str] | None = None,
) -> tuple[str, str]:
    """Route a tool call to the appropriate handler based on tool type.

    Returns a tuple of (result_json, event_type) where event_type is one of:
    - "agent.tool_result" for built-in tools
    - "agent.mcp_tool_result" for MCP tools
    - "agent.tool_result" for custom_http tools (result comes back directly)
    """
    tool_name = block.tool_name or ""
    tool_input = block.tool_input or {}

    try:
        kind = classify_tool(tool_name, agent)

        if kind == "mcp":
            mcp_server = _find_mcp_server_for_tool(tool_name, agent)
            server_name = mcp_server.name if mcp_server else None
            credentials = mcp_server.env if mcp_server else None
            # Strip the server prefix to get the actual tool name for the MCP server
            actual_tool_name = tool_name
            if server_name and tool_name.startswith(f"{server_name}__"):
                actual_tool_name = tool_name[len(server_name) + 2:]

            # Resolve MCP credentials from vaults if available
            if vault_ids and mcp_server:
                # Use the MCP server command as a URL-like identifier for credential lookup
                mcp_url = mcp_server.env.get("MCP_SERVER_URL") or mcp_server.command
                resolver = CredentialResolver()
                vault_creds = await resolver.resolve_mcp_credential(vault_ids, mcp_url)
                if vault_creds is not None:
                    credentials = vault_creds

            connector_resp = await invoke_connector(
                session_id=session_id,
                tool_type="mcp",
                tool_name=actual_tool_name,
                arguments=tool_input,
                server_name=server_name,
                credentials=credentials,
            )
            if connector_resp.get("status") == "error":
                return json.dumps({"error": connector_resp.get("error", "MCP tool invocation failed")}), "agent.mcp_tool_result"
            return json.dumps(connector_resp.get("result", {})), "agent.mcp_tool_result"

        elif kind == "custom_http":
            custom_cfg = _find_custom_tool_config(tool_name, agent)
            endpoint = getattr(custom_cfg, "endpoint", None) if custom_cfg else None

            if endpoint:
                # Tool has an endpoint — forward to connector for direct invocation
                connector_resp = await invoke_connector(
                    session_id=session_id,
                    tool_type="custom_http",
                    tool_name=tool_name,
                    arguments=tool_input,
                    endpoint=endpoint,
                )
                if connector_resp.get("status") == "error":
                    return json.dumps({"error": connector_resp.get("error", "Custom tool invocation failed")}), "agent.tool_result"
                return json.dumps(connector_resp.get("result", {})), "agent.tool_result"
            else:
                # No endpoint — this is a client-handled custom tool.
                # The orchestrator emits agent.custom_tool_use and waits for
                # user.custom_tool_result. Return a sentinel so the caller
                # knows to handle the async flow.
                return "__custom_tool_needs_client__", "agent.custom_tool_use"

        else:
            # Built-in tool
            session = await load_session(session_id)
            container_id = session.get("container_id")
            if not container_id:
                return json.dumps({"error": "No container associated with session"}), "agent.tool_result"

            # Atlas B5: pass session attribution so tools like save_artifact
            # can write API-side resources (files rows) owned by this session.
            context = {
                "session_id": session_id,
                "project_id": str(session["project_id"]) if session.get("project_id") else None,
            }
            result = await execute_builtin_tool(tool_name, tool_input, sandbox, container_id, context)
            return json.dumps(result), "agent.tool_result"

    except Exception as exc:
        return json.dumps({"error": str(exc)}), "agent.tool_result"


# ---------------------------------------------------------------------------
# Main orchestrator loop
# ---------------------------------------------------------------------------


async def check_for_interrupt(session_id: str) -> bool:
    """Check if the most recent event for this session is a user.interrupt.

    Returns True if an interrupt was detected.
    """
    uid = uuid.UUID(session_id)
    row = await fetch_one(
        """
        SELECT type FROM events
        WHERE session_id = $1
        ORDER BY seq DESC LIMIT 1
        """,
        uid,
    )
    return row is not None and row["type"] == "user.interrupt"


async def handle_interrupt(session_id: str) -> None:
    """Transition session to idle with stop_reason='interrupt'."""
    # Atlas B2 — this is an interrupt consumption point: clear the
    # in-process flag so it can't leak into the next streaming turn.
    interrupts.clear_interrupt(session_id)
    await transition(session_id, "idle")
    await append_event(session_id, "session.status_idle", {
        "stop_reason": "interrupt",
    })


async def has_pending_steer(session_id: str, since_seq: int) -> bool:
    """Atlas B6 — True when an unconsumed user.steer exists.

    A steer counts as pending only when it was appended after the
    current turn started (seq > since_seq). Steers at or before the
    watermark were already replayed into context by build_context;
    counting them would make end_turn re-run the agent forever on a
    stale steer.
    """
    uid = uuid.UUID(session_id)
    try:
        row = await fetch_one(
            """
            SELECT 1 FROM events
            WHERE session_id = $1 AND type = 'user.steer' AND seq > $2
            LIMIT 1
            """,
            uid,
            since_seq,
        )
    except Exception:
        # Best-effort (same contract as maybe_compact): steer is advisory.
        # Fail closed — the session still idles normally, it just doesn't
        # get a steer continuation this round.
        logger.warning("has_pending_steer lookup failed for session %s", session_id, exc_info=True)
        return False
    return row is not None


async def _acquire_session_lease(session_id: str) -> SessionLease | None:
    """Try to acquire the cross-replica session lease (Atlas C2).

    Returns the held lease, or ``None`` when running unleased — no DB pool
    (unit tests, single-process mode) or a transient lease-stamp failure
    (in-process locks plus the events-table PK remain the correctness
    story). Raises :class:`LeaseHeldElsewhere` when another live replica
    owns the lease.
    """
    lease = SessionLease(session_id)
    try:
        acquired = await lease.acquire()
    except RuntimeError:
        # No asyncpg pool (unit tests): in-process orchestration task
        # tracking already guarantees a single owner per session.
        return None
    except Exception:
        # Pool/DB hiccup right at acquisition: run unleased rather than
        # fail the session — double-append is guarded by the events PK.
        logger.warning(
            "Session lease unavailable for %s; running unleased", session_id,
            exc_info=True,
        )
        return None
    if not acquired:
        raise LeaseHeldElsewhere(
            f"Session {session_id} is leased by another orchestrator instance"
        )
    return lease


async def _lease_heartbeat(lease: SessionLease, interval: float) -> None:
    """Background task refreshing the lease while the session runs.

    On the first failed renewal the lease marks itself broken; the main
    loop checks at the top of each iteration and exits so the supervisor
    (or another replica) can take the session over.
    """
    while True:
        await asyncio.sleep(interval)
        if not await lease.renew():
            logger.warning(
                "Session %s lease lost (instance %s)",
                lease.session_id, get_instance_id(),
            )
            return


async def run_session(
    session_id: str, sandbox: DockerSandbox, *, lease: SessionLease | None = None
) -> None:
    """Run the orchestrator loop, guarded by a cross-replica session lease.

    Atlas C2: the lease is a Postgres advisory lock bound to a dedicated
    pool connection — it dies with the connection, so a crashed holder can
    never block takeover. A background heartbeat refreshes the lease (and
    ``sessions.orchestrator_heartbeat``) while the loop runs. Pass
    ``lease`` when the caller already holds one (lease supervisor takeover)
    to avoid a release→re-acquire race window.
    """
    if lease is None:
        try:
            lease = await _acquire_session_lease(session_id)
        except LeaseHeldElsewhere:
            logger.info(
                "Session %s leased by another instance; not starting here", session_id,
            )
            return
    heartbeat_task: asyncio.Task | None = None
    if lease is not None:
        heartbeat_task = asyncio.create_task(
            _lease_heartbeat(lease, HEARTBEAT_INTERVAL_SEC)
        )
    try:
        await _run_session_loop(session_id, sandbox, lease)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if lease is not None:
            await lease.release()


async def _run_session_loop(
    session_id: str, sandbox: DockerSandbox, lease: SessionLease | None
) -> None:
    """Main orchestrator loop for a single session.

    Runs as an async task. Loops:
    1. Exit if the lease was lost to another replica (Atlas C2)
    2. Load session, exit if terminal state
    3. Wait for input via LISTEN/NOTIFY
    4. Check for user.interrupt — if so, transition to idle
    5. Build context from event log
    6. Send to model provider
    7. Process response blocks (thinking, text, tool_use)
    8. Between tool calls, check for interrupts
    9. If end_turn, transition to idle; if tool calls, loop back
    """
    logger.info("Orchestrator started for session %s", session_id)

    # Tracks whether the next iteration needs to block on LISTEN/NOTIFY for
    # external input (a fresh user message), or whether we should re-enter
    # the model loop immediately because we just wrote tool_results the model
    # hasn't seen yet. Set to False after a tool dispatch round; True after
    # idle transitions or session start.
    needs_input = True

    # Atlas M4 (FR-5.6) — deferred tool loading state. ``activated`` holds
    # tool names surfaced by earlier tool_search calls; it lives for the
    # whole _run_session_loop invocation (across steer re-entries) and is
    # reset on the next run_session — cheap to re-search, no stale state.
    activated: set[str] = set()
    search_mode = False
    search_max_results = 10

    while True:
        try:
            # Atlas C2 — lease broken (heartbeat couldn't renew): another
            # instance now owns this session; exit instead of double-driving.
            if lease is not None and lease.broken:
                logger.warning("Session %s lease lost; orchestrator exiting.", session_id)
                tracing.end_session_tracing(session_id)
                return

            # 1. Load session from DB
            session = await load_session(session_id)
            status = session["status"]

            if status in ("terminated", "failed"):
                logger.info("Session %s is %s, orchestrator exiting.", session_id, status)
                tracing.end_session_tracing(session_id)
                return

            # 2. Wait for input event via LISTEN/NOTIFY — skipped when we
            # have pending tool_results from the previous iteration that the
            # model still needs to see. Without this skip, the loop would
            # block here forever waiting for a notification that no one is
            # going to send (the orchestrator wrote those events itself).
            channel = f"session_{session_id.replace('-', '_')}"
            if needs_input:
                async for _payload in listen(channel):
                    break  # Got a notification, proceed

            # Re-check status after waking up (or after a no-wait re-entry)
            session = await load_session(session_id)
            status = session["status"]
            if status in ("terminated", "failed"):
                logger.info("Session %s is %s after wake, orchestrator exiting.", session_id, status)
                tracing.end_session_tracing(session_id)
                return

            # 3. Check for user.interrupt before processing
            if await check_for_interrupt(session_id):
                logger.info("Session %s interrupted by user.", session_id)
                await handle_interrupt(session_id)
                continue

            # Transition to running
            await transition(session_id, "running")

            # Atlas B10 — open this iteration's agent.turn span (implicitly
            # closes the previous turn's span). No-op unless OTel is enabled.
            tracing.start_turn(session_id)

            # Atlas B6 — watermark of the current turn's start. A steer is
            # "pending" (unconsumed) only when its seq exceeds this value.
            # Re-taken every iteration: after a steer-triggered
            # continuation the new watermark covers the consumed steer, so
            # it can't trigger another round. Best-effort: 0 on failure —
            # production always has a pool; tests without one degrade to
            # the idle path (has_pending_steer fails closed in parallel).
            try:
                turn_start_row = await fetch_one(
                    "SELECT COALESCE(MAX(seq), 0) AS seq FROM events WHERE session_id = $1",
                    uuid.UUID(session_id),
                )
                turn_start_seq = turn_start_row["seq"] if turn_start_row else 0
            except Exception:
                logger.warning("Turn watermark lookup failed for session %s", session_id, exc_info=True)
                turn_start_seq = 0

            # 4. Load agent config pinned to the session's agent_version
            # (Atlas A1) so mid-session agent PATCHes don't leak into an
            # already-running or recovering session.
            agent = await load_agent_at_version(
                str(session["agent_id"]), session["agent_version"]
            )

            # 5. Resolve API key from vaults (if session has vault_ids).
            # Sessions without vault_ids fall through to the env-var lookup
            # below (and providers that don't need a key, like Ollama, may
            # leave it as None).
            #
            # Atlas B11 — the key name is keyed off the provider string, not
            # the endpoint: a gateway (e.g. LiteLLM) is reached via
            # provider="openrouter" + model.base_url override, so the env
            # fallback stays OPENROUTER_API_KEY and vault credentials match
            # on provider="openrouter" regardless of which upstream serves
            # the request. See docs/model-gateway.md.
            env_var_map = {
                "openrouter": "OPENROUTER_API_KEY",
            }
            env_var_name = env_var_map.get(agent.model.provider)
            env_key = os.environ.get(env_var_name, "") if env_var_name else ""

            resolved_api_key: str | None = None
            vault_ids = session.get("vault_ids") or []
            if vault_ids:
                resolver = CredentialResolver()
                resolved_api_key = await resolver.resolve_api_key(vault_ids, agent.model.provider)

                # Vault-bound session but no credential found — try env fallback.
                if resolved_api_key is None:
                    if env_key:
                        resolved_api_key = env_key
                    elif agent.model.provider != "ollama":
                        await append_event(session_id, "session.error", {
                            "error": f"No API key found for provider '{agent.model.provider}'. "
                                     f"Configure a vault credential or set the "
                                     f"{env_var_name or agent.model.provider.upper() + '_API_KEY'} environment variable.",
                            "source": "credential_resolution",
                        })
                        await transition(session_id, "failed", stop_reason="credential_missing")
                        tracing.end_session_tracing(session_id)
                        return
            else:
                # No vault bound — fall back to env var so the provider has auth.
                resolved_api_key = env_key or None

            # 6. Get model provider (compaction below reuses the resolved key)
            provider = get_provider(agent.model)

            # Atlas B1 — compact the covered prefix of the event log into a
            # summary before rebuilding context when the token estimate is
            # near the model's context window. Best-effort: failure degrades
            # to no compaction and never breaks the agent loop.
            await maybe_compact(session_id, agent, provider, api_key=resolved_api_key)

            # 7. Build conversation context from event log
            messages = await build_context(session_id, agent)
            all_tools = await build_tool_definitions_with_mcp(agent, session_id, vault_ids=vault_ids)

            # Atlas M4 (FR-5.6) — deferred tool loading. When the session's
            # environment enabled tool_search and the assembled catalog is
            # larger than the threshold, only the meta-tool plus previously
            # activated tools ride on the model request.
            ts_cfg = (session.get("metadata") or {}).get("tool_search") \
                if isinstance(session.get("metadata"), dict) else None
            if isinstance(ts_cfg, dict) and ts_cfg.get("enabled"):
                try:
                    threshold = max(1, int(ts_cfg.get("threshold", 20)))
                    search_max_results = max(1, int(ts_cfg.get("max_results", 10)))
                except (TypeError, ValueError):
                    threshold, search_max_results = 20, 10
                search_mode = len(all_tools) > threshold
            else:
                search_mode = False
            if search_mode:
                tools = [dict(TOOL_SEARCH_DEF)] + [
                    t for t in all_tools if t.get("name") in activated
                ]
            else:
                tools = all_tools

            # --- Streaming integration ---
            message_id = str(uuid.uuid4())
            accumulated_text = ""
            content_blocks: list[ContentBlock] = []
            stop_reason: str | None = None
            usage = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
            deltas_emitted = False
            stream_interrupted = False

            # Start in-memory stream for live delta delivery (no DB writes for deltas)
            await start_stream(session_id, message_id)

            # v0.2.0 item #9 — span around the model request. We emit
            # `span.model_request_start` before the streaming call begins and
            # pair it with `span.model_request_end` after the stream
            # completes (success OR failure). `span_id` lets clients pair the
            # two events; `elapsed_ms` is filled in on end. Both emissions
            # are best-effort — span failures must not abort the agent loop.
            span_id = str(uuid.uuid4())
            span_started_at = time.monotonic()
            try:
                await append_event(session_id, "span.model_request_start", {
                    "span_id": span_id,
                    "model": {
                        "provider": agent.model.provider,
                        "id": agent.model.id,
                    },
                })
            except Exception:
                logger.exception("failed to append span.model_request_start")

            # Atlas B10 — mirror the span into OpenTelemetry (no-op unless
            # LINCHPIN_OTEL_ENABLED). Kept outside the append try-block: a
            # tracing failure must never affect the event log.
            tracing.record_model_request_start(
                session_id, span_id, agent.model.provider, agent.model.id,
            )

            span_error: str | None = None
            stream_gen = provider.send_streaming(
                messages, agent.model, tools or None, api_key=resolved_api_key,
            )
            try:
                async for chunk in stream_gen:
                    # Atlas B2 — check the in-process interrupt flag every
                    # chunk. On interrupt, drop the generator: its finally
                    # closes the httpx stream, cancelling the request.
                    if interrupts.is_interrupted(session_id):
                        stream_interrupted = True
                        logger.info("Session %s interrupted mid-stream.", session_id)
                        break
                    if chunk.type == "text_delta":
                        accumulated_text += chunk.text or ""
                        # Write to in-memory buffer only — no DB write
                        await append_delta(session_id, chunk.text or "")
                        deltas_emitted = True
                    elif chunk.type == "thinking":
                        await append_event(session_id, "agent.thinking", {"text": chunk.text or ""})
                    elif chunk.type == "tool_use":
                        content_blocks.append(ContentBlock(
                            type="tool_use",
                            tool_use_id=chunk.tool_use_id,
                            tool_name=chunk.tool_name,
                            tool_input=chunk.tool_input,
                        ))
                    elif chunk.type == "final":
                        stop_reason = chunk.stop_reason
                        usage = chunk.usage or usage
            except Exception as stream_exc:
                span_error = str(stream_exc)
                # Emit the span end on failure too so observability tools see
                # paired start/end events for every request. The full exception
                # is logged + surfaced via session.error below; we don't
                # re-raise from the span emit.
                try:
                    await append_event(session_id, "span.model_request_end", {
                        "span_id": span_id,
                        "model": {
                            "provider": agent.model.provider,
                            "id": agent.model.id,
                        },
                        "model_usage": usage,
                        "elapsed_ms": int((time.monotonic() - span_started_at) * 1000),
                        "error": span_error,
                    })
                except Exception:
                    logger.exception("failed to append span.model_request_end on failure")

                # Atlas B10 — close the OTel model_request span with the error.
                tracing.record_model_request_end(
                    session_id, span_id,
                    usage=usage,
                    elapsed_ms=int((time.monotonic() - span_started_at) * 1000),
                    error=span_error,
                )
                # Clean up the in-memory stream
                asyncio.create_task(finish_stream(session_id))
                if deltas_emitted:
                    logger.error("Mid-stream error in session %s: %s", session_id, stream_exc)
                    await append_event(session_id, "session.error", {
                        "error": str(stream_exc),
                        "source": "streaming",
                    })
                    await transition(session_id, "failed", stop_reason="provider_error")
                    tracing.end_session_tracing(session_id)
                    return
                else:
                    raise
            finally:
                # Atlas B2 — close the async generator so the provider's
                # finally runs (httpx stream aclose) even after a mid-stream
                # interrupt break. No-op when the generator is exhausted.
                try:
                    await stream_gen.aclose()
                except Exception:
                    logger.exception("failed to close streaming generator")

            # Mark stream as done (cleanup happens after delay)
            asyncio.create_task(finish_stream(session_id))

            # v0.2.0 item #9 — span end on the success path, paired with the
            # start emit above by span_id. model_usage is the per-request
            # usage from the final chunk (not the cumulative session total —
            # that's tracked separately via update_usage).
            try:
                await append_event(session_id, "span.model_request_end", {
                    "span_id": span_id,
                    "model": {
                        "provider": agent.model.provider,
                        "id": agent.model.id,
                    },
                    "model_usage": usage,
                    "elapsed_ms": int((time.monotonic() - span_started_at) * 1000),
                })
            except Exception:
                logger.exception("failed to append span.model_request_end")

            # Atlas B10 — close the OTel model_request span (success path).
            tracing.record_model_request_end(
                session_id, span_id,
                usage=usage,
                elapsed_ms=int((time.monotonic() - span_started_at) * 1000),
            )

            # 8. Update usage stats
            await update_usage(session_id, usage)

            # Atlas B1 — calibrate the context estimate from the provider's
            # real usage (input + output + cache). GREATEST keeps the stored
            # estimate monotone across compaction resets. Best-effort: this
            # must never break the agent loop.
            try:
                calibrated = sum(
                    int(usage.get(key, 0) or 0)
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    )
                )
                await execute(
                    "UPDATE sessions SET context_estimate = GREATEST(context_estimate, $1) "
                    "WHERE id = $2",
                    calibrated,
                    uuid.UUID(session_id),
                )
            except Exception:
                logger.exception("failed to calibrate context_estimate")

            # Atlas A2 — a completed model turn resets the retryable-error
            # counter, giving the next failure a fresh retry budget.
            if session.get("retry_count"):
                await execute(
                    "UPDATE sessions SET retry_count = 0 WHERE id = $1",
                    uuid.UUID(session_id),
                )

            # 9. Emit final agent.message with full accumulated content
            if accumulated_text:
                await append_event(session_id, "agent.message", {"content": accumulated_text})

            # Atlas B2 — the stream was interrupted mid-flight. The partial
            # text above is already persisted as agent.message and no tool
            # has run this turn; close out with idle(end_turn) per the plan.
            if stream_interrupted:
                interrupts.clear_interrupt(session_id)
                await transition(session_id, "idle", stop_reason="end_turn")
                await append_event(session_id, "session.status", {
                    "status": "idle",
                    "stop_reason": "end_turn",
                })
                await append_event(session_id, "session.status_idle", {
                    "stop_reason": "end_turn",
                })
                needs_input = True
                continue

            # 10. Process tool_use content blocks
            has_tool_calls = len(content_blocks) > 0
            interrupted = False
            for block in content_blocks:
                # Check for interrupt between content blocks
                if await check_for_interrupt(session_id):
                    logger.info("Session %s interrupted during tool execution.", session_id)
                    await handle_interrupt(session_id)
                    interrupted = True
                    break

                # Classify the tool to determine the correct event types
                tool_kind = classify_tool(block.tool_name or "", agent)

                # Emit the appropriate tool_use event
                if tool_kind == "mcp":
                    use_event_type = "agent.mcp_tool_use"
                elif tool_kind == "custom_http":
                    use_event_type = "agent.custom_tool_use"
                else:
                    use_event_type = "agent.tool_use"

                await append_event(session_id, use_event_type, {
                    "tool_use_id": block.tool_use_id or "",
                    "name": block.tool_name or "",
                    "input": block.tool_input or {},
                })

                # Atlas M4 (FR-5.6) — tool search interception. Handled
                # inline (before policy evaluation: the meta-tool is not in
                # agent.tools, so the evaluator would gate it behind a
                # confirmation the user never asked for). Matches are
                # activated for the rest of the session run and returned to
                # the model as the tool_result payload.
                if search_mode:
                    if block.tool_name == "tool_search":
                        query = str((block.tool_input or {}).get("query", ""))
                        matches = search_tool_definitions(
                            query, all_tools, search_max_results,
                        )
                        activated.update(m.get("name", "") for m in matches)
                        payload: dict[str, Any] = {"tools": matches}
                        if not matches:
                            payload["note"] = (
                                "No tools matched the query. Try broader "
                                "keywords."
                            )
                        await append_event(session_id, "agent.tool_result", {
                            "tool_use_id": block.tool_use_id or "",
                            "result": json.dumps(payload),
                        })
                        continue
                    if block.tool_name not in activated:
                        await append_event(session_id, "agent.tool_result", {
                            "tool_use_id": block.tool_use_id or "",
                            "error": (
                                f"Tool '{block.tool_name}' is not activated. "
                                "Call the tool_search tool with relevant "
                                "keywords first, then retry."
                            ),
                        })
                        continue

                # Evaluate policy
                evaluator = PolicyEvaluator(agent)
                permission = evaluator.evaluate(block.tool_name or "")

                if permission == "denied":
                    await append_event(session_id, "agent.tool_result", {
                        "tool_use_id": block.tool_use_id or "",
                        "error": f"Tool '{block.tool_name}' is not available",
                    })
                    continue

                if permission == "always_ask":
                    # Emit requires_action, wait for confirmation
                    await append_event(session_id, "session.requires_action", {
                        "tool_use_id": block.tool_use_id or "",
                        "tool_name": block.tool_name or "",
                    })
                    # Atlas A2 — externalize the wait: API consumers now see
                    # idle(requires_action), so the post_events gate matrix
                    # only admits tool_confirmation/interrupt while the
                    # in-process LISTEN wait below is unchanged.
                    await transition(session_id, "idle", stop_reason="requires_action")
                    await append_event(session_id, "session.status", {
                        "status": "idle",
                        "stop_reason": "requires_action",
                        "tool_use_id": block.tool_use_id or "",
                        "tool_name": block.tool_name or "",
                    })
                    # Wait for user.tool_confirmation event
                    confirmation = await wait_for_confirmation(
                        session_id, block.tool_use_id or ""
                    )
                    # Resolution received — back to running (Atlas A2).
                    await transition(session_id, "running")
                    await append_event(session_id, "session.status", {"status": "running"})
                    # Atlas B9 — audit the confirmation decision. Low
                    # frequency (always_ask tools only); one extra load
                    # for project attribution is fine.
                    try:
                        _srow = await load_session(session_id)
                        _project_id = _srow.get("project_id")
                    except Exception:
                        _project_id = None
                    await record_audit(
                        "tool.confirmation_decided",
                        "session",
                        session_id,
                        project_id=str(_project_id) if _project_id else None,
                        detail={
                            "tool_use_id": block.tool_use_id or "",
                            "tool_name": block.tool_name or "",
                            "approved": confirmation.get("result") != "deny",
                        },
                    )
                    if confirmation.get("result") == "deny":
                        await append_event(session_id, "agent.tool_result", {
                            "tool_use_id": block.tool_use_id or "",
                            "error": confirmation.get(
                                "deny_message", "Tool call denied by user"
                            ),
                        })
                        # Atlas B2 — a deny produced by the waiter's
                        # interrupt sentinel (or a concurrent interrupt)
                        # must be consumed here: the tool_result above
                        # masks the interrupt from the loop-top DB check,
                        # which would leak the flag into the next turn.
                        if interrupts.is_interrupted(session_id):
                            await handle_interrupt(session_id)
                            interrupted = True
                            break
                        continue

                # Atlas B2 — race the dispatch against the in-process
                # interrupt event. If the interrupt wins, terminate the
                # sandbox command (builtin tools), reap the dispatch, keep
                # the tool_use/tool_result pairing intact, and close out.
                tool_task = asyncio.ensure_future(dispatch_tool(
                    session_id, block, sandbox, agent, vault_ids=vault_ids,
                ))
                int_waiter = asyncio.ensure_future(
                    interrupts.event(session_id).wait()
                )
                done, _pending = await asyncio.wait(
                    {tool_task, int_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if int_waiter in done:
                    # Interrupt arrived while the tool was running.
                    if tool_kind not in ("mcp", "custom_http"):
                        try:
                            srow = await load_session(session_id)
                            cid = srow.get("container_id")
                            pid = sandbox.active_exec_pid(cid) if cid else None
                            if pid is not None:
                                await sandbox.kill_exec(cid, pid)
                        except Exception:
                            logger.exception(
                                "failed to kill sandbox command in session %s",
                                session_id,
                            )
                    # Reap the dispatch. After a kill the exec returns
                    # quickly; MCP/custom tools have nothing to kill and
                    # just run to completion. dispatch_tool converts its
                    # own failures into error-result JSON, so this
                    # resolves in all cases.
                    result, result_event_type = await tool_task
                    if result == "__custom_tool_needs_client__":
                        result = json.dumps({"error": "Tool call interrupted"})
                        result_event_type = "agent.tool_result"
                    await append_event(session_id, result_event_type, {
                        "tool_use_id": block.tool_use_id or "",
                        "result": result,
                    })
                    interrupts.clear_interrupt(session_id)
                    await transition(session_id, "idle", stop_reason="end_turn")
                    await append_event(session_id, "session.status", {
                        "status": "idle",
                        "stop_reason": "end_turn",
                    })
                    await append_event(session_id, "session.status_idle", {
                        "stop_reason": "end_turn",
                    })
                    interrupted = True
                    break
                # Tool won the race — drop the interrupt waiter.
                int_waiter.cancel()
                result, result_event_type = tool_task.result()

                if result == "__custom_tool_needs_client__":
                    # Client-handled custom tool: wait for user.custom_tool_result
                    # Atlas A2 — externalize the wait as idle(requires_action)
                    # (see the always_ask branch above for the rationale).
                    await transition(session_id, "idle", stop_reason="requires_action")
                    await append_event(session_id, "session.status", {
                        "status": "idle",
                        "stop_reason": "requires_action",
                        "tool_use_id": block.tool_use_id or "",
                        "tool_name": block.tool_name or "",
                    })
                    custom_result = await wait_for_custom_tool_result(
                        session_id, block.tool_use_id or ""
                    )
                    # Resolution received — back to running (Atlas A2).
                    await transition(session_id, "running")
                    await append_event(session_id, "session.status", {"status": "running"})
                    await append_event(session_id, "agent.tool_result", {
                        "tool_use_id": block.tool_use_id or "",
                        "result": custom_result.get("result", ""),
                    })
                    # Atlas B2 — same interrupt consumption as the deny
                    # branch above: the tool_result would otherwise mask
                    # the interrupt from the loop-top DB check.
                    if interrupts.is_interrupted(session_id):
                        await handle_interrupt(session_id)
                        interrupted = True
                        break
                else:
                    await append_event(session_id, result_event_type, {
                        "tool_use_id": block.tool_use_id or "",
                        "result": result,
                    })

            if interrupted:
                needs_input = True
                continue

            # 11. If no tool calls (end_turn), transition to idle and wait
            # for the next user message on the next iteration.
            if stop_reason == "end_turn" or not has_tool_calls:
                # Atlas B2 — an interrupt landing between the last
                # tool_result and this close-out would otherwise leak the
                # flag into the next turn's first stream chunk. The turn
                # completed; drop it.
                interrupts.clear_interrupt(session_id)

                # Atlas B6 — safe point: if a user.steer landed mid-turn
                # (seq > turn_start_seq), don't idle. Continue the loop so
                # build_context replays the steer as an injected user
                # message and the model gets another round with it.
                # Interrupt close-outs (stream_interrupted / race-won
                # dispatch) deliberately skip this check — interrupt wins.
                if await has_pending_steer(session_id, turn_start_seq):
                    logger.info(
                        "Session %s steered mid-turn; continuing without idling.",
                        session_id,
                    )
                    needs_input = False
                else:
                    await transition(session_id, "idle", stop_reason=stop_reason or "end_turn")
                    # Atlas A2 — unified status event; session.status_idle kept
                    # one transition period for existing consumers.
                    await append_event(session_id, "session.status", {
                        "status": "idle",
                        "stop_reason": stop_reason or "end_turn",
                    })
                    await append_event(session_id, "session.status_idle", {
                        "stop_reason": stop_reason or "end_turn",
                    })
                    needs_input = True
            else:
                # Tool calls were dispatched (some or all of which may have
                # returned errors). The model needs to see the tool_results
                # we just wrote — re-enter the loop immediately without
                # blocking on LISTEN, otherwise the session stalls until
                # an external NOTIFY (which won't come, because we're the
                # writer).
                needs_input = False

        except RetryableError as exc:
            # Atlas A2 — retryable provider failures (429/5xx/network) park
            # the session in 'error' with exponential backoff, then re-enter
            # the loop. After MAX_RETRIES the session lands in idle with
            # stop_reason='retries_exhausted' — per PRD §4.1 it stays
            # resumable by the caller (post_events admits user.message on
            # it) rather than becoming terminal.
            retry_count = int(session.get("retry_count") or 0)
            if retry_count >= MAX_RETRIES:
                logger.error("Retries exhausted for session %s: %s", session_id, exc)
                await execute(
                    "UPDATE sessions SET retry_count = 0 WHERE id = $1",
                    uuid.UUID(session_id),
                )
                await append_event(session_id, "session.error", {
                    "error": str(exc),
                    "source": "model_provider",
                    "retries_exhausted": True,
                })
                await transition(session_id, "idle", stop_reason="retries_exhausted")
                await append_event(session_id, "session.status", {
                    "status": "idle",
                    "stop_reason": "retries_exhausted",
                })
                needs_input = True
            else:
                attempts = retry_count + 1
                backoff = min(60.0, 5.0 * (2 ** retry_count))
                await execute(
                    "UPDATE sessions SET retry_count = $2 WHERE id = $1",
                    uuid.UUID(session_id),
                    attempts,
                )
                await transition(session_id, "error", stop_reason="retrying")
                await append_event(session_id, "session.status", {
                    "status": "error",
                    "stop_reason": "retrying",
                    "retry_count": attempts,
                    "next_retry_in_ms": int(backoff * 1000),
                    "error": str(exc),
                })
                logger.info(
                    "Session %s retryable error (attempt %d/%d), retrying in %.0fs: %s",
                    session_id, attempts, MAX_RETRIES + 1, backoff, exc,
                )
                await asyncio.sleep(backoff)
                # Re-enter the model loop immediately — the request we're
                # retrying hasn't produced anything the model needs to see.
                needs_input = False

        except ProviderError as exc:
            logger.error("Provider error in session %s: %s", session_id, exc)
            await append_event(session_id, "session.error", {
                "error": str(exc),
                "source": "model_provider",
            })
            await transition(session_id, "failed", stop_reason="provider_error")
            tracing.end_session_tracing(session_id)
            return

        except Exception as exc:
            logger.exception("Unrecoverable error in session %s", session_id)
            try:
                await append_event(session_id, "session.error", {
                    "error": str(exc),
                    "source": "orchestrator",
                })
                await transition(session_id, "failed", stop_reason="internal_error")
            except Exception:
                logger.exception("Failed to record error for session %s", session_id)
            tracing.end_session_tracing(session_id)
            return


# ---------------------------------------------------------------------------
# Session recovery on startup
# ---------------------------------------------------------------------------


async def recover_sessions(
    sandbox: DockerSandbox,
    orchestrator_tasks: dict[str, Any],
    *,
    watcher_tasks: dict[str, Any] | None = None,
    spawn_watcher: Callable[[str], Awaitable[None]] | None = None,
    sandbox_resolver: Callable[[Any], Awaitable[Any]] | None = None,
) -> None:
    """Recover non-terminal sessions after process restart.

    1. Query for sessions with status in (running, idle, rescheduling,
       provisioning, error) — Atlas A2 adds the two retryable states.
    2. Set each to ``rescheduling``, emit ``session.status_rescheduled``.
    3. Check if the container still exists.
    4. If container alive → transition to ``idle`` (keeping the row's
       stop_reason so an interrupted requires_action wait still gates
       post_events correctly), start orchestrator task, and (PR5 D4)
       re-spawn the deliverables watcher so its boot scan ingests
       anything written during the outage.
    5. If container missing → transition to ``failed``, emit ``session.error``.

    ``spawn_watcher`` is passed in (not imported) to avoid a circular
    import between orchestrator and watcher; ``app.main.lifespan`` wires it
    to ``watch_session_deliverables``. When omitted (legacy callers, unit
    tests), watcher re-spawn is skipped without erroring.

    ``sandbox_resolver`` (Atlas M4, FR-4.6) resolves the sandbox per row so
    sessions pinned to a remote executor node rebuild their HTTP sandbox
    instead of reusing the process-wide one. When omitted, every row uses
    the passed-in ``sandbox``. Resolution failures (node unreachable,
    capacity/decryption issues) mark the session failed rather than
    blocking recovery of the remaining rows.

    Requirements: 23.1, 23.2, 23.3, 23.4, 6.8
    """
    # Atlas C2 — lease-aware scan: skip sessions whose lease is held by a
    # live replica (fresh heartbeat) — that replica is already driving
    # them. Rows with a stale or missing heartbeat belong to a dead or
    # pre-C2 replica and still need recovery. Tests patch fetch_all, so
    # the added parameters are transparent to them.
    rows = await fetch_all(
        "SELECT * FROM sessions WHERE status IN "
        "('running', 'idle', 'rescheduling', 'provisioning', 'error') "
        "AND (orchestrator_instance IS NULL OR orchestrator_instance = $1 "
        "OR orchestrator_heartbeat IS NULL "
        "OR orchestrator_heartbeat < now() - make_interval(secs => $2))",
        get_instance_id(),
        LEASE_TIMEOUT_SEC,
    )

    for row in rows:
        session_id = str(row["id"])
        container_id = row.get("container_id")

        try:
            # Mark as rescheduling
            await transition(session_id, "rescheduling")
            await append_event(session_id, "session.status_rescheduled", {
                "reason": "process_restart",
            })

            # Atlas M4 (FR-4.6) — resolve the sandbox per row. Sessions
            # pinned to a remote executor node rebuild their HTTP sandbox
            # here; an unreachable node fails just that session and the
            # rest of the scan continues.
            row_sandbox: Any = sandbox
            if sandbox_resolver is not None:
                try:
                    row_sandbox = await sandbox_resolver(row)
                except SandboxError as exc:
                    await transition(
                        session_id, "failed", stop_reason="sandbox_unavailable"
                    )
                    await append_event(session_id, "session.error", {
                        "error": str(exc),
                        "source": "recovery",
                    })
                    continue

            # Check if container still exists
            if container_id:
                try:
                    await row_sandbox.exec(container_id, "true")
                    # Container is alive — transition to idle and start orchestrator.
                    # Keep the row's stop_reason so a requires_action wait that
                    # was in flight during the restart still gates post_events.
                    await transition(
                        session_id, "idle", stop_reason=row.get("stop_reason")
                    )
                    task = asyncio.create_task(run_session(session_id, row_sandbox))
                    orchestrator_tasks[session_id] = task

                    # PR5 D4 — re-attach the deliverables watcher. Its boot
                    # scan picks up files written to the outputs bind while
                    # the api was down; the awatch loop then takes over.
                    if spawn_watcher is not None and watcher_tasks is not None:
                        watcher_task = asyncio.create_task(spawn_watcher(session_id))

                        def _watcher_done(t: asyncio.Task, _sid: str = session_id) -> None:
                            if t.cancelled():
                                return
                            exc = t.exception()
                            if exc:
                                logger.error(
                                    "Recovered watcher for session %s failed: %s", _sid, exc,
                                )

                        watcher_task.add_done_callback(_watcher_done)
                        watcher_tasks[session_id] = watcher_task
                except Exception:
                    # Container is gone
                    await transition(session_id, "failed", stop_reason="container_lost")
                    await append_event(session_id, "session.error", {
                        "error": "Container not found after process restart",
                        "source": "recovery",
                    })
            else:
                # No container_id at all
                await transition(session_id, "failed", stop_reason="container_lost")
                await append_event(session_id, "session.error", {
                    "error": "Container not found after process restart",
                    "source": "recovery",
                })

        except Exception:
            logger.exception("Failed to recover session %s", session_id)
            try:
                await transition(session_id, "failed", stop_reason="recovery_failed")
                await append_event(session_id, "session.error", {
                    "error": "Recovery failed due to unexpected error",
                    "source": "recovery",
                })
            except Exception:
                logger.exception("Failed to mark session %s as failed", session_id)


# ---------------------------------------------------------------------------
# Atlas C2 — lease supervisor: reclaim sessions whose owner died
# ---------------------------------------------------------------------------


def supervisor_enabled() -> bool:
    """Lease supervisor runs unless explicitly disabled.

    ``LINCHPIN_LEASE_SUPERVISOR=false`` skips it (tests, or single-process
    deployments that don't want takeover passes).
    """
    return os.environ.get("LINCHPIN_LEASE_SUPERVISOR", "true").lower() != "false"


async def take_over_stale_sessions(
    sandbox: DockerSandbox,
    orchestrator_tasks: dict[str, Any],
    *,
    watcher_tasks: dict[str, Any] | None = None,
    spawn_watcher: Callable[[str], Awaitable[None]] | None = None,
    stale_after: float | None = None,
    sandbox_resolver: Callable[[Any], Awaitable[Any]] | None = None,
) -> int:
    """Take over sessions whose lease heartbeat has gone stale (Atlas C2).

    A session is a takeover candidate when its status is running/idle and
    ``orchestrator_heartbeat`` is older than ``stale_after`` seconds — its
    owning replica stopped renewing (crash, network partition). For each
    candidate:

    1. Try the advisory-lock lease (fails closed when another replica
       raced us to it, or the old owner is somehow still alive).
    2. Check the container; dead → ``failed(container_lost)`` + release.
    3. Alive → transition to ``idle`` (keeping stop_reason), emit
       ``session.takeover``, start an orchestrator task that reuses the
       already-held lease, re-spawn the deliverables watcher.

    Deliberately no notify is emitted for the taken-over session: a
    mid-turn owner death is not auto-replayed, matching the pre-existing
    process-restart semantics in :func:`recover_sessions` (turn replay is
    a durable-execution concern — plan §8, M4 evaluation).

    Returns the number of sessions taken over.
    """
    if stale_after is None:
        stale_after = LEASE_TIMEOUT_SEC
    rows = await fetch_all(
        "SELECT * FROM sessions WHERE status IN ('running', 'idle') "
        "AND orchestrator_heartbeat < now() - make_interval(secs => $1)",
        stale_after,
    )
    taken = 0
    for row in rows:
        session_id = str(row["id"])

        # Skip sessions this process is already driving — their heartbeat
        # is renewed by our own heartbeat task; a stale row while our task
        # is alive means renewal writes are failing, which the loop's
        # broken-lease check handles.
        task = orchestrator_tasks.get(session_id)
        if task is not None and not task.done():
            continue

        try:
            lease = SessionLease(session_id)
            acquired = await lease.acquire()
        except Exception:
            logger.info(
                "Takeover lease unavailable for session %s; skipping", session_id,
            )
            continue
        if not acquired:
            continue  # another replica won the race

        # Atlas M4 (FR-4.6) — resolve the sandbox per row so takeover works
        # for remote executor sessions too; the process-wide sandbox can't
        # probe containers that live on a remote node's Docker host.
        row_sandbox: Any = sandbox
        if sandbox_resolver is not None:
            try:
                row_sandbox = await sandbox_resolver(row)
            except SandboxError as exc:
                await lease.release()
                await transition(session_id, "failed", stop_reason="sandbox_unavailable")
                await append_event(session_id, "session.error", {
                    "error": str(exc),
                    "source": "lease_supervisor",
                })
                continue

        try:
            container_id = row.get("container_id")
            alive = False
            if container_id:
                try:
                    await row_sandbox.exec(container_id, "true")
                    alive = True
                except Exception:
                    alive = False
            if not alive:
                await lease.release()
                await transition(session_id, "failed", stop_reason="container_lost")
                await append_event(session_id, "session.error", {
                    "error": "Container not found during lease takeover",
                    "source": "lease_supervisor",
                })
                continue

            # Container alive — resume the loop as the new lease holder.
            # Keep the row's stop_reason so a requires_action wait that was
            # in flight on the dead replica still gates post_events.
            await transition(session_id, "idle", stop_reason=row.get("stop_reason"))
            await append_event(session_id, "session.takeover", {
                "previous_instance": row.get("orchestrator_instance"),
                "new_instance": get_instance_id(),
            })
            new_task = asyncio.create_task(
                run_session(session_id, row_sandbox, lease=lease)
            )
            orchestrator_tasks[session_id] = new_task
            taken += 1

            if spawn_watcher is not None and watcher_tasks is not None:
                watcher_task = asyncio.create_task(spawn_watcher(session_id))
                watcher_tasks[session_id] = watcher_task
        except Exception:
            logger.exception("Failed to take over session %s", session_id)
            await lease.release()
    return taken


async def supervise_stale_sessions(
    sandbox: DockerSandbox,
    orchestrator_tasks: dict[str, Any],
    *,
    watcher_tasks: dict[str, Any] | None = None,
    spawn_watcher: Callable[[str], Awaitable[None]] | None = None,
    interval_seconds: float = 30.0,
    sandbox_resolver: Callable[[Any], Awaitable[Any]] | None = None,
) -> None:
    """Background loop reclaiming abandoned sessions every pass (Atlas C2)."""
    while True:
        try:
            await take_over_stale_sessions(
                sandbox,
                orchestrator_tasks,
                watcher_tasks=watcher_tasks,
                spawn_watcher=spawn_watcher,
                sandbox_resolver=sandbox_resolver,
            )
        except Exception:
            logger.exception("Lease supervisor pass failed")
        await asyncio.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# Session TTL cleanup background task
# ---------------------------------------------------------------------------


async def cleanup_expired_sessions(
    sandbox: DockerSandbox,
    orchestrator_tasks: dict[str, Any],
    interval_seconds: int = 60,
    sandbox_resolver: Callable[[Any], Awaitable[Any]] | None = None,
) -> None:
    """Background task that periodically checks for and terminates expired sessions.

    A session is expired when:
    - ttl_seconds IS NOT NULL
    - status NOT IN ('terminated', 'failed')
    - updated_at + ttl_seconds < now()

    On expiry: set status to 'terminated', destroy container, cancel orchestrator task.

    Requirements: 19.1, 19.2
    """
    import asyncio

    logger.info("Session TTL cleanup task started (interval=%ds).", interval_seconds)

    while True:
        try:
            await asyncio.sleep(interval_seconds)

            rows = await fetch_all(
                """
                SELECT id, container_id, metadata
                FROM sessions
                WHERE ttl_seconds IS NOT NULL
                  AND status NOT IN ('terminated', 'failed')
                  AND updated_at + (ttl_seconds || ' seconds')::interval < now()
                """,
            )

            for row in rows:
                session_id = str(row["id"])
                container_id = row["container_id"]
                logger.info("Session %s expired (TTL). Terminating.", session_id)

                try:
                    # Update status to terminated
                    await transition(session_id, "terminated")
                    # Mirror terminate_session: drop any live resource mounts
                    # to 'unmounted' so DELETE /v1/files no longer 409s on
                    # files that were only mounted in TTL-expired sessions.
                    await execute(
                        """UPDATE session_resources
                           SET state = 'unmounted', unmounted_at = now()
                           WHERE session_id = $1
                             AND state IN ('mounted', 'unmounting')""",
                        uuid.UUID(session_id),
                    )
                    await append_event(session_id, "session.status_terminated", {
                        "reason": "ttl_expired",
                    })

                    # Cancel orchestrator task
                    task = orchestrator_tasks.pop(session_id, None)
                    if task is not None and not task.done():
                        task.cancel()

                    # Destroy container
                    if container_id:
                        # Atlas M4 (FR-4.6) — resolve per row so TTL expiry
                        # destroys remote executor containers on their node
                        # instead of silently no-oping against local Docker.
                        row_sandbox = sandbox
                        if sandbox_resolver is not None:
                            row_sandbox = await sandbox_resolver(row)
                        await row_sandbox.destroy(container_id)

                except Exception:
                    logger.exception("Error terminating expired session %s", session_id)

        except asyncio.CancelledError:
            logger.info("Session TTL cleanup task cancelled.")
            return
        except Exception:
            logger.exception("Error in TTL cleanup loop")
