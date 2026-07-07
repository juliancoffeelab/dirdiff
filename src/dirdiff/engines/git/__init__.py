"""Git no-index-backed diff engine.

This package renders already-loaded text sides by writing temporary files,
running `git diff --no-index`, and projecting Git's unified patch text into
dirdiff engine rows.  Code outside the package imports `GitDiffEngine` from this
package root; subprocess execution lives in `git.py`, and patch-to-row
projection lives in `logic.py`.

The engine is intentionally repo-agnostic.  Backend code loads text from Git
refs, presets, or the worktree before this package is called.  This package must
not discover repositories, resolve refs, build manifests, decide HTTP modes, or
attach display-only syntax/fold enrichment.
"""

from dirdiff.engines.git.git import GitDiffEngine

__all__ = [
    "GitDiffEngine",
]
