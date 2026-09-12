"""Store Difftastic comment handling and GumTree matcher preferences.

Existing Profiles keep comment changes visible and use GumTree Simple.
Upgrade adds required fields; downgrade removes only these options.
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

revision: str = "f73ac904b612"
down_revision: str | Sequence[str] | None = "e04cb8529d71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve existing behavior and require explicit values for future writes."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column(
                "difftastic_ignore_comments",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "gumtree_matcher",
                sa.String(),
                nullable=False,
                server_default="gumtree-simple",
            )
        )
    with op.batch_alter_table("user_preferences") as batch:
        batch.alter_column("difftastic_ignore_comments", server_default=None)
        batch.alter_column("gumtree_matcher", server_default=None)


def downgrade() -> None:
    """Remove the two structural engine options, retaining other preferences."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("gumtree_matcher")
        batch.drop_column("difftastic_ignore_comments")
