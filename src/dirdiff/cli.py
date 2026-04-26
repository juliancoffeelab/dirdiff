from __future__ import annotations

import argparse
import errno
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlencode

from dirdiff.diff import TextDiffService
from dirdiff.server import DiffViewerServer
from typing import Any


DEFAULT_PORT = 5052
PORT_FALLBACK_ATTEMPTS = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone diff viewer for generic text files."
    )
    parser.add_argument("--left", default="index", help="Left diff side or Git ref")
    parser.add_argument("--right", default="worktree", help="Right diff side or Git ref")
    parser.add_argument(
        "--base-branch",
        help="Base branch for branch-review mode (defaults to master/main when available)",
    )
    parser.add_argument(
        "--branch",
        help="Branch to compare against the merge-base with the base branch",
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
            "branch",
        }
    }
    return f"http://127.0.0.1:{port}/?{urlencode(query, quote_via=quote)}"


def build_defaults(service: TextDiffService, args: argparse.Namespace) -> dict[str, Any]:
    default_base_branch = service.default_base_branch()
    review_branch = service.preferred_review_branch(base_branch=default_base_branch)
    initial_mode = "files"
    if args.branch:
        initial_mode = "branch-review"
    elif args.left != "index" or args.right != "worktree":
        initial_mode = "refs"

    return {
        "mode": initial_mode,
        "left": args.left,
        "right": args.right,
        "base_branch": args.base_branch or default_base_branch,
        "branch": args.branch or review_branch,
        "ref_choices": service.list_ref_choices(),
        "repo_available": bool(service.repo_root),
    }


def create_server(
    requested_port: int,
    service: TextDiffService,
    defaults: dict[str, str],
) -> DiffViewerServer:
    last_error: OSError | None = None

    for port in range(requested_port, requested_port + PORT_FALLBACK_ATTEMPTS):
        try:
            return DiffViewerServer(("127.0.0.1", port), service, defaults)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_error = exc

    assert last_error is not None
    raise last_error


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).expanduser() if args.repo_root else None
    service = TextDiffService.discover(repo_root=repo_root)
    defaults = build_defaults(service, args)

    server = create_server(args.port, service, defaults)
    actual_port = server.server_address[1]
    url = _build_url(actual_port, defaults)
    if actual_port != args.port:
        print(
            f"Port {args.port} is in use; using {actual_port} instead.",
            file=sys.stderr,
        )
    if not (args.no_open_browser or args.headless):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
