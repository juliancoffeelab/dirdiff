"""Define server contracts and prepare selected sources for Snapshot capture.

`RuntimeConfig` carries startup values between the CLI and application factory.
`ApiModel`, response metadata, the review excerpt, and `ClassRoutes` are shared
HTTP contracts. Validated Tab selections explicitly prepare sources for initial
manifest and agent captures; `dirdiff.room_lord.RoomLord.corresponding_room` is
the capture entrypoint shared with persisted-selection operations.

Source setup interprets HTTP capture inputs, validates storage placement, and
returns a selected source and prepared law facts for `dirdiff.room_lord.RoomLord`.
Route groups also use the shared `ClassRoutes` declarations. Application
construction and rendering have separate entrypoints.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Literal, Self, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

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
)
from dirdiff.server._base.magic import ClassRoutes

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "ApiModel",
    "BranchReviewManifestSelection",
    "ClassRoutes",
    "ErrorResponse",
    "HeadManifestSelection",
    "ManifestSelection",
    "PreparedCapture",
    "PresetManifestSelection",
    "PullRequestManifestSelection",
    "RefsManifestSelection",
    "Responses",
    "ReviewExcerptResponse",
    "RuntimeConfig",
    "preset_catalog_dirs",
    "validate_source_placement",
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


class HeadManifestSelection(ApiModel):
    """Select HEAD against the worktree of one active Mark.

    Source preparation freezes the current HEAD baseline. Untracked inclusion
    is explicit; the Tab always uses HEAD and worktree as its two sides.
    """

    tab: Literal["head"]
    """Discriminator selecting the Head Tab."""
    project_id: Annotated[int, Field(gt=0)]
    """Positive active repository Mark id."""
    show_untracked: bool
    """Whether to include untracked worktree Files."""

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
        """Freeze the current HEAD baseline and select its complete worktree.

        Call explicitly after model validation. An unborn HEAD uses Empty as
        its baseline. Construction and validation of this model perform no I/O.

        # Parameters

        - `db`: Active Mark registry and application database location.
        - `presets_root`: Application preset root supplied with capture
          configuration.
        - `store_path`: Snapshot root configured for the caller's RoomLord.

        # Returns

        One `PreparedCapture` with Head correspondence, the selected source,
        Mark id, and fixed `HEAD`/`worktree` labels.

        # Failures

        - Missing Marks/repositories and unsafe storage placement raise
          `DirdiffError`.
        - Unexpected repository and filesystem failures propagate.
        """
        root, repository = _capture_repository(db, self.project_id, store_path)
        left = repository.commit_id("HEAD", allow_unborn=True)
        source = GitChangeSource(
            root,
            left=left,
            right="worktree",
            show_untracked=self.show_untracked,
            max_link_target_bytes=1 << 20,
        )
        return PreparedCapture(
            source,
            HeadCaptureSelection(left),
            self.project_id,
            "HEAD",
            "worktree",
        )


class RefsManifestSelection(ApiModel):
    """Select two explicit revision controls of one active Mark.

    Commit-backed controls are frozen during preparation. Explicit `index` and
    `worktree` select their current state for this capture.
    """

    tab: Literal["refs"]
    """Discriminator selecting the Refs Tab."""
    project_id: Annotated[int, Field(gt=0)]
    """Positive active repository Mark id."""
    left: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    """Nonblank old Git revision, `index`, or `worktree`."""
    right: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    """Nonblank new Git revision, `index`, or `worktree`."""
    show_untracked: bool
    """Whether to include untracked Files when the new side is the worktree."""

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
        """Prepare the selected revision pair and freeze commit-backed controls.

        `index` and `worktree` remain explicit live sides for this capture;
        other controls become immutable commit ids. Model validation checks
        nonblank syntax without accessing Git.

        # Parameters

        - `db`: Active Mark registry and application database location.
        - `presets_root`: Application preset root supplied with capture
          configuration.
        - `store_path`: Snapshot root configured for the caller's RoomLord.

        # Returns

        One `PreparedCapture` with frozen Refs correspondence, the selected
        source, Mark id, and prepared side identities as labels.

        # Failures

        - Missing Marks/repositories, unavailable revision controls, and unsafe
          storage placement raise `DirdiffError`.
        - Unexpected repository and filesystem failures propagate.
        """
        root, repository = _capture_repository(db, self.project_id, store_path)
        left = repository.normalize_side(self.left)
        right = repository.normalize_side(self.right)
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
        source = GitChangeSource(
            root,
            left=left,
            right=right,
            show_untracked=self.show_untracked,
            max_link_target_bytes=1 << 20,
        )
        return PreparedCapture(
            source,
            RevisionsCaptureSelection(left, right),
            self.project_id,
            left,
            right,
        )


class BranchReviewManifestSelection(ApiModel):
    """Select symbolic base and review branches of one active Mark.

    Each branch uses the local or remote `dirdiff.change_source.BranchSelection`
    shape. Preparation reads their current revisions and merge base.
    """

    tab: Literal["branch-review"]
    """Discriminator selecting the Branch Review Tab."""
    project_id: Annotated[int, Field(gt=0)]
    """Positive active repository Mark id."""
    base: BranchSelection
    """Complete symbolic base branch, including remote only for remote source."""
    review: BranchSelection
    """Complete symbolic review branch under the same local/remote rules."""

    @field_validator("base", "review")
    @classmethod
    def nonblank_branch_names(cls, value: BranchSelection) -> BranchSelection:
        """Trim symbolic names and reject blank branch or remote fields.

        The local/remote shape is already validated. This field boundary keeps
        names nonblank before concrete source preparation receives them.
        """
        value = value.copy()
        value["branch"] = value["branch"].strip()
        if value["branch"] == "":
            raise ValueError("branch must be nonblank")
        if value["source"] == "remote":
            value["remote"] = value["remote"].strip()
            if value["remote"] == "":
                raise ValueError("remote must be nonblank")
        return value

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
        """Prepare current branch revisions and their merge base.

        Correspondence retains the validated symbolic branch identities;
        source contents use freshly prepared commit ids. Model validation checks
        branch shape and nonblank names without accessing Git.

        # Parameters

        - `db`: Active Mark registry and application database location.
        - `presets_root`: Application preset root supplied with capture
          configuration.
        - `store_path`: Snapshot root configured for the caller's RoomLord.

        # Returns

        One `PreparedCapture` with symbolic Branch Review correspondence, the
        merge-base/review source, Mark id, and branch display labels.

        # Failures

        - Missing Marks/repositories, unavailable branches or merge base, and
          unsafe storage placement raise `DirdiffError`.
        - Unexpected repository and filesystem failures propagate.
        """
        root, repository = _capture_repository(db, self.project_id, store_path)
        base, left, review, right = repository.resolve_branch_diff_sides(
            base_selection=self.base,
            review_selection=self.review,
        )
        identities = self.model_dump(mode="json", include={"base", "review"})
        correspondence = BranchReviewCaptureSelection(
            identities["base"], identities["review"]
        )
        source = GitChangeSource(
            root,
            left=left,
            right=right,
            show_untracked=False,
            max_link_target_bytes=1 << 20,
        )
        return PreparedCapture(
            source,
            correspondence,
            self.project_id,
            f"{base}...{review}",
            review,
        )


class PullRequestManifestSelection(ApiModel):
    """Select already prepared revisions under a canonical review URL.

    Forge preparation must have fetched these complete revision ids into the
    selected Mark before this input is submitted.
    """

    tab: Literal["pull-request"]
    """Discriminator selecting the Pull Request Tab."""
    project_id: Annotated[int, Field(gt=0)]
    """Positive active Mark id containing the prepared revisions."""
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    """Canonical review URL used for Room correspondence."""
    left_commit: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, pattern=r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z"
        ),
    ]
    """Complete lowercase SHA-1 or SHA-256 prepared merge-base commit id."""
    right_commit: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, pattern=r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z"
        ),
    ]
    """Complete lowercase SHA-1 or SHA-256 prepared review head commit id."""

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
        """Verify prepared commit objects and select them under the review URL.

        Model validation accepts complete lowercase SHA-1 or SHA-256 object
        ids. This explicit operation checks availability and requires each id
        to identify a commit directly in the selected repository, not a tag.

        # Parameters

        - `db`: Active Mark registry and application database location.
        - `presets_root`: Application preset root supplied with capture
          configuration.
        - `store_path`: Snapshot root configured for the caller's RoomLord.

        # Returns

        One `PreparedCapture` with URL correspondence, immutable commit source,
        Mark id, and the two complete commit ids as labels.

        # Failures

        - Missing Marks/repositories, absent or non-commit objects, object ids
          incompatible with the repository format, and unsafe placement raise
          `DirdiffError`.
        - Unexpected repository and filesystem failures propagate.
        """
        root, repository = _capture_repository(db, self.project_id, store_path)
        left = repository.commit_id(self.left_commit)
        right = repository.commit_id(self.right_commit)
        if left != self.left_commit or right != self.right_commit:
            raise DirdiffError(
                "Pull Request capture sides must identify commit objects directly."
            )
        source = GitChangeSource(
            root,
            left=left,
            right=right,
            show_untracked=False,
            max_link_target_bytes=1 << 20,
        )
        return PreparedCapture(
            source,
            PullRequestCaptureSelection(self.url),
            self.project_id,
            left,
            right,
        )


class PresetManifestSelection(ApiModel):
    """Choose a catalog-local fixture group without a repository Mark.

    Obtain `catalog` and `subset` from the preset picker. Preparation constructs
    the selected fixture source; the response uses the subset as its display
    label. Neither value is a numeric repository project id.
    """

    tab: Literal["preset"]
    """Discriminator selecting the Preset Tab."""
    catalog: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    """Nonblank catalog directory id from the preset picker."""
    subset: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    """Nonblank fixture-group directory id within that catalog."""

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
        """Select a fixture source from the configured catalog and group.

        Model validation checks nonblank catalog/group input without reading
        directories. Call this explicitly to discover the catalog and construct
        its selected source. Group-path and fixture availability checks occur
        when capture calls the source's `changes()`. Response display naming
        remains the caller's work.

        # Parameters

        - `db`: Active Mark registry and application database location.
        - `presets_root`: Application preset root supplied with capture
          configuration.
        - `store_path`: Snapshot root configured for the caller's RoomLord.

        # Returns

        One `PreparedCapture` with catalog/group correspondence, fixture source,
        absent Mark id, and `old`/`new` labels.

        # Failures

        - Unknown catalogs raise `DirdiffError`.
        - Unexpected catalog and filesystem failures propagate.
        """
        catalog = _preset_catalog_for_id(presets_root, self.catalog)
        source = PresetChangeSource(
            catalog.root, group=self.subset, max_link_target_bytes=1 << 20
        )
        return PreparedCapture(
            source,
            PresetCaptureSelection(self.catalog, self.subset),
            None,
            "old",
            "new",
        )


type ManifestSelection = Annotated[
    HeadManifestSelection
    | RefsManifestSelection
    | BranchReviewManifestSelection
    | PullRequestManifestSelection
    | PresetManifestSelection,
    Field(discriminator="tab"),
]
"""Complete per-Tab input for manifest HTTP validation and source preparation.

