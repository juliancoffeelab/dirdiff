from __future__ import annotations

import time
from typing import Any

from dirdiff.fold import FoldHint, fold_hints_for_path
from dirdiff.highlight import highlight_lines_for_path
from dirdiff.services.base import (
    _file_kind_for_change_type,
    _perf_log,
)
from dirdiff.services.gumtree.gumtree import (
    GumTreeInvalidJsonError,
    GumTreeJson,
    gumtree_executable_for_cwd,
    run_gumtree_json,
)
from dirdiff.services.gumtree.logic import (
    build_gumtree_rows_from_json,
    unified_patch_rows,
)
from dirdiff.services.textdiff import (
    TextDiffService,
    _default_expanded_for_payload,
    _looks_like_notebook_path,
    _payload_size_bytes,
    _plain_line_rows_for_side,
)
from dirdiff.sources import (
    TextDiffError,
    TextVersion,
    WorkspaceBackend,
    _display_name_for_repo_paths,
)

GUMTREE_FALLBACK_WARNING = {
    "type": "gumtree_invalid_json",
    "message": (
        "GumTree returned invalid JSON, so dirdiff could not render GumTree "
        "move tokens for this file. Showing a unified diff fallback instead."
    ),
}


def _build_gumtree_rows_payload(
    *,
    rows: list[dict[str, Any]],
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
    summarize_line_statuses: bool = False,
) -> dict[str, Any]:
    left_syntax_lines = highlight_lines_for_path(left_path_hint, left_text)
    right_syntax_lines = highlight_lines_for_path(right_path_hint, right_text)
    plain_render = left_syntax_lines is None and right_syntax_lines is None
    fold_hints: list[FoldHint] = []
    if not plain_render:
        fold_hints = fold_hints_for_path(right_path_hint, right_text, rows)

    for row in rows:
        left_no = row.get("left_no")
        if (
            isinstance(left_no, int)
            and left_syntax_lines
            and left_no - 1 < len(left_syntax_lines)
            and left_syntax_lines[left_no - 1]
        ):
            row["left_syntax"] = left_syntax_lines[left_no - 1]

        right_no = row.get("right_no")
        if (
            isinstance(right_no, int)
            and right_syntax_lines
            and right_no - 1 < len(right_syntax_lines)
            and right_syntax_lines[right_no - 1]
        ):
            row["right_syntax"] = right_syntax_lines[right_no - 1]

    added_lines = 0
    removed_lines = 0
    moved_lines = 0
    if summarize_line_statuses:
        added_lines = sum(1 for row in rows if row["status"] == "insert")
        removed_lines = sum(1 for row in rows if row["status"] == "delete")
        moved_lines = sum(1 for row in rows if row["status"] == "move")

    payload: dict[str, Any] = {
        "rows": rows,
        "changed_lines": added_lines + removed_lines + moved_lines,
        "modified_lines": 0,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "moved_lines": moved_lines,
    }
    if plain_render:
        payload["render_mode"] = "plain"
    if fold_hints:
        payload["fold_hints"] = fold_hints
    return payload


class GumTreeDiffService(TextDiffService):
    def __init__(self, repo: WorkspaceBackend) -> None:
        super().__init__(repo)

    def _run_gumtree_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str,
        right_path_hint: str,
    ) -> GumTreeJson:
        gumtree_bin = gumtree_executable_for_cwd(self.cwd)
        return run_gumtree_json(
            gumtree_bin=gumtree_bin,
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
        if _looks_like_notebook_path(right_path):
            return super().build_git_diff_paths(
                left_path=left_path,
                right_path=right_path,
                left=left,
                right=right,
                display_name=display_name,
                change_type=change_type,
                file_kind=file_kind,
            )
        if _looks_like_notebook_path(left_path):
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

        left_text_value = ""
        if left_version.text is not None:
            left_text_value = left_version.text
        right_text_value = ""
        if right_version.text is not None:
            right_text_value = right_version.text

        if left_version.exists and right_version.exists:
            if normalized_left is None:
                raise TextDiffError(
                    "GumTree requires both file paths for move detection."
                )
            if normalized_right is None:
                raise TextDiffError(
                    "GumTree requires both file paths for move detection."
                )
            engine_warning: dict[str, str] | None = None
            summarize_line_statuses = False
            try:
                diff_json = self._run_gumtree_json(
                    left_text=left_text_value,
                    right_text=right_text_value,
                    left_path_hint=normalized_left,
                    right_path_hint=normalized_right,
                )
            except GumTreeInvalidJsonError:
                rows = unified_patch_rows(
                    left_text=left_text_value,
                    right_text=right_text_value,
                    left_path_hint=normalized_left,
                    right_path_hint=normalized_right,
                )
                engine_warning = GUMTREE_FALLBACK_WARNING
                summarize_line_statuses = True
            else:
                rows = build_gumtree_rows_from_json(
                    diff_json=diff_json,
                    left_text=left_text_value,
                    right_text=right_text_value,
                )
        elif left_version.exists:
            rows = _plain_line_rows_for_side(
                text=left_text_value,
                side="left",
            )
            engine_warning = None
            summarize_line_statuses = True
        else:
            rows = _plain_line_rows_for_side(
                text=right_text_value,
                side="right",
            )
            engine_warning = None
            summarize_line_statuses = True

        rows_payload = _build_gumtree_rows_payload(
            rows=rows,
            left_text=left_text_value,
            right_text=right_text_value,
            left_path_hint=normalized_left,
            right_path_hint=normalized_right,
            summarize_line_statuses=summarize_line_statuses,
        )
        if display_name is None:
            resolved_display_name = _display_name_for_repo_paths(
                normalized_left,
                normalized_right,
            )
        else:
            resolved_display_name = display_name

        payload = {
            "display_name": resolved_display_name,
            "mode": "git",
            "left_label": normalized_left_side,
            "right_label": normalized_right_side,
            "summary": {
                "changed_lines": rows_payload["changed_lines"],
                "modified_lines": rows_payload["modified_lines"],
                "added_lines": rows_payload["added_lines"],
                "removed_lines": rows_payload["removed_lines"],
                "moved_lines": rows_payload["moved_lines"],
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
        move_row_count = sum(1 for row in rows if row.get("status") == "move")
        syntax_span_count = sum(
            len(row.get("left_syntax", ())) + len(row.get("right_syntax", ()))
            for row in rows
        )
        token_count = sum(
            len(row.get("left_tokens", ())) + len(row.get("right_tokens", ()))
            for row in rows
        )
        payload_bytes = _payload_size_bytes(payload)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _perf_log(
            "gumtree-file"
            f" name={payload['display_name']!r}"
            f" change={change_type}"
            f" rows={row_count}"
            f" moves={move_row_count}"
            f" left_chars={len(left_text_value)}"
            f" right_chars={len(right_text_value)}"
            f" syntax_spans={syntax_span_count}"
            f" diff_tokens={token_count}"
            f" payload_bytes={payload_bytes}"
            f" elapsed_ms={elapsed_ms:.1f}"
        )
        return payload
