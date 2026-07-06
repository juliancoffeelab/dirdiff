"""Repo-mark helpers used by `dirdiff mark` and `dirdiff refs`.

The command functions in `dirdiff.cli` call this module before touching
`RepoMarkStore`: `db_path_or_default` chooses the SQLite file, `mark_repo`
stores a repo path/name pair, and `print_marked_repos` formats the terminal
listing.  `absolute_repo_path` keeps relative CLI arguments anchored to the
user's shell directory, and `duplicate_repo_path_error` converts the repo-path
uniqueness constraint into a readable command failure.

FastAPI routes do not use this module.  Server code receives already-selected
repo ids and should call `RepoMarkStore` directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

from sqlalchemy.exc import IntegrityError

from dirdiff.db.base import open_sqlite_engine
from dirdiff.db.repo_registry import RepoMarkStore

DEFAULT_DB_PATH = (
    Path.home() / ".local" / "share" / "dirdiff" / "dirdiff.sqlite"
)

__all__ = [
    "DEFAULT_DB_PATH",
    "absolute_repo_path",
    "db_path_or_default",
    "duplicate_repo_path_error",
    "mark_repo",
    "print_marked_repos",
]


def db_path_or_default(db_path: Path | None) -> Path:
    """Resolve an optional CLI database path to the path dirdiff should use.

    CLI commands accept `--db-path` on operations that touch the repo
    registry.  Omitting it means all commands share the same user-level
    registry path.
    """

    if db_path is None:
        return DEFAULT_DB_PATH
    return db_path.expanduser()


def absolute_repo_path(repo_path: Path) -> Path:
    """Resolve a repo path the same way an interactive shell user expects.

    `Path.cwd()` can differ from the original shell directory after process
    launch details or test harnesses get involved.  `PWD` preserves the path
    the user typed relative paths against, including symlink spelling.
    """

    expanded_path = repo_path.expanduser()
    if expanded_path.is_absolute():
        return expanded_path
    return Path(os.environ["PWD"]) / expanded_path


def duplicate_repo_path_error(repo_path: Path, exc: IntegrityError) -> NoReturn:
    """Raise a readable duplicate-mark error or re-raise unrelated DB errors.

    SQLAlchemy reports all integrity failures through the same exception type.
    This helper recognizes the repo path uniqueness constraint and preserves
    unexpected database failures for the caller.
    """

    if "repo_mark.path" in str(exc):
        raise SystemExit(f"Repo is already marked: {repo_path}") from exc
    raise exc


def mark_repo(
    *, repo_path: Path, name: str | None, db_path: Path | None
) -> None:
    """Store one repository mark in the selected registry database.

    The CLI accepts a relative path and optional display name.  This helper
    normalizes the path, derives the default name from the final path component,
    writes the mark, and prints the created mark id for shell feedback.
    """

    engine = open_sqlite_engine(db_path_or_default(db_path))
    store = RepoMarkStore(engine)
    expanded_repo_path = absolute_repo_path(repo_path)
    if name is None:
        display_name = expanded_repo_path.name
        if not display_name:
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


def print_marked_repos(*, db_path: Path | None) -> None:
    """Print the registered repositories in the selected database.

    This is intentionally plain terminal output rather than API formatting.
    It supports `dirdiff mark --list` and mirrors the database the browser app
    will read on launch.
    """

    engine = open_sqlite_engine(db_path_or_default(db_path))
    store = RepoMarkStore(engine)
    marks = store.list()
    if len(marks) == 0:
        print("No marked repos.")
        return
    for mark_record in marks:
        print(f"{mark_record.id}. {mark_record.name}")
        print(f"   path: {mark_record.path}")
