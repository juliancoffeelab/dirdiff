"""Public database storage surface for dirdiff.

Import this package when application code needs the supported SQLite table
bootstrap helpers or typed store classes.  The package re-exports store
records and constructors only; table definitions stay in their owning modules
and route handlers should depend on stores rather than SQLAlchemy internals.
"""

from dirdiff.db.base import (
    TableBase,
    bootstrap_tables,
    open_ephemeral_engine,
    open_sqlite_engine,
)
from dirdiff.db.preferences import (
    PreferencesRecord,
    PreferencesStore,
)
from dirdiff.db.repo_registry import (
    RepoMainBranchRecord,
    RepoMarkRecord,
    RepoMarkStore,
)
from dirdiff.db.user_profile import (
    UserProfileRecord,
    UserProfileStore,
)

__all__ = [
    "PreferencesRecord",
    "PreferencesStore",
    "RepoMainBranchRecord",
    "RepoMarkRecord",
    "RepoMarkStore",
    "TableBase",
    "UserProfileRecord",
    "UserProfileStore",
    "bootstrap_tables",
    "open_ephemeral_engine",
    "open_sqlite_engine",
]
