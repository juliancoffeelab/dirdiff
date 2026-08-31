"""Define contracts shared by the dirdiff server package.

`RuntimeConfig` carries startup values between the CLI and application factory.
`ApiModel`, response metadata, and the review excerpt are shared HTTP
contracts. `capture_snapshot` is the one capture operation used by the HUD and
external-agent boundaries.

The module does not construct an application, register routes, implement
handlers, retain request state, or perform rendering.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Literal, Self, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dirdiff.backend import (
    BranchSelection,
    GitBackend,
    PresetBackend,
    PresetCatalogDir,
    WorkspaceBackendProtocol,
    preset_catalogs,
)
from dirdiff.db import RepoMarkStore
from dirdiff.engines import DirdiffError
from dirdiff.room_lord import (
    CaptureSelection,
    PresetCaptureSelection,
    Room,
    RoomLord,
)

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "ApiModel",
    "ErrorResponse",
    "Responses",
    "ReviewExcerptResponse",
    "RuntimeConfig",
    "capture_snapshot",
    "preset_catalog_dirs",
]


RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"
"""Process boundary carrying one serialized `RuntimeConfig` to uvicorn.

The CLI writes it before uvicorn imports the selected app factory. The shared
runtime constructor reads it once; ordinary HTTP code never treats it as live
settings.
"""


@dataclass(frozen=True)
class RuntimeConfig:
    """Server startup configuration passed across the uvicorn factory boundary.

    The CLI creates this value before starting uvicorn.  `run_uvicorn`
    serializes it into `RUNTIME_CONFIG_ENV` because uvicorn imports the app
    factory in a fresh module-loading path, especially when reload is enabled.
    This shared contract lets the CLI construct startup values without importing
    the HTTP handlers. The shared runtime constructor is the only consumer of
    the serialized payload.
    """

    db_path: str
    """
    SQLite database path used for repo marks, preferences, and user profile data.

    The CLI resolves this to an absolute-ish string before launching uvicorn so
    reload workers do not need to know how command-line defaults were chosen.
    """

    migration_config_path: str
    """
    Alembic configuration selected for the installation mode.

    Editable launches use the canonical checkout file; release launches use the
    distribution resource bundled in the wheel. The server consumes this exact
    path and must not discover or substitute migration resources itself.
    """

    agent_skills_path: str
    """
    Directory containing the external-agent workflow skills.

    Editable launches use the canonical project-local directory; release uses
    the installed wheel resource. Application construction validates the exact
    required skill entry files before serving onboarding links.
    """

    store_path: str
    """
    Directory containing immutable Snapshot files.

    The CLI defaults this to a `store` directory beside `db_path`, while an
    explicit `--store-path` supplies a separate location. Persistent databases
    use a database-adjacent `.room.lock` file so every store root shares one
    publication lock.
    """

    presets_root: str
    """
    Absolute directory holding the Preset catalogs for this server.

    The CLI resolves the installation default or explicit option before this
    process boundary. The server consumes this exact path and must not discover
    or substitute Preset resources itself.
    """

    tab: Literal["head", "refs", "branch-review"] = "head"
    """
    Initial Tab encoded into the browser URL.

    This is startup navigation state, not a server-wide restriction; the API can
    still serve other Tabs after the frontend is running.
    """

    left: str = "HEAD"
    """
    Left backend side placed in the initial Refs Tab URL.

    The CLI passes the string through as startup navigation state. The manifest
    route later normalizes and validates it through the selected backend.
    """

    right: str = "worktree"
    """
    Right backend side placed in the initial Refs Tab URL.

    It forms an ordered pair with `left` only when `tab` is `refs`. Other startup
    Tabs do not treat the default as implicit manifest input.
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


class ResponseMetadata(TypedDict):
    """Describe one additional response in FastAPI route metadata.

    Route declarations use this shape to associate an HTTP status with the
    Pydantic model advertised for that response. It is not a runtime body and
    contains no status or response data.
    """

    model: type[BaseModel]
    """Pydantic model FastAPI advertises for the associated HTTP status."""


