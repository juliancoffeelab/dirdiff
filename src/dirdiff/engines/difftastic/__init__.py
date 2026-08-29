"""Structural diff rendering through Difftastic.

`DifftasticDiffEngine` compares two already-loaded text sides through
Difftastic and returns the common engine result. `build_difftastic_ast` exposes
the validated structural rows and recognized warning for callers that need the
Difftastic-stage result itself.

## Purpose and boundaries

This package converts Difftastic alignment facts into rows whose text still
comes exactly from the supplied sides. Path hints may select a parser but are
never loaded as files. Known structural failures produce the documented engine
warning and textual comparison; display folds and syntax are added later.
"""

from dirdiff.engines.difftastic.logic import (
    DifftasticDiffEngine,
    build_difftastic_ast,
)

__all__ = [
    "DifftasticDiffEngine",
    "build_difftastic_ast",
]
