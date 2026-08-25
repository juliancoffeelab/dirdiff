"""FastAPI wiring for diff rendering and persistent review operations.

The server validates HTTP inputs and constructs concrete workspace backends for
manifest. `RoomLord.corresponding_room` applies the Tab law there, while
follow-up routes recover a Room directly from the returned Snapshot key. Room
methods expose captured Paths and metadata; private capture stores never cross
the application boundary. Diff engines render already-loaded text and do not
decide whether a file is a notebook.
For `.ipynb` paths, this module calls the public notebook payload builders
before using the selected text engine.

Keeping notebook routing here preserves the REST API while preventing concrete
engines from depending on notebook internals.

Snapshot-keyed browser routes expose Thread, Comment, and lifecycle operations.
Agent routes capture and continue a logical Tab, expose its changed Files on
disk, page discussions, and apply atomic create/reply/resolve batches. This
module validates and translates HTTP entities; Room
and Thread perform discussion operations, while RoomStore alone persists their
records. The server must not own review state, placement rules, or private
source coordinates.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import (
    Annotated,
    Any,
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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
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
    RefChoices,
    RepoDiffPath,
    WorkspaceBackendProtocol,
    build_lazy_info_for_paths,
    build_repo_manifest_for_paths,
    display_name_for_repo_paths,
    file_kind_for_change_type,
    preferred_review_selection,
    prepare_pull_request,
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
    ComposeContext,
    Composer,
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
    TextTarget,
    ThreadDiscussionView,
)
from dirdiff.room_lord import (
    BranchReviewCaptureSelection,
    CaptureSelection,
    FileMeta,
    PresetCaptureSelection,
    PresetCatalog,
    PullRequestCaptureSelection,
    RevisionsCaptureSelection,
    Room,
    RoomLord,
)

LOGGER = logging.getLogger(__name__)

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

RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "RuntimeConfig",
    "branch_selection_request_to_selection",
    "create_app",
    "repo_main_branch_record_to_selection",
    "selected_branch_selections",
    "uvicorn_entrypoint",
]


@dataclass(frozen=True)
class RuntimeConfig:
    """Server startup configuration passed across the uvicorn factory boundary.

    The CLI creates this value before starting uvicorn.  `run_uvicorn`
    serializes it into `RUNTIME_CONFIG_ENV` because uvicorn imports the app
    factory in a fresh module-loading path, especially when reload is enabled.
    The server owns the shape because `uvicorn_entrypoint` is the only place
    that consumes the serialized payload.
    """

    db_path: str
    """
    SQLite database path used for repo marks, preferences, and user profile data.

    The CLI resolves this to an absolute-ish string before launching uvicorn so
    reload workers do not need to know how command-line defaults were chosen.
    """

    store_path: str
    """
    Directory containing immutable Snapshot files.

    The CLI defaults this to a `store` directory beside `db_path`, while an
    explicit `--store-path` supplies a separate location. Persistent databases
    use a database-adjacent `.room.lock` file so every store root shares one
    publication lock.
    """

    tab: Literal["head", "refs", "branch-review"] = "head"
    """
    Initial Tab encoded into the browser URL.

    This is startup navigation state, not a server-wide restriction; the API can
    still serve other Tabs after the frontend is running.
    """

    left: str = "HEAD"
    """
    Left ref or side name for the Refs startup Tab.
    """

    right: str = "worktree"
    """
    Right ref or side name for the Refs startup Tab.
    """

    base_selection: BranchSelection | None = None
    """
    Base branch selection for the Branch Review startup Tab.

    The CLI writes this structured value into the first browser URL; API
    handlers parse the same local/remote shape from query params afterward.
    """

    review_selection: BranchSelection | None = None
    """
    Review branch selection for the Branch Review startup Tab.

    This is startup navigation state only.  Diff requests still carry their own
    explicit branch-review selections.
    """

    presets_root: str | None = None
    """
    Optional preset root supplied by the CLI for local fixture browsing.
    """


TabParam = Literal[
    "head",
    "refs",
    "branch-review",
    "pull-request",
    "preset",
]
"""One complete HUD Tab discriminator accepted by manifest."""
PresetTypeParam = PresetCatalog
BranchSourceParam = BranchSource
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
BranchSelections = tuple[BranchSelection | None, BranchSelection | None]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ReviewFilePairModel(ApiModel):
    """Identify one File through its complete nullable side pair."""

    left_path: str | None
    right_path: str | None

    @model_validator(mode="after")
    def validate_presence(self) -> Self:
        """Require at least one normalized relative side value."""
        FilePair(self.left_path, self.right_path)
        return self


class ReviewLineRange(ApiModel):
    """Identify one positive one-based inclusive range."""

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """Reject a range whose end precedes its start."""
        LineRange(self.start_line, self.end_line)
        return self


class ReviewTextBayModel(ApiModel):
    """Address one composed bay by its public key.

    The key is the same coordinate the composed diff gives that bay, so a
    target names a bay without describing what format produced it.
    """

    bay_key: str = Field(min_length=1)


class TextReviewTarget(ApiModel):
    """Address one line range on one present side of a rendered bay."""

    kind: Literal["text"]
    file: ReviewFilePairModel
    bay: ReviewTextBayModel
    side: Literal["left", "right"]
    range: ReviewLineRange

    @model_validator(mode="after")
    def validate_selected_side(self) -> Self:
        """Require the selected side in the exact File pair."""
        if self.side == "left" and self.file.left_path is None:
            raise ValueError("The selected left side is absent.")
        if self.side == "right" and self.file.right_path is None:
            raise ValueError("The selected right side is absent.")
        return self


ReviewTargetModel = TextReviewTarget
"""Identify one rendered text range in HTTP payloads."""


class NewCodeCommentRequest(ApiModel):
    """Post a Comment on code and let the backend start its Thread."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    target: ReviewTargetModel
    body: str = Field(min_length=1)


class ReplyCommentRequest(ApiModel):
    """Post a Comment to one existing Snapshot-bound Thread."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    body: str = Field(min_length=1)
    attention: Literal["inert", "alert"]


PostCommentRequest = NewCodeCommentRequest | ReplyCommentRequest
"""Post either the first Comment on code or a reply to one Thread."""


class EditReviewCommentRequest(ApiModel):
    """Replace one authored Comment body by Comment ID."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    body: str = Field(min_length=1)


class DeleteReviewCommentRequest(ApiModel):
    """Attribute one Comment tombstone to a valid acting Profile."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)


class ChangeReviewThreadStateRequest(ApiModel):
    """Resolve or reopen one Thread, optionally adding a Comment."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    body: str | None = Field(default=None, min_length=1)


