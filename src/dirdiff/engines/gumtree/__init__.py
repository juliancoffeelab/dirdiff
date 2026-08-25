"""GumTree-backed move renderer engine.

This package renders already-loaded ordinary source files with GumTree so
dirdiff can represent moved code as move rows and tokens.  Application code
outside the package imports only `GumTreeDiffEngine` and the `GumTreeJson`
contract from this package root.  GumTree subprocess integration and raw JSON
types live in `gumtree.py`; move projection and row assembly live in `logic.py`.

The package intentionally knows nothing about repository selection, file
formats, API request modes, or frontend display enrichment.  It renders the two
text sides it is handed; which sides those are, and whether they came from a
whole file or one notebook cell, was settled by `dirdiff.formats` before any
renderer was selected.
"""

from dirdiff.engines.gumtree.gumtree import GumTreeJson
from dirdiff.engines.gumtree.logic import GumTreeDiffEngine

__all__ = [
    "GumTreeDiffEngine",
    "GumTreeJson",
]
