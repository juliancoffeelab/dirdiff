"""Difftastic-backed structural renderer engine.

This package is the public entrypoint for rendering already-loaded text sides
with difftastic. The public export surface is `DifftasticDiffEngine` and
`build_difftastic_ast`. Raw
subprocess execution, JSON parsing, fallback row construction, and payload
projection stay inside the package implementation modules.

Difftastic is used only at the renderer boundary: callers supply text, existence
flags, labels, and path hints, and the engine returns dirdiff row payloads plus
an optional engine warning.  The package must not load files, resolve refs,
build manifests, inspect notebooks, own API modes, or attach display-only
syntax/fold enrichment.
"""

from dirdiff.engines.difftastic.logic import (
    DifftasticDiffEngine,
    build_difftastic_ast,
)

__all__ = [
    "DifftasticDiffEngine",
    "build_difftastic_ast",
]
