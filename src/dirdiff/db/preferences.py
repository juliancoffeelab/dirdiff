"""Persistence for per-user dirdiff UI preferences.

`PreferencesStore` reads and writes the preferences used by FastAPI preference
routes in `dirdiff.server`.  The exported `PreferencesRecord` is the read model
returned to that route layer.  The private `UserPreferences` SQLAlchemy table
stores the actual row keyed by `user_profile_id`.

This module owns the persisted shape of preference rows only.  It does not
decide which user profile is active, does not manage repository marks, and does
not know how preferences are rendered in the frontend.
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
    """
    Persisted UI preferences for one user profile.
    """

    __tablename__ = "user_preferences"

    user_profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"),
        primary_key=True,
    )
    aggressive_folds: Mapped[bool] = mapped_column(Boolean, nullable=False)


@dataclass(frozen=True)
class PreferencesRecord:
    """Persisted preference values for one `UserProfileRecord` id."""

    user_profile_id: int
    """Primary key of the user profile these preferences belong to."""

    aggressive_folds: bool
    """Whether the UI should fold unchanged regions aggressively by default."""


class PreferencesStore:
    def __init__(self, engine: Engine) -> None:
        self.engine: Engine = engine

    def get(self, user_profile_id: int) -> PreferencesRecord | None:
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
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                sqlite_insert(UserPreferences)
                .values(
                    user_profile_id=user_profile_id,
                    aggressive_folds=True,
                )
                .on_conflict_do_update(
                    index_elements=[UserPreferences.user_profile_id],
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
