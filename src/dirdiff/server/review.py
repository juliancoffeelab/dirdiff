"""Expose browser review HTTP contracts and routes.

ReviewRoutes is the package-internal route group for reading Threads and
performing browser-authored Comment and lifecycle writes. The module keeps the
browser review Pydantic models beside the handlers that serialize them.

Instances retain one RoomLord for the application lifetime. Domain review
state remains in dirdiff.review and Room persistence; this module neither owns
Thread state nor handles external-agent or file-rendering endpoints.
"""

from datetime import datetime
from http import HTTPStatus
from typing import (
    Annotated,
    Literal,
    Self,
)
from uuid import UUID, uuid4

from fastapi import Query, Request
from fastapi.responses import (
    JSONResponse,
)
from pydantic import (
    Field,
    model_validator,
)

from dirdiff.engines import (
    DirdiffError,
)
from dirdiff.review import (
    AddComment,
    ChangeThreadState,
    CreateThread,
    DeleteComment,
    EditComment,
    FilePair,
    LineRange,
    ProfileAuthor,
    ReviewError,
    ReviewErrorCode,
    TextTarget,
)
from dirdiff.room_lord import (
    RoomLord,
)
from dirdiff.server.base import (
    ApiModel,
    Responses,
    ReviewExcerptResponse,
)
from dirdiff.server.magic import ClassRoutes

__all__ = ["ReviewRoutes"]


class ReviewFilePairModel(ApiModel):
    """Validate one reviewed File's exact nullable repository path pair.

    Review request models embed this shape before converting it to the domain
    `FilePair`. At least one normalized relative side must be present.

    It does not accept captured absolute paths or infer a missing side.
    """

    left_path: str | None
    """Repository-relative path of the captured left side.

    `None` means that side does not exist. `right_path` must then be present;
    callers must preserve the absence instead of copying the other path.
    """

    right_path: str | None
    """Repository-relative path of the captured right side.

    `None` means that side does not exist. `left_path` must then be present;
    together the two fields are the exact File identity used by review calls.
    """

    @model_validator(mode="after")
    def validate_presence(self) -> Self:
        """Enforce the domain File-pair presence and path invariants.

        Constructing `FilePair` rejects two absent sides and invalid normalized
        paths. Return this already validated HTTP model without changing values.

        # Usage

        Pydantic invokes this callback after validating both path fields. Callers
        construct `ReviewFilePairModel`; they do not call the validator directly.

        # Failures

        - Raises `ValueError` when both sides are absent or a present path is not
          a normalized repository-relative POSIX name.
        """
        FilePair(self.left_path, self.right_path)
        return self


class ReviewLineRange(ApiModel):
    """Validate one positive one-based inclusive review range.

    Review targets and placement responses use this shape for coordinates local
    to one bay side. Equal endpoints select one line.

    It is not a half-open rendered-row interval or File-global range.
    """

    start_line: int = Field(ge=1)
    """First selected source line within the bay side.

    The coordinate is one-based and inclusive. It must not exceed `end_line`.
    """

    end_line: int = Field(ge=1)
    """Last selected source line within the bay side.

    The coordinate is one-based and inclusive. Equal endpoints select exactly
    one line; an earlier value than `start_line` is invalid.
    """

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """Enforce the domain ordering for an inclusive source range.

        `LineRange` rejects reversed endpoints after field bounds have made both
        positive. Return this model unchanged when the relationship is valid.

        # Usage

        Pydantic invokes this callback after both bounded line fields validate.
        Callers construct `ReviewLineRange`; they do not call it directly.

        # Failures

        - Raises `ValueError` when `end_line` precedes `start_line`.
        """
        LineRange(self.start_line, self.end_line)
        return self


class ReviewTextBayModel(ApiModel):
    """Address one composed bay by its public key.

    Request and response types embed this value with a File pair and side. The
    key is the exact public coordinate emitted by composition.

    It does not describe the format, content kind, or File identity, and is not
    globally unique outside its File.
    """

    bay_key: str = Field(min_length=1)
    """Public key emitted by composition for one bay.

    The key is non-empty and meaningful only with the enclosing File pair.
    Callers must send it unchanged rather than deriving a key from a label.
    """


class TextReviewTarget(ApiModel):
    """Validate one constructible review target from an HTTP entity.

    New-Comment routes convert this value to the domain `TextTarget`, then Room
    validates it against the named Snapshot's composed File.

    The selected side must exist in the File pair. Text bays accept their real
    source-line ranges. Image and blob bays expose their rendered media facts as
    one review pseudo-line and accept only `1..1`. This type cannot target a
    complete File, raw media bytes, or a missing bay.
    """

    kind: Literal["text"]
    """Select the line-addressable review-target shape.

    The HTTP creation boundary accepts only the literal `text`; retained
    File-level origins use a response-only variant elsewhere.
    """

    file: ReviewFilePairModel
    """Exact captured File pair containing the target.

    The selected `side` must name a present path in this pair. The route does
    not search for a File by one path or infer the absent side.
    """

    bay: ReviewTextBayModel
    """Public composed bay containing the selected range.

    Room validates that this key belongs to `file` and that the selected side
    exposes the requested lines. For image and blob bays it identifies the bay
    whose media facts form the single review pseudo-line. It is not a rendered-row
    identity.
    """

    side: Literal["left", "right"]
    """Captured side against which the Comment is anchored.

    The corresponding path in `file` must be present. The value also selects
    the source used to validate `range` and reconstruct the origin excerpt.
    """

    range: ReviewLineRange
    """Author-selected source lines on `side` within `bay`.

    Coordinates are one-based and inclusive. Text bays accept lines in their
    decoded source. Image and blob bays accept only `1..1`, which selects their
    media-facts pseudo-line. Room rejects other or out-of-bounds ranges rather
    than trimming them to available content.
    """

    @model_validator(mode="after")
    def validate_selected_side(self) -> Self:
        """Reject a target whose selected side is absent from its File pair.

        The check relates `side` to the corresponding nullable path after each
        field is valid. It does not test whether the File or bay exists in a
        Snapshot; Room performs that validation during creation.

        # Usage

        Pydantic invokes this callback after validating the File pair and side.
        Callers construct `TextReviewTarget`; they do not call it directly.

        # Failures

        - Raises `ValueError` when `side` names an absent File path.
        """
        if self.side == "left" and self.file.left_path is None:
            raise ValueError("The selected left side is absent.")
        if self.side == "right" and self.file.right_path is None:
            raise ValueError("The selected right side is absent.")
        return self


