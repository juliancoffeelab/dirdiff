"""Token-first dirdiff text engine.

This package renders already-loaded ordinary text files by diffing whole
token streams instead of lines first, so content moving across line
boundaries — comment reflow, line joins and splits — diffs at word
granularity. Code outside the package imports `TokenDiffEngine` from this
package root. The implementation in `logic.py` owns tokenization, the token
edit script, line pairing, and row emission.

The engine has no subprocess integration and no repository knowledge. It
must not load files, resolve refs, choose API modes, handle notebooks, or
attach display-only syntax/fold enrichment.
"""

from dirdiff.engines.tokendiff.logic import TokenDiffEngine

__all__ = [
    "TokenDiffEngine",
]
