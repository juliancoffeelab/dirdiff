"""Define the common interface and values for workspace backends.

## Public interface

`WorkspaceBackendProtocol` turns backend-specific side names into affected
repository paths and exact File bytes. The records and aliases in this module
carry the branch selections, manifest facts, and ref metadata shared by Git,
presets, Room capture, and the HTTP boundary.

## Purpose and boundaries

This module lets Room capture use any workspace backend without knowing how it
finds refs or loads bytes. Loading stops at bytes. `dirdiff.formats` classifies
and decodes them later. Backend contracts must not publish Snapshots, choose a
diff engine, or define HTTP and frontend presentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from dirdiff.engines import DirdiffError, git_executable

SideName = str
"""A backend-specific name for one loadable side of a workspace comparison.

# Usage
Obtain one from `WorkspaceBackendProtocol.normalize_side`, then pass it back to
that same backend's diff and loading operations.

# Boundaries
A `SideName` is meaningful only to the backend that normalized it. It is not a
repository path, a display label, or proof that the side exists.
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
*Implementation detail: currently it means that frontend won't ask us to diff and
process the file alongside other files when loading the page*

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

    `preset_catalogs` builds these records for the preset picker. Callers send
    `id` back to select the directory and show `display_name` to the user.

    This is catalog metadata. It does not contain fixtures, paths, or loaded
    File content.
    """

    id: str
    """Catalog-relative directory name sent back when the group is selected.

    `PresetBackend.normalize_side` accepts this exact value. It is stable only
    while that directory keeps its name.
    """

    display_name: str
    """Picker label derived from the group directory name.

    It is presentation text and must not be sent back as preset identity.
    """


type GitFileStatus = Literal[
    "modified", "added", "deleted", "renamed", "copied"
]
"""Git-backed File classification published in manifest metadata.

- `modified` keeps the same path on both sides.
- `added` exists only on the right.
- `deleted` exists only on the left.
- `renamed` keeps File identity under a different path.
- `copied` adds another path from existing tracked content.

This status describes the File pair reported by Git. It says nothing about
line-level changes or whether dirdiff has loaded the File.
"""


class GitFileKind(TypedDict):
    """Identify one manifest File as tracked Git content.

    Manifest builders construct this record from backend change metadata. The
    HUD uses `type` to select this variant and `status` to present the Git
    relationship.

    It carries provenance only. Side paths, loading policy, and rendered diff
    data belong to the surrounding manifest entry or composed File.
    """

    type: Literal["git"]
    """Selects the tracked-Git variant when consumers branch on File provenance.

    Its fixed value says the adjacent status is meaningful. It does not prove
    that either side was captured successfully.
    """

    status: GitFileStatus
    """Git's relationship between the two repository paths.

    The HUD uses it for File status presentation. It does not summarize rendered
    line changes.
    """


class UntrackedFileKind(TypedDict):
    """Identify one manifest File as worktree content absent from Git.

    Manifest builders use this variant when the backend marks a path untracked;
    the HUD selects it through the `type` discriminator.

    The record does not state which side contains the File or why it is lazy.
    Those facts belong to `RepoManifestFileEntry`.
    """

    type: Literal["untracked"]
    """Selects the untracked variant for content absent from Git's tracked set.

    Consumers must not expect a Git status field on this variant.
    """


type RepoFileKind = GitFileKind | UntrackedFileKind
"""File provenance carried by manifest and composed-diff payloads.

- `GitFileKind` describes tracked content and includes its Git status.
- `UntrackedFileKind` describes worktree content absent from Git.

