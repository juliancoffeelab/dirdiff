"""Shared backend contracts and file-loading helpers.

Concrete backends such as `GitBackend` and `PresetBackend` implement
`WorkspaceBackendProtocol` to provide normalized sides, changed path lists, ref
metadata, and exact file contents. Loading stops at bytes: what those bytes are
— text, a notebook, an image — is classified by `dirdiff.formats`, which owns
the definition of text and the decoding that goes with it.

It should not know about HTTP endpoints, Snapshot ids, frontend rendering, or
which diff engine will consume the loaded bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from dirdiff.engines import DirdiffError, git_executable

SideName = str
BranchSource = Literal["local", "remote"]
LazyReason = Literal[
    "too_big", "generated", "deleted", "untracked", "pure_renamed"
]
type GitFileStatus = Literal[
    "modified", "added", "deleted", "renamed", "copied"
]
"""Git-backed File classification published in manifest metadata."""


class GitFileKind(TypedDict):
    """Manifest metadata for one tracked Git File."""

    type: Literal["git"]
    """Discriminator for tracked repository content."""

    status: GitFileStatus
    """Frontend-visible Git change classification."""


class UntrackedFileKind(TypedDict):
    """Manifest metadata for one untracked File."""

    type: Literal["untracked"]
    """Discriminator for worktree content absent from Git."""


type RepoFileKind = GitFileKind | UntrackedFileKind
"""Complete manifest File-kind union consumed by the frontend."""


class RepoManifestSummary(TypedDict):
    """Repository-wide File totals and optional backend line totals.

    File counts partition `changed_files` into added, removed, and updated
    Files; `skipped_files` records entries omitted from loading. Added and
    removed line counts are either both backend-reported integers or both
    `None` when the backend cannot provide aggregate line metadata.
    """

    changed_files: int
    added_files: int
    removed_files: int
    updated_files: int
    added_lines: int | None
    removed_lines: int | None
    skipped_files: int


class RepoManifestFileEntry(TypedDict):
    """File metadata stored at one leaf of the manifest tree.

    At least one repository-relative side path is present. `file_kind`
    describes tracked status or untracked provenance, while `lazy` names why
    the File has not been loaded and is `None` for an eagerly loadable File.
    """

    left_path: str | None
    right_path: str | None
    file_kind: RepoFileKind
    lazy: LazyReason | None


class RepoManifestFileNode(TypedDict):
    """One named File leaf in the recursive manifest tree.

    `type` is the recursive-union discriminator, `name` is the final path
    component displayed at this level, and `entry` carries the File metadata.
    A File node never contains child entries.
    """

    type: Literal["file"]
    name: str
    entry: RepoManifestFileEntry


class RepoManifestDirectoryNode(TypedDict):
    """One named directory containing recursive manifest entries.

    `path` is the complete repository-relative directory path used for stable
    identity. `name` may contain several slash-separated components after
    single-child chain compaction, and `entries` contains the resulting child
    directories and Files.
    """

    type: Literal["directory"]
    name: str
    path: str
    entries: list[RepoManifestTreeEntry]


type RepoManifestTreeEntry = RepoManifestFileNode | RepoManifestDirectoryNode
"""One recursive node in the repository manifest tree."""


class RepoManifest(TypedDict):
    """Complete repository manifest before its Snapshot id is attached.

    Labels describe the compared sides, `summary` carries repository-wide
    totals, and `tree` contains every listed File exactly once. Snapshot
    identity belongs to Room/server orchestration and must not enter this
    backend value.
    """

    display_name: str
    left_label: str
    right_label: str
    summary: RepoManifestSummary
    tree: list[RepoManifestTreeEntry]


class LazyInfoFile(TypedDict):
    """Metadata sufficient to render one unloaded File placeholder.

    Side paths, display name, and File kind reproduce the manifest identity.
    `lazy` is required because this record represents only unloaded Files;
    line counts remain `None` until the File is rendered.
    """

    left_path: str | None
    right_path: str | None
    file_kind: RepoFileKind
    display_name: str
    changed_lines: int | None
    added_lines: int | None
    removed_lines: int | None
    lazy: LazyReason


class LazyInfo(TypedDict):
    """Complete lazy-file metadata response before HTTP validation.

    `files` contains only entries with a concrete lazy reason and preserves
    their backend path order. The response carries no Snapshot identity or
    eagerly loaded File metadata.
    """

    files: list[LazyInfoFile]


BUILTIN_SIDES = frozenset({"HEAD", "index", "worktree"})

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "DefaultBaseSelection",
    "DefaultBaseSelectionError",
    "GitFileKind",
    "GitFileStatus",
    "LazyInfo",
    "LazyInfoFile",
    "LazyReason",
    "LocalBranchSelection",
    "RefChoices",
    "RefMetadata",
    "RemoteBranchRef",
    "RemoteBranchSelection",
    "RepoDiff",
    "RepoDiffPath",
    "RepoFileKind",
    "RepoManifest",
    "RepoManifestDirectoryNode",
    "RepoManifestFileEntry",
    "RepoManifestFileNode",
    "RepoManifestSummary",
    "RepoManifestTreeEntry",
    "SideName",
    "StructuredRemoteBranchRef",
    "UntrackedFileKind",
    "WorkspaceBackendProtocol",
    "display_name_for_repo_paths",
    "git_executable",
]


@dataclass(frozen=True)
class RepoDiffPath:
    """Describe one affected repository filepath pair before rendering.

    At least one side path must be present. Paths are repository-relative;
    `change_type` describes their backend relationship, `untracked` records
    provenance, and `lazy_reason_override` carries only an explicit backend
    loading decision. A side's object id is the backend's content address for
    exactly that side's bytes (a Git blob id); `None` means the side has no
    cheap content identity and must be read to be identified (worktree and
    untracked sides, preset fixtures). The record contains no rendered rows
    or line counts.
    """

    left_path: str | None
    right_path: str | None
    display_name: str
    change_type: Literal["modify", "add", "delete", "rename", "copy"]
    lazy_reason_override: LazyReason | None
    untracked: bool = False
    left_object_id: str | None = None
    right_object_id: str | None = None


@dataclass(frozen=True)
class RepoDiff:
    """Describe one backend comparison before Snapshot content loading.

    `paths` contains every affected File pair. Aggregate line counts are either
    both present and nonnegative or both absent when the backend cannot state
    them. The record contains no loaded bytes or rendered output.
    """

    paths: tuple[RepoDiffPath, ...]
    added_lines: int | None
    removed_lines: int | None


class LocalBranchSelection(TypedDict):
    """Local branch-review selection shared by defaults, JSON, and requests."""

    source: Literal["local"]
    branch: str


class RemoteBranchSelection(TypedDict):
    """Remote branch-review selection shared by defaults, JSON, and requests."""

    source: Literal["remote"]
    branch: str
    remote: str


BranchSelection = LocalBranchSelection | RemoteBranchSelection
"""Branch-review selection with remote branches kept as structured data."""


class DefaultBaseSelectionError(TypedDict):
    """Default-base result when Git metadata cannot identify a safe base."""

    kind: Literal["error"]
    error: Literal["heuristic_fail"]


DefaultBaseSelection = BranchSelection | DefaultBaseSelectionError
"""Default base selection or an explicit unresolved result for the UI."""


class StructuredRemoteBranchRef(TypedDict):
    """Structured remote branch choice used by branch-review controls."""

    remote: str
    branch: str


class RemoteBranchRef(TypedDict):
    """Paired remote-branch autocomplete entry from repository ref metadata."""

    structured: StructuredRemoteBranchRef
    # Use only for freeform ref inputs like Compare Refs. Structured branch
    # review, defaults, and runtime JSON must keep remote and branch separate.
    gitref: str


class RefChoices(TypedDict):
    """Repository ref choices returned by backends for UI controls."""

    builtins: list[str]
    local_branches: list[str]
    remotes: list[str]
    remote_branches: list[RemoteBranchRef]


class RefMetadata(TypedDict):
    """One consistent snapshot of repository branch and remote metadata.

    Read once per request by `GitBackend.read_ref_metadata` and consumed by
    pure derivations, so branch-control responses are computed from a single
    repository observation instead of mixing repeated Git reads. Lists are
    sorted for stable API responses. `current_branch` is empty when HEAD is
    detached. `upstreams` maps only local branches with a configured upstream
    to that upstream's short name. `remote_head_branches` maps only remotes
    whose local `refs/remotes/<remote>/HEAD` symref is present to the default
    branch name it targets. The snapshot must not carry commit ids: capture
    resolution stays a separate explicit step.
    """

    current_branch: str
    local_branches: list[str]
    remote_names: list[str]
    remote_branches: list[StructuredRemoteBranchRef]
    upstreams: dict[str, str]
    remote_head_branches: dict[str, str]


def display_name_for_repo_paths(
    left_path: str | None,
    right_path: str | None,
) -> str:
    """Return the user-visible file label for a left/right repo path pair.

    Path-pair display names are backend metadata: they describe what file the
    backend found before any diff engine renders contents.  Renames and copies
    show both sides, unchanged paths show the single path, and one-sided files
    show the path that exists.
    """
    if left_path is not None and right_path is not None:
        return (
            left_path
            if left_path == right_path
            else f"{left_path} -> {right_path}"
        )
    if left_path is not None:
        return left_path
    if right_path is not None:
        return right_path
    return "(unknown)"


class WorkspaceBackendProtocol(Protocol):
    """Interface implemented by Git and preset-backed workspace backends."""

    @property
    def repo_root(self) -> Path | None:
        """Filesystem root used for display and path validation."""
        ...

    @property
    def cwd(self) -> Path:
        """Working directory used by renderers that need to spawn tools."""
        ...

    def normalize_side(self, raw_side: str) -> SideName:
        """Normalize a user-facing side name into a backend-loadable side."""
        ...

    def discover_default_path(self) -> str:
        """Return the default path for single-file comparisons when available."""
        ...

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str, str]:
        """Resolve branch labels into immutable merge-base and review commits."""
        ...

    def repo_diff(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> RepoDiff:
        """Return affected paths and aggregate counts for normalized sides.

        Both values are absent when the backend reports no totals. Additional
        Files included by `show_untracked` need not participate in them.
        """
        ...

    def normalize_repo_path(self, raw_path: str) -> str:
        """Normalize a request path into this backend's repo-relative path form."""
        ...

    def load_version(self, path: str, side: SideName) -> bytes:
        """Return the exact contents of one present file.

        The path and side must already be normalized for this backend. Loading
        must not decode or classify the contents; consumers apply any textual
        restrictions required by their operation. A listed file that cannot be
        loaded raises `DirdiffError` with the backend's actual failure reason.
        """
        ...

    def load_versions(
        self, requests: tuple[tuple[str, SideName], ...]
    ) -> tuple[bytes | DirdiffError, ...]:
        """Load several exact File sides while retaining per-side failures.

        Results preserve request order. A listed side that cannot be loaded is
        returned as its concrete `DirdiffError`; unexpected failures still
        abort the complete operation.
        """
        ...
