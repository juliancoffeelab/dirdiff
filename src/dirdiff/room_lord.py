"""Rooms and immutable Snapshots of workspace state.

## Public interface

Callers use `RoomLord.corresponding_room` for an explicit Tab capture. It returns
the corresponding `Room` and the opaque Snapshot key produced or reused by that
capture. Follow-up operations use `RoomLord.find_room` to recover the containing
Room from an existing Snapshot key without reading live workspace state.

A `Room` provides Snapshot metadata, captured File lookup, review Threads, and
persisted correspondence facts for source setup. It contains
multiple Snapshots but retains no selected Snapshot, so every Snapshot-scoped operation requires the exact key.

## Purpose and boundaries

This module joins selected `dirdiff.change_source.ChangeSourceProtocol` instances to `dirdiff.db.RoomStore`:
it captures the Files they report, publishes immutable Snapshot contents, and returns
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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Optional, TypedDict
from uuid import UUID, uuid4

from dirdiff.change_source import (
    CapturedLink,
    ChangeSourceProtocol,
    FileChange,
    FileData,
    FileKey,
    FileProperties,
    FileSide,
    LazyReason,
    LoadedFile,
    WorkspaceChanges,
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
    "HeadCaptureSelection",
    "PresetCaptureSelection",
    "PullRequestCaptureSelection",
    "RevisionsCaptureSelection",
    "Room",
    "RoomCaptureContext",
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
"""Source classification of how one captured File pair changed.

- `modify` keeps the same path on both sides.
- `add` and `delete` are one-sided.
- `rename` relates different paths for one File identity.
- `copy` relates a new path to retained source content.

Snapshot publication stores this value with the File pair. It does not describe
line changes produced later by a diff engine.
"""


@dataclass(frozen=True)
class HeadCaptureSelection:
    """Identify a Head Room by its prepared original baseline.

    Source setup supplies this opaque identity after preparing the selected
    state. Room uses equality only; it does not interpret revisions or storage.
    """

    baseline: str
    """Nonblank source identity of the original baseline, retained on continuation."""


@dataclass(frozen=True)
class RevisionsCaptureSelection:
    """Identify a Refs Room by its ordered prepared side identities.

    Source setup freezes revision-backed selections and preserves explicit
    ephemeral selections. Room compares these opaque values without resolving
    refs or deciding how to load them.
    """

    left: str
    """Nonblank prepared identity for the selected left state."""

    right: str
    """Nonblank prepared identity for the selected right state."""


@dataclass(frozen=True)
class BranchReviewCaptureSelection:
    """Identify a Branch Review Room by two opaque symbolic branch identities.

    Source setup validates its concrete branch representation. Room only embeds
    these JSON values in the canonical correspondence key. Mutable containers
    belong to the caller and must not change while this selection is in use.
    No resolved revision or live source is part of this law.
    """

    base: JsonValue
    """Non-null JSON identity of the selected symbolic base branch."""

    review: JsonValue
    """Non-null JSON identity of the selected symbolic review branch."""


@dataclass(frozen=True)
class PullRequestCaptureSelection:
    """Identify a Pull Request Room by its prepared canonical review URL.

    Source setup acquires revisions separately. Room uses the URL for
    correspondence without interpreting the forge or inspecting its storage.
    """

    url: str
    """Nonblank canonical review URL retained across fresh source preparations."""


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

    It selects a catalog identity and participates in Room correspondence; the
    value is not opened as a caller-supplied filesystem path.
    """

    subset: str
    """Nonblank fixture-group identity within the selected catalog.

    It selects the old/new preset pair and joins `catalog` in correspondence.
    The actual fixture bytes remain the source's responsibility.
    """


# One concrete Tab selection: each variant carries exactly the fields its
# Tab's law consumes, so callers never manufacture irrelevant nulls and the
# law never branches on cross-Tab nullability.
CaptureSelection = (
    HeadCaptureSelection
    | RevisionsCaptureSelection
    | BranchReviewCaptureSelection
    | PullRequestCaptureSelection
    | PresetCaptureSelection
)
"""Prepared correspondence facts for one supported Tab.

- `HeadCaptureSelection` supplies the original prepared baseline identity.
- `RevisionsCaptureSelection` supplies the prepared Refs side identities.
- `BranchReviewCaptureSelection` supplies symbolic branch selections.
- `PullRequestCaptureSelection` supplies a canonical review URL.
- `PresetCaptureSelection` supplies a catalog and fixture group.

Manifest conversion constructs the matching variant before asking `RoomLord`
for a Room. No variant contains Snapshot identity, renderer selection, or HTTP
presentation state.
"""

