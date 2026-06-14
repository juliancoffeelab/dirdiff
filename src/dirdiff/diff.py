from __future__ import annotations

from dirdiff.difftastic import (
    DifftasticAlignedLine,
    DifftasticLineFragment,
    _difftastic_engine_warning,
    _difftastic_rows_from_json,
)
from dirdiff.services import (
    DifftasticDiffService,
    GitDiffService,
    TextDiffService,
    build_loaded_diff,
)
from dirdiff.sources import (
    BUILTIN_SIDES,
    BuiltinSideName,
    GitBackend,
    PatchBackend,
    PresetBackend,
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackend,
)

__all__ = [
    "BUILTIN_SIDES",
    "BuiltinSideName",
    "DifftasticAlignedLine",
    "DifftasticDiffService",
    "DifftasticLineFragment",
    "GitBackend",
    "GitDiffService",
    "PatchBackend",
    "PresetBackend",
    "RepoDiffPath",
    "SideName",
    "TextDiffError",
    "TextDiffService",
    "TextVersion",
    "WorkspaceBackend",
    "_difftastic_engine_warning",
    "_difftastic_rows_from_json",
    "build_loaded_diff",
]