class DeleteReviewThreadRequest(ApiModel):
    """Delete one Thread without creating a Comment."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)


class ReviewAuthorResponse(ApiModel):
    """Return one ordinary Profile attribution."""

    profile_id: int = Field(gt=0)
    display_name: str = Field(min_length=1)


class ReviewCommentResponse(ApiModel):
    """Return one current Comment or retained deletion tombstone."""

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=0)
    author: ReviewAuthorResponse
    revision: int = Field(ge=0)
    body: str | None
    deleted: bool
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_tombstone(self) -> Self:
        """Require deletion state and retained body absence to agree."""
        if self.deleted != (self.body is None):
            raise ValueError("Deleted Comments must be body-less tombstones.")
        return self


class RangeThreadCodeLocationResponse(ApiModel):
    """Locate one Thread on an exact rendered range."""

    kind: Literal["range"]
    file: ReviewFilePairModel
    bay: ReviewTextBayModel
    side: Literal["left", "right"]
    range: ReviewLineRange


class BayStartThreadCodeLocationResponse(ApiModel):
    """Locate one Thread at the start of one composed bay.

    `region_not_found` Threads keep their origin's bay; `bay_not_found`
    Threads land on the File's first bay carrying their side. Either way the
    bay names a real composed unit of this Snapshot's File, chosen at
    derivation time.
    """

    kind: Literal["bay-start"]
    file: ReviewFilePairModel
    bay: ReviewTextBayModel
    side: Literal["left", "right"]


class FileStartThreadCodeLocationResponse(ApiModel):
    """Locate one Thread on its File with no bay coordinate to land on.

    Historical File-level Threads always take this shape; a text Thread
    takes it only when its File composes no bay carrying the side. It is
    never navigable — History is its home.
    """

    kind: Literal["file-start"]
    file: ReviewFilePairModel
    side: Literal["left", "right"]


ThreadCodeLocationResponse = Annotated[
    RangeThreadCodeLocationResponse
    | BayStartThreadCodeLocationResponse
    | FileStartThreadCodeLocationResponse,
    Field(discriminator="kind"),
]
"""Return one valid current code-location variant."""

ReviewOriginTargetResponse = Annotated[
    TextReviewTarget | FileStartThreadCodeLocationResponse,
    Field(discriminator="kind"),
]
"""Return a text origin or one retained historical File-start origin."""


class ReviewExcerptResponse(ApiModel):
    """Return one bounded selected-side excerpt from the origin Snapshot."""

    side: Literal["left", "right"]
    start_line: int = Field(ge=1)
    selected_start_line: int = Field(ge=1)
    selected_end_line: int = Field(ge=1)
    lines: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        """Require the selected range to lie within the returned lines."""
        excerpt_end = self.start_line + len(self.lines) - 1
        if not (
            self.start_line
            <= self.selected_start_line
            <= self.selected_end_line
            <= excerpt_end
        ):
            raise ValueError("Selected review range exceeds its excerpt.")
        return self


class ReviewThreadResponse(ApiModel):
    """Return one complete live discussion through one exact Snapshot."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: datetime
    state: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    discussion_revision: int = Field(ge=0)
    origin_target: ReviewOriginTargetResponse
    code_location: ThreadCodeLocationResponse | None
    outdated_reason: (
        Literal[
            "region_changed",
            "region_not_found",
            "bay_not_found",
            "file_missing",
        ]
        | None
    )
    original_excerpt: ReviewExcerptResponse | None
    comments: list[ReviewCommentResponse] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_review_state(self) -> Self:
        """Require exact code, reason, and snippet combinations."""
        if isinstance(self.origin_target, FileStartThreadCodeLocationResponse):
            if self.original_excerpt is not None:
                raise ValueError(
                    "A historical File-start origin cannot have an excerpt."
                )
            if self.outdated_reason is None:
                if not isinstance(
                    self.code_location, FileStartThreadCodeLocationResponse
                ):
                    raise ValueError(
                        "A historical File-start Thread requires File start."
                    )
                if (
                    self.code_location.file != self.origin_target.file
                    or self.code_location.side != self.origin_target.side
                ):
                    raise ValueError(
                        "A historical File-start location changed identity."
                    )
            elif (
                self.outdated_reason != "file_missing"
                or self.code_location is not None
            ):
                raise ValueError(
                    "A historical File-start Thread may only lose its File."
                )
            return self
        if self.original_excerpt is None:
            raise ValueError("A text Thread requires its original excerpt.")
        if self.outdated_reason is None:
            if not isinstance(
                self.code_location,
                RangeThreadCodeLocationResponse,
            ):
                raise ValueError(
                    "A current Thread requires a current location."
                )
        elif self.outdated_reason == "region_changed":
            if not isinstance(
                self.code_location, RangeThreadCodeLocationResponse
            ):
                raise ValueError("A changed Thread requires its new range.")
        elif self.outdated_reason == "region_not_found":
            if not isinstance(
                self.code_location, BayStartThreadCodeLocationResponse
            ):
                raise ValueError(
                    "An unmatched Thread requires its origin bay's start."
                )
        elif self.outdated_reason == "bay_not_found":
            if not isinstance(
                self.code_location,
                BayStartThreadCodeLocationResponse
                | FileStartThreadCodeLocationResponse,
            ):
                raise ValueError(
                    "A bay-lost Thread requires a bay or File start."
                )
        elif self.code_location is not None:
            raise ValueError("A missing File cannot have a code location.")
        return self


class ReviewThreadUpdateResponse(ApiModel):
    """Return bounded authoritative state changed by one Thread action."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    state: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    discussion_revision: int = Field(ge=0)
    comment: ReviewCommentResponse | None


class ReviewThreadPage(ApiModel):
    """Return one bounded page of Threads represented in a Snapshot."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    through_activity_id: int = Field(ge=0)
    threads: list[ReviewThreadResponse]
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total_threads: int = Field(ge=0)
    has_more: bool


class AgentBranch(ApiModel):
    """Name one local or remote branch in a Branch Review Tab."""

    remote: str | None
    name: str = Field(min_length=1)


class AgentHeadTab(ApiModel):
    """Capture HEAD against the worktree of one marked repository path."""

    kind: Literal["head"]
    repo_path: str = Field(min_length=1)


class AgentRefsTab(ApiModel):
    """Capture two explicit sides of one marked repository path."""

    kind: Literal["refs"]
    repo_path: str = Field(min_length=1)
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)


class AgentBranchReviewTab(ApiModel):
    """Capture one symbolic base and review branch pair."""

    kind: Literal["branch-review"]
    repo_path: str = Field(min_length=1)
    base: AgentBranch
    review: AgentBranch


class AgentPullRequestTab(ApiModel):
    """Prepare and capture one supported Pull Request URL."""

    kind: Literal["pull-request"]
    url: str = Field(min_length=1)


AgentReviewTab = Annotated[
    AgentHeadTab | AgentRefsTab | AgentBranchReviewTab | AgentPullRequestTab,
    Field(discriminator="kind"),
]
"""Describe one complete agent-known Tab context."""


