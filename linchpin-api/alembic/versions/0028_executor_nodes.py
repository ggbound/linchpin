"""Atlas M4 (FR-4.6) — BYO executor node registry.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-21 00:00:00.000000

Bring-your-own executor: customers register compute nodes ("executor
daemons") that run session sandboxes on their own infrastructure. The API
schedules sandboxes onto a node by HTTP-proxying the SandboxProtocol calls
(app.sandbox.RemoteExecutorSandbox).

- ``token`` is stored Fernet-encrypted (reversible) because the API must
  present it to the daemon on every call — the auth direction is the
  opposite of api-keys, where only a hash is needed. The SHA-256 hash and
  a short prefix are also persisted for display/integrity checks.
- ``last_heartbeat_at`` is renewed by the daemon; online/offline is
  derived at read time (heartbeat fresher than 90s), not stored.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        CREATE TABLE executor_nodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL,
            name TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL,
            token_encrypted BYTEA NOT NULL,
            token_hash TEXT NOT NULL,
            token_prefix TEXT NOT NULL DEFAULT '',
            max_sessions INT NOT NULL DEFAULT 5,
            last_heartbeat_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX idx_executor_nodes_org ON executor_nodes (org_id) "
        "WHERE archived_at IS NULL"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS executor_nodes")
