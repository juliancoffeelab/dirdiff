"""Store each Profile's Git word-boundary mode and custom word regex.

Existing Profiles keep Git's default whitespace boundary and no custom regex,
stored as NULL. Future preference writes require the mode explicitly; this
migration stores no rendered diff data.
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

revision: str = "30badddc272b"
down_revision: str | Sequence[str] | None = "b1844caa891f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Initialize existing rows to the default boundary with no custom regex."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column(
                "git_word_boundary",
                sa.String(),
                nullable=False,
                server_default="default",
            )
        )
        batch.add_column(
            sa.Column("git_word_diff_regex", sa.String(), nullable=True)
        )
    with op.batch_alter_table("user_preferences") as batch:
        batch.alter_column("git_word_boundary", server_default=None)


def downgrade() -> None:
    """Remove both word-boundary preferences while preserving other values."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("git_word_boundary")
        batch.drop_column("git_word_diff_regex")
