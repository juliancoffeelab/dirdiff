import json
import logging
import os
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from dirdiff.db.base import open_sqlite_engine
from dirdiff.db.preferences import PreferencesStore
from dirdiff.db.repo_registry import RepoMarkStore
from dirdiff.db.user_profile import UserProfileStore
from dirdiff.runtime import RUNTIME_CONFIG_ENV, RuntimeConfig
from dirdiff.services import (
    DiffServiceProtocol,
    DifftasticDiffService,
    GitDiffService,
    TextDiffService,
)
from dirdiff.sources import (
    GitBackend,
    PresetBackend,
    TextDiffError,
    WorkspaceBackend,
)

LOGGER = logging.getLogger(__name__)

ModeParam = Literal[
    "files", "staged", "head", "refs", "branch-review", "preset"
]
EngineParam = Literal["dirdiff", "git", "difftastic"]
PresetTypeParam = Literal["diff", "fold"]
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
GitFileStatus = Literal["modified", "added", "deleted", "renamed", "copied"]
LazyReason = (
    Literal["too_big", "generated", "deleted", "untracked", "pure_renamed"]
    | None
)
RowStatus = Literal["equal", "replace", "insert", "delete", "fold", "elided"]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ErrorResponse(ApiModel):
    error: str


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
    id: int
    aggressive_folds: bool


class PreferencesUpdateRequest(ApiModel):
    aggressive_folds: bool


class PresetGroupResponse(ApiModel):
    name: str
    display_name: str


class PresetCatalogResponse(ApiModel):
    default_preset: str
    groups: list[PresetGroupResponse]


class PresetCatalogsResponse(ApiModel):
    diff: PresetCatalogResponse
    fold: PresetCatalogResponse


class SyntaxSpanResponse(ApiModel):
    start: int
    end: int
    classes: list[str]


class InlineTokenResponse(ApiModel):
    text: str
    is_ws: bool
    status: Literal["unchanged", "replace", "insert", "delete"]


class FoldHintResponse(ApiModel):
    start_row: int
    end_row: int
    label: str


class DiffRowResponse(ApiModel):
    status: RowStatus
    left_no: int | None = None
    right_no: int | None = None
    left_text: str | None = None
    right_text: str | None = None
    left_tokens: list[InlineTokenResponse] = Field(default_factory=list)
    right_tokens: list[InlineTokenResponse] = Field(default_factory=list)
    left_syntax: list[SyntaxSpanResponse] = Field(default_factory=list)
    right_syntax: list[SyntaxSpanResponse] = Field(default_factory=list)
    count: int | None = None
    foldedRows: list[DiffRowResponse] = Field(default_factory=list)
    label: str | None = None


class TextDiffSummaryResponse(ApiModel):
    changed_lines: int
    modified_lines: int
    added_lines: int
    removed_lines: int
    left_exists: bool
    right_exists: bool


class NotebookDiffSummaryResponse(TextDiffSummaryResponse):
    changed_cells: int
    added_cells: int
    removed_cells: int
    modified_cells: int
    notebook_metadata_changed: bool


class RepoDiffSummaryResponse(ApiModel):
    changed_files: int
    added_files: int
    removed_files: int
    updated_files: int
    changed_lines: int
    modified_lines: int
    added_lines: int
    removed_lines: int
    skipped_files: int
    changed_cells: int | None = None
    added_cells: int | None = None
    removed_cells: int | None = None
    modified_cells: int | None = None


class GitFileKindResponse(ApiModel):
    type: Literal["git"]
    status: GitFileStatus


class UntrackedFileKindResponse(ApiModel):
    type: Literal["untracked"]


FileKindResponse = GitFileKindResponse | UntrackedFileKindResponse


class EngineWarningResponse(ApiModel):
    type: Literal["difftastic_graph_limit"]
    message: str


class TextFileDiffResponse(ApiModel):
    display_name: str
    mode: Literal["git"]
    left_label: str
    right_label: str
    summary: TextDiffSummaryResponse
    rows: list[DiffRowResponse]
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    lazy: LazyReason = None
    default_expanded: bool = True
    render_mode: Literal["plain"] | None = None
    truncated_rows: int | None = None
    fold_hints: list[FoldHintResponse] = Field(default_factory=list)
    engine_warning: EngineWarningResponse | None = None


