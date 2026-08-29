"""Command-line entrypoint for dirdiff.

The package exports `main`, the entrypoint used by both the installed `dirdiff`
command and `python -m dirdiff`. It accepts terminal choices for opening a Tab
or changing the local repository registry.

## Purpose and boundaries

The CLI turns validated terminal input into a registry operation or complete
`dirdiff.server.RuntimeConfig`, then manages local server and browser process
lifetime. HTTP routes, workspace loading, diff rendering, frontend behavior,
and database schemas remain behind the interfaces the command invokes.
"""

from dirdiff.cli.base import main

__all__ = [
    "main",
]
