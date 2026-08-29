"""Define dirdiff HTTP entities and construct the FastAPI application.

## Public interface

`create_app` binds concrete stores and one Room service to a fresh application.
`uvicorn_entrypoint` constructs those dependencies from the startup contract in
`dirdiff.server.base`. The response models and branch-selection conversions in
this module are the JSON contracts used by the HUD and agent endpoints.

## Purpose and boundaries

Routes validate HTTP input, coordinate backend capture or Snapshot reads, and
translate domain values into HTTP responses. Typed review failures retain their
stable status and error entity; unexpected failures stop at the application
handler, which logs the traceback before returning the generic response.

This module does not implement persistence, Thread placement, workspace
backends, diff algorithms, format composition, or class-route collection.
"""

import json
import logging
import os
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import (
    Annotated,
    Literal,
    Optional,
    Self,
)
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from dirdiff.backend import (
    BranchSelection,
    BranchSource,
    DefaultBaseSelection,
    GitBackend,
    LazyReason,
    PreparedPullRequest,
    PresetBackend,
    PresetCatalogDir,
    RefChoices,
    RepoDiffPath,
    WorkspaceBackendProtocol,
    build_lazy_info_for_paths,
    build_repo_manifest_for_paths,
    display_name_for_repo_paths,
    file_kind_for_change_type,
    preferred_review_selection,
    prepare_pull_request,
    preset_catalogs,
    ref_choices,
)
from dirdiff.db import (
    PreferencesStore,
    RepoMainBranchRecord,
    RepoMarkStore,
    RoomStore,
    UserProfileStore,
    open_sqlite_engine,
)
from dirdiff.engines import (
    DirdiffError,
    EngineKind,
    InlineTokenStatus,
    engine,
)
from dirdiff.formats import (
    BayContext,
    ComposeContext,
    Composer,
    ImageBay,
)
from dirdiff.rendering import (
    SyntaxClass,
)
from dirdiff.review import (
    AddComment,
    ChangeThreadState,
    CreateThread,
    DeleteComment,
    DeleteThread,
    EditComment,
    FilePair,
    LineRange,
    ProfileAuthor,
    ReplyToThread,
    ResolveThread,
    ReviewError,
    ReviewErrorCode,
    ReviewOriginView,
    TextTarget,
    ThreadDiscussionView,
    ThreadPlacementView,
    ThreadSummaryView,
)
from dirdiff.room_lord import (
    BranchReviewCaptureSelection,
    CaptureSelection,
    FileMeta,
    PresetCaptureSelection,
    PullRequestCaptureSelection,
    RevisionsCaptureSelection,
    Room,
    RoomLord,
)
from dirdiff.server.base import (
    RUNTIME_CONFIG_ENV,
    Responses,
    RuntimeConfig,
)
from dirdiff.server.magic import ClassRoutes

__all__ = [
    "ComposedDiffResponse",
    "branch_selection_request_to_selection",
    "create_app",
    "repo_main_branch_record_to_selection",
    "selected_branch_selections",
    "uvicorn_entrypoint",
]


LOGGER = logging.getLogger(__name__)
"""Module logger for unexpected HTTP failures and rejected persistence damage.

Application-boundary handlers attach the HTTP method, path, and traceback.
Expected domain failures take their typed response path and do not use it.
"""

_AGENT_ROUTE_PATHS = frozenset(
    {
        "/api/agent/join_review",
        "/api/agent/thread_summary",
        "/api/agent/threads",
        "/api/agent/thread/{thread_id}",
        "/api/agent/continue_review",
        "/api/agent/actions",
    }
)
"""Exact endpoint templates whose failures use the agent plain-text contract.

The validation and HTTP exception handlers compare Starlette's matched route
template, not the concrete URL, so parameterized Thread paths remain covered.
"""

TabParam = Literal[
    "head",
    "refs",
    "branch-review",
    "pull-request",
    "preset",
]
"""Select the manifest correspondence and capture flow used by the HUD.

- `head` compares HEAD with the worktree.
- `refs` compares two explicit sides.
- `branch-review` resolves symbolic base and review branches.
- `pull-request` uses prepared immutable commits.
- `preset` opens one fixture group.

Route validation uses this value with the parameters required by the selected
Tab. It is not a persisted Room identity by itself.
"""
BranchSourceParam = BranchSource
"""Select local or remote branch parameters at the HTTP boundary.

`selected_branch_selections` combines this discriminator with the matching
branch and remote parameters. It does not carry either name by itself.
"""
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
"""Validate backend File relationships before HTTP conversion.

- `modify` keeps one File path relationship.
- `add` and `delete` are one-sided.
- `rename` and `copy` retain cross-path provenance.

This value describes File identity, not rendered line status.
"""
BranchSelections = tuple[BranchSelection | None, BranchSelection | None]
"""Validated base and review selections in that order.

Both entries are present only for the Branch Review Tab. Other Tabs return two
`None` values so callers can unpack the result without inventing selections.
"""


class ApiModel(BaseModel):
    """Strict base for every validated HTTP entity defined by this module.

    Every request and response model in this module subclasses `ApiModel`.
    FastAPI validates incoming JSON through it, and route code validates
    outgoing domain dictionaries before returning them.

    Models reject unknown fields, coercion, invalid defaults, and non-finite
    numbers. This base adds no shared entity fields or application behavior.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )


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


class ReviewExcerptResponse(ApiModel):
    """Return bounded origin context around one selected text range.

    Full discussion reads attach this to text origins. `start_line` numbers the
    first returned source line; the selected inclusive range must lie inside the
    returned context.

    Coordinates belong to the origin bay side, not the current placement or
    rendered rows.
    """

    side: Literal["left", "right"]
    """Immutable origin side from which the excerpt was reconstructed.

    Every item in `lines` belongs to this same side. The value does not describe
    the Thread's current placement side separately.
    """

    start_line: int = Field(ge=1)
    """One-based source coordinate of the first returned line.

    It may precede `selected_start_line` by at most the bounded context. Adding
    a list offset yields the source coordinate for that item.
    """

    selected_start_line: int = Field(ge=1)
    """First origin line the author selected.

    The coordinate is inclusive and must fall within `lines`, no earlier than
    `start_line` and no later than `selected_end_line`.
    """

    selected_end_line: int = Field(ge=1)
    """Last origin line the author selected.

    The coordinate is inclusive and must fall within the returned excerpt. It
    may equal `selected_start_line` for a one-line target.
    """

    lines: list[str] = Field(min_length=1)
    """Exact origin source lines in ascending order.

    The list contains the complete selected range plus at most three context
    lines before and after it. It is never empty and is not rendered diff text.
    """

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        """Require the complete selected range to lie inside the excerpt.

        The check relates the one-based coordinates to `lines` length and also
        enforces selected endpoint ordering. It returns the excerpt unchanged.

        # Usage

        Pydantic invokes this callback after validating excerpt coordinates and
        lines. Callers validate `ReviewExcerptResponse` as one complete value.

        # Failures

        - Raises `ValueError` when the selected range is reversed or extends
          beyond the returned excerpt.
        """
        excerpt_end = self.start_line + len(self.lines) - 1
        if not (
            self.start_line
            <= self.selected_start_line
            <= self.selected_end_line
            <= excerpt_end
        ):
            raise ValueError("Selected review range exceeds its excerpt.")
        return self


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


class AgentBranch(ApiModel):
    """Name one symbolic branch in an agent Branch Review Tab.

    `remote=None` selects a local branch; a remote name keeps the two parts
    structured for backend resolution.

    The value is not an arbitrary Git ref or resolved commit.
    """

    remote: str | None
    """Remote namespace containing `name`, when one is selected.

    `None` makes the branch local. A present value is passed separately to the
    backend and must not be joined with the branch into an arbitrary ref string.
    """

    name: str = Field(min_length=1)
    """Symbolic branch name within the selected local or remote namespace.

    It must be non-empty. Resolution happens during capture; this value does
    not promise an immutable commit or accept an absent branch.
    """


class AgentHeadTab(ApiModel):
    """Ask the agent API to capture HEAD against the worktree.

    Join-review validates `repo_path` against the registry and applies the HEAD
    Tab correspondence law.

    The model carries no explicit refs or Snapshot identity.
    """

    kind: Literal["head"]
    """Select HEAD-versus-worktree capture for agent review.

    Only the literal `head` is valid. It makes `repo_path` the complete Tab
    input, with no caller-supplied refs.
    """

    repo_path: str = Field(min_length=1)
    """Exact workspace path of an already active repository mark.

    Join review looks it up without creating a mark. The non-empty string must
    match the stored path; a merely readable unregistered directory is invalid.
    """


class AgentRefsTab(ApiModel):
    """Ask the agent API to capture two explicit backend sides.

    Join-review validates the marked repository and normalizes `left` and
    `right` through its backend before capture.

    The values are side names, not display labels or guaranteed commits.
    """

    kind: Literal["refs"]
    """Select capture of the two explicitly named backend sides.

    Only the literal `refs` is valid. It requires both `left` and `right` and
    does not apply Branch Review merge-base semantics.
    """

    repo_path: str = Field(min_length=1)
    """Exact workspace path of an already active repository mark.

    Join review looks it up without creating a mark. The path selects the
    backend that normalizes both side names.
    """

    left: str = Field(min_length=1)
    """Non-empty backend side name for the left capture input.

    The selected repository backend normalizes and loads it. The caller must not
    assume the value is already an immutable commit.
    """

    right: str = Field(min_length=1)
    """Non-empty backend side name for the right capture input.

    It is interpreted by the same backend as `left`; the ordered pair defines
    the comparison and must not be reversed during continuation.
    """


class AgentBranchReviewTab(ApiModel):
    """Ask the agent API to capture one Branch Review comparison.

    Join-review resolves the structured base and review branches to immutable
    commits, then applies Branch Review correspondence.

    This request keeps symbolic selections only; it does not let callers supply
    arbitrary merge-base commits.
    """

    kind: Literal["branch-review"]
    """Select Branch Review resolution and capture.

    Only the literal `branch-review` is valid. It requires structured `base`
    and `review` selections and applies their merge-base comparison law.
    """

    repo_path: str = Field(min_length=1)
    """Exact workspace path of an already active repository mark.

    The mark selects the backend that resolves both symbolic branch selections.
    Join review does not register an unmarked path.
    """

    base: AgentBranch
    """Symbolic base branch used to establish the comparison boundary.

    Capture resolves it together with `review`; callers provide no merge-base
    commit and must retain the symbolic selection for later recapture.
    """

    review: AgentBranch
    """Symbolic branch containing the changes under review.

    Capture resolves it with `base` and retains the structured selection as
    part of the Tab context. It is not a presentation label.
    """


class AgentPullRequestTab(ApiModel):
    """Ask the agent API to prepare and capture one Pull Request.

    Join-review matches the URL to a marked repository, fetches the supported
    forge refs, and captures the prepared immutable commits.

    The model does not accept a repository path or caller-supplied commits.
    """

    kind: Literal["pull-request"]
    """Select forge Pull Request preparation before capture.

    Only the literal `pull-request` is valid. The URL supplies repository and
    refs, so this variant accepts no workspace path or caller-selected commits.
    """

    url: str = Field(min_length=1)
    """Pull Request or Merge Request URL to prepare for review.

    Preparation canonicalizes it, matches its base repository to an active
    mark, fetches the required refs, and returns immutable capture commits.
    """


AgentReviewTab = Annotated[
    AgentHeadTab | AgentRefsTab | AgentBranchReviewTab | AgentPullRequestTab,
    Field(discriminator="kind"),
]
"""Describe one complete Tab the agent API can capture.

- `AgentHeadTab` compares HEAD and worktree.
- `AgentRefsTab` compares explicit sides.
- `AgentBranchReviewTab` resolves symbolic branches.
- `AgentPullRequestTab` prepares a supported forge URL.

Join and continuation persist this logical context. Preset Tabs are not part of
the current agent boundary.
"""


class NewAgentReviewRequest(ApiModel):
    """Start one agent review session and capture its initial Tab.

    The agent supplies a fresh registration UUID, display name, and complete Tab
    context. The route creates an ordinary Profile and returns retained Snapshot
    information.

    Reusing the UUID is invalid. The request does not grant a reviewer role or
    preserve process-local agent state.
    """

    agent_uuid: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Agent-supplied registration identity for the new ordinary Profile.

    It must be 32 lowercase hexadecimal characters and unused. Repeating a
    prior value is an error rather than a way to resume that registration.
    """

    name: str = Field(min_length=1)
    """Username assigned to the newly registered Profile.

    It must be unique, nonblank, and unchanged by trimming. Later reads expose
    the current Profile name, while writes use the returned numeric id.
    """

    tab: AgentReviewTab
    """Logical repository-backed review mode to capture and persist.

    Join review records this exact context with the Room. Continuation recaptures
    it rather than accepting replacement Tab parameters.
    """

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Accept an exact nonblank Profile name or reject the registration.

        The validator deliberately returns `value` unchanged. It does not trim
        input because silent normalization would make the registered name differ
        from the agent's request.

        # Usage

        Pydantic invokes this callback while validating the `name` field of
        `NewAgentReviewRequest`. Callers construct the request model instead of
        calling it directly.

        # Failures

        - Raises `ValueError` when the name is blank or has surrounding
          whitespace.
        """
        if value != value.strip() or value.strip() == "":
            raise ValueError("Invalid agent name.")
        return value


class NewAgentReviewResponse(ApiModel):
    """Return the retained facts needed to continue one agent review.

    The agent stores `profile_id`, Snapshot id and path, and activity boundary
    for later API calls. Attention counts summarize currently open work.

    `snapshot_path` is read-only captured evidence, not a workspace path or
    writable checkout.
    """

    profile_id: int = Field(gt=0)
    """Durable identity of the ordinary Profile created for the agent.

    Later action batches must send this positive id. It conveys authorship, not
    a permanent author or reviewer role.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Initial retained Snapshot produced by the requested Tab capture.

    The agent uses this exact key for Thread reads, action batches, and the first
    continuation. It must not derive a key from `snapshot_path`.
    """

    last_activity_id: int = Field(ge=0)
    """Latest authored review activity visible when the session began.

    The nonnegative cursor is exclusive input to continuation. Passing it back
    requests only later authored actions without claiming code stayed unchanged.
    """

    snapshot_path: str = Field(min_length=1)
    """Absolute directory containing this Snapshot's captured File pairs.

    Agents inspect it read-only. Its opaque child directories and `left` or
    `right` files are evidence, not a writable checkout or path-address map.
    """

    attention_counts: dict[Literal["author", "reviewer", "both"], int]
    """Counts of open Threads needing author, reviewer, or both roles.

    All three keys describe the initial Snapshot at the returned activity
    boundary. Threads with `none` attention and non-open Threads are excluded.
    """


class AgentBayRange(ApiModel):
    """Address one bay of a File and an inclusive line range inside it.

    `bay_key` is the public composed bay key, and the line numbers are
    local to that bay rather than to the captured File. An ordinary text
    File composes the single `flatfile` bay spanning the whole File, so
    there the two coincide; a composed File such as a notebook has one bay
    per cell, where they do not. A reader that drops the key, or a writer that
    counts lines in the wrong text, addresses the wrong code.

    Agents derive both values from the captured bytes they already read: an
    ordinary File is `flatfile` and its own lines, and a notebook cell is keyed
    by the `id` in the `.ipynb` with the lines of that cell's joined `source`.
    This is the one placement coordinate the agent boundary speaks, in both
    directions.
    """

    bay_key: str = Field(min_length=1)
    """Public composed-bay key containing the selected lines.

    The key is meaningful with the action's captured File. The agent must obtain
    it from the format contract and must not substitute a frame or File name.
    """

    start_line: int = Field(ge=1)
    """First selected source line within `bay_key`.

    It is one-based and inclusive. The value must not exceed `end_line`, and it
    is never a rendered File-wide row number.
    """

    end_line: int = Field(ge=1)
    """Last selected source line within `bay_key`.

    It is one-based and inclusive. Equal endpoints select a single line; Room
    rejects a value outside the composed bay rather than clamping it.
    """

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """Enforce the inclusive line ordering of an agent bay range.

        `LineRange` checks the relationship after Pydantic has bounded both
        endpoints. Bay existence and content bounds remain Room's responsibility.

        # Usage

        Pydantic invokes this callback after validating both line fields. Callers
        construct `AgentBayRange`; they do not call the validator directly.

        # Failures

        - Raises `ValueError` when `end_line` precedes `start_line`.
        """
        LineRange(self.start_line, self.end_line)
        return self


class AgentBayStart(ApiModel):
    """Address the start of one bay of a File, without a line range.

    A Thread reads back with this shape when its original lines no longer
    exist in the Snapshot being read: derivation kept the origin's bay but
    could not match the region inside it, or the origin's bay is gone and the
    landing is the File's first bay carrying the Thread's side. The
    `outdated_reason` beside it says which. `bay_key` is the same public
    composed bay key `AgentBayRange` speaks.

    This shape is read-only: agents never write it, and `AgentCreateAction`
    keeps requiring a full `AgentBayRange`.
    """

    bay_key: str = Field(min_length=1)
    """Public key of the bay where the outdated Thread now lands.

    The placement means the bay's first line and supplies no writable range.
    Agents must not send this read-only shape in a create action.
    """


class AgentAuthor(ApiModel):
    """Expose ordinary Profile attribution through the agent boundary.

    Agent Comment and activity models embed this shape. Use `profile_id` for
    stable identity and `name` for presentation.

    It does not encode author/reviewer role, permissions, or agent UUID.
    """

    profile_id: int = Field(gt=0)
    """Durable identity of the Profile attributed to the Comment or action.

    It remains stable when `name` changes. Agents use numeric ids for writes and
    must not treat this value as a role assignment.
    """

    name: str = Field(min_length=1)
    """Current non-empty username of the attributed Profile.

    A rename changes later reads without changing `profile_id` or past action
    authorship. The name is for display, not subsequent addressing.
    """


class AgentComment(ApiModel):
    """Expose one complete current Comment to an agent.

    Thread reads use this model for full Comment content and attribution.
    Deletion retains timestamps and author while setting `body=None`.

    The shape does not expose edit revisions or individual action records.
    """

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Backend-generated identity retained through Comment changes.

    The lowercase hexadecimal id remains stable after edits and deletion.
    Agents may use it to correlate activity but action instruments target Threads.
    """

    author: AgentAuthor
    """Profile that authored the Comment's creation action.

    Later edits and deletion do not replace it. The nested name reflects the
    Profile's current username rather than a historical copy.
    """

    body: str | None
    """Complete Comment text after applying its current actions.

    A live Comment has a present body. `None` is valid only with `deleted=true`
    and represents a retained tombstone rather than unavailable content.
    """

    deleted: bool
    """Whether deletion replaced the Comment body with a tombstone.

    It must agree exactly with body absence. The response validator rejects a
    deleted Comment with text or a live Comment without text.
    """

    created_at: datetime
    """Timestamp of the Comment's creation action.

    It remains unchanged through edits and deletion and is no later than the
    current `updated_at` value.
    """

    updated_at: datetime
    """Timestamp of the action producing the current Comment.

    It equals `created_at` before revision and advances for edits or deletion.
    Thread lifecycle actions do not change it.
    """

    @model_validator(mode="after")
    def validate_tombstone(self) -> Self:
        """Reject an agent Comment whose body contradicts deletion state.

        Tombstones require `body=None`; live Comments require complete text.
        Return the validated response unchanged when those fields agree.

        # Usage

        Pydantic invokes this callback after validating the agent Comment
        fields. Callers validate the complete `AgentComment` model.

        # Failures

        - Raises `ValueError` when deletion state and body presence disagree.
        """
        if self.deleted != (self.body is None):
            raise ValueError("Deleted Comments must be body-less tombstones.")
        return self


