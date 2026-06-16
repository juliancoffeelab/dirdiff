from __future__ import annotations

import logging
import os
import time
from pathlib import Path, PurePosixPath
from typing import Any

from dirdiff.difftastic import (
    _difftastic_engine_warning,
    _difftastic_rows_from_json,
    run_difftastic_json,
)
from dirdiff.notebooks import (
    _build_notebook_diff_payload,
    _normalize_notebook_document,
    build_notebook_section_payload,
)
from dirdiff.sources import (
    PatchBackend,
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackend,
    _display_name_for_repo_paths,
)
from dirdiff.textdiff import (
    _build_git_rows_payload,
    _build_rows_payload,
    _default_expanded_for_payload,
    _git_style_line_rows,
    _looks_like_notebook_path,
    _parse_git_patch_rows,
    _payload_size_bytes,
    _plain_line_rows_for_side,
)

ENABLE_PERF_LOGS = os.environ.get("DIRDIFF_DEBUG_PERF") == "1"
LARGE_CHANGED_LINES_LAZY_THRESHOLD = 1000
GENERATED_FILES = frozenset(
    {
        "cargo.lock",
        "composer.lock",
        "flake.lock",
        "go.sum",
        "bun.lock",
        "pdm.lock",
        "pipfile.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)
GIT_FILE_STATUS_BY_CHANGE_TYPE = {
    "modify": "modified",
    "add": "added",
    "delete": "deleted",
    "rename": "renamed",
    "copy": "copied",
}
LOGGER = logging.getLogger(__name__)


def _perf_log(message: str) -> None:
    if not ENABLE_PERF_LOGS:
        return
    LOGGER.info("[dirdiff-perf] %s", message)


def _looks_generated_path(path: str | None) -> bool:
    if not path:
        return False
    return PurePosixPath(path).name.casefold() in GENERATED_FILES


def _should_lazy_load_repo_entry(entry: RepoDiffPath) -> bool:
    return (
        entry.untracked
        or entry.change_type == "delete"
        or (entry.change_type == "rename" and (entry.changed_lines or 0) == 0)
        or _looks_generated_path(entry.right_path)
        or _looks_generated_path(entry.left_path)
        or (
            entry.changed_lines is not None
            and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD
        )
    )


def _file_kind_for_repo_entry(entry: RepoDiffPath) -> dict[str, str]:
    if entry.untracked:
        return {"type": "untracked"}
    return {
        "type": "git",
        "status": GIT_FILE_STATUS_BY_CHANGE_TYPE.get(
            entry.change_type, "modified"
        ),
    }


def _file_kind_for_change_type(
    change_type: str,
    *,
    file_kind: str | None = None,
) -> dict[str, str]:
    if file_kind == "untracked":
        return {"type": "untracked"}
    return {
        "type": "git",
        "status": GIT_FILE_STATUS_BY_CHANGE_TYPE.get(change_type, "modified"),
    }


def _lazy_reason_for_repo_entry(entry: RepoDiffPath) -> str | None:
    if entry.untracked:
        return "untracked"
    if entry.change_type == "delete":
        return "deleted"
    if entry.change_type == "rename" and (entry.changed_lines or 0) == 0:
        return "pure_renamed"
    if _looks_generated_path(entry.right_path) or _looks_generated_path(
        entry.left_path
    ):
        return "generated"
    if (
        entry.changed_lines is not None
        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD
    ):
        return "too_big"
    return None


def _to_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    return {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": _file_kind_for_repo_entry(entry),
    }


def _to_lazy_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": _file_kind_for_repo_entry(entry),
    }
    lazy = _lazy_reason_for_repo_entry(entry)
    if lazy is not None:
        payload["lazy"] = lazy
    return payload


def _to_lazy_info_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    return {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": _file_kind_for_repo_entry(entry),
        "display_name": entry.display_name,
        "summary": _summary_for_repo_path(entry),
    }


def _summary_for_repo_path(entry: RepoDiffPath) -> dict[str, int | bool]:
    raw_added = entry.added_lines or 0
    raw_removed = entry.removed_lines or 0
    modified_lines = min(raw_added, raw_removed)
    added_lines = raw_added - modified_lines
    removed_lines = raw_removed - modified_lines
    return {
        "changed_lines": modified_lines + added_lines + removed_lines,
        "modified_lines": modified_lines,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "left_exists": entry.left_path is not None,
        "right_exists": entry.right_path is not None,
    }


def build_loaded_diff(
    *,
    display_name: str,
    mode: str,
    left_label: str,
    right_label: str,
    left_exists: bool,
    right_exists: bool,
    left_text: str | None,
    right_text: str | None,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> dict[str, Any]:
    if _looks_like_notebook_path(right_path_hint) or _looks_like_notebook_path(
        left_path_hint
    ):
        notebook_payload = _build_notebook_diff_payload(
            display_name=display_name,
            mode=mode,
            left_label=left_label,
            right_label=right_label,
            left_exists=left_exists,
            right_exists=right_exists,
            left_text=left_text,
            right_text=right_text,
        )
        if notebook_payload is not None:
            return notebook_payload

    rows_payload = _build_rows_payload(
        left_text=left_text or "",
        right_text=right_text or "",
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    payload = {
        "display_name": display_name,
        "mode": mode,
        "left_label": left_label,
        "right_label": right_label,
        "summary": {
            "changed_lines": rows_payload["changed_lines"],
            "modified_lines": rows_payload["modified_lines"],
            "added_lines": rows_payload["added_lines"],
            "removed_lines": rows_payload["removed_lines"],
            "left_exists": left_exists,
            "right_exists": right_exists,
        },
        "rows": rows_payload["rows"],
    }
    payload["default_expanded"] = _default_expanded_for_payload(payload)
    if "render_mode" in rows_payload:
        payload["render_mode"] = rows_payload["render_mode"]
    if "truncated_rows" in rows_payload:
        payload["truncated_rows"] = rows_payload["truncated_rows"]
    if "fold_hints" in rows_payload:
        payload["fold_hints"] = rows_payload["fold_hints"]
    return payload


def _empty_repo_summary() -> dict[str, int]:
    return {
        "changed_files": 0,
        "added_files": 0,
        "removed_files": 0,
        "updated_files": 0,
        "changed_lines": 0,
        "modified_lines": 0,
        "added_lines": 0,
        "removed_lines": 0,
        "skipped_files": 0,
    }


class TextDiffService:
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

        payload = build_loaded_diff(
            display_name=display_name
            or _display_name_for_repo_paths(normalized_left, normalized_right),
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
        left_text = left_version.text or ""
        right_text = right_version.text or ""
        row_groups: list[list[dict[str, Any]]] = []
        if payload.get("render_kind") == "notebook":
            if payload.get("notebook_metadata_rows"):
                row_groups.append(payload["notebook_metadata_rows"])
            for cell in payload.get("cells", []):
                row_groups.append(cell.get("source_rows", []))
                if cell.get("metadata_rows"):
                    row_groups.append(cell["metadata_rows"])
                if cell.get("outputs_rows"):
                    row_groups.append(cell["outputs_rows"])
        else:
            row_groups.append(payload["rows"])

        row_count = sum(len(rows) for rows in row_groups)
        syntax_span_count = sum(
            len(row.get("left_syntax", ())) + len(row.get("right_syntax", ()))
            for rows in row_groups
            for row in rows
        )
        token_count = sum(
            len(row.get("left_tokens", ())) + len(row.get("right_tokens", ()))
            for rows in row_groups
            for row in rows
        )
        payload_bytes = _payload_size_bytes(payload)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _perf_log(
            "file"
            f" name={payload['display_name']!r}"
            f" change={change_type}"
            f" rows={row_count}"
            f" left_chars={len(left_text)}"
            f" right_chars={len(right_text)}"
            f" syntax_spans={syntax_span_count}"
            f" diff_tokens={token_count}"
            f" payload_bytes={payload_bytes}"
            f" elapsed_ms={elapsed_ms:.1f}"
        )
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
        left_notebook = context["left_notebook"]
        right_notebook = context["right_notebook"]
        left_label = context["left_label"]
        right_label = context["right_label"]

        return build_notebook_section_payload(
            left_notebook=left_notebook,
            right_notebook=right_notebook,
            left_label=left_label,
            right_label=right_label,
            section=section,
            cell_key=cell_key,
        )

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        normalized_left = self.normalize_side(left)
        normalized_right = self.normalize_side(right)
        paths = self.list_repo_diff_paths(
            left=normalized_left,
            right=normalized_right,
            show_untracked=show_untracked,
        )
        summary = _empty_repo_summary()
        files: list[dict[str, Any]] = []

        for entry in paths:
            file_entry = (
                _to_lazy_repo_manifest_file_entry(entry)
                if _should_lazy_load_repo_entry(entry)
                else _to_repo_manifest_file_entry(entry)
            )
            line_summary = _summary_for_repo_path(entry)
            summary["changed_files"] += 1
            if entry.change_type == "add":
                summary["added_files"] += 1
            elif entry.change_type == "delete":
                summary["removed_files"] += 1
            else:
                summary["updated_files"] += 1
            summary["changed_lines"] += line_summary["changed_lines"]
            summary["modified_lines"] += line_summary["modified_lines"]
            summary["added_lines"] += line_summary["added_lines"]
            summary["removed_lines"] += line_summary["removed_lines"]
            files.append(file_entry)

        return {
            "display_name": "Repository diff",
            "mode": "repo",
            "left_label": normalized_left,
            "right_label": normalized_right,
            "summary": summary,
            "files": files,
        }

    def build_lazy_info(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        normalized_left = self.normalize_side(left)
        normalized_right = self.normalize_side(right)
        paths = self.list_repo_diff_paths(
            left=normalized_left,
            right=normalized_right,
            show_untracked=show_untracked,
        )
        files: list[dict[str, Any]] = []

        for entry in paths:
            if _should_lazy_load_repo_entry(entry):
                files.append(_to_lazy_info_file_entry(entry))

        return {"files": files}


class GitDiffService(TextDiffService):
    repo: PatchBackend

    def __init__(self, repo: PatchBackend) -> None:
        super().__init__(repo)
        self.repo = repo

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
        patch_path = normalized_right or normalized_left
        rows = (
            _parse_git_patch_rows(
                self.repo.load_unified_patch(
                    left=normalized_left_side,
                    right=normalized_right_side,
                    path=patch_path,
                )
            )
            if patch_path is not None
            else []
        )
        if not rows and left_version.exists and not right_version.exists:
            rows = _plain_line_rows_for_side(text=left_text, side="left")
        elif not rows and right_version.exists and not left_version.exists:
            rows = _plain_line_rows_for_side(text=right_text, side="right")
        elif not rows:
            rows = _git_style_line_rows(left_text, right_text)

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
            "git-file"
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
        raise TextDiffError(
            "Notebook sections are not available in the Git engine."
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