Branch on the `type` discriminator. Do not use this union as a File identity,
loading state, or diff result.
"""


class RepoManifestSummary(TypedDict):
    """Repository-wide File totals and optional backend line totals.

    Manifest builders attach this record to `RepoManifest`; the HUD uses it for
    the comparison summary before individual Files are rendered.

    The summary does not aggregate rendered bays and cannot describe per-File
    or per-row changes.
    """

    changed_files: int
    """Count of all manifest File leaves, including untracked Files.

    It equals the sum of the added, removed, and updated File counters.
    """

    added_files: int
    """Count of File pairs classified as backend additions.

    These entries have no left side; untracked worktree Files also contribute.
    """

    removed_files: int
    """Count of File pairs classified as backend deletions.

    These entries have no right side and may be deferred by deleted-File policy.
    """

    updated_files: int
    """Count of modifications, renames, and copies in the manifest.

    This is the remainder after additions and deletions, not a line-change count.
    """

    added_lines: int | None
    """Backend-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` are either both integers or both `None`.
    """

    removed_lines: int | None
    """Backend-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` are either both integers or both `None`.
    """

    skipped_files: int
    """Count of backend entries deliberately omitted from File loading.

    Current builders skip none and report zero. Invalid path pairs fail the build
    instead of incrementing this value.
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
    """Provenance and Git relationship shown for this exact File pair.

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

    Backend manifest builders return this value to Room/server orchestration,
    which adds the Snapshot id before sending it to the HUD.

    Snapshot identity, captured paths, and rendered content stay outside this
    backend value.
    """

    display_name: str
    """Heading shown for the complete Tab comparison.

    Repository manifests use a generic label; the Preset Tab replaces it with
    the selected preset group name.
    """

    left_label: str
    """Snapshot-provided left-side label shown above rendered File content.

    It may describe a ref while capture reads from a frozen commit.
    """

    right_label: str
    """Snapshot-provided right-side label shown above rendered File content.

    It is presentation metadata and need not be a backend-loadable side name.
    """

    summary: RepoManifestSummary
    """Repository-wide totals shown before individual Files render.

    Its line counts remain absent when the backend could not report complete
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
    """Provenance used to render the unloaded File's status treatment.

    It matches the eager manifest entry for the same side-path pair.
    """

    display_name: str
    """Backend-selected File label shown before contents are requested.

    Renames may include both side paths; it is not used to retrieve the File.
    """

    changed_lines: int | None
    """Combined changed-line count before explicit File rendering.

    Lazy placeholders always publish `None`, so the HUD must not present a known
    value until the focused File response replaces this record.
    """

    added_lines: int | None
    """Added-line count before explicit File rendering.

    Lazy placeholders always publish `None`; backend aggregate totals do not
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
    reason and preserves backend path order.

    It does not repeat eager File metadata or carry captured contents.
    """

    files: list[LazyInfoFile]
    """Deferred File entries in the backend's path order.

    Every entry has a concrete lazy reason; eagerly rendered Files are absent.
    """


BUILTIN_SIDES = frozenset({"HEAD", "index", "worktree"})
"""Side names with Git-specific loading behavior.

`GitBackend.normalize_side` accepts these without ref verification. They name
the current commit, staging index, and working tree rather than arbitrary Git
refs. Keep this set aligned with the branches in Git loading and diff commands.
"""

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "DefaultBaseSelection",
    "GitFileStatus",
    "LazyInfo",
    "LazyInfoFile",
    "LazyReason",
    "PresetGroup",
    "RefChoices",
    "RefMetadata",
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
    "WorkspaceBackendProtocol",
    "display_name_for_repo_paths",
    "git_executable",
]


@dataclass(frozen=True)
class RepoDiffPath:
    """Describe one affected repository filepath pair before rendering.

    Backends return these records from `WorkspaceBackendProtocol.repo_diff`.
    Snapshot capture normalizes the paths, loads or identifies each side, and
    persists the resulting File facts.

    This record contains no loaded bytes, Snapshot identity, rendered rows, or
    line counts.
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
    """Backend-selected label for presenting this File before capture.

    It may include both paths for renames and copies. Snapshot identity uses the
    side-path pair instead.
    """

    change_type: Literal["modify", "add", "delete", "rename", "copy"]
    """Relationship used for File counts, Git status, and path-side invariants.

    It describes the File pair before rendering and does not summarize line
    changes.
    """

    lazy_reason_override: LazyReason | None
    """Backend-supplied lazy reason that cannot be derived from other fields.

    Manifest construction uses this value before its own untracked, deleted,
    and generated-file rules. `None` delegates the decision to those rules.
    """

    untracked: bool = False
    """Whether this pair came from Git's untracked worktree listing.

    Manifest construction uses true values for untracked provenance and default
    lazy policy. Preset and tracked Git entries leave it false.
    """

    left_object_id: str | None = None
    """Content identity for the left side, when cheaply available.

    `None` means capture must read the left bytes to establish identity.
    """

    right_object_id: str | None = None
    """Content identity for the right side, when cheaply available.

    `None` means capture must read the right bytes to establish identity.
    """