class AgentCommentPreview(ApiModel):
    """Expose enough Comment text for agent discovery pages.

    Summary and activity endpoints return bounded body text plus explicit
    deletion and truncation flags. Agents fetch the complete Thread before
    acting when the preview is insufficient.

    This value is not valid input for replies and must not be treated as the
    complete persisted Comment.
    """

    body: str | None
    """Current Comment prefix exposed by a discovery or activity read.

    `None` means a tombstone and requires `deleted=true`. A present value is the
    complete body only when `truncated` is false.
    """

    deleted: bool
    """Whether the preview represents a retained deletion tombstone.

    True requires an absent `body`; false requires text. Deletion is distinct
    from truncation and does not remove the Comment from counts.
    """

    truncated: bool
    """Whether the present `body` omits trailing Comment characters.

    True means the boundary returned its bounded prefix and an agent must fetch
    the full Thread before relying on the missing text. Tombstones are not truncated.
    """


class AgentThreadSummary(ApiModel):
    """Expose enough of one open Thread for an agent to choose further work.

    Discovery endpoints return the current attention, landing coordinate, first
    and latest Comment previews, and total Comment count. The agent reads the
    full Thread before responding.

    Only open discussions appear here. The summary omits full excerpts,
    intermediate Comments, and immutable origin structure.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable identity of the open discussion represented by this summary.

    Agents pass it to focused Thread reads and later action instruments. It is
    independent of the current File or bay landing.
    """

    status: Literal["open"]
    """Confirm that discovery returned an actionable open Thread.

    Only the literal `open` is valid. Resolved and deleted discussions are
    excluded rather than represented with another status in this model.
    """

    attention: Literal["author", "reviewer", "both"]
    """Current role set expected to act on the open discussion.

    Discovery omits `none`. Callers use the literal to select author or reviewer
    work and must not derive it from the latest Comment author.
    """

    file: str | None
    """Absolute captured side path for the current landing, when one exists.

    Agents may inspect a present path read-only. `None` means placement has no
    usable current File side and must not be replaced with an origin path.
    """

    bay: AgentBayRange | AgentBayStart | None
    """Current public bay coordinate associated with `file`.

    A range gives exact lines, a start gives only the bay landing, and `None`
    means no bay coordinate remains. It is read-only discovery data.
    """

    first_comment: AgentCommentPreview
    """Current bounded form of the Thread's creation Comment.

    Edits or deletion may change this preview while its sequence remains zero.
    Agents fetch the focused Thread when truncated text matters.
    """

    latest_comment: AgentCommentPreview
    """Current bounded form of the highest-sequence Comment.

    It may equal `first_comment` when `comment_count` is one. It is not a preview
    of a lifecycle-only action.
    """

    comment_count: int = Field(ge=1)
    """Number of Comment positions currently materialized in the Thread.

    Every Thread has at least its creation Comment. Tombstones remain counted,
    and lifecycle-only actions do not increase this value.
    """


AgentOutdatedReason = Literal[
    "region_changed",
    "region_not_found",
    "bay_not_found",
    "file_unreadable",
    "file_missing",
]
"""Name what became of a Thread, in the agent boundary's own vocabulary.

- `region_changed` means the structural region remains but its text changed.
- `region_not_found` means no unique current region could be placed.
- `bay_not_found` means the origin bay no longer composes.
- `file_unreadable` means capture retained generated error content.
- `file_missing` means the exact File pair is absent.

The agent shape states placement as a bay plus this nullable reason, which
predates the browser response's placement union and stays frozen for the
skills and tooling written against it. `None` means the Thread still rests
where it was written.
"""


class AgentThread(ApiModel):
    """Expose one complete Snapshot-bound discussion to an agent.

    Thread reads combine current lifecycle, landing coordinate, origin excerpt,
    outdated reason, and complete Comments. Agents use this before choosing a
    review instrument.

    The older agent coordinate shape intentionally differs from the browser
    placement union. It does not expose private reattachment coordinates.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable identity shared by every placement of this discussion.

    Agents retain it for replies and reviewer actions. It does not change when
    `file`, `bay`, lifecycle, or attention changes.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Captured code universe used to interpret `file` and `bay`.

    The value must accompany subsequent actions against this placement. A later
    continuation may return a different Snapshot for the same Thread.
    """

    status: Literal["open", "resolved", "deleted"]
    """Lifecycle outcome at the read boundary.

    Open and resolved Threads may accept the instruments their contracts allow;
    deleted is terminal and rejects later writes.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Persisted role-attention outcome at the same boundary as `status`.

    `none` is explicit and must not be replaced by an inference from authorship
    or lifecycle state.
    """

    file: str | None
    """Absolute captured side path for the current landing, if usable.

    Agents inspect a present path read-only. `None` means the placement has no
    valid current File side, even though the immutable origin may name one.
    """

    bay: AgentBayRange | AgentBayStart | None
    """Current public bay coordinate associated with `file`.

    A range is exact, a start is an outdated landing without lines, and `None`
    states that no bay coordinate remains. It never carries private coordinates.
    """

    original_excerpt: ReviewExcerptResponse | None
    """Bounded source context from the immutable text origin.

    `None` is reserved for retained historical File-level origins. A present
    excerpt describes original content, not the current `file` landing.
    """

    outdated_reason: AgentOutdatedReason | None
    """Reason the current landing cannot reproduce the exact origin.

    `None` means the region still matches. A present value explains the shape of
    `file` and `bay`; callers must not infer a more precise coordinate.
    """

    comments: list[AgentComment]
    """Complete current Comment sequence for this Thread read.

    Items remain in creation order and tombstones retain their positions. This
    non-paged model is not a preview list.
    """


class AgentPage[AgentPageItem](ApiModel):
    """Wrap one bounded agent endpoint page with stable pagination metadata.

    Callers start at page one and repeat the returned activity boundary when an
    endpoint supplies it. Pagination facts describe that same bounded read.

    The generic wrapper does not define item semantics or promise that later
    unbounded reads see identical state.
    """

    items: list[AgentPageItem]
    """Values selected for this page in the endpoint's stable order.

    The concrete endpoint defines the item contract. An empty list is valid
    past the end and does not change `total`.
    """

    page: int = Field(ge=1)
    """One-based page number represented by `items`.

    It echoes the validated query and is interpreted together with `limit`.
    """

    limit: int = Field(ge=1)
    """Positive maximum number of items selected for this page.

    The final page may contain fewer values. Endpoint-specific query validation
    applies its upper bound before constructing this model.
    """

    total: int = Field(ge=0)
    """Count of all values matching the endpoint read.

    It covers every page, not only `items`, and shares the activity boundary
    when `through_activity_id` is present.
    """

    has_more: bool
    """Whether values remain after the represented page.

    True directs callers to request `page + 1` with the same filters, limit,
    and activity pivot when one is present.
    """

    through_activity_id: int | None = Field(default=None, ge=0)
    """Inclusive activity boundary for endpoints that need stable paging.

    `None` means the wrapped read has no activity-pivot contract. A present
    value must be repeated unchanged on subsequent pages.
    """


class AgentThreadPage(ApiModel):
    """Expose one discussion while paging its Comments independently.

    Agents retain the Thread facts while requesting later Comment pages with the
    same Snapshot and page size. `total_comments` and `has_more` apply only to
    the Comment sequence.

    The value is a read model, not an action input or activity boundary.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable identity of the discussion whose Comments are paged.

    It remains unchanged across pages and Snapshot placements. Later actions
    address this id together with `snapshot_id`.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Captured code universe used to interpret the repeated landing facts.

    Every Comment page for one read must use the same Snapshot. Pagination does
    not move the discussion to a newer capture.
    """

    status: Literal["open", "resolved", "deleted"]
    """Lifecycle outcome observed while reading this Comment page.

    Deleted is terminal. The endpoint repeats the field on each page but does
    not promise that an unbounded later read sees unchanged state.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Persisted role-attention outcome observed with `status`.

    `none` is explicit. Agents must not infer attention from the Comments in the
    current page, which may omit the action that set it.
    """

    file: str | None
    """Absolute captured side path for the current placement, if usable.

    A present path is read-only evidence. `None` means no current File landing
    and must remain absent on every Comment page for this response.
    """

    bay: AgentBayRange | AgentBayStart | None
    """Public bay landing within `file`, when placement provides one.

    A range is exact, a start has no line range, and `None` has no bay. Comment
    pagination must not reinterpret or refine the value.
    """

    original_excerpt: ReviewExcerptResponse | None
    """Bounded source context reconstructed from the immutable origin.

    It repeats unchanged on each page. `None` identifies a retained historical
    File-level origin rather than a failed excerpt read.
    """

    outdated_reason: AgentOutdatedReason | None
    """Reason placement could not retain the exact origin region.

    `None` means an exact current landing. A present value explains `file` and
    `bay` but never supplies private reattachment coordinates.
    """

    comments: list[AgentComment]
    """Complete current Comments selected for this page.

    Items retain creation order and include tombstones. The list may be empty
    past the end without implying that the Thread has no Comments.
    """

    page: int = Field(ge=1)
    """One-based Comment page represented by `comments`.

    The value echoes the query and is interpreted with `limit`; callers advance
    it only while `has_more` is true.
    """

    limit: int = Field(ge=1)
    """Positive maximum number of Comments selected for this page.

    The final page may contain fewer entries. Route validation applies the
    endpoint's upper bound before response construction.
    """

    total_comments: int = Field(ge=0)
    """Number of materialized Comment positions before pagination.

    The count spans every page and includes tombstones. Lifecycle-only actions
    do not contribute to it.
    """

    has_more: bool
    """Whether Comment positions remain after this page.

    True instructs the agent to request `page + 1` with the same Snapshot,
    Thread, and limit.
    """


class ContinueAgentReviewRequest(ApiModel):
    """Continue a retained agent review from one Snapshot and activity cursor.

    The route recaptures the persisted Tab context, compares Snapshot Files, and
    returns authored Thread changes after `last_activity_id`, bounded by
    `limit`.

    The request cannot replace the Tab, Profile, or Room context.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Retained Snapshot whose persisted Tab will be captured again.

    It locates the Room and supplies the old side of `file_delta`. The route
    does not accept a replacement Tab or choose the latest Snapshot implicitly.
    """

    last_activity_id: int = Field(ge=0)
    """Greatest authored activity the agent has already processed.

    Continuation returns actions with larger ids in increasing order. Zero
    requests activity from the Room's beginning.
    """

    limit: int = Field(default=20, ge=1, le=100)
    """Maximum number of later authored actions included in the response.

    The accepted range is one through one hundred and defaults to twenty.
    `has_more_thread_changes` reports whether the bound stopped the result.
    """


class AgentFileDelta(ApiModel):
    """Expose exact captured side paths changed by review continuation.

    Paths are captured evidence, not repository-relative names or writable
    workspace files.
    """

    added: list[str]
    """Absolute captured side paths introduced by the new Snapshot.

    Each path exists only in the recaptured side set. The sorted values are
    read-only evidence and are not repository-relative workspace paths.
    """

    changed: list[str]
    """New Snapshot paths for persistent sides whose captured bytes changed.

    Entries point to the new evidence. Their corresponding old paths are found
    through the previous Snapshot rather than duplicated in this list.
    """

    removed: list[str]
    """Absolute captured side paths absent from the new Snapshot.

    Each value still points into the immutable previous Snapshot. Consumers may
    inspect it read-only and must not expect it under `snapshot_path`.
    """


class AgentThreadChangeBase(ApiModel):
    """Expose attribution and ordering shared by agent activity variants.

    Continuation converts each authored action after the cursor into a Comment
    or lifecycle variant carrying these fields.

    The base has no discriminator or action-specific payload and is not emitted
    on its own.
    """

    activity_id: int = Field(gt=0)
    """Durable Room-wide order assigned to this authored action.

    Continuation returns values greater than the supplied cursor in increasing
    order. Agents advance their cursor to the greatest processed id.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable discussion identity affected by the action.

    It correlates Comment and lifecycle variants across Snapshot recaptures.
    The activity id, not this value, establishes global return order.
    """

    author: AgentAuthor
    """Ordinary Profile that authored this persisted action.

    The id is historical attribution, while the nested name reflects the
    Profile's current username at read time.
    """

    created_at: datetime
    """Timestamp recorded when the action was accepted.

    It describes authorship time, not recapture or continuation time. Ordering
    still follows `activity_id` when timestamps coincide.
    """


class AgentCommentThreadChange(AgentThreadChangeBase):
    """Expose one Comment activity after an agent cursor.

    `kind` distinguishes creation, edit, and deletion. `comment` is a bounded
    preview of the resulting Comment state.

    The activity does not carry complete Thread state or full Comment history.
    """

    kind: Literal[
        "comment_created",
        "comment_edited",
        "comment_deleted",
    ]
    """Identify which Comment mutation produced this activity.

    The literal determines how to present `comment`; every variant still carries
    the resulting current preview and stable Comment id.
    """

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable identity of the Comment created, edited, or tombstoned.

    It remains the same across later Comment changes. Agents use it to correlate
    the bounded resulting preview with a full Thread read.
    """

    comment: AgentCommentPreview
    """Comment state after applying this activity and earlier actions.

    The preview may be truncated and deletion retains a body-less tombstone.
    It is not necessarily the Comment's final state after later activities.
    """


class AgentStateThreadChange(AgentThreadChangeBase):
    """Expose one Thread lifecycle transition after an agent cursor.

    `kind` identifies resolve, reopen, or terminal deletion. Shared fields state
    ordering and attribution.

    Transitions that also create a Comment appear separately; this variant
    contains no discussion body.
    """

    kind: Literal["thread_resolved", "thread_reopened", "thread_deleted"]
    """Identify the lifecycle transition persisted by this action.

    Resolve, reopen, and terminal deletion carry no Comment payload here. A
    Comment created in the same domain operation appears as its own activity.
    """


AgentThreadChange = Annotated[
    AgentCommentThreadChange | AgentStateThreadChange,
    Field(discriminator="kind"),
]
"""Expose one authored action returned by review continuation.

- `AgentCommentThreadChange` carries Comment creation or mutation.
- `AgentStateThreadChange` carries lifecycle transition.

Consumers branch on `kind` and advance the activity cursor in returned order.
This union is output only.
"""


class ContinueAgentReviewResponse(ApiModel):
    """Return the result of continuing one retained agent review.

    The agent replaces its retained Snapshot facts and activity cursor with the
    returned values, inspects captured File changes, and processes authored
    activities in order. A non-exhausted page requires another continuation
    read before the agent assumes it has seen the complete activity stream.

    The response does not mutate the previous captured Snapshot or make its
    paths writable.
    """

    previous_snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Retained Snapshot used as the old side of this continuation.

    It echoes the request and anchors `file_delta`. Agents may keep its immutable
    path evidence but use `snapshot_id` for the next continuation.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot selected after recapturing the persisted Tab.

    The agent replaces its current code-universe key with this value for later
    reads, actions, and continuation, even when capture reused prior content.
    """

    snapshot_path: str = Field(min_length=1)
    """Absolute directory containing the returned Snapshot's File pairs.

    Agents inspect it read-only. It is immutable captured evidence, not a
    workspace checkout or a repository-path index.
    """

    last_activity_id: int = Field(ge=0)
    """Greatest authored activity included by this continuation page.

    Pass it as the next exclusive cursor. When `thread_delta` is empty, it
    retains the caller's cursor rather than inventing an unseen position.
    """

    unresolved_thread_count: int = Field(ge=0)
    """Number of open discussions placed in the returned Snapshot.

    The count is a current workload fact independent of the activity page; it
    can change when review actions open, resolve, reopen, or delete Threads even
    if those actions fall outside the returned page. Recapturing code alone does
    not change it.
    """

    file_delta: AgentFileDelta
    """Sorted captured-side differences between the two Snapshot keys.

    Old-only paths point into `previous_snapshot_id`; new and changed paths
    point into `snapshot_id`. All are read-only evidence.
    """

    thread_delta: list[AgentThreadChange]
    """Bounded authored actions with ids greater than the request cursor.

    Values are ordered by `activity_id` and may be empty. Placement and code
    changes are not synthesized as Thread activities.
    """

    has_more_thread_changes: bool
    """Whether authored actions remain after `last_activity_id`.

    True requires another continuation call before the agent treats the stream
    as caught up. That call may recapture the persisted Tab again.
    """


class AgentCreateAction(ApiModel):
    """Create one Thread on a named bay and its first Comment.

    `file` is the exact absolute captured side path the agent inspected; the
    bay names where in that File the finding sits. Batch application validates
    both against the selected Snapshot before creating the discussion.

    The action cannot target a complete File, a different Snapshot, or an absent
    bay side, and it cannot choose the Thread's lifecycle state.
    """

    kind: Literal["create-finding"]
    """Select Thread creation as this batch instrument.

    Only `create-finding` is valid. The action then requires captured `file`, a
    writable full `bay` range, and the first Comment body.
    """

    file: str = Field(min_length=1)
    """Absolute captured side path on which the agent found the issue.

    It must be an existing `left` or `right` file inside the request Snapshot.
    The server rejects workspace paths and captured paths from another Snapshot.
    """

    bay: AgentBayRange
    """Public bay key and exact inclusive lines selected in `file`.

    Room verifies that the File composes the key and the side exposes the range.
    Non-text bays accept only their defined single-line target.
    """

    body: str = Field(min_length=1)
    """Complete text of the new Thread's sequence-zero Comment.

    It must contain at least one character. The server does not trim it or
    create a finding without the Comment.
    """


class AgentReplyAction(ApiModel):
    """Request one role-directed Comment on a Snapshot-bound Thread.

    The action cannot resolve or delete the Thread, and the server enforces the
    current attention required by the selected instrument.
    """

    kind: Literal["author-response", "reviewer-return", "inert-comment"]
    """Select the Comment instrument and its persisted attention outcome.

    Author response, reviewer return, and inert Comment have distinct state and
    attention guards. The server applies the chosen transition with `body`.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Existing discussion that receives the new Comment.

    It must have a placement in the batch Snapshot and satisfy the selected
    instrument's lifecycle and attention guards.
    """

    body: str = Field(min_length=1)
    """Complete text appended at the next Comment sequence.

    It must contain at least one character. The action and attention outcome
    persist together or the whole batch fails.
    """


class AgentResolveAction(ApiModel):
    """Request reviewer resolution with a required explanation Comment.

    The batch binds `thread_id` through the supplied Snapshot and requires the
    Thread to be open with reviewer attention.

    This action cannot resolve silently or bypass role guards.
    """

    kind: Literal["reviewer-resolve"]
    """Select reviewer resolution with a required Comment.

    Only `reviewer-resolve` is valid. The instrument resolves an eligible open
    Thread and records `body` as the reviewer explanation in one operation.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Discussion to resolve through the batch Snapshot.

    It must be open, placed in that Snapshot, and currently admit the reviewer
    resolution instrument. The server does not reopen or redirect it.
    """

    body: str = Field(min_length=1)
    """Complete reviewer explanation appended with resolution.

    It must contain at least one character. The Comment and lifecycle change
    are atomic and share the batch Profile as author.
    """


class AgentDeleteAction(ApiModel):
    """Request exceptional terminal reviewer deletion of one Thread.

    The action carries no Comment body and applies only under the deletion
    instrument's state and attention guards.

    It is not ordinary cleanup, resolution, or Comment deletion.
    """

    kind: Literal["reviewer-delete"]
    """Select exceptional terminal Thread deletion.

    Only `reviewer-delete` is valid. Unlike resolution, the instrument accepts
    no Comment body and returns no Comment id.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Discussion to delete through the batch Snapshot.

    It must satisfy the reviewer deletion instrument's state and attention
    guards. Acceptance makes the Thread terminal for all later operations.
    """