ReviewTargetModel = TextReviewTarget
"""Name the complete set of review targets accepted for new HTTP Threads.

Only `TextReviewTarget` is currently constructible. Historical File-level
origins appear in response types but cannot enter through this alias.
"""


class NewCodeCommentRequest(ApiModel):
    """Request creation of one Thread and its first Comment.

    The browser sends the exact Snapshot, acting Profile, validated text target,
    and non-empty body. The route generates durable ids and asks Room to create
    the discussion.

    The request cannot create a File-level origin or choose lifecycle state.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Retained Snapshot against which Room validates `target`.

    The lowercase hexadecimal key must identify the same captured code the HUD
    displayed. The route does not redirect creation to a newer Snapshot.
    """

    profile_id: int = Field(gt=0)
    """Existing Profile that authors the Thread's first Comment.

    The positive id must remain valid through the write. The server rejects an
    unknown Profile and never substitutes the currently selected HUD Profile.
    """

    target: ReviewTargetModel
    """Code location that becomes the Thread's immutable origin.

    Room validates its File, bay, side, and range against `snapshot_id` before
    persisting the origin and first Comment in one operation.
    """

    body: str = Field(min_length=1)
    """Complete text of the Thread's first Comment.

    The body must contain at least one character. The HTTP boundary does not
    trim it or turn an empty value into an absent Comment.
    """


class ReplyCommentRequest(ApiModel):
    """Request one new Comment on an existing Snapshot-bound Thread.

    The route binds the named discussion through one exact captured code
    universe, attributes a fresh Comment to an existing Profile, and applies
    the explicitly chosen attention behavior.

    It cannot edit an existing Comment or change Thread lifecycle directly.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot placement through which the route binds `thread_id`.

    The key must be the captured universe currently shown to the caller. A
    matching logical Thread in another Snapshot is not an equivalent address.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Logical discussion that receives the new Comment.

    It must have a placement in `snapshot_id` and must not be deleted. The
    route performs no lookup by Comment or code location.
    """

    profile_id: int = Field(gt=0)
    """Existing Profile attributed as the new Comment's author.

    Any valid Profile may reply. The backend records this exact positive id and
    does not infer identity from the Thread's earlier authors.
    """

    body: str = Field(min_length=1)
    """Complete text appended as the next Comment.

    The body must contain at least one character. Its accepted write receives
    the next Thread sequence; the client supplies no sequence or revision.
    """

    attention: Literal["inert", "alert"]
    """Attention behavior applied with the Comment write.

    `inert` retains the current outcome and `alert` directs attention to both
    roles. The operation records that result in the same action as the Comment.
    """


PostCommentRequest = NewCodeCommentRequest | ReplyCommentRequest
"""Accept either Thread creation or a reply at the browser Comment endpoint.

- `NewCodeCommentRequest` supplies a code target and starts a Thread.
- `ReplyCommentRequest` supplies a Thread id and appends a Comment.

Route dispatch branches on the concrete validated model. The union excludes
edits, deletions, and lifecycle operations.
"""


class EditReviewCommentRequest(ApiModel):
    """Request replacement of one authored Comment body.

    The route locates the Comment through the exact Snapshot, verifies the
    acting Profile, and applies the non-empty replacement under revision rules.

    This request cannot change attribution, sequence, or Thread placement.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot placement used to locate the Comment's Thread.

    The edit applies only through this exact captured universe. The route does
    not search other Rooms or silently move the action to a newer Snapshot.
    """

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable Comment whose current body will be replaced.

    The Comment must belong to a Thread placed in `snapshot_id` and must not be
    a tombstone. Its existing sequence and creation time remain unchanged.
    """

    profile_id: int = Field(gt=0)
    """Existing Profile attempting the edit.

    The id must match the Comment's original author. The backend rejects edits
    by other valid Profiles rather than changing attribution.
    """

    body: str = Field(min_length=1)
    """Complete replacement for the current Comment text.

    It must contain at least one character. Accepting it increments the Comment
    revision without changing sequence, author, or creation time.
    """


class DeleteReviewCommentRequest(ApiModel):
    """Request conversion of one Comment into a retained tombstone.

    The route binds the Comment through the Snapshot and attributes the action
    to `profile_id` before applying revision checks.

    It does not remove the Comment row, its position, or the containing Thread.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot placement used to locate the Comment's Thread.

    The deletion is bound to this exact captured universe even though the
    Comment identity is globally unique.
    """

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable Comment to convert into a body-less tombstone.

    It must belong to a non-deleted Thread in `snapshot_id`. The action retains
    the Comment's identity, position, author, and creation time.
    """

    profile_id: int = Field(gt=0)
    """Existing Profile attributed as performing the deletion.

    The actor need not be the Comment author. The backend records this exact
    positive id on the append-only deletion action.
    """


