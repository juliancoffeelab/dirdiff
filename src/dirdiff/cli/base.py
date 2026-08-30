"""Define the `dirdiff` terminal commands.

## Public interface

`main` invokes the Typer application used by both the installed console script
and `python -m dirdiff`. The root command opens a Head Tab. The `refs` and
`branch` subcommands select other initial Tabs, while `mark` edits the local
repository registry.

## Purpose and boundaries

This module defines command spelling, parses terminal input, and builds
`RuntimeConfig`. Repository-mark operations live in
`dirdiff.cli.marker_utils`; server and browser process lifetime lives in
`dirdiff.cli.server_launch`. The command layer must not define HTTP routes,
workspace loading, diff rendering, or frontend behavior.
"""

from __future__ import annotations

import json
import logging
import os
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Annotated

import typer

from dirdiff.server import RuntimeConfig
from dirdiff.util import JsonValue

from . import marker_utils, server_launch
from .marker_utils import DB_PATH_ENV, InstallationMode

__all__ = ["main"]

cli_app = typer.Typer(
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    help="Open the dirdiff browser UI.",
)
"""Process-lifetime Typer application used by both console entrypoints.

Decorators below register the root startup callback and subcommands on this
exact instance during import. `main` invokes it once; importing the module does
not parse arguments or start the app.
"""


def configure_logging() -> None:
    """Configure process logging before launching command work.

    Normal CLI runs keep framework logs quiet.  `DIRDIFF_DEBUG_PERF=1` opts
    into more verbose application logging without introducing another command
    line flag.

    # Usage

    Each command handler calls this immediately before doing command work.
    `logging.basicConfig` leaves an already-configured root handler intact; the
    uvicorn logger levels are set on every call.
    """

    logging.basicConfig(
        level=logging.INFO
        if os.environ.get("DIRDIFF_DEBUG_PERF") == "1"
        else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


@cli_app.callback(invoke_without_command=True)
def start(
    ctx: typer.Context,
    db_path: Annotated[
        Path | None,
        typer.Option(
            envvar=DB_PATH_ENV,
            help="Repo registry database path.",
        ),
    ] = None,
    store_path: Annotated[
        Path | None,
        typer.Option(
            help="Snapshot storage directory. Defaults to store beside the database.",
        ),
    ] = None,
    presets_root: Annotated[
        str | None,
        typer.Option(
            help="Directory of preset catalogs. Defaults to tests/presets.",
        ),
    ] = None,
    port: Annotated[
        int,
        typer.Option(help="Local web server port."),
    ] = server_launch.DEFAULT_PORT,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless",
            help="Do not open the browser.",
        ),
    ] = False,
    frontend_port: Annotated[
        int,
        typer.Option(help="Browser UI dev server port."),
    ] = server_launch.DEFAULT_FRONTEND_PORT,
    no_frontend_dev: Annotated[
        bool,
        typer.Option(
            "--no-frontend-dev",
            help="Run the backend only.",
        ),
    ] = False,
) -> None:
    """Launch the default working-tree-vs-HEAD browser session.

    Typer invokes this callback before subcommands as well, so it stores shared
    options on `ctx.obj` and only launches the app when no subcommand was
    selected.

    # Parameters

    - `ctx`: Current Typer invocation used to share options with a subcommand.
    - `db_path`: Registry database path, or the configured user-level default.
    - `store_path`: Snapshot directory, or `None` for the database sibling.
    - `presets_root`: Optional preset-catalog root passed unchanged to the server.
    - `port`: Requested backend loopback port.
    - `headless`: Whether startup must skip opening a browser.
    - `frontend_port`: Requested Vite loopback port.
    - `no_frontend_dev`: Whether startup omits the Vite process.

    The callback configures logging and blocks in app startup only when Typer
    selected no subcommand.

    # Usage

    Typer invokes this as the root command. Use `dirdiff` for the default
    working-tree comparison or put the shared options before a subcommand, for
    example `dirdiff --headless refs HEAD worktree`. Application code should
    construct `RuntimeConfig` or call `dirdiff.server.create_app` instead of
    calling this callback directly.

    # Failures

    Unavailable ports, missing repository marks, database errors, and process
    startup failures propagate through the CLI boundary and terminate the
    command.
    """

    # Standard direct-URL metadata is the sole development/release selector.
    # Read it once here so every command and launch decision uses one result.
    try:
        installed_distribution = distribution("dirdiff")
    except PackageNotFoundError as exc:
        raise RuntimeError("dirdiff distribution metadata is missing") from exc
    direct_url_text = installed_distribution.read_text("direct_url.json")
    mode: InstallationMode = "release"
    if direct_url_text is not None:
        direct_url: JsonValue = json.loads(direct_url_text)
        if not isinstance(direct_url, dict):
            raise RuntimeError("dirdiff direct_url.json must contain an object")
        url = direct_url.get("url")
        if not isinstance(url, str) or url == "":
            raise RuntimeError("dirdiff direct_url.json has no valid URL")
        subdirectory = direct_url.get("subdirectory")
        if subdirectory is not None and not isinstance(subdirectory, str):
            raise RuntimeError(
                "dirdiff direct_url.json has an invalid subdirectory"
            )
        info_keys = [
            key
            for key in ("archive_info", "dir_info", "vcs_info")
            if key in direct_url
        ]
        if len(info_keys) != 1:
            raise RuntimeError(
                "dirdiff direct_url.json must contain one installation type"
            )
        info_key = info_keys[0]
        info = direct_url[info_key]
        if not isinstance(info, dict):
            raise RuntimeError(
                f"dirdiff direct_url.json {info_key} must contain an object"
            )
        if info_key == "dir_info":
            editable = info.get("editable")
            if editable is not None and not isinstance(editable, bool):
                raise RuntimeError(
                    "dirdiff direct_url.json editable flag must be boolean"
                )
            if editable is True:
                mode = "development"
        elif info_key == "vcs_info":
            for field in ("vcs", "commit_id"):
                value = info.get(field)
                if not isinstance(value, str) or value == "":
                    raise RuntimeError(
                        f"dirdiff direct_url.json has no valid {field}"
                    )
            requested_revision = info.get("requested_revision")
            if requested_revision is not None and not isinstance(
                requested_revision, str
            ):
                raise RuntimeError(
                    "dirdiff direct_url.json has an invalid requested revision"
                )
        else:
            hashes = info.get("hashes")
            if hashes is not None and (
                not isinstance(hashes, dict)
                or not all(
                    isinstance(name, str) and isinstance(value, str)
                    for name, value in hashes.items()
                )
            ):
                raise RuntimeError(
                    "dirdiff direct_url.json has invalid archive hashes"
                )
            archive_hash = info.get("hash")
            if archive_hash is not None and not isinstance(archive_hash, str):
                raise RuntimeError(
                    "dirdiff direct_url.json has an invalid archive hash"
                )

    resolved_db_path = marker_utils.db_path_or_default(db_path, mode)
    resolved_store_path = (
        store_path.expanduser() if store_path is not None else None
    )
    ctx.obj = server_launch.AppOptions(
        db_path=resolved_db_path,
        store_path=resolved_store_path,
        presets_root=presets_root,
        port=port,
        frontend_port=frontend_port,
        headless=headless,
        no_frontend_dev=no_frontend_dev,
        mode=mode,
    )
    if ctx.invoked_subcommand is not None:
        return
    configure_logging()
    config = RuntimeConfig(
        db_path=str(resolved_db_path),
        migration_config_path=str(marker_utils.migration_config_path(mode)),
        store_path=str(
            resolved_store_path or resolved_db_path.parent / "store"
        ),
        presets_root=presets_root,
    )
    server_launch.run_app(
        config=config,
        port=port,
        frontend_port=frontend_port,
        headless=headless,
        no_frontend_dev=no_frontend_dev,
        mode=mode,
    )


