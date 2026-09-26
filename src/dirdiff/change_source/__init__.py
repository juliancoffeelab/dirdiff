"""Prepare workspace selections and read their changed File sides.

Import `GitRepository` for ref and Branch Review preparation or `PresetCatalog`
for picker groups. Construct `GitChangeSource` or `PresetChangeSource` with one
selected Tab's inputs. Both implement `ChangeSourceProtocol`, whose two operations
list `WorkspaceChanges` and load paired `LoadedFile` results. Manifest builders
turn captured path facts into HUD payloads. Pull Request preparation is also
exported here.

## Purpose and boundaries

This package keeps source-specific Git and fixture storage behind one capture
contract. It does not select Rooms, publish Snapshots, or decode formats.
`dirdiff.room_lord` handles capture and persistence; `dirdiff.formats` uses
the supplied bytes after capture.
"""

from dirdiff.change_source.base import (
    BranchSelection,
    CapturedLink,
    ChangeSourceProtocol,
    DefaultBaseSelection,
    FileChange,
    FileData,
    FileKey,
    FileProperties,
    FileSide,
    LazyReason,
    LoadedFile,
    RefChoices,
    WorkspaceChanges,
    display_name_for_repo_paths,
)
from dirdiff.change_source.git import (
    GitChangeSource,
    GitRepository,
    preferred_review_selection,
    ref_choices,
)
from dirdiff.change_source.manifest import (
    ManifestPath,
    RepoManifest,
    build_lazy_info_for_paths,
    build_repo_manifest_for_paths,
    file_kind_for_change_type,
)
from dirdiff.change_source.preset import (
    PresetCatalog,
    PresetCatalogDir,
    PresetChangeSource,
    preset_catalogs,
)
from dirdiff.change_source.pull_request import (
    PreparedPullRequest,
    prepare_pull_request,
)

__all__ = [
    "BranchSelection",
    "CapturedLink",
    "ChangeSourceProtocol",
    "DefaultBaseSelection",
    "FileChange",
    "FileData",
    "FileKey",
    "FileProperties",
    "FileSide",
    "GitChangeSource",
    "GitRepository",
    "LazyReason",
    "LoadedFile",
    "ManifestPath",
    "PreparedPullRequest",
    "PresetCatalog",
    "PresetCatalogDir",
    "PresetChangeSource",
    "RefChoices",
    "RepoManifest",
    "WorkspaceChanges",
    "build_lazy_info_for_paths",
    "build_repo_manifest_for_paths",
    "display_name_for_repo_paths",
    "file_kind_for_change_type",
    "preferred_review_selection",
    "prepare_pull_request",
    "preset_catalogs",
    "ref_choices",
]
