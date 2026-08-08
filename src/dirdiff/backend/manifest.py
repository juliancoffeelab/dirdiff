"""Manifest and lazy-file payload helpers for workspace backends.

This module is part of `dirdiff.backend` because it turns backend path
metadata into API payloads.  It does not render file contents and does not know
which diff engine the user selected.  Services render one already-loaded file;
backends list, classify, and load workspace paths.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal, Optional

from dirdiff.backend.base import (
    RepoDiffPath,
    WorkspaceBackendProtocol,
)

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

__all__ = [
    "GENERATED_FILES",
    "GIT_FILE_STATUS_BY_CHANGE_TYPE",
    "build_lazy_info_for_paths",
    "build_repo_manifest_for_backend",
    "build_repo_manifest_for_paths",
    "file_kind_for_change_type",
    "file_kind_for_repo_entry",
]


def file_kind_for_repo_entry(entry: RepoDiffPath) -> dict[str, str]:
    """Convert backend change metadata into the frontend's file-kind contract."""
    if entry.untracked:
        return {"type": "untracked"}
    return {
        "type": "git",
        "status": GIT_FILE_STATUS_BY_CHANGE_TYPE.get(
            entry.change_type, "modified"
        ),
    }


def file_kind_for_change_type(
    change_type: Literal["modify", "add", "delete", "rename", "copy"],
    *,
    file_kind: Literal["git", "untracked"] | None = None,
) -> dict[str, str]:
    """Mirror manifest file-kind encoding for lazy file-diff responses."""
    if file_kind == "untracked":
        return {"type": "untracked"}
    return {
        "type": "git",
        "status": GIT_FILE_STATUS_BY_CHANGE_TYPE.get(change_type, "modified"),
    }


def _lazy_reason_for_repo_entry(entry: RepoDiffPath) -> str | None:
    """Classify files that should be represented by lazy placeholders."""

    def _looks_generated_path(path: str | None) -> bool:
        """Recognize generated filenames for this manifest classification."""
        if path is None:
            return False
        return PurePosixPath(path).name.casefold() in GENERATED_FILES

    if entry.lazy_reason_override is not None:
        return entry.lazy_reason_override
    if entry.untracked:
        return "untracked"
    if entry.change_type == "delete":
        return "deleted"
    if _looks_generated_path(entry.right_path) or _looks_generated_path(
        entry.left_path
    ):
        return "generated"
    return None


def _should_lazy_load_repo_entry(entry: RepoDiffPath) -> bool:
    """Use the lazy classifier as the single decision point for placeholders."""
    return _lazy_reason_for_repo_entry(entry) is not None


def _empty_repo_summary() -> dict[str, Any]:
    """Provide zero File totals before aggregate line metadata is attached."""
    return {
        "changed_files": 0,
        "added_files": 0,
        "removed_files": 0,
        "updated_files": 0,
        "added_lines": None,
        "removed_lines": None,
        "skipped_files": 0,
    }


def _to_lazy_info_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    """Expose enough metadata for the frontend to render unloaded file rows."""
    return {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": file_kind_for_repo_entry(entry),
        "display_name": entry.display_name,
        "changed_lines": None,
        "added_lines": None,
        "removed_lines": None,
        "lazy": _lazy_reason_for_repo_entry(entry),
    }


def _insert_tree_entry(
    entries: list[dict[str, Any]],
    *,
    parts: list[str],
    full_path: str,
    file_entry: dict[str, Any],
) -> None:
    """Mutate one tree level while preserving directory identity by path."""
    if parts == []:
        raise ValueError(f"Cannot insert empty manifest tree path: {full_path}")
    if len(parts) == 1:
        entries.append({"type": "file", "name": parts[0], "entry": file_entry})
        return

    directory_name = parts[0]
    directory_path = full_path.rsplit("/".join(parts[1:]), 1)[0].removesuffix(
        "/"
    )
    directory_node: dict[str, Any] | None = None
    for entry in entries:
        if (
            entry["type"] == "directory"
            and entry["name"] == directory_name
            and entry["path"] == directory_path
        ):
            directory_node = entry
            break
    if directory_node is None:
        directory_node = {
            "type": "directory",
            "name": directory_name,
            "path": directory_path,
            "entries": [],
        }
        entries.append(directory_node)
    child_entries = directory_node["entries"]
    if not isinstance(child_entries, list):
        raise ValueError(f"Directory node is missing entries: {directory_path}")
    _insert_tree_entry(
        child_entries,
        parts=parts[1:],
        full_path=full_path,
        file_entry=file_entry,
    )


