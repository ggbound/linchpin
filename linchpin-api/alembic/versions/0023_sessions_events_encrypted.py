"""Atlas B8 — envelope encryption for business-content event payloads.

Revision ID: 0023
Revises: 0022

Adds ``sessions.events_encrypted`` (opt-in via the ``x-events-encrypted``
request header) and ``sessions.data_key`` (the per-session data key wrapped
under the master Fernet key, ``VAULT_ENCRYPTION_KEY``). Business-content
payloads (user.*/agent.*) are stored as ``{"enc": "v1", "ct": ...}``;
control events (session.*/span.*/thread.*) stay plaintext so scheduling
queries never need the key.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql(
        "ALTER TABLE sessions "
        "ADD COLUMN events_encrypted BOOLEAN NOT NULL DEFAULT false"
    )
    conn.exec_driver_sql(
        "ALTER TABLE sessions ADD COLUMN data_key TEXT"
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql("ALTER TABLE sessions DROP COLUMN IF EXISTS data_key")
    conn.exec_driver_sql(
        "ALTER TABLE sessions DROP COLUMN IF EXISTS events_encrypted"
    )
