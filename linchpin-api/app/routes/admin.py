"""Atlas C1 — multi-tenancy admin endpoints.

GET   /v1/projects              — list projects in the caller's org
PATCH /v1/projects/{id}         — update project quotas (bootstrap key only)
POST  /v1/api-keys              — issue a key (plaintext returned ONCE)
GET   /v1/api-keys              — list keys for the caller's project
DELETE /v1/api-keys/{id}        — revoke a key (soft: status='revoked')

Key format: ``lpk_`` + 32 bytes of URL-safe entropy. Only the SHA-256
hash and a 12-character display prefix are persisted, so a leaked DB
dump cannot be replayed against the API. Scope enforcement for these
endpoints reuses the central check in :mod:`app.auth` (first path
segment after ``/v1/``: ``projects`` / ``api-keys``).
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.audit import record_audit
from app.auth import AuthContext, _hash_token, require_auth
from app.db import execute, fetch_all, fetch_one
from app.models import (
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    CreateApiKeyRequest,
    ProjectResponse,
    UpdateProjectRequest,
)

router = APIRouter(tags=["admin"])

KEY_PREFIX_TAG = "lpk_"
_KEY_ENTROPY_BYTES = 32


def _row_to_project(row) -> ProjectResponse:
    return ProjectResponse(
        id=str(row["id"]),
        org_id=str(row["org_id"]),
        name=row["name"],
        slug=row["slug"],
        max_concurrent_sessions=row["max_concurrent_sessions"],
        monthly_token_budget=row["monthly_token_budget"],
        created_at=row["created_at"],
    )


def _row_to_api_key(row, *, plaintext: str | None = None) -> ApiKeyResponse:
    data = ApiKeyResponse(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        name=row["name"],
        prefix=row["prefix"],
        scopes=list(row["scopes"] or []),
        status=row["status"],
        rate_limit_rpm=row["rate_limit_rpm"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
    )
    if plaintext is not None:
        return ApiKeyCreatedResponse(**data.model_dump(), key=plaintext)
    return data


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(ctx: AuthContext = Depends(require_auth)):
    """List projects in the caller's organization."""
    rows = await fetch_all(
        "SELECT * FROM projects WHERE org_id = $1 ORDER BY created_at ASC",
        uuid.UUID(ctx.org_id),
    )
    return [_row_to_project(r) for r in rows]


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    ctx: AuthContext = Depends(require_auth),
) -> ProjectResponse:
    """Update project quota limits (Atlas M5, FR-11.2/11.3).

    Bootstrap key only: a project-scoped DB key must not be able to lift
    its own tenant's limits. Omitted fields keep their current value;
    explicit ``null`` clears a limit (unlimited).
    """
    if ctx.api_key_id is not None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": "Quota management requires the bootstrap API key",
            },
        )

    try:
        uid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Project {project_id} not found"},
        )

    updates: list[str] = []
    args: list = []
    if "max_concurrent_sessions" in body.model_fields_set:
        updates.append(f"max_concurrent_sessions = ${len(args) + 1}")
        args.append(body.max_concurrent_sessions)
    if "monthly_token_budget" in body.model_fields_set:
        updates.append(f"monthly_token_budget = ${len(args) + 1}")
        args.append(body.monthly_token_budget)

    if updates:
        updates.append(f"org_id = ${len(args) + 1}")
        args.append(uuid.UUID(ctx.org_id))
        row = await fetch_one(
            f"""
            UPDATE projects SET {", ".join(updates)}
            WHERE id = ${len(args) + 1}
            RETURNING *
            """,
            *args,
            uid,
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "not_found",
                    "message": f"Project {project_id} not found",
                },
            )
        await record_audit(
            "project.quota_updated", "project", project_id, ctx=ctx,
            detail={name: getattr(body, name) for name in body.model_fields_set},
        )
    else:
        row = await fetch_one(
            "SELECT * FROM projects WHERE id = $1 AND org_id = $2",
            uid,
            uuid.UUID(ctx.org_id),
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "not_found",
                    "message": f"Project {project_id} not found",
                },
            )
    return _row_to_project(row)


@router.post("/api-keys", status_code=201, response_model=ApiKeyCreatedResponse)
async def create_api_key(
    body: CreateApiKeyRequest,
    ctx: AuthContext = Depends(require_auth),
) -> ApiKeyResponse:
    """Issue a new API key bound to a project.

    The plaintext key is returned exactly once in this response — only its
    SHA-256 hash and a 12-char display prefix are stored.
    """
    project_id = body.project_id or ctx.project_id
    try:
        project_uid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Project {project_id} not found"},
        )

    project_row = await fetch_one(
        "SELECT org_id FROM projects WHERE id = $1", project_uid
    )
    if project_row is None or str(project_row["org_id"]) != ctx.org_id:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Project {project_id} not found"},
        )

    plaintext = KEY_PREFIX_TAG + secrets.token_urlsafe(_KEY_ENTROPY_BYTES)
    key_hash = _hash_token(plaintext)
    prefix = plaintext[:12]
    scopes = body.scopes if body.scopes is not None else ["*"]

    row = await fetch_one(
        """
        INSERT INTO api_keys (project_id, name, key_hash, prefix, scopes, rate_limit_rpm)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        project_uid,
        body.name,
        key_hash,
        prefix,
        scopes,
        body.rate_limit_rpm,
    )
    await record_audit(
        "api_key.created", "api_key", str(row["id"]), ctx=ctx,
        detail={
            "name": body.name, "prefix": prefix,
            "project_id": str(project_uid),
            "rate_limit_rpm": body.rate_limit_rpm,
        },
    )
    return _row_to_api_key(row, plaintext=plaintext)


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(ctx: AuthContext = Depends(require_auth)):
    """List API keys for the caller's project (metadata only)."""
    rows = await fetch_all(
        "SELECT * FROM api_keys WHERE project_id = $1 ORDER BY created_at ASC",
        uuid.UUID(ctx.project_id),
    )
    return [_row_to_api_key(r) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> None:
    """Revoke an API key (soft delete: ``status='revoked'``).

    Only keys belonging to the caller's project can be revoked. Revocation
    takes effect immediately for new requests; the short-lived auth cache
    (60s TTL) is the upper bound on staleness.
    """
    try:
        uid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"API key {key_id} not found"},
        )
    result = await execute(
        "UPDATE api_keys SET status = 'revoked' "
        "WHERE id = $1 AND project_id = $2 AND status = 'active'",
        uid,
        uuid.UUID(ctx.project_id),
    )
    if result == "UPDATE 0":
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"API key {key_id} not found"},
        )
    await record_audit(
        "api_key.revoked", "api_key", key_id, ctx=ctx,
        detail={"project_id": ctx.project_id},
    )
