"""Public diff-engine entrypoints.

The package export surface is intentionally small.  Code outside
`dirdiff.engines` may choose one of the concrete engines or type against
`DiffEngineProtocol` when it already has loaded file text to render.  The
internal workspace adapter protocol, shared row builders, and engine-specific
subprocess helpers stay in their modules so this package does not become a
grab bag of semi-public implementation details.
"""

from dirdiff.engines.contract import (
    DiffEngineProtocol,
)
from dirdiff.engines.difftastic import DifftasticDiffEngine
from dirdiff.engines.git import GitDiffEngine
from dirdiff.engines.gumtree import GumTreeDiffEngine
from dirdiff.engines.textdiff import TextDiffEngine

__all__ = [
    "DiffEngineProtocol",
    "DifftasticDiffEngine",
    "GitDiffEngine",
    "GumTreeDiffEngine",
    "TextDiffEngine",
]
