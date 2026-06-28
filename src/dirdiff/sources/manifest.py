"""Manifest and lazy-file payload helpers for workspace backends.

This module is part of ``dirdiff.sources`` because it turns backend path
metadata into API payloads.  It does not render file contents and does not know
which diff engine the user selected.  Services render one already-loaded file;
sources list, classify, and load workspace paths.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

from dirdiff.sources.base import RepoDiffPath, WorkspaceBackend

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


def file_kind_for_repo_entry(entry: RepoDiffPath) -> dict[str, str]:
    """Return the file-kind payload for one backend manifest entry."""
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
    """Return the file-kind payload for a file-diff request parameter."""
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


def _lazy_reason_for_repo_entry(entry: RepoDiffPath) -> str | None:
    if entry.untracked:
        return "untracked"
    if entry.change_type == "delete":
        return "deleted"
    changed_lines = 0
    if entry.changed_lines is not None:
        changed_lines = entry.changed_lines
    if entry.change_type == "rename" and changed_lines == 0:
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


def _should_lazy_load_repo_entry(entry: RepoDiffPath) -> bool:
    return _lazy_reason_for_repo_entry(entry) is not None


def _to_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    return {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": file_kind_for_repo_entry(entry),
        "lazy": None,
    }


def _to_lazy_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    payload = _to_repo_manifest_file_entry(entry)
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
        "added_lines": 0,
        "removed_lines": 0,
        "skipped_files": 0,
    }


def _to_lazy_info_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    return {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": file_kind_for_repo_entry(entry),
        "display_name": entry.display_name,
        "changed_lines": entry.changed_lines,
        "added_lines": entry.added_lines,
        "removed_lines": entry.removed_lines,
        "lazy": _lazy_reason_for_repo_entry(entry),
    }


def _tree_path_for_repo_entry(entry: RepoDiffPath) -> str:
    """Return the path used by ``_build_repo_manifest_tree`` for placement."""
    path = entry.right_path or entry.left_path
    if path is None:
        raise ValueError("Repo manifest entry is missing both paths.")
    return path


def _manifest_file_entry_for_tree(entry: RepoDiffPath) -> dict[str, Any]:
    """Build the file node payload used by ``_build_repo_manifest_tree``."""
    return (
        _to_lazy_repo_manifest_file_entry(entry)
        if _should_lazy_load_repo_entry(entry)
        else _to_repo_manifest_file_entry(entry)
    )


def _empty_directory_node(name: str, path: str) -> dict[str, Any]:
    """Create directory nodes for ``_insert_tree_entry`` while building a tree."""
    return {
        "type": "directory",
        "name": name,
        "path": path,
        "entries": [],
    }


def _insert_tree_entry(
    entries: list[dict[str, Any]],
    *,
    parts: list[str],
    full_path: str,
    file_entry: dict[str, Any],
) -> None:
    """Insert one manifest file into the tree built by ``_build_repo_manifest_tree``."""
    if not parts:
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
        directory_node = _empty_directory_node(directory_name, directory_path)
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
    """Order each tree level for ``_build_repo_manifest_tree``.

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

    Used by ``_build_repo_manifest_tree`` after root-file ordering.

    ``frontend -> src -> App.tsx`` becomes ``frontend/src -> App.tsx`` so API
    consumers get a tree shaped around meaningful choices rather than every
    path segment.
    """
    compacted_entries: list[dict[str, Any]] = []
    for entry in entries:
        if entry["type"] != "directory":
            compacted_entries.append(entry)
            continue

        entry["entries"] = _compact_single_directory_chains(entry["entries"])
        while (
            len(entry["entries"]) == 1
            and entry["entries"][0]["type"] == "directory"
        ):
            child = entry["entries"][0]
            entry = {
                "type": "directory",
                "name": f"{entry['name']}/{child['name']}",
                "path": child["path"],
                "entries": child["entries"],
            }
        compacted_entries.append(entry)
    return compacted_entries


def _build_repo_manifest_tree(
    entries: list[RepoDiffPath],
) -> list[dict[str, Any]]:
    """Build the ``tree`` field returned by ``build_repo_manifest_for_backend``."""
    tree_entries: list[dict[str, Any]] = []
    for entry in entries:
        path = _tree_path_for_repo_entry(entry)
        parts = [part for part in PurePosixPath(path).parts if part != "."]
        _insert_tree_entry(
            tree_entries,
            parts=parts,
            full_path=path,
            file_entry=_manifest_file_entry_for_tree(entry),
        )
    return _compact_single_directory_chains(_root_files_last(tree_entries))


def build_repo_manifest_for_backend(
    backend: WorkspaceBackend,
    *,
    left: str,
    right: str,
    show_untracked: bool = False,
) -> dict[str, Any]:
    """Build the repository manifest payload from backend path metadata."""
    normalized_left = backend.normalize_side(left)
    normalized_right = backend.normalize_side(right)
    paths = backend.list_repo_diff_paths(
        left=normalized_left,
        right=normalized_right,
        show_untracked=show_untracked,
    )
    summary = _empty_repo_summary()

    for entry in paths:
        summary["changed_files"] += 1
        if entry.change_type == "add":
            summary["added_files"] += 1
        elif entry.change_type == "delete":
            summary["removed_files"] += 1
        else:
            summary["updated_files"] += 1
        if entry.added_lines is not None:
            summary["added_lines"] += entry.added_lines
        if entry.removed_lines is not None:
            summary["removed_lines"] += entry.removed_lines

    return {
        "display_name": "Repository diff",
        "mode": "repo",
        "left_label": normalized_left,
        "right_label": normalized_right,
        "summary": summary,
        "tree": _build_repo_manifest_tree(paths),
    }


def build_lazy_info_for_backend(
    backend: WorkspaceBackend,
    *,
    left: str,
    right: str,
    show_untracked: bool = False,
) -> dict[str, Any]:
    """Build lazy-file metadata from backend path entries."""
    normalized_left = backend.normalize_side(left)
    normalized_right = backend.normalize_side(right)
    paths = backend.list_repo_diff_paths(
        left=normalized_left,
        right=normalized_right,
        show_untracked=show_untracked,
    )
    files: list[dict[str, Any]] = []

    for entry in paths:
        if _should_lazy_load_repo_entry(entry):
            files.append(_to_lazy_info_file_entry(entry))

    return {"files": files}
