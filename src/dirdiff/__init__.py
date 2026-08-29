"""The Python package for the dirdiff application.

The root has no application-level interface. Import the capability you need
from its public package, such as `dirdiff.backend`, `dirdiff.db`,
`dirdiff.engines`, `dirdiff.formats`, or `dirdiff.rendering`.

## Purpose and boundaries

This namespace groups the backend, persistence, composition, rendering, Room,
and server parts of dirdiff without inventing a second facade over them. Adding
an item here would obscure which package defines its contract.
"""

# Note: do not add __all__ here, this will break `pdoc` discovery
