"""Migrate to project_id

Revision ID: a81905840a9e
Revises: 1ff3a7065910
Create Date: 2026-07-11 00:46:42.000203

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a81905840a9e"
down_revision: str | Sequence[str] | None = "1ff3a7065910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""

    with op.batch_alter_table("repo_main_branch", schema=None) as batch_op:
        batch_op.alter_column(
            "repo_id",
            new_column_name="project_id",
        )
    with op.batch_alter_table("repo_mark_meta", schema=None) as batch_op:
        batch_op.alter_column(
            "repo_id",
            new_column_name="project_id",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("repo_main_branch", schema=None) as batch_op:
        batch_op.alter_column(
            "project_id",
            new_column_name="repo_id",
        )
    with op.batch_alter_table("repo_mark_meta", schema=None) as batch_op:
        batch_op.alter_column(
            "project_id",
            new_column_name="repo_id",
        )