@cli_app.command()
def refs(
    ctx: typer.Context,
    left: Annotated[
        str,
        typer.Argument(help="Left Git ref or diff side."),
    ] = "HEAD",
    right: Annotated[
        str,
        typer.Argument(help="Right Git ref or diff side."),
    ] = "worktree",
) -> None:
    """Launch the browser with an arbitrary-ref comparison selected.

    This subcommand keeps the same local app startup behavior as the default
    command, but seeds the frontend URL with the Refs Tab and the two side
    names the user supplied.

    # Parameters

    - `ctx`: Typer context containing `AppOptions` from the root callback.
    - `left`: Left Git ref or built-in side placed in startup state.
    - `right`: Right Git ref or built-in side placed in startup state.

    Side existence is validated later by the selected workspace backend.

    # Usage

    Run `dirdiff refs <left> <right>`. The root callback supplies `ctx.obj`, so
    direct Python callers must not invoke this function without the matching
    Typer context.

    # Failures

    A missing `AppOptions` context is a programming error. Port selection,
    repository-mark, and startup failures propagate from `run_app`; invalid Git
    sides are reported later when the backend captures the selection.
    """

    configure_logging()
    options = ctx.obj
    assert isinstance(options, server_launch.AppOptions), "app options missing"
    config = RuntimeConfig(
        db_path=str(options.db_path),
        migration_config_path=str(
            marker_utils.migration_config_path(options.mode)
        ),
        store_path=str(options.store_path or options.db_path.parent / "store"),
        tab="refs",
        left=left,
        right=right,
        presets_root=options.presets_root,
    )
    server_launch.run_app(
        config=config,
        port=options.port,
        frontend_port=options.frontend_port,
        headless=options.headless,
        no_frontend_dev=options.no_frontend_dev,
        mode=options.mode,
    )


