"""SQLite persistence for dirdiff application state.

Import database construction, immutable records, and the Profile, preference,
repository-mark, and Room stores from `dirdiff.db`. Each store exposes complete
domain operations and records rather than sessions or table models.

## Purpose and boundaries

This package keeps relational invariants and transaction boundaries inside the
operation that needs them. Callers choose the domain action and consume its
record; they do not assemble generic database queries through this facade.
Workspace capture, review behavior, and HTTP representation remain decisions of
their callers.
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
