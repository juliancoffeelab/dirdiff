from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dirdiff.diff import TextDiffError, TextDiffService


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
        template_path = files("dirdiff").joinpath("templates/index.html")
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
        asset_path = files("dirdiff").joinpath("static", relative_path)
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
        mode = _first(query, "mode", self.server.defaults.get("mode"))
        base_branch = (
            _first(query, "base_branch", self.server.defaults.get("base_branch"))
            if mode == "branch-review"
            else None
        )
        branch = (
            _first(query, "branch", self.server.defaults.get("branch"))
            if mode == "branch-review"
            else None
        )
        try:
            payload = self.server.service.build_diff(
                left=_first(query, "left", self.server.defaults["left"]),
                right=_first(query, "right", self.server.defaults["right"]),
                base_branch=base_branch,
                branch=branch,
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
