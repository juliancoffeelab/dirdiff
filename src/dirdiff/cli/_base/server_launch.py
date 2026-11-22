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
from typing import Literal
from urllib.parse import quote, urlencode

from dirdiff.change_source import BranchSelection
from dirdiff.db import RepoMarkStore, open_sqlite_engine
from dirdiff.server import RUNTIME_CONFIG_ENV, RuntimeConfig

DEFAULT_PORT = 5052
DEFAULT_FRONTEND_PORT = 5173
# Offsets 0..19 are tried, so the last candidate is requested port + 19.
PORT_FALLBACK_ATTEMPTS = 20
FRONTEND_DIR = Path(__file__).resolve().parents[4] / "frontend"
# Watched by uvicorn reload with a *.py-only pattern so frontend edits do not
# restart the backend.
BACKEND_RELOAD_DIR = Path(__file__).resolve().parents[2]
# The app upgrades the database when it opens it, so restarting on a new
# migration applies it to the running server.
MIGRATION_RELOAD_DIR = (
    Path(__file__).resolve().parents[4] / "alembic" / "versions"
)

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
    db_path: Path
    store_path: Path | None
    presets_root: str
    port: int
    frontend_port: int
    headless: bool
    no_frontend_dev: bool
    mode: Literal["development", "release"]


def build_url(port: int, config: RuntimeConfig) -> str:
    root = f"http://127.0.0.1:{port}/"
    if config.tab == "head":
        return root

    query = {
        "tab": config.tab,
        "engine": "tokendiff",
        "view": "inline",
    }

    def add_branch_selection(prefix: str, selection: BranchSelection) -> None:
        query[f"{prefix}_source"] = selection["source"]
        query[f"{prefix}_branch"] = selection["branch"]
        if selection["source"] == "remote":
            query[f"{prefix}_remote"] = selection["remote"]

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
        add_branch_selection("base", config.base_selection)
        add_branch_selection("review", config.review_selection)
    return f"{root}?{urlencode(query, quote_via=quote)}"


def start_frontend_dev_server(
    *,
    backend_port: int,
    frontend_port: int,
) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["VITE_DIRDIFF_BACKEND_ORIGIN"] = f"http://127.0.0.1:{backend_port}"
    # --strictPort: Vite must not silently move, or the printed URL would point
    # at another process. Caller owns the child and must terminate it.
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
    # Non-reserving probe: another process can still take the port before the
    # server binds. Any bind error counts as unavailable.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def require_bindable_port(port: int, *, label: str) -> None:
    if can_bind_port(port):
        return
    raise SystemExit(
        f"{label} port {port} is already in use. "
        "Stop the existing dirdiff process or pass an explicit port."
    )


def choose_port_pair(backend_port: int, frontend_port: int) -> tuple[int, int]:
    # Both ports shift by the same offset so their requested relationship holds.
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


def require_marked_repos(
    db_path: Path,
    *,
    migration_config_path: Path,
    default_db_path: Path,
) -> None:
    engine = open_sqlite_engine(db_path, migration_config_path)
    marks = RepoMarkStore(engine).list()
    if len(marks) > 0:
        return
    if db_path == default_db_path:
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
    for offset in range(PORT_FALLBACK_ATTEMPTS):
        selected_backend_port = backend_port + offset
        if can_bind_port(selected_backend_port):
            if selected_backend_port != backend_port:
                print(
                    f"Requested port is in use; using {selected_backend_port}.",
                    file=sys.stderr,
                )
            return selected_backend_port, frontend_port

    raise SystemExit(
        "Could not find an available backend port. "
        "Stop an existing dirdiff process or pass an explicit port."
    )


def open_browser(url: str) -> None:
    # Delay so uvicorn and Vite bind first; failures surface on the timer
    # thread and are not reported. The printed URL remains for manual opening.
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


def start_frontend(
    *,
    use_frontend_dev: bool,
    backend_port: int,
    frontend_port: int,
    backend_url: str,
    frontend_url: str,
) -> tuple[subprocess.Popen[bytes] | None, str]:
    if not use_frontend_dev:
        return None, backend_url
    try:
        frontend_process = start_frontend_dev_server(
            backend_port=backend_port,
            frontend_port=frontend_port,
        )
    except FileNotFoundError:
        # Missing `bun` only; other launch errors propagate.
        print(
            "Could not start the Vite frontend dev server. Opening the backend diagnostic page instead.",
            file=sys.stderr,
        )
        return None, backend_url
    return frontend_process, frontend_url


def run_uvicorn(
    *,
    config: RuntimeConfig,
    port: int,
    mode: Literal["development", "release"],
) -> None:
    # Keep uvicorn out of normal CLI import cost; this path is only reached
    # once the command is ready to hand control to the backend server process.
    import uvicorn  # noqa: PLC0415

    # uvicorn imports the factory independently of this command, so the config
    # travels through the environment; reload children rely on it staying set.
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
        reload_dirs=(
            [str(BACKEND_RELOAD_DIR), str(MIGRATION_RELOAD_DIR)]
            if development
            else None
        ),
        reload_includes=["*.py"] if development else None,
        # An agent `wait` holds its request for up to nine minutes, and
        # uvicorn otherwise finishes every open request before a stop or a
        # reload completes; a second Ctrl+C does not help, because uvicorn
        # still awaits `Server.wait_closed()`, which waits for every open
        # connection. The limit is server-wide: any request still open
        # after five seconds is cancelled and answered with HTTP 500, slow
        # ones such as preparing a pull request included. Only the response
        # ends; a synchronous handler's worker thread runs on.
        timeout_graceful_shutdown=5,
    )


def run_app(
    *,
    config: RuntimeConfig,
    port: int,
    frontend_port: int,
    headless: bool,
    no_frontend_dev: bool,
    mode: Literal["development", "release"],
    default_db_path: Path,
) -> None:
    if mode == "release":
        if no_frontend_dev:
            raise SystemExit(
                "--no-frontend-dev is unavailable in a release installation."
            )
        if frontend_port != DEFAULT_FRONTEND_PORT:
            raise SystemExit(
                "--frontend-port is unavailable in a release installation."
            )
    require_marked_repos(
        Path(config.db_path),
        migration_config_path=Path(config.migration_config_path),
        default_db_path=default_db_path,
    )
    if mode == "release":
        _run_release(config=config, port=port, headless=headless)
    else:
        _run_development(
            config=config,
            port=port,
            frontend_port=frontend_port,
            headless=headless,
            use_frontend_dev=not no_frontend_dev,
        )


def _run_release(*, config: RuntimeConfig, port: int, headless: bool) -> None:
    port, _ = choose_runtime_ports(
        backend_port=port,
        frontend_port=DEFAULT_FRONTEND_PORT,
        use_frontend_dev=False,
    )
    url = build_url(port, config)
    print(f"dirdiff: {url}", file=sys.stderr)
    if not headless:
        open_browser(url)
    run_uvicorn(config=config, port=port, mode="release")


def _run_development(
    *,
    config: RuntimeConfig,
    port: int,
    frontend_port: int,
    headless: bool,
    use_frontend_dev: bool,
) -> None:
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
        run_uvicorn(config=config, port=backend_port, mode="development")
    finally:
        # Vite is this process's child; it must not outlive the backend.
        if frontend_process is not None:
            frontend_process.terminate()