type Responses = Mapping[HTTPStatus, ResponseMetadata]
"""Additional response metadata accepted by dirdiff route decorators."""


class ApiModel(BaseModel):
    """Strict base for every validated HTTP entity in the server package.

    Every server request and response model subclasses `ApiModel`.
    FastAPI validates incoming JSON through it, and route code validates
    outgoing domain dictionaries before returning them.

    Models reject unknown fields, coercion, invalid defaults, and non-finite
    numbers. This base adds no shared entity fields or application behavior.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ReviewExcerptResponse(ApiModel):
    """Return bounded origin context around one selected text range.

    Full discussion reads attach this to text origins. `start_line` numbers the
    first returned source line; the selected inclusive range must lie inside the
    returned context.

    Coordinates belong to the origin bay side, not the current placement or
    rendered rows.
    """

    side: Literal["left", "right"]
    """Immutable origin side from which the excerpt was reconstructed.

    Every item in `lines` belongs to this same side. The value does not describe
    the Thread's current placement side separately.
    """

    start_line: int = Field(ge=1)
    """One-based source coordinate of the first returned line.

    It may precede `selected_start_line` by at most the bounded context. Adding
    a list offset yields the source coordinate for that item.
    """

    selected_start_line: int = Field(ge=1)
    """First origin line the author selected.

    The coordinate is inclusive and must fall within `lines`, no earlier than
    `start_line` and no later than `selected_end_line`.
    """

    selected_end_line: int = Field(ge=1)
    """Last origin line the author selected.

    The coordinate is inclusive and must fall within the returned excerpt. It
    may equal `selected_start_line` for a one-line target.
    """

    lines: list[str] = Field(min_length=1)
    """Exact origin source lines in ascending order.

    The list contains the complete selected range plus at most three context
    lines before and after it. It is never empty and is not rendered diff text.
    """

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        """Require the complete selected range to lie inside the excerpt.

        The check relates the one-based coordinates to `lines` length and also
        enforces selected endpoint ordering. It returns the excerpt unchanged.

        # Usage

        Pydantic invokes this callback after validating excerpt coordinates and
        lines. Callers validate `ReviewExcerptResponse` as one complete value.

        # Failures

        - Raises `ValueError` when the selected range is reversed or extends
          beyond the returned excerpt.
        """
        excerpt_end = self.start_line + len(self.lines) - 1
        if not (
            self.start_line
            <= self.selected_start_line
            <= self.selected_end_line
            <= excerpt_end
        ):
            raise ValueError("Selected review range exceeds its excerpt.")
        return self


class ErrorResponse(ApiModel):
    """Advertise the intended non-review failure shape in route metadata.

    Repository, profile, preference, and Pull Request routes reference this
    model in their OpenAPI response declarations. Their current
    `HTTPException` path is still serialized by FastAPI as a `detail` envelope,
    so runtime failures are not validated through this model.

    Review routes use `ReviewErrorResponse` instead, and unexpected failures are
    not converted to this type.
    """

    # TODO: Either serialize non-review HTTP failures through this model or
    # advertise FastAPI's actual `detail` envelope in route metadata.
    error: str
    """Presentation text claimed by the non-review OpenAPI response model.

    Current `HTTPException` handling does not serialize this field. Callers must
    not assume it is the present runtime envelope.
    """


def preset_catalog_dirs(
    presets_root: str,
) -> tuple[PresetCatalogDir, ...]:
    """List the preset catalogs this server offers right now.

    The presets root is rescanned per request rather than captured at
    startup, so a catalog directory added while the server runs appears on
    the next refresh, which is how every other hot-reloadable part of this
    project behaves. The server uses the exact root supplied across its startup
    boundary and does not perform installation discovery.

    # Usage

    Call for each preset listing or catalog lookup so hot-added directories
    are visible without rebuilding the application.

    # Returns

    - Each item contains one visible catalog's id, configured display name,
      and root path used to construct its backend.
    - Items follow catalog-id order. An empty tuple means the current root
      has no catalog directories.
    """
    return preset_catalogs(Path(presets_root))


def _preset_backend_for_catalog(
    presets_root: str,
    catalog_id: str,
) -> PresetBackend:
    """Construct the backend reading one named preset catalog.

    This is the only place a catalog id is checked against the catalogs
    that exist; an id no directory answers to is refused here rather than
    producing an empty listing. Catalogs exercise different product
    surfaces and each is a directory, so nothing but the directory listing
    decides which ones a request may name.

    # Parameters

    - `presets_root`: Exact root whose immediate directories are catalogs.
    - `catalog_id`: Exact catalog directory id selected by the caller.

    # Usage

    Parse a nonblank catalog id first, then use the returned backend for the
    selected subset's capture.

    # Failures

    - Raises `DirdiffError` when no current preset catalog has the exact id.
    """
    for catalog in preset_catalog_dirs(presets_root):
        if catalog.catalog_id == catalog_id:
            return PresetBackend(catalog.root)
    raise DirdiffError(f"Unknown preset catalog: {catalog_id}")


def _marked_project_id(project_id: str | None) -> int:
    """Parse a positive Mark id from a repo-backed HTTP parameter.

    Manifest uses the result to construct the workspace backend. Follow-up
    operations never call this parser because their Snapshot id is sufficient.

    # Usage

    Call while translating a repository-backed manifest selection, then use the
    positive integer for `RepoMarkStore` and Room correspondence.

    # Failures

    - Raises `DirdiffError` when the value is absent, blank, nonnumeric, or not
      positive.
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


