"""Atlas A1/A2 — session retry counters + stop_reason (PRD §4.1).

Revision ID: 0016
Revises: 0015

Adds two columns to ``sessions`` for the Atlas state machine:

1. ``retry_count`` — automatic retry attempts after a retryable
   provider/sandbox error. Orchestrator caps at 3; once exhausted the
   session lands in ``idle`` with ``stop_reason='retries_exhausted'``
   and can be resumed manually.
2. ``stop_reason`` — machine-readable reason for the current non-running
   state: 'end_turn', 'requires_action', 'retries_exhausted',
   'provisioning_failed', 'interrupted', ...

Note: ``sessions.agent_version`` already exists since migration 0001
(sessions pin the agent's version at create time), so A1 needs no
schema change here — only the orchestrator read path.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE sessions ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
    )
    conn.exec_driver_sql("ALTER TABLE sessions ADD COLUMN stop_reason TEXT")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE sessions DROP COLUMN IF EXISTS stop_reason")
    conn.exec_driver_sql("ALTER TABLE sessions DROP COLUMN IF EXISTS retry_count")
