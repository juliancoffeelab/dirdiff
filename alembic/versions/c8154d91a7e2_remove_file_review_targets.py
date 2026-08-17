"""Forbid new File-level origins while retaining historical placements.

Revision ID: c8154d91a7e2
Revises: b74d52f083c1
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

revision: str = "c8154d91a7e2"
down_revision: str | Sequence[str] | None = "b74d52f083c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Retain historical File targets at File start, then forbid new ones."""
    connection = op.get_bind()
    review_thread = sa.table(
        "review_thread",
        sa.column("thread_id"),
        sa.column("snapshot_id"),
        sa.column("snapshot_file_id"),
        sa.column("is_origin"),
        sa.column("target_kind"),
        sa.column("region_kind"),
        sa.column("side"),
        sa.column("start_line"),
        sa.column("end_line"),
        sa.column("private_locator"),
    )
    left_side = sa.table(
        "snapshot_file_left",
        sa.column("file_id"),
    )
    right_side = sa.table(
        "snapshot_file_right",
        sa.column("file_id"),
    )
    has_left = sa.exists(
        sa.select(left_side.c.file_id).where(
            left_side.c.file_id == review_thread.c.snapshot_file_id
        )
    )
    has_right = sa.exists(
        sa.select(right_side.c.file_id).where(
            right_side.c.file_id == review_thread.c.snapshot_file_id
        )
    )
    invalid_placement_count = connection.execute(
        sa.select(sa.func.count())
        .select_from(review_thread)
        .where((review_thread.c.target_kind == "file") & ~has_left & ~has_right)
    ).scalar_one()
    if invalid_placement_count != 0:
        raise RuntimeError("File-level review placement has no captured side.")

    # SQLite checks each UPDATE against the old File-only shape. Recreate once
    # with both the old and retained shapes, transform the rows, then the final
    # recreation below removes the obsolete variant.
    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_review_thread_location", type_="check")
        batch_op.drop_constraint("ck_review_thread_locator", type_="check")
        batch_op.create_check_constraint(
            "ck_review_thread_location",
            sa.case(
                (
                    sa.column("snapshot_file_id").is_(None),
                    sa.column("target_kind").is_(None)
                    & sa.column("region_kind").is_(None)
                    & sa.column("region_key").is_(None)
                    & sa.column("side").is_(None)
                    & sa.column("start_line").is_(None)
                    & sa.column("end_line").is_(None)
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
                    & (
                        sa.column("outdated_reason").is_(None)
                        | (sa.column("outdated_reason") == "region_not_found")
                    ),
                ),
                else_=False,
            ),
        )
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
                    & sa.column("target_kind").in_(("file", "file-start")),
                    sa.column("private_locator").is_(None)
                    & sa.column("outdated_reason").is_(None),
                ),
                (
                    sa.column("is_origin") == 0,
                    sa.column("private_locator").is_(None),
                ),
                else_=False,
            ),
        )

    connection.execute(
        sa.update(review_thread)
        .where(review_thread.c.target_kind == "file")
        .values(
            target_kind="file-start",
            side=sa.case((has_right, "right"), else_="left"),
        )
    )

    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch_op.drop_constraint("ck_review_thread_location", type_="check")
        batch_op.drop_constraint("ck_review_thread_locator", type_="check")
        batch_op.create_check_constraint(
            "ck_review_thread_target_kind",
            sa.column("target_kind").is_(None)
            | sa.column("target_kind").in_(("range", "file-start")),
        )
        batch_op.create_check_constraint(
            "ck_review_thread_location",
            sa.case(
                (
                    sa.column("snapshot_file_id").is_(None),
                    sa.column("target_kind").is_(None)
                    & sa.column("region_kind").is_(None)
                    & sa.column("region_key").is_(None)
                    & sa.column("side").is_(None)
                    & sa.column("start_line").is_(None)
                    & sa.column("end_line").is_(None)
                    & (sa.column("outdated_reason") == "file_missing"),
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
                    & (
                        sa.column("outdated_reason").is_(None)
                        | (sa.column("outdated_reason") == "region_not_found")
                    ),
                ),
                else_=False,
            ),
        )
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
                    & (sa.column("target_kind") == "file-start"),
                    sa.column("private_locator").is_(None)
                    & sa.column("outdated_reason").is_(None),
                ),
                (
                    sa.column("is_origin") == 0,
                    sa.column("private_locator").is_(None),
                ),
                else_=False,
            ),
        )


def downgrade() -> None:
    """Restore the former File-level target variants without adding rows."""
    connection = op.get_bind()
    review_thread = sa.table(
        "review_thread",
        sa.column("target_kind"),
        sa.column("region_kind"),
        sa.column("region_key"),
        sa.column("side"),
        sa.column("start_line"),
        sa.column("end_line"),
        sa.column("outdated_reason"),
        sa.column("private_locator"),
        sa.column("is_origin"),
        sa.column("snapshot_file_id"),
    )
    # Restore the old File variant in a shape SQLite can validate while rows
    # are transformed. The final recreation below reinstates the exact former
    # constraints and retains only region-not-found File-start placements.
    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch_op.drop_constraint("ck_review_thread_location", type_="check")
        batch_op.drop_constraint("ck_review_thread_locator", type_="check")
        batch_op.create_check_constraint(
            "ck_review_thread_target_kind",
            sa.column("target_kind").is_(None)
            | sa.column("target_kind").in_(("file", "range", "file-start")),
        )
        batch_op.create_check_constraint(
            "ck_review_thread_location",
            sa.case(
                (
                    sa.column("snapshot_file_id").is_(None),
                    sa.column("target_kind").is_(None)
                    & sa.column("region_kind").is_(None)
                    & sa.column("region_key").is_(None)
                    & sa.column("side").is_(None)
                    & sa.column("start_line").is_(None)
                    & sa.column("end_line").is_(None)
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
                    & (
                        sa.column("outdated_reason").is_(None)
                        | (sa.column("outdated_reason") == "region_not_found")
                    ),
                ),
                else_=False,
            ),
        )
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
                    & sa.column("target_kind").in_(("file", "file-start")),
                    sa.column("private_locator").is_(None)
                    & sa.column("outdated_reason").is_(None),
                ),
                (
                    sa.column("is_origin") == 0,
                    sa.column("private_locator").is_(None),
                ),
                else_=False,
            ),
        )
    connection.execute(
        sa.update(review_thread)
        .where(
            (review_thread.c.target_kind == "file-start")
            & review_thread.c.outdated_reason.is_(None)
        )
        .values(target_kind="file", side=None)
    )
    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch_op.drop_constraint("ck_review_thread_location", type_="check")
        batch_op.drop_constraint("ck_review_thread_locator", type_="check")
        batch_op.create_check_constraint(
            "ck_review_thread_target_kind",
            sa.column("target_kind").is_(None)
            | sa.column("target_kind").in_(("file", "range", "file-start")),
        )
        batch_op.create_check_constraint(
            "ck_review_thread_location",
            sa.case(
                (
                    sa.column("snapshot_file_id").is_(None),
                    sa.column("target_kind").is_(None)
                    & sa.column("region_kind").is_(None)
                    & sa.column("region_key").is_(None)
                    & sa.column("side").is_(None)
                    & sa.column("start_line").is_(None)
                    & sa.column("end_line").is_(None)
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
                    & (sa.column("outdated_reason") == "region_not_found"),
                ),
                else_=False,
            ),
        )
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
