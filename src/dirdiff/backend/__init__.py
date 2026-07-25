"""Repository and preset access boundary for dirdiff.

Code outside `dirdiff.backend` imports backend contracts and concrete workspace
backends from this package root.  The package owns branch/ref selection types,
repository path discovery, manifest construction, file-side loading, text diff
preset loading, and the in-process repo-info cache used by the
server between manifest and file-detail requests.

Backend implementations may read Git repositories, preset directories, and the
cache backend, but they must not render rich diff rows, choose HTTP request
modes, build FastAPI responses, or know about frontend state.  Core backend
side/path/text contracts live in `base.py`; cache-specific public contracts live
in `cache.py`.  Sibling backend modules import shared internals from their
owning implementation modules, while external callers use the exports here.
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
    TextVersion,
    WorkspaceBackendProtocol,
    display_name_for_repo_paths,
    load_diff_sides,
)
from dirdiff.backend.cache import (
    CacheBackendProtocol,
    MemoryCacheBackend,
    RepoInfo,
)
from dirdiff.backend.git import GitBackend, git_diff_args_with_direction
from dirdiff.backend.manifest import (
    build_lazy_info_for_paths,
    build_repo_manifest_for_backend,
    build_repo_manifest_for_paths,
    file_kind_for_change_type,
)
from dirdiff.backend.preset import PresetBackend
from dirdiff.backend.pull_request import (
    PreparedPullRequest,
    PreparedPullRequestBranch,
    prepare_pull_request,
)

__all__ = [
    "BUILTIN_SIDES",
    "BranchSelection",
    "BranchSource",
    "CacheBackendProtocol",
    "DefaultBaseSelection",
    "DefaultBaseSelectionError",
    "GitBackend",
    "LoadedDiffSides",
    "LocalBranchSelection",
    "MemoryCacheBackend",
    "PreparedPullRequest",
    "PreparedPullRequestBranch",
    "PresetBackend",
    "RefChoices",
    "RemoteBranchRef",
    "RemoteBranchSelection",
    "RepoDiffPath",
    "RepoInfo",
    "SideName",
    "StructuredRemoteBranchRef",
    "TextVersion",
    "WorkspaceBackendProtocol",
    "build_lazy_info_for_paths",
    "build_repo_manifest_for_backend",
    "build_repo_manifest_for_paths",
    "display_name_for_repo_paths",
    "file_kind_for_change_type",
    "git_diff_args_with_direction",
    "load_diff_sides",
    "prepare_pull_request",
]
