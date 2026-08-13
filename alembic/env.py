"""
Alembic entry point.

Configures logging, exposes migration functions and sets the path
to sqlite database.
Works with explicit database path, but by default will do the right thing.
"""

from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import URL, create_engine

from alembic import context
from dirdiff.cli.marker_utils import db_path_or_default
from dirdiff.db import TableBase

# This is the Alembic Config object, which provides
# access to the values within the .ini file in use.
#
# Or to config we supply programmatically.
config = context.config
if config.config_file_name is not None:
    # Migrations run while the application is starting. Preserve Uvicorn's
    # already-configured startup, access, and error loggers.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Table base metadata for, uh, all fun things.
target_metadata = TableBase.metadata

# The path to our db
provided_db_path = context.get_x_argument(as_dictionary=True).get("db_path")
attribute_db_path = config.attributes.get("db_path")
match attribute_db_path, provided_db_path:
    case Path() as attribute_path, _:
        db_path = db_path_or_default(attribute_path)
    case None, str() as provided_path:
        db_path = db_path_or_default(Path(provided_path))
    case None, None:
        db_path = db_path_or_default(None)
    case r:
        raise AssertionError(f"unreachable path configuration: {r}")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = URL.create("sqlite", database=str(db_path))
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    url = URL.create("sqlite", database=str(db_path))
    connectable = create_engine(url)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
