"""Rooms and immutable Snapshots of workspace state.

## Public interface

Callers use `RoomLord.corresponding_room` for an explicit Tab capture. It returns
the corresponding `Room` and the opaque Snapshot key produced or reused by that
capture. Follow-up operations use `RoomLord.find_room` to recover the containing
Room from an existing Snapshot key without reading live workspace state.

A `Room` provides Snapshot metadata, captured File lookup, review Threads, and
continuation of its persisted Tab. It contains multiple Snapshots but retains no
selected Snapshot, so every Snapshot-scoped operation requires the exact key.

## Purpose and boundaries

This module joins workspace backends to `dirdiff.db.RoomStore`: it captures the
Files reported by a backend, publishes immutable Snapshot contents, and returns
Room handles limited by the selected correspondence identity. Callers receive
repository-relative File pairs and validated paths to captured contents;
they never receive staging paths or mutable publication state.

Rendering and HTTP serialization happen after this boundary. A returned
`dirdiff.review.Thread`, rather than `Room`, performs Comment and Thread-lifecycle
writes. `spec/rooms.md` describes the complete Room and Snapshot lifecycle.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Literal, Optional, TypedDict
from uuid import UUID, uuid4

from dirdiff.backend import (
    SYMLINK_MODE,
    BranchSelection,
    GitBackend,
    LazyReason,
    PresetBackend,
    WorkspaceBackendProtocol,
)
from dirdiff.db import (
    ReviewActionRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    SnapshotFileSideRecord,
    SnapshotFileSymlinkRecord,
    SnapshotMetaRecord,
    SnapshotRecord,
    UserProfileRecord,
)
from dirdiff.engines import DirdiffError
from dirdiff.formats import (
    CapturedLink,
    read_captured_link,
    write_captured_link,
)
from dirdiff.review import (
    CreateThread,
    ReviewBatchAction,
    ReviewBatchResult,
    ReviewError,
    Thread,
    apply_review_batch,
    create_thread,
    derive_room_threads,
    get_thread,
    thread_objects,
)
from dirdiff.util import JsonValue

__all__ = [
    "BranchReviewCaptureSelection",
    "CaptureSelection",
    "CapturedFileSide",
    "FileMeta",
    "PresetCaptureSelection",
    "PullRequestCaptureSelection",
    "RevisionsCaptureSelection",
    "Room",
    "RoomLord",
]

RoomTab = Literal[
    "head",
    "refs",
    "branch-review",
    "pull-request",
    "preset",
]
"""One supported Tab identity used by the law of correspondence.

- `head` and `refs` compare revision-like sides.
- `branch-review` compares a base branch with a review branch.
- `pull-request` compares the commits prepared for a forge review.
- `preset` compares one fixture group without a Mark.

`RoomLord` uses the value to select correspondence, capture, and persisted Room
identity. It is not a frontend view state or a diff-engine selection.
"""

ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
"""Backend classification of how one captured File pair changed.

- `modify` keeps the same path on both sides.
- `add` and `delete` are one-sided.
- `rename` relates different paths for one File identity.
- `copy` relates a new path to retained source content.

Snapshot publication stores this value with the File pair. It does not describe
line changes produced later by a diff engine.
"""


@dataclass(frozen=True)
class RevisionsCaptureSelection:
    """Select the head or refs Tab: two explicit side names of one Mark.

    Manifest conversion constructs this value for `head` and `refs`, then
    RoomLord applies the matching correspondence and capture law.

    The value has no Mark, resolved content, or Snapshot id.
    """

    tab: Literal["head", "refs"]
    """Repository revision Tab whose correspondence law this selection uses.

    `head` requires the canonical HEAD/worktree pair; `refs` accepts two
    explicit revision-like sides. Callers must construct the matching value
    before Room selection rather than reinterpret it later.
    """

    left: str
    """Caller-supplied left revision handle for the selected Tab.

    Head capture requires it to normalize to `HEAD`. Refs capture requires a
    nonblank value and resolves commits while preserving `index` or `worktree`.
    """

    right: str
    """Caller-supplied right revision handle for the selected Tab.

    Head capture requires it to normalize to `worktree`; Refs accepts a
    nonblank revision, index, or worktree handle under the same resolution law.
    """

    show_untracked: bool
    """Whether backend discovery includes untracked worktree Files.

    The option affects capture identity through the resulting File set. Callers
    use it only where the selected revision pair includes supported worktree
    content; it does not alter tracked-file classification.
    """


@dataclass(frozen=True)
class BranchReviewCaptureSelection:
    """Select the Branch Review Tab through symbolic base and review branches.

    Manifest conversion constructs this value from validated base and review
    controls, then RoomLord uses it for correspondence and commit resolution.

    Local and remote branches remain structured. The value has no resolved
    commits, Mark identity, or Snapshot state.
    """

    base: BranchSelection
    """Symbolic local or remote branch chosen as review ancestry.

    Room correspondence persists this structure so recapture can resolve a new
    merge base from the same branch choice rather than freeze its old commit.
    """

    review: BranchSelection
    """Symbolic local or remote branch whose changes form the review side.

    It is resolved together with `base`; callers must keep the structured source
    and remote information instead of reducing it to a display label.
    """


@dataclass(frozen=True)
class PullRequestCaptureSelection:
    """Select the Pull Request Tab: prepared URL and frozen commit ids.

    Manifest conversion builds this only from `PreparedPullRequest` output, then
    RoomLord uses the canonical URL for correspondence and the commits for
    capture.

    Preparation has already fetched forge state. This value cannot contain
    symbolic branches or trigger preparation itself.
    """

    url: str
    """Canonical nonblank forge URL identifying the logical Pull Request Room.

    It selects correspondence across recaptures. The prepared commits may
    advance independently while this URL remains the Room identity.
    """

    left_commit: str
    """Complete prepared merge-base commit id used for this capture's left side.

    RoomLord verifies that backend normalization returns the identical full id;
    abbreviated or symbolic revisions are rejected.
    """

    right_commit: str
    """Complete prepared Pull Request head commit id for the right side.

    It must already have been fetched and normalized by preparation. Capture
    never resolves a branch or contacts the forge through this value.
    """


@dataclass(frozen=True)
class PresetCaptureSelection:
    """Select the Preset Tab: one validated catalog and fixture group.

    Manifest conversion builds this after validating the catalog directory and
    fixture group. RoomLord uses the pair as correspondence for a Mark-less
    Room.

    The value does not support repository-backed Tabs.
    """

    catalog: str
    """Nonblank validated preset catalog identity.

    It selects the `PresetBackend` and participates in Room correspondence; the
    value is not opened as a caller-supplied filesystem path.
    """

    subset: str
    """Nonblank fixture-group identity within the selected catalog.

    It selects the old/new preset pair and joins `catalog` in correspondence.
    The actual fixture bytes remain the backend's responsibility.
    """


# One concrete Tab selection: each variant carries exactly the fields its
# Tab's law consumes, so callers never manufacture irrelevant nulls and the
# law never branches on cross-Tab nullability.
CaptureSelection = (
    RevisionsCaptureSelection
    | BranchReviewCaptureSelection
    | PullRequestCaptureSelection
    | PresetCaptureSelection
)
"""Complete capture inputs for one supported Tab.

- `RevisionsCaptureSelection` supplies sides for Head or Refs.
- `BranchReviewCaptureSelection` supplies symbolic branch selections.
- `PullRequestCaptureSelection` supplies a canonical URL and prepared commits.
- `PresetCaptureSelection` supplies a catalog and fixture group.

Manifest conversion constructs the matching variant before asking `RoomLord`
for a Room. No variant contains Snapshot identity, renderer selection, or HTTP
presentation state.
"""

_SNAPSHOT_HASH_DOMAIN = b"dirdiff-snapshot-v9"
"""Versioned prefix that keeps Snapshot equality tied to its token format.

Changing which capture facts participate in identity requires a new value so
old digests cannot be mistaken for the new definition.
"""
_MAX_CAPTURED_LINK_TARGET_BYTES = 1 << 20
"""Largest final link target retained in one immutable Snapshot.

Capture asks the backend for exact size before loading the reached non-link
File, then checks the returned byte count against the same bound. Larger targets
stop the walk with a diagnosis instead of entering Snapshot identity.
"""
_CAPTURE_ERROR_PREAMBLE = (
    "MACHINE-GENERATED BY DIRDIFF\nThe original file could not be captured."
)
"""Stable marker placed before captured backend failure details.

