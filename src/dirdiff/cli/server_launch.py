"""CLI-side launch orchestration for the local browser app.

This module owns process startup concerns: port probing, Vite startup by
default, the backend-only opt-out path, browser opening, repo-mark preflight
checks, and the uvicorn factory handoff.  It does not define REST endpoints or
diff behavior; those remain in ``dirdiff.server`` and the engine/rendering
modules.
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

from dirdiff.cli.marker_utils import DEFAULT_DB_PATH
from dirdiff.db.base import open_sqlite_engine
from dirdiff.db.repo_registry import RepoMarkStore
from dirdiff.server import RUNTIME_CONFIG_ENV, RuntimeConfig
from dirdiff.sources import BranchSelection

DEFAULT_PORT = 5052
DEFAULT_FRONTEND_PORT = 5173
PORT_FALLBACK_ATTEMPTS = 20
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
BACKEND_RELOAD_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AppOptions:
    """CLI options shared between the root callback and subcommands.

    Typer stores this value on ``ctx.obj`` so commands such as ``refs`` and
    ``branch`` can reuse the database, port, preset, and browser-launch options
    parsed by the root callback.
    """

    db_path: Path
    """
    Repo registry database path selected by the CLI.
    """

    presets_root: str | None
    """
    Optional preset root passed through to the server startup config.
    """

    port: int
    """
    Requested backend port.
    """

    frontend_port: int
    """
    Requested Vite frontend port.
    """

    headless: bool
    """
    Whether the CLI should skip opening a browser window.
    """

    no_frontend_dev: bool
    """
    Whether to serve only the backend diagnostic/static route.
    """


def _add_branch_selection_query(
    query: dict[str, str],
    *,
    prefix: str,
    selection: BranchSelection,
) -> None:
    """Encode one startup branch selection into frontend URL params.

    ``build_url`` uses this for CLI-provided branch-review state.  It mirrors
    the API query contract: source and branch are always present, while remote
    exists only on the remote variant.
    """
    query[f"{prefix}_source"] = selection["source"]
    query[f"{prefix}_branch"] = selection["branch"]
    if selection["source"] == "remote":
        query[f"{prefix}_remote"] = selection["remote"]


def build_url(port: int, config: RuntimeConfig) -> str:
    """Build the browser URL that matches the requested startup state.

    The backend is still capable of serving all modes after startup.  This URL
    only tells the frontend which mode and refs to select on first load.
    """

    query = {
        key: value
        for key, value in {
            "mode": config.mode,
            "left": config.left,
            "right": config.right,
        }.items()
        if value
    }
    if config.mode == "branch-review" and config.base_selection is not None:
        _add_branch_selection_query(
            query,
            prefix="base",
            selection=config.base_selection,
        )
    if config.mode == "branch-review" and config.review_selection is not None:
        _add_branch_selection_query(
            query,
            prefix="review",
            selection=config.review_selection,
        )
    return f"http://127.0.0.1:{port}/?{urlencode(query, quote_via=quote)}"


def start_frontend_dev_server(
    *,
    backend_port: int,
    frontend_port: int,
) -> subprocess.Popen[bytes]:
    """Start Vite with a backend-origin override for this dirdiff process.

    The backend and frontend ports are selected together before this function
    runs.  Vite is launched with ``--strictPort`` so the browser URL printed by
    the CLI cannot silently point at a different process.
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
    """Return whether localhost can bind ``port`` right now.

    The check opens and immediately closes a loopback socket.  It is only a
    launch-time probe; another process can still race us before uvicorn or Vite
    binds the selected port.
    """

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def require_bindable_port(port: int, *, label: str) -> None:
    """Exit with a user-facing message if ``port`` is already occupied.

    Backend-only mode does not have a frontend port to shift in tandem, so the
    requested backend port must be available exactly or the CLI should stop with
    a clear command-line error.
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
    use, both ports advance by the same offset so the chosen pair stays easy to
    understand in logs and browser URLs.
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


def require_marked_repos(db_path: Path) -> None:
    """Require at least one marked repository before launching the app.

    The browser UI needs a repo catalog immediately.  Failing here gives the
    user the exact ``dirdiff mark`` command instead of starting a mostly-empty
    server that would fail later through API calls.
    """

    engine = open_sqlite_engine(db_path)
    marks = RepoMarkStore(engine).list()
    if len(marks) > 0:
        return
    if db_path == DEFAULT_DB_PATH:
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
    """Open ``url`` shortly after startup without blocking uvicorn launch.

    The timer gives uvicorn and, when enabled, Vite a moment to bind their
    sockets before the user's browser tries to load the app.
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

    Backend-only mode is the explicit opt-out.  If Vite is disabled or cannot
    be started, the CLI falls back to the backend URL.  The returned process is
    owned by ``run_app`` and terminated when uvicorn exits.
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


def run_uvicorn(*, config: RuntimeConfig, port: int) -> None:
    """Run the FastAPI app through uvicorn's factory entrypoint.

    The config is serialized through ``RUNTIME_CONFIG_ENV`` because uvicorn
    imports ``dirdiff.server:uvicorn_entrypoint`` independently of the Typer
    command invocation.  Keeping that handoff here avoids global mutable server
    state while preserving reload support.
    """

    import uvicorn  # noqa: PLC0415

    os.environ[RUNTIME_CONFIG_ENV] = json.dumps(asdict(config))
    uvicorn.run(
        "dirdiff.server:uvicorn_entrypoint",
        host="127.0.0.1",
        port=port,
        factory=True,
        reload=True,
        reload_dirs=[str(BACKEND_RELOAD_DIR)],
        reload_includes=["*.py"],
    )


def run_app(
    *,
    config: RuntimeConfig,
    port: int,
    frontend_port: int,
    headless: bool,
    no_frontend_dev: bool,
) -> None:
    """Launch one complete local dirdiff app session.

    This is the CLI orchestration boundary: verify repo marks, choose ports,
    start Vite unless backend-only mode was requested, print the browser URL,
    open the browser unless headless, and finally run uvicorn until the process
    exits.
    """

    use_frontend_dev = not no_frontend_dev

    require_marked_repos(Path(config.db_path))
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
        run_uvicorn(config=config, port=backend_port)
    finally:
        if frontend_process is not None:
            frontend_process.terminate()
