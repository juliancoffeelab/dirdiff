"""Launch the local servers and browser selected by the CLI.

## Public interface

`run_app` is the entrypoint used by the Typer commands. `AppOptions` carries
their shared launch options. The remaining public functions expose individual
port, URL, Vite, browser, and uvicorn operations for the launch sequence.

## Purpose and boundaries

This module keeps process lifetime and port selection outside command parsing.
It starts Vite by default, runs uvicorn until shutdown, and terminates the Vite
child afterward. It does not define HTTP routes or diff behavior. Those belong
to `dirdiff.server`, the selected backend, and the rendering packages.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from dirdiff.backend import BranchSelection
from dirdiff.cli.marker_utils import (
    InstallationMode,
    default_db_path,
    migration_config_path,
)
from dirdiff.db import RepoMarkStore, open_sqlite_engine
from dirdiff.server import RUNTIME_CONFIG_ENV, RuntimeConfig

DEFAULT_PORT = 5052
"""Initial backend loopback port requested by the root CLI command.

Vite mode may shift it together with `DEFAULT_FRONTEND_PORT` when occupied.
Backend-only mode requires this exact port unless the user supplies another.
"""
DEFAULT_FRONTEND_PORT = 5173
"""Initial Vite loopback port requested by the root CLI command.

It is irrelevant in backend-only mode. In Vite mode, port-pair selection may
shift it by the same offset as the backend port.
"""
PORT_FALLBACK_ATTEMPTS = 20
"""Number of equal backend/frontend port offsets tried before startup stops.

`choose_port_pair` tests offsets starting at zero, so the last candidate adds
19 to each requested port. This bound prevents an unbounded search.
"""
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
"""Vite project directory passed as the frontend process working directory.

The path is derived from the installed source layout once at import time.
`start_frontend_dev_server` does not search for another frontend directory.
"""
BACKEND_RELOAD_DIR = Path(__file__).resolve().parents[1]
"""Python package directory watched by uvicorn reload.

