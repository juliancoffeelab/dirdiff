"""Add persistent review Threads, actions, and agent Profile bindings.

Revision ID: 9fd6c3a1e5b4
Revises: eec8692d4cb9
Create Date: 2026-08-12 00:00:00.000000
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

revision: str = "9fd6c3a1e5b4"
down_revision: str | Sequence[str] | None = "eec8692d4cb9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the normalized persistence required by review and agent APIs."""
    with op.batch_alter_table("snapshot_file") as batch_op:
        batch_op.create_unique_constraint(
            "uq_snapshot_file_id_snapshot",
            ["id", "snapshot_id"],
        )

    op.create_table(
        "agent_profile",
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("user_profile.id"),
            primary_key=True,
        ),
        sa.Column("agent_uuid", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("agent_uuid", name="uq_agent_profile_uuid"),
        sa.CheckConstraint(
            (sa.func.length(sa.column("agent_uuid")) == 32)
            & sa.column("agent_uuid").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_agent_profile_uuid",
        ),
    )

    op.create_table(
        "review_thread",
        sa.Column("thread_id", sa.String(length=32), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.String(length=32),
            sa.ForeignKey("snapshot.id"),
            primary_key=True,
        ),
        sa.Column("snapshot_file_id", sa.String(length=32), nullable=True),
        sa.Column("is_origin", sa.Boolean, nullable=False),
        sa.Column("target_kind", sa.String, nullable=True),
        sa.Column("region_kind", sa.String, nullable=True),
        sa.Column("region_key", sa.String, nullable=True),
        sa.Column("side", sa.String, nullable=True),
        sa.Column("start_line", sa.Integer, nullable=True),
        sa.Column("end_line", sa.Integer, nullable=True),
        sa.Column("outdated_reason", sa.String, nullable=True),
        sa.Column("private_locator_version", sa.Integer, nullable=True),
        sa.Column("private_locator", sa.LargeBinary, nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_file_id", "snapshot_id"],
            ["snapshot_file.id", "snapshot_file.snapshot_id"],
            name="fk_review_thread_snapshot_file",
        ),
        sa.CheckConstraint(
            (sa.func.length(sa.column("thread_id")) == 32)
            & sa.column("thread_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_thread_id",
        ),
        sa.CheckConstraint(
            sa.column("target_kind").is_(None)
            | sa.column("target_kind").in_(("file", "range", "file-start")),
            name="ck_review_thread_target_kind",
        ),
        sa.CheckConstraint(
            sa.column("region_kind").is_(None)
            | sa.column("region_kind").in_(
                ("ordinary", "notebook-cell-source")
            ),
            name="ck_review_thread_region_kind",
        ),
        sa.CheckConstraint(
            sa.column("side").is_(None)
            | sa.column("side").in_(("left", "right")),
            name="ck_review_thread_side",
        ),
        sa.CheckConstraint(
            sa.column("outdated_reason").is_(None)
            | sa.column("outdated_reason").in_(
                ("region_changed", "region_not_found", "file_missing")
            ),
            name="ck_review_thread_outdated_reason",
        ),
        sa.CheckConstraint(
            sa.case(
                (
                    sa.column("region_kind") == "ordinary",
                    sa.column("region_key").is_(None),
                ),
                (
                    sa.column("region_kind") == "notebook-cell-source",
                    sa.column("region_key").is_not(None)
                    & (sa.func.length(sa.column("region_key")) > 0),
                ),
                (
                    sa.column("region_kind").is_(None),
                    sa.column("region_key").is_(None),
                ),
                else_=False,
            ),
            name="ck_review_thread_region",
        ),
        sa.CheckConstraint(
            sa.case(
                (
                    sa.column("snapshot_file_id").is_(None),
                    sa.column("target_kind").is_(None)
                    & sa.column("region_kind").is_(None)
                    & sa.column("region_key").is_(None)
                    & sa.column("side").is_(None)
                    & sa.column("start_line").is_(None)
                    & sa.column("end_line").is_(None)
                    & sa.column("outdated_reason").is_not(None)
                    & (sa.column("outdated_reason") == "file_missing"),
                ),
                (
                    sa.column("target_kind") == "file",
                    sa.column("region_kind").is_(None)
                    & sa.column("region_key").is_(None)
                    & sa.column("side").is_(None)
                    & sa.column("start_line").is_(None)
                    & sa.column("end_line").is_(None)
                    & sa.column("outdated_reason").is_(None),
                ),
                (
                    sa.column("target_kind") == "range",
                    sa.column("region_kind").is_not(None)
                    & sa.column("side").is_not(None)
                    & sa.column("start_line").is_not(None)
                    & (sa.column("start_line") >= 1)
                    & sa.column("end_line").is_not(None)
                    & (sa.column("end_line") >= sa.column("start_line"))
                    & (
                        sa.column("outdated_reason").is_(None)
                        | (sa.column("outdated_reason") == "region_changed")
                    ),
                ),
                (
                    sa.column("target_kind") == "file-start",
                    sa.column("region_kind").is_(None)
                    & sa.column("region_key").is_(None)
                    & sa.column("side").is_not(None)
                    & sa.column("start_line").is_(None)
                    & sa.column("end_line").is_(None)
                    & sa.column("outdated_reason").is_not(None)
                    & (sa.column("outdated_reason") == "region_not_found"),
                ),
                else_=False,
            ),
            name="ck_review_thread_location",
        ),
        sa.CheckConstraint(
            sa.case(
                (
                    (sa.column("is_origin") == 1)
                    & (sa.column("target_kind") == "range"),
                    sa.column("private_locator_version").is_not(None)
                    & (sa.column("private_locator_version") == 1)
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
            name="ck_review_thread_locator",
        ),
    )
    op.create_index(
        "uq_review_thread_origin",
        "review_thread",
        ["thread_id"],
        unique=True,
        sqlite_where=sa.column("is_origin") == 1,
    )
    op.create_index(
        "ix_review_thread_snapshot",
        "review_thread",
        ["snapshot_id"],
    )

    op.create_table(
        "review_action",
        sa.Column("operation_id", sa.String(length=32), primary_key=True),
        sa.Column("activity_id", sa.Integer, nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("user_profile.id"),
            nullable=False,
        ),
        sa.Column("comment_id", sa.String(length=32), nullable=True),
        sa.Column("expected_revision", sa.Integer, nullable=True),
        sa.Column("body", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id", "snapshot_id"],
            ["review_thread.thread_id", "review_thread.snapshot_id"],
            name="fk_review_action_thread",
        ),
        sa.UniqueConstraint("activity_id", name="uq_review_action_activity"),
        sa.UniqueConstraint(
            "thread_id", "sequence", name="uq_review_action_sequence"
        ),
        sa.CheckConstraint(
            (sa.func.length(sa.column("operation_id")) == 32)
            & sa.column("operation_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_action_operation_id",
        ),
        sa.CheckConstraint(
            (sa.func.length(sa.column("thread_id")) == 32)
            & sa.column("thread_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_action_thread_id",
        ),
        sa.CheckConstraint(
            sa.column("profile_id") > 0,
            name="ck_review_action_profile_id",
        ),
        sa.CheckConstraint(
            sa.column("comment_id").is_(None)
            | (
                (sa.func.length(sa.column("comment_id")) == 32)
                & sa.column("comment_id").op("NOT GLOB")("*[^0-9a-f]*")
            ),
            name="ck_review_action_comment_id",
        ),
        sa.CheckConstraint(
            (sa.column("sequence") >= 0)
            & (
                sa.column("expected_revision").is_(None)
                | (sa.column("expected_revision") >= 0)
            ),
            name="ck_review_action_revisions",
        ),
        sa.CheckConstraint(
            sa.case(
                (
                    sa.column("kind") == "comment-created",
                    sa.column("comment_id").is_not(None)
                    & sa.column("expected_revision").is_(None)
                    & sa.column("body").is_not(None)
                    & (sa.func.length(sa.column("body")) > 0),
                ),
                (
                    sa.column("kind") == "comment-edited",
                    sa.column("comment_id").is_not(None)
                    & sa.column("expected_revision").is_not(None)
                    & sa.column("body").is_not(None)
                    & (sa.func.length(sa.column("body")) > 0),
                ),
                (
                    sa.column("kind") == "comment-deleted",
                    sa.column("comment_id").is_not(None)
                    & sa.column("expected_revision").is_not(None)
                    & sa.column("body").is_(None),
                ),
                (
                    sa.column("kind").in_(
                        ("thread-resolved", "thread-reopened", "thread-deleted")
                    ),
                    sa.column("comment_id").is_(None)
                    & sa.column("expected_revision").is_not(None)
                    & sa.column("body").is_(None),
                ),
                else_=False,
            ),
            name="ck_review_action_variant",
        ),
        sa.CheckConstraint(
            sa.func.length(sa.column("created_at")) > 0,
            name="ck_review_action_created_at",
        ),
    )
    op.create_index(
        "uq_review_action_comment_created",
        "review_action",
        ["comment_id"],
        unique=True,
        sqlite_where=sa.column("kind") == "comment-created",
    )


def downgrade() -> None:
    """Remove review persistence while retaining Room and Snapshot state."""
    op.drop_index(
        "uq_review_action_comment_created", table_name="review_action"
    )
    op.drop_table("review_action")
    op.drop_index("ix_review_thread_snapshot", table_name="review_thread")
    op.drop_index("uq_review_thread_origin", table_name="review_thread")
    op.drop_table("review_thread")
    op.drop_table("agent_profile")
    with op.batch_alter_table("snapshot_file") as batch_op:
        batch_op.drop_constraint(
            "uq_snapshot_file_id_snapshot",
            type_="unique",
        )
