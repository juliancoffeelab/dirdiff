"""Define the reversible schema transition for retained Room file state.

Alembic calls `upgrade` to add inactive Mark state and the normalized Room,
Snapshot, File, side, metadata, and lazy-reason relations. It calls `downgrade`
to remove those relations and restore the preceding Mark schema. This module
owns only that database-schema transition and its relational constraints; it
guarantees no initial Room data and leaves existing Marks active.

The migration does not select Rooms, capture or remove filesystem contents,
interpret correspondence keys, or implement application persistence queries.

Revision ID: eec8692d4cb9
Revises: a81905840a9e
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "eec8692d4cb9"
down_revision: str | Sequence[str] | None = "a81905840a9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add retained Rooms, captured Files, metadata, and inactive Marks.

    Existing Marks remain active. The new relations contain no initial rows;
    manifests populate them through `RoomStore` after deployment.
    """
    op.add_column(
        "repo_mark",
        sa.Column(
            "active",
            sa.Boolean,
            nullable=False,
            server_default="1",
        ),
    )
    op.create_table(
        "room",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "mark_id",
            sa.Integer,
            sa.ForeignKey("repo_mark.id"),
            nullable=True,
        ),
        sa.Column("tab", sa.String, nullable=False),
        sa.Column("backend_key", sa.LargeBinary, nullable=False),
        sa.CheckConstraint(
            sa.column("tab").in_(
                ("head", "refs", "branch-review", "pull-request", "preset")
            ),
            name="ck_room_tab",
        ),
        sa.CheckConstraint(
            sa.column("mark_id").is_(None) == (sa.column("tab") == "preset"),
            name="ck_room_mark_tab",
        ),
        sa.CheckConstraint(
            sa.func.length(sa.column("backend_key")) > 0,
            name="ck_room_backend_key",
        ),
    )
    op.create_index(
        "uq_room_mark_tab_backend_key",
        "room",
        ["mark_id", "tab", "backend_key"],
        unique=True,
        sqlite_where=sa.column("mark_id").is_not(None),
    )
    op.create_index(
        "uq_room_preset_tab_backend_key",
        "room",
        ["tab", "backend_key"],
        unique=True,
        sqlite_where=sa.column("mark_id").is_(None),
    )
    op.create_table(
        "snapshot",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "room_id",
            sa.Integer,
            sa.ForeignKey("room.id"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.UniqueConstraint(
            "room_id",
            "content_hash",
            name="uq_snapshot_content",
        ),
        sa.CheckConstraint(
            (sa.func.length(sa.column("id")) == 32)
            & sa.column("id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_snapshot_id",
        ),
        sa.CheckConstraint(
            sa.func.length(sa.column("content_hash")) == 32,
            name="ck_snapshot_content_hash",
        ),
    )
    op.create_table(
        "snapshot_meta",
        sa.Column(
            "snapshot_id",
            sa.String(length=32),
            sa.ForeignKey("snapshot.id"),
            primary_key=True,
        ),
        sa.Column("left_label", sa.String, nullable=False),
        sa.Column("right_label", sa.String, nullable=False),
        sa.Column("added_lines", sa.Integer, nullable=True),
        sa.Column("removed_lines", sa.Integer, nullable=True),
        sa.CheckConstraint(
            (sa.func.length(sa.column("left_label")) > 0)
            & (sa.func.length(sa.column("right_label")) > 0),
            name="ck_snapshot_meta_labels",
        ),
        sa.CheckConstraint(
            (
                sa.column("added_lines").is_(None)
                & sa.column("removed_lines").is_(None)
            )
            | (
                sa.column("added_lines").is_not(None)
                & sa.column("removed_lines").is_not(None)
                & (sa.column("added_lines") >= 0)
                & (sa.column("removed_lines") >= 0)
            ),
            name="ck_snapshot_meta_line_counts",
        ),
    )
    op.create_table(
        "snapshot_file",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.String(length=32),
            sa.ForeignKey("snapshot.id"),
            nullable=False,
        ),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("tracked", sa.Boolean, nullable=False),
        sa.Column("change_type", sa.String, nullable=False),
        sa.Column("error", sa.String, nullable=True),
        sa.UniqueConstraint("path", name="uq_snapshot_file_path"),
        sa.CheckConstraint(
            (sa.func.length(sa.column("id")) == 32)
            & sa.column("id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_snapshot_file_id",
        ),
        sa.CheckConstraint(
            sa.func.substr(sa.column("path"), 1, 1) == "/",
            name="ck_snapshot_file_path",
        ),
        sa.CheckConstraint(
            sa.column("change_type").in_(
                ("modify", "add", "delete", "rename", "copy")
            ),
            name="ck_snapshot_file_change_type",
        ),
        sa.CheckConstraint(
            sa.column("error").is_(None)
            | (sa.func.length(sa.column("error")) > 0),
            name="ck_snapshot_file_error",
        ),
    )
    op.create_index(
        "ix_snapshot_file_snapshot_id",
        "snapshot_file",
        ["snapshot_id"],
    )
    op.create_table(
        "snapshot_file_left",
        sa.Column(
            "file_id",
            sa.String(length=32),
            sa.ForeignKey("snapshot_file.id"),
            primary_key=True,
        ),
        sa.Column("repository_path", sa.String, nullable=False),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.CheckConstraint(
            (sa.func.length(sa.column("repository_path")) > 0)
            & (sa.func.substr(sa.column("repository_path"), 1, 1) != "/")
            & (sa.column("repository_path") != ".")
            & (sa.column("repository_path") != "..")
            & sa.column("repository_path").not_like("../%")
            & sa.column("repository_path").not_like("%/../%")
            & sa.column("repository_path").not_like("%/.."),
            name="ck_snapshot_file_left_repository_path",
        ),
        sa.CheckConstraint(
            sa.func.length(sa.column("content_hash")) == 32,
            name="ck_snapshot_file_left_content_hash",
        ),
    )
    op.create_table(
        "snapshot_file_lazy_reason",
        sa.Column(
            "file_id",
            sa.String(length=32),
            sa.ForeignKey("snapshot_file.id"),
            primary_key=True,
        ),
        sa.Column("reason", sa.String, nullable=False),
        sa.CheckConstraint(
            sa.column("reason").in_(
                ("too_big", "generated", "deleted", "untracked", "pure_renamed")
            ),
            name="ck_snapshot_file_lazy_reason",
        ),
    )
    op.create_table(
        "snapshot_file_lazy_reason_content",
        sa.Column(
            "file_id",
            sa.String(length=32),
            sa.ForeignKey("snapshot_file_lazy_reason.file_id"),
            primary_key=True,
        ),
        sa.Column("content", sa.String, nullable=False),
        sa.CheckConstraint(
            sa.func.length(sa.column("content")) > 0,
            name="ck_snapshot_file_lazy_reason_content",
        ),
    )
    op.create_table(
        "snapshot_file_right",
        sa.Column(
            "file_id",
            sa.String(length=32),
            sa.ForeignKey("snapshot_file.id"),
            primary_key=True,
        ),
        sa.Column("repository_path", sa.String, nullable=False),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.CheckConstraint(
            (sa.func.length(sa.column("repository_path")) > 0)
            & (sa.func.substr(sa.column("repository_path"), 1, 1) != "/")
            & (sa.column("repository_path") != ".")
            & (sa.column("repository_path") != "..")
            & sa.column("repository_path").not_like("../%")
            & sa.column("repository_path").not_like("%/../%")
            & sa.column("repository_path").not_like("%/.."),
            name="ck_snapshot_file_right_repository_path",
        ),
        sa.CheckConstraint(
            sa.func.length(sa.column("content_hash")) == 32,
            name="ck_snapshot_file_right_content_hash",
        ),
    )


def downgrade() -> None:
    """Remove retained Room state and restore the previous Mark schema.

    Downgrading discards captured-state relations and the Mark activity flag;
    directories outside SQLite are deliberately outside Alembic's authority.
    """
    op.drop_table("snapshot_file_right")
    op.drop_table("snapshot_file_lazy_reason_content")
    op.drop_table("snapshot_file_lazy_reason")
    op.drop_table("snapshot_file_left")
    op.drop_table("snapshot_file")
    op.drop_table("snapshot_meta")
    op.drop_table("snapshot")
    op.drop_index("uq_room_preset_tab_backend_key", table_name="room")
    op.drop_index("uq_room_mark_tab_backend_key", table_name="room")
    op.drop_table("room")
    op.drop_column("repo_mark", "active")
