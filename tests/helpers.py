"""Shared test adapters around the production diff pipeline.

Tests may use this module to assemble backend, engine, notebook, and rendering
objects without reintroducing retired production service classes.  Helpers here
must stay thin adapters over public dirdiff contracts, so behavior assertions
remain in the calling tests and not hidden behind convenience fixtures.
"""

from __future__ import annotations

from typing import Any, Literal

from dirdiff.backend import (
    BranchSelection,
    RefChoices,
    WorkspaceBackendProtocol,
    build_repo_manifest_for_backend,
    display_name_for_repo_paths,
    file_kind_for_change_type,
    load_diff_sides,
)
from dirdiff.engines import GitDiffEngine, TextDiffEngine
from dirdiff.engines.contract import DiffEngineProtocol, DiffSide
from dirdiff.notebooks import (
    build_notebook_diff_payload,
    build_notebook_section_payload,
    normalize_notebook_document,
)
from dirdiff.rendering import (
    default_expanded_for_payload,
    enrich_rows_for_display,
)

__all__ = [
    "GitDiffService",
    "TextDiffService",
    "WorkspaceDiffServiceAdapter",
    "build_loaded_diff",
    "build_workspace_file_payload",
]


def build_loaded_diff(
    *,
    display_name: str,
    mode: str,
    left_label: str,
    right_label: str,
    left_exists: bool,
    right_exists: bool,
    left_text: str | None,
    right_text: str | None,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> dict[str, Any]:
    """Build the text/notebook payload shape expected by legacy logic tests.

    The production helper with this name was removed when rendering was split
    into backend loading, engine rendering, notebook handling, and display
    enrichment.  Tests still need a compact way to exercise that combined
    behavior without reintroducing the old production service surface, so this
    helper wires the new public pieces together locally.
    """

    renderer = TextDiffEngine()
    notebook_payload = build_notebook_diff_payload(
        renderer=renderer,
        display_name=display_name,
        mode=mode,
        left_label=left_label,
        right_label=right_label,
        left_exists=left_exists,
        right_exists=right_exists,
        left_text=left_text,
        right_text=right_text,
    )
    if notebook_payload is not None:
        return notebook_payload

    rendered = renderer.render_diff(
        old=DiffSide(
            exists=left_exists,
            text=left_text,
            path_hint=left_path_hint,
        ),
        new=DiffSide(
            exists=right_exists,
            text=right_text,
            path_hint=right_path_hint,
        ),
    )
    left_text_value = "" if left_text is None else left_text
    right_text_value = "" if right_text is None else right_text
    display = enrich_rows_for_display(
        rows=[dict(row) for row in rendered["rows"]],
        left_text=left_text_value,
        right_text=right_text_value,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    payload: dict[str, Any] = {
        "display_name": display_name,
        "mode": mode,
        "left_label": left_label,
        "right_label": right_label,
        "summary": {
            **rendered["summary"],
            "left_exists": left_exists,
            "right_exists": right_exists,
        },
        "rows": display["rows"],
        "default_expanded": False,
    }
    if "render_mode" in display:
        payload["render_mode"] = display["render_mode"]
    if "truncated_rows" in display:
        payload["truncated_rows"] = display["truncated_rows"]
    if "fold_hints" in display:
        payload["fold_hints"] = display["fold_hints"]
    payload["default_expanded"] = default_expanded_for_payload(payload)
    return payload


def build_workspace_file_payload(
    *,
    backend: WorkspaceBackendProtocol,
    renderer: DiffEngineProtocol,
    left_path: str | None,
    right_path: str | None,
    left: str,
    right: str,
    display_name: str | None = None,
    change_type: Literal[
        "modify", "add", "delete", "rename", "copy"
    ] = "modify",
    file_kind: Literal["git", "untracked"] = "git",
) -> dict[str, Any]:
    context = load_diff_sides(
        backend=backend,
        left_path=left_path,
        right_path=right_path,
        left=left,
        right=right,
    )
    left_version = context["left_version"]
    right_version = context["right_version"]
    if display_name is None:
        resolved_display_name = display_name_for_repo_paths(
            context["left_path"],
            context["right_path"],
        )
    else:
        resolved_display_name = display_name
    notebook_payload = build_notebook_diff_payload(
        renderer=renderer,
        display_name=resolved_display_name,
        mode="git",
        left_label=context["left_label"],
        right_label=context["right_label"],
        left_exists=left_version.exists,
        right_exists=right_version.exists,
        left_text=left_version.text,
        right_text=right_version.text,
    )
    if notebook_payload is not None:
        notebook_payload["left_path"] = context["left_path"]
        notebook_payload["right_path"] = context["right_path"]
        if file_kind == "untracked":
            notebook_payload["file_kind"] = {"type": "untracked"}
        else:
            notebook_payload["file_kind"] = file_kind_for_change_type(
                change_type,
                file_kind="git",
            )
        return notebook_payload

    rendered = renderer.render_diff(
        old=DiffSide(
            exists=left_version.exists,
            text=left_version.text,
            path_hint=context["left_path"],
        ),
        new=DiffSide(
            exists=right_version.exists,
            text=right_version.text,
            path_hint=context["right_path"],
        ),
    )
    left_text_value = "" if left_version.text is None else left_version.text
    right_text_value = "" if right_version.text is None else right_version.text
    display = enrich_rows_for_display(
        rows=[dict(row) for row in rendered["rows"]],
        left_text=left_text_value,
        right_text=right_text_value,
        left_path_hint=context["left_path"],
        right_path_hint=context["right_path"],
    )
    payload: dict[str, Any] = {
        "display_name": resolved_display_name,
        "mode": "git",
        "left_label": context["left_label"],
        "right_label": context["right_label"],
        "rows": display["rows"],
        "summary": {
            **rendered["summary"],
            "left_exists": left_version.exists,
            "right_exists": right_version.exists,
        },
        "default_expanded": False,
        "left_path": context["left_path"],
        "right_path": context["right_path"],
        "file_kind": (
            {"type": "untracked"}
            if file_kind == "untracked"
            else file_kind_for_change_type(change_type, file_kind="git")
        ),
    }
    if "engine_warning" in rendered:
        payload["engine_warning"] = rendered["engine_warning"]
    if "render_mode" in display:
        payload["render_mode"] = display["render_mode"]
    if "truncated_rows" in display:
        payload["truncated_rows"] = display["truncated_rows"]
    if "fold_hints" in display:
        payload["fold_hints"] = display["fold_hints"]
    payload["default_expanded"] = default_expanded_for_payload(payload)
    return payload


class WorkspaceDiffServiceAdapter:
    def __init__(
        self,
        backend: WorkspaceBackendProtocol,
        renderer: DiffEngineProtocol,
    ) -> None:
        self.backend = backend
        self.renderer = renderer

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: Literal[
            "modify", "add", "delete", "rename", "copy"
        ] = "modify",
        file_kind: Literal["git", "untracked"] = "git",
    ) -> dict[str, Any]:
        return build_workspace_file_payload(
            backend=self.backend,
            renderer=self.renderer,
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
            display_name=display_name,
            change_type=change_type,
            file_kind=file_kind,
        )

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_repo_manifest_for_backend(
            self.backend,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def list_repo_diff_paths(self, *, left: str, right: str) -> Any:
        return self.backend.list_repo_diff_paths(left=left, right=right)

    def list_ref_choices(self) -> RefChoices:
        return self.backend.list_ref_choices()

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str]:
        return self.backend.resolve_branch_diff_sides(
            base_selection=base_selection,
            review_selection=review_selection,
        )

    def normalize_side(self, raw_side: str) -> str:
        return self.backend.normalize_side(raw_side)

    def build_notebook_section_diff(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        section: str | None,
        cell_key: str | None = None,
    ) -> dict[str, Any]:
        context = load_diff_sides(
            backend=self.backend,
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
        )
        left_version = context["left_version"]
        right_version = context["right_version"]
        left_notebook = (
            normalize_notebook_document(left_version.text)
            if left_version.exists and left_version.text is not None
            else None
        )
        right_notebook = (
            normalize_notebook_document(right_version.text)
            if right_version.exists and right_version.text is not None
            else None
        )
        return build_notebook_section_payload(
            left_notebook=left_notebook,
            right_notebook=right_notebook,
            left_label=context["left_label"],
            right_label=context["right_label"],
            section=section,
            cell_key=cell_key,
        )


class TextDiffService(WorkspaceDiffServiceAdapter):
    def __init__(self, backend: WorkspaceBackendProtocol) -> None:
        super().__init__(backend, TextDiffEngine())


class GitDiffService(WorkspaceDiffServiceAdapter):
    def __init__(self, backend: WorkspaceBackendProtocol) -> None:
        super().__init__(backend, GitDiffEngine())
