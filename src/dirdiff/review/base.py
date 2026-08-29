"""Define contracts shared across persistent review operations.

## Public interface

This module contains the command, target, error, and view types re-exported by
`dirdiff.review`. Sibling modules use the same values for ordinary Thread
operations, Snapshot placement, and external-agent batches.

## Purpose and boundaries

The types validate context-free review invariants and describe domain results.
The small shared functions validate authors and Comment bodies, timestamp
actions, and acquire the Room write lock. This module does not load captured
Files, derive placements, reduce Thread history, or perform persistence writes.
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Literal, NotRequired, Optional, TypedDict
from uuid import UUID

from dirdiff.db import RoomStore, UserProfileRecord
from dirdiff.engines import DirdiffError

__all__ = [
    "AddComment",
    "ChangeThreadState",
    "CreateThread",
    "DeleteComment",
    "EditComment",
    "FilePair",
    "LineRange",
    "ProfileAuthor",
    "ReviewCommentView",
    "ReviewError",
    "ReviewErrorCode",
    "ReviewExcerptView",
    "ReviewFilePairView",
    "ReviewOriginView",
    "ReviewProfileView",
    "TextTarget",
    "ThreadDiscussionView",
    "ThreadPlacementView",
    "ThreadSummaryView",
    "ThreadUpdateView",
    "action_timestamp",
    "room_write_lock",
    "validate_author",
    "validate_comment_body",
]

ReviewErrorCode = Literal[
    "profile_not_found",
    "thread_not_found",
    "comment_not_found",
    "invalid_target",
    "revision_conflict",
    "state_conflict",
    "forbidden",
]
"""Stable machine-readable review failures used by HTTP boundaries.

- `profile_not_found`, `thread_not_found`, and `comment_not_found` identify the
  missing durable entity.
- `invalid_target` rejects invalid authored input or a target unavailable in
  captured state.
