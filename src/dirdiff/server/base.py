"""Define server contracts and prepare selected sources for Snapshot capture.

`RuntimeConfig` carries startup values between the CLI and application factory.
`ApiModel`, response metadata, the review excerpt, and `ClassRoutes` are shared
HTTP contracts. `capture_snapshot` prepares raw Tab controls for initial
manifest and agent captures; `dirdiff.room_lord.RoomLord.corresponding_room` is
the capture entrypoint shared with persisted-selection operations.

Source setup interprets HTTP capture inputs, validates storage placement, and
passes a selected source and prepared law facts to `dirdiff.room_lord.RoomLord`.
Route groups also use the shared `ClassRoutes` declarations. Application
construction and rendering have separate entrypoints.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Literal, Self, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dirdiff.change_source import (
    BranchSelection,
    ChangeSourceProtocol,
    GitChangeSource,
    GitRepository,
    PresetCatalog,
    PresetCatalogDir,
    PresetChangeSource,
    preset_catalogs,
)
from dirdiff.db import RepoMarkStore
from dirdiff.engines import DirdiffError
from dirdiff.room_lord import (
    BranchReviewCaptureSelection,
    CaptureSelection,
    HeadCaptureSelection,
    PresetCaptureSelection,
    PullRequestCaptureSelection,
    RevisionsCaptureSelection,
    Room,
    RoomLord,
)
from dirdiff.server._base.magic import ClassRoutes
from dirdiff.util import JsonValue

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "ApiModel",
    "BranchReviewCaptureInput",
    "CaptureInput",
    "ClassRoutes",
    "ErrorResponse",
    "PullRequestCaptureInput",
    "Responses",
    "ReviewExcerptResponse",
    "RevisionsCaptureInput",
    "RuntimeConfig",
    "capture_snapshot",
    "preset_catalog_dirs",
    "validate_source_placement",
]


@dataclass(frozen=True)
class RevisionsCaptureInput:
    """Carry concrete revision controls from HTTP into Git source setup.

    This is unprepared input, separate from Room's opaque correspondence facts.
    Setup validates the Head pair or freezes Refs before constructing a source.
    """

    tab: Literal["head", "refs"]
    """Tab whose concrete source-input rules apply."""

    left: str
    """Raw left revision control; `HEAD` for the Head Tab.

    Refs accepts a commit-backed Git ref, `index`, or `worktree`. Setup freezes
    commit-backed refs before capture.
    """

    right: str
    """Raw right revision control; `worktree` for the Head Tab.

    Refs follows the same accepted controls as `left`, preserving side order.
    """

    show_untracked: bool
    """Include untracked Files when the selected right state is `worktree`."""


@dataclass(frozen=True)
class BranchReviewCaptureInput:
    """Carry validated concrete branch controls into Git source preparation.

    Setup resolves current revisions and separately copies symbolic identities
    into Room's law facts. No live repository object crosses this record.
    """

    base: BranchSelection
    """Validated local or remote base selection for Git ancestry preparation."""

    review: BranchSelection
    """Validated local or remote review selection prepared with the base."""


@dataclass(frozen=True)
class PullRequestCaptureInput:
    """Carry prepared Git revisions separately from review correspondence.

    Forge preparation has already fetched these complete ids. Source setup
    checks them before construction; Room receives only the canonical URL.
    """

    url: str
    """Canonical nonblank URL used solely for review correspondence."""

    left_commit: str
    """Complete prepared merge-base revision for the left source state."""

    right_commit: str
    """Complete prepared head revision for the right source state."""


CaptureInput = (
    RevisionsCaptureInput
    | BranchReviewCaptureInput
    | PullRequestCaptureInput
    | PresetCaptureSelection
)
"""Selected HTTP inputs interpreted by concrete source setup before capture.

- Revision inputs provide raw Head or Refs controls.
- Branch inputs provide structured concrete branch controls.
- Pull Request inputs provide prepared revisions and review correspondence.
- Preset inputs provide selected catalog and group identities.

