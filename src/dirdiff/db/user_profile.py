"""Persistence for local dirdiff users.

`UserProfileStore` is used by FastAPI profile routes in `dirdiff.server` to
create, fetch, list, and rename local dirdiff users.  The exported
`UserProfileRecord` is the read model returned to that route layer, and the
private `UserProfile` SQLAlchemy table stores the generated id plus username.

This module owns username validation and profile rows only.  It does not manage
UI preferences or repository marks; those belong to `PreferencesStore` and
`RepoMarkStore`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, String, insert, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import TableBase

__all__ = [
    "UserProfileRecord",
    "UserProfileStore",
]


class UserProfile(TableBase):
    """
    Persistent user profile table.

    The first version stores only the local username.
    """

    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, nullable=False)


@dataclass(frozen=True)
class UserProfileRecord:
    """Persisted local dirdiff user."""

    id: int
    """Stable database id used as the profile key in related stores."""

    username: str
    """Human-readable local name shown by profile controls."""


def _validate_username(username: str) -> None:
    if username == "":
        raise ValueError("Username cannot be empty.")
    if username != username.strip():
        raise ValueError("Username must not start or end with whitespace.")
    if username.strip() == "":
        raise ValueError("Username cannot be blank.")


class UserProfileStore:
    def __init__(self, engine: Engine) -> None:
        """
        Bind the store to a concrete SQLAlchemy engine.
        """

        self.engine: Engine = engine

    def create(self, username: str) -> UserProfileRecord:
        """
        Create a persisted user profile row.
        """

        _validate_username(username)
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                insert(UserProfile)
                .values(username=username)
                .returning(UserProfile.id, UserProfile.username)
            ).one()
            return UserProfileRecord(id=row[0], username=row[1])

    def get(self, profile_id: int) -> UserProfileRecord | None:
        """
        Return one persisted user profile row by id.
        """

        with Session(self.engine) as session:
            row = session.execute(
                select(UserProfile.id, UserProfile.username).where(
                    UserProfile.id == profile_id
                )
            ).one_or_none()
            if row is None:
                return None
            return UserProfileRecord(id=row[0], username=row[1])

    def update_username(
        self, profile_id: int, username: str
    ) -> UserProfileRecord | None:
        """
        Update the username for one persisted user profile row.
        """

        _validate_username(username)
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                update(UserProfile)
                .where(UserProfile.id == profile_id)
                .values(username=username)
                .returning(UserProfile.id, UserProfile.username)
            ).one_or_none()
            if row is None:
                return None
            return UserProfileRecord(id=row[0], username=row[1])
