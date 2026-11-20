"""Persistence for local dirdiff Profiles.

## Classes

`UserProfileStore` creates, finds, and renames ordinary Profiles. It can also
upsert a Profile bound to an agent UUID and its reported model and harness.
`UserProfileRecord` carries the stable database identity and current username
used to attribute review actions.

## Purpose and boundaries

All human and agent authors use the same Profile identity. Usernames are exact,
globally unique display names, while an agent UUID is a separate one-to-one
registration that makes agent registration idempotent.

This module validates and persists identity. It does not authenticate users,
select an active Profile, store HUD preferences, or decide whether an author may
perform a review operation.
"""

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
    """Reject a username that cannot be stored as an exact display identity.

    Usernames must contain a non-whitespace character and may not carry
    surrounding whitespace. Uniqueness is checked by the database write.

    # Failures

    - Raises `ValueError` for empty, blank, or whitespace-padded input.
    """
    if username == "":
        raise ValueError("Username cannot be empty.")
    if username != username.strip():
        raise ValueError("Username must not start or end with whitespace.")
    if username.strip() == "":
        raise ValueError("Username cannot be blank.")


class UserProfileStore:
    """Create and select durable Profiles and their agent registrations.

    # Usage
    Construct one store from the application engine. Use `create`, lookup, and
    rename operations for human Profiles. Use `upsert_agent` for an
    agent UUID; it returns the same `UserProfileRecord` shape.

    Usernames are non-empty, have no surrounding whitespace, and are globally
    unique.

    # Boundaries
    The store persists identity and agent registration only. It does not select
    an active Profile, assign roles, manage preferences, or create a separate
    kind of author for agents.
    """

    def __init__(self, engine: Engine) -> None:
        """Bind Profile operations to one concrete database engine.

        Construction retains the engine but opens no session and performs no
        validation. Each later operation owns its short-lived transaction or
        read session, so the store carries no selected Profile state.

        # Parameters

        - `engine`: Bootstrapped engine containing the shared Profile relations.
        """

        self.engine: Engine = engine

    def create(
        self,
        username: str,
    ) -> UserProfileRecord:
        """Create one durable ordinary Profile with an exact display name.

        The name must be non-empty, nonblank, have no surrounding whitespace,
        and be globally unique. Success commits the new row and returns its stable
        positive id; duplicate or invalid input raises `ValueError` without a row.

        # Parameters

        - `username`: Complete display name to validate and persist unchanged.

        # Usage

        Pass the exact name accepted from the Profile creation boundary. Store
        the returned id for review attribution; do not use the mutable username
        as identity.

        # Failures

        - Raises `ValueError` when the name is empty, blank, padded with
          whitespace, or already used by another Profile.
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
        """Return the one persisted Profile with an exact username.

        Matching is exact after ordinary username validation. Absence returns
        `None`; this lookup neither creates a Profile nor chooses it as active.

        # Usage

        Use this when the caller explicitly selected a username. Treat `None` as
        absence; Profile selection must never create an identity implicitly.

        # Returns

        - The Profile whose current username exactly matches the input.
        - `None`: No such identity exists. The caller must handle absence
          without creating or selecting a Profile as a side effect.

        # Failures

        - Raises `ValueError` when the lookup value is empty, blank, or padded
          with whitespace.
        """
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
        """Return the Profile bound to an agent UUID, creating it on first use.

        The call is idempotent on `agent_uuid`. The first call atomically
        creates the ordinary Profile and its agent binding. A repeat call with
        the same UUID returns that same Profile and replaces its username,
        `model`, and `harness` with the supplied values, so an agent that
        restarts under a new model or name keeps its identity and its authored
        history.

        # Parameters

        - `username`: Valid globally unique display name.
        - `agent_uuid`: Lowercase hexadecimal UUID without separators, exactly
          32 characters.
        - `model`: Non-empty model name the agent reports.
        - `harness`: Non-empty name of the program driving the agent.

        # Failures

        - Raises `ValueError` when the username, UUID, model, or harness is
          invalid, or when the username already belongs to a different Profile.
          Nothing is written on failure.
        """
        _validate_username(username)
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
        """Read one durable Profile by stable database identity.

        The method returns current display data for the exact id, or `None` when
        no row exists. It does not create, select, or validate an active Profile.

        # Parameters

        - `profile_id`: Database identity to match exactly.

        # Usage

        Review and preferences boundaries use this to prove that a caller's
        Profile id still names an author before performing another operation.

        # Returns

        - The current Profile record for `profile_id`.
        - `None`: The durable identity does not exist. A caller requiring an
          author must reject the operation before performing it.

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
        """Rename one Profile while preserving its durable identity.

        # Parameters

        - `profile_id`: Profile to rename.
        - `username`: Valid globally unique replacement display name.

        # Usage

        Pass the stable Profile id selected earlier and return the updated record
        to the caller. Existing review actions require no rewrite because they
        refer to the unchanged id.

        # Returns

        - The updated record with the same stable id and replacement username.
        - `None`: `profile_id` does not exist and no row was changed. The caller
          must not treat the requested username as persisted.

        # Failures

        - Returns `None` when `profile_id` does not exist.
        - Raises `ValueError` when the replacement is empty, blank, padded with
          whitespace, or already used by another Profile.
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
