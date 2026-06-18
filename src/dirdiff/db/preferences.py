from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Engine, insert, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import TableBase


class Preferences(TableBase):
    """
    Persisted global UI preferences.
    """

    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    aggressive_folds: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )


@dataclass(frozen=True)
class PreferencesRecord:
    id: int
    aggressive_folds: bool


class PreferencesStore:
    def __init__(self, engine: Engine) -> None:
        self.engine: Engine = engine

    def get(self) -> PreferencesRecord | None:
        with Session(self.engine) as session:
            row = session.execute(
                select(Preferences.id, Preferences.aggressive_folds)
                .order_by(Preferences.id.asc())
                .limit(1)
            ).one_or_none()
            if row is None:
                return None
            return PreferencesRecord(
                id=row[0],
                aggressive_folds=row[1],
            )

    def get_or_create(self) -> PreferencesRecord:
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                select(Preferences.id, Preferences.aggressive_folds)
                .order_by(Preferences.id.asc())
                .limit(1)
            ).one_or_none()
            if row is not None:
                return PreferencesRecord(
                    id=row[0],
                    aggressive_folds=row[1],
                )
            created = session.execute(
                insert(Preferences)
                .values(aggressive_folds=True)
                .returning(Preferences.id, Preferences.aggressive_folds)
            ).one()
            return PreferencesRecord(
                id=created[0],
                aggressive_folds=created[1],
            )

    def update_aggressive_folds(
        self, preferences_id: int, aggressive_folds: bool
    ) -> PreferencesRecord | None:
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                update(Preferences)
                .where(Preferences.id == preferences_id)
                .values(aggressive_folds=aggressive_folds)
                .returning(Preferences.id, Preferences.aggressive_folds)
            ).one_or_none()
            if row is None:
                return None
            return PreferencesRecord(
                id=row[0],
                aggressive_folds=row[1],
            )
