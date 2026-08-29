"""Persistent review discussions bound to exact Room Snapshots.

## Public interface

`Thread` represents one discussion at one `(snapshot_id, thread_id)` placement.
Callers obtain it through `dirdiff.room_lord.Room`, then use its Comment and
lifecycle operations. Command types carry complete write input; view types
return the current discussion, placement, attribution, and attention state.

## Purpose and boundaries

This module applies review rules to append-only actions and translates a
Thread's immutable source origin into its placement in a selected Snapshot. It
guarantees that writes use the bound Room, Snapshot, Thread, and expected
revision rather than caller-supplied persistence coordinates.

Snapshot capture and File publication belong to `dirdiff.room_lord`. SQL belongs
to `dirdiff.db.RoomStore`, and HTTP validation and serialization belong to
`dirdiff.server`. This module returns review-domain values and never selects or
mutates Snapshot contents.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Literal, NotRequired, Optional, TypedDict
from uuid import UUID

from tree_sitter import Language, Node, Parser

from dirdiff.db import (
    ReviewActionRecord,
    ReviewThreadRecord,
    ReviewThreadsRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    SnapshotRecord,
    UserProfileRecord,
)
from dirdiff.engines import DirdiffError
from dirdiff.formats import (
    BayContext,
    Composer,
    MediaSide,
    TextBay,
    media_ref,
)

__all__ = [
    "AddComment",
    "ChangeThreadState",
    "CreateThread",
    "DeleteComment",
    "DeleteThread",
    "EditComment",
    "FilePair",
    "LineRange",
    "ProfileAuthor",
    "ReplyToThread",
    "ResolveThread",
    "ReviewBatchAction",
    "ReviewBatchResult",
    "ReviewError",
    "ReviewErrorCode",
    "ReviewOriginView",
    "ReviewTarget",
    "TextTarget",
    "Thread",
    "ThreadDiscussionView",
    "ThreadPlacementView",
    "ThreadSummaryView",
    # Room-facade internals: implemented here, consumed only by room_lord's
    # Room methods; every other module goes through the Room facade.
    "apply_review_batch",
    "create_thread",
    "derive_room_threads",
    "get_thread",
    "thread_objects",
]

LOGGER = logging.getLogger(__name__)
"""Report a contained File failure that no HTTP status can carry.

Derivation refuses to fail a whole Snapshot's Threads over one File dirdiff
could not capture, so the placement it stores names the failure and the
operator still gets the captured `error` text here.
"""

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
class ReplyToThread:
    """Apply one role-directed Comment instrument in an atomic batch.

    Agent batch translation creates this value for author responses, reviewer
    returns, and inert Comments. It joins an existing discussion, one new
    attributed Comment, and the attention transition that the selected
    instrument permits.

    It cannot resolve or delete a Thread and does not weaken current-attention
    requirements.
    """

    thread_id: UUID
    """Stable identity of the existing discussion receiving the reply.

    The Thread must have a placement in the batch Snapshot. A prior action in
    the same ordered batch may have created it; otherwise persistence must.
    """

    command: AddComment
    """New Comment data applied atomically with the attention transition.

    Its author is the acting Profile for the instrument. If validation fails,
    neither this Comment nor any action in the surrounding batch is persisted.
    """

    instrument: Literal["author-response", "reviewer-return", "inert-comment"]
    """Role-directed rule governing validity and post-Comment attention.

    Author response requires author attention and hands attention to reviewer;
    reviewer return does the inverse. An inert Comment accepts any live outcome
    and leaves attention unchanged. No instrument applies to a deleted Thread.
    """


@dataclass(frozen=True)
class ResolveThread:
    """Resolve one existing Thread with a required reviewer Comment.

    Agent batch translation creates this value for `reviewer-resolve`. The
    selected Thread must be open and awaiting the reviewer; resolution and the
    required Comment are persisted atomically.

    It cannot resolve without an explanation or act on author attention.
    """

    thread_id: UUID
    """Stable identity of the discussion to close as resolved.

    At its point in the ordered batch, the Thread must be open and require
    reviewer or both-role attention. Other outcomes cause the whole batch to
    fail without writes.
    """

    command: AddComment
    """Nonblank explanation and acting Profile for the resolution action.

    The supplied Comment becomes a new ordered Comment on the Thread in the
    same transaction that changes status to resolved and attention to none.
    """


@dataclass(frozen=True)
class DeleteThread:
    """Delete one existing Thread through the exceptional reviewer instrument.

    Agent batch translation creates this value only for the exceptional
    `reviewer-delete` instrument. The state command identifies the reviewer but
    carries no Comment.

    Deletion is terminal and remains in history. This command is not ordinary
    resolution or Comment deletion.
    """

    thread_id: UUID
    """Stable identity of the live discussion to mark deleted.

    It must be placed in the batch Snapshot and not already deleted when this
    ordered action runs. Deletion retains all preceding history.
    """

    command: ChangeThreadState
    """Acting Profile and operation identity for terminal deletion.

    Both optional Comment fields must be absent because reviewer deletion
    writes no explanation Comment. The batch records the actor in its action.
    """


ReviewBatchAction = CreateThread | ReplyToThread | ResolveThread | DeleteThread
"""One role-specific write accepted by an agent review batch.

- `CreateThread` starts a discussion.
- `ReplyToThread` applies a role-directed Comment instrument.
- `ResolveThread` closes a reviewer-attention Thread with a Comment.
- `DeleteThread` applies exceptional terminal deletion.

`Room.apply_review_batch` applies values in their supplied order inside one
transaction. This union is not used by browser single-Thread operations.
"""


@dataclass(frozen=True)
class ReviewBatchResult:
    """Return the authoritative outcome of one applied batch action.

    `Room.apply_review_batch` returns one result per input action in the same
    order. `kind` echoes the instrument; state and attention are authoritative
    after it.

    Every result except `reviewer-delete` carries the created Comment id. The
    value does not contain the complete discussion or placement.
    """

    kind: Literal[
        "create-finding",
        "author-response",
        "reviewer-return",
        "reviewer-resolve",
        "inert-comment",
        "reviewer-delete",
    ]
    """Agent instrument that produced this authoritative outcome.

    It preserves input order and lets the caller interpret whether a Comment
    was created without re-inspecting the original action object.
    """

    thread_id: UUID
    """Stable discussion identity created or changed by the matching action.

    The value corresponds positionally to the input batch and is valid only
    after the complete batch transaction succeeds.
    """

    comment_id: Optional[UUID]
    """Global identity of the Comment created with the action.

    It is present for findings, replies, and reviewer resolution. Only terminal
    reviewer deletion returns `None`, because that instrument writes no Comment.
    """

    status: Literal["open", "resolved", "deleted"]
    """Persisted Thread lifecycle outcome immediately after this action.

    Later actions in the same batch may change the same Thread again, so callers
    treat this as the positional result rather than the batch's final aggregate.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Persisted role-attention outcome immediately after this action.

    The value reflects the instrument's transition after current state was
    validated; callers must not recompute it from the action kind alone.
    """

    def __post_init__(self) -> None:
        """Prove the result's Comment presence matches its instrument.

        Construction fails if an action that creates a Comment omits its id or if
        reviewer deletion claims one, preventing an internally ambiguous result.

        # Failures

        - Raises `AssertionError` when Comment presence contradicts `kind`.
        """
        assert (self.comment_id is not None) == (self.kind != "reviewer-delete")


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


@dataclass(frozen=True)
class _Segment:
    """Name one structural container enclosing a private source region.

    Locator construction records these outermost first; reattachment compares
    the sequence when choosing candidate regions.

    This value never crosses persistence except inside encoded private locator
    bytes and never becomes public syntax metadata.
    """

    node_type: str
    """Nonempty Tree-sitter kind of one eligible enclosing container.

    Reattachment compares it positionally with the other outer-to-inner
    segments; it is private syntax identity, not public language metadata.
    """

    name: Optional[str]
    """Declared container name extracted from the grammar, when available.

    `None` means the syntax node exposes no usable name. Matching preserves that
    absence rather than inventing text from another child.
    """


@dataclass(frozen=True)
class _Locator:
    """Retain only the private facts required to find an origin region.

    These are private source coordinates: they never cross the HTTP boundary and
    the store never interprets them. `_RangePlacement` contains one, and the side the
    coordinates address is the placement's own `side` rather than a field here,
    because a locator that disagreed with its placement would be unusable.

    The persisted JSON does still carry `side`, and `_locator_of()` requires it to
    agree. This type is the decoded domain value, not the storage format.
    """

    region_hash: bytes
    """SHA-256 digest of the exact origin region bytes.

    Derivation uses it to distinguish unchanged structural candidates; the
    digest is verified against immutable origin content before matching.
    """

    region_start_byte: int
    """Inclusive byte offset of the region in UTF-8 encoded origin bay text.

    It is interpreted only with `region_end_byte` and the placement's selected
    side; it is not a public source coordinate.
    """

    region_end_byte: int
    """Exclusive byte offset of the region in encoded origin bay text.

    It must exceed `region_start_byte` and remain within immutable origin bytes;
    verification treats disagreement as persisted corruption.
    """

    segments: tuple[_Segment, ...]
    """Eligible structural ancestry of the origin region, outermost first.

    Candidate relocation requires exact sequence equality. An empty tuple means
    only the complete-text root supplies a region identity.
    """


@dataclass(frozen=True)
class _RangePlacement:
    """A Thread placed on a selected line range inside one composed bay.

    This is the shape every newly created Thread takes. Its public coordinate
    comes from composition and its inclusive lines remain local to that bay.

    The private locator is deliberately not a field. Only derivation reads one,
    and decoding it costs several times what the rest of this conversion does, so
    a placement carries no coordinates and `derive_room_threads()` decodes the
    single origin locator it is about to use.
    """

    thread_id: str
    """Global logical Thread identity to which this immutable landing belongs.

    The same id may have one placement per Snapshot but never two placements in
    the same Snapshot.
    """

    snapshot_id: str
    """Immutable Snapshot whose composed content contains this landing.

    It must agree with the referenced File and is the code universe used for
    all public placement reads.
    """

    snapshot_file_id: str
    """Opaque File id within `snapshot_id` containing the landing bay.

    Reads authenticate it and prove that its nullable path pair equals the
    origin File pair before exposing the placement.
    """

    bay_key: str
    """Composition-issued bay key containing the current line range.

    A matched range must retain the immutable origin bay key; derivation does
    not relocate a region across bays.
    """

    side: Literal["left", "right"]
    """Captured File side containing this bay-local range.

    It must equal the origin side. Loss of that side produces another placement
    variant instead of changing this value.
    """

    start_line: int
    """Positive one-based first included line in the current bay-side source.

    For an unchanged match it preserves the origin's offset within the matched
    region; for changed content it is the candidate's first line.
    """

    end_line: int
    """One-based last included line in the current bay-side source.

    It is no earlier than `start_line`; changed-region placement may narrow
    the range to that one structural landing line.
    """

    outdated_reason: Optional[Literal["region_changed"]]
    """Whether the unique structural candidate changed its source bytes.

    `None` means both structure and region digest matched exactly. No other
    outdated reason is valid for a placement that still has a range.
    """


@dataclass(frozen=True)
class _BayStartPlacement:
    """A Thread placed at the start of one composed bay, its region lost.

    `region_not_found` means the bay the origin named still composes in this
    Snapshot's File but the origin region inside it matched no candidate or
    matched ambiguously, so `bay_key` is the origin's own bay.
    `bay_not_found` means the origin's bay is gone entirely, and `bay_key` is
    the File's first composed bay carrying `side`, chosen at derivation
    time, when the composed bays are already in hand, and stored so reads
    never recompute it. Derivation is the only producer; origins never take
    this shape.
    """

    thread_id: str
    """Global logical Thread identity receiving this bay-start landing.

    It relates the immutable placement to its unique origin and action history.
    """

    snapshot_id: str
    """Immutable Snapshot whose composition supplied the landing bay.

    Publication persists this choice before the Snapshot becomes visible.
    """

    snapshot_file_id: str
    """Opaque selected-Snapshot File id containing the landing bay.

    The File must retain the origin's exact nullable path pair and selected side.
    """

    bay_key: str
    """Composition-issued bay key whose start is the stored landing.

    It is the origin bay for region loss or the first replacement bay that
    contains the origin side for bay loss, as distinguished by `outdated_reason`.
    """

    side: Literal["left", "right"]
    """Origin-selected side on which the landing bay still composes.

    Derivation never changes sides to obtain a usable bay.
    """

    outdated_reason: Literal["region_not_found", "bay_not_found"]
    """Exact loss that reduced navigation to a bay start.

    `region_not_found` retains the origin bay; `bay_not_found` requires
    `bay_key` to hold the replacement chosen during derivation.
    """


@dataclass(frozen=True)
class _FileStartPlacement:
    """A Thread placed at File start, with no bay coordinate to land on.

    It has no bay and no line range, so it is never navigable; History is
    its home. A reason of None marks a retained historical File-level
    origin. These are the only origins of this shape. Every placement derived
    from such an origin also has this shape. `bay_not_found` marks a placement
    derived from a range origin whose File composes no bay carrying the side.
    """

    thread_id: str
    """Global logical Thread identity receiving this File-side landing.

    It joins the placement to its immutable origin and append-only discussion.
    """

    snapshot_id: str
    """Immutable Snapshot in which only the File-side coordinate remains.

    The placement is not reusable for another Snapshot even when paths match.
    """

    snapshot_file_id: str
    """Opaque File id validated within `snapshot_id` for discovery.

    It identifies the exact path pair but offers no bay or line coordinate.
    """

    side: Literal["left", "right"]
    """Origin-selected side retained for the historical File landing.

    The side exists on the exact File pair, but no composed bay on it is valid
    for navigation in this placement.
    """

    outdated_reason: Optional[Literal["bay_not_found"]]
    """Why only a File-side landing remains, when the placement is outdated.

    `None` is reserved for retained historical File-level origins;
    `bay_not_found` means a range origin's selected side composes no bay.
    """


@dataclass(frozen=True)
class _FileMissingPlacement:
    """A Thread with no code location, because its exact File pair is absent.

    It references no Snapshot File, and its public outdated reason is always
    `file_missing`, so neither is carried as a field.
    """

    thread_id: str
    """Global logical Thread identity whose origin pair has no current File.

    Discussion history remains addressable by this id even though code
    navigation has no selected-Snapshot target.
    """

    snapshot_id: str
    """Immutable Snapshot proven not to contain the origin File pair.

    Focused File loading checks this absence before the public placement is
    returned, so the value is not an unchecked claim.
    """


@dataclass(frozen=True)
class _FileUnreadablePlacement:
    """A Thread with no code location, because its File could not be captured.

    The exact File pair is present in this Snapshot because the backend listed
    it, but capture failed. The only bytes beneath its capture directory are
    the ones dirdiff generated to stand in for the File. Nothing here can hold
    a Thread: a bay would name composed placeholder text, and File start would
    name a side record whose digest describes that same text. It references no
    Snapshot File, and its public outdated reason is always `file_unreadable`,
    so neither is carried as a field.

    This is not `_FileMissingPlacement`. That one states the File pair is
    absent from the Snapshot, and the read boundary verifies that absence;
    this one states the opposite about the same Snapshot.
    """

    thread_id: str
    """Global logical Thread identity whose matching File is unreadable.

    The discussion remains intact while review withholds generated capture-error
    content from placement and excerpts.
    """

    snapshot_id: str
    """Immutable Snapshot in which the origin pair exists but capture failed.

    This distinguishes the placement from true File absence without storing a
    reference to placeholder bytes.
    """


_Placement = (
    _RangePlacement
    | _BayStartPlacement
    | _FileStartPlacement
    | _FileMissingPlacement
    | _FileUnreadablePlacement
)
"""One Thread's immutable location in one Snapshot, in the shape review needs.

