from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool


class TableBase(DeclarativeBase):
    """
    Shared SQLAlchemy declarative base for dirdiff tables.
    """

    pass


def bootstrap_tables(engine: Engine) -> None:
    """
    Ensure all declared tables exist on the provided engine.
    """

    TableBase.metadata.create_all(engine)


def open_sqlite_engine(db_path: Path) -> Engine:
    """
    Open a persistent SQLite engine and create all known tables.
    """

    expanded_path = db_path.expanduser()
    expanded_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{expanded_path}")
    bootstrap_tables(engine)
    return engine


def open_ephemeral_engine() -> Engine:
    """
    Open an in-memory SQLite engine and create all known tables.
    """

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    bootstrap_tables(engine)
    return engine
