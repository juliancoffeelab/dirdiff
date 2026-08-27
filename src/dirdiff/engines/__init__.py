"""Diff rendering engines for already-loaded file sides.

Code outside `dirdiff.engines` imports engine contracts, row payload types, and
the concrete engine classes from this package root.  Callers are expected to
arrive with `DiffSide` values whose text, existence flags, labels, and path
hints were prepared by the backend or notebook layer; engines render those sides
into neutral row payloads and summary counts.

The engines package owns the shared failure and row contracts in `base.py`, the
concrete renderers for difftastic, Git no-index, GumTree, native text, and
token-first text diffs, and the mapping from an engine name to its renderer.
It must not load repositories, resolve refs, build manifests, classify file
formats, serialize HTTP responses, or attach display-only syntax/fold
enrichment.  Those steps belong to `dirdiff.backend`, `dirdiff.formats`,
`dirdiff.server`, and `dirdiff.rendering` respectively.

`engine()` is the authorized exception to this package's re-exports-only facade
rule.  Selection has to reach the concrete engine classes, and the sibling
modules holding those classes import their contracts from `base.py`, so
defining it there would make the import graph circular.
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
    returned renderer owns no workspace state and no request state: callers
    construct one per use and hand it already-loaded `DiffSide` values.
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
