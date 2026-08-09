"""FastAPI wiring and request-level diff rendering.

The server validates HTTP inputs and constructs concrete workspace backends for
manifest. `RoomLord.corresponding_room` applies the Tab law there, while
follow-up routes recover a Room directly from the returned Snapshot key. Room
methods expose captured Paths and metadata; private capture stores never cross
the application boundary. Diff engines render already-loaded text and do not
decide whether a file is a notebook.
For `.ipynb` paths, this module calls the public notebook payload builders
before using the selected text engine.

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
from typing import Any, Literal, NotRequired, Optional, TypedDict
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dirdiff.backend import (
    BranchSelection,
    BranchSource,
    DefaultBaseSelection,
    GitBackend,
    LazyReason,
    LoadedDiffSides,
    PreparedPullRequest,
    PresetBackend,
    RefChoices,
    RepoDiffPath,
    TextVersion,
    WorkspaceBackendProtocol,
    build_lazy_info_for_paths,
    build_repo_manifest_for_paths,
    decode_text_content,
    display_name_for_repo_paths,
    file_kind_for_change_type,
    prepare_pull_request,
)
from dirdiff.db import (
    PreferencesStore,
    RepoMainBranchRecord,
    RepoMarkStore,
    RoomStore,
    UserProfileStore,
    open_sqlite_engine,
)
from dirdiff.engines import (
    DiffEngineProtocol,
    DiffSide,
    DiffSummary,
    DifftasticDiffEngine,
    DirdiffError,
    EngineWarning,
    GitDiffEngine,
    GumTreeDiffEngine,
    InlineTokenStatus,
    TextDiffEngine,
)
from dirdiff.notebooks import (
    build_notebook_diff_payload,
)
from dirdiff.rendering import (
    DiffRow,
    FoldHint,
    SyntaxClass,
    default_expanded_for_payload,
    enrich_rows_for_display,
)
from dirdiff.room_lord import RoomLord

LOGGER = logging.getLogger(__name__)

RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "RuntimeConfig",
    "branch_selection_request_to_selection",
    "create_app",
    "repo_main_branch_record_to_selection",
    "selected_branch_selections",
    "service_for_engine",
    "uvicorn_entrypoint",
]


@dataclass(frozen=True)
class RuntimeConfig:
    """Server startup configuration passed across the uvicorn factory boundary.

    The CLI creates this value before starting uvicorn.  `run_uvicorn`
    serializes it into `RUNTIME_CONFIG_ENV` because uvicorn imports the app
    factory in a fresh module-loading path, especially when reload is enabled.
    The server owns the shape because `uvicorn_entrypoint` is the only place
    that consumes the serialized payload.
    """

    db_path: str
    """
    SQLite database path used for repo marks, preferences, and user profile data.

    The CLI resolves this to an absolute-ish string before launching uvicorn so
    reload workers do not need to know how command-line defaults were chosen.
    """

    store_path: str
    """
    Directory containing immutable Snapshot files.

    The CLI defaults this to a `store` directory beside `db_path`, while an
    explicit `--store-path` supplies a separate location. Persistent databases
    use a database-adjacent `.room.lock` file so every store root shares one
    publication lock.
    """

    tab: Literal["head", "refs", "branch-review"] = "head"
    """
    Initial Tab encoded into the browser URL.

    This is startup navigation state, not a server-wide restriction; the API can
    still serve other Tabs after the frontend is running.
    """

    left: str = "HEAD"
    """
    Left ref or side name for the Refs startup Tab.
    """

    right: str = "worktree"
    """
    Right ref or side name for the Refs startup Tab.
    """

    base_selection: BranchSelection | None = None
    """
    Base branch selection for the Branch Review startup Tab.

    The CLI writes this structured value into the first browser URL; API
    handlers parse the same local/remote shape from query params afterward.
    """

    review_selection: BranchSelection | None = None
    """
    Review branch selection for the Branch Review startup Tab.

    This is startup navigation state only.  Diff requests still carry their own
    explicit branch-review selections.
    """

    presets_root: str | None = None
    """
    Optional preset root supplied by the CLI for local fixture browsing.
    """


TabParam = Literal[
    "head",
    "refs",
    "branch-review",
    "pull-request",
    "preset",
]
"""One complete HUD Tab discriminator accepted by manifest."""
EngineParam = Literal["dirdiff", "git", "difftastic", "gumtree"]
PresetTypeParam = Literal["diff", "fold", "gumtree", "scroll"]
BranchSourceParam = BranchSource
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
BranchSelections = tuple[BranchSelection | None, BranchSelection | None]


class DiffPayloadSummary(DiffSummary):
    """API summary sent to the frontend for a text-file payload.

    Engines return count-only summaries.  The backend returns loaded side
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

    This is intentionally wider than `DiffEngineResult`.  It carries the
    rendered engine core plus request/UI metadata: the file display name, the
    human-facing side labels, file-kind metadata, and the normalized paths used
    to load each side. Engines should not construct this type directly.
    """

    summary: DiffPayloadSummary
    """
    Count summary plus loaded-side existence flags.
    """

    rows: list[DiffRow]
    """
    Display/API rows after engine rows have been syntax/fold enriched.
    """

    hunk_count: int
    """
    Number of backend-identified hunks in this file diff.
    """

    default_expanded: bool
    """
    Whether the frontend should initially expand this file.
    """

    display_name: str
    """
    Human-facing file display name chosen by the API/backend layer.
    """

    left_label: str
    """
    Human-facing label for the old/left side.
    """

    right_label: str
    """
    Human-facing label for the new/right side.
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
    Repository file-kind metadata attached by the API/backend layer.
    """

    left_path: NotRequired[str | None]
    """
    Normalized old/left backend path used to load this payload.
    """

    right_path: NotRequired[str | None]
    """
    Normalized new/right backend path used to load this payload.
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
    """Request body for saving the repository main branch selection."""

    selection: BranchSelection