`run_uvicorn` passes this exact directory with a Python-only include pattern,
so frontend files do not trigger backend process reloads.
"""

__all__ = [
    "DEFAULT_FRONTEND_PORT",
    "DEFAULT_PORT",
    "AppOptions",
    "choose_port_pair",
    "require_bindable_port",
    "run_app",
]


@dataclass(frozen=True)
class AppOptions:
    """CLI options shared between the root callback and subcommands.

    The root callback constructs this value and stores it on `ctx.obj`; `refs`
    and `branch` read it when building `RuntimeConfig`. The `mark` command reads
    its installation mode so its implicit state stays isolated.

    It contains parsed CLI configuration only. The value owns no process,
    database connection, server state, or browser lifetime.

    # Usage

    The root Typer callback stores one instance on `typer.Context.obj` before
    dispatching a subcommand. Command handlers read the same value and pass its
    fields to `run_app`; callers do not mutate it after construction.
    """

    db_path: Path
    """Registry path after command-option and environment/default selection.

    Command handlers use this same path for mark checks and server config.
    """

    store_path: Path | None
    """User-selected Snapshot directory after tilde expansion.

    `None` tells each command handler to use `db_path.parent / "store"`.
    """

    presets_root: str
    """Absolute Preset-catalog root forwarded to `RuntimeConfig`.

    The root callback resolves either `--presets-root` or the installation's
    exact default before constructing this shared command state.
    """

    port: int
    """Requested backend loopback port before availability checks.

    Vite mode may move it by a bounded offset; backend-only mode keeps it exact.
    """

    frontend_port: int
    """Requested Vite loopback port before paired availability checks.

    The value is retained but unused when `no_frontend_dev` is true.
    """

    headless: bool
    """Whether app startup skips scheduling the browser-open timer.

    This does not suppress either server or the printed URL.
    """

    no_frontend_dev: bool
    """Whether startup omits Vite and selects the backend URL directly.

    Uvicorn still runs with reload enabled in this mode. Release startup rejects
    this option because its bundled HUD is always present.
    """

    mode: InstallationMode
    """Topology selected once from standardized distribution metadata.

    The value determines the server factory, reload behavior, frontend process,
    and implicit persistence root. It is not inferred again by this module.
    """


def _add_branch_selection_query(
    query: dict[str, str],
    *,
    prefix: str,
    selection: BranchSelection,
) -> None:
    """Encode one startup branch selection into frontend URL params.

    `build_url` uses this for CLI-provided Branch Review state. It mirrors the
    canonical browser fields: source and branch are always present, while
    remote exists only on the remote variant.

    # Parameters

    - `query`: Mutable URL parameter map receiving this selection's fields.
    - `prefix`: `base` or `review`, used in every inserted key.
    - `selection`: Structured local or remote branch selection to encode.
    """
    query[f"{prefix}_source"] = selection["source"]
    query[f"{prefix}_branch"] = selection["branch"]
    if selection["source"] == "remote":
        query[f"{prefix}_remote"] = selection["remote"]


def build_url(port: int, config: RuntimeConfig) -> str:
    """Build the canonical browser URL for the requested startup state.

    A Head launch returns the root URL without query parameters, leaving its
    defaults to the frontend. Explicit CLI workflows use browser Tab fields
    rather than backend API parameters.

    # Parameters

    - `port`: Loopback port of the server whose page the browser should open.
    - `config`: Startup Tab state encoded into query parameters when non-default.

    Branch Review config must contain both structured branch selections.

    # Usage

    `run_app` calls this after port selection, once for the backend and once for
    Vite. Head startup returns the bare root URL. Refs and Branch Review startup
    encode the initial Tab in the query string.

    # Failures

    A Branch Review config without either branch selection violates the
    `RuntimeConfig` contract and raises `AssertionError`.
    """

    root = f"http://127.0.0.1:{port}/"
    if config.tab == "head":
        return root

    query = {
        "tab": config.tab,
        "engine": "tokendiff",
        "view": "inline",
    }
    if config.tab == "refs":
        query["left"] = config.left
        query["right"] = config.right
    else:
        assert config.base_selection is not None, (
            "Branch Review startup requires a base selection"
        )
        assert config.review_selection is not None, (
            "Branch Review startup requires a review selection"
        )
        _add_branch_selection_query(
            query,
            prefix="base",
            selection=config.base_selection,
        )
        _add_branch_selection_query(
            query,
            prefix="review",
            selection=config.review_selection,
        )
    return f"{root}?{urlencode(query, quote_via=quote)}"


def start_frontend_dev_server(
    *,
    backend_port: int,
    frontend_port: int,
) -> subprocess.Popen[bytes]:
    """Start Vite with a backend-origin override for this dirdiff process.

    The backend and frontend ports are selected together before this function
    runs.  Vite is launched with `--strictPort` so the browser URL printed by
    the CLI cannot silently point at a different process.

    # Parameters

    - `backend_port`: Selected backend port exported to Vite as its API origin.
    - `frontend_port`: Selected exact port passed to Vite.

    # Usage

    `start_frontend` calls this only after `choose_runtime_ports` has selected
    an exact free pair. The caller must retain the returned process and
    terminate it when the backend server stops.

    # Returns

    - The handle refers to the newly started Vite child and has not been waited
      on; startup may poll it for early exit.
    - The `bytes` parameter denotes subprocess byte mode. This launch inherits
      the parent's standard streams instead of exposing output pipes.
    - The caller owns the returned process lifetime and must terminate it when
      uvicorn exits.

    # Failures

    `subprocess.Popen` errors propagate. In particular, missing `bun` raises
    `FileNotFoundError`; `start_frontend` handles that one case.
    """

    env = os.environ.copy()
    env["VITE_DIRDIFF_BACKEND_ORIGIN"] = f"http://127.0.0.1:{backend_port}"
    return subprocess.Popen(
        [
            "bun",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(frontend_port),
            "--strictPort",
        ],
        cwd=FRONTEND_DIR,
        env=env,
    )


def can_bind_port(port: int) -> bool:
    """Return whether localhost can bind `port` right now.

    The check opens and immediately closes a loopback socket.  It is only a
    launch-time probe; another process can still race us before uvicorn or Vite
    binds the selected port.

    # Usage

    Port-selection functions use this as a non-reserving probe. A false result
    means the candidate cannot be used, not that another process necessarily
    owns it; invalid port numbers and other bind errors also return false.
    """

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def require_bindable_port(port: int, *, label: str) -> None:
    """Exit with a user-facing message if `port` is already occupied.

    Backend-only mode does not have a frontend port to shift in tandem, so the
    requested backend port must be available exactly or the CLI should stop with
    a clear command-line error.

    # Parameters

    - `port`: Exact loopback port to probe once.
    - `label`: User-facing server name included in the failure message.

    A successful probe does not reserve the port. An occupied port raises
    `SystemExit`.

    # Usage

    `choose_runtime_ports` calls this for backend-only startup, where there is
    no Vite port to shift alongside the backend. Tests may call it directly to
    verify the terminal diagnostic.

    # Failures

    Any bind-probe failure terminates startup with `SystemExit` naming `label`
    and `port`. The successful probe remains subject to a later bind race.
    """

    if can_bind_port(port):
        return
    raise SystemExit(
        f"{label} port {port} is already in use. "
        "Stop the existing dirdiff process or pass an explicit port."
    )


def choose_port_pair(backend_port: int, frontend_port: int) -> tuple[int, int]:
    """Find a backend/frontend port pair for dev-server mode.

    The two servers must use distinct ports.  When either requested port is in
    use, both ports advance by the same offset. The printed ports therefore
    preserve the relationship requested on the command line.

    # Parameters

    - `backend_port`: First backend candidate in the bounded search.
    - `frontend_port`: First frontend candidate shifted by the same offsets.

    The probes do not reserve either port. Equal or unavailable pairs are
    skipped; exhausting the configured attempts raises `SystemExit`.

    # Usage

    Call this for Vite mode before starting either server. Use both returned
    ports together; the function preserves their requested offset while
    searching and never returns an equal pair.

    # Returns

    - First, an available backend port at the selected offset.
    - Second, the available, distinct frontend port at the same offset. Neither
      probe reserves its port after this function returns.

    # Failures

    Raises `SystemExit` if none of the `PORT_FALLBACK_ATTEMPTS` pairs can be
    bound at probe time. A later process may still win either port.
    """

    for offset in range(PORT_FALLBACK_ATTEMPTS):
        next_backend_port = backend_port + offset
        next_frontend_port = frontend_port + offset
        if (
            next_backend_port != next_frontend_port
            and can_bind_port(next_backend_port)
            and can_bind_port(next_frontend_port)
        ):
            return next_backend_port, next_frontend_port

    raise SystemExit(
        "Could not find an available backend/frontend port pair. "
        "Stop an existing dirdiff process or pass explicit ports."
    )


def require_marked_repos(db_path: Path, *, mode: InstallationMode) -> None:
    """Require at least one marked repository before launching the app.

    The browser UI needs a repo catalog immediately.  Failing here gives the
    user the exact `dirdiff mark` command instead of starting a mostly-empty
    server that would fail later through API calls.

    # Parameters

    - `db_path`: Exact registry path the server will open.
    - `mode`: Validated installation mode used to identify an implicit path.

    # Usage

    `run_app` calls this before selecting ports or starting child processes.
    Pass the same database path stored in `RuntimeConfig` and its validated
    installation mode so the preflight and server read one repository registry
    and the implicit-path diagnostic stays accurate.

    # Failures

    Raises `SystemExit` when the registry contains no active marks. Database
    open and query errors propagate unchanged.
    """

    engine = open_sqlite_engine(db_path, migration_config_path(mode))
    marks = RepoMarkStore(engine).list()
    if len(marks) > 0:
        return
    if db_path == default_db_path(mode):
        raise SystemExit("No marked repos. Run: dirdiff mark /path/to/repo")
    raise SystemExit(
        f"No marked repos. Run: dirdiff mark /path/to/repo --db-path {db_path}"
    )


def choose_runtime_ports(
    *,
    backend_port: int,
    frontend_port: int,
    use_frontend_dev: bool,
) -> tuple[int, int]:
    """Choose ports for the selected frontend mode.

    Vite mode can shift both ports to a free pair.  Backend-only mode keeps the
    requested backend port exact because there is no second local server whose
    URL needs to be coordinated.

    # Parameters

    - `backend_port`: Backend port requested by the command.
    - `frontend_port`: Vite port requested by the command.
    - `use_frontend_dev`: Whether both servers need a free coordinated pair.

    The function prints only when Vite mode shifts the pair. Port failures
    terminate the command through `SystemExit`.

    # Usage

    `run_app` calls this once before building either browser URL. Vite mode must
    use both returned values; backend-only mode may ignore the unchanged
    frontend value.

    # Returns

    - First, the backend port. Vite mode may shift it; backend-only mode returns
      the requested value after proving it can bind.
    - Second, the frontend port. Vite mode shifts it by the same offset as the
      backend; backend-only mode returns it unchanged without probing it.

    # Failures

    Backend-only mode propagates `require_bindable_port` failure. Vite mode
    propagates `choose_port_pair` exhaustion. Neither successful path reserves
    a socket.
    """

    if use_frontend_dev:
        backend, frontend = choose_port_pair(
            backend_port,
            frontend_port,
        )
        if backend != backend_port or frontend != frontend_port:
            print(
                "Requested ports are in use; "
                f"using backend {backend} and frontend {frontend}.",
                file=sys.stderr,
            )
        return backend, frontend
    require_bindable_port(backend_port, label="Backend")
    return backend_port, frontend_port


def open_browser(url: str) -> None:
    """Open `url` shortly after startup without blocking uvicorn launch.

    The timer gives uvicorn and, when enabled, Vite a moment to bind their
    sockets before the user's browser tries to load the app.

    # Usage

    `run_app` calls this after printing the selected URL and before entering
    uvicorn. Headless startup skips the call. The function schedules one timer
    that waits one second, invokes the system browser, and exits. The caller
    cannot cancel it.

    # Failures

    Browser-launch refusal or an exception happens on the timer thread and is
    not reported to the caller. The printed URL remains available for manual
    opening.
    """

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


def start_frontend(
    *,
    use_frontend_dev: bool,
    backend_port: int,
    frontend_port: int,
    backend_url: str,
    frontend_url: str,
) -> tuple[subprocess.Popen[bytes] | None, str]:
    """Start the default Vite frontend process and return the URL to show.

    Backend-only mode is the explicit opt-out. If Vite is disabled or its
    executable is missing, the result contains no process and selects the
    backend URL. The returned process is owned by `run_app` and terminated when
    uvicorn exits.

    # Parameters

    - `use_frontend_dev`: Whether to attempt starting Vite.
    - `backend_port`: Selected backend port exported to Vite.
    - `frontend_port`: Selected exact Vite port.
    - `backend_url`: URL returned when Vite is disabled or unavailable.
    - `frontend_url`: URL returned after the Vite child starts.

    # Usage

    `run_app` calls this after choosing ports and building both URLs. If the
    returned process is present, the caller must terminate it after uvicorn
    exits. The returned URL always matches the process path selected here.

    # Returns

    - First, the running Vite child when startup succeeded.
    - `None`: Vite was disabled or `bun` was unavailable, so no child needs
      later termination.
    - Second, `frontend_url` when the child is present, otherwise `backend_url`.

    # Failures

    Missing `bun` is handled by printing a diagnostic and selecting the backend
    URL. Other process-launch errors propagate and abort startup.
    """

    if not use_frontend_dev:
        return None, backend_url
    try:
        frontend_process = start_frontend_dev_server(
            backend_port=backend_port,
            frontend_port=frontend_port,
        )
    except FileNotFoundError:
        print(
            "Could not start the Vite frontend dev server. Opening the backend diagnostic page instead.",
            file=sys.stderr,
        )
        return None, backend_url
    return frontend_process, frontend_url


def run_uvicorn(
    *, config: RuntimeConfig, port: int, mode: InstallationMode
) -> None:
    """Run the FastAPI app through uvicorn's factory entrypoint.

    The config is serialized through `RUNTIME_CONFIG_ENV` because uvicorn
    imports the selected `dirdiff.server` factory independently of the Typer
    command invocation. Keeping that handoff here avoids global mutable server
    state while preserving development reload support.

    # Parameters

    - `config`: Complete runtime config serialized for the factory process.
    - `port`: Selected backend loopback port uvicorn must bind.
    - `mode`: Validated topology selecting the factory and reload behavior.

    The call blocks until uvicorn exits and leaves the serialized config in the
    process environment for reload children.

    # Usage

    `run_app` calls this after mark validation, port selection, URL printing,
    and optional Vite startup. No launch work follows until uvicorn returns.

    # Failures

    Import, configuration serialization, port binding, and uvicorn startup
    errors propagate to `run_app`. The caller remains responsible for cleaning
    up resources started before this call.
    """

    # Keep uvicorn out of normal CLI import cost; this path is only reached
    # once the command is ready to hand control to the backend server process.
    import uvicorn  # noqa: PLC0415

    os.environ[RUNTIME_CONFIG_ENV] = json.dumps(asdict(config))
    development = mode == "development"
    uvicorn.run(
        (
            "dirdiff.server:development_uvicorn_entrypoint"
            if development
            else "dirdiff.server:release_uvicorn_entrypoint"
        ),
        host="127.0.0.1",
        port=port,
        factory=True,
        reload=development,
        reload_dirs=[str(BACKEND_RELOAD_DIR)] if development else None,
        reload_includes=["*.py"] if development else None,
    )


def run_app(
    *,
    config: RuntimeConfig,
    port: int,
    frontend_port: int,
    headless: bool,
    no_frontend_dev: bool,
    mode: InstallationMode,
) -> None:
    """Launch one complete local dirdiff app session.

    This is the CLI orchestration boundary: verify repo marks, choose ports,
    start Vite unless backend-only mode was requested, print the browser URL,
    open the browser unless headless, and finally run uvicorn until the process
    exits.

    # Parameters

    - `config`: Complete backend and initial Tab configuration.
    - `port`: Requested backend loopback port.
    - `frontend_port`: Requested Vite loopback port.
    - `headless`: Whether to skip scheduling browser opening.
    - `no_frontend_dev`: Whether to omit Vite and use the backend URL.
    - `mode`: Validated installation topology controlling the launch sequence.

    At least one repository mark is required. Development starts Vite unless
    disabled and terminates that child when uvicorn returns or raises. Release
    starts only uvicorn and rejects development-only frontend options.

    # Usage

    The Typer handlers construct a complete `RuntimeConfig` and call this once
    per CLI invocation. `config.db_path` must name the same registry selected by
    the command. The function blocks in uvicorn until the app shuts down.

    # Failures

    Missing marks, unavailable ports, Vite launch errors other than missing
    `bun`, and uvicorn startup failures propagate. A started Vite child is still
    terminated in the `finally` block.
    """

    if mode == "release":
        if no_frontend_dev:
            raise SystemExit(
                "--no-frontend-dev is unavailable in a release installation."
            )
        if frontend_port != DEFAULT_FRONTEND_PORT:
            raise SystemExit(
                "--frontend-port is unavailable in a release installation."
            )
        require_marked_repos(Path(config.db_path), mode=mode)
        require_bindable_port(port, label="Server")
        url = build_url(port, config)
        print(f"dirdiff: {url}", file=sys.stderr)
        if not headless:
            open_browser(url)
        run_uvicorn(config=config, port=port, mode=mode)
        return

    use_frontend_dev = not no_frontend_dev

    require_marked_repos(Path(config.db_path), mode=mode)
    backend_port, frontend_port = choose_runtime_ports(
        backend_port=port,
        frontend_port=frontend_port,
        use_frontend_dev=use_frontend_dev,
    )
    backend_url = build_url(backend_port, config)
    frontend_url = build_url(frontend_port, config)
    frontend_process, url = start_frontend(
        use_frontend_dev=use_frontend_dev,
        backend_port=backend_port,
        frontend_port=frontend_port,
        backend_url=backend_url,
        frontend_url=frontend_url,
    )

    print(f"dirdiff: {url}", file=sys.stderr)
    if not headless:
        open_browser(url)
    try:
        run_uvicorn(config=config, port=backend_port, mode=mode)
    finally:
        if frontend_process is not None:
            frontend_process.terminate()
