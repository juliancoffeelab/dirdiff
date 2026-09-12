"""Persist Git moved-color options in each Profile's complete preference row.

Upgrade initializes existing selections to no and requires explicit future
writes. Downgrade removes only these two settings; this migration stores no
rendered output or move relationships.
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

revision: str = "a62b910c3d47"
down_revision: str | Sequence[str] | None = "f73ac904b612"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Initialize existing Profiles without making API or future writes optional."""
    with op.batch_alter_table("user_preferences") as batch:
        for name in ("git_color_moved", "git_color_moved_ws"):
            batch.add_column(
                sa.Column(
                    name, sa.String(), nullable=False, server_default="no"
                )
            )
    with op.batch_alter_table("user_preferences") as batch:
        for name in ("git_color_moved", "git_color_moved_ws"):
            batch.alter_column(name, server_default=None)


def downgrade() -> None:
    """Remove Git move settings while preserving all other Profile preferences."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("git_color_moved_ws")
        batch.drop_column("git_color_moved")
