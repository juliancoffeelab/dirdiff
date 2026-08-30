"""Terminal operations for the local repository registry.

## Public interface

The `dirdiff mark` command uses this module to select its SQLite database, add
or deactivate repository marks, and print the active registry. Paths retain the
shell's spelling rules, and expected registry conflicts become readable command
failures.

## Purpose and boundaries

This is the adapter between Typer commands and `dirdiff.db.RepoMarkStore`. It
handles CLI path interpretation and terminal output, while the store handles
persistence. FastAPI routes use the store directly because they already carry
validated project identities and must not emit terminal output.
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import distribution
from pathlib import Path
from typing import Literal, NoReturn

from sqlalchemy.exc import IntegrityError

from dirdiff.db import RepoMarkStore, open_sqlite_engine

InstallationMode = Literal["development", "release"]
"""Launch topology selected from the installed distribution metadata.

Development means an editable installation with the repository Vite HUD.
Release means every standard installation with the bundled HUD. It must not
represent a runtime fallback or a user-selected frontend preference.
"""

DEVELOPMENT_DB_PATH = (
    Path.home() / ".local" / "share" / "dirdiff" / "dirdiff.sqlite"
)
"""Default database path for an editable development installation."""
RELEASE_DB_PATH = (
    Path.home() / ".local" / "share" / "dirdiff" / "release" / "dirdiff.sqlite"
)
"""Default database path for a standard release installation."""
DEVELOPMENT_MIGRATION_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "alembic.ini"
)
"""Canonical Alembic configuration in an editable source checkout."""
DEVELOPMENT_AGENT_SKILLS_PATH = (
    Path(__file__).resolve().parents[3] / ".agents" / "skills"
)
"""Canonical external-agent skills in an editable source checkout."""

DB_PATH_ENV = "DIRDIFF_DB_PATH"
"""Environment variable accepted as the CLI registry-path override.