The `tab` discriminator selects required fields. Unknown fields are rejected,
including controls belonging to another Tab. These are concrete source inputs,
not the prepared correspondence facts consumed by RoomLord.

# Usage

After Pydantic validates the selected variant, call
`selection.prepare(db, presets_root, store_path=store_path)`. Pass the returned
`PreparedCapture` fields to `dirdiff.room_lord.RoomLord.corresponding_room`.

Validation checks only input syntax. Explicit preparation reads repositories or
catalogs and verifies their availability, but does not observe Files, select a
Room, or publish a Snapshot. Supply the application's configured preset and
Snapshot roots on every variant; repository variants use `db` and `store_path`,
while Preset uses `presets_root` and carries no Mark identity.
"""


@dataclass(frozen=True)
class PreparedCapture:
    """Inputs for one `dirdiff.room_lord.RoomLord.corresponding_room` call.

    A selection's explicit `prepare()` constructs this value without observing
    Files or writing a Snapshot. Callers invoke RoomLord explicitly, then consume
    Room metadata to build their HTTP response.
    """

    source: ChangeSourceProtocol
    """Selected source whose observation belongs to this capture."""
    correspondence: CaptureSelection
    """Prepared law facts passed as RoomLord's `selection` argument."""
    mark_id: int | None
    """Active Mark id for repository Tabs; absent for Preset."""
    left_label: str
    """Display and diagnostic label for the old source state."""
    right_label: str
    """Display and diagnostic label for the new source state."""


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


def _capture_repository(
    db: RepoMarkStore,
    project_id: int,
    store_path: Path,
) -> tuple[Path, GitRepository]:
    """Open the selected Mark's repository after checking application placement.

    The four repository selection variants share Mark lookup, reviewed-root
    placement checks, and repository discovery. Their explicit `prepare()` calls
    use this operation before interpreting their own revision controls.

    # Parameters

    - `db`: Active Mark registry and application database location.
    - `project_id`: Validated positive Mark id.
    - `store_path`: Exact Snapshot root configured for the caller's RoomLord.

    # Returns

    - First, the selected Mark's physical root for source construction.
    - Second, its discovered repository for concrete revision preparation.

    # Failures

    - A missing Mark, unavailable repository, or unsafe application placement
      raises `DirdiffError`.
    - Filesystem and unexpected discovery failures propagate.
    """
    mark = db.get(project_id)
    if mark is None:
        raise DirdiffError(f"Invalid project_id: {project_id}")
    root = Path(mark.path)
    validate_source_placement(db, store_path, root)
    return root, GitRepository.discover(repo_root=root)
