"""Record the model and harness each agent Profile reported.

Both columns are nullable. Agent Profiles registered before this revision keep
NULL because their model and harness were never recorded; no existing row is
rewritten or deleted.
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

revision: str = "7c41e9a05d2b"
down_revision: str | Sequence[str] | None = "30badddc272b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable model and harness columns to agent Profiles."""
    with op.batch_alter_table("agent_profile") as batch:
        batch.add_column(sa.Column("model", sa.String(), nullable=True))
        batch.add_column(sa.Column("harness", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the model and harness columns, keeping Profiles and UUIDs."""
    with op.batch_alter_table("agent_profile") as batch:
        batch.drop_column("model")
        batch.drop_column("harness")