def _root_files_last(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order each tree level for `_build_repo_manifest_tree`.

    Directory entries stay before file entries so root files render last in the
    frontend tree and flat depth-first file list.
    """
    directory_entries: list[dict[str, Any]] = []
    file_entries: list[dict[str, Any]] = []
    for entry in entries:
        if entry["type"] == "directory":
            entry["entries"] = _root_files_last(entry["entries"])
            directory_entries.append(entry)
        else:
            file_entries.append(entry)
    return [*directory_entries, *file_entries]


def _compact_single_directory_chains(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse directory chains that contain no branching choice.

    Used by `_build_repo_manifest_tree` after root-file ordering.

    `frontend -> src -> App.tsx` becomes `frontend/src -> App.tsx` so API
    consumers get a tree shaped around meaningful choices rather than every
    path segment.
    """
    compacted_entries: list[dict[str, Any]] = []
    for entry in entries:
        if entry["type"] != "directory":
            compacted_entries.append(entry)
            continue

        entry["entries"] = _compact_single_directory_chains(entry["entries"])
        compacted_entry = entry
        while len(compacted_entry["entries"]) == 1:
            child = compacted_entry["entries"][0]
            assert isinstance(child, dict)
            if child["type"] != "directory":
                break
            collapsed_entry = {
                "type": "directory",
                "name": f"{compacted_entry['name']}/{child['name']}",
                "path": child["path"],
                "entries": child["entries"],
            }
            compacted_entry = collapsed_entry
        compacted_entries.append(compacted_entry)
    return compacted_entries


def _build_repo_manifest_tree(
    entries: list[RepoDiffPath],
) -> list[dict[str, Any]]:
    """Turn flat backend paths into the nested tree consumed by the sidebar."""
    tree_entries: list[dict[str, Any]] = []
    for entry in entries:
        path = (
            entry.right_path
            if entry.right_path is not None
            else entry.left_path
        )
        if path is None:
            raise ValueError("Repo manifest entry is missing both paths.")
        parts = [part for part in PurePosixPath(path).parts if part != "."]
        file_entry = {
            "left_path": entry.left_path,
            "right_path": entry.right_path,
            "file_kind": file_kind_for_repo_entry(entry),
            "lazy": None,
        }
        if _should_lazy_load_repo_entry(entry):
            lazy = _lazy_reason_for_repo_entry(entry)
            if lazy is not None:
                file_entry["lazy"] = lazy
        _insert_tree_entry(
            tree_entries,
            parts=parts,
            full_path=path,
            file_entry=file_entry,
        )
    return _compact_single_directory_chains(_root_files_last(tree_entries))


def build_repo_manifest_for_backend(
    backend: WorkspaceBackendProtocol,
    *,
    left: str,
    right: str,
    show_untracked: bool = False,
) -> dict[str, Any]:
    """Build a manifest from a backend for tests and uncached callers."""
    normalized_left = backend.normalize_side(left)
    normalized_right = backend.normalize_side(right)
    paths = backend.list_repo_diff_paths(
        left=normalized_left,
        right=normalized_right,
        show_untracked=show_untracked,
    )
    added_lines, removed_lines = backend.line_counts(
        left=normalized_left,
        right=normalized_right,
        show_untracked=show_untracked,
    )
    return build_repo_manifest_for_paths(
        left_label=normalized_left,
        right_label=normalized_right,
        paths=paths,
        added_lines=added_lines,
        removed_lines=removed_lines,
    )


def build_repo_manifest_for_paths(
    *,
    left_label: str,
    right_label: str,
    paths: list[RepoDiffPath] | tuple[RepoDiffPath, ...],
    added_lines: Optional[int],
    removed_lines: Optional[int],
) -> dict[str, Any]:
    """Build a manifest from captured paths and aggregate Snapshot metadata."""
    assert (added_lines is None) == (removed_lines is None), (
        "manifest line counts must have equal presence"
    )
    summary = _empty_repo_summary()
    summary["added_lines"] = added_lines
    summary["removed_lines"] = removed_lines

    for entry in paths:
        summary["changed_files"] += 1
        if entry.change_type == "add":
            summary["added_files"] += 1
        elif entry.change_type == "delete":
            summary["removed_files"] += 1
        else:
            summary["updated_files"] += 1

    return {
        "display_name": "Repository diff",
        "left_label": left_label,
        "right_label": right_label,
        "summary": summary,
        "tree": _build_repo_manifest_tree(list(paths)),
    }


def build_lazy_info_for_paths(
    *,
    paths: list[RepoDiffPath] | tuple[RepoDiffPath, ...],
) -> dict[str, Any]:
    """Derive lazy-file metadata from the same path snapshot as the manifest."""
    files: list[dict[str, Any]] = []

    for entry in paths:
        if _should_lazy_load_repo_entry(entry):
            files.append(_to_lazy_info_file_entry(entry))

    return {"files": files}
