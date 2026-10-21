"""Atlas M5 (FR-11.2/11.3) — project-level session quotas.

Two optional per-project limits, enforced before any session-creation
side effect:

- ``projects.max_concurrent_sessions`` — live-session count, with the
  same liveness semantics as the orchestrator's node-capacity check:
  ``status NOT IN ('terminated', 'failed') AND archived_at IS NULL``.
  Sub-agent threads are created through the same code path, so they
  count toward the limit too.
- ``projects.monthly_token_budget`` — sum of the four ``sessions.usage``
  token counters over sessions created in the current UTC month
  (COALESCE so legacy rows missing a JSONB key count as zero). The
  month boundary is computed in UTC explicitly so a non-UTC server
  timezone cannot skew the window.

Both columns are nullable; ``NULL`` = unlimited (default after
migration 0029, existing projects unaffected).

Isolation note: queries run via this module's *own* ``fetch_one``
binding rather than any route-module import. When the database pool is
not initialised (unit tests, degraded startup) the check degrades to
"allow" — quotas are a capacity guard, not a correctness guard, and
failing all session creation while the pool is down mirrors neither the
C2 lease fallback nor the compaction no-pool tolerance. Real database
errors still propagate.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException

from app.db import fetch_one


async def enforce_session_quotas(project_id: str) -> None:
    """Raise ``HTTPException(429, quota_exceeded)`` when a limit is reached."""
    try:
        project_uid = uuid.UUID(project_id)
    except ValueError:
        return

    try:
        row = await fetch_one(
            "SELECT max_concurrent_sessions, monthly_token_budget "
            "FROM projects WHERE id = $1",
            project_uid,
        )
    except RuntimeError:
        # Pool not initialised — degraded mode, skip the check.
        return

    if row is None:
        return

    max_concurrent = row["max_concurrent_sessions"]
    if max_concurrent is not None:
        try:
            count_row = await fetch_one(
                """
                SELECT count(*) AS active
                FROM sessions
                WHERE project_id = $1
                  AND status NOT IN ('terminated', 'failed')
                  AND archived_at IS NULL
                """,
                project_uid,
            )
        except RuntimeError:
            return
        active = int(count_row["active"]) if count_row is not None else 0
        limit = int(max_concurrent)
        if active >= limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "quota_exceeded",
                    "message": (
                        "Concurrent session limit reached "
                        f"({active}/{limit} active)"
                    ),
                    "limit_type": "concurrent_sessions",
                    "limit": limit,
                    "active": active,
                },
            )

    token_budget = row["monthly_token_budget"]
    if token_budget is not None:
        try:
            usage_row = await fetch_one(
                """
                SELECT COALESCE(SUM(
                    COALESCE((usage->>'input_tokens')::bigint, 0)
                    + COALESCE((usage->>'output_tokens')::bigint, 0)
                    + COALESCE((usage->>'cache_creation_input_tokens')::bigint, 0)
                    + COALESCE((usage->>'cache_read_input_tokens')::bigint, 0)
                ), 0) AS tokens
                FROM sessions
                WHERE project_id = $1
                  AND created_at >= (
                      date_trunc('month', now() AT TIME ZONE 'UTC')
                  ) AT TIME ZONE 'UTC'
                """,
                project_uid,
            )
        except RuntimeError:
            return
        used = int(usage_row["tokens"]) if usage_row is not None else 0
        budget = int(token_budget)
        if used >= budget:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "quota_exceeded",
                    "message": (
                        "Monthly token budget exhausted "
                        f"({used}/{budget} tokens)"
                    ),
                    "limit_type": "monthly_tokens",
                    "limit": budget,
                    "used": used,
                },
            )
