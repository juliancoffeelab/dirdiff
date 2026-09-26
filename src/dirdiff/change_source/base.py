"""Define source data and shared facts for Git and preset captures.

## Public interface

`ChangeSourceProtocol` lists selected File changes and loads exact materialized sides.
`FileKey`, `FileSide`, `FileChange`, `WorkspaceChanges`, `FileData`, and `LoadedFile` carry
those facts across the Room capture boundary. Other records here carry ref,
picker, and manifest values used by the HTTP boundary.

## Purpose and boundaries

An observation holds only its own discovered facts and acquired data. This
module does not publish Snapshots, choose engines, or decode
formats. `dirdiff.formats` classifies and decodes supplied bytes later.

*Implementation detail:* Git and preset sources share observation, acquisition,
and link traversal here, using private listing and storage contracts.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol, TypedDict

from dirdiff.engines import DirdiffError, git_executable

SideName = str
"""Internal name for one selected Git ref or preset fixture side.

# Usage
Concrete preparation validates these names before constructing a source.

# Boundaries
A name is meaningful only to its concrete Git or preset storage. It is not a
repository path, a display label, or proof that content exists.
"""

BranchSource = Literal["local", "remote"]
"""Where a branch-review selection is resolved.

- `local` selects a local branch name.
- `remote` selects a branch together with its remote name.

Use this discriminator through `BranchSelection`. It does not encode the
remote or branch name itself.
"""
LazyReason = Literal[
    "too_big", "generated", "deleted", "untracked", "pure_renamed"
]
"""
A reason to mark the file as lazy, which in the end means that we won't
produce the file's contents to the user instantly and instead wait on the user
to ask for it.

*Implementation detail:* currently it means that frontend won't ask us to diff and
process the file alongside other files when loading the page.

Reasons for that are different, and here they are:
- `too_big` is used when file is generally too big, and might not be useful
to review anyway. It's a user call whether to load such file.
- `generated` is used for generated files, whose source of truth lies elsewhere
and hence not useful to review.
- `deleted` is used in cases where mere notion of file being deleted serves
more purpose than what content the file had.
- `untracked` is for files that are not yet added into VCS and hence might
not be desired in diff UI.
- `pure_renamed` similar to `deleted`, mere notion of a file being renamed
is more useful than its content.

It shall be said, that while lazy file's contents are not shown to the reviewer
instantly, such files must have a quick and obvious way to show them anyway.
"""


class PresetGroup(TypedDict):
    """Describe one selectable fixture group within a preset catalog.

    `dirdiff.change_source.PresetCatalog.list_preset_groups` returns these
    records for the preset picker. Callers send
    `id` back to select the directory and show `display_name` to the user.

    This is catalog metadata. It does not contain fixtures, paths, or loaded
    File content.
    """

    id: str
    """Catalog-relative directory name sent back when the group is selected.

    Pass this exact value as `dirdiff.change_source.PresetChangeSource`'s `group`.
    It is stable only while that directory keeps its name.
    """

    display_name: str
    """Picker label derived from the group directory name.

    It is presentation text and must not be sent back as preset identity.
    """


type FileStatus = Literal["modified", "added", "deleted", "renamed", "copied"]
"""Source-independent File relationship published in manifest metadata.

- `modified` keeps the same path on both sides.
- `added` exists only on the right.
- `deleted` exists only on the left.
- `renamed` keeps File identity under a different path.
- `copied` adds another path from existing source content.

The source supplies this classification. It does not describe line changes or
whether the File's data was successfully captured.
"""


class TrackedFileKind(TypedDict):
    """Identify tracked VCS content and its File change status.

    Manifest builders derive this from source tracking and change metadata.
    Side paths, loading policy, and rendered data remain on the enclosing File.

    *Implementation detail:* preset fixtures use this variant because VCS
    tracking does not apply to them.
    """

    type: Literal["tracked"]
    """Select the tracked variant; it does not identify a particular VCS."""

    status: FileStatus
    """Source-reported change status used for HUD presentation."""


class UntrackedFileKind(TypedDict):
    """Identify File content not tracked by the selected VCS.

    The source's untracked fact selects this variant. Side presence and loading
    policy belong to the enclosing File, not this tracking classification.
    """

    type: Literal["untracked"]
    """Select the untracked variant, which has no tracked change status."""


type RepoFileKind = TrackedFileKind | UntrackedFileKind
"""VCS tracking classification in manifest and composed payloads.

- `TrackedFileKind` identifies tracked content and includes its change status.
- `UntrackedFileKind` identifies content not tracked by the VCS.

Branch on `type`; it does not identify a particular VCS, File identity, or
loading state.

