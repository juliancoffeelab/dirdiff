"""Guard that the migration chain and the declared model agree.

Every schema change is written twice by hand: once in `dirdiff.db`'s declared
model and once in an alembic revision. Ephemeral databases (including every
other test) call `create_all` from the model, so they match the model by
construction and can never notice a migration that produces something else —
the blind spot that let migration `f63b8a1d2e40` leave two constraints under
their pre-rename names for weeks. This module is the comparator that closes
it: build one database through the full migration chain, one from the model,
and require the normalized schemas to be identical.
"""

import re
import sqlite3
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine

from alembic import command
from dirdiff.db import TableBase

_CLAUSE_BOUNDARY = re.compile(
    r",\s*(?=CONSTRAINT |PRIMARY KEY|FOREIGN KEY|UNIQUE |CHECK "
    r"|[a-z_]+ (?:VARCHAR|INTEGER|BOOLEAN|BLOB|DATETIME))"
)
"""Split normalized SQLite CREATE statements at top-level schema clauses.

Migration rebuilds and SQLAlchemy metadata creation may reorder clauses and
change quoting without changing the schema. The parity test sorts only these
recognized clause boundaries; names, types, expressions, referenced columns,
and index order must still match verbatim.
"""


def test_migrated_schema_matches_declared_model(tmp_path: Path) -> None:
    """The full migration chain must produce exactly the declared schema.

    Builds two SQLite databases — one via `alembic upgrade head`, one via
    `TableBase.metadata.create_all` — and asserts their `sqlite_master`
    contents are identical after normalization. A failure means a migration
    and the model drifted apart; fix whichever side is wrong with a new
    revision or a model change, never by loosening this comparison.
    """
    migrated_path = tmp_path / "migrated.sqlite"
    declared_path = tmp_path / "declared.sqlite"

    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.attributes["db_path"] = migrated_path
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{declared_path}")
    TableBase.metadata.create_all(engine)
    engine.dispose()

    schemas: dict[str, dict[str, list[str]]] = {}
    for label, database_path in (
        ("migrated", migrated_path),
        ("declared", declared_path),
    ):
        connection = sqlite3.connect(database_path)
        try:
            rows = connection.execute(
                "SELECT name, sql FROM sqlite_master"
                " WHERE sql IS NOT NULL AND name != 'alembic_version'"
            ).fetchall()
        finally:
            connection.close()
        objects: dict[str, list[str]] = {}
        for name, sql in rows:
            flattened = re.sub(r'["\s]+', " ", sql).strip()
            # The CREATE TABLE closer attaches to whichever clause happens
            # to be last; drop it so clause sorting cannot depend on it.
            flattened = flattened.removesuffix(")").rstrip()
            objects[name] = sorted(
                clause.strip() for clause in _CLAUSE_BOUNDARY.split(flattened)
            )
        schemas[label] = objects

    assert schemas["migrated"] == schemas["declared"]
