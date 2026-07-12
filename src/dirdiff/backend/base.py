"""Shared backend contracts and text-loading helpers.

Concrete backends such as `GitBackend` and `PresetBackend` implement
`WorkspaceBackendProtocol` to provide normalized sides, changed path lists, ref
metadata, and loaded file text.  This module defines those data shapes plus the
small text/unified-diff helpers reused by backend implementations.

It should not know about HTTP endpoints, cache ids, frontend rendering, or
which diff engine will consume the loaded text.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal, Protocol, TypedDict

SideName = str
BranchSource = Literal["local", "remote"]
RepoLazyReason = Literal[
    "too_big", "generated", "deleted", "untracked", "pure_renamed"
]
BUILTIN_SIDES = frozenset({"head", "index", "worktree"})
UNIFIED_HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
    r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
)

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "DefaultBaseSelection",
    "DefaultBaseSelectionError",
    "LoadedDiffSides",
    "LocalBranchSelection",
    "RefChoices",
    "RemoteBranchRef",
    "RemoteBranchSelection",
    "RepoDiffPath",
    "RepoLazyReason",
    "SideName",
    "StructuredRemoteBranchRef",
    "TextDiffError",
    "TextVersion",
    "UnifiedDiffLine",
    "WorkspaceBackendProtocol",
    "display_name_for_repo_paths",
    "load_diff_sides",
    "unified_diff_lines",
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
    """Path-level metadata produced by a backend path listing."""

    left_path: str | None
    right_path: str | None
    display_name: str
    change_type: Literal["modify", "add", "delete", "rename", "copy"]
    lazy_reason_override: RepoLazyReason | None
    changed_lines: int | None = None
    added_lines: int | None = None
    removed_lines: int | None = None
    untracked: bool = False


@dataclass(frozen=True)
class UnifiedDiffLine:
    """One parsed content line from a unified diff hunk.

    This backend-level shape is intentionally not a frontend row.  It records
    the line status, side line numbers, and text extracted from
    `difflib.unified_diff` so engines can project the fallback into their own
    row payloads without each parsing unified-diff headers.
    """

    status: Literal["equal", "insert", "delete"]
    left_no: int | None
    right_no: int | None
    text: str


class TextDiffError(ValueError):
    """Raised when a diff request cannot be fulfilled safely."""


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


class LoadedDiffSides(TypedDict):
    """Loaded left/right text sides returned by `load_diff_sides`.

    `WorkspaceBackendProtocol` objects own path normalization, side-name
    normalization, and text loading.  This bundle is the handoff into
    server-level notebook routing or engine rendering: it contains normalized
    repo paths, display labels for the two selected sides, and the loaded
    `TextVersion` objects.
    """

    left_path: str | None
    right_path: str | None
    left_label: str
    right_label: str
    left_version: TextVersion
    right_version: TextVersion


def _decode_text(data: bytes, *, label: str) -> str:
    """Decode backend bytes as UTF-8 and turn binary data into a request error."""
    if b"\x00" in data:
        raise TextDiffError(f"{label} appears to be a binary file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TextDiffError(f"{label} is not valid UTF-8 text: {exc}") from exc


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


def _count_changed_line_stats(
    left_text: str,
    right_text: str,
) -> tuple[int, int, int]:
    """Count added, removed, and replaced lines for manifest summaries."""
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = SequenceMatcher(
        a=[line.lstrip() for line in left_lines],
        b=[line.lstrip() for line in right_lines],
        autojunk=False,
    )
    added = 0
    removed = 0
    replaced = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_count = i2 - i1
        right_count = j2 - j1
        if tag == "equal":
            replaced += sum(
                1
                for left_line, right_line in zip(
                    left_lines[i1:i2],
                    right_lines[j1:j2],
                    strict=True,
                )
                if left_line != right_line
            )
        elif tag == "delete":
            removed += left_count
        elif tag == "insert":
            added += right_count
        else:
            paired = min(left_count, right_count)
            replaced += paired
            removed += left_count - paired
            added += right_count - paired
    return added, removed, replaced


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

    def current_branch_name(self) -> str:
        """Return the current branch name when the backend has branch metadata."""
        ...

    def list_branch_names(self) -> list[str]:
        """Return local branch choices for branch-review controls."""
        ...

    def list_remote_ref_names(self) -> list[str]:
        """Return remote ref names for freeform ref compares."""
        ...

    def list_remote_names(self) -> list[str]:
        """Return configured remotes known to the backend."""
        ...

    def list_ref_choices(self) -> RefChoices:
        """Return structured ref metadata consumed by repo-ref controls."""
        ...

    def branch_upstream_name(self, branch_name: str) -> str:
        """Return the upstream ref configured for a local branch."""
        ...

    def default_base_selection(self) -> DefaultBaseSelection:
        """Choose the initial branch-review base for this backend."""
        ...

    def preferred_review_selection(
        self, *, base_selection: DefaultBaseSelection | None = None
    ) -> BranchSelection:
        """Choose the initial branch-review target for this backend."""
        ...

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str]:
        """Resolve branch-review selections into label, merge base, and target."""
        ...

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        """List changed paths between two normalized backend sides."""
        ...

    def normalize_repo_path(self, raw_path: str) -> str:
        """Normalize a request path into this backend's repo-relative path form."""
        ...

    def load_version(self, path: str, side: SideName) -> TextVersion:
        """Load one file version for a normalized side and path."""
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

    The caller supplies already-resolved side names: raw refs for ordinary
    modes, preset names for preset mode, or merge-base/review refs for branch
    review.  Missing paths are represented as `TextVersion` values with
    `exists=False` so added/deleted files can still render through the same
    downstream payload builders.

    This is backend logic: normalize repo paths, normalize side names, ask
    the selected backend for text, and raise `TextDiffError` when the selected
    sides cannot be loaded safely.
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
    left_version = (
        backend.load_version(normalized_left, normalized_left_side)
        if normalized_left is not None
        else TextVersion(label=normalized_left_side, exists=False, text=None)
    )
    right_version = (
        backend.load_version(normalized_right, normalized_right_side)
        if normalized_right is not None
        else TextVersion(label=normalized_right_side, exists=False, text=None)
    )

    if left_version.error is not None:
        raise TextDiffError(left_version.error)
    if right_version.error is not None:
        raise TextDiffError(right_version.error)
    if not left_version.exists and not right_version.exists:
        raise TextDiffError("The selected file is missing on both sides.")

    return {
        "left_path": normalized_left,
        "right_path": normalized_right,
        "left_label": normalized_left_side,
        "right_label": normalized_right_side,
        "left_version": left_version,
        "right_version": right_version,
    }


