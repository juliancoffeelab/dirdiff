"""Display enrichment for already-rendered diff rows.

Import syntax and fold contracts plus row-enrichment operations from
`dirdiff.rendering`. The facade can highlight source lines, combine syntax with
inline diff status, find fold hints, and enrich neutral engine rows for the HUD.

## Purpose and boundaries

Rendering begins with rows whose alignment and change status an engine has
already decided. It preserves those decisions while adding display facts, so
syntax and folding cannot create a second logical diff. Engine selection,
workspace loading, and HTTP representation stay outside this package.
"""

from dirdiff.rendering.base import (
    DiffRow,
    SyntaxClass,
    SyntaxSpan,
    enrich_rows_for_display,
    highlight_lines_for_path,
    weave_decorated_parts,
)
from dirdiff.rendering.fold import FoldHint

__all__ = [
    "DiffRow",
    "FoldHint",
    "SyntaxClass",
    "SyntaxSpan",
    "enrich_rows_for_display",
    "highlight_lines_for_path",
    "weave_decorated_parts",
]