- `_RangePlacement` locates a usable line range.
- `_BayStartPlacement` locates a bay whose original region was lost.
- `_FileStartPlacement` retains a File-side landing without a bay.
- `_FileMissingPlacement` states that the exact File pair is absent.
- `_FileUnreadablePlacement` states that capture could not retain the File.

`RoomStore` returns a flat record whose optional fields can describe all five
variants without proving which one applies. `_placement_of()` validates that
record into exactly one variant at the read boundary; `_record_of()` converts it
back. The union omits query-local origin labels and private reattachment
coordinates.
"""


@dataclass(frozen=True)
class _SourceRegion:
    """Describe one candidate region during private Thread reattachment.

    Structural scanning creates these values from one composed bay side.
    Matching compares source bytes, line bounds, and enclosing segments against
    the origin locator.

    It is temporary derivation state, not a public region or persisted record.
    """

    source: bytes
    """Complete UTF-8 encoded bay-side source containing this candidate.

    The byte offsets slice this shared value for digest comparison; it is
    operation-local and never becomes persisted review content.
    """

    start_byte: int
    """Inclusive UTF-8 byte offset at which the candidate region begins.

    It is interpreted against `source` and participates in choosing the
    smallest containing origin region.
    """

    end_byte: int
    """Exclusive UTF-8 byte offset at which the candidate region ends.

    It is strictly greater than `start_byte`; their slice is the content hashed
    for immutable origin matching.
    """

    start_line: int
    """Positive one-based first line covered by the syntax candidate.

    Selected ranges must begin no earlier than this coordinate for the region
    to contain them.
    """

    end_line: int
    """One-based last line covered by the syntax candidate, included.

    The coordinate accounts for parsers whose end point begins a following line
    and is never earlier than `start_line`.
    """

    segments: tuple[_Segment, ...]
    """Eligible syntax ancestry used as the candidate's structural identity.

    Reattachment first requires exact equality with the persisted origin
    sequence before considering the candidate's content digest.
    """


@dataclass(frozen=True)
class _ComposedBay:
    """One composed bay's identity and the text review reads on each side.

    This is what review needs from composition and all it needs: the public
    bay key a target may name, its kind, the text each side holds, and the path
    hint that selects a parser for structural matching. It carries nothing an
    engine produced, because `Composer.bays()` has no renderer in reach.

    An image bay reaches review through the same shape. Its text is the one
    pseudo-line it exposes, reconstructed from the picture's own facts, so a
    target against it runs through validation, placement, and excerpt reads
    without a second variant. `kind` is retained because one thing does still
    depend on it: which line ranges are valid.
    """

    bay_key: str
    """Public File-local identity emitted by the composer for this bay.

    Targets and placements retain this exact value; review does not derive a
    parallel key from paths or content.
    """

    kind: Literal["text", "image"]
    """Composition kind controlling the review coordinate contract.

    Text bays accept ranges within decoded lines. Image bays expose exactly one
    content-derived pseudo-line and therefore accept only line 1 through 1.
    """

    left_text: Optional[str]
    """Decoded left source or content-derived media pseudo-line when present.

    `None` means the composed bay has no left side. Review never substitutes
    right-side content for that absence.
    """

    right_text: Optional[str]
    """Decoded right source or content-derived media pseudo-line when present.

    `None` means the composed bay has no right side. The text is used for target
    validation, excerpts, and private relocation only.
    """

    left_hint: Optional[str]
    """Optional left-side path hint supplied by composition for parser choice.

    Absence delegates to the captured repository path. Media uses a neutral hint
    so source-language parsers do not interpret its pseudo-line.
    """

    right_hint: Optional[str]
    """Optional right-side path hint supplied by composition for parser choice.

    It follows the same absence and media rules as `left_hint` and carries no
    public placement meaning.
    """

    def text_for(self, side: Literal["left", "right"]) -> Optional[str]:
        """Return the review source for the exact requested bay side.

        `None` reports true side absence. The method performs no substitution,
        decoding, or validation beyond selecting the matching stored field.

        # Returns

        - `str`: The exact stored review source for the selected side.
        - `None`: The composed bay has no content on that side. The caller must
          preserve the absence rather than borrow the other side's text.
        """
        return self.left_text if side == "left" else self.right_text

    def hint_for(self, side: Literal["left", "right"]) -> Optional[str]:
        """Return the structural parser hint associated with one exact side.

        `None` tells the caller to use the captured repository path; it does not
        mean that the side itself is absent.

        # Returns

        - `str`: The composition-supplied parser hint for the selected side.
        - `None`: Composition supplied no override. The caller must use the
          selected side's captured repository path for parser choice.
        """
        return self.left_hint if side == "left" else self.right_hint


@dataclass
class _ReviewReadCache:
    """Share composed bay identity across one review read.

    Composing a File's bays decodes both of its sides, so one read covering
    several Threads against the same File pays that cost once. Review read
    functions create and discard the cache within one operation.

    It does not persist composed content, cross Snapshot boundaries, or become
    an authoritative copy of File or Thread state.
    """

    bays: dict[str, dict[str, _ComposedBay]] = field(default_factory=dict)
    """Read-local composition results indexed by File id and public bay key.

    Entries are added on first composition and reused only within the enclosing
    review operation. The cache never crosses a Snapshot read or persists bytes.
    """


_ELIGIBLE_NODE_TYPES = frozenset(
    {
        "array",
        "array_expression",
        "arrow_function",
        "block_mapping",
        "block_sequence",
        "class_declaration",
        "class_definition",
        "decorated_definition",
        "dictionary",
        "enum_item",
        "function_declaration",
        "function_definition",
        "function_expression",
        "function_item",
        "generator_function_declaration",
        "impl_item",
        "interface_declaration",
        "keyframes_statement",
        "list",
        "media_statement",
        "method_definition",
        "object",
        "pair",
        "rule_set",
        "set",
        "struct_item",
        "supports_statement",
        "table",
        "table_array_element",
        "trait_item",
        "tuple",
    }
)
"""Tree-sitter containers that may identify a review origin structurally.

Region extraction records only these nodes as outer-to-inner segments. Leaf
syntax and punctuation cannot become identity, which keeps relocation tied to
declared source structures and the whole-text root.
"""

_LANGUAGES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    ((".py", ".pyi", ".pyw"), "tree_sitter_python", "language"),
    ((".js", ".jsx", ".mjs", ".cjs"), "tree_sitter_javascript", "language"),
    ((".ts", ".mts", ".cts"), "tree_sitter_typescript", "language_typescript"),
    ((".tsx",), "tree_sitter_typescript", "language_tsx"),
    ((".rs",), "tree_sitter_rust", "language"),
    ((".css",), "tree_sitter_css", "language"),
    ((".json",), "tree_sitter_json", "language"),
    ((".toml",), "tree_sitter_toml", "language"),
    ((".yaml", ".yml"), "tree_sitter_yaml", "language"),
    ((".md", ".markdown"), "tree_sitter_markdown", "language"),
)
"""Ordered path-suffix map for optional Tree-sitter parser selection.

