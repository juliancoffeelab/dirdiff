from __future__ import annotations

import json
import logging
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
LARGE_DIFF_FILE_LIMIT = 10
LOGGER = logging.getLogger(__name__)


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
        if parsed.path == "/api/file-diff":
            self._serve_file_diff(parsed.query)
            return
        if parsed.path == "/api/notebook-section":
            self._serve_notebook_section(parsed.query)
            return
        if parsed.path == "/api/diff-stream":
            self._serve_diff_stream(parsed.query)
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
        query, mode, base_branch, branch = self._parse_diff_request(query_string)
        try:
            self._enforce_large_diff_limit(
                query=query,
                mode=mode,
                base_branch=base_branch,
                branch=branch,
            )
            if _is_truthy(query, "check"):
                self._send_json({"ok": True})
                return
            payload = self.server.service.build_diff(
                left=_first(query, "left", self.server.defaults["left"]),
                right=_first(query, "right", self.server.defaults["right"]),
                base_branch=base_branch,
                branch=branch,
            )
        except TextDiffError as exc:
            self._send_json(
                {
                    "error": str(exc),
                    "can_force": True,
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        except Exception as exc:
            LOGGER.exception("Diff request crashed: %s", exc)
            self._send_json(
                {"error": "Internal server error."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(payload)

    def _serve_diff_stream(self, query_string: str) -> None:
        query, mode, base_branch, branch = self._parse_diff_request(query_string)
        left = _first(query, "left", self.server.defaults["left"])
        right = _first(query, "right", self.server.defaults["right"])
        try:
            self._enforce_large_diff_limit(
                query=query,
                mode=mode,
                base_branch=base_branch,
                branch=branch,
            )
            initial_payload, progress_iter, label_overrides = self._build_stream_payload(
                mode=mode,
                left=left,
                right=right,
                base_branch=base_branch,
                branch=branch,
            )
        except TextDiffError as exc:
            self._send_json(
                {
                    "error": str(exc),
                    "can_force": True,
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        except Exception as exc:
            LOGGER.exception("Diff stream request crashed before streaming: %s", exc)
            self._send_json(
                {"error": "Internal server error."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        if not self._write_sse("init", initial_payload):
            return

        try:
            for progress in progress_iter:
                entry = dict(progress.entry)
                if label_overrides[0]:
                    entry["left_label"] = label_overrides[0]
                if label_overrides[1]:
                    entry["right_label"] = label_overrides[1]
                if not self._write_sse(
                    "file",
                    {
                        "entry": entry,
                        "summary": progress.summary,
                    },
                ):
                    return
        except TextDiffError as exc:
            self._write_sse("stream-error", {"error": str(exc)})
            return
        except Exception as exc:
            LOGGER.exception("Diff stream request crashed while streaming: %s", exc)
            self._write_sse("stream-error", {"error": "Internal server error."})
            return

        self._write_sse("done", {"summary": initial_payload["summary"]})

    def _serve_file_diff(self, query_string: str) -> None:
        query, mode, base_branch, branch = self._parse_diff_request(query_string)
        left_path = _first(query, "left_path")
        right_path = _first(query, "right_path")
        display_name = _first(query, "display_name")
        change_type = _first(query, "change_type", "modify") or "modify"

        try:
            if mode == "branch-review" and branch and branch.strip():
                resolved_base_branch = (
                    base_branch or self.server.service.default_base_branch()
                )
                merge_base, normalized_branch = self.server.service.resolve_branch_diff_sides(
                    base_branch=resolved_base_branch,
                    branch=branch,
                )
                left_label = f"{resolved_base_branch.strip()}...{normalized_branch}"
                payload = self.server.service.build_git_diff_paths(
                    left_path=left_path,
                    right_path=right_path,
                    left=merge_base,
                    right=normalized_branch,
                    display_name=display_name,
                    change_type=change_type,
                )
                payload["left_label"] = left_label
                payload["right_label"] = normalized_branch
            else:
                left = _first(query, "left", self.server.defaults["left"])
                right = _first(query, "right", self.server.defaults["right"])
                payload = self.server.service.build_git_diff_paths(
                    left_path=left_path,
                    right_path=right_path,
                    left=left or self.server.defaults["left"],
                    right=right or self.server.defaults["right"],
                    display_name=display_name,
                    change_type=change_type,
                )
        except TextDiffError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            LOGGER.exception("File diff request crashed: %s", exc)
            self._send_json(
                {"error": "Internal server error."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(payload)

    def _serve_notebook_section(self, query_string: str) -> None:
        query, mode, base_branch, branch = self._parse_diff_request(query_string)
        section = _first(query, "section")
        cell_key = _first(query, "cell_key")
        left_path = _first(query, "left_path")
        right_path = _first(query, "right_path")

        try:
            if mode == "branch-review" and branch and branch.strip():
                resolved_base_branch = (
                    base_branch or self.server.service.default_base_branch()
                )
                merge_base, normalized_branch = self.server.service.resolve_branch_diff_sides(
                    base_branch=resolved_base_branch,
                    branch=branch,
                )
                payload = self.server.service.build_notebook_section_diff(
                    left_path=left_path,
                    right_path=right_path,
                    left=merge_base,
                    right=normalized_branch,
                    section=section or "",
                    cell_key=cell_key,
                )
                payload["left_label"] = f"{resolved_base_branch.strip()}...{normalized_branch}"
                payload["right_label"] = normalized_branch
            else:
                left = _first(query, "left", self.server.defaults["left"])
                right = _first(query, "right", self.server.defaults["right"])
                payload = self.server.service.build_notebook_section_diff(
                    left_path=left_path,
                    right_path=right_path,
                    left=left or self.server.defaults["left"],
                    right=right or self.server.defaults["right"],
                    section=section or "",
                    cell_key=cell_key,
                )
        except TextDiffError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            LOGGER.exception("Notebook section diff request crashed: %s", exc)
            self._send_json(
                {"error": "Internal server error."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(payload)

    def _build_stream_payload(
        self,
        *,
        mode: str | None,
        left: str | None,
        right: str | None,
        base_branch: str | None,
        branch: str | None,
    ) -> tuple[dict[str, Any], Any, tuple[str | None, str | None]]:
        if mode == "branch-review" and branch and branch.strip():
            resolved_base_branch = (
                base_branch or self.server.service.default_base_branch()
            )
            merge_base, normalized_branch = self.server.service.resolve_branch_diff_sides(
                base_branch=resolved_base_branch,
                branch=branch,
            )
            left_label = f"{resolved_base_branch.strip()}...{normalized_branch}"
            progress_iter = self.server.service.iter_repo_diff_progress(
                left=merge_base,
                right=normalized_branch,
            )
            return (
                {
                    "display_name": "Repository diff",
                    "mode": "repo",
                    "left_label": left_label,
                    "right_label": normalized_branch,
                    "summary": {
                        "changed_files": 0,
                        "added_files": 0,
                        "removed_files": 0,
                        "updated_files": 0,
                        "changed_lines": 0,
                        "modified_lines": 0,
                        "added_lines": 0,
                        "removed_lines": 0,
                        "skipped_files": 0,
                    },
                },
                progress_iter,
                (left_label, normalized_branch),
            )

        normalized_left = self.server.service.normalize_side(
            left or self.server.defaults["left"]
        )
        normalized_right = self.server.service.normalize_side(
            right or self.server.defaults["right"]
        )
        progress_iter = self.server.service.iter_repo_diff_progress(
            left=normalized_left,
            right=normalized_right,
        )
        return (
            {
                "display_name": "Repository diff",
                "mode": "repo",
                "left_label": normalized_left,
                "right_label": normalized_right,
                "summary": {
                    "changed_files": 0,
                    "added_files": 0,
                    "removed_files": 0,
                    "updated_files": 0,
                    "changed_lines": 0,
                    "modified_lines": 0,
                    "added_lines": 0,
                    "removed_lines": 0,
                    "skipped_files": 0,
                },
            },
            progress_iter,
            (None, None),
        )

    def _write_sse(self, event: str, payload: dict[str, Any]) -> bool:
        body = f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            return False
        return True

    def _enforce_large_diff_limit(
        self,
        *,
        query: dict[str, list[str]],
        mode: str | None,
        base_branch: str | None,
        branch: str | None,
    ) -> None:
        if _is_truthy(query, "force"):
            return

        changed_files = self._count_changed_files(
            query=query,
            mode=mode,
            base_branch=base_branch,
            branch=branch,
        )
        if changed_files <= LARGE_DIFF_FILE_LIMIT:
            return

        raise TextDiffError(
            f"This diff has {changed_files} changed files. Showing everything may freeze the page."
        )

    def _count_changed_files(
        self,
        *,
        query: dict[str, list[str]],
        mode: str | None,
        base_branch: str | None,
        branch: str | None,
    ) -> int:
        if mode == "refs":
            return 0

        if mode == "branch-review" and branch and branch.strip():
            resolved_base_branch = (
                base_branch or self.server.service.default_base_branch()
            )
            merge_base, normalized_branch = self.server.service.resolve_branch_diff_sides(
                base_branch=resolved_base_branch,
                branch=branch,
            )
            return len(
                self.server.service.list_repo_diff_paths(
                    left=merge_base,
                    right=normalized_branch,
                )
            )

        left = self.server.service.normalize_side(
            _first(query, "left", self.server.defaults["left"])
            or self.server.defaults["left"]
        )
        right = self.server.service.normalize_side(
            _first(query, "right", self.server.defaults["right"])
            or self.server.defaults["right"]
        )
        return len(self.server.service.list_repo_diff_paths(left=left, right=right))

    def _parse_diff_request(
        self,
        query_string: str,
    ) -> tuple[dict[str, list[str]], str | None, str | None, str | None]:
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
        return query, mode, base_branch, branch

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


def _is_truthy(values: dict[str, list[str]], key: str) -> bool:
    value = _first(values, key, "")
    return value in {"1", "true", "yes", "on"}
