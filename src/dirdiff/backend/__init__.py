"""Public backend-layer exports.

The backend package owns repository and preset access: normalizing sides,
listing changed paths, loading file versions, and exposing ref metadata.  It
does not render diffs, own HTTP request mode branching, or serialize API
responses.  This module is only the public import surface for those primitives.
"""

from dirdiff.backend.base import (
    BUILTIN_SIDES,
    BranchSelection,
    BranchSource,
    DefaultBaseSelection,
    DefaultBaseSelectionError,
    LoadedDiffSides,
    LocalBranchSelection,
    RefChoices,
    RemoteBranchRef,
    RemoteBranchSelection,
    RepoDiffPath,
    SideName,
    StructuredRemoteBranchRef,
    TextDiffError,
    TextVersion,
    UnifiedDiffLine,
    WorkspaceBackendProtocol,
    display_name_for_repo_paths,
    load_diff_sides,
    unified_diff_lines,
)
from dirdiff.backend.git import GitBackend, git_diff_args_with_direction
from dirdiff.backend.manifest import (
    build_lazy_info_for_paths,
    build_repo_manifest_for_backend,
    build_repo_manifest_for_paths,
    file_kind_for_change_type,
)
from dirdiff.backend.preset import PresetBackend

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "DefaultBaseSelection",
    "DefaultBaseSelectionError",
    "GitBackend",
    "LoadedDiffSides",
    "LocalBranchSelection",
    "PresetBackend",
    "RefChoices",
    "RemoteBranchRef",
    "RemoteBranchSelection",
    "RepoDiffPath",
    "SideName",
    "StructuredRemoteBranchRef",
    "TextDiffError",
    "TextVersion",
    "UnifiedDiffLine",
    "WorkspaceBackendProtocol",
    "build_lazy_info_for_paths",
    "build_repo_manifest_for_backend",
    "build_repo_manifest_for_paths",
    "display_name_for_repo_paths",
    "file_kind_for_change_type",
    "git_diff_args_with_direction",
    "load_diff_sides",
    "unified_diff_lines",
]
