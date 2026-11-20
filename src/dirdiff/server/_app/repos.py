import logging
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException, Query

from dirdiff.change_source import (
    BranchSelection,
    DefaultBaseSelectionError,
    GitRepository,
    PreparedPullRequest,
    RefChoices,
    preferred_review_selection,
    prepare_pull_request,
    ref_choices,
)
from dirdiff.db import (
    RepoMainBranchRecord,
    RepoMarkStore,
)
from dirdiff.engines import (
    DirdiffError,
)
from dirdiff.server.base import ApiModel, ClassRoutes, ErrorResponse
from dirdiff.util import Ok

__all__ = [
    "RepoRoutes",
]

LOGGER = logging.getLogger(__name__)


def branch_selection_request_to_selection(
    request: BranchSelection,
) -> BranchSelection:
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
    selection: BranchSelection


class RepoMainBranchResponse(ApiModel):
    project_id: int

    selection: BranchSelection


class RepoDefaultsResponse(ApiModel):
    default_base_selection: BranchSelection | DefaultBaseSelectionError

    preferred_review_selection: BranchSelection


class RepoRefsResponse(ApiModel):
    ref_choices: RefChoices


class PullRequestPrepareRequest(ApiModel):
    url: str


class PullRequestPrepareResponse(ApiModel):
    project_id: int

    pull_request_url: str

    left_commit: str

    right_commit: str

    base_branch: str

    head_branch: str


def _pull_request_prepare_response(
    prepared: PreparedPullRequest,
) -> PullRequestPrepareResponse:

    return PullRequestPrepareResponse.model_validate(
        {
            "project_id": prepared.project_id,
            "pull_request_url": prepared.pull_request_url,
            "left_commit": prepared.left_commit,
            "right_commit": prepared.right_commit,
            "base_branch": prepared.base_branch,
            "head_branch": prepared.head_branch,
        }
    )


class RepoMarkResponse(ApiModel):
    id: int

    path: str

    name: str

    marked_at: datetime


class RepoRoutes:
    routes = ClassRoutes()

    def __init__(self, db: RepoMarkStore) -> None:
        self.db = db

    @routes.get("/api/repo-defaults")
    def serve_repo_defaults(
        self,
        project_id: int = Query(
            description="Marked project id. Required for repo-backed defaults.",
        ),
    ) -> RepoDefaultsResponse:
        mark = self.db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        repository = GitRepository.discover(repo_root=Path(mark.path))
        # One metadata snapshot feeds both derivations so base and review
        # defaults cannot come from different repository states.
        metadata = repository.read_ref_metadata()
        saved_main_branch = self.db.get_main_branch(project_id)
        default_base_selection = (
            Ok(repo_main_branch_record_to_selection(saved_main_branch))
            if saved_main_branch is not None
            else repository.default_base_selection(metadata)
        )
        return RepoDefaultsResponse.model_validate(
            {
                "default_base_selection": (
                    default_base_selection.value
                    if Ok.good(default_base_selection)
                    else default_base_selection.error
                ),
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
        mark = self.db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        repository = GitRepository.discover(repo_root=Path(mark.path))
        return RepoRefsResponse.model_validate(
            {"ref_choices": ref_choices(repository.read_ref_metadata())}
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

    @routes.get("/api/repos")
    def serve_repos(self) -> list[RepoMarkResponse]:
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
        try:
            return _pull_request_prepare_response(
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
