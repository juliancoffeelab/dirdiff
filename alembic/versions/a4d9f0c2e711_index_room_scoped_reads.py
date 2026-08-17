"""Index the Room-scoped activity and origin reads.

Revision ID: a4d9f0c2e711
Revises: f63b8a1d2e40
Create Date: 2026-08-16 00:00:00.000000

Review activity ids are one global sequence, so every Room-scoped read
(latest-activity boundary, continuation pages, attention counts) filtered the
whole action table through Snapshot and Room joins without index support and
scanned activity belonging to other Rooms. `snapshot.room_id` and
`review_action(snapshot_id, activity_id)` make those joins index-driven.
"""

from collections.abc import Sequence

from alembic import op

__all__ = [
    "branch_labels",
    "depends_on",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]

revision: str = "a4d9f0c2e711"
down_revision: str | Sequence[str] | None = "f63b8a1d2e40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the two Room-scoped read indexes."""
    op.create_index("ix_snapshot_room", "snapshot", ["room_id"])
    op.create_index(
        "ix_review_action_snapshot_activity",
        "review_action",
        ["snapshot_id", "activity_id"],
    )


def downgrade() -> None:
    """Drop the two Room-scoped read indexes."""
    op.drop_index(
        "ix_review_action_snapshot_activity", table_name="review_action"
    )
    op.drop_index("ix_snapshot_room", table_name="snapshot")
