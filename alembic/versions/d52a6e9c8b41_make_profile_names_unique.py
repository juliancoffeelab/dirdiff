"""Merge duplicate Profiles and require unique usernames.

Revision ID: d52a6e9c8b41
Revises: c8154d91a7e2
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

revision: str = "d52a6e9c8b41"
down_revision: str | Sequence[str] | None = "c8154d91a7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge same-name identities without losing attribution or preferences."""
    connection = op.get_bind()
    user_profile = sa.table(
        "user_profile", sa.column("id"), sa.column("username")
    )
    agent_profile = sa.table("agent_profile", sa.column("profile_id"))
    review_action = sa.table("review_action", sa.column("profile_id"))
    user_preferences = sa.table(
        "user_preferences",
        sa.column("user_profile_id"),
        sa.column("aggressive_folds"),
    )
    duplicate_names = (
        connection.execute(
            sa.select(user_profile.c.username)
            .group_by(user_profile.c.username)
            .having(sa.func.count() > 1)
            .order_by(user_profile.c.username)
        )
        .scalars()
        .all()
    )
    for username in duplicate_names:
        agent_bound_expression = sa.exists(
            sa.select(sa.literal(1)).where(
                agent_profile.c.profile_id == user_profile.c.id
            )
        ).label("agent_bound")
        action_count_expression = (
            sa.select(sa.func.count())
            .select_from(review_action)
            .where(review_action.c.profile_id == user_profile.c.id)
            .scalar_subquery()
            .label("action_count")
        )
        preferences_expression = sa.exists(
            sa.select(sa.literal(1)).where(
                user_preferences.c.user_profile_id == user_profile.c.id
            )
        ).label("has_preferences")
        profiles = connection.execute(
            sa.select(
                user_profile.c.id,
                agent_bound_expression,
                action_count_expression,
                preferences_expression,
            )
            .where(user_profile.c.username == username)
            .order_by(
                agent_bound_expression.desc(),
                action_count_expression.desc(),
                preferences_expression.desc(),
                user_profile.c.id.desc(),
            )
        ).all()
        agent_bound = [
            profile.id for profile in profiles if profile.agent_bound
        ]
        if len(agent_bound) > 1:
            raise RuntimeError(
                f"Duplicate Profile name has multiple agent bindings: {username}"
            )
        canonical_id = profiles[0].id
        duplicate_ids = [profile.id for profile in profiles[1:]]
        ids = [canonical_id, *duplicate_ids]
        preference_values = (
            connection.execute(
                sa.select(user_preferences.c.aggressive_folds)
                .distinct()
                .where(user_preferences.c.user_profile_id.in_(ids))
            )
            .scalars()
            .all()
        )
        if len(preference_values) > 1:
            raise RuntimeError(
                f"Duplicate Profile preferences disagree: {username}"
            )
        if preference_values:
            connection.execute(
                sa.delete(user_preferences).where(
                    user_preferences.c.user_profile_id.in_(ids)
                )
            )
            connection.execute(
                sa.insert(user_preferences).values(
                    user_profile_id=canonical_id,
                    aggressive_folds=preference_values[0],
                )
            )
        for duplicate_id in duplicate_ids:
            connection.execute(
                sa.update(review_action)
                .where(review_action.c.profile_id == duplicate_id)
                .values(profile_id=canonical_id)
            )
            connection.execute(
                sa.update(agent_profile)
                .where(agent_profile.c.profile_id == duplicate_id)
                .values(profile_id=canonical_id)
            )
            connection.execute(
                sa.delete(user_profile).where(user_profile.c.id == duplicate_id)
            )

    with op.batch_alter_table("user_profile", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            "uq_user_profile_username", ["username"]
        )


def downgrade() -> None:
    """Permit duplicate usernames without recreating merged identities."""
    with op.batch_alter_table("user_profile", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_user_profile_username", type_="unique")
