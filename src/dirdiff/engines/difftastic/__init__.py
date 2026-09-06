"""Structural diff rendering through Difftastic.

`DifftasticDiffEngine` compares two already-loaded text sides through
Difftastic and returns the common engine result. `DifftasticJson` and `is_difftastic_json` expose the
consumed raw JSON shape and its validation without running the tool. The structural row builder
remains an implementation detail of that engine.

## Purpose and boundaries

This package converts Difftastic alignment facts into rows whose text still
comes exactly from the supplied sides. Path hints may select a parser but are
never loaded as files. Known structural failures produce the documented engine
warning and textual comparison; display folds and syntax are added later.
"""

from dirdiff.engines.difftastic.logic import (
    DifftasticDiffEngine,
    DifftasticJson,
    is_difftastic_json,
)

__all__ = [
    "DifftasticDiffEngine",
    "DifftasticJson",
    "is_difftastic_json",
]