class ChangeReviewThreadStateRequest(ApiModel):
    """Request a Thread resolve or reopen operation.

    The called endpoint supplies which transition to apply. This body supplies
    Snapshot, Thread, Profile, and an optional non-empty Comment to record in the
    same operation.

    It cannot delete a Thread or name arbitrary target state.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot placement through which the Thread transition is applied.

    The endpoint binds `thread_id` to this exact captured universe and does not
    transition another placement implicitly.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Discussion whose lifecycle state the endpoint changes.

    It must exist in `snapshot_id` and admit the transition named by the route.
    Deleted Threads reject both operations.
    """

    profile_id: int = Field(gt=0)
    """Existing Profile attributed as performing the transition.

    The backend records this actor on the lifecycle action and on the optional
    Comment created by the same operation.
    """

    body: str | None = Field(default=None, min_length=1)
    """Comment text to append with the transition, if supplied.

    `None` requests a bare lifecycle action. A present value must be non-empty;
    the Comment and transition either persist together or neither persists.
    """


class DeleteReviewThreadRequest(ApiModel):
    """Request exceptional terminal deletion of one Thread.

    The route binds the exact Snapshot and acting Profile, then applies the
    review deletion instrument without creating a Comment.

    This is not ordinary resolution and cannot be reversed by reopen.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot placement through which terminal deletion is applied.

    The endpoint binds the logical discussion to this exact captured universe.
    It does not delete matching code locations or Threads in other Rooms.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Discussion to move into its terminal deleted state.

    The Thread must be open or resolved in `snapshot_id`. Once accepted, later
    replies, Comment changes, and lifecycle transitions are rejected.
    """

    profile_id: int = Field(gt=0)
    """Existing Profile attributed as performing Thread deletion.

    Any valid Profile may perform this exceptional action. The server records
    the supplied actor and never substitutes a Thread participant.
    """


class ReviewAuthorResponse(ApiModel):
    """Return current Profile attribution beside a browser Comment.

    Response conversion uses the stable positive id and current display name
    from review materialization.

    The shape carries no role, permission, or agent registration.
    """

    profile_id: int = Field(gt=0)
    """Durable identity of the Profile attributed to the Comment.

    The positive id remains the write identity when the Profile is renamed;
    clients must use it, rather than `display_name`, for later actions.
    """

    display_name: str = Field(min_length=1)
    """Current username of the attributed Profile.

    Reads resolve the latest non-empty name, so it may differ from the text
    shown when the Comment was authored. It is presentation, not identity.
    """