class NewAgentReviewRequest(ApiModel):
    """Register one disposable ordinary Profile and capture its Tab."""

    agent_uuid: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1)
    tab: AgentReviewTab

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Require a nonblank display name without edge whitespace."""
        if value != value.strip() or value.strip() == "":
            raise ValueError("Invalid agent name.")
        return value


class NewAgentReviewResponse(ApiModel):
    """Return the minimal initial context for one captured review."""

    profile_id: int = Field(gt=0)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    last_activity_id: int = Field(ge=0)
    snapshot_path: str = Field(min_length=1)
    attention_counts: dict[Literal["author", "reviewer", "both"], int]


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
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """Reject a bay whose end precedes its start."""
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


class AgentAuthor(ApiModel):
    """Expose the ordinary Profile attribution of one review action."""

    profile_id: int = Field(gt=0)
    name: str = Field(min_length=1)


class AgentComment(ApiModel):
    """Expose one complete Comment or retained deletion tombstone."""

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    author: AgentAuthor
    body: str | None
    deleted: bool
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_tombstone(self) -> Self:
        """Require deletion state and body absence to agree."""
        if self.deleted != (self.body is None):
            raise ValueError("Deleted Comments must be body-less tombstones.")
        return self


class AgentCommentPreview(ApiModel):
    """Expose bounded Comment text for discovery and activity pages."""

    body: str | None
    deleted: bool
    truncated: bool


class AgentThreadSummary(ApiModel):
    """Expose enough of one open Thread to decide whether to read it."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["open"]
    attention: Literal["author", "reviewer", "both"]
    file: str | None
    bay: AgentBayRange | AgentBayStart | None
    first_comment: AgentCommentPreview
    latest_comment: AgentCommentPreview
    comment_count: int = Field(ge=1)


class AgentThread(ApiModel):
    """Expose one complete Snapshot-bound review discussion."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    file: str | None
    bay: AgentBayRange | AgentBayStart | None
    original_excerpt: ReviewExcerptResponse | None
    outdated_reason: (
        Literal[
            "region_changed",
            "region_not_found",
            "bay_not_found",
            "file_missing",
        ]
        | None
    )
    comments: list[AgentComment]


class AgentPage[AgentPageItem](ApiModel):
    """Expose one one-based bounded page and its complete item count."""

    items: list[AgentPageItem]
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total: int = Field(ge=0)
    has_more: bool
    through_activity_id: int | None = Field(default=None, ge=0)


class AgentThreadPage(ApiModel):
    """Expose one Thread with an independently paged Comment sequence."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    file: str | None
    bay: AgentBayRange | AgentBayStart | None
    original_excerpt: ReviewExcerptResponse | None
    outdated_reason: (
        Literal[
            "region_changed",
            "region_not_found",
            "bay_not_found",
            "file_missing",
        ]
        | None
    )
    comments: list[AgentComment]
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total_comments: int = Field(ge=0)
    has_more: bool


class ContinueAgentReviewRequest(ApiModel):
    """Capture a Tab again and read later authored Thread activity."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    last_activity_id: int = Field(ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class AgentFileDelta(ApiModel):
    """Expose changed filesystem paths between two captured Snapshots."""

    added: list[str]
    changed: list[str]
    removed: list[str]


class AgentThreadChangeBase(ApiModel):
    """Expose fields shared by every authored action after a boundary."""

    activity_id: int = Field(gt=0)
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    author: AgentAuthor
    created_at: datetime


class AgentCommentThreadChange(AgentThreadChangeBase):
    """Expose one authored Comment creation, replacement, or tombstone."""

    kind: Literal[
        "comment_created",
        "comment_edited",
        "comment_deleted",
    ]
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    comment: AgentCommentPreview


class AgentStateThreadChange(AgentThreadChangeBase):
    """Expose one authored Thread lifecycle transition."""

    kind: Literal["thread_resolved", "thread_reopened", "thread_deleted"]


AgentThreadChange = Annotated[
    AgentCommentThreadChange | AgentStateThreadChange,
    Field(discriminator="kind"),
]
"""Expose exactly one valid authored Thread action variant."""


class ContinueAgentReviewResponse(ApiModel):
    """Return a fresh Snapshot plus bounded File and Thread changes."""

    previous_snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_path: str = Field(min_length=1)
    last_activity_id: int = Field(ge=0)
    unresolved_thread_count: int = Field(ge=0)
    file_delta: AgentFileDelta
    thread_delta: list[AgentThreadChange]
    has_more_thread_changes: bool


class AgentCreateAction(ApiModel):
    """Create one Thread on a named bay and its first Comment.

    `file` is the exact absolute captured side path the agent inspected; the
    bay names where in that File the finding sits.
    """

    kind: Literal["create-finding"]
    file: str = Field(min_length=1)
    bay: AgentBayRange
    body: str = Field(min_length=1)


class AgentReplyAction(ApiModel):
    """Append one role-directed Comment to a Snapshot-bound Thread."""

    kind: Literal["author-response", "reviewer-return", "inert-comment"]
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    body: str = Field(min_length=1)


class AgentResolveAction(ApiModel):
    """Resolve one open reviewer-attention Thread with an explanation."""

    kind: Literal["reviewer-resolve"]
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    body: str = Field(min_length=1)


class AgentDeleteAction(ApiModel):
    """Apply exceptional terminal deletion to one Thread."""

    kind: Literal["reviewer-delete"]
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")


AgentAction = Annotated[
    AgentCreateAction
    | AgentReplyAction
    | AgentResolveAction
    | AgentDeleteAction,
    Field(discriminator="kind"),
]
"""Describe exactly one action available through the agent boundary."""


class AgentActionsRequest(ApiModel):
    """Apply one ordered atomic batch as an ordinary Profile."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    actions: list[AgentAction] = Field(min_length=1, max_length=100)


class AgentActionResult(ApiModel):
    """Expose identifiers created or affected by one applied batch item."""

    kind: Literal[
        "create-finding",
        "author-response",
        "reviewer-return",
        "reviewer-resolve",
        "inert-comment",
        "reviewer-delete",
    ]
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    comment_id: str | None = None
    status: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]

    @model_validator(mode="after")
    def validate_variant(self) -> Self:
        """Require Comment ids for every instrument except deletion."""
        if (self.comment_id is not None) != (self.kind != "reviewer-delete"):
            raise ValueError("Action result Comment presence is invalid.")
        return self


class AgentActionsResponse(ApiModel):
    """Return one result in the same order as every applied action."""

    results: list[AgentActionResult]


class ReviewErrorResponse(ApiModel):
    """Return stable browser review failure classification and presentation."""

    code: ReviewErrorCode
    message: str = Field(min_length=1)


class _ReviewHttpException(Exception):
    """Carry one validated browser review failure to its JSON handler."""

    def __init__(self, status: HTTPStatus, error: ReviewError) -> None:
        """Bind the mapped HTTP status to one typed review-domain failure."""
        super().__init__(str(error))
        self.status = status
        self.response = ReviewErrorResponse(
            code=error.code,
            message=str(error),
        )


_REVIEW_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    int(HTTPStatus.BAD_REQUEST): {"model": ReviewErrorResponse},
    int(HTTPStatus.NOT_FOUND): {"model": ReviewErrorResponse},
    int(HTTPStatus.FORBIDDEN): {"model": ReviewErrorResponse},
    int(HTTPStatus.CONFLICT): {"model": ReviewErrorResponse},
}


class ErrorResponse(ApiModel):
    error: str


