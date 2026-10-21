"""Atlas B9 — audit log writer.

Every control-plane mutation funnels through :func:`record_audit`. The
write is best-effort by design: audit failures must never take down the
serving request, so any exception (including the RuntimeError raised by
``app.db`` when no pool exists — e.g. unit tests) is swallowed and logged.

``fetch_one``/``execute`` are imported into *this* module's namespace so
route-level test patches (``@patch("app.routes.x.fetch_one")``) don't
interfere with audit bookkeeping.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.auth import AuthContext
from app.db import execute

logger = logging.getLogger(__name__)


def _derive_actor(ctx: AuthContext | None) -> str:
    if ctx is None:
        return "bootstrap"
    if ctx.api_key_id:
        return f"apikey:{ctx.api_key_id}"
    return "bootstrap"


async def record_audit(
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    *,
    ctx: AuthContext | None = None,
    project_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Insert one audit row. Best-effort: never raises."""
    try:
        pid = project_id or (ctx.project_id if ctx else None)
        if pid is not None:
            pid = uuid.UUID(str(pid))
        await execute(
            """
            INSERT INTO audit_log (project_id, actor, action, resource_type, resource_id, detail)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            pid,
            _derive_actor(ctx),
            action,
            resource_type,
            resource_id,
            json.dumps(detail or {}),
        )
    except Exception:  # noqa: BLE001 — audit must be fail-open
        logger.warning("audit write failed for action=%s resource=%s", action, resource_type, exc_info=True)
