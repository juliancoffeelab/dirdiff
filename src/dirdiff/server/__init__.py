"""FastAPI boundary for dirdiff.

Import application construction, startup configuration, and externally used
response contracts from this package root. Application composition lives in
`app`; the domain route groups, shared contracts, and class-route collection
remain package internals.

The package validates HTTP input and coordinates the backend modules. It does
not implement persistence, workspace loading, diff engines, format composition,
or review placement.
"""

from dirdiff.server.app import (
    create_app,
    development_uvicorn_entrypoint,
    release_uvicorn_entrypoint,
)
from dirdiff.server.base import RUNTIME_CONFIG_ENV, RuntimeConfig
from dirdiff.server.diff import ComposedDiffResponse

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "ComposedDiffResponse",
    "RuntimeConfig",
    "create_app",
    "development_uvicorn_entrypoint",
    "release_uvicorn_entrypoint",
]
