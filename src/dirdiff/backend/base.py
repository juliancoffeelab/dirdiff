"""Shared backend contracts and file-loading helpers.

Concrete backends such as `GitBackend` and `PresetBackend` implement
`WorkspaceBackendProtocol` to provide normalized sides, changed path lists, ref
metadata, and exact file contents. This module also defines the text boundary
used by consumers that require decoded input.

It should not know about HTTP endpoints, Snapshot ids, frontend rendering, or
which diff engine will consume the loaded text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from dirdiff.engines import DirdiffError

SideName = str
BranchSource = Literal["local", "remote"]
LazyReason = Literal[
    "too_big", "generated", "deleted", "untracked", "pure_renamed"
]
BUILTIN_SIDES = frozenset({"HEAD", "index", "worktree"})

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "DefaultBaseSelection",
    "DefaultBaseSelectionError",
    "LazyReason",
    "LoadedDiffSides",
    "LocalBranchSelection",
    "RefChoices",
    "RefMetadata",
    "RemoteBranchRef",
    "RemoteBranchSelection",
    "RepoDiff",
    "RepoDiffPath",
    "SideName",
    "StructuredRemoteBranchRef",
    "TextVersion",
    "WorkspaceBackendProtocol",
    "decode_text_content",
    "display_name_for_repo_paths",
    "load_diff_sides",
]


@dataclass(frozen=True)
class TextVersion:
    """One loaded side of a file before it is handed to a diff engine."""

    label: str
    exists: bool
    text: str | None
    error: str | None = None


@dataclass(frozen=True)
class RepoDiffPath:
    """Describe one affected repository filepath pair before rendering.

    At least one side path must be present. Paths are repository-relative;
    `change_type` describes their backend relationship, `untracked` records
    provenance, and `lazy_reason_override` carries only an explicit backend
    loading decision. The record contains no rendered rows or line counts.
    """

    left_path: str | None
    right_path: str | None
    display_name: str
    change_type: Literal["modify", "add", "delete", "rename", "copy"]
    lazy_reason_override: LazyReason | None
    untracked: bool = False


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


class LoadedDiffSides(TypedDict):
    """Loaded left/right text sides returned by `load_diff_sides`.

    `WorkspaceBackendProtocol` objects own path normalization, side-name
    normalization, and exact content loading. `load_diff_sides` applies the
    text requirement and builds this handoff into server-level notebook routing
    or engine rendering: normalized repo paths, display labels, and loaded
    `TextVersion` objects.
    """

    left_path: str | None
    right_path: str | None
    left_label: str
    right_label: str
    left_version: TextVersion
    right_version: TextVersion


def decode_text_content(data: bytes, *, label: str) -> str:
    """Decode exact file contents for a consumer that requires UTF-8 text.

    Loading and Snapshot capture accept arbitrary file contents. Text renderers
    call this boundary only when they need a textual representation; binary or
    non-UTF-8 input is then reported as an unsupported file diff.
    """
    if b"\x00" in data:
        raise DirdiffError(f"{label} appears to be a binary file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DirdiffError(f"{label} is not valid UTF-8 text: {exc}") from exc


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


def load_diff_sides(
    *,
    backend: WorkspaceBackendProtocol,
    left_path: str | None,
    right_path: str | None,
    left: str,
    right: str,
) -> LoadedDiffSides:
    """Load and validate the left/right text sides for one file diff.

    The caller supplies already-resolved side names: raw refs for repository
    Tabs, preset names for the Preset Tab, or merge-base/review refs for Branch
    Review. Missing paths are represented as `TextVersion` values with
    `exists=False` so added/deleted files can still render through the same
    downstream payload builders.

    This is the text-consumer boundary: normalize repo paths and side names, ask
    the selected backend for exact contents, decode each present side, and
    raise `DirdiffError` when a textual diff cannot be produced safely.
    """
    normalized_left = (
        backend.normalize_repo_path(left_path)
        if left_path is not None
        else None
    )
    normalized_right = (
        backend.normalize_repo_path(right_path)
        if right_path is not None
        else None
    )
    normalized_left_side = backend.normalize_side(left)
    normalized_right_side = backend.normalize_side(right)
    left_content = (
        backend.load_version(normalized_left, normalized_left_side)
        if normalized_left is not None
        else None
    )
    right_content = (
        backend.load_version(normalized_right, normalized_right_side)
        if normalized_right is not None
        else None
    )
    if left_content is None and right_content is None:
        raise DirdiffError("The selected file is missing on both sides.")

    left_version = TextVersion(
        label=normalized_left_side,
        exists=left_content is not None,
        text=decode_text_content(
            left_content,
            label=f"{normalized_left_side}:{normalized_left}",
        )
        if left_content is not None
        else None,
    )
    right_version = TextVersion(
        label=normalized_right_side,
        exists=right_content is not None,
        text=decode_text_content(
            right_content,
            label=f"{normalized_right_side}:{normalized_right}",
        )
        if right_content is not None
        else None,
    )

    return {
        "left_path": normalized_left,
        "right_path": normalized_right,
        "left_label": normalized_left_side,
        "right_label": normalized_right_side,
        "left_version": left_version,
        "right_version": right_version,
    }
