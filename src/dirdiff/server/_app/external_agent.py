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

_PAGE_LIMIT_MAX = 100

_WAIT_TIMEOUT_MAX = 540

_WAIT_POLL_SECONDS = 0.25

_TITLE_MAX = 120


class AgentBranch(ApiModel):
    remote: str | None

    name: str = Field(min_length=1)


class AgentHeadTab(ApiModel):
    kind: Literal["head"]

    repo_path: str = Field(min_length=1)

    baseline: str = Field(min_length=1)


class AgentRefsTab(ApiModel):
    kind: Literal["refs"]

    repo_path: str = Field(min_length=1)

    left: str = Field(min_length=1)

    right: str = Field(min_length=1)


class AgentBranchReviewTab(ApiModel):
    kind: Literal["branch-review"]

    repo_path: str = Field(min_length=1)

    base: AgentBranch

    review: AgentBranch


class AgentPullRequestTab(ApiModel):
    kind: Literal["pull-request"]

    url: str = Field(min_length=1)


AgentReviewTab = Annotated[
    AgentHeadTab | AgentRefsTab | AgentBranchReviewTab | AgentPullRequestTab,
    Field(discriminator="kind"),
]


class AgentOnboardResponse(ApiModel):
    dirdiff_url: str = Field(min_length=1)

    project_id: int | None = Field(default=None, ge=1)

    tab: AgentReviewTab

    skill_paths: list[str] = Field(min_length=3, max_length=3)

    next_step: str = Field(min_length=1)


class AgentProfileRequest(ApiModel):
    agent_uuid: str = Field(pattern=r"^[0-9a-f]{32}$")

    name: str = Field(min_length=1)

    model: str = Field(min_length=1)

    harness: str = Field(min_length=1)

    @field_validator("agent_uuid", mode="before")
    @classmethod
    def normalize_agent_uuid(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str) and len(value) == 36:
            try:
                return UUID(value).hex
            except ValueError:
                return value
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value != value.strip() or value.strip() == "":
            raise ValueError("Invalid agent name.")
        return value

    @field_validator("model", "harness")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("Value must not be blank.")
        return value


class AgentProfileResponse(ApiModel):
    profile_id: int = Field(gt=0)

    name: str

    model: str

    harness: str


class AgentCaptureRequest(ApiModel):
    tab: AgentReviewTab


class AgentManifestFile(ApiModel):
    left_path: str | None

    right_path: str | None

    change_type: Literal["modify", "add", "delete", "rename", "copy"]

    capture_error: str | None

    left_file: str | None

    right_file: str | None


class AgentCaptureResponse(ApiModel):
    project_id: int = Field(gt=0)

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    tab: AgentReviewTab

    snapshot_path: str = Field(min_length=1)

    latest_activity_id: int = Field(ge=0)

    files: list[AgentManifestFile]


class AgentAuthor(ApiModel):
    profile_id: int = Field(gt=0)

    name: str

    is_agent: bool

    model: str | None

    harness: str | None


class AgentComment(ApiModel):
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    author: AgentAuthor

    body: str | None

    deleted: bool

    revision: int = Field(ge=0)

    created_at: datetime

    updated_at: datetime


class AgentCommentPreview(ApiModel):
    body: str | None

    deleted: bool

    truncated: bool


class AgentBayRange(ApiModel):
    bay_key: str = Field(min_length=1)

    start_line: int = Field(ge=1)

    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        LineRange(self.start_line, self.end_line)
        return self


class AgentLocation(ApiModel):
    path: str

    side: Literal["left", "right"]

    bay_key: str | None

    start_line: int | None = Field(default=None, ge=1)

    end_line: int | None = Field(default=None, ge=1)


AgentOutdatedReason = Literal[
    "region_changed",
    "region_not_found",
    "bay_not_found",
    "file_missing",
    "file_unreadable",
]

AgentStatus = Literal["open", "resolved", "deleted"]

AgentWaiting = Literal["author", "reviewer", "both", "none"]


class AgentThreadSummary(ApiModel):
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    status: AgentStatus

    waiting_on: AgentWaiting

    title: str | None

    location: AgentLocation | None

    outdated_reason: AgentOutdatedReason | None

    comment_count: int = Field(ge=1)

    first_comment: AgentCommentPreview

    latest_comment: AgentCommentPreview

    latest_activity_id: int = Field(ge=1)


