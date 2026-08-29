"""Expose repository registry and ref-selection HTTP routes.

RepoRoutes keeps Mark, default-branch, ref-choice, and Pull Request preparation
models beside their handlers. Its conversion functions keep wire-to-domain
normalization beside the handlers that use it.

Instances retain one RepoMarkStore. This module does not capture Snapshots,
render diffs, persist Profiles, or construct the FastAPI application.
"""

import logging
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException, Query

from dirdiff.backend import (
    BranchSelection,
    DefaultBaseSelection,
    GitBackend,
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
from dirdiff.server.base import ApiModel, ErrorResponse
from dirdiff.server.magic import ClassRoutes

__all__ = [
    "RepoRoutes",
]

LOGGER = logging.getLogger(__name__)
"""Record unexpected failures at this HTTP boundary."""


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


def _pull_request_prepare_response(
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


class RepoRoutes:
    """Bind repository registry handlers to one Mark store.

    One instance retains the repository registry used for Mark lookup, ref
    discovery, saved defaults, and Pull Request preparation. Route declarations
    contain no Snapshot or rendering operations.
    """

    routes = ClassRoutes()
    """Import-time declarations bound to one route-group instance."""

    def __init__(self, db: RepoMarkStore) -> None:
        """Retain the repository registry used by every route in this group."""
        self.db = db

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
