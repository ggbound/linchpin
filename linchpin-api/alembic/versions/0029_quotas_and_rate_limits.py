"""Atlas M5 (FR-11.2/11.3) — project quotas and per-key rate limits.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-21 00:00:00.000000

Self-hostable limits, all nullable so ``NULL`` = unlimited and existing
tenants are unaffected (no backfill):

- ``projects.max_concurrent_sessions`` — enforced at session creation
  (app.quotas) against the live-session count (same liveness semantics
  as the orchestrator's node-capacity check: not terminated/failed and
  not archived).
- ``projects.monthly_token_budget`` — enforced against the sum of the
  four ``sessions.usage`` token counters over sessions created in the
  current UTC month.
- ``api_keys.rate_limit_rpm`` — requests per minute for the key,
  enforced in-process as a sliding window (app.ratelimit). Bootstrap
  keys (``LINCHPIN_API_KEY``) are never limited.

Precise global limits across multiple API replicas are a gateway-level
concern — see docs/limits.md for the APISIX recipe.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE projects ADD COLUMN max_concurrent_sessions INTEGER"
    )
    conn.exec_driver_sql(
        "ALTER TABLE projects ADD COLUMN monthly_token_budget BIGINT"
    )
    conn.exec_driver_sql(
        "ALTER TABLE api_keys ADD COLUMN rate_limit_rpm INTEGER"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE api_keys DROP COLUMN IF EXISTS rate_limit_rpm")
    conn.exec_driver_sql("ALTER TABLE projects DROP COLUMN IF EXISTS monthly_token_budget")
    conn.exec_driver_sql("ALTER TABLE projects DROP COLUMN IF EXISTS max_concurrent_sessions")
