"""Console-command entrypoint for the local dirdiff application.

The installed `dirdiff` script and `python -m dirdiff` both enter through
`main`, the only public item exported by this package.  The CLI package owns
terminal command spelling, Typer option parsing, local app launch configuration,
and repo-mark commands that write the local repository registry.

The command implementation may assemble `RuntimeConfig`, choose ports, open the
browser, and print terminal feedback.  It must not own FastAPI routes,
repository loading semantics, diff rendering, frontend behavior, or database
schema.  Those responsibilities belong to `dirdiff.server`, `dirdiff.backend`,
`dirdiff.engines`, `dirdiff.rendering`, and `dirdiff.db`.
"""

from dirdiff.cli.base import main

__all__ = [
    "main",
]
