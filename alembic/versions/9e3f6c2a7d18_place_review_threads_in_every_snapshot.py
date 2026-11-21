"""Place every review-scoped Thread in every Snapshot of its Room.

Before this revision, creating a review-scoped Thread placed it only in its
origin Snapshot, and capture copied it only into later Snapshots. Snapshots
captured before the Thread was created held no placement for it, so reading
or writing the Thread from them failed. This revision inserts the missing
review placements; existing rows are not changed.
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

revision: str = "9e3f6c2a7d18"
down_revision: str | Sequence[str] | None = "7c41e9a05d2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Insert the review placements missing from earlier Snapshots."""
    review_thread = sa.table(
        "review_thread",
        sa.column("thread_id"),
        sa.column("origin_snapshot_id"),
    )
    placement = sa.table(
        "review_thread_placement",
        sa.column("thread_id"),
        sa.column("snapshot_id"),
        sa.column("target_kind"),
    )
    snapshot = sa.table("snapshot", sa.column("id"), sa.column("room_id"))
    origin_snapshot = snapshot.alias("origin_snapshot")
    room_snapshot = snapshot.alias("room_snapshot")
    existing = placement.alias("existing")
    missing = (
        sa.select(
            review_thread.c.thread_id,
            room_snapshot.c.id,
            sa.literal("review"),
        )
        .join(
            placement,
            (placement.c.thread_id == review_thread.c.thread_id)
            & (placement.c.snapshot_id == review_thread.c.origin_snapshot_id),
        )
        .join(
            origin_snapshot,
            origin_snapshot.c.id == review_thread.c.origin_snapshot_id,
        )
        .join(
            room_snapshot,
            room_snapshot.c.room_id == origin_snapshot.c.room_id,
        )
        .where(
            placement.c.target_kind == "review",
            ~sa.exists().where(
                existing.c.thread_id == review_thread.c.thread_id,
                existing.c.snapshot_id == room_snapshot.c.id,
            ),
        )
    )
    op.get_bind().execute(
        sa.insert(placement).from_select(
            ["thread_id", "snapshot_id", "target_kind"], missing
        )
    )


def downgrade() -> None:
    """Keep the inserted placements; Comments may already reference them."""