AgentAction = Annotated[
    AgentCreateAction
    | AgentReplyAction
    | AgentResolveAction
    | AgentDeleteAction,
    Field(discriminator="kind"),
]
"""Describe one write accepted by the agent review batch.

- `AgentCreateAction` starts a finding on captured code.
- `AgentReplyAction` posts a role-directed or inert Comment.
- `AgentResolveAction` resolves with an explanation.
- `AgentDeleteAction` applies exceptional terminal deletion.

The batch applies these values in order and atomically. Browser review requests
use separate models.
"""


class AgentActionsRequest(ApiModel):
    """Request an ordered atomic agent review batch.

    One retained agent Profile authors every action against one exact Snapshot.
    The boundary accepts between one and one hundred writes and applies them in
    supplied order inside one transaction.

    The request cannot mix Profiles or Snapshots and never partially succeeds.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Captured code universe shared by every action in the batch.

    Create actions must name its captured files, and existing-Thread actions
    bind through its placements. The batch cannot span Snapshots.
    """

    profile_id: int = Field(gt=0)
    """Existing registered Profile that authors every batch action.

    The same positive id is recorded on each accepted write. It does not grant a
    role; individual instruments enforce their own attention guards.
    """

    actions: list[AgentAction] = Field(min_length=1, max_length=100)
    """Ordered instruments to validate and persist as one transaction.

    The list contains one through one hundred actions. Later items observe prior
    items in the same batch; any failure rolls back every result.
    """


class AgentActionResult(ApiModel):
    """Return the authoritative outcome of one agent batch action.

    Results preserve request order. `kind` echoes the instrument; Thread status
    and attention are current after it. Every instrument except deletion also
    returns the created Comment id.

    The value does not include complete discussion content or placement.
    """

    kind: Literal[
        "create-finding",
        "author-response",
        "reviewer-return",
        "reviewer-resolve",
        "inert-comment",
        "reviewer-delete",
    ]
    """Instrument accepted for the corresponding request position.

    The literal matches the input action kind exactly. Callers use it with the
    stable result ordering rather than guessing from optional fields.
    """

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable discussion identity produced or changed by the instrument.

    Creation returns a fresh id; other instruments echo their target. The value
    remains usable across later Snapshot placements.
    """

    comment_id: str | None = None
    """Identity of the Comment created by this instrument, if any.

    Every result except `reviewer-delete` must carry a fresh Comment id. Deletion
    must carry `None`; the validator rejects any other pairing.
    """

    status: Literal["open", "resolved", "deleted"]
    """Authoritative lifecycle outcome immediately after this batch item.

    Later items in the same batch may change it again. The result records this
    position's state rather than only the transaction's final state.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Authoritative role-attention outcome after this batch item.

    It is persisted with the action and may change again in a later item. The
    caller must not derive it from `kind` alone.
    """

    @model_validator(mode="after")
    def validate_variant(self) -> Self:
        """Enforce the result shape selected by the accepted instrument.

        Every action kind that creates a Comment must return its id, while
        reviewer deletion must not. Return the result unchanged after checking
        that exact presence relationship.

        # Usage

        Pydantic invokes this callback after validating one batch result. Callers
        construct `AgentActionResult`; they do not call it directly.

        # Failures

        - Raises `ValueError` when Comment presence contradicts `kind`.
        """
        if (self.comment_id is not None) != (self.kind != "reviewer-delete"):
            raise ValueError("Action result Comment presence is invalid.")
        return self


class AgentActionsResponse(ApiModel):
    """Return all outcomes of one successful atomic agent batch.

    `results` has the same length and order as the request actions, allowing the
    caller to retain generated Thread and Comment ids.

    A failed batch returns an error instead of this partial response.
    """

    results: list[AgentActionResult]
    """Authoritative results aligned one-for-one with request actions.

    The list has the same length and order as the accepted batch. A failed batch
    returns no response of this type, so partial result lists are invalid.
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


class ErrorResponse(ApiModel):
    """Advertise the intended non-review failure shape in route metadata.

    Repository, profile, preference, and Pull Request routes reference this
    model in their OpenAPI response declarations. Their current
    `HTTPException` path is still serialized by FastAPI as a `detail` envelope,
    so runtime failures are not validated through this model.

    Review routes use `ReviewErrorResponse` instead, and unexpected failures are
    not converted to this type.
    """

    # TODO: Either serialize non-review HTTP failures through this model or
    # advertise FastAPI's actual `detail` envelope in route metadata.
    error: str
    """Presentation text claimed by the non-review OpenAPI response model.

    Current `HTTPException` handling does not serialize this field. Callers must
    not assume it is the present runtime envelope.
    """


def branch_selection_request_to_selection(
    request: BranchSelection,
) -> BranchSelection:
    """Validate one client-sent branch selection into its canonical value.

    Whitespace-padded names are trimmed; an empty branch, or an empty remote
    on a remote selection, is a request error. The result carries exactly the
    fields its source variant defines.

    # Usage

    Convert a validated `AgentBranch` before passing it to backend branch
    selection. Use the returned dictionary as a complete value; do not add a
    remote to its local variant.

    # Failures

    - Raises `ValueError` when the branch is blank or a remote selection has no
      nonblank remote name.
    """
    branch = request["branch"].strip()
    if branch == "":
        raise DirdiffError("branch is required.")
    if request["source"] == "local":
        return {"source": "local", "branch": branch}
    remote = request["remote"].strip()
    if remote == "":
        raise DirdiffError("remote is required for remote selections.")
    return {
        "source": "remote",
        "remote": remote,
        "branch": branch,
    }


def repo_main_branch_record_to_selection(
    record: RepoMainBranchRecord,
) -> BranchSelection:
    """Reshape one stored main-branch row into a canonical branch selection.

    The database row is trusted except for its variant invariant: a remote
    row must carry its remote. An unknown source value is a contract failure.

    # Usage

    Use this after `RepoMarkStore.get_main_branch` returns a record. Pass the
    resulting value to branch-default responses or backend selection unchanged.

    # Failures

    - Raises `AssertionError` when persisted source and remote fields do not form
      a local or remote branch selection.
    """
    if record.source == "local":
        return {"source": "local", "branch": record.branch}
    if record.source == "remote":
        assert record.remote is not None, (
            "remote main branch row is missing remote"
        )
        return {
            "source": "remote",
            "remote": record.remote,
            "branch": record.branch,
        }
    raise DirdiffError(f"Unknown main branch source: {record.source}")


class RepoMainBranchRequest(ApiModel):
    """Request persistence of one repository's default Branch Review base.

    The repository id comes from the route path; this body carries one validated
    structured branch selection for the store.

    It does not resolve the branch or capture a Snapshot.
    """

    selection: BranchSelection
    """Local or remote symbolic branch chosen as the repository default.

    The route validates and canonicalizes its names before persistence. It does
    not resolve the selection to a commit or apply it to another repository.
    """


class RepoMainBranchResponse(ApiModel):
    """Return the saved default Branch Review base for one repository.

    The HUD uses `project_id` to associate `selection` with its marked
    repository and seed later controls.

    The selection remains symbolic. It is not a resolved commit or guarantee
    that the ref still exists.
    """

    project_id: int
    """Registry identity of the repository whose default was stored.

    It echoes the path parameter after a successful write and lets the HUD
    associate `selection` with the correct marked repository.
    """

    selection: BranchSelection
    """Canonical symbolic branch selection read back from persistence.

    Local values omit a remote and remote values require one. It remains a
    branch choice, not proof that later Git resolution will succeed.
    """


class RepoDefaultsResponse(ApiModel):
    """Return the two selections that seed Branch Review controls.

    `default_base_selection` may be a usable branch or an explicit heuristic
    failure. `preferred_review_selection` is the branch the HUD should start
    with on the review side.

    These values initialize controls only; they do not resolve or capture refs.
    """

    default_base_selection: DefaultBaseSelection
    """Initial base-control value for the selected repository.

    It is either a saved or inferred symbolic selection, or the typed heuristic
    failure the HUD must present. Failure is not replaced with an invented branch.
    """

    preferred_review_selection: BranchSelection
    """Backend-preferred symbolic selection for the review-side control.

    The HUD may present it beside the base result, but capture still receives
    and resolves explicit selections from the eventual manifest call.
    """


class RepoRefsResponse(ApiModel):
    """Return one coherent set of repository ref choices.

    The route derives `ref_choices` from one backend metadata observation so
    controls do not mix repository states from repeated Git reads.

    Choices are suggestions. They do not reserve refs or promise later
    resolution.
    """

    ref_choices: RefChoices
    """Local branches, remote branches, and refs offered by the backend.

    All choices come from one observation so their relationships are coherent.
    They seed controls and do not reserve or validate a future selection.
    """


class PullRequestPrepareRequest(ApiModel):
    """Request preparation of one supported Pull Request URL.

    The route matches the URL to a marked repository and asks the forge-specific
    backend preparation code to fetch required refs.

    The request cannot supply repository identity or commit overrides.
    """

    url: str
    """Forge URL whose repository and immutable review sides must be prepared.

    The route parses the complete value and rejects unsupported hosts or shapes.
    It does not accept a repository id or commit override beside the URL.
    """


class PullRequestPrepareResponse(ApiModel):
    """Return the complete prepared state required for Pull Request capture.

    Manifest requests send back the canonical URL, repository id, merge-base
    commit, and review commit exactly as prepared.

    The response does not contain mutable branch names or rendered diff data.
    """

    project_id: int
    """Registry identity of the marked repository matched to the forge base.

    The later manifest call sends this exact id with the prepared commits. It is
    not inferred again from the canonical URL.
    """

    pull_request_url: str
    """Canonical forge URL produced by preparation.

    The manifest sends it back unchanged so Pull Request captures select the
    same Room correspondence. It may differ in spelling from the input URL.
    """

    left_commit: str
    """Immutable merge-base object selected for the left capture side.

    Preparation computes it from fetched refs. Callers must send it unchanged
    rather than resolving the base branch again.
    """

    right_commit: str
    """Immutable Pull Request head object selected for the right capture side.

    Preparation obtains it from the supported forge refs. It forms one prepared
    unit with `left_commit`, `project_id`, and `pull_request_url`.
    """


def pull_request_prepare_response(
    prepared: PreparedPullRequest,
) -> PullRequestPrepareResponse:
    """Validate a backend preparation result as the public HTTP response.

    The conversion copies the matched repository id, canonical URL, merge base,
    and review head without resolving or normalizing them again. Pydantic rejects
    any value that violates the response shape.

    # Usage

    Call immediately after backend Pull Request preparation and return the
    resulting model from the HTTP endpoint.

    # Failures

    - Raises Pydantic validation errors when backend output cannot satisfy the
      declared response contract.
    """

    return PullRequestPrepareResponse.model_validate(
        {
            "project_id": prepared.project_id,
            "pull_request_url": prepared.pull_request_url,
            "left_commit": prepared.left_commit,
            "right_commit": prepared.right_commit,
        }
    )


class RepoMarkResponse(ApiModel):
    """Return one active repository mark shown by CLI and HUD selectors.

    Repository routes validate active registry records through this model. The
    HUD uses `id` for later operations, `path` for workspace context, and the
    remaining fields for presentation.

    The response carries no Git refs, saved branch default, Room, or proof that
    the directory stays readable after the query.
    """

    id: int
    """Durable registry identity assigned to the active repository mark.

    Later repository and capture calls use it instead of matching display name.
    Reactivating the same stored mark retains this identity.
    """

    path: str
    """Canonical workspace path recorded for the mark.

    Server routes use it to construct the repository backend. The response does
    not promise that external filesystem changes leave it readable later.
    """

    name: str
    """Presentation name associated with the marked repository.

    The HUD may display it, but identity and backend lookup use `id` and `path`.
    It is not a Git remote or Room name.
    """

    marked_at: datetime
    """Timestamp of the most recent mark or reactivation operation.

    It orders registry presentation and does not describe repository modification
    time, Snapshot capture time, or last access.
    """


class UserProfileResponse(ApiModel):
    """Return persisted Profile identity after create, lookup, or rename.

    Profile routes validate store records through this model. Callers retain the
    id for preference and review operations and show the current username.

    The response carries no active-selection state, role, or preferences.
    """

    # TODO: Successful Profile routes reject missing records, so make both
    # fields required instead of admitting a partial or empty Profile.
    id: int | None
    """Durable Profile identity returned by successful current routes.

    The model still admits `None`, although route guards reject missing records.
    Callers must not invent an id when absence appears.
    """

    username: str | None
    """Current globally unique Profile username.

    Successful routes return a present string paired with `id`. The nullable
    model does not authorize a partial Profile or anonymous review author.
    """


class UserProfileUpdateRequest(ApiModel):
    """Supply one exact username to a Profile create or rename route.

    The called endpoint determines whether it creates or updates. Store
    validation rejects empty, edge-padded, blank, or duplicate names.

    This body does not identify the Profile being renamed; the route path does.
    """

    username: str
    """Complete username proposed for creation or rename.

    Persistence requires a unique nonblank value without edge whitespace. The
    route does not trim it or use it to select the Profile being renamed.
    """


class PreferencesResponse(ApiModel):
    """Return the complete persisted HUD preferences for one Profile.

    Preference routes validate `PreferencesRecord` through this model after read
    or write. The Profile id keeps the response bound to the addressed user.

    The server does not interpret how the HUD applies these values.
    """

    user_profile_id: int
    """Durable Profile identity associated with this preference record.

    It matches the route's addressed Profile. The HUD must not apply the value
    to another selected user merely because a username changed.
    """

    aggressive_folds: bool
    """Initial policy for folding renderer-provided unchanged intervals.

    True asks the HUD to start eligible ranges folded; false starts them open.
    The value does not collapse Files or override later reviewer actions.
    """


class PreferencesUpdateRequest(ApiModel):
    """Supply the mutable preference values to store for one Profile.

    The route path identifies the Profile; this body contains the replacement
    value written by `PreferencesStore`.

    It does not select or create a Profile and carries no defaults.
    """

    aggressive_folds: bool
    """Complete replacement for the Profile's initial folding policy.

    The route stores this exact boolean for the Profile named in its path. It
    does not create the Profile or change already mounted fold state directly.
    """


class PresetGroupResponse(ApiModel):
    """Return one selectable fixture group from a preset catalog.

    The preset picker shows `display_name` and sends `id` back in a manifest
    request to choose the directory.

    The response contains no fixtures, catalog identity, or File content.
    """

    id: str
    """Catalog-local directory token identifying this fixture group.

    The Preset Tab sends it as `preset_subset` with its catalog id. Consumers
    must not treat the human-readable name as the selection key.
    """

    display_name: str
    """Presentation name read from the fixture group's metadata.

    It may be shown in the picker but does not identify a directory or manifest
    request on its own.
    """


class PresetCatalogResponse(ApiModel):
    """Describe one selectable preset catalog and its fixture groups.

    `/api/presets` returns one value per catalog directory. The picker gets its
    display text, manifest selection token, initial group choice, and offered
    groups from this value.

    This response describes catalog choices only. It contains no fixture paths,
    loaded bytes, or Snapshot identity.
    """

    id: str
    """Preset-root-relative directory token identifying this catalog.

    The HUD sends it as the Preset manifest `project_id`. It is not a repository
    mark id or display name.
    """

    name: str
    """Presentation name for the catalog selector.

    It does not participate in directory lookup. The stable selection token is
    `id` even when the displayed name changes.
    """

    default_preset: str
    """Catalog-local group id the picker should select initially.

    It must correspond to one entry in `groups`. Callers send the id rather than
    copying that group's display name.
    """

    groups: list[PresetGroupResponse]
    """Complete selectable fixture groups for this catalog.

    The backend supplies their display order. Each group id is meaningful only
    with this catalog, and the HUD must preserve the association.
    """


class DecoratedPartResponse(ApiModel):
    """One ordered, lossless slice of a rendered line.

    Diff-row conversion returns a complete partition for each present side. The
    HUD concatenates `text` in order and applies the supplied diff, syntax, and
    whitespace decoration directly.

    Parts carry no offsets, line identity, or review coordinates. Their text
    must not be reordered or independently edited.
    """

    text: str
    """Exact source characters covered by this decoration slice.

    Concatenating sibling parts in order must reproduce the present side's full
    line text. Consumers must preserve whitespace and empty-looking content.
    """

    syntax_classes: list[SyntaxClass]
    """Renderer syntax categories active across every character in `text`.

    The list may be empty when no syntax category applies. Its order and values
    are presentation metadata, not source-token identity.
    """

    diff_status: InlineTokenStatus
    """Inline comparison relationship assigned to this source slice.

    The HUD renders it directly within the row-level status. It does not replace
    or determine the enclosing row's alignment status.
    """

    is_whitespace: bool
    """Whether this complete slice contains only whitespace characters.

    The renderer computes the flag from the preserved source text. Consumers use
    it for display and must not strip the slice when true.
    """

    is_leading_whitespace: bool
    """Whether this slice is whitespace before the line's first content.

    True implies `is_whitespace`; later whitespace stays false. The distinction
    lets the HUD present indentation separately without changing source text.
    """


class FoldHintResponse(ApiModel):
    """Return one foldable rendered-row interval to the HUD.

    Text-bay conversion validates renderer fold hints through this model. The
    HUD may hide the zero-based half-open row interval under policy selected by
    `kind` and present `label`.

    A hint does not change row alignment, contain a hunk, or represent current
    DOM expansion state.
    """

    start_row: int
    """First rendered-row index included in the foldable interval.

    The coordinate is zero-based and inclusive. It belongs to one bay's row
    list and must precede `end_row`.
    """

    end_row: int
    """Exclusive rendered-row bound of the foldable interval.

    It is strictly greater than `start_row` and no greater than the enclosing bay's
    row count. Consumers fold exactly the half-open interval.
    """

    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    """Renderer's structural category for the foldable interval.

    The HUD uses the closed vocabulary to choose folding policy and styling. It
    does not alter the interval or imply current expanded state.
    """

    label: str
    """Presentation text describing the hidden structural interval.

    The HUD shows it while rows are folded. It is not a source line, stable
    identity, or navigation coordinate.
    """


class DiffRowResponse(ApiModel):
    """Return one aligned row with complete display decoration.

    Present sides carry their source line number, text, and lossless ordered
    parts. An absent side has no line number or decorated parts. Current
    renderers encode its text as an empty string, while the transport model
    also accepts `None`. `status` summarizes the row relationship.
    """

    status: Literal["equal", "replace", "insert", "delete", "move"]
    """Relationship the engine assigned to this aligned source row.

    `equal`, `replace`, and `move` pair present lines from both sides; they mean
    unchanged text, changed aligned text, and structurally moved text respectively.
    `insert` is right-only and therefore has no left line, while `delete` is
    left-only and has no right line. The HUD uses this closed vocabulary for row
    styling and change accounting; token statuses may still describe finer
    changes within a paired row.
    """

    left_no: int | None
    """One-based left source coordinate represented by this row.

    `None` means the alignment has no left line. Current renderer output pairs
    it with empty text and no left parts. A present value identifies source,
    not rendered order.
    """

    right_no: int | None
    """One-based right source coordinate represented by this row.

    `None` means the alignment has no right line. Current renderer output pairs
    it with empty text and no right parts. A present value identifies source,
    not rendered order.
    """

    left_text: str | None
    """Complete left source line without a newline, when one is aligned.

    With a present `left_no`, concatenating `left_parts` reproduces this exact
    string. Current renderer output uses `""` when `left_no` is absent; the
    nullable model also accepts `None` from other constructors.
    """

    right_text: str | None
    """Complete right source line without a newline, when one is aligned.

    With a present `right_no`, concatenating `right_parts` reproduces this exact
    string. Current renderer output uses `""` when `right_no` is absent; the
    nullable model also accepts `None` from other constructors.
    """

    left_parts: list[DecoratedPartResponse]
    """Ordered decoration slices covering the entire present left line.

    For a present side, their text concatenates to `left_text` without loss or
    overlap. Current renderer output leaves the list empty when `left_no` is
    absent.
    """

    right_parts: list[DecoratedPartResponse]
    """Ordered decoration slices covering the entire present right line.

    For a present side, their text concatenates to `right_text` without loss or
    overlap. Current renderer output leaves the list empty when `right_no` is
    absent.
    """
    hunk_index: int | None
    """
    Zero-based bay-local hunk identity on a hunk's first rendered row.

    Other rows carry `None`. Display enrichment numbers each bay's own rows from
    zero, so a hunk coordinate is this index plus the enclosing bay's key; the
    backend never numbers a File as a whole. The value is assigned before
    frontend folding and virtualization and is therefore independent of DOM
    layout.
    """


