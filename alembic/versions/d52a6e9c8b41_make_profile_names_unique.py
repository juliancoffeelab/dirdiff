"""Merge duplicate Profiles and require unique usernames.

Revision ID: d52a6e9c8b41
Revises: c8154d91a7e2
Create Date: 2026-08-13 00:00:00.000000
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

revision: str = "d52a6e9c8b41"
down_revision: str | Sequence[str] | None = "c8154d91a7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge same-name identities without losing attribution or preferences."""
    connection = op.get_bind()
    duplicate_names = (
        connection.exec_driver_sql(
            "SELECT username FROM user_profile "
            "GROUP BY username HAVING COUNT(*) > 1 ORDER BY username"
        )
        .scalars()
        .all()
    )
    for username in duplicate_names:
        profiles = connection.exec_driver_sql(
            "SELECT up.id, "
            "EXISTS(SELECT 1 FROM agent_profile AS ap "
            "WHERE ap.profile_id = up.id) AS agent_bound, "
            "(SELECT COUNT(*) FROM review_action AS ra "
            "WHERE ra.profile_id = up.id) AS action_count, "
            "EXISTS(SELECT 1 FROM user_preferences AS pref "
            "WHERE pref.user_profile_id = up.id) AS has_preferences "
            "FROM user_profile AS up WHERE up.username = ? "
            "ORDER BY agent_bound DESC, action_count DESC, "
            "has_preferences DESC, up.id DESC",
            (username,),
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
        placeholders = ", ".join("?" for _ in ids)
        preference_values = (
            connection.exec_driver_sql(
                "SELECT DISTINCT aggressive_folds FROM user_preferences "
                f"WHERE user_profile_id IN ({placeholders})",
                tuple(ids),
            )
            .scalars()
            .all()
        )
        if len(preference_values) > 1:
            raise RuntimeError(
                f"Duplicate Profile preferences disagree: {username}"
            )
        if preference_values:
            connection.exec_driver_sql(
                "DELETE FROM user_preferences "
                f"WHERE user_profile_id IN ({placeholders})",
                tuple(ids),
            )
            connection.exec_driver_sql(
                "INSERT INTO user_preferences "
                "(user_profile_id, aggressive_folds) VALUES (?, ?)",
                (canonical_id, preference_values[0]),
            )
        for duplicate_id in duplicate_ids:
            connection.exec_driver_sql(
                "UPDATE review_action SET profile_id = ? WHERE profile_id = ?",
                (canonical_id, duplicate_id),
            )
            connection.exec_driver_sql(
                "UPDATE agent_profile SET profile_id = ? WHERE profile_id = ?",
                (canonical_id, duplicate_id),
            )
            connection.exec_driver_sql(
                "DELETE FROM user_profile WHERE id = ?", (duplicate_id,)
            )

    with op.batch_alter_table("user_profile", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            "uq_user_profile_username", ["username"]
        )


def downgrade() -> None:
    """Permit duplicate usernames without recreating merged identities."""
    with op.batch_alter_table("user_profile", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_user_profile_username", type_="unique")
