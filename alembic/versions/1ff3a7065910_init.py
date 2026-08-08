"""Initial schema

Revision ID: 1ff3a7065910
Revises:
Create Date: 2026-07-11 00:17:35.608299
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1ff3a7065910"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""

    # repo marks
    op.create_table(
        "repo_mark",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("path", sa.String, nullable=False, unique=True),
    )
    op.create_table(
        "repo_mark_meta",
        sa.Column(
            "repo_id",
            sa.Integer,
            sa.ForeignKey("repo_mark.id"),
            primary_key=True,
        ),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "repo_main_branch",
        sa.Column(
            "repo_id",
            sa.Integer,
            sa.ForeignKey("repo_mark.id"),
            primary_key=True,
        ),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("remote", sa.String, nullable=True),
        sa.Column("branch", sa.String, nullable=False),
    )

    # users
    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String, nullable=False),
    )
    op.create_table(
        "user_preferences",
        sa.Column(
            "user_profile_id",
            sa.Integer,
            sa.ForeignKey("user_profile.id"),
            primary_key=True,
        ),
        sa.Column("aggressive_folds", sa.Boolean, nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise RuntimeError("don't downgrade from init")
