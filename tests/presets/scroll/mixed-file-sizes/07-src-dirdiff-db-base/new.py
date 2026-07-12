"""SQLAlchemy engine and table-base boundary for dirdiff persistence.

Database modules define their tables against `TableBase` and callers obtain
ready-to-use engines through this module.  It owns table bootstrapping for both
persistent user databases and in-memory test databases.  It does not expose
query helpers or application records; those live in the feature-specific store
modules under `dirdiff.db`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from alembic import command

__all__ = [
    "TableBase",
    "open_ephemeral_engine",
    "open_sqlite_engine",
]


class TableBase(DeclarativeBase):
    """
    Shared SQLAlchemy declarative base for dirdiff tables.
    """

    pass


def bootstrap_tables(engine: Engine, *, migrate: Optional[Path] = None) -> None:
    """
    Ensure all declared tables exist on the provided engine.

    Migrate takes a path to db you want to run migrations on.
    """

    db_path = migrate
    if db_path is not None:
        project_root = Path(__file__).parents[3]
        config = Config(project_root / "alembic.ini")
        config.attributes["db_path"] = db_path
        command.upgrade(config, "head")
    else:
        TableBase.metadata.create_all(engine)


def open_sqlite_engine(db_path: Path) -> Engine:
    """
    Open a persistent SQLite engine and create all known tables.
    """

    expanded_path = db_path.expanduser()
    expanded_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{expanded_path}")
    bootstrap_tables(engine, migrate=expanded_path)
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