Typer reads it for exposed options, and `db_path_or_default` also reads it for
helpers invoked directly. Blank values are treated as absent.
"""


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
    """Return the fixed external-agent skill root for an installation mode.

    Development exposes the canonical project-local skills so edits take effect
    immediately. Release exposes the immutable copies bundled in the installed
    package. Application construction validates every required entry file.

    # Parameters

    - `mode`: Validated installation mode selecting source or wheel resources.
    """

    if mode == "development":
        return DEVELOPMENT_AGENT_SKILLS_PATH
    return Path(str(distribution("dirdiff").locate_file("dirdiff/skills")))


def migration_config_path(mode: InstallationMode) -> Path:
    """Return the fixed Alembic configuration path for an installation mode.

    Development uses the canonical file in the editable checkout so migration
    edits hot-reload with the rest of the backend. Release uses the copy bundled
    inside the installed distribution. The database boundary validates that the
    selected file exists; this operation does not search for substitutes.

    # Parameters

    - `mode`: Validated installation mode selecting source or wheel resources.

    # Usage

    Pass the result to `open_sqlite_engine` whenever opening a persistent
    dirdiff database.
    """

    if mode == "development":
        return DEVELOPMENT_MIGRATION_CONFIG_PATH
    return Path(
        str(distribution("dirdiff").locate_file("dirdiff/db/alembic.ini"))
    )


def default_db_path(mode: InstallationMode) -> Path:
    """Return the implicit database path for one installation mode.

    Callers use this only after validating standardized distribution metadata.
    Development and release must keep separate implicit state roots; explicit
    command or environment paths bypass this selection in
    `db_path_or_default`.
    """

    if mode == "development":
        return DEVELOPMENT_DB_PATH
    return RELEASE_DB_PATH


def db_path_or_default(db_path: Path | None, mode: InstallationMode) -> Path:
    """Resolve an optional CLI database path to the path dirdiff should use.

    CLI commands accept `--db-path` on operations that touch the repo
    registry.  `DIRDIFF_DB_PATH` provides the same selection for shell scripts
    and cram transcripts that would otherwise have to repeat the option.
    Omitting both selects the mode-specific user-level registry path.

    # Parameters

    - `db_path`: Command option, or `None` to inspect the environment and mode.
    - `mode`: Validated installation mode used only for the implicit default.

    # Usage

    CLI operations call this once before opening `RepoMarkStore`. Pass the
    parsed `--db-path` value and the validated installation mode; the helper
    checks `DIRDIFF_DB_PATH` only when that value is absent.
    """

    if db_path is not None:
        return db_path.expanduser()
    configured = os.environ.get(DB_PATH_ENV)
    if configured is not None and configured.strip() != "":
        return Path(configured).expanduser()
    return default_db_path(mode)


def absolute_repo_path(repo_path: Path) -> Path:
    """Anchor a repo path the same way an interactive shell user expects.

    Tildes are expanded. Absolute paths pass through without resolving symlinks
    or `..`; relative paths are joined to `PWD`, which preserves the shell path
    spelling even when `Path.cwd()` differs after launch or under a test harness.

    # Usage

    Call this on the path supplied to `dirdiff mark` before storing it. The
    result deliberately preserves symlinks and parent components because the
    registry records the shell-facing path spelling.

    # Failures

    Relative input requires the process environment to contain `PWD`; its
    absence raises `KeyError`.
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

    # Parameters

    - `repo_path`: Absolute path whose mark insertion failed.
    - `exc`: Integrity failure raised by `RepoMarkStore.new_mark`.

    # Usage

    Call this only from the `IntegrityError` handler around
    `RepoMarkStore.new_mark`; it never returns.

    # Failures

    A duplicate path raises `SystemExit` with a readable message. Every other
    integrity failure is re-raised unchanged.
    """

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
    """Store one repository mark in the selected registry database.

    The CLI accepts a relative path and optional display name.  This helper
    expands and anchors the path, derives the default name from its final
    component, writes the mark, and prints the created mark id for shell feedback.

    # Parameters

    - `repo_path`: User-supplied absolute or shell-relative repository path.
    - `name`: Explicit display name, or `None` to use the final path component.
    - `db_path`: Registry path override, or `None` for environment/default lookup.
    - `mode`: Validated installation mode used only when no path override exists.

    # Usage

    `dirdiff mark` calls this in its add mode after ruling out `--list` and
    `--remove`. Successful writes print the new id and name to stderr.

    # Failures

    A path with no final component and no explicit `name` raises `SystemExit`.
    Relative input without `PWD` raises `KeyError`. A duplicate active path is
    translated to `SystemExit`; other database failures propagate unchanged.
    """

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
    """Print the registered repositories in the selected database.

    This is intentionally plain terminal output rather than API formatting.
    It supports `dirdiff mark --list` and mirrors the database the browser app
    will read on launch.

    # Parameters

    - `db_path`: Registry path override, or `None` for environment/default lookup.
    - `mode`: Validated installation mode used only when no path override exists.

    # Usage

    `dirdiff mark --list` calls this instead of starting the application. An
    empty registry prints one explanatory line; otherwise records retain store
    order and use two terminal lines each.

    `mode` selects the implicit database only when `db_path` and
    `DIRDIFF_DB_PATH` are absent.

    # Failures

    Database creation and read failures propagate to the command boundary.
    """

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
    """Deactivate one repository mark in the selected registry database.

    The mark id comes from `dirdiff mark --list`.  Deactivation hides it from
    ordinary registry operations while retaining its id and related persisted
    state.  The repository directory and its Git data remain untouched.

    # Parameters

    - `project_id`: Existing mark id obtained from the registry listing.
    - `db_path`: Registry path override, or `None` for environment/default lookup.
    - `mode`: Validated installation mode used only when no path override exists.

    # Usage

    `dirdiff mark --remove <id>` passes the positive id selected from the
    registry listing. Success prints confirmation to stderr.

    # Failures

    An unknown or inactive id raises `SystemExit`. Database failures propagate
    unchanged.
    """

    engine = open_sqlite_engine(
        db_path_or_default(db_path, mode), migration_config_path(mode)
    )
    store = RepoMarkStore(engine)
    if not store.delete(project_id):
        raise SystemExit(f"No marked repo with id: {project_id}")
    print(f"Removed repo mark {project_id}", file=sys.stderr)
