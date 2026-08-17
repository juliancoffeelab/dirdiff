"""Remove speculative review locator versioning.

Revision ID: b74d52f083c1
Revises: 9fd6c3a1e5b4
Create Date: 2026-08-13 00:00:00.000000
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

revision: str = "b74d52f083c1"
down_revision: str | Sequence[str] | None = "9fd6c3a1e5b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the unused locator version while preserving every locator."""
    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "ck_review_thread_locator",
            type_="check",
        )
        batch_op.drop_column("private_locator_version")
        batch_op.create_check_constraint(
            "ck_review_thread_locator",
            sa.case(
                (
                    (sa.column("is_origin") == 1)
                    & (sa.column("target_kind") == "range"),
                    sa.column("private_locator").is_not(None)
                    & sa.column("outdated_reason").is_(None),
                ),
                (
                    (sa.column("is_origin") == 1)
                    & (sa.column("target_kind") == "file"),
                    sa.column("private_locator").is_(None),
                ),
                (
                    sa.column("is_origin") == 0,
                    sa.column("private_locator").is_(None),
                ),
                else_=False,
            ),
        )


def downgrade() -> None:
    """Restore version one for each persisted origin locator."""
    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "ck_review_thread_locator",
            type_="check",
        )
        batch_op.add_column(
            sa.Column("private_locator_version", sa.Integer(), nullable=True)
        )

    review_thread = sa.table(
        "review_thread",
        sa.column("private_locator_version"),
        sa.column("private_locator"),
    )
    op.execute(
        sa.update(review_thread)
        .where(review_thread.c.private_locator.is_not(None))
        .values(private_locator_version=1)
    )

    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.create_check_constraint(
            "ck_review_thread_locator",
            sa.case(
                (
                    (sa.column("is_origin") == 1)
                    & (sa.column("target_kind") == "range"),
                    (sa.column("private_locator_version") == 1)
                    & sa.column("private_locator").is_not(None)
                    & sa.column("outdated_reason").is_(None),
                ),
                (
                    (sa.column("is_origin") == 1)
                    & (sa.column("target_kind") == "file"),
                    sa.column("private_locator_version").is_(None)
                    & sa.column("private_locator").is_(None),
                ),
                (
                    sa.column("is_origin") == 0,
                    sa.column("private_locator_version").is_(None)
                    & sa.column("private_locator").is_(None),
                ),
                else_=False,
            ),
        )
