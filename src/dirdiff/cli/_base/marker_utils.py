from __future__ import annotations

import os
import sys
from importlib.metadata import distribution
from pathlib import Path
from typing import Literal, NoReturn

from sqlalchemy.exc import IntegrityError

from dirdiff.db import RepoMarkStore, open_sqlite_engine

# development: editable install with the repo Vite HUD; release: every
# standard install with the bundled HUD. Never a runtime fallback or preference.
InstallationMode = Literal["development", "release"]

# The two modes must keep separate implicit state roots.
DEVELOPMENT_DB_PATH = (
    Path.home() / ".local" / "share" / "dirdiff" / "dirdiff.sqlite"
)
RELEASE_DB_PATH = (
    Path.home() / ".local" / "share" / "dirdiff" / "release" / "dirdiff.sqlite"
)
DEVELOPMENT_MIGRATION_CONFIG_PATH = (
    Path(__file__).resolve().parents[4] / "alembic.ini"
)
DEVELOPMENT_AGENT_SKILLS_PATH = (
    Path(__file__).resolve().parents[4] / ".agents" / "skills"
)

DB_PATH_ENV = "DIRDIFF_DB_PATH"


__all__ = [
    "DB_PATH_ENV",
    "InstallationMode",
    "agent_skills_path",
    "db_path_or_default",
    "default_db_path",
    "mark_repo",
    "migration_config_path",
    "print_marked_repos",
    "remove_marked_repo",
]


def agent_skills_path(mode: InstallationMode) -> Path:

    if mode == "development":
        return DEVELOPMENT_AGENT_SKILLS_PATH
    return Path(str(distribution("dirdiff").locate_file("dirdiff/skills")))


def migration_config_path(mode: InstallationMode) -> Path:

    if mode == "development":
        return DEVELOPMENT_MIGRATION_CONFIG_PATH
    return Path(
        str(distribution("dirdiff").locate_file("dirdiff/db/alembic.ini"))
    )


def default_db_path(mode: InstallationMode) -> Path:

    if mode == "development":
        return DEVELOPMENT_DB_PATH
    return RELEASE_DB_PATH


def db_path_or_default(db_path: Path | None, mode: InstallationMode) -> Path:

    if db_path is not None:
        return db_path.expanduser()
    configured = os.environ.get(DB_PATH_ENV)
    if configured is not None and configured.strip() != "":
        return Path(configured).expanduser()
    return default_db_path(mode)


def absolute_repo_path(repo_path: Path) -> Path:

    expanded_path = repo_path.expanduser()
    if expanded_path.is_absolute():
        return expanded_path
    # PWD keeps the shell's spelling (symlinks, `..`) that Path.cwd() would
    # resolve away; the registry records that spelling. KeyError if unset.
    return Path(os.environ["PWD"]) / expanded_path


def duplicate_repo_path_error(repo_path: Path, exc: IntegrityError) -> NoReturn:

    # SQLAlchemy raises one IntegrityError type for every constraint; only the
    # path uniqueness failure is an expected user error.
    if "repo_mark.path" in str(exc):
        raise SystemExit(f"Repo is already marked: {repo_path}") from exc
    raise exc


def mark_repo(
    *,
    repo_path: Path,
    name: str | None,
    db_path: Path | None,
    mode: InstallationMode,
) -> None:

    engine = open_sqlite_engine(
        db_path_or_default(db_path, mode), migration_config_path(mode)
    )
    store = RepoMarkStore(engine)
    expanded_repo_path = absolute_repo_path(repo_path)
    if name is None:
        display_name = expanded_repo_path.name
        if display_name == "":
            raise SystemExit(
                f"Cannot derive a repo name from path: {expanded_repo_path}"
            )
    else:
        display_name = name
    try:
        mark = store.new_mark(path=expanded_repo_path, name=display_name)
    except IntegrityError as exc:
        duplicate_repo_path_error(expanded_repo_path, exc)
    print(f"Marked repo {mark.id}: {mark.name}", file=sys.stderr)


def print_marked_repos(*, db_path: Path | None, mode: InstallationMode) -> None:

    engine = open_sqlite_engine(
        db_path_or_default(db_path, mode), migration_config_path(mode)
    )
    store = RepoMarkStore(engine)
    marks = store.list()
    if len(marks) == 0:
        print("No marked repos.")
        return
    for mark_record in marks:
        print(f"{mark_record.id}. {mark_record.name}")
        print(f"   path: {mark_record.path}")


def remove_marked_repo(
    *, project_id: int, db_path: Path | None, mode: InstallationMode
) -> None:

    engine = open_sqlite_engine(
        db_path_or_default(db_path, mode), migration_config_path(mode)
    )
    store = RepoMarkStore(engine)
    if not store.delete(project_id):
        raise SystemExit(f"No marked repo with id: {project_id}")
    print(f"Removed repo mark {project_id}", file=sys.stderr)