def branch_selection_request_to_selection(
    request: BranchSelection,
) -> BranchSelection:
    """Validate one client-sent branch selection into its canonical value.

    Whitespace-padded names are trimmed; an empty branch, or an empty remote
    on a remote selection, is a request error. The result carries exactly the
    fields its source variant defines.
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
    """Request body for saving the repository main branch selection."""

    selection: BranchSelection


class RepoMainBranchResponse(ApiModel):
    """Persisted repository main branch selection."""

    project_id: int
    selection: BranchSelection


class RepoDefaultsResponse(ApiModel):
    """Repository defaults used to seed branch-review controls."""

    default_base_selection: DefaultBaseSelection
    preferred_review_selection: BranchSelection


class RepoRefsResponse(ApiModel):
    """Repository ref choices used for autocomplete and ref selection."""

    ref_choices: RefChoices


class PullRequestPrepareRequest(ApiModel):
    """Request body for preparing a pull request URL for diffing."""

    url: str


class PullRequestPrepareResponse(ApiModel):
    """Prepared Pull Request identity and immutable capture commits."""

    project_id: int
    pull_request_url: str
    left_commit: str
    right_commit: str


def pull_request_prepare_response(
    prepared: PreparedPullRequest,
) -> PullRequestPrepareResponse:
    """Serialize the complete prepared Pull Request for the HTTP API."""

    return PullRequestPrepareResponse.model_validate(
        {
            "project_id": prepared.project_id,
            "pull_request_url": prepared.pull_request_url,
            "left_commit": prepared.left_commit,
            "right_commit": prepared.right_commit,
        }
    )


class RepoMarkResponse(ApiModel):
    id: int
    path: str
    name: str
    marked_at: datetime


class UserProfileResponse(ApiModel):
    id: int | None
    username: str | None


class UserProfileUpdateRequest(ApiModel):
    username: str


class PreferencesResponse(ApiModel):
    user_profile_id: int
    aggressive_folds: bool


class PreferencesUpdateRequest(ApiModel):
    aggressive_folds: bool


class PresetGroupResponse(ApiModel):
    id: str
    display_name: str


class PresetCatalogResponse(ApiModel):
    default_preset: str
    groups: list[PresetGroupResponse]


class PresetCatalogsResponse(ApiModel):
    diff: PresetCatalogResponse
    fold: PresetCatalogResponse
    gumtree: PresetCatalogResponse
    scroll: PresetCatalogResponse
    notebook: PresetCatalogResponse


class DecoratedPartResponse(ApiModel):
    """One ordered, lossless slice of a rendered line.

    The API returns a complete partition for each present row side. Every part
    carries both the engine's inline diff classification and the rendering
    layer's syntax classes, so clients render the text directly without
    intersecting parallel offset ranges.
    """

    text: str
    syntax_classes: list[SyntaxClass]
    diff_status: InlineTokenStatus
    is_whitespace: bool
    is_leading_whitespace: bool


class FoldHintResponse(ApiModel):
    start_row: int
    end_row: int
    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    label: str


class DiffRowResponse(ApiModel):
    status: Literal["equal", "replace", "insert", "delete", "move"]
    """
    Display status of one real aligned engine row.
    """

    left_no: int | None
    right_no: int | None
    left_text: str | None
    right_text: str | None
    left_parts: list[DecoratedPartResponse]
    right_parts: list[DecoratedPartResponse]
    hunk_index: int | None
    """
    Zero-based bay-local hunk identity on a hunk's first rendered row.

    Other rows carry `None`. Display enrichment numbers each bay's own rows from
    zero, so a hunk coordinate is this index plus the owning bay's key; the
    backend never numbers a File as a whole. The value is assigned before
    frontend folding and virtualization and is therefore independent of DOM
    layout.
    """


class DiffSummaryResponse(ApiModel):
    changed_lines: int
    modified_lines: int
    added_lines: int
    removed_lines: int
    moved_lines: int = 0
    left_exists: bool
    right_exists: bool


class RepoDiffSummaryResponse(ApiModel):
    """Validate manifest-wide File totals and optional backend line totals.

    Added and removed line counts are either both backend-reported integers or
    both absent. They need not include additional untracked Files. Cell totals
    remain optional because only notebook-aware payloads can provide them.
    """

    changed_files: int
    added_files: int
    removed_files: int
    updated_files: int
    added_lines: Optional[int]
    removed_lines: Optional[int]
    skipped_files: int
    changed_cells: int | None = None
    added_cells: int | None = None
    removed_cells: int | None = None
    modified_cells: int | None = None

    @model_validator(mode="after")
    def validate_line_count_presence(self) -> RepoDiffSummaryResponse:
        """Reject a response carrying only one aggregate line count."""
        if (self.added_lines is None) != (self.removed_lines is None):
            raise ValueError(
                "added_lines and removed_lines must have equal presence"
            )
        return self


class GitFileKindResponse(ApiModel):
    type: Literal["git"]
    status: Literal["modified", "added", "deleted", "renamed", "copied"]


class UntrackedFileKindResponse(ApiModel):
    type: Literal["untracked"]


FileKindResponse = GitFileKindResponse | UntrackedFileKindResponse


class EngineWarningResponse(ApiModel):
    type: Literal[
        "difftastic_graph_limit",
        "difftastic_empty_rows",
        "gumtree_invalid_json",
        "tokendiff_region_limit",
    ]
    message: str


class BayStatsResponse(ApiModel):
    """Per-bay engine line counts, before File-level aggregation.

    A text bay reports the same five counts every engine reports. Side
    existence is a File fact and lives on the File `summary`, not here.
    """

    changed_lines: int
    modified_lines: int
    added_lines: int
    removed_lines: int
    moved_lines: int = 0


class ChangeStatusResponse(ApiModel):
    """A bay outcome fully told by its kind; moves carry coordinates and
    arrive as `MovedChangeStatusResponse` instead."""

    kind: Literal["added", "removed", "changed", "unchanged"]


class MovedChangeStatusResponse(ApiModel):
    """A bay's move: the name it wore in the old and the new document, or
    `None` on a side the builder cannot name. The rows may still show an
    edit."""

    kind: Literal["moved"]
    from_heading: str | None
    to_heading: str | None


class TextBayResponse(ApiModel):
    """One `text` bay: decorated rows from the shared text-bay renderer.

    `bay_key` is the sub-file coordinate shared with line pins and review
    text targets. `label` names the whole bay in its collapsed placeholder;
    `left_label`/`right_label` are the two side headings inside the grid.

    `change` reports what happened to this bay. Only the format builder can
    answer it: a notebook cell that moved and one whose output changed beyond
    its rendered text both have every row equal, so nothing downstream can tell
    them apart. Row `hunk_index` values are bay-local, numbered from zero
    within this bay; the frontend turns them into the File's one navigable
    sequence.
    """

    bay_key: str = Field(min_length=1)
    label: str
    detail: str | None = None
    collapsible: bool
    default_expanded: bool
    kind: Literal["text"]
    left_label: str
    right_label: str
    rows: list[DiffRowResponse]
    fold_hints: list[FoldHintResponse] = Field(default_factory=list)
    stats: BayStatsResponse
    engine_warning: EngineWarningResponse | None = None
    change: Annotated[
        ChangeStatusResponse | MovedChangeStatusResponse,
        Field(discriminator="kind"),
    ]


BayResponse = TextBayResponse
"""One bay of a composed diff.

