from __future__ import annotations

import time
from typing import Any

from dirdiff.difftastic import (
    _difftastic_engine_warning,
    _difftastic_rows_from_json,
    run_difftastic_json,
)
from dirdiff.services.base import (
    TextDiffService,
    _file_kind_for_change_type,
    _perf_log,
)
from dirdiff.sources import (
    TextDiffError,
    TextVersion,
    _display_name_for_repo_paths,
)
from dirdiff.textdiff import (
    _build_git_rows_payload,
    _default_expanded_for_payload,
    _git_style_line_rows,
    _looks_like_notebook_path,
    _payload_size_bytes,
    _plain_line_rows_for_side,
)


class DifftasticDiffService(TextDiffService):
    def _run_difftastic_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str | None,
        right_path_hint: str | None,
    ) -> dict[str, Any]:
        return run_difftastic_json(
            left_text=left_text,
            right_text=right_text,
            left_path_hint=left_path_hint,
            right_path_hint=right_path_hint,
        )

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str = "modify",
        file_kind: str | None = None,
    ) -> dict[str, Any]:
        if _looks_like_notebook_path(right_path) or _looks_like_notebook_path(
            left_path
        ):
            return super().build_git_diff_paths(
                left_path=left_path,
                right_path=right_path,
                left=left,
                right=right,
                display_name=display_name,
                change_type=change_type,
                file_kind=file_kind,
            )

        started_at = time.perf_counter()
        normalized_left = (
            self.normalize_repo_path(left_path)
            if left_path is not None
            else None
        )
        normalized_right = (
            self.normalize_repo_path(right_path)
            if right_path is not None
            else None
        )
        normalized_left_side = self.normalize_side(left)
        normalized_right_side = self.normalize_side(right)
        left_version = (
            self.load_version(normalized_left, normalized_left_side)
            if normalized_left is not None
            else TextVersion(
                label=normalized_left_side, exists=False, text=None
            )
        )
        right_version = (
            self.load_version(normalized_right, normalized_right_side)
            if normalized_right is not None
            else TextVersion(
                label=normalized_right_side, exists=False, text=None
            )
        )

        if left_version.error:
            raise TextDiffError(left_version.error)
        if right_version.error:
            raise TextDiffError(right_version.error)
        if not left_version.exists and not right_version.exists:
            raise TextDiffError("The selected file is missing on both sides.")

        left_text = left_version.text or ""
        right_text = right_version.text or ""
        engine_warning: dict[str, str] | None = None
        if left_version.exists and right_version.exists:
            diff_json = self._run_difftastic_json(
                left_text=left_text,
                right_text=right_text,
                left_path_hint=normalized_left,
                right_path_hint=normalized_right,
            )
            engine_warning = _difftastic_engine_warning(diff_json)
            rows = _difftastic_rows_from_json(
                diff_json,
                left_text=left_text,
                right_text=right_text,
            )
            if not rows:
                rows = _git_style_line_rows(left_text, right_text)
        elif left_version.exists:
            rows = _plain_line_rows_for_side(text=left_text, side="left")
        else:
            rows = _plain_line_rows_for_side(text=right_text, side="right")

        rows_payload = _build_git_rows_payload(
            rows=rows,
            left_text=left_text,
            right_text=right_text,
            left_path_hint=normalized_left,
            right_path_hint=normalized_right,
        )
        payload = {
            "display_name": display_name
            or _display_name_for_repo_paths(normalized_left, normalized_right),
            "mode": "git",
            "left_label": normalized_left_side,
            "right_label": normalized_right_side,
            "summary": {
                "changed_lines": rows_payload["changed_lines"],
                "modified_lines": rows_payload["modified_lines"],
                "added_lines": rows_payload["added_lines"],
                "removed_lines": rows_payload["removed_lines"],
                "left_exists": left_version.exists,
                "right_exists": right_version.exists,
            },
            "rows": rows_payload["rows"],
            "file_kind": _file_kind_for_change_type(
                change_type,
                file_kind=file_kind,
            ),
            "left_path": normalized_left,
            "right_path": normalized_right,
        }
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        payload["default_expanded"] = _default_expanded_for_payload(payload)
        if "render_mode" in rows_payload:
            payload["render_mode"] = rows_payload["render_mode"]
        if "truncated_rows" in rows_payload:
            payload["truncated_rows"] = rows_payload["truncated_rows"]
        if "fold_hints" in rows_payload:
            payload["fold_hints"] = rows_payload["fold_hints"]

        row_count = len(rows)
        syntax_span_count = sum(
            len(row.get("left_syntax", ())) + len(row.get("right_syntax", ()))
            for row in rows
        )
        payload_bytes = _payload_size_bytes(payload)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _perf_log(
            "difftastic-file"
            f" name={payload['display_name']!r}"
            f" change={change_type}"
            f" rows={row_count}"
            f" left_chars={len(left_text)}"
            f" right_chars={len(right_text)}"
            f" syntax_spans={syntax_span_count}"
            f" payload_bytes={payload_bytes}"
            f" elapsed_ms={elapsed_ms:.1f}"
        )
        return payload
