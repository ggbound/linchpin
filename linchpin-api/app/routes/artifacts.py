"""Artifacts API endpoints (Atlas B5, FR-9.1).

GET    /v1/artifacts                 — List agent-saved artifacts (source='artifact')
GET    /v1/artifacts/{artifact_id}   — Get artifact metadata
GET    /v1/artifacts/{artifact_id}/content — Stream artifact bytes

Artifacts are files the agent explicitly exported from its sandbox via the
``save_artifact`` tool (``source='artifact'``, ``scope_type='session'``,
``downloadable=TRUE``). They live in the same ``files`` table as uploads and
deliverables — the artifact surface is a filtered read-only view, so tenant
isolation follows directly from ``files.project_id`` (assigned at INSERT time
via a subquery on the owning session) and no session join is needed.
"""

from __future__ import annotations

import logging
import urllib.parse
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth import AuthContext, require_auth
from app.db import fetch_all, fetch_one
from app.files import get_file_store
from app.models import FileResponse, PaginatedFilesResponse

logger = logging.getLogger("linchpin-api")

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_artifact(row: asyncpg.Record) -> FileResponse:
    return FileResponse(
        id=str(row["id"]),
        filename=row["filename"],
        content_type=row["content_type"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        source=row["source"],
        downloadable=row["downloadable"],
        scope_type=row["scope_type"],
        scope_id=str(row["scope_id"]) if row["scope_id"] is not None else None,
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    """Parse a UUID or raise 404; rejects malformed input as missing."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"{label} {value} not found"},
        )


async def _load_artifact(artifact_uid: uuid.UUID, project_uid: uuid.UUID) -> asyncpg.Record:
    """Fetch an artifact row scoped to the caller's project and the
    artifact source. Returns None when the row doesn't exist, belongs to
    another project, or isn't an agent-saved artifact."""
    return await fetch_one(
        """
        SELECT * FROM files
        WHERE id = $1 AND project_id = $2
          AND source = 'artifact' AND archived_at IS NULL
        """,
        artifact_uid,
        project_uid,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=PaginatedFilesResponse)
async def list_artifacts(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_auth),
) -> PaginatedFilesResponse:
    """List artifacts saved by agents. ``session_id`` narrows to one session;
    omit it to list every artifact in the project."""
    project_uid = uuid.UUID(ctx.project_id)
    if session_id is not None:
        try:
            session_uid = uuid.UUID(session_id)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"Invalid session id: {session_id}",
                },
            )
        rows = await fetch_all(
            """
            SELECT * FROM files
            WHERE project_id = $1 AND archived_at IS NULL
              AND source = 'artifact'
              AND scope_type = 'session' AND scope_id = $2
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
            """,
            project_uid,
            session_uid,
            limit + 1,
            offset,
        )
    else:
        rows = await fetch_all(
            """
            SELECT * FROM files
            WHERE project_id = $1 AND archived_at IS NULL
              AND source = 'artifact'
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            project_uid,
            limit + 1,
            offset,
        )

    has_more = len(rows) > limit
    items = rows[:limit]
    return PaginatedFilesResponse(
        data=[_row_to_artifact(r) for r in items],
        has_more=has_more,
        next_cursor=None,
    )


@router.get("/{artifact_id}", response_model=FileResponse)
async def get_artifact(
    artifact_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> FileResponse:
    """Get artifact metadata."""
    artifact_uid = _parse_uuid(artifact_id, "Artifact")
    row = await _load_artifact(artifact_uid, uuid.UUID(ctx.project_id))
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Artifact {artifact_id} not found",
            },
        )
    return _row_to_artifact(row)


@router.get("/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> StreamingResponse:
    """Stream artifact bytes as an attachment (RFC 5987 filename encoding)."""
    artifact_uid = _parse_uuid(artifact_id, "Artifact")
    row = await _load_artifact(artifact_uid, uuid.UUID(ctx.project_id))
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"Artifact {artifact_id} not found",
            },
        )
    if not row["downloadable"]:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "not_downloadable",
                "message": "this file is not marked downloadable",
            },
        )

    store = get_file_store()
    try:
        iterator = store.read(row["storage_path"])
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "storage_missing",
                "message": "artifact row exists but its bytes are missing from storage",
            },
        )

    # RFC 5987 encoding so filenames with spaces / unicode survive transport.
    filename: str = row["filename"]
    quoted = urllib.parse.quote(filename, safe="")
    disposition = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quoted}'

    return StreamingResponse(
        iterator,
        media_type=row["content_type"],
        headers={"Content-Disposition": disposition},
    )