_SNAPSHOT_HASH_DOMAIN = b"dirdiff-snapshot-v11"
"""Versioned prefix that keeps Snapshot equality tied to its token format.

Changing which capture facts participate in identity requires a new value so
old digests cannot be mistaken for the new definition.
"""
_CAPTURE_ERROR_PREAMBLE = (
    "MACHINE-GENERATED BY DIRDIFF\nThe original file could not be captured."
)
"""Stable marker placed before captured source failure details.

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
    """Human-facing label frozen with the captured left source state.

    Callers may present it but must not use it as repository identity or assume
    it follows a branch name after capture.
    """

    right_label: str
    """Human-facing label frozen with the captured right source state.

    It remains paired with this immutable Snapshot even if live refs or
    source state later changes.
    """

    added_lines: Optional[int]
    """Source-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` always have equal presence.
    """

    removed_lines: Optional[int]
    """Source-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` always have equal presence.
    """


class RoomCaptureContext(TypedDict):
    """Supply complete persisted correspondence for source setup.

    `Room.capture_context` returns a fresh selection and its persisted Mark identity.
    Concrete setup interprets these facts to construct a new source; this record
    contains no source, storage path, or loaded data. The selection covers every
    Tab; Preset Rooms have no Mark identity.
    """

    mark_id: int | None
    """Positive persisted Mark id, or `None` for a Mark-less Preset Room."""

    selection: CaptureSelection
    """Complete persisted Tab law, retaining its original correspondence facts.

    Branch JSON containers are newly decoded and belong to the caller. They
    must remain unchanged while used for source setup or correspondence.
    """


class FileMeta(TypedDict):
    """Expose persisted classification and read status for one captured File pair.

    # Usage

    `Room.manifested` and `Room.get` return this with the exact side-path pair.
    Manifest builders combine its source policy with derived lazy policy;
    rendering checks its capture error before reading the captured bytes.
    No live source access or renderer output is part of this record.
    """

    untracked: bool
    """Whether this File is untracked by the selected VCS.

    *Implementation detail: presets report false because VCS tracking does not
    apply to fixture content.*
    """
    change_type: ChangeType
    """
    What kind of change the file has.
    """
    # TODO: should this be an *override* over *derived* values?
    #
    # At the very least, maybe pick a better name.
    lazy_reason_override: Optional[LazyReason]
    """
    Explicit source policy for laziness that manifest metadata cannot derive.

    Callers expected to combine it with `untracked`, `change_type` and other
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

    `Room.get` returns this variant for captured data without link facts.
    Consumers read `path` as the exact captured
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

    `Room.get` returns this variant with authenticated raw payload and link
    facts, including any reached target bytes. Consumers use `link` directly
    instead of deriving or probing sidecar paths.

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
    *Implementation detail: it does store its own hidden identity to ensure
    validity of operations, to avoid handing access to unrelated information.*

    # Thread boundary
    Room is not and must not be responsible for creating or managing comments
    on `Thread`s, that is the responsibility of `Thread` class, `Room`
    only locates and creates threads.
    Exception to this rule is `apply_review_batch`, because it spans multiple
    threads, and is a forced performance optimization.

    # Capture boundary

    Room exposes persisted correspondence through `capture_context`. Concrete
    source setup interprets it outside this module and supplies a source and
    selection to `RoomLord.corresponding_room`. Room does not construct
    sources, interpret revisions, or inspect source roots.

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
        """Return captured labels, aggregate line counts, and the Room's Tab.

        # Parameters

        - `snapshot_id`: Exact retained Snapshot key returned by capture; it must
          belong to this Room.

        # Usage

        Read this after `RoomLord.corresponding_room` or `RoomLord.find_room` when a
        response or `dirdiff.formats.ComposeContext` needs captured side labels. Use
        `manifested` for File paths and metadata, and `get` for their captured contents.

        # Returns

        A `SnapshotMeta` for this Snapshot. Its Tab selects display rules; its side
        labels describe the captured states and its aggregate line totals are either
        both present or both `None`. None of these fields is a live source lookup.

        # Failures

        - An unknown or cross-Room key raises `DirdiffError`.
        - A persisted Room naming an unknown Tab raises `AssertionError`.
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

        The iterator performs no live source access and does not read captured
        contents. Lazy overrides accompany their exact File pairs.

        # Usage

        Iterate this after capture to build a manifest or delayed-File response.
        Keep each nullable path pair together; that pair is the address required
        by `get`.

        # Returns

        The iterator follows persisted Snapshot File order. Each yielded tuple
        contains:

        - First, its optional left repository path.
        - Second, its optional right repository path. At
          least one path is present; neither is a capture path.
        - Third, the `FileMeta` for that exact path pair,
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
                    "untracked": not file.tracked,
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
        or symbolic-link variant. Its captured bytes and any link facts are
        authenticated before return.

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
                "untracked": not file.tracked,
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
        scope: Literal["snapshot", "room"],
    ) -> tuple[tuple[Thread, ...], int, int]:
        """Return one bounded Thread page and its inclusive activity pivot.

        `None` selects the latest Room activity in the same persistence read.
        Passing a returned concrete pivot makes later pages observe the same
        Thread existence, lifecycle state, ordering, count, and actions.

        # Parameters

        - `snapshot_id`: Exact displayed Snapshot used for Room lookup and
          placement selection.
        - `page`: One-based page number.
        - `limit`: Positive maximum number of Threads in this page.
        - `state`: Lifecycle filter for all Threads or only open Threads.
        - `through_activity_id`: Inclusive pivot from page one, or `None` to
          choose it with this read.
        - `attention`: Optional open-Thread role filter used by agent inboxes.
        - `scope`: Require a placement in `snapshot_id`, or include every Room
          Thread for browser History.

        # Usage

        Start with `through_activity_id=None`, retain the concrete pivot from the
        result, and pass it to each later page. Browser History uses Room scope;
        agent inboxes use Snapshot scope. Use the returned total to stop paging
        rather than selecting a new pivot.

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
            scope=scope,
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
        """Return the complete persisted selection and Mark identity.

        # Usage

        Source setup interprets `selection` outside Room and constructs a source.
        Pass these persisted facts and the source to `RoomLord.corresponding_room`
        to capture under the same correspondence.

        # Returns

        A `RoomCaptureContext` containing freshly decoded law facts. No source
        operations or filesystem reads occur here.

        # Failures

        - Malformed persisted correspondence raises `AssertionError`; no
          missing facts are replaced.
        """
        identity = self._identity
        if identity.tab == "preset":
            assert identity.mark_id is None
        else:
            assert identity.mark_id is not None and identity.mark_id > 0
        try:
            correspondence: JsonValue = json.loads(identity.correspondence_key)
        except (TypeError, ValueError) as exc:
            raise AssertionError(
                "invalid persisted Room correspondence"
            ) from exc
        selection: CaptureSelection
        if identity.tab == "pull-request":
            assert (
                isinstance(correspondence, str) and correspondence.strip() != ""
            )
            selection = PullRequestCaptureSelection(correspondence)
        else:
            assert isinstance(correspondence, dict)
            if identity.tab == "head":
                assert set(correspondence) == {"commit"}
                baseline = correspondence["commit"]
                assert isinstance(baseline, str) and baseline.strip() != ""
                selection = HeadCaptureSelection(baseline)
            elif identity.tab == "refs":
                assert set(correspondence) == {"left", "right"}
                left, right = correspondence["left"], correspondence["right"]
                assert isinstance(left, str) and left.strip() != ""
                assert isinstance(right, str) and right.strip() != ""
                selection = RevisionsCaptureSelection(left, right)
            elif identity.tab == "preset":
                assert set(correspondence) == {"catalog", "subset"}
                catalog, subset = (
                    correspondence["catalog"],
                    correspondence["subset"],
                )
                assert isinstance(catalog, str) and catalog.strip() != ""
                assert isinstance(subset, str) and subset.strip() != ""
                selection = PresetCaptureSelection(catalog, subset)
            else:
                assert identity.tab == "branch-review"
                assert set(correspondence) == {"base", "review"}
                base, review = correspondence["base"], correspondence["review"]
                assert base is not None and review is not None
                selection = BranchReviewCaptureSelection(base, review)
        return {"mark_id": identity.mark_id, "selection": selection}

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
    Snapshot key to recover its Room for follow-up operations. Every capture
    uses the same entrypoint with explicit prepared correspondence facts.

    # Usage

    Prepare a source and matching correspondence facts first. The Refs example
    in `dirdiff.change_source.GitChangeSource` supplies `source`, `selection`,
    and both labels; its repository's active Mark supplies `mark_id`.
    `room_lord` is the application's existing
    `dirdiff.room_lord.RoomLord`. Capture first; render the retained Files with
    `dirdiff.formats.Composer` and a selected diff engine afterward:

    ```python
    from dirdiff.engines import DirdiffError, TextDiffEngine
    from dirdiff.formats import ComposeContext, Composer

    room, snapshot_id = room_lord.corresponding_room(
        mark_id=mark_id,
        selection=selection,
        source=source,
        left_label=left_label,
        right_label=right_label,
    )
    snapshot = room.meta(snapshot_id)
    for left_path, right_path, _metadata in room.manifested(snapshot_id):
        left, right, metadata = room.get(snapshot_id, left_path, right_path)
        if metadata["capture_error"] is not None:
            raise DirdiffError(metadata["capture_error"])
        context = ComposeContext.build(
            left_path=str(left_path) if left_path is not None else None,
            right_path=str(right_path) if right_path is not None else None,
            left_label=snapshot["left_label"],
            right_label=snapshot["right_label"],
            left_link=left.link if left is not None and left.kind == "symlink" else None,
            right_link=right.link if right is not None and right.kind == "symlink" else None,
            renderer=TextDiffEngine(),
        )
        composed = Composer().compose(
            left.path.read_bytes() if left is not None else None,
            right.path.read_bytes() if right is not None else None,
            context,
        )
    ```

    The returned `Room` is the entrypoint for Snapshot reads and review work.
    `RoomLord` does not retain a selected Room or Snapshot between calls.
    """

    def __init__(self, database: RoomStore, store_path: Path) -> None:
        """Create the application boundary for one database and store root.

        Construction creates no directories. Concrete setup must validate
        physical source placement before calling capture; sources without physical roots need no such check.

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
        mark_id: int | None,
        selection: CaptureSelection,
        source: ChangeSourceProtocol,
        left_label: str,
        right_label: str,
    ) -> tuple[Room, UUID]:
        """Apply one correspondence law and capture the supplied source in its Room.

        Source setup prepares the selection and constructs the source before
        this call. Correspondence identifies only the Room; source results
        independently determine Snapshot equality.

        # Parameters

        - `mark_id`: Positive active Mark id, or `None` only for a Preset Room.
        - `selection`: Complete prepared law facts. Source identities are opaque
          here; branch JSON values must remain unchanged during the call.
        - `source`: One selected observation source, used only through
          `dirdiff.change_source.ChangeSourceProtocol.changes` and `load`.
        - `left_label`: Human label persisted with the left captured state and
          used in any left-side diagnosis.
        - `right_label`: Corresponding right-state label.

        # Usage

        Pass an already constructed source alongside its independent law facts.
        Storage placement and concrete input validation belong to source setup.
        Keep both returned values for Snapshot-scoped follow-up operations.
        To capture under an existing Room identity, supply its `Room.capture_context`
        facts with a newly prepared source.

        # Returns

        - First, the existing or newly selected `Room` under this law.
        - Second, the immutable Snapshot id captured or reused within that Room.

        # Failures

        - Invalid law values or Mark presence assert.
        - Source and persistence operation failures propagate; expected side
          failures remain captured under the source contract.
        """
        assert mark_id is None or mark_id > 0
        correspondence: JsonValue
        if isinstance(selection, PresetCaptureSelection):
            tab: RoomTab = "preset"
            assert mark_id is None
            assert (
                selection.catalog.strip() != ""
                and selection.subset.strip() != ""
            )
            correspondence = {
                "catalog": selection.catalog,
                "subset": selection.subset,
            }
        else:
            assert mark_id is not None
            if isinstance(selection, HeadCaptureSelection):
                tab = "head"
                assert selection.baseline.strip() != ""
                correspondence = {"commit": selection.baseline}
            elif isinstance(selection, RevisionsCaptureSelection):
                tab = "refs"
                assert (
                    selection.left.strip() != ""
                    and selection.right.strip() != ""
                )
                correspondence = {
                    "left": selection.left,
                    "right": selection.right,
                }
            elif isinstance(selection, BranchReviewCaptureSelection):
                tab = "branch-review"
                assert (
                    selection.base is not None and selection.review is not None
                )
                correspondence = {
                    "base": selection.base,
                    "review": selection.review,
                }
            else:
                assert isinstance(selection, PullRequestCaptureSelection)
                tab = "pull-request"
                assert selection.url.strip() != ""
                correspondence = selection.url
        identity = RoomIdentity(
            mark_id=mark_id,
            tab=tab,
            correspondence_key=json.dumps(
                correspondence,
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        room = Room(
            database=self._database,
            identity=identity,
            staging_path=self._staging_path,
            snapshots_path=self._snapshots_path,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
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
            source=source,
            left_label=left_label,
            right_label=right_label,
        )
        return room, snapshot_id

    def find_room(self, snapshot_id: UUID) -> Room:
        """Return the Room containing an existing Snapshot key.

        The Snapshot id is globally unique in this database. Missing keys are
        rejected; this lookup neither executes a Tab law nor reads live source
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

    `RoomLord.corresponding_room` constructs and calls the
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
        source: ChangeSourceProtocol,
        left_label: str,
        right_label: str,
    ) -> UUID:
        """Capture current source state and return its retained Snapshot key.

        Equality combines repository paths, semantic properties, source content
        identities, VCS tracking, change type, and explicit policy text.
        Source order, human labels, and aggregate line counts do not affect
        identity. Every Thread contained by the Room is placed in the captured
        Snapshot before it becomes visible.

        # Parameters

        - `source`: Selected `dirdiff.change_source.ChangeSourceProtocol` used for File facts and side bytes.
        - `left_label`: Human display and diagnostic label retained for the left state.
        - `right_label`: Human display and diagnostic label retained for the right state.

        # Usage

        `RoomLord.corresponding_room` calls this once after
        receiving the selected source and labels for one Room identity.

        # Failures

        - Duplicate observed File pairs raise `DirdiffError`.
        - Source, filesystem, and persistence operation failures propagate;
          expected outer read failures remain captured File errors.
        - Raises `AssertionError` when source output, retained contents, or
          publication records violate Snapshot invariants.
        """
        self._staging_path.mkdir(parents=True, exist_ok=True)
        self._snapshots_path.mkdir(parents=True, exist_ok=True)
        self._lock_path.touch(exist_ok=True)

        changes: WorkspaceChanges = source.changes()
        paths: tuple[FileChange, ...] = changes.files
        added_lines = changes.added_lines
        removed_lines = changes.removed_lines
        captured_paths: set[tuple[FileKey | None, FileKey | None]] = set()
        for file in paths:
            keys = (
                file.left.key if file.left is not None else None,
                file.right.key if file.right is not None else None,
            )
            if keys in captured_paths:
                raise DirdiffError(
                    "Source returned duplicate left/right paths: "
                    f"{tuple(key.path if key is not None else None for key in keys)!r}"
                )
            captured_paths.add(keys)
        # Sorting describes a set for equality. Source observation and all
        # acquisition timing remain independent of Snapshot publication.
        sorted_files = sorted(
            paths,
            key=lambda file: (
                file.left.key.path if file.left is not None else "",
                file.right.key.path if file.right is not None else "",
            ),
        )
        identities = {
            side.key: side.content_identity
            for file in sorted_files
            for side in (file.left, file.right)
            if side is not None
        }

        def captured_state_hash() -> bytes:
            """Hash semantic File facts and source-supplied side identities.

            The source determines each side's content identity. RoomLord combines
            it with paths, properties, and File metadata to define equality
            inside this Room. It never interprets a content identity as a
            storage address or acquisition instruction.
            """
            digest = hashlib.sha256(_SNAPSHOT_HASH_DOMAIN)
            for file in sorted_files:
                values: list[bytes | None] = []
                side: FileSide | None
                for side in (file.left, file.right):
                    if side is None:
                        values.extend((None, None, None, None))
                    else:
                        properties: FileProperties = side.properties
                        values.extend(
                            (
                                side.key.path.encode(),
                                properties.kind.encode(),
                                b"1" if properties.executable else b"0",
                                identities[side.key],
                            )
                        )
                values.extend(
                    (
                        b"1" if file.untracked else b"0",
                        file.change_type.encode(),
                        None
                        if file.lazy_reason_override is None
                        else file.lazy_reason_override.encode(),
                        None
                        if file.lazy_reason_content is None
                        else file.lazy_reason_content.encode(),
                    )
                )
                for value in values:
                    if value is None:
                        digest.update(b"\x00")
                    else:
                        digest.update(b"\x01")
                        digest.update(len(value).to_bytes(8, "big"))
                        digest.update(value)
            return digest.digest()

        snapshot_hash = captured_state_hash()

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
            self._identity, snapshot_hash
        )
        if retained_id is not None:
            return verified_retained_snapshot(retained_id)

        # A new Snapshot needs all data. The source returns observed results or
        # acquires exact listed versions without exposing which operation ran.
        acquired: Sequence[LoadedFile] = source.load(sorted_files)
        assert len(acquired) == len(sorted_files), (
            "source returned wrong File count"
        )
        for position, loaded in enumerate(acquired):
            assert loaded.change is sorted_files[position], (
                "source returned a different observed File"
            )
            file = loaded.change
            assert (loaded.left is None) == (file.left is None)
            assert (loaded.right is None) == (file.right is None)
            if loaded.left is not None and file.left is not None:
                identities[file.left.key] = loaded.left.content_identity
            if loaded.right is not None and file.right is not None:
                identities[file.right.key] = loaded.right.content_identity
        acquired_hash = captured_state_hash()
        if acquired_hash != snapshot_hash:
            snapshot_hash = acquired_hash
            retained_id = self._database.snapshot_id_for_content(
                self._identity, snapshot_hash
            )
            if retained_id is not None:
                return verified_retained_snapshot(retained_id)

        def substituted_side(
            loaded: FileData,
            side_path: str,
            side_name: str,
            side_label: str,
            errors: list[str],
        ) -> bytes:
            """Create persisted diagnostic bytes for an expected outer failure.

            # Parameters

            - `loaded`: Complete source result for this present side.
            - `side_path`: Canonical path retained beside the captured data.
            - `side_name`: Left or right display slot for the diagnosis.
            - `side_label`: Prepared capture label for that selected state.
            - `errors`: This File's side-ordered failure messages.
            """
            if isinstance(loaded.content, DirdiffError):
                failure = (
                    f"Could not capture {side_name} side "
                    f"{side_label}:{side_path}: {loaded.content}"
                )
                errors.append(failure)
                return f"{_CAPTURE_ERROR_PREAMBLE}\n\n{failure}\n".encode()
            return loaded.content

        captured: list[
            tuple[
                str | None,
                bytes | None,
                str | None,
                bytes | None,
                bool,
                ChangeType,
                LazyReason | None,
                str | None,
                str | None,
                CapturedLink | None,
                CapturedLink | None,
            ]
        ] = []
        for loaded in acquired:
            file = loaded.change
            errors: list[str] = []
            left, right = loaded.left, loaded.right
            captured.append(
                (
                    file.left.key.path if file.left is not None else None,
                    substituted_side(
                        left, file.left.key.path, "left", left_label, errors
                    )
                    if left is not None and file.left is not None
                    else None,
                    file.right.key.path if file.right is not None else None,
                    substituted_side(
                        right, file.right.key.path, "right", right_label, errors
                    )
                    if right is not None and file.right is not None
                    else None,
                    not file.untracked,
                    file.change_type,
                    file.lazy_reason_override,
                    file.lazy_reason_content,
                    "\n".join(errors) if errors else None,
                    left.link if left is not None else None,
                    right.link if right is not None else None,
                )
            )

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