*Implementation detail:* presets use the tracked variant because VCS tracking
is not applicable to fixture content.
"""


class RepoManifestSummary(TypedDict):
    """Source-wide File totals and optional source line totals.

    Manifest builders attach this record to `RepoManifest`; the HUD uses it for
    the Tab summary before individual Files are rendered.

    The summary does not aggregate rendered bays and cannot describe per-File
    or per-row changes.
    """

    changed_files: int
    """Count of all manifest File leaves, including untracked Files.

    It equals the sum of the added, removed, and updated File counters.
    """

    added_files: int
    """Count of File pairs classified as source additions.

    These entries have no left side; untracked worktree Files also contribute.
    """

    removed_files: int
    """Count of File pairs classified as source deletions.

    These entries have no right side and may be deferred by deleted-File policy.
    """

    updated_files: int
    """Count of modifications, renames, and copies in the manifest.

    This is the remainder after additions and deletions, not a line-change count.
    """

    added_lines: int | None
    """Source-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` are either both integers or both `None`.
    """

    removed_lines: int | None
    """Source-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` are either both integers or both `None`.
    """

    skipped_files: int
    """Count of source entries deliberately omitted from File loading.

    Invalid path pairs fail the build instead of incrementing this value.

    *Implementation detail:* Current builders skip no Files and report zero.
    """


class RepoManifestFileEntry(TypedDict):
    """File metadata stored at one leaf of the manifest tree.

    Manifest builders create one entry for every affected File pair. Tree code
    places it under a `RepoManifestFileNode`; the HUD later uses its exact path
    pair when asking Room for the File.

    This record has no display name, captured filesystem path, or rendered
    content.
    """

    left_path: str | None
    """Repository-relative left path, or `None` when that side is absent.

    At least one of `left_path` and `right_path` is present.
    """

    right_path: str | None
    """Repository-relative right path, or `None` when that side is absent.

    At least one of `left_path` and `right_path` is present.
    """

    file_kind: RepoFileKind
    """VCS tracking and change status shown for this exact File pair.

    Consumers branch on its discriminator instead of inferring VCS state from
    missing paths or lazy policy.
    """

    lazy: LazyReason | None
    """Policy reason to defer the first render until explicit user action.

    `None` tells initial loading to request the File eagerly. Either value leaves
    the File available for later loading.
    """


class RepoManifestFileNode(TypedDict):
    """One named File leaf in the recursive manifest tree.

    Tree builders wrap one `RepoManifestFileEntry` in this node. Consumers can
    distinguish the leaf from a directory, show its final path component, and
    use its payload for later File operations.

    A File node is always a leaf. It has no directory path or child entries and
    is not itself the File's identity.
    """

    type: Literal["file"]
    """Selects the leaf variant during recursive manifest traversal.

    Consumers stop descending when they encounter this value.
    """

    name: str
    """Final component of the File's destination path, or source path if deleted.

    This is a display label inside its containing directory, not File identity.
    """

    entry: RepoManifestFileEntry
    """Payload used to address and decide initial loading of this File.

    The side-path pair inside it, not the tree label, is the stable lookup input.
    """


class RepoManifestDirectoryNode(TypedDict):
    """One named directory containing recursive manifest entries.

    Manifest tree builders create these nodes around File leaves. Consumers use
    `path` as directory identity and render `entries` recursively.

    Directory nodes organize the manifest only. They do not summarize content
    or select Files for loading.
    """

    type: Literal["directory"]
    """Selects the recursive branch variant during manifest traversal.

    Consumers may descend into child entries only for this value.
    """

    name: str
    """Displayed directory name.

    It may contain several slash-separated components when intermediate
    directories contain no branching choice.
    """

    path: str
    """Complete repository-relative identity of the represented directory.

    Unlike the display name, it is not shortened when single-child directory
    chains are compacted.
    """

    entries: list[RepoManifestTreeEntry]
    """Ordered direct children rendered beneath this directory.

    Directory children precede File children. Single-directory chains may be
    compacted into the containing node's display name.
    """


type RepoManifestTreeEntry = RepoManifestFileNode | RepoManifestDirectoryNode
"""One recursive node in the repository manifest tree.

- `RepoManifestFileNode` terminates a branch with one File entry.
- `RepoManifestDirectoryNode` contains more tree entries.

Consumers branch on `type`. This union represents tree structure, not a
filesystem handle or a captured Snapshot path.
"""


class RepoManifest(TypedDict):
    """Complete repository manifest before its Snapshot id is attached.

    Manifest builders return this value to server orchestration,
    which adds the Snapshot id before sending it to the HUD.

    Snapshot identity, captured paths, and rendered content stay outside this
    source value.
    """

    display_name: str
    """Heading shown for the complete Tab.

    Repository manifests use a generic label; the Preset Tab replaces it with
    the selected preset group name.
    """

    left_label: str
    """Snapshot-provided left-side label shown above rendered File content.

    It may describe a ref while capture reads from a frozen commit.
    """

    right_label: str
    """Snapshot-provided right-side label shown above rendered File content.

    It is presentation metadata and need not be a source-loadable side name.
    """

    summary: RepoManifestSummary
    """Repository-wide totals shown before individual Files render.

    Its line counts remain absent when the source could not report complete
    aggregate counts.
    """

    tree: list[RepoManifestTreeEntry]
    """Affected Files arranged as a recursive directory tree.

    Every listed File occurs exactly once.
    """


class LazyInfoFile(TypedDict):
    """Metadata sufficient to render one unloaded File placeholder.

    Lazy-info builders derive this record from one manifest entry. The HUD uses
    it to construct a deferred File card without copying fields from the
    manifest response.

    This record does not contain File bytes, a Snapshot id, or rendered bays.
    """

    left_path: str | None
    """Repository-relative left lookup path, or `None` when that side is absent.

    At least one side path in the same record is present.
    """

    right_path: str | None
    """Repository-relative right lookup path, or `None` when that side is absent.

    At least one side path in the same record is present.
    """

    file_kind: RepoFileKind
    """VCS tracking used to render the unloaded File's status treatment.

    It matches the eager manifest entry for the same side-path pair.
    """

    display_name: str
    """Source-selected File label shown before contents are requested.

    Renames may include both side paths; it is not used to retrieve the File.
    """

    changed_lines: int | None
    """Combined changed-line count before explicit File rendering.

    Lazy placeholders always publish `None`, so the HUD must not present a known
    value until the focused File response replaces this record.
    """

    added_lines: int | None
    """Added-line count before explicit File rendering.

    Lazy placeholders always publish `None`; source aggregate totals do not
    provide a substitute per-File value.
    """

    removed_lines: int | None
    """Removed-line count before explicit File rendering.

    Lazy placeholders always publish `None`; callers learn it only by rendering
    the focused File.
    """

    lazy: LazyReason
    """Concrete policy reason shown while this File waits for explicit loading.

    Unlike the corresponding manifest field, this value cannot be `None`
    because eager Files are omitted from `LazyInfo`.
    """


class LazyInfo(TypedDict):
    """Complete lazy-file metadata response before HTTP validation.

    Build this after manifest construction and attach the Snapshot identity at
    the HTTP boundary. `files` contains only entries with a concrete lazy
    reason and preserves source path order.

    It does not repeat eager File metadata or carry captured contents.
    """

    files: list[LazyInfoFile]
    """Deferred File entries in the source's path order.

    Every entry has a concrete lazy reason; eagerly rendered Files are absent.
    """


BUILTIN_SIDES = frozenset({"HEAD", "index", "worktree"})
"""Side names with Git-specific loading behavior.

