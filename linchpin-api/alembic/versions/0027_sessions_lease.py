"""Atlas C2 — session orchestrator lease columns.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-21 00:00:00.000000

Multi-replica support (C2): orchestrator ownership of a session moves from
"whatever process spawned the task" to an explicit, inspectable lease.

- ``sessions.orchestrator_instance`` — instance id of the replica currently
  driving the session (or NULL for pre-C2 rows / released sessions).
- ``sessions.orchestrator_heartbeat`` — renewed every heartbeat interval
  while the lease is held. A heartbeat older than the lease timeout means
  the owner died or lost its DB connection, and the supervisor may take the
  session over (FR-2.3 "worker crash → automatic migration").

The actual exclusion primitive is a Postgres advisory lock held on a
dedicated connection (app.cluster.SessionLease); these columns are the
observable bookkeeping that lets any replica find abandoned sessions. The
partial index covers exactly the rows the supervisor scans.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE sessions ADD COLUMN orchestrator_instance TEXT"
    )
    conn.exec_driver_sql(
        "ALTER TABLE sessions ADD COLUMN orchestrator_heartbeat TIMESTAMPTZ"
    )
    conn.exec_driver_sql(
        "CREATE INDEX idx_sessions_lease_stale ON sessions (orchestrator_heartbeat) "
        "WHERE status IN ('running', 'idle')"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP INDEX IF EXISTS idx_sessions_lease_stale")
    conn.exec_driver_sql(
        "ALTER TABLE sessions DROP COLUMN IF EXISTS orchestrator_heartbeat"
    )
    conn.exec_driver_sql(
        "ALTER TABLE sessions DROP COLUMN IF EXISTS orchestrator_instance"
    )
