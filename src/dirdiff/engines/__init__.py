from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffsitterEngineConfig,
    DiffSummary,
    DifftasticEngineConfig,
    DirdiffEngineConfig,
    DirdiffError,
    EngineConfig,
    EngineWarning,
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    GitEngineConfig,
    GitWordBoundaryCustom,
    GitWordBoundaryDefault,
    GitWordBoundaryLanguage,
    GitWordBoundaryMode,
    GumTreeEngineConfig,
    GumTreeMatcher,
    InlineToken,
    InlineTokenStatus,
    PairedMove,
    TokenDiffEngineConfig,
    UnpairedMove,
    git_executable,
)
from dirdiff.engines.diffsitter import (
    DiffsitterDiffEngine,
    DiffsitterJson,
    build_diffsitter_rows_from_json,
    is_diffsitter_json,
)
from dirdiff.engines.difftastic import (
    DifftasticDiffEngine,
    DifftasticJson,
    is_difftastic_json,
)
from dirdiff.engines.git import GitDiffEngine
from dirdiff.engines.gumtree import (
    GumTreeDiffEngine,
    GumTreeJson,
    build_gumtree_rows_from_json,
    is_gumtree_json,
)
from dirdiff.engines.textdiff import TextDiffEngine, text_diff_summary
from dirdiff.engines.tokendiff import TokenDiffEngine


# Lives in the facade rather than base.py: selection imports the concrete
# engine modules, which themselves depend on base.py.
def engine(config: EngineConfig) -> DiffEngineProtocol:
    match config:
        case GitEngineConfig(
            algorithm=algorithm,
            ignore_space_change=ignore_space_change,
            word_diff=word_diff,
            word_boundary=word_boundary,
            color_moved=color_moved,
            color_moved_ws=color_moved_ws,
        ):
            return GitDiffEngine(
                algorithm=algorithm,
                color_moved=color_moved,
                color_moved_ws=color_moved_ws,
                ignore_space_change=ignore_space_change,
                word_diff=word_diff,
                word_boundary=word_boundary,
            )
        case DifftasticEngineConfig(ignore_comments=ignore_comments):
            return DifftasticDiffEngine(ignore_comments=ignore_comments)
        case GumTreeEngineConfig(matcher=matcher):
            return GumTreeDiffEngine(matcher=matcher)
        case DiffsitterEngineConfig():
            return DiffsitterDiffEngine()
        case TokenDiffEngineConfig():
            return TokenDiffEngine()
        case DirdiffEngineConfig():
            return TextDiffEngine()


__all__ = [
    "DiffEngineProtocol",
    "DiffEngineResult",
    "DiffEngineRow",
    "DiffSide",
    "DiffSummary",
    "DiffsitterDiffEngine",
    "DiffsitterJson",
    "DifftasticDiffEngine",
    "DifftasticJson",
    "DirdiffError",
    "EngineConfig",
    "EngineWarning",
    "GitAlgorithm",
    "GitColorMoved",
    "GitColorMovedWs",
    "GitDiffEngine",
    "GitWordBoundaryCustom",
    "GitWordBoundaryDefault",
    "GitWordBoundaryLanguage",
    "GitWordBoundaryMode",
    "GumTreeDiffEngine",
    "GumTreeJson",
    "GumTreeMatcher",
    "InlineToken",
    "InlineTokenStatus",
    "PairedMove",
    "TextDiffEngine",
    "TokenDiffEngine",
    "UnpairedMove",
    "build_diffsitter_rows_from_json",
    "build_gumtree_rows_from_json",
    "engine",
    "git_executable",
    "is_diffsitter_json",
    "is_difftastic_json",
    "is_gumtree_json",
    "text_diff_summary",
]