Render and review boundaries use the persisted failure reason, while this text
makes the substituted side unmistakably generated if inspected on disk.
"""


class SnapshotMeta(TypedDict):
    """Metadata captured for a complete Snapshot.

    `Room.meta` combines persisted Room and Snapshot facts for the HTTP
    boundary. This record contains no File metadata or rendered content.
    """

    tab: RoomTab
    """Persisted Tab of the Room containing this Snapshot.

    It governs presentation and continuation rules. The value comes from Room
    identity rather than mutable frontend state or Snapshot metadata.
    """

    left_label: str
    """Human-facing label frozen with the captured left backend state.

    Callers may present it but must not use it as repository identity or assume
    it follows a branch name after capture.
    """

    right_label: str
    """Human-facing label frozen with the captured right backend state.

    It remains paired with this immutable Snapshot even if live refs or
    worktree state later change.
    """

    added_lines: Optional[int]
    """Backend-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` always have equal presence.
    """

    removed_lines: Optional[int]
    """Backend-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` always have equal presence.
    """


class RoomCaptureContext(TypedDict):
    """Expose only the persisted facts needed to continue one agent review.

    `Room.capture_context` returns this to agent continuation so it can build a
    backend and capture the Room's current repository state. Preset Rooms are
    outside that operation.
    """

    tab: Literal["head", "refs", "branch-review", "pull-request"]
    """Repository-backed Tab whose persisted law continuation must replay.

    It tells the HTTP boundary whether to reuse stored revisions or branches or
    prepare fresh Pull Request commits before calling `Room.recapture`.
    """

    mark_id: int
    """Positive persisted Mark id naming the repository used by this Room.

    The continuation boundary resolves the current registered path from it and
    constructs a fresh backend; Room never stores or returns a live backend.
    """

    pull_request_url: Optional[str]
    """Canonical URL to prepare again for a Pull Request Room.

    It is `None` for every other supported Tab.
    """


class FileMeta(TypedDict):
    """Facts that go with capture file pair

    Provided by the workspace backends, and then snapshot machinery and used by
    callers mainly to display information to the user, or to pick which files
    to produce.
    """

    tracked: bool
    """
    Whether a particular change is tracked in VCS or not.

    *Implementation detail: presets always has it as True, since for them it is
    irrelevant.*
    """
    change_type: ChangeType
    """
    What kind of change the file has.
    """
    # TODO: should this be an *override* over *derived* values?
    # Can't backend report lazy reason on its own?
    #
    # At the very least, maybe pick a better name.
    lazy_reason_override: Optional[LazyReason]
    """
    WorkspaceBackend reason to make a file lazy that can't be derived.

    Callers expected to combine it with `tracked`, `change_type` and other
    strategies (like filtering for generated files) to produce a final
    `lazy_reason`.
    """
    capture_error: Optional[str]
    """
    Reported and persisted when snapshot capture couldn't load a file side.

    *Implementation detail: Set by snapshot machinery to avoid aborting entire
    snapshot, while still signaling the error.*
    """


@dataclass(frozen=True)
class CapturedRegularFileSide:
    """Expose one authenticated ordinary captured File side.

    `Room.get` constructs this variant when the File side exists and has no
    relational symbolic-link row. Consumers read `path` as the exact captured
    content and must not look for adjacent format sidecars.

    This variant carries no repository identity, link facts, or format choice.
    """

    path: Path
    """Absolute path to the authenticated captured File bytes."""

    kind: Literal["regular"] = "regular"
    """Discriminator selecting an ordinary captured side without link facts."""


@dataclass(frozen=True)
class CapturedSymlinkFileSide:
    """Expose one authenticated symbolic-link File side.

    `Room.get` constructs this variant only from a relational symbolic-link row
    after checking the raw payload, metadata, and optional target bytes against
    their stored digests. Consumers use `link` directly and never derive or
    probe sidecar paths.

    The raw `path` still contains the outer link target spelling. Reached target
    bytes and chain facts live in `link`.
    """

    path: Path
    """Absolute path to the authenticated raw symbolic-link payload."""

    link: CapturedLink
    """Authenticated chain, diagnosis, and optional reached target content."""

    kind: Literal["symlink"] = "symlink"
    """Discriminator selecting a captured side with required link facts."""


type CapturedFileSide = CapturedRegularFileSide | CapturedSymlinkFileSide
"""Authenticated captured-side variants returned by `Room.get`.

- `CapturedRegularFileSide` contains ordinary captured bytes.
- `CapturedSymlinkFileSide` contains the raw link payload and required link
  facts.

