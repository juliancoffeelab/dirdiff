"""Persist each Profile's Git algorithm in its existing preference row.

Existing Profiles begin with Myers, the declared Git default. This revision
changes only preference storage; it does not alter Snapshots or review data.
Upgrade adds the required column and downgrade removes that preference.
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

revision: str = "c62e91f5a803"
down_revision: str | Sequence[str] | None = "a91d5e2c4f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Initialize existing preference rows with Myers and require future values."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column(
                "git_algorithm",
                sa.String(),
                nullable=False,
                server_default="myers",
            )
        )
    with op.batch_alter_table("user_preferences") as batch:
        batch.alter_column("git_algorithm", server_default=None)


def downgrade() -> None:
    """Remove the stored Git choice while preserving other Profile preferences."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("git_algorithm")