class RepoMainBranchResponse(ApiModel):
    """Persisted repository main branch selection."""

    project_id: int
    selection: BranchSelection


class RepoDefaultsResponse(ApiModel):
    """Repository defaults used to seed branch-review controls."""

    default_base_selection: DefaultBaseSelection
    preferred_review_selection: BranchSelection


class RepoRefsResponse(ApiModel):
    """Repository ref choices used for autocomplete and ref selection."""

    ref_choices: RefChoices


class PullRequestPrepareRequest(ApiModel):
    """Request body for preparing a pull request URL for diffing."""

    url: str


class PullRequestPrepareResponse(ApiModel):
    """Prepared Pull Request identity and immutable capture commits."""

    project_id: int
    pull_request_url: str
    left_commit: str
    right_commit: str


def pull_request_prepare_response(
    prepared: PreparedPullRequest,
) -> PullRequestPrepareResponse:
    """Serialize the complete prepared Pull Request for the HTTP API."""

    return PullRequestPrepareResponse.model_validate(
        {
            "project_id": prepared.project_id,
            "pull_request_url": prepared.pull_request_url,
            "left_commit": prepared.left_commit,
            "right_commit": prepared.right_commit,
        }
    )


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
    user_profile_id: int
    aggressive_folds: bool


class PreferencesUpdateRequest(ApiModel):
    aggressive_folds: bool


class PresetGroupResponse(ApiModel):
    id: str
    display_name: str


class PresetCatalogResponse(ApiModel):
    default_preset: str
    groups: list[PresetGroupResponse]


class PresetCatalogsResponse(ApiModel):
    diff: PresetCatalogResponse
    fold: PresetCatalogResponse
    gumtree: PresetCatalogResponse
    scroll: PresetCatalogResponse