`GitRepository.normalize_side` accepts these without ref verification. They name
the current commit, staging index, and working tree rather than arbitrary Git
refs. Keep this set aligned with the branches in Git loading and diff commands.
"""

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "CapturedLink",
    "ChangeSourceProtocol",
    "DefaultBaseSelection",
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
    """Carry one private Git or preset listing record until source conversion.

    Concrete listing code constructs these records while parsing Git output or
    fixture directories. Observation converts them to `FileChange`
    with canonical present `FileSide` values. Room never sees this type.

    This record contains no loaded bytes, Snapshot identity, rendered rows, or
    line counts.

    @private
    """

    left_path: str | None
    """Repository-relative left path, or `None` when that side is absent.

    At least one of `left_path` and `right_path` must be present.
    """

    right_path: str | None
    """Repository-relative right path, or `None` when that side is absent.

    At least one of `left_path` and `right_path` must be present.
    """

    display_name: str
    """Source-selected label for presenting this File before capture.

    It may include both paths for renames and copies. Snapshot identity uses the
    side-path pair instead.
    """

    change_type: Literal["modify", "add", "delete", "rename", "copy"]
    """Relationship used for File counts, Git status, and path-side invariants.

    It describes the File pair before rendering and does not summarize line
    changes.
    """

    lazy_reason_override: LazyReason | None
    """Source-supplied lazy reason that cannot be derived from other fields.

    Manifest construction uses this value before its own untracked, deleted,
    and generated-file rules. `None` delegates the decision to those rules.
    """

    untracked: bool = False
    """Whether the VCS listing identifies this pair as untracked content.

    Manifest construction uses this fact for tracking classification and lazy
    policy.

    *Implementation detail:* presets leave this false because VCS tracking does
    not apply to fixture content.
    """

    left_object_id: str | None = None
    """Exact stored object id for the left side, when cheaply available.

    `None` means the source must acquire bytes to establish content identity.
    """

    right_object_id: str | None = None
    """Exact stored object id for the right side, when cheaply available.

    `None` means the source must acquire bytes to establish content identity.
    """

    left_properties: FileProperties | None = None
    """Semantic properties of the left side, or `None` with no left path.

    Concrete listing must supply properties for every present side. The source
    asserts path and property presence before converting this private record.
    """

    right_properties: FileProperties | None = None
    """Semantic properties of the right side, or `None` with no right path.

    The source asserts path and property
    presence before converting this private record.
    """

    lazy_metadata_content: str | None = None
    """Complete source policy text read with the lazy reason at discovery."""


@dataclass(frozen=True)
class ListedChanges:
    """Carry a private concrete listing and its aggregate line totals.

    Concrete Git and preset listing code returns this value. `changes()`
    converts it to `WorkspaceChanges`; Room never sees this private shape.

    The record contains no loaded bytes, Snapshot identity, or rendered output.

    @private
    """

    paths: tuple[ListedPath, ...]
    """Every affected File pair in the source's deterministic order.

    The source converts this sequence in order. Its order does not define
    Snapshot equality or aggregate counts.
    """

    added_lines: int | None
    """Aggregate added-line count, or `None` when unavailable.

    This field and `removed_lines` are either both nonnegative integers or both
    `None`.
    """

    removed_lines: int | None
    """Aggregate removed-line count, or `None` when unavailable.

    This field and `added_lines` are either both nonnegative integers or both
    `None`.
    """


class LocalBranchSelection(TypedDict):
    """Select one local branch for Branch Review.

    Defaults, HTTP conversion, and repository preparation pass this record without
    flattening the branch into a Git ref string. `source` selects this union
    variant and `branch` is the local branch name.

    It does not identify a remote or resolved commit.
    """

    source: Literal["local"]
    """Selects resolution through `refs/heads` without a remote component.

    Consumers use this fixed value before reading the local-only record shape.
    """

    branch: str
    """Local branch name resolved specifically below `refs/heads`.

    Callers retain the symbolic name in Room correspondence while each capture
    freezes its current commit.
    """


class RemoteBranchSelection(TypedDict):
    """Select one branch on a named remote for Branch Review.

    Defaults, HTTP conversion, and repository preparation preserve the remote and
    branch names separately. The discriminator selects this union variant.

    It is not a free-form Git ref and does not contain a resolved commit.
    """

    source: Literal["remote"]
    """Selects resolution through one named remote's tracking namespace.

    Consumers use this fixed value before requiring the adjacent remote field.
    """

    branch: str
    """Branch suffix resolved below the selected remote-tracking namespace.

    It must remain separate from the remote so names containing slashes are not
    parsed ambiguously.
    """

    remote: str
    """Configured remote whose tracking namespace contains the branch.

    This is a Git configuration name such as `origin`, not a URL or forge host.
    """


BranchSelection = LocalBranchSelection | RemoteBranchSelection
"""One symbolic branch selection accepted by Branch Review.

