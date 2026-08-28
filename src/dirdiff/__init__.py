"""Top-level package marker for the dirdiff application.

The package root intentionally exports nothing.  Application code should import
through the owning package surfaces such as `dirdiff.backend`,
`dirdiff.engines`, `dirdiff.rendering`, `dirdiff.cli`, or the FastAPI wiring in
`dirdiff.server`.  This keeps the root from becoming a second public API that
silently bypasses the ownership boundaries documented by those packages.
"""

# Note: do not add __all__ here, this will break `pdoc` discovery
