from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol, TypedDict

from dirdiff.engines import DirdiffError, git_executable
from dirdiff.util import Nothing, Ok, Option, Result, Some, maybe

SideName = str

LazyReason = Literal[
    "too_big", "generated", "deleted", "untracked", "pure_renamed"
]


class PresetGroup(TypedDict):
    id: str

    display_name: str


type FileStatus = Literal["modified", "added", "deleted", "renamed", "copied"]


class TrackedFileKind(TypedDict):
    type: Literal["tracked"]

    status: FileStatus


class UntrackedFileKind(TypedDict):
    type: Literal["untracked"]


type RepoFileKind = TrackedFileKind | UntrackedFileKind


class RepoManifestSummary(TypedDict):
    changed_files: int

    added_files: int

    removed_files: int

    updated_files: int

    added_lines: int | None

    removed_lines: int | None

    skipped_files: int


class RepoManifestFileEntry(TypedDict):
    left_path: str | None

    right_path: str | None

    file_kind: RepoFileKind

    lazy: LazyReason | None


class RepoManifestFileNode(TypedDict):
    type: Literal["file"]

    name: str

    entry: RepoManifestFileEntry


class RepoManifestDirectoryNode(TypedDict):
    type: Literal["directory"]

    name: str

    path: str

    entries: list[RepoManifestTreeEntry]


type RepoManifestTreeEntry = RepoManifestFileNode | RepoManifestDirectoryNode


class RepoManifest(TypedDict):
    display_name: str

    left_label: str

    right_label: str

    summary: RepoManifestSummary

    tree: list[RepoManifestTreeEntry]


class LazyInfoFile(TypedDict):
    left_path: str | None

    right_path: str | None

    file_kind: RepoFileKind

    display_name: str

    changed_lines: int | None

    added_lines: int | None

    removed_lines: int | None

    lazy: LazyReason


class LazyInfo(TypedDict):
    files: list[LazyInfoFile]


BUILTIN_SIDES = frozenset({"HEAD", "index", "worktree"})

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "CapturedLink",
    "ChangeSourceProtocol",
    "DefaultBaseSelection",
    "DefaultBaseSelectionError",
    "FileChange",
    "FileData",
    "FileKey",
    "FileProperties",
    "FileSide",
    "FileStatus",
    "LazyInfo",
    "LazyInfoFile",
    "LazyReason",
    "ListedChanges",
    "ListedPath",
    "LoadedFile",
    "PresetGroup",
    "RefChoices",
    "RefMetadata",
    "RepoFileKind",
    "RepoManifest",
    "RepoManifestDirectoryNode",
    "RepoManifestFileEntry",
    "RepoManifestFileNode",
    "RepoManifestSummary",
    "RepoManifestTreeEntry",
    "SideName",
    "SourceFiles",
    "StorageFile",
    "StructuredRemoteBranchRef",
    "WorkspaceChanges",
    "WorkspaceStorageProtocol",
    "display_name_for_repo_paths",
    "git_executable",
]


@dataclass(frozen=True)
class ListedPath:
    left_path: str | None

    right_path: str | None

    display_name: str

    change_type: Literal["modify", "add", "delete", "rename", "copy"]

    lazy_reason_override: LazyReason | None

    untracked: bool = False

    left_object_id: str | None = None

    right_object_id: str | None = None

    left_properties: FileProperties | None = None

    right_properties: FileProperties | None = None

    lazy_metadata_content: str | None = None


@dataclass(frozen=True)
class ListedChanges:
    paths: tuple[ListedPath, ...]

    added_lines: int | None

    removed_lines: int | None


class LocalBranchSelection(TypedDict):
    source: Literal["local"]

    branch: str


class RemoteBranchSelection(TypedDict):
    source: Literal["remote"]

    branch: str

    remote: str


BranchSelection = LocalBranchSelection | RemoteBranchSelection


class DefaultBaseSelectionError(TypedDict):
    kind: Literal["error"]

    error: Literal["heuristic_fail"]


type DefaultBaseSelection = Result[BranchSelection, DefaultBaseSelectionError]


class StructuredRemoteBranchRef(TypedDict):
    remote: str

    branch: str


class RemoteBranchRef(TypedDict):
    structured: StructuredRemoteBranchRef

    # Use only for freeform ref inputs like Compare Refs. Structured branch
    # review, defaults, and runtime JSON must keep remote and branch separate.
    gitref: str