- `LocalBranchSelection` names a local branch.
- `RemoteBranchSelection` keeps remote and branch names separate.

Pass this value to branch-resolution operations. It selects symbolic repository
state, not an immutable commit or arbitrary Git ref.
"""


class DefaultBaseSelectionError(TypedDict):
    """Report that Git metadata cannot identify a safe default base branch.

    Default-selection code returns this discriminated record instead of
    inventing a branch. The HUD can show the `heuristic_fail` reason and require
    an explicit selection.

    This is a result value, not an exception and not a valid branch selection.
    """

    kind: Literal["error"]
    """Separates this failure result from selections carrying branch source.

    Consumers inspect it before attempting to send the value to branch resolution.
    """

    error: Literal["heuristic_fail"]
    """Stable HUD reason stating that no safe base could be inferred.

    It asks for explicit user selection and must not be treated as a branch name.
    """


DefaultBaseSelection = BranchSelection | DefaultBaseSelectionError
"""Result of choosing a default Branch Review base.

- `LocalBranchSelection` and `RemoteBranchSelection` are usable defaults.
- `DefaultBaseSelectionError` requires the user to choose explicitly.

Consumers must inspect the discriminator before treating the value as a branch.
It never contains a resolved commit.
"""


class StructuredRemoteBranchRef(TypedDict):
    """Keep a remote branch choice split into remote and branch names.

    Ref metadata returns this shape for Branch Review controls and default-base
    selection. Pass it through structured branch APIs without joining it into a
    free-form Git ref.

    It does not state an upstream relationship or resolved commit.
    """

    remote: str
    """Configured remote offered as a distinct Branch Review choice.

    It remains separate from the branch so remotes containing slashes stay
    unambiguous.
    """

    branch: str
    """Remote-tracking branch suffix associated with the adjacent remote name.

    The pair can be converted to free-form ref spelling, but structured controls
    retain both components.
    """


class RemoteBranchRef(TypedDict):
    """Offer one remote branch to both structured and free-form controls.

    `structured` is the value Branch Review must retain. `gitref` is the joined
    spelling used only by free-form ref inputs such as Compare Refs.

    Do not round-trip Branch Review state through `gitref`; that would discard
    the remote/branch boundary.
    """

    structured: StructuredRemoteBranchRef
    """Remote and branch components safe for structured Branch Review state.

    Callers pass this member without splitting the free-form spelling.
    """

    # Use only for freeform ref inputs like Compare Refs. Structured branch
    # review, defaults, and runtime JSON must keep remote and branch separate.
    gitref: str
    """Joined spelling offered only to controls that accept arbitrary Git refs.

    It may be ambiguous when names contain slashes and must not reconstruct the
    adjacent structured member.
    """


class RefChoices(TypedDict):
    """Group repository ref choices for the HUD controls that accept them.

    `ref_choices` derives this record from one `RefMetadata` observation. Each
    HUD control reads the collection matching the kind of ref it accepts.

    These are suggestions, not proof that a later capture will resolve the ref.
    """

    builtins: list[str]
    """Git side names offered without repository ref enumeration.

    Compare Refs may send these exact values back to `normalize_side`.
    """

    local_branches: list[str]
    """Sorted local branch choices shared by structured and free-form controls.

    Names exclude the `refs/heads` prefix and remain symbolic, not frozen commits.
    """

    remotes: list[str]
    """Sorted Git configuration names offered by the remote selector.

    They are names such as `origin`, not URLs or forge identities.
    """

    remote_branches: list[RemoteBranchRef]
    """Remote branches paired with both supported control representations.

    The list is sorted by remote and branch through the metadata source.
    """


class RefMetadata(TypedDict):
    """Branch and remote metadata collected for one caller operation.

    `GitRepository.read_ref_metadata` returns this once, then pure derivations build
    branch defaults and control choices from the same value. Git reads are not
    protected by a repository lock, so callers must not treat the record as an
    atomic observation of concurrently changing refs and configuration.

    This value describes symbolic refs. It must not carry commit ids or promise
    that repository state remains unchanged after the read.
    """

    current_branch: str
    """Checked-out local branch observed during the shared Git metadata read.

    Detached HEAD is represented by an empty string, not an invented branch.
    """

    local_branches: list[str]
    """All observed `refs/heads` names in lexical order.

    The collection feeds choices and default derivation from the same repository
    observation.
    """

    remote_names: list[str]
    """All names reported by `git remote`, deduplicated and sorted.

    URL validity and remote reachability are outside this metadata field.
    """

    remote_branches: list[StructuredRemoteBranchRef]
    """Observed tracking refs with remote and branch names kept separate.

    Remote HEAD symbolic refs are excluded and reported through their own mapping.
    """

    upstreams: dict[str, str]
    """Configured upstream ref spelling keyed by local branch name.

    Local branches without an upstream are absent.
    """

    remote_head_branches: dict[str, str]
    """Default branch name keyed by remote with a local HEAD symbolic ref.

    Remotes whose HEAD symbolic ref is unavailable are absent.
    """


def display_name_for_repo_paths(
    left_path: str | None,
    right_path: str | None,
) -> str:
    """Return the user-visible file label for a left/right repo path pair.

    Path-pair display names are source metadata: they describe what file the
    source found before any diff engine renders contents.  Renames and copies
    show both sides, unchanged paths show the single path, and one-sided files
    show the path that exists.

    # Parameters

    - `left_path`: Repository-relative path on the left, or `None` when absent.
    - `right_path`: Repository-relative path on the right, or `None` when absent.

    Both paths absent produces `(unknown)`. Valid source File pairs always
    supply at least one path, but this function also labels incomplete input
    for diagnostics.

    # Usage

    Sources use this when labeling discovered Files. Manifest and File response
    builders also derive labels from captured side paths. Pass the complete
    side-path pair instead of choosing one path at the call site.
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