These records carry values only. They are not Room law facts, repositories,
source services, or loaded results.
"""


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
    explicit `--store-path` supplies a separate location. It must match the path
    supplied to `dirdiff.room_lord.RoomLord` and stay outside reviewed roots.
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
    Left revision handle placed in the initial Refs Tab URL.

    The CLI passes the string through as startup navigation state. The manifest
    route later normalizes and validates it during source setup.
    """

    right: str = "worktree"
    """
    Right revision handle placed in the initial Refs Tab URL.

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

    Catalog records in catalog-id order. An empty tuple means the current root
    has no catalog directories. Each record provides:

    - `catalog_id`: Exact id accepted by preset HTTP controls.
    - `name`: Configured label for the catalog picker.
    - `root`: Directory used to construct `dirdiff.change_source.PresetCatalog`.
    """
    return preset_catalogs(Path(presets_root))


def _preset_catalog_for_id(
    presets_root: str,
    catalog_id: str,
) -> PresetCatalog:
    """Construct the picker catalog for one validated catalog id.

    This is the only place a catalog id is checked against the catalogs
    that exist; an id no directory answers to is refused here rather than
    producing an empty listing. Catalogs exercise different product
    surfaces and each is a directory, so nothing but the directory listing
    decides which ones a request may name.

    # Parameters

    - `presets_root`: Exact root whose immediate directories are catalogs.
    - `catalog_id`: Exact catalog directory id selected by the caller.

    # Usage

    Parse a nonblank catalog id first, then use the returned catalog for the
    selected subset's capture.

    # Failures

    - Raises `DirdiffError` when no current preset catalog has the exact id.
    """
    for catalog in preset_catalog_dirs(presets_root):
        if catalog.catalog_id == catalog_id:
            return PresetCatalog(catalog.root)
    raise DirdiffError(f"Unknown preset catalog: {catalog_id}")


