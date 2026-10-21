"""Atlas M4 (FR-4.6) — BYO executor node registry.

POST   /v1/executor-nodes            — register a node (token returned ONCE)
GET    /v1/executor-nodes            — list live nodes for the caller's org
GET    /v1/executor-nodes/{id}       — node detail
DELETE /v1/executor-nodes/{id}       — soft delete (archived_at = now())
POST   /v1/executor-nodes/{id}/heartbeat — daemon self-auth heartbeat

Token model differs from api-keys by direction of use: the API is the
*presenter* of the secret (it calls the node's daemon with it), so the
token is stored Fernet-encrypted under VAULT_ENCRYPTION_KEY alongside a
SHA-256 hash (heartbeat self-auth) and a 12-char display prefix. The
plaintext — ``lex_`` + 32 bytes of URL-safe entropy — is returned exactly
once at registration. Scope enforcement for the /v1/executor-nodes
resource reuses the central check in :mod:`app.auth`.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.audit import record_audit
from app.auth import AuthContext, _extract_token, _hash_token, require_auth
from app.db import execute, fetch_all, fetch_one
from app.models import (
    ExecutorNodeCreatedResponse,
    ExecutorNodeCreateRequest,
    ExecutorNodeHeartbeatResponse,
    ExecutorNodeResponse,
)

router = APIRouter(tags=["executors"])

# The heartbeat endpoint authenticates with the *node token* (not an API
# key), so it lives on its own router without the global
# verify_bearer_token dependency that /v1 routes carry.
daemon_router = APIRouter(tags=["executors"])

TOKEN_PREFIX_TAG = "lex_"
_TOKEN_ENTROPY_BYTES = 32
# last_heartbeat_at within this window → status "online"
HEARTBEAT_FRESH_SEC = 90.0


def _node_status(last_heartbeat_at: datetime | None) -> str:
    if last_heartbeat_at is None:
        return "offline"
    delta = (datetime.now(timezone.utc) - last_heartbeat_at).total_seconds()
    return "online" if delta <= HEARTBEAT_FRESH_SEC else "offline"


def _row_to_node(row) -> ExecutorNodeResponse:
    return ExecutorNodeResponse(
        id=str(row["id"]),
        org_id=str(row["org_id"]),
        name=row["name"],
        url=row["url"],
        prefix=row["token_prefix"],
        max_sessions=row["max_sessions"],
        status=_node_status(row["last_heartbeat_at"]),
        last_heartbeat_at=row["last_heartbeat_at"],
        created_at=row["created_at"],
    )


_NODE_COLUMNS = (
    "id, org_id, name, url, token_encrypted, token_hash, token_prefix, "
    "max_sessions, last_heartbeat_at, created_at"
)


@router.post("/executor-nodes", status_code=201, response_model=ExecutorNodeCreatedResponse)
async def register_executor_node(
    body: ExecutorNodeCreateRequest,
    ctx: AuthContext = Depends(require_auth),
) -> ExecutorNodeCreatedResponse:
    """Register an executor daemon for the caller's org.

    The daemon token is returned exactly once in this response — configure
    it into the daemon's ``LINCHPIN_EXECUTOR_TOKEN``.
    """
    from app.encryption import get_encryption_service

    try:
        encryption = get_encryption_service()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "encryption_unavailable",
                "message": "VAULT_ENCRYPTION_KEY is not configured; "
                           "executor nodes cannot be registered",
            },
        ) from exc

    token = TOKEN_PREFIX_TAG + secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
    try:
        row = await fetch_one(
            """
            INSERT INTO executor_nodes
                (org_id, name, url, token_encrypted, token_hash,
                 token_prefix, max_sessions)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, org_id, name, url, token_prefix, max_sessions,
                      last_heartbeat_at, created_at
            """,
            uuid.UUID(ctx.org_id),
            body.name,
            body.url,
            encryption.encrypt(token),
            _hash_token(token),
            token[:12],
            body.max_sessions,
        )
    except UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "name_conflict",
                "message": f"Executor node '{body.name}' already exists",
            },
        )

    await record_audit(
        "executor_node.created", "executor_node", str(row["id"]), ctx=ctx,
        detail={"name": body.name, "url": body.url},
    )
    base = _row_to_node(row)
    return ExecutorNodeCreatedResponse(**base.model_dump(), token=token)


@router.get("/executor-nodes", response_model=list[ExecutorNodeResponse])
async def list_executor_nodes(ctx: AuthContext = Depends(require_auth)):
    """List live executor nodes in the caller's organization."""
    rows = await fetch_all(
        f"SELECT {_NODE_COLUMNS} FROM executor_nodes "
        "WHERE org_id = $1 AND archived_at IS NULL ORDER BY created_at ASC",
        uuid.UUID(ctx.org_id),
    )
    return [_row_to_node(r) for r in rows]


@router.get("/executor-nodes/{node_id}", response_model=ExecutorNodeResponse)
async def get_executor_node(
    node_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> ExecutorNodeResponse:
    """Fetch one executor node (org-scoped)."""
    row = await _load_node(node_id, ctx)
    return _row_to_node(row)


@router.delete("/executor-nodes/{node_id}", status_code=204)
async def delete_executor_node(
    node_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> None:
    """Soft-delete an executor node (archived_at = now())."""
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Executor node {node_id} not found",
            },
        )
    result = await execute(
        "UPDATE executor_nodes SET archived_at = now(), updated_at = now() "
        "WHERE id = $1 AND org_id = $2 AND archived_at IS NULL",
        uid,
        uuid.UUID(ctx.org_id),
    )
    if result == "UPDATE 0":
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Executor node {node_id} not found",
            },
        )
    await record_audit(
        "executor_node.deleted", "executor_node", node_id, ctx=ctx,
        detail={"org_id": ctx.org_id},
    )


@daemon_router.post(
    "/v1/executor-nodes/{node_id}/heartbeat",
    response_model=ExecutorNodeHeartbeatResponse,
)
async def executor_node_heartbeat(node_id: str, request: Request):
    """Daemon self-auth heartbeat: bearer token must hash to the node's
    stored token_hash. Updates last_heartbeat_at (readiness signal for
    the status derivation in list/detail responses)."""
    token = _extract_token(request)
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Executor node {node_id} not found",
            },
        )
    row = await fetch_one(
        "SELECT id FROM executor_nodes "
        "WHERE id = $1 AND token_hash = $2 AND archived_at IS NULL",
        uid,
        _hash_token(token),
    )
    if row is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "message": "Invalid executor node token",
            },
        )
    await execute(
        "UPDATE executor_nodes SET last_heartbeat_at = now(), updated_at = now() "
        "WHERE id = $1",
        uid,
    )
    return ExecutorNodeHeartbeatResponse(id=str(row["id"]), status="online")


async def _load_node(node_id: str, ctx: AuthContext):
    try:
        uid = uuid.UUID(node_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Executor node {node_id} not found",
            },
        )
    row = await fetch_one(
        f"SELECT {_NODE_COLUMNS} FROM executor_nodes "
        "WHERE id = $1 AND org_id = $2 AND archived_at IS NULL",
        uid,
        uuid.UUID(ctx.org_id),
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Executor node {node_id} not found",
            },
        )
    return row
