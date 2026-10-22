"""Skill version column (console PRD Requirement 23).

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-21 00:00:00.000000

Adds ``skills.version`` — the optional ``version`` field from the
SKILL.md frontmatter, surfaced in the API and the console's Skills
table. Nullable: legacy rows and bundles without a version in their
frontmatter simply report ``null``.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE skills ADD COLUMN IF NOT EXISTS version TEXT"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE skills DROP COLUMN IF EXISTS version")
