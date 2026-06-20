from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from dirdiff.sources import (
    RepoDiffPath,
    SideName,
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


class DiffServiceProtocol(Protocol):
    @property
    def repo_root(self) -> Path | None: ...

    def default_base_branch(self) -> str: ...

    def preferred_review_branch(
        self, *, base_branch: str | None = None
    ) -> str: ...

    def list_ref_choices(self) -> dict[str, list[str]]: ...

    def normalize_side(self, side: str) -> str: ...

    def resolve_branch_diff_sides(
        self, *, base_branch: str, branch: str
    ) -> tuple[str, str]: ...

    def build_repo_manifest(
        self, *, left: str, right: str, show_untracked: bool = False
    ) -> dict[str, Any]: ...

    def build_lazy_info(
        self, *, left: str, right: str, show_untracked: bool = False
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...

    def build_notebook_section_diff(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        section: str | None,
        cell_key: str | None = None,
    ) -> dict[str, Any]: ...


def _perf_log(message: str) -> None:
    if not ENABLE_PERF_LOGS:
        return
    LOGGER.info("[dirdiff-perf] %s", message)


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
        "moved_lines": 0,
        "skipped_files": 0,
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
        "moved_lines": 0,
        "left_exists": entry.left_path is not None,
        "right_exists": entry.right_path is not None,
    }


def _to_lazy_info_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    # /api/lazy-info must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    return {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": _file_kind_for_repo_entry(entry),
        "display_name": entry.display_name,
        "summary": _summary_for_repo_path(entry),
        "lazy": _lazy_reason_for_repo_entry(entry),
    }


def build_repo_manifest_for_service(
    service: DiffServiceProtocol,
    *,
    left: str,
    right: str,
    show_untracked: bool = False,
) -> dict[str, Any]:
    normalized_left = service.normalize_side(left)
    normalized_right = service.normalize_side(right)
    paths = service.list_repo_diff_paths(
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
        summary["moved_lines"] += line_summary["moved_lines"]
        files.append(file_entry)

    return {
        "display_name": "Repository diff",
        "mode": "repo",
        "left_label": normalized_left,
        "right_label": normalized_right,
        "summary": summary,
        "files": files,
    }


def build_lazy_info_for_service(
    service: DiffServiceProtocol,
    *,
    left: str,
    right: str,
    show_untracked: bool = False,
) -> dict[str, Any]:
    normalized_left = service.normalize_side(left)
    normalized_right = service.normalize_side(right)
    paths = service.list_repo_diff_paths(
        left=normalized_left,
        right=normalized_right,
        show_untracked=show_untracked,
    )
    files: list[dict[str, Any]] = []

    for entry in paths:
        if _should_lazy_load_repo_entry(entry):
            files.append(_to_lazy_info_file_entry(entry))

    return {"files": files}
