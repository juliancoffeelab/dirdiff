"""Persistence for local dirdiff users.

`UserProfileStore` is used by FastAPI profile routes in `dirdiff.server` to
create, fetch, and rename ordinary Profiles. Agent registration adds only a
UUID binding to the same Profile shape. The exported
`UserProfileRecord` is the read model returned to that route layer. The shared
internal `UserProfile` table lets Room persistence retain Profile-authored
review actions without duplicating Profile identity.

This module owns username validation and profile rows only.  It does not manage
UI preferences or repository marks; those belong to `PreferencesStore` and
`RepoMarkStore`.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Engine,
    ForeignKey,
    String,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import (
    TableBase,
    UserProfile,
    UserProfileRecord,
    profile_record,
)

__all__ = [
    "UserProfileRecord",
    "UserProfileStore",
]


class AgentProfile(TableBase):
    """Bind one disposable Profile to the UUID supplied by an agent.

    The Profile row contains the display name and authored-action identity.
    This relation contains only the caller's unique registration identifier;
    it does not create another author shape or classify Profiles.
    """

    __tablename__ = "agent_profile"
    __table_args__ = (
        UniqueConstraint("agent_uuid", name="uq_agent_profile_uuid"),
        CheckConstraint(
            "length(agent_uuid) = 32 AND agent_uuid NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_profile_uuid",
        ),
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"), primary_key=True
    )
    agent_uuid: Mapped[str] = mapped_column(String(32), nullable=False)


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

    def create(
        self,
        username: str,
    ) -> UserProfileRecord:
        """
        Create one persisted Profile.
        """

        _validate_username(username)
        try:
            with Session(self.engine) as session, session.begin():
                row = session.execute(
                    insert(UserProfile)
                    .values(username=username)
                    .returning(
                        UserProfile.id,
                        UserProfile.username,
                    )
                ).one()
                return profile_record(row.id, row.username)
        except IntegrityError as exc:
            raise ValueError("Username already exists.") from exc

    def get_by_username(self, username: str) -> UserProfileRecord | None:
        """Return the one persisted Profile with an exact username."""
        _validate_username(username)
        with Session(self.engine) as session:
            row = session.execute(
                select(UserProfile.id, UserProfile.username).where(
                    UserProfile.username == username
                )
            ).one_or_none()
            if row is None:
                return None
            return profile_record(row.id, row.username)

    def agent_exists(self, agent_uuid: str) -> bool:
        """Return whether one exact agent registration UUID already exists."""
        with Session(self.engine) as session:
            return (
                session.execute(
                    select(AgentProfile.profile_id).where(
                        AgentProfile.agent_uuid == agent_uuid
                    )
                ).scalar_one_or_none()
                is not None
            )

    def create_agent(
        self,
        username: str,
        agent_uuid: str,
    ) -> UserProfileRecord:
        """Atomically create a disposable Profile and its agent UUID binding.

        The UUID must be 32 lowercase hexadecimal characters and must not have
        been registered before. A uniqueness race rolls back both inserts and
        is reported as invalid registration input.
        """
        _validate_username(username)
        if len(agent_uuid) != 32 or bool(
            set(agent_uuid) - set("0123456789abcdef")
        ):
            raise ValueError("Invalid agent UUID.")
        try:
            with Session(self.engine) as session, session.begin():
                row = session.execute(
                    insert(UserProfile)
                    .values(username=username)
                    .returning(UserProfile.id, UserProfile.username)
                ).one()
                session.execute(
                    insert(AgentProfile).values(
                        profile_id=row.id,
                        agent_uuid=agent_uuid,
                    )
                )
                return profile_record(row.id, row.username)
        except IntegrityError as exc:
            raise ValueError("Agent UUID or username already exists.") from exc

    def get(self, profile_id: int) -> UserProfileRecord | None:
        """
        Return one persisted user profile row by id.
        """

        with Session(self.engine) as session:
            row = session.execute(
                select(
                    UserProfile.id,
                    UserProfile.username,
                ).where(UserProfile.id == profile_id)
            ).one_or_none()
            if row is None:
                return None
            return profile_record(row.id, row.username)

    def update_username(
        self, profile_id: int, username: str
    ) -> UserProfileRecord | None:
        """
        Update the username for one persisted user profile row.
        """

        _validate_username(username)
        try:
            with Session(self.engine) as session, session.begin():
                row = session.execute(
                    update(UserProfile)
                    .where(UserProfile.id == profile_id)
                    .values(username=username)
                    .returning(
                        UserProfile.id,
                        UserProfile.username,
                    )
                ).one_or_none()
                if row is None:
                    return None
                return profile_record(row.id, row.username)
        except IntegrityError as exc:
            raise ValueError("Username already exists.") from exc
