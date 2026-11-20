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
    "HeadBaselineManifestSelection",
    "ManifestSelection",
    "PreparedCapture",
    "PresetManifestSelection",
    "PullRequestManifestSelection",
    "RefsManifestSelection",
    "Responses",
    "ReviewExcerptResponse",
    "RuntimeConfig",
    "preset_catalog_dirs",
]


RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: str
    migration_config_path: str
    agent_skills_path: str
    store_path: str
    presets_root: str
    tab: Literal["head", "refs", "branch-review"] = "head"
    left: str = "HEAD"
    right: str = "worktree"
    base_selection: BranchSelection | None = None
    review_selection: BranchSelection | None = None


class ResponseMetadata(TypedDict):
    model: type[BaseModel]


type Responses = Mapping[HTTPStatus, ResponseMetadata]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class HeadManifestSelection(ApiModel):
    tab: Literal["head"]
    project_id: Annotated[int, Field(gt=0)]
    show_untracked: bool

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
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


class HeadBaselineManifestSelection(ApiModel):
    tab: Literal["head"]
    project_id: Annotated[int, Field(gt=0)]
    baseline: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    show_untracked: bool

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
        root, repository = _capture_repository(db, self.project_id, store_path)
        # An unborn-HEAD review records the empty-tree id, which is not a
        # commit; any other baseline must be a commit.
        empty_tree = repository.empty_tree_id()
        left = (
            empty_tree
            if self.baseline.strip() == empty_tree
            else repository.commit_id(self.baseline)
        )
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
    tab: Literal["refs"]
    project_id: Annotated[int, Field(gt=0)]
    left: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    right: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    show_untracked: bool

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
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
    tab: Literal["branch-review"]
    project_id: Annotated[int, Field(gt=0)]
    base: BranchSelection
    review: BranchSelection

    @field_validator("base", "review")
    @classmethod
    def nonblank_branch_names(cls, value: BranchSelection) -> BranchSelection:
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
    tab: Literal["pull-request"]
    project_id: Annotated[int, Field(gt=0)]
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    left_commit: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, pattern=r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z"
        ),
    ]
    right_commit: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, pattern=r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z"
        ),
    ]

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
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
    tab: Literal["preset"]
    catalog: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    subset: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]

    def prepare(
        self,
        db: RepoMarkStore,
        presets_root: str,
        *,
        store_path: Path,
    ) -> PreparedCapture:
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


@dataclass(frozen=True)
class PreparedCapture:
    source: ChangeSourceProtocol
    correspondence: CaptureSelection
    mark_id: int | None
    left_label: str
    right_label: str


class ReviewExcerptResponse(ApiModel):
    side: Literal["left", "right"]
    start_line: int = Field(ge=1)
    selected_start_line: int = Field(ge=1)
    selected_end_line: int = Field(ge=1)
    lines: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
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
    # TODO: Either serialize non-review HTTP failures through this model or
    # advertise FastAPI's actual `detail` envelope in route metadata.
    error: str


def preset_catalog_dirs(
    presets_root: str,
) -> tuple[PresetCatalogDir, ...]:
    return preset_catalogs(Path(presets_root))


def _preset_catalog_for_id(
    presets_root: str,
    catalog_id: str,
) -> PresetCatalog:
    for catalog in preset_catalog_dirs(presets_root):
        if catalog.catalog_id == catalog_id:
            return PresetCatalog(catalog.root)
    raise DirdiffError(f"Unknown preset catalog: {catalog_id}")


def validate_source_placement(
    db: RepoMarkStore,
    store_path: Path,
    source_root: Path,
) -> None:
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
    mark = db.get(project_id)
    if mark is None:
        raise DirdiffError(f"Invalid project_id: {project_id}")
    root = Path(mark.path)
    validate_source_placement(db, store_path, root)
    return root, GitRepository.discover(repo_root=root)
