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
            "length(agent_uuid) = 32 AND agent_uuid NOT GLOB '*[^0-9a-f]*'",
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
            "length(thread_id) = 32 AND thread_id NOT GLOB '*[^0-9a-f]*'",
            name="ck_review_thread_id",
        ),
        sa.CheckConstraint(
            "target_kind IS NULL OR "
            "target_kind IN ('file', 'range', 'file-start')",
            name="ck_review_thread_target_kind",
        ),
        sa.CheckConstraint(
            "region_kind IS NULL OR "
            "region_kind IN ('ordinary', 'notebook-cell-source')",
            name="ck_review_thread_region_kind",
        ),
        sa.CheckConstraint(
            "side IS NULL OR side IN ('left', 'right')",
            name="ck_review_thread_side",
        ),
        sa.CheckConstraint(
            "outdated_reason IS NULL OR outdated_reason IN "
            "('region_changed', 'region_not_found', 'file_missing')",
            name="ck_review_thread_outdated_reason",
        ),
        sa.CheckConstraint(
            "CASE "
            "WHEN region_kind = 'ordinary' THEN region_key IS NULL "
            "WHEN region_kind = 'notebook-cell-source' THEN "
            "region_key IS NOT NULL AND length(region_key) > 0 "
            "WHEN region_kind IS NULL THEN region_key IS NULL "
            "ELSE 0 END",
            name="ck_review_thread_region",
        ),
        sa.CheckConstraint(
            "CASE "
            "WHEN snapshot_file_id IS NULL THEN "
            "target_kind IS NULL AND region_kind IS NULL AND "
            "region_key IS NULL AND side IS NULL AND start_line IS NULL AND "
            "end_line IS NULL AND outdated_reason IS NOT NULL AND "
            "outdated_reason = 'file_missing' "
            "WHEN target_kind = 'file' THEN "
            "region_kind IS NULL AND region_key IS NULL AND side IS NULL AND "
            "start_line IS NULL AND end_line IS NULL AND outdated_reason IS NULL "
            "WHEN target_kind = 'range' THEN "
            "region_kind IS NOT NULL AND side IS NOT NULL AND start_line IS NOT NULL AND "
            "start_line >= 1 AND end_line IS NOT NULL AND end_line >= start_line AND "
            "(outdated_reason IS NULL OR outdated_reason = 'region_changed') "
            "WHEN target_kind = 'file-start' THEN "
            "region_kind IS NULL AND region_key IS NULL AND side IS NOT NULL AND "
            "start_line IS NULL AND end_line IS NULL AND outdated_reason IS NOT NULL AND "
            "outdated_reason = 'region_not_found' "
            "ELSE 0 END",
            name="ck_review_thread_location",
        ),
        sa.CheckConstraint(
            "CASE "
            "WHEN is_origin = 1 AND target_kind = 'range' THEN "
            "private_locator_version IS NOT NULL AND "
            "private_locator_version = 1 AND private_locator IS NOT NULL AND "
            "outdated_reason IS NULL "
            "WHEN is_origin = 1 AND target_kind = 'file' THEN "
            "private_locator_version IS NULL AND private_locator IS NULL "
            "WHEN is_origin = 0 THEN "
            "private_locator_version IS NULL AND private_locator IS NULL "
            "ELSE 0 END",
            name="ck_review_thread_locator",
        ),
    )
    op.create_index(
        "uq_review_thread_origin",
        "review_thread",
        ["thread_id"],
        unique=True,
        sqlite_where=sa.text("is_origin = 1"),
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
            "length(operation_id) = 32 AND operation_id NOT GLOB '*[^0-9a-f]*'",
            name="ck_review_action_operation_id",
        ),
        sa.CheckConstraint(
            "length(thread_id) = 32 AND thread_id NOT GLOB '*[^0-9a-f]*'",
            name="ck_review_action_thread_id",
        ),
        sa.CheckConstraint(
            "profile_id > 0", name="ck_review_action_profile_id"
        ),
        sa.CheckConstraint(
            "comment_id IS NULL OR (length(comment_id) = 32 AND "
            "comment_id NOT GLOB '*[^0-9a-f]*')",
            name="ck_review_action_comment_id",
        ),
        sa.CheckConstraint(
            "sequence >= 0 AND "
            "(expected_revision IS NULL OR expected_revision >= 0)",
            name="ck_review_action_revisions",
        ),
        sa.CheckConstraint(
            "CASE "
            "WHEN kind = 'comment-created' THEN "
            "comment_id IS NOT NULL AND expected_revision IS NULL AND "
            "body IS NOT NULL AND length(body) > 0 "
            "WHEN kind = 'comment-edited' THEN "
            "comment_id IS NOT NULL AND expected_revision IS NOT NULL AND "
            "body IS NOT NULL AND length(body) > 0 "
            "WHEN kind = 'comment-deleted' THEN "
            "comment_id IS NOT NULL AND expected_revision IS NOT NULL AND "
            "body IS NULL "
            "WHEN kind IN ('thread-resolved', 'thread-reopened', "
            "'thread-deleted') THEN comment_id IS NULL AND "
            "expected_revision IS NOT NULL AND body IS NULL "
            "ELSE 0 END",
            name="ck_review_action_variant",
        ),
        sa.CheckConstraint(
            "length(created_at) > 0",
            name="ck_review_action_created_at",
        ),
    )
    op.create_index(
        "uq_review_action_comment_created",
        "review_action",
        ["comment_id"],
        unique=True,
        sqlite_where=sa.text("kind = 'comment-created'"),
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