class DecoratedPartResponse(ApiModel):
    """One ordered, lossless slice of a rendered line.

    The API returns a complete partition for each present row side. Every part
    carries both the engine's inline diff classification and the rendering
    layer's syntax classes, so clients render the text directly without
    intersecting parallel offset ranges.
    """

    text: str
    syntax_classes: list[SyntaxClass]
    diff_status: InlineTokenStatus
    is_whitespace: bool
    is_leading_whitespace: bool


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
    status: Literal["equal", "replace", "insert", "delete", "move"]
    """
    Display status of one real aligned engine row.
    """

    left_no: int | None
    right_no: int | None
    left_text: str | None
    right_text: str | None
    left_parts: list[DecoratedPartResponse]
    right_parts: list[DecoratedPartResponse]
    hunk_index: int | None
    """
    Zero-based file-local hunk identity on a hunk's first rendered row.

    Other rows carry `None`. The value is assigned by `/api/file-diff` before
    frontend folding and virtualization and is therefore independent of DOM
    layout.
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
    """Validate manifest-wide File totals and optional backend line totals.

    Added and removed line counts are either both backend-reported integers or
    both absent. They need not include additional untracked Files. Cell totals
    remain optional because only notebook-aware payloads can provide them.
    """

    changed_files: int
    added_files: int
    removed_files: int
    updated_files: int
    added_lines: Optional[int]
    removed_lines: Optional[int]
    skipped_files: int
    changed_cells: int | None = None
    added_cells: int | None = None
    removed_cells: int | None = None
    modified_cells: int | None = None

    @model_validator(mode="after")
    def validate_line_count_presence(self) -> RepoDiffSummaryResponse:
        """Reject a response carrying only one aggregate line count."""
        if (self.added_lines is None) != (self.removed_lines is None):
            raise ValueError(
                "added_lines and removed_lines must have equal presence"
            )
        return self


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
    left_label: str
    right_label: str
    summary: DiffSummaryResponse
    rows: list[DiffRowResponse]
    hunk_count: int
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    lazy: LazyReason | None = None
    default_expanded: bool = True
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
    source_hunk_count: int
    source_changed_lines: int
    source_modified_lines: int
    source_added_lines: int
    source_removed_lines: int
    source_moved_lines: int
    source_fold_hints: list[FoldHintResponse] = Field(default_factory=list)
    metadata_changed_lines: int
    metadata_modified_lines: int
    metadata_added_lines: int
    metadata_removed_lines: int
    outputs_changed_lines: int
    outputs_modified_lines: int
    outputs_added_lines: int
    outputs_removed_lines: int


class NotebookFileDiffResponse(ApiModel):
    display_name: str
    render_kind: Literal["notebook"]
    left_label: str
    right_label: str
    summary: NotebookDiffSummaryResponse
    hunk_count: int
    notebook_metadata_changed_lines: int
    cells: list[NotebookCellDiffResponse]
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    default_expanded: bool = True


class RepoFileEntryResponse(ApiModel):
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    lazy: LazyReason | None = None


class RepoManifestFileNodeResponse(ApiModel):
    type: Literal["file"]
    name: str
    entry: RepoFileEntryResponse


class RepoManifestDirectoryNodeResponse(ApiModel):
    type: Literal["directory"]
    name: str
    path: str
    entries: list[RepoManifestTreeEntryResponse]


RepoManifestTreeEntryResponse = (
    RepoManifestFileNodeResponse | RepoManifestDirectoryNodeResponse
)


class LazyInfoFileResponse(ApiModel):
    # This response must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    display_name: str
    changed_lines: int | None = None
    added_lines: int | None = None
    removed_lines: int | None = None
    lazy: LazyReason | None = None


class RepoManifestResponse(ApiModel):
    """Return one complete manifest and its retained Snapshot address.

    `snapshot_id` is exactly 32 lowercase hexadecimal characters and is the
    only Room lookup input accepted by follow-up endpoints.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    display_name: str
    left_label: str
    right_label: str
    summary: RepoDiffSummaryResponse
    tree: list[RepoManifestTreeEntryResponse]


class LazyInfoResponse(ApiModel):
    files: list[LazyInfoFileResponse]


