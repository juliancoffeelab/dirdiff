"""Dirdiff's token-first text engine.

`TokenDiffEngine` compares the complete token streams of two already-loaded
text sides, allowing matching content to cross line boundaries before it
returns common engine rows.

## Purpose and boundaries

This package supplies a token-first comparison strategy behind the shared
engine contract. It receives text rather than workspace handles and does not
choose the File format. Display folds and syntax are added only after its rows
have been arranged.
"""

from dirdiff.engines.tokendiff.logic import TokenDiffEngine

__all__ = [
    "TokenDiffEngine",
]