class RefChoices(TypedDict):
    builtins: list[str]

    local_branches: list[str]

    remotes: list[str]

    remote_branches: list[RemoteBranchRef]


class RefMetadata(TypedDict):
    current_branch: str

    local_branches: list[str]

    remote_names: list[str]

    remote_branches: list[StructuredRemoteBranchRef]

    upstreams: dict[str, str]

    remote_head_branches: dict[str, str]


def display_name_for_repo_paths(
    left_path: Option[str],
    right_path: Option[str],
) -> str:
    if Some.present(left_path) and Some.present(right_path):
        return (
            left_path.value
            if left_path.value == right_path.value
            else f"{left_path.value} -> {right_path.value}"
        )
    if Some.present(left_path):
        return left_path.value
    if Some.present(right_path):
        return right_path.value
    return "(unknown)"


class WorkspaceStorageProtocol(Protocol):
    def normalize_repo_path(self, raw_path: str) -> str: ...

    def file_properties(self, path: str, side: SideName) -> FileProperties: ...

    def file_size(self, path: str, side: SideName) -> int: ...

    def load_version(self, path: str, side: SideName) -> bytes: ...

    def load_versions(
        self, files: tuple[StorageFile, ...]
    ) -> tuple[Result[bytes, DirdiffError], ...]: ...


@dataclass(frozen=True)
class FileKey:
    slot: Literal["left", "right"]

    path: str

    def __post_init__(self) -> None:
        assert self.slot in {"left", "right"}
        assert self.path not in {"", "."} and not self.path.startswith("/")
        assert "\x00" not in self.path and not self.path.endswith("/")
        assert self.path == posixpath.normpath(self.path)
        assert self.path != ".." and not self.path.startswith("../")


@dataclass(frozen=True)
class FileProperties:
    kind: Literal["file", "symbolic_link", "repository_reference"]

    executable: bool

    def __post_init__(self) -> None:
        assert self.kind in {"file", "symbolic_link", "repository_reference"}
        assert not self.executable or self.kind == "file"


@dataclass(frozen=True)
class FileSide:
    key: FileKey

    properties: FileProperties

    content_identity: bytes


@dataclass(frozen=True)
class FileChange:
    left: Option[FileSide]

    right: Option[FileSide]

    display_name: str

    change_type: Literal["modify", "add", "delete", "rename", "copy"]

    untracked: bool

    lazy_reason_override: LazyReason | None

    lazy_reason_content: str | None

    def __post_init__(self) -> None:
        assert Some.present(self.left) or Some.present(self.right)
        assert not Some.present(self.left) or self.left.value.key.slot == "left"
        assert (
            not Some.present(self.right) or self.right.value.key.slot == "right"
        )
        assert (
            self.lazy_reason_content is None
            or self.lazy_reason_override is not None
        )


@dataclass(frozen=True)
class WorkspaceChanges:
    files: tuple[FileChange, ...]

    added_lines: int | None

    removed_lines: int | None

    def __post_init__(self) -> None:
        assert (self.added_lines is None) == (self.removed_lines is None)
        assert self.added_lines is None or self.added_lines >= 0
        assert self.removed_lines is None or self.removed_lines >= 0


@dataclass(frozen=True)
class CapturedLink:
    nested_links: tuple[tuple[str, str], ...]

    diagnosis: Option[str]

    target_path: Option[str]

    target_data: Option[bytes]

    def __post_init__(self) -> None:
        assert Some.present(self.target_path) == Some.present(self.target_data)
        assert Some.present(self.diagnosis) != Some.present(self.target_data)
        assert all(path != "" for path, _ in self.nested_links)


@dataclass(frozen=True)
class LoadedSide:
    content: bytes

    link: Option[CapturedLink]


@dataclass(frozen=True)
class FileData:
    content_identity: bytes

    content: Result[bytes, DirdiffError]

    link: Option[CapturedLink]

    def __post_init__(self) -> None:
        assert Ok.good(self.content) or not Some.present(self.link)


@dataclass(frozen=True)
class LoadedFile:
    change: FileChange

    left: Option[FileData]

    right: Option[FileData]

    def __post_init__(self) -> None:
        assert Some.present(self.left) == Some.present(self.change.left)
        assert Some.present(self.right) == Some.present(self.change.right)


StorageFile = tuple[str, SideName, FileProperties | None, str | None]


class ChangeSourceProtocol(Protocol):
    def changes(self) -> WorkspaceChanges: ...

    def load(
        self,
        files: Sequence[FileChange],
    ) -> Sequence[LoadedFile]: ...