def selected_branch_selections(
    tab: TabParam = Query(description="HUD Tab."),
    base_source: BranchSourceParam | None = Query(
        default=None,
        description="Base branch source for a branch-backed Tab.",
    ),
    base_remote: str | None = Query(
        default=None,
        description="Base remote for remote branch-backed selections.",
    ),
    base_branch: str | None = Query(
        default=None,
        description="Base branch name for a branch-backed Tab.",
    ),
    review_source: BranchSourceParam | None = Query(
        default=None,
        description="Review branch source for a branch-backed Tab.",
    ),
    review_remote: str | None = Query(
        default=None,
        description="Review remote for remote branch-backed selections.",
    ),
    review_branch: str | None = Query(
        default=None,
        description="Review branch name for a branch-backed Tab.",
    ),
) -> BranchSelections:
    """Return structured branch selections for Branch Review.

    The UI can keep base/review branch controls populated while the user moves
    between Tabs. API handlers call this helper so branch parameters do not
    accidentally influence another Tab's manifest.
    """
    if tab != "branch-review":
        return None, None

    return (
        _branch_selection_from_query(
            label="base",
            source=base_source,
            remote=base_remote,
            branch=base_branch,
        ),
        _branch_selection_from_query(
            label="review",
            source=review_source,
            remote=review_remote,
            branch=review_branch,
        ),
    )


def preset_project_parts(
    *,
    project_id: str | None,
    preset_subset: str | None,
) -> tuple[PresetTypeParam, str]:
    """Parse the preset catalog and subset used to prepare a manifest.

    The Preset Tab uses `project_id` as the catalog discriminator (`diff`, `fold`,
    `gumtree`, or `scroll`) and `preset_subset` as the selected group within that
    catalog. Follow-up endpoints find the prepared Room by Snapshot key and do
    not call this parser. The preset backend still validates traversal and
    unknown-group errors while preparing the manifest.
    """
    if project_id is None or project_id.strip() == "":
        raise DirdiffError("project_id is required for the Preset Tab.")
    if preset_subset is None or preset_subset.strip() == "":
        raise DirdiffError("preset_subset is required for the Preset Tab.")
    if project_id == "diff":
        preset_type: PresetTypeParam = "diff"
    elif project_id == "fold":
        preset_type = "fold"
    elif project_id == "gumtree":
        preset_type = "gumtree"
    elif project_id == "scroll":
        preset_type = "scroll"
    else:
        raise DirdiffError(f"Unknown preset project_id: {project_id}")
    return preset_type, preset_subset


def marked_project_id(project_id: str | None) -> int:
    """Parse a positive Mark id from a repo-backed HTTP parameter.

    Manifest uses the result to construct the workspace backend. Follow-up
    operations never call this parser because their Snapshot id is sufficient.
    """
    if project_id is None or project_id.strip() == "":
        raise DirdiffError("project_id is required for repo-backed Tabs.")
    try:
        parsed_project_id = int(project_id)
    except ValueError as exc:
        raise DirdiffError(f"Invalid project_id: {project_id}") from exc
    if parsed_project_id <= 0:
        raise DirdiffError(f"Invalid project_id: {project_id}")
    return parsed_project_id


def _branch_selection_from_query(
    *,
    label: str,
    source: BranchSourceParam | None,
    remote: str | None,
    branch: str | None,
) -> BranchSelection:
    """Parse one split Branch Review selection from query parameters.

    Used only while building a manifest for a branch-backed Tab. Follow-up file
    endpoints use the snapshot id returned by that manifest request.
    """
    if source is None:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_source is required for the Branch Review Tab.",
        )
    if branch is None or (branch_name := branch.strip()) == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_branch is required for the Branch Review Tab.",
        )
    if source == "local":
        return {"source": source, "branch": branch_name}
    if remote is None or (remote_name := remote.strip()) == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_remote is required for remote Branch Review selections.",
        )
    return {
        "source": source,
        "remote": remote_name,
        "branch": branch_name,
    }


