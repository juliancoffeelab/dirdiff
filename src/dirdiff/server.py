from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from http import HTTPStatus
from importlib.resources import files
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from dirdiff.diff import TextDiffError, TextDiffService


LARGE_DIFF_FILE_LIMIT = 10
LOGGER = logging.getLogger(__name__)


def pick_or[T](value: T | None, default: T) -> T:
    return default if value is None else value


def selected_branches(
    defaults: Mapping[str, Any],
    *,
    mode: str | None,
    base_branch: str | None,
    review_branch: str | None,
) -> tuple[str | None, str | None]:
    selected_mode = pick_or(mode, defaults["mode"])
    if selected_mode != "branch-review":
        return None, None

    selected_base_branch = pick_or(base_branch, defaults.get("base_branch"))
    selected_review_branch = pick_or(review_branch, defaults.get("review_branch"))
    return selected_base_branch, selected_review_branch


def create_app(service: TextDiffService, defaults: dict[str, Any]) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
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
        return HTMLResponse(html)

    @app.get("/api/diff")
    def serve_diff(
        mode: str | None = None,
        left: str | None = None,
        right: str | None = None,
        base_branch: str | None = None,
        review_branch: str | None = None,
        check: bool = False,
        force: bool = False,
    ) -> JSONResponse:
        selected_mode = pick_or(mode, defaults["mode"])
        selected_left = pick_or(left, defaults["left"])
        selected_right = pick_or(right, defaults["right"])
        selected_base_branch, selected_review_branch = selected_branches(
            defaults,
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        try:
            _enforce_large_diff_limit(
                service=service,
                defaults=defaults,
                mode=selected_mode,
                left=selected_left,
                right=selected_right,
                base_branch=selected_base_branch,
                branch=selected_review_branch,
                force=force,
            )
            if check:
                return JSONResponse({"ok": True})
            payload = service.build_diff(
                left=selected_left,
                right=selected_right,
                base_branch=selected_base_branch,
                branch=selected_review_branch,
            )
        except TextDiffError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "can_force": True,
                },
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            LOGGER.exception("Diff request crashed: %s", exc)
            return JSONResponse(
                {"error": "Internal server error."},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        return JSONResponse(payload)

    @app.get("/api/diff-stream", response_model=None)
    def serve_diff_stream(
        mode: str | None = None,
        left: str | None = None,
        right: str | None = None,
        base_branch: str | None = None,
        review_branch: str | None = None,
        force: bool = False,
    ) -> Any:
        selected_mode = pick_or(mode, defaults["mode"])
        selected_left = pick_or(left, defaults["left"])
        selected_right = pick_or(right, defaults["right"])
        selected_base_branch, selected_review_branch = selected_branches(
            defaults,
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )
        try:
            _enforce_large_diff_limit(
                service=service,
                defaults=defaults,
                mode=selected_mode,
                left=selected_left,
                right=selected_right,
                base_branch=selected_base_branch,
                branch=selected_review_branch,
                force=force,
            )
            initial_payload, progress_iter, label_overrides = _build_stream_payload(
                service=service,
                defaults=defaults,
                mode=selected_mode,
                left=selected_left,
                right=selected_right,
                base_branch=selected_base_branch,
                branch=selected_review_branch,
            )
        except TextDiffError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "can_force": True,
                },
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
            try:
                for progress in progress_iter:
                    entry = dict(progress.entry)
                    if label_overrides[0]:
                        entry["left_label"] = label_overrides[0]
                    if label_overrides[1]:
                        entry["right_label"] = label_overrides[1]
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

            yield _encode_sse("done", {"summary": initial_payload["summary"]})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
        )

    @app.get("/api/file-diff")
    def serve_file_diff(
        mode: str | None = None,
        left: str | None = None,
        right: str | None = None,
        base_branch: str | None = None,
        review_branch: str | None = None,
        left_path: str | None = None,
        right_path: str | None = None,
        display_name: str | None = None,
        change_type: str = "modify",
    ) -> JSONResponse:
        selected_mode = pick_or(mode, defaults["mode"])
        selected_left = pick_or(left, defaults["left"])
        selected_right = pick_or(right, defaults["right"])
        selected_base_branch, selected_review_branch = selected_branches(
            defaults,
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )

        try:
            if (
                selected_mode == "branch-review"
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
                    left=selected_left,
                    right=selected_right,
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

        return JSONResponse(payload)

    @app.get("/api/notebook-section")
    def serve_notebook_section(
        mode: str | None = None,
        left: str | None = None,
        right: str | None = None,
        base_branch: str | None = None,
        review_branch: str | None = None,
        section: str | None = None,
        cell_key: str | None = None,
        left_path: str | None = None,
        right_path: str | None = None,
    ) -> JSONResponse:
        selected_mode = pick_or(mode, defaults["mode"])
        selected_left = pick_or(left, defaults["left"])
        selected_right = pick_or(right, defaults["right"])
        selected_base_branch, selected_review_branch = selected_branches(
            defaults,
            mode=mode,
            base_branch=base_branch,
            review_branch=review_branch,
        )

        try:
            if (
                selected_mode == "branch-review"
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
                    left=selected_left,
                    right=selected_right,
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

        return JSONResponse(payload)

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


def _enforce_large_diff_limit(
    *,
    service: TextDiffService,
    defaults: Mapping[str, Any],
    mode: str | None,
    left: str | None,
    right: str | None,
    base_branch: str | None,
    branch: str | None,
    force: bool,
) -> None:
    if force:
        return

    changed_files = _count_changed_files(
        service=service,
        defaults=defaults,
        mode=mode,
        left=left,
        right=right,
        base_branch=base_branch,
        branch=branch,
    )
    if changed_files <= LARGE_DIFF_FILE_LIMIT:
        return

    raise TextDiffError(
        f"This diff has {changed_files} changed files. Showing everything may freeze the page."
    )


def _count_changed_files(
    *,
    service: TextDiffService,
    defaults: Mapping[str, Any],
    mode: str | None,
    left: str | None,
    right: str | None,
    base_branch: str | None,
    branch: str | None,
) -> int:
    if mode == "refs":
        return 0

    if mode == "branch-review" and branch and branch.strip():
        _, merge_base, normalized_branch = _resolve_branch_review_refs(
            service=service,
            base_branch=base_branch,
            branch=branch,
        )
        return len(
            service.list_repo_diff_paths(
                left=merge_base,
                right=normalized_branch,
            )
        )

    left = service.normalize_side(left or defaults["left"])
    right = service.normalize_side(right or defaults["right"])
    return len(service.list_repo_diff_paths(left=left, right=right))