@dataclass(frozen=True)
class RepoDiff:
    """Describe one backend comparison before Snapshot content loading.

    `WorkspaceBackendProtocol.repo_diff` returns this value. Snapshot capture
    walks `paths` in order and retains the optional totals as Snapshot metadata.

    The record contains no loaded bytes, Snapshot identity, or rendered output.
    """

    paths: tuple[RepoDiffPath, ...]
    """Every affected File pair in the backend's deterministic order.

    Snapshot capture walks this sequence for loading and identity construction;
    callers must not infer aggregate counts from its order.
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

    Defaults, HTTP conversion, and backend resolution pass this record without
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

    Defaults, HTTP conversion, and backend resolution preserve the remote and
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

    `GitBackend.read_ref_metadata` returns this once, then pure derivations build
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

    Path-pair display names are backend metadata: they describe what file the
    backend found before any diff engine renders contents.  Renames and copies
    show both sides, unchanged paths show the single path, and one-sided files
    show the path that exists.

    # Parameters

    - `left_path`: Repository-relative path on the left, or `None` when absent.
    - `right_path`: Repository-relative path on the right, or `None` when absent.

    Both paths absent produces `(unknown)`. Valid backend File pairs always
    supply at least one path, but this function also labels incomplete input
    for diagnostics.

    # Usage

    Backends use this while constructing `RepoDiffPath`; server code uses it
    when older captured metadata has no stored display name. Pass the complete
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


class WorkspaceBackendProtocol(Protocol):
    """Provide workspace comparisons and exact File contents to dirdiff.

    # Usage
    Callers normally construct `dirdiff.backend.GitBackend` or
    `dirdiff.backend.PresetBackend`, normalize user-facing sides and paths with
    that instance, call `repo_diff`, then load the listed File sides through the
    same instance. Branch Review resolves symbolic selections before that flow.

    Implementations return repository-relative paths and complete bytes.
    Expected loading failures use `DirdiffError` with the backend's actual
    reason.

    # Boundaries
    Backends do not publish Snapshots, decode or classify content, choose a diff
    engine, or construct display and HTTP payloads.
    """

    @property
    def repo_root(self) -> Path | None:
        """The stable filesystem root from which this backend reads its input.

        The value is fixed when the backend is constructed.

        `RoomLord` uses a present root to keep its database and Snapshot store
        outside the reviewed files.

        # Invariants
        Reading it must perform no filesystem work and must not fail.

        # Returns
        - `pathblib.Path` to an absolute path containing this backend's source.
        - `None` means the backend is not bound to a repository root.
        """
        # TODO: should it even be None?
        ...

    @property
    def cwd(self) -> Path:
        """Stable absolute command directory supplied to external renderers.

        It is fixed at construction and may differ from `repo_root`. Reading it
        performs no filesystem work and must not fail.

        # Usage

        Use this when an operation must reproduce the command context captured
        when the backend was constructed. File loading remains a backend
        operation and must not be reconstructed relative to this property.
        """
        ...

    def normalize_side(self, raw_side: str) -> SideName:
        """Validate a user-facing side and return this backend's loadable name.

        The result is meaningful only to the same backend instance. Blank,
        unknown, or unsupported sides raise `DirdiffError`.

        # Usage

        Normalize user or persisted side spelling once, then pass the result to
        `repo_diff` and `load_version` on the same backend instance.

        # Failures

        Raises `DirdiffError` when the spelling is blank or cannot identify a
        side supported by this backend.
        """
        ...

    def discover_default_path(self) -> str:
        """Return one repository-relative File path suitable for initial display.

        Backends choose a deterministic present File from current workspace
        state. If none is available, they raise `DirdiffError`.

        # Usage

        Call this when an interface needs one initial File before it has built a
        comparison manifest. Treat the returned value as a repository-relative
        backend path, not an absolute filesystem path.

        # Failures

        Raises `DirdiffError` when the backend cannot find a present File.
        """
        ...

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str, str]:
        """Resolve symbolic branches into labels and immutable capture commits.

        # Parameters

        - `base_selection`: Base branch together with its local or remote source.
        - `review_selection`: Reviewed branch with the same structured source.

        # Usage

        `RoomLord` calls this while preparing a Branch Review capture. Keep the
        labels for presentation and pass the two returned commits to `repo_diff`
        and subsequent File loading on the same backend.

        # Returns

        - First, the displayed base label selected by the caller.
        - Second, the immutable merge-base commit used as capture's left side.
        - Third, the displayed review label selected by the caller.
        - Fourth, the immutable review-head commit used as capture's right side.

        # Failures

        Raises `DirdiffError` when this backend does not support Branch Review,
        either selection cannot be resolved, or no merge base exists.
        """
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

        # Parameters

        - `left`: Normalized side used as the comparison's left input.
        - `right`: Normalized side used as the comparison's right input.
        - `show_untracked`: Whether supported worktree comparisons add untracked Files.

        # Usage

        Call after normalizing or resolving both sides. Room capture walks the
        returned paths in order, normalizes each present path, and loads those
        sides with the same backend instance.

        # Failures

        Raises `DirdiffError` when either side cannot be compared or the backend
        cannot obtain a complete affected-path result.
        """
        ...

    def normalize_repo_path(self, raw_path: str) -> str:
        """Validate input and return this backend's repository-relative path.

        The result stays inside `repo_root` and names a File-shaped path. Blank,
        absolute, escaping, or backend-invalid paths raise `DirdiffError`.

        # Usage

        Normalize every present path returned by `repo_diff` before persisting
        it or passing it to a loading method. Never join an unnormalized value
        to `repo_root` in caller code.

        # Failures

        Raises `DirdiffError` when the value is empty, absolute, escapes the
        workspace, or violates the concrete backend's path rules.
        """
        ...

    def load_version(self, path: str, side: SideName) -> bytes:
        """Return the exact contents of one present file.

        The path and side must already be normalized for this backend. Loading
        must not decode or classify the contents; consumers apply any textual
        restrictions required by their operation. A listed file that cannot be
        loaded raises `DirdiffError` with the backend's actual failure reason.

        # Parameters

        - `path`: Normalized repository-relative path reported for the File side.
        - `side`: Normalized backend side from which to read the bytes.

        # Usage

        Call only for a present side listed by `repo_diff`, after normalizing
        its path and side through this backend. The returned bytes go to capture
        or `dirdiff.formats` without decoding in the backend layer.

        # Failures

        Raises `DirdiffError` when the listed side cannot be read exactly.
        Unexpected implementation failures propagate instead of being turned
        into substitute content.
        """
        ...

    def load_versions(
        self, requests: tuple[tuple[str, SideName], ...]
    ) -> tuple[bytes | DirdiffError, ...]:
        """Load several exact File sides while retaining per-side failures.

        Results preserve request order. A listed side that cannot be loaded is
        returned as its concrete `DirdiffError`; unexpected failures still
        abort the complete operation.

        # Usage

        Room capture passes all eager or deferred side requests in one tuple,
        then zips the result positions back to those requests. Every path and
        side must already be normalized by this backend.

        # Returns

        - Each item corresponds to one input `(path, side)`, preserving input
          order so callers may zip both tuples without another identity key.
        - A `bytes` item is the exact content loaded for that input pair.
        - A `DirdiffError` item is the expected failure for only that input pair;
          successful sibling results remain available.

        # Failures

        Expected per-side `DirdiffError` values occupy their result positions.
        Other exceptions abort the batch and propagate to the caller.
        """
        ...
