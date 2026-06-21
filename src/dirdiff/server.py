"""FastAPI wiring and request-level diff orchestration.

The server owns REST concerns: resolving modes, refs, presets, repository
marks, and response validation.  Diff engines render already-loaded text; they
do not decide whether a file is a notebook.  For ``.ipynb`` paths, this module
loads the two file versions through the selected ``WorkspaceBackend`` and calls
the public notebook payload builders before falling back to the selected text
engine.

Keeping notebook routing here preserves the REST API while preventing concrete
engines from depending on notebook internals.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from dirdiff.db.base import open_sqlite_engine
from dirdiff.db.preferences import PreferencesStore
from dirdiff.db.repo_registry import RepoMarkStore
from dirdiff.db.user_profile import UserProfileStore
from dirdiff.engines import (
    DiffEngineProtocol,
    DifftasticDiffEngine,
    GitDiffEngine,
    GumTreeDiffEngine,
    TextDiffEngine,
)
from dirdiff.engines.contract import (
    DiffRow,
    DiffSide,
    DiffSummary,
    EngineWarning,
    FoldHint,
)
from dirdiff.notebooks import (
    build_notebook_diff_payload,
    build_notebook_section_payload,
    normalize_notebook_document,
)
from dirdiff.rendering import (
    default_expanded_for_payload,
    enrich_rows_for_display,
)
from dirdiff.sources import (
    GitBackend,
    PresetBackend,
    TextDiffError,
    WorkspaceBackend,
    build_lazy_info_for_backend,
    build_repo_manifest_for_backend,
    display_name_for_repo_paths,
    file_kind_for_change_type,
    load_diff_sides,
)

LOGGER = logging.getLogger(__name__)

RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"


@dataclass(frozen=True)
class RuntimeConfig:
    """Server startup configuration passed across the uvicorn factory boundary.

    The CLI creates this value before starting uvicorn.  ``run_uvicorn``
    serializes it into ``RUNTIME_CONFIG_ENV`` because uvicorn imports the app
    factory in a fresh module-loading path, especially when reload is enabled.
    The server owns the shape because ``uvicorn_entrypoint`` is the only place
    that consumes the serialized payload.
    """

    db_path: str
    """
    SQLite database path used for repo marks, preferences, and user profile data.

    The CLI resolves this to an absolute-ish string before launching uvicorn so
    reload workers do not need to know how command-line defaults were chosen.
    """

    mode: Literal["head", "refs", "branch-review"] = "head"
    """
    Initial comparison mode encoded into the browser URL.

    This is startup navigation state, not a server-wide restriction; the API can
    still serve other modes after the frontend is running.
    """

    left: str = "head"
    """
    Left ref or side name for ``refs`` startup mode.
    """

    right: str = "worktree"
    """
    Right ref or side name for ``refs`` startup mode.
    """

    base_branch: str | None = None
    """
    Base branch for ``branch-review`` startup mode.
    """

    review_branch: str | None = None
    """
    Review branch for ``branch-review`` startup mode.
    """

    presets_root: str | None = None
    """
    Optional preset root supplied by the CLI for local fixture browsing.
    """


ModeParam = Literal[
    "files", "staged", "head", "refs", "branch-review", "preset"
]
EngineParam = Literal["dirdiff", "git", "difftastic", "gumtree"]
PresetTypeParam = Literal["diff", "fold", "gumtree"]
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
LazyReason = (
    Literal["too_big", "generated", "deleted", "untracked", "pure_renamed"]
    | None
)


class DiffPayloadSummary(DiffSummary):
    """API summary sent to the frontend for a text-file payload.

    Engines return count-only summaries.  The source layer returns loaded side
    data, so the server adds side-existence flags while assembling the HTTP
    response payload.
    """

    left_exists: bool
    """
    Whether the loaded old/left side exists.
    """

    right_exists: bool
    """
    Whether the loaded new/right side exists.
    """


class DiffPayload(TypedDict):
    """Text-file response payload assembled at the API boundary.

    This is intentionally wider than ``DiffEngineResult``.  It carries the
    rendered engine core plus request/UI metadata: the file display name, the
    source mode selected by the API route, human-facing side labels, file-kind
    metadata, and the normalized paths used to load each side.  Engines should
    not construct this type directly.
    """

    summary: DiffPayloadSummary
    """
    Count summary plus loaded-side existence flags.
    """

    rows: list[DiffRow]
    """
    Display/API rows after engine rows have been syntax/fold enriched.
    """

    default_expanded: bool
    """
    Whether the frontend should initially expand this file.
    """

    display_name: str
    """
    Human-facing file display name chosen by the API/source layer.
    """

    mode: str
    """
    Source mode reported to the frontend for this payload.
    """

    left_label: str
    """
    Human-facing label for the old/left side.
    """

    right_label: str
    """
    Human-facing label for the new/right side.
    """

    render_mode: NotRequired[Literal["plain"]]
    """
    Present when display enrichment had to fall back to plain rendering.
    """

    truncated_rows: NotRequired[int]
    """
    Number of rows elided from a large plain render.
    """

    fold_hints: NotRequired[list[FoldHint]]
    """
    Syntax-aware fold hints produced during display enrichment.
    """

    engine_warning: NotRequired[EngineWarning]
    """
    Optional warning supplied by the selected diff engine.
    """

    file_kind: NotRequired[dict[str, str]]
    """
    Repository file-kind metadata attached by the API/source layer.
    """

    left_path: NotRequired[str | None]
    """
    Normalized old/left source path used to load this payload.
    """

    right_path: NotRequired[str | None]
    """
    Normalized new/right source path used to load this payload.
    """


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
    gumtree: PresetCatalogResponse


class SyntaxSpanResponse(ApiModel):
    start: int
    end: int
    classes: list[str]


class InlineTokenResponse(ApiModel):
    text: str
    is_ws: bool
    status: Literal["unchanged", "replace", "insert", "delete", "move"]


class FoldHintResponse(ApiModel):
    start_row: int
    end_row: int
    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    label: str


class DiffRowResponse(ApiModel):
    status: Literal[
        "equal", "replace", "insert", "delete", "move", "fold", "elided"
    ]
    """
    Display row status.

    TODO: part of fold micro-optimisation, investigate for removal.

    ``fold`` is a client-expandable placeholder for hidden rows that are still
    included in ``foldedRows``.  ``elided`` is a non-expandable placeholder for
    rows omitted from a large plain render.  These synthetic row statuses should
    probably be removed from the core diff row shape.
    """

    left_no: int | None = None
    right_no: int | None = None
    left_text: str | None = None
    right_text: str | None = None
    left_tokens: list[InlineTokenResponse] = Field(default_factory=list)
    right_tokens: list[InlineTokenResponse] = Field(default_factory=list)
    left_syntax: list[SyntaxSpanResponse] = Field(default_factory=list)
    right_syntax: list[SyntaxSpanResponse] = Field(default_factory=list)
    count: int | None = None
    """
    Number of hidden rows represented by a synthetic fold/elided row.
    """

    foldedRows: list[DiffRowResponse] = Field(default_factory=list)
    """
    Hidden rows kept in the payload for fold mode.
    """

    label: str | None = None
    """
    Label displayed during fold/elided rendering.
    """


class DiffSummaryResponse(ApiModel):
    changed_lines: int
    modified_lines: int
    added_lines: int
    removed_lines: int
    moved_lines: int = 0
    left_exists: bool
    right_exists: bool


class NotebookDiffSummaryResponse(DiffSummaryResponse):
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
    moved_lines: int = 0
    skipped_files: int
    changed_cells: int | None = None
    added_cells: int | None = None
    removed_cells: int | None = None
    modified_cells: int | None = None


class GitFileKindResponse(ApiModel):
    type: Literal["git"]
    status: Literal["modified", "added", "deleted", "renamed", "copied"]


class UntrackedFileKindResponse(ApiModel):
    type: Literal["untracked"]


FileKindResponse = GitFileKindResponse | UntrackedFileKindResponse


class EngineWarningResponse(ApiModel):
    type: Literal[
        "difftastic_graph_limit",
        "difftastic_empty_rows",
        "gumtree_invalid_json",
    ]
    message: str


class TextFileDiffResponse(ApiModel):
    display_name: str
    mode: Literal["git"]
    left_label: str
    right_label: str
    summary: DiffSummaryResponse
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
    # This response must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    display_name: str
    summary: DiffSummaryResponse
    lazy: LazyReason = None


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
    """Return branch-review selections only when the active mode uses them.

    The UI can keep base/review branch controls populated while the user moves
    between modes.  API handlers call this helper so branch-review parameters
    do not accidentally influence normal file, ref, or preset comparisons.
    """
    if mode != "branch-review":
        return None, None

    return base_branch, review_branch


def service_for_engine(
    engine: EngineParam,
    *,
    cwd: Path,
) -> DiffEngineProtocol:
    """Return the renderer selected by the request.

    The returned service does not own workspace state.  ``cwd`` is passed only
    to GumTree so it can discover the executable relative to the active
    workspace.
    """
    if engine == "dirdiff":
        return TextDiffEngine()
    if engine == "git":
        return GitDiffEngine()
    if engine == "difftastic":
        return DifftasticDiffEngine()
    if engine == "gumtree":
        return GumTreeDiffEngine(cwd=cwd)
    raise TextDiffError(f"Unknown diff engine: {engine}")


def create_app(
    db: RepoMarkStore,
    user_profile_store: UserProfileStore | None = None,
    preferences_store: PreferencesStore | None = None,
    *,
    presets_root: str | None = None,
) -> FastAPI:
    """Create the dirdiff FastAPI app and wire request orchestration.

    The app layer owns HTTP validation, database-backed repo marks, preset
    catalog selection, notebook detection, and response-model validation.  It
    constructs a backend for each request, optionally builds notebook payloads
    at the API boundary, and otherwise delegates already-loaded text rendering
    to the selected diff engine.
    """
    if user_profile_store is None:
        user_profile_store = UserProfileStore(db.engine)
    if preferences_store is None:
        preferences_store = PreferencesStore(db.engine)

    app = FastAPI()

    def preset_backend_for_type(preset_type: PresetTypeParam) -> PresetBackend:
        """Return the preset backend for the selected preset catalog.

        Diff, fold, and GumTree presets live in separate catalogs because they
        exercise different product surfaces.  Keeping that split here means
        ``/api/presets`` can expose all catalogs without asking any rendering
        engine to know about fixture layout.
        """
        if preset_type == "diff":
            if presets_root is not None:
                return PresetBackend.discover(presets_root=Path(presets_root))
            return PresetBackend.discover()
        if preset_type == "fold":
            return PresetBackend.discover(
                presets_root=Path.cwd() / "tests" / "presets" / "folds"
            )
        return PresetBackend.discover(
            presets_root=Path.cwd() / "tests" / "presets" / "gumtree"
        )

    def preset_catalog_for_type(
        preset_type: PresetTypeParam,
    ) -> PresetCatalogResponse:
        """Return the response model for one preset catalog.

        This helper converts the backend's catalog discovery result into the
        strict API response type.  It does not choose a diff engine; preset
        catalog selection and engine selection are independent UI controls.
        """
        preset_backend = preset_backend_for_type(preset_type)
        return PresetCatalogResponse.model_validate(
            {
                "default_preset": preset_backend.default_preset_name(),
                "groups": preset_backend.list_preset_groups(),
            }
        )

    def backend_for_request(
        *,
        mode: ModeParam,
        repo_id: int,
        preset_type: PresetTypeParam | None,
    ) -> WorkspaceBackend:
        """Return the backend that owns file/ref loading for this request.

        Preset mode uses a ``PresetBackend`` rooted at the selected preset
        catalog.  Repo-backed modes look up the marked repository and create a
        ``GitBackend``.  The returned backend is intentionally separate from
        the diff engine: backend objects load data, engines render loaded
        data.

        Keeping this helper separate from renderer construction is what lets
        notebook routing happen without involving any concrete diff engine.
        The server can load file versions from the backend, decide whether the
        notebook payload path applies, and only then ask an engine renderer to
        handle ordinary text.
        """
        if mode == "preset":
            if preset_type is None:
                raise TextDiffError("preset_type is required for preset mode.")
            return preset_backend_for_type(preset_type)

        mark = db.get(repo_id)
        if mark is None:
            raise TextDiffError(f"Invalid repo_id: {repo_id}")
        return GitBackend.discover(repo_root=Path(mark.path))

    def looks_like_notebook_path(path: str | None) -> bool:
        """Return whether a repo path should be routed through notebook logic."""
        return path is not None and path.endswith(".ipynb")

    def build_text_file_payload(
        *,
        backend: WorkspaceBackend,
        renderer: DiffEngineProtocol,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None,
        change_type: ChangeType,
        file_kind: Literal["git", "untracked"],
    ) -> dict[str, Any]:
        """Load one backend path pair and render it with the selected engine.

        Loading and source metadata live here, at the API/source boundary.
        The renderer receives only already-loaded text, existence flags, and
        path hints; this function wraps the rendered core with HTTP metadata.
        """
        context = load_diff_sides(
            backend=backend,
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
        )
        left_version = context["left_version"]
        right_version = context["right_version"]
        resolved_display_name = display_name
        if resolved_display_name is None:
            resolved_display_name = display_name_for_repo_paths(
                context["left_path"],
                context["right_path"],
            )
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
        right_text_value = (
            "" if right_version.text is None else right_version.text
        )
        display = enrich_rows_for_display(
            rows=[dict(row) for row in rendered["rows"]],
            left_text=left_text_value,
            right_text=right_text_value,
            left_path_hint=context["left_path"],
            right_path_hint=context["right_path"],
        )
        payload: DiffPayload = {
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
        }
        if "engine_warning" in rendered:
            payload["engine_warning"] = rendered["engine_warning"]
        if "render_mode" in display:
            payload["render_mode"] = display["render_mode"]
        if "truncated_rows" in display:
            payload["truncated_rows"] = display["truncated_rows"]
        if "fold_hints" in display:
            payload["fold_hints"] = display["fold_hints"]
        payload["default_expanded"] = default_expanded_for_payload(
            dict(payload)
        )
        payload["file_kind"] = file_kind_for_change_type(
            change_type,
            file_kind=file_kind,
        )
        payload["left_path"] = context["left_path"]
        payload["right_path"] = context["right_path"]
        return dict(payload)

    def build_notebook_file_payload_if_applicable(
        *,
        backend: WorkspaceBackend,
        renderer: DiffEngineProtocol,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None,
        change_type: ChangeType,
        file_kind: Literal["git", "untracked"],
    ) -> dict[str, Any] | None:
        """Return a notebook file payload when the request targets a notebook.

        This is the boundary that keeps engines notebook-agnostic.  The server
        inspects paths, loads file text through the backend, and asks
        ``notebooks.py`` to build the ``render_kind: "notebook"`` payload.  If a
        path looks like a notebook but parsing fails, ``None`` is returned so
        the selected diff engine can render the file as plain text, matching the
        previous fallback behavior.

        The returned payload is shaped exactly like the existing
        ``NotebookFileDiffResponse`` branch of ``/api/file-diff``.  The REST API
        therefore does not change: the only difference is that the notebook
        decision is made before engine rendering instead of inside each service.
        """
        left_is_notebook = looks_like_notebook_path(left_path)
        right_is_notebook = looks_like_notebook_path(right_path)
        if not left_is_notebook and not right_is_notebook:
            return None

        context = load_diff_sides(
            backend=backend,
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
        )
        left_version = context["left_version"]
        right_version = context["right_version"]
        resolved_display_name = display_name
        if resolved_display_name is None:
            resolved_display_name = display_name_for_repo_paths(
                context["left_path"],
                context["right_path"],
            )
        payload = build_notebook_diff_payload(
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
        if payload is None:
            return None
        if file_kind == "untracked":
            payload["file_kind"] = {"type": "untracked"}
        else:
            status_by_change_type = {
                "modify": "modified",
                "add": "added",
                "delete": "deleted",
                "rename": "renamed",
                "copy": "copied",
            }
            payload["file_kind"] = {
                "type": "git",
                "status": status_by_change_type[change_type],
            }
        payload["left_path"] = context["left_path"]
        payload["right_path"] = context["right_path"]
        return payload

    def build_notebook_section_for_request(
        *,
        backend: WorkspaceBackend,
        renderer: DiffEngineProtocol,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        section: str | None,
        cell_key: str | None,
    ) -> dict[str, Any]:
        """Load notebook files and build one lazy section payload.

        ``/api/notebook-section`` uses the same selected engine as the top-level
        notebook payload.  Notebook parsing and section selection stay in the
        REST orchestration layer; the text-like section body is rendered only
        through ``DiffEngineProtocol``.

        Unlike top-level notebook file rendering, invalid notebook JSON is an
        error here.  A section request can only be made after the frontend has
        seen a notebook payload with a section key, so failure to parse either
        side means the lazy section cannot be fulfilled honestly.
        """
        context = load_diff_sides(
            backend=backend,
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
        )
        left_version = context["left_version"]
        right_version = context["right_version"]
        left_notebook = None
        if left_version.exists:
            if left_version.text is None:
                raise TextDiffError(
                    f"Could not load notebook on {context['left_label']}."
                )
            left_notebook = normalize_notebook_document(left_version.text)

        right_notebook = None
        if right_version.exists:
            if right_version.text is None:
                raise TextDiffError(
                    f"Could not load notebook on {context['right_label']}."
                )
            right_notebook = normalize_notebook_document(right_version.text)
        if left_version.exists and left_notebook is None:
            raise TextDiffError(
                f"Could not parse notebook on {context['left_label']}."
            )
        if right_version.exists and right_notebook is None:
            raise TextDiffError(
                f"Could not parse notebook on {context['right_label']}."
            )
        return build_notebook_section_payload(
            renderer=renderer,
            left_notebook=left_notebook,
            right_notebook=right_notebook,
            left_label=context["left_label"],
            right_label=context["right_label"],
            section=section,
            cell_key=cell_key,
        )

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
        default_base_branch = backend.default_base_branch()
        preferred_review_branch = backend.preferred_review_branch(
            base_branch=default_base_branch
        )
        return {
            "default_base_branch": default_base_branch,
            "preferred_review_branch": preferred_review_branch,
            "ref_choices": backend.list_ref_choices(),
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
                    "gumtree": preset_catalog_for_type("gumtree"),
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
            backend = backend_for_request(
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = backend.normalize_side(preset)
                payload = build_repo_manifest_for_backend(
                    backend,
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
                        backend=backend,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                left_label = (
                    f"{resolved_base_branch.strip()}...{normalized_branch}"
                )
                payload = build_repo_manifest_for_backend(
                    backend,
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
                normalized_left = backend.normalize_side(left)
                normalized_right = backend.normalize_side(right)
                payload = build_repo_manifest_for_backend(
                    backend,
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
            backend = backend_for_request(
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = backend.normalize_side(preset)
                payload = build_lazy_info_for_backend(
                    backend,
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
                    backend=backend,
                    base_branch=selected_base_branch,
                    branch=selected_review_branch,
                )
                payload = build_lazy_info_for_backend(
                    backend,
                    left=merge_base,
                    right=normalized_branch,
                    show_untracked=False,
                )
            else:
                if left is None or not left.strip():
                    raise TextDiffError("left is required for this diff mode.")
                if right is None or not right.strip():
                    raise TextDiffError("right is required for this diff mode.")
                normalized_left = backend.normalize_side(left)
                normalized_right = backend.normalize_side(right)
                payload = build_lazy_info_for_backend(
                    backend,
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
            backend = backend_for_request(
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            renderer = service_for_engine(engine, cwd=backend.cwd)
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = backend.normalize_side(preset)
                payload = build_notebook_file_payload_if_applicable(
                    backend=backend,
                    renderer=renderer,
                    left_path=left_path,
                    right_path=right_path,
                    left=preset_name,
                    right="new",
                    display_name=display_name,
                    change_type=change_type,
                    file_kind=file_kind,
                )
                if payload is None:
                    payload = build_text_file_payload(
                        backend=backend,
                        renderer=renderer,
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
                        backend=backend,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                left_label = (
                    f"{resolved_base_branch.strip()}...{normalized_branch}"
                )
                payload = build_notebook_file_payload_if_applicable(
                    backend=backend,
                    renderer=renderer,
                    left_path=left_path,
                    right_path=right_path,
                    left=merge_base,
                    right=normalized_branch,
                    display_name=display_name,
                    change_type=change_type,
                    file_kind=file_kind,
                )
                if payload is None:
                    payload = build_text_file_payload(
                        backend=backend,
                        renderer=renderer,
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
                payload = build_notebook_file_payload_if_applicable(
                    backend=backend,
                    renderer=renderer,
                    left_path=left_path,
                    right_path=right_path,
                    left=left,
                    right=right,
                    display_name=display_name,
                    change_type=change_type,
                    file_kind=file_kind,
                )
                if payload is None:
                    payload = build_text_file_payload(
                        backend=backend,
                        renderer=renderer,
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
            backend = backend_for_request(
                mode=mode,
                repo_id=repo_id,
                preset_type=preset_type,
            )
            renderer = service_for_engine(engine, cwd=backend.cwd)
            if mode == "preset":
                if preset is None or not preset.strip():
                    raise TextDiffError("preset is required for preset mode.")
                preset_name = backend.normalize_side(preset)
                payload = build_notebook_section_for_request(
                    backend=backend,
                    renderer=renderer,
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
                        backend=backend,
                        base_branch=selected_base_branch,
                        branch=selected_review_branch,
                    )
                )
                payload = build_notebook_section_for_request(
                    backend=backend,
                    renderer=renderer,
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
                payload = build_notebook_section_for_request(
                    backend=backend,
                    renderer=renderer,
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
    backend: WorkspaceBackend,
    base_branch: str | None,
    branch: str,
) -> tuple[str, str, str]:
    resolved_base_branch = base_branch or backend.default_base_branch()
    merge_base, normalized_branch = backend.resolve_branch_diff_sides(
        base_branch=resolved_base_branch,
        branch=branch,
    )
    return resolved_base_branch, merge_base, normalized_branch