This is a single-variant alias today. `image` and `binary` bay kinds turn it
into a `kind`-discriminated union; adding one is a change to make here and in the
frontend's matching declaration, and nowhere else.
"""


class FrameResponse(ApiModel):
    """One presentational frame: an optional heading over ordered bays.

    A frame carries no annotations of its own; everything a reviewer acts on
    belongs to a bay, which can be navigated to, collapsed, and commented on.
    """

    frame_key: str = Field(min_length=1)
    heading: str | None = None
    bays: list[BayResponse]


class ComposedDiffResponse(ApiModel):
    """The single `/api/file-diff` response shape: one composed diff.

    File-level metadata plus an ordered list of frames, each holding an ordered
    list of bays. There is no `render_kind`: the frame list is the shape, and
    a plain text File is simply one heading-less frame holding one text bay.
    """

    display_name: str
    left_label: str
    right_label: str
    left_path: str | None = None
    right_path: str | None = None
    file_kind: FileKindResponse
    summary: DiffSummaryResponse
    default_expanded: bool = True
    frames: list[FrameResponse]


class RepoFileEntryResponse(ApiModel):
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    lazy: LazyReason | None = None


class RepoManifestFileNodeResponse(ApiModel):
    type: Literal["file"]
    name: str
    entry: RepoFileEntryResponse


class RepoManifestDirectoryNodeResponse(ApiModel):
    type: Literal["directory"]
    name: str
    path: str
    entries: list[RepoManifestTreeEntryResponse]


RepoManifestTreeEntryResponse = (
    RepoManifestFileNodeResponse | RepoManifestDirectoryNodeResponse
)


class LazyInfoFileResponse(ApiModel):
    # This response must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    display_name: str
    changed_lines: int | None = None
    added_lines: int | None = None
    removed_lines: int | None = None
    lazy: LazyReason | None = None


class RepoManifestResponse(ApiModel):
    """Return one complete manifest and its retained Snapshot address.

    `snapshot_id` is exactly 32 lowercase hexadecimal characters and is the
    only Room lookup input accepted by follow-up endpoints.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    display_name: str
    left_label: str
    right_label: str
    summary: RepoDiffSummaryResponse
    tree: list[RepoManifestTreeEntryResponse]


class LazyInfoResponse(ApiModel):
    files: list[LazyInfoFileResponse]


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
) -> tuple[PresetTypeParam, str]:
    """Parse the preset catalog and subset used to prepare a manifest.

    The Preset Tab uses `project_id` as the catalog discriminator (`diff`, `fold`,
    `gumtree`, `scroll`, or `notebook`) and `preset_subset` as the selected group
    within that
    catalog. Follow-up endpoints find the prepared Room by Snapshot key and do
    not call this parser. The preset backend still validates traversal and
    unknown-group errors while preparing the manifest.
    """
    if project_id is None or project_id.strip() == "":
        raise DirdiffError("project_id is required for the Preset Tab.")
    if preset_subset is None or preset_subset.strip() == "":
        raise DirdiffError("preset_subset is required for the Preset Tab.")
    if project_id == "diff":
        preset_type: PresetTypeParam = "diff"
    elif project_id == "fold":
        preset_type = "fold"
    elif project_id == "gumtree":
        preset_type = "gumtree"
    elif project_id == "scroll":
        preset_type = "scroll"
    elif project_id == "notebook":
        preset_type = "notebook"
    else:
        raise DirdiffError(f"Unknown preset project_id: {project_id}")
    return preset_type, preset_subset


