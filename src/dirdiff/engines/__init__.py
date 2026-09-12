"""Diff engines for already-loaded text.

Import the common input, row, warning, and result contracts from
`dirdiff.engines`. `engine()` selects the implementation named by an
`EngineConfig`; concrete classes used directly elsewhere in the repository are
also part of this facade.

## Purpose and boundaries

Every engine compares the text in two supplied `DiffSide` values and returns
the same neutral row representation. This lets composition change comparison
strategy without changing its downstream flow. Engines never load workspace
content or decide its format, and display enrichment adds folds and syntax only
after comparison.
"""

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
    GitEngineConfig,
    GumTreeEngineConfig,
    GumTreeMatcher,
    InlineMove,
    InlineToken,
    InlineTokenStatus,
    TokenDiffEngineConfig,
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


def engine(config: EngineConfig) -> DiffEngineProtocol:
    """Return the renderer that `config` selects.

    Every validated configuration maps to one renderer, which holds no workspace
    or HTTP state. Callers construct one per use and hand it already-loaded `DiffSide` values.

    # Usage

    Select the renderer after validating an `EngineConfig`, then pass it to
    `dirdiff.formats.ComposeContext.build`. Composition invokes
    `DiffEngineProtocol.render_diff` for each text bay.
    """
    match config:
        case GitEngineConfig(
            algorithm=algorithm, ignore_space_change=ignore_space_change
        ):
            return GitDiffEngine(
                algorithm=algorithm,
                ignore_space_change=ignore_space_change,
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
    "GitDiffEngine",
    "GumTreeDiffEngine",
    "GumTreeJson",
    "GumTreeMatcher",
    "InlineMove",
    "InlineToken",
    "InlineTokenStatus",
    "TextDiffEngine",
    "TokenDiffEngine",
    "build_diffsitter_rows_from_json",
    "build_gumtree_rows_from_json",
    "engine",
    "git_executable",
    "is_diffsitter_json",
    "is_difftastic_json",
    "is_gumtree_json",
    "text_diff_summary",
]
