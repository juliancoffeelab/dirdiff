"""Expose the HTTP boundary used by separately running agents.

ExternalAgentRoutes keeps the agent wire models, the plain-text failure
contract, and every /api/agent/* handler together. It translates between agent
entities (Tab descriptors, repository paths, roles) and the Room review
operations that the browser also uses.

Instances retain only constructor-injected application interfaces and immutable
resource configuration. This module does not implement an integrated agent, own
review state, render the HUD, or construct the FastAPI application.

The boundary mirrors the browser and gates nothing: any Profile may act on any
Thread, a role is stated per action instead of stored, and every read is
addressed by the ids the previous response returned.
"""

import asyncio
import logging
import time
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import (
    Annotated,
    Literal,
    Self,
)
from uuid import UUID, uuid4

from fastapi import Query, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    PlainTextResponse,
    Response,
)
from pydantic import (
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from dirdiff.change_source import (
    BranchSelection,
    GitRepository,
    prepare_pull_request,
)
from dirdiff.db import (
    CommentCreatedAction,
    CommentDeletedAction,
    CommentEditedAction,
    FileStartPlacement,
    RangePlacement,
    RepoMarkStore,
    ReviewScopePlacement,
    ReviewThreadHistory,
    ThreadDeletedAction,
    ThreadStateAction,
    UserProfileStore,
)
from dirdiff.engines import (
    DirdiffError,
)
from dirdiff.review import (
    AddComment,
    BatchComment,
    BatchCreate,
    BatchDeleteComment,
    BatchDeleteThread,
    BatchEditComment,
    BatchReopen,
    BatchResolve,
    ChangeThreadState,
    CreateReviewThread,
    CreateThread,
    DeleteComment,
    EditComment,
    FilePair,
    LineRange,
    ProfileAuthor,
    ReviewBatchAction,
    ReviewCommentView,
    ReviewError,
    ReviewRole,
    TextTarget,
    ThreadDiscussionView,
    ThreadSummaryView,
)
from dirdiff.room_lord import (
    BranchReviewCaptureSelection,
    HeadCaptureSelection,
    PullRequestCaptureSelection,
    RevisionsCaptureSelection,
    Room,
    RoomLord,
)
from dirdiff.server.base import (
    ApiModel,
    BranchReviewManifestSelection,
    ClassRoutes,
    HeadBaselineManifestSelection,
    ManifestSelection,
    PullRequestManifestSelection,
    RefsManifestSelection,
    ReviewExcerptResponse,
)
from dirdiff.util import JsonValue, Some

__all__ = ["ExternalAgentRoutes"]

LOGGER = logging.getLogger(__name__)
"""Record unexpected failures at this HTTP boundary."""

_AGENT_ROUTE_PATHS = frozenset(
    {
        "/api/agent/onboard",
        "/api/agent/profile",
        "/api/agent/capture",
        "/api/agent/reviews",
        "/api/agent/threads",
        "/api/agent/thread/{thread_id}",
        "/api/agent/activity",
        "/api/agent/wait",
        "/api/agent/actions",
    }
)
"""Exact endpoint templates whose failures use the agent plain-text contract.

The validation and HTTP exception handlers compare Starlette's matched route
template, not the concrete URL, so parameterized Thread paths remain covered.
"""

_PAGE_LIMIT_MAX = 100
"""Largest page or batch size any agent listing endpoint accepts."""

_WAIT_TIMEOUT_MAX = 540
"""Longest `wait` hold in seconds (nine minutes), below common 10 minute proxy
and tool timeouts so a held call is never cut off by its caller."""

_WAIT_POLL_SECONDS = 0.25
"""Interval at which a held `wait` re-reads the repository's newest activity id.

Each poll is one small indexed read; no worker thread is held between polls."""

_TITLE_MAX = 120
"""Longest Thread title in characters, including the truncation ellipsis."""


class AgentBranch(ApiModel):
    """Name one symbolic branch in an agent Branch Review Tab.

    `remote=None` selects a local branch; a remote name keeps the two parts
    structured for repository resolution.

    The value is not an arbitrary Git ref or resolved commit.
    """

    remote: str | None
    """Remote namespace containing `name`, when one is selected.

    `None` makes the branch local. A present value is passed separately to the
    repository and must not be joined with the branch into an arbitrary ref string.
    """

    name: str = Field(min_length=1)
    """Symbolic branch name within the selected local or remote namespace.

    It must be non-empty. Resolution happens during capture; this value does
    not promise an immutable commit or accept an absent branch.
    """


class AgentHeadTab(ApiModel):
    """Ask the agent API to capture the worktree against a named baseline.

    Capture validates `repo_path` against the registry and applies the HEAD
    Tab correspondence law: the Room is the one keyed on `baseline`, so the
    same descriptor keeps reaching the same Room after HEAD advances.

    The model carries no Snapshot identity.
    """

    kind: Literal["head"]
    """Select HEAD-versus-worktree capture for agent review.

    Only the literal `head` is valid. `repo_path` and `baseline` are the
    complete Tab input.
    """

    repo_path: str = Field(min_length=1)
    """Exact workspace path of an already active repository mark.

    Capture looks it up without creating a mark. The non-empty string must
    match the stored path; a merely readable unregistered directory is invalid.
    """

    baseline: str = Field(min_length=1)
    """Full baseline commit id the Head Room is keyed on.

    `onboard` fills the repository's current HEAD commit (or the empty-tree id
    for an unborn HEAD); `reviews` and `capture` return the Room's original
    baseline. Capture compares the live worktree against exactly this commit,
    so it must name a commit of the repository or its empty tree.
    """


class AgentRefsTab(ApiModel):
    """Ask the agent API to capture two explicit repository sides.

    Capture validates the marked repository and normalizes `left` and
    `right` through its repository before capture.

    The values are side names, not display labels or guaranteed commits.
    """

    kind: Literal["refs"]
    """Select capture of the two explicitly named repository sides.

    Only the literal `refs` is valid. It requires both `left` and `right` and
    does not apply Branch Review merge-base semantics.
    """

    repo_path: str = Field(min_length=1)
    """Exact workspace path of an already active repository mark.

    Capture looks it up without creating a mark. The path selects the
    repository that normalizes both side names.
    """

    left: str = Field(min_length=1)
    """Non-empty repository side name for the left capture input.

    The selected repository normalizes and loads it. The caller must not
    assume the value is already an immutable commit.
    """

    right: str = Field(min_length=1)
    """Non-empty repository side name for the right capture input.

    It is interpreted by the same repository as `left`; the ordered pair defines
    the ordered Tab sides and must not be reversed during continuation.
    """


class AgentBranchReviewTab(ApiModel):
    """Ask the agent API to capture one Branch Review Tab.

    Capture resolves the structured base and review branches to immutable
    commits, then applies Branch Review correspondence.

    This request keeps symbolic selections only; it does not let callers supply
    arbitrary merge-base commits.
    """

    kind: Literal["branch-review"]
    """Select Branch Review resolution and capture.

    Only the literal `branch-review` is valid. It requires structured `base`
    and `review` selections. Source setup prepares capture sides using their
    merge base.
    """

    repo_path: str = Field(min_length=1)
    """Exact workspace path of an already active repository mark.

    The mark selects the repository that resolves both symbolic branch selections.
    Capture does not register an unmarked path.
    """

    base: AgentBranch
    """Symbolic base branch used to establish the review baseline.

    Capture resolves it together with `review`; callers provide no merge-base
    commit and must keep the symbolic selection.
    """

    review: AgentBranch
    """Symbolic branch containing the changes under review.

    Capture resolves it with `base` and retains the structured selection as
    part of the Tab context. It is not a presentation label.
    """


class AgentPullRequestTab(ApiModel):
    """Ask the agent API to prepare and capture one Pull Request.

    Capture matches the URL to a marked repository, fetches the supported
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

- `AgentHeadTab` compares a named baseline commit and the worktree.
- `AgentRefsTab` compares explicit sides.
- `AgentBranchReviewTab` resolves symbolic branches.
- `AgentPullRequestTab` prepares a supported forge URL.

Capture and `reviews` use this descriptor. Preset Tabs are not part of
the current agent boundary.
"""


class AgentOnboardResponse(ApiModel):
    """Return everything needed to enter one supported agent workflow.

    The copied onboarding URL selects the running dirdiff server and one
    complete HUD Tab. This response translates that selection to the Tab
    descriptor `capture` accepts and publishes the installed instruction files
    for the three agent roles. It creates no Profile, Room, Snapshot, or review
    activity.
    """

    dirdiff_url: str = Field(min_length=1)
    """Absolute origin of the dirdiff server that returned this response.

    Agent command references use this value as `DD_URL`. It contains no endpoint
    path, Tab parameters, credentials, or browser-only state.
    """

    project_id: int | None = Field(default=None, ge=1)
    """Repository Mark id of the selected Tab, as `reviews`, `activity`, and
    `wait` take it; `None` for a Pull Request Tab, whose repository is only
    known after `capture` (which returns it)."""

    tab: AgentReviewTab
    """Complete Tab descriptor reconstructed from the copied HUD selection.

    Repository-backed variants contain the marked repository's exact path rather
    than its browser-only numeric id. The value can be sent unchanged as the
    `tab` of a `capture` body.
    """

    skill_paths: list[str] = Field(min_length=3, max_length=3)
    """Absolute paths to the review, round, and babysit skill entry files.

    The order is `review-patch`, `round-review`, then `babysit-patch`. Every path
    names a validated regular `SKILL.md` in this running installation.
    """

    next_step: str = Field(min_length=1)
    """Plain-text instructions for an agent that has read only this response.

    Gives the `dirdiff agent init` command with this request's full URL filled
    in as `--onboard-url` and placeholders for what the agent must supply, says
    which `skill_paths` entry fits which role, and names the `dirdiff agent` CLI
    as the supported way to use the API. It carries no state beyond that text.
    """


class AgentProfileRequest(ApiModel):
    """Register or update the Profile bound to one agent UUID.

    The call is idempotent on `agent_uuid`: the first call creates an ordinary
    Profile, later calls with the same UUID return the same Profile and replace
    its name, model, and harness. The request grants no role.
    """

    agent_uuid: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Agent-chosen stable identity, kept across restarts of the agent.

    A canonical hyphenated UUID is accepted and stored in its compact lowercase
    form.
    """

    name: str = Field(min_length=1)
    """Unique, nonblank display name without surrounding whitespace.

    A name already used by a different Profile is an error.
    """

    model: str = Field(min_length=1)
    """Model the agent reports, shown next to its Comments."""

    harness: str = Field(min_length=1)
    """Program driving the agent, shown next to its Comments."""

    @field_validator("agent_uuid", mode="before")
    @classmethod
    def normalize_agent_uuid(cls, value: JsonValue) -> JsonValue:
        """Normalize a canonical hyphenated UUID to the stored compact form.

        Pydantic then applies the compact lowercase pattern, so malformed
        values remain ordinary validation failures.
        """
        if isinstance(value, str) and len(value) == 36:
            try:
                return UUID(value).hex
            except ValueError:
                return value
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Accept an exact nonblank name or reject the request.

        The value is returned unchanged; trimming would make the stored name
        differ from the one the agent sent.

        # Failures

        - Raises `ValueError` when the name is blank or has surrounding
          whitespace.
        """
        if value != value.strip() or value.strip() == "":
            raise ValueError("Invalid agent name.")
        return value

    @field_validator("model", "harness")
    @classmethod
    def validate_label(cls, value: str) -> str:
        """Reject a blank model or harness.

        # Failures

        - Raises `ValueError` when the value is only whitespace.
        """
        if value.strip() == "":
            raise ValueError("Value must not be blank.")
        return value


class AgentProfileResponse(ApiModel):
    """Return the Profile bound to an agent UUID after registration."""

    profile_id: int = Field(gt=0)
    """Durable Profile id sent as `profile_id` with every action batch."""

    name: str
    """Current display name."""

    model: str
    """Current model."""

    harness: str
    """Current harness."""


class AgentCaptureRequest(ApiModel):
    """Capture one Tab and return its Snapshot, exactly as the browser would.

    The body is the Tab descriptor that `onboard` and `reviews` return. The
    Tab is resolved by the same code that backs the browser manifest, so the
    same descriptor addresses the same Room.
    """

    tab: AgentReviewTab
    """Complete Tab descriptor to capture."""


class AgentManifestFile(ApiModel):
    """Describe one captured File pair of a Snapshot.

    Both repository paths are relative to the repository root. The absolute
    paths locate the captured bytes on disk, read-only.
    """

    left_path: str | None
    """Repository path of the left side, `None` when the left side is absent."""

    right_path: str | None
    """Repository path of the right side, `None` when the right side is absent."""

    change_type: Literal["modify", "add", "delete", "rename", "copy"]
    """Kind of change between the sides."""

    capture_error: str | None
    """Why a side could not be captured, or `None` for a clean capture.

    A File with an error stays in the manifest; its captured bytes are
    dirdiff's placeholder, not source.
    """

    left_file: str | None
    """Absolute path of the captured left side, `None` when it is absent."""

    right_file: str | None
    """Absolute path of the captured right side, `None` when it is absent."""


class AgentCaptureResponse(ApiModel):
    """Return the captured Snapshot of one Tab and its File manifest."""

    project_id: int = Field(gt=0)
    """Repository Mark id; the value `reviews`, `activity`, and `wait` take."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot key to send with Thread reads and `create-thread` actions."""

    tab: AgentReviewTab
    """Descriptor of the Room the capture landed in, as `reviews` lists it.

    A Head descriptor carries the Room's original baseline commit. Keep this
    value and send it unchanged to capture again in the same Room.
    """

    snapshot_path: str = Field(min_length=1)
    """Absolute directory holding the captured files, read-only evidence."""

    latest_activity_id: int = Field(ge=0)
    """Greatest authored activity id of the whole repository right now.

    Pass it as `after` to `activity` or `wait` to hear only about later
    actions. 0 means no authored action exists yet.
    """

    files: list[AgentManifestFile]
    """Every captured File pair in Snapshot order."""


class AgentAuthor(ApiModel):
    """Identify who wrote or did something, human or agent."""

    profile_id: int = Field(gt=0)
    """Durable Profile id."""

    name: str
    """Current display name of the Profile."""

    is_agent: bool
    """Whether the Profile was registered through `POST /api/agent/profile`."""

    model: str | None
    """Reported model; `None` for humans and for agents registered before it
    was recorded."""

    harness: str | None
    """Reported harness; `None` for humans and for agents registered before it
    was recorded."""


class AgentComment(ApiModel):
    """Expose one Comment of a Thread, deleted ones as tombstones."""

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable Comment id."""

    author: AgentAuthor
    """Profile that wrote the Comment."""

    body: str | None
    """Current text, `None` once the Comment is deleted."""

    deleted: bool
    """Whether the Comment is a tombstone; it keeps its position."""

    revision: int = Field(ge=0)
    """Edit count; grows with every edit or deletion."""

    created_at: datetime
    """Creation time in UTC."""

    updated_at: datetime
    """Time of the latest edit or deletion in UTC; equals `created_at` before
    any change."""


class AgentCommentPreview(ApiModel):
    """Bound one Comment body for summary lists."""

    body: str | None
    """Text cut to 256 characters (ending in an ellipsis when cut), or `None`
    for a deleted Comment."""

    deleted: bool
    """Whether the Comment is a tombstone."""

    truncated: bool
    """Whether `body` was cut; read the Thread for the full text."""


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


class AgentLocation(ApiModel):
    """Name the repository code a Thread is about.

    The path is a repository-relative path, not a captured path on disk. A
    review-scoped Thread has no location at all, so wherever a location is
    optional, `None` means the whole review.
    """

    path: str
    """Repository-relative path of the File side the Thread is on."""

    side: Literal["left", "right"]
    """Side of the diff the lines belong to."""

    bay_key: str | None
    """Composed bay holding the lines; `None` for a historical File-level
    Thread, which names no bay."""

    start_line: int | None = Field(default=None, ge=1)
    """First line, one-based and inclusive within the bay, or `None` when no
    range is known."""

    end_line: int | None = Field(default=None, ge=1)
    """Last line, one-based and inclusive within the bay, or `None` when no
    range is known."""


AgentOutdatedReason = Literal[
    "region_changed",
    "region_not_found",
    "bay_not_found",
    "file_missing",
    "file_unreadable",
]
"""Why a Thread's original lines cannot be shown in the read Snapshot.

`region_changed` still lands on moved lines; the others do not land on the
original lines. Absent means the original lines are intact."""

AgentStatus = Literal["open", "resolved", "deleted"]
"""Lifecycle state of a Thread."""

AgentWaiting = Literal["author", "reviewer", "both", "none"]
"""Role a Thread currently waits on.

`both` appears only for Threads whose latest state came from a browser action;
count it toward each role. `none` means resolved or deleted."""


class AgentThreadSummary(ApiModel):
    """Summarize one Thread for a listing."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable Thread id."""

    status: AgentStatus
    """Lifecycle state."""

    waiting_on: AgentWaiting
    """Role the Thread waits on."""

    title: str | None
    """First line of the first Comment, cut to 120 characters; `None` when that
    Comment was deleted."""

    location: AgentLocation | None
    """Where the Thread sits in the listed Snapshot; `None` for a
    review-scoped Thread."""

    outdated_reason: AgentOutdatedReason | None
    """Why the original lines are not intact in the listed Snapshot."""

    comment_count: int = Field(ge=1)
    """Number of Comments including tombstones."""

    first_comment: AgentCommentPreview
    """Bounded first Comment."""

    latest_comment: AgentCommentPreview
    """Bounded latest Comment."""

    latest_activity_id: int = Field(ge=1)
    """Activity id of the Thread's newest authored action."""


class AgentPage[AgentPageItem](ApiModel):
    """Wrap one bounded page of a listing."""

    items: list[AgentPageItem]
    """Values of this page in the endpoint's stable order."""

    page: int = Field(ge=1)
    """One-based page number."""

    limit: int = Field(ge=1)
    """Requested page capacity, at most 100."""

    total: int = Field(ge=0)
    """Count of all matching values across pages."""

    has_more: bool
    """Whether a following page exists."""


class AgentThreadPage(AgentPage[AgentThreadSummary]):
    """One page of the Room's Thread listing, fixed by an activity pivot.

    The first page chooses `through_activity_id`; every later page must send it
    back so membership, order, and total cannot shift while the caller pages.
    """

    through_activity_id: int = Field(ge=0)
    """Inclusive append-only activity pivot this page was read at.

    Page one sets it to the Room's greatest current activity; repeat it
    unchanged as the request parameter on every later page.
    """


class AgentThread(ApiModel):
    """Expose one Thread with a page of its Comments."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Stable Thread id."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot the Thread was created in; `location` and
    `original_excerpt` describe that Snapshot."""

    status: AgentStatus
    """Lifecycle state."""

    waiting_on: AgentWaiting
    """Role the Thread waits on."""

    location: AgentLocation | None
    """Where the Thread was created; `None` for a review-scoped Thread."""

    original_excerpt: ReviewExcerptResponse | None
    """Source lines around the original selection; `None` when the Thread has
    no text range (review-scoped or historical File-level)."""

    comments: list[AgentComment]
    """This page of Comments in creation order, tombstones included."""

    page: int = Field(ge=1)
    """One-based Comment page number."""

    limit: int = Field(ge=1)
    """Requested Comment page capacity, at most 100."""

    total_comments: int = Field(ge=1)
    """Count of all Comments including tombstones."""

    has_more: bool
    """Whether a following Comment page exists."""


class AgentReview(ApiModel):
    """Summarize the Thread load of one Room of a repository."""

    tab: AgentReviewTab
    """Tab descriptor accepted unchanged by `capture`.

    A Head Room's descriptor carries its original baseline commit, so each Head
    review is distinguishable and capturing it again selects that same Room
    even after HEAD advanced."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Newest Snapshot of the Room."""

    open_threads: int = Field(ge=0)
    """Open Thread count."""

    waiting_on_author: int = Field(ge=0)
    """Open Threads waiting on the author (including `both`)."""

    waiting_on_reviewer: int = Field(ge=0)
    """Open Threads waiting on the reviewer (including `both`)."""


class AgentReviewsResponse(ApiModel):
    """List the Rooms of one repository that have a Snapshot.

    Preset Rooms belong to no repository and never appear.
    """

    reviews: list[AgentReview]
    """Rooms in creation order."""


class AgentEventComment(ApiModel):
    """Carry the Comment an action created, edited, or deleted."""

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Comment id."""

    author: AgentAuthor
    """Profile that performed the action on the Comment."""

    body: str | None
    """Text the action wrote; `None` for a deletion."""


AgentActionKind = Literal[
    "create-thread",
    "comment",
    "resolve",
    "reopen",
    "edit-comment",
    "delete-comment",
    "delete-thread",
]
"""Kind of an authored action, named as in the `actions` request."""


class AgentActivityEvent(ApiModel):
    """Describe one authored action completely, without further reads."""

    activity_id: int = Field(ge=1)
    """Increasing id of the action across the whole database."""

    created_at: datetime
    """Time of the action in UTC."""

    tab: AgentReviewTab
    """Tab descriptor of the review the Thread belongs to."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread the action touched."""

    title: str | None
    """First line of the Thread's first Comment as originally written, cut to
    120 characters."""

    location: AgentLocation | None
    """Where the Thread was created; `None` for a review-scoped Thread."""

    action: AgentActionKind
    """What the action did."""

    author: AgentAuthor
    """Profile that performed the action."""

    comment: AgentEventComment | None
    """Comment written, edited, or deleted by the action, if any."""

    status: AgentStatus
    """Thread status right after the action."""

    waiting_on: AgentWaiting
    """Role the Thread waited on right after the action."""


class AgentActivityResponse(ApiModel):
    """Return authored actions newer than a cursor, oldest first."""

    events: list[AgentActivityEvent]
    """Actions with `activity_id` greater than `after`, up to `limit`."""

    next_after: int = Field(ge=0)
    """Cursor to send as `after` next: the last event's id, or the request's
    `after` when `events` is empty."""

    has_more: bool
    """Whether more newer actions exist beyond `events`."""


class AgentCreateAction(ApiModel):
    """Open a Thread on code or on the review as a whole.

    The target fields are validated as a group: sent together they anchor a
    code Thread, all omitted they make a Thread on the whole review.
    """

    kind: Literal["create-thread"]
    """Action discriminator."""

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Snapshot whose captured code the range refers to."""

    role: ReviewRole
    """Role the actor plays; afterwards the Thread waits on the other role."""

    body: str
    """First Comment text."""

    path: str | None = Field(default=None, min_length=1)
    """Repository-relative path of the File side to comment on."""

    side: Literal["left", "right"] | None = None
    """Side of the diff that `path` names."""

    bay: AgentBayRange | None = None
    """Bay and inclusive one-based line range inside it."""

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        """Require `path`, `side`, and `bay` to be all present or all absent.

        # Failures

        - Raises `ValueError` for a partial target.
        """
        present = [
            value is not None for value in (self.path, self.side, self.bay)
        ]
        if any(present) and not all(present):
            raise ValueError("path, side, and bay must be sent together.")
        return self


class AgentCommentAction(ApiModel):
    """Add a Comment to a Thread in any state except deleted."""

    kind: Literal["comment"]
    """Action discriminator."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread receiving the Comment."""

    role: ReviewRole
    """Role the actor plays; afterwards the Thread waits on the other role."""

    body: str
    """Comment text."""


class AgentResolveAction(ApiModel):
    """Resolve an open Thread, optionally with a closing Comment."""

    kind: Literal["resolve"]
    """Action discriminator."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread to resolve; afterwards it waits on nobody."""

    body: str | None = None
    """Optional closing Comment."""


class AgentReopenAction(ApiModel):
    """Reopen a resolved Thread, optionally with a Comment."""

    kind: Literal["reopen"]
    """Action discriminator."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread to reopen."""

    role: ReviewRole
    """Role the actor plays; afterwards the Thread waits on the other role."""

    body: str | None = None
    """Optional Comment."""


class AgentEditCommentAction(ApiModel):
    """Replace the text of the actor's own Comment."""

    kind: Literal["edit-comment"]
    """Action discriminator."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread containing the Comment."""

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Comment to edit; only its author may."""

    body: str
    """Replacement text."""


class AgentDeleteCommentAction(ApiModel):
    """Tombstone a Comment; any Profile may."""

    kind: Literal["delete-comment"]
    """Action discriminator."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread containing the Comment."""

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Comment to delete."""


class AgentDeleteThreadAction(ApiModel):
    """Delete a Thread permanently; its history stays readable."""

    kind: Literal["delete-thread"]
    """Action discriminator."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread to delete; every later action on it is rejected."""


AgentAction = Annotated[
    AgentCreateAction
    | AgentCommentAction
    | AgentResolveAction
    | AgentReopenAction
    | AgentEditCommentAction
    | AgentDeleteCommentAction
    | AgentDeleteThreadAction,
    Field(discriminator="kind"),
]
"""One item of an `actions` batch; `kind` selects the variant."""


class AgentActionsRequest(ApiModel):
    """Apply an ordered batch of actions as one Profile, atomically.

    Every item is validated against the state earlier items produce, then all
    are appended in one transaction or none are. All items must belong to one
    review (one Room).
    """

    profile_id: int = Field(gt=0)
    """Profile every item is attributed to."""

    actions: list[AgentAction] = Field(min_length=1, max_length=_PAGE_LIMIT_MAX)
    """One to 100 items, applied in order."""


class AgentActionResult(ApiModel):
    """Report the outcome of one applied item."""

    kind: AgentActionKind
    """Kind of the item, echoed."""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Thread the item created or changed."""

    comment_id: str | None
    """Comment the item created or changed, `None` for bare transitions."""

    activity_id: int = Field(ge=1)
    """Activity id assigned to the item."""

    status: AgentStatus
    """Thread status right after the item."""

    waiting_on: AgentWaiting
    """Role the Thread waits on right after the item."""


class AgentActionsResponse(ApiModel):
    """Return one result per submitted item, in submission order."""

    results: list[AgentActionResult]
    """Outcomes in the order of the request's `actions`."""


class ExternalAgentRoutes:
    """Bind external-agent handlers to their application interfaces.

    One instance retains the repository registry, Profile store, Room service,
    preset root, and validated installed skill paths used by the agent HTTP
    entities. Its declarations include the agent-specific framework failure
    handlers.
    """

    routes = ClassRoutes()
    """Import-time declarations bound to one route-group instance."""

    def __init__(
        self,
        db: RepoMarkStore,
        user_profile_store: UserProfileStore,
        *,
        agent_skills_root: Path,
        room_lord: RoomLord,
        presets_root: str,
        store_path: Path,
    ) -> None:
        """Retain the exact interfaces used by external-agent handlers.

        # Parameters

        - `db`: Repository registry used to resolve Tabs and Marks.
        - `user_profile_store`: Profile and agent binding persistence.
        - `agent_skills_root`: Directory containing the three installed agent
          workflow skills exposed by onboarding.
        - `room_lord`: Room selection and Thread lookup interface.
        - `presets_root`: Exact Preset catalog root used by Tab preparation.
        - `store_path`: Configured Snapshot root, matching the RoomLord store;
          concrete source setup uses it for physical placement checks.

        # Failures

        - Raises when the skill root or any required `SKILL.md` is missing.
        """
        resolved_skills_root = agent_skills_root.resolve(strict=True)
        skill_paths = tuple(
            (resolved_skills_root / name / "SKILL.md").resolve(strict=True)
            for name in ("review-patch", "round-review", "babysit-patch")
        )
        assert all(path.is_file() for path in skill_paths), (
            "agent skill entry must be a regular file"
        )
        self.db = db
        self.user_profile_store = user_profile_store
        self.agent_skill_paths = tuple(str(path) for path in skill_paths)
        self.room_lord = room_lord
        self.presets_root = presets_root
        self.store_path = store_path

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
        """Bound one Comment body to the 256-character preview rule.

        # Parameters

        - `body`: Current Comment text, or `None` for a tombstone.
        - `deleted`: Whether the Comment is a tombstone.
        """
        if body is None or len(body) <= 256:
            return AgentCommentPreview(
                body=body, deleted=deleted, truncated=False
            )
        return AgentCommentPreview(
            body=f"{body[:255]}…", deleted=deleted, truncated=True
        )

    def agent_title(self, body: str | None) -> str | None:
        """Cut a Thread's first Comment to its first line, at most 120 chars.

        # Parameters

        - `body`: First Comment text, or `None` when it was deleted.

        # Returns

        - The first line, ending in an ellipsis when it exceeds the limit.
        - `None`: the first Comment was deleted, so there is no title.
        """
        if body is None:
            return None
        line = body.strip().split("\n", 1)[0].strip()
        if len(line) <= _TITLE_MAX:
            return line
        return f"{line[: _TITLE_MAX - 1]}…"

    def agent_comment(self, comment: ReviewCommentView) -> AgentComment:
        """Translate one review Comment, author facts included.

        # Parameters

        - `comment`: Comment as read from the review layer; its author view
          already carries the agent facts.
        """
        author = comment["author"]
        return AgentComment(
            comment_id=comment["comment_id"],
            author=AgentAuthor(
                profile_id=author["profile_id"],
                name=author["display_name"],
                is_agent=author["is_agent"],
                model=author["model"],
                harness=author["harness"],
            ),
            body=comment["body"],
            deleted=comment["deleted"],
            revision=comment["revision"],
            created_at=comment["created_at"],
            updated_at=comment["updated_at"],
        )

    def agent_location(
        self,
        view: ThreadDiscussionView | ThreadSummaryView,
    ) -> tuple[AgentLocation | None, AgentOutdatedReason | None]:
        """Translate a view's origin and placement to a location and reason.

        The File path and side come from the immutable origin. The lines are
        the placement's landed range when it kept or moved the region, and the
        origin's original range otherwise; a lost bay names the replacement
        bay. A review-scoped Thread has no location and is never outdated.

        # Returns

        - The repository location, or `None` for a review-scoped Thread.
        - The outdated reason, or `None` when the original lines are intact.

        # Failures

        - Raises `AssertionError` for a placement kind outside the union.
        """
        origin = view["origin_target"]
        placement = view["placement"]
        if origin["kind"] == "review":
            assert placement["kind"] == "review-scope"
            return None, None
        pair = origin["file"]
        path = (
            pair["left_path"]
            if origin["side"] == "left"
            else pair["right_path"]
        )
        assert path is not None, "origin side must name a present path"
        reason: AgentOutdatedReason | None
        match placement["kind"]:
            case "region-kept" | "whole-file":
                reason = None
            case "region-changed":
                reason = "region_changed"
            case "region-lost":
                reason = "region_not_found"
            case "bay-lost" | "side-lost":
                reason = "bay_not_found"
            case "file-absent":
                reason = "file_missing"
            case "file-unreadable":
                reason = "file_unreadable"
            case "review-scope":
                raise AssertionError("review-scope placement on a File origin")
        if origin["kind"] == "file-start":
            return (
                AgentLocation(path=path, side=origin["side"], bay_key=None),
                reason,
            )
        bay_key = origin["bay"]["bay_key"]
        lines = origin["range"]
        if placement["kind"] in ("region-kept", "region-changed"):
            lines = placement["range"]
        elif placement["kind"] == "bay-lost":
            bay_key = placement["bay"]["bay_key"]
        return (
            AgentLocation(
                path=path,
                side=origin["side"],
                bay_key=bay_key,
                start_line=lines["start_line"],
                end_line=lines["end_line"],
            ),
            reason,
        )

    def tab_descriptor(self, room: Room) -> AgentReviewTab:
        """Describe a repository Room as the Tab descriptor `capture` accepts.

        The descriptor is rebuilt from the Room's persisted correspondence: a
        Head Room yields its repository and original baseline, Refs its stored
        sides, Branch Review its stored branches, and a Pull Request its URL.

        # Failures

        - Raises `AssertionError` for a Preset Room, which has no repository and
          never reaches this boundary.
        """
        context = room.capture_context()
        selection = context["selection"]
        if isinstance(selection, PullRequestCaptureSelection):
            return AgentPullRequestTab(kind="pull-request", url=selection.url)
        mark_id = context["mark_id"]
        assert mark_id is not None
        mark = self.db.get(mark_id)
        assert mark is not None, "a Room's Mark must be registered"
        repo_path = str(mark.path)
        if isinstance(selection, HeadCaptureSelection):
            return AgentHeadTab(
                kind="head", repo_path=repo_path, baseline=selection.baseline
            )
        if isinstance(selection, RevisionsCaptureSelection):
            return AgentRefsTab(
                kind="refs",
                repo_path=repo_path,
                left=selection.left,
                right=selection.right,
            )
        assert isinstance(selection, BranchReviewCaptureSelection)
        branches: list[AgentBranch] = []
        for stored in (selection.base, selection.review):
            assert isinstance(stored, dict)
            name = stored["branch"]
            remote = stored.get("remote")
            assert isinstance(name, str)
            assert remote is None or isinstance(remote, str)
            branches.append(AgentBranch(remote=remote, name=name))
        return AgentBranchReviewTab(
            kind="branch-review",
            repo_path=repo_path,
            base=branches[0],
            review=branches[1],
        )

    def activity_response(
        self, mark_id: int, after: int, limit: int
    ) -> AgentActivityResponse:
        """Build self-contained events for authored actions after a cursor.

        # Parameters

        - `mark_id`: Repository whose Rooms are all included.
        - `after`: Exclusive activity cursor.
        - `limit`: Maximum events, at most 100.

        # Returns

        Oldest-first events with their review descriptor, Thread title and
        location, the action, and the Thread's status and waiting role right
        after it; the cursor for the next call; and whether more remain.
        """
        records = self.room_lord.mark_activity(mark_id, after, limit + 1)
        has_more = len(records) > limit
        records = records[:limit]
        histories, history_profiles = self.room_lord.thread_histories(
            {item.record.action.thread_id for item in records}
        )
        # Every event's action is part of its Thread's history, so its actor
        # is among the returned Profiles.
        authors = {
            profile.id: AgentAuthor(
                profile_id=profile.id,
                name=profile.username,
                is_agent=profile.agent is not None,
                model=None if profile.agent is None else profile.agent.model,
                harness=(
                    None if profile.agent is None else profile.agent.harness
                ),
            )
            for profile in history_profiles
        }
        descriptors: dict[bytes, AgentReviewTab] = {}
        events: list[AgentActivityEvent] = []
        for item in records:
            action = item.record.action
            history = histories[action.thread_id]
            key = history.identity.correspondence_key
            if key not in descriptors:
                descriptors[key] = self.tab_descriptor(
                    self.room_lord.room_for(history.identity)
                )
            payload = action.payload
            comment: AgentEventComment | None = None
            kind: AgentActionKind
            if isinstance(payload, CommentCreatedAction):
                kind = (
                    "create-thread"
                    if payload.kind == "thread-created"
                    else "comment"
                )
                comment = AgentEventComment(
                    comment_id=payload.comment.id,
                    author=authors[action.profile_id],
                    body=payload.comment.body,
                )
            elif isinstance(payload, CommentEditedAction):
                kind = "edit-comment"
                comment = AgentEventComment(
                    comment_id=payload.comment.id,
                    author=authors[action.profile_id],
                    body=payload.comment.body,
                )
            elif isinstance(payload, CommentDeletedAction):
                kind = "delete-comment"
                comment = AgentEventComment(
                    comment_id=payload.comment_id,
                    author=authors[action.profile_id],
                    body=None,
                )
            elif isinstance(payload, ThreadStateAction):
                kind = (
                    "resolve" if payload.kind == "thread-resolved" else "reopen"
                )
                if payload.comment is not None:
                    comment = AgentEventComment(
                        comment_id=payload.comment.id,
                        author=authors[action.profile_id],
                        body=payload.comment.body,
                    )
            else:
                assert isinstance(payload, ThreadDeletedAction)
                kind = "delete-thread"
            first = history.actions[0].action.payload
            assert isinstance(first, CommentCreatedAction)
            events.append(
                AgentActivityEvent(
                    activity_id=item.record.activity_id,
                    created_at=datetime.fromisoformat(action.created_at),
                    tab=descriptors[key],
                    thread_id=action.thread_id,
                    title=self.agent_title(first.comment.body),
                    location=self.origin_location(history),
                    action=kind,
                    author=authors[action.profile_id],
                    comment=comment,
                    status=action.status_after,
                    waiting_on=action.attention_after,
                )
            )
        return AgentActivityResponse(
            events=events,
            next_after=events[-1].activity_id if events else after,
            has_more=has_more,
        )

    def origin_location(
        self, history: ReviewThreadHistory
    ) -> AgentLocation | None:
        """Describe a Thread's creation target from its raw history.

        # Returns

        - The location of the origin File on the origin side; a historical
          File-level Thread has no bay or lines.
        - `None`: the Thread is review-scoped and has no location.

        # Failures

        - Raises `AssertionError` when the origin placement is not a range,
          File-start, or review-scope placement.
        """
        placement = history.origin.placement
        if isinstance(placement, ReviewScopePlacement):
            return None
        assert isinstance(placement, (RangePlacement, FileStartPlacement))
        path = (
            history.origin_left_path
            if placement.side == "left"
            else history.origin_right_path
        )
        assert path is not None, "origin side must name a present path"
        if isinstance(placement, FileStartPlacement):
            return AgentLocation(path=path, side=placement.side, bay_key=None)
        return AgentLocation(
            path=path,
            side=placement.side,
            bay_key=placement.bay_key,
            start_line=placement.start_line,
            end_line=placement.end_line,
        )

    @routes.get(
        "/api/agent/onboard",
        response_model=AgentOnboardResponse,
    )
    def onboard_agent(
        self,
        request: Request,
        tab: Literal["head", "refs", "branch-review", "pull-request"],
        project_id: int | None = Query(default=None, ge=1),
        left: str | None = Query(default=None, min_length=1),
        right: str | None = Query(default=None, min_length=1),
        base_source: Literal["local", "remote"] | None = None,
        base_remote: str | None = Query(default=None, min_length=1),
        base_branch: str | None = Query(default=None, min_length=1),
        review_source: Literal["local", "remote"] | None = None,
        review_remote: str | None = Query(default=None, min_length=1),
        review_branch: str | None = Query(default=None, min_length=1),
        pull_request_url: str | None = Query(default=None, min_length=1),
    ) -> AgentOnboardResponse | PlainTextResponse:
        """Describe one copied supported Tab and its installed agent skills.

        The HUD constructs this URL only from a complete selected Head, Refs,
        Branch Review, or Pull Request Tab. Repository ids are translated through
        the active Mark registry; the response returns the Tab descriptor `capture` accepts, the server origin, and exact installed skill entry paths.

        # Parameters

        - `request`: Incoming HTTP entity whose origin becomes `dirdiff_url`.
        - `tab`: Supported selected HUD Tab discriminating every other parameter.
        - `project_id`: Active Mark identity required by repository-backed Tabs.
        - `left`: Exact old side required only by Refs.
        - `right`: Exact new side required only by Refs.
        - `base_source`: Local or remote namespace required by Branch Review.
        - `base_remote`: Remote name required only for a remote base branch.
        - `base_branch`: Exact symbolic base branch required by Branch Review.
        - `review_source`: Local or remote namespace required by Branch Review.
        - `review_remote`: Remote name required only for a remote review branch.
        - `review_branch`: Exact symbolic review branch required by Branch Review.
        - `pull_request_url`: Complete Pull Request URL required only by that Tab.

        # Returns

        - `AgentOnboardResponse` for one exact supported parameter variant.
        - `PlainTextResponse` with the agent error contract when parameters do not
          describe that variant or the selected Mark is inactive.

        # Failures

        FastAPI rejects malformed scalar values. The handler rejects missing,
        repeated, extraneous, or cross-variant parameters and inactive Marks.
        """
        values: dict[str, str | int] = {
            name: value
            for name, value in (
                ("project_id", project_id),
                ("left", left),
                ("right", right),
                ("base_source", base_source),
                ("base_remote", base_remote),
                ("base_branch", base_branch),
                ("review_source", review_source),
                ("review_remote", review_remote),
                ("review_branch", review_branch),
                ("pull_request_url", pull_request_url),
            )
            if value is not None
        }

        def require_parameters(required: set[str]) -> None:
            """Reject parameters outside one exact discriminated Tab variant."""

            supplied_query = request.query_params.multi_items()
            supplied_names = {name for name, _value in supplied_query}
            expected_names = {"tab", *required}
            if (
                set(values) != required
                or supplied_names != expected_names
                or len(supplied_query) != len(expected_names)
            ):
                required_text = ", ".join(sorted(expected_names))
                raise DirdiffError(
                    f"{tab} onboarding requires exactly: {required_text}."
                )

        try:
            onboard_tab: AgentReviewTab
            if tab == "pull-request":
                require_parameters({"pull_request_url"})
                assert pull_request_url is not None
                onboard_tab = AgentPullRequestTab(
                    kind="pull-request", url=pull_request_url
                )
            else:
                if tab == "head":
                    require_parameters({"project_id"})
                elif tab == "refs":
                    require_parameters({"project_id", "left", "right"})
                else:
                    required = {
                        "project_id",
                        "base_source",
                        "base_branch",
                        "review_source",
                        "review_branch",
                    }
                    if base_source == "remote":
                        required.add("base_remote")
                    if review_source == "remote":
                        required.add("review_remote")
                    require_parameters(required)

                assert project_id is not None
                mark = self.db.get(project_id)
                if mark is None:
                    raise DirdiffError(
                        f"Unknown active repository Mark: {project_id}."
                    )
                repo_path = str(mark.path)
                if tab == "head":
                    # The browser's Head selection names no baseline; it means
                    # the current HEAD, so the descriptor freezes that commit.
                    onboard_tab = AgentHeadTab(
                        kind="head",
                        repo_path=repo_path,
                        baseline=GitRepository.discover(
                            repo_root=Path(repo_path)
                        ).commit_id("HEAD", allow_unborn=True),
                    )
                elif tab == "refs":
                    assert left is not None and right is not None
                    onboard_tab = AgentRefsTab(
                        kind="refs",
                        repo_path=repo_path,
                        left=left,
                        right=right,
                    )
                else:
                    assert base_source is not None
                    assert base_branch is not None
                    assert review_source is not None
                    assert review_branch is not None
                    onboard_tab = AgentBranchReviewTab(
                        kind="branch-review",
                        repo_path=repo_path,
                        base=AgentBranch(remote=base_remote, name=base_branch),
                        review=AgentBranch(
                            remote=review_remote, name=review_branch
                        ),
                    )
            return AgentOnboardResponse(
                dirdiff_url=str(request.base_url).rstrip("/"),
                project_id=project_id,
                tab=onboard_tab,
                skill_paths=list(self.agent_skill_paths),
                next_step=(
                    "Run: dirdiff agent init --session PATH --onboard-url "
                    f"'{request.url}' --name NAME --role author|reviewer "
                    "--model MODEL --harness HARNESS\n"
                    "Then read the skill in skill_paths for your role: "
                    "review-patch to review, round-review to answer a human "
                    "reviewer's Threads as author, babysit-patch to coordinate "
                    "an agent reviewer as author.\n"
                    "The `dirdiff agent` CLI is the supported way to use this "
                    "API; do not learn the API from openapi.json."
                ),
            )
        except DirdiffError as exc:
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

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

    @routes.post("/api/agent/profile", response_model=AgentProfileResponse)
    def agent_profile(
        self, request: AgentProfileRequest
    ) -> AgentProfileResponse | PlainTextResponse:
        """Register an agent Profile, or update the one bound to its UUID.

        Idempotent on `agent_uuid`: a repeat call returns the same Profile id
        and replaces its name, model, and harness with the supplied values.

        # Returns

        - `AgentProfileResponse` with the Profile id to send with actions.
        - `PlainTextResponse` (400) when the name belongs to a different
          Profile or is invalid; nothing is written.
        """
        try:
            record = self.user_profile_store.upsert_agent(
                request.name,
                request.agent_uuid,
                request.model,
                request.harness,
            )
        except ValueError as exc:
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))
        return AgentProfileResponse(
            profile_id=record.id,
            name=record.username,
            model=request.model,
            harness=request.harness,
        )

    @routes.post("/api/agent/capture", response_model=AgentCaptureResponse)
    def agent_capture(
        self, request: AgentCaptureRequest
    ) -> AgentCaptureResponse | PlainTextResponse:
        """Capture a Tab as the browser manifest would and list its Files.

        The Tab is resolved by the same selection code as the browser, so the
        same descriptor lands in the same Room. Capturing is repeatable: an
        unchanged tree yields the same Snapshot. Nothing is written into the
        Snapshot directory.

        # Returns

        - `AgentCaptureResponse` with the Snapshot id and directory, the Room's
          Tab descriptor, the repository's newest activity id, and every File
          pair with its change kind, capture error, and absolute captured side
          paths.
        - `PlainTextResponse` (400) for an unknown repository, unsupported or
          invalid Tab input, a Pull Request that cannot be prepared, or a
          capture failure.
        """
        try:
            tab = request.tab
            if isinstance(tab, AgentPullRequestTab):
                prepared = prepare_pull_request(
                    url=tab.url, repo_marks=self.db.list()
                )
                selection: ManifestSelection | HeadBaselineManifestSelection = (
                    PullRequestManifestSelection(
                        tab="pull-request",
                        project_id=prepared.project_id,
                        url=prepared.pull_request_url,
                        left_commit=prepared.left_commit,
                        right_commit=prepared.right_commit,
                    )
                )
            else:
                matches = [
                    mark
                    for mark in self.db.list()
                    if mark.path == tab.repo_path
                ]
                if len(matches) != 1:
                    raise DirdiffError("Tab path does not identify one Mark.")
                project_id = matches[0].id

                def branch(value: AgentBranch) -> BranchSelection:
                    """Translate one explicit API branch to repository input.

                    A missing remote produces the local branch shape; a concrete
                    remote produces the remote shape with the same symbolic
                    name. Nothing is resolved here.
                    """
                    if value.remote is None:
                        return {"source": "local", "branch": value.name}
                    return {
                        "source": "remote",
                        "remote": value.remote,
                        "branch": value.name,
                    }

                try:
                    if isinstance(tab, AgentHeadTab):
                        selection = HeadBaselineManifestSelection(
                            tab="head",
                            project_id=project_id,
                            baseline=tab.baseline,
                            show_untracked=True,
                        )
                    elif isinstance(tab, AgentRefsTab):
                        selection = RefsManifestSelection(
                            tab="refs",
                            project_id=project_id,
                            left=tab.left,
                            right=tab.right,
                            show_untracked=False,
                        )
                    else:
                        assert isinstance(tab, AgentBranchReviewTab)
                        selection = BranchReviewManifestSelection(
                            tab="branch-review",
                            project_id=project_id,
                            base=branch(tab.base),
                            review=branch(tab.review),
                        )
                except ValidationError as exc:
                    # Invalid names rejected by the shared source-selection
                    # boundary are expected input failures.
                    raise DirdiffError(str(exc)) from exc
            prepared_capture = selection.prepare(
                self.db,
                self.presets_root,
                store_path=self.store_path,
            )
            room, snapshot_id = self.room_lord.corresponding_room(
                mark_id=prepared_capture.mark_id,
                selection=prepared_capture.correspondence,
                source=prepared_capture.source,
                left_label=prepared_capture.left_label,
                right_label=prepared_capture.right_label,
            )
            manifest = [
                (left.map(Path.as_posix), right.map(Path.as_posix), meta)
                for left, right, meta in room.manifested(snapshot_id)
            ]
            # One read validates and stat-checks every captured side path.
            captured = room.captured_files_for_pairs(
                snapshot_id,
                tuple((left, right) for left, right, _meta in manifest),
            )
            files: list[AgentManifestFile] = []
            for left, right, meta in manifest:
                left_file, right_file = captured[(left, right)]
                files.append(
                    AgentManifestFile(
                        left_path=left.get(),
                        right_path=right.get(),
                        change_type=meta["change_type"],
                        capture_error=meta["capture_error"],
                        left_file=left_file.map(str).get(),
                        right_file=right_file.map(str).get(),
                    )
                )
            mark_id = prepared_capture.mark_id
            assert mark_id is not None
            return AgentCaptureResponse(
                project_id=mark_id,
                snapshot_id=snapshot_id.hex,
                tab=self.tab_descriptor(room),
                snapshot_path=str(room.path_for_snapshot(snapshot_id)),
                latest_activity_id=self.room_lord.mark_latest_activity_id(
                    mark_id
                ),
                files=files,
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent capture request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/agent/reviews", response_model=AgentReviewsResponse)
    def agent_reviews(
        self, project_id: int = Query(ge=1)
    ) -> AgentReviewsResponse | PlainTextResponse:
        """List the Rooms of one repository with their open-Thread load.

        # Returns

        - `AgentReviewsResponse` with one entry per Room that has a Snapshot:
          its Tab descriptor, newest Snapshot id, and open Thread counts.
        - `PlainTextResponse` (400) for an unknown repository.
        """
        try:
            if self.db.get(project_id) is None:
                raise DirdiffError(f"Unknown repository Mark: {project_id}.")
            latest = self.room_lord.mark_latest_activity_id(project_id)
            reviews: list[AgentReview] = []
            for room, snapshot_id in self.room_lord.mark_rooms(project_id):
                counts = room.review_attention_counts(snapshot_id, latest)
                reviews.append(
                    AgentReview(
                        tab=self.tab_descriptor(room),
                        snapshot_id=snapshot_id.hex,
                        open_threads=sum(counts.values()),
                        waiting_on_author=counts["author"] + counts["both"],
                        waiting_on_reviewer=counts["reviewer"] + counts["both"],
                    )
                )
            return AgentReviewsResponse(reviews=reviews)
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent reviews request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/agent/threads", response_model=AgentThreadPage)
    def agent_threads(
        self,
        snapshot_id: UUID,
        status: Literal["open", "all"],
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=_PAGE_LIMIT_MAX),
        through_activity_id: int | None = Query(default=None, ge=0),
    ) -> AgentThreadPage | PlainTextResponse:
        """List the Threads of the Room containing a Snapshot.

        The scope is the whole Room, as in the browser History: `status=all`
        includes resolved and deleted Threads, and any Snapshot id of the Room
        works, so an older id still lists every Thread.

        # Parameters

        - `snapshot_id`: Any Snapshot of the Room.
        - `status`: `open` or `all`.
        - `page`: One-based page number.
        - `limit`: Page size, at most 100.
        - `through_activity_id`: Absent on page one, where the server picks the
          Room's greatest current activity; every later page repeats the value
          the response returned, so membership, order, and total stay fixed.

        Locations are read
        against the given Snapshot.

        # Returns

        - `AgentThreadPage` of summaries, with the concrete
          `through_activity_id` and each Thread's title, status, waiting role,
          location, outdated reason, Comment count, previews, and newest
          activity id.
        - `PlainTextResponse` (400) for an unknown Snapshot, a first page that
          sends a pivot, or a later page that omits it.
        """
        try:
            room = self.room_lord.find_room(snapshot_id)
            if page == 1 and through_activity_id is not None:
                raise DirdiffError("First page chooses its activity pivot.")
            if page > 1 and through_activity_id is None:
                raise DirdiffError(
                    "Later pages require the first page's through_activity_id."
                )
            threads, total, through_activity_id = room.threads(
                snapshot_id,
                page=page,
                limit=limit,
                state=status,
                scope="room",
                through_activity_id=through_activity_id,
            )
            views = [thread.summary() for thread in threads]
            latest = self.room_lord.thread_latest_activity(
                {view["thread_id"] for view in views}, through_activity_id
            )
            summaries: list[AgentThreadSummary] = []
            for view in views:
                location, reason = self.agent_location(view)
                first = view["first_comment"]
                last = view["latest_comment"]
                summaries.append(
                    AgentThreadSummary(
                        thread_id=view["thread_id"],
                        status=view["state"],
                        waiting_on=view["attention"],
                        title=self.agent_title(first["body"]),
                        location=location,
                        outdated_reason=reason,
                        comment_count=view["comment_count"],
                        first_comment=self.agent_preview(
                            first["body"], first["deleted"]
                        ),
                        latest_comment=self.agent_preview(
                            last["body"], last["deleted"]
                        ),
                        latest_activity_id=latest[view["thread_id"]],
                    )
                )
            return AgentThreadPage(
                through_activity_id=through_activity_id,
                items=summaries,
                page=page,
                limit=limit,
                total=total,
                has_more=page * limit < total,
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent threads request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/agent/thread/{thread_id}", response_model=AgentThread)
    def agent_thread(
        self,
        thread_id: UUID,
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=_PAGE_LIMIT_MAX),
    ) -> AgentThread | PlainTextResponse:
        """Return one Thread by id alone, with a page of its Comments.

        Resolved Threads are readable. Deleted Threads stay readable too, with
        every Comment tombstoned as recorded.

        # Parameters

        - `thread_id`: Thread to read.
        - `page`: One-based Comment page number.
        - `limit`: Comments per page, at most 100.

        # Returns

        - `AgentThread` with its creation location, original excerpt, status,
          waiting role, and the requested Comment page.
        - `PlainTextResponse` (400) for an unknown Thread.
        """
        try:
            room, origin_snapshot = self.room_lord.find_thread_room(thread_id)
            view = room.get_thread(origin_snapshot, thread_id).discussion()
            location, _reason = self.agent_location(view)
            origin = view["origin_target"]
            excerpt = (
                origin["excerpt"]
                if origin["kind"] == "text" and "excerpt" in origin
                else None
            )
            start = (page - 1) * limit
            total = len(view["comments"])
            return AgentThread(
                thread_id=view["thread_id"],
                snapshot_id=view["snapshot_id"],
                status=view["state"],
                waiting_on=view["attention"],
                location=location,
                original_excerpt=(
                    ReviewExcerptResponse.model_validate(excerpt)
                    if excerpt is not None
                    else None
                ),
                comments=[
                    self.agent_comment(comment)
                    for comment in view["comments"][start : start + limit]
                ],
                page=page,
                limit=limit,
                total_comments=total,
                has_more=page * limit < total,
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent thread request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/agent/activity", response_model=AgentActivityResponse)
    def agent_activity(
        self,
        project_id: int = Query(ge=1),
        after: int = Query(ge=0),
        limit: int = Query(default=50, ge=1, le=_PAGE_LIMIT_MAX),
    ) -> AgentActivityResponse | PlainTextResponse:
        """Return authored actions newer than a cursor, across the repository.

        Every Room of the repository is included, ordered by increasing
        activity id. Each event is self-contained; send `next_after` as `after`
        in the next call.

        # Parameters

        - `project_id`: Repository (Mark) id.
        - `after`: Exclusive activity id cursor.
        - `limit`: Maximum events, at most 100.

        # Returns

        - `AgentActivityResponse`, empty when nothing is newer than `after`.
        - `PlainTextResponse` (400) for an unknown repository.
        """
        try:
            if self.db.get(project_id) is None:
                raise DirdiffError(f"Unknown repository Mark: {project_id}.")
            return self.activity_response(project_id, after, limit)
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent activity request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.get("/api/agent/wait", response_model=AgentActivityResponse)
    async def agent_wait(
        self,
        project_id: int = Query(ge=1),
        after: int = Query(ge=0),
        timeout: int = Query(ge=1, le=_WAIT_TIMEOUT_MAX),
        limit: int = Query(default=50, ge=1, le=_PAGE_LIMIT_MAX),
    ) -> AgentActivityResponse | PlainTextResponse:
        """Hold until an action newer than a cursor exists, then return it.

        Same response as `activity`. When nothing newer than `after` exists the
        call waits, up to `timeout` seconds (1 to 540), and returns an empty
        event list if the time runs out. The wait polls the newest activity id
        with `asyncio.sleep`, so it holds no worker thread.

        # Parameters

        - `project_id`: Repository (Mark) id.
        - `after`: Exclusive activity id cursor.
        - `timeout`: Seconds to wait, 1 to 540.
        - `limit`: Maximum events, at most 100.

        # Returns

        - `AgentActivityResponse`: events as soon as one exists, or empty after
          `timeout`.
        - `PlainTextResponse` (400) for an unknown repository.
        """
        try:
            if self.db.get(project_id) is None:
                raise DirdiffError(f"Unknown repository Mark: {project_id}.")
            deadline = time.monotonic() + timeout
            while self.room_lord.mark_latest_activity_id(project_id) <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(_WAIT_POLL_SECONDS, remaining))
            return self.activity_response(project_id, after, limit)
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent wait request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))

    @routes.post("/api/agent/actions", response_model=AgentActionsResponse)
    def agent_actions(
        self, request: AgentActionsRequest
    ) -> AgentActionsResponse | PlainTextResponse:
        """Validate and atomically append an ordered batch of actions.

        All items are attributed to `profile_id` and must belong to one Room.
        Each item is validated against the state earlier items produced; any
        failure rejects the whole batch and writes nothing. Any Profile may act
        on any Thread; only a Comment's author edits it, and a deleted Thread
        rejects everything.

        # Returns

        - `AgentActionsResponse` with one result per item, in order.
        - `PlainTextResponse` (400) for an unknown Profile, Thread, Comment,
          Snapshot, or path, an invalid range, an invalid transition, items
          from different Rooms, or any other rejected item.
        """
        try:
            author = ProfileAuthor(request.profile_id)
            first = request.actions[0]
            room = (
                self.room_lord.find_room(UUID(hex=first.snapshot_id))
                if isinstance(first, AgentCreateAction)
                else self.room_lord.find_thread_room(UUID(hex=first.thread_id))[
                    0
                ]
            )
            # Repository path to File pair, read once per Snapshot that a
            # code-targeted creation names.
            pairs: dict[str, dict[tuple[str, str], FilePair]] = {}
            batch: list[ReviewBatchAction] = []
            for action in request.actions:
                if isinstance(action, AgentCreateAction):
                    snapshot_id = UUID(hex=action.snapshot_id)
                    if action.path is None:
                        assert action.side is None and action.bay is None
                        batch.append(
                            BatchCreate(
                                snapshot_id,
                                CreateReviewThread(
                                    thread_id=uuid4(),
                                    operation_id=uuid4(),
                                    comment_id=uuid4(),
                                    author=author,
                                    body=action.body,
                                ),
                                action.role,
                            )
                        )
                        continue
                    assert action.side is not None and action.bay is not None
                    if action.snapshot_id not in pairs:
                        by_path: dict[tuple[str, str], FilePair] = {}
                        for left, right, _meta in room.manifested(snapshot_id):
                            pair = FilePair(
                                left.map(Path.as_posix),
                                right.map(Path.as_posix),
                            )
                            if isinstance(left, Some):
                                by_path[("left", left.get().as_posix())] = pair
                            if isinstance(right, Some):
                                by_path[("right", right.get().as_posix())] = (
                                    pair
                                )
                        pairs[action.snapshot_id] = by_path
                    found = pairs[action.snapshot_id].get(
                        (action.side, action.path)
                    )
                    if found is None:
                        raise DirdiffError(
                            f"No {action.side} File {action.path!r} in the "
                            "Snapshot."
                        )
                    batch.append(
                        BatchCreate(
                            snapshot_id,
                            CreateThread(
                                thread_id=uuid4(),
                                operation_id=uuid4(),
                                comment_id=uuid4(),
                                author=author,
                                target=TextTarget(
                                    found,
                                    action.bay.bay_key,
                                    action.side,
                                    LineRange(
                                        action.bay.start_line,
                                        action.bay.end_line,
                                    ),
                                ),
                                body=action.body,
                            ),
                            action.role,
                        )
                    )
                    continue
                thread_id = UUID(hex=action.thread_id)
                if isinstance(action, AgentCommentAction):
                    batch.append(
                        BatchComment(
                            thread_id,
                            AddComment(
                                operation_id=uuid4(),
                                comment_id=uuid4(),
                                author=author,
                                body=action.body,
                            ),
                            action.role,
                        )
                    )
                elif isinstance(
                    action, (AgentResolveAction, AgentReopenAction)
                ):
                    change = ChangeThreadState(
                        uuid4(),
                        author,
                        uuid4() if action.body is not None else None,
                        action.body,
                    )
                    if isinstance(action, AgentResolveAction):
                        batch.append(BatchResolve(thread_id, change))
                    else:
                        batch.append(
                            BatchReopen(thread_id, change, action.role)
                        )
                elif isinstance(action, AgentEditCommentAction):
                    batch.append(
                        BatchEditComment(
                            thread_id,
                            UUID(hex=action.comment_id),
                            EditComment(uuid4(), author, action.body),
                        )
                    )
                elif isinstance(action, AgentDeleteCommentAction):
                    batch.append(
                        BatchDeleteComment(
                            thread_id,
                            UUID(hex=action.comment_id),
                            DeleteComment(uuid4(), author),
                        )
                    )
                else:
                    assert isinstance(action, AgentDeleteThreadAction)
                    batch.append(
                        BatchDeleteThread(
                            thread_id,
                            ChangeThreadState(uuid4(), author, None, None),
                        )
                    )
            results = room.apply_review_batch(tuple(batch))
            return AgentActionsResponse(
                results=[
                    AgentActionResult(
                        kind=action.kind,
                        thread_id=result.thread_id.hex,
                        comment_id=(
                            result.comment_id.hex
                            if result.comment_id is not None
                            else None
                        ),
                        activity_id=result.activity_id,
                        status=result.status,
                        waiting_on=result.attention,
                    )
                    for action, result in zip(
                        request.actions, results, strict=True
                    )
                ]
            )
        except (DirdiffError, ReviewError) as exc:
            LOGGER.exception("Agent actions request failed")
            return self.agent_failure(HTTPStatus.BAD_REQUEST, str(exc))
