"""Atlas FR-6.1 — agent ``multi_agents`` (subagent declaration surface).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-20 00:00:00.000000

The subagent runtime (spawn_thread, parent-link mirror, depth cap) already
exists; what FR-6.1 lacked was the configuration surface: an agent declaring
*which* workers it may spawn. That declaration lives on both the live row and
the version snapshots so A1 version-locking semantics extend naturally —
a session spawns workers from the exact declaration it booted under:

- ``agents.multi_agents`` — JSONB list of SubagentConfig
  (``{name, agent_id, environment_id?, description?}``).
- ``agent_versions.multi_agents`` — same column on snapshots (0006 table);
  rows written before this migration backfill to ``'[]'`` via the server
  default, which simply means "no spawn_subagent tool" (declaration is the
  enablement gate).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE agents ADD COLUMN multi_agents JSONB NOT NULL "
        "DEFAULT '[]'::jsonb"
    )
    conn.exec_driver_sql(
        "ALTER TABLE agent_versions ADD COLUMN multi_agents JSONB NOT NULL "
        "DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE agent_versions DROP COLUMN IF EXISTS multi_agents"
    )
    conn.exec_driver_sql(
        "ALTER TABLE agents DROP COLUMN IF EXISTS multi_agents"
    )