class ReviewCommentResponse(ApiModel):
    """Return one current Comment or retained deletion tombstone.

    Discussion and update routes validate domain Comment views through this
    model. Sequence stays stable and revision counts edits.

    A tombstone retains attribution and timestamps with `body=None`; the
    response does not expose individual action history.
    """

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Backend-generated stable identity of this Comment.

    Edits and deletion retain the same lowercase hexadecimal id. Callers use it
    for Comment-addressed writes and must not derive identity from sequence.
    """

    sequence: int = Field(ge=0)
    """Creation position of the Comment within its Thread.

    Sequence starts at zero and remains fixed across edits and deletion. The
    returned Comment list is ordered by this value without gaps.
    """

    author: ReviewAuthorResponse
    """Profile that originally authored the Comment.

    The identity never changes through revisions. Its display name reflects
    the current Profile record rather than a historical copy.
    """

    revision: int = Field(ge=0)
    """Current zero-based version of the Comment content state.

    Creation starts at zero. Each accepted edit or tombstone action increments
    it, while unrelated Thread actions leave it unchanged.
    """

    body: str | None
    """Current Comment text after applying its actions.

    A live Comment carries its complete non-empty text. `None` is valid only
    when `deleted` is true and represents a retained tombstone.
    """

    deleted: bool
    """Whether a deletion action replaced the Comment body with a tombstone.

    True requires `body=None`; false requires a present body. The validator
    rejects either inconsistent pairing.
    """

    created_at: datetime
    """Timestamp of the Comment's creation action.

    It remains fixed through edits and deletion and is no later than
    `updated_at` for a valid materialized Comment.
    """

    updated_at: datetime
    """Timestamp of the action that produced the current Comment revision.

    It equals `created_at` before any change and advances on an edit or
    tombstone action. Thread-only lifecycle actions do not affect it.
    """

    @model_validator(mode="after")
    def validate_tombstone(self) -> Self:
        """Reject an inconsistent live-Comment or tombstone response.

        A deleted Comment has no body, while a non-deleted Comment always has
        text. Return the validated response unchanged when the pairing agrees.

        # Usage

        Pydantic invokes this callback after validating the Comment response
        fields. Callers validate the complete `ReviewCommentResponse` model.

        # Failures

        - Raises `ValueError` when deletion state and body presence disagree.
        """
        if self.deleted != (self.body is None):
            raise ValueError("Deleted Comments must be body-less tombstones.")
        return self


class ThreadRegionKeptPlacementResponse(ApiModel):
    """Locate one Thread on its unchanged region, wherever it now sits.

    The region holds the bytes the Thread was created against; only its line
    numbers may have moved, so the range is the relocated one. This is the
    single text placement that reports nothing wrong.
    """

    kind: Literal["region-kept"]
    """Identify a placement whose original bytes matched uniquely.

    Consumers may navigate to `range` without presenting an outdated warning.
    The literal does not claim the line numbers stayed unchanged.
    """

    range: ReviewLineRange
    """Current inclusive range containing the exact origin bytes.

    Coordinates are local to the origin bay and side. They may differ from the
    origin range when surrounding content moved.
    """


class ThreadRegionChangedPlacementResponse(ApiModel):
    """Locate one Thread on the first line of its changed region.

    The origin's structural container matched uniquely but its bytes differ,
    so browser navigation uses the one-line range where the reviewed region now
    begins. It does not claim that the original line range still exists.
    """

    kind: Literal["region-changed"]
    """Identify a structurally matched region with changed source bytes.

    Consumers present it as outdated but may still navigate to `range`. The
    variant promises one identified landing, not an exact content match.
    """

    range: ReviewLineRange
    """Single-line landing at the current changed region's start.

    Both endpoints are equal. The value deliberately does not reproduce the
    origin range because those source lines no longer match.
    """


class ThreadRegionLostPlacementResponse(ApiModel):
    """Locate one Thread at the start of its own bay, its region unmatched.

    The bay the Thread was created in still composes, but the region inside it
    matched nothing or matched ambiguously, so no line can be named. The bay
    is the origin's, which is why this states no coordinate of its own.
    """

    kind: Literal["region-lost"]
    """Identify an unmatched region whose origin bay still exists.

    The origin supplies the bay and side; consumers land at that bay's first
    line. This variant must not invent or expose a more precise range.
    """


class ThreadBayLostPlacementResponse(ApiModel):
    """Locate one Thread at the start of a bay that is not its own.

    The origin's bay no longer composes, so derivation chose the File's first
    composed bay carrying the origin's side and stored that choice. This is
    the only placement that states a bay, because it is the only one whose
    bay differs from the origin's.
    """

    kind: Literal["bay-lost"]
    """Identify a missing origin bay with a stored replacement landing.

    Consumers use `bay` on the origin side and land at its first line. The
    literal records loss and must remain visible even though navigation works.
    """

    bay: ReviewTextBayModel
    """Replacement bay selected when the origin bay disappeared.

    Derivation stores the first composed bay carrying the origin side. Reads
    publish that exact key; clients must not recompute or replace the choice.
    """


class ThreadSideLostPlacementResponse(ApiModel):
    """Locate one Thread on its File, no composed bay carrying its side.

    The File composes, but nothing it composes carries the origin's side, so
    there is no bay to land on. History can display the discussion through its
    origin, but navigation must not invent a bay or line coordinate.
    """

    kind: Literal["side-lost"]
    """Identify a present File with no bay carrying the origin side.

    The placement has no navigable bay or range. Consumers retain the Thread in
    History and must not borrow a coordinate from the other side.
    """


class ThreadFileAbsentPlacementResponse(ApiModel):
    """State that the Thread's exact File pair is absent from this Snapshot.

    The read that loads placement Files proves that absence, so this is an
    invariant rather than a stand-in for a File that failed to load. History may
    display the origin, but no current File or code coordinate exists.
    """

    kind: Literal["file-absent"]
    """Identify an origin File pair missing from the selected Snapshot.

    No current File, bay, or line coordinate exists. Consumers may display the
    immutable origin but must disable code navigation.
    """


class ThreadFileUnreadablePlacementResponse(ApiModel):
    """State that the Thread's File is present and could not be captured.

    The capture retains dirdiff's placeholder text rather than the File's own
    bytes, so every coordinate the File could offer would describe something
    dirdiff wrote. Never navigable.
    """

    kind: Literal["file-unreadable"]
    """Identify a listed File whose captured content is not usable.

    The File differs from `file-absent`, but dirdiff's placeholder is not a
    valid review coordinate. Consumers must not navigate to its bays or lines.
    """


class ThreadWholeFilePlacementResponse(ApiModel):
    """Locate one retained historical File-level Thread on its File.

    Only a `file-start` origin takes this shape, and it takes it in every
    Snapshot holding the File pair: such a Thread names no bay in the first
    place, so nothing about it can go outdated. Never navigable.
    """

    kind: Literal["whole-file"]
    """Identify a retained origin that names a File side without a bay.

    Only migrated historical Threads have this shape. It is never a creation
    target or line-navigation coordinate, even when the File is present.
    """


ThreadPlacementResponse = Annotated[
    ThreadRegionKeptPlacementResponse
    | ThreadRegionChangedPlacementResponse
    | ThreadRegionLostPlacementResponse
    | ThreadBayLostPlacementResponse
    | ThreadSideLostPlacementResponse
    | ThreadFileAbsentPlacementResponse
    | ThreadFileUnreadablePlacementResponse
    | ThreadWholeFilePlacementResponse,
    Field(discriminator="kind"),
]
"""Return where one Thread sits in one Snapshot, and what became of it.

Derivation returns exactly one placement:

- `region-kept` and `region-changed` carry a usable line range.
- `region-lost` lands at the origin bay's start.
- `bay-lost` names a replacement bay on the origin side.
- `side-lost`, `file-absent`, and `file-unreadable` have no code coordinate.
- `whole-file` retains a historical File-level origin.

