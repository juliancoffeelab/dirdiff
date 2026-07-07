"""Top-level package marker for the dirdiff application.

The package root intentionally exports nothing.  Application code should import
through the owning package surfaces such as `dirdiff.backend`,
`dirdiff.engines`, `dirdiff.rendering`, `dirdiff.cli`, or the FastAPI wiring in
`dirdiff.server`.  This keeps the root from becoming a second public API that
silently bypasses the ownership boundaries documented by those packages.
"""

# Intentionally empty: application imports should use the package roots that own
# public contracts, or `dirdiff.server` for FastAPI wiring.
__all__: list[str] = []