Consumers branch on `kind`. Side absence remains `None` outside this union.
Neither variant carries the enclosing File's repository path or `FileMeta`.
"""


class SnapshotFileDelta(TypedDict):
    """Describe captured side paths changed between two Snapshots.

    `Room.file_delta` returns this to agent continuation. Paths are absolute,
    read-only captured sides rather than repository names or writable workspace
    files.
    """

    added: tuple[Path, ...]
    """Absolute captured side paths whose side/path identity is newly present.

    Every path points into the later immutable Snapshot. The tuple is sorted and
    may include either side of a newly introduced File identity.
    """

    changed: tuple[Path, ...]
    """Absolute later-Snapshot side paths with a changed captured digest.

    The side and repository path exist in both Snapshots; only their immutable
    byte identity differs. Returned paths always name the newer capture.
    """

    removed: tuple[Path, ...]
    """Absolute captured side paths whose side/path identity is no longer present.

    Every path points into the earlier immutable Snapshot so callers can still
    inspect the removed bytes. The tuple is sorted and never names live files.
    """


class Room:
    """Own one correspondence-selected Room's snapshots and hand out `Thread`s.

    Room represents a chunk of continuous work in the workspace.

    Since it owns multiple snapshots, most methods here will require
    `snapshot_id` key, as `Room` doesn't store any of them, and must not store
    any of them.
    *Implementation note: it does store its own hidden identity to ensure
    validity of operations, to avoid handing access to unrelated information.*

    # Thread boundary
    Room is not and must not be responsible for creating or managing comments
    on `Thread`s, that is the responsibility of `Thread` class, `Room`
    only locates and creates threads.
    Exception to this rule is `apply_review_batch`, because it spans multiple
    threads, and is a forced performance optimization.

    # Entrypoints

    The most basic usage is to get a `Room` from `RoomLord.corresponding_room`,
    then call `Room.manifested` to get list of files this room governs,
    and when needed `Room.get` to get exact physical handles for filepaths.

    If you need to create or get a thread, use `get_thread` or `create_thread`.

    For more, read the documentation for individual methods.
    """

    def __init__(
        self,
        *,
        database: RoomStore,
        identity: RoomIdentity,
        staging_path: Path,
        snapshots_path: Path,
        lock_path: Path,
        thread_lock: Lock,
    ) -> None:
        """Create a Room over one correspondence identity.

        # Parameters

        - `database`: Persistence interface for this Room's Snapshot and review
          records.
        - `identity`: Exact correspondence identity that bounds every Room read.
        - `staging_path`: Root for incomplete process-private captures.
        - `snapshots_path`: Root for complete published Snapshot directories.
        - `lock_path`: Cross-process lock file shared by publication and review
          writes.
        - `thread_lock`: In-process lock shared by publication and review writes.

        # Usage note
        Only `RoomLord` constructs this object. The supplied identity limits
        every relational read to this Room but does not select a Snapshot;
        callers must pass the exact key to every public read. The store paths
        locate this Room's durable Snapshot directories and staging area.

        @private
        """
        self._database = database
        self._identity = identity
        self._staging_path = staging_path
        self._snapshots_path = snapshots_path
        self._lock_path = lock_path
        self._thread_lock = thread_lock

    def meta(self, snapshot_id: UUID) -> SnapshotMeta:
        """Return retained Snapshot facts and the containing Room's Tab.

        `snapshot_id` must name a Snapshot belonging to this Room. Unknown or
        cross-Room keys are rejected instead of producing substitute metadata.
        The Tab comes from the Room identity and is not duplicated in Snapshot
        persistence.

        # Usage

        Call this when a Snapshot-scoped response needs labels, Tab identity, or
        backend totals without loading the manifest.

        # Failures

        - Raises `DirdiffError` when the key does not belong to this Room.
        - Raises `AssertionError` when persisted Room data names an unknown Tab.
        """
        record = self._database.snapshot_meta(self._identity, snapshot_id.hex)
        if record is None:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        match self._identity.tab:
            case "head" | "refs" | "branch-review" | "pull-request" | "preset":
                tab: RoomTab = self._identity.tab
            case _:
                raise AssertionError(
                    f"invalid persisted Room Tab: {self._identity.tab!r}"
                )
        return {
            "tab": tab,
            "left_label": record.left_label,
            "right_label": record.right_label,
            "added_lines": record.added_lines,
            "removed_lines": record.removed_lines,
        }

    def manifested(
        self,
        snapshot_id: UUID,
    ) -> Iterator[
        tuple[
            Optional[Path],
            Optional[Path],
            FileMeta,
        ]
    ]:
        """Yield every captured File pair and policy record in Snapshot order.

        `snapshot_id` must belong to this Room. Each nullable `Path` is a
        repository-relative identity, not a physical capture handle, and at
        least one side is present. Files with capture failures remain in the
        iteration with the exact error in `FileMeta` so callers can present the
        complete manifest without treating generated error bytes as source.

        Lazy overrides are loaded once for the Snapshot and must reference its
        Files. The iterator performs no live backend access and does not read
        captured contents.

        # Usage

        Iterate this after capture to build a manifest or delayed-File response.
        Keep each nullable path pair together; that pair is the address required
        by `get`.

        # Returns

        - The iterator yields items in persisted Snapshot File order.
        - Each item's first value is its optional left repository path.
        - Each item's second value is its optional right repository path. At
          least one path is present; neither is a capture path.
        - Each item's third value is the `FileMeta` for that exact path pair,
          including its capture error and loading policy when present.

        # Failures

        - Raises `DirdiffError` when the Snapshot does not belong to this Room.
        - Raises `AssertionError` when persisted File metadata or lazy reasons
          violate the declared value sets.
        """
        record = self._database.snapshot(self._identity, snapshot_id.hex)
        if record is None:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        persisted_reasons = self._database.snapshot_lazy_reasons(
            self._identity,
            snapshot_id.hex,
        )
        lazy_reasons: dict[str, LazyReason] = {}
        for file_id, reason in persisted_reasons.items():
            match reason:
                case (
                    "too_big"
                    | "generated"
                    | "deleted"
                    | "untracked"
                    | "pure_renamed"
                ):
                    lazy_reasons[file_id] = reason
                case _:
                    raise AssertionError(
                        f"invalid persisted lazy reason: {reason!r}"
                    )
        file_ids = {file.id for file in record.files}
        assert lazy_reasons.keys() <= file_ids, (
            "lazy reasons must identify Files in their Snapshot"
        )
        for file in record.files:
            match file.change_type:
                case "modify" | "add" | "delete" | "rename" | "copy":
                    change_type: ChangeType = file.change_type
                case _:
                    raise AssertionError(
                        "invalid persisted File change type: "
                        f"{file.change_type!r}"
                    )
            lazy_reason = lazy_reasons.get(file.id)
            yield (
                Path(file.left.repository_path)
                if file.left is not None
                else None,
                Path(file.right.repository_path)
                if file.right is not None
                else None,
                {
                    "tracked": file.tracked,
                    "change_type": change_type,
                    "lazy_reason_override": lazy_reason,
                    "capture_error": file.error,
                },
            )

    def get(
        self,
        snapshot_id: UUID,
        left: Optional[Path],
        right: Optional[Path],
    ) -> tuple[
        Optional[CapturedFileSide],
        Optional[CapturedFileSide],
        FileMeta,
    ]:
        """Return authenticated captured sides for one filepath pair.

        Input Paths are repository-relative. Each returned side is an ordinary
        or symbolic-link variant selected by relational state. Raw and link
        sidecar bytes have passed their stored digest checks.

        A captured File failure is returned in `FileMeta.capture_error`; callers
        decide whether their boundary presents or classifies that exact reason.

        # Parameters

        - `snapshot_id`: Exact Snapshot containing the manifested pair.
        - `left`: Repository-relative left path, or `None` for an added File.
        - `right`: Repository-relative right path, or `None` for a deleted File.

        # Usage

        Pass one exact pair obtained from `manifested` or a retained review
        placement. Read returned paths only; they point to immutable captured
        contents, never the live workspace.

        # Returns

        - First, the immutable left capture variant, or `None` when the
          manifested pair has no left side.
        - Second, the immutable right capture under the same convention.
        - Third, the matched File's metadata, including any capture failure.

        # Failures

        - Raises `DirdiffError` for an unknown Snapshot, an absent pair, two
          missing sides, or an absolute or parent-traversing repository path.
        - Raises `AssertionError` when persisted metadata is invalid or a
          captured side no longer matches its digest.
        """
        if left is None and right is None:
            raise DirdiffError("left or right filepath is required.")

        for path in (left, right):
            if path is not None and (path.is_absolute() or ".." in path.parts):
                raise DirdiffError(
                    f"Room filepath must be repository-relative: {path}"
                )
            if path is not None and path.as_posix() in {"", "."}:
                raise DirdiffError("Room filepath must identify a file.")

        snapshot_exists, loaded = self._database.snapshot_file(
            self._identity,
            snapshot_id=snapshot_id.hex,
            left_path=left.as_posix() if left is not None else None,
            right_path=right.as_posix() if right is not None else None,
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        if loaded is None:
            raise DirdiffError("Snapshot manifest path is missing.")
        file = loaded.file
        directory = Path(file.path)
        assert directory.is_absolute(), (
            f"persisted Snapshot File path is not absolute: {file.path!r}"
        )
        result: list[Optional[CapturedFileSide]] = []
        for name, side, symlink in (
            ("left", file.left, file.left_symlink),
            ("right", file.right, file.right_symlink),
        ):
            if side is None:
                assert symlink is None, (
                    f"persisted {name} link has no captured File side"
                )
                result.append(None)
                continue
            path = directory / name
            content = path.read_bytes()
            assert hashlib.sha256(content).digest() == side.content_hash, (
                f"Snapshot File content hash mismatch: {path}"
            )
            if symlink is None:
                result.append(CapturedRegularFileSide(path=path))
            else:
                result.append(
                    CapturedSymlinkFileSide(
                        path=path,
                        link=read_captured_link(
                            metadata_path=Path(symlink.metadata_path),
                            metadata_hash=symlink.metadata_hash,
                            target_capture_path=(
                                Path(symlink.target_capture_path)
                                if symlink.target_capture_path is not None
                                else None
                            ),
                            target_hash=symlink.target_hash,
                        ),
                    )
                )
        assert len(result) == 2
        match file.change_type:
            case "modify" | "add" | "delete" | "rename" | "copy":
                change_type = file.change_type
            case _:
                raise AssertionError(
                    f"invalid persisted File change type: {file.change_type!r}"
                )
        lazy_reason: Optional[LazyReason] = None
        if loaded.lazy_reason is not None:
            match loaded.lazy_reason:
                case (
                    "too_big"
                    | "generated"
                    | "deleted"
                    | "untracked"
                    | "pure_renamed"
                ):
                    lazy_reason = loaded.lazy_reason
                case _:
                    raise AssertionError(
                        f"invalid persisted lazy reason: {loaded.lazy_reason!r}"
                    )
        return (
            result[0],
            result[1],
            {
                "tracked": file.tracked,
                "change_type": change_type,
                "lazy_reason_override": lazy_reason,
                "capture_error": file.error,
            },
        )

    def file_delta(
        self, previous_snapshot_id: UUID, snapshot_id: UUID
    ) -> SnapshotFileDelta:
        """Compare persisted File-side hashes without rereading captured bytes.

        # Parameters

        - `previous_snapshot_id`: Earlier Snapshot whose removed side paths are
          reported from its immutable directory.
        - `snapshot_id`: Later Snapshot whose added and changed paths are
          reported.

        # Usage

        Agent continuation calls this after recapture. Use returned absolute
        paths as handles into the two immutable Snapshot directories; do not
        reinterpret them as repository paths.

        # Failures

        - Raises `DirdiffError` unless both keys belong to this Room.
        """
        snapshots = []
        for captured_id in (previous_snapshot_id, snapshot_id):
            snapshot = self._database.snapshot(self._identity, captured_id.hex)
            if snapshot is None:
                raise DirdiffError(f"Unknown snapshot id: {captured_id.hex}")
            snapshots.append(snapshot)

        def sides(
            snapshot: SnapshotRecord,
        ) -> dict[tuple[str, str], tuple[Path, bytes]]:
            """Index present captured sides by side name and repository path.

            Each value carries the immutable capture path and persisted digest.
            The helper is called once for each already-validated Snapshot
            and never reads the side's bytes.

            # Returns

            - Each key's first item is the `left` or `right` side name. Both
              present sides of one File receive separate entries.
            - Each key's second item is that side's repository path.
            - Each value's first item is the absolute immutable capture path.
            - Each value's second item is the persisted content digest.
            """
            indexed: dict[tuple[str, str], tuple[Path, bytes]] = {}
            for file in snapshot.files:
                for side, record in (
                    ("left", file.left),
                    ("right", file.right),
                ):
                    if record is not None:
                        indexed[(side, record.repository_path)] = (
                            Path(file.path) / side,
                            record.content_hash,
                        )
            return indexed

        previous = sides(snapshots[0])
        current = sides(snapshots[1])
        return {
            "added": tuple(
                sorted(
                    current[key][0] for key in current.keys() - previous.keys()
                )
            ),
            "changed": tuple(
                sorted(
                    current[key][0]
                    for key in current.keys() & previous.keys()
                    if current[key][1] != previous[key][1]
                )
            ),
            "removed": tuple(
                sorted(
                    previous[key][0] for key in previous.keys() - current.keys()
                )
            ),
        }

    def captured_files_for_pairs(
        self,
        snapshot_id: UUID,
        pairs: tuple[tuple[Optional[str], Optional[str]], ...],
    ) -> dict[
        tuple[Optional[str], Optional[str]],
        tuple[Optional[Path], Optional[Path]],
    ]:
        """Return actual retained side paths for exactly the requested pairs.

        Each pair is a repository-path pair coming from this Room's own
        Thread placements, so an absent pair is an invariant violation, not
        caller input; an unknown Snapshot raises. One read and transaction
        serves every pair; contents are never read, and only the returned
        Files are stat-checked.

        # Parameters

        - `snapshot_id`: Exact Snapshot holding all requested placements.
        - `pairs`: Repository-path pairs taken from those placements.

        # Usage

        Build `pairs` from already validated Thread placements, then use the
        returned paths to translate review locations for the agent boundary.

        # Returns

        - Each key is one distinct requested repository path pair. Its first item
          is the nullable left path and its second is the nullable right path.
        - Each value's first item is the optional absolute left capture path.
        - Each value's second item is the optional absolute right capture path.
          A side is `None` only when the corresponding key item is absent.

        # Failures

        - Raises `DirdiffError` for an unknown Snapshot.
        - Raises `AssertionError` when a placement pair or captured side is
          missing from the immutable Snapshot.
        """
        snapshot_exists, found = self._database.snapshot_files_by_pairs(
            self._identity,
            snapshot_id=snapshot_id.hex,
            pairs=pairs,
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        result: dict[
            tuple[Optional[str], Optional[str]],
            tuple[Optional[Path], Optional[Path]],
        ] = {}
        for pair in dict.fromkeys(pairs):
            record = found.get(pair)
            assert record is not None, (
                "Thread location references a File absent from its Snapshot"
            )
            directory = Path(record.path)
            left_file = directory / "left" if record.left is not None else None
            right_file = (
                directory / "right" if record.right is not None else None
            )
            assert left_file is None or left_file.is_file()
            assert right_file is None or right_file.is_file()
            result[pair] = (left_file, right_file)
        return result

    def locate_captured_files(
        self, snapshot_id: UUID, captured: tuple[Path, ...]
    ) -> dict[
        Path,
        Optional[
            tuple[Optional[Path], Optional[Path], Literal["left", "right"]]
        ],
    ]:
        """Identify captured side paths as their repository pairs and sides.

        Every supplied path receives an entry; `None` means it does not name
        a captured side of this exact Snapshot. The captured layout is
        `<file-directory>/<side>` with the File id as the directory name; the
        persisted record's own directory must equal the supplied parent, so a
        lookalike path outside the Snapshot store never matches. One read
        serves every path; no side content is touched.

        # Parameters

        - `snapshot_id`: Exact Snapshot against which paths are validated.
        - `captured`: Absolute paths supplied at the agent action boundary.

        # Usage

        Pass agent-supplied captured paths here before converting them to review
        targets. A `None` result is an unauthenticated path and must be rejected
        by the caller.

        # Returns

        - Each key is one distinct absolute input path, whether valid or not.
        - A valid value's first item is the File's optional left repository path.
        - Its second item is the optional right repository path.
        - Its third item is the matched `left` or `right` side name.
        - `None`: The key does not identify a present side of this exact
          Snapshot. The caller must reject it as an untrusted captured handle.

        # Failures

        - Invalid or unrelated paths are returned as `None`; this method does
          not raise merely because an input fails validation.
        """

        def parsed_file_id(path: Path) -> Optional[str]:
            """Extract an untrusted candidate File id from a capture-side path.

            The helper accepts only `<hex-id>/left` or `<hex-id>/right` shape and
            returns `None` for every other input. A candidate is not trusted
            until the focused database read proves its exact persisted parent.

            # Returns

            - The 32-character lowercase hexadecimal directory name when the
              path has the expected captured-side shape.
            - `None`: The basename is not a side name or its parent is not a
              syntactically valid File id. The caller excludes it from lookup.
            """
            if path.name != "left" and path.name != "right":
                return None
            file_id = path.parent.name
            if len(file_id) != 32 or any(
                character not in "0123456789abcdef" for character in file_id
            ):
                return None
            return file_id

        distinct = tuple(dict.fromkeys(captured))
        candidate_ids = tuple(
            dict.fromkeys(
                file_id
                for file_id in (parsed_file_id(path) for path in distinct)
                if file_id is not None
            )
        )
        records = self._database.snapshot_files_by_ids(
            self._identity,
            snapshot_id=snapshot_id.hex,
            file_ids=candidate_ids,
        )
        result: dict[
            Path,
            Optional[
                tuple[Optional[Path], Optional[Path], Literal["left", "right"]]
            ],
        ] = {}
        for path in distinct:
            result[path] = None
            file_id = parsed_file_id(path)
            if file_id is None:
                continue
            record = records.get(file_id)
            if record is None or Path(record.path) != path.parent:
                continue
            side: Literal["left", "right"] = (
                "left" if path.name == "left" else "right"
            )
            if (record.left if side == "left" else record.right) is None:
                continue
            left = (
                Path(record.left.repository_path)
                if record.left is not None
                else None
            )
            right = (
                Path(record.right.repository_path)
                if record.right is not None
                else None
            )
            result[path] = (left, right, side)
        return result

    def threads(
        self,
        snapshot_id: UUID,
        *,
        page: int,
        limit: int,
        state: Literal["all", "open"],
        through_activity_id: Optional[int],
        attention: Optional[Literal["author", "reviewer"]] = None,
    ) -> tuple[tuple[Thread, ...], int, int]:
        """Return one bounded Thread page and its inclusive activity pivot.

        `None` selects the latest Room activity in the same persistence read.
        Passing a returned concrete pivot makes later pages observe the same
        Thread existence, lifecycle state, ordering, count, and actions.

        # Parameters

        - `snapshot_id`: Exact Snapshot whose placements are selected.
        - `page`: One-based page number.
        - `limit`: Positive maximum number of Threads in this page.
        - `state`: Lifecycle filter for all Threads or only open Threads.
        - `through_activity_id`: Inclusive pivot from page one, or `None` to
          choose it with this read.
        - `attention`: Optional open-Thread role filter used by agent inboxes.

        # Usage

        Start with `through_activity_id=None`, retain the concrete pivot from the
        result, and pass it to each later page. Use the returned total to stop
        paging rather than selecting a new pivot.

        # Returns

        - First, the selected page of `Thread` handles in review order.
        - Second, the total number of Threads matching the filters before page
          slicing.
        - Third, the concrete inclusive activity pivot used to build this page;
          callers reuse it for every later page of the same listing.

        # Failures

        - Asserts when `page` or `limit` is less than one.
        - Raises `DirdiffError` when the Snapshot does not belong to this Room.
        """
        assert page >= 1 and limit >= 1
        return thread_objects(
            database=self._database,
            identity=self._identity,
            snapshot_id=snapshot_id,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
            offset=(page - 1) * limit,
            limit=limit,
            state=state,
            attention=attention,
            through_activity_id=through_activity_id,
        )

    def get_thread(self, snapshot_id: UUID, thread_id: UUID) -> Thread:
        """Return one Thread bound to the exact Snapshot and Thread IDs.

        # Parameters

        - `snapshot_id`: Exact code universe in which the Thread must be placed.
        - `thread_id`: Stable discussion identity to bind.

        # Usage

        Use this when the caller already has a Thread id. The returned handle
        performs reads and writes only through this Snapshot placement.

        # Failures

        - Raises `ReviewError` when the Snapshot or Thread placement does not
          exist in this Room.
        """
        return get_thread(
            database=self._database,
            identity=self._identity,
            snapshot_id=snapshot_id,
            thread_id=thread_id,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )

    def thread_for_comment(self, snapshot_id: UUID, comment_id: UUID) -> Thread:
        """Return the bound Thread whose discussion contains one exact Comment.

        Comment-addressed HTTP writes know only the Comment key; this lookup
        locates its placed Thread in the exact Snapshot and returns it bound,
        or rejects an unknown Comment.

        # Parameters

        - `snapshot_id`: Exact Snapshot containing the Thread placement.
        - `comment_id`: Comment whose stable identity addresses the discussion.

        # Usage

        Comment edit and delete routes use this when their HTTP address carries
        a Comment id but no Thread id.

        # Failures

        - Raises `ReviewError` when no Thread placed in this Snapshot contains
          the Comment.
        """
        thread_id = self._database.review_thread_for_comment(
            snapshot_id.hex, comment_id.hex
        )
        if thread_id is None:
            raise ReviewError(
                "comment_not_found", f"Unknown Comment: {comment_id.hex}"
            )
        return self.get_thread(snapshot_id, UUID(hex=thread_id))

    def create_thread(
        self,
        snapshot_id: UUID,
        command: CreateThread,
    ) -> Thread:
        """Create one Thread in the exact Snapshot and return it bound.

        # Parameters

        - `snapshot_id`: Origin Snapshot whose captured range was selected.
        - `command`: Valid target, author, and first Comment supplied by the
          caller.

        # Usage

        Construct `CreateThread` from one validated code target and author, then
        use the returned bound Thread for the response or later operations.

        # Failures

        - Raises `ReviewError` when the Snapshot, author, target, or supplied
          identities are invalid or already used.
        """
        return create_thread(
            database=self._database,
            identity=self._identity,
            snapshot_id=snapshot_id,
            command=command,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )

    def latest_activity_id(self, snapshot_id: UUID) -> int:
        """Return the Room's current activity boundary without Thread hydration.

        The Snapshot must belong to this Room; the boundary is 0 for a Room
        with no review actions yet.

        # Usage

        Record this cursor with an agent capture when later continuation should
        return only review actions authored afterward.

        # Failures

        - Raises `DirdiffError` when the Snapshot does not belong to this Room.
        """
        self.meta(snapshot_id)
        return self._database.review_latest_activity_id(self._identity)

    def continuation(
        self,
        snapshot_id: UUID,
        activity_id: int,
        limit: int,
    ) -> tuple[
        tuple[ReviewActionRecord, ...],
        bool,
        int,
        tuple[UserProfileRecord, ...],
    ]:
        """Return one bounded ordered page of later Thread changes.

        The page, has-more marker, open-Thread count, and acting Profiles are
        read consistently in one persistence read; the count holds at the
        page's inclusive end boundary.

        # Parameters

        - `snapshot_id`: Snapshot used to prove this Room is the intended one.
        - `activity_id`: Exclusive lower boundary retained by the agent.
        - `limit`: Positive maximum number of later actions.

        # Usage

        Begin with the cursor retained from capture or the prior page. Continue
        using the last returned action id until the has-more value is false.

        # Returns

        - First, at most `limit` later actions in activity order.
        - Second, whether another action exists after this page.
        - Third, the open logical Thread count at the page's inclusive end.
        - Fourth, current Profile records for every author in the returned page.

        # Failures

        - Raises `DirdiffError` for a Snapshot outside this Room.
        - Asserts when the activity id is negative or `limit` is not positive.
        """
        self.meta(snapshot_id)
        return self._database.review_continuation(
            self._identity,
            activity_id,
            limit,
        )

    def review_attention_counts(
        self, snapshot_id: UUID, through_activity_id: int
    ) -> dict[Literal["author", "reviewer", "both"], int]:
        """Return actionable open-Thread counts at one activity boundary.

        # Parameters

        - `snapshot_id`: Snapshot used to validate access to this Room.
        - `through_activity_id`: Inclusive outcome boundary for every count.

        # Usage

        Use the same inclusive activity id as the Thread page or continuation
        state these counts accompany.

        # Returns

        - The keys are exactly `author`, `reviewer`, and `both`.
        - Each value is the number of open Threads assigned that outcome at the
          inclusive boundary. Categories without Threads remain present as zero.

        # Failures

        - Raises `DirdiffError` for a Snapshot outside this Room.
        """
        self.meta(snapshot_id)
        return self._database.review_attention_counts(
            self._identity, through_activity_id
        )

    def apply_review_batch(
        self,
        snapshot_id: UUID,
        batch: tuple[ReviewBatchAction, ...],
    ) -> tuple[ReviewBatchResult, ...]:
        """Apply one validated agent batch in a single database transaction.

        # Parameters

        - `snapshot_id`: Exact Snapshot against which every action is validated.
        - `batch`: Ordered, non-empty agent actions sharing one author Profile.

        # Usage

        Preserve the agent's submitted order and pass the complete non-empty
        batch once. Use the returned results in the same order as the commands.

        # Returns

        - Each item is the canonical persisted action outcome for one command,
          including its Thread state and any created Comment identity.
        - Results preserve the non-empty batch's submission order, so callers
          may zip both tuples without another ordering key.

        # Failures

        - Raises `ReviewError` when any command is invalid for the bound
          Snapshot or current Thread state. No command is persisted on failure.
        """
        return apply_review_batch(
            database=self._database,
            identity=self._identity,
            snapshot_id=snapshot_id,
            batch=batch,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )

    def capture_context(self) -> RoomCaptureContext:
        """Return the repository context required to continue this Room.

        The HTTP boundary needs the Mark and any Pull Request URL to construct
        the concrete backend and prepared commits before `recapture` runs.
        Preset Rooms have no repository to continue and are rejected.

        # Usage

        Read this before agent continuation. Use `mark_id` to reconstruct the
        backend, and prepare the returned Pull Request URL before calling
        `recapture` when the Tab is `pull-request`.

        # Failures

        - Raises `DirdiffError` for a Preset Room, which cannot be continued by
          the agent API.
        - Raises `AssertionError` when persisted correspondence is malformed.
        """
        identity = self._identity
        if identity.mark_id is None or identity.tab == "preset":
            raise DirdiffError("Agent review does not support preset Rooms.")
        try:
            correspondence = json.loads(identity.correspondence_key)
        except (TypeError, ValueError) as exc:
            raise AssertionError(
                "invalid persisted Room correspondence"
            ) from exc
        match identity.tab:
            case "head" | "refs" | "branch-review":
                assert isinstance(correspondence, dict)
                tab: Literal[
                    "head", "refs", "branch-review", "pull-request"
                ] = identity.tab
                pull_request_url = None
            case "pull-request":
                tab = "pull-request"
                assert isinstance(correspondence, str) and correspondence != ""
                pull_request_url = correspondence
            case _:
                raise AssertionError(
                    f"invalid persisted Room Tab: {identity.tab!r}"
                )
        return {
            "tab": tab,
            "mark_id": identity.mark_id,
            "pull_request_url": pull_request_url,
        }

    def recapture(
        self,
        backend: WorkspaceBackendProtocol,
        *,
        pull_request_left: Optional[str] = None,
        pull_request_right: Optional[str] = None,
    ) -> UUID:
        """Capture this Room's persisted Tab context into a new Snapshot.

        The concrete backend comes from the HTTP boundary's active Mark. A Pull
        Request continuation must supply the newly prepared complete commits;
        other Tabs reject them. The new Snapshot remains in this exact Room.

        # Parameters

        - `backend`: Workspace backend constructed from this Room's active Mark.
        - `pull_request_left`: Newly prepared merge-base commit for a Pull
          Request Room, otherwise `None`.
        - `pull_request_right`: Newly prepared head commit for a Pull Request
          Room, otherwise `None`.

        # Usage

        Obtain the Mark and Tab from `capture_context`, construct the matching
        `GitBackend`, and prepare fresh Pull Request commits only when that
        context names a Pull Request.

        # Failures

        - Raises `DirdiffError` for Preset Rooms, missing Pull Request commits,
          or commits that are not complete ids.
        - Raises `AssertionError` when the backend kind, extra parameters, or
          persisted Tab correspondence contradict this Room.
        """

        def persisted_branch(value: JsonValue) -> BranchSelection:
            """Decode one persisted symbolic branch into the backend contract.

            The value is read only during branch-review recapture. It must carry
            a nonblank branch and either local source or a nonblank remote;
            malformed persisted correspondence is an invariant failure.
            """
            assert isinstance(value, dict)
            source = value.get("source")
            branch = value.get("branch")
            assert isinstance(branch, str) and branch != ""
            if source == "local":
                return {"source": "local", "branch": branch}
            assert source == "remote"
            remote = value.get("remote")
            assert isinstance(remote, str) and remote != ""
            return {
                "source": "remote",
                "remote": remote,
                "branch": branch,
            }

        identity = self._identity
        if identity.mark_id is None or identity.tab == "preset":
            raise DirdiffError("Agent review does not support preset Rooms.")
        try:
            correspondence: JsonValue = json.loads(identity.correspondence_key)
        except (TypeError, ValueError) as exc:
            raise AssertionError(
                "invalid persisted Room correspondence"
            ) from exc
        assert isinstance(backend, GitBackend)
        left_side: str
        right_side: str

        if identity.tab == "head":
            assert pull_request_left is None and pull_request_right is None
            assert isinstance(correspondence, dict)
            stored_commit = correspondence.get("commit")
            assert isinstance(stored_commit, str) and stored_commit != ""
            left_side = stored_commit
            right_side = "worktree"
            left_label = "HEAD"
            right_label = "worktree"
            show_untracked = True
        elif identity.tab == "refs":
            assert pull_request_left is None and pull_request_right is None
            assert isinstance(correspondence, dict)
            stored_left = correspondence.get("left")
            stored_right = correspondence.get("right")
            assert isinstance(stored_left, str) and stored_left != ""
            assert isinstance(stored_right, str) and stored_right != ""
            left_side = stored_left
            right_side = stored_right
            left_label = left_side
            right_label = right_side
            show_untracked = False
        elif identity.tab == "branch-review":
            assert pull_request_left is None and pull_request_right is None
            assert isinstance(correspondence, dict)
            base_value = correspondence.get("base")
            review_value = correspondence.get("review")
            resolved_base, left_side, review_side, right_side = (
                backend.resolve_branch_diff_sides(
                    base_selection=persisted_branch(base_value),
                    review_selection=persisted_branch(review_value),
                )
            )
            left_label = f"{resolved_base.strip()}...{review_side}"
            right_label = review_side
            show_untracked = False
        else:
            assert identity.tab == "pull-request"
            assert isinstance(correspondence, str) and correspondence != ""
            if pull_request_left is None or pull_request_right is None:
                raise DirdiffError(
                    "Pull Request continuation requires prepared commits."
                )
            left_side = backend.commit_id(pull_request_left)
            right_side = backend.commit_id(pull_request_right)
            if (
                left_side != pull_request_left
                or right_side != pull_request_right
            ):
                raise DirdiffError(
                    "Pull Request continuation requires complete commit ids."
                )
            left_label = left_side
            right_label = right_side
            show_untracked = False

        store = _SnapshotStore(
            database=self._database,
            staging_path=self._staging_path,
            snapshots_path=self._snapshots_path,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
            identity=identity,
        )
        return store.capture(
            backend=backend,
            left_side=left_side,
            right_side=right_side,
            left_label=left_label,
            right_label=right_label,
            show_untracked=show_untracked,
        )

    def path_for_snapshot(self, snapshot_id: UUID) -> Path:
        """Return the existing durable directory of one captured Snapshot.

        The key must identify a persisted Snapshot of this Room. This read
        exposes the directory already published by ordinary Snapshot capture
        and creates no file, directory, link, row, or alternative
        representation.

        # Usage

        Use this only when a caller needs the published Snapshot root, such as
        the agent filesystem contract. Treat the returned directory as read-only.

        # Failures

        - Raises `DirdiffError` when the Snapshot does not belong to this Room.
        - Raises `AssertionError` when persistence names a Snapshot whose
          published directory is missing.
        """
        self.meta(snapshot_id)
        path = self._snapshots_path / snapshot_id.hex
        assert path.is_dir(), f"Snapshot directory is missing: {path}"
        return path


class RoomLord:
    """Apply the active Tab's law of correspondence and return a Room.

    `corresponding_room` applies the law for one explicit capture and returns
    both the Room and the captured Snapshot key. `find_room` uses an existing
    Snapshot key to recover its Room for follow-up operations.

    The returned `Room` is the entrypoint for Snapshot reads and review work.
    `RoomLord` does not retain a selected Room or Snapshot between calls.
    """

    def __init__(self, database: RoomStore, store_path: Path) -> None:
        """Create the application boundary for one database and store root.

        The paths are normalized but not created here. This keeps construction
        harmless; the first valid manifest capture creates storage only after
        repository-placement checks succeed.

        # Parameters

        - `database`: Application Room persistence interface.
        - `store_path`: Root reserved for staging and published Snapshot data.

        # Usage

        Construct one `RoomLord` for the application database and one dedicated
        Snapshot root outside every reviewed repository. Reuse it for manifest
        capture and Snapshot-keyed follow-up operations.

        """
        self._database = database
        self._store_path = store_path.expanduser().resolve()
        self._staging_path = self._store_path / "staging"
        self._snapshots_path = self._store_path / "snapshots"
        database_path = self._database.engine.url.database
        if database_path is None or database_path == ":memory:":
            self._lock_path = self._store_path / "store.lock"
        else:
            database_file = Path(database_path).expanduser().resolve()
            self._lock_path = database_file.with_name(
                f"{database_file.name}.room.lock"
            )
        # `flock` coordinates processes; this lock gives worker threads one
        # unambiguous publication critical section.
        self._thread_lock = Lock()

    def corresponding_room(
        self,
        *,
        mark_id: Optional[int],
        backend: WorkspaceBackendProtocol,
        selection: CaptureSelection,
    ) -> tuple[Room, UUID]:
        """Apply one Tab's law and return its Room and current Snapshot key.

        The selection variant carries exactly its Tab's inputs, so validity
        checks concern values, never cross-Tab absence. The law of
        correspondence selects only the Room. The independently derived sides
        are supplied to Snapshot capture after that Room identity is
        complete.

        # Parameters

        - `mark_id`: Active repository mark, or `None` only for presets.
        - `backend`: Concrete source of paths, bytes, and backend line totals.
        - `selection`: Complete discriminated Tab inputs governing both
          correspondence and capture.

        # Usage

        Construct exactly one `CaptureSelection` variant from the active Tab and
        pair it with the matching backend. Retain both returned values: the Room
        bounds follow-up operations, while the Snapshot id selects captured
        state inside it.

        # Returns

        - First, the selected or newly created `Room` under the Tab's
          correspondence law.
        - Second, the current immutable Snapshot id captured inside that Room.
          The Room does not retain it, so callers must preserve both values.

        # Failures

        - Raises `DirdiffError` for incomplete or contradictory Tab values,
          unresolved refs, or a database or Snapshot store placed inside the
          reviewed repository.
        - Asserts when a selection is paired with the wrong backend or Mark
          presence.
        """
        repo_root = backend.repo_root
        if mark_id is not None and repo_root is not None:
            reviewed_root = repo_root.resolve()
            database = self._database.engine.url.database
            if database is not None and database != ":memory:":
                database_path = Path(database).expanduser().resolve()
                if database_path.is_relative_to(reviewed_root):
                    raise DirdiffError(
                        "The dirdiff database must be outside the reviewed "
                        f"repository: {database_path}"
                    )
            if self._store_path.is_relative_to(reviewed_root):
                raise DirdiffError(
                    "The dirdiff Snapshot store must be outside the reviewed "
                    f"repository: {self._store_path}"
                )

        tab: RoomTab
        show_untracked = False
        git_backend: Optional[GitBackend] = None
        if not isinstance(selection, PresetCaptureSelection):
            assert isinstance(backend, GitBackend), (
                "repository Rooms require GitBackend"
            )
            git_backend = backend

        if isinstance(selection, PresetCaptureSelection):
            tab = "preset"
            assert mark_id is None, "preset Rooms cannot belong to a Mark"
            if (catalog := selection.catalog.strip()) == "":
                raise DirdiffError(
                    "preset_catalog is required for the Preset Tab."
                )
            if (subset := selection.subset.strip()) == "":
                raise DirdiffError(
                    "preset_subset is required for the Preset Tab."
                )
            left_side = backend.normalize_side(subset)
            right_side = "new"
            left_label = "old"
            right_label = "new"
            correspondence: str | dict[str, str | BranchSelection] = {
                "catalog": catalog,
                "subset": subset,
            }
        elif isinstance(selection, PullRequestCaptureSelection):
            tab = "pull-request"
            assert mark_id is not None, "Pull Request Rooms require a Mark"
            if (corresponding_url := selection.url.strip()) == "":
                raise DirdiffError(
                    "pull_request_url is required for the Pull Request Tab."
                )
            if (prepared_left := selection.left_commit.strip()) == "":
                raise DirdiffError(
                    "left_commit is required for the Pull Request Tab."
                )
            if (prepared_right := selection.right_commit.strip()) == "":
                raise DirdiffError(
                    "right_commit is required for the Pull Request Tab."
                )
            assert git_backend is not None
            left_side = git_backend.commit_id(prepared_left)
            right_side = git_backend.commit_id(prepared_right)
            if left_side != prepared_left or right_side != prepared_right:
                raise DirdiffError(
                    "Pull Request capture inputs must be complete commit ids."
                )
            left_label = left_side
            right_label = right_side
            correspondence = corresponding_url
        elif isinstance(selection, BranchReviewCaptureSelection):
            tab = "branch-review"
            assert mark_id is not None, "Branch Review Rooms require a Mark"
            assert git_backend is not None
            resolved_base, left_side, review, right_side = (
                git_backend.resolve_branch_diff_sides(
                    base_selection=selection.base,
                    review_selection=selection.review,
                )
            )
            left_label = f"{resolved_base.strip()}...{review}"
            right_label = review
            correspondence = {
                "base": selection.base,
                "review": selection.review,
            }
        elif selection.tab == "head":
            tab = "head"
            show_untracked = selection.show_untracked
            assert mark_id is not None, "Diff against HEAD requires a Mark"
            left_label = backend.normalize_side(selection.left.strip())
            right_label = backend.normalize_side(selection.right.strip())
            if left_label != "HEAD" or right_label != "worktree":
                raise DirdiffError(
                    "Diff against HEAD requires left=HEAD and right=worktree."
                )
            assert git_backend is not None
            left_side = git_backend.commit_id(left_label)
            right_side = right_label
            correspondence = {"commit": left_side}
        else:
            assert selection.tab == "refs"
            tab = "refs"
            show_untracked = selection.show_untracked
            assert mark_id is not None, "repo Rooms require a Mark"
            assert git_backend is not None
            if selection.left.strip() == "":
                raise DirdiffError("left is required for the Refs Tab.")
            if selection.right.strip() == "":
                raise DirdiffError("right is required for the Refs Tab.")
            left_label = backend.normalize_side(selection.left.strip())
            right_label = backend.normalize_side(selection.right.strip())
            left_side = (
                left_label
                if left_label in {"index", "worktree"}
                else git_backend.commit_id(left_label)
            )
            right_side = (
                right_label
                if right_label in {"index", "worktree"}
                else git_backend.commit_id(right_label)
            )
            left_label = left_side
            right_label = right_side
            correspondence = {"left": left_side, "right": right_side}

        identity = RoomIdentity(
            mark_id=mark_id,
            tab=tab,
            correspondence_key=json.dumps(
                correspondence,
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        store = _SnapshotStore(
            database=self._database,
            staging_path=self._staging_path,
            snapshots_path=self._snapshots_path,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
            identity=identity,
        )
        snapshot_id = store.capture(
            backend=backend,
            left_side=left_side,
            right_side=right_side,
            left_label=left_label,
            right_label=right_label,
            show_untracked=show_untracked,
        )
        return Room(
            database=self._database,
            identity=identity,
            staging_path=self._staging_path,
            snapshots_path=self._snapshots_path,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        ), snapshot_id

    def find_room(self, snapshot_id: UUID) -> Room:
        """Return the Room containing an existing Snapshot key.

        The Snapshot id is globally unique in this database. Missing keys are
        rejected; this lookup neither executes a Tab law nor reads live backend
        state.

        # Usage

        Use this for follow-up endpoints that receive only the opaque Snapshot
        key returned by capture. Pass the same key to the returned Room method.

        # Failures

        - Raises `DirdiffError` when no Snapshot has the supplied key.
        - Raises `AssertionError` when its persisted Room names an unknown Tab.
        """
        identity = self._database.room_identity(snapshot_id.hex)
        if identity is None:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        if identity.tab not in {
            "head",
            "refs",
            "branch-review",
            "pull-request",
            "preset",
        }:
            raise AssertionError(
                f"invalid persisted Room Tab: {identity.tab!r}"
            )
        return Room(
            database=self._database,
            identity=identity,
            staging_path=self._staging_path,
            snapshots_path=self._snapshots_path,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )


class _SnapshotStore:
    """Implement immutable Snapshot capture for one private Room.

    `RoomLord.corresponding_room` and `Room.recapture` construct and call the
    store for exactly one capture. It manages publication directories, locking,
    relational records, and digest validation; API code never receives it.
    """

    def __init__(
        self,
        *,
        database: RoomStore,
        staging_path: Path,
        snapshots_path: Path,
        lock_path: Path,
        thread_lock: Lock,
        identity: RoomIdentity,
    ) -> None:
        """Create private persistence access for exactly one Room identity.

        `RoomLord` supplies shared paths and locking, while `identity` limits
        every relational lookup. Constructing the store performs no filesystem
        or database work.

        # Parameters

        - `database`: Persistence interface used for retained checks and
          publication.
        - `staging_path`: Root for incomplete process-private captures.
        - `snapshots_path`: Root for complete published Snapshot directories.
        - `lock_path`: Cross-process publication lock file.
        - `thread_lock`: In-process publication lock.
        - `identity`: Exact Room correspondence receiving the capture.
        """
        assert identity.correspondence_key != b"", (
            "Room correspondence key cannot be empty"
        )
        self._database = database
        self._staging_path = staging_path
        self._snapshots_path = snapshots_path
        self._lock_path = lock_path
        self._thread_lock = thread_lock
        self._identity = identity

    def capture(
        self,
        *,
        backend: WorkspaceBackendProtocol,
        left_side: str,
        right_side: str,
        left_label: str,
        right_label: str,
        show_untracked: bool,
    ) -> UUID:
        """Capture current backend state and return its retained Snapshot key.

        Equality includes repository paths, tracked provenance, change type,
        captured contents, capture errors, and complete explicit preset metadata.
        Backend order, human labels, and aggregate line counts do not affect
        identity. Every Thread contained by the Room is placed in the captured
        Snapshot before it becomes visible.

        # Parameters

        - `backend`: Workspace source used for manifest facts and side bytes.
        - `left_side`: Backend handle for the captured left state.
        - `right_side`: Backend handle for the captured right state.
        - `left_label`: Human label retained for the left state.
        - `right_label`: Human label retained for the right state.
        - `show_untracked`: Whether backend discovery includes untracked Files.

        # Usage

        `RoomLord.corresponding_room` or `Room.recapture` calls this once after
        selecting complete backend sides and labels for one Room identity.

        # Failures

        - Raises `DirdiffError` for backend capture failures that cannot produce
          an immutable side.
        - Raises `AssertionError` when backend output, retained contents, or
          publication records violate Snapshot invariants.
        """
        self._staging_path.mkdir(parents=True, exist_ok=True)
        self._snapshots_path.mkdir(parents=True, exist_ok=True)
        self._lock_path.touch(exist_ok=True)

        diff = backend.repo_diff(
            left=left_side,
            right=right_side,
            show_untracked=show_untracked,
        )
        paths = diff.paths
        added_lines = diff.added_lines
        removed_lines = diff.removed_lines
        assert (added_lines is None) == (removed_lines is None), (
            "backend aggregate line counts must have equal presence"
        )
        normalized_paths: list[
            tuple[
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[str],
                bool,
                ChangeType,
                Optional[LazyReason],
                Optional[str],
                Optional[str],
            ]
        ] = []
        captured_paths: set[tuple[Optional[str], Optional[str]]] = set()
        for path in paths:
            repository_paths = (path.left_path, path.right_path)
            if repository_paths in captured_paths:
                raise DirdiffError(
                    "Backend returned duplicate left/right paths: "
                    f"{repository_paths!r}"
                )
            captured_paths.add(repository_paths)
            left_path = (
                backend.normalize_repo_path(path.left_path)
                if path.left_path is not None
                else None
            )
            right_path = (
                backend.normalize_repo_path(path.right_path)
                if path.right_path is not None
                else None
            )
            assert (left_path is None) == (path.left_mode is None), (
                "backend left File path and mode must have equal presence"
            )
            assert (right_path is None) == (path.right_mode is None), (
                "backend right File path and mode must have equal presence"
            )
            normalized_paths.append(
                (
                    left_path,
                    path.left_mode,
                    right_path,
                    path.right_mode,
                    not path.untracked,
                    path.change_type,
                    path.lazy_reason_override,
                    path.left_object_id,
                    path.right_object_id,
                )
            )

        # Ordinary sides carrying a backend object id participate in Snapshot
        # identity by that id, so their bytes are read only for a new Snapshot.
        # Object-id-less sides and all links are read before the retained check:
        # a link's walk and final target are additional identity facts. A failed
        # eager read substitutes the established error content; a deferred
        # side's later read failure aborts rather than creating error content
        # under an identity that names the real immutable bytes.
        eager_requests: list[tuple[str, str]] = []
        for (
            left_path,
            left_mode,
            right_path,
            right_mode,
            _tracked,
            _change_type,
            _lazy_reason_override,
            left_object_id,
            right_object_id,
        ) in normalized_paths:
            if left_path is not None and (
                left_object_id is None or left_mode == SYMLINK_MODE
            ):
                eager_requests.append((left_path, left_side))
            if right_path is not None and (
                right_object_id is None or right_mode == SYMLINK_MODE
            ):
                eager_requests.append((right_path, right_side))
        eager_versions = iter(backend.load_versions(tuple(eager_requests)))

        # Per file: side slots hold loaded bytes for eager sides and None for
        # deferred object-id sides; error strings collect per file in side
        # order exactly as the previous single-pass capture did.
        interim: list[
            tuple[
                Optional[str],
                Optional[str],
                Optional[bytes],
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[bytes],
                Optional[str],
                bool,
                ChangeType,
                Optional[LazyReason],
                Optional[str],
                list[str],
                Optional[CapturedLink],
                Optional[CapturedLink],
            ]
        ] = []

        def substituted_side(
            loaded: bytes | DirdiffError,
            side_path: str,
            side_name: str,
            side_label: str,
            errors: list[str],
        ) -> bytes:
            """Return one loaded side, substituting established error text.

            # Parameters

            - `loaded`: Exact bytes or the expected backend loading failure.
            - `side_path`: Repository path used in the persisted failure.
            - `side_name`: Capture slot, `left` or `right`.
            - `side_label`: Backend state label paired with the path.
            - `errors`: File-local failure list updated on substitution.
            """
            if isinstance(loaded, DirdiffError):
                failure = (
                    f"Could not capture {side_name} side "
                    f"{side_label}:{side_path}: {loaded}"
                )
                errors.append(failure)
                return f"{_CAPTURE_ERROR_PREAMBLE}\n\n{failure}\n".encode()
            return loaded

        def captured_link(
            *,
            path: str,
            mode: str,
            content: bytes,
            side: str,
        ) -> CapturedLink | None:
            """Walk one captured link iteratively inside its backend side.

            Each normalized link path enters `visited` before its target is
            loaded. Repeating a path therefore records the loop immediately and
            never loads that link twice. Absolute and repo-escaping targets fail
            normalization; missing, directory-shaped, and unsupported targets
            fail backend inspection. Every such expected resolution failure is
            retained as the terminal diagnosis and leaves final content absent.

            Non-link modes return `None`. A successful link walk returns its
            nested links plus exact final non-link bytes for immutable Snapshot
            publication. The outer link payload remains the ordinary side.

            # Parameters

            - `path`: Normalized path of the outer link File.
            - `mode`: Git-compatible mode already captured for that side.
            - `content`: Raw target bytes loaded for the outer link.
            - `side`: Backend side in which every target must be inspected.

            # Returns

            - `CapturedLink`: Complete stopped or successfully resolved walk.
            - `None`: The supplied mode identifies an ordinary File.
            """
            if mode != SYMLINK_MODE:
                return None
            visited: set[str] = set()
            nested_links: list[tuple[str, str]] = []
            current_path = path
            current_content = content
            while True:
                assert current_path not in visited, (
                    "link loops stop before a repeated path is loaded"
                )
                visited.add(current_path)
                try:
                    target = current_content.decode("utf-8")
                except UnicodeDecodeError:
                    escaped = json.dumps(
                        current_content.decode(
                            "utf-8", errors="backslashreplace"
                        ),
                        ensure_ascii=False,
                    )
                    if current_path != path:
                        nested_links.append((current_path, escaped))
                    return CapturedLink(
                        nested_links=tuple(nested_links),
                        diagnosis="stopped: link target is not UTF-8",
                        target_path=None,
                        target_data=None,
                    )
                displayed_target = (
                    target
                    if target.isprintable()
                    else json.dumps(target, ensure_ascii=False)
                )
                if current_path != path:
                    nested_links.append((current_path, displayed_target))
                if target == "" or "\x00" in target:
                    return CapturedLink(
                        nested_links=tuple(nested_links),
                        diagnosis=(
                            "stopped: link target is empty or contains NUL"
                        ),
                        target_path=None,
                        target_data=None,
                    )
                unresolved = str(PurePosixPath(current_path).parent / target)
                try:
                    next_path = backend.normalize_repo_path(unresolved)
                except DirdiffError as exc:
                    return CapturedLink(
                        nested_links=tuple(nested_links),
                        diagnosis=f"stopped: {exc}",
                        target_path=None,
                        target_data=None,
                    )
                if next_path in visited:
                    return CapturedLink(
                        nested_links=tuple(nested_links),
                        diagnosis=(f"loop: {next_path} was already visited"),
                        target_path=None,
                        target_data=None,
                    )
                try:
                    next_mode = backend.file_mode(next_path, side)
                    if (
                        next_mode != SYMLINK_MODE
                        and backend.file_size(next_path, side)
                        > _MAX_CAPTURED_LINK_TARGET_BYTES
                    ):
                        return CapturedLink(
                            nested_links=tuple(nested_links),
                            diagnosis="stopped: link target exceeds 1 MiB",
                            target_path=None,
                            target_data=None,
                        )
                    next_content = backend.load_version(next_path, side)
                except DirdiffError as exc:
                    return CapturedLink(
                        nested_links=tuple(nested_links),
                        diagnosis=f"stopped: {exc}",
                        target_path=None,
                        target_data=None,
                    )
                if next_mode != SYMLINK_MODE:
                    if len(next_content) > _MAX_CAPTURED_LINK_TARGET_BYTES:
                        return CapturedLink(
                            nested_links=tuple(nested_links),
                            diagnosis="stopped: link target exceeds 1 MiB",
                            target_path=None,
                            target_data=None,
                        )
                    return CapturedLink(
                        nested_links=tuple(nested_links),
                        diagnosis=None,
                        target_path=next_path,
                        target_data=next_content,
                    )
                current_path = next_path
                current_content = next_content

        for (
            left_path,
            left_mode,
            right_path,
            right_mode,
            tracked,
            change_type,
            lazy_reason_override,
            left_object_id,
            right_object_id,
        ) in normalized_paths:
            errors: list[str] = []
            left_content: Optional[bytes] = None
            left_failed = False
            if left_path is not None and (
                left_object_id is None or left_mode == SYMLINK_MODE
            ):
                loaded_left = next(eager_versions)
                left_failed = isinstance(loaded_left, DirdiffError)
                left_content = substituted_side(
                    loaded_left, left_path, "left", left_side, errors
                )
            right_content: Optional[bytes] = None
            right_failed = False
            if right_path is not None and (
                right_object_id is None or right_mode == SYMLINK_MODE
            ):
                loaded_right = next(eager_versions)
                right_failed = isinstance(loaded_right, DirdiffError)
                right_content = substituted_side(
                    loaded_right,
                    right_path,
                    "right",
                    right_side,
                    errors,
                )
            assert left_path is not None or right_path is not None, (
                "Snapshot File must have at least one side"
            )
            left_link = (
                None
                if left_path is None
                or left_mode is None
                or left_content is None
                or left_failed
                else captured_link(
                    path=left_path,
                    mode=left_mode,
                    content=left_content,
                    side=left_side,
                )
            )
            right_link = (
                None
                if right_path is None
                or right_mode is None
                or right_content is None
                or right_failed
                else captured_link(
                    path=right_path,
                    mode=right_mode,
                    content=right_content,
                    side=right_side,
                )
            )

            lazy_metadata: Optional[tuple[LazyReason, str]] = None
            if self._identity.tab == "preset":
                assert isinstance(backend, PresetBackend), (
                    "preset Rooms require PresetBackend"
                )
                # A fixture's metadata lives beside both of its sides, so
                # either path reaches it. A deleted fixture has only the old
                # side, which is the one the preset backend named the File by
                # when it built this entry.
                metadata_path = (
                    right_path if right_path is not None else left_path
                )
                assert metadata_path is not None, (
                    "a captured File has at least one side"
                )
                lazy_metadata = backend.lazy_reason_metadata(metadata_path)
                assert (
                    lazy_metadata[0] if lazy_metadata is not None else None
                ) == lazy_reason_override, (
                    "preset lazy reason changed during Snapshot capture"
                )
            else:
                assert lazy_metadata is None
            interim.append(
                (
                    left_path,
                    left_mode,
                    left_content,
                    left_object_id,
                    right_path,
                    right_mode,
                    right_content,
                    right_object_id,
                    tracked,
                    change_type,
                    lazy_reason_override,
                    lazy_metadata[1] if lazy_metadata is not None else None,
                    errors,
                    left_link,
                    right_link,
                )
            )
        try:
            next(eager_versions)
        except StopIteration:
            pass
        else:
            raise AssertionError("backend returned excess captured sides")

        # Canonicalization makes the digest describe a set of Files; it does
        # not define storage or manifest presentation order. Each present side
        # contributes one identity token: its backend object id when it has
        # one, otherwise its captured bytes (which already embed any eager
        # read failure).
        interim.sort(key=lambda item: (item[0] or "", item[4] or ""))
        digest = hashlib.sha256()
        digest.update(_SNAPSHOT_HASH_DOMAIN)
        for (
            left_path,
            left_mode,
            left_content,
            left_object_id,
            right_path,
            right_mode,
            right_content,
            right_object_id,
            tracked,
            change_type,
            lazy_reason,
            metadata_content,
            _errors,
            left_link,
            right_link,
        ) in interim:

            def side_token(
                content: Optional[bytes], object_id: Optional[str]
            ) -> Optional[bytes]:
                """Encode one side's identity token, or `None` when absent.

                # Parameters

                - `content`: Eager bytes for an object-id-less side or link.
                - `object_id`: Backend content identity for a deferred side.

                An eager link backed by a Git object carries both; the object id
                remains the raw side token and its walk joins identity separately.

                # Returns

                - A tagged byte token. `O` prefixes a backend object id and `C`
                  prefixes eager captured contents, keeping the two identity
                  sources distinct in the Snapshot digest.
                - `None`: Both inputs are absent, so this Snapshot File has no
                  side in this position and contributes the absent marker.
                """
                if object_id is not None:
                    return b"O" + object_id.encode("ascii")
                if content is not None:
                    return b"C" + content
                return None

            for value in (
                left_path.encode() if left_path is not None else None,
                left_mode.encode() if left_mode is not None else None,
                side_token(left_content, left_object_id),
                left_link.identity_bytes() if left_link is not None else None,
                right_path.encode() if right_path is not None else None,
                right_mode.encode() if right_mode is not None else None,
                side_token(right_content, right_object_id),
                right_link.identity_bytes() if right_link is not None else None,
                b"1" if tracked else b"0",
                change_type.encode(),
                lazy_reason.encode() if lazy_reason is not None else None,
                metadata_content.encode()
                if metadata_content is not None
                else None,
            ):
                if value is None:
                    digest.update(b"\x00")
                else:
                    digest.update(b"\x01")
                    digest.update(len(value).to_bytes(8, "big"))
                    digest.update(value)
        snapshot_hash = digest.digest()

        def verified_retained_snapshot(snapshot_id: str) -> UUID:
            """Authenticate all physical sides before reusing an equal Snapshot.

            It is invoked when the Room already contains the computed capture
            digest, both before and inside the publication lock. Every persisted
            side is reread and checked against its stored SHA-256; missing or
            changed bytes fail instead of returning a poisoned Snapshot key.

            # Failures

            - Raises `AssertionError` when the equal Snapshot disappeared or a
              retained side no longer matches its digest. Reads propagate I/O
              failures.
            """
            visible_snapshot = self._database.snapshot(
                self._identity,
                snapshot_id,
            )
            assert visible_snapshot is not None, (
                "equal Snapshot disappeared during publication"
            )
            for file in visible_snapshot.files:
                directory = Path(file.path)
                for name, side, symlink in (
                    ("left", file.left, file.left_symlink),
                    ("right", file.right, file.right_symlink),
                ):
                    if side is None:
                        assert symlink is None, (
                            f"persisted {name} link has no captured File side"
                        )
                        continue
                    captured_path = directory / name
                    content = captured_path.read_bytes()
                    assert (
                        hashlib.sha256(content).digest() == side.content_hash
                    ), f"Snapshot File content hash mismatch: {captured_path}"
                    if symlink is not None:
                        read_captured_link(
                            metadata_path=Path(symlink.metadata_path),
                            metadata_hash=symlink.metadata_hash,
                            target_capture_path=(
                                Path(symlink.target_capture_path)
                                if symlink.target_capture_path is not None
                                else None
                            ),
                            target_hash=symlink.target_hash,
                        )
            return UUID(hex=snapshot_id)

        retained_id = self._database.snapshot_id_for_content(
            self._identity,
            snapshot_hash,
        )
        if retained_id is not None:
            return verified_retained_snapshot(retained_id)

        # The Snapshot is new: read the deferred object-id sides and assemble
        # the complete captured rows the storage loop below persists.
        deferred_requests: list[tuple[str, str]] = []
        for (
            left_path,
            left_mode,
            _left_content,
            left_object_id,
            right_path,
            right_mode,
            _right_content,
            right_object_id,
            *_rest,
        ) in interim:
            if (
                left_path is not None
                and left_object_id is not None
                and left_mode != SYMLINK_MODE
            ):
                deferred_requests.append((left_path, left_side))
            if (
                right_path is not None
                and right_object_id is not None
                and right_mode != SYMLINK_MODE
            ):
                deferred_requests.append((right_path, right_side))
        deferred_versions = iter(
            backend.load_versions(tuple(deferred_requests))
        )
        captured: list[
            tuple[
                Optional[str],
                Optional[bytes],
                Optional[str],
                Optional[bytes],
                bool,
                ChangeType,
                Optional[LazyReason],
                Optional[str],
                Optional[str],
                Optional[CapturedLink],
                Optional[CapturedLink],
            ]
        ] = []

        def deferred_side(
            loaded: bytes | DirdiffError,
            side_path: str,
            side_name: str,
            side_label: str,
        ) -> bytes:
            """Return one object-id side's bytes, failing the capture on error.

            The side's object id already joined the Snapshot's identity, so a
            failed read must abort the capture: substituting error content
            here would persist an error placeholder under an identity token
            that names the real bytes, and every later capture of the same
            state would reuse the poisoned Snapshot without re-reading the
            backend. The id-addressed object is immutable, so the failure is
            infrastructural and a retry captures cleanly.

            # Parameters

            - `loaded`: Exact immutable-object bytes or its loading failure.
            - `side_path`: Repository path included in a failure message.
            - `side_name`: Capture slot, `left` or `right`.
            - `side_label`: Backend state label paired with the path.

            # Failures

            - Raises `DirdiffError` when the backend could not load the immutable
              object. The complete capture stops.
            """
            if isinstance(loaded, DirdiffError):
                raise DirdiffError(
                    f"Could not capture {side_name} side "
                    f"{side_label}:{side_path}: {loaded}"
                )
            return loaded

        for (
            left_path,
            left_mode,
            left_content,
            left_object_id,
            right_path,
            right_mode,
            right_content,
            right_object_id,
            tracked,
            change_type,
            lazy_reason_override,
            metadata_content,
            errors,
            left_link,
            right_link,
        ) in interim:
            full_left = left_content
            if (
                left_path is not None
                and left_object_id is not None
                and left_mode != SYMLINK_MODE
            ):
                full_left = deferred_side(
                    next(deferred_versions), left_path, "left", left_side
                )
            full_right = right_content
            if (
                right_path is not None
                and right_object_id is not None
                and right_mode != SYMLINK_MODE
            ):
                full_right = deferred_side(
                    next(deferred_versions), right_path, "right", right_side
                )
            assert (left_path is None) == (full_left is None), (
                "left repository path and captured contents must have equal presence"
            )
            assert (right_path is None) == (full_right is None), (
                "right repository path and captured contents must have equal presence"
            )
            assert full_left is not None or full_right is not None, (
                "Snapshot File must have at least one side"
            )
            captured.append(
                (
                    left_path,
                    full_left,
                    right_path,
                    full_right,
                    tracked,
                    change_type,
                    lazy_reason_override,
                    metadata_content,
                    "\n".join(errors) if errors else None,
                    left_link,
                    right_link,
                )
            )
        try:
            next(deferred_versions)
        except StopIteration:
            pass
        else:
            raise AssertionError("backend returned excess captured sides")

        snapshot_id = uuid4()
        staging_path = Path(
            tempfile.mkdtemp(
                prefix=f"{snapshot_id.hex}-",
                dir=self._staging_path,
            )
        )
        final_path = (self._snapshots_path / snapshot_id.hex).resolve()
        files: list[SnapshotFileRecord] = []
        lazy_reasons: dict[str, tuple[str, Optional[str]]] = {}
        try:
            for (
                left_path,
                left_content,
                right_path,
                right_content,
                tracked,
                change_type,
                lazy_reason,
                metadata_content,
                error,
                left_link,
                right_link,
            ) in captured:
                file_id = uuid4().hex
                file_path = staging_path / file_id
                file_path.mkdir()
                left_record: Optional[SnapshotFileSideRecord] = None
                left_symlink_record: Optional[SnapshotFileSymlinkRecord] = None
                if left_path is not None and left_content is not None:
                    left_capture_path = file_path / "left"
                    left_capture_path.write_bytes(left_content)
                    if left_link is not None:
                        left_metadata_path = file_path / "left-link.json"
                        left_target_path = (
                            file_path / "left-target"
                            if left_link.target_data is not None
                            else None
                        )
                        metadata_hash, target_hash = write_captured_link(
                            left_link,
                            metadata_path=left_metadata_path,
                            target_capture_path=left_target_path,
                        )
                        left_symlink_record = SnapshotFileSymlinkRecord(
                            metadata_path=str(
                                final_path / file_id / "left-link.json"
                            ),
                            metadata_hash=metadata_hash,
                            target_capture_path=(
                                str(final_path / file_id / "left-target")
                                if left_target_path is not None
                                else None
                            ),
                            target_hash=target_hash,
                        )
                    left_record = SnapshotFileSideRecord(
                        repository_path=left_path,
                        content_hash=hashlib.sha256(left_content).digest(),
                    )
                right_record: Optional[SnapshotFileSideRecord] = None
                right_symlink_record: Optional[SnapshotFileSymlinkRecord] = None
                if right_path is not None and right_content is not None:
                    right_capture_path = file_path / "right"
                    right_capture_path.write_bytes(right_content)
                    if right_link is not None:
                        right_metadata_path = file_path / "right-link.json"
                        right_target_path = (
                            file_path / "right-target"
                            if right_link.target_data is not None
                            else None
                        )
                        metadata_hash, target_hash = write_captured_link(
                            right_link,
                            metadata_path=right_metadata_path,
                            target_capture_path=right_target_path,
                        )
                        right_symlink_record = SnapshotFileSymlinkRecord(
                            metadata_path=str(
                                final_path / file_id / "right-link.json"
                            ),
                            metadata_hash=metadata_hash,
                            target_capture_path=(
                                str(final_path / file_id / "right-target")
                                if right_target_path is not None
                                else None
                            ),
                            target_hash=target_hash,
                        )
                    right_record = SnapshotFileSideRecord(
                        repository_path=right_path,
                        content_hash=hashlib.sha256(right_content).digest(),
                    )
                files.append(
                    SnapshotFileRecord(
                        id=file_id,
                        snapshot_id=snapshot_id.hex,
                        path=str(final_path / file_id),
                        tracked=tracked,
                        change_type=change_type,
                        error=error,
                        left=left_record,
                        right=right_record,
                        left_symlink=left_symlink_record,
                        right_symlink=right_symlink_record,
                    )
                )
                if lazy_reason is not None:
                    lazy_reasons[file_id] = lazy_reason, metadata_content

            # TODO: Unify this lock protocol with `room_write_lock` in
            # `dirdiff.review.base` so Snapshot publication and all review
            # writes use one shared context manager.
            with self._thread_lock, self._lock_path.open("a+b") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    visible_id = self._database.snapshot_id_for_content(
                        self._identity,
                        snapshot_hash,
                    )
                    if visible_id is None:
                        published_snapshot = SnapshotRecord(
                            id=snapshot_id.hex,
                            content_hash=snapshot_hash,
                            meta=SnapshotMetaRecord(
                                left_label=left_label,
                                right_label=right_label,
                                added_lines=added_lines,
                                removed_lines=removed_lines,
                            ),
                            files=tuple(files),
                        )
                        # Thread derivation reads the target Snapshot's own
                        # captured text (region relocation), so the bytes must
                        # already sit at their final address before it runs.
                        staging_path.rename(final_path)
                        try:
                            review_threads = derive_room_threads(
                                database=self._database,
                                identity=self._identity,
                                target_snapshot=published_snapshot,
                            )
                            self._database.publish(
                                self._identity,
                                published_snapshot,
                                lazy_reasons=lazy_reasons,
                                review_threads=review_threads,
                            )
                        except BaseException:
                            # Derivation or publication failed after the
                            # rename: restore the staging address so cleanup
                            # removes the bytes and no orphaned, untracked
                            # Snapshot directory survives at its published
                            # address. Publication itself is one transaction,
                            # so no database rows exist either way.
                            final_path.rename(staging_path)
                            raise
                        visible_id = snapshot_id.hex
                    else:
                        return verified_retained_snapshot(visible_id)
                    return UUID(hex=visible_id)
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            if staging_path.exists():
                shutil.rmtree(staging_path)