- `revision_conflict` rejects a stale expected discussion revision.
- `state_conflict` rejects an instrument that cannot follow current state.
- `forbidden` rejects an operation the acting Profile may not perform.
"""


class ReviewError(DirdiffError):
    """Report one expected review failure with a stable code.

    Review operations raise this value when valid caller input cannot apply to
    current persisted or captured state. The server maps `code` to an HTTP
    status and returns `message` for presentation.

    It does not represent programming errors, persistence corruption, or an
    unexpected failure that should propagate.
    """

    def __init__(self, code: ReviewErrorCode, message: str) -> None:
        """Create an expected failure suitable for the review HTTP boundary.

        # Parameters

        - `code`: Stable machine category used to select an HTTP status and by
          clients to distinguish conflicts without parsing prose.
        - `message`: Concrete presentation text describing this occurrence.
        """
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FilePair:
    """Identify one File by its exact nullable left and right captured paths.

    Build this value at review boundaries from the exact nullable paths carried
    by manifest, composed File, or agent input. Thread targets and File lookup
    use the pair as one identity.

    At least one normalized repository-relative POSIX path is present. A
    one-sided pair represents an added or deleted File. It is not a captured
    filesystem path or a display name.
    """

    left_path: Optional[str]
    """Normalized repository-relative path of the captured left side.

    `None` means the File did not exist on the left; `right_path` must then be
    present. Callers must preserve this absence when addressing the File.
    """

    right_path: Optional[str]
    """Normalized repository-relative path of the captured right side.

    `None` means the File did not exist on the right; `left_path` must then be
    present. The pair, rather than either path alone, is the File identity.
    """

    def __post_init__(self) -> None:
        """Reject a pair that cannot address one captured File safely.

        At least one side must be present. Present paths must already be
        canonical repository-relative POSIX names; construction never cleans
        or interprets a caller's path on its behalf.

        # Failures

        - Raises `ValueError` when both sides are absent or a present side is
          empty, absolute, parent-traversing, or not normalized POSIX form.
        """
        if self.left_path is None and self.right_path is None:
            raise ValueError("A review File pair requires at least one side.")
        for value in (self.left_path, self.right_path):
            if value is None:
                continue
            path = PurePosixPath(value)
            if (
                value in {"", ".", ".."}
                or path.is_absolute()
                or ".." in path.parts
                or value != path.as_posix()
            ):
                raise ValueError(
                    "Review File sides must be normalized relative names."
                )


@dataclass(frozen=True)
class LineRange:
    """Identify one positive, one-based inclusive line range.

    Build this from validated browser or agent input and attach it to a
    `TextTarget`. Both endpoints are inclusive, so equal values select one line.

    The coordinates are local to one bay side. They are not File lines, rendered
    row indexes, or half-open offsets.
    """

    start_line: int
    """First selected bay-side source line, counted from one and included.

    The value is interpreted only with `end_line` and the enclosing bay; it is
    never a rendered row index or a File-global line number.
    """

    end_line: int
    """Last selected bay-side source line, counted from one and included.

    It may equal `start_line` for a single-line target but may not precede it.
    Validation against actual bay text happens when the Thread is created.
    """

    def __post_init__(self) -> None:
        """Require coordinates that can describe an inclusive source range.

        This checks positivity and ordering only. The Room later proves that
        the range fits the selected bay in the origin Snapshot.

        # Failures

        - Raises `ValueError` when either endpoint is below one or `end_line`
          precedes `start_line`.
        """
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("Review line range must be positive and ordered.")


@dataclass(frozen=True)
class TextTarget:
    """Address one selected-side line range in one composed bay.

    Browser and agent boundaries construct this value before asking Room to
    create a Thread. Review validates the File, bay, selected side, and range
    against the exact origin Snapshot.

    `bay_key` is the universal sub-File coordinate emitted by composition.
    Review stores it without interpreting format-specific spelling. The target
    cannot address image bytes, a complete File, or private source coordinates.
    """

    file: FilePair
    """Exact nullable path pair of the captured File containing the bay.

    Thread creation uses the complete pair for one focused Snapshot lookup;
    callers must not identify renames or one-sided Files by a single path.
    """

    bay_key: str
    """Public bay identity emitted by composition for this File.

    It is meaningful only with `file` and is checked against the origin
    Snapshot. Callers must pass the key they received rather than fabricate or
    reinterpret format-specific spelling.
    """

    side: Literal["left", "right"]
    """Captured side whose bay-local source lines the author selected.

    The chosen side must be present in both `file` and the composed bay. Review
    does not redirect an absent side to its counterpart.
    """

    range: LineRange
    """Inclusive source-line selection within `bay_key` on `side`.

    Thread creation checks the range against composed content and retains it as
    the immutable origin even when later placement moves or becomes outdated.
    """

    kind: Literal["text"] = "text"
    """Wire discriminator identifying the target as a text-range selection.

    Callers leave the default unchanged. Historical File-level origins are
    readable through view types but are not valid creation commands.
    """

    def __post_init__(self) -> None:
        """Reject a target whose public coordinates contradict its File pair.

        This boundary requires a nonempty bay key and a present selected side;
        Room creation later proves that the named bay and range exist.

        # Failures

        - Raises `ValueError` when `bay_key` is empty or `side` selects a path
          absent from `file`.
        """
        if self.bay_key == "":
            raise ValueError("Review bay key cannot be empty.")
        if self.side == "left" and self.file.left_path is None:
            raise ValueError("The selected left side is absent.")
        if self.side == "right" and self.file.right_path is None:
            raise ValueError("The selected right side is absent.")


ReviewTarget = TextTarget
"""A valid target for creating a new review Thread.

Pass this alias to Thread creation. New Threads always select rendered text.