class DiffSummaryResponse(ApiModel):
    """Return File-wide line totals and side existence.

    Composed File conversion validates the aggregate engine counts and captured
    side existence through this model. The HUD uses it for File-level totals.

    Counts cover text bays only. They do not describe repository-wide totals or
    image differences; existence distinguishes an absent side from an empty
    File.
    """

    changed_lines: int
    """Number of non-equal aligned rows across all rendered text bays.

    It is the File-wide aggregate of modified, added, removed, and moved row
    accounting supplied by the renderer, not a repository total.
    """

    modified_lines: int
    """Count of changed rows with both left and right source lines.

    The value covers the File's text bays only. It excludes inserted, deleted,
    and line-level moved rows counted in their own fields.
    """

    added_lines: int
    """Count of rendered rows that have only a right source line.

    It aggregates text bays for this File and does not include repository
    additions that the backend did not render as lines.
    """

    removed_lines: int
    """Count of rendered rows that have only a left source line.

    It aggregates text bays for this File and does not count an absent File side
    independently of the renderer's produced rows.
    """

    moved_lines: int = 0
    """Count of rows the selected engine classified as moved.

    The value excludes inline token movement and defaults to zero for engines
    that report no line moves. It remains part of `changed_lines` accounting.
    """

    left_exists: bool
    """Whether capture retained a left File side.

    True includes an empty file whose renderer produced no rows. False denotes
    side absence and must not be inferred from line totals.
    """

    right_exists: bool
    """Whether capture retained a right File side.

    True includes an empty file whose renderer produced no rows. False denotes
    side absence and must not be inferred from line totals.
    """


class RepoDiffSummaryResponse(ApiModel):
    """Validate manifest-wide File totals and optional backend line totals.

    Added and removed line counts are either both backend-reported integers or
    both absent. They need not include additional untracked Files. Cell totals
    remain optional because only notebook-aware payloads can provide them.
    """

    changed_files: int
    """Total affected File relationships reported for the manifest.

    It equals `added_files + removed_files + updated_files`, including untracked
    Files that became manifest leaves. Skipped entries are counted separately.
    """

    added_files: int
    """Count of affected File pairs with only a right side.

    The backend computes this before rendering. Additional untracked entries
    follow the backend's manifest policy rather than line totals.
    """

    removed_files: int
    """Count of affected File pairs with only a left side.

    The backend computes this before rendering. The value is independent of how
    many deleted source rows an engine later produces.
    """

    updated_files: int
    """Count of affected File relationships present on both sides.

    Modified, renamed, and copied tracked Files contribute here. The value does
    not claim the side paths are equal.
    """

    added_lines: Optional[int]
    """Backend-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` always have equal presence.
    """

    removed_lines: Optional[int]
    """Backend-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` always have equal presence.
    """

    skipped_files: int
    """Count of backend-reported entries not included as manifest Files.

    This records backend summary damage or filtering and is not a lazy-File
    count. Consumers must not add it to the manifest tree.
    """

    changed_cells: int | None = None
    """Aggregate count of notebook cells with any reported change.

    `None` means the selected backend supplied no cell summary, not zero. A
    present value is repository-wide and separate from File line counts.
    """

    added_cells: int | None = None
    """Aggregate count of notebook cells present only on the right.

    `None` means no cell summary was supplied. Consumers must preserve that
    distinction instead of presenting an unavailable count as zero.
    """

    removed_cells: int | None = None
    """Aggregate count of notebook cells present only on the left.

    `None` means no cell summary was supplied. The value is metadata, not a
    count derived from composed notebook bays.
    """

    modified_cells: int | None = None
    """Aggregate count of paired notebook cells whose content changed.

    `None` means the backend supplied no cell summary. Moved-only or format
    details remain governed by the backend's summary contract.
    """

    @model_validator(mode="after")
    def validate_line_count_presence(self) -> RepoDiffSummaryResponse:
        """Require repository added and removed line totals as one unit.

        Both backend totals must be integers or both must be unavailable. Return
        the summary unchanged after rejecting a partial pair.

        # Usage

        Pydantic invokes this callback after validating repository summary
        fields. Callers validate `RepoDiffSummaryResponse` as one complete value.

        # Failures

        - Raises `ValueError` when exactly one repository line total is present.
        """
        if (self.added_lines is None) != (self.removed_lines is None):
            raise ValueError(
                "added_lines and removed_lines must have equal presence"
            )
        return self


class GitFileKindResponse(ApiModel):
    """Return tracked Git provenance for one manifest or composed File.

    The `git` discriminator selects this variant; `status` describes the File
    relationship reported by the backend.

    It does not identify side paths, loading state, or line changes.
    """

    type: Literal["git"]
    """Select the tracked Git provenance variant.

    Only the literal `git` is valid. Consumers may then rely on `status`; the
    value does not mean the File currently exists on both sides.
    """

    status: Literal["modified", "added", "deleted", "renamed", "copied"]
    """Tracked relationship the Git backend reported for this File pair.

    The closed value distinguishes one-sided, same-path, and cross-path changes.
    It does not summarize rendered line or bay status.
    """


class UntrackedFileKindResponse(ApiModel):
    """Return untracked provenance for one worktree File.

    The `untracked` discriminator lets the HUD present content absent from Git's
    tracked state.

    Side paths and lazy reason remain on the enclosing File response.
    """

    type: Literal["untracked"]
    """Select provenance for a File not present in Git's tracked state.

    Only the literal `untracked` is valid. No tracked change status accompanies
    it; path presence and lazy loading stay on the enclosing File model.
    """


FileKindResponse = GitFileKindResponse | UntrackedFileKindResponse
"""Return one complete File provenance variant to the HUD.

- `GitFileKindResponse` includes tracked Git status.
- `UntrackedFileKindResponse` identifies worktree-only content.

Consumers branch on `type`. This union is not a File identity or rendered diff
classification.
"""


class BayWarningResponse(ApiModel):
    """Return one visible degradation attached to a usable bay.

    Format conversion preserves engine and builder warnings through this common
    shape. `type` is stable for consumers; `message` explains the exact damage.

    A warning cannot stand in for missing or invalid bay output.
    """

    type: str = Field(min_length=1)
    """Non-empty warning category supplied by the engine or format builder.

    Consumers may group known categories without parsing `message`. A warning
    records degraded usable output, not a successful result kind.
    """

    message: str
    """Concrete explanation of the damage affecting this bay.

    The HUD may present it directly. Behavior must use the warning category or
    kind data rather than depend on wording.
    """


class BayStatsResponse(ApiModel):
    """Per-bay engine line counts, before File-level aggregation.

    Text-kind conversion validates one engine summary through this model; the
    HUD shows it on the corresponding bay.

    It counts that bay's rows only. Side existence is a File fact on the File
    summary, and repository totals remain outside this response.
    """

    changed_lines: int
    """Number of non-equal aligned rows in this text bay.

    It summarizes only the enclosing bay and feeds File aggregation. The value is
    not a source-line span or repository-wide count.
    """

    modified_lines: int
    """Count of changed bay rows with both source sides present.

    Inserted, deleted, and line-moved rows use their own counts. Inline token
    changes do not add another row to this total.
    """

    added_lines: int
    """Count of bay rows containing only a right source line.

    It comes from the engine alignment for this bay. It does not state that the
    complete File exists only on the right.
    """

    removed_lines: int
    """Count of bay rows containing only a left source line.

    It comes from the engine alignment for this bay. It does not state that the
    complete File exists only on the left.
    """

    moved_lines: int = 0
    """Count of rows this bay's engine classified as moved.

    Inline token movement is excluded. Engines without line-move output use the
    zero default, which still participates in File aggregation.
    """


class ChangeStatusResponse(ApiModel):
    """Return the semantic outcome of a bay that did not move.

    Moved bays use `MovedChangeStatusResponse`. This value does not classify
    individual rows.
    """

    kind: Literal["added", "removed", "changed", "unchanged"]
    """Whole-bay semantic outcome decided by the format builder.

    `added` and `removed` are one-sided, `changed` retains document position
    with different content, and `unchanged` reports no semantic change.
    """


class MovedChangeStatusResponse(ApiModel):
    """Return the old and new headings of one moved bay.

    Format builders use this variant when semantic document position changed.
    Either heading may be absent when that side has no useful name.

    Movement does not imply unchanged content; the bay's rows may still show an
    edit. This value carries no numeric position.
    """

    kind: Literal["moved"]
    """Select the semantic status for a bay whose frame position changed.

    Only the literal `moved` is valid. Content may also differ, so consumers must
    still render the bay's own rows or media.
    """

    from_heading: str | None
    """Old frame heading that located the bay on the left side.

    `None` means that side had no useful heading. It does not mean the left File
    side or bay was absent.
    """

    to_heading: str | None
    """New frame heading that locates the bay on the right side.

    `None` means that side has no useful heading. Callers present absence rather
    than substituting `from_heading`.
    """


class TextKindResponse(ApiModel):
    """What a `text` bay holds: decorated rows from the shared text renderer.

    The `text` discriminator sends this variant to the HUD text grid. Side
    labels name its columns; rows, fold hints, and stats come from the shared
    renderer. Hunk indexes are bay-local.

    Engine and format warnings stay on the bay envelope. This value contains no
    File identity, expansion policy, or image bytes.
    """

    kind: Literal["text"]
    """Select line-oriented rendering for this bay's content.

    Only the literal `text` is valid. Consumers may then use rows, fold hints,
    and line statistics; the enclosing bay retains identity and warnings.
    """

    left_label: str
    """Presentation heading for the text bay's left-side column.

    It may name a notebook cell source or another format-local side. It is not a
    captured path, bay key, or proof that a left row exists.
    """

    right_label: str
    """Presentation heading for the text bay's right-side column.

    It is format-authored independently of `left_label` and must not be used as
    a side identity or review coordinate.
    """

    rows: list[DiffRowResponse]
    """Complete aligned and decorated rows for this bay.

    Order is the renderer's source order and defines row coordinates used by
    fold hints. Consumers must not regroup rows by status.
    """

    fold_hints: list[FoldHintResponse] = Field(default_factory=list)
    """Renderer-provided half-open intervals eligible for folding.

    Every interval indexes `rows`. An empty list means no structural folds were
    supplied and does not authorize the HUD to invent them.
    """

    stats: BayStatsResponse
    """Line-status totals computed from this bay's engine result.

    They summarize `rows` before File aggregation. They contain no File-side
    existence or image-byte facts.
    """


class MediaRefResponse(ApiModel):
    """One captured media side, described without its bytes.

    Image-kind conversion sends this value to the HUD so its widget can show
    byte facts and request the exact side from `/api/file-media`.

    The reference contains no bytes, dimensions, captured path, or authorization
    to read a local file. Snapshot, side, and File path address the media route.
    """

    media_type: str = Field(min_length=1)
    """Detected non-empty media type of the captured picture bytes.

    `/api/file-media` returns the same value as `Content-Type` for this side.
    Consumers must not redetect the format from path extension.
    """

    byte_size: int = Field(ge=0)
    """Length of the immutable captured media payload.

    Zero is valid if the format builder produced such a reference. The value is
    a byte count, not decoded dimensions or transfer size.
    """

    digest: str = Field(min_length=1)
    """SHA-256 digest computed from the exact captured bytes.

    The non-empty lowercase hexadecimal value changes with content and lets the
    HUD compare side facts. It is not an HTTP address by itself.
    """


class ImageKindResponse(ApiModel):
    """What an `image` bay holds: two optional references to captured pictures.

    The `image` discriminator sends this variant to the HUD image widget. Either
    reference may be absent for an added or removed File; the widget requests
    present sides from `/api/file-media`.

    It contains no bytes or dimensions and must not turn an absent side into an
    empty picture.
    """

    kind: Literal["image"]
    """Select picture rendering for this bay's content.

    Only the literal `image` is valid. Consumers request bytes for each present
    side and do not expect text rows or fold hints in this variant.
    """

    left: MediaRefResponse | None = None
    """Facts describing the captured left picture side, if present.

    `None` means the File has no left media side, not an empty or failed image.
    A present reference addresses bytes only with Snapshot and File context.
    """

    right: MediaRefResponse | None = None
    """Facts describing the captured right picture side, if present.

    `None` means the File has no right media side. At least one side is present
    for a valid image bay, and callers must preserve the absent-side state.
    """


BayKindResponse = Annotated[
    TextKindResponse | ImageKindResponse,
    Field(discriminator="kind"),
]
"""The content of one bay, discriminated by the widget that renders it.

- `TextKindResponse` supplies decorated lines, folds, labels, and statistics.
- `ImageKindResponse` supplies references for present picture sides.

Named byte facts remain text because reviewers read them as lines. Consumers
dispatch on `kind`; this union does not contain bay identity, frame placement,
change status, or warnings, which remain on `BayResponse`. A new kind must
represent content that cannot honestly be read as lines and requires a matching
frontend variant; frames and the bay envelope do not change to admit it.
"""


class BayResponse(ApiModel):
    """Expose one bay's public coordinate, presentation, and content.

    Its stable sub-File coordinate is shared with line pins and review targets.
    Format-authored text names the whole bay in its placeholder and inline-grid
    content column.

    Only the format builder reports what happened to the bay. A notebook cell
    that moved and one whose output changed beyond its rendered text may have
    equal rows, so nothing downstream can recover that status.

    The widget discriminator stays inside the content value. Placement,
    identity, expansion, and status consumers never need to know the widget kind.
    """

    bay_key: str = Field(min_length=1)
    """Stable public sub-File coordinate used by navigation and review.

    Its meaning is scoped by the enclosing File pair; clients return it unchanged
    for line pins and Thread targets rather than deriving it from labels or order.
    """

    label: str
    """Backend-authored name shown for the bay as a whole.

    It is presentation text and may repeat across bays, so callers must not use it
    as the stable coordinate.
    """

    detail: str | None = None
    """Optional secondary backend-authored description.

    Null means the format supplied no additional text; consumers omit the detail
    instead of substituting change status or widget metadata.
    """

    collapsible: bool
    """Whether the reviewer may hide this bay's body.

    False makes the body permanently present and therefore makes initial expansion
    policy non-actionable.
    """

    default_expanded: bool
    """Initial expansion state when `collapsible` is true.

    It seeds presentation only; later reviewer interaction remains frontend state
    and is not written back into this response.
    """

    change: Annotated[
        ChangeStatusResponse | MovedChangeStatusResponse,
        Field(discriminator="kind"),
    ]
    """Format-authored semantic outcome for this bay.

    The HUD presents it directly because row equality cannot recover moves or
    format-specific changes; widget content must not reinterpret it.
    """

    warnings: list[BayWarningResponse]
    """Visible non-fatal damage confined to this bay.

    An empty list means composition completed without localized warnings, while
    entries preserve usable content rather than turning the whole File into failure.
    """

    kind_data: BayKindResponse
    """Kind-specific content consumed only by widget dispatch.

    Its discriminator selects text or image rendering; identity, placement, and
    semantic outcome remain on the enclosing bay.
    """


class FrameResponse(ApiModel):
    """One presentational frame: an optional heading over ordered bays.

    Composed File conversion groups contiguous bays with the same frame identity
    into this response. The HUD renders the optional heading over bays in
    document order.

    A frame carries no change state, navigation target, or Comment coordinate of
    its own. Those belong to bays.
    """

    frame_key: str = Field(min_length=1)
    """Stable frame identity emitted by the format builder.

    Contiguous bays sharing it compose into this frame; it is not a review or
    navigation coordinate by itself.
    """

    heading: str | None = None
    """Backend-authored frame heading, or `None` for a heading-less frame.

    Absence instructs the HUD to render no heading and must not be replaced with
    a File name or frame key.
    """

    bays: list[BayResponse]
    """Bays contained by the frame in document order.

    Their order is format-authored and consumers must not regroup them by widget
    kind, change status, or review activity.
    """


class ComposedDiffResponse(ApiModel):
    """The single `/api/file-diff` response shape: one composed diff.

    `/api/file-diff` validates the complete composition through this model. The
    HUD receives File metadata followed by frames and their bays in document
    order; a plain text File is one heading-less frame with one text bay.

    There is no File-wide render discriminator. Bay content chooses its widget,
    and this response contains no captured media bytes or review discussion
    state.
    """

    display_name: str
    """Manifest-authored human-readable name of the File pair.

    It is presentation text only; exact nullable repository paths remain the
    address used by File and review endpoints.
    """

    left_label: str
    """Human-readable label for the complete left comparison side.

    Individual text bays may supply their own column labels, so this value names
    the File envelope rather than every widget column.
    """

    right_label: str
    """Human-readable label for the complete right comparison side.

    It remains valid when the File's right path is absent because it describes
    the comparison side, not side presence.
    """

    left_path: str | None = None
    """Repository-relative left File path, or `None` when absent.

    At least one side path exists for a valid File pair; consumers preserve
    absence instead of substituting the right path or an empty string.
    """

    right_path: str | None = None
    """Repository-relative right File path, or `None` when absent.

    Together with `left_path` it is the exact manifest identity accepted by
    follow-up operations, not merely a display label.
    """

    file_kind: FileKindResponse
    """Tracked or untracked provenance copied from manifest metadata.

    Composition retains this capture-time classification and does not infer it
    from rendered content or path presence.
    """

    summary: DiffSummaryResponse
    """File-wide text-row totals and captured-side existence.

    The aggregate spans composed text bays; image-only content contributes side
    existence without inventing line counts.
    """

    default_expanded: bool = True
    """Whether the HUD initially opens the File body.

    This is initial presentation policy only; it does not persist or override a
    reviewer's later explicit expansion choice.
    """

    frames: list[FrameResponse]
    """Complete composed document in format-defined order.

    Frames and their bays are the authoritative presentation sequence; consumers
    must not reconstruct a File-wide widget discriminator from them.
    """


class RepoFileEntryResponse(ApiModel):
    """Return one manifest File leaf before it is arranged into the tree.

    Manifest conversion places this value on one File tree leaf. The HUD uses
    its exact path pair and provenance for later File operations.

    At least one side path is present. `lazy` explains deferred loading or is
    `None` for ordinary loading. The entry has no captured path, display name,
    or rendered content.
    """

    file_kind: FileKindResponse
    """Tracked or untracked provenance and backend change classification.

    The HUD passes this capture-time metadata to File loading and must not derive
    it from path nullability or lazy policy.
    """

    left_path: str | None = None
    """Repository-relative left path, or `None` when absent.

    Absence is a real side-presence fact; at least one of the two paths is present
    and callers address the File by the exact pair.
    """

    right_path: str | None = None
    """Repository-relative right path, or `None` when absent.

    It may differ from the left path for renames and must not be normalized into
    a single path before follow-up requests.
    """

    # TODO: Lazy-info contains deferred Files only, so require the concrete
    # reason that `build_lazy_info_for_paths` always supplies.
    lazy: LazyReason | None = None
    """Reason to defer File rendering, or `None` for eager loading.

    Null means ordinary loading policy, not an unknown error; a present reason
    instructs the HUD to construct a deferred File placeholder.
    """


