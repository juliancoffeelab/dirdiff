"""Workspace access through Git repositories and preset catalogs.

Import workspace contracts, `GitBackend`, `PresetBackend`, manifest builders,
and Pull Request preparation from `dirdiff.backend`. Together they normalize a
workspace selection, enumerate its affected paths, and load exact File sides as
bytes.

## Purpose and boundaries

This package gives Room capture one interface for repository-backed and preset
workspaces. It reports source facts needed to build a manifest but does not
publish Snapshots or interpret loaded bytes as a format. Composition and diff
rendering begin only after a backend has supplied those bytes.
"""

from dirdiff.backend.base import (
    BUILTIN_SIDES,
    BranchSelection,
    BranchSource,
    DefaultBaseSelection,
    DefaultBaseSelectionError,
    LazyReason,
    LocalBranchSelection,
    PresetGroup,
    RefChoices,
    RefMetadata,
    RemoteBranchRef,
    RemoteBranchSelection,
    RepoDiffPath,
    SideName,
    StructuredRemoteBranchRef,
    WorkspaceBackendProtocol,
    display_name_for_repo_paths,
)
from dirdiff.backend.git import (
    GitBackend,
    preferred_review_selection,
    ref_choices,
)
from dirdiff.backend.manifest import (
    RepoManifest,
    build_lazy_info_for_paths,
    build_repo_manifest_for_backend,
    build_repo_manifest_for_paths,
    file_kind_for_change_type,
)
from dirdiff.backend.preset import (
    PresetBackend,
    PresetCatalogDir,
    preset_catalogs,
)
from dirdiff.backend.pull_request import (
    PreparedPullRequest,
    prepare_pull_request,
)

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "DefaultBaseSelection",
    "DefaultBaseSelectionError",
    "GitBackend",
    "LazyReason",
    "LocalBranchSelection",
    "PreparedPullRequest",
    "PresetBackend",
    "PresetCatalogDir",
    "PresetGroup",
    "RefChoices",
    "RefMetadata",
    "RemoteBranchRef",
    "RemoteBranchSelection",
    "RepoDiffPath",
    "RepoManifest",
    "SideName",
    "StructuredRemoteBranchRef",
    "WorkspaceBackendProtocol",
    "build_lazy_info_for_paths",
    "build_repo_manifest_for_backend",
    "build_repo_manifest_for_paths",
    "display_name_for_repo_paths",
    "file_kind_for_change_type",
    "preferred_review_selection",
    "prepare_pull_request",
    "preset_catalogs",
    "ref_choices",
]
