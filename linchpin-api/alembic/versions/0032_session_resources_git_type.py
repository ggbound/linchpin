"""Allow git_repository (and vault) in session_resources.type.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-22 00:00:00.000000

Fixes a schema/API drift: the session-create persist loop in
``app/routes/sessions.py`` canonicalizes both GitRepositoryResource and
GithubRepositoryResource mounts to ``type='git_repository'`` (v0.5 PR3),
but the 0004-era CHECK only admitted ('file', 'memory_store',
'github_repository') — so every session create carrying a git repo
mount failed with a CHECK violation at persist time.

Widen the CHECK to the full ``SessionResourceType`` union: adds
'git_repository' and 'vault'. ('vault' is already accepted by the API
model even though vault mounts currently don't persist rows —
sessions.vault_ids is the canonical surface.) 'github_repository'
stays admitted for rows written before the canonicalization rule.

The 0004 CHECK was an unnamed inline column CHECK, which PostgreSQL
auto-named ``session_resources_type_check`` (verified via
pg_constraint). Downgrade restores the original 3-value CHECK and will
fail if 'git_repository'/'vault' rows exist — prune them first if you
really need to go back.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE session_resources "
        "DROP CONSTRAINT IF EXISTS session_resources_type_check"
    )
    conn.exec_driver_sql("""
        ALTER TABLE session_resources ADD CONSTRAINT session_resources_type_check
            CHECK (type IN ('file', 'memory_store', 'github_repository',
                            'git_repository', 'vault'))
    """)


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "ALTER TABLE session_resources "
        "DROP CONSTRAINT IF EXISTS session_resources_type_check"
    )
    conn.exec_driver_sql("""
        ALTER TABLE session_resources ADD CONSTRAINT session_resources_type_check
            CHECK (type IN ('file', 'memory_store', 'github_repository'))
    """)
