"""Atlas B10.1 — usage aggregation endpoint.

GET /v1/usage — aggregate ``sessions.usage`` token counters for the
caller's project. The time window filters on ``sessions.created_at``
(``from``/``to``, both optional ISO-8601); grouping is by UTC day
(default) or by project id.

Token counters mirror ``SessionUsage``: input / output / cache
creation / cache read. Every counter is summed with COALESCE so legacy
rows missing a JSONB key count as zero.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth import AuthContext, require_auth
from app.db import fetch_all
from app.models import UsageBucket, UsageResponse

logger = logging.getLogger("linchpin-api.routes.usage")

router = APIRouter(tags=["usage"])

# The four counters written by orchestrator.update_usage(); the JSONB
# keys are also the response column names.
_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _row_to_bucket(row) -> UsageBucket:
    return UsageBucket(
        bucket=str(row["bucket"]),
        sessions=int(row["sessions"]),
        **{key: int(row[key]) for key in _TOKEN_KEYS},
    )


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    ctx: AuthContext = Depends(require_auth),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    group_by: Literal["day", "project"] = Query("day"),
) -> UsageResponse:
    """Aggregate token usage for the caller's project.

    Buckets are UTC days by default; ``group_by=project`` folds the
    whole window into a single project row (kept for symmetry with the
    platform-level aggregation in the upgrade plan).
    """

    bucket_expr = (
        "to_char(date_trunc('day', created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')"
        if group_by == "day"
        else "project_id::text"
    )

    where = ["project_id = $1"]
    args: list = [uuid.UUID(ctx.project_id)]
    if from_ is not None:
        where.append(f"created_at >= ${len(args) + 1}")
        args.append(from_)
    if to is not None:
        where.append(f"created_at <= ${len(args) + 1}")
        args.append(to)
    where_sql = " AND ".join(where)

    def _select(bucket_sql: str, group_clause: str) -> str:
        counters = ", ".join(
            f"COALESCE(SUM(COALESCE((usage->>'{key}')::bigint, 0)), 0)::bigint AS {key}"
            for key in _TOKEN_KEYS
        )
        return (
            f"SELECT {bucket_sql} AS bucket, COUNT(*) AS sessions, {counters} "
            f"FROM sessions WHERE {where_sql}{group_clause}"
        )

    bucket_rows = await fetch_all(
        _select(bucket_expr, " GROUP BY bucket ORDER BY bucket ASC"), *args
    )
    total_rows = await fetch_all(_select("'total'", ""), *args)

    return UsageResponse(
        group_by=group_by,
        from_=from_,
        to=to,
        total=_row_to_bucket(total_rows[0]) if total_rows else UsageBucket(bucket="total"),
        buckets=[_row_to_bucket(row) for row in bucket_rows],
    )
