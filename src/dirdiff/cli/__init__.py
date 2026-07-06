"""Console command module for the local dirdiff application.

The installed `dirdiff` console script and `python -m dirdiff` both call
`main`, which hands execution to the Typer application defined here.  The
decorated command functions (`start`, `mark`, `refs`, and `branch`) parse
terminal options, build `RuntimeConfig` for server startup, and delegate repo
mark operations to `dirdiff.cli.marker_utils`.

This module is allowed to own command-line spelling and terminal feedback.  It
must not own FastAPI routes, repository loading, diff rendering, or frontend
behavior.  `main` is the only exported symbol; the decorated command functions
are Typer wiring, not importable application API.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from dirdiff.server import RuntimeConfig

from . import marker_utils, server_launch
from .marker_utils import DEFAULT_DB_PATH

__all__ = ["main"]

cli_app = typer.Typer(
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    help="Open the dirdiff browser UI.",
)


def configure_logging() -> None:
    """Configure process logging before launching command work.

    Normal CLI runs keep framework logs quiet.  `DIRDIFF_DEBUG_PERF=1` opts
    into more verbose application logging without introducing another command
    line flag.
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
        typer.Option(help="Repo registry database path."),
    ] = None,
    presets_root: Annotated[
        str | None,
        typer.Option(help="Preset directory."),
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
    """

    resolved_db_path = (
        DEFAULT_DB_PATH if db_path is None else db_path.expanduser()
    )
    ctx.obj = server_launch.AppOptions(
        db_path=resolved_db_path,
        presets_root=presets_root,
        port=port,
        frontend_port=frontend_port,
        headless=headless,
        no_frontend_dev=no_frontend_dev,
    )
    if ctx.invoked_subcommand is not None:
        return
    configure_logging()
    config = RuntimeConfig(
        db_path=str(resolved_db_path),
        presets_root=presets_root,
    )
    server_launch.run_app(
        config=config,
        port=port,
        frontend_port=frontend_port,
        headless=headless,
        no_frontend_dev=no_frontend_dev,
    )


@cli_app.command()
def refs(
    ctx: typer.Context,
    left: Annotated[
        str,
        typer.Argument(help="Left Git ref or diff side."),
    ] = "head",
    right: Annotated[
        str,
        typer.Argument(help="Right Git ref or diff side."),
    ] = "worktree",
) -> None:
    """Launch the browser with an arbitrary-ref comparison selected.

    This subcommand keeps the same local app startup behavior as the default
    command, but seeds the frontend URL with `refs` mode and the two side
    names the user supplied.
    """

    configure_logging()
    options = ctx.obj
    assert isinstance(options, server_launch.AppOptions), "app options missing"
    config = RuntimeConfig(
        db_path=str(options.db_path),
        mode="refs",
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
    """

    configure_logging()
    options = ctx.obj
    assert isinstance(options, server_launch.AppOptions), "app options missing"
    config = RuntimeConfig(
        db_path=str(options.db_path),
        mode="branch-review",
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
    )


@cli_app.command()
def mark(
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
        typer.Option(help="Repo registry database path."),
    ] = None,
    list_marks: Annotated[
        bool,
        typer.Option(
            "--list",
            help="Print marked repositories.",
        ),
    ] = False,
) -> None:
    """Add or list repositories in the local dirdiff registry.

    Repository marks are CLI-managed state.  The browser server reads the same
    database later to build the repo picker, but path normalization and duplicate
    handling belong to this command path.
    """

    configure_logging()
    if list_marks:
        marker_utils.print_marked_repos(db_path=db_path)
        return
    marker_utils.mark_repo(repo_path=path, name=name, db_path=db_path)


def main() -> None:
    """Run the Typer application used by the console entrypoint.

    `pyproject.toml` points the `dirdiff` script at this function.  Keeping
    the wrapper tiny makes the package importable without launching anything.
    """

    cli_app()
