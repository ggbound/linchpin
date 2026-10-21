"""Atlas B9 — unified audit log (PRD FR-12).

Revision ID: 0021
Revises: 0020

Creates ``audit_log`` for immutable record-keeping of control-plane actions:

- Every resource create / update / archive / delete across the v1 API.
- Credential resolution hits (vault + credential ids, never secret values).
- Tool confirmation decisions (approvals / denials).
- Session deletion with data purge.

``project_id`` is nullable so pre-tenancy bootstrap events can still be
recorded; ``detail`` carries structured context (never secrets).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql("""
        CREATE TABLE audit_log (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            project_id    UUID REFERENCES projects(id),
            actor         TEXT NOT NULL,
            action        TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id   TEXT,
            detail        JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)

    conn.exec_driver_sql(
        "CREATE INDEX audit_log_at_idx ON audit_log (at DESC)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX audit_log_project_at_idx ON audit_log (project_id, at DESC)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX audit_log_resource_idx ON audit_log (resource_type, resource_id)"
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql("DROP INDEX IF EXISTS audit_log_resource_idx")
    conn.exec_driver_sql("DROP INDEX IF EXISTS audit_log_project_at_idx")
    conn.exec_driver_sql("DROP INDEX IF EXISTS audit_log_at_idx")
    conn.exec_driver_sql("DROP TABLE IF EXISTS audit_log")
