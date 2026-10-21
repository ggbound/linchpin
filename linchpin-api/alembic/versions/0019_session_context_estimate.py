"""Atlas B1 — session context estimate for compaction (PRD FR-2.2).

Revision ID: 0019
Revises: 0018

Adds one column to ``sessions``:

``context_estimate`` — the orchestrator's running estimate of the
conversation's token footprint (input + output + cache read/write of the
last turn, calibrated after each provider call). When the estimate
reaches ~80% of the model context window, ``maybe_compact`` rewrites the
covered prefix of the event log into a ``session.compacted`` event and
``build_context`` replays it as a system summary instead of raw history.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE sessions ADD COLUMN context_estimate INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE sessions DROP COLUMN IF EXISTS context_estimate"
    )