The File pair and side always come from the immutable origin; the bay also comes
from the origin except for `bay-lost`. Consumers must not infer a more precise
coordinate than the selected variant provides.
"""


class TextReviewOriginResponse(TextReviewTarget):
    """Return the text target a Thread was created against, with its excerpt.

    Creation accepts `TextReviewTarget`; only a read can carry the excerpt cut
    from the origin Snapshot's captured bytes, so the response states its own
    model. A File-level origin has no excerpt field at all, which makes "only
    a text origin has an excerpt" a fact of the shape rather than a rule.
    """

    excerpt: ReviewExcerptResponse
    """Source context reconstructed from the immutable origin Snapshot.

    Its side and selected range agree with the inherited target fields. The
    excerpt is read data only and never changes the Thread's stored origin.
    """


class FileStartReviewOriginResponse(ApiModel):
    """Return an immutable File-level origin retained for existing Threads.

    Browser reads use its File pair and side to display those discussions.
    Creation routes accept only text targets, so callers cannot construct new
    Threads with this shape or infer a bay coordinate from it.
    """

    kind: Literal["file-start"]
    """Identify a retained origin created before line targets were required.

    Current creation routes reject this literal. Readers use it to preserve
    history without fabricating a bay, range, or excerpt.
    """

    file: ReviewFilePairModel
    """Immutable File pair against which the historical Thread began.

    At least one side exists and `side` selects a present path. The pair remains
    the origin even when current placement reports the File absent.
    """

    side: Literal["left", "right"]
    """Present side selected by the historical File-level origin.

    It is immutable and agrees with `file`. Consumers must not infer a bay or
    line range from it.
    """


ReviewOriginResponse = Annotated[
    TextReviewOriginResponse | FileStartReviewOriginResponse,
    Field(discriminator="kind"),
]
"""Return the immutable creation target of one Thread.

- `TextReviewOriginResponse` names a File pair, bay, side, range, and excerpt.
- `FileStartReviewOriginResponse` retains a File-and-side target without a bay.