class RepoManifestFileNodeResponse(ApiModel):
    """Return one named File leaf in the recursive manifest tree.

    Tree consumers distinguish the leaf, show its final path component, and
    pass its exact File metadata to later File operations.

    A File node has no children and is not the File's standalone identity.
    """

    type: Literal["file"]
    """Discriminator for a manifest tree leaf.

    Consumers use it to stop recursion and read `entry`; it never denotes a
    directory whose children happen to be empty.
    """

    name: str
    """Final path component displayed for this File.

    It is presentation derived from the manifest path and is not sufficient to
    identify Files with the same basename in different directories.
    """

    entry: RepoFileEntryResponse
    """Exact side paths, provenance, and loading policy for the File.

    Tree placement supplies hierarchy only; callers retain this complete value
    for later File requests and placeholder construction.
    """


class RepoManifestDirectoryNodeResponse(ApiModel):
    """Return one directory in the recursive manifest tree.

    The HUD uses the complete repository-relative path as directory identity,
    shows its possibly compacted label, and renders children recursively in
    supplied order.

    The node organizes Files only. It carries no content or aggregate summary.
    """

    type: Literal["directory"]
    """Discriminator for a manifest tree branch.

    Consumers recurse through `entries`; the node itself is not a loadable File
    and cannot be supplied to File endpoints.
    """

    name: str
    """Displayed directory name, possibly compacting a single-child chain.

    Because compaction may include separators, consumers use `path` rather than
    this label for stable directory identity.
    """

    path: str
    """Complete repository-relative directory identity.

    It remains stable regardless of compacted display text and is the value used
    for directory expansion state.
    """

    entries: list[RepoManifestTreeEntryResponse]
    """File and directory children in backend-supplied order.

    The order reflects manifest construction; the HUD must not sort it and thereby
    separate it from backend File sequence.
    """


RepoManifestTreeEntryResponse = (
    RepoManifestFileNodeResponse | RepoManifestDirectoryNodeResponse
)
"""Return one node in the recursive manifest tree.

- `RepoManifestFileNodeResponse` terminates a branch with a File.
- `RepoManifestDirectoryNodeResponse` contains further nodes.

Consumers branch on `type`. The union is tree structure, not a filesystem
handle.
"""


class LazyInfoFileResponse(ApiModel):
    """Return everything the HUD needs to construct one deferred File card.

    Lazy-info conversion produces one value for each deferred File. The HUD uses
    it to construct a placeholder without copying fields from the manifest.

    The path pair and provenance identify the File; display name, known counts,
    and reason describe the placeholder. It contains no captured bytes, frames,
    or bays.
    """

    # This response must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    file_kind: FileKindResponse
    """Tracked or untracked provenance and backend change classification.

    The deferred placeholder receives the same manifest fact as a later loaded
    File, so loading must not change classification.
    """

    left_path: str | None = None
    """Repository-relative left path, or `None` when absent.

    Together with the right path it identifies exactly the deferred manifest File;
    null remains distinct from an empty File side.
    """

    right_path: str | None = None
    """Repository-relative right path, or `None` when absent.

    Rename pairs retain both paths so explicit loading can address the same capture.
    """

    display_name: str
    """Human-readable name of the deferred File pair.

    The HUD may show it on the placeholder, but later File requests still use the
    nullable path pair rather than this label.
    """

    changed_lines: int | None = None
    """Changed-line count, `None` until the File is rendered.

    Null means the deferred metadata cannot state the value, not zero changed
    lines; consumers preserve the unknown state.
    """

    added_lines: int | None = None
    """Added-line count, `None` until the File is rendered.

    It is supplied only when authoritative placeholder metadata exists and must
    not be inferred from File kind or side presence.
    """

    removed_lines: int | None = None
    """Removed-line count, `None` until the File is rendered.

    Consumers distinguish an unknown null from an authoritative zero when showing
    deferred summary information.
    """

    lazy: LazyReason | None = None
    """Reason for deferred loading; current lazy-info entries carry one.

    The optional model shape follows manifest metadata, but conversion currently
    emits only deferred Files and therefore supplies a concrete reason.
    """


class RepoManifestResponse(ApiModel):
    """Return one complete manifest and its retained Snapshot address.

    `snapshot_id` is exactly 32 lowercase hexadecimal characters and is the
    only Room lookup input accepted by follow-up endpoints. The HUD uses the
    labels, summary, and tree to build File navigation before loading content.

    The manifest contains File identity and loading metadata, not captured
    bytes, composed bays, or review discussions.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Opaque retained Snapshot key used by every follow-up endpoint.

    Clients must return it unchanged; it selects the Room and immutable capture
    without exposing a repository path or correspondence key.
    """

    display_name: str
    """Human-readable name of the complete comparison.

    It labels the Tab but is not an identity and may be shared by distinct Rooms
    or recaptures.
    """

    left_label: str
    """Human-readable label for the captured left side.

    It is retained with the Snapshot and does not imply that every File has a
    present left path.
    """

    right_label: str
    """Human-readable label for the captured right side.

    The label describes the comparison side even when an individual File is
    removed and has no right-side content.
    """

    summary: RepoDiffSummaryResponse
    """Repository-wide File totals and optional backend line totals.

    File counts are manifest-authoritative while nullable line totals remain
    unknown when the backend deferred or could not calculate them.
    """

    tree: list[RepoManifestTreeEntryResponse]
    """Every affected File arranged in the recursive directory tree.

    Each manifest File appears once in backend order; the tree contains metadata
    only and no composed File contents.
    """


class LazyInfoResponse(ApiModel):
    """Return deferred File-card metadata for one Snapshot.

    The HUD consumes `files` after manifest loading to create placeholders for
    every File awaiting explicit reviewer action.

    The response contains no eager File entries, captured bytes, or rendered
    bays.
    """

    files: list[LazyInfoFileResponse]
    """Deferred File entries in the manifest's backend path order.

    The collection excludes eager Files and lets the HUD create placeholders
    without joining partial data from the manifest tree.
    """


def selected_branch_selections(
    tab: TabParam = Query(description="HUD Tab."),
    base_source: BranchSourceParam | None = Query(
        default=None,
        description="Base branch source for a branch-backed Tab.",
    ),
    base_remote: str | None = Query(
        default=None,
        description="Base remote for remote branch-backed selections.",
    ),
    base_branch: str | None = Query(
        default=None,
        description="Base branch name for a branch-backed Tab.",
    ),
    review_source: BranchSourceParam | None = Query(
        default=None,
        description="Review branch source for a branch-backed Tab.",
    ),
    review_remote: str | None = Query(
        default=None,
        description="Review remote for remote branch-backed selections.",
    ),
    review_branch: str | None = Query(
        default=None,
        description="Review branch name for a branch-backed Tab.",
    ),
) -> BranchSelections:
    """Return structured branch selections for Branch Review.

    The UI can keep base/review branch controls populated while the user moves
    between Tabs. API handlers call this helper so branch parameters do not
    accidentally influence another Tab's manifest.

    # Parameters

    - `tab`: Selected HUD Tab; only Branch Review consumes the other values.
    - `base_source`: Local or remote namespace for the base branch.
    - `base_remote`: Required remote name when the base source is remote.
    - `base_branch`: Required symbolic base branch name.
    - `review_source`: Local or remote namespace for the review branch.
    - `review_remote`: Required remote name when the review source is remote.
    - `review_branch`: Required symbolic review branch name.

    Non-Branch-Review Tabs return two absent selections without validating
    controls they do not use.

    # Usage

    Install this function as the manifest route dependency. Callers unpack the
    returned base and review selections in that order.

    # Failures

    - Raises `HTTPException` with status 400 when Branch Review parameters are
      missing, blank, or internally inconsistent.
    """
    if tab != "branch-review":
        return None, None

    return (
        _branch_selection_from_query(
            label="base",
            source=base_source,
            remote=base_remote,
            branch=base_branch,
        ),
        _branch_selection_from_query(
            label="review",
            source=review_source,
            remote=review_remote,
            branch=review_branch,
        ),
    )


def preset_project_parts(
    *,
    project_id: str | None,
    preset_subset: str | None,
) -> tuple[str, str]:
    """Parse the preset catalog id and subset used to prepare a manifest.

    The Preset Tab uses `project_id` as the catalog id. It is the name of a
    directory under the presets root. `preset_subset` is the selected
    group within that catalog. Both are required; whether the catalog exists is
    settled by `preset_backend_for_catalog`, which is the one place that reads
    the presets root, so this parser does not carry a second copy of the
    catalog set. Follow-up endpoints find the prepared Room by Snapshot key and
    do not call this parser. The preset backend still validates traversal and
    unknown-group errors while preparing the manifest.

    # Parameters

    - `project_id`: Nonblank catalog directory id carried by the common
      manifest parameter.
    - `preset_subset`: Nonblank fixture-group id within that catalog.

    # Usage

    Call only for the Preset Tab while translating manifest parameters. Use the
    returned catalog id to choose a backend and the subset as its capture side.

    # Returns

    - First, the unchanged catalog id used to select `PresetBackend`.
    - Second, the unchanged fixture-group id used as that backend's capture side.

    # Failures

    - Raises `DirdiffError` when either value is absent or blank.
    """
    if project_id is None or project_id.strip() == "":
        raise DirdiffError("project_id is required for the Preset Tab.")
    if preset_subset is None or preset_subset.strip() == "":
        raise DirdiffError("preset_subset is required for the Preset Tab.")
    return project_id, preset_subset


def marked_project_id(project_id: str | None) -> int:
    """Parse a positive Mark id from a repo-backed HTTP parameter.

    Manifest uses the result to construct the workspace backend. Follow-up
    operations never call this parser because their Snapshot id is sufficient.

    # Usage

    Call while translating a repository-backed manifest selection, then use the
    positive integer for `RepoMarkStore` and Room correspondence.

    # Failures

    - Raises `DirdiffError` when the value is absent, blank, nonnumeric, or not
      positive.
    """
    if project_id is None or project_id.strip() == "":
        raise DirdiffError("project_id is required for repo-backed Tabs.")
    try:
        parsed_project_id = int(project_id)
    except ValueError as exc:
        raise DirdiffError(f"Invalid project_id: {project_id}") from exc
    if parsed_project_id <= 0:
        raise DirdiffError(f"Invalid project_id: {project_id}")
    return parsed_project_id


def _branch_selection_from_query(
    *,
    label: str,
    source: BranchSourceParam | None,
    remote: str | None,
    branch: str | None,
) -> BranchSelection:
    """Parse one split Branch Review selection from query parameters.

    Used only while building a manifest for a branch-backed Tab. Follow-up file
    endpoints use the snapshot id returned by that manifest request.

    # Parameters

    - `label`: `base` or `review`, used to identify invalid input precisely.
    - `source`: Required local or remote branch namespace.
    - `remote`: Required nonblank remote name only for a remote source.
    - `branch`: Required nonblank symbolic branch name.

    # Usage

    `selected_branch_selections` calls this once for base and once for review.
    The label is used only to produce a precise HTTP error.

    # Failures

    - Raises `HTTPException` with status 400 when source, remote, and branch do
      not form one complete local or remote selection.
    """
    if source is None:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_source is required for the Branch Review Tab.",
        )
    if branch is None or (branch_name := branch.strip()) == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_branch is required for the Branch Review Tab.",
        )
    if source == "local":
        return {"source": source, "branch": branch_name}
    if remote is None or (remote_name := remote.strip()) == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_remote is required for remote Branch Review selections.",
        )
    return {
        "source": source,
        "remote": remote_name,
        "branch": branch_name,
    }