def _marked_project_id(project_id: str | None) -> int:
    """Parse a positive Mark id from a repo-backed HTTP parameter.

    Manifest uses the result to prepare the repository source. Follow-up
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


def validate_source_placement(
    db: RepoMarkStore,
    store_path: Path,
    source_root: Path,
) -> None:
    """Reject application persistence within a selected physical source root.

    # Parameters

    - `db`: Registry whose engine identifies the application's database path.
    - `store_path`: Exact configured Snapshot store path, matching RoomLord.
    - `source_root`: Physical reviewed root known only by concrete source setup.

    # Usage

    Initial and continuation setup call this before constructing a repository
    source. Room and source-neutral capture APIs never receive this root.

    # Failures

    - A database or Snapshot store inside the reviewed root raises
      `DirdiffError`.
    - Filesystem resolution failures propagate.
    """
    reviewed_root = source_root.expanduser().resolve()
    database = db.engine.url.database
    if database is not None and database != ":memory:":
        database_path = Path(database).expanduser().resolve()
        if database_path.is_relative_to(reviewed_root):
            raise DirdiffError(
                "The dirdiff database must be outside the reviewed "
                f"repository: {database_path}"
            )
    snapshot_path = store_path.expanduser().resolve()
    if snapshot_path.is_relative_to(reviewed_root):
        raise DirdiffError(
            "The dirdiff Snapshot store must be outside the reviewed "
            f"repository: {snapshot_path}"
        )


def capture_snapshot(
    db: RepoMarkStore,
    room_lord: RoomLord,
    presets_root: str,
    *,
    store_path: Path,
    project_id: str,
    selection: CaptureInput,
) -> tuple[Room, UUID, str | None]:
    """Prepare a selected source, then capture it under independent Room law facts.

    Concrete setup validates controls, freezes revisions, and checks physical
    source placement here. Only the constructed source and opaque law facts
    reach RoomLord. Browser and agent opening use this same operation.

    # Parameters

    - `db`: Active Mark registry and application database location.
    - `room_lord`: Correspondence and Snapshot service for this application.
    - `presets_root`: Configured root containing selectable preset catalogs.
    - `store_path`: Configured Snapshot root, matching the supplied RoomLord.
    - `project_id`: Active Mark id, or selected preset catalog id.
    - `selection`: Complete unprepared source inputs validated by the HTTP Tab
      parser; these are separate from Room's prepared `CaptureSelection`.

    # Usage

    Construct a `RevisionsCaptureInput`, `BranchReviewCaptureInput`, or
    `PullRequestCaptureInput` from validated HTTP controls, or use a
    `dirdiff.room_lord.PresetCaptureSelection` for a chosen catalog/group.
    Preserve the returned Room and Snapshot id together for follow-up operations.

    # Returns

    - First, the Room selected by its prepared correspondence facts.
    - Second, the immutable Snapshot id captured or reused inside that Room.
    - Third, the preset group for display, or `None` for repository Tabs.

    # Failures

    - Missing Marks/catalogs, invalid controls or revisions, and unsafe
      storage placement raise `DirdiffError`.
    - Source and capture failures propagate.
    """
    preset_name: str | None = None
    mark_id: int | None = None
    source: ChangeSourceProtocol
    law: CaptureSelection
    if isinstance(selection, PresetCaptureSelection):
        catalog = selection.catalog.strip()
        preset_name = selection.subset.strip()
        if catalog == "" or preset_name == "":
            raise DirdiffError("Preset capture requires catalog and subset.")
        selected_catalog = _preset_catalog_for_id(presets_root, catalog)
        source = PresetChangeSource(
            selected_catalog.root,
            group=preset_name,
            max_link_target_bytes=1 << 20,
        )
        law = PresetCaptureSelection(catalog, preset_name)
        left_label, right_label = "old", "new"
    else:
        mark_id = _marked_project_id(project_id)
        mark = db.get(mark_id)
        if mark is None:
            raise DirdiffError(f"Invalid project_id: {mark_id}")
        root = Path(mark.path)
        validate_source_placement(db, store_path, root)
        repository = GitRepository.discover(repo_root=root)
        show_untracked = False
        if isinstance(selection, PullRequestCaptureInput):
            if selection.url.strip() == "":
                raise DirdiffError(
                    "pull_request_url is required for the Pull Request Tab."
                )
            if (
                selection.left_commit.strip() == ""
                or selection.right_commit.strip() == ""
            ):
                raise DirdiffError(
                    "Pull Request capture requires prepared commits."
                )
            left = repository.commit_id(selection.left_commit.strip())
            right = repository.commit_id(selection.right_commit.strip())
            if (
                left != selection.left_commit.strip()
                or right != selection.right_commit.strip()
            ):
                raise DirdiffError(
                    "Pull Request capture inputs must be complete commit ids."
                )
            law = PullRequestCaptureSelection(selection.url.strip())
            left_label, right_label = left, right
        elif isinstance(selection, BranchReviewCaptureInput):
            base, left, review, right = repository.resolve_branch_diff_sides(
                base_selection=selection.base,
                review_selection=selection.review,
            )
            # Copy the validated concrete identities into caller-owned JSON law
            # values. Room embeds these without interpreting their branch shape.
            identities: list[dict[str, JsonValue]] = []
            for branch in (selection.base, selection.review):
                identity: dict[str, JsonValue] = {}
                for key, value in branch.items():
                    assert isinstance(value, str)
                    identity[key] = value
                identities.append(identity)
            law = BranchReviewCaptureSelection(identities[0], identities[1])
            left_label, right_label = f"{base.strip()}...{review}", review
        else:
            show_untracked = selection.show_untracked
            if selection.left.strip() == "" or selection.right.strip() == "":
                raise DirdiffError(
                    "left and right are required for revision Tabs."
                )
            left = repository.normalize_side(selection.left.strip())
            right = repository.normalize_side(selection.right.strip())
            if selection.tab == "head":
                if left != "HEAD" or right != "worktree":
                    raise DirdiffError(
                        "Diff against HEAD requires left=HEAD and right=worktree."
                    )
                left_label, right_label = left, right
                left = repository.commit_id(left, allow_unborn=True)
                law = HeadCaptureSelection(left)
            else:
                assert selection.tab == "refs"
                left = (
                    left
                    if left in {"index", "worktree"}
                    else repository.commit_id(left)
                )
                right = (
                    right
                    if right in {"index", "worktree"}
                    else repository.commit_id(right)
                )
                law = RevisionsCaptureSelection(left, right)
                left_label, right_label = left, right
        source = GitChangeSource(
            root,
            left=left,
            right=right,
            show_untracked=show_untracked,
            max_link_target_bytes=1 << 20,
        )
    room, snapshot_id = room_lord.corresponding_room(
        mark_id=mark_id,
        selection=law,
        source=source,
        left_label=left_label,
        right_label=right_label,
    )
    return room, snapshot_id, preset_name
