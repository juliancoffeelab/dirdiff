from __future__ import annotations

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dirdiff.db.base import (
    AgentProfile,
    UserProfile,
    UserProfileRecord,
    profile_record,
)

__all__ = [
    "UserProfileStore",
]


def _validate_username(username: str) -> None:
    if username == "":
        raise ValueError("Username cannot be empty.")
    if username != username.strip():
        raise ValueError("Username must not start or end with whitespace.")
    if username.strip() == "":
        raise ValueError("Username cannot be blank.")


class UserProfileStore:
    def __init__(self, engine: Engine) -> None:

        self.engine: Engine = engine

    def create(
        self,
        username: str,
    ) -> UserProfileRecord:

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

    def upsert_agent(
        self,
        username: str,
        agent_uuid: str,
        model: str,
        harness: str,
    ) -> UserProfileRecord:
        """Idempotent on `agent_uuid`: a repeat call renames the Profile and
        refreshes model and harness.
        """
        _validate_username(username)
        # Lowercase hex UUID without separators.
        if len(agent_uuid) != 32 or bool(
            set(agent_uuid) - set("0123456789abcdef")
        ):
            raise ValueError("Invalid agent UUID.")
        if model.strip() == "" or harness.strip() == "":
            raise ValueError("Model and harness cannot be blank.")
        try:
            with Session(self.engine) as session, session.begin():
                existing = session.execute(
                    select(AgentProfile.profile_id).where(
                        AgentProfile.agent_uuid == agent_uuid
                    )
                ).scalar_one_or_none()
                if existing is None:
                    row = session.execute(
                        insert(UserProfile)
                        .values(username=username)
                        .returning(UserProfile.id, UserProfile.username)
                    ).one()
                    session.execute(
                        insert(AgentProfile).values(
                            profile_id=row.id,
                            agent_uuid=agent_uuid,
                            model=model,
                            harness=harness,
                        )
                    )
                    return profile_record(row.id, row.username)
                row = session.execute(
                    update(UserProfile)
                    .where(UserProfile.id == existing)
                    .values(username=username)
                    .returning(UserProfile.id, UserProfile.username)
                ).one()
                session.execute(
                    update(AgentProfile)
                    .where(AgentProfile.profile_id == existing)
                    .values(model=model, harness=harness)
                )
                return profile_record(row.id, row.username)
        except IntegrityError as exc:
            raise ValueError("Username already exists.") from exc

    def get(self, profile_id: int) -> UserProfileRecord | None:

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