class _Server:
    """Bind application-lifetime interfaces to dirdiff HTTP handlers.

    One instance retains the stores, Room service, and preset root used by one
    FastAPI application. Its decorated methods are ordinary functions until
    route registration validates and binds them during create_app.
    """

    routes = ClassRoutes()
    """Import-time declarations shared without retaining runtime state."""

    def __init__(
        self,
        db: RepoMarkStore,
        user_profile_store: UserProfileStore,
        preferences_store: PreferencesStore,
        *,
        room_lord: RoomLord,
        presets_root: str | None,
    ) -> None:
        """Retain the interfaces required by HTTP orchestration.

        The caller supplies concrete stores and one Room service for the full
        application lifetime. Construction performs no route registration.

        # Parameters

        - `db`: Repository registry used by repository-facing routes.
        - `user_profile_store`: Profile persistence used by review routes.
        - `preferences_store`: Preference persistence used by HUD routes.
        - `room_lord`: Room selection and Snapshot lookup interface.
        - `presets_root`: Optional root scanned for preset catalogs.
        """
        self.db = db
        self.user_profile_store = user_profile_store
        self.preferences_store = preferences_store
        self.room_lord = room_lord
        self.presets_root = presets_root

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

    @routes.exception_handler(Exception)
    async def serve_unexpected_error(
        self,
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        """Log an unexpected HTTP failure before returning a generic response.

        # Parameters

        - `request`: Failed HTTP entity whose method and path identify damage.
        - `error`: Unexpected exception recorded with its traceback.

        The response discloses neither exception text nor traceback.

        # Usage

        FastAPI invokes this last-resort handler after typed domain and framework
        failures have taken their narrower paths.

        """
        LOGGER.error(
            "Unexpected %s %s failure",
            request.method,
            request.url.path,
            exc_info=error,
        )
        return JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error."},
        )

    def preset_catalog_dirs(self) -> tuple[PresetCatalogDir, ...]:
        """List the preset catalogs this server offers right now.

        The presets root is rescanned per request rather than captured at
        startup, so a catalog directory added while the server runs appears on
        the next refresh, which is how every other hot-reloadable part of this
        project behaves.

        # Usage

        Call for each preset listing or catalog lookup so hot-added directories
        are visible without rebuilding the application.

        # Returns

        - Each item contains one visible catalog's id, configured display name,
          and root path used to construct its backend.
        - Items follow catalog-id order. An empty tuple means the current root
          has no catalog directories.
        """
        root = (
            Path(self.presets_root)
            if self.presets_root is not None
            else Path.cwd() / "tests" / "presets"
        )
        return preset_catalogs(root)

    def preset_backend_for_catalog(self, catalog_id: str) -> PresetBackend:
        """Construct the backend reading one named preset catalog.

        This is the only place a catalog id is checked against the catalogs
        that exist; an id no directory answers to is refused here rather than
        producing an empty listing. Catalogs exercise different product
        surfaces and each is a directory, so nothing but the directory listing
        decides which ones a request may name.

        # Usage

        Parse a nonblank catalog id first, then use the returned backend for the
        selected subset's capture.

        # Failures

        - Raises `DirdiffError` when no current preset catalog has the exact id.
        """
        for catalog in self.preset_catalog_dirs():
            if catalog.catalog_id == catalog_id:
                return PresetBackend(catalog.root)
        raise DirdiffError(f"Unknown preset catalog: {catalog_id}")

    def preset_catalog_response(
        self,
        catalog: PresetCatalogDir,
    ) -> PresetCatalogResponse:
        """Serialize one preset catalog for the presets endpoint.

        Preset catalog selection and engine selection are independent UI
        controls, so this endpoint exposes catalog metadata without involving a
        renderer.

        # Usage

        Map each value from `preset_catalog_dirs` through this helper when
        building the preset-list response.
        """
        preset_backend = PresetBackend(catalog.root)
        return PresetCatalogResponse.model_validate(
            {
                "id": catalog.catalog_id,
                "name": catalog.name,
                "default_preset": preset_backend.default_preset_name(),
                "groups": preset_backend.list_preset_groups(),
            }
        )

    def manifest_capture_selection(
        self,
        *,
        project_id: str,
        tab: TabParam,
        branch_selections: BranchSelections,
        left: str | None,
        right: str | None,
        pull_request_url: str | None,
        left_commit: str | None,
        right_commit: str | None,
        preset_subset: str | None,
        show_untracked: bool,
    ) -> CaptureSelection:
        """Translate one manifest query into its concrete Tab selection.

        The query parameters carry every cross-Tab value, so this is the
        single boundary that rejects a missing Tab-required value and asserts
        that inapplicable values were not supplied. Downstream code receives
        one discriminated selection and never branches on nullability.

        # Parameters

        - `project_id`: Mark id or preset catalog id, interpreted by the Tab.
        - `tab`: Tab whose law determines the valid parameter combination.
        - `branch_selections`: Structured base/review pair from the dependency.
        - `left`: Left revision handle used by Head or Refs.
        - `right`: Right revision handle used by Head or Refs.
        - `pull_request_url`: Canonical Pull Request Room correspondence value.
        - `left_commit`: Prepared Pull Request merge-base capture commit.
        - `right_commit`: Prepared Pull Request head capture commit.
        - `preset_subset`: Fixture-group id for a Preset capture.
        - `show_untracked`: Whether supported repository Tabs include untracked
          worktree Files.

        # Usage

        Call once at the manifest boundary after dependency parsing, then pass
        the returned selection unchanged to `capture_snapshot`.

        # Failures

        - Raises `DirdiffError` when a required Tab value is absent or blank.
        - Asserts when parameters inapplicable to the selected Tab are present or
          the dependency supplied an impossible branch-selection pair.
        """
        selected_base, selected_review = branch_selections
        if tab == "preset":
            assert selected_base is None and selected_review is None
            assert left is None and right is None
            assert pull_request_url is None
            assert left_commit is None and right_commit is None
            assert not show_untracked, (
                "the Preset Tab does not support intruding Files"
            )
            catalog, subset = preset_project_parts(
                project_id=project_id,
                preset_subset=preset_subset,
            )
            return PresetCaptureSelection(catalog=catalog, subset=subset)
        if tab == "pull-request":
            assert selected_base is None and selected_review is None
            assert left is None and right is None
            assert preset_subset is None
            assert not show_untracked, (
                "the Pull Request Tab does not support intruding Files"
            )
            if pull_request_url is None:
                raise DirdiffError(
                    "pull_request_url is required for the Pull Request Tab."
                )
            if left_commit is None:
                raise DirdiffError(
                    "left_commit is required for the Pull Request Tab."
                )
            if right_commit is None:
                raise DirdiffError(
                    "right_commit is required for the Pull Request Tab."
                )
            return PullRequestCaptureSelection(
                url=pull_request_url,
                left_commit=left_commit,
                right_commit=right_commit,
            )
        if tab == "branch-review":
            assert left is None and right is None
            assert pull_request_url is None
            assert left_commit is None and right_commit is None
            assert preset_subset is None
            assert not show_untracked, (
                "the Branch Review Tab does not support intruding Files"
            )
            if selected_base is None or selected_review is None:
                raise DirdiffError("branch selections are required.")
            return BranchReviewCaptureSelection(
                base=selected_base,
                review=selected_review,
            )
        assert tab == "head" or tab == "refs"
        assert selected_base is None and selected_review is None
        assert pull_request_url is None
        assert left_commit is None and right_commit is None
        assert preset_subset is None
        if tab == "head":
            if left is None or right is None:
                raise DirdiffError(
                    "Diff against HEAD requires HEAD and worktree sides."
                )
        else:
            if left is None or left.strip() == "":
                raise DirdiffError("left is required for the Refs Tab.")
            if right is None or right.strip() == "":
                raise DirdiffError("right is required for the Refs Tab.")
        return RevisionsCaptureSelection(
            tab=tab,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def capture_snapshot(
        self,
        *,
        project_id: str,
        selection: CaptureSelection,
    ) -> tuple[Room, UUID, str | None]:
        """Capture one exact Tab selection for browser and agent callers.

        The caller supplies the concrete selection variant. This operation
        selects its active mark or preset backend, applies Room
        correspondence, and returns the immutable Snapshot address plus the
        validated preset subset used only for its display name.

        # Parameters

        - `project_id`: Active Mark id for repository selections or catalog id
          for a preset selection.
        - `selection`: Complete discriminated Tab input already validated.

        # Usage

        Pass the result of `manifest_capture_selection` or an agent Tab
        conversion. Keep the returned Room and Snapshot id together for all
        follow-up work.

        # Returns

        - First, the Room selected by the Tab's correspondence law.
        - Second, the immutable Snapshot id captured inside that Room.
        - Third, the validated preset subset used for display when `selection`
          is a Preset Tab, or `None` for every repository-backed Tab because
          those selections have no preset display suffix.

        # Failures

        - Raises `DirdiffError` when the Mark or preset catalog is absent, or
          when backend preparation and capture reject the selection.
        """
        preset_name: str | None = None
        parsed_project_id: int | None = None
        if isinstance(selection, PresetCaptureSelection):
            preset_name = selection.subset
            backend: WorkspaceBackendProtocol = self.preset_backend_for_catalog(
                selection.catalog
            )
        else:
            parsed_project_id = marked_project_id(project_id)
            mark = self.db.get(parsed_project_id)
            if mark is None:
                raise DirdiffError(f"Invalid project_id: {parsed_project_id}")
            backend = GitBackend.discover(repo_root=Path(mark.path))
        room, snapshot_id = self.room_lord.corresponding_room(
            mark_id=parsed_project_id,
            backend=backend,
            selection=selection,
        )
        return room, snapshot_id, preset_name

    def render_loaded_snapshot_file(
        self,
        *,
        room: Room,
        snapshot_id: UUID,
        engine_name: EngineKind,
        pair: FilePair,
        left_file: Optional[Path],
        right_file: Optional[Path],
        file_meta: FileMeta,
    ) -> ComposedDiffResponse:
        """Compose one focused File into its `/api/file-diff` response payload.

        The handler does HTTP work only: it checks the capture error, reads the
        two captured byte sides, builds one `ComposeContext`, calls `compose()`,
        and attaches the two envelope fields composition does not produce -- the
        File's display name and file kind, which the manifest already settled.
        Decoding, engine selection, and payload assembly belong to composition.

        # Parameters

        - `room`: Room supplying retained labels and Tab presentation rules.
        - `snapshot_id`: Exact Snapshot containing the loaded File.
        - `engine_name`: Requested renderer selected only for text bays.
        - `pair`: Exact nullable repository paths identifying the File.
        - `left_file`: Absolute captured left side, or `None` when absent.
        - `right_file`: Absolute captured right side, or `None` when absent.
        - `file_meta`: Persisted provenance, change type, lazy policy, and
          capture failure for this pair.

        # Usage

        Call after `Room.get` validates the exact File pair. Return the
        validated model directly rather than reshaping composed frames or bays.

        # Failures

        - Raises `DirdiffError` when capture recorded a File failure.
        - Propagates captured-file I/O, engine, composition, and response
          validation failures.
        """
        if file_meta["capture_error"] is not None:
            raise DirdiffError(file_meta["capture_error"])
        snapshot_meta = room.meta(snapshot_id)
        left_bytes = left_file.read_bytes() if left_file is not None else None
        right_bytes = (
            right_file.read_bytes() if right_file is not None else None
        )
        context = ComposeContext.build(
            left_path=pair.left_path,
            right_path=pair.right_path,
            left_label=snapshot_meta["left_label"],
            right_label=snapshot_meta["right_label"],
            renderer=engine(engine_name),
        )
        composed = Composer().compose(left_bytes, right_bytes, context)
        # TODO: the frontend should probably take the display name and file kind
        # from the manifest, which already emits one per File. Until then the
        # HTTP boundary attaches both here: the two envelope fields composition
        # deliberately does not produce.
        file_kind: Literal["git", "untracked"] = (
            "git" if file_meta["tracked"] else "untracked"
        )
        display_name = (
            pair.right_path
            if snapshot_meta["tab"] == "preset" and pair.right_path is not None
            else display_name_for_repo_paths(pair.left_path, pair.right_path)
        )
        return ComposedDiffResponse.model_validate(
            {
                **composed,
                "display_name": display_name,
                "file_kind": file_kind_for_change_type(
                    file_meta["change_type"],
                    file_kind=file_kind,
                ),
            }
        )

    def snapshot_room(self, snapshot_id: UUID) -> Room:
        """Return the Room containing one exact agent-selected Snapshot.

        Agent routes call this boundary before any placement or action read. It
        preserves the requested opaque identity and converts a missing retained
        Snapshot into the concrete diagnostic expected by their plain-text failure path.

        # Parameters

        - `snapshot_id`: Exact retained capture selected by the agent request.

        # Usage

        Agent routes call this before reading paths, Threads, or review actions.

        # Failures

        - Raises `DirdiffError` when the Snapshot is unknown.
        """
        try:
            return self.room_lord.find_room(snapshot_id)
        except DirdiffError:
            raise DirdiffError(
                f"Unknown snapshot id: {snapshot_id.hex}"
            ) from None

    def agent_failure(
        self, status: HTTPStatus, detail: str
    ) -> PlainTextResponse:
        """Return one concrete diagnostic for a rejected agent operation.

        # Parameters

        - `status`: Non-success HTTP status matching the rejection boundary.
        - `detail`: Plain text safe for an agent to report without parsing JSON.

        # Usage

        Agent routes return this value for expected failures instead of raising
        a JSON-wrapped framework exception.

        """
        return PlainTextResponse(detail, status_code=status)

    def agent_preview(
        self, body: str | None, deleted: bool
    ) -> AgentCommentPreview:
        """Bound one Comment body to the shared 256-character preview rule.

        # Parameters

        - `body`: Current Comment text, or `None` for a tombstone.
        - `deleted`: Folded deletion state retained even when text is absent.

        # Usage

        Use only for summary and activity responses. Complete Thread responses
        retain the full Comment body.

        """
        if body is None:
            return AgentCommentPreview(
                body=None, deleted=deleted, truncated=False
            )
        if len(body) <= 256:
            return AgentCommentPreview(
                body=body, deleted=deleted, truncated=False
            )
        return AgentCommentPreview(
            body=f"{body[:255]}…", deleted=deleted, truncated=True
        )

    def placed_file_pair(
        self, origin: ReviewOriginView, placement: ThreadPlacementView
    ) -> tuple[str | None, str | None] | None:
        """Return the File pair a placement rests on, or `None` for no File.

        A placement states no File pair of its own. The pair comes from the
        origin, so the only question here is whether this Snapshot holds
        captured bytes under it. `file-absent` names no File at all, and
        `file-unreadable` names one whose capture retains dirdiff's placeholder
        text, so neither addresses code an agent may read.

        # Parameters

        - `origin`: Immutable File pair and selected side of the discussion.
        - `placement`: Selected-Snapshot landing that decides whether readable
          captured code remains.

        # Usage

        Use while collecting the exact File pairs that need captured-path
        validation for one agent response.

        # Returns

        - First, the origin's left repository path, or `None` when that readable
          File has no left side.
        - Second, the origin's right repository path, or `None` when that
          readable File has no right side.
        - `None`: The placement is `file-absent` or `file-unreadable`, so an
          agent must not receive a captured path for it.
        """
        if placement["kind"] in ("file-absent", "file-unreadable"):
            return None
        pair = origin["file"]
        return pair["left_path"], pair["right_path"]

    def captured_files_for_placements(
        self,
        room: Room,
        snapshot_id: UUID,
        views: list[ThreadDiscussionView] | list[ThreadSummaryView],
    ) -> dict[tuple[str | None, str | None], tuple[Path | None, Path | None]]:
        """Load actual retained paths for exactly the Files placements rest on.

        Only the Files the supplied Threads landed in are read; a Thread whose
        placement rests on no File contributes nothing. Contents are never
        read.

        # Parameters

        - `room`: Bound Room that validates placement File pairs.
        - `snapshot_id`: Exact selected Snapshot holding any landed Files.
        - `views`: Discussion or summary values whose placements need paths.

        # Usage

        Pass one already bounded set of views, then reuse the returned mapping
        for every `agent_thread` conversion in that response.

        # Returns

        - Each key is one distinct repository path pair from a readable
          placement. Its first item is the nullable left path and its second is
          the nullable right path. Views without a readable File add no key.
        - Each value's first item is the optional absolute left capture path.
        - Each value's second item is the optional absolute right capture path.
          An empty mapping means none of the views landed on a readable File.
        """
        pairs: list[tuple[str | None, str | None]] = []
        for view in views:
            pair = self.placed_file_pair(
                view["origin_target"], view["placement"]
            )
            if pair is not None:
                pairs.append(pair)
        return room.captured_files_for_pairs(snapshot_id, tuple(pairs))

    def agent_placement(
        self,
        captured_files: dict[
            tuple[str | None, str | None], tuple[Path | None, Path | None]
        ],
        origin: ReviewOriginView,
        placement: ThreadPlacementView,
    ) -> tuple[str | None, AgentBayRange | AgentBayStart | None]:
        """Translate one placement into its captured File path and bay.

        The File pair and side come from the origin, which is where the
        response states them; the placement contributes the bay it landed in
        and the range inside it. A placement resting on no File yields
        neither, and one naming no bay yields the captured File path alone.

        # Parameters

        - `captured_files`: Authenticated retained side paths indexed by exact
          placement File pair.
        - `origin`: Immutable File, side, and original bay facts.
        - `placement`: Current landing to translate without recomputation.

        # Usage

        Call only with the Snapshot-validated mapping from
        `captured_files_for_placements` and the origin and placement from the
        same view.

        # Returns

        - First, the authenticated captured path for the origin's selected side,
          or `None` when the placement rests on no readable File.
        - Second, the landed bay range for kept or changed content, the bay start
          for a lost region or bay, or `None` when the placement names no bay.

        # Failures

        - Raises `AssertionError` when a readable placement lacks a validated
          File or selected captured side.
        """
        pair = self.placed_file_pair(origin, placement)
        if pair is None:
            return None, None
        left_file, right_file = captured_files[pair]
        # An equal File pair holds equal side presence, so the origin's own
        # side was captured wherever the placement rests.
        selected_file = left_file if origin["side"] == "left" else right_file
        assert selected_file is not None, (
            "a placement rests on a side this Snapshot did not capture"
        )
        bay: AgentBayRange | AgentBayStart | None = None
        if placement["kind"] in (
            "region-kept",
            "region-changed",
            "region-lost",
        ):
            assert origin["kind"] == "text"
            origin_bay = origin["bay"]
            if placement["kind"] == "region-lost":
                bay = AgentBayStart.model_validate(origin_bay)
            else:
                landed = placement["range"]
                bay = AgentBayRange.model_validate({**origin_bay, **landed})
        elif placement["kind"] == "bay-lost":
            landed_bay = placement["bay"]
            bay = AgentBayStart.model_validate(landed_bay)
        return str(selected_file), bay

    def agent_outdated_reason(
        self,
        placement: ThreadPlacementView,
    ) -> AgentOutdatedReason | None:
        """Translate one placement into the agent boundary's outdated name.

        Every placement maps to exactly one name, and the two that report
        nothing wrong map to `None`. Both bay-level losses map to
        `bay_not_found`: the agent shape has no name for the difference
        between landing in another bay and landing in none.

        # Usage

        Use this while translating a domain placement into one agent response.

        # Returns

        - `region_changed`, `region_not_found`, `bay_not_found`, `file_missing`,
          or `file_unreadable` for the corresponding degraded placement.
        - `None`: A `region-kept` or `whole-file` placement remains current, so
          the agent response must not claim an outdated reason.

        # Failures

        - Raises `AssertionError` when a placement kind is outside the complete
          review placement union.
        """
        match placement["kind"]:
            case "region-kept" | "whole-file":
                return None
            case "region-changed":
                return "region_changed"
            case "region-lost":
                return "region_not_found"
            case "bay-lost" | "side-lost":
                return "bay_not_found"
            case "file-absent":
                return "file_missing"
            case "file-unreadable":
                return "file_unreadable"
        raise AssertionError(f"unknown placement kind {placement['kind']!r}")

    def agent_thread(
        self,
        captured_files: dict[
            tuple[str | None, str | None], tuple[Path | None, Path | None]
        ],
        view: ThreadDiscussionView,
    ) -> AgentThread:
        """Translate one discussion using its existing captured File path.

        # Parameters

        - `captured_files`: Authenticated paths for every located placement in
          this response batch.
        - `view`: Complete current discussion with origin and current landing.

        # Usage

        Convert a complete domain discussion after validating all placement
        Files for its response batch.

        """
        origin = view["origin_target"]
        file_path, bay = self.agent_placement(
            captured_files, origin, view["placement"]
        )
        # The excerpt travels inside the text origin it is cut from; a
        # File-level origin has none to carry.
        excerpt = origin.get("excerpt")
        comments = [
            AgentComment(
                comment_id=comment["comment_id"],
                author=AgentAuthor(
                    profile_id=comment["author"]["profile_id"],
                    name=comment["author"]["display_name"],
                ),
                body=comment["body"],
                deleted=comment["deleted"],
                created_at=comment["created_at"],
                updated_at=comment["updated_at"],
            )
            for comment in view["comments"]
        ]
        return AgentThread(
            thread_id=view["thread_id"],
            snapshot_id=view["snapshot_id"],
            status=view["state"],
            attention=view["attention"],
            file=file_path,
            bay=bay,
            original_excerpt=(
                ReviewExcerptResponse.model_validate(excerpt)
                if excerpt is not None
                else None
            ),
            outdated_reason=self.agent_outdated_reason(view["placement"]),
            comments=comments,
        )

    def agent_page[AgentPageItem](
        self,
        items: list[AgentPageItem],
        page: int,
        limit: int,
        total: int,
        through_activity_id: int | None = None,
    ) -> AgentPage[AgentPageItem]:
        """Build one valid one-based page response.

        # Parameters

        - `items`: Endpoint-specific values already sliced for this page.
        - `page`: Positive requested page number.
        - `limit`: Positive requested page capacity.
        - `total`: Matching count measured at the same read boundary.
        - `through_activity_id`: Optional inclusive discussion pivot that later
          pages must repeat.

        # Usage

        Pass values already sliced by the domain read and counts measured at the
        same activity boundary.

        # Returns

        - `items`, `page`, `limit`, and `total` preserve the supplied bounded
          values; `has_more` states whether another page position exists.
        - `through_activity_id` preserves the optional inclusive discussion
          pivot so callers can repeat it for later pages.

        # Failures

        - Pydantic validation rejects nonpositive page values, impossible counts,
          or a pivot outside the response contract.
        """
        return AgentPage[AgentPageItem](
            items=items,
            page=page,
            limit=limit,
            total=total,
            has_more=page * limit < total,
            through_activity_id=through_activity_id,
        )

    @routes.exception_handler(RequestValidationError)
    async def validation_failure(
        self, request: Request, exc: RequestValidationError
    ) -> Response:
        """Return validation detail at the agent API boundary.

        # Parameters

        - `request`: HTTP entity whose matched route selects the response shape.
        - `exc`: Pydantic validation failures to bound and serialize.

        Non-agent routes delegate to FastAPI's standard JSON handler.

        # Usage

        FastAPI invokes this handler for request-model validation failures. It
        selects plain text only for matched agent route templates.

        """
        route = request.scope.get("route")
        if getattr(route, "path", None) in _AGENT_ROUTE_PATHS:
            errors = exc.errors()
            bounded = errors[:20]
            detail = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: "
                f"{error['msg']}"
                for error in bounded
            )
            if len(errors) > len(bounded):
                detail += f"; {len(errors) - len(bounded)} more errors"
            return self.agent_failure(HTTPStatus.UNPROCESSABLE_ENTITY, detail)
        return await request_validation_exception_handler(request, exc)

    @routes.exception_handler(StarletteHTTPException)
    async def http_failure(
        self, request: Request, exc: StarletteHTTPException
    ) -> Response:
        """Return framework failure detail at the agent API boundary.

        # Parameters

        - `request`: HTTP entity whose matched route selects the response shape.
        - `exc`: Framework status and detail produced by routing or handlers.

        Non-agent routes delegate to FastAPI's standard JSON handler.

        # Usage

        FastAPI invokes this handler for framework HTTP exceptions. It preserves
        standard JSON outside the agent route set.

        """
        route = request.scope.get("route")
        if getattr(route, "path", None) in _AGENT_ROUTE_PATHS:
            return self.agent_failure(
                HTTPStatus(exc.status_code), str(exc.detail)
            )
        return await http_exception_handler(request, exc)

    @routes.get("/", response_class=HTMLResponse)
    def serve_frontend_missing(self) -> HTMLResponse:
        """Explain that the development API has no bundled HUD to serve.

        The local startup flow expects Vite to serve the browser UI separately.
        This fixed unavailable response points a human to that process and does
        not probe, start, or substitute for it.

        # Usage

        The root development route returns this fixed response when no bundled
        frontend is installed.
        """
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="en">
              <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>dirdiff frontend unavailable</title>
                <style>
                  body {
                    margin: 0;
                    background: #fbfaf7;
                    color: #24231f;
                    font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  }
                  main {
                    max-width: 640px;
                    margin: 72px auto;
                    padding: 0 24px;
                  }
                  h1 {
                    margin: 0 0 12px;
                    font-size: 28px;
                  }
                  p {
                    color: #625f58;
                    line-height: 1.45;
                  }
                  code {
                    color: #24231f;
                    font-weight: 700;
                  }
                </style>
              </head>
              <body>
                <main>
                  <h1>Oops, the Vite frontend is not running.</h1>
                  <p>
                    dirdiff's API server is up, but the browser UI is served by
                    Vite during local runs. Start dirdiff without
                    <code>--no-frontend-dev</code>, or check the terminal for why
                    Vite refused to start.
                  </p>
                </main>
              </body>
            </html>
            """,
            status_code=503,
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

    @routes.post(
        "/api/agent/join_review",
        response_model=NewAgentReviewResponse,
    )
    def join_agent_review(
        self,
        request: NewAgentReviewRequest,
    ) -> NewAgentReviewResponse | PlainTextResponse:
        """Register one disposable Profile and capture its explicit Tab.

        The endpoint rejects a reused agent UUID before capture, resolves the
        request's exact Pull Request or marked-repository Tab, then creates the
        Profile only after the Snapshot succeeds. The response binds Profile,
        Snapshot path, activity cursor, and attention counts at one initial review
        boundary; domain failures are returned as the agent plain-text contract.

        # Parameters

        - `request`: Fresh agent identity, display name, and discriminated Tab selection.

        # Returns

        - `NewAgentReviewResponse` after both capture and Profile registration
          succeed. It binds the new Profile to the Snapshot path, activity
          cursor, and attention counts from the initial review boundary.
        - `PlainTextResponse` with the agent error status and explanation when
          validation, capture, or registration fails; no successful agent
          session can be inferred from this variant.

        # Failures

        - Returns plain text for a reused or invalid agent identity, missing Mark,
          unsupported Tab input, Pull Request preparation failure, or capture
          failure. Profile creation happens only after capture succeeds.
        """
        try:
            if self.user_profile_store.agent_exists(request.agent_uuid):
                raise DirdiffError("Agent UUID already exists.")
            tab = request.tab
            if isinstance(tab, AgentPullRequestTab):
                prepared = prepare_pull_request(
                    url=tab.url, repo_marks=self.db.list()
                )
                room, snapshot_id, _ = self.capture_snapshot(
                    project_id=str(prepared.project_id),
                    selection=PullRequestCaptureSelection(
                        url=prepared.pull_request_url,
                        left_commit=prepared.left_commit,
                        right_commit=prepared.right_commit,
                    ),
                )
            else:
                matches = [
                    mark
                    for mark in self.db.list()
                    if mark.path == tab.repo_path
                ]
                if len(matches) != 1:
                    raise DirdiffError("Tab path does not identify one Mark.")
                project_id = str(matches[0].id)

                def branch(value: AgentBranch) -> BranchSelection:
                    """Translate one explicit API branch to backend input.

                    A missing remote produces the local branch shape; a concrete
                    remote produces the remote shape with the same symbolic name.
                    The validated API value is copied without resolving a ref or
                    choosing defaults.

                    # Parameters

                    - `value`: Explicit agent branch name and optional remote.
                    """
                    if value.remote is None:
                        return {"source": "local", "branch": value.name}
                    return {
                        "source": "remote",
                        "remote": value.remote,
                        "branch": value.name,
                    }

                selection: CaptureSelection
                if isinstance(tab, AgentHeadTab):
                    selection = RevisionsCaptureSelection(
                        tab="head",
                        left="HEAD",
                        right="worktree",
                        show_untracked=True,
                    )
                elif isinstance(tab, AgentRefsTab):
                    selection = RevisionsCaptureSelection(
                        tab="refs",
                        left=tab.left,
                        right=tab.right,
                        show_untracked=False,
                    )
                else:
                    assert isinstance(tab, AgentBranchReviewTab)
                    selection = BranchReviewCaptureSelection(
                        base=branch(tab.base),
                        review=branch(tab.review),
                    )
                room, snapshot_id, _ = self.capture_snapshot(
                    project_id=project_id,
                    selection=selection,
                )
            try:
                profile = self.user_profile_store.create_agent(
                    request.name, request.agent_uuid
                )
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            last_activity_id = room.latest_activity_id(snapshot_id)
            return NewAgentReviewResponse(
                profile_id=profile.id,
                snapshot_id=snapshot_id.hex,
                last_activity_id=last_activity_id,
                snapshot_path=str(room.path_for_snapshot(snapshot_id)),
                attention_counts=room.review_attention_counts(
                    snapshot_id, last_activity_id
                ),
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent new-review request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get(
        "/api/agent/thread_summary",
        response_model=AgentPage[AgentThreadSummary],
    )
    def agent_thread_summary(
        self,
        snapshot_id: UUID,
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> AgentPage[AgentThreadSummary] | PlainTextResponse:
        """Return a bounded discovery page of unresolved Threads.

        # Parameters

        - `snapshot_id`: Exact Snapshot whose open placements are summarized.
        - `page`: Positive one-based discovery page.
        - `limit`: Maximum summaries returned, capped at one hundred.

        # Returns

        - `AgentPage[AgentThreadSummary]` containing the requested open-Thread
          summaries, matching total, page bounds, and no discussion pivot.
        - `PlainTextResponse` when the Snapshot or review read is invalid; its
          body is an agent-facing diagnostic, not a partial page.

        # Failures

        - Returns plain text with status 400 for an unknown Snapshot. Invalid
          pagination is rejected by FastAPI before the handler runs.
        """
        try:
            room = self.snapshot_room(snapshot_id)
            page_threads, total, _concrete_activity_id = room.threads(
                snapshot_id,
                page=page,
                limit=limit,
                state="open",
                through_activity_id=None,
            )
            views = [thread.summary() for thread in page_threads]
            captured_files = self.captured_files_for_placements(
                room, snapshot_id, views
            )
            summaries = []
            for view in views:
                assert view["state"] == "open"
                attention = view["attention"]
                assert attention != "none"
                file_path, bay = self.agent_placement(
                    captured_files, view["origin_target"], view["placement"]
                )
                first = view["first_comment"]
                latest = view["latest_comment"]
                summaries.append(
                    AgentThreadSummary(
                        thread_id=view["thread_id"],
                        status="open",
                        attention=attention,
                        file=file_path,
                        bay=bay,
                        first_comment=self.agent_preview(
                            first["body"], first["deleted"]
                        ),
                        latest_comment=self.agent_preview(
                            latest["body"], latest["deleted"]
                        ),
                        comment_count=view["comment_count"],
                    )
                )
            return self.agent_page(summaries, page, limit, total)
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent Thread-summary request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/agent/threads", response_model=AgentPage[AgentThread])
    def agent_threads(
        self,
        snapshot_id: UUID,
        for_role: Literal["author", "reviewer"] | None = Query(
            default=None, alias="for"
        ),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=5, ge=1, le=20),
        through_activity_id: int | None = Query(default=None, ge=0),
    ) -> AgentPage[AgentThread] | PlainTextResponse:
        """Return complete unresolved Threads in bounded batches.

        # Parameters

        - `snapshot_id`: Exact Snapshot whose open placements are returned.
        - `for_role`: Optional author or reviewer attention inbox filter.
        - `page`: Positive one-based Thread page.
        - `limit`: Maximum complete discussions returned, capped at twenty.
        - `through_activity_id`: Inclusive pivot chosen with the first page and
          repeated to keep later pages stable.

        # Returns

        - `AgentPage[AgentThread]` containing complete open discussions and the
          concrete inclusive activity pivot used to build them. Later pages use
          that same pivot.
        - `PlainTextResponse` when the Snapshot or review read is invalid; no
          page items or replacement pivot accompany the failure.

        # Failures

        - Returns plain text with status 400 for an unknown Snapshot. Invalid
          pagination or attention values are rejected by FastAPI.
        """
        try:
            room = self.snapshot_room(snapshot_id)
            page_threads, total, concrete_activity_id = room.threads(
                snapshot_id,
                page=page,
                limit=limit,
                state="open",
                attention=for_role,
                through_activity_id=through_activity_id,
            )
            views = [thread.discussion() for thread in page_threads]
            captured_files = self.captured_files_for_placements(
                room, snapshot_id, views
            )
            threads = [
                self.agent_thread(captured_files, view) for view in views
            ]
            return self.agent_page(
                threads, page, limit, total, concrete_activity_id
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent Threads request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/agent/thread/{thread_id}", response_model=AgentThreadPage)
    def agent_thread_by_id(
        self,
        thread_id: UUID,
        snapshot_id: UUID,
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> AgentThreadPage | PlainTextResponse:
        """Return one exact Thread with an independently paged discussion.

        # Parameters

        - `thread_id`: Stable discussion identity, including resolved or deleted.
        - `snapshot_id`: Exact Snapshot placement through which it is read.
        - `page`: Positive one-based Comment page.
        - `limit`: Maximum Comments returned, capped at one hundred.

        # Returns

        - `AgentThreadPage` for the exact discussion, with its Comment slice,
          total Comment count, and independently computed `has_more` value.
        - `PlainTextResponse` when the Snapshot, Thread, or review read is
          invalid; the response does not substitute an empty discussion.

        # Failures

        - Returns plain text with status 400 when the Snapshot or Thread is
          unknown. Invalid UUID or pagination input is rejected by FastAPI.
        """
        try:
            room = self.snapshot_room(snapshot_id)
            view = room.get_thread(snapshot_id, thread_id).discussion()
            thread = self.agent_thread(
                self.captured_files_for_placements(room, snapshot_id, [view]),
                view,
            )
            total = len(thread.comments)
            start = (page - 1) * limit
            return AgentThreadPage(
                **thread.model_dump(exclude={"comments"}),
                comments=thread.comments[start : start + limit],
                page=page,
                limit=limit,
                total_comments=total,
                has_more=page * limit < total,
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent Thread request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.post(
        "/api/agent/continue_review",
        response_model=ContinueAgentReviewResponse,
    )
    def continue_agent_review(
        self,
        request: ContinueAgentReviewRequest,
    ) -> ContinueAgentReviewResponse | PlainTextResponse:
        """Recapture one Tab and return bounded File and Thread changes.

        The prior Snapshot selects its Room and persisted capture law. Repository
        Tabs recapture that law; Pull Request Rooms first prepare the same canonical
        correspondence again. The response compares old and new captured Files and
        returns at most the requested append-only activities after the supplied
        cursor, advancing the cursor only through returned events and stating whether
        more remain. Capture or review failures use the agent plain-text response.

        # Parameters

        - `request`: Prior Snapshot, last observed activity id, and positive change limit.

        # Returns

        - `ContinueAgentReviewResponse` with the recaptured Snapshot, captured
          File delta, bounded action delta, resulting cursor, open-Thread count,
          and whether more actions remain.
        - `PlainTextResponse` when Room recovery, backend preparation, recapture,
          or continuation fails. The variant contains no replacement Snapshot
          or cursor for the agent to adopt.

        # Failures

        - Returns plain text for an unknown Snapshot or Mark, unsupported Preset
          continuation, Pull Request preparation failure, recapture failure, or
          invalid persisted capture context.
        """
        try:
            previous_id = UUID(hex=request.snapshot_id)
            room = self.room_lord.find_room(previous_id)
            context = room.capture_context()
            mark = self.db.get(context["mark_id"])
            if mark is None:
                raise DirdiffError("Snapshot Mark no longer exists.")
            backend = GitBackend.discover(repo_root=Path(mark.path))
            if context["tab"] == "pull-request":
                assert context["pull_request_url"] is not None
                prepared = prepare_pull_request(
                    url=context["pull_request_url"], repo_marks=self.db.list()
                )
                if prepared.project_id != mark.id:
                    raise DirdiffError("Pull Request Mark changed.")
                snapshot_id = room.recapture(
                    backend,
                    pull_request_left=prepared.left_commit,
                    pull_request_right=prepared.right_commit,
                )
            else:
                snapshot_id = room.recapture(backend)
            snapshot_path = room.path_for_snapshot(snapshot_id)

            file_delta = room.file_delta(previous_id, snapshot_id)
            actions, has_more, unresolved_count, profiles = room.continuation(
                snapshot_id, request.last_activity_id, request.limit
            )
            authors = {
                profile.id: AgentAuthor(
                    profile_id=profile.id, name=profile.username
                )
                for profile in profiles
            }
            changes: list[AgentThreadChange] = []
            for action in actions:
                assert action.activity_id is not None
                author = authors[action.profile_id]
                created_at = datetime.fromisoformat(action.created_at)
                change: AgentCommentThreadChange | AgentStateThreadChange
                match action.kind:
                    case "thread-created" | "comment-created":
                        assert action.comment_id is not None
                        change = AgentCommentThreadChange(
                            activity_id=action.activity_id,
                            thread_id=action.thread_id,
                            author=author,
                            created_at=created_at,
                            kind="comment_created",
                            comment_id=action.comment_id,
                            comment=self.agent_preview(action.body, False),
                        )
                    case "comment-edited":
                        assert action.comment_id is not None
                        change = AgentCommentThreadChange(
                            activity_id=action.activity_id,
                            thread_id=action.thread_id,
                            author=author,
                            created_at=created_at,
                            kind="comment_edited",
                            comment_id=action.comment_id,
                            comment=self.agent_preview(action.body, False),
                        )
                    case "comment-deleted":
                        assert action.comment_id is not None
                        change = AgentCommentThreadChange(
                            activity_id=action.activity_id,
                            thread_id=action.thread_id,
                            author=author,
                            created_at=created_at,
                            kind="comment_deleted",
                            comment_id=action.comment_id,
                            comment=self.agent_preview(None, True),
                        )
                    case "thread-resolved":
                        change = AgentStateThreadChange(
                            activity_id=action.activity_id,
                            thread_id=action.thread_id,
                            author=author,
                            created_at=created_at,
                            kind="thread_resolved",
                        )
                    case "thread-reopened":
                        change = AgentStateThreadChange(
                            activity_id=action.activity_id,
                            thread_id=action.thread_id,
                            author=author,
                            created_at=created_at,
                            kind="thread_reopened",
                        )
                    case "thread-deleted":
                        change = AgentStateThreadChange(
                            activity_id=action.activity_id,
                            thread_id=action.thread_id,
                            author=author,
                            created_at=created_at,
                            kind="thread_deleted",
                        )
                changes.append(change)
            return ContinueAgentReviewResponse(
                previous_snapshot_id=previous_id.hex,
                snapshot_id=snapshot_id.hex,
                snapshot_path=str(snapshot_path),
                last_activity_id=(
                    changes[-1].activity_id
                    if changes
                    else request.last_activity_id
                ),
                unresolved_thread_count=unresolved_count,
                file_delta=AgentFileDelta(
                    added=[str(path) for path in file_delta["added"]],
                    changed=[str(path) for path in file_delta["changed"]],
                    removed=[str(path) for path in file_delta["removed"]],
                ),
                thread_delta=changes,
                has_more_thread_changes=has_more,
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent continue-review request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.post("/api/agent/actions", response_model=AgentActionsResponse)
    def apply_agent_actions(
        self,
        request: AgentActionsRequest,
    ) -> AgentActionsResponse | PlainTextResponse:
        """Validate and atomically apply one ordered agent-authored batch.

        The Profile and Snapshot must already exist. Creation paths are resolved in
        one set-based read, then actions are translated in request order so the first
        invalid item determines the diagnostic and later actions may observe earlier
        accepted state. `Room.apply_review_batch` commits all translated actions or
        none; the response preserves result order and returns canonical Thread state.

        # Parameters

        - `request`: Snapshot, acting Profile, and non-empty ordered action sequence.

        # Returns

        - `AgentActionsResponse` with one canonical result per submitted action,
          in submission order, after the complete batch commits.
        - `PlainTextResponse` when validation or the atomic write fails. No
          action from the submitted batch is persisted in this case.

        # Failures

        - Returns plain text for a missing Profile or Snapshot, unauthenticated
          captured path, invalid target or Thread state, reused identity, mixed
          author, or any other rejected batch item. The transaction writes none
          of the actions when one fails.
        """
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = self.snapshot_room(snapshot_id)
            if self.user_profile_store.get(request.profile_id) is None:
                raise DirdiffError("Unknown Profile.")
            # One set-based id-addressed read identifies every distinct
            # creation path in the batch; nothing scans or stat-checks the
            # Snapshot. Validation stays in the per-action loop below so a
            # batch with several invalid actions reports the first one in
            # batch order; unvalidated paths resolve to None harmlessly.
            located = room.locate_captured_files(
                snapshot_id,
                tuple(
                    Path(action.file)
                    for action in request.actions
                    if isinstance(action, AgentCreateAction)
                ),
            )
            batch: list[
                CreateThread | ReplyToThread | ResolveThread | DeleteThread
            ] = []
            author = ProfileAuthor(request.profile_id)
            for action in request.actions:
                operation_id = uuid4()
                if isinstance(action, AgentCreateAction):
                    path = Path(action.file)
                    if not path.is_absolute():
                        raise DirdiffError("Invalid captured File path.")
                    match = located[path]
                    if match is None:
                        raise DirdiffError("File is absent from the Snapshot.")
                    left, right, side = match
                    batch.append(
                        CreateThread(
                            thread_id=uuid4(),
                            operation_id=operation_id,
                            comment_id=uuid4(),
                            author=author,
                            target=TextTarget(
                                FilePair(
                                    left.as_posix()
                                    if left is not None
                                    else None,
                                    right.as_posix()
                                    if right is not None
                                    else None,
                                ),
                                action.bay.bay_key,
                                side,
                                LineRange(
                                    action.bay.start_line,
                                    action.bay.end_line,
                                ),
                            ),
                            body=action.body,
                        )
                    )
                elif isinstance(action, AgentReplyAction):
                    batch.append(
                        ReplyToThread(
                            UUID(hex=action.thread_id),
                            AddComment(
                                operation_id=operation_id,
                                comment_id=uuid4(),
                                author=author,
                                body=action.body,
                            ),
                            action.kind,
                        )
                    )
                elif isinstance(action, AgentResolveAction):
                    batch.append(
                        ResolveThread(
                            UUID(hex=action.thread_id),
                            AddComment(
                                operation_id=operation_id,
                                comment_id=uuid4(),
                                author=author,
                                body=action.body,
                            ),
                        )
                    )
                else:
                    assert isinstance(action, AgentDeleteAction)
                    batch.append(
                        DeleteThread(
                            UUID(hex=action.thread_id),
                            ChangeThreadState(operation_id, author, None, None),
                        )
                    )
            results = room.apply_review_batch(snapshot_id, tuple(batch))
            return AgentActionsResponse(
                results=[
                    AgentActionResult(
                        kind=result.kind,
                        thread_id=result.thread_id.hex,
                        comment_id=(
                            result.comment_id.hex
                            if result.comment_id is not None
                            else None
                        ),
                        status=result.status,
                        attention=result.attention,
                    )
                    for result in results
                ]
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent actions request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/repo-defaults")
    def serve_repo_defaults(
        self,
        project_id: int = Query(
            description="Marked project id. Required for repo-backed defaults.",
        ),
    ) -> RepoDefaultsResponse:
        """Return structured defaults for Branch Review controls.

        One active Mark selects the repository. A single ref-metadata read supplies
        both selections so they cannot describe different Git states; a saved symbolic
        main branch overrides discovered base policy, while the review choice remains
        preferred relative to that exact base. Invalid Mark identity is a client error.

        # Parameters

        - `project_id`: Active repository Mark whose defaults are requested.

        # Failures

        - Raises `HTTPException` with status 400 when the Mark is absent or the
          repository backend cannot provide coherent ref metadata.
        """
        mark = self.db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        backend = GitBackend.discover(repo_root=Path(mark.path))
        # One metadata snapshot feeds both derivations so base and review
        # defaults cannot come from different repository states.
        metadata = backend.read_ref_metadata()
        saved_main_branch = self.db.get_main_branch(project_id)
        default_base_selection = (
            repo_main_branch_record_to_selection(saved_main_branch)
            if saved_main_branch is not None
            else backend.default_base_selection(metadata)
        )
        return RepoDefaultsResponse.model_validate(
            {
                "default_base_selection": default_base_selection,
                "preferred_review_selection": preferred_review_selection(
                    metadata, base_selection=default_base_selection
                ),
            }
        )

    @routes.get("/api/repo-refs")
    def serve_repo_refs(
        self,
        project_id: int = Query(
            description="Marked project id. Required for repo-backed refs.",
        ),
    ) -> RepoRefsResponse:
        """Return ref choices for repository-backed controls.

        The active Mark supplies the workspace, and one backend metadata snapshot is
        converted into autocomplete choices without changing saved defaults or resolving
        a comparison. Invalid Mark identity is reported as a client error.

        # Parameters

        - `project_id`: Active repository Mark whose symbolic refs are listed.

        # Failures

        - Raises `HTTPException` with status 400 when the Mark is absent or the
          repository backend cannot read its refs.
        """
        mark = self.db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        backend = GitBackend.discover(repo_root=Path(mark.path))
        return RepoRefsResponse.model_validate(
            {"ref_choices": ref_choices(backend.read_ref_metadata())}
        )

    @routes.post(
        "/api/repos/{project_id}/main-branch",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Save the repository main branch selection",
    )
    def save_repo_main_branch(
        self,
        project_id: int,
        request: RepoMainBranchRequest,
    ) -> RepoMainBranchResponse:
        """Replace one active Mark's symbolic Branch Review base.

        # Parameters

        - `project_id`: Active repository mark whose shared default changes.
        - `request`: Validated local or remote branch selection to persist.

        The endpoint stores symbolic names without resolving a commit.

        # Failures

        - Raises `HTTPException` with status 404 for an inactive or missing Mark,
          or status 400 when the selection cannot be persisted.
        """
        # Future auth belongs here: setting shared repository main remote/branch
        # should be admin-only once dirdiff has real users/permissions.
        mark = self.db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Invalid project_id: {project_id}",
            )
        try:
            selection = branch_selection_request_to_selection(request.selection)
            remote = (
                selection["remote"] if selection["source"] == "remote" else None
            )
            record = self.db.set_main_branch(
                project_id,
                source=selection["source"],
                remote=remote,
                branch=selection["branch"],
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        selection = repo_main_branch_record_to_selection(record)
        return RepoMainBranchResponse.model_validate(
            {"project_id": record.project_id, "selection": selection}
        )

    @routes.get(
        "/api/presets",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load grouped preset metadata",
    )
    def serve_presets(self) -> list[PresetCatalogResponse]:
        """List current preset catalogs and their selectable groups.

        The root is rescanned for every call, so hot-added catalogs appear
        without restarting the backend. Invalid metadata is reported as a bad
        request; unexpected catalog failures are logged and contained.

        # Failures

        - Raises `HTTPException` with status 400 for invalid preset metadata, or
          status 500 after logging an unexpected catalog failure.
        """
        try:
            return [
                self.preset_catalog_response(catalog)
                for catalog in self.preset_catalog_dirs()
            ]
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Preset catalog request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

    @routes.get("/api/repos")
    def serve_repos(self) -> list[RepoMarkResponse]:
        """List active repository marks in registry presentation order.

        Deactivated marks remain in persistence for Room identity but do not
        appear here. The endpoint performs no repository or Git inspection.

        # Failures

        - Propagates registry database failures to the application error handler.
        """
        return [
            RepoMarkResponse.model_validate(mark, from_attributes=True)
            for mark in self.db.list()
        ]

    @routes.delete(
        "/api/repos/{project_id}",
        status_code=HTTPStatus.NO_CONTENT,
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Remove a marked repository",
    )
    def delete_repo_mark(self, project_id: int) -> None:
        """Deactivate one repository mark without deleting retained review data.

        `project_id` must identify an active Mark. A successful response has no
        body; missing ids are reported without touching repository files.

        # Failures

        - Raises `HTTPException` with status 400 for a nonpositive id or status
          404 when no active Mark is changed.
        """
        try:
            if not self.db.delete(project_id):
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail=f"No marked project with id: {project_id}",
                )
        except HTTPException:
            raise
        except Exception as exc:
            LOGGER.exception("Repo mark delete request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

    @routes.post(
        "/api/pull-request/prepare",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Prepare immutable repository state for a Pull Request Tab",
    )
    def prepare_pull_request_endpoint(
        self,
        request: PullRequestPrepareRequest,
    ) -> PullRequestPrepareResponse:
        """Prepare canonical Pull Request commits before manifest capture.

        `request` supplies the forge URL. Preparation matches it to one active
        Mark, fetches required refs, and returns the canonical URL, merge base,
        and head commit. Manifest does not repeat this work.

        # Failures

        - Raises `HTTPException` with status 400 when the URL is unsupported, no
          active Mark matches it, or forge or Git preparation fails.
        """
        try:
            return pull_request_prepare_response(
                prepare_pull_request(
                    url=request.url,
                    repo_marks=self.db.list(),
                )
            )
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Pull request prepare request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

    @routes.post(
        "/api/user-profile",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
        },
        summary="Create persisted user profile data",
    )
    def create_user_profile(
        self,
        request: UserProfileUpdateRequest,
    ) -> UserProfileResponse:
        """Create one durable Profile selected later by its exact username.

        `request` supplies a validated display name. Duplicate or otherwise
        invalid names return a client error; the endpoint selects no active
        browser identity by itself.

        # Failures

        - Raises `HTTPException` with status 400 when the username is blank,
          padded with whitespace, or already used.
        """
        try:
            return UserProfileResponse.model_validate(
                self.user_profile_store.create(request.username),
                from_attributes=True,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @routes.get(
        "/api/user-profile",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Select persisted user profile data by exact username",
    )
    def get_user_profile(self, username: str) -> UserProfileResponse:
        """Return the one existing Profile selected by its exact username.

        Username validation failures are client errors and exact absence is 404.
        A successful response exposes the durable identity and current display name
        without selecting browser state or creating preferences.

        # Parameters

        - `username`: Exact persisted display name, with no surrounding whitespace.

        # Failures

        - Raises `HTTPException` with status 400 for invalid username syntax or
          status 404 when no exact Profile exists.
        """
        try:
            profile = self.user_profile_store.get_by_username(username)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {username}.",
            )
        return UserProfileResponse.model_validate(
            profile,
            from_attributes=True,
        )

    @routes.patch(
        "/api/user-profile/{profile_id}",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted user profile data",
    )
    def update_user_profile(
        self,
        profile_id: int,
        request: UserProfileUpdateRequest,
    ) -> UserProfileResponse:
        """Rename one durable Profile without rewriting authored actions.

        # Parameters

        - `profile_id`: Existing Profile whose stable identity is preserved.
        - `request`: Validated globally unique replacement display name.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid or duplicate name
          and status 404 for a missing Profile.
        """
        try:
            profile = self.user_profile_store.update_username(
                profile_id, request.username
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return UserProfileResponse.model_validate(
            profile,
            from_attributes=True,
        )

    @routes.get(
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Load persisted user preferences",
    )
    def serve_preferences(self, profile_id: int) -> PreferencesResponse:
        """Return one Profile's complete preferences, creating defaults if absent.

        `profile_id` must identify an existing Profile. Default creation is the
        preference store's explicit operation and does not select that Profile
        as active.

        # Failures

        - Raises `HTTPException` with status 404 when `profile_id` does not name
          an existing Profile. Persistence failures propagate.
        """
        profile = self.user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            self.preferences_store.get_or_create(profile_id),
            from_attributes=True,
        )

    @routes.patch(
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted user preferences",
    )
    def update_preferences(
        self,
        profile_id: int,
        request: PreferencesUpdateRequest,
    ) -> PreferencesResponse:
        """Replace one existing Profile's aggressive-fold preference.

        # Parameters

        - `profile_id`: Existing Profile whose preference row is updated.
        - `request`: Exact boolean value to persist and return.

        # Failures

        - Raises `HTTPException` with status 404 when `profile_id` does not name
          an existing Profile. Persistence failures propagate.
        """
        profile = self.user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            self.preferences_store.set_aggressive_folds(
                profile_id, request.aggressive_folds
            ),
            from_attributes=True,
        )

    @routes.get(
        "/api/manifest",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Show the repository state selected by a Tab",
    )
    def serve_manifest(
        self,
        project_id: str = Query(
            description="Manifest project id: marked project id for repo-backed Tabs, preset catalog id for Preset.",
        ),
        tab: TabParam = Query(description="HUD Tab."),
        branch_selections: BranchSelections = Depends(
            selected_branch_selections
        ),
        left: str | None = Query(
            default=None, description="Left ref or diff side."
        ),
        right: str | None = Query(
            default=None, description="Right ref or diff side."
        ),
        pull_request_url: str | None = Query(
            default=None,
            description="Pull Request URL used only for Room correspondence.",
        ),
        left_commit: str | None = Query(
            default=None,
            description="Left commit prepared for the Pull Request Tab.",
        ),
        right_commit: str | None = Query(
            default=None,
            description="Right commit prepared for the Pull Request Tab.",
        ),
        preset_subset: str | None = Query(
            default=None,
            description="Preset subset/group id for the Preset Tab.",
        ),
        show_untracked: bool = Query(
            default=False,
            description="Include untracked worktree files when supported by the selected Tab.",
        ),
    ) -> RepoManifestResponse:
        """Show the supplied repository state and provide follow-up keys.

        The handler constructs the concrete backend and supplies the complete
        Tab state to `RoomLord.corresponding_room`. The response contains the
        manifest tree, aggregate backend totals, and the opaque Snapshot key
        required by follow-up endpoints. It does not prepare Pull Requests;
        Pull Request parameters already contain the URL and capture commits.

        # Parameters

        - `project_id`: Active Mark id for repository Tabs or catalog id for
          Preset.
        - `tab`: Selected HUD Tab governing the valid parameter combination.
        - `branch_selections`: Parsed base/review selections for Branch Review.
        - `left`: Left revision handle for Head or Refs.
        - `right`: Right revision handle for Head or Refs.
        - `pull_request_url`: Canonical URL identifying a Pull Request Room.
        - `left_commit`: Already prepared Pull Request merge-base commit.
        - `right_commit`: Already prepared Pull Request head commit.
        - `preset_subset`: Selected fixture group for a Preset catalog.
        - `show_untracked`: Whether a supported repository Tab includes
          untracked worktree Files.

        # Failures

        - Raises `HTTPException` with status 400 for invalid Tab parameters,
          Mark, backend, or capture input.
        - Logs unexpected capture or persistence failures and raises status 500.
        """
        try:
            room, snapshot_id, preset_name = self.capture_snapshot(
                project_id=project_id,
                selection=self.manifest_capture_selection(
                    project_id=project_id,
                    tab=tab,
                    branch_selections=branch_selections,
                    left=left,
                    right=right,
                    pull_request_url=pull_request_url,
                    left_commit=left_commit,
                    right_commit=right_commit,
                    preset_subset=preset_subset,
                    show_untracked=show_untracked,
                ),
            )
            snapshot_meta = room.meta(snapshot_id)
            manifest_paths = tuple(
                sorted(
                    (
                        RepoDiffPath(
                            left_path=left_path.as_posix()
                            if left_path is not None
                            else None,
                            right_path=right_path.as_posix()
                            if right_path is not None
                            else None,
                            display_name=(
                                right_path.as_posix()
                                if snapshot_meta["tab"] == "preset"
                                and right_path is not None
                                else display_name_for_repo_paths(
                                    left_path.as_posix()
                                    if left_path is not None
                                    else None,
                                    right_path.as_posix()
                                    if right_path is not None
                                    else None,
                                )
                            ),
                            change_type=file_meta["change_type"],
                            lazy_reason_override=file_meta[
                                "lazy_reason_override"
                            ],
                            untracked=not file_meta["tracked"],
                        )
                        for left_path, right_path, file_meta in room.manifested(
                            snapshot_id
                        )
                    ),
                    key=lambda path: (path.display_name, path.change_type),
                )
            )
            manifest = build_repo_manifest_for_paths(
                left_label=snapshot_meta["left_label"],
                right_label=snapshot_meta["right_label"],
                paths=manifest_paths,
                added_lines=snapshot_meta["added_lines"],
                removed_lines=snapshot_meta["removed_lines"],
            )
            if tab == "preset":
                assert preset_name is not None
                manifest["display_name"] = preset_name
            payload = {"snapshot_id": snapshot_id.hex, **manifest}
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Manifest request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return RepoManifestResponse.model_validate(payload)

    @routes.get(
        "/api/lazy-info",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load repository lazy file metadata",
    )
    def serve_lazy_info(
        self,
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
    ) -> LazyInfoResponse:
        """Return delayed-file metadata from one manifest Snapshot.

        `snapshot_id` is the sole Room lookup input. The containing Room's
        persisted Tab supplies presentation behavior; no live Git, worktree,
        index, or preset state is read.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid or unknown
          Snapshot id.
        - Logs unexpected persistence or manifest failures and raises status 500.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            room = self.room_lord.find_room(snapshot_key)
            snapshot_meta = room.meta(snapshot_key)
            lazy_paths = tuple(
                sorted(
                    (
                        RepoDiffPath(
                            left_path=left_path.as_posix()
                            if left_path is not None
                            else None,
                            right_path=right_path.as_posix()
                            if right_path is not None
                            else None,
                            display_name=(
                                right_path.as_posix()
                                if snapshot_meta["tab"] == "preset"
                                and right_path is not None
                                else display_name_for_repo_paths(
                                    left_path.as_posix()
                                    if left_path is not None
                                    else None,
                                    right_path.as_posix()
                                    if right_path is not None
                                    else None,
                                )
                            ),
                            change_type=file_meta["change_type"],
                            lazy_reason_override=file_meta[
                                "lazy_reason_override"
                            ],
                            untracked=not file_meta["tracked"],
                        )
                        for left_path, right_path, file_meta in room.manifested(
                            snapshot_key
                        )
                    ),
                    key=lambda path: (path.display_name, path.change_type),
                )
            )
            payload = build_lazy_info_for_paths(paths=lazy_paths)
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Lazy info request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return LazyInfoResponse.model_validate(payload)

    @routes.get(
        "/api/file-diff",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load a single file diff",
    )
    def serve_file_diff(
        self,
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
        engine: EngineKind = Query(description="Diff engine."),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
    ) -> ComposedDiffResponse:
        """Render one exact filepath pair from a manifest Snapshot.

        The opaque key finds the containing Room and is passed again with the
        repository-relative paths to select the exact captured File. Rendering
        reads only the returned absolute capture paths; it never reloads the
        workspace backend.

        # Parameters

        - `snapshot_id`: Opaque key returned by manifest.
        - `engine`: Renderer requested for composed text bays.
        - `left_path`: Exact left repository path, or absent for an added File.
        - `right_path`: Exact right repository path, or absent for a deleted
          File.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid Snapshot id or
          File pair, missing capture, stored capture error, or rejected rendering.
        - Logs unexpected I/O, persistence, or response failures and raises
          status 500.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            try:
                pair = FilePair(left_path, right_path)
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left = Path(pair.left_path) if pair.left_path is not None else None
            right = (
                Path(pair.right_path) if pair.right_path is not None else None
            )
            left_file, right_file, file_meta = room.get(
                snapshot_key, left, right
            )
            payload = self.render_loaded_snapshot_file(
                room=room,
                snapshot_id=snapshot_key,
                engine_name=engine,
                pair=pair,
                left_file=left_file,
                right_file=right_file,
                file_meta=file_meta,
            )
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("File diff request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return payload

    @routes.get(
        "/api/file-media",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Serve one captured media side",
        response_class=Response,
    )
    def serve_file_media(
        self,
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
        side: Literal["left", "right"] = Query(
            description="Which captured side of the File pair to serve.",
        ),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
    ) -> Response:
        """Serve one side of one File as the exact bytes the Snapshot captured.

        Addressed by Snapshot, side, and File pair -- the same addressing
        `/api/file-diff` uses, and for the same reason: a File is identified by
        its pair of nullable paths, so a renamed image is unaddressable by one
        path alone. The composed diff the caller already holds carries both.

        The route does HTTP work only: it recovers the Room, reads the two
        captured byte sides, asks `bays()` which image bay the File composes
        into, and writes that side's bytes under its media type. `bays()` runs
        no engine, so this never renders a diff to serve a picture, and the
        media type is the one composition concluded rather than a second
        opinion formed here.

        Snapshots are immutable and a Snapshot id is never reused, so the
        response for one address can never change and is declared cacheable
        outright.

        # Parameters

        - `snapshot_id`: Opaque immutable Snapshot key returned by manifest.
        - `side`: Present captured side whose exact bytes are returned.
        - `left_path`: Exact left repository path, or absent for an added File.
        - `right_path`: Exact right repository path, or absent for a deleted
          File.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid Snapshot id or
          File pair, missing or failed capture, non-image File, or absent selected
          side.
        - Logs unexpected I/O, persistence, or composition failures and raises
          status 500.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            try:
                pair = FilePair(left_path, right_path)
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left_file, right_file, file_meta = room.get(
                snapshot_key,
                Path(pair.left_path) if pair.left_path is not None else None,
                Path(pair.right_path) if pair.right_path is not None else None,
            )
            if file_meta["capture_error"] is not None:
                raise DirdiffError(file_meta["capture_error"])
            snapshot_meta = room.meta(snapshot_key)
            for bay in Composer().bays(
                left_file.read_bytes() if left_file is not None else None,
                right_file.read_bytes() if right_file is not None else None,
                BayContext(
                    left_path=pair.left_path,
                    right_path=pair.right_path,
                    left_label=snapshot_meta["left_label"],
                    right_label=snapshot_meta["right_label"],
                ),
            ):
                if isinstance(bay, ImageBay):
                    media_bay = bay
                    break
            else:
                raise DirdiffError(
                    "The selected file composes no media content."
                )
            media_side = media_bay.left if side == "left" else media_bay.right
            if media_side is None:
                raise DirdiffError(
                    f"The selected file was not captured on the {side} side."
                )
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("File media request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return Response(
            content=media_side.data,
            media_type=media_side.media_type,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )


