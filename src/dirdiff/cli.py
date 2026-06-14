from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from dirdiff.diff import (
    DifftasticDiffService,
    GitDiffService,
    GitRepository,
    TextDiffService,
)
from dirdiff.server import create_app
from typing import Any
import uvicorn


DEFAULT_PORT = 5052
DEFAULT_FRONTEND_PORT = 5173
PORT_FALLBACK_ATTEMPTS = 20
RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"


@dataclass(frozen=True)
class RuntimeConfig:
    left: str = "index"
    right: str = "worktree"
    base_branch: str | None = None
    review_branch: str | None = None
    repo_root: str | None = None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO
        if os.environ.get("DIRDIFF_DEBUG_PERF") == "1"
        else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone diff viewer for generic text files."
    )
    parser.add_argument("--left", default="index", help="Left diff side or Git ref")
    parser.add_argument(
        "--right", default="worktree", help="Right diff side or Git ref"
    )
    parser.add_argument(
        "--base-branch",
        help="Base branch for branch-review mode (defaults to master/main when available)",
    )
    parser.add_argument(
        "--review-branch",
        "--branch",
        dest="review_branch",
        help="Branch to review against the merge-base with the base branch",
    )
    parser.add_argument(
        "--repo-root",
        help="Optional Git repo root to use for repo-backed diffs",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Local web server port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not automatically open the browser on startup.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Start the local server without opening a browser tab.",
    )
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=DEFAULT_FRONTEND_PORT,
        help=f"Vite frontend dev server port (default: {DEFAULT_FRONTEND_PORT})",
    )
    parser.add_argument(
        "--no-frontend-dev",
        action="store_true",
        help="Do not start Vite; serve only the backend API and diagnostic page.",
    )
    return parser.parse_args()


def _build_url(port: int, defaults: dict[str, Any]) -> str:
    query = {
        key: value
        for key, value in defaults.items()
        if value
        and key
        in {
            "mode",
            "left",
            "right",
            "base_branch",
            "review_branch",
        }
    }
    return f"http://127.0.0.1:{port}/?{urlencode(query, quote_via=quote)}"


def _frontend_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend"


def _start_frontend_dev_server(
    *,
    backend_port: int,
    frontend_port: int,
) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["VITE_DIRDIFF_BACKEND_ORIGIN"] = f"http://127.0.0.1:{backend_port}"
    return subprocess.Popen(
        [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(frontend_port),
            "--strictPort",
        ],
        cwd=_frontend_dir(),
        env=env,
    )


def build_defaults(
    service: TextDiffService,
    *,
    left: str = "index",
    right: str = "worktree",
    base_branch: str | None = None,
    review_branch: str | None = None,
) -> dict[str, Any]:
    default_base_branch = service.default_base_branch()
    preferred_review_branch = service.preferred_review_branch(
        base_branch=default_base_branch
    )
    initial_mode = "files"
    if review_branch:
        initial_mode = "branch-review"
    elif left != "index" or right != "worktree":
        initial_mode = "refs"

    return {
        "engine": "dirdiff",
        "mode": initial_mode,
        "left": left,
        "right": right,
        "base_branch": base_branch or default_base_branch,
        "review_branch": review_branch or preferred_review_branch,
        "ref_choices": service.list_ref_choices(),
        "repo_available": bool(service.repo_root),
    }


def runtime_config_from_args(args: argparse.Namespace) -> RuntimeConfig:
    return RuntimeConfig(
        left=args.left,
        right=args.right,
        base_branch=args.base_branch,
        review_branch=args.review_branch,
        repo_root=args.repo_root,
    )


def store_runtime_config(config: RuntimeConfig) -> None:
    os.environ[RUNTIME_CONFIG_ENV] = json.dumps(asdict(config))


def load_runtime_config() -> RuntimeConfig:
    payload = os.environ.get(RUNTIME_CONFIG_ENV)
    if not payload:
        return RuntimeConfig()
    return RuntimeConfig(**json.loads(payload))


def create_app_from_runtime_config() -> Any:
    config = load_runtime_config()
    repo_root = Path(config.repo_root).expanduser() if config.repo_root else None
    repo = GitRepository.discover(repo_root=repo_root)
    service = TextDiffService(repo)
    git_service = GitDiffService(repo)
    difftastic_service = DifftasticDiffService(repo)
    defaults = build_defaults(
        service,
        left=config.left,
        right=config.right,
        base_branch=config.base_branch,
        review_branch=config.review_branch,
    )
    return create_app(
        service,
        defaults,
        services={"git": git_service, "difftastic": difftastic_service},
    )


def ensure_port_available(port: int, *, label: str) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SystemExit(
            f"{label} port {port} is already in use. "
            "Stop the existing dirdiff process or pass an explicit port."
        ) from exc


def port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def choose_port_pair(backend_port: int, frontend_port: int) -> tuple[int, int]:
    for offset in range(PORT_FALLBACK_ATTEMPTS):
        next_backend_port = backend_port + offset
        next_frontend_port = frontend_port + offset
        if (
            next_backend_port != next_frontend_port
            and port_available(next_backend_port)
            and port_available(next_frontend_port)
        ):
            return next_backend_port, next_frontend_port

    raise SystemExit(
        "Could not find an available backend/frontend port pair. "
        "Stop an existing dirdiff process or pass explicit ports."
    )


def main() -> None:
    configure_logging()
    args = parse_args()
    config = runtime_config_from_args(args)
    repo_root = Path(config.repo_root).expanduser() if config.repo_root else None
    repo = GitRepository.discover(repo_root=repo_root)
    service = TextDiffService(repo)
    defaults = build_defaults(
        service,
        left=config.left,
        right=config.right,
        base_branch=config.base_branch,
        review_branch=config.review_branch,
    )

    use_frontend_dev = not args.no_frontend_dev
    if use_frontend_dev:
        actual_port, actual_frontend_port = choose_port_pair(
            args.port,
            args.frontend_port,
        )
    else:
        ensure_port_available(args.port, label="Backend")
        actual_port = args.port
        actual_frontend_port = args.frontend_port

    backend_url = _build_url(actual_port, defaults)
    url = (
        _build_url(actual_frontend_port, defaults) if use_frontend_dev else backend_url
    )
    if use_frontend_dev and (
        actual_port != args.port or actual_frontend_port != args.frontend_port
    ):
        print(
            "Requested ports are in use; "
            f"using backend {actual_port} and frontend {actual_frontend_port}.",
            file=sys.stderr,
        )

    frontend_process: subprocess.Popen[bytes] | None = None
    if use_frontend_dev:
        try:
            frontend_process = _start_frontend_dev_server(
                backend_port=actual_port,
                frontend_port=actual_frontend_port,
            )
        except FileNotFoundError:
            print(
                "Could not start the Vite frontend dev server. Opening the backend diagnostic page instead.",
                file=sys.stderr,
            )
            url = backend_url

    print(f"dirdiff: {url}", file=sys.stderr)

    if not (args.no_open_browser or args.headless):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    store_runtime_config(config)

    try:
        uvicorn.run(
            "dirdiff.cli:create_app_from_runtime_config",
            host="127.0.0.1",
            port=actual_port,
            factory=True,
            reload=True,
            reload_dirs=[str(Path(__file__).resolve().parent)],
            reload_includes=["*.py", "*.html", "*.js", "*.css"],
            log_config=None,
            access_log=False,
        )
    finally:
        if frontend_process is not None:
            frontend_process.terminate()
