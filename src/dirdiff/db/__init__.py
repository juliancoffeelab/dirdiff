"""Package facade for dirdiff's database persistence interfaces.

Import this package when application code needs the supported SQLite table
bootstrap helpers, typed stores, or Room and Snapshot records. The package
re-exports public records and constructors only; table definitions stay in
their modules and application logic lives outside this package. RoomStore also
persists Profile-authored review actions and placements without interpreting
discussion state. These are
persistence interfaces for backend modules, not HTTP or rendering contracts.
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
from dirdiff.db.room import (
    ReviewActionRecord,
    ReviewThreadRecord,
    ReviewThreadsRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileLoadRecord,
    SnapshotFileRecord,
    SnapshotFileSideRecord,
    SnapshotMetaRecord,
    SnapshotRecord,
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
    "ReviewActionRecord",
    "ReviewThreadRecord",
    "ReviewThreadsRecord",
    "RoomIdentity",
    "RoomStore",
    "SnapshotFileLoadRecord",
    "SnapshotFileRecord",
    "SnapshotFileSideRecord",
    "SnapshotMetaRecord",
    "SnapshotRecord",
    "TableBase",
    "UserProfileRecord",
    "UserProfileStore",
    "bootstrap_tables",
    "open_ephemeral_engine",
    "open_sqlite_engine",
]
