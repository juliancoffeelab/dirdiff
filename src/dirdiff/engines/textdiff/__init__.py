"""Dirdiff's native line-first text engine.

`TextDiffEngine` aligns already-loaded text by line and marks inline changes
without an external executable. `text_diff_summary` calculates the common
summary for callers that already hold engine rows.

## Purpose and boundaries

This package provides dirdiff's native comparison strategy while preserving the
same inputs and results as every other engine. It compares supplied text only.
Workspace loading and format choice happen before comparison; display folds and
syntax happen after it returns.
"""

from dirdiff.engines.textdiff.logic import (
    TextDiffEngine,
    text_diff_summary,
)

__all__ = [
    "TextDiffEngine",
    "text_diff_summary",
]
