"""Native dirdiff text engine.

`TextDiffEngine` is the built-in renderer for ordinary text files.  Unlike
the external engines, it has no subprocess integration: `logic.py` owns the
native Python line alignment and inline tokenization, while this module owns
the public engine class.
"""

from __future__ import annotations

from typing import final

from dirdiff.engines.contract import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffSide,
)
from dirdiff.engines.textdiff.logic import render_native_text_diff

__all__ = ["TextDiffEngine"]


@final
class TextDiffEngine(DiffEngineProtocol):
    """Native dirdiff renderer for already-loaded text sides."""

    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Build a native dirdiff engine result from already-loaded sides.

        Source loading and request metadata are handled before this engine is
        called.  Display enrichment such as syntax highlighting and folding is
        applied later by server-side payload assembly.
        """
        return render_native_text_diff(
            left_text=old.text,
            right_text=new.text,
        )
