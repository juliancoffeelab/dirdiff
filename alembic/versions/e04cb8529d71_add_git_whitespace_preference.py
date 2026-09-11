"""Store the Git whitespace policy in each Profile's preference row.

Existing Profiles keep whitespace changes visible. Upgrade adds the required
boolean; downgrade removes it without changing other Profile preferences.
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

revision: str = "e04cb8529d71"
down_revision: str | Sequence[str] | None = "c62e91f5a803"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep whitespace changes visible for existing rows and require future values."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column(
                "git_ignore_space_change",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("user_preferences") as batch:
        batch.alter_column("git_ignore_space_change", server_default=None)


def downgrade() -> None:
    """Remove the Git whitespace preference while preserving other stored values."""
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("git_ignore_space_change")
