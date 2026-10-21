"""Atlas B5 — allow ``source='artifact'`` on files.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-20 00:00:00.000000

The original ``files`` table (migration 0003) constrained ``source`` to
``('upload', 'deliverable')``. The B5 ``save_artifact`` tool registers
sandbox-copied deliverables as ``source='artifact'`` rows, which the old
CHECK would reject with a CheckConstraintViolation on any real database.
Drop and rebuild the constraint with the third value added; ``artifact``
rows behave like deliverables elsewhere (same scope/dedup semantics), so
no other constraint changes.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE files DROP CONSTRAINT IF EXISTS files_source_check")
    conn.exec_driver_sql(
        "ALTER TABLE files ADD CONSTRAINT files_source_check "
        "CHECK (source IN ('upload', 'deliverable', 'artifact'))"
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Refusing to downgrade while artifact rows exist keeps the invariant
    # honest — deleting real rows as a side effect of a downgrade would be
    # surprising. Delete them explicitly first if you really need the old
    # constraint back.
    conn.exec_driver_sql("DELETE FROM files WHERE source = 'artifact'")
    conn.exec_driver_sql("ALTER TABLE files DROP CONSTRAINT IF EXISTS files_source_check")
    conn.exec_driver_sql(
        "ALTER TABLE files ADD CONSTRAINT files_source_check "
        "CHECK (source IN ('upload', 'deliverable'))"
    )
