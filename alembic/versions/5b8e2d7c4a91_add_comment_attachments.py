"""Store image attachments that review Comments reference.

A Comment body refers to an attachment by its SHA-256 digest, so the
relation between Comment and attachment lives in the body text and no review
table changes. `attachment` holds the facts of the bytes themselves;
`room_attachment` makes an attachment readable in one Room and records its
first upload there. Nothing exists to backfill.
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

revision: str = "5b8e2d7c4a91"
down_revision: str | Sequence[str] | None = "9e3f6c2a7d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the attachment relations."""
    op.create_table(
        "attachment",
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(content_hash) = 32", name="ck_attachment_content_hash"
        ),
        sa.CheckConstraint(
            "length(media_type) > 0", name="ck_attachment_media_type"
        ),
        sa.CheckConstraint("size > 0", name="ck_attachment_size"),
        sa.PrimaryKeyConstraint("content_hash"),
    )
    op.create_table(
        "room_attachment",
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(created_at) > 0", name="ck_room_attachment_created_at"
        ),
        sa.ForeignKeyConstraint(["room_id"], ["room.id"]),
        sa.ForeignKeyConstraint(["content_hash"], ["attachment.content_hash"]),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profile.id"]),
        sa.PrimaryKeyConstraint("room_id", "content_hash"),
    )


def downgrade() -> None:
    """Drop the attachment relations."""
    op.drop_table("room_attachment")
    op.drop_table("attachment")
