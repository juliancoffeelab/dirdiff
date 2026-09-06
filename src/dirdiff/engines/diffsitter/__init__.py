"""Grapheme-level diff rendering through Diffsitter.

`DiffsitterDiffEngine` compares two already-loaded text sides through the
`diffsitter` executable and returns the common engine result. `DiffsitterJson`
is the validated shape accepted from that process and `is_diffsitter_json`
validates it without executable discovery or row construction.
`build_diffsitter_rows_from_json` maps validated JSON onto exact source texts
without running the executable.

## Purpose and boundaries

This package reproduces Diffsitter's report faithfully: each unmatched old
grapheme is a deleted token and each unmatched new grapheme is an inserted
token, on physical rows aligned locally. It infers no replacements or moves and
rejects output that disagrees with the supplied text. Workspace loading and
format choice happen before the engine runs; display folds and syntax happen
after it returns.
"""

from dirdiff.engines.diffsitter.logic import (
    DiffsitterDiffEngine,
    DiffsitterJson,
    build_diffsitter_rows_from_json,
    is_diffsitter_json,
)

__all__ = [
    "DiffsitterDiffEngine",
    "DiffsitterJson",
    "build_diffsitter_rows_from_json",
    "is_diffsitter_json",
]