class WorkspaceStorageProtocol(Protocol):
    """Internal operations needed to read selected outer sides and link targets.

    Private Git and preset storage classes explicitly implement this contract.
    Preparation and Snapshot capture never receive this broad object.

    @private
    """

    def normalize_repo_path(self, raw_path: str) -> str:
        """Canonicalize one repository-local path or reject invalid input."""
        ...

    def file_properties(self, path: str, side: SideName) -> FileProperties:
        """Inspect one path without following a link.

        # Parameters

        - `path`: Canonical path in the selected source.
        - `side`: Concrete selected storage state containing that path.

        Missing or unsupported File kinds raise `DirdiffError`.
        """
        ...

    def file_size(self, path: str, side: SideName) -> int:
        """Inspect exact materialized size without loading a full file.

        # Parameters

        - `path`: Canonical reached path inspected before its content read.
        - `side`: Concrete selected storage state containing that path.

        Missing, unsupported, or uninspectable Files raise `DirdiffError`.
        """
        ...

    def load_version(self, path: str, side: SideName) -> bytes:
        """Read one exact materialized outer or reached side.

        # Parameters

        - `path`: Canonical path whose contents must be returned exactly.
        - `side`: Concrete selected storage state containing that path.

        Expected read failures raise `DirdiffError`; programming failures
        propagate.
        """
        ...

    def load_versions(
        self, files: tuple[StorageFile, ...]
    ) -> tuple[bytes | DirdiffError, ...]:
        """Batch read sides in order, retaining expected per-side failures.

        # Returns

        - Each `bytes` value is the exact content for its input position.
        - Each `DirdiffError` value explains only its own failed input, leaving
          successful sibling results in their original positions.
        """
        ...


@dataclass(frozen=True)
class FileKey:
    """Address one canonical source-local path in one selected source slot.

    The key is meaningful only to the source instance that produced it. It
    identifies where to read, not immutable contents or a Snapshot side.
    """

    slot: Literal["left", "right"]
    """Selected source state containing this path, not a display label."""

    path: str
    """Canonical source-local File path, without a leading slash or `..`."""

    def __post_init__(self) -> None:
        """Reject noncanonical or escaping paths before source loading."""
        assert self.slot in {"left", "right"}
        assert self.path not in {"", "."} and not self.path.startswith("/")
        assert "\x00" not in self.path and not self.path.endswith("/")
        assert self.path == posixpath.normpath(self.path)
        assert self.path != ".." and not self.path.startswith("../")


@dataclass(frozen=True)
class FileProperties:
    """Describe the meaning of a present entry independently of its storage.

    RoomLord includes these facts in Snapshot equality. They do not instruct the
    caller how or when to acquire the entry's data.
    """

    kind: Literal["file", "symbolic_link", "repository_reference"]
    """Regular contents, a symbolic link, or another repository's revision.

    A repository reference supplies the referenced revision as readable data;
    it does not include that repository's Files.
    """

    executable: bool
    """Whether a regular File has executable permission; false for other kinds."""

    def __post_init__(self) -> None:
        """Reject entry kinds and permissions outside the semantic contract."""
        assert self.kind in {"file", "symbolic_link", "repository_reference"}
        assert not self.executable or self.kind == "file"


@dataclass(frozen=True)
class FileSide:
    """Describe one present side returned by a source observation.

    # Usage

    Obtain this record from `ChangeSourceProtocol.changes`. Its facts participate
    in Snapshot equality. Pass its containing observed `FileChange` to the same
    source's `load()` for paired data. Storage versions and acquisition timing
    remain private to the source.
    """

    key: FileKey
    """Address of this present side within the same source observation."""

    properties: FileProperties
    """Semantic entry kind and executable permission at observation."""

    content_identity: bytes
    """Opaque equality identity for this observed side's data and link facts.

    Equal values across captures under the same source materialization rules
    identify equal side results. This is not a storage address or necessarily
    a checksum of loaded bytes. Sources namespace incompatible rules. A later
    acquisition failure supplies a different identity in `FileData`.
    """


@dataclass(frozen=True)
class FileChange:
    """Describe one affected File pair in a selected source.

    # Invariants

    At least one side is present. Each present side has the matching selected
    source slot. These are observation facts, not loaded bytes or a Snapshot
    identity.

    # Usage

    Read these records from `WorkspaceChanges.files`. Pass selected records
    to `ChangeSourceProtocol.load`; keep their metadata for manifest
    presentation and Snapshot equality.
    """

    left: FileSide | None
    """Present left side, or `None` when this File was added."""

    right: FileSide | None
    """Present right side, or `None` when this File was deleted."""

    display_name: str
    """Source label for the File pair; never used to address either side."""

    change_type: Literal["modify", "add", "delete", "rename", "copy"]
    """Source classification for manifest status and File counts."""

    untracked: bool
    """Whether this File is untracked by the selected VCS.

    *Implementation detail:* presets report false because VCS tracking does not
    apply to fixture content.
    """

    lazy_reason_override: LazyReason | None
    """Explicit source policy, or `None` for manifest-derived lazy policy."""

    lazy_reason_content: str | None
    """Complete source policy text supplying the override, or `None`.

    This input participates in Snapshot equality even when editing the text
    leaves the selected lazy reason unchanged.

    *Implementation detail:* Preset sources use complete fixture TOML for this
    field.
    """

    def __post_init__(self) -> None:
        """Reject missing sides and mismatched source slots at discovery."""
        assert self.left is not None or self.right is not None
        assert self.left is None or self.left.key.slot == "left"
        assert self.right is None or self.right.key.slot == "right"
        assert (
            self.lazy_reason_content is None
            or self.lazy_reason_override is not None
        )


