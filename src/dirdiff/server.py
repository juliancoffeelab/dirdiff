import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Literal

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from dirdiff.diff import TextDiffError, TextDiffService


LOGGER = logging.getLogger(__name__)

ModeParam = Literal["files", "staged", "head", "refs", "branch-review"]
EngineParam = Literal["dirdiff", "git", "difftastic"]
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
RowStatus = Literal["equal", "replace", "insert", "delete", "fold", "elided"]


class ErrorResponse(BaseModel):
    error: str


class SyntaxSpanResponse(BaseModel):
    start: int
    end: int
    classes: list[str]


class InlineTokenResponse(BaseModel):
    text: str
    is_ws: bool
    status: Literal["unchanged", "replace", "insert", "delete"]


class FoldHintResponse(BaseModel):
    start_row: int
    end_row: int
    label: str


class DiffRowResponse(BaseModel):
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
    foldedRows: list["DiffRowResponse"] = Field(default_factory=list)
    label: str | None = None


class TextDiffSummaryResponse(BaseModel):
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


class RepoDiffSummaryResponse(BaseModel):
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


class TextFileDiffResponse(BaseModel):
    display_name: str
    mode: Literal["git"]
    left_label: str
    right_label: str
    summary: TextDiffSummaryResponse
    rows: list[DiffRowResponse]
    change_type: ChangeType | None = None
    left_path: str | None = None
    right_path: str | None = None
    lazy: bool = False
    default_expanded: bool = True
    lazy_reason: str | None = None
    render_mode: Literal["plain"] | None = None
    truncated_rows: int | None = None
    fold_hints: list[FoldHintResponse] = Field(default_factory=list)


class NotebookCellDiffResponse(BaseModel):
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


class NotebookFileDiffResponse(BaseModel):
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
    change_type: ChangeType | None = None
    left_path: str | None = None
    right_path: str | None = None
    default_expanded: bool = True


class RepoFileEntryResponse(BaseModel):
    change_type: ChangeType
    left_path: str | None = None
    right_path: str | None = None
    lazy: bool = False
    lazy_reason: str | None = None


class NotebookSectionDiffResponse(BaseModel):
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


class RepoDiffResponse(BaseModel):
    display_name: str
    mode: Literal["repo"]
    left_label: str
    right_label: str
    summary: RepoDiffSummaryResponse
    files: list[RepoFileEntryResponse]


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


