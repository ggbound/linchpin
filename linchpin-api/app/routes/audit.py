"""Atlas B9 — audit log export endpoint.

GET /v1/audit — NDJSON stream of audit rows for the caller's project.

Filters: ``from`` / ``to`` (ISO-8601 timestamps), ``resource_type``,
``action``, ``limit`` (default 5000, hard cap 50000). Rows are streamed as
newline-delimited JSON ordered newest-first, using internal keyset
pagination so large exports never buffer fully in memory.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.auth import AuthContext, require_auth
from app.db import fetch_all

logger = logging.getLogger("linchpin-api.routes.audit")

router = APIRouter(tags=["audit"])

_PAGE_SIZE = 1000
_MAX_LIMIT = 50000


def _row_to_json(row) -> str:
    detail = row["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    record = {
        "id": str(row["id"]),
        "at": row["at"].isoformat(),
        "project_id": str(row["project_id"]) if row["project_id"] else None,
        "actor": row["actor"],
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "detail": detail or {},
    }
    return json.dumps(record, ensure_ascii=False, default=str)


@router.get("/audit")
async def export_audit(
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    resource_type: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(5000, ge=1, le=_MAX_LIMIT),
):
    """Stream audit log entries as NDJSON (newest first)."""

    async def rows() -> AsyncIterator[str]:
        remaining = limit
        cursor_at: datetime | None = None
        cursor_id: uuid.UUID | None = None

        while remaining > 0:
            batch = min(_PAGE_SIZE, remaining)
            where = ["project_id = $1"]
            args: list = [uuid.UUID(ctx.project_id)]
            if from_ is not None:
                where.append(f"at >= ${len(args) + 1}")
                args.append(from_)
            if to is not None:
                where.append(f"at <= ${len(args) + 1}")
                args.append(to)
            if resource_type:
                where.append(f"resource_type = ${len(args) + 1}")
                args.append(resource_type)
            if action:
                where.append(f"action = ${len(args) + 1}")
                args.append(action)
            if cursor_at is not None:
                where.append(f"(at, id) < (${len(args) + 1}, ${len(args) + 2})")
                args.extend([cursor_at, cursor_id])

            sql = (
                "SELECT * FROM audit_log WHERE "
                + " AND ".join(where)
                + f" ORDER BY at DESC, id DESC LIMIT {batch}"
            )
            rows_out = await fetch_all(sql, *args)
            if not rows_out:
                break

            for row in rows_out:
                yield _row_to_json(row) + "\n"

            remaining -= len(rows_out)
            if len(rows_out) < batch:
                break
            cursor_at = rows_out[-1]["at"]
            cursor_id = rows_out[-1]["id"]

    return StreamingResponse(
        rows(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )
