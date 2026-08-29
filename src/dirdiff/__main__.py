"""Execution entrypoint for `python -m dirdiff`.

## Public interface

Executing this module invokes `dirdiff.cli.main`, the same callable used by the
installed `dirdiff` command.

## Purpose and boundaries

This module makes the package executable without defining another set of
commands. Argument parsing, command behavior, and process lifetime remain in the
CLI package.
"""

from dirdiff.cli import main

# Intentionally empty: `main` is imported here only so module execution can
# delegate to the real public entrypoint, `dirdiff.cli:main`.
__all__: list[str] = []

if __name__ == "__main__":
    main()