def create_app(
    service: TextDiffService,
    defaults: dict[str, Any],
    *,
    services: Mapping[str, TextDiffService] | None = None,
) -> FastAPI:
    app = FastAPI()
    diff_services = {"dirdiff": service, **(services or {})}

    def selected_service(engine: EngineParam) -> TextDiffService:
        return diff_services.get(engine, service)

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

    @app.get("/api/defaults")
    def serve_defaults() -> dict[str, Any]:
        return defaults

    @app.get(
        "/api/diff",
        response_model=RepoDiffResponse,
        response_model_exclude_defaults=True,
        response_model_exclude_none=True,
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load a repository diff",
    )
    def serve_diff(
        engine: EngineParam = Query(
            default=defaults["engine"], description="Diff engine."
        ),
        mode: ModeParam = Query(default=defaults["mode"], description="UI diff mode."),
        left: str = Query(
            default=defaults["left"], description="Left ref or diff side."
        ),
        right: str = Query(
            default=defaults["right"], description="Right ref or diff side."
        ),
        base_branch: str | None = Query(
            default=defaults.get("base_branch"),
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=defaults.get("review_branch"),
            description="Branch being reviewed in branch-review mode.",
        ),
    ) -> RepoDiffResponse | JSONResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        diff_service = selected_service(engine)
        try:
            if mode == "branch-review" and selected_review_branch:
                resolved_base_branch, merge_base, normalized_branch = (
                    _resolve_branch_review_refs(
                        service=diff_service,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                left_label = f"{resolved_base_branch.strip()}...{normalized_branch}"
                payload = diff_service.build_repo_manifest(
                    left=merge_base,
                    right=normalized_branch,
                )
                payload["left_label"] = left_label
                payload["right_label"] = normalized_branch
            else:
                normalized_left = diff_service.normalize_side(left)
                normalized_right = diff_service.normalize_side(right)
                payload = diff_service.build_repo_manifest(
                    left=normalized_left,
                    right=normalized_right,
                )
        except TextDiffError as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            LOGGER.exception("Diff request crashed: %s", exc)
            return JSONResponse(
                {"error": "Internal server error."},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        return RepoDiffResponse.model_validate(payload)

    @app.get(
        "/api/file-diff",
        response_model=TextFileDiffResponse | NotebookFileDiffResponse,
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load a single file diff",
    )
    def serve_file_diff(
        engine: EngineParam = Query(
            default=defaults["engine"], description="Diff engine."
        ),
        mode: ModeParam = Query(default=defaults["mode"], description="UI diff mode."),
        left: str = Query(
            default=defaults["left"], description="Left ref or diff side."
        ),
        right: str = Query(
            default=defaults["right"], description="Right ref or diff side."
        ),
        base_branch: str | None = Query(
            default=defaults.get("base_branch"),
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=defaults.get("review_branch"),
            description="Branch being reviewed in branch-review mode.",
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
    ) -> TextFileDiffResponse | NotebookFileDiffResponse | JSONResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        diff_service = selected_service(engine)

        try:
            if (
                mode == "branch-review"
                and selected_review_branch
                and selected_review_branch.strip()
            ):
                resolved_base_branch, merge_base, normalized_branch = (
                    _resolve_branch_review_refs(
                        service=diff_service,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                left_label = f"{resolved_base_branch.strip()}...{normalized_branch}"
                payload = diff_service.build_git_diff_paths(
                    left_path=left_path,
                    right_path=right_path,
                    left=merge_base,
                    right=normalized_branch,
                    display_name=display_name,
                    change_type=change_type,
                )
                payload["left_label"] = left_label
                payload["right_label"] = normalized_branch
            else:
                payload = diff_service.build_git_diff_paths(
                    left_path=left_path,
                    right_path=right_path,
                    left=left,
                    right=right,
                    display_name=display_name,
                    change_type=change_type,
                )
        except TextDiffError as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            LOGGER.exception("File diff request crashed: %s", exc)
            return JSONResponse(
                {"error": "Internal server error."},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        if payload.get("render_kind") == "notebook":
            return NotebookFileDiffResponse.model_validate(payload)
        return TextFileDiffResponse.model_validate(payload)

    @app.get(
        "/api/notebook-section",
        response_model=NotebookSectionDiffResponse,
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load notebook metadata or output rows for a specific cell",
    )
    def serve_notebook_section(
        engine: EngineParam = Query(
            default=defaults["engine"], description="Diff engine."
        ),
        mode: ModeParam = Query(default=defaults["mode"], description="UI diff mode."),
        left: str = Query(
            default=defaults["left"], description="Left ref or diff side."
        ),
        right: str = Query(
            default=defaults["right"], description="Right ref or diff side."
        ),
        base_branch: str | None = Query(
            default=defaults.get("base_branch"),
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=defaults.get("review_branch"),
            description="Branch being reviewed in branch-review mode.",
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
    ) -> NotebookSectionDiffResponse | JSONResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        diff_service = selected_service(engine)

        try:
            if (
                mode == "branch-review"
                and selected_review_branch
                and selected_review_branch.strip()
            ):
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
                    section=section or "",
                    cell_key=cell_key,
                )
                payload["left_label"] = (
                    f"{resolved_base_branch.strip()}...{normalized_branch}"
                )
                payload["right_label"] = normalized_branch
            else:
                payload = diff_service.build_notebook_section_diff(
                    left_path=left_path,
                    right_path=right_path,
                    left=left,
                    right=right,
                    section=section or "",
                    cell_key=cell_key,
                )
        except TextDiffError as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            LOGGER.exception("Notebook section diff request crashed: %s", exc)
            return JSONResponse(
                {"error": "Internal server error."},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        return NotebookSectionDiffResponse.model_validate(payload)

    return app


def _resolve_branch_review_refs(
    *,
    service: TextDiffService,
    base_branch: str | None,
    branch: str,
) -> tuple[str, str, str]:
    resolved_base_branch = base_branch or service.default_base_branch()
    merge_base, normalized_branch = service.resolve_branch_diff_sides(
        base_branch=resolved_base_branch,
        branch=branch,
    )
    return resolved_base_branch, merge_base, normalized_branch