This is the only response value that states the Thread's File pair and side.
Readers combine it with placement, which must not repeat or contradict those
immutable facts.
"""


class ReviewThreadResponse(ApiModel):
    """Return one complete live discussion through one exact Snapshot.

    `origin_target` states what the Thread was written against and never
    changes; `placement` states where that landed in this Snapshot and what
    became of it. Nothing is stated twice, so no combination of the two needs
    proving here.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable identity shared by every Snapshot placement of the discussion.

    The backend generates this lowercase hexadecimal id at creation. Clients
    retain it for later Thread actions and must not derive it from location.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Captured code universe used to interpret `placement`.

    The value addresses one placement of `thread_id`; origin and Comments stay
    logical Thread facts even when another Snapshot places them differently.
    """

    created_at: datetime
    """Timestamp of the sequence-zero action that created the Thread.

    It is independent of Snapshot placement and remains fixed across later
    Comments, lifecycle actions, and recaptures.
    """

    state: Literal["open", "resolved", "deleted"]
    """Current Thread lifecycle at the page's activity boundary.

    The value includes actions through `discussion_revision`. Deleted is
    terminal; callers must not treat a resolved Thread as deleted.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Role attention outcome at the same boundary as `state`.

    It records whom the latest accepted action directs to respond. `none` is a
    real outcome and must not be replaced with an inferred participant.
    """

    discussion_revision: int = Field(ge=0)
    """Per-Thread sequence of the latest action included in this response.

    It starts at zero and advances contiguously. The HUD uses it to determine
    whether a mutation result can update its cached discussion directly.
    """

    origin_target: ReviewOriginResponse
    """Immutable code or historical File target that began the Thread.

    It is the sole source of the origin File pair and side. Consumers combine
    it with `placement` without expecting either value to repeat those facts.
    """

    placement: ThreadPlacementResponse
    """Stored result of placing the origin into `snapshot_id`.

    The selected variant supplies only the current facts it can prove. Callers
    must not infer a bay or range for variants that omit one.
    """

    comments: list[ReviewCommentResponse] = Field(min_length=1)
    """Complete current Comment sequence for the Thread.

    The non-empty list starts with the creation Comment and stays in ascending
    `sequence` order. Tombstones retain their position instead of disappearing.
    """


class ReviewThreadUpdateResponse(ApiModel):
    """Return authoritative Thread state immediately after one browser action.

    Mutation routes use this compact response instead of rebuilding the complete
    discussion. Revision, lifecycle, and attention are current after the write;
    `comment` contains the affected Comment when one exists.

    It does not include origin, placement, or unrelated Comments.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable identity of the discussion changed by the accepted action.

    It matches the request's existing Thread or the id generated for a new
    Thread. The response never substitutes a code-location identity.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot placement through which the write was accepted.

    This echoes the action boundary so a client can reject an update for a
    differently mounted Snapshot without a follow-up read.
    """

    state: Literal["open", "resolved", "deleted"]
    """Authoritative lifecycle outcome after applying the action.

    It includes this write. A client replaces older cached state only when the
    returned discussion revision is contiguous with that cache.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Authoritative role-attention outcome after applying the action.

    It is persisted with the same action as `state`. Clients must not derive it
    again from action kind or Comment author.
    """

    discussion_revision: int = Field(ge=0)
    """Per-Thread sequence assigned to the accepted action.

    A contiguous value follows the cached revision by one. A gap tells the HUD
    to refetch because unseen actions exist.
    """

    comment: ReviewCommentResponse | None
    """Current Comment affected by the accepted action, when one exists.

    Comment writes and lifecycle actions with a body return it. `None` is
    reserved for a lifecycle action that created no Comment.
    """


class ReviewThreadPage(ApiModel):
    """Return one activity-bounded page of discussions in a Snapshot.

    The first read fixes `through_activity_id`; callers repeat it for later
    pages so concurrent actions do not move the boundary. `total_threads` is
    measured at that same boundary.

    The response does not claim the Snapshot or discussions remain unchanged
    after the read.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Captured code universe containing every returned placement.

    All page items are interpreted against this exact key. Callers must not mix
    pages from another Snapshot into the same canonical Thread set.
    """

    through_activity_id: int = Field(ge=0)
    """Inclusive Room activity boundary chosen for this paged read.

    The first page establishes it. Callers repeat the exact value on later pages
    so concurrent writes cannot change membership, state, or totals mid-read.
    """

    threads: list[ReviewThreadResponse]
    """Complete discussions selected for this page and activity boundary.

    Entries follow stable lifecycle and persistence ordering. An empty list is
    valid past the end and does not mean the Snapshot has no Threads.
    """

    page: int = Field(ge=1)
    """One-based page number represented by `threads`.

    It echoes the validated query parameter and is meaningful with `limit` and
    the fixed `through_activity_id`.
    """

    limit: int = Field(ge=1)
    """Maximum number of discussions selected for this page.

    The final page may contain fewer entries without changing this echoed
    positive bound.
    """

    total_threads: int = Field(ge=0)
    """Count of discussions matching the query at the activity boundary.

    The value covers every page, not merely `threads`, and stays stable when the
    caller repeats the same pivot.
    """

    has_more: bool
    """Whether the fixed result contains entries after this page.

    True instructs the caller to fetch `page + 1` with the same Snapshot,
    filters, limit, and `through_activity_id`.
    """


class ReviewErrorResponse(ApiModel):
    """Return one expected browser review failure.

    The exception handler validates `ReviewError.code` and its human-readable
    message through this model before writing JSON.

    It does not expose tracebacks or represent non-review route failures.
    """

    code: ReviewErrorCode
    """Stable review-domain failure category for client branching.

    The HUD compares this typed value rather than parsing `message`. It does not
    classify validation failures or unexpected server exceptions.
    """

    message: str = Field(min_length=1)
    """Concrete non-empty explanation suitable for direct HUD display.

    Clients may show it but must not depend on its wording for behavior. The
    handler does not include tracebacks or private persistence details.
    """


class _ReviewHttpException(Exception):
    """Carry a mapped review-domain failure to the FastAPI handler.

    Route methods construct this private exception from `ReviewError`; the
    handler reads its HTTP status and already-validated response model.

    It never crosses the HTTP boundary and must not wrap unexpected exceptions.
    """

    def __init__(self, status: HTTPStatus, error: ReviewError) -> None:
        """Bind one expected review failure to its validated HTTP response.

        # Parameters

        - `status`: Browser review status selected from the stable error code.
        - `error`: Domain failure whose code and message form the response body.
        """
        super().__init__(str(error))
        self.status = status
        self.response = ReviewErrorResponse(
            code=error.code,
            message=str(error),
        )


_REVIEW_ERROR_RESPONSES: Responses = {
    HTTPStatus.BAD_REQUEST: {"model": ReviewErrorResponse},
    HTTPStatus.NOT_FOUND: {"model": ReviewErrorResponse},
    HTTPStatus.FORBIDDEN: {"model": ReviewErrorResponse},
    HTTPStatus.CONFLICT: {"model": ReviewErrorResponse},
}
"""OpenAPI metadata shared by every browser review write route.

