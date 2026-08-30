"""Construct dirdiff database engines and define shared persistence contracts.

## Functions and types

`open_sqlite_engine` opens a persistent database and brings its schema to the
current declared form. `open_ephemeral_engine` provides the same schema in an
in-memory database. Their internal `bootstrap_tables` operation performs that
schema work on an already-constructed engine.

`TableBase` is the common SQLAlchemy metadata root. The exported
`UserProfileRecord` is the Profile identity shared by Profile and review
persistence.

## Purpose and boundaries

All dirdiff table modules must use this metadata root so foreign keys and schema
creation see one complete model. Engine construction enables SQLite foreign
keys before stores can open sessions.

This module does not provide feature queries. Add those to the store responsible
for the affected data rather than building a generic SQL helper here.
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
    """Register every dirdiff table in one SQLAlchemy metadata collection.

    Database table classes subclass this base. `bootstrap_tables` uses its
    metadata when creating an ephemeral database or a database without an
    Alembic migration path.

    Application code must use feature stores rather than querying `TableBase`
    or its mapped subclasses directly.
    """

    pass


class UserProfile(TableBase):
    """Persist one durable identity used by review data.

    Profile and Room stores share this table so authored review actions point to
    the same identity used by human and agent Profiles.

    Callers do not receive this mutable ORM object. Store operations return
    `UserProfileRecord`, and agent registration remains a separate relation.
    """

    __tablename__ = "user_profile"
    __table_args__ = (
        UniqueConstraint("username", name="uq_user_profile_username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    """Generated positive identity referenced by authored review actions.

    The value remains stable when the Profile is renamed and is the only Profile
    fact foreign keys retain.
    """

    username: Mapped[str] = mapped_column(String, nullable=False)
    """Globally unique display name, mutable without rewriting actions.

    Readers join the current value for presentation; it is not an immutable copy
    of the name shown when an action was authored.
    """


@dataclass(frozen=True)
class UserProfileRecord:
    """Expose one durable review Profile without exposing its table row.

    Profile stores return this immutable record to HTTP and review code. Use
    `id` for attribution and persistence joins; show `username` as current
    display data.

    The record carries no agent registration, preferences, role, or permission.
    """

    id: int
    """Stable positive database id used by Profile-authored review actions.

    Callers persist and compare this value for attribution rather than treating
    the current username as identity.
    """

    username: str
    """Current display name shown by Profile controls or Comments.

    It may change while `id` and every relation referencing the Profile remain
    unchanged.
    """


def profile_record(
    profile_id: int,
    username: str,
) -> UserProfileRecord:
    """Validate persisted Profile fields before exposing the shared record.

    # Parameters

    - `profile_id`: Positive identity loaded from the shared Profile table.
    - `username`: Non-empty current display name loaded with that identity.

    # Usage

    Profile stores call this after selecting or inserting the two database
    columns. Pass values read from the shared Profile table, not unvalidated
    HTTP input.

    # Failures

    - Asserts when the id is not positive or the stored username is empty.
      Either condition means persisted data has violated the Profile schema.
    """
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

    # Parameters

    - `dbapi_connection`: Newly opened SQLite connection to configure before
      SQLAlchemy gives it to application code.
    - `connection_record`: SQLAlchemy pool callback context. Dirdiff does not
      retain or inspect it.

    # Usage

    Register this function as SQLAlchemy's `connect` listener before opening
    application sessions. Application code must not call it for an engine that
    is already serving work.

    """
    del connection_record
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
    """Bring a database to the complete schema declared by dirdiff.

    Persistent databases follow the Alembic history and skip the upgrade when
    already at its head. Ephemeral databases have no migration lifetime, so
    SQLAlchemy creates the current metadata directly.

    # Parameters

    - `engine`: Engine connected to the database whose schema must be ready
      when this call returns.
    - `migrate`: Persistent SQLite path and its exact Alembic configuration, or
      `None` for a new ephemeral database.

    # Usage

    Engine factories call this after installing the SQLite connection listener
    and before constructing any feature store. Pass the persistent database path
    when its schema must follow Alembic history.

    # Failures

    - Propagates connection, migration, and schema-creation failures. A caller
      must not use the engine when bootstrapping does not complete.
    """

    if migrate is not None:
        db_path, migration_config_path = migrate
        assert migration_config_path.is_file(), (
            "database migration configuration is missing: "
            f"{migration_config_path}"
        )
        config = Config(migration_config_path)
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


def open_sqlite_engine(db_path: Path, migration_config_path: Path) -> Engine:
    """Open and bootstrap the persistent dirdiff SQLite database.

    The path is expanded, its parent directories are created, SQLite connection
    invariants are installed, and Alembic upgrades the database to the current
    schema before return. The caller owns the returned engine's disposal.

    # Parameters

    - `db_path`: User-expandable database path; it need not already exist.
    - `migration_config_path`: Existing Alembic configuration paired with its
      migration history for this installation.

    # Usage

    Use this for a user database whose contents must survive process exit. Keep
    the returned engine for the lifetime of the stores built from it, then call
    `Engine.dispose` during application shutdown.

    # Failures

    - Propagates directory creation, SQLite connection, and Alembic migration
      failures. No usable engine is returned unless the current schema exists.
    """

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
    """Open one process-local in-memory database with the complete current schema.

    A `StaticPool` makes every session share the same SQLite connection and
    disables the driver's thread check for test/application access. Tables are
    created directly rather than migrated; the caller owns engine disposal and
    all data disappears with that engine.

    # Usage

    Use this when the database lifetime must match one process, primarily for
    tests. Share the returned engine among all stores that need to observe the
    same in-memory rows, then dispose it when the scenario ends.

    # Failures

    - Propagates SQLite connection or table-creation failures.
    """

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    bootstrap_tables(engine)
    return engine