def load_file_sides(
    storage: WorkspaceStorageProtocol,
    files: tuple[StorageFile, ...],
    *,
    max_link_target_bytes: int,
) -> tuple[Result[LoadedSide, DirdiffError], ...]:
    assert max_link_target_bytes >= 0
    limit = (
        "1 MiB"
        if max_link_target_bytes == 1024 * 1024
        else f"{max_link_target_bytes} bytes"
    )
    outer = storage.load_versions(files)
    assert len(outer) == len(files), "storage returned wrong side count"
    results: list[Result[LoadedSide, DirdiffError]] = []
    for (path, side, properties, _object_id), outcome in zip(
        files, outer, strict=True
    ):
        if not Ok.good(outcome):
            results.append(outcome)
            continue
        content = outcome.value
        assert properties is not None
        if properties.kind != "symbolic_link":
            results.append(Ok(LoadedSide(content, Nothing())))
            continue

        visited: set[str] = set()
        nested_links: list[tuple[str, str]] = []
        current_path = path
        current_content = content
        while True:
            # A repeated normalized path must stop before another target read.
            assert current_path not in visited
            visited.add(current_path)
            try:
                target = current_content.decode("utf-8")
            except UnicodeDecodeError:
                escaped = json.dumps(
                    current_content.decode("utf-8", errors="backslashreplace"),
                    ensure_ascii=False,
                )
                if current_path != path:
                    nested_links.append((current_path, escaped))
                link = CapturedLink(
                    tuple(nested_links),
                    Some("stopped: link target is not UTF-8"),
                    Nothing(),
                    Nothing(),
                )
                break
            displayed_target = (
                target
                if target.isprintable()
                else json.dumps(target, ensure_ascii=False)
            )
            if current_path != path:
                nested_links.append((current_path, displayed_target))
            if target == "" or "\x00" in target:
                link = CapturedLink(
                    tuple(nested_links),
                    Some("stopped: link target is empty or contains NUL"),
                    Nothing(),
                    Nothing(),
                )
                break
            unresolved = str(PurePosixPath(current_path).parent / target)
            try:
                next_path = storage.normalize_repo_path(unresolved)
            except DirdiffError as exc:
                link = CapturedLink(
                    tuple(nested_links),
                    Some(f"stopped: {exc}"),
                    Nothing(),
                    Nothing(),
                )
                break
            if next_path in visited:
                link = CapturedLink(
                    tuple(nested_links),
                    Some(f"loop: {next_path} was already visited"),
                    Nothing(),
                    Nothing(),
                )
                break
            try:
                next_properties = storage.file_properties(next_path, side)
                if (
                    next_properties.kind != "symbolic_link"
                    and storage.file_size(next_path, side)
                    > max_link_target_bytes
                ):
                    link = CapturedLink(
                        tuple(nested_links),
                        Some(f"stopped: link target exceeds {limit}"),
                        Nothing(),
                        Nothing(),
                    )
                    break
                next_content = storage.load_version(next_path, side)
            except DirdiffError as exc:
                link = CapturedLink(
                    tuple(nested_links),
                    Some(f"stopped: {exc}"),
                    Nothing(),
                    Nothing(),
                )
                break
            if next_properties.kind != "symbolic_link":
                if len(next_content) > max_link_target_bytes:
                    link = CapturedLink(
                        tuple(nested_links),
                        Some(f"stopped: link target exceeds {limit}"),
                        Nothing(),
                        Nothing(),
                    )
                else:
                    link = CapturedLink(
                        tuple(nested_links),
                        Nothing(),
                        Some(next_path),
                        Some(next_content),
                    )
                break
            current_path = next_path
            current_content = next_content
        results.append(Ok(LoadedSide(content, Some(link))))
    return tuple(results)