def create_app(
    db: RepoMarkStore,
    user_profile_store: UserProfileStore | None = None,
    preferences_store: PreferencesStore | None = None,
    *,
    room_lord: RoomLord,
    presets_root: str | None = None,
) -> FastAPI:
    """Create the dirdiff FastAPI app and wire request orchestration.

    The app layer performs HTTP validation, database-backed repo-mark access, concrete
    backend construction, notebook detection, and response-model validation.
    The caller provides the `RoomLord` that selects prepared Rooms for manifest
    and recovers them by Snapshot key for follow-up operations. Storage and
    capture remain behind that interface. The server delegates already-loaded
    text rendering to the selected diff engine.

    # Parameters

    - `db`: Repository registry and source of the shared SQLAlchemy engine.
    - `user_profile_store`: Profile persistence, or `None` to bind one to the
      registry engine.
    - `preferences_store`: Preference persistence, or `None` to bind one to the
      registry engine.
    - `room_lord`: Application boundary for Room selection and Snapshot lookup.
    - `presets_root`: Optional catalog root; omission uses the project's test
      presets directory at request time.

    # Usage

    Construct the stores and `RoomLord` once for one database and Snapshot root,
    then keep the returned application for the server lifetime. Tests may omit
    Profile and preference stores to derive both from the registry engine.

    # Failures

    - Construction propagates dependency and route-registration failures. HTTP
      operation failures are handled by the installed application handlers.
    """
    if user_profile_store is None:
        user_profile_store = UserProfileStore(db.engine)
    if preferences_store is None:
        preferences_store = PreferencesStore(db.engine)

    server = _Server(
        db,
        user_profile_store,
        preferences_store,
        room_lord=room_lord,
        presets_root=presets_root,
    )
    app = FastAPI()
    _Server.routes.register(app, server)
    return app