class AgentPage[AgentPageItem](ApiModel):
    items: list[AgentPageItem]

    page: int = Field(ge=1)

    limit: int = Field(ge=1)

    total: int = Field(ge=0)

    has_more: bool


class AgentThreadPage(AgentPage[AgentThreadSummary]):
    through_activity_id: int = Field(ge=0)


class AgentThread(ApiModel):
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    status: AgentStatus

    waiting_on: AgentWaiting

    location: AgentLocation | None

    original_excerpt: ReviewExcerptResponse | None

    comments: list[AgentComment]

    page: int = Field(ge=1)

    limit: int = Field(ge=1)

    total_comments: int = Field(ge=1)

    has_more: bool


class AgentReview(ApiModel):
    tab: AgentReviewTab

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    open_threads: int = Field(ge=0)

    waiting_on_author: int = Field(ge=0)

    waiting_on_reviewer: int = Field(ge=0)


class AgentReviewsResponse(ApiModel):
    reviews: list[AgentReview]


class AgentEventComment(ApiModel):
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    author: AgentAuthor

    body: str | None


AgentActionKind = Literal[
    "create-thread",
    "comment",
    "resolve",
    "reopen",
    "edit-comment",
    "delete-comment",
    "delete-thread",
]


class AgentActivityEvent(ApiModel):
    activity_id: int = Field(ge=1)

    created_at: datetime

    tab: AgentReviewTab

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    title: str | None

    location: AgentLocation | None

    action: AgentActionKind

    author: AgentAuthor

    comment: AgentEventComment | None

    status: AgentStatus

    waiting_on: AgentWaiting


class AgentActivityResponse(ApiModel):
    events: list[AgentActivityEvent]

    next_after: int = Field(ge=0)

    has_more: bool


class AgentCreateAction(ApiModel):
    kind: Literal["create-thread"]

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    role: ReviewRole

    body: str

    path: str | None = Field(default=None, min_length=1)

    side: Literal["left", "right"] | None = None

    bay: AgentBayRange | None = None

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        present = [
            value is not None for value in (self.path, self.side, self.bay)
        ]
        if any(present) and not all(present):
            raise ValueError("path, side, and bay must be sent together.")
        return self


class AgentCommentAction(ApiModel):
    kind: Literal["comment"]

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    role: ReviewRole

    body: str


class AgentResolveAction(ApiModel):
    kind: Literal["resolve"]

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    body: str | None = None


class AgentReopenAction(ApiModel):
    kind: Literal["reopen"]

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    role: ReviewRole

    body: str | None = None


class AgentEditCommentAction(ApiModel):
    kind: Literal["edit-comment"]

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    body: str


class AgentDeleteCommentAction(ApiModel):
    kind: Literal["delete-comment"]

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class AgentDeleteThreadAction(ApiModel):
    kind: Literal["delete-thread"]

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")


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


class AgentActionsRequest(ApiModel):
    profile_id: int = Field(gt=0)

    actions: list[AgentAction] = Field(min_length=1, max_length=_PAGE_LIMIT_MAX)


class AgentActionResult(ApiModel):
    kind: AgentActionKind

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    comment_id: str | None

    activity_id: int = Field(ge=1)

    status: AgentStatus

    waiting_on: AgentWaiting


class AgentActionsResponse(ApiModel):
    results: list[AgentActionResult]


class ExternalAgentRoutes:
    routes = ClassRoutes()

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
        return PlainTextResponse(detail, status_code=status)

    def agent_preview(
        self, body: str | None, deleted: bool
    ) -> AgentCommentPreview:
        if body is None or len(body) <= 256:
            return AgentCommentPreview(
                body=body, deleted=deleted, truncated=False
            )
        return AgentCommentPreview(
            body=f"{body[:255]}…", deleted=deleted, truncated=True
        )

    def agent_title(self, body: str | None) -> str | None:
        if body is None:
            return None
        line = body.strip().split("\n", 1)[0].strip()
        if len(line) <= _TITLE_MAX:
            return line
        return f"{line[: _TITLE_MAX - 1]}…"

    def agent_comment(self, comment: ReviewCommentView) -> AgentComment:
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