Historical File-level origins remain readable through view types but cannot be
constructed through this alias.
"""


@dataclass(frozen=True)
class ProfileAuthor:
    """Identify one author through an ordinary durable Profile row.

    Review boundaries construct this from the acting persisted Profile and put
    it on every command. Thread methods use the id for attribution and
    authorization checks.

    Humans and registered agents share this shape. It carries no display name,
    role, or permission by itself.
    """

    profile_id: int
    """Durable Profile identity attributed to the authored operation.

    It must be positive and still name a persisted Profile when a write runs.
    Thread methods use it for both displayed attribution and permission checks.
    """

    def __post_init__(self) -> None:
        """Reject values outside the durable Profile id domain.

        Existence is intentionally checked at write time so a deleted or
        unknown Profile cannot be accepted from a stale command.

        # Failures

        - Raises `ValueError` when `profile_id` is not positive.
        """
        if self.profile_id < 1:
            raise ValueError("Review Profile id must be positive.")


@dataclass(frozen=True)
class CreateThread:
    """Create one globally identified Thread and its first Comment.

    Pass this command to `Room.create_thread` or an agent batch after validating
    the target against the current Snapshot. The caller supplies fresh Thread,
    operation, and Comment UUIDs plus the first non-empty body.

    The command creates one discussion only. It does not select a Room, derive a
    placement for later Snapshots, or substitute ids on conflict.
    """

    thread_id: UUID
    """Caller-generated global identity for the discussion being created.

    It must be fresh across review history. Persistence rejects a collision;
    review never substitutes another id or treats the command as an update.
    """

    operation_id: UUID
    """Caller-generated identity of the creation action in the event log.

    It names the same atomic write as `thread_id` and `comment_id` but remains
    distinct from both. A reused id is a persistence conflict, not idempotency.
    """

    comment_id: UUID
    """Caller-generated global identity for the sequence-zero Comment.

    The Comment is inserted atomically with the Thread origin and creation
    action. It must not identify any earlier Comment.
    """

    author: ProfileAuthor
    """Acting Profile attributed to the first Comment and creation action.

    The Profile must exist when the Room applies the command. Its current name
    is read from persistence; the command carries no cached display metadata.
    """

    target: ReviewTarget
    """Public text coordinate proposed as the immutable Thread origin.

    The Room resolves it against the supplied Snapshot, verifies File, bay,
    side, and range, then derives private relocation coordinates before insert.
    """

    body: str
    """Authored text persisted as the Thread's sequence-zero Comment.

    Whitespace-only input is rejected while the Room lock is held. The text is
    stored verbatim and returned through the materialized discussion view.
    """


@dataclass(frozen=True)
class AddComment:
    """Append one globally identified Comment to a bound Thread.

    Pass this to a bound `Thread` reply or lifecycle method. The Thread supplies
    discussion and Snapshot identity; this command supplies fresh operation and
    Comment ids, author, and non-empty body.

    It does not choose an instrument, edit an existing Comment, or carry Thread
    lifecycle state.
    """

    operation_id: UUID
    """Caller-generated identity for this one Comment append action.

    It is persisted only after the bound Thread and author pass validation and
    must not reuse an operation already present in review history.
    """

    comment_id: UUID
    """Caller-generated global identity assigned to the new Comment.

    The id becomes stable across later edits and deletion. It must be fresh;
    appending never replaces a Comment with the same id.
    """

    author: ProfileAuthor
    """Existing Profile attributed to the appended Comment.

    Validation happens against current persistence at append time. The actor's
    role does not come from this value; the selected instrument supplies it.
    """

    body: str
    """Authored Comment text appended without normalization.

    Whitespace-only text is rejected. Once accepted, later actions may replace
    or tombstone it but do not alter the Comment's identity or sequence.
    """


@dataclass(frozen=True)
class EditComment:
    """Replace one authored Comment body.

    Pass this to `Thread.edit_comment` with the Comment id supplied separately
    by the bound call. The command supplies a fresh operation id, acting
    Profile, and non-empty replacement body. The Thread reloads current history
    under the Room lock and records that current Comment revision on the action.

    It cannot move the Comment, alter attribution, or ask the backend to accept
    a caller-selected stale revision.
    """

    operation_id: UUID
    """Caller-generated identity for the accepted Comment edit action.

    It is persisted after current authorship and deletion state are checked and
    must not collide with another review operation.
    """

    author: ProfileAuthor
    """Profile attempting to replace the Comment body.

    The current persisted Comment author must have this id. Review rejects a
    different Profile instead of transferring authorship.
    """

    body: str
    """Replacement text for the current non-deleted Comment.

    Whitespace-only input is rejected. Acceptance increments the Comment
    revision while retaining identity, author, sequence, and creation time.
    """


@dataclass(frozen=True)
class DeleteComment:
    """Attribute one Comment tombstone to a valid acting Profile.

    Pass this to `Thread.delete_comment` with the Comment id supplied by the
    bound call. The command supplies a fresh operation id and acting Profile.
    The Thread reloads current history under the Room lock and records the
    current Comment revision on the tombstone action.

    The Comment remains in sequence as a tombstone. This does not delete its
    Thread or erase authored history.
    """

    operation_id: UUID
    """Caller-generated identity for the Comment tombstone action.

    It is distinct from the Comment id and becomes part of append-only history
    only after the Comment is found and confirmed not already deleted.
    """

    author: ProfileAuthor
    """Existing Profile attributed to the tombstone action.

    Comment deletion is not restricted to the original author in this model;
    the action retains this actor without changing original Comment attribution.
    """


@dataclass(frozen=True)
class ChangeThreadState:
    """Apply one Thread lifecycle transition, optionally with an explanation.

    Pass this to a bound Thread's resolve, reopen, or delete operation.
    `comment_id` and `body` together add one Comment with the transition; both
    are `None` for a bare transition.

    The command does not name the desired state by itself. The called Thread
    method supplies that meaning, and terminal deletion never carries Comment
    fields.
    """

    operation_id: UUID
    """Caller-generated identity of this one Thread state transition.

    The id is recorded only if the bound Thread's current state permits the
    selected resolve, reopen, or delete method.
    """

    author: ProfileAuthor
    """Existing Profile attributed to the lifecycle action.

    The called Thread method supplies the transition semantics; this value
    carries identity for attribution and current persistence validation only.
    """

    comment_id: Optional[UUID]
    """Fresh Comment identity when resolve or reopen includes an explanation.

    It must be present exactly when `body` is present. Thread deletion requires
    both fields to remain `None` and never creates a Comment.
    """

    body: Optional[str]
    """Optional explanation appended atomically with resolve or reopen.

    When present it must be nonblank and paired with a fresh `comment_id`;
    absence requests a bare transition. Delete callers must always omit it.
    """

    def __post_init__(self) -> None:
        """Reject a lifecycle command with a partial explanation Comment.

        The chosen Thread method later decides whether that paired Comment is
        permitted; construction only enforces equal presence of id and body.

        # Failures

        - Raises `AssertionError` when exactly one of `comment_id` and `body` is
          present.
        """
        assert (self.comment_id is None) == (self.body is None)


class ReviewProfileView(TypedDict):
    """Expose current Profile attribution beside a public Comment.

    Discussion reads build this from persisted Profile records. Callers use
    `profile_id` for stable identity and `display_name` for presentation.

    It carries no role, agent registration, or permission.
    """

    profile_id: int
    """Stable durable identity of the Profile attributed to the action.

    Clients use it for identity across username changes; it does not by itself
    state a review role or grant permission.
    """

    display_name: str
    """Current persisted username presented for the attributed Profile.

    The name is read when the discussion is materialized and may differ from what was
    displayed earlier; durable attribution remains `profile_id`.
    """


class ReviewCommentView(TypedDict):
    """Return one current Comment or retained deletion tombstone.

    Discussion and update reads return this current view after reducing
    immutable actions.

    Sequence remains stable inside the Thread. Revision increments on edits;
    deletion retains attribution and timestamps while setting `body` to `None`.
    The view does not expose the underlying action history.
    """

    comment_id: str
    """Stable global identity assigned when the Comment was created.

    Edits and deletion retain this value so later commands and UI state continue
    to address the same sequence entry.
    """

    sequence: int
    """Zero-based creation position of the Comment within its Thread.

    Action reduction retains this order through edits and tombstoning; it is distinct
    from the discussion revision, which counts every action.
    """

    author: ReviewProfileView
    """Current public Profile view for the Comment's original author.

    Later edits and deletion do not replace this attribution, even when another
    Profile performs the deletion action.
    """

    revision: int
    """Current zero-based version of this Comment's materialized content state.

    Creation starts at zero; each accepted edit or deletion increments it once.
    It is not the Thread-wide discussion revision.
    """

    body: Optional[str]
    """Latest authored text, or `None` after deletion.

    Empty absence is never used for an ordinary Comment: a deleted entry is
    retained in order and is also marked by `deleted`.
    """

    deleted: bool
    """Whether an accepted deletion action has tombstoned this Comment.

    When true, `body` is `None`, revision includes the deletion, and identity,
    author, sequence, and timestamps remain visible.
    """

    created_at: datetime
    """UTC-aware time recorded by the sequence entry's creation action.

    It remains fixed through every later edit or deletion and is parsed from
    the immutable action timestamp.
    """

    updated_at: datetime
    """UTC-aware time of the latest action affecting this Comment.

    It equals `created_at` before any edit and advances on each edit or
    tombstone; unrelated Thread actions do not change it.
    """


class ReviewFilePairView(TypedDict):
    """Expose a reviewed File's exact nullable repository path pair.

    Origin views carry this shape so callers can address the same captured File
    in later operations. At least one side is present.

    These are repository paths, not absolute captured paths or a display label.
    """

    left_path: str | None
    """Repository-relative path of the immutable origin File's left side.

    `None` records true side absence. Consumers combine it with `right_path` to
    address the exact captured File rather than choosing one available name.
    """

    right_path: str | None
    """Repository-relative path of the immutable origin File's right side.

    `None` records true side absence. At least one side in the enclosing pair is
    present, including for added, deleted, and renamed Files.
    """


class ReviewTextBayView(TypedDict):
    """Expose the public bay coordinate of a reviewed text target.

    Origin and placement views carry this value back to browser and agent
    callers. Use `bay_key` with the enclosing File pair and side.

    The key alone is not globally unique and does not reveal bay content.
    """

    bay_key: str
    """Composition-issued identity of the bay named by the enclosing view.

    The key is File-local and must be combined with the origin File pair and
    side; callers must not treat it as a globally unique location.
    """


class ReviewLineRangeView(TypedDict):
    """Expose one inclusive one-based line range within a text bay.

    Review views use this shape for origin and current placement coordinates.
    Both endpoints refer to the selected bay side.

    It is not a rendered-row interval or File-global range.
    """

    start_line: int
    """Positive one-based first source line included in the range.

    The coordinate is local to the bay and side supplied by the enclosing
    origin or placement view.
    """

    end_line: int
    """One-based last source line included in the range.

    It is no earlier than `start_line`; equal endpoints identify one source
    line rather than an empty interval.
    """


class ReviewExcerptView(TypedDict):
    """Return one bounded selected-side excerpt from the origin Snapshot.

    `start_line` numbers the first entry of `lines`. The selected inclusive
    range lies inside that bounded context and uses the same one-based source
    coordinates.
    """

    side: Literal["left", "right"]
    """Origin File side from which all excerpt lines were reconstructed.

    The excerpt never aligns or mixes the opposite side, even for a modified
    File with content on both sides.
    """

    start_line: int
    """One-based bay-side source coordinate represented by `lines[0]`.

    Consumers add a zero-based index in `lines` to recover each displayed
    source line number.
    """

    selected_start_line: int
    """Inclusive first line of the authored origin selection.

    It is expressed in the same bay-side coordinates as `start_line` and lies
    within the returned `lines` window.
    """

    selected_end_line: int
    """Inclusive last line of the authored origin selection.

    It is no earlier than `selected_start_line`, and the complete selection is
    always retained in `lines` even when surrounding context is clipped.
    """

    lines: list[str]
    """Origin-side source lines in display order without newline terminators.

    The list contains the complete selected range and up to three available
    context lines before and after it; Snapshot boundaries may shorten context.
    """


class TextReviewOriginView(TypedDict):
    """Expose the immutable text target that created a Thread.

    Discussion and summary reads return this variant with the exact File, bay,
    side, and original range. Full reads may attach a bounded excerpt.

    The origin never changes when placement moves or becomes outdated.
    """

    kind: Literal["text"]
    """Discriminator selecting the constructible text-range origin variant.

    Consumers use it before reading bay, range, or optional excerpt fields.
    """

    file: ReviewFilePairView
    """Exact repository path pair of the File captured at Thread creation.

    It remains the Thread's File identity across every later placement and is
    the coordinate consumers combine with `bay` and `side`.
    """

    bay: ReviewTextBayView
    """Public identity of the composed bay selected at creation.

    This origin bay never changes. A later `bay-lost` placement may name a
    different landing without rewriting this value.
    """

    side: Literal["left", "right"]
    """Present origin File side whose source range the author selected.

    Every derived placement preserves this side; loss is reported explicitly
    instead of redirecting the Thread to the opposite side.
    """

    range: ReviewLineRangeView
    """Immutable inclusive bay-side coordinates selected at creation.

    Placement ranges may move as code moves, but this range remains the source
    coordinate used for original context and private relocation.
    """

    excerpt: NotRequired[ReviewExcerptView]
    """Bounded original source context included by full discussion reads.

    Summary reads omit the key entirely so they need not read captured bytes;
    consumers distinguish omission from an empty source excerpt.
    """


class FileStartReviewOriginView(TypedDict):
    """Expose one retained historical File-level Thread origin.

    Reads use this variant for discussions created before text-range-only
    targets. It identifies the File and selected side without a bay or range.

    New Thread creation cannot produce this shape.
    """

    kind: Literal["file-start"]
    """Discriminator selecting a retained historical File-level origin.

    Consumers must not expect a bay, range, or excerpt on this variant, and new
    Thread creation cannot produce it.
    """

    file: ReviewFilePairView
    """Exact repository path pair retained by the historical origin.

    It remains sufficient for File discovery even though no bay-level public
    coordinate was stored when the Thread was created.
    """

    side: Literal["left", "right"]
    """Present origin File side retained by the historical discussion.

    Reads preserve the side without manufacturing text coordinates that the
    original record never contained.
    """


type ReviewOriginView = TextReviewOriginView | FileStartReviewOriginView
"""Expose the immutable creation target of one Thread.

