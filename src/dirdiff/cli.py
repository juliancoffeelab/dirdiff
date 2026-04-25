from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlencode

from dirdiff.diff import TextDiffService
from dirdiff.server import DiffViewerServer


DEFAULT_PORT = 5052


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone diff viewer for generic text files."
    )
    parser.add_argument("--path", help="Repo-relative file path for Git-backed diff mode")
    parser.add_argument("--left", default="index", help="Left diff side or Git ref")
    parser.add_argument("--right", default="worktree", help="Right diff side or Git ref")
    parser.add_argument("--left-file", help="Left filesystem path for direct file mode")
    parser.add_argument("--right-file", help="Right filesystem path for direct file mode")
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
    return parser.parse_args()


def _build_url(port: int, defaults: dict[str, str]) -> str:
    query = {
        key: value
        for key, value in defaults.items()
        if value and key in {"path", "left", "right", "left_file", "right_file"}
    }
    return f"http://127.0.0.1:{port}/?{urlencode(query, quote_via=quote)}"


def build_defaults(service: TextDiffService, args: argparse.Namespace) -> dict[str, str]:
    return {
        "path": args.path or "",
        "left": args.left,
        "right": args.right,
        "left_file": args.left_file or "",
        "right_file": args.right_file or "",
        "repo_available": bool(service.repo_root),
    }


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).expanduser() if args.repo_root else None
    service = TextDiffService.discover(repo_root=repo_root)
    defaults = build_defaults(service, args)

    server = DiffViewerServer(("127.0.0.1", args.port), service, defaults)
    url = _build_url(args.port, defaults)
    if not args.no_open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
