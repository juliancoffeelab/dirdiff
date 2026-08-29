"""Persistence for per-user dirdiff UI preferences.

## Classes
`PreferencesStore` reads and writes the preferences of user profiles we have.

The exported `PreferencesRecord` represents preferences of one user.

## Purpose and boundaries

This module owns the persisted shape of preference rows, if any caller
(currently `dirdiff.server`), wants to do something about stored preferences,
that's the module to use.

Note, this module shouldn't interperet anything besides that, that is the
responsibility of the callers, but if the callers need more intricate data-access
patterns, the `PreferencesStore` class should be extended. Nor does it know
about users per se, for example, if they are active or not.

This must not be turned into generic ORM layer, it must provide functionality
callers need and no more, in a most efficient way possible, minimising redundant
SQL round-trips.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Engine, ForeignKey, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import TableBase

__all__ = [
    "PreferencesRecord",
    "PreferencesStore",
]


class UserPreferences(TableBase):
    """Persist the complete preference row for one Profile.

    `PreferencesStore` reads and updates this table by `user_profile_id`. The
    primary key enforces at most one current preference row per Profile.

    This ORM type is private persistence state. Callers use `PreferencesRecord`
    and must not query it directly.
    """

    __tablename__ = "user_preferences"

    user_profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"),
        primary_key=True,
    )
    """Profile whose single complete preference record this row stores.

    It is both the row identity and a foreign key, enforcing at most one record
    for an existing Profile.
    """

    aggressive_folds: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """Whether the HUD initially folds eligible unchanged regions.

    Persistence does not decide which regions are eligible; the frontend applies
    this exact default to its own folding rules.
    """


@dataclass(frozen=True)
class PreferencesRecord:
    """Return the complete persisted preferences of one Profile.

    `PreferencesStore` returns this immutable record after reads and writes;
    the server validates it into the corresponding HTTP response.

    The record does not identify an active Profile or interpret what the HUD
    should render. It only states stored values.
    """

    user_profile_id: int
    """Primary key of the Profile these preferences belong to.

    Servers use it to verify the returned record corresponds to the requested
    Profile; it does not indicate which Profile is currently selected.
    """

    aggressive_folds: bool
    """Whether the UI should fold unchanged regions aggressively by default.

    The value is stored and returned without interpreting current File contents
    or mutating an open Tab.
    """


class PreferencesStore:
    """Read and update the complete persisted preferences of one Profile.

    # Usage
    Construct one store from the application's database engine. Use `get` for a
    non-creating lookup, `get_or_create` when a complete row is required, and
    the named setter to change one persisted value.

    # Boundaries
    Each operation owns its short-lived session and commits its own write. The
    store does not select the active Profile, validate HTTP entities, or
    interpret how a preference changes the HUD.
    """

    def __init__(self, engine: Engine) -> None:
        """Bind preference operations to the application's database engine.

        Construction retains the engine only. Each operation opens and closes
        its own session, so the store itself has no transaction lifetime or
        selected Profile state.
        """
        self.engine: Engine = engine

    def get(self, user_profile_id: int) -> PreferencesRecord | None:
        """Read one Profile's stored preferences without creating them.

        The exact Profile id selects at most one complete row. Absence returns
        `None`, leaving the caller to decide whether defaults should be created;
        this lookup opens no write transaction and changes no persisted value.

        # Parameters

        - `user_profile_id`: Profile identity whose optional row is requested.

        # Usage

        Use this when absence is meaningful to the caller. Routes that require a
        complete preference value should validate the Profile first and call
        `get_or_create` instead.

        # Returns

        - The complete stored preference record when this Profile has a row.
        - `None`: This Profile has no stored preferences. A caller requiring
          defaults must use `get_or_create`, not treat absence as stored data.

        """
        with Session(self.engine) as session:
            row = session.execute(
                select(
                    UserPreferences.user_profile_id,
                    UserPreferences.aggressive_folds,
                ).where(UserPreferences.user_profile_id == user_profile_id)
            ).one_or_none()
            if row is None:
                return None
            return PreferencesRecord(
                user_profile_id=row[0],
                aggressive_folds=row[1],
            )

    def get_or_create(self, user_profile_id: int) -> PreferencesRecord:
        """Return a Profile's preferences, creating them if absent.

        An existing record is returned unchanged. If none exists, the method
        creates one with aggressive folding enabled and returns it.
        Everything happens in one SQL query.

        # Usage

        You would most probably get a `profile_id` from somewhere else, like
        from a frontend that got it previously.

        ```python
        profile = user_profile_store.get(profile_id)
        preferences = preferences_store.get_or_create(profile_id)
        ```

        # Failures

        - `user_profile_id` must identify an existing Profile; otherwise the
        database rejects the write and propagates an exception.
        """
        # TODO: make get_or_create return None for a user that doesn't exist,
        # so that callers dont need to validate it to produce a useful error
        with Session(self.engine) as session, session.begin():
            # NOTE: we use sqlite upcert with conflict mechanic here to
            # do the work in one atomic query
            row = session.execute(
                sqlite_insert(UserPreferences)
                .values(
                    user_profile_id=user_profile_id,
                    aggressive_folds=True,
                )
                .on_conflict_do_update(
                    index_elements=[UserPreferences.user_profile_id],
                    # on conflict, set `aggressive_folds` back to old row
                    # instead of writing down the default `True`
                    set_={
                        "aggressive_folds": UserPreferences.aggressive_folds,
                    },
                )
                .returning(
                    UserPreferences.user_profile_id,
                    UserPreferences.aggressive_folds,
                )
            ).one()
            return PreferencesRecord(
                user_profile_id=row[0],
                aggressive_folds=row[1],
            )

    def set_aggressive_folds(
        self, user_profile_id: int, aggressive_folds: bool
    ) -> PreferencesRecord:
        """Persist and return one Profile's aggressive-fold preference.

        The operation creates the complete preference row when the Profile has
        none, or changes only this value on its existing row.

        # Parameters

        - `user_profile_id`: Existing Profile whose preference is being set.
        - `aggressive_folds`: Exact value later returned to the HUD.

        # Usage

        Validate the Profile before calling, then use the returned record as the
        authoritative stored value for the response.

        # Failures

        - The database rejects a Profile id that does not exist.
        """
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                sqlite_insert(UserPreferences)
                .values(
                    user_profile_id=user_profile_id,
                    aggressive_folds=aggressive_folds,
                )
                .on_conflict_do_update(
                    index_elements=[UserPreferences.user_profile_id],
                    set_={"aggressive_folds": aggressive_folds},
                )
                .returning(
                    UserPreferences.user_profile_id,
                    UserPreferences.aggressive_folds,
                )
            ).one()
            return PreferencesRecord(
                user_profile_id=row[0],
                aggressive_folds=row[1],
            )
