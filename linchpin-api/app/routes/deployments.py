"""Deployment CRUD + pause/resume endpoints (Atlas B7, FR-8.1).

POST   /v1/deployments               — create (next_run_at computed from now)
GET    /v1/deployments               — list (paginated, optional agent_id)
GET    /v1/deployments/{id}          — get
PATCH  /v1/deployments/{id}          — update cron/timezone/initial_events
DELETE /v1/deployments/{id}          — archive (soft-delete)
POST   /v1/deployments/{id}/pause    — stop scheduling (idempotent)
POST   /v1/deployments/{id}/resume   — resume + recompute next_run_at from now

The scheduler loop itself lives in ``app.deployments``; a deployment's
run history is sessions filtered on ``deployment_id``
(``GET /v1/sessions?deployment_id=...``).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.audit import record_audit
from app.auth import AuthContext, require_auth
from app.db import execute, fetch_all, fetch_one
from app.deployments import (
    compute_next_run,
    validate_cron,
    validate_timezone,
)
from app.models import (
    CreateDeploymentRequest,
    DeploymentResponse,
    InitialEvent,
    PaginatedListResponse,
    UpdateDeploymentRequest,
)


router = APIRouter(prefix="/deployments", tags=["deployments"])


def _row_to_deployment(row) -> DeploymentResponse:
    initial_events_raw = row["initial_events"]
    if isinstance(initial_events_raw, str):
        initial_events_raw = json.loads(initial_events_raw)
    return DeploymentResponse(
        id=str(row["id"]),
        agent_id=str(row["agent_id"]),
        environment_id=str(row["environment_id"]),
        cron=row["cron"],
        timezone=row["timezone"],
        initial_events=[
            InitialEvent.model_validate(item) for item in (initial_events_raw or [])
        ],
        status=row["status"],
        next_run_at=row["next_run_at"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


def _parse_deployment_uuid(deployment_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(deployment_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Deployment {deployment_id} not found",
            },
        )


def _raise_validation(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"error": "validation_error", "message": message},
    )


async def _load_owned_deployment(
    deployment_uid: uuid.UUID, project_uid: uuid.UUID
):
    """Fetch the deployment row, 404 when missing or foreign (C1)."""
    row = await fetch_one(
        "SELECT * FROM deployments WHERE id = $1 AND project_id = $2",
        deployment_uid,
        project_uid,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Deployment {deployment_uid} not found",
            },
        )
    return row


@router.post("", status_code=201, response_model=DeploymentResponse)
async def create_deployment(
    body: CreateDeploymentRequest,
    ctx: AuthContext = Depends(require_auth),
) -> DeploymentResponse:
    """Create a deployment. ``next_run_at`` is computed from now in the
    given timezone; the first scheduled run fires at the next cron match."""
    project_uid = uuid.UUID(ctx.project_id)

    # C1 — the referenced agent/environment must belong to the caller's
    # project (same checks as session creation).
    try:
        agent_uid = uuid.UUID(body.agent_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {body.agent_id} not found"},
        )
    agent_row = await fetch_one(
        "SELECT id FROM agents WHERE id = $1 AND project_id = $2",
        agent_uid,
        project_uid,
    )
    if agent_row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Agent {body.agent_id} not found"},
        )

    try:
        env_uid = uuid.UUID(body.environment_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Environment {body.environment_id} not found",
            },
        )
    env_row = await fetch_one(
        "SELECT id FROM environments WHERE id = $1 AND project_id = $2",
        env_uid,
        project_uid,
    )
    if env_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Environment {body.environment_id} not found",
            },
        )

    # Reject unparseable cron/timezone up front — the scheduler would
    # otherwise park the row on its first tick with no useful signal.
    try:
        validate_cron(body.cron)
        validate_timezone(body.timezone)
        next_run_at = compute_next_run(body.cron, body.timezone)
    except ValueError as exc:
        raise _raise_validation(str(exc))

    initial_events_json = json.dumps(
        [item.model_dump() for item in body.initial_events]
    )

    row = await fetch_one(
        """
        INSERT INTO deployments
            (project_id, agent_id, environment_id, cron, timezone,
             initial_events, status, next_run_at)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'active', $7)
        RETURNING *
        """,
        project_uid,
        agent_uid,
        env_uid,
        body.cron,
        body.timezone,
        initial_events_json,
        next_run_at,
    )

    await record_audit(
        "deployment.created", "deployment", str(row["id"]), ctx=ctx,
        detail={"cron": body.cron, "timezone": body.timezone,
                "agent_id": body.agent_id},
    )
    return _row_to_deployment(row)


@router.get("", response_model=PaginatedListResponse)
async def list_deployments(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    agent_id: str | None = Query(default=None),
    ctx: AuthContext = Depends(require_auth),
) -> PaginatedListResponse:
    """Return a paginated list of deployments, optionally by agent_id."""
    project_uid = uuid.UUID(ctx.project_id)
    if agent_id is not None:
        try:
            agent_uid = uuid.UUID(agent_id)
        except ValueError:
            return PaginatedListResponse(data=[], has_more=False, next_cursor=None)
        rows = await fetch_all(
            """SELECT * FROM deployments
               WHERE agent_id = $1 AND project_id = $2
               ORDER BY created_at DESC
               LIMIT $3 OFFSET $4""",
            agent_uid,
            project_uid,
            limit + 1,
            offset,
        )
    else:
        rows = await fetch_all(
            """SELECT * FROM deployments
               WHERE project_id = $1
               ORDER BY created_at DESC
               LIMIT $2 OFFSET $3""",
            project_uid,
            limit + 1,
            offset,
        )

    has_more = len(rows) > limit
    items = rows[:limit]
    return PaginatedListResponse(
        data=[_row_to_deployment(r) for r in items],
        has_more=has_more,
        next_cursor=None,
    )


@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> DeploymentResponse:
    deployment_uid = _parse_deployment_uuid(deployment_id)
    row = await _load_owned_deployment(deployment_uid, uuid.UUID(ctx.project_id))
    return _row_to_deployment(row)


@router.patch("/{deployment_id}", response_model=DeploymentResponse)
async def update_deployment(
    deployment_id: str,
    body: UpdateDeploymentRequest,
    ctx: AuthContext = Depends(require_auth),
) -> DeploymentResponse:
    """Update cron/timezone/initial_events. A cron or timezone change
    recomputes ``next_run_at`` from now — the new schedule starts from
    the edit, not the old plan."""
    deployment_uid = _parse_deployment_uuid(deployment_id)
    await _load_owned_deployment(deployment_uid, uuid.UUID(ctx.project_id))

    next_run_at: datetime | None = None
    if body.cron is not None or body.timezone is not None:
        cron = body.cron
        timezone = body.timezone
        if cron is None or timezone is None:
            current = await fetch_one(
                "SELECT cron, timezone FROM deployments WHERE id = $1",
                deployment_uid,
            )
            cron = cron if cron is not None else current["cron"]
            timezone = timezone if timezone is not None else current["timezone"]
        try:
            validate_cron(cron)
            validate_timezone(timezone)
            next_run_at = compute_next_run(cron, timezone)
        except ValueError as exc:
            raise _raise_validation(str(exc))

    if body.initial_events is not None:
        await execute(
            "UPDATE deployments SET initial_events = $2::jsonb WHERE id = $1",
            deployment_uid,
            json.dumps([item.model_dump() for item in body.initial_events]),
        )
    if body.cron is not None:
        await execute(
            "UPDATE deployments SET cron = $2 WHERE id = $1",
            deployment_uid,
            body.cron,
        )
    if body.timezone is not None:
        await execute(
            "UPDATE deployments SET timezone = $2 WHERE id = $1",
            deployment_uid,
            body.timezone,
        )
    if next_run_at is not None:
        await execute(
            "UPDATE deployments SET next_run_at = $2 WHERE id = $1",
            deployment_uid,
            next_run_at,
        )

    row = await fetch_one(
        "SELECT * FROM deployments WHERE id = $1", deployment_uid
    )
    await record_audit(
        "deployment.updated", "deployment", deployment_id, ctx=ctx,
        detail={k: v for k, v in {
            "cron": body.cron,
            "timezone": body.timezone,
            "initial_events_changed": body.initial_events is not None,
        }.items() if v is not None},
    )
    return _row_to_deployment(row)


@router.delete("/{deployment_id}", response_model=DeploymentResponse)
async def delete_deployment(
    deployment_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> DeploymentResponse:
    """Archive a deployment (soft-delete). The scheduler only scans
    ``archived_at IS NULL``; historical sessions keep their
    ``deployment_id`` so the run history remains queryable."""
    deployment_uid = _parse_deployment_uuid(deployment_id)
    await _load_owned_deployment(deployment_uid, uuid.UUID(ctx.project_id))

    await execute(
        """UPDATE deployments
           SET archived_at = NOW(), status = 'paused'
           WHERE id = $1""",
        deployment_uid,
    )
    row = await fetch_one(
        "SELECT * FROM deployments WHERE id = $1", deployment_uid
    )
    await record_audit(
        "deployment.archived", "deployment", deployment_id, ctx=ctx,
    )
    return _row_to_deployment(row)


@router.post("/{deployment_id}/pause", response_model=DeploymentResponse)
async def pause_deployment(
    deployment_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> DeploymentResponse:
    """Stop scheduling. Idempotent — pausing a paused deployment is a no-op."""
    deployment_uid = _parse_deployment_uuid(deployment_id)
    await _load_owned_deployment(deployment_uid, uuid.UUID(ctx.project_id))

    await execute(
        "UPDATE deployments SET status = 'paused' WHERE id = $1",
        deployment_uid,
    )
    row = await fetch_one(
        "SELECT * FROM deployments WHERE id = $1", deployment_uid
    )
    await record_audit(
        "deployment.paused", "deployment", deployment_id, ctx=ctx,
    )
    return _row_to_deployment(row)


@router.post("/{deployment_id}/resume", response_model=DeploymentResponse)
async def resume_deployment(
    deployment_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> DeploymentResponse:
    """Resume scheduling. ``next_run_at`` is recomputed from now so a
    long-paused deployment doesn't fire its backlog immediately."""
    deployment_uid = _parse_deployment_uuid(deployment_id)
    row = await _load_owned_deployment(deployment_uid, uuid.UUID(ctx.project_id))

    next_run_at = compute_next_run(row["cron"], row["timezone"])
    await execute(
        """UPDATE deployments
           SET status = 'active', next_run_at = $2
           WHERE id = $1""",
        deployment_uid,
        next_run_at,
    )
    row = await fetch_one(
        "SELECT * FROM deployments WHERE id = $1", deployment_uid
    )
    await record_audit(
        "deployment.resumed", "deployment", deployment_id, ctx=ctx,
    )
    return _row_to_deployment(row)