def service_for_engine(
    engine: EngineParam,
    *,
    cwd: Path,
) -> DiffEngineProtocol:
    """Return the renderer selected by the request.

    The returned service does not own workspace state.  `cwd` is passed only
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
    raise DirdiffError(f"Unknown diff engine: {engine}")


def create_app(
    db: RepoMarkStore,
    user_profile_store: UserProfileStore | None = None,
    preferences_store: PreferencesStore | None = None,
    *,
    room_lord: RoomLord,
    presets_root: str | None = None,
) -> FastAPI:
    """Create the dirdiff FastAPI app and wire request orchestration.

    The app layer owns HTTP validation, database-backed repo marks, concrete
    backend construction, notebook detection, and response-model validation.
    The caller provides the `RoomLord` that selects prepared Rooms for manifest
    and recovers them by Snapshot key for follow-up operations. Storage and
    capture remain behind that interface. The server delegates already-loaded
    text rendering to the selected diff engine.
    """
    if user_profile_store is None:
        user_profile_store = UserProfileStore(db.engine)
    if preferences_store is None:
        preferences_store = PreferencesStore(db.engine)

    app = FastAPI()

    def preset_backend_for_type(preset_type: PresetTypeParam) -> PresetBackend:
        """Resolve which preset catalog backs a preset request.

        Diff, fold, GumTree, and scroll presets live in separate catalogs because
        they exercise different product surfaces. Keeping that split here means
        `/api/presets` can expose all catalogs without asking any rendering
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
        if preset_type == "gumtree":
            return PresetBackend.discover(
                presets_root=Path.cwd() / "tests" / "presets" / "gumtree"
            )
        return PresetBackend.discover(
            presets_root=Path.cwd() / "tests" / "presets" / "scroll"
        )

    def preset_catalog_for_type(
        preset_type: PresetTypeParam,
    ) -> PresetCatalogResponse:
        """Serialize one preset catalog for the presets endpoint.

        Preset catalog selection and engine selection are independent UI
        controls, so this endpoint exposes catalog metadata without involving a
        renderer.
        """
        preset_backend = preset_backend_for_type(preset_type)
        default_preset = preset_backend.default_preset_name()
        return PresetCatalogResponse.model_validate(
            {
                "default_preset": default_preset,
                "groups": preset_backend.list_preset_groups(),
            }
        )

    def looks_like_notebook_path(path: str | None) -> bool:
        """Return whether a repo path should be routed through notebook logic."""
        return path is not None and path.endswith(".ipynb")

    def build_text_file_payload(
        *,
        renderer: DiffEngineProtocol,
        display_name: str | None,
        change_type: ChangeType,
        file_kind: Literal["git", "untracked"],
        context: LoadedDiffSides,
    ) -> dict[str, Any]:
        """Render an already-loaded text file and add API metadata.

        Backend loading has already happened before this function runs.  That
        keeps the renderer contract narrow: it receives text versions and path
        hints, then the server wraps the rendered core with response fields.
        """
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
            "left_label": context["left_label"],
            "right_label": context["right_label"],
            "rows": display["rows"],
            "hunk_count": display["hunk_count"],
            "summary": {
                **rendered["summary"],
                "left_exists": left_version.exists,
                "right_exists": right_version.exists,
            },
            "default_expanded": False,
        }
        if "engine_warning" in rendered:
            payload["engine_warning"] = rendered["engine_warning"]
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
        renderer: DiffEngineProtocol,
        display_name: str | None,
        change_type: ChangeType,
        file_kind: Literal["git", "untracked"],
        context: LoadedDiffSides,
    ) -> dict[str, Any] | None:
        """Return a notebook file payload when the request targets a notebook.

        This is the boundary that keeps engines notebook-agnostic.  The server
        inspects paths and asks `notebooks.py` to build the
        `render_kind: "notebook"` payload from already captured text. If a path
        looks like a notebook but parsing fails, `None` tells the caller to
        render that text as an ordinary file.

        The returned payload is shaped exactly like the existing
        `NotebookFileDiffResponse` branch of `/api/file-diff`.  The REST API
        therefore does not change: the only difference is that the notebook
        decision is made before engine rendering instead of inside each service.
        """
        left_is_notebook = looks_like_notebook_path(context["left_path"])
        right_is_notebook = looks_like_notebook_path(context["right_path"])
        if not left_is_notebook and not right_is_notebook:
            return None

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

    @app.get("/api/repo-defaults")
    def serve_repo_defaults(
        project_id: int = Query(
            description="Marked project id. Required for repo-backed defaults.",
        ),
    ) -> RepoDefaultsResponse:
        """Return structured defaults for branch-review controls."""
        mark = db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        backend = GitBackend.discover(repo_root=Path(mark.path))
        saved_main_branch = db.get_main_branch(project_id)
        default_base_selection = (
            repo_main_branch_record_to_selection(saved_main_branch)
            if saved_main_branch is not None
            else backend.default_base_selection()
        )
        preferred_review_selection = backend.preferred_review_selection(
            base_selection=default_base_selection
        )
        return RepoDefaultsResponse.model_validate(
            {
                "default_base_selection": default_base_selection,
                "preferred_review_selection": preferred_review_selection,
            }
        )

    @app.get("/api/repo-refs")
    def serve_repo_refs(
        project_id: int = Query(
            description="Marked project id. Required for repo-backed refs.",
        ),
    ) -> RepoRefsResponse:
        """Return ref choices for repo-backed controls."""
        mark = db.get(project_id)
        if mark is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid project_id: {project_id}",
            )
        backend = GitBackend.discover(repo_root=Path(mark.path))
        return RepoRefsResponse.model_validate(
            {"ref_choices": backend.list_ref_choices()}
        )

    @app.post(
        "/api/repos/{project_id}/main-branch",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Save the repository main branch selection",
    )
    def save_repo_main_branch(
        project_id: int,
        request: RepoMainBranchRequest,
    ) -> RepoMainBranchResponse:
        # Future auth belongs here: setting shared repository main remote/branch
        # should be admin-only once dirdiff has real users/permissions.
        mark = db.get(project_id)
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
            record = db.set_main_branch(
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
                    "scroll": preset_catalog_for_type("scroll"),
                }
            )
        except DirdiffError as exc:
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

    @app.delete(
        "/api/repos/{project_id}",
        status_code=HTTPStatus.NO_CONTENT,
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Remove a marked repository",
    )
    def delete_repo_mark(project_id: int) -> None:
        try:
            if not db.delete(project_id):
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

    @app.post(
        "/api/pull-request/prepare",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Prepare immutable repository state for a Pull Request Tab",
    )
    def prepare_pull_request_endpoint(
        request: PullRequestPrepareRequest,
    ) -> PullRequestPrepareResponse:
        try:
            return pull_request_prepare_response(
                prepare_pull_request(
                    url=request.url,
                    repo_marks=db.list(),
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
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Load persisted user preferences",
    )
    def serve_preferences(profile_id: int) -> PreferencesResponse:
        profile = user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            preferences_store.get_or_create(profile_id),
            from_attributes=True,
        )

    @app.patch(
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted user preferences",
    )
    def update_preferences(
        profile_id: int,
        request: PreferencesUpdateRequest,
    ) -> PreferencesResponse:
        profile = user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            preferences_store.set_aggressive_folds(
                profile_id, request.aggressive_folds
            ),
            from_attributes=True,
        )

    @app.get(
        "/api/manifest",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Show the repository state selected by a Tab",
    )
    def serve_manifest(
        project_id: str = Query(
            description="Manifest project id: marked project id for repo-backed Tabs, preset catalog id for Preset.",
        ),
        tab: TabParam = Query(description="HUD Tab."),
        branch_selections: BranchSelections = Depends(
            selected_branch_selections
        ),
        left: str | None = Query(
            default=None, description="Left ref or diff side."
        ),
        right: str | None = Query(
            default=None, description="Right ref or diff side."
        ),
        pull_request_url: str | None = Query(
            default=None,
            description="Pull Request URL used only for Room correspondence.",
        ),
        left_commit: str | None = Query(
            default=None,
            description="Left commit prepared for the Pull Request Tab.",
        ),
        right_commit: str | None = Query(
            default=None,
            description="Right commit prepared for the Pull Request Tab.",
        ),
        preset_subset: str | None = Query(
            default=None,
            description="Preset subset/group id for the Preset Tab.",
        ),
        show_untracked: bool = Query(
            default=False,
            description="Include untracked worktree files when supported by the selected Tab.",
        ),
    ) -> RepoManifestResponse:
        """Show the supplied repository state and provide follow-up keys.

        The handler constructs the concrete backend and supplies the complete
        Tab state to `RoomLord.corresponding_room`. The response contains the
        manifest tree, aggregate backend totals, and the opaque Snapshot key
        required by follow-up endpoints. It does not prepare Pull Requests;
        Pull Request parameters already contain the URL and capture commits.
        """
        try:
            preset_catalog: str | None = None
            preset_name: str | None = None
            parsed_project_id: int | None = None
            if tab == "preset":
                preset_catalog, preset_name = preset_project_parts(
                    project_id=project_id,
                    preset_subset=preset_subset,
                )
                backend: WorkspaceBackendProtocol = preset_backend_for_type(
                    preset_catalog
                )
            else:
                parsed_project_id = marked_project_id(project_id)
                mark = db.get(parsed_project_id)
                if mark is None:
                    raise DirdiffError(
                        f"Invalid project_id: {parsed_project_id}"
                    )
                backend = GitBackend.discover(repo_root=Path(mark.path))
            room, snapshot_id = room_lord.corresponding_room(
                mark_id=parsed_project_id,
                tab=tab,
                backend=backend,
                branch_selections=branch_selections,
                left=left,
                right=right,
                pull_request_url=pull_request_url,
                left_commit=left_commit,
                right_commit=right_commit,
                preset_catalog=preset_catalog,
                preset_subset=preset_name,
                show_untracked=show_untracked,
            )
            snapshot_meta = room.meta(snapshot_id)
            manifest_paths = tuple(
                sorted(
                    (
                        RepoDiffPath(
                            left_path=left_path.as_posix()
                            if left_path is not None
                            else None,
                            right_path=right_path.as_posix()
                            if right_path is not None
                            else None,
                            display_name=(
                                right_path.as_posix()
                                if snapshot_meta["tab"] == "preset"
                                and right_path is not None
                                else display_name_for_repo_paths(
                                    left_path.as_posix()
                                    if left_path is not None
                                    else None,
                                    right_path.as_posix()
                                    if right_path is not None
                                    else None,
                                )
                            ),
                            change_type=file_meta["change_type"],
                            lazy_reason_override=file_meta[
                                "lazy_reason_override"
                            ],
                            untracked=not file_meta["tracked"],
                        )
                        for left_path, right_path, file_meta in room.manifested(
                            snapshot_id
                        )
                    ),
                    key=lambda path: (path.display_name, path.change_type),
                )
            )
            payload = build_repo_manifest_for_paths(
                left_label=snapshot_meta["left_label"],
                right_label=snapshot_meta["right_label"],
                paths=manifest_paths,
                added_lines=snapshot_meta["added_lines"],
                removed_lines=snapshot_meta["removed_lines"],
            )
            if tab == "preset":
                assert preset_name is not None
                payload["display_name"] = preset_name
            payload["snapshot_id"] = snapshot_id.hex
        except DirdiffError as exc:
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
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
    ) -> LazyInfoResponse:
        """Return delayed-file metadata from one manifest Snapshot.

        `snapshot_id` is the sole Room lookup input. The containing Room's
        persisted Tab supplies presentation behavior; no live Git, worktree,
        index, or preset state is read.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            room = room_lord.find_room(snapshot_key)
            snapshot_meta = room.meta(snapshot_key)
            lazy_paths = tuple(
                sorted(
                    (
                        RepoDiffPath(
                            left_path=left_path.as_posix()
                            if left_path is not None
                            else None,
                            right_path=right_path.as_posix()
                            if right_path is not None
                            else None,
                            display_name=(
                                right_path.as_posix()
                                if snapshot_meta["tab"] == "preset"
                                and right_path is not None
                                else display_name_for_repo_paths(
                                    left_path.as_posix()
                                    if left_path is not None
                                    else None,
                                    right_path.as_posix()
                                    if right_path is not None
                                    else None,
                                )
                            ),
                            change_type=file_meta["change_type"],
                            lazy_reason_override=file_meta[
                                "lazy_reason_override"
                            ],
                            untracked=not file_meta["tracked"],
                        )
                        for left_path, right_path, file_meta in room.manifested(
                            snapshot_key
                        )
                    ),
                    key=lambda path: (path.display_name, path.change_type),
                )
            )
            payload = build_lazy_info_for_paths(paths=lazy_paths)
        except DirdiffError as exc:
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
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
        engine: EngineParam = Query(description="Diff engine."),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
    ) -> TextFileDiffResponse | NotebookFileDiffResponse:
        """Render one exact filepath pair from a manifest Snapshot.

        The opaque key finds the containing Room and is passed again with the
        repository-relative paths to select the exact captured File. Rendering
        reads only the returned absolute capture paths; it never reloads the
        workspace backend.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            room = room_lord.find_room(snapshot_key)
            left = Path(left_path) if left_path is not None else None
            right = Path(right_path) if right_path is not None else None
            left_file, right_file, file_meta = room.get(
                snapshot_key,
                left,
                right,
            )
            snapshot_meta = room.meta(snapshot_key)
            context: LoadedDiffSides = {
                "left_path": left.as_posix() if left is not None else None,
                "right_path": right.as_posix() if right is not None else None,
                "left_label": snapshot_meta["left_label"],
                "right_label": snapshot_meta["right_label"],
                "left_version": TextVersion(
                    label=snapshot_meta["left_label"],
                    exists=left_file is not None,
                    text=decode_text_content(
                        left_file.read_bytes(),
                        label=f"{snapshot_meta['left_label']}:{left}",
                    )
                    if left_file is not None
                    else None,
                ),
                "right_version": TextVersion(
                    label=snapshot_meta["right_label"],
                    exists=right_file is not None,
                    text=decode_text_content(
                        right_file.read_bytes(),
                        label=f"{snapshot_meta['right_label']}:{right}",
                    )
                    if right_file is not None
                    else None,
                ),
            }
            renderer = service_for_engine(engine, cwd=Path.cwd())
            file_kind: Literal["git", "untracked"] = (
                "git" if file_meta["tracked"] else "untracked"
            )
            display_name = (
                right.as_posix()
                if snapshot_meta["tab"] == "preset" and right is not None
                else display_name_for_repo_paths(
                    left.as_posix() if left is not None else None,
                    right.as_posix() if right is not None else None,
                )
            )
            payload = build_notebook_file_payload_if_applicable(
                renderer=renderer,
                display_name=display_name,
                change_type=file_meta["change_type"],
                file_kind=file_kind,
                context=context,
            )
            if payload is None:
                payload = build_text_file_payload(
                    renderer=renderer,
                    display_name=display_name,
                    change_type=file_meta["change_type"],
                    file_kind=file_kind,
                    context=context,
                )
        except DirdiffError as exc:
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

    return app


def uvicorn_entrypoint() -> FastAPI:
    """Construct the production app from the CLI's serialized runtime config.

    The factory opens one SQLite engine, builds the registry and Room
    persistence interfaces over it, and supplies the configured store root to
    `RoomLord`. The CLI must provide the environment payload and at least one
    active Mark before uvicorn imports this function.
    """
    payload = os.environ.get(RUNTIME_CONFIG_ENV)
    assert payload is not None, "dirdiff runtime config missing"
    config = RuntimeConfig(**json.loads(payload))
    engine = open_sqlite_engine(Path(config.db_path))
    repo_store = RepoMarkStore(engine)
    room_lord = RoomLord(RoomStore(engine), Path(config.store_path))
    user_profile_store = UserProfileStore(engine)
    preferences_store = PreferencesStore(engine)
    marks = repo_store.list()
    assert marks != [], "dirdiff runtime config has no marked repos"
    return create_app(
        repo_store,
        user_profile_store,
        preferences_store,
        room_lord=room_lord,
        presets_root=config.presets_root,
    )
