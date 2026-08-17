"""Native dirdiff text engine.

This package is the built-in renderer for already-loaded ordinary text files
when no external structural engine is needed.  Code outside the package imports
`TextDiffEngine` and the token-free `text_diff_summary` from this package
root.  The implementation in `logic.py` owns native Python line alignment,
inline tokenization, and summary assembly.

The text engine has no subprocess integration and no repository knowledge.  It
must not load files, resolve refs, choose API modes, handle notebooks, or attach
display-only syntax/fold enrichment.
"""

from dirdiff.engines.textdiff.logic import (
    TextDiffEngine,
    text_diff_summary,
)

__all__ = [
    "TextDiffEngine",
    "text_diff_summary",
]
