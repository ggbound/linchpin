"""Agent CRUD + versioning + archive endpoints (v0.2.0 item #5).

POST   /v1/agents                       — Create agent
GET    /v1/agents                       — List agents (archived excluded by default; ?include_archived=true)
GET    /v1/agents/{id}                  — Get current agent state
PATCH  /v1/agents/{id}                  — Update + snapshot prior state into agent_versions
GET    /v1/agents/{id}/versions         — List historical versions (newest first)
POST   /v1/agents/{id}/archive          — Soft-delete (idempotent)

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.audit import record_audit
from app.auth import AuthContext, require_auth
from app.db import execute, fetch_all, fetch_one
from app.models import (
    AgentResponse,
    AgentVersion,
    CreateAgentRequest,
    PaginatedListResponse,
    SubagentConfig,
    UpdateAgentRequest,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _row_to_agent(row) -> AgentResponse:
    """Convert an asyncpg Record to an AgentResponse."""
    # asyncpg Record.keys() returns a one-shot iterator — materialize it
    # once or every subsequent ``in`` check silently fails.
    keys = set(row.keys())
    metadata_raw = row["metadata"] if "metadata" in keys else {}
    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
    # v0.4.0 — skills column is JSONB; default '[]' so pre-0012 rows
    # don't crash. Stored as a list of skill-id strings.
    skills_raw = row["skills"] if "skills" in keys else []
    skills = json.loads(skills_raw) if isinstance(skills_raw, str) else (skills_raw or [])
    # FR-6.1 — declared subagents (JSONB); default '[]' so pre-0026 rows
    # and older test mocks don't crash.
    multi_raw = row["multi_agents"] if "multi_agents" in keys else []
    multi_agents = json.loads(multi_raw) if isinstance(multi_raw, str) else (multi_raw or [])
    return AgentResponse(
        id=str(row["id"]),
        name=row["name"],
        version=row["version"],
        model=json.loads(row["model"]) if isinstance(row["model"], str) else row["model"],
        system=row["system"],
        tools=json.loads(row["tools"]) if isinstance(row["tools"], str) else row["tools"],
        mcp_servers=json.loads(row["mcp_servers"]) if isinstance(row["mcp_servers"], str) else row["mcp_servers"],
        skills=skills,
        multi_agents=multi_agents,
        created_at=row["created_at"],
        description=row["description"] if "description" in keys else None,
        metadata=metadata or {},
        archived_at=row["archived_at"] if "archived_at" in keys else None,
    )


def _row_to_agent_version(row) -> AgentVersion:
    metadata_raw = row["metadata"]
    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
    keys = set(row.keys())
    skills_raw = row["skills"] if "skills" in keys else []
    skills = json.loads(skills_raw) if isinstance(skills_raw, str) else (skills_raw or [])
    multi_raw = row["multi_agents"] if "multi_agents" in keys else []
    multi_agents = json.loads(multi_raw) if isinstance(multi_raw, str) else (multi_raw or [])
    return AgentVersion(
        agent_id=str(row["agent_id"]),
        version=row["version"],
        name=row["name"],
        description=row["description"],
        metadata=metadata or {},
        model=json.loads(row["model"]) if isinstance(row["model"], str) else row["model"],
        system=row["system"],
        tools=json.loads(row["tools"]) if isinstance(row["tools"], str) else row["tools"],
        mcp_servers=json.loads(row["mcp_servers"]) if isinstance(row["mcp_servers"], str) else row["mcp_servers"],
        skills=skills,
        multi_agents=multi_agents,
        snapshotted_at=row["snapshotted_at"],
    )


def _parse_agent_uuid(agent_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {agent_id} not found"},
        )


@router.post("", status_code=201, response_model=AgentResponse)
async def create_agent(
    body: CreateAgentRequest,
    ctx: AuthContext = Depends(require_auth),
) -> AgentResponse:
    """Create a new agent resource."""
    agent_id = str(uuid.uuid4())
    project_uid = uuid.UUID(ctx.project_id)

    model_json = body.model.model_dump(mode="json")
    tools_json = [t.model_dump(mode="json") for t in body.tools]
    mcp_json = [m.model_dump(mode="json") for m in body.mcp_servers]

    # v0.4.0 — validate every skill id exists + isn't archived. The
    # max-8 cap is enforced by the Pydantic validator on the model.
    await _validate_skill_ids(body.skills, project_uid)
    # FR-6.1 — declared workers must exist (and be live) in this project.
    await _validate_subagent_refs(body.multi_agents, project_uid)

    row = await fetch_one(
        """
        INSERT INTO agents
            (id, project_id, name, model, system, tools, mcp_servers, skills,
             multi_agents, description, metadata)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb, $8::jsonb,
                $9::jsonb, $10, $11::jsonb)
        RETURNING *
        """,
        uuid.UUID(agent_id),
        project_uid,
        body.name,
        json.dumps(model_json),
        body.system,
        json.dumps(tools_json),
        json.dumps(mcp_json),
        json.dumps(body.skills),
        json.dumps([s.model_dump(mode="json") for s in body.multi_agents]),
        body.description,
        json.dumps(body.metadata),
    )

    await record_audit("agent.created", "agent", agent_id, ctx=ctx, detail={"name": body.name})

    return _row_to_agent(row)


async def _validate_skill_ids(skill_ids: list[str], project_uid: uuid.UUID) -> None:
    """Reject create/update if any skill id is missing or archived.

    Mirrors the vault_ids existence check in sessions.py — fail fast
    with a 422 so the caller doesn't end up with an agent pointing at
    deleted bundles. C1: skills carry ``workspace_id`` (== project id),
    so the lookup is also tenant-scoped.
    """
    for sid in skill_ids:
        try:
            uid = uuid.UUID(sid)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Invalid skill id: {sid}",
                },
            )
        row = await fetch_one(
            "SELECT id, archived_at FROM skills WHERE id = $1 AND workspace_id = $2",
            uid,
            project_uid,
        )
        if row is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Skill {sid} not found",
                },
            )
        if row["archived_at"] is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Skill {sid} is archived",
                },
            )


async def _validate_subagent_refs(
    subagents: list[SubagentConfig], project_uid: uuid.UUID
) -> None:
    """FR-6.1 — reject create/update if any declared worker agent is
    missing or archived in this project.

    Mirrors ``_validate_skill_ids``: fail fast with a 422 so callers don't
    declare workers that could never be spawned. Worker agents must live
    in the same project (tenant-scoped lookup).
    """
    for sub in subagents:
        try:
            uid = uuid.UUID(sub.agent_id)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Invalid subagent agent id: {sub.agent_id}",
                },
            )
        row = await fetch_one(
            "SELECT id, archived_at FROM agents WHERE id = $1 AND project_id = $2",
            uid,
            project_uid,
        )
        if row is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Subagent agent {sub.agent_id} not found",
                },
            )
        if row["archived_at"] is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Subagent agent {sub.agent_id} is archived",
                },
            )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> AgentResponse:
    """Retrieve an agent by id. Returns archived agents too — callers
    can filter on ``archived_at`` if needed."""
    uid = _parse_agent_uuid(agent_id)
    row = await fetch_one(
        "SELECT * FROM agents WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {agent_id} not found"},
        )
    return _row_to_agent(row)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    ctx: AuthContext = Depends(require_auth),
) -> AgentResponse:
    """Partially update an agent and snapshot the prior config into agent_versions.

    v0.2.0 item #5 — the snapshot is taken BEFORE the UPDATE applies, so
    GET /v1/agents/{id}/versions can return the exact agent config any past
    session pinned via its agent_version field.
    """
    uid = _parse_agent_uuid(agent_id)
    project_uid = uuid.UUID(ctx.project_id)

    # Snapshot the current row first. We use a SELECT FOR UPDATE so a
    # concurrent PATCH can't double-snapshot the same version.
    current = await fetch_one(
        "SELECT * FROM agents WHERE id = $1 AND project_id = $2 FOR UPDATE",
        uid,
        project_uid,
    )
    if current is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {agent_id} not found"},
        )
    if current["archived_at"] is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "agent_archived",
                "message": f"Agent {agent_id} is archived; unarchive (not yet supported) before updating.",
            },
        )

    metadata_raw = current["metadata"]
    metadata_str = (
        metadata_raw if isinstance(metadata_raw, str) else json.dumps(metadata_raw)
    )
    model_str = (
        current["model"] if isinstance(current["model"], str) else json.dumps(current["model"])
    )
    tools_str = (
        current["tools"] if isinstance(current["tools"], str) else json.dumps(current["tools"])
    )
    mcp_str = (
        current["mcp_servers"] if isinstance(current["mcp_servers"], str)
        else json.dumps(current["mcp_servers"])
    )
    # Materialize keys once — asyncpg Record.keys() is a one-shot iterator.
    current_keys = set(current.keys())
    skills_current = current["skills"] if "skills" in current_keys else []
    skills_str = (
        skills_current if isinstance(skills_current, str)
        else json.dumps(skills_current or [])
    )
    multi_current = current["multi_agents"] if "multi_agents" in current_keys else []
    multi_str = (
        multi_current if isinstance(multi_current, str)
        else json.dumps(multi_current or [])
    )
    await execute(
        """
        INSERT INTO agent_versions
            (agent_id, version, name, description, metadata, model, system,
             tools, mcp_servers, skills, multi_agents)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8::jsonb, $9::jsonb,
                $10::jsonb, $11::jsonb)
        ON CONFLICT (agent_id, version) DO NOTHING
        """,
        uid,
        current["version"],
        current["name"],
        current["description"],
        metadata_str,
        model_str,
        current["system"],
        tools_str,
        mcp_str,
        skills_str,
        multi_str,
    )

    # Build dynamic SET clause from non-None fields.
    set_parts: list[str] = []
    params: list[object] = []
    idx = 2  # $1 / $2 are reserved for the WHERE id + project_id

    if body.name is not None:
        idx += 1
        set_parts.append(f"name = ${idx}")
        params.append(body.name)

    if body.model is not None:
        idx += 1
        set_parts.append(f"model = ${idx}::jsonb")
        params.append(json.dumps(body.model.model_dump(mode="json")))

    if body.system is not None:
        idx += 1
        set_parts.append(f"system = ${idx}")
        params.append(body.system)

    if body.tools is not None:
        idx += 1
        set_parts.append(f"tools = ${idx}::jsonb")
        params.append(json.dumps([t.model_dump(mode="json") for t in body.tools]))

    if body.mcp_servers is not None:
        idx += 1
        set_parts.append(f"mcp_servers = ${idx}::jsonb")
        params.append(json.dumps([m.model_dump(mode="json") for m in body.mcp_servers]))

    if body.skills is not None:
        await _validate_skill_ids(body.skills, project_uid)
        idx += 1
        set_parts.append(f"skills = ${idx}::jsonb")
        params.append(json.dumps(body.skills))

    # FR-6.1 — declared subagents validate like skills: worker refs must
    # exist (and be live) in this project.
    if body.multi_agents is not None:
        await _validate_subagent_refs(body.multi_agents, project_uid)
        idx += 1
        set_parts.append(f"multi_agents = ${idx}::jsonb")
        params.append(
            json.dumps([s.model_dump(mode="json") for s in body.multi_agents])
        )

    # v0.2.0 item #5 — description + metadata
    if body.description is not None:
        idx += 1
        set_parts.append(f"description = ${idx}")
        params.append(body.description)

    if body.metadata is not None:
        idx += 1
        set_parts.append(f"metadata = ${idx}::jsonb")
        params.append(json.dumps(body.metadata))

    # Always bump version
    set_parts.append("version = version + 1")

    query = f"UPDATE agents SET {', '.join(set_parts)} WHERE id = $1 AND project_id = $2 RETURNING *"
    row = await fetch_one(query, uid, project_uid, *params)

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {agent_id} not found"},
        )

    changed = [p.split(" =")[0] for p in set_parts if p != "version = version + 1"]
    await record_audit("agent.updated", "agent", agent_id, ctx=ctx, detail={"fields": changed})

    return _row_to_agent(row)


@router.get("", response_model=PaginatedListResponse)
async def list_agents(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    ctx: AuthContext = Depends(require_auth),
) -> PaginatedListResponse:
    """Return a paginated list of agents. Archived agents are excluded by
    default; opt back in with ``?include_archived=true``."""
    project_uid = uuid.UUID(ctx.project_id)
    if include_archived:
        rows = await fetch_all(
            """SELECT * FROM agents
               WHERE project_id = $1
               ORDER BY created_at DESC
               LIMIT $2 OFFSET $3""",
            project_uid,
            limit + 1,
            offset,
        )
    else:
        rows = await fetch_all(
            """SELECT * FROM agents
               WHERE project_id = $1 AND archived_at IS NULL
               ORDER BY created_at DESC
               LIMIT $2 OFFSET $3""",
            project_uid,
            limit + 1,
            offset,
        )

    has_more = len(rows) > limit
    items = rows[:limit]

    agents = [_row_to_agent(r) for r in items]

    return PaginatedListResponse(
        data=agents,
        has_more=has_more,
        next_cursor=None,
    )


@router.get("/{agent_id}/versions", response_model=PaginatedListResponse)
async def list_agent_versions(
    agent_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_auth),
) -> PaginatedListResponse:
    """List historical versions of an agent, newest first.

    v0.2.0 item #5 — Sessions pin agent_version on creation; this endpoint
    lets callers fetch the exact agent config a session was running under.
    Returns 404 if the agent itself never existed; returns an empty list
    if the agent exists but has never been PATCH'd (no snapshots taken yet).
    """
    uid = _parse_agent_uuid(agent_id)

    exists = await fetch_one(
        "SELECT id FROM agents WHERE id = $1 AND project_id = $2",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if exists is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {agent_id} not found"},
        )

    rows = await fetch_all(
        """SELECT * FROM agent_versions
           WHERE agent_id = $1
           ORDER BY version DESC
           LIMIT $2 OFFSET $3""",
        uid,
        limit + 1,
        offset,
    )
    has_more = len(rows) > limit
    versions = [_row_to_agent_version(r) for r in rows[:limit]]
    return PaginatedListResponse(
        data=versions,
        has_more=has_more,
        next_cursor=None,
    )


@router.post("/{agent_id}/archive", response_model=AgentResponse)
async def archive_agent(
    agent_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> AgentResponse:
    """Soft-delete an agent (v0.2.0 item #5). Idempotent — archiving an
    already-archived agent returns the same row without re-stamping."""
    uid = _parse_agent_uuid(agent_id)
    project_uid = uuid.UUID(ctx.project_id)

    existing = await fetch_one(
        "SELECT * FROM agents WHERE id = $1 AND project_id = $2",
        uid,
        project_uid,
    )
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {agent_id} not found"},
        )
    if existing["archived_at"] is not None:
        return _row_to_agent(existing)

    row = await fetch_one(
        """UPDATE agents
              SET archived_at = now()
            WHERE id = $1 AND project_id = $2
        RETURNING *""",
        uid,
        project_uid,
    )
    await record_audit("agent.archived", "agent", agent_id, ctx=ctx)
    return _row_to_agent(row)