@dataclass(frozen=True)
class WorkspaceChanges:
    """Describe the complete affected File set for one source observation.

    # Usage

    `ChangeSourceProtocol.changes` returns this record. Iterate `files` for
    presentation or pass selected File records for loading. Source order does
    not determine Snapshot identity.

    # Invariants

    Both aggregate totals are present or absent. Present totals are nonnegative
    and describe tracked changes, excluding untracked Files. They are source
    facts, not renderer-derived counts.
    """

    files: tuple[FileChange, ...]
    """Affected Files in source presentation order, each listed once."""

    added_lines: int | None
    """Tracked added-line total, or `None` when complete totals are unavailable."""

    removed_lines: int | None
    """Tracked removed-line total, with the same presence as `added_lines`."""

    def __post_init__(self) -> None:
        """Require a complete aggregate pair at the source boundary."""
        assert (self.added_lines is None) == (self.removed_lines is None)
        assert self.added_lines is None or self.added_lines >= 0
        assert self.removed_lines is None or self.removed_lines >= 0


@dataclass(frozen=True)
class CapturedLink:
    """Describe the target walk of one successfully acquired symbolic link.

    A walk either reaches a terminal File or stops with an expected diagnosis.
    Both outcomes belong to a successful outer read.

    # Usage

    Read this through `FileData.link`. The outer link's raw payload remains in
    `FileData.content`; reached bytes never replace it. Rendering uses these
    retained facts without reading live source state.
    """

    nested_links: tuple[tuple[str, str], ...]
    """Subsequent visited links as canonical path and displayed payload text.

    Non-UTF-8 payloads are escaped for display; exact outer bytes stay in
    `FileData.content`.
    """

    diagnosis: str | None
    """Expected terminal failure, absent only when final bytes were reached."""

    target_path: str | None
    """Canonical reached non-link path, absent when the walk stopped."""

    target_data: bytes | None
    """Exact reached bytes, absent when the walk stopped with a diagnosis."""

    def __post_init__(self) -> None:
        """Reject incomplete terminal facts at the source-data boundary."""
        assert (self.target_path is None) == (self.target_data is None)
        assert (self.diagnosis is None) != (self.target_data is None)
        assert all(path != "" for path, _ in self.nested_links)


@dataclass(frozen=True)
class LoadedSide:
    """Hold privately acquired outer bytes and optional reached-link facts.

    A regular side has no link. A link retains raw outer target bytes here and
    never substitutes final target bytes for them. The result is not decoded.

    @private
    """

    content: bytes
    """Exact materialized outer side; for a link, its raw target spelling."""

    link: CapturedLink | None
    """Walk facts for a link side, or `None` for a regular File or gitlink."""


@dataclass(frozen=True)
class FileData:
    """Supply one observed side's acquired outcome for Snapshot persistence.

    # Usage

    Read this through `LoadedFile.left` or `LoadedFile.right`, associated with
    the same side of `LoadedFile.change`. The loading contract describes outcomes
    and how they affect the final equality check before persistence.

    The source retains this outcome for its observation, including expected
    failures. Another capture needs a new source to observe current state.
    """

    content_identity: bytes
    """Final side equality identity, including an expected failure when present.

    Successful acquisition keeps the observed identity. A failure discovered
    during later acquisition replaces it. An already observed failure retains
    its identity. RoomLord uses this value in its final equality check.
    """

    content: bytes | DirdiffError
    """Exact outer data, or the expected failure for this side only.

    A link keeps its raw target bytes. A repository reference supplies its
    referenced revision. No format decoding or diagnostic substitution occurs.
    """

    link: CapturedLink | None
    """Acquired link walk, absent for other kinds or a failed outer read."""

    def __post_init__(self) -> None:
        """Require successful outer data for reached-link facts."""
        assert not isinstance(self.content, DirdiffError) or self.link is None


@dataclass(frozen=True)
class LoadedFile:
    """Retain an observed File pair together with its acquired side outcomes.

    `ChangeSourceProtocol.load` returns these records so capture can read File
    metadata and named side data without reconstructing their association.
    Side presence exactly matches the observed change, including failed reads.
    This record is not a persisted File or a rendered diff.
    """

    change: FileChange
    """Exact observed input record, retaining side addresses and File metadata."""

    left: FileData | None
    """Outcome for `change.left`, or `None` only when that side is absent."""

    right: FileData | None
    """Outcome for `change.right`, or `None` only when that side is absent."""

    def __post_init__(self) -> None:
        """Reject acquired outcomes whose presence disagrees with observation."""
        assert (self.left is None) == (self.change.left is None)
        assert (self.right is None) == (self.change.right is None)


StorageFile = tuple[str, SideName, FileProperties | None, str | None]
"""Private positional read input with optional complete discovered facts.

First is the canonical repository path, second the selected storage state,
third semantic properties when already discovered, and fourth an exact stored
object id when available. Absent facts allow live inspection of unlisted paths
reached by a link. Sources supply every available fact for listed entries so
storage reads their observed versions, including mutable index entries.
"""


