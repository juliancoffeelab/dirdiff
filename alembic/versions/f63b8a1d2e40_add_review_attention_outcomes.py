"""Separate logical Threads and persist action outcomes.

Revision ID: f63b8a1d2e40
Revises: d52a6e9c8b41
Create Date: 2026-08-15 00:00:00.000000
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

revision: str = "f63b8a1d2e40"
down_revision: str | Sequence[str] | None = "d52a6e9c8b41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist lifecycle and attention at every historical action boundary."""
    connection = op.get_bind()
    review_thread = sa.table(
        "review_thread",
        sa.column("thread_id"),
        sa.column("origin_snapshot_id"),
    )
    review_thread_placement = sa.table(
        "review_thread_placement",
        sa.column("thread_id"),
        sa.column("snapshot_id"),
        sa.column("is_origin"),
    )
    review_action = sa.table(
        "review_action",
        sa.column("thread_id"),
        sa.column("sequence"),
        sa.column("kind"),
        sa.column("comment_id"),
        sa.column("expected_revision"),
        sa.column("body"),
        sa.column("status_after"),
        sa.column("attention_after"),
    )
    tables = set(sa.inspect(connection).get_table_names())
    if "review_thread_placement" not in tables:
        assert "review_thread" in tables
        op.rename_table("review_thread", "review_thread_placement")
        op.create_table(
            "review_thread",
            sa.Column("thread_id", sa.String(32), primary_key=True),
            sa.Column(
                "origin_snapshot_id",
                sa.String(),
                sa.ForeignKey("snapshot.id"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "length(thread_id) = 32 AND thread_id NOT GLOB '*[^0-9a-f]*'",
                name="ck_review_thread_id",
            ),
        )
    else:
        assert "review_thread" in tables
        assert {
            column["name"]
            for column in sa.inspect(connection).get_columns("review_thread")
        } == {"thread_id", "origin_snapshot_id"}
        assert "is_origin" in {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "review_thread_placement"
            )
        }
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(review_thread)
            ).scalar_one()
            == 0
        )
    connection.execute(
        sa.insert(review_thread).from_select(
            ("thread_id", "origin_snapshot_id"),
            sa.select(
                review_thread_placement.c.thread_id,
                review_thread_placement.c.snapshot_id,
            ).where(review_thread_placement.c.is_origin == 1),
        )
    )
    with op.batch_alter_table("review_action") as batch:
        batch.drop_constraint("ck_review_action_variant", type_="check")
        batch.add_column(sa.Column("status_after", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("attention_after", sa.String(), nullable=True)
        )
    outcome = (
        sa.select(
            review_action.c.thread_id,
            review_action.c.sequence,
            sa.literal("open").label("status_after"),
            sa.literal("author").label("attention_after"),
        )
        .where(review_action.c.sequence == 0)
        .cte("outcome", recursive=True)
    )
    next_action = review_action.alias("next_action")
    outcome = outcome.union_all(
        sa.select(
            next_action.c.thread_id,
            next_action.c.sequence,
            sa.case(
                (next_action.c.kind == "thread-resolved", "resolved"),
                (next_action.c.kind == "thread-reopened", "open"),
                (next_action.c.kind == "thread-deleted", "deleted"),
                else_=outcome.c.status_after,
            ).label("status_after"),
            sa.case(
                (next_action.c.kind == "thread-resolved", "none"),
                (next_action.c.kind == "thread-reopened", "both"),
                (next_action.c.kind == "thread-deleted", "none"),
                (next_action.c.kind == "comment-created", "both"),
                else_=outcome.c.attention_after,
            ).label("attention_after"),
        ).join(
            outcome,
            (next_action.c.thread_id == outcome.c.thread_id)
            & (next_action.c.sequence == outcome.c.sequence + 1),
        )
    )
    outcome_for_action = outcome.select().where(
        outcome.c.thread_id == review_action.c.thread_id,
        outcome.c.sequence == review_action.c.sequence,
    )
    connection.execute(
        sa.update(review_action).values(
            status_after=outcome_for_action.with_only_columns(
                outcome.c.status_after
            ).scalar_subquery(),
            attention_after=outcome_for_action.with_only_columns(
                outcome.c.attention_after
            ).scalar_subquery(),
        )
    )
    connection.execute(
        sa.update(review_action)
        .where(review_action.c.sequence == 0)
        .values(kind="thread-created")
    )
    connection.execute(
        sa.update(review_action)
        .where(
            review_action.c.kind.in_(
                ("thread-resolved", "thread-reopened", "thread-deleted")
            )
        )
        .values(expected_revision=None)
    )
    op.drop_index(
        "uq_review_action_comment_created", table_name="review_action"
    )
    with op.batch_alter_table("review_action") as batch:
        batch.alter_column("status_after", nullable=False)
        batch.alter_column("attention_after", nullable=False)
        batch.create_check_constraint(
            "ck_review_action_status_after",
            sa.column("status_after").in_(("open", "resolved", "deleted")),
        )
        batch.create_check_constraint(
            "ck_review_action_attention_after",
            sa.column("attention_after").in_(
                ("author", "reviewer", "both", "none")
            ),
        )
        batch.create_check_constraint(
            "ck_review_action_variant",
            "CASE "
            "WHEN kind IN ('thread-created', 'comment-created') THEN "
            "comment_id IS NOT NULL AND expected_revision IS NULL AND body IS NOT NULL AND length(body) > 0 "
            "WHEN kind = 'comment-edited' THEN comment_id IS NOT NULL AND expected_revision IS NOT NULL AND body IS NOT NULL AND length(body) > 0 "
            "WHEN kind = 'comment-deleted' THEN comment_id IS NOT NULL AND expected_revision IS NOT NULL AND body IS NULL "
            "WHEN kind IN ('thread-resolved', 'thread-reopened') THEN expected_revision IS NULL AND ((comment_id IS NULL AND body IS NULL) OR (comment_id IS NOT NULL AND body IS NOT NULL AND length(body) > 0)) "
            "WHEN kind = 'thread-deleted' THEN comment_id IS NULL AND expected_revision IS NULL AND body IS NULL "
            "ELSE 0 END",
        )
    op.drop_index(
        "uq_review_thread_origin", table_name="review_thread_placement"
    )
    op.drop_index(
        "ix_review_thread_snapshot", table_name="review_thread_placement"
    )
    with op.batch_alter_table("review_thread_placement") as batch:
        batch.drop_constraint("ck_review_thread_locator", type_="check")
        batch.drop_column("is_origin")
        batch.create_check_constraint(
            "ck_review_thread_placement_locator",
            sa.column("private_locator").is_(None)
            | (
                (sa.column("target_kind") == "range")
                & sa.column("outdated_reason").is_(None)
            ),
        )
        batch.create_foreign_key(
            "fk_review_thread_placement_thread",
            "review_thread",
            ["thread_id"],
            ["thread_id"],
        )
    op.create_index(
        "ix_review_thread_origin_snapshot",
        "review_thread",
        ["origin_snapshot_id"],
    )
    op.create_index(
        "ix_review_thread_placement_snapshot",
        "review_thread_placement",
        ["snapshot_id"],
    )
    op.create_index(
        "uq_review_action_comment_created",
        "review_action",
        ["comment_id"],
        unique=True,
        sqlite_where=sa.text(
            "kind IN ('thread-created', 'comment-created', "
            "'thread-resolved', 'thread-reopened')"
        ),
    )
    op.create_index(
        "ix_review_action_thread_activity",
        "review_action",
        ["thread_id", sa.text("activity_id DESC")],
    )


def downgrade() -> None:
    """Reject reversal because persisted attention has no legacy equivalent."""
    raise NotImplementedError("Review attention migration is irreversible.")
