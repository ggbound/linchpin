"""Atlas A8 — idempotency keys (PRD FR-1.3).

Revision ID: 0018
Revises: 0017

New table ``idempotency_keys``: stores completed responses for requests
that carried an ``Idempotency-Key`` header so a client retry with the
same key + same body replays the original response instead of
double-executing. Rows expire after 24h (hourly lifespan cleanup pass).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            endpoint TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_status INTEGER,
            response_body JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_idempotency_keys_created_at "
        "ON idempotency_keys (created_at)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS idempotency_keys")