@cli_app.command()
def branch(
    ctx: typer.Context,
    base_branch: Annotated[
        str,
        typer.Argument(help="Base branch."),
    ],
    review_branch: Annotated[
        str,
        typer.Argument(help="Review branch."),
    ],
) -> None:
    """Launch the browser with branch-review controls preselected.

    The server still exposes the same API once it is running.  The branch names
    here are only startup state passed to the frontend so the review view opens
    on the requested base/review pair.

    # Parameters

    - `ctx`: Typer context containing `AppOptions` from the root callback.
    - `base_branch`: Local branch name seeded into the base selector.
    - `review_branch`: Local branch name seeded into the review selector.

    Branch resolution happens during capture, not in this command.

    # Usage

    Run `dirdiff branch <base-branch> <review-branch>`. The root callback must
    have populated `ctx.obj`; this function is Typer wiring rather than a Python
    API for resolving branches.

    # Failures

    A missing `AppOptions` context is a programming error. App startup failures
    propagate from `run_app`, while nonexistent or otherwise invalid branches
    fail later at the backend capture boundary.
    """

    configure_logging()
    options = ctx.obj
    assert isinstance(options, server_launch.AppOptions), "app options missing"
    config = RuntimeConfig(
        db_path=str(options.db_path),
        migration_config_path=str(
            marker_utils.migration_config_path(options.mode)
        ),
        store_path=str(options.store_path or options.db_path.parent / "store"),
        tab="branch-review",
        base_selection={"source": "local", "branch": base_branch},
        review_selection={"source": "local", "branch": review_branch},
        presets_root=options.presets_root,
    )
    server_launch.run_app(
        config=config,
        port=options.port,
        frontend_port=options.frontend_port,
        headless=options.headless,
        no_frontend_dev=options.no_frontend_dev,
        mode=options.mode,
    )


@cli_app.command()
def mark(
    ctx: typer.Context,
    path: Annotated[
        Path,
        typer.Option("--path", help="Repository path."),
    ] = Path("."),
    name: Annotated[
        str | None,
        typer.Option(help="Display name. If omitted, uses the last path part."),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option(
            envvar=DB_PATH_ENV,
            help="Repo registry database path.",
        ),
    ] = None,
    list_marks: Annotated[
        bool,
        typer.Option(
            "--list",
            help="Print marked repositories.",
        ),
    ] = False,
    remove_id: Annotated[
        int | None,
        typer.Option(
            "--remove",
            min=1,
            help="Remove a marked repository by id.",
        ),
    ] = None,
) -> None:
    """Add, list, or deactivate repositories in the local dirdiff registry.

    Repository marks are CLI-managed state.  The browser server reads the same
    database later to build the repo picker, but path normalization and duplicate
    handling belong to this command path.

    # Parameters

    - `ctx`: Typer context containing the validated installation mode.
    - `path`: Repository path used when creating a mark.
    - `name`: Display name, or `None` to derive it from the path.
    - `db_path`: Registry database path, or the configured default.
    - `list_marks`: Whether to print marks instead of creating one.
    - `remove_id`: Mark id to deactivate instead of creating one.

    # Usage

    Use `dirdiff mark --path <repository>` to add a mark, `dirdiff mark --list`
    to list active marks, or `dirdiff mark --remove <id>` to deactivate one.
    These modes update or inspect the registry and never start the browser app.

    # Failures

    Listing and removal together raise `typer.BadParameter`. Duplicate active
    paths, unknown removal ids, missing `PWD` for relative paths, and database
    failures terminate the command through the selected marker helper.
    """

    configure_logging()
    options = ctx.obj
    assert isinstance(options, server_launch.AppOptions), "app options missing"
    if list_marks and remove_id is not None:
        raise typer.BadParameter("--list and --remove cannot be combined.")
    if list_marks:
        marker_utils.print_marked_repos(db_path=db_path, mode=options.mode)
        return
    if remove_id is not None:
        marker_utils.remove_marked_repo(
            project_id=remove_id,
            db_path=db_path,
            mode=options.mode,
        )
        return
    marker_utils.mark_repo(
        repo_path=path,
        name=name,
        db_path=db_path,
        mode=options.mode,
    )


def main() -> None:
    """Run the Typer application used by the console entrypoint.

    `pyproject.toml` points the `dirdiff` script at this function.  Keeping
    the wrapper tiny makes the package importable without launching anything.

    # Usage

    The installed `dirdiff` script and `python -m dirdiff` call this function.
    Importers should use the package interfaces for application work rather
    than invoking the command parser in-process.

    # Failures

    Typer parameter errors and every selected command failure propagate through
    this boundary using Typer's normal command-line exit behavior.
    """

    cli_app()