def capture_snapshot(
    db: RepoMarkStore,
    room_lord: RoomLord,
    presets_root: str,
    *,
    project_id: str,
    selection: CaptureSelection,
) -> tuple[Room, UUID, str | None]:
    """Capture one exact Tab selection for browser and agent callers.

    The caller supplies the concrete selection variant. This operation
    selects its active mark or preset backend, applies Room
    correspondence, and returns the immutable Snapshot address plus the
    validated preset subset used only for its display name.

    # Parameters

    - `db`: Repository registry used to resolve repository-backed selections.
    - `room_lord`: Room service that applies correspondence and captures state.
    - `presets_root`: Exact root used to resolve preset-backed selections.
    - `project_id`: Active Mark id for repository selections or catalog id
      for a preset selection.
    - `selection`: Complete discriminated Tab input already validated.

    # Usage

    Pass the result of `manifest_capture_selection` or an agent Tab
    conversion. Keep the returned Room and Snapshot id together for all
    follow-up work.

    # Returns

    - First, the Room selected by the Tab's correspondence law.
    - Second, the immutable Snapshot id captured inside that Room.
    - Third, the validated preset subset used for display when `selection`
      is a Preset Tab, or `None` for every repository-backed Tab because
      those selections have no preset display suffix.

    # Failures

    - Raises `DirdiffError` when the Mark or preset catalog is absent, or
      when backend preparation and capture reject the selection.
    """
    preset_name: str | None = None
    parsed_project_id: int | None = None
    if isinstance(selection, PresetCaptureSelection):
        preset_name = selection.subset
        backend: WorkspaceBackendProtocol = _preset_backend_for_catalog(
            presets_root,
            selection.catalog,
        )
    else:
        parsed_project_id = _marked_project_id(project_id)
        mark = db.get(parsed_project_id)
        if mark is None:
            raise DirdiffError(f"Invalid project_id: {parsed_project_id}")
        backend = GitBackend.discover(repo_root=Path(mark.path))
    room, snapshot_id = room_lord.corresponding_room(
        mark_id=parsed_project_id,
        backend=backend,
        selection=selection,
    )
    return room, snapshot_id, preset_name