class NotebookCellDiffResponse(ApiModel):
    kind: Literal["added", "removed", "modified"]
    cell_type: str
    cell_id: str | None = None
    cell_key: str
    left_index: int | None = None
    right_index: int | None = None
    left_id: str | None = None
    right_id: str | None = None
    source_changed: bool
    metadata_changed: bool
    outputs_changed: bool
    source_rows: list[DiffRowResponse]
    source_changed_lines: int
    source_modified_lines: int
    source_added_lines: int
    source_removed_lines: int
    source_fold_hints: list[FoldHintResponse] = Field(default_factory=list)
    metadata_rows: list[DiffRowResponse] = Field(default_factory=list)
    outputs_rows: list[DiffRowResponse] = Field(default_factory=list)
    metadata_changed_lines: int
    metadata_modified_lines: int
    metadata_added_lines: int
    metadata_removed_lines: int
    metadata_hunk_count: int
    metadata_lazy: bool
    outputs_changed_lines: int
    outputs_modified_lines: int
    outputs_added_lines: int
    outputs_removed_lines: int
    outputs_hunk_count: int
    outputs_lazy: bool
    source_render_mode: Literal["plain"] | None = None
    source_truncated_rows: int | None = None
    metadata_render_mode: Literal["plain"] | None = None
    metadata_truncated_rows: int | None = None
    outputs_render_mode: Literal["plain"] | None = None
    outputs_truncated_rows: int | None = None


class NotebookFileDiffResponse(ApiModel):
    display_name: str
    mode: Literal["git"]
    render_kind: Literal["notebook"]
    left_label: str
    right_label: str
    summary: NotebookDiffSummaryResponse
    notebook_metadata_rows: list[DiffRowResponse] = Field(default_factory=list)
    notebook_metadata_changed_lines: int
    notebook_metadata_hunk_count: int
    notebook_metadata_lazy: bool
    cells: list[NotebookCellDiffResponse]
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    default_expanded: bool = True


class RepoFileEntryResponse(ApiModel):
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    lazy: LazyReason = None


class LazyInfoFileResponse(ApiModel):
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    display_name: str
    summary: TextDiffSummaryResponse


class NotebookSectionDiffResponse(ApiModel):
    section: str
    cell_key: str | None = None
    left_index: int | None = None
    right_index: int | None = None
    left_label: str
    right_label: str
    rows: list[DiffRowResponse]
    render_mode: Literal["plain"] | None = None
    truncated_rows: int = 0
    fold_hints: list[FoldHintResponse] = Field(default_factory=list)


class RepoManifestResponse(ApiModel):
    display_name: str
    mode: Literal["repo"]
    left_label: str
    right_label: str
    summary: RepoDiffSummaryResponse
    files: list[RepoFileEntryResponse]


class LazyInfoResponse(ApiModel):
    files: list[LazyInfoFileResponse]


DiffRowResponse.model_rebuild()


def selected_branches(
    *,
    mode: ModeParam,
    base_branch: str | None,
    review_branch: str | None,
) -> tuple[str | None, str | None]:
    if mode != "branch-review":
        return None, None

    return base_branch, review_branch


def service_for_backend(
    engine: EngineParam, backend: WorkspaceBackend
) -> DiffServiceProtocol:
    if engine == "dirdiff":
        return TextDiffService(backend)
    if engine == "git":
        return GitDiffService(backend)
    if engine == "difftastic":
        return DifftasticDiffService(backend)
    raise TextDiffError(f"Unknown diff engine: {engine}")


