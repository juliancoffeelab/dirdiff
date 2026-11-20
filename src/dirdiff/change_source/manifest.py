from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from dirdiff.change_source.base import (
    FileStatus,
    LazyInfo,
    LazyInfoFile,
    LazyReason,
    RepoFileKind,
    RepoManifest,
    RepoManifestDirectoryNode,
    RepoManifestFileEntry,
    RepoManifestFileNode,
    RepoManifestSummary,
    RepoManifestTreeEntry,
)
from dirdiff.util import Option, Some


@dataclass(frozen=True)
class ManifestPath:
    left_path: Option[str]

    right_path: Option[str]

    display_name: str

    change_type: Literal["modify", "add", "delete", "rename", "copy"]

    lazy_reason_override: LazyReason | None

    untracked: bool

    def __post_init__(self) -> None:
        assert Some.present(self.left_path) or Some.present(self.right_path)


# Case-folded basenames; matched against the final component of either path.
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
FILE_STATUS_BY_CHANGE_TYPE: dict[
    Literal["modify", "add", "delete", "rename", "copy"], FileStatus
] = {
    "modify": "modified",
    "add": "added",
    "delete": "deleted",
    "rename": "renamed",
    "copy": "copied",
}

__all__ = [
    "ManifestPath",
    "RepoManifest",
    "build_lazy_info_for_paths",
    "build_repo_manifest_for_paths",
    "file_kind_for_change_type",
]


def file_kind_for_repo_entry(entry: ManifestPath) -> RepoFileKind:
    if entry.untracked:
        return {"type": "untracked"}
    return {
        "type": "tracked",
        "status": FILE_STATUS_BY_CHANGE_TYPE[entry.change_type],
    }


def file_kind_for_change_type(
    change_type: Literal["modify", "add", "delete", "rename", "copy"],
    *,
    file_kind: Literal["tracked", "untracked"] | None = None,
) -> RepoFileKind:
    if file_kind == "untracked":
        return {"type": "untracked"}
    return {
        "type": "tracked",
        "status": FILE_STATUS_BY_CHANGE_TYPE[change_type],
    }


def _lazy_reason_for_repo_entry(entry: ManifestPath) -> LazyReason | None:
    def _looks_generated_path(path: Option[str]) -> bool:
        return (
            Some.present(path)
            and PurePosixPath(path.value).name.casefold() in GENERATED_FILES
        )

    # Precedence: explicit source reason, untracked, deleted, generated.
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
    entry: ManifestPath, lazy: LazyReason
) -> LazyInfoFile:
    return {
        "left_path": entry.left_path.get(),
        "right_path": entry.right_path.get(),
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
    # `directories` maps full path to node so each ancestor is one dict lookup;
    # scanning siblings was quadratic in wide directories. One tree build only.
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
    # Directories before files, so root files come last in the sidebar and in
    # the frontend's flat depth-first file order.
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
    compacted_entries: list[RepoManifestTreeEntry] = []
    for entry in entries:
        if entry["type"] != "directory":
            compacted_entries.append(entry)
            continue

        # `path` keeps the deepest directory's full path; only the display
        # name is joined.
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
    entries: list[ManifestPath],
) -> list[RepoManifestTreeEntry]:
    tree_entries: list[RepoManifestTreeEntry] = []
    directories: dict[str, RepoManifestDirectoryNode] = {}
    for entry in entries:
        path = (
            entry.right_path
            if Some.present(entry.right_path)
            else entry.left_path
        )
        if not Some.present(path):
            raise ValueError("Repo manifest entry is missing both paths.")
        parts = [
            part for part in PurePosixPath(path.value).parts if part != "."
        ]
        file_entry: RepoManifestFileEntry = {
            "left_path": entry.left_path.get(),
            "right_path": entry.right_path.get(),
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


def build_repo_manifest_for_paths(
    *,
    left_label: str,
    right_label: str,
    paths: list[ManifestPath] | tuple[ManifestPath, ...],
    added_lines: int | None,
    removed_lines: int | None,
) -> RepoManifest:
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
    paths: list[ManifestPath] | tuple[ManifestPath, ...],
) -> LazyInfo:
    files: list[LazyInfoFile] = []

    for entry in paths:
        lazy = _lazy_reason_for_repo_entry(entry)
        if lazy is not None:
            files.append(_to_lazy_info_file_entry(entry, lazy))

    return {"files": files}