class SourceFiles:
    def __init__(
        self,
        storage: WorkspaceStorageProtocol,
        listing: ListedChanges,
        *,
        left: SideName,
        right: SideName,
        identity_namespace: bytes,
        max_link_target_bytes: int,
    ) -> None:
        assert identity_namespace != b"" and max_link_target_bytes >= 0
        self._storage = storage
        self._namespace = identity_namespace
        self._max_link_target_bytes = max_link_target_bytes
        self._files: dict[FileKey, StorageFile] = {}
        self._acquired: dict[FileKey, FileData] = {}
        pairs: list[tuple[FileKey | None, FileKey | None, ListedPath]] = []
        names = {"left": left, "right": right}
        for file in listing.paths:
            keys: list[FileKey | None] = []
            entries: tuple[
                tuple[
                    Literal["left", "right"],
                    str | None,
                    FileProperties | None,
                    str | None,
                ],
                ...,
            ] = (
                (
                    "left",
                    file.left_path,
                    file.left_properties,
                    file.left_object_id,
                ),
                (
                    "right",
                    file.right_path,
                    file.right_properties,
                    file.right_object_id,
                ),
            )
            for slot, path, properties, object_id in entries:
                assert (path is None) == (properties is None)
                if path is None:
                    keys.append(None)
                    continue
                key = FileKey(slot, storage.normalize_repo_path(path))
                keys.append(key)
                value = (key.path, names[slot], properties, object_id)
                assert key not in self._files or self._files[key] == value
                self._files[key] = value
            pairs.append((keys[0], keys[1], file))
        # Mutable bytes and link walks establish identity now. Retaining their
        # exact results binds later load to this observation without a reread.
        eager = tuple(
            (key, file)
            for key, file in self._files.items()
            if file[3] is None
            or (file[2] is not None and file[2].kind == "symbolic_link")
        )
        acquired = load_file_sides(
            storage,
            tuple(file for _key, file in eager),
            max_link_target_bytes=max_link_target_bytes,
        )
        for (key, storage_file), data in zip(eager, acquired, strict=True):
            self._acquired[key] = FileData(
                self._identity(storage_file, data),
                data.map(lambda side: side.content),
                data.value.link if Ok.good(data) else Nothing(),
            )
        sides = {
            key: FileSide(
                key,
                file[2],
                self._acquired[key].content_identity
                if key in self._acquired
                else self._identity(file, None),
            )
            for key, file in self._files.items()
            if file[2] is not None
        }
        self.changes = WorkspaceChanges(
            tuple(
                FileChange(
                    maybe(left_key).map(sides.__getitem__),
                    maybe(right_key).map(sides.__getitem__),
                    file.display_name,
                    file.change_type,
                    file.untracked,
                    file.lazy_reason_override,
                    file.lazy_metadata_content,
                )
                for left_key, right_key, file in pairs
            ),
            listing.added_lines,
            listing.removed_lines,
        )
        # Index exact observed records for constant-time boundary validation.
        # The observation retains them throughout this source's lifetime.
        self._changes = {id(file) for file in self.changes.files}

    changes: WorkspaceChanges

    def _identity(
        self, file: StorageFile, data: Result[LoadedSide, DirdiffError] | None
    ) -> bytes:
        _path, _side, properties, object_id = file
        assert properties is not None
        parts = [self._namespace, properties.kind.encode()]
        if data is not None and not Ok.good(data):
            parts.extend((b"error", str(data.error).encode()))
        else:
            if object_id is not None:
                parts.extend((b"stored", object_id.encode()))
            else:
                assert data is not None and Ok.good(data)
                parts.extend((b"bytes", data.value.content))
            if properties.kind == "symbolic_link":
                assert data is not None and Ok.good(data)
                link = data.value.link.must()
                # The identity encoding uses empty bytes for absent walk facts;
                # preserve that established encoding at this byte boundary.
                parts.extend(
                    (
                        json.dumps(
                            link.nested_links,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode(),
                        link.diagnosis.map_or(b"", str.encode),
                        link.target_path.map_or(b"", str.encode),
                        link.target_data.get_or(b""),
                    )
                )
        digest = hashlib.sha256(b"dirdiff-side-content-v1\0")
        for part in parts:
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
        return digest.digest()

    def load(self, files: Sequence[FileChange]) -> Sequence[LoadedFile]:
        for file in files:
            assert id(file) in self._changes, (
                "File does not belong to this observation"
            )
        remaining = {
            side.value.key: self._files[side.value.key]
            for file in files
            for side in (file.left, file.right)
            if Some.present(side) and side.value.key not in self._acquired
        }
        loaded = load_file_sides(
            self._storage,
            tuple(remaining.values()),
            max_link_target_bytes=self._max_link_target_bytes,
        )
        for (key, storage_file), data in zip(
            remaining.items(), loaded, strict=True
        ):
            self._acquired[key] = FileData(
                self._identity(storage_file, data),
                data.map(lambda side: side.content),
                data.value.link if Ok.good(data) else Nothing(),
            )
        return tuple(
            LoadedFile(
                file,
                file.left.map(lambda side: self._acquired[side.key]),
                file.right.map(lambda side: self._acquired[side.key]),
            )
            for file in files
        )
