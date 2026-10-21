"""Atlas M6 (C1 phase-2) — row-level security tenant isolation.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-21 00:00:00.000000

Defense-in-depth for multi-tenancy (the application-level
``WHERE project_id = ...`` predicates remain the primary line). A single
fail-open policy is attached to every tenant-owned table:

    USING / WITH CHECK (
        coalesce(current_setting('app.current_project', true), '') = ''
        OR <tenant column>::text = current_setting('app.current_project', true)
    )

Semantics:

- ``app.current_project`` GUC unset (background jobs, internal tasks,
  pre-auth lookups such as ``_load_api_key``) -> all rows visible.
  This is deliberately fail-open: an unset context must never brick
  housekeeping, and the API layer still applies its own predicates.
  The emptiness check is ``coalesce(..., '') = ''`` rather than
  ``IS NULL`` because Postgres reads a RESET placeholder GUC as the
  empty string (verified on PG 17), not as NULL — an ``IS NULL``
  branch alone would never fire after a RESET.
- The tenant comparison is ``::text`` equality rather than
  ``= current_setting(...)::uuid``. The planner may evaluate a stable
  function with constant arguments at PLAN time (observed on PG 17:
  even ``EXPLAIN`` raised ``invalid input syntax for type uuid: ""``
  after a RESET), and short-circuit ``OR`` evaluation is not
  guaranteed during planning — so the cast would raise before the
  fail-open branch could save the query. With ``::text`` comparison
  the worst case is a plain ``false`` (empty string never equals a
  uuid's canonical text form), never an error.
- GUC set -> only rows of that project/workspace/org are readable or
  writable, regardless of a forgotten WHERE clause. A malformed
  (non-uuid) GUC value matches nothing: fail-closed for the bad
  value, which only the application can set.

``FORCE ROW LEVEL SECURITY`` is required because the application
connects as the table owner (same role runs the Alembic migrations);
FORCE makes policies apply to the owner too. Superusers and BYPASSRLS
roles still bypass — by design, for operational break-glass access.

Tables intentionally NOT covered:

- ``organizations`` / ``projects`` — auth does JOINs across projects and
  admin listing needs cross-project visibility.
- ``audit_log`` — compliance requires the full cross-tenant view and its
  ``project_id`` is nullable (platform-level rows).
- Child tables reached through a parent JOIN (``credentials`` via
  ``vault_id``, ``memories`` / ``memory_versions`` via ``memory_store_id``,
  ``session_resources`` / ``webhook_deliveries`` / ``outcome_evaluations``
  / ``agent_versions`` / ``events`` / ``idempotency_keys``) — filtering
  the parent suffices and keeps hot fan-out queries single-plan.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, tenant column) pairs — column must be NOT NULL uuid.
_PROJECT_TABLES = (
    "agents",
    "environments",
    "sessions",
    "vaults",
    "files",
    "webhook_endpoints",
    "api_keys",
    "deployments",
)
_WORKSPACE_TABLES = ("memory_stores", "skills", "dreams")
_ORG_TABLES = ("executor_nodes",)

_POLICY = """
CREATE POLICY tenant_isolation ON {table}
USING (
    coalesce(current_setting('app.current_project', true), '') = ''
    OR {column}::text = current_setting('app.current_project', true)
)
WITH CHECK (
    coalesce(current_setting('app.current_project', true), '') = ''
    OR {column}::text = current_setting('app.current_project', true)
)
"""


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in (
        [(t, "project_id") for t in _PROJECT_TABLES]
        + [(t, "workspace_id") for t in _WORKSPACE_TABLES]
        + [(t, "org_id") for t in _ORG_TABLES]
    ):
        conn.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(_POLICY.format(table=table, column=column))


def downgrade() -> None:
    conn = op.get_bind()
    for table in _PROJECT_TABLES + _WORKSPACE_TABLES + _ORG_TABLES:
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        conn.exec_driver_sql(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
