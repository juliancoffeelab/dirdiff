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

from dirdiff.runtime import DEFAULT_DB_PATH, RUNTIME_CONFIG_ENV, RuntimeConfig

DEFAULT_PORT = 5052
DEFAULT_FRONTEND_PORT = 5173
PORT_FALLBACK_ATTEMPTS = 20
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@dataclass(frozen=True)
class AppOptions:
    db_path: Path
    presets_root: str | None
    port: int
    frontend_port: int
    headless: bool
    no_frontend_dev: bool


def build_url(port: int, config: RuntimeConfig) -> str:
    query = {
        key: value
        for key, value in {
            "mode": config.mode,
            "left": config.left,
            "right": config.right,
            "base_branch": config.base_branch,
            "review_branch": config.review_branch,
        }.items()
        if value
    }
    return f"http://127.0.0.1:{port}/?{urlencode(query, quote_via=quote)}"


def start_frontend_dev_server(
    *,
    backend_port: int,
    frontend_port: int,
) -> subprocess.Popen[bytes]:
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
    from dirdiff.repo_registry import RepoMarkStore  # noqa: PLC0415

    marks = RepoMarkStore.open(db_path).list()
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
        print(
            "Could not start the Vite frontend dev server. Opening the backend diagnostic page instead.",
            file=sys.stderr,
        )
        return None, backend_url
    return frontend_process, frontend_url


def run_uvicorn(*, config: RuntimeConfig, port: int) -> None:
    import uvicorn  # noqa: PLC0415

    os.environ[RUNTIME_CONFIG_ENV] = json.dumps(asdict(config))
    uvicorn.run(
        "dirdiff.server:uvicorn_entrypoint",
        host="127.0.0.1",
        port=port,
        factory=True,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parent)],
        reload_includes=["*.py", "*.html", "*.js", "*.css"],
        log_config=None,
        access_log=False,
    )


def run_app(
    *,
    config: RuntimeConfig,
    port: int,
    frontend_port: int,
    headless: bool,
    no_frontend_dev: bool,
) -> None:
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
