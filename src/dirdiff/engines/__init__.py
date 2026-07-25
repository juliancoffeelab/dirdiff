"""Diff rendering engines for already-loaded file sides.

Code outside `dirdiff.engines` imports engine contracts, row payload types, and
the concrete engine classes from this package root.  Callers are expected to
arrive with `DiffSide` values whose text, existence flags, labels, and path
hints were prepared by the backend or notebook layer; engines render those sides
into neutral row payloads and summary counts.

The engines package owns the shared failure and row contracts in `base.py` and
the concrete renderers for difftastic, Git no-index, GumTree, and native text
diffs. It must not load repositories, resolve refs, build manifests, decide
notebook routing, serialize HTTP responses, or attach display-only syntax/fold
enrichment. Those steps belong to `dirdiff.backend`, `dirdiff.notebooks`,
`dirdiff.server`, and `dirdiff.rendering` respectively.
"""

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffSummary,
    DirdiffError,
    EngineWarning,
    InlineToken,
    InlineTokenStatus,
    engine_row_has_change,
)
from dirdiff.engines.difftastic import DifftasticDiffEngine
from dirdiff.engines.git import GitDiffEngine
from dirdiff.engines.gumtree import GumTreeDiffEngine
from dirdiff.engines.textdiff import TextDiffEngine

__all__ = [
    "DiffEngineProtocol",
    "DiffEngineResult",
    "DiffEngineRow",
    "DiffSide",
    "DiffSummary",
    "DifftasticDiffEngine",
    "DirdiffError",
    "EngineWarning",
    "GitDiffEngine",
    "GumTreeDiffEngine",
    "InlineToken",
    "InlineTokenStatus",
    "TextDiffEngine",
    "engine_row_has_change",
]
