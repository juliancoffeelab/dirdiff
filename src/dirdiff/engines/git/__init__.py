"""Git-diff backed diff engine.

This package implements a version of diff engine using native functionality
of git.
As all engines, it exports `GitDiffEngine` with a method to turn two `DiffSide`
handles into structured diff representation, this one by using `git diff`.
The private `_logic/driver.py` module invokes Git. Row construction and the
engine entrypoint live in `dirdiff.engines.git.logic`.

This engine is, maybe surprisingly, agnostic to repo-backend, and can be used
with any kind of version storage, even if it doesn't use git at all (like
presets): the secret is in using `git diff --no-index`. This allows us to use
real functionality with any option it gives (i.e. different diff algorithm,
even if we don't use any at the moment), on any two files we want.

Because of that, none of the code in this package should read any git refs,
branches, or commits. That's the responsibility of `dirdiff.backend.git` module.
Nor should it manage semantic representation such as folds or syntax, that
is the job of `dirdiff.rendering`.
"""

from dirdiff.engines.git.logic import GitDiffEngine

__all__ = [
    "GitDiffEngine",
]
