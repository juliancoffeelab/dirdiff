"""Structural diff rendering through GumTree.

`GumTreeDiffEngine` compares two already-loaded text sides and returns the
common engine result. `GumTreeJson` is the validated shape accepted from the
external GumTree process. `is_gumtree_json` validates these raw facts without
executable discovery or row construction.

## Purpose and boundaries

This package maps GumTree's classified source ranges back onto the exact text
supplied by the caller. It rejects unusable process output instead of treating
it as a valid comparison. Workspace loading and format choice happen before the
engine runs; display folds and syntax happen after it returns.
"""

from dirdiff.engines.gumtree.logic import (
    GumTreeDiffEngine,
    GumTreeJson,
    build_gumtree_rows_from_json,
    is_gumtree_json,
)

__all__ = [
    "GumTreeDiffEngine",
    "GumTreeJson",
    "build_gumtree_rows_from_json",
    "is_gumtree_json",
]
