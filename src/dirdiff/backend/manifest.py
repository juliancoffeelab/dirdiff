"""Build manifest trees and lazy-File metadata from backend path facts.

## Public interface

`build_repo_manifest_for_paths` turns captured `RepoDiffPath` records into the
manifest consumed by the HUD. `build_lazy_info_for_paths` selects the deferred
Files from those same records. The File-kind helpers translate backend change
metadata into the public provenance union.

## Purpose and boundaries

This module is the one place that applies manifest ordering, directory
compaction, File counters, and initial lazy policy. It consumes path metadata
only. It must not load or render File contents, select an engine, or attach the
Snapshot id added by the HTTP boundary.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from dirdiff.backend.base import (
    GitFileStatus,
    LazyInfo,
    LazyInfoFile,
    LazyReason,
    RepoDiffPath,
    RepoFileKind,
    RepoManifest,
    RepoManifestDirectoryNode,
    RepoManifestFileEntry,
    RepoManifestFileNode,
    RepoManifestSummary,
    RepoManifestTreeEntry,
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
"""Case-folded lockfile names that manifest construction defers by default.

`_lazy_reason_for_repo_entry` compares only the final component of either side
path. A backend's explicit lazy reason takes precedence over this classification.
"""
GIT_FILE_STATUS_BY_CHANGE_TYPE: dict[
    Literal["modify", "add", "delete", "rename", "copy"], GitFileStatus
] = {
    "modify": "modified",
    "add": "added",
    "delete": "deleted",
    "rename": "renamed",
    "copy": "copied",
}
"""Translate backend change relationships to the public Git File vocabulary.

