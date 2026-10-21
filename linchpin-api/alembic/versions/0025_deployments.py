"""Atlas B7 — deployments (scheduled agent runs, FR-8.1).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-20 00:00:00.000000

Adds the table that backs cron-triggered agent runs plus the reverse
link from sessions to their deployment:

- ``deployments`` — one row per scheduled run configuration (cron
  expression + IANA timezone + initial event seed). ``next_run_at`` is
  recomputed with croniter after every trigger; the scheduler loop polls
  ``status='active' AND archived_at IS NULL AND next_run_at <= NOW()``
  with ``FOR UPDATE SKIP LOCKED`` so multi-process deployments are safe.
- ``sessions.deployment_id`` — provenance for runs the scheduler
  created; the run history for a deployment is simply sessions filtered
  on this column.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        CREATE TABLE deployments (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id),
            agent_id        UUID NOT NULL REFERENCES agents(id),
            environment_id  UUID NOT NULL REFERENCES environments(id),
            cron            TEXT NOT NULL,
            timezone        TEXT NOT NULL DEFAULT 'UTC',
            initial_events  JSONB NOT NULL DEFAULT '[]'::jsonb,
            status          TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('paused', 'active')),
            next_run_at     TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            archived_at     TIMESTAMPTZ
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX deployments_due_idx ON deployments "
        "(next_run_at) WHERE status = 'active' AND archived_at IS NULL"
    )
    conn.exec_driver_sql(
        """
        ALTER TABLE sessions
        ADD COLUMN deployment_id UUID REFERENCES deployments(id) ON DELETE SET NULL
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX sessions_deployment_idx ON sessions (deployment_id) "
        "WHERE deployment_id IS NOT NULL"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP INDEX IF EXISTS sessions_deployment_idx")
    conn.exec_driver_sql("ALTER TABLE sessions DROP COLUMN IF EXISTS deployment_id")
    conn.exec_driver_sql("DROP INDEX IF EXISTS deployments_due_idx")
    conn.exec_driver_sql("DROP TABLE IF EXISTS deployments")
