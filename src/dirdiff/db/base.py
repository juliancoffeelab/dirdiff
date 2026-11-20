from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    Engine,
    ForeignKey,
    String,
    UniqueConstraint,
    column,
    create_engine,
    event,
    func,
)
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

from alembic import command

__all__ = [
    "AgentDetails",
    "AgentProfile",
    "TableBase",
    "UserProfile",
    "UserProfileRecord",
    "open_ephemeral_engine",
    "open_sqlite_engine",
    "profile_record",
]


class TableBase(DeclarativeBase):
    pass


class UserProfile(TableBase):
    __tablename__ = "user_profile"
    __table_args__ = (
        UniqueConstraint("username", name="uq_user_profile_username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    username: Mapped[str] = mapped_column(String, nullable=False)


@dataclass(frozen=True)
class UserProfileRecord:
    id: int

    username: str


class AgentProfile(TableBase):
    __tablename__ = "agent_profile"
    __table_args__ = (
        UniqueConstraint("agent_uuid", name="uq_agent_profile_uuid"),
        CheckConstraint(
            (func.length(column("agent_uuid")) == 32)
            & column("agent_uuid").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_agent_profile_uuid",
        ),
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"), primary_key=True
    )

    agent_uuid: Mapped[str] = mapped_column(String(32), nullable=False)

    # NULL for agents registered before model and harness were recorded.
    model: Mapped[str | None] = mapped_column(String, nullable=True)

    harness: Mapped[str | None] = mapped_column(String, nullable=True)


@dataclass(frozen=True)
class AgentDetails:
    model: str | None

    harness: str | None


def profile_record(
    profile_id: int,
    username: str,
) -> UserProfileRecord:
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
    del connection_record
    # SQLite parses but ignores foreign keys unless enabled per connection.
    # WAL lets Snapshot writes proceed alongside browser and agent reads
    # (in-memory databases ignore it).
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-16000")
    cursor.execute("PRAGMA mmap_size=134217728")
    cursor.close()


def bootstrap_tables(
    engine: Engine,
    *,
    migrate: Optional[tuple[Path, Path]] = None,
) -> None:
    if migrate is not None:
        db_path, migration_config_path = migrate
        assert migration_config_path.is_file(), (
            "database migration configuration is missing: "
            f"{migration_config_path}"
        )
        config = Config(migration_config_path)
        config.attributes["db_path"] = db_path
        # A database already at head skips the Alembic environment run, which
        # costs startup latency on every launch.
        with engine.connect() as connection:
            current = MigrationContext.configure(
                connection
            ).get_current_revision()
        if current != ScriptDirectory.from_config(config).get_current_head():
            command.upgrade(config, "head")
    else:
        TableBase.metadata.create_all(engine)


def open_sqlite_engine(db_path: Path, migration_config_path: Path) -> Engine:
    expanded_path = db_path.expanduser()
    expanded_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{expanded_path}")
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    bootstrap_tables(
        engine,
        migrate=(expanded_path, migration_config_path),
    )
    return engine


def open_ephemeral_engine() -> Engine:
    # StaticPool shares one connection across sessions and threads; each new
    # connection would otherwise be a separate empty in-memory database.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    bootstrap_tables(engine)
    return engine
