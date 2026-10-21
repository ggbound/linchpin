"""Atlas B12 — events retention support (PRD §7 / FR-1.4).

Revision ID: 0022
Revises: 0021

Adds ``events.created_at`` (TIMESTAMPTZ, server default) so the retention
cleanup loop can delete old events in bulk, plus a supporting index.
Existing rows get the migration timestamp as ``created_at`` — acceptable
backfill, since they either age out first or belong to already-terminated
sessions.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql(
        "ALTER TABLE events "
        "ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    conn.exec_driver_sql(
        "CREATE INDEX idx_events_created_at ON events (created_at)"
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql("DROP INDEX IF EXISTS idx_events_created_at")
    conn.exec_driver_sql("ALTER TABLE events DROP COLUMN IF EXISTS created_at")