def create_app(
    db: RepoMarkStore,
    user_profile_store: UserProfileStore | None = None,
    preferences_store: PreferencesStore | None = None,
    *,
    presets_root: str | None = None,
) -> FastAPI:
    if user_profile_store is None:
        user_profile_store = UserProfileStore(db.engine)
    if preferences_store is None:
        preferences_store = PreferencesStore(db.engine)

    app = FastAPI()

    def preset_backend_for_type(preset_type: PresetTypeParam) -> PresetBackend:
        if preset_type == "diff":
            if presets_root is not None:
                return PresetBackend.discover(presets_root=Path(presets_root))
            return PresetBackend.discover()
        return PresetBackend.discover(
            presets_root=Path.cwd() / "tests" / "presets" / "folds"
        )

    def preset_catalog_for_type(
        preset_type: PresetTypeParam,
    ) -> PresetCatalogResponse:
        preset_backend = preset_backend_for_type(preset_type)
        return PresetCatalogResponse.model_validate(
            {
                "default_preset": preset_backend.default_preset_name(),
                "groups": preset_backend.list_preset_groups(),
            }
        )

    def service_for_request(
        engine: EngineParam,
        *,
        mode: ModeParam,
        repo_id: int,
        preset_type: PresetTypeParam | None,
    ) -> DiffServiceProtocol:
        if mode == "preset":
            if preset_type is None:
                raise TextDiffError("preset_type is required for preset mode.")
            return service_for_backend(
                engine, preset_backend_for_type(preset_type)
            )

        mark = db.get(repo_id)
        if mark is None:
            raise TextDiffError(f"Invalid repo_id: {repo_id}")
        backend = GitBackend.discover(repo_root=Path(mark.path))
        return service_for_backend(engine, backend)

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

    @app.get("/api/repo-refs")
    def serve_repo_refs(
        repo_id: int = Query(
            description="Marked repo id. Required for repo-backed refs.",
        ),
    ) -> dict[str, Any]:
        mark = db.get(repo_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid repo_id: {repo_id}",
            )
        backend = GitBackend.discover(repo_root=Path(mark.path))
        service = TextDiffService(backend)
        default_base_branch = service.default_base_branch()
        preferred_review_branch = service.preferred_review_branch(
            base_branch=default_base_branch
        )
        return {
            "default_base_branch": default_base_branch,
            "preferred_review_branch": preferred_review_branch,
            "ref_choices": service.list_ref_choices(),
        }

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
                }
            )
        except TextDiffError as exc:
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
        "/api/preferences",
        summary="Load persisted global preferences",
    )
    def serve_preferences() -> PreferencesResponse:
        return PreferencesResponse.model_validate(
            preferences_store.get_or_create(),
            from_attributes=True,
        )

    @app.patch(
        "/api/preferences/{preferences_id}",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted global preferences",
    )
    def update_preferences(
        preferences_id: int,
        request: PreferencesUpdateRequest,
    ) -> PreferencesResponse:
        preferences = preferences_store.update_aggressive_folds(
            preferences_id, request.aggressive_folds
        )
        if preferences is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Preferences not found: {preferences_id}.",
            )
        return PreferencesResponse.model_validate(
            preferences,
            from_attributes=True,
        )

    @app.get(
        "/api/manifest",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load a repository diff manifest",
    )
    def serve_manifest(
        repo_id: int = Query(
            description="Marked repo id. Required for repo-backed modes.",
        ),
        engine: EngineParam = Query(description="Diff engine."),
        mode: ModeParam = Query(description="UI diff mode."),
        left: str | None = Query(
            default=None, description="Left ref or diff side."
        ),
        right: str | None = Query(
            default=None, description="Right ref or diff side."
        ),
        base_branch: str | None = Query(
            default=None,
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=None,
            description="Branch being reviewed in branch-review mode.",
        ),
        preset: str | None = Query(
            default=None,
            description="Preset name for preset mode.",
        ),
        preset_type: PresetTypeParam | None = Query(
            default=None,
            description="Preset catalog type for preset mode.",
        ),
        show_untracked: bool = Query(
            default=False,
            description="Include untracked worktree files when supported by the selected mode.",
        ),
    ) -> RepoManifestResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        try:
            diff_service = service_for_request(
                engine,
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = diff_service.normalize_side(preset)
                payload = diff_service.build_repo_manifest(
                    left=preset_name,
                    right="new",
                    show_untracked=False,
                )
                payload["display_name"] = preset_name
                payload["left_label"] = "old"
                payload["right_label"] = "new"
            elif mode == "branch-review":
                if (
                    selected_review_branch is None
                    or not selected_review_branch.strip()
                ):
                    raise TextDiffError(
                        "review_branch is required for branch-review mode."
                    )
                resolved_base_branch, merge_base, normalized_branch = (
                    _resolve_branch_review_refs(
                        service=diff_service,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                left_label = (
                    f"{resolved_base_branch.strip()}...{normalized_branch}"
                )
                payload = diff_service.build_repo_manifest(
                    left=merge_base,
                    right=normalized_branch,
                    show_untracked=False,
                )
                payload["left_label"] = left_label
                payload["right_label"] = normalized_branch
            else:
                if left is None or not left.strip():
                    raise TextDiffError("left is required for this diff mode.")
                if right is None or not right.strip():
                    raise TextDiffError("right is required for this diff mode.")
                normalized_left = diff_service.normalize_side(left)
                normalized_right = diff_service.normalize_side(right)
                payload = diff_service.build_repo_manifest(
                    left=normalized_left,
                    right=normalized_right,
                    show_untracked=show_untracked,
                )
        except TextDiffError as exc:
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
        repo_id: int = Query(
            description="Marked repo id. Required for repo-backed modes.",
        ),
        engine: EngineParam = Query(description="Diff engine."),
        mode: ModeParam = Query(description="UI diff mode."),
        left: str | None = Query(
            default=None, description="Left ref or diff side."
        ),
        right: str | None = Query(
            default=None, description="Right ref or diff side."
        ),
        base_branch: str | None = Query(
            default=None,
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=None,
            description="Branch being reviewed in branch-review mode.",
        ),
        preset: str | None = Query(
            default=None,
            description="Preset name for preset mode.",
        ),
        preset_type: PresetTypeParam | None = Query(
            default=None,
            description="Preset catalog type for preset mode.",
        ),
        show_untracked: bool = Query(
            default=False,
            description="Include untracked worktree files when supported by the selected mode.",
        ),
    ) -> LazyInfoResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        try:
            diff_service = service_for_request(
                engine,
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = diff_service.normalize_side(preset)
                payload = diff_service.build_lazy_info(
                    left=preset_name,
                    right="new",
                    show_untracked=False,
                )
            elif mode == "branch-review":
                if (
                    selected_review_branch is None
                    or not selected_review_branch.strip()
                ):
                    raise TextDiffError(
                        "review_branch is required for branch-review mode."
                    )
                _, merge_base, normalized_branch = _resolve_branch_review_refs(
                    service=diff_service,
                    base_branch=selected_base_branch,
                    branch=selected_review_branch,
                )
                payload = diff_service.build_lazy_info(
                    left=merge_base,
                    right=normalized_branch,
                    show_untracked=False,
                )
            else:
                if left is None or not left.strip():
                    raise TextDiffError("left is required for this diff mode.")
                if right is None or not right.strip():
                    raise TextDiffError("right is required for this diff mode.")
                normalized_left = diff_service.normalize_side(left)
                normalized_right = diff_service.normalize_side(right)
                payload = diff_service.build_lazy_info(
                    left=normalized_left,
                    right=normalized_right,
                    show_untracked=show_untracked,
                )
        except TextDiffError as exc:
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
        repo_id: int = Query(
            description="Marked repo id. Required for repo-backed modes.",
        ),
        engine: EngineParam = Query(description="Diff engine."),
        mode: ModeParam = Query(description="UI diff mode."),
        left: str | None = Query(
            default=None, description="Left ref or diff side."
        ),
        right: str | None = Query(
            default=None, description="Right ref or diff side."
        ),
        base_branch: str | None = Query(
            default=None,
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=None,
            description="Branch being reviewed in branch-review mode.",
        ),
        preset: str | None = Query(
            default=None,
            description="Preset name for preset mode.",
        ),
        preset_type: PresetTypeParam | None = Query(
            default=None,
            description="Preset catalog type for preset mode.",
        ),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
        display_name: str | None = Query(
            default=None, description="UI display name override."
        ),
        change_type: ChangeType = Query(
            default="modify", description="Git change classification."
        ),
        file_kind: Literal["git", "untracked"] = Query(
            default="git", description="File kind from the repo manifest."
        ),
    ) -> TextFileDiffResponse | NotebookFileDiffResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )

        try:
            diff_service = service_for_request(
                engine,
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = diff_service.normalize_side(preset)
                payload = diff_service.build_git_diff_paths(
                    left_path=left_path,
                    right_path=right_path,
                    left=preset_name,
                    right="new",
                    display_name=display_name,
                    change_type=change_type,
                    file_kind=file_kind,
                )
                payload["left_label"] = "old"
                payload["right_label"] = "new"
            elif mode == "branch-review":
                if (
                    selected_review_branch is None
                    or not selected_review_branch.strip()
                ):
                    raise TextDiffError(
                        "review_branch is required for branch-review mode."
                    )
                resolved_base_branch, merge_base, normalized_branch = (
                    _resolve_branch_review_refs(
                        service=diff_service,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                left_label = (
                    f"{resolved_base_branch.strip()}...{normalized_branch}"
                )
                payload = diff_service.build_git_diff_paths(
                    left_path=left_path,
                    right_path=right_path,
                    left=merge_base,
                    right=normalized_branch,
                    display_name=display_name,
                    change_type=change_type,
                    file_kind=file_kind,
                )
                payload["left_label"] = left_label
                payload["right_label"] = normalized_branch
            else:
                if left is None or not left.strip():
                    raise TextDiffError("left is required for this diff mode.")
                if right is None or not right.strip():
                    raise TextDiffError("right is required for this diff mode.")
                payload = diff_service.build_git_diff_paths(
                    left_path=left_path,
                    right_path=right_path,
                    left=left,
                    right=right,
                    display_name=display_name,
                    change_type=change_type,
                    file_kind=file_kind,
                )
        except TextDiffError as exc:
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

        if payload.get("render_kind") == "notebook":
            return NotebookFileDiffResponse.model_validate(payload)
        return TextFileDiffResponse.model_validate(payload)

    @app.get(
        "/api/notebook-section",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load notebook metadata or output rows for a specific cell",
    )
    def serve_notebook_section(
        repo_id: int = Query(
            description="Marked repo id. Required for repo-backed modes.",
        ),
        engine: EngineParam = Query(description="Diff engine."),
        mode: ModeParam = Query(description="UI diff mode."),
        left: str | None = Query(
            default=None, description="Left ref or diff side."
        ),
        right: str | None = Query(
            default=None, description="Right ref or diff side."
        ),
        base_branch: str | None = Query(
            default=None,
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=None,
            description="Branch being reviewed in branch-review mode.",
        ),
        preset: str | None = Query(
            default=None,
            description="Preset name for preset mode.",
        ),
        preset_type: PresetTypeParam | None = Query(
            default=None,
            description="Preset catalog type for preset mode.",
        ),
        section: str | None = Query(
            default=None,
            description="Notebook section name, for example `notebook-metadata`, `cell-metadata`, or `cell-outputs`.",
        ),
        cell_key: str | None = Query(
            default=None, description="Stable notebook cell key."
        ),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
    ) -> NotebookSectionDiffResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )

        try:
            diff_service = service_for_request(
                engine,
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = diff_service.normalize_side(preset)
                payload = diff_service.build_notebook_section_diff(
                    left_path=left_path,
                    right_path=right_path,
                    left=preset_name,
                    right="new",
                    section=section,
                    cell_key=cell_key,
                )
                payload["left_label"] = "old"
                payload["right_label"] = "new"
            elif mode == "branch-review":
                if (
                    selected_review_branch is None
                    or not selected_review_branch.strip()
                ):
                    raise TextDiffError(
                        "review_branch is required for branch-review mode."
                    )
                resolved_base_branch, merge_base, normalized_branch = (
                    _resolve_branch_review_refs(
                        service=diff_service,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                payload = diff_service.build_notebook_section_diff(
                    left_path=left_path,
                    right_path=right_path,
                    left=merge_base,
                    right=normalized_branch,
                    section=section,
                    cell_key=cell_key,
                )
                payload["left_label"] = (
                    f"{resolved_base_branch.strip()}...{normalized_branch}"
                )
                payload["right_label"] = normalized_branch
            else:
                if left is None or not left.strip():
                    raise TextDiffError("left is required for this diff mode.")
                if right is None or not right.strip():
                    raise TextDiffError("right is required for this diff mode.")
                payload = diff_service.build_notebook_section_diff(
                    left_path=left_path,
                    right_path=right_path,
                    left=left,
                    right=right,
                    section=section,
                    cell_key=cell_key,
                )
        except TextDiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Notebook section diff request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return NotebookSectionDiffResponse.model_validate(payload)

    return app


def uvicorn_entrypoint() -> FastAPI:
    payload = os.environ.get(RUNTIME_CONFIG_ENV)
    assert payload is not None, "dirdiff runtime config missing"
    config = RuntimeConfig(**json.loads(payload))
    engine = open_sqlite_engine(Path(config.db_path))
    repo_store = RepoMarkStore(engine)
    user_profile_store = UserProfileStore(engine)
    preferences_store = PreferencesStore(engine)
    marks = repo_store.list()
    assert marks, "dirdiff runtime config has no marked repos"
    return create_app(
        repo_store,
        user_profile_store,
        preferences_store,
        presets_root=config.presets_root,
    )


def _resolve_branch_review_refs(
    *,
    service: DiffServiceProtocol,
    base_branch: str | None,
    branch: str,
) -> tuple[str, str, str]:
    resolved_base_branch = base_branch or service.default_base_branch()
    merge_base, normalized_branch = service.resolve_branch_diff_sides(
        base_branch=resolved_base_branch,
        branch=branch,
    )
    return resolved_base_branch, merge_base, normalized_branch