def uvicorn_entrypoint() -> FastAPI:
    """Construct the production app from the CLI's serialized runtime config.

    The factory opens one SQLite engine, builds the registry and Room
    persistence interfaces over it, and supplies the configured store root to
    `RoomLord`. The CLI must provide the environment payload and at least one
    active Mark before uvicorn imports this function.

    # Usage

    Configure uvicorn with this factory after the CLI writes
    `RUNTIME_CONFIG_ENV`. Application code should call `create_app` directly
    when dependencies are already available.

    # Failures

    - Asserts when runtime configuration is absent or the configured database
      has no active repository mark.
    - Propagates invalid JSON, configuration, database, and schema failures.
    """
    payload = os.environ.get(RUNTIME_CONFIG_ENV)
    assert payload is not None, "dirdiff runtime config missing"
    config = RuntimeConfig(**json.loads(payload))
    engine = open_sqlite_engine(Path(config.db_path))
    repo_store = RepoMarkStore(engine)
    room_lord = RoomLord(RoomStore(engine), Path(config.store_path))
    user_profile_store = UserProfileStore(engine)
    preferences_store = PreferencesStore(engine)
    marks = repo_store.list()
    assert marks != [], "dirdiff runtime config has no marked repos"
    return create_app(
        repo_store,
        user_profile_store,
        preferences_store,
        room_lord=room_lord,
        presets_root=config.presets_root,
    )
