"""Expose the HTTP boundary used by separately running agents.

ExternalAgentRoutes keeps the agent wire models, pagination and placement
conversion, plain-text failure contract, and /api/agent/* handlers together.
It translates between external agent entities and the existing Room review
operations.

Instances retain only constructor-injected application interfaces and immutable
resource configuration needed by those handlers. This module does not implement
an integrated agent, own review state, render the HUD, or construct the FastAPI
application.
"""

import logging
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
    field_validator,
    model_validator,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from dirdiff.backend import (
    BranchSelection,
    GitBackend,
    prepare_pull_request,
)
from dirdiff.db import (
    RepoMarkStore,
    UserProfileStore,
)
from dirdiff.engines import (
    DirdiffError,
)
from dirdiff.review import (
    AddComment,
    ChangeThreadState,
    CreateThread,
    DeleteThread,
    FilePair,
    LineRange,
    ProfileAuthor,
    ReplyToThread,
    ResolveThread,
    ReviewError,
    ReviewOriginView,
    TextTarget,
    ThreadDiscussionView,
    ThreadPlacementView,
    ThreadSummaryView,
)
from dirdiff.room_lord import (
    BranchReviewCaptureSelection,
    CaptureSelection,
    PullRequestCaptureSelection,
    RevisionsCaptureSelection,
    Room,
    RoomLord,
)
from dirdiff.server.base import (
    ApiModel,
    ReviewExcerptResponse,
    capture_snapshot,
)
from dirdiff.server.magic import ClassRoutes

__all__ = ["ExternalAgentRoutes"]

LOGGER = logging.getLogger(__name__)
"""Record unexpected failures at this HTTP boundary."""

_AGENT_ROUTE_PATHS = frozenset(
    {
        "/api/agent/onboard",
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


class AgentOnboardResponse(ApiModel):
    """Return everything needed to enter one supported agent workflow.

    The copied onboarding URL selects the running dirdiff server and one complete
    HUD Tab. This response translates that selection to the existing join-review
    shape and publishes the exact installed instruction files for the three agent
    roles. It creates no Profile, Room, Snapshot, or review activity.
    """

    dirdiff_url: str = Field(min_length=1)
    """Absolute origin of the dirdiff server that returned this response.

    Agent command references use this value as `DD_URL`. It contains no endpoint
    path, Tab parameters, credentials, or browser-only state.
    """

    tab: AgentReviewTab
    """Complete join-review Tab reconstructed from the copied HUD selection.

    Repository-backed variants contain the marked repository's exact path rather
    than its browser-only numeric id. The value can be copied unchanged into a
    `join_review` body.
    """

    skill_paths: list[str] = Field(min_length=3, max_length=3)
    """Absolute paths to the review, round, and babysit skill entry files.

    The order is `review-patch`, `round-review`, then `babysit-patch`. Every path
    names a validated regular `SKILL.md` in this running installation.
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


class ExternalAgentRoutes:
    """Bind external-agent handlers to their application interfaces.

    One instance retains the repository registry, Profile store, Room service,
    preset root, and validated installed skill paths used by external agent HTTP
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
        presets_root: str | None,
    ) -> None:
        """Retain the exact interfaces used by external-agent handlers.

        # Parameters

        - `db`: Repository registry used to open and continue agent reviews.
        - `user_profile_store`: Profile and agent binding persistence.
        - `agent_skills_root`: Directory containing the three installed agent
          workflow skills exposed by onboarding.
        - `room_lord`: Room selection and Snapshot lookup interface.
        - `presets_root`: Optional preset catalog root used by review opening.

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

    def snapshot_room(self, snapshot_id: UUID) -> Room:
        """Return the Room containing one exact agent-selected Snapshot.

        Agent routes call this boundary before any placement or action read. It
        preserves the requested opaque identity and converts a missing retained
        Snapshot into the concrete diagnostic expected by their plain-text
        failure path.

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
        the active Mark registry; the response returns the existing join-review
        Tab vocabulary, the server origin, and exact installed skill entry paths.

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
                    onboard_tab = AgentHeadTab(kind="head", repo_path=repo_path)
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
                tab=onboard_tab,
                skill_paths=list(self.agent_skill_paths),
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
                room, snapshot_id, _ = capture_snapshot(
                    self.db,
                    self.room_lord,
                    self.presets_root,
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
                room, snapshot_id, _ = capture_snapshot(
                    self.db,
                    self.room_lord,
                    self.presets_root,
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