class ChangeSourceProtocol(Protocol):
    """Observe the affected Files for one selected Tab and supply their data.

    A source fixes the selected inputs and materialization policy for one
    capture. It handles storage access and supplies semantic File facts; callers
    do not choose acquisition order from those facts.

    # Entrypoints

    Construct `dirdiff.change_source.GitChangeSource` with prepared Git sides,
    or `dirdiff.change_source.PresetChangeSource` with a selected fixture group.
    Call `changes()` to observe the selected state. Pass its `FileChange`
    records to `load()` when you need their paired data.

    # Usage

    For application capture, pass the selected source to
    `dirdiff.room_lord.RoomLord.corresponding_room`. That method handles
    observation and acquisition. Its class documentation shows how to capture
    and then compose the retained Files with a diff engine.

    Direct consumers follow this order with one source instance:

    ```python
    changes = source.changes()
    loaded_files = source.load(changes.files)
    ```

    `loaded_files` contains paired `LoadedFile` results. Each result's `change`
    is the observed input record, and `left` and `right` hold its acquired data
    or remain `None` for an absent side.

    RoomLord calls `changes()` for equality facts and, when capture needs data,
    `load(changes.files)` for paired `LoadedFile` results. Keep the observed
    File records: equal reconstructed records are invalid loading inputs.
    Subsets, reordered Files, and duplicates are allowed.

    # Observation and lifetime

    The first successful `changes()` establishes one observation, including
    complete side identities. It may read data to establish those identities.
    Repeated successful calls return the same observation, not refreshed Files.
    Observation is sequential across Files and promises no atomic worktree view.

    The source retains any acquired results until the source is discarded.
    `load()` returns retained results or acquires the exact listed immutable
    versions. It never substitutes newer mutable data or a newer stored version.
    An immutable version can become unavailable before acquisition; that side
    then returns an expected failure with its final failure identity. Acquired
    failures are retained too, so repeated loads do not retry them. Construct a
    new source for another capture. Source instances do not share acquired-result
    state or a cache; callers may retain the immutable returned records.

    # Capture integration

    RoomLord uses the observation's equality facts and asks for data only when
    needed for a Snapshot. Sources must support a capture that calls `changes()`
    without calling `load()`. Loaded content identities report a failure first
    discovered during acquisition so the capture can use that final outcome.

    Sources supply bytes and link facts. See
    `dirdiff.room_lord.RoomLord` for persistence and
    `dirdiff.formats.Composer` for rendering captured contents.
    """

    def changes(self) -> WorkspaceChanges:
        """Observe affected Files and their complete side identities once.

        # Usage

        Call this before `load()`. Use the returned facts for File presentation
        and Snapshot equality. No caller-side storage inspection is needed.
        Repeated successful calls return this same observation.

        # Returns

        A `WorkspaceChanges` containing affected File pairs in source
        presentation order, with canonical side keys, semantic properties, and
        complete content identities. Its aggregate line totals are either both
        present or both absent. The observation contains no loaded bytes,
        although establishing it may acquire data retained inside the source.

        # Failures

        - A failure that prevents a complete listing raises instead of
          returning partial changes; concrete sources document their listing
          failures.
        - Expected outer read failures encountered while observing individual
          sides become side identities and later `FileData.content` errors.
        - A readable link whose target cannot be reached remains a successful
          outer read with a `CapturedLink.diagnosis`.
        - Unexpected failures propagate.
        """
        ...

    def load(
        self,
        files: Sequence[FileChange],
    ) -> Sequence[LoadedFile]:
        """Supply paired data for the observed Files supplied.

        # Parameters

        - `files`: Exact `FileChange` objects returned by this instance's successful
          `changes()`. Equal reconstructed objects and other sources' objects
          are invalid. Subsets, reordering, and duplicates are allowed.

        # Usage

        Call `changes()` first and keep this source alive through loading. An
        empty input returns an empty sequence. Repeated loads return retained
        outcomes without rereading acquired data or retrying expected failures.

        # Returns

        A sequence of `LoadedFile` with exactly the input File count and order.
        Duplicate input Files retain the same acquired side outcomes. Each result carries
        its exact input in `LoadedFile.change`; named left/right fields preserve
        side absence. Each present `FileData.content` has one of these outcomes:

        - `bytes` contains the exact materialized outer data. A symbolic link
          keeps its raw payload here and has separate walk facts in
          `FileData.link`. A target diagnosis is still this successful outcome.
        - `dirdiff.engines.DirdiffError` describes an expected failure to read
          that outer side. Its link facts are absent; successful sibling sides
          remain available in their named fields.

        `FileData.content_identity` is the final equality identity. It agrees
        with the observed identity after success or an already observed failure.
        A failure first discovered by this call changes that side's identity.

        # Failures

        - Calling before successful observation or passing foreign,
          reconstructed, or unlisted Files raises `AssertionError`.
        - Operation-level storage failures may raise
          `dirdiff.engines.DirdiffError`.
        - Unexpected failures propagate.

        These exceptions abort the call rather than producing a per-File
        result sequence.
        """
        ...