def marked_project_id(project_id: str | None) -> int:
    """Parse a positive Mark id from a repo-backed HTTP parameter.

    Manifest uses the result to construct the workspace backend. Follow-up
    operations never call this parser because their Snapshot id is sufficient.
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


def create_app(
    db: RepoMarkStore,
    user_profile_store: UserProfileStore | None = None,
    preferences_store: PreferencesStore | None = None,
    *,
    room_lord: RoomLord,
    presets_root: str | None = None,
) -> FastAPI:
    """Create the dirdiff FastAPI app and wire request orchestration.

    The app layer owns HTTP validation, database-backed repo marks, concrete
    backend construction, notebook detection, and response-model validation.
    The caller provides the `RoomLord` that selects prepared Rooms for manifest
    and recovers them by Snapshot key for follow-up operations. Storage and
    capture remain behind that interface. The server delegates already-loaded
    text rendering to the selected diff engine.
    """
    if user_profile_store is None:
        user_profile_store = UserProfileStore(db.engine)
    if preferences_store is None:
        preferences_store = PreferencesStore(db.engine)
    app = FastAPI()

    def review_http_exception(error: ReviewError) -> _ReviewHttpException:
        """Map one typed domain failure to the browser review HTTP contract."""
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

    @app.exception_handler(_ReviewHttpException)
    async def serve_review_error(
        request: Request,
        error: _ReviewHttpException,
    ) -> JSONResponse:
        """Serialize one typed review failure without an HTTP detail wrapper."""
        del request
        return JSONResponse(
            status_code=error.status,
            content=error.response.model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def serve_unexpected_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        """Log an unexpected HTTP failure before returning a generic response."""
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

    def preset_backend_for_type(preset_type: PresetTypeParam) -> PresetBackend:
        """Resolve which preset catalog backs a preset request.

        Diff, fold, GumTree, and scroll presets live in separate catalogs because
        they exercise different product surfaces. Keeping that split here means
        `/api/presets` can expose all catalogs without asking any rendering
        engine to know about fixture layout.
        """
        if preset_type == "diff":
            if presets_root is not None:
                return PresetBackend.discover(presets_root=Path(presets_root))
            return PresetBackend.discover()
        if preset_type == "fold":
            return PresetBackend.discover(
                presets_root=Path.cwd() / "tests" / "presets" / "folds"
            )
        if preset_type == "gumtree":
            return PresetBackend.discover(
                presets_root=Path.cwd() / "tests" / "presets" / "gumtree"
            )
        if preset_type == "notebook":
            return PresetBackend.discover(
                presets_root=Path.cwd() / "tests" / "presets" / "notebook"
            )
        return PresetBackend.discover(
            presets_root=Path.cwd() / "tests" / "presets" / "scroll"
        )

    def preset_catalog_for_type(
        preset_type: PresetTypeParam,
    ) -> PresetCatalogResponse:
        """Serialize one preset catalog for the presets endpoint.

        Preset catalog selection and engine selection are independent UI
        controls, so this endpoint exposes catalog metadata without involving a
        renderer.
        """
        preset_backend = preset_backend_for_type(preset_type)
        default_preset = preset_backend.default_preset_name()
        return PresetCatalogResponse.model_validate(
            {
                "default_preset": default_preset,
                "groups": preset_backend.list_preset_groups(),
            }
        )

    def manifest_capture_selection(
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

        The query surface carries every cross-Tab parameter, so this is the
        single boundary that rejects a missing Tab-required value and asserts
        that inapplicable values were not supplied. Downstream code receives
        one discriminated selection and never branches on nullability.
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
        *,
        project_id: str,
        selection: CaptureSelection,
    ) -> tuple[Room, UUID, str | None]:
        """Capture one exact Tab selection for browser and agent callers.

        The caller supplies the concrete selection variant. This operation
        selects its active mark or preset backend, applies Room
        correspondence, and returns the immutable Snapshot address plus the
        validated preset subset used only for its display name.
        """
        preset_name: str | None = None
        parsed_project_id: int | None = None
        if isinstance(selection, PresetCaptureSelection):
            preset_name = selection.subset
            backend: WorkspaceBackendProtocol = preset_backend_for_type(
                selection.catalog
            )
        else:
            parsed_project_id = marked_project_id(project_id)
            mark = db.get(parsed_project_id)
            if mark is None:
                raise DirdiffError(f"Invalid project_id: {parsed_project_id}")
            backend = GitBackend.discover(repo_root=Path(mark.path))
        room, snapshot_id = room_lord.corresponding_room(
            mark_id=parsed_project_id,
            backend=backend,
            selection=selection,
        )
        return room, snapshot_id, preset_name

    def render_loaded_snapshot_file(
        *,
        room: Room,
        snapshot_id: UUID,
        engine_name: EngineKind,
        pair: FilePair,
        left_file: Optional[Path],
        right_file: Optional[Path],
        file_meta: FileMeta,
    ) -> dict[str, Any]:
        """Compose one focused File into its `/api/file-diff` response payload.

        The handler does HTTP work only: it checks the capture error, reads the
        two captured byte sides, builds one `ComposeContext`, calls `compose()`,
        and attaches the two envelope fields composition does not produce -- the
        File's display name and file kind, which the manifest already settled.
        Decoding, engine selection, and payload assembly belong to composition.
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
        payload: dict[str, Any] = dict(composed)
        payload["display_name"] = display_name
        payload["file_kind"] = file_kind_for_change_type(
            file_meta["change_type"],
            file_kind=file_kind,
        )
        return payload

    def snapshot_room(snapshot_id: UUID) -> Room:
        """Return the Room containing one exact agent-selected Snapshot."""
        try:
            return room_lord.find_room(snapshot_id)
        except DirdiffError:
            raise DirdiffError(
                f"Unknown snapshot id: {snapshot_id.hex}"
            ) from None

    def agent_failure(status: HTTPStatus, detail: object) -> PlainTextResponse:
        """Return one concrete diagnostic for a rejected agent operation."""
        return PlainTextResponse(str(detail), status_code=status)

    def agent_preview(body: str | None, deleted: bool) -> AgentCommentPreview:
        """Bound one Comment body to the shared 256-character preview rule."""
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

    def captured_files_for_locations(
        room: Room,
        snapshot_id: UUID,
        locations: list[dict[str, object] | None],
    ) -> dict[tuple[str | None, str | None], tuple[Path | None, Path | None]]:
        """Load actual retained paths for exactly the referenced locations.

        Only the Files the supplied code locations name are read; Threads
        without a code location contribute nothing. Contents are never read.
        """
        pairs: list[tuple[str | None, str | None]] = []
        for location in locations:
            if location is None:
                continue
            pair = location["file"]
            assert isinstance(pair, dict)
            left_value = pair.get("left_path")
            right_value = pair.get("right_path")
            assert left_value is None or isinstance(left_value, str)
            assert right_value is None or isinstance(right_value, str)
            pairs.append((left_value, right_value))
        return room.captured_files_for_pairs(snapshot_id, tuple(pairs))

    def agent_location(
        captured_files: dict[
            tuple[str | None, str | None], tuple[Path | None, Path | None]
        ],
        location: dict[str, object] | None,
    ) -> tuple[str | None, AgentBayRange | AgentBayStart | None]:
        """Translate one code location into its captured File path and bay.

        A range location yields an `AgentBayRange`; a bay-start location
        yields an `AgentBayStart`; a file-start location yields no bay at
        all — the captured File path is the whole landing.
        """
        if location is None:
            return None, None
        pair = location["file"]
        assert isinstance(pair, dict)
        left_value = pair.get("left_path")
        right_value = pair.get("right_path")
        assert left_value is None or isinstance(left_value, str)
        assert right_value is None or isinstance(right_value, str)
        left_file, right_file = captured_files[(left_value, right_value)]
        side_value = location.get("side")
        if side_value == "left":
            selected_file = left_file
        elif side_value == "right" or right_file is not None:
            selected_file = right_file
        else:
            selected_file = left_file
        assert selected_file is not None
        bay: AgentBayRange | AgentBayStart | None = None
        range_value = location.get("range")
        bay_value = location.get("bay")
        if isinstance(range_value, dict):
            assert isinstance(bay_value, dict), (
                "a ranged location always names the bay it sits in"
            )
            bay = AgentBayRange.model_validate({**bay_value, **range_value})
        elif isinstance(bay_value, dict):
            # a bay without a range is a bay-start landing
            bay = AgentBayStart.model_validate(bay_value)
        return str(selected_file), bay

    def agent_thread(
        captured_files: dict[
            tuple[str | None, str | None], tuple[Path | None, Path | None]
        ],
        view: ThreadDiscussionView,
    ) -> AgentThread:
        """Translate one discussion using its existing captured File path."""
        file_path, bay = agent_location(captured_files, view["code_location"])
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
                ReviewExcerptResponse.model_validate(view["original_excerpt"])
                if view["original_excerpt"] is not None
                else None
            ),
            outdated_reason=view["outdated_reason"],
            comments=comments,
        )

    def agent_page[AgentPageItem](
        items: list[AgentPageItem],
        page: int,
        limit: int,
        total: int,
        through_activity_id: int | None = None,
    ) -> AgentPage[AgentPageItem]:
        """Build one valid one-based page response."""
        return AgentPage[AgentPageItem](
            items=items,
            page=page,
            limit=limit,
            total=total,
            has_more=page * limit < total,
            through_activity_id=through_activity_id,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_failure(
        request: Request, exc: RequestValidationError
    ) -> Any:
        """Return validation detail at the agent API boundary."""
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
            return agent_failure(HTTPStatus.UNPROCESSABLE_ENTITY, detail)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_failure(
        request: Request, exc: StarletteHTTPException
    ) -> Any:
        """Return framework failure detail at the agent API boundary."""
        route = request.scope.get("route")
        if getattr(route, "path", None) in _AGENT_ROUTE_PATHS:
            return agent_failure(HTTPStatus(exc.status_code), exc.detail)
        return await http_exception_handler(request, exc)

    @app.get("/", response_class=HTMLResponse)
    def serve_frontend_missing() -> HTMLResponse:
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

    @app.get(
        "/api/review/threads",
        response_model=ReviewThreadPage,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Read one page of review Threads",
    )
    def serve_review(
        snapshot_id: UUID = Query(description="Exact retained Snapshot id."),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
        through_activity_id: int | None = Query(default=None, ge=0),
    ) -> ReviewThreadPage:
        """Return one stable complete-Thread page for the History UI.

        Page one chooses the append-only review activity pivot. Later pages
        must repeat it so lifecycle changes cannot reorder the paged read.
        """
        try:
            room = room_lord.find_room(snapshot_id)
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
            raise review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    def review_target(request: NewCodeCommentRequest) -> TextTarget:
        """Translate one validated browser code target to Thread input."""
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

    @app.post(
        "/api/review/post_comment",
        response_model=ReviewThreadResponse | ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Post one review Comment",
    )
    def post_review_comment(
        request: PostCommentRequest,
    ) -> ReviewThreadResponse | ReviewThreadUpdateResponse:
        """Start one Thread or append one Comment to an existing Thread."""
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = room_lord.find_room(snapshot_id)
            if isinstance(request, NewCodeCommentRequest):
                thread = room.create_thread(
                    snapshot_id,
                    CreateThread(
                        uuid4(),
                        uuid4(),
                        uuid4(),
                        ProfileAuthor(request.profile_id),
                        review_target(request),
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
            raise review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @app.post(
        "/api/review/edit_comment",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Edit one review Comment",
    )
    def edit_review_comment(
        request: EditReviewCommentRequest,
    ) -> ReviewThreadUpdateResponse:
        """Edit one authored Comment using the backend's current revision."""
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = room_lord.find_room(snapshot_id)
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
            raise review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @app.post(
        "/api/review/delete_comment",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Delete one review Comment",
    )
    def delete_review_comment(
        request: DeleteReviewCommentRequest,
    ) -> ReviewThreadUpdateResponse:
        """Tombstone one Comment and retain its acting Profile in the action log."""
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = room_lord.find_room(snapshot_id)
            comment_id = UUID(hex=request.comment_id)
            return ReviewThreadUpdateResponse.model_validate(
                room.thread_for_comment(snapshot_id, comment_id).delete_comment(
                    comment_id,
                    DeleteComment(uuid4(), ProfileAuthor(request.profile_id)),
                )
            )
        except ReviewError as exc:
            raise review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    def change_review_thread_state(
        *,
        request: ChangeReviewThreadStateRequest | DeleteReviewThreadRequest,
        action: Literal["resolve", "reopen", "delete"],
    ) -> ReviewThreadUpdateResponse:
        """Apply one exact lifecycle operation shared by three HTTP routes."""
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            thread_id = UUID(hex=request.thread_id)
            room = room_lord.find_room(snapshot_id)
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
            raise review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @app.post(
        "/api/review/resolve_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Resolve one review Thread",
    )
    def resolve_review_thread(
        request: ChangeReviewThreadStateRequest,
    ) -> ReviewThreadUpdateResponse:
        """Resolve an open Thread at its exact current revision."""
        return change_review_thread_state(
            request=request,
            action="resolve",
        )

    @app.post(
        "/api/review/reopen_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Reopen one review Thread",
    )
    def reopen_review_thread(
        request: ChangeReviewThreadStateRequest,
    ) -> ReviewThreadUpdateResponse:
        """Reopen a resolved Thread at its exact current revision."""
        return change_review_thread_state(
            request=request,
            action="reopen",
        )

    @app.post(
        "/api/review/delete_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Delete one review Thread",
    )
    def delete_review_thread(
        request: DeleteReviewThreadRequest,
    ) -> ReviewThreadUpdateResponse:
        """Record terminal Thread deletion at its exact current revision."""
        return change_review_thread_state(
            request=request,
            action="delete",
        )

    @app.post(
        "/api/agent/join_review",
        response_model=NewAgentReviewResponse,
    )
    def join_agent_review(
        request: NewAgentReviewRequest,
    ) -> NewAgentReviewResponse | PlainTextResponse:
        """Register one disposable Profile and capture its explicit Tab."""
        try:
            if user_profile_store.agent_exists(request.agent_uuid):
                raise DirdiffError("Agent UUID already exists.")
            tab = request.tab
            if isinstance(tab, AgentPullRequestTab):
                prepared = prepare_pull_request(
                    url=tab.url, repo_marks=db.list()
                )
                room, snapshot_id, _ = capture_snapshot(
                    project_id=str(prepared.project_id),
                    selection=PullRequestCaptureSelection(
                        url=prepared.pull_request_url,
                        left_commit=prepared.left_commit,
                        right_commit=prepared.right_commit,
                    ),
                )
            else:
                matches = [
                    mark for mark in db.list() if mark.path == tab.repo_path
                ]
                if len(matches) != 1:
                    raise DirdiffError("Tab path does not identify one Mark.")
                project_id = str(matches[0].id)

                def branch(value: AgentBranch) -> BranchSelection:
                    """Translate one explicit API branch to backend input."""
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
                room, snapshot_id, _ = capture_snapshot(
                    project_id=project_id,
                    selection=selection,
                )
            try:
                profile = user_profile_store.create_agent(
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
            return agent_failure(HTTPStatus.BAD_REQUEST, exc)

    @app.get(
        "/api/agent/thread_summary",
        response_model=AgentPage[AgentThreadSummary],
    )
    def agent_thread_summary(
        snapshot_id: UUID,
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> AgentPage[AgentThreadSummary] | PlainTextResponse:
        """Return a bounded discovery page of unresolved Threads."""
        try:
            room = snapshot_room(snapshot_id)
            page_threads, total, _concrete_activity_id = room.threads(
                snapshot_id,
                page=page,
                limit=limit,
                state="open",
                through_activity_id=None,
            )
            views = [thread.summary() for thread in page_threads]
            captured_files = captured_files_for_locations(
                room, snapshot_id, [view["code_location"] for view in views]
            )
            summaries = []
            for view in views:
                assert view["state"] == "open"
                attention = view["attention"]
                assert attention != "none"
                file_path, bay = agent_location(
                    captured_files, view["code_location"]
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
                        first_comment=agent_preview(
                            first["body"], first["deleted"]
                        ),
                        latest_comment=agent_preview(
                            latest["body"], latest["deleted"]
                        ),
                        comment_count=view["comment_count"],
                    )
                )
            return agent_page(summaries, page, limit, total)
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent Thread-summary request failed")
            return agent_failure(HTTPStatus.BAD_REQUEST, exc)

    @app.get("/api/agent/threads", response_model=AgentPage[AgentThread])
    def agent_threads(
        snapshot_id: UUID,
        for_role: Literal["author", "reviewer"] | None = Query(
            default=None, alias="for"
        ),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=5, ge=1, le=20),
        through_activity_id: int | None = Query(default=None, ge=0),
    ) -> AgentPage[AgentThread] | PlainTextResponse:
        """Return complete unresolved Threads in bounded batches."""
        try:
            room = snapshot_room(snapshot_id)
            page_threads, total, concrete_activity_id = room.threads(
                snapshot_id,
                page=page,
                limit=limit,
                state="open",
                attention=for_role,
                through_activity_id=through_activity_id,
            )
            views = [thread.discussion() for thread in page_threads]
            captured_files = captured_files_for_locations(
                room, snapshot_id, [view["code_location"] for view in views]
            )
            threads = [agent_thread(captured_files, view) for view in views]
            return agent_page(threads, page, limit, total, concrete_activity_id)
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent Threads request failed")
            return agent_failure(HTTPStatus.BAD_REQUEST, exc)

    @app.get("/api/agent/thread/{thread_id}", response_model=AgentThreadPage)
    def agent_thread_by_id(
        thread_id: UUID,
        snapshot_id: UUID,
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> AgentThreadPage | PlainTextResponse:
        """Return one exact Thread with an independently paged discussion."""
        try:
            room = snapshot_room(snapshot_id)
            view = room.get_thread(snapshot_id, thread_id).discussion()
            thread = agent_thread(
                captured_files_for_locations(
                    room, snapshot_id, [view["code_location"]]
                ),
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
            return agent_failure(HTTPStatus.BAD_REQUEST, exc)

    @app.post(
        "/api/agent/continue_review",
        response_model=ContinueAgentReviewResponse,
    )
    def continue_agent_review(
        request: ContinueAgentReviewRequest,
    ) -> ContinueAgentReviewResponse | PlainTextResponse:
        """Recapture one Tab and return its bounded File and Thread changes."""
        try:
            previous_id = UUID(hex=request.snapshot_id)
            room = room_lord.find_room(previous_id)
            context = room.capture_context()
            mark = db.get(context["mark_id"])
            if mark is None:
                raise DirdiffError("Snapshot Mark no longer exists.")
            backend = GitBackend.discover(repo_root=Path(mark.path))
            if context["tab"] == "pull-request":
                assert context["pull_request_url"] is not None
                prepared = prepare_pull_request(
                    url=context["pull_request_url"], repo_marks=db.list()
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
                            comment=agent_preview(action.body, False),
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
                            comment=agent_preview(action.body, False),
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
                            comment=agent_preview(None, True),
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
            return agent_failure(HTTPStatus.BAD_REQUEST, exc)

    @app.post("/api/agent/actions", response_model=AgentActionsResponse)
    def apply_agent_actions(
        request: AgentActionsRequest,
    ) -> AgentActionsResponse | PlainTextResponse:
        """Validate and atomically apply one ordered agent-authored batch."""
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = snapshot_room(snapshot_id)
            if user_profile_store.get(request.profile_id) is None:
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
            return agent_failure(HTTPStatus.BAD_REQUEST, exc)

    @app.get("/api/repo-defaults")
    def serve_repo_defaults(
        project_id: int = Query(
            description="Marked project id. Required for repo-backed defaults.",
        ),
    ) -> RepoDefaultsResponse:
        """Return structured defaults for branch-review controls."""
        mark = db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        backend = GitBackend.discover(repo_root=Path(mark.path))
        # One metadata snapshot feeds both derivations so base and review
        # defaults cannot come from different repository states.
        metadata = backend.read_ref_metadata()
        saved_main_branch = db.get_main_branch(project_id)
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

    @app.get("/api/repo-refs")
    def serve_repo_refs(
        project_id: int = Query(
            description="Marked project id. Required for repo-backed refs.",
        ),
    ) -> RepoRefsResponse:
        """Return ref choices for repo-backed controls."""
        mark = db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        backend = GitBackend.discover(repo_root=Path(mark.path))
        return RepoRefsResponse.model_validate(
            {"ref_choices": ref_choices(backend.read_ref_metadata())}
        )

    @app.post(
        "/api/repos/{project_id}/main-branch",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Save the repository main branch selection",
    )
    def save_repo_main_branch(
        project_id: int,
        request: RepoMainBranchRequest,
    ) -> RepoMainBranchResponse:
        # Future auth belongs here: setting shared repository main remote/branch
        # should be admin-only once dirdiff has real users/permissions.
        mark = db.get(project_id)
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
            record = db.set_main_branch(
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

    @app.get(
        "/api/presets",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load grouped preset metadata",
    )
    def serve_presets() -> PresetCatalogsResponse:
        try:
            return PresetCatalogsResponse.model_validate(
                {
                    "diff": preset_catalog_for_type("diff"),
                    "fold": preset_catalog_for_type("fold"),
                    "gumtree": preset_catalog_for_type("gumtree"),
                    "scroll": preset_catalog_for_type("scroll"),
                    "notebook": preset_catalog_for_type("notebook"),
                }
            )
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

    @app.get("/api/repos")
    def serve_repos() -> list[RepoMarkResponse]:
        return [
            RepoMarkResponse.model_validate(mark, from_attributes=True)
            for mark in db.list()
        ]

    @app.delete(
        "/api/repos/{project_id}",
        status_code=HTTPStatus.NO_CONTENT,
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Remove a marked repository",
    )
    def delete_repo_mark(project_id: int) -> None:
        try:
            if not db.delete(project_id):
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

    @app.post(
        "/api/pull-request/prepare",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Prepare immutable repository state for a Pull Request Tab",
    )
    def prepare_pull_request_endpoint(
        request: PullRequestPrepareRequest,
    ) -> PullRequestPrepareResponse:
        try:
            return pull_request_prepare_response(
                prepare_pull_request(
                    url=request.url,
                    repo_marks=db.list(),
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

    @app.post(
        "/api/user-profile",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
        },
        summary="Create persisted user profile data",
    )
    def create_user_profile(
        request: UserProfileUpdateRequest,
    ) -> UserProfileResponse:
        try:
            return UserProfileResponse.model_validate(
                user_profile_store.create(request.username),
                from_attributes=True,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.get(
        "/api/user-profile",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Select persisted user profile data by exact username",
    )
    def get_user_profile(username: str) -> UserProfileResponse:
        """Return the one existing Profile selected by its exact username."""
        try:
            profile = user_profile_store.get_by_username(username)
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

    @app.patch(
        "/api/user-profile/{profile_id}",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted user profile data",
    )
    def update_user_profile(
        profile_id: int,
        request: UserProfileUpdateRequest,
    ) -> UserProfileResponse:
        try:
            profile = user_profile_store.update_username(
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

    @app.get(
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Load persisted user preferences",
    )
    def serve_preferences(profile_id: int) -> PreferencesResponse:
        profile = user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            preferences_store.get_or_create(profile_id),
            from_attributes=True,
        )

    @app.patch(
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted user preferences",
    )
    def update_preferences(
        profile_id: int,
        request: PreferencesUpdateRequest,
    ) -> PreferencesResponse:
        profile = user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            preferences_store.set_aggressive_folds(
                profile_id, request.aggressive_folds
            ),
            from_attributes=True,
        )

    @app.get(
        "/api/manifest",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Show the repository state selected by a Tab",
    )
    def serve_manifest(
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
        """
        try:
            room, snapshot_id, preset_name = capture_snapshot(
                project_id=project_id,
                selection=manifest_capture_selection(
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
            payload = build_repo_manifest_for_paths(
                left_label=snapshot_meta["left_label"],
                right_label=snapshot_meta["right_label"],
                paths=manifest_paths,
                added_lines=snapshot_meta["added_lines"],
                removed_lines=snapshot_meta["removed_lines"],
            )
            if tab == "preset":
                assert preset_name is not None
                payload["display_name"] = preset_name
            payload["snapshot_id"] = snapshot_id.hex
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

    @app.get(
        "/api/lazy-info",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load repository lazy file metadata",
    )
    def serve_lazy_info(
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
    ) -> LazyInfoResponse:
        """Return delayed-file metadata from one manifest Snapshot.

        `snapshot_id` is the sole Room lookup input. The containing Room's
        persisted Tab supplies presentation behavior; no live Git, worktree,
        index, or preset state is read.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            room = room_lord.find_room(snapshot_key)
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

    @app.get(
        "/api/file-diff",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load a single file diff",
    )
    def serve_file_diff(
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
            room = room_lord.find_room(snapshot_key)
            left = Path(pair.left_path) if pair.left_path is not None else None
            right = (
                Path(pair.right_path) if pair.right_path is not None else None
            )
            left_file, right_file, file_meta = room.get(
                snapshot_key, left, right
            )
            payload = render_loaded_snapshot_file(
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

        return ComposedDiffResponse.model_validate(payload)

    return app


def uvicorn_entrypoint() -> FastAPI:
    """Construct the production app from the CLI's serialized runtime config.

    The factory opens one SQLite engine, builds the registry and Room
    persistence interfaces over it, and supplies the configured store root to
    `RoomLord`. The CLI must provide the environment payload and at least one
    active Mark before uvicorn imports this function.
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
