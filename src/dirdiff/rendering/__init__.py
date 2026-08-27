"""Display enrichment for already-rendered diff rows.

Code outside `dirdiff.rendering` imports row-enrichment functions and fold types
from this package root.  The package receives neutral rows that an engine or
notebook builder has already computed, then attaches display-only metadata such
as decorated text parts, fold hints, backend-owned hunk identities, and default
expansion policy.

Rendering enrichment must not choose a diff engine, compare text, calculate
changed-line summaries, load files, resolve refs, build manifests, or serialize
HTTP route responses. Syntax highlighting and hunk enrichment live in
`base.py`; structural fold discovery lives in `fold.py`.
"""

from dirdiff.rendering.base import (
    DecoratedPart,
    DiffRow,
    EnrichedRows,
    SyntaxClass,
    SyntaxSpan,
    enrich_rows_for_display,
    highlight_lines_for_path,
    weave_decorated_parts,
)
from dirdiff.rendering.fold import FoldHint, engine_row_has_change

__all__ = [
    "DecoratedPart",
    "DiffRow",
    "EnrichedRows",
    "FoldHint",
    "SyntaxClass",
    "SyntaxSpan",
    "engine_row_has_change",
    "enrich_rows_for_display",
    "highlight_lines_for_path",
    "weave_decorated_parts",
]