def load_file_sides(
    storage: WorkspaceStorageProtocol,
    files: tuple[StorageFile, ...],
    *,
    max_link_target_bytes: int,
) -> tuple[LoadedSide | DirdiffError, ...]:
    """Load selected sides through private storage and walk reached links.

    A normalized path enters the visited set before its target is inspected.
    A repeated path stops before a second load. Absolute and escaping targets
    fail path normalization and become a terminal diagnosis. The returned
    `LoadedSide` always retains the outer raw link bytes separately from final
    reached bytes so capture can persist and authenticate both.

    # Parameters

    - `storage`: Concrete private storage with canonical path, properties, size,
      single-side and batch loading operations.
    - `files`: Source-side records in the caller's required output order.
    - `max_link_target_bytes`: Nonnegative reached-file size limit.

    # Returns

    - Each `LoadedSide` keeps exact outer bytes. Linked sides also keep their
      stopped or reached target facts.
    - Each expected outer `DirdiffError` remains in its input position.
      Expected failures after an outer link load become link diagnoses.

    # Failures

    - An invalid bound or a wrong storage result count asserts.
    - Unexpected storage failures propagate.

    @private
    """
    assert max_link_target_bytes >= 0
    limit = (
        "1 MiB"
        if max_link_target_bytes == 1024 * 1024
        else f"{max_link_target_bytes} bytes"
    )
    outer = storage.load_versions(files)
    assert len(outer) == len(files), "storage returned wrong side count"
    results: list[LoadedSide | DirdiffError] = []
    for (path, side, properties, _object_id), content in zip(
        files, outer, strict=True
    ):
        if isinstance(content, DirdiffError):
            results.append(content)
            continue
        assert properties is not None
        if properties.kind != "symbolic_link":
            results.append(LoadedSide(content, None))
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
                    "stopped: link target is not UTF-8",
                    None,
                    None,
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
                    "stopped: link target is empty or contains NUL",
                    None,
                    None,
                )
                break
            unresolved = str(PurePosixPath(current_path).parent / target)
            try:
                next_path = storage.normalize_repo_path(unresolved)
            except DirdiffError as exc:
                link = CapturedLink(
                    tuple(nested_links), f"stopped: {exc}", None, None
                )
                break
            if next_path in visited:
                link = CapturedLink(
                    tuple(nested_links),
                    f"loop: {next_path} was already visited",
                    None,
                    None,
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
                        f"stopped: link target exceeds {limit}",
                        None,
                        None,
                    )
                    break
                next_content = storage.load_version(next_path, side)
            except DirdiffError as exc:
                link = CapturedLink(
                    tuple(nested_links), f"stopped: {exc}", None, None
                )
                break
            if next_properties.kind != "symbolic_link":
                if len(next_content) > max_link_target_bytes:
                    link = CapturedLink(
                        tuple(nested_links),
                        f"stopped: link target exceeds {limit}",
                        None,
                        None,
                    )
                else:
                    link = CapturedLink(
                        tuple(nested_links), None, next_path, next_content
                    )
                break
            current_path = next_path
            current_content = next_content
        results.append(LoadedSide(content, link))
    return tuple(results)


class SourceFiles:
    """Hold one source observation and supply its exact acquired side results.

    Git and preset sources compose this object after concrete discovery. It
    retains discovered storage versions and data read to establish identity.
    Its lifetime ends with that capture's source; no state survives another
    capture. All storage scheduling and positional acquisition stay here.

    @private
    """

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
        """Observe listed sides with enough acquired data for complete identity.

        # Parameters

        - `storage`: Concrete storage used only within this observation.
        - `listing`: Complete ordered discovery facts and aggregate totals.
        - `left`: Selected storage state for the left slot.
        - `right`: Selected storage state for the right slot.
        - `identity_namespace`: Stable encoding name for these materialization
          rules, including storage decoding and link semantics.
        - `max_link_target_bytes`: Nonnegative reached-target size bound.
        """
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
                data if isinstance(data, DirdiffError) else data.content,
                None if isinstance(data, DirdiffError) else data.link,
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
                    sides[left_key] if left_key is not None else None,
                    sides[right_key] if right_key is not None else None,
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
    """Stable semantic observation returned on every changes call."""

    def _identity(
        self, file: StorageFile, data: LoadedSide | DirdiffError | None
    ) -> bytes:
        """Encode this side's content facts under its materialization namespace.

        Stored immutable identities avoid ordinary data reads. Acquired errors
        replace that evidence with their exact failure reason; link walks add
        all reached facts. Materialization rules, rather than source-instance
        identity, namespace the digest so equal observations span captures.

        # Parameters

        - `file`: Private observed read facts, including any immutable id.
        - `data`: Acquired result, or `None` only for immutable non-link content
          whose identity can be established without reading its data.
        """
        _path, _side, properties, object_id = file
        assert properties is not None
        parts = [self._namespace, properties.kind.encode()]
        if isinstance(data, DirdiffError):
            parts.extend((b"error", str(data).encode()))
        else:
            if object_id is not None:
                parts.extend((b"stored", object_id.encode()))
            else:
                assert isinstance(data, LoadedSide)
                parts.extend((b"bytes", data.content))
            if properties.kind == "symbolic_link":
                assert isinstance(data, LoadedSide) and data.link is not None
                link = data.link
                parts.extend(
                    (
                        json.dumps(
                            link.nested_links,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode(),
                        b""
                        if link.diagnosis is None
                        else link.diagnosis.encode(),
                        b""
                        if link.target_path is None
                        else link.target_path.encode(),
                        b"" if link.target_data is None else link.target_data,
                    )
                )
        digest = hashlib.sha256(b"dirdiff-side-content-v1\0")
        for part in parts:
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
        return digest.digest()

    def load(self, files: Sequence[FileChange]) -> Sequence[LoadedFile]:
        """Acquire observed File pairs, batching remaining side versions once.

        Validate every exact File record before storage work. Duplicate Files
        reuse retained side outcomes; failures stay retained for this source
        observation. Another capture uses a new source.

        # Returns

        Each `LoadedFile` retains its exact input File in input order.

        - `LoadedFile.left` holds the observed left-side outcome and final
          identity, or `None` when the left side is absent.
        - `LoadedFile.right` holds the observed right-side outcome and final
          identity, or `None` when the right side is absent.
        """
        for file in files:
            assert id(file) in self._changes, (
                "File does not belong to this observation"
            )
        remaining = {
            side.key: self._files[side.key]
            for file in files
            for side in (file.left, file.right)
            if side is not None and side.key not in self._acquired
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
                data if isinstance(data, DirdiffError) else data.content,
                None if isinstance(data, DirdiffError) else data.link,
            )
        return tuple(
            LoadedFile(
                file,
                self._acquired[file.left.key]
                if file.left is not None
                else None,
                self._acquired[file.right.key]
                if file.right is not None
                else None,
            )
            for file in files
        )
