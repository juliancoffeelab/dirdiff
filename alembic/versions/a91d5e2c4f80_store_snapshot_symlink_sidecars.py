"""Store authoritative physical sidecars for symbolic-link Snapshot sides.

Stage 4 originally inferred link kind by probing for a sibling
`<side>-link.json` file. That made an unauthenticated filename convention more
authoritative than the Snapshot database and left target bytes outside the
stored integrity boundary.

This revision adds one relational row per captured link side. It records the
exact metadata and optional target-capture paths plus SHA-256 digests for both.
Row presence now says that a side is a link; readers do not probe filenames.

No backfill is needed: symbolic-link sidecars and this relation enter the
published Snapshot contract together. Older Snapshots contain no relational
link fact and therefore retain their pre-Stage-4 interpretation.
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

revision: str = "a91d5e2c4f80"
down_revision: str | Sequence[str] | None = "e2a71c6b5d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the one-row-per-link-side authoritative capture relation."""
    op.create_table(
        "snapshot_file_symlink",
        sa.Column("file_id", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("metadata_path", sa.String(), nullable=False),
        sa.Column("metadata_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("target_capture_path", sa.String(), nullable=True),
        sa.Column("target_hash", sa.LargeBinary(length=32), nullable=True),
        sa.CheckConstraint(
            "substr(metadata_path, 1, 1) = '/'",
            name="ck_snapshot_file_symlink_metadata_path",
        ),
        sa.CheckConstraint(
            "length(metadata_hash) = 32",
            name="ck_snapshot_file_symlink_metadata_hash",
        ),
        sa.CheckConstraint(
            "(target_capture_path IS NULL) = (target_hash IS NULL)",
            name="ck_snapshot_file_symlink_target_presence",
        ),
        sa.CheckConstraint(
            "target_capture_path IS NULL OR "
            "substr(target_capture_path, 1, 1) = '/'",
            name="ck_snapshot_file_symlink_target_capture_path",
        ),
        sa.CheckConstraint(
            "target_hash IS NULL OR length(target_hash) = 32",
            name="ck_snapshot_file_symlink_target_hash",
        ),
        sa.ForeignKeyConstraint(["file_id"], ["snapshot_file.id"]),
        sa.PrimaryKeyConstraint("file_id", "side"),
        sa.UniqueConstraint(
            "metadata_path",
            name="uq_snapshot_file_symlink_metadata_path",
        ),
        sa.UniqueConstraint(
            "target_capture_path",
            name="uq_snapshot_file_symlink_target_capture_path",
        ),
    )


def downgrade() -> None:
    """Drop symbolic-link sidecar authority and all stored descriptors."""
    op.drop_table("snapshot_file_symlink")
