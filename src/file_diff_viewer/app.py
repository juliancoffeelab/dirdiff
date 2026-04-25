from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from file_diff_viewer.diff_logic import TextDiffError, TextDiffService


DEFAULT_PORT = 5052

STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


class DiffViewerServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        service: TextDiffService,
        defaults: dict[str, str],
    ) -> None:
        super().__init__(server_address, DiffRequestHandler)
        self.service = service
        self.defaults = defaults


class DiffRequestHandler(BaseHTTPRequestHandler):
    server: DiffViewerServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_index()
            return
        if parsed.path == "/api/diff":
            self._serve_diff(parsed.query)
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _serve_index(self) -> None:
        template_path = files("file_diff_viewer").joinpath("templates/index.html")
        html = template_path.read_text(encoding="utf-8").replace(
            "__DEFAULTS_JSON__",
            json.dumps(self.server.defaults),
        )
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, relative_path: str) -> None:
        asset_path = files("file_diff_viewer").joinpath("static", relative_path)
        if not asset_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        body = asset_path.read_bytes()
        suffix = Path(relative_path).suffix
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            STATIC_CONTENT_TYPES.get(suffix, "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_diff(self, query_string: str) -> None:
        query = parse_qs(query_string)
        try:
            payload = self.server.service.build_diff(
                path=_first(query, "path"),
                left=_first(query, "left", self.server.defaults["left"]),
                right=_first(query, "right", self.server.defaults["right"]),
                left_file=_first(query, "left_file"),
                right_file=_first(query, "right_file"),
            )
        except TextDiffError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        self._send_json(payload)

    def _send_json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _first(values: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    bucket = values.get(key)
    if not bucket:
        return default
    value = bucket[0].strip()
    return value or default


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


def _default_path_for(service: TextDiffService, args: argparse.Namespace) -> str:
    if args.path:
        return args.path
    if args.left_file or args.right_file:
        return ""
    if service.repo_root is None:
        return ""
    try:
        return service.discover_default_path()
    except TextDiffError:
        return ""


def _build_url(port: int, defaults: dict[str, str]) -> str:
    query = {
        key: value
        for key, value in defaults.items()
        if value and key in {"path", "left", "right", "left_file", "right_file"}
    }
    return f"http://127.0.0.1:{port}/?{urlencode(query, quote_via=quote)}"


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).expanduser() if args.repo_root else None
    service = TextDiffService.discover(repo_root=repo_root)
    defaults = {
        "path": _default_path_for(service, args),
        "left": args.left,
        "right": args.right,
        "left_file": args.left_file or "",
        "right_file": args.right_file or "",
    }

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


if __name__ == "__main__":
    main()
