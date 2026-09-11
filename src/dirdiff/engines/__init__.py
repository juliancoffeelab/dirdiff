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

from typing import assert_never

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffSummary,
    DirdiffError,
    EngineConfig,
    EngineWarning,
    GitAlgorithm,
    GitEngineConfig,
    InlineMove,
    InlineToken,
    InlineTokenStatus,
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
    if config == "dirdiff":
        return TextDiffEngine()
    if isinstance(config, GitEngineConfig):
        return GitDiffEngine(
            algorithm=config.algorithm,
            ignore_space_change=config.ignore_space_change,
        )
    if config == "difftastic":
        return DifftasticDiffEngine()
    if config == "gumtree":
        return GumTreeDiffEngine()
    if config == "diffsitter":
        return DiffsitterDiffEngine()
    if config == "tokendiff":
        return TokenDiffEngine()
    assert_never(config)


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
