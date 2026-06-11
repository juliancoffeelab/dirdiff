from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping
from http import HTTPStatus
from importlib.resources import files
from typing import Any, Literal

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dirdiff.diff import TextDiffError, TextDiffService


LOGGER = logging.getLogger(__name__)

ModeParam = Literal["files", "staged", "head", "refs", "branch-review"]
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
RowStatus = Literal["equal", "replace", "insert", "delete", "fold", "elided"]


class ErrorResponse(BaseModel):
    error: str


class SaveLogRequest(BaseModel):
    text: str


class SaveLogResponse(BaseModel):
    path: str


class SyntaxSpanResponse(BaseModel):
    start: int
    end: int
    classes: list[str]


class InlineTokenResponse(BaseModel):
    text: str
    changed: bool
    is_ws: bool


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


def create_app(service: TextDiffService, defaults: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    asset_version = str(time.time_ns())
    app.mount(
        "/static",
        StaticFiles(packages=[("dirdiff", "static")]),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    def serve_index() -> HTMLResponse:
        template_path = files("dirdiff").joinpath("templates/index.html")
        html = template_path.read_text(encoding="utf-8").replace(
            "__DEFAULTS_JSON__",
            json.dumps(defaults),
        )
        html = html.replace("__ASSET_VERSION__", asset_version)
        return HTMLResponse(html)

    @app.get("/api/defaults")
    def serve_defaults() -> dict[str, Any]:
        return defaults

    @app.post(
        "/api/save-log",
        response_model=SaveLogResponse,
        responses={
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Save a debug log to the launch directory",
    )
    def save_log(payload: SaveLogRequest) -> SaveLogResponse | JSONResponse:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        destination = service.cwd / f"dirdiff-scroll-debug-{timestamp}.log"
        try:
            destination.write_text(payload.text, encoding="utf-8")
        except OSError as exc:
            LOGGER.exception("Failed to save debug log: %s", exc)
            return JSONResponse(
                {"error": f"Failed to save debug log: {exc}"},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return SaveLogResponse(path=str(destination))

    @app.get(
        "/api/diff-stream",
        response_model=None,
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
            HTTPStatus.OK: {
                "content": {
                    "text/event-stream": {
                        "schema": {
                            "type": "string",
                            "description": "Server-sent events emitting `init`, `file`, `done`, and `stream-error` events.",
                        }
                    }
                }
            },
        },
        summary="Stream a diff with SSE progress events",
    )
    def serve_diff_stream(
        mode: ModeParam = Query(default=defaults["mode"], description="UI diff mode."),
        left: str = Query(default=defaults["left"], description="Left ref or diff side."),
        right: str = Query(default=defaults["right"], description="Right ref or diff side."),
        base_branch: str | None = Query(
            default=defaults.get("base_branch"),
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=defaults.get("review_branch"),
            description="Branch being reviewed in branch-review mode.",
        ),
    ) -> Any:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        try:
            initial_payload, progress_iter, label_overrides = _build_stream_payload(
                service=service,
                defaults=defaults,
                mode=mode,
                left=left,
                right=right,
                base_branch=selected_base_branch,
                branch=selected_review_branch,
            )
        except TextDiffError as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            LOGGER.exception("Diff stream request crashed before streaming: %s", exc)
            return JSONResponse(
                {"error": "Internal server error."},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        def event_stream() -> Iterator[bytes]:
            yield _encode_sse("init", initial_payload)
            latest_summary = initial_payload["summary"]
            try:
                for progress in progress_iter:
                    entry = dict(progress.entry)
                    if label_overrides[0]:
                        entry["left_label"] = label_overrides[0]
                    if label_overrides[1]:
                        entry["right_label"] = label_overrides[1]
                    latest_summary = progress.summary
                    yield _encode_sse(
                        "file",
                        {
                            "entry": entry,
                            "summary": progress.summary,
                        },
                    )
            except TextDiffError as exc:
                yield _encode_sse("stream-error", {"error": str(exc)})
                return
            except Exception as exc:
                LOGGER.exception("Diff stream request crashed while streaming: %s", exc)
                yield _encode_sse("stream-error", {"error": "Internal server error."})
                return

            yield _encode_sse("done", {"summary": latest_summary})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
        )

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
        mode: ModeParam = Query(default=defaults["mode"], description="UI diff mode."),
        left: str = Query(default=defaults["left"], description="Left ref or diff side."),
        right: str = Query(default=defaults["right"], description="Right ref or diff side."),
        base_branch: str | None = Query(
            default=defaults.get("base_branch"),
            description="Base branch for branch-review mode.",
        ),
        review_branch: str | None = Query(
            default=defaults.get("review_branch"),
            description="Branch being reviewed in branch-review mode.",
        ),
        left_path: str | None = Query(default=None, description="Repo-relative path on the left side."),
        right_path: str | None = Query(default=None, description="Repo-relative path on the right side."),
        display_name: str | None = Query(default=None, description="UI display name override."),
        change_type: ChangeType = Query(default="modify", description="Git change classification."),
    ) -> TextFileDiffResponse | NotebookFileDiffResponse | JSONResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )

        try:
            if (
                mode == "branch-review"
                and selected_review_branch
                and selected_review_branch.strip()
            ):
                resolved_base_branch, merge_base, normalized_branch = _resolve_branch_review_refs(
                    service=service,
                    base_branch=selected_base_branch,
                    branch=selected_review_branch,
                )
                left_label = f"{resolved_base_branch.strip()}...{normalized_branch}"
                payload = service.build_git_diff_paths(
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
                payload = service.build_git_diff_paths(
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
        mode: ModeParam = Query(default=defaults["mode"], description="UI diff mode."),
        left: str = Query(default=defaults["left"], description="Left ref or diff side."),
        right: str = Query(default=defaults["right"], description="Right ref or diff side."),
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
        cell_key: str | None = Query(default=None, description="Stable notebook cell key."),
        left_path: str | None = Query(default=None, description="Repo-relative path on the left side."),
        right_path: str | None = Query(default=None, description="Repo-relative path on the right side."),
    ) -> NotebookSectionDiffResponse | JSONResponse:
        selected_base_branch, selected_review_branch = selected_branches(
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )

        try:
            if (
                mode == "branch-review"
                and selected_review_branch
                and selected_review_branch.strip()
            ):
                resolved_base_branch, merge_base, normalized_branch = _resolve_branch_review_refs(
                    service=service,
                    base_branch=selected_base_branch,
                    branch=selected_review_branch,
                )
                payload = service.build_notebook_section_diff(
                    left_path=left_path,
                    right_path=right_path,
                    left=merge_base,
                    right=normalized_branch,
                    section=section or "",
                    cell_key=cell_key,
                )
                payload["left_label"] = f"{resolved_base_branch.strip()}...{normalized_branch}"
                payload["right_label"] = normalized_branch
            else:
                payload = service.build_notebook_section_diff(
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


def _build_stream_payload(
    *,
    service: TextDiffService,
    defaults: Mapping[str, Any],
    mode: str | None,
    left: str | None,
    right: str | None,
    base_branch: str | None,
    branch: str | None,
) -> tuple[dict[str, Any], Any, tuple[str | None, str | None]]:
    if mode == "branch-review" and branch and branch.strip():
        resolved_base_branch, merge_base, normalized_branch = _resolve_branch_review_refs(
            service=service,
            base_branch=base_branch,
            branch=branch,
        )
        left_label = f"{resolved_base_branch.strip()}...{normalized_branch}"
        progress_iter = service.iter_repo_diff_progress(
            left=merge_base,
            right=normalized_branch,
        )
        return (
            {
                "display_name": "Repository diff",
                "mode": "repo",
                "left_label": left_label,
                "right_label": normalized_branch,
                "summary": {
                    "changed_files": 0,
                    "added_files": 0,
                    "removed_files": 0,
                    "updated_files": 0,
                    "changed_lines": 0,
                    "modified_lines": 0,
                    "added_lines": 0,
                    "removed_lines": 0,
                    "skipped_files": 0,
                },
            },
            progress_iter,
            (left_label, normalized_branch),
        )

    normalized_left = service.normalize_side(left or defaults["left"])
    normalized_right = service.normalize_side(right or defaults["right"])
    progress_iter = service.iter_repo_diff_progress(
        left=normalized_left,
        right=normalized_right,
    )
    return (
        {
            "display_name": "Repository diff",
            "mode": "repo",
            "left_label": normalized_left,
            "right_label": normalized_right,
            "summary": {
                "changed_files": 0,
                "added_files": 0,
                "removed_files": 0,
                "updated_files": 0,
                "changed_lines": 0,
                "modified_lines": 0,
                "added_lines": 0,
                "removed_lines": 0,
                "skipped_files": 0,
            },
        },
        progress_iter,
        (None, None),
    )


def _encode_sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
