from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from dirdiff.notebooks import (
    _normalize_notebook_document,
    build_notebook_section_payload,
)
from dirdiff.services.base import (
    DiffServiceProtocol,
    _file_kind_for_change_type,
    _perf_log,
    build_lazy_info_for_service,
    build_repo_manifest_for_service,
)
from dirdiff.services.difftastic.difft import (
    DifftasticJson,
    run_difftastic_json,
)
from dirdiff.services.difftastic.logic import (
    build_difftastic_ast,
)
from dirdiff.services.textdiff import (
    _build_git_rows_payload,
    _default_expanded_for_payload,
    _git_style_line_rows,
    _looks_like_notebook_path,
    _payload_size_bytes,
    _plain_line_rows_for_side,
    build_loaded_diff,
)
from dirdiff.sources import (
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackend,
    _display_name_for_repo_paths,
)


class DifftasticDiffService(DiffServiceProtocol):
    def __init__(self, repo: WorkspaceBackend) -> None:
        self.repo = repo

    @property
    def repo_root(self) -> Path | None:
        return self.repo.repo_root

    @property
    def cwd(self) -> Path:
        return self.repo.cwd

    def normalize_side(self, raw_side: str) -> SideName:
        return self.repo.normalize_side(raw_side)

    def discover_default_path(self) -> str:
        return self.repo.discover_default_path()

    def current_branch_name(self) -> str:
        return self.repo.current_branch_name()

    def list_branch_names(self) -> list[str]:
        return self.repo.list_branch_names()

    def list_remote_ref_names(self) -> list[str]:
        return self.repo.list_remote_ref_names()

    def list_remote_names(self) -> list[str]:
        return self.repo.list_remote_names()

    def list_ref_choices(self) -> dict[str, list[str]]:
        return self.repo.list_ref_choices()

    def default_remote_name(self) -> str:
        return self.repo.default_remote_name()

    def branch_upstream_name(self, branch_name: str) -> str:
        return self.repo.branch_upstream_name(branch_name)

    def default_base_branch(self) -> str:
        return self.repo.default_base_branch()

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        return self.repo.preferred_review_branch(base_branch=base_branch)

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]:
        return self.repo.resolve_branch_diff_sides(
            base_branch=base_branch,
            branch=branch,
        )

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        return self.repo.list_repo_diff_paths(
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def normalize_repo_path(self, raw_path: str) -> str:
        return self.repo.normalize_repo_path(raw_path)

    def load_version(self, path: str, side: SideName) -> TextVersion:
        return self.repo.load_version(path, side)

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_repo_manifest_for_service(
            self,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def build_lazy_info(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_lazy_info_for_service(
            self,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def _run_difftastic_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str | None,
        right_path_hint: str | None,
    ) -> DifftasticJson:
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
            return self._build_text_git_diff_paths(
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

        left_text = "" if left_version.text is None else left_version.text
        right_text = "" if right_version.text is None else right_version.text
        engine_warning: dict[str, str] | None = None
        if left_version.exists and right_version.exists:
            difftastic_ast = build_difftastic_ast(
                left_text=left_text,
                right_text=right_text,
                left_path_hint=normalized_left,
                right_path_hint=normalized_right,
            )
            engine_warning = difftastic_ast.engine_warning
            rows = cast("list[dict[str, Any]]", difftastic_ast.rows)
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

    def _build_text_git_diff_paths(
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

        if display_name is None:
            resolved_display_name = _display_name_for_repo_paths(
                normalized_left,
                normalized_right,
            )
        else:
            resolved_display_name = display_name

        payload = build_loaded_diff(
            display_name=resolved_display_name,
            mode="git",
            left_label=normalized_left_side,
            right_label=normalized_right_side,
            left_exists=left_version.exists,
            right_exists=right_version.exists,
            left_text=left_version.text,
            right_text=right_version.text,
            left_path_hint=normalized_left,
            right_path_hint=normalized_right,
        )
        payload["file_kind"] = _file_kind_for_change_type(
            change_type,
            file_kind=file_kind,
        )
        payload["left_path"] = normalized_left
        payload["right_path"] = normalized_right
        return payload

    def _load_git_notebook_context(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
    ) -> dict[str, Any]:
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

        left_notebook = (
            _normalize_notebook_document(left_version.text)
            if left_version.exists
            else None
        )
        right_notebook = (
            _normalize_notebook_document(right_version.text)
            if right_version.exists
            else None
        )
        if left_version.exists and left_notebook is None:
            raise TextDiffError(
                f"Could not parse notebook on {normalized_left_side}."
            )
        if right_version.exists and right_notebook is None:
            raise TextDiffError(
                f"Could not parse notebook on {normalized_right_side}."
            )

        return {
            "left_path": normalized_left,
            "right_path": normalized_right,
            "left_label": normalized_left_side,
            "right_label": normalized_right_side,
            "left_notebook": left_notebook,
            "right_notebook": right_notebook,
        }

    def build_notebook_section_diff(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        section: str | None,
        cell_key: str | None = None,
    ) -> dict[str, Any]:
        context = self._load_git_notebook_context(
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
        )
        return build_notebook_section_payload(
            left_notebook=context["left_notebook"],
            right_notebook=context["right_notebook"],
            left_label=context["left_label"],
            right_label=context["right_label"],
            section=section,
            cell_key=cell_key,
        )
