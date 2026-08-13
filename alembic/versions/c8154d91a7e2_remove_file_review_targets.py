"""Require every review origin to select a text range.

Revision ID: c8154d91a7e2
Revises: b74d52f083c1
Create Date: 2026-08-13 00:00:00.000000
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

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
    """Pin historical File-level origins to line one, then forbid new ones."""
    connection = op.get_bind()
    file_origins = connection.exec_driver_sql(
        "SELECT rt.thread_id, rt.snapshot_id, sf.path, "
        "left_side.repository_path, right_side.repository_path "
        "FROM review_thread AS rt "
        "JOIN snapshot_file AS sf ON sf.id = rt.snapshot_file_id "
        "LEFT JOIN snapshot_file_left AS left_side "
        "ON left_side.file_id = sf.id "
        "LEFT JOIN snapshot_file_right AS right_side "
        "ON right_side.file_id = sf.id "
        "WHERE rt.is_origin = 1 AND rt.target_kind = 'file'"
    ).all()
    for (
        thread_id,
        snapshot_id,
        capture_path,
        left_path,
        right_path,
    ) in file_origins:
        side = "right" if right_path is not None else "left"
        if side == "left" and left_path is None:
            raise RuntimeError("File-level review origin has no captured side.")
        text = (Path(capture_path) / side).read_text(encoding="utf-8-sig")
        if text.splitlines() == []:
            raise RuntimeError(
                "File-level review origin cannot be pinned to an empty File."
            )
        source = text.encode()
        locator = json.dumps(
            {
                "side": side,
                "region_hash": hashlib.sha256(source).hexdigest(),
                "region_start_byte": 0,
                "region_end_byte": len(source),
                "segments": [],
                "notebook_cell_id": None,
                "notebook_source_hash": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        connection.exec_driver_sql(
            "UPDATE review_thread SET target_kind = 'range', "
            "region_kind = 'ordinary', side = ?, start_line = 1, "
            "end_line = 1, private_locator = ? "
            "WHERE thread_id = ? AND snapshot_id = ?",
            (side, locator, thread_id, snapshot_id),
        )
        connection.exec_driver_sql(
            "UPDATE review_thread SET target_kind = 'range', "
            "region_kind = 'ordinary', side = ?, start_line = 1, "
            "end_line = 1 WHERE thread_id = ? AND target_kind = 'file'",
            (side, thread_id),
        )

    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch_op.drop_constraint("ck_review_thread_location", type_="check")
        batch_op.drop_constraint("ck_review_thread_locator", type_="check")
        batch_op.create_check_constraint(
            "ck_review_thread_target_kind",
            "target_kind IS NULL OR target_kind IN ('range', 'file-start')",
        )
        batch_op.create_check_constraint(
            "ck_review_thread_location",
            "CASE "
            "WHEN snapshot_file_id IS NULL THEN "
            "target_kind IS NULL AND region_kind IS NULL AND "
            "region_key IS NULL AND side IS NULL AND start_line IS NULL AND "
            "end_line IS NULL AND outdated_reason = 'file_missing' "
            "WHEN target_kind = 'range' THEN "
            "region_kind IS NOT NULL AND side IS NOT NULL AND "
            "start_line IS NOT NULL AND start_line >= 1 AND "
            "end_line IS NOT NULL AND end_line >= start_line AND "
            "(outdated_reason IS NULL OR outdated_reason = 'region_changed') "
            "WHEN target_kind = 'file-start' THEN "
            "region_kind IS NULL AND region_key IS NULL AND side IS NOT NULL AND "
            "start_line IS NULL AND end_line IS NULL AND "
            "outdated_reason = 'region_not_found' "
            "ELSE 0 END",
        )
        batch_op.create_check_constraint(
            "ck_review_thread_locator",
            "CASE "
            "WHEN is_origin = 1 AND target_kind = 'range' THEN "
            "private_locator IS NOT NULL AND outdated_reason IS NULL "
            "WHEN is_origin = 0 THEN private_locator IS NULL "
            "ELSE 0 END",
        )


def downgrade() -> None:
    """Restore the former File-level target variants without adding rows."""
    with op.batch_alter_table("review_thread", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch_op.drop_constraint("ck_review_thread_location", type_="check")
        batch_op.drop_constraint("ck_review_thread_locator", type_="check")
        batch_op.create_check_constraint(
            "ck_review_thread_target_kind",
            "target_kind IS NULL OR "
            "target_kind IN ('file', 'range', 'file-start')",
        )
        batch_op.create_check_constraint(
            "ck_review_thread_location",
            "CASE "
            "WHEN snapshot_file_id IS NULL THEN "
            "target_kind IS NULL AND region_kind IS NULL AND "
            "region_key IS NULL AND side IS NULL AND start_line IS NULL AND "
            "end_line IS NULL AND outdated_reason = 'file_missing' "
            "WHEN target_kind = 'file' THEN "
            "region_kind IS NULL AND region_key IS NULL AND side IS NULL AND "
            "start_line IS NULL AND end_line IS NULL AND outdated_reason IS NULL "
            "WHEN target_kind = 'range' THEN "
            "region_kind IS NOT NULL AND side IS NOT NULL AND "
            "start_line IS NOT NULL AND start_line >= 1 AND "
            "end_line IS NOT NULL AND end_line >= start_line AND "
            "(outdated_reason IS NULL OR outdated_reason = 'region_changed') "
            "WHEN target_kind = 'file-start' THEN "
            "region_kind IS NULL AND region_key IS NULL AND side IS NOT NULL AND "
            "start_line IS NULL AND end_line IS NULL AND "
            "outdated_reason = 'region_not_found' "
            "ELSE 0 END",
        )
        batch_op.create_check_constraint(
            "ck_review_thread_locator",
            "CASE "
            "WHEN is_origin = 1 AND target_kind = 'range' THEN "
            "private_locator IS NOT NULL AND outdated_reason IS NULL "
            "WHEN is_origin = 1 AND target_kind = 'file' THEN "
            "private_locator IS NULL "
            "WHEN is_origin = 0 THEN private_locator IS NULL "
            "ELSE 0 END",
        )
