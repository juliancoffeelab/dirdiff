"""SQLAlchemy engine and table-base boundary for dirdiff persistence.

Database modules define their tables against `TableBase` and callers obtain
ready-to-use engines through this module. It also defines the User Profile
table and record shared by profile and Room persistence. It owns table
bootstrapping for both persistent user databases and in-memory test databases.
It does not expose query helpers; those live in the feature-specific store
modules under `dirdiff.db`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, String, UniqueConstraint, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

from alembic import command

__all__ = [
    "TableBase",
    "UserProfile",
    "UserProfileRecord",
    "open_ephemeral_engine",
    "open_sqlite_engine",
    "profile_record",
]


class TableBase(DeclarativeBase):
    """
    Shared SQLAlchemy declarative base for dirdiff tables.
    """

    pass


class UserProfile(TableBase):
    """Persist one durable identity used by review data.

    The generated positive id is the stable relational identity. The username
    is globally unique current display data and may change without changing
    authored actions. Agent registration refers to this same identity through
    its separate one-to-one relation.
    """

    __tablename__ = "user_profile"
    __table_args__ = (
        UniqueConstraint("username", name="uq_user_profile_username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, nullable=False)


@dataclass(frozen=True)
class UserProfileRecord:
    """Expose one durable review Profile without exposing its table row."""

    id: int
    """Stable positive database id used by Profile-authored review actions."""

    username: str
    """Current display name shown by Profile controls or Comments."""


def profile_record(
    profile_id: int,
    username: str,
) -> UserProfileRecord:
    """Validate persisted Profile fields before exposing the shared record."""
    assert profile_id > 0, "persisted Profile id must be positive"
    assert username != "", "persisted Profile name must not be empty"
    return UserProfileRecord(
        id=profile_id,
        username=username,
    )


def enable_sqlite_foreign_keys(
    dbapi_connection: DBAPIConnection,
    connection_record: ConnectionPoolEntry,
) -> None:
    """Configure every opened SQLite connection for dirdiff's access pattern.

    SQLAlchemy calls this for persistent and in-memory dirdiff engines before
    the connection is used. Foreign keys are otherwise parsed but unenforced,
    which would allow orphan persistence rows. WAL journaling lets Snapshot
    publication write without excluding concurrent browser and agent reads
    (an in-memory database ignores the request and stays in memory mode), and
    NORMAL synchronous is the documented safe pairing with WAL. The enlarged
    page cache and mmap window fit the whole database of a busy Room so
    repeated review scans read hot pages without I/O.
    """
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-16000")
    cursor.execute("PRAGMA mmap_size=134217728")
    cursor.close()


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
        # A database already at head skips the full Alembic environment run,
        # which otherwise costs startup latency on every launch. A fresh or
        # behind database still upgrades exactly as before.
        with engine.connect() as connection:
            current = MigrationContext.configure(
                connection
            ).get_current_revision()
        if current != ScriptDirectory.from_config(config).get_current_head():
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
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
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
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    bootstrap_tables(engine)
    return engine