Runtime handlers validate the same response model before serialization. The
mapping advertises only expected domain statuses; unexpected failures keep the
generic application-boundary response.
"""


class ReviewRoutes:
    """Bind browser review handlers to one Room service.

    One instance retains the RoomLord used to locate the exact Snapshot for
    each HTTP entity. Its class-local declarations bind only browser review
    routes and the typed review failure handler.
    """

    routes = ClassRoutes()
    """Import-time declarations bound to one route-group instance."""

    def __init__(self, room_lord: RoomLord) -> None:
        """Retain the Room lookup interface used by browser review handlers."""
        self.room_lord = room_lord

    def review_http_exception(self, error: ReviewError) -> _ReviewHttpException:
        """Map one typed domain failure to the browser review HTTP contract.

        Missing entities become 404, denied authorship becomes 403, stale state
        becomes 409, and invalid targets become 400. The validated error body is
        preserved without FastAPI's ordinary `detail` wrapper for the registered
        handler to serialize.

        # Parameters

        - `error`: Domain code and safe message raised by Room review operations.

        # Usage

        Browser review routes catch `ReviewError` and raise this mapped value so
        the registered handler can preserve the typed response body.

        """
        if error.code in {
            "profile_not_found",
            "thread_not_found",
            "comment_not_found",
        }:
            status = HTTPStatus.NOT_FOUND
        elif error.code == "forbidden":
            status = HTTPStatus.FORBIDDEN
        elif error.code in {
            "revision_conflict",
            "state_conflict",
        }:
            status = HTTPStatus.CONFLICT
        else:
            assert error.code == "invalid_target"
            status = HTTPStatus.BAD_REQUEST
        return _ReviewHttpException(status, error)

    @routes.exception_handler(_ReviewHttpException)
    async def serve_review_error(
        self,
        request: Request,
        error: _ReviewHttpException,
    ) -> JSONResponse:
        """Serialize one typed review failure without an HTTP detail wrapper.

        # Parameters

        - `request`: FastAPI request context; this handler needs no request data.
        - `error`: Already mapped status and validated review response.

        # Usage

        FastAPI invokes this registered handler for `_ReviewHttpException`.

        """
        del request
        return JSONResponse(
            status_code=error.status,
            content=error.response.model_dump(mode="json"),
        )

    @routes.get(
        "/api/review/threads",
        response_model=ReviewThreadPage,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Read one page of review Threads",
    )
    def serve_review(
        self,
        snapshot_id: UUID = Query(description="Exact retained Snapshot id."),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
        through_activity_id: int | None = Query(default=None, ge=0),
    ) -> ReviewThreadPage:
        """Return one stable complete-Thread page for the History UI.

        Page one chooses the append-only review activity pivot. Later pages
        must repeat it so lifecycle changes cannot reorder the paged read.

        # Parameters

        - `snapshot_id`: Exact Snapshot whose placements History displays.
        - `page`: Positive one-based Thread page.
        - `limit`: Maximum Threads returned, capped for the browser boundary.
        - `through_activity_id`: Absent on page one; concrete inclusive pivot
          returned there and required unchanged on later pages.

        # Failures

        - Returns a typed review error for an unknown Snapshot or invalid page
          pivot. Unexpected persistence or placement damage propagates to the
          application error handler.
        """
        try:
            room = self.room_lord.find_room(snapshot_id)
            if page == 1:
                if through_activity_id is not None:
                    raise ReviewError(
                        "invalid_target",
                        "First review page chooses its activity pivot.",
                    )
            elif through_activity_id is None:
                raise ReviewError(
                    "invalid_target",
                    "Later review pages require the first page activity pivot.",
                )
            threads, total, through_activity_id = room.threads(
                snapshot_id,
                page=page,
                limit=limit,
                state="all",
                through_activity_id=through_activity_id,
            )
            return ReviewThreadPage(
                snapshot_id=snapshot_id.hex,
                through_activity_id=through_activity_id,
                threads=[
                    ReviewThreadResponse.model_validate(thread.discussion())
                    for thread in threads
                ],
                page=page,
                limit=limit,
                total_threads=total,
                has_more=page * limit < total,
            )
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    def review_target(self, request: NewCodeCommentRequest) -> TextTarget:
        """Translate one validated browser code target to Thread input.

        Pydantic has already checked path, bay, side, and ordered line fields;
        this helper preserves them exactly in the domain types used by Room target
        validation. It performs no File lookup and invents no coordinate.

        # Parameters

        - `request`: New-Thread HTTP entity carrying one complete text target.

        # Failures

        - Domain constructors may reject coordinates that contradict the
          validated File pair. The calling route maps that rejection as an
          invalid review target.
        """
        file = FilePair(
            request.target.file.left_path, request.target.file.right_path
        )
        return TextTarget(
            file,
            request.target.bay.bay_key,
            request.target.side,
            LineRange(
                request.target.range.start_line,
                request.target.range.end_line,
            ),
        )

    @routes.post(
        "/api/review/post_comment",
        response_model=ReviewThreadResponse | ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Post one review Comment",
    )
    def post_review_comment(
        self,
        request: PostCommentRequest,
    ) -> ReviewThreadResponse | ReviewThreadUpdateResponse:
        """Start one Thread or append one Comment to an existing Thread.

        New-code input allocates fresh Thread, operation, and Comment identities and
        validates its target against the exact Snapshot. Reply input addresses one
        existing Thread and applies the requested attention result. The endpoint
        returns the complete created discussion or one contiguous Thread update;
        typed review failures retain their browser status contract.

        # Parameters

        - `request`: Discriminated new-Thread or reply entity already validated by FastAPI.

        # Returns

        - `ReviewThreadResponse` with the complete new discussion when the input
          starts a Thread.
        - `ReviewThreadUpdateResponse` with the contiguous appended update when
          the input replies to an existing Thread.

        # Failures

        - Returns the typed browser review error for a missing Profile, Snapshot,
          Thread, or target; reused identities; forbidden state; or invalid
          Comment input.
        """
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = self.room_lord.find_room(snapshot_id)
            if isinstance(request, NewCodeCommentRequest):
                thread = room.create_thread(
                    snapshot_id,
                    CreateThread(
                        uuid4(),
                        uuid4(),
                        uuid4(),
                        ProfileAuthor(request.profile_id),
                        self.review_target(request),
                        request.body,
                    ),
                )
                return ReviewThreadResponse.model_validate(thread.discussion())
            update = room.get_thread(
                snapshot_id, UUID(hex=request.thread_id)
            ).add_comment(
                AddComment(
                    uuid4(),
                    uuid4(),
                    ProfileAuthor(request.profile_id),
                    request.body,
                ),
                attention=request.attention,
            )
            return ReviewThreadUpdateResponse.model_validate(update)
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @routes.post(
        "/api/review/edit_comment",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Edit one review Comment",
    )
    def edit_review_comment(
        self,
        request: EditReviewCommentRequest,
    ) -> ReviewThreadUpdateResponse:
        """Edit one authored Comment using the backend's current revision.

        The exact Snapshot and Comment locate their Thread; the selected Profile
        must be the author, and the Room applies the edit only at its current
        revision. The response is the single canonical Thread update; missing,
        forbidden, or conflicting writes use the typed review error mapping.

        # Parameters

        - `request`: Snapshot, Comment, acting Profile, and non-empty replacement body.

        # Failures

        - Returns the typed browser review error when the Profile, Snapshot, or
          Comment is missing, the actor is not the author, the Thread is deleted,
          or the operation id conflicts.
        """
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = self.room_lord.find_room(snapshot_id)
            comment_id = UUID(hex=request.comment_id)
            return ReviewThreadUpdateResponse.model_validate(
                room.thread_for_comment(snapshot_id, comment_id).edit_comment(
                    comment_id,
                    EditComment(
                        uuid4(),
                        ProfileAuthor(request.profile_id),
                        request.body,
                    ),
                )
            )
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @routes.post(
        "/api/review/delete_comment",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Delete one review Comment",
    )
    def delete_review_comment(
        self,
        request: DeleteReviewCommentRequest,
    ) -> ReviewThreadUpdateResponse:
        """Tombstone one Comment and retain its acting Profile in the action log.

        The endpoint locates the containing Thread at the requested Snapshot and
        records a deletion operation instead of removing history. The returned
        contiguous update carries the tombstone; missing or conflicting state is
        translated through the same browser review error boundary.

        # Parameters

        - `request`: Exact Snapshot, Comment identity, and acting Profile identity.

        # Failures

        - Returns the typed browser review error when an addressed entity is
          missing, the Comment or Thread is already deleted, or the operation id
          conflicts.
        """
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = self.room_lord.find_room(snapshot_id)
            comment_id = UUID(hex=request.comment_id)
            return ReviewThreadUpdateResponse.model_validate(
                room.thread_for_comment(snapshot_id, comment_id).delete_comment(
                    comment_id,
                    DeleteComment(uuid4(), ProfileAuthor(request.profile_id)),
                )
            )
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    def change_review_thread_state(
        self,
        *,
        request: ChangeReviewThreadStateRequest | DeleteReviewThreadRequest,
        action: Literal["resolve", "reopen", "delete"],
    ) -> ReviewThreadUpdateResponse:
        """Apply one exact lifecycle operation shared by three HTTP routes.

        # Parameters

        - `request`: Validated Snapshot, Thread, Profile, and optional Comment
          body for the chosen route.
        - `action`: Resolve, reopen, or terminal deletion instrument.

        # Failures

        - Returns the typed browser review error when an addressed entity is
          missing, current lifecycle does not permit `action`, explanation input
          is invalid, or an operation identity conflicts.
        """
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            thread_id = UUID(hex=request.thread_id)
            room = self.room_lord.find_room(snapshot_id)
            thread = room.get_thread(snapshot_id, thread_id)
            # Only resolve and reopen requests can carry an explanation
            # Comment; deletion has no body field and never creates one.
            body = (
                request.body
                if isinstance(request, ChangeReviewThreadStateRequest)
                else None
            )
            command = ChangeThreadState(
                uuid4(),
                ProfileAuthor(request.profile_id),
                uuid4() if body is not None else None,
                body,
            )
            if action == "resolve":
                updated = thread.resolve(command)
            elif action == "reopen":
                updated = thread.reopen(command)
            else:
                updated = thread.delete(command)
            return ReviewThreadUpdateResponse.model_validate(updated)
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @routes.post(
        "/api/review/resolve_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Resolve one review Thread",
    )
    def resolve_review_thread(
        self,
        request: ChangeReviewThreadStateRequest,
    ) -> ReviewThreadUpdateResponse:
        """Resolve an open Thread at its exact current revision.

        This route selects only the resolve transition and delegates identity,
        Profile, current-state, and revision validation to the shared state-change
        boundary. It returns that boundary's canonical update and never reopens or deletes.

        # Parameters

        - `request`: Snapshot, Thread, and acting Profile for the resolve action.

        # Failures

        - Returns the typed review error produced by the shared lifecycle
          boundary when resolution cannot apply.
        """
        return self.change_review_thread_state(
            request=request,
            action="resolve",
        )

    @routes.post(
        "/api/review/reopen_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Reopen one review Thread",
    )
    def reopen_review_thread(
        self,
        request: ChangeReviewThreadStateRequest,
    ) -> ReviewThreadUpdateResponse:
        """Reopen a resolved Thread at its exact current revision.

        This route selects only the reopen transition; the shared boundary rejects
        other current states and maps typed failures. The returned update is the
        accepted lifecycle result, not a locally inferred Thread copy.

        # Parameters

        - `request`: Snapshot, Thread, and acting Profile for the reopen action.

        # Failures

        - Returns the typed review error produced by the shared lifecycle
          boundary when reopening cannot apply.
        """
        return self.change_review_thread_state(
            request=request,
            action="reopen",
        )

    @routes.post(
        "/api/review/delete_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Delete one review Thread",
    )
    def delete_review_thread(
        self,
        request: DeleteReviewThreadRequest,
    ) -> ReviewThreadUpdateResponse:
        """Record terminal Thread deletion at its exact current revision.

        The shared state-change boundary records the terminal lifecycle event while
        retaining the discussion for History. It rejects stale or already invalid
        state and returns the canonical update after persistence.

        # Parameters

        - `request`: Snapshot, Thread, and acting Profile for terminal deletion.

        # Failures

        - Returns the typed review error produced by the shared lifecycle
          boundary when terminal deletion cannot apply.
        """
        return self.change_review_thread_state(
            request=request,
            action="delete",
        )
