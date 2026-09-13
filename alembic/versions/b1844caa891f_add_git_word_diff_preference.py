"""Persist whether each Profile enables Git word-level decoration.

Existing Profiles retain line-only Git output. Future preference writes require
the boolean explicitly; this migration stores no rendered diff data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

__all__ = [
    "branch_labels",
    "depends_on",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]

revision: str = "b1844caa891f"
down_revision: str | Sequence[str] | None = "a62b910c3d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep word decoration off for existing rows and require future values."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column(
                "git_word_diff",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("user_preferences") as batch:
        batch.alter_column("git_word_diff", server_default=None)


def downgrade() -> None:
    """Remove the Git word-decoration preference and preserve other values."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("git_word_diff")
