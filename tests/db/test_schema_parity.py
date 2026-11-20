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


def test_migrated_schema_matches_declared_model(tmp_path: Path) -> None:
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
