"""Diff engines for already-loaded text.

Import the common input, row, warning, and result contracts from
`dirdiff.engines`. `engine()` selects the implementation named by an
`EngineKind`; concrete engine classes are also available when the caller has
already made that choice.

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
    EngineKind,
    EngineWarning,
    InlineToken,
    InlineTokenStatus,
    git_executable,
)
from dirdiff.engines.difftastic import DifftasticDiffEngine
from dirdiff.engines.git import GitDiffEngine
from dirdiff.engines.gumtree import GumTreeDiffEngine
from dirdiff.engines.textdiff import TextDiffEngine, text_diff_summary
from dirdiff.engines.tokendiff import TokenDiffEngine


def engine(name: EngineKind) -> DiffEngineProtocol:
    """Return the renderer that `name` selects.

    Every engine name maps to a renderer, so this always returns one. The
    returned renderer holds no workspace or HTTP state. Callers
    construct one per use and hand it already-loaded `DiffSide` values.

    # Usage

    Select the renderer after validating an `EngineKind`, then pass it to
    `dirdiff.formats.ComposeContext.build`. Composition invokes
    `DiffEngineProtocol.render_diff` for each text bay.
    """
    if name == "dirdiff":
        return TextDiffEngine()
    if name == "git":
        return GitDiffEngine()
    if name == "difftastic":
        return DifftasticDiffEngine()
    if name == "gumtree":
        return GumTreeDiffEngine()
    if name == "tokendiff":
        return TokenDiffEngine()
    assert_never(name)


__all__ = [
    "DiffEngineProtocol",
    "DiffEngineResult",
    "DiffEngineRow",
    "DiffSide",
    "DiffSummary",
    "DifftasticDiffEngine",
    "DirdiffError",
    "EngineKind",
    "EngineWarning",
    "GitDiffEngine",
    "GumTreeDiffEngine",
    "InlineToken",
    "InlineTokenStatus",
    "TextDiffEngine",
    "TokenDiffEngine",
    "engine",
    "git_executable",
    "text_diff_summary",
]
