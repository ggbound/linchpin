"""Atlas C1 — multi-tenancy identity layer (PRD FR-10.1).

Revision ID: 0020
Revises: 0019

Creates the tenant hierarchy and stamps existing rows into the default
project:

- ``organizations`` → ``projects`` → ``api_keys`` identity tables.
- Default org + project seeded with well-known UUID
  ``00000000-0000-0000-0000-000000000001`` (matches the placeholder
  ``LINCHPIN_DEFAULT_WORKSPACE_ID`` already used by memory_stores/dreams).
- Tenant-stamped resource tables get ``project_id``: agents, environments,
  sessions, vaults, files, webhook_endpoints. Child resources (agent_versions,
  memories, memory_versions, webhook_deliveries, outcome_evaluations,
  session_resources, events, credentials) stay tenant-scoped through their
  parent row.
- ``skills`` / ``dreams`` / ``memory_stores`` keep their existing
  ``workspace_id`` column — its semantics upgrade from placeholder to
  "project id"; the all-zeros placeholder value from skills is migrated to
  the default project.

The ``LINCHPIN_API_KEY`` bootstrap key is NOT inserted here — it is a
runtime environment variable; auth.py authenticates it directly against
the default project without a table row.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TENANT_UUID = "00000000-0000-0000-0000-000000000001"

# (table, index name) pairs that receive a project_id column + index.
_TENANT_TABLES = [
    ("agents", "agents_project_idx"),
    ("environments", "environments_project_idx"),
    ("sessions", "sessions_project_idx"),
    ("vaults", "vaults_project_idx"),
    ("files", "files_project_idx"),
    ("webhook_endpoints", "webhook_endpoints_project_idx"),
]


def upgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql("""
        CREATE TABLE organizations (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name       TEXT NOT NULL,
            slug       TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    conn.exec_driver_sql("""
        CREATE TABLE projects (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id     UUID NOT NULL REFERENCES organizations(id),
            name       TEXT NOT NULL,
            slug       TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (org_id, slug)
        )
    """)

    conn.exec_driver_sql("""
        CREATE TABLE api_keys (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id   UUID NOT NULL REFERENCES projects(id),
            name         TEXT NOT NULL DEFAULT '',
            key_hash     TEXT NOT NULL UNIQUE,
            prefix       TEXT NOT NULL,
            scopes       TEXT[] NOT NULL DEFAULT '{}',
            status       TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'revoked')),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at TIMESTAMPTZ
        )
    """)

    # Seed the default tenant. Idempotent so re-running against a DB that
    # already has the well-known rows (e.g. restored dump) is a no-op.
    conn.exec_driver_sql(
        f"""
        INSERT INTO organizations (id, name, slug)
        VALUES ('{DEFAULT_TENANT_UUID}', 'Default Organization', 'default')
        ON CONFLICT (id) DO NOTHING
        """
    )
    conn.exec_driver_sql(
        f"""
        INSERT INTO projects (id, org_id, name, slug)
        VALUES ('{DEFAULT_TENANT_UUID}', '{DEFAULT_TENANT_UUID}',
                'Default Project', 'default')
        ON CONFLICT (id) DO NOTHING
        """
    )

    # Stamp existing resource tables with a project_id. NOT NULL with a
    # DEFAULT keeps the migration online and acts as a safety net: any
    # code path that forgets to pass an explicit project_id lands the row
    # in the default project rather than failing the write.
    for table, index in _TENANT_TABLES:
        conn.exec_driver_sql(
            f"""
            ALTER TABLE {table}
            ADD COLUMN project_id UUID NOT NULL DEFAULT '{DEFAULT_TENANT_UUID}'
            REFERENCES projects(id)
            """
        )
        conn.exec_driver_sql(
            f"CREATE INDEX {index} ON {table} (project_id)"
        )

    # skills used an all-zeros placeholder for workspace_id; unify every
    # tenant-stamped column on the default project UUID.
    conn.exec_driver_sql(
        f"""
        UPDATE skills
        SET workspace_id = '{DEFAULT_TENANT_UUID}'
        WHERE workspace_id = '00000000-0000-0000-0000-000000000000'
        """
    )


def downgrade() -> None:
    conn = op.get_bind()

    for table, index in reversed(_TENANT_TABLES):
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {index}")
        conn.exec_driver_sql(
            f"ALTER TABLE {table} DROP COLUMN IF EXISTS project_id"
        )

    conn.exec_driver_sql("DROP TABLE IF EXISTS api_keys")
    conn.exec_driver_sql("DROP TABLE IF EXISTS projects")
    conn.exec_driver_sql("DROP TABLE IF EXISTS organizations")