- `TextReviewOriginView` is the current constructible text target.
- `FileStartReviewOriginView` preserves historical File-level origins.

Consumers branch on `kind`. Placement is a separate value and may change across
Snapshots; origin never does.
"""


class ThreadRegionPlacementView(TypedDict):
    """Locate a Thread on a retained or changed text range.

    `region-kept` preserves the origin bytes; `region-changed` lands on the
    matched region after its content changed. Navigation uses `range` together
    with the origin's File, bay, and side.

    The value does not repeat those origin coordinates.
    """

    kind: Literal["region-kept", "region-changed"]
    """Outcome of placing the origin region in the selected Snapshot.

    `region-kept` means a unique structural and byte match; `region-changed`
    means the structural candidate remained unique but its bytes did not.
    """

    range: ReviewLineRangeView
    """Inclusive bay-side range where navigation lands in this Snapshot.

    For changed content the range is the surviving structural candidate's first
    line; callers use `kind` to decide whether to show outdated content.
    """


class ThreadRegionLostPlacementView(TypedDict):
    """Place a Thread at its origin bay after the text region is lost.

    Navigation uses the origin File, bay, and side and lands at bay start. There
    is no valid line range to expose.
    """

    kind: Literal["region-lost"]
    """State that no unique origin region survives inside the origin bay.

    The origin File, side, and bay still supply a valid landing, but there is no
    current line range for navigation or display.
    """


class ThreadBayLostPlacementView(TypedDict):
    """Place a Thread at another bay after its origin bay is lost.

    `bay` names the replacement landing point on the origin File and side. The
    original bay remains available only through the origin view.
    """

    kind: Literal["bay-lost"]
    """State that the origin bay no longer composes on the selected side.

    Navigation may use the replacement `bay`; the immutable origin continues
    to identify the bay in which the discussion began.
    """

    bay: ReviewTextBayView
    """Replacement bay chosen and persisted during Snapshot derivation.

    It is the first composed bay that carries the origin side. Reads use this
    stored choice directly and do not recalculate a landing.
    """


class ThreadSideLostPlacementView(TypedDict):
    """Keep a Thread discoverable after its selected File side is lost.

    History can still show the discussion through its origin File pair, but no
    bay or line navigation target remains.
    """

    kind: Literal["side-lost"]
    """State that the File remains but its origin side has no composed bay.

    The discussion remains discoverable through the origin File pair, while
    callers must not offer bay or line navigation.
    """


class ThreadFileAbsentPlacementView(TypedDict):
    """State that the Thread's exact File pair is absent from this Snapshot.

    Reads still return the discussion and immutable origin. No current File,
    side, bay, or range can be exposed.
    """

    kind: Literal["file-absent"]
    """State that no selected-Snapshot File has the immutable origin pair.

    The loading boundary verifies this absence. Consumers may show history but
    have no current File, side, bay, or range to navigate to.
    """


class ThreadFileUnreadablePlacementView(TypedDict):
    """State that the Thread's File exists but capture could not read it.

    Reads retain the discussion and origin while refusing to place it on
    generated error content. This is distinct from an absent File pair.
    """

    kind: Literal["file-unreadable"]
    """State that the origin File pair is present but its capture failed.

    Review refuses to locate a Thread on generated error text. Consumers keep
    the discussion visible without treating the File as absent.
    """


class ThreadWholeFilePlacementView(TypedDict):
    """Locate one retained historical File-level Thread on its File.

    History uses the origin File and side without inventing a bay coordinate.
    Only historical File-level origins can produce this placement.
    """

    kind: Literal["whole-file"]
    """State that a historical File-level origin still lands on its File.

    The placement deliberately provides no bay or range; only origin File and
    side coordinates are valid for this retained shape.
    """


type ThreadPlacementView = (
    ThreadRegionPlacementView
    | ThreadRegionLostPlacementView
    | ThreadBayLostPlacementView
    | ThreadSideLostPlacementView
    | ThreadFileAbsentPlacementView
    | ThreadFileUnreadablePlacementView
    | ThreadWholeFilePlacementView
)
"""Expose where one Thread lands in the selected Snapshot.