def unified_diff_lines(
    *,
    left_text: str,
    right_text: str,
    left_label: str,
    right_label: str,
) -> list[UnifiedDiffLine]:
    """Return parsed unified-diff content lines for a text pair.

    This helper centralizes the `difflib.unified_diff` fallback used when a
    structural engine cannot produce its normal row model.  It deliberately
    returns backend-level line records rather than dirdiff rows: engines still
    choose how to map those records into their renderer-specific payloads.
    """
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    patch_lines = difflib.unified_diff(
        left_lines,
        right_lines,
        fromfile=left_label,
        tofile=right_label,
        lineterm="",
        n=max(len(left_lines), len(right_lines)),
    )
    lines: list[UnifiedDiffLine] = []
    left_no = 1
    right_no = 1
    in_hunk = False

    for line in patch_lines:
        hunk_match = UNIFIED_HUNK_HEADER_PATTERN.match(line)
        if hunk_match is not None:
            left_no = int(hunk_match.group("left_start"))
            right_no = int(hunk_match.group("right_start"))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("\\"):
            continue

        prefix = " "
        text = ""
        if line != "":
            prefix = line[0]
            text = line[1:]

        if prefix == " ":
            lines.append(
                UnifiedDiffLine(
                    status="equal",
                    left_no=left_no,
                    right_no=right_no,
                    text=text,
                )
            )
            left_no += 1
            right_no += 1
            continue
        if prefix == "-":
            lines.append(
                UnifiedDiffLine(
                    status="delete",
                    left_no=left_no,
                    right_no=None,
                    text=text,
                )
            )
            left_no += 1
            continue
        if prefix == "+":
            lines.append(
                UnifiedDiffLine(
                    status="insert",
                    left_no=None,
                    right_no=right_no,
                    text=text,
                )
            )
            right_no += 1

    return lines
