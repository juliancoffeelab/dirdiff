from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

from sqlalchemy.exc import IntegrityError

from dirdiff.repo_registry import RepoMarkStore
from dirdiff.runtime import DEFAULT_DB_PATH


def db_path_or_default(db_path: Path | None) -> Path:
    if db_path is None:
        return DEFAULT_DB_PATH
    return db_path.expanduser()


def absolute_repo_path(repo_path: Path) -> Path:
    expanded_path = repo_path.expanduser()
    if expanded_path.is_absolute():
        return expanded_path
    return Path(os.environ["PWD"]) / expanded_path


def duplicate_repo_path_error(repo_path: Path, exc: IntegrityError) -> NoReturn:
    if "repo_mark.path" in str(exc):
        raise SystemExit(f"Repo is already marked: {repo_path}") from exc
    raise exc


def mark_repo(
    *, repo_path: Path, name: str | None, db_path: Path | None
) -> None:
    store = RepoMarkStore.open(db_path_or_default(db_path))
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
    store = RepoMarkStore.open(db_path_or_default(db_path))
    marks = store.list()
    if len(marks) == 0:
        print("No marked repos.")
        return
    for mark_record in marks:
        print(f"{mark_record.id}. {mark_record.name}")
        print(f"   path: {mark_record.path}")
