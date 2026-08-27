"""Repository and preset access boundary for dirdiff.

Code outside `dirdiff.backend` imports backend contracts and concrete workspace
backends from this package root.  The package owns branch/ref selection types,
repository path discovery, manifest construction, file-side loading, text diff
preset loading, and pull-request preparation.

Backend implementations may read Git repositories and preset directories, but
they must not publish Snapshots, render rich diff rows, choose HTTP request
modes, build FastAPI responses, or know about frontend state. Core backend
side/path/text contracts live in `base.py`. Sibling backend modules import
shared internals from their owning implementation modules, while external
callers use the exports here.
"""

from dirdiff.backend.base import (
    BUILTIN_SIDES,
    BranchSelection,
    BranchSource,
    DefaultBaseSelection,
    DefaultBaseSelectionError,
    LazyReason,
    LocalBranchSelection,
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