Both eager manifest entries and focused lazy File responses use this complete
mapping. An unsupported backend change type is a contract violation and raises
`KeyError` rather than producing substitute metadata.
"""

__all__ = [
    "GENERATED_FILES",
    "GIT_FILE_STATUS_BY_CHANGE_TYPE",
    "RepoManifest",
    "build_lazy_info_for_paths",
    "build_repo_manifest_for_backend",
    "build_repo_manifest_for_paths",
    "file_kind_for_change_type",
    "file_kind_for_repo_entry",
]


def file_kind_for_repo_entry(entry: RepoDiffPath) -> RepoFileKind:
    """Encode one backend File pair in the manifest provenance union.

    Untracked entries select the untracked variant. Every tracked entry maps its
    change relationship through the complete Git status table.

    # Usage

    Manifest and lazy-info builders call this for each `RepoDiffPath`. Preserve
    the untracked discriminator instead of inferring provenance from the change
    type or missing side.

    # Failures

    A tracked entry with a change type outside the declared `RepoDiffPath`
    contract raises `KeyError`.
    """
    if entry.untracked:
        return {"type": "untracked"}
    return {
        "type": "git",
        "status": GIT_FILE_STATUS_BY_CHANGE_TYPE[entry.change_type],
    }


def file_kind_for_change_type(
    change_type: Literal["modify", "add", "delete", "rename", "copy"],
    *,
    file_kind: Literal["git", "untracked"] | None = None,
) -> RepoFileKind:
    """Encode captured change metadata in the manifest File-kind contract.

    # Parameters

    - `change_type`: Captured backend relationship between the File sides.
    - `file_kind`: Provenance override; `untracked` omits Git status metadata.

    The default and explicit `git` cases require a recognized change type.

    # Usage

    The focused File endpoint calls this when converting captured Room metadata
    back to the same provenance shape published by the manifest. Pass
    `untracked` explicitly when the captured File did not come from Git.

    # Failures

    A Git-kind value with an unsupported `change_type` raises `KeyError`.
    """
    if file_kind == "untracked":
        return {"type": "untracked"}
    return {
        "type": "git",
        "status": GIT_FILE_STATUS_BY_CHANGE_TYPE[change_type],
    }


def _lazy_reason_for_repo_entry(entry: RepoDiffPath) -> LazyReason | None:
    """Derive the initial loading policy for one backend File pair.

    An explicit backend reason wins, followed by untracked, deleted, and known
    generated-file classifications. `None` leaves the File eager.

    # Returns

    - A lazy reason that permits capture to defer this File.
    - `None`: No lazy rule applies, so capture must load the File's present
      sides immediately.
    """

    def _looks_generated_path(path: str | None) -> bool:
        """Match a present path's case-folded basename against known lockfiles.

        Directory components do not affect this policy. An absent side cannot
        establish generated status on its own.
        """
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


def _empty_repo_summary() -> RepoManifestSummary:
    """Create the mutable starting summary for one manifest build.

    File counters and skipped Files start at zero. Aggregate line totals start
    absent and are replaced together from backend or Snapshot metadata.
    """
    return {
        "changed_files": 0,
        "added_files": 0,
        "removed_files": 0,
        "updated_files": 0,
        "added_lines": None,
        "removed_lines": None,
        "skipped_files": 0,
    }


def _to_lazy_info_file_entry(
    entry: RepoDiffPath, lazy: LazyReason
) -> LazyInfoFile:
    """Expose enough metadata for the frontend to render unloaded file rows.

    The caller supplies the entry's already-derived lazy reason so the
    listing derives it once per entry, for the predicate and the value.

    # Parameters

    - `entry`: Backend File pair whose metadata is exposed without contents.
    - `lazy`: Concrete reason already derived for that exact entry.
    """
    return {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": file_kind_for_repo_entry(entry),
        "display_name": entry.display_name,
        "changed_lines": None,
        "added_lines": None,
        "removed_lines": None,
        "lazy": lazy,
    }


def _insert_tree_entry(
    root_entries: list[RepoManifestTreeEntry],
    directories: dict[str, RepoManifestDirectoryNode],
    *,
    parts: list[str],
    file_entry: RepoManifestFileEntry,
) -> None:
    """Mutate the tree while preserving directory identity by path.

    `directories` indexes every created directory node by its full path, so
    inserting a path costs one dictionary lookup per ancestor instead of a
    linear scan of its sibling level, which measured quadratic at wide
    directories. The caller owns the index for exactly one tree build.

    # Parameters

    - `root_entries`: Mutable roots of the manifest tree under construction.
    - `directories`: Full-path index shared for this one tree build.
    - `parts`: Non-empty repository path components ending in a File name.
    - `file_entry`: Manifest payload stored at the new leaf.

    # Failures

    Raises `ValueError` when `parts` is empty. The check happens before either
    mutable input changes, so the partial tree and its directory index remain
    unchanged.
    """
    if parts == []:
        raise ValueError("Cannot insert an empty manifest tree path.")
    entries = root_entries
    prefix = ""
    for name in parts[:-1]:
        prefix = name if prefix == "" else f"{prefix}/{name}"
        existing_directory = directories.get(prefix)
        if existing_directory is None:
            directory_node: RepoManifestDirectoryNode = {
                "type": "directory",
                "name": name,
                "path": prefix,
                "entries": [],
            }
            directories[prefix] = directory_node
            entries.append(directory_node)
        else:
            directory_node = existing_directory
        entries = directory_node["entries"]
    entries.append({"type": "file", "name": parts[-1], "entry": file_entry})


def _root_files_last(
    entries: list[RepoManifestTreeEntry],
) -> list[RepoManifestTreeEntry]:
    """Order each tree level for `_build_repo_manifest_tree`.

    Directory entries stay before file entries so root files render last in the
    frontend tree and flat depth-first file list.
    """
    directory_entries: list[RepoManifestDirectoryNode] = []
    file_entries: list[RepoManifestFileNode] = []
    for entry in entries:
        if entry["type"] == "directory":
            entry["entries"] = _root_files_last(entry["entries"])
            directory_entries.append(entry)
        else:
            file_entries.append(entry)
    return [*directory_entries, *file_entries]


def _compact_single_directory_chains(
    entries: list[RepoManifestTreeEntry],
) -> list[RepoManifestTreeEntry]:
    """Collapse directory chains that contain no branching choice.

    Used by `_build_repo_manifest_tree` after root-file ordering.

    `frontend -> src -> App.tsx` becomes `frontend/src -> App.tsx` so API
    consumers get a tree shaped around meaningful choices rather than every
    path segment.
    """
    compacted_entries: list[RepoManifestTreeEntry] = []
    for entry in entries:
        if entry["type"] != "directory":
            compacted_entries.append(entry)
            continue

        entry["entries"] = _compact_single_directory_chains(entry["entries"])
        compacted_entry = entry
        while len(compacted_entry["entries"]) == 1:
            child = compacted_entry["entries"][0]
            if child["type"] != "directory":
                break
            collapsed_entry: RepoManifestDirectoryNode = {
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
) -> list[RepoManifestTreeEntry]:
    """Build the ordered, compact directory tree consumed by the File sidebar.

    Each File uses its right path when present and otherwise its left path.
    Every valid pair becomes one leaf before directory ordering and compaction.

    # Failures

    Raises `ValueError` when an entry has neither path, or when its selected
    path has no repository components and `_insert_tree_entry` cannot create a
    File leaf. `build_repo_manifest_for_paths` propagates either error to its
    caller rather than returning a partial tree.
    """
    tree_entries: list[RepoManifestTreeEntry] = []
    directories: dict[str, RepoManifestDirectoryNode] = {}
    for entry in entries:
        path = (
            entry.right_path
            if entry.right_path is not None
            else entry.left_path
        )
        if path is None:
            raise ValueError("Repo manifest entry is missing both paths.")
        parts = [part for part in PurePosixPath(path).parts if part != "."]
        file_entry: RepoManifestFileEntry = {
            "left_path": entry.left_path,
            "right_path": entry.right_path,
            "file_kind": file_kind_for_repo_entry(entry),
            "lazy": None,
        }
        lazy = _lazy_reason_for_repo_entry(entry)
        if lazy is not None:
            file_entry["lazy"] = lazy
        _insert_tree_entry(
            tree_entries,
            directories,
            parts=parts,
            file_entry=file_entry,
        )
    return _compact_single_directory_chains(_root_files_last(tree_entries))


def build_repo_manifest_for_backend(
    backend: WorkspaceBackendProtocol,
    *,
    left: str,
    right: str,
    show_untracked: bool = False,
) -> RepoManifest:
    """Read one backend diff and build its manifest payload.

    # Parameters

    - `backend`: Backend used to normalize sides and list affected Files.
    - `left`: User-facing left side accepted by that backend.
    - `right`: User-facing right side accepted by that backend.
    - `show_untracked`: Whether supported worktree diffs add untracked Files.

    # Usage

    Use this when the caller needs a manifest directly from a backend and does
    not already have captured `RepoDiffPath` records. Snapshot-backed HTTP code
    uses `build_repo_manifest_for_paths` so it describes the retained capture.

    # Failures

    Side normalization and backend comparison failures propagate as
    `DirdiffError`. Invalid path pairs or inconsistent aggregate totals fail in
    `build_repo_manifest_for_paths`.
    """
    normalized_left = backend.normalize_side(left)
    normalized_right = backend.normalize_side(right)
    diff = backend.repo_diff(
        left=normalized_left,
        right=normalized_right,
        show_untracked=show_untracked,
    )
    return build_repo_manifest_for_paths(
        left_label=normalized_left,
        right_label=normalized_right,
        paths=diff.paths,
        added_lines=diff.added_lines,
        removed_lines=diff.removed_lines,
    )


def build_repo_manifest_for_paths(
    *,
    left_label: str,
    right_label: str,
    paths: list[RepoDiffPath] | tuple[RepoDiffPath, ...],
    added_lines: int | None,
    removed_lines: int | None,
) -> RepoManifest:
    """Build a manifest from captured paths and aggregate Snapshot metadata.

    # Parameters

    - `left_label`: Human-readable label for the captured left side.
    - `right_label`: Human-readable label for the captured right side.
    - `paths`: Captured File pairs in the order used to construct the tree.
    - `added_lines`: Backend-wide added-line total, or `None` if unavailable.
    - `removed_lines`: Backend-wide removed-line total, or `None` if unavailable.

    Added and removed totals must either both exist or both be absent. They are
    backend metadata and are not recomputed from rendered bays.

    # Usage

    The manifest endpoint passes the labels and File records retained by one
    Snapshot. Reuse the same `paths` with `build_lazy_info_for_paths` so eager
    and deferred responses apply one lazy policy and File-kind mapping.

    # Failures

    Mismatched line-total presence raises `AssertionError`. A File with neither
    side path raises `ValueError`; an unsupported tracked change type raises
    `KeyError`.
    """
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
) -> LazyInfo:
    """Return placeholder metadata for only the paths classified as lazy.

    Input order is preserved. Each reason is derived once and supplied to the
    output record; eager paths do not appear in the result.

    # Usage

    Call this with the captured paths used for the manifest, after filtering to
    the current Snapshot. The HTTP boundary attaches the Snapshot id and
    validates the result.

    # Failures

    A lazy tracked entry with a change type outside the `RepoDiffPath` contract
    raises `KeyError` while its File kind is built.
    """
    files: list[LazyInfoFile] = []

    for entry in paths:
        lazy = _lazy_reason_for_repo_entry(entry)
        if lazy is not None:
            files.append(_to_lazy_info_file_entry(entry, lazy))

    return {"files": files}