- Region variants land on text or at its original bay.
- `ThreadBayLostPlacementView` lands at another bay.
- `ThreadSideLostPlacementView` retains only File discovery.
- File absent and unreadable variants have no current code location.
- `ThreadWholeFilePlacementView` preserves historical File-level placement.

Combine this value with `ReviewOriginView`; placement deliberately avoids
repeating File, side, and origin bay facts.
"""


class ThreadDiscussionView(TypedDict):
    """Return one complete discussion as observed in a bounded Snapshot.

    The origin target never changes. Placement describes where it lands in the
    selected Snapshot; state, attention, revision, and Comments are the latest
    action reduction at the read's activity boundary.
    """

    thread_id: str
    """Stable global identity of the discussion represented by this view.

    It remains unchanged across Snapshots and all Comment and lifecycle actions.
    """

    snapshot_id: str
    """Exact immutable code universe used to interpret `placement`.

    The origin may belong to an earlier Snapshot; callers use this key for all
    follow-up operations on the returned placement.
    """

    created_at: datetime
    """UTC-aware time recorded by the Thread's creation action.

    It remains fixed through later discussion activity and is independent of
    placement derivation time in newer Snapshots.
    """

    state: Literal["open", "resolved", "deleted"]
    """Current lifecycle outcome at the read's inclusive activity boundary.

    Deleted Threads remain readable as history; consumers must not infer
    writability solely from the presence of the view.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Current role-attention outcome paired with `state` at this boundary.

    It is authoritative for inbox filtering; callers do not derive it from the
    latest Comment author or lifecycle state.
    """

    discussion_revision: int
    """Zero-based sequence of the latest action included in this materialization.

    It advances for every Comment or lifecycle action and is distinct from each
    Comment's own revision counter.
    """

    origin_target: ReviewOriginView
    """Immutable public coordinate that began the discussion.

    Full text-origin reads include bounded original context here. The value is
    never rewritten to match `placement` in a later Snapshot.
    """

    placement: ThreadPlacementView
    """Derived landing of `origin_target` in the selected Snapshot.

    Consumers combine it with origin coordinates and honor outdated variants;
    the placement intentionally omits repeated File and side facts.
    """

    comments: list[ReviewCommentView]
    """Current Comment views in immutable creation order.

    Deleted entries remain as tombstones, so list position and sequence are
    stable even when bodies are no longer available.
    """


class ThreadSummaryView(TypedDict):
    """Return discovery facts for one placed Thread without its excerpt.

    The lightweight agent-summary contract: the same action reduction and
    placement semantics as `ThreadDiscussionView`, but no original excerpt
    is constructed (so no captured text is read from disk) and only the
    first and latest Comments travel with their total count. The origin
    travels because a placement states no File pair, bay, or side of its own:
    every coordinate a caller needs to name captured code comes from here.
    """

    thread_id: str
    """Stable global identity of the summarized discussion.

    It addresses the same Thread across Snapshot placements and continuation
    activity; summary pagination never creates a page-local identity.
    """

    state: Literal["open", "resolved", "deleted"]
    """Folded lifecycle outcome at the page's inclusive activity pivot.

    When a caller reuses that pivot on later pages, status remains part of the
    same stable review universe.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Folded role-attention outcome at the page's inclusive pivot.

    Agent inbox filters use this authoritative value together with open state;
    it is not inferred from the two Comments carried by the summary.
    """

    origin_target: ReviewOriginView
    """Immutable origin coordinate with no captured-text excerpt key.

    It still supplies File pair, side, and bay facts needed to interpret the
    compact placement while keeping summary reads free of content loading.
    """

    placement: ThreadPlacementView
    """Derived landing of the immutable origin in the page's Snapshot.

    Consumers use its variant to distinguish navigable, outdated, absent, and
    unreadable outcomes without loading a complete discussion.
    """

    first_comment: ReviewCommentView
    """Sequence-zero Comment after applying edits or deletion through the pivot.

    It establishes the finding that began the discussion; it may be a tombstone
    if that Comment was later deleted.
    """

    latest_comment: ReviewCommentView
    """Highest creation-sequence Comment materialized at the pivot.

    With a one-Comment Thread it is the same logical Comment as `first_comment`;
    it is not necessarily the Comment most recently edited.
    """

    comment_count: int
    """Total number of Comment sequence entries at the activity pivot.

    Tombstones count because deletion does not remove an entry. The value may
    exceed the two compact Comment views carried by the summary.
    """


class ThreadUpdateView(TypedDict):
    """Return authoritative Thread state changed by one action.

    The revision, lifecycle state, and attention are current after the write.
    `comment` is the created or changed Comment, and is `None` for a bare state
    transition or Thread deletion.
    """

    thread_id: str
    """Stable identity of the discussion changed by the accepted write.

    The update is bounded to this Thread and contains no other discussion state.
    """

    snapshot_id: str
    """Exact Snapshot placement through which the Thread was addressed.

    The write changes logical discussion history, not captured code or this
    immutable placement.
    """

    state: Literal["open", "resolved", "deleted"]
    """Authoritative lifecycle outcome immediately after the accepted action.

    Callers replace their prior Thread state with this value rather than infer
    the transition locally.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Authoritative role-attention outcome after the accepted action.

    It already accounts for the selected Comment instrument or lifecycle
    transition and is the state handed back across the HTTP boundary.
    """

    discussion_revision: int
    """Thread action sequence assigned to the accepted write.

    It is the new zero-based discussion revision and advances contiguously from
    the previous authoritative history.
    """

    comment: Optional[ReviewCommentView]
    """Folded Comment created or changed by the accepted action.

    It is `None` for a bare resolve or reopen and for Thread deletion. When
    present, callers may update that Comment without refetching the discussion.
    """


def action_timestamp() -> str:
    """Return the current UTC time serialized once for an immutable action.

    Callers invoke this at the point an action is planned. They reuse the
    returned value for every row belonging to that one action rather than read
    the clock again during persistence.
    """
    return datetime.now(UTC).isoformat()


def validate_comment_body(body: str) -> None:
    """Validate that authored Comment text contains a non-whitespace character.

    The body itself is not trimmed or rewritten. Invalid input raises the typed
    review failure used by browser and agent boundaries.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when `body` contains only
      whitespace. The original body remains unchanged in the accepted case.
    """
    if body.strip() == "":
        raise ReviewError("invalid_target", "Comment body cannot be blank.")


def validate_author(
    database: RoomStore, author: ProfileAuthor
) -> UserProfileRecord:
    """Return the exact durable Profile or reject the write.

    # Parameters

    - `database`: Room persistence used for the authoritative Profile lookup.
    - `author`: Command attribution whose positive id must still exist.

    # Failures

    - Raises `ReviewError` with code `profile_not_found` when the command's
      Profile id no longer names a persisted Profile.
    """
    profile = database.review_profile(author.profile_id)
    if profile is None:
        raise ReviewError(
            "profile_not_found", f"Unknown Profile: {author.profile_id}"
        )
    return profile


@contextmanager
def room_write_lock(thread_lock: Lock, lock_path: Path) -> Iterator[None]:
    """Hold the process and file locks shared with Snapshot publication.

    # Parameters

    - `thread_lock`: Application-lifetime lock serializing worker threads.
    - `lock_path`: Advisory-lock file shared by processes using the database.

    The context releases both locks on ordinary exit and exceptions.

    # Returns

    - `Entry`: The context yields `None` only after the process lock and advisory
      file lock are both held.
    - `Exit`: It releases the advisory lock before the process lock on ordinary
      exit and exceptions.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with thread_lock, lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
