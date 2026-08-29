"""Define contracts shared by the dirdiff server package.

`RuntimeConfig` and `RUNTIME_CONFIG_ENV` carry validated startup values between
the CLI and application factory. `Responses` is the class-route metadata shape
shared by route declarations in `app` and their registration in `magic`.

This module defines data only. It does not construct an application, register a
route, open persistence, or handle an HTTP entity.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Literal, TypedDict

from pydantic import BaseModel

from dirdiff.backend import BranchSelection

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "ResponseMetadata",
    "Responses",
    "RuntimeConfig",
]


RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"
"""Process boundary carrying one serialized `RuntimeConfig` to uvicorn.

The CLI writes it before uvicorn imports the app factory. `uvicorn_entrypoint`
reads it once during construction; ordinary HTTP code never treats it as live
settings.
"""


@dataclass(frozen=True)
class RuntimeConfig:
    """Server startup configuration passed across the uvicorn factory boundary.

    The CLI creates this value before starting uvicorn.  `run_uvicorn`
    serializes it into `RUNTIME_CONFIG_ENV` because uvicorn imports the app
    factory in a fresh module-loading path, especially when reload is enabled.
    This shared contract lets the CLI construct startup values without importing
    the HTTP handlers. `uvicorn_entrypoint` is the only consumer of the serialized
    payload.
    """

    db_path: str
    """
    SQLite database path used for repo marks, preferences, and user profile data.

    The CLI resolves this to an absolute-ish string before launching uvicorn so
    reload workers do not need to know how command-line defaults were chosen.
    """

    store_path: str
    """
    Directory containing immutable Snapshot files.

    The CLI defaults this to a `store` directory beside `db_path`, while an
    explicit `--store-path` supplies a separate location. Persistent databases
    use a database-adjacent `.room.lock` file so every store root shares one
    publication lock.
    """

    tab: Literal["head", "refs", "branch-review"] = "head"
    """
    Initial Tab encoded into the browser URL.

    This is startup navigation state, not a server-wide restriction; the API can
    still serve other Tabs after the frontend is running.
    """

    left: str = "HEAD"
    """
    Left backend side placed in the initial Refs Tab URL.

    The CLI passes the string through as startup navigation state. The manifest
    route later normalizes and validates it through the selected backend.
    """

    right: str = "worktree"
    """
    Right backend side placed in the initial Refs Tab URL.

    It forms an ordered pair with `left` only when `tab` is `refs`. Other startup
    Tabs do not treat the default as implicit manifest input.
    """

    base_selection: BranchSelection | None = None
    """
    Base branch selection for the Branch Review startup Tab.

    The CLI writes this structured value into the first browser URL; API
    handlers parse the same local/remote shape from query params afterward.
    """

    review_selection: BranchSelection | None = None
    """
    Review branch selection for the Branch Review startup Tab.

    This is startup navigation state only.  Diff requests still carry their own
    explicit branch-review selections.
    """

    presets_root: str | None = None
    """
    Directory holding preset catalogs, or `None` for `tests/presets` under the
    working directory.

    Its immediate subdirectories are the catalogs the Preset Tab offers, one
    per directory. It is not a catalog itself.
    """


class ResponseMetadata(TypedDict):
    """Describe one additional response in FastAPI route metadata.

    Route declarations use this shape to associate an HTTP status with the
    Pydantic model advertised for that response. It is not a runtime body and
    contains no status or response data.
    """

    model: type[BaseModel]
    """Pydantic model FastAPI advertises for the associated HTTP status."""


type Responses = Mapping[HTTPStatus, ResponseMetadata]
"""Additional response metadata accepted by dirdiff route decorators."""
