"""GumTree-backed move renderer engine.

This package renders already-loaded ordinary source files with GumTree so
dirdiff can represent moved code as move rows and tokens.  Application code
outside the package imports only `GumTreeDiffEngine` and the `GumTreeJson`
contract from this package root.  GumTree subprocess integration and raw JSON
types live in `gumtree.py`; move projection and row assembly live in `logic.py`.

The package intentionally knows nothing about repository selection, notebook
routing, API request modes, or frontend display enrichment.  If a request points
at a notebook, server orchestration must route to notebook payload builders
before any GumTree renderer is selected.
"""

from dirdiff.engines.gumtree.gumtree import GumTreeJson
from dirdiff.engines.gumtree.logic import GumTreeDiffEngine

__all__ = [
    "GumTreeDiffEngine",
    "GumTreeJson",
]
