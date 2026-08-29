"""FastAPI boundary for dirdiff.

Import application construction, startup configuration, response contracts,
and branch-selection conversions from this package root. Implementation lives
in `app`; package-shared contracts live in `base`; class-route collection lives
in `magic`.

The package validates HTTP input and coordinates the backend modules. It does
not implement persistence, workspace loading, diff engines, format composition,
or review placement.
"""

from dirdiff.server.app import (
    ComposedDiffResponse,
    branch_selection_request_to_selection,
    create_app,
    repo_main_branch_record_to_selection,
    selected_branch_selections,
    uvicorn_entrypoint,
)
from dirdiff.server.base import RUNTIME_CONFIG_ENV, RuntimeConfig

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "ComposedDiffResponse",
    "RuntimeConfig",
    "branch_selection_request_to_selection",
    "create_app",
    "repo_main_branch_record_to_selection",
    "selected_branch_selections",
    "uvicorn_entrypoint",
]