Each entry names accepted lowercase suffixes, an importable language module,
and its factory. Paths matching no entry use the complete source as one region;
module loading happens only after a suffix matches.
"""


def _now() -> str:
    """Return the current UTC time serialized once for an immutable action.

    Callers invoke this at the point an action is planned. They reuse the
    returned value for every row belonging to that one action rather than read
    the clock again during persistence.
    """
    return datetime.now(UTC).isoformat()


def _nonblank(body: str) -> None:
    """Validate that authored Comment text contains a non-whitespace character.

    The body itself is not trimmed or rewritten. Invalid input raises the typed
    review failure used by browser and agent boundaries.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when `body` contains only
      whitespace. The original body remains unchanged in the accepted case.
    """
    if body.strip() == "":
        raise ReviewError("invalid_target", "Comment body cannot be blank.")


def _validate_author(
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
def _room_write_lock(thread_lock: Lock, lock_path: Path) -> Iterator[None]:
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


def _file_pair(file: SnapshotFileRecord) -> FilePair:
    """Convert one captured File record to its exact public nullable path pair.

    Side absence is preserved and the `FilePair` boundary rechecks canonical
    path invariants. Physical capture paths and File ids do not escape.
    """
    return FilePair(
        file.left.repository_path if file.left is not None else None,
        file.right.repository_path if file.right is not None else None,
    )


def _file_indexes(
    snapshot: SnapshotRecord,
) -> tuple[dict[str, SnapshotFileRecord], dict[FilePair, SnapshotFileRecord]]:
    """Index a complete loaded Snapshot by File id and exact public pair.

    The two indexes cover every File exactly once. Duplicate ids or path pairs
    are persisted corruption and fail here before derivation chooses a target.

    # Returns

    - `First`: Every Snapshot File keyed by its durable File id.
    - `Second`: The same records keyed by exact nullable old/new repository paths.
    """
    by_id = {file.id: file for file in snapshot.files}
    by_pair = {_file_pair(file): file for file in snapshot.files}
    assert len(by_id) == len(by_pair) == len(snapshot.files), (
        "Snapshot contains duplicate review File identities"
    )
    return by_id, by_pair


def _path_hint(file: SnapshotFileRecord, side: Literal["left", "right"]) -> str:
    """Return the selected side path used only to select a parser.

    # Parameters

    - `file`: Captured pair containing the selected side record.
    - `side`: Present side whose repository suffix guides parsing.
    """
    record = file.left if side == "left" else file.right
    assert record is not None, "selected review side must be present"
    return record.repository_path


@lru_cache(maxsize=3)
def _regions_for_source(path: str, text: str) -> tuple[_SourceRegion, ...]:
    """Return candidate structural regions, including the complete text root.

    The process retains only the three most recent exact path/text results.
    Thread derivation groups equal target sources so repeated placements reuse
    their current File parse without retaining historical source collections.

    # Parameters

    - `path`: Repository or bay hint used only for language selection.
    - `text`: Complete decoded bay source to partition losslessly.

    # Returns

    - `Root`: The complete text as the first and always-present candidate region.
    - `Descendants`: Eligible syntax regions in source traversal order;
      unsupported paths contribute no descendants.
    """

    def parser_for_path() -> Optional[Parser]:
        """Load the optional Tree-sitter parser selected by the source suffix.

        It is called once per uncached path/text input. An unsupported suffix
        returns `None`, which makes the complete text the sole candidate region.

        # Returns

        - `Parser`: A Tree-sitter parser for the first configured suffix match.
        - `None`: No configured language claims the path. The caller must use
          the complete text as the sole candidate region.
        """
        lower = path.lower()
        for suffixes, module_name, attribute in _LANGUAGES:
            if not lower.endswith(suffixes):
                continue
            module = importlib.import_module(module_name)
            factory = getattr(module, attribute)
            return Parser(Language(factory()))
        return None

    def node_name(node: Node, source: bytes) -> Optional[str]:
        """Return a stable declared name when one syntax node exposes it.

        # Parameters

        - `node`: Tree-sitter container whose language may define a name field.
        - `source`: UTF-8 bytes that the node's byte coordinates index.

        # Returns

        - `str`: The nonblank source text of the node's `name` field.
        - `None`: The node has no name field or its name is blank. The caller
          must retain the structural segment without a declared name.
        """
        name = node.child_by_field_name("name")
        if name is None:
            return None
        value = source[name.start_byte : name.end_byte].decode().strip()
        return value if value != "" else None

    def eligible_ancestors(node: Node, source: bytes) -> tuple[_Segment, ...]:
        """Return this node's eligible ancestry in outer-to-inner order.

        # Parameters

        - `node`: Syntax node whose enclosing structural identity is recorded.
        - `source`: UTF-8 bytes used to recover optional declared names.

        # Returns

        - `Members`: Each eligible ancestor carries its syntax kind and optional
          declared name.
        - `Order`: Ancestors run from the outermost eligible node through the
          supplied node; an empty tuple means no eligible ancestor exists.
        """
        ancestors: list[Node] = []
        current: Optional[Node] = node
        while current is not None:
            if current.type in _ELIGIBLE_NODE_TYPES:
                ancestors.append(current)
            current = current.parent
        ancestors.reverse()
        return tuple(
            _Segment(item.type, node_name(item, source)) for item in ancestors
        )

    def line_span(node: Node) -> tuple[int, int]:
        """Convert Tree-sitter points to a positive inclusive line span.

        A node ending at column zero does not include that following line; other
        end points do. The returned end is never earlier than the start.

        # Returns

        - `First`: The node's positive one-based inclusive starting line.
        - `Second`: Its positive inclusive final line, with a column-zero end
          kept on the preceding line and never earlier than the start.
        """
        start = node.start_point.row + 1
        end = node.end_point.row + (1 if node.end_point.column > 0 else 0)
        return start, max(start, end)

    source = text.encode("utf-8")
    parser = parser_for_path()
    if parser is None:
        line_count = max(1, len(text.splitlines()))
        return (_SourceRegion(source, 0, len(source), 1, line_count, ()),)
    root = parser.parse(source).root_node
    regions = [
        _SourceRegion(
            source,
            root.start_byte,
            root.end_byte,
            *line_span(root),
            (),
        )
    ]
    stack = list(reversed(root.children))
    while stack != []:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type not in _ELIGIBLE_NODE_TYPES:
            continue
        start_line, end_line = line_span(node)
        regions.append(
            _SourceRegion(
                source,
                node.start_byte,
                node.end_byte,
                start_line,
                end_line,
                eligible_ancestors(node, source),
            )
        )
    return tuple(regions)


def _composed_bays(
    file: SnapshotFileRecord,
    cache: _ReviewReadCache,
) -> dict[str, _ComposedBay]:
    """Return every bay this File composes into, indexed by public key.

    This is the review bridge. The bay keys a target may name are exactly the
    keys composition produces, never an independent approximation of what the
    renderer shows, so validation and rendering cannot disagree about which
    bays exist. `Composer.bays()` takes a `BayContext`, which carries no
    renderer, so reconstructing an origin still involves no diff engine.

    Composition is total. Every pair of byte sides reaches the blob terminal,
    so the one failure this can report is its own: a File whose capture failed
    retains dirdiff's placeholder text rather than the File's bytes, and reading
    it as review content would quote a fabrication back to the reviewer. That
    raises `ReviewError("invalid_target", ...)` carrying the persisted reason.
    A caller that must survive such a File checks `SnapshotFileRecord.error`
    before calling; there is nothing else here to catch.

    # Parameters

    - `file`: Captured File whose exact sides composition reads.
    - `cache`: Read-scoped store reused by Threads addressing the same File.

    # Returns

    - `Keys`: Every public bay key produced by composition for this exact File.
    - `Values`: Text bays retain decoded sides and parser hints; image bays
      expose their content-derived pseudo-lines.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when capture recorded a
      failure for the File. Placeholder error bytes are never composed as
      reviewable content.
    - Raises `AssertionError` when retained side bytes disagree with their
      persisted SHA-256 digest. Reading a missing or inaccessible captured side
      propagates its filesystem exception.
    """
    cached = cache.bays.get(file.id)
    if cached is not None:
        return cached
    pair = _file_pair(file)

    def side_bytes(side: Literal["left", "right"]) -> Optional[bytes]:
        """Read and authenticate one requested captured File side.

        The helper is invoked once per side when this File is first composed.
        It returns `None` only for true side absence, raises the persisted
        capture failure for unreadable Files, and asserts digest equality.

        # Returns

        - `bytes`: The authenticated captured bytes for the requested side.
        - `None`: The Snapshot File has no record for that side. The caller must
          preserve true side absence during composition.

        # Failures

        - Raises `ReviewError` when capture recorded a failure for the File.
        - Raises `AssertionError` when retained bytes no longer match their
          persisted digest. File reads propagate their I/O failures.
        """
        record = file.left if side == "left" else file.right
        if record is None:
            return None
        if file.error is not None:
            raise ReviewError("invalid_target", file.error)
        content = (Path(file.path) / side).read_bytes()
        assert hashlib.sha256(content).digest() == record.content_hash, (
            f"Snapshot File content hash mismatch: {file.path}/{side}"
        )
        return content

    def pseudo_line(side: Optional[MediaSide]) -> Optional[str]:
        """Render the one line an image bay exposes to review, or `None`.

        An image bay has no lines, and a target against one is defined to name
        the single line `1..1`, so review needs exactly one line of text to
        place that target in and to quote back as its excerpt. It is built
        from the media side's own media type, size, and digest. This makes it do
        real work rather than stand in for missing text. When the content
        changes, the line changes, so the region hash
        retained at creation stops matching and the Thread is reported
        outdated, which is precisely what a comment on a replaced image
        deserves.

        `None` is a side the File was not captured on, matching how a text bay
        reports the same thing.

        # Returns

        - `str`: One line containing media type, byte size, and SHA-256 digest
          for the captured image side.
        - `None`: The image bay has no side here. The caller must preserve that
          absence during target validation.
        """
        if side is None:
            return None
        ref = media_ref(side)
        return (
            f"{ref['media_type']}, {ref['byte_size']} bytes, "
            f"sha256 {ref['digest']}"
        )

    composed = Composer().bays(
        side_bytes("left"),
        side_bytes("right"),
        BayContext(
            left_path=pair.left_path,
            right_path=pair.right_path,
            left_label="left",
            right_label="right",
        ),
    )
    bays = {
        bay.bay_key: (
            _ComposedBay(
                bay_key=bay.bay_key,
                kind="text",
                left_text=bay.left.text,
                right_text=bay.right.text,
                left_hint=bay.left.path_hint,
                right_hint=bay.right.path_hint,
            )
            if isinstance(bay, TextBay)
            else _ComposedBay(
                bay_key=bay.bay_key,
                kind="image",
                left_text=pseudo_line(bay.left),
                right_text=pseudo_line(bay.right),
                # A pseudo-line is not source in any language, so it must
                # not select a parser. `media` names no suffix any parser
                # claims, which keeps structural matching over the whole
                # line and off whatever the File's real extension implies.
                left_hint="media",
                right_hint="media",
            )
        )
        for bay in composed
    }
    cache.bays[file.id] = bays
    return bays


def _selected_bay(
    file: SnapshotFileRecord,
    *,
    side: Literal["left", "right"],
    bay_key: str,
    cache: _ReviewReadCache,
) -> _ComposedBay:
    """Return one named bay, requiring it to exist and hold the side.

    # Parameters

    - `file`: Captured File whose composition defines valid bay identity.
    - `side`: Required present side of the bay.
    - `bay_key`: Exact public key produced by composition.
    - `cache`: Read-scoped composed-bay cache.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when composition produced
      no `bay_key` or when the named bay has no content on `side`.
    - Propagates capture, digest, and filesystem failures from `_composed_bays`.
    """
    bay = _composed_bays(file, cache).get(bay_key)
    if bay is None:
        raise ReviewError("invalid_target", "Unknown rendered bay.")
    if bay.text_for(side) is None:
        raise ReviewError(
            "invalid_target", "Bay is absent on the selected side."
        )
    return bay


def _locator_of(payload: bytes, *, side: Literal["left", "right"]) -> _Locator:
    """Decode one persisted locator, proving the payload is well formed.

    `side` is the placement's selected side. The persisted JSON repeats it and must
    agree, because a locator addressing the other side of its File would search
    the wrong source. The field set is exact: an unknown or missing key means the
    payload was written by a revision this code does not understand, and reading
    it as if it were current would silently mislocate the Thread.

    This proves the payload alone. Whether the coordinates still describe the
    origin's captured bytes is `_verify_locator()`'s question, because only that
    caller has the text.

    # Parameters

    - `payload`: Persisted private JSON bytes with the exact supported shape.
    - `side`: Origin-placement side the repeated persisted value must match.

    # Failures

    - Raises `AssertionError` when the payload is not UTF-8 JSON, is not an
      object with the exact supported fields, repeats another side, contains an
      invalid digest or byte range, or has malformed structural segments. These
      failures identify incompatible or corrupt persisted coordinates.
    """
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("private locator is not valid JSON") from exc
    assert isinstance(value, dict), "private locator must be an object"
    assert set(value) == {
        "side",
        "region_hash",
        "region_start_byte",
        "region_end_byte",
        "segments",
    }, "invalid private locator fields"
    assert value["side"] == side, "private locator side disagrees with origin"
    hash_value = value["region_hash"]
    assert isinstance(hash_value, str) and len(hash_value) == 64
    assert all(character in "0123456789abcdef" for character in hash_value)
    start_value = value["region_start_byte"]
    end_value = value["region_end_byte"]
    assert type(start_value) is int and type(end_value) is int
    assert 0 <= start_value < end_value
    segment_values = value["segments"]
    assert isinstance(segment_values, list), "private segments must be a list"
    segments: list[_Segment] = []
    for segment in segment_values:
        assert isinstance(segment, dict) and set(segment) == {
            "node_type",
            "name",
        }, "invalid private segment"
        assert (
            isinstance(segment["node_type"], str)
            and segment["node_type"].strip() != ""
        )
        assert segment["name"] is None or (
            isinstance(segment["name"], str) and segment["name"].strip() != ""
        )
        segments.append(_Segment(segment["node_type"], segment["name"]))
    locator = _Locator(
        region_hash=bytes.fromhex(hash_value),
        region_start_byte=start_value,
        region_end_byte=end_value,
        segments=tuple(segments),
    )
    assert len(locator.region_hash) == 32
    return locator


def _locator_bytes(
    locator: _Locator, *, side: Literal["left", "right"]
) -> bytes:
    """Encode one locator in the exact field set `_locator_of()` requires.

    `side` comes from the placement, which is where the decoded type keeps
    it. The two functions are a pair: a field added here without being accepted
    there fails every later read of that Thread.

    # Parameters

    - `locator`: Valid private structural coordinate for the origin region.
    - `side`: Selected placement side written into the storage payload.
    """
    return json.dumps(
        {
            "side": side,
            "region_hash": locator.region_hash.hex(),
            "region_start_byte": locator.region_start_byte,
            "region_end_byte": locator.region_end_byte,
            "segments": [
                {"node_type": segment.node_type, "name": segment.name}
                for segment in locator.segments
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _verify_locator(
    placement: _RangePlacement, locator: _Locator, *, text: str
) -> None:
    """Prove one origin's locator still identifies its immutable source.

    `text` is the origin side's decoded captured text. The Snapshot File a
    locator addresses is immutable, so a disagreement here is corruption rather
    than drift, and the caller has no valid result to fall back to.

    # Parameters

    - `placement`: Origin range whose selected lines must lie in the region.
    - `locator`: Decoded byte span and digest to verify.
    - `text`: Exact decoded origin-bay text retained by the Snapshot.
    """
    source = text.encode()
    assert locator.region_end_byte <= len(source), (
        "private locator byte span exceeds its immutable origin source"
    )
    origin_slice = source[locator.region_start_byte : locator.region_end_byte]
    assert hashlib.sha256(origin_slice).digest() == locator.region_hash, (
        "private locator hash disagrees with its immutable origin region"
    )
    origin_region_start = source[: locator.region_start_byte].count(b"\n") + 1
    origin_region_prefix = source[: locator.region_end_byte]
    origin_region_end = origin_region_prefix.count(b"\n") + (
        0 if origin_region_prefix.endswith(b"\n") else 1
    )
    assert origin_region_start <= placement.start_line <= placement.end_line
    assert placement.end_line <= origin_region_end


def _placement_of(record: ReviewThreadRecord) -> _Placement:
    """Prove one stored placement's shape, once, at the read boundary.

    `RoomStore` returns every placement as the same flat row because the schema
    is one table. `target_kind` distinguishes the located shapes. The outdated
    reason distinguishes an absent File from one that is present and
    unreadable. `ReviewThreadRecord` has
    already refused any row whose
    fields disagree with its tag, so the assertions here re-state that shape to
    narrow the record's optional fields into this module's variants; a
    violation is a corrupt database rather than an input this code can place.
    """
    match record.target_kind:
        case "range":
            assert record.snapshot_file_id is not None
            assert record.bay_key is not None and record.bay_key != ""
            assert record.side is not None
            assert record.start_line is not None
            assert record.end_line is not None
            assert 1 <= record.start_line <= record.end_line
            reason = record.outdated_reason
            assert reason is None or reason == "region_changed"
            return _RangePlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
                snapshot_file_id=record.snapshot_file_id,
                bay_key=record.bay_key,
                side=record.side,
                start_line=record.start_line,
                end_line=record.end_line,
                outdated_reason=reason,
            )
        case "bay-start":
            assert record.snapshot_file_id is not None
            assert record.bay_key is not None and record.bay_key != ""
            assert record.side is not None
            assert record.start_line is None and record.end_line is None
            assert record.private_locator is None
            bay_reason = record.outdated_reason
            assert (
                bay_reason == "region_not_found"
                or bay_reason == "bay_not_found"
            )
            return _BayStartPlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
                snapshot_file_id=record.snapshot_file_id,
                bay_key=record.bay_key,
                side=record.side,
                outdated_reason=bay_reason,
            )
        case "file-start":
            assert record.snapshot_file_id is not None
            assert record.bay_key is None
            assert record.side is not None
            assert record.start_line is None and record.end_line is None
            assert record.private_locator is None
            start_reason = record.outdated_reason
            assert start_reason is None or start_reason == "bay_not_found"
            return _FileStartPlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
                snapshot_file_id=record.snapshot_file_id,
                side=record.side,
                outdated_reason=start_reason,
            )
        case None:
            assert record.snapshot_file_id is None
            assert record.bay_key is None and record.side is None
            assert record.start_line is None and record.end_line is None
            assert record.private_locator is None
            # Both unlocated shapes persist as the same untagged row. The
            # reason is what separates a File that is gone from one that is
            # here and unreadable, so it selects the variant.
            if record.outdated_reason == "file_unreadable":
                return _FileUnreadablePlacement(
                    thread_id=record.thread_id,
                    snapshot_id=record.snapshot_id,
                )
            assert record.outdated_reason == "file_missing"
            return _FileMissingPlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
            )


def _record_of(
    placement: _Placement,
    *,
    is_origin: bool,
    locator: Optional[_Locator],
) -> ReviewThreadRecord:
    """Convert one placement back into the flat row the store persists.

    `is_origin` and `locator` are the caller's facts, not the placement's.
    `is_origin` is a per-query label rather than a stored column, and the store
    uses it only to reject an existing origin republished into a new Snapshot.
    `locator` belongs only to a range origin; every other row stores none.

    # Parameters

    - `placement`: Proven domain variant to flatten for persistence.
    - `is_origin`: Whether this Snapshot pair is the discussion's unique origin.
    - `locator`: Private coordinates only for a range origin, otherwise `None`.
    """
    assert locator is None or isinstance(placement, _RangePlacement), (
        "only a range placement stores private coordinates"
    )
    match placement:
        case _RangePlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=placement.snapshot_file_id,
                is_origin=is_origin,
                target_kind="range",
                bay_key=placement.bay_key,
                side=placement.side,
                start_line=placement.start_line,
                end_line=placement.end_line,
                outdated_reason=placement.outdated_reason,
                private_locator=(
                    None
                    if locator is None
                    else _locator_bytes(locator, side=placement.side)
                ),
            )
        case _BayStartPlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=placement.snapshot_file_id,
                is_origin=is_origin,
                target_kind="bay-start",
                bay_key=placement.bay_key,
                side=placement.side,
                start_line=None,
                end_line=None,
                outdated_reason=placement.outdated_reason,
                private_locator=None,
            )
        case _FileStartPlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=placement.snapshot_file_id,
                is_origin=is_origin,
                target_kind="file-start",
                bay_key=None,
                side=placement.side,
                start_line=None,
                end_line=None,
                outdated_reason=placement.outdated_reason,
                private_locator=None,
            )
        case _FileMissingPlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=None,
                is_origin=is_origin,
                target_kind=None,
                bay_key=None,
                side=None,
                start_line=None,
                end_line=None,
                outdated_reason="file_missing",
                private_locator=None,
            )
        case _FileUnreadablePlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=None,
                is_origin=is_origin,
                target_kind=None,
                bay_key=None,
                side=None,
                start_line=None,
                end_line=None,
                outdated_reason="file_unreadable",
                private_locator=None,
            )


def _derive_record(
    *,
    origin: _RangePlacement | _FileStartPlacement,
    locator: Optional[_Locator],
    origin_file: SnapshotFileRecord,
    target_snapshot_id: str,
    target_files_by_pair: dict[FilePair, SnapshotFileRecord],
    cache: _ReviewReadCache,
) -> _Placement:
    """Derive one immutable Thread placement directly from its unique origin.

    The caller has already resolved the origin's Snapshot File and decoded the
    origin's private coordinates. `locator` is required for a range origin and is
    `None` for a File-start one, which retains none. An origin is never
    `_FileMissingPlacement`, because a discussion is created against a File that
    exists.

    # Parameters

    - `origin`: Unique immutable range or retained historical File origin.
    - `locator`: Verified private coordinates required for a range origin.
    - `origin_file`: Captured File the origin row references.
    - `target_snapshot_id`: New code universe receiving the derived placement.
    - `target_files_by_pair`: Target Files indexed by the exact origin pair.
    - `cache`: Operation-scoped composed-bay cache shared across derivations.
    """

    def file_start(side: Literal["left", "right"]) -> _FileStartPlacement:
        """Build the File-side landing used when the selected side has no bay.

        It is invoked only after a matching target File has been found and
        composition supplied no bay containing that side. The result records
        `bay_not_found` and retains the origin side without inventing a key.
        """
        assert target_file is not None
        return _FileStartPlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            side=side,
            outdated_reason="bay_not_found",
        )

    target_file = target_files_by_pair.get(_file_pair(origin_file))
    if target_file is None:
        return _FileMissingPlacement(
            thread_id=origin.thread_id, snapshot_id=target_snapshot_id
        )
    if isinstance(origin, _FileStartPlacement):
        assert locator is None, "a File-start origin retains no coordinates"
        assert origin.outdated_reason is None
        selected_side = (
            target_file.left if origin.side == "left" else target_file.right
        )
        assert selected_side is not None, (
            "historical File-start side disappeared from an exact File pair"
        )
        return _FileStartPlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            side=origin.side,
            outdated_reason=None,
        )
    target_bay_key = origin.bay_key
    # The origin's own bytes produced this key when the Thread was created, so
    # composing them again yields it. A lookup that fails here means the Room
    # disagrees with itself, and raising is the honest report of that.
    origin_side = origin.side
    origin_bay_text = _selected_bay(
        origin_file,
        side=origin_side,
        bay_key=target_bay_key,
        cache=cache,
    ).text_for(origin_side)
    assert origin_bay_text is not None
    origin_text = origin_bay_text
    assert locator is not None, "a range origin retains its coordinates"
    _verify_locator(origin, locator, text=origin_text)
    # A File whose capture failed retains dirdiff's placeholder text, not the
    # File's own bytes, so every coordinate it could offer describes something
    # dirdiff wrote. The Thread therefore lands nowhere, which is the damage
    # boundary: raising instead would fail the whole Snapshot's Threads over
    # one unreadable File and hide every other discussion in the review. The
    # `error` text cannot travel in a placement, so it is logged here.
    if target_file.error is not None:
        target_pair = _file_pair(target_file)
        LOGGER.error(
            "Thread %s has no code location: %s could not be captured in "
            "Snapshot %s: %s",
            origin.thread_id,
            target_pair.right_path or target_pair.left_path,
            target_snapshot_id,
            target_file.error,
        )
        return _FileUnreadablePlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
        )
    # Placement carries the origin bay key unchanged into the target Snapshot.
    # A File that offers no bay coordinate at all lands at File start instead.
    target_bays = _composed_bays(target_file, cache)
    target_bay = target_bays.get(target_bay_key)
    target_text = (
        target_bay.text_for(origin_side) if target_bay is not None else None
    )
    if target_bay is None or target_text is None:
        # The origin's bay (or its side) is gone, but the composed bays are
        # already in hand. The first bay carrying the side is chosen and stored
        # here rather than recomputed by reads.
        for bay in target_bays.values():
            if bay.text_for(origin_side) is not None:
                return _BayStartPlacement(
                    thread_id=origin.thread_id,
                    snapshot_id=target_snapshot_id,
                    snapshot_file_id=target_file.id,
                    bay_key=bay.bay_key,
                    side=origin_side,
                    outdated_reason="bay_not_found",
                )
        return file_start(origin_side)
    text = target_text
    path = target_bay.hint_for(origin_side) or _path_hint(
        target_file, origin_side
    )
    candidates = [
        region
        for region in _regions_for_source(path, text)
        if region.segments == locator.segments
    ]
    matching = [
        region
        for region in candidates
        if hashlib.sha256(
            region.source[region.start_byte : region.end_byte]
        ).digest()
        == locator.region_hash
    ]
    if len(matching) == 1:
        candidate = matching[0]
        origin_region_start = (
            origin_text.encode()[: locator.region_start_byte].count(b"\n") + 1
        )
        start_offset = origin.start_line - origin_region_start
        end_offset = origin.end_line - origin_region_start
        return _RangePlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            bay_key=target_bay_key,
            side=origin_side,
            start_line=candidate.start_line + start_offset,
            end_line=candidate.start_line + end_offset,
            outdated_reason=None,
        )
    if len(candidates) == 1 and len(matching) == 0:
        candidate = candidates[0]
        return _RangePlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            bay_key=target_bay_key,
            side=origin_side,
            start_line=candidate.start_line,
            end_line=candidate.start_line,
            outdated_reason="region_changed",
        )
    # The bay survives while the region inside it matched nothing or matched
    # ambiguously, so only the bay coordinate is retained.
    return _BayStartPlacement(
        thread_id=origin.thread_id,
        snapshot_id=target_snapshot_id,
        snapshot_file_id=target_file.id,
        bay_key=target_bay_key,
        side=origin_side,
        outdated_reason="region_not_found",
    )


def _origin_target_dict(
    origin: _RangePlacement | _FileStartPlacement,
    file: SnapshotFileRecord,
) -> ReviewOriginView:
    """Reconstruct the immutable public creation target from retained facts.

    # Parameters

    - `origin`: Proven range or historical File-start origin placement.
    - `file`: Exact captured origin File providing its public path pair.
    """
    origin_pair = _file_pair(file)
    pair: ReviewFilePairView = {
        "left_path": origin_pair.left_path,
        "right_path": origin_pair.right_path,
    }
    if isinstance(origin, _FileStartPlacement):
        assert origin.outdated_reason is None
        return {"kind": "file-start", "file": pair, "side": origin.side}
    return {
        "kind": "text",
        "file": pair,
        "bay": {"bay_key": origin.bay_key},
        "side": origin.side,
        "range": {
            "start_line": origin.start_line,
            "end_line": origin.end_line,
        },
    }


@dataclass
class _CommentState:
    """Reduce immutable Comment actions into one current Comment view.

    Discussion materialization creates this private mutable value on Comment creation,
    then applies edits or deletion in sequence before exporting
    `ReviewCommentView`.

    It exists only during one read and is not persisted or shared between
    Threads.
    """

    comment_id: str
    """Stable global identity of the Comment being materialized.

    It keys this mutable state while every edit and deletion retains the same
    sequence entry.
    """

    sequence: int
    """Zero-based Comment creation position assigned during reduction.

    Later mutations do not reorder this state, so exported views remain in
    durable discussion order.
    """

    profile_id: int
    """Durable identity of the Profile that originally authored the Comment.

    Action reduction uses it for edit authorization invariants and resolves the current
    display name only when exporting the public view.
    """

    revision: int
    """Current zero-based Comment version accumulated by action reduction.

    It starts at creation and increments once for every accepted edit or
    tombstone action that targets this Comment.
    """

    body: Optional[str]
    """Latest Comment text, or `None` after a tombstone action.

    Before deletion every assigned value is nonblank because persisted action
    validation treats a blank body as corruption.
    """

    deleted: bool
    """Whether the action sequence has terminally tombstoned this Comment.

    Once true, later edit or deletion actions violate persisted history rather
    than reviving or deleting the Comment again.
    """

    created_at: str
    """Immutable serialized UTC time from the Comment creation action.

    Export converts it to `datetime`; later actions never replace this value.
    """

    updated_at: str
    """Serialized UTC time of the latest applied edit or deletion.

    It begins equal to `created_at` and changes only when this Comment is the
    target of a mutating action.
    """


def fold_actions(
    actions: tuple[ReviewActionRecord, ...],
    profiles: dict[int, UserProfileRecord],
) -> tuple[
    Literal["open", "resolved", "deleted"],
    Literal["author", "reviewer", "both", "none"],
    list[ReviewCommentView],
]:
    """Reduce ordered authored actions into current discussion state.

    # Parameters

    - `actions`: Complete contiguous sequence beginning with Thread creation.
    - `profiles`: Current Profile records for every action author.

    # Usage

    Pass the complete persisted history and every referenced current Profile.
    Use the returned state, attention, revision, and Comments as the
    authoritative current discussion; do not merge them with cached values.

    # Returns

    - `First`: The discussion status after the last action.
    - `Second`: The attention value after the last action.
    - `Third`: Comments in creation order, including deleted Comments as
      tombstones with their final revision and timestamps.

    # Failures

    - Raises `AssertionError` when creation, transition, revision, body, or
      attribution facts form an impossible persisted history. Reduction never
      repairs or skips incomplete actions.
    """

    assert actions != () and actions[0].kind == "thread-created"
    assert [action.sequence for action in actions] == list(
        range(len(actions))
    ), "review action sequence must be contiguous"
    state = actions[-1].status_after
    attention = actions[-1].attention_after
    comments: dict[str, _CommentState] = {}
    order: list[str] = []
    for action in actions:
        profile_id = action.profile_id
        assert profile_id in profiles, "review action has no Profile"
        if action.comment_id is not None and action.kind in {
            "thread-created",
            "comment-created",
            "thread-resolved",
            "thread-reopened",
        }:
            assert action.comment_id is not None and action.body is not None
            assert action.expected_revision is None
            assert action.comment_id not in comments
            assert action.body.strip() != "", "persisted Comment body is blank"
            comments[action.comment_id] = _CommentState(
                action.comment_id,
                len(order),
                profile_id,
                0,
                action.body,
                False,
                action.created_at,
                action.created_at,
            )
            order.append(action.comment_id)
        elif action.kind == "comment-edited":
            assert action.comment_id is not None and action.body is not None
            assert action.body.strip() != "", "persisted Comment body is blank"
            comment = comments[action.comment_id]
            assert not comment.deleted, "deleted Comment was edited"
            assert profile_id == comment.profile_id, (
                "Comment was edited by another author"
            )
            assert action.expected_revision == comment.revision, (
                "Comment edit has a stale revision"
            )
            comment.revision += 1
            comment.body = action.body
            comment.updated_at = action.created_at
        elif action.kind == "comment-deleted":
            assert action.comment_id is not None
            comment = comments[action.comment_id]
            assert not comment.deleted, "Comment was deleted twice"
            assert action.expected_revision == comment.revision, (
                "Comment deletion has a stale revision"
            )
            comment.revision += 1
            comment.body = None
            comment.deleted = True
            comment.updated_at = action.created_at
        else:
            assert action.kind in {
                "thread-resolved",
                "thread-reopened",
                "thread-deleted",
            }
    views: list[ReviewCommentView] = []
    for comment_id in order:
        comment = comments[comment_id]
        profile = profiles[comment.profile_id]
        author = ReviewProfileView(
            profile_id=profile.id,
            display_name=profile.username,
        )
        views.append(
            {
                "comment_id": comment.comment_id,
                "sequence": comment.sequence,
                "author": author,
                "revision": comment.revision,
                "body": comment.body,
                "deleted": comment.deleted,
                "created_at": datetime.fromisoformat(comment.created_at),
                "updated_at": datetime.fromisoformat(comment.updated_at),
            }
        )
    return state, attention, views


def _build_original_excerpt(
    origin: _RangePlacement,
    origin_file: SnapshotFileRecord,
    cache: _ReviewReadCache,
) -> ReviewExcerptView:
    """Return the selected origin lines with three surrounding lines.

    Creation calls this before persistence so every accepted text target can
    satisfy the mandatory Thread response. Thread reads call the same operation
    so the response cannot drift from the creation boundary. Absolute line
    coordinates identify the selected subrange inside this bounded selected-
    side source without involving a diff renderer or line alignment.

    # Parameters

    - `origin`: Immutable selected range and bay coordinate.
    - `origin_file`: Captured File retaining the selected side bytes.
    - `cache`: Read-scoped composition shared with other Thread views.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when the origin bay or side
      is absent or the selected end line exceeds the original bay text.
    - Raises `AssertionError` if `_selected_bay` returns a bay without the side
      it just required. Capture and filesystem failures propagate while the bay
      is composed.
    """

    selected_start = origin.start_line
    selected_end = origin.end_line
    # An excerpt is the origin bay's own text, never an alignment of two
    # sides, so no diff engine takes part in building one. `bays()` is the
    # engine-free entry point, reading decoded text without a renderer.
    origin_bay = _selected_bay(
        origin_file,
        side=origin.side,
        bay_key=origin.bay_key,
        cache=cache,
    )
    # `_selected_bay` above required this bay to carry the selected side, so
    # the text is present. The unselected side is not read: an excerpt is one
    # side's own text.
    selected_text = origin_bay.text_for(origin.side)
    assert selected_text is not None, (
        "_selected_bay accepted a bay absent on the selected side."
    )
    selected_lines = selected_text.splitlines()
    if selected_end > len(selected_lines):
        raise ReviewError(
            "invalid_target",
            "Review range exceeds the selected original text.",
        )
    excerpt_start = max(1, selected_start - 3)
    excerpt_end = min(len(selected_lines), selected_end + 3)
    return {
        "side": origin.side,
        "start_line": excerpt_start,
        "selected_start_line": selected_start,
        "selected_end_line": selected_end,
        "lines": selected_lines[excerpt_start - 1 : excerpt_end],
    }


def append_review_action(
    *,
    database: RoomStore,
    snapshot_id: UUID,
    thread_id: UUID,
    operation_id: UUID,
    author: ProfileAuthor,
    kind: Literal[
        "comment-created",
        "comment-edited",
        "comment-deleted",
        "thread-resolved",
        "thread-reopened",
        "thread-deleted",
    ],
    comment_id: Optional[UUID],
    body: Optional[str],
    comment_attention: Literal["inert", "alert"],
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[
    tuple[ReviewActionRecord, ...],
    dict[int, UserProfileRecord],
]:
    """Validate and append one action without loading captured Snapshot Files.

    Returns the appended authoritative action sequence and current Profiles;
    the caller that reports an update view folds them itself, so this write
    builds nothing its other caller discards.

    # Parameters

    - `database`: Persistence used for authoritative Profile and action reads
      and the final append.
    - `snapshot_id`: Exact placement through which the Thread is addressed.
    - `thread_id`: Existing live discussion receiving the action.
    - `operation_id`: Fresh backend-generated identity for this accepted write.
    - `author`: Existing Profile attributed to the action.
    - `kind`: Comment or lifecycle transition to validate against current state.
    - `comment_id`: New or existing Comment identity for kinds that create,
      edit, or delete a Comment, otherwise `None`.
    - `body`: Nonblank Comment text for creating or editing, optional text for
      lifecycle actions, or `None` when that kind carries no body.
    - `comment_attention`: Whether a new Comment preserves attention or alerts
      both roles.
    - `lock_path`: Cross-process Room write lock shared with publication.
    - `thread_lock`: In-process Room write lock held for the same lifetime.

    # Usage

    Call through a bound `Thread` while holding no external database session.
    Supply a fresh operation id and the exact command fields for one operation;
    this function acquires the Room locks and reloads current state before the
    append.

    # Returns

    - `First`: The complete prior action history with the accepted action
      appended at the next contiguous sequence number.
    - `Second`: Current Profiles for every author in that returned history,
      keyed by Profile id.

    # Failures

    - Raises `ReviewError` when the author or Thread is missing, the Thread is
      deleted, the expected lifecycle transition is invalid, a Comment is
      missing or already deleted, or the operation violates authorship rules.
    - Raises `AssertionError` for an impossible internal operation shape or
      persisted history.
    """
    with _room_write_lock(thread_lock, lock_path):
        profile_id = author.profile_id
        profile = _validate_author(database, author)
        persisted = database.review_actions(snapshot_id.hex, thread_id.hex)
        if persisted is None:
            raise ReviewError(
                "thread_not_found", f"Unknown Thread: {thread_id.hex}"
            )
        actions, persisted_profiles = persisted
        profiles = {
            persisted_profile.id: persisted_profile
            for persisted_profile in persisted_profiles
        }
        state, attention, comments = fold_actions(actions, profiles)
        if state == "deleted":
            raise ReviewError("state_conflict", "Thread is deleted.")
        comment_by_id = {comment["comment_id"]: comment for comment in comments}

        if kind == "comment-created":
            assert comment_id is not None and body is not None
            _nonblank(body)
            accepted_revision = None
        elif kind in {"comment-edited", "comment-deleted"}:
            assert comment_id is not None
            comment = comment_by_id.get(comment_id.hex)
            if comment is None:
                raise ReviewError(
                    "comment_not_found", f"Unknown Comment: {comment_id.hex}"
                )
            if (
                kind == "comment-edited"
                and comment["author"]["profile_id"] != profile_id
            ):
                raise ReviewError(
                    "forbidden", "Only the Comment author may edit it."
                )
            if comment["deleted"]:
                raise ReviewError("state_conflict", "Comment is deleted.")
            accepted_revision = comment["revision"]
            if kind == "comment-edited":
                assert body is not None
                _nonblank(body)
        else:
            accepted_revision = None
            if kind == "thread-resolved" and state != "open":
                raise ReviewError(
                    "state_conflict", "Only an open Thread may be resolved."
                )
            if kind == "thread-reopened" and state != "resolved":
                raise ReviewError(
                    "state_conflict", "Only a resolved Thread may be reopened."
                )

        record = ReviewActionRecord(
            operation_id=operation_id.hex,
            thread_id=thread_id.hex,
            snapshot_id=snapshot_id.hex,
            sequence=len(actions),
            kind=kind,
            profile_id=profile_id,
            comment_id=comment_id.hex if comment_id is not None else None,
            expected_revision=accepted_revision,
            body=body,
            created_at=_now(),
            status_after=(
                "resolved"
                if kind == "thread-resolved"
                else "open"
                if kind == "thread-reopened"
                else "deleted"
                if kind == "thread-deleted"
                else state
            ),
            attention_after=(
                "none"
                if kind in {"thread-resolved", "thread-deleted"}
                else "both"
                if kind == "thread-reopened"
                or (kind == "comment-created" and comment_attention == "alert")
                else attention
            ),
        )
        database.append_review_action(record)
        profiles[profile.id] = profile
        return (*actions, record), profiles


@dataclass
class _ThreadFiles:
    """Hold the Snapshot File records one bound Thread locates code against.

    `origin_file` is the origin Snapshot File behind the discussion;
    `selected_file` is the selected-Snapshot File the placement locates, or
    `None` for a file-missing placement whose absence the loading read has
    verified. The cache bounds repeated captured-text reads for excerpts.
    """

    origin_file: SnapshotFileRecord
    """Authenticated captured File referenced by the immutable origin row.

    It supplies the public path pair and, for full reads, original bay content.
    The record may belong to an earlier Snapshot than the bound handle.
    """

    selected_file: Optional[SnapshotFileRecord]
    """Authenticated File referenced by the selected placement, when located.

    `None` is valid for explicit file-missing and file-unreadable placements;
    located variants require a record whose pair equals `origin_file`.
    """

    cache: _ReviewReadCache
    """Composition cache shared by handles hydrated in the same read operation.

    It avoids decoding a File twice while building views and is discarded with
    the bound handles; it is never authoritative state.
    """


class Thread:
    """Operate on one live discussion through one exact Snapshot.

    The bound keys are immutable. The object is a lightweight handle: reads
    that interpret placement load their Snapshot Files on first use, while
    writes never load Files at all. Every write reloads authoritative actions
    under the Room publication lock, validates the requested action, appends
    it, and returns the bounded authoritative update view the HTTP boundary
    reports.
    """

    def __init__(
        self,
        *,
        database: RoomStore,
        identity: RoomIdentity,
        snapshot_id: UUID,
        thread_id: UUID,
        lock_path: Path,
        thread_lock: Lock,
        placement: ReviewThreadRecord,
        origin: ReviewThreadRecord,
        actions: tuple[ReviewActionRecord, ...],
        profiles: dict[int, UserProfileRecord],
        files: Optional[_ThreadFiles],
    ) -> None:
        """Bind one Thread to its placement and optionally preloaded Files.

        `files` carries the referenced Snapshot Files when the caller already
        loaded them in bulk; `None` defers that single focused read to the
        first placement-interpreting read on this handle.

        # Parameters

        - `database`: Store used for fresh actions and deferred File hydration.
        - `identity`: Room boundary that limits every deferred read.
        - `snapshot_id`: Exact selected placement bound for this handle's life.
        - `thread_id`: Stable discussion identity bound for this handle's life.
        - `lock_path`: Cross-process Room write lock used by methods that append.
        - `thread_lock`: In-process lock paired with `lock_path`.
        - `placement`: Selected-Snapshot flat row converted once on construction.
        - `origin`: Unique immutable origin row for the discussion.
        - `actions`: Complete ordered action sequence at construction time.
        - `profiles`: Current records for every author in `actions`.
        - `files`: Already validated referenced Files and shared cache, or
          `None` to load them only when a read interprets placement.
        """
        self.snapshot_id = snapshot_id
        self.thread_id = thread_id
        self._database = database
        self._identity = identity
        self._lock_path = lock_path
        self._thread_lock = thread_lock
        # The store returns the flat row shape; every read on this handle wants
        # the proven one, so both are converted once here rather than at each
        # interpreting read.
        self._placement = _placement_of(placement)
        origin_placement = _placement_of(origin)
        assert isinstance(
            origin_placement, (_RangePlacement, _FileStartPlacement)
        ), "a discussion origin is a stored range or File-start row"
        self._origin: _RangePlacement | _FileStartPlacement = origin_placement
        self._action_records = actions
        self._profiles = profiles
        # Mutated once by `_located_files` when constructed deferred; every
        # later locating read reuses the same loaded records and cache.
        self._files = files

    def _records(
        self,
    ) -> tuple[_Placement, _RangePlacement | _FileStartPlacement]:
        """Return the proven selected placement and unique immutable origin.

        Both values were validated from flat persistence rows during handle
        construction, so callers need not repeat discriminator narrowing.

        # Returns

        - `First`: The Thread's placement in the bound Snapshot.
        - `Second`: Its immutable origin placement, narrowed to the two valid
          origin shapes.
        """
        return self._placement, self._origin

    def _actions(self) -> tuple[ReviewActionRecord, ...]:
        """Return the handle's current complete contiguous action sequence.

        Construction and accepted writes keep this tuple authoritative for the
        handle. A missing creation action is persisted corruption and fails.

        # Returns

        - `Members`: The handle's complete action records, beginning with Thread
          creation and including every accepted later write.
        - `Order`: Records form contiguous sequence order; an empty history is
          persisted corruption and raises instead of returning.
        """
        assert self._action_records != (), (
            "persisted Thread has no first Comment"
        )
        return self._action_records

    def _located_files(self) -> _ThreadFiles:
        """Load and retain the placement's Snapshot Files on first use.

        Handles constructed without preloaded Files pay this one focused read
        the first time a read interprets placement; the same read doubles as
        the file-missing absence proof. Writes never call this.
        """
        if self._files is None:
            placement, origin = self._records()
            origin_ref = (origin.snapshot_id, origin.snapshot_file_id)
            selected_ids: tuple[str, ...] = ()
            absent_refs: tuple[tuple[str, str], ...] = ()
            # An unreadable File is present and deliberately unreferenced, so
            # it asks for neither: proving its absence would fail, and loading
            # it would offer bytes no read may use.
            if isinstance(placement, _FileMissingPlacement):
                absent_refs = (origin_ref,)
            elif not isinstance(placement, _FileUnreadablePlacement):
                selected_ids = (placement.snapshot_file_id,)
            origin_files, selected_files, conflicts = (
                self._database.review_thread_files(
                    self.snapshot_id.hex,
                    (origin_ref,),
                    selected_ids,
                    absent_refs,
                )
            )
            assert conflicts == (), (
                "file_missing placement has an exact Snapshot File"
            )
            selected_file: Optional[SnapshotFileRecord] = None
            if selected_ids != ():
                assert not isinstance(
                    placement,
                    _FileMissingPlacement | _FileUnreadablePlacement,
                )
                selected_file = selected_files.get(placement.snapshot_file_id)
                assert selected_file is not None, (
                    "located placement has no exact Snapshot File"
                )
            self._files = _ThreadFiles(
                origin_file=origin_files[origin_ref],
                selected_file=selected_file,
                cache=_ReviewReadCache(),
            )
        return self._files

    def _placement_view(self) -> ThreadPlacementView:
        """Fold placement facts into the public placement.

        The returned shape names one derivation outcome and states only what
        the origin does not: the File pair and side are the origin's in every
        variant, and the bay is the origin's in all but a `bay-lost` landing,
        which names the bay derivation chose instead. `region-kept` and
        `whole-file` report nothing wrong; the six others are the complete
        public outdated vocabulary, one name per state.

        The two unlocated variants state nothing but their kind. For the
        absent File the File-loading read has already verified no
        selected-Snapshot File carries the origin pair, so absence there is an
        invariant, not a substitute.
        """
        placement, origin = self._records()
        files = self._located_files()
        if isinstance(placement, _FileUnreadablePlacement):
            return {"kind": "file-unreadable"}
        if isinstance(placement, _FileMissingPlacement):
            assert files.selected_file is None, (
                "file_missing placement has an exact Snapshot File"
            )
            return {"kind": "file-absent"}
        target_file = files.selected_file
        assert target_file is not None, (
            "located placement has no exact Snapshot File"
        )
        assert target_file.id == placement.snapshot_file_id, (
            "placement references the wrong Snapshot File"
        )
        # The File pair travels once, on the origin. A placement that named
        # another File would be read under the origin's paths with nothing
        # left to contradict it, so the equality is proven here instead.
        assert _file_pair(target_file) == _file_pair(files.origin_file), (
            "placement references the wrong Snapshot File pair"
        )
        assert placement.side == origin.side, (
            "placement selects the side the origin did not"
        )
        match placement:
            case _RangePlacement():
                # A matched region stays inside the bay it was written in, so
                # the bay the wire omits here is exactly the origin's.
                assert isinstance(origin, _RangePlacement), (
                    "a File-level origin never matches a region"
                )
                assert placement.bay_key == origin.bay_key, (
                    "a matched region left its origin's bay"
                )
                return {
                    "kind": (
                        "region-changed"
                        if placement.outdated_reason == "region_changed"
                        else "region-kept"
                    ),
                    "range": {
                        "start_line": placement.start_line,
                        "end_line": placement.end_line,
                    },
                }
            case _BayStartPlacement():
                if placement.outdated_reason == "region_not_found":
                    # Only the region inside the bay was lost, so this landing
                    # also sits in the origin's own bay.
                    assert isinstance(origin, _RangePlacement), (
                        "a File-level origin never loses a region"
                    )
                    assert placement.bay_key == origin.bay_key, (
                        "a region-lost landing left its origin's bay"
                    )
                    return {"kind": "region-lost"}
                return {
                    "kind": "bay-lost",
                    "bay": {"bay_key": placement.bay_key},
                }
            case _FileStartPlacement():
                if placement.outdated_reason is None:
                    assert isinstance(origin, _FileStartPlacement), (
                        "a text origin never rests on its File unchanged"
                    )
                    return {"kind": "whole-file"}
                return {"kind": "side-lost"}

    def discussion(self) -> ThreadDiscussionView:
        """Fold the complete discussion with its bounded original excerpt.

        The excerpt travels inside the origin it is cut from, so a File-level
        origin carries none. Index-style callers read the placement for where
        the Thread landed, and explicitly render that File when it reports
        `region-changed`.

        # Usage

        Use this for a complete Thread page or browser response. It may read
        captured File contents to build the origin excerpt; use `summary` when
        discovery facts are enough.

        # Failures

        - Raises `AssertionError` when persisted placement, origin, File, or
          action data cannot form one valid discussion.
        """
        _placement, origin = self._records()
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        files = self._located_files()
        origin_target = _origin_target_dict(origin, files.origin_file)
        if isinstance(origin, _RangePlacement):
            # Only a discussion read builds an excerpt, and it belongs to the
            # origin it is cut from. The summary path reads no captured text,
            # so the key is attached here rather than by the shared builder.
            assert origin_target["kind"] == "text"
            origin_target["excerpt"] = _build_original_excerpt(
                origin, files.origin_file, files.cache
            )
        return ThreadDiscussionView(
            thread_id=self.thread_id.hex,
            snapshot_id=self.snapshot_id.hex,
            created_at=datetime.fromisoformat(actions[0].created_at),
            state=state,
            attention=attention,
            discussion_revision=len(actions) - 1,
            origin_target=origin_target,
            placement=self._placement_view(),
            comments=comments,
        )

    def summary(self) -> ThreadSummaryView:
        """Materialize discovery facts without reading any captured text.

        The same action reduction and placement checks as `discussion`, minus the
        original-excerpt construction and the complete Comment list. The
        origin still travels: it is where the File pair, bay, and side a
        caller needs to name captured code are stated.

        # Usage

        Use this for Thread indexes and agent discovery pages. Call
        `discussion` only after the caller selects a Thread and needs Comments
        or original source context.

        # Failures

        - Raises `AssertionError` when persisted placement, origin, or action
          data cannot form one valid summary.
        """
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        assert comments != [], "persisted Thread folded to zero Comments"
        _placement, origin = self._records()
        files = self._located_files()
        return ThreadSummaryView(
            thread_id=self.thread_id.hex,
            state=state,
            attention=attention,
            origin_target=_origin_target_dict(origin, files.origin_file),
            placement=self._placement_view(),
            first_comment=comments[0],
            latest_comment=comments[-1],
            comment_count=len(comments),
        )

    def _append(
        self,
        *,
        operation_id: UUID,
        author: ProfileAuthor,
        kind: Literal[
            "comment-created",
            "comment-edited",
            "comment-deleted",
            "thread-resolved",
            "thread-reopened",
            "thread-deleted",
        ],
        comment_id: Optional[UUID],
        body: Optional[str],
        comment_attention: Literal["inert", "alert"],
    ) -> ThreadUpdateView:
        """Validate, append, and return the write's bounded update view.

        # Parameters

        - `operation_id`: Fresh identity for the one attempted append.
        - `author`: Existing Profile whose permissions are checked.
        - `kind`: Exact Comment or lifecycle transition to perform.
        - `comment_id`: Affected Comment identity, or `None` for a body-less
          Thread lifecycle action.
        - `body`: New Comment text when the transition carries one.
        - `comment_attention`: Attention rule used only for Comment creation.
        """
        actions, profiles = append_review_action(
            database=self._database,
            snapshot_id=self.snapshot_id,
            thread_id=self.thread_id,
            operation_id=operation_id,
            author=author,
            kind=kind,
            comment_id=comment_id,
            body=body,
            comment_attention=comment_attention,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )
        # Placement and captured code are immutable. Only authored action history
        # and current Profile names change after an accepted write.
        self._action_records = actions
        self._profiles = profiles
        # The HTTP boundary is the one consumer of the update view, so every
        # write materializes the bounded view it reports instead of rehydrating
        # placement.
        state, attention, comments = fold_actions(actions, profiles)
        comment = (
            next(
                folded
                for folded in comments
                if folded["comment_id"] == comment_id.hex
            )
            if comment_id is not None
            else None
        )
        return ThreadUpdateView(
            thread_id=self.thread_id.hex,
            snapshot_id=self.snapshot_id.hex,
            state=state,
            attention=attention,
            discussion_revision=len(actions) - 1,
            comment=comment,
        )

    def add_comment(
        self,
        command: AddComment,
        *,
        attention: Literal["inert", "alert"],
    ) -> ThreadUpdateView:
        """Append one Comment and return the authoritative update view.

        `attention` is the posting instrument: `alert` raises both-role
        attention with the new Comment, `inert` leaves current attention
        unchanged.

        # Parameters

        - `command`: Fresh Comment identity, existing author, and nonblank body.
        - `attention`: Instrument-specific attention transition for this append.

        # Usage

        Create `AddComment` with a fresh operation and Comment id. Choose
        `alert` for an ordinary reply and `inert` only when another instrument
        controls attention for the same logical action.

        # Failures

        - Raises `ReviewError` when the author is missing, the body is blank, an
          id is reused, or the Thread is deleted.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-created",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention=attention,
        )

    def edit_comment(
        self, comment_id: UUID, command: EditComment
    ) -> ThreadUpdateView:
        """Edit one authored Comment and return the update view.

        # Parameters

        - `comment_id`: Existing non-deleted Comment to replace.
        - `command`: Acting author, fresh operation id, and nonblank replacement
          body. Only the original author may edit; the current revision is
          loaded by the Thread rather than supplied by the caller.

        # Usage

        Bind the Thread through the Comment's Snapshot placement, then pass the
        target Comment id and a fresh operation id. The returned Comment carries
        the newly assigned revision.

        # Failures

        - Raises `ReviewError` when the Comment or author is missing, the body is
          blank, the Comment is deleted, the actor is not its original author,
          or the Thread is deleted.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-edited",
            comment_id=comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def delete_comment(
        self, comment_id: UUID, command: DeleteComment
    ) -> ThreadUpdateView:
        """Tombstone one Comment and retain the acting Profile attribution.

        # Parameters

        - `comment_id`: Existing non-deleted Comment to tombstone.
        - `command`: Acting Profile and fresh operation id. The Thread loads the
          current revision; the actor need not be the original author.

        # Usage

        Bind the Thread through the Comment's Snapshot placement and pass a
        fresh operation id. Use the returned tombstone and revision instead of
        mutating a previously read Comment locally.

        # Failures

        - Raises `ReviewError` when the Comment or actor is missing, the Comment
          is already deleted, an id is reused, or the Thread is deleted.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-deleted",
            comment_id=comment_id,
            body=None,
            comment_attention="inert",
        )

    def resolve(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Resolve this bound open Thread and return its authoritative outcome.

        The command may include a paired nonblank explanation Comment. The
        method reloads current actions under the Room lock, rejects any non-open
        state, appends atomically, then returns state, attention, revision, and
        the optional created Comment.

        # Usage

        A reviewer uses this on an open bound Thread. Supply a fresh operation
        id and either both explanation fields or neither.

        # Failures

        - Raises `ReviewError` when the author is missing, the Thread is not
          open, explanation fields are invalid, or an identity is reused.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-resolved",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def reopen(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Reopen this bound resolved Thread and return its authoritative outcome.

        A paired explanation Comment is optional. Current actions are reloaded
        under the Room lock; any state other than resolved fails before append,
        and an accepted transition sets attention to both roles.

        # Usage

        Use this on a resolved bound Thread. Supply a fresh operation id and,
        when explaining the reopen, both a fresh Comment id and nonblank body.

        # Failures

        - Raises `ReviewError` when the author is missing, the Thread is not
          resolved, explanation fields are invalid, or an identity is reused.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-reopened",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def delete(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Record terminal deletion of this bound live Thread.

        The command must carry no Comment id or body. Current history is
        revalidated under the Room lock, the deletion action is appended with
        attention `none`, and later writes through the Thread are rejected.

        # Usage

        Use this exceptional reviewer instrument only when the whole Thread
        must become a retained terminal tombstone. Supply a fresh operation id
        and no explanation fields.

        # Failures

        - Raises `ReviewError` when the author is missing, the Thread is already
          deleted, explanation fields are present, or an identity is reused.
        """
        assert command.comment_id is None, (
            "Thread deletion never carries a Comment."
        )
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-deleted",
            comment_id=None,
            body=None,
            comment_attention="inert",
        )


def thread_objects(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
    offset: int,
    limit: int,
    state: Literal["all", "open"],
    attention: Optional[Literal["author", "reviewer"]],
    through_activity_id: Optional[int],
) -> tuple[tuple[Thread, ...], int, int]:
    """Bulk-hydrate one bounded Thread page at one inclusive activity pivot.

    # Parameters

    - `database`: Room persistence supplying the bounded relational page.
    - `identity`: Room containing the selected Snapshot and discussions.
    - `snapshot_id`: Exact code universe whose placements are bound.
    - `lock_path`: Cross-process write lock carried by returned handles.
    - `thread_lock`: In-process write lock carried by returned handles.
    - `offset`: Zero-based number of ordered matching Threads to skip.
    - `limit`: Positive maximum number of Threads to return.
    - `state`: Include every lifecycle state or only open discussions.
    - `attention`: Optional agent-role attention filter.
    - `through_activity_id`: Inclusive stable page pivot, or `None` to choose
      the current Room boundary with this persistence read.

    # Usage

    Room paging calls this once per page. Retain the returned concrete activity
    pivot and pass it to later pages so every `Thread` reflects the same bounded
    action history.

    # Returns

    - `First`: Bound Thread handles in the store's page order.
    - `Second`: Total Threads matching the filters, independent of page length.
    - `Third`: The concrete inclusive activity pivot used for this page and any
      continuation pages.

    # Failures

    - Raises `DirdiffError` when the Snapshot does not belong to the Room.
    - Raises `AssertionError` when persisted placements, origins, actions,
      Profiles, or referenced Files are incomplete or contradictory.
    """
    result = database.review_threads(
        identity,
        snapshot_id.hex,
        offset=offset,
        limit=limit,
        state=state,
        attention=attention,
        through_activity_id=through_activity_id,
    )
    if result is None:
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    data, concrete_activity_id = result
    return (
        _bind_threads(
            database=database,
            identity=identity,
            snapshot_id=snapshot_id,
            data=data,
            lock_path=lock_path,
            thread_lock=thread_lock,
        ),
        data.total_threads,
        concrete_activity_id,
    )


def _bind_threads(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    data: ReviewThreadsRecord,
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[Thread, ...]:
    """Bind hydrated Thread rows to exactly the Files they reference.

    One focused store read loads every referenced origin File, every located
    selected-Snapshot File, and the file-missing absence proof, replacing the
    former complete-Snapshot loads and thrown-away indexes.

    # Parameters

    - `database`: Persistence used for the one focused File hydration.
    - `identity`: Room identity carried by every returned handle.
    - `snapshot_id`: Exact placement Snapshot selected by the page.
    - `data`: Complete mutually consistent rows returned by one store read.
    - `lock_path`: Cross-process write lock carried by returned handles.
    - `thread_lock`: In-process write lock carried by returned handles.

    # Returns

    - `Members`: One bound handle per origin in `data`, each carrying its exact
      selected placement, origin, action history, Profiles, and focused Files.
    - `Order and sharing`: Handles preserve persistence order and share one
      read-scoped composed-bay cache.
    """
    placements = {row.thread_id: row for row in data.threads}
    origins = {row.thread_id: row for row in data.origins}
    actions: dict[str, list[ReviewActionRecord]] = {
        thread_id: [] for thread_id in origins
    }
    for action in data.actions:
        actions[action.thread_id].append(action)
    assert (
        len(placements) == len(origins) == len(data.origins)
        and placements.keys() == origins.keys()
    ), "review read contains duplicate Thread rows"
    profiles = {profile.id: profile for profile in data.profiles}
    assert len(profiles) == len(data.profiles), (
        "review read contains duplicate Profiles"
    )
    for origin in data.origins:
        assert origin.snapshot_file_id is not None, (
            "review origin has no Snapshot File"
        )
    origin_refs = tuple(
        dict.fromkeys(
            (origin.snapshot_id, origin.snapshot_file_id)
            for origin in data.origins
            if origin.snapshot_file_id is not None
        )
    )
    located_file_ids = tuple(
        dict.fromkeys(
            placement.snapshot_file_id
            for placement in data.threads
            if placement.snapshot_file_id is not None
        )
    )
    absent_origin_refs = tuple(
        dict.fromkeys(
            (
                origins[placement.thread_id].snapshot_id,
                origin_file_id,
            )
            for placement in data.threads
            # An unreadable File is unreferenced but present, so it is not an
            # absence to prove.
            if placement.snapshot_file_id is None
            and placement.outdated_reason != "file_unreadable"
            and (
                origin_file_id := origins[placement.thread_id].snapshot_file_id
            )
            is not None
        )
    )
    origin_files, selected_files, conflicts = database.review_thread_files(
        snapshot_id.hex,
        origin_refs,
        located_file_ids,
        absent_origin_refs,
    )
    assert conflicts == (), "file_missing placement has an exact Snapshot File"
    cache = _ReviewReadCache()
    threads: list[Thread] = []
    for origin in data.origins:
        placement = placements[origin.thread_id]
        assert origin.snapshot_file_id is not None
        selected_file: Optional[SnapshotFileRecord] = None
        if placement.snapshot_file_id is not None:
            selected_file = selected_files.get(placement.snapshot_file_id)
            assert selected_file is not None, (
                "located placement has no exact Snapshot File"
            )
        threads.append(
            Thread(
                database=database,
                identity=identity,
                snapshot_id=snapshot_id,
                thread_id=UUID(hex=origin.thread_id),
                lock_path=lock_path,
                thread_lock=thread_lock,
                placement=placement,
                origin=origin,
                actions=tuple(actions[origin.thread_id]),
                profiles=profiles,
                files=_ThreadFiles(
                    origin_file=origin_files[
                        (origin.snapshot_id, origin.snapshot_file_id)
                    ],
                    selected_file=selected_file,
                    cache=cache,
                ),
            )
        )
    return tuple(threads)


def get_thread(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    thread_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
) -> Thread:
    """Return one exact bound Thread or report that it does not exist.

    The returned handle carries placement and the authored actions; it loads its
    Snapshot Files only when a read interprets placement, so write-only
    callers never pay for File hydration.

    # Parameters

    - `database`: Persistence used to load the focused discussion.
    - `identity`: Room that must contain the Snapshot and Thread origin.
    - `snapshot_id`: Exact placement to bind.
    - `thread_id`: Stable discussion identity to bind.
    - `lock_path`: Cross-process write lock carried by the handle.
    - `thread_lock`: In-process write lock carried by the handle.

    # Usage

    Use this for a focused discussion read or write after the Room and Snapshot
    are known. The handle remains bound to that placement for its lifetime.

    # Failures

    - Raises `ReviewError` when the Snapshot is unknown in the Room or the
      Thread has no placement in that Snapshot.
    - Raises `AssertionError` when persisted discussion rows violate review
      invariants.
    """
    data = database.review_thread(
        identity,
        snapshot_id.hex,
        thread_id.hex,
    )
    if data is None:
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    if data.origins == ():
        raise ReviewError(
            "thread_not_found", f"Unknown Thread: {thread_id.hex}"
        )
    assert len(data.threads) == len(data.origins) == 1
    profiles = {profile.id: profile for profile in data.profiles}
    assert len(profiles) == len(data.profiles), (
        "review read contains duplicate Profiles"
    )
    return Thread(
        database=database,
        identity=identity,
        snapshot_id=snapshot_id,
        thread_id=thread_id,
        lock_path=lock_path,
        thread_lock=thread_lock,
        placement=data.threads[0],
        origin=data.origins[0],
        actions=data.actions,
        profiles=profiles,
        files=None,
    )


def derive_room_threads(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    target_snapshot: SnapshotRecord,
) -> tuple[ReviewThreadRecord, ...]:
    """Place only Room Threads absent from one target Snapshot.

    # Parameters

    - `database`: Persistence supplying missing origins and referenced Files.
    - `identity`: Room whose complete discussion set is considered.
    - `target_snapshot`: Fully captured new Snapshot receiving placements.

    The function returns immutable rows for publication and writes nothing.

    # Usage

    Call during publication of a genuinely new Snapshot, after its complete
    File records exist in memory and before the database publication. Pass the
    returned rows to `RoomStore.publish` with that same Snapshot.

    # Returns

    - `Members`: One immutable target-Snapshot placement for each discussion
      missing there; discussions already placed in the Snapshot are absent.
    - `Order`: Placements follow File pair, side, bay, and Thread id so adjacent
      derivations can reuse source parses.

    # Failures

    - Raises `AssertionError` when an origin File is missing, a derived placement
      contradicts the target Snapshot, or persisted origin data is malformed.
    """
    origins = {
        origin.thread_id: origin
        for origin in database.review_origins_missing(
            identity,
            target_snapshot.id,
        )
    }
    # One set-based read loads exactly the origin Files these placements
    # reference; the origin Snapshots themselves are never hydrated. The
    # target Snapshot arrives fully loaded from the capture that triggered
    # this derivation.
    origin_refs = tuple(
        dict.fromkeys(
            (origin.snapshot_id, origin.snapshot_file_id)
            for origin in origins.values()
            if origin.snapshot_file_id is not None
        )
    )
    origin_files, _selected_files, _conflicts = database.review_thread_files(
        target_snapshot.id, origin_refs, (), ()
    )
    target_files_by_pair = _file_indexes(target_snapshot)[1]
    cache = _ReviewReadCache()
    grouped_origins: list[
        tuple[
            tuple[str, str, str, str, str],
            _RangePlacement | _FileStartPlacement,
            Optional[_Locator],
            SnapshotFileRecord,
        ]
    ] = []
    for record in origins.values():
        origin = _placement_of(record)
        assert isinstance(origin, (_RangePlacement, _FileStartPlacement)), (
            "a discussion origin is a stored range or File-start row"
        )
        # Derivation is the only reader of private coordinates, so this is the
        # one place that decodes them; reads never pay for it.
        locator = (
            None
            if record.private_locator is None
            else _locator_of(record.private_locator, side=origin.side)
        )
        origin_file = origin_files[
            (origin.snapshot_id, origin.snapshot_file_id)
        ]
        pair = _file_pair(origin_file)
        grouped_origins.append(
            (
                (
                    pair.left_path or "",
                    pair.right_path or "",
                    origin.side,
                    origin.bay_key
                    if isinstance(origin, _RangePlacement)
                    else "",
                    origin.thread_id,
                ),
                origin,
                locator,
                origin_file,
            )
        )
    # Adjacent target sources stay resident in the three-entry region cache.
    grouped_origins.sort(key=lambda item: item[0])
    placements: list[ReviewThreadRecord] = []
    for _group, origin, locator, origin_file in grouped_origins:
        # An origin already addressing the target Snapshot is its own placement
        # there and needs no derivation.
        is_origin = origin.snapshot_id == target_snapshot.id
        placed = (
            origin
            if is_origin
            else _derive_record(
                origin=origin,
                locator=locator,
                origin_file=origin_file,
                target_snapshot_id=target_snapshot.id,
                target_files_by_pair=target_files_by_pair,
                cache=cache,
            )
        )
        placements.append(
            _record_of(
                placed,
                is_origin=is_origin,
                locator=locator if is_origin else None,
            )
        )
    return tuple(placements)


def _origin_record(
    command: CreateThread,
    snapshot_id: str,
    file: SnapshotFileRecord,
    cache: _ReviewReadCache,
) -> tuple[_RangePlacement, _Locator]:
    """Build one unique origin and the private coordinates that retain it.

    The coordinates are returned beside the placement rather than inside it,
    because only persistence and later derivation read them.

    # Parameters

    - `command`: Validated new-Thread target and generated identities.
    - `snapshot_id`: Exact immutable Snapshot in which the origin is created.
    - `file`: Captured File matching the command's exact path pair.
    - `cache`: Operation-scoped composition shared with excerpt validation.

    # Returns

    - `First`: The immutable public origin coordinates in the selected Snapshot
      File and bay.
    - `Second`: Private region hash, byte bounds, and structural segments used
      only to derive later placements.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when the bay or selected
      side is absent, a non-text bay is addressed by anything except `1..1`, the
      range exceeds the bay text, or no source region contains the range.
    - Raises `AssertionError` if a bay accepted for the selected side yields no
      text. Capture, digest, and filesystem failures propagate from composition.
    """

    def origin_region(
        path: str, text: str, selected: LineRange
    ) -> _SourceRegion:
        """Choose the smallest source region containing the selected range.

        # Parameters

        - `path`: Selected bay hint used for structural parser choice.
        - `text`: Complete selected-side bay source.
        - `selected`: Positive bay-local range that the region must contain.

        # Usage

        Call only after composition has selected the exact bay-side text. The
        returned region becomes part of the immutable origin locator.

        # Failures

        - Raises `ReviewError` when the range exceeds the source or no parser
          region contains it.
        """
        line_count = len(text.splitlines())
        if selected.end_line > line_count:
            raise ReviewError(
                "invalid_target",
                "Review range exceeds the selected rendered text.",
            )
        candidates = [
            region
            for region in _regions_for_source(path, text)
            if region.start_line <= selected.start_line
            and region.end_line >= selected.end_line
        ]
        if candidates == []:
            raise ReviewError(
                "invalid_target",
                "Review range has no containing text region.",
            )
        return min(
            candidates,
            key=lambda region: (
                region.end_byte - region.start_byte,
                region.segments == (),
            ),
        )

    # The bay must be one composition actually produced for this File. A
    # File that composes no such bay cannot carry the target, which is what
    # rejects an ordinary target against a notebook and any stale key alike.
    selected = _selected_bay(
        file,
        side=command.target.side,
        bay_key=command.target.bay_key,
        cache=cache,
    )
    # An image bay exposes exactly one pseudo-line, so `1..1` is the only
    # coordinate that describes anything in it. The kind comes from
    # composition, so this rejects a stale line range against a File that used
    # to be text rather than trusting the range the client sent.
    if selected.kind != "text" and command.target.range != LineRange(1, 1):
        raise ReviewError(
            "invalid_target",
            "A non-text bay accepts only the single line 1 to 1.",
        )
    text = selected.text_for(command.target.side)
    assert text is not None, "selected bay text was already required"
    path = selected.hint_for(command.target.side) or _path_hint(
        file, command.target.side
    )
    region = origin_region(path, text, command.target.range)
    locator = _Locator(
        region_hash=hashlib.sha256(
            region.source[region.start_byte : region.end_byte]
        ).digest(),
        region_start_byte=region.start_byte,
        region_end_byte=region.end_byte,
        segments=region.segments,
    )
    return (
        _RangePlacement(
            thread_id=command.thread_id.hex,
            snapshot_id=snapshot_id,
            snapshot_file_id=file.id,
            bay_key=command.target.bay_key,
            side=command.target.side,
            start_line=command.target.range.start_line,
            end_line=command.target.range.end_line,
            outdated_reason=None,
        ),
        locator,
    )


def _plan_thread_creation(
    *,
    command: CreateThread,
    created_at: str,
    snapshot_id: str,
    target_file: Optional[SnapshotFileRecord],
    cache: _ReviewReadCache,
) -> tuple[tuple[ReviewThreadRecord, ...], ReviewActionRecord]:
    """Validate and build immutable rows for one new discussion.

    The caller holds the Room write lock and supplies the focused lookup
    result for the command's target pair; `None` means the pair named no
    captured File and is rejected here, so the absence check lives in one
    place. This operation performs no insert so one or several planned
    creations can join a larger database transaction.

    # Parameters

    - `command`: New discussion identities, author, target, and first Comment.
    - `created_at`: One serialized UTC time shared by the first action.
    - `snapshot_id`: Exact origin Snapshot key in persistence form.
    - `target_file`: Focused pair lookup result, or `None` when absent.
    - `cache`: Operation-scoped composition reused by target validation and
      original-excerpt proof.

    # Returns

    - `First`: The one origin placement row, wrapped for the persistence API that
      also accepts derived placement collections.
    - `Second`: Sequence-zero Thread creation and first Comment, using the same
      identities and timestamp as the validated command.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when the target File is
      absent, the first Comment is blank, or the target bay, side, or range is
      not reviewable in the origin Snapshot.
    - Propagates capture, digest, filesystem, and persisted-data failures from
      origin construction and mandatory excerpt proof. No row has been inserted
      when validation fails.
    """
    # The absence rejection precedes body validation: callers historically
    # resolved the target before planning, so a doubly-invalid creation
    # reports the absent File, not the blank body.
    if target_file is None:
        raise ReviewError(
            "invalid_target",
            "Review target File is absent from the Snapshot.",
        )
    _nonblank(command.body)
    profile_id = command.author.profile_id
    origin, locator = _origin_record(command, snapshot_id, target_file, cache)
    _build_original_excerpt(origin, target_file, cache)
    return (
        (_record_of(origin, is_origin=True, locator=locator),),
        ReviewActionRecord(
            operation_id=command.operation_id.hex,
            thread_id=command.thread_id.hex,
            snapshot_id=snapshot_id,
            sequence=0,
            kind="thread-created",
            profile_id=profile_id,
            comment_id=command.comment_id.hex,
            expected_revision=None,
            body=command.body,
            created_at=created_at,
            status_after="open",
            attention_after="author",
        ),
    )


def create_thread(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    command: CreateThread,
    lock_path: Path,
    thread_lock: Lock,
) -> Thread:
    """Create one discussion in its immutable origin Snapshot.

    # Parameters

    - `database`: Persistence for Profile validation, focused File lookup, and
      the atomic origin/action insert.
    - `identity`: Room that must contain the origin Snapshot.
    - `snapshot_id`: Exact Snapshot whose selected range becomes the origin.
    - `command`: Fresh identities, author, valid target, and first Comment.
    - `lock_path`: Cross-process Room write lock shared with publication.
    - `thread_lock`: In-process Room write lock held for the same lifetime.

    # Usage

    Construct the command from a validated public target and fresh Thread,
    Comment, and operation ids. Call through `Room.create_thread`, which supplies
    the Room identity and shared locks.

    # Failures

    - Raises `ReviewError` when the Snapshot or Profile is missing, the target
      File or bay cannot be reviewed, the range is invalid, or an identity has
      already been used.
    - Raises `AssertionError` when captured or persisted data violates an
      internal review invariant.
    """

    with _room_write_lock(thread_lock, lock_path):
        profile = _validate_author(database, command.author)
        # One focused File read replaces hydrating the whole Snapshot: the
        # creation needs exactly its target File and Snapshot visibility.
        snapshot_exists, loaded_target = database.snapshot_file(
            identity,
            snapshot_id=snapshot_id.hex,
            left_path=command.target.file.left_path,
            right_path=command.target.file.right_path,
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        cache = _ReviewReadCache()
        created_at = _now()
        rows, first_action = _plan_thread_creation(
            command=command,
            created_at=created_at,
            snapshot_id=snapshot_id.hex,
            target_file=loaded_target.file
            if loaded_target is not None
            else None,
            cache=cache,
        )
        database.create_review_thread(
            rows,
            first_action,
        )
        assert loaded_target is not None
        assert len(rows) == 1 and rows[0].is_origin
        assert rows[0].snapshot_file_id == loaded_target.file.id
        created_file = loaded_target.file
        return Thread(
            database=database,
            identity=identity,
            snapshot_id=snapshot_id,
            thread_id=command.thread_id,
            lock_path=lock_path,
            thread_lock=thread_lock,
            placement=rows[0],
            origin=rows[0],
            actions=(first_action,),
            profiles={profile.id: profile},
            # A new Thread's origin Snapshot is the selected Snapshot, so one
            # File serves both bindings.
            files=_ThreadFiles(
                origin_file=created_file,
                selected_file=created_file,
                cache=cache,
            ),
        )


def apply_review_batch(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    batch: tuple[ReviewBatchAction, ...],
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[ReviewBatchResult, ...]:
    """Validate and apply one ordered multi-Thread batch atomically.

    The agent boundary generates fresh identifiers before calling. Every
    semantic check runs while holding the same Room lock used by browser
    writes. Only after all items are valid are their placement and action rows
    inserted in one database transaction.

    # Parameters

    - `database`: Persistence used for set-based validation and one final write.
    - `identity`: Room containing every addressed Thread and Snapshot.
    - `snapshot_id`: Exact code universe against which all actions apply.
    - `batch`: Ordered non-empty instruments; later items observe earlier items
      in memory before anything is committed.
    - `lock_path`: Cross-process Room write lock shared with publication.
    - `thread_lock`: In-process Room write lock held for the complete batch.

    # Usage

    Call through `Room.apply_review_batch` with the agent's full ordered batch.
    Every command must use the same author Profile. Results preserve input order
    and contain the authoritative state after each accepted command.

    # Returns

    - `Members`: One authoritative result for every accepted input action,
      containing its instrument, identities, resulting state, and attention.
    - `Order`: Results match input order after later actions have observed
      earlier batch state; an invalid batch returns nothing and writes nothing.

    # Failures

    - Raises `ReviewError` when the batch is empty, mixes authors, repeats an
      operation id, addresses invalid code, or contains an operation invalid for
      the current in-memory Thread state. Nothing is written on failure.
    - Raises `AssertionError` when persistence or command shapes contradict the
      review invariants.
    """
    if batch == ():
        raise ReviewError("invalid_target", "Review batch cannot be empty.")
    with _room_write_lock(thread_lock, lock_path):
        # One set-based File read replaces hydrating the whole Snapshot:
        # only the batch's distinct creation targets are loaded, in a single
        # query and transaction that also answers Snapshot visibility.
        # Absent pairs stay unmapped so planning rejects exactly the invalid
        # creation.
        creation_pairs = tuple(
            dict.fromkeys(
                action.target.file
                for action in batch
                if isinstance(action, CreateThread)
            )
        )
        snapshot_exists, found_by_pair = database.snapshot_files_by_pairs(
            identity,
            snapshot_id=snapshot_id.hex,
            pairs=tuple(
                (pair.left_path, pair.right_path) for pair in creation_pairs
            ),
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        selected_files_by_pair: dict[FilePair, SnapshotFileRecord] = {
            pair: found_by_pair[(pair.left_path, pair.right_path)]
            for pair in creation_pairs
            if (pair.left_path, pair.right_path) in found_by_pair
        }
        cache = _ReviewReadCache()
        placements: list[ReviewThreadRecord] = []
        records: list[ReviewActionRecord] = []
        results: list[ReviewBatchResult] = []

        # Set-based reads replace per-action queries: every author in one
        # Profile read, every addressed existing Thread history in one
        # placement/action read. The ordered in-memory reduction below still lets
        # later actions observe earlier ones from the same batch, including a
        # Thread the batch itself creates.
        author_ids = [
            action.author.profile_id
            if isinstance(action, CreateThread)
            else action.command.author.profile_id
            for action in batch
        ]
        known_profiles = {
            profile.id: profile
            for profile in database.review_profiles(
                tuple(dict.fromkeys(author_ids))
            )
        }
        for author_id in author_ids:
            if author_id not in known_profiles:
                raise ReviewError(
                    "profile_not_found", f"Unknown Profile: {author_id}"
                )
        addressed = tuple(
            dict.fromkeys(
                action.thread_id.hex
                for action in batch
                if not isinstance(action, CreateThread)
            )
        )
        histories, history_profiles = database.review_actions_many(
            snapshot_id.hex, addressed
        )
        fold_profiles = {
            profile.id: profile for profile in history_profiles
        } | known_profiles
        simulated: dict[str, list[ReviewActionRecord]] = {}

        for action in batch:
            if isinstance(action, CreateThread):
                rows, first_action = _plan_thread_creation(
                    command=action,
                    created_at=_now(),
                    snapshot_id=snapshot_id.hex,
                    target_file=selected_files_by_pair.get(action.target.file),
                    cache=cache,
                )
                placements.extend(rows)
                records.append(first_action)
                # Seed the materialized state so later batch actions can address the
                # Thread this batch just created.
                simulated[action.thread_id.hex] = [first_action]
                results.append(
                    ReviewBatchResult(
                        "create-finding",
                        action.thread_id,
                        action.comment_id,
                        "open",
                        "author",
                    )
                )
                continue

            thread_key = action.thread_id.hex
            thread_actions = simulated.get(thread_key)
            if thread_actions is None:
                persisted_actions = histories.get(thread_key)
                if persisted_actions is None:
                    raise ReviewError(
                        "thread_not_found",
                        f"Unknown Thread: {thread_key}",
                    )
                thread_actions = list(persisted_actions)
                simulated[thread_key] = thread_actions

            command = action.command
            profile_id = command.author.profile_id
            state, attention, _comments = fold_actions(
                tuple(thread_actions), fold_profiles
            )
            if state == "deleted":
                raise ReviewError("state_conflict", "Thread is deleted.")
            if isinstance(action, ReplyToThread):
                reply = action.command
                _nonblank(reply.body)
                allowed_attention = {
                    "author-response": {"author", "both"},
                    "reviewer-return": {"reviewer", "both"},
                    "inert-comment": {"author", "reviewer", "both", "none"},
                }[action.instrument]
                if action.instrument != "inert-comment" and (
                    state != "open" or attention not in allowed_attention
                ):
                    raise ReviewError(
                        "state_conflict",
                        f"{action.instrument} is not valid for this Thread outcome.",
                    )
                next_attention: Literal["author", "reviewer", "both", "none"]
                if action.instrument == "author-response":
                    next_attention = "reviewer"
                elif action.instrument == "reviewer-return":
                    next_attention = "author"
                else:
                    next_attention = attention
                record = ReviewActionRecord(
                    operation_id=reply.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="comment-created",
                    profile_id=profile_id,
                    comment_id=reply.comment_id.hex,
                    expected_revision=None,
                    body=reply.body,
                    created_at=_now(),
                    status_after=state,
                    attention_after=next_attention,
                )
                result = ReviewBatchResult(
                    action.instrument,
                    action.thread_id,
                    reply.comment_id,
                    state,
                    next_attention,
                )
            elif isinstance(action, ResolveThread):
                resolve = action.command
                if state != "open" or attention not in {"reviewer", "both"}:
                    raise ReviewError(
                        "state_conflict",
                        "reviewer-resolve requires an open reviewer-attention Thread.",
                    )
                _nonblank(resolve.body)
                record = ReviewActionRecord(
                    operation_id=resolve.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="thread-resolved",
                    profile_id=profile_id,
                    comment_id=resolve.comment_id.hex,
                    expected_revision=None,
                    body=resolve.body,
                    created_at=_now(),
                    status_after="resolved",
                    attention_after="none",
                )
                result = ReviewBatchResult(
                    "reviewer-resolve",
                    action.thread_id,
                    resolve.comment_id,
                    "resolved",
                    "none",
                )
            else:
                assert isinstance(action, DeleteThread)
                deletion = action.command
                record = ReviewActionRecord(
                    operation_id=deletion.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="thread-deleted",
                    profile_id=profile_id,
                    comment_id=None,
                    expected_revision=None,
                    body=None,
                    created_at=_now(),
                    status_after="deleted",
                    attention_after="none",
                )
                result = ReviewBatchResult(
                    "reviewer-delete",
                    action.thread_id,
                    None,
                    "deleted",
                    "none",
                )
            thread_actions.append(record)
            records.append(record)
            results.append(result)

        database.apply_review_batch(tuple(placements), tuple(records))
        return tuple(results)
