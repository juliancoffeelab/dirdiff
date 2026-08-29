"""Persistence for repositories that dirdiff can open by id.

## Classes

`RepoMarkStore` creates, lists, finds, and deactivates repository marks. It also
stores the optional symbolic base branch used to seed Branch Review. The
exported records carry those values to CLI and server callers.

## Purpose and boundaries

A mark gives later commands and HTTP parameters a stable integer identity even
when repository paths or display order differ. Deactivation preserves the row
so retained Rooms and review data can continue to refer to it.

The registry treats repository paths and branch selections as stored values. It
does not inspect worktrees, resolve refs, read File contents, or construct Tabs;
those operations belong to backend and server code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Engine,
    ForeignKey,
    String,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import TableBase

__all__ = [
    "RepoMainBranchRecord",
    "RepoMarkRecord",
    "RepoMarkStore",
]


class RepoMark(TableBase):
    """Persist the stable identity and lifecycle of one repository mark.

    `RepoMarkStore` creates or reactivates rows and filters ordinary reads to
    `active=True`.

    Display metadata and main-branch selection live in separate relations.
    Callers never receive this ORM object directly.
    """

    __tablename__ = "repo_mark"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    """Synthetic repository identity referenced by Rooms and HTTP entities.

    Reactivating the same path preserves this value, so callers may retain it
    across mark lifecycle changes.
    """

    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    """Globally unique absolute workspace path.

    Registration uses its string form as the durable correspondence key; the row
    does not guarantee the directory remains readable after insertion.
    """

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="1",
    )
    """Whether ordinary registry reads expose this mark.

    Deactivation preserves the row and its referenced identity.
    """


class RepoMarkMeta(TableBase):
    """Persist display metadata for one repository mark.

    `RepoMarkStore` writes this one-to-one row with `RepoMark` and joins it when
    producing `RepoMarkRecord` values.

    It contains the picker name and mark timestamp only. Repository identity,
    path, lifecycle, and Git state stay outside this relation.
    """

    __tablename__ = "repo_mark_meta"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("repo_mark.id"),
        primary_key=True,
    )
    """Repository mark whose one-to-one display metadata this row stores.

    As the primary and foreign key, it prevents metadata from outliving or
    multiplying for one registry identity.
    """

    name: Mapped[str] = mapped_column(String, nullable=False)
    """User-facing repository name shown by CLI and HUD pickers.

    Remarking may replace it without changing project identity or the registered
    filesystem path.
    """

    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    """UTC time when the repository was most recently marked or reactivated.

    It describes registry activity for presentation, not repository history or
    filesystem modification time.
    """


class RepoMainBranch(TableBase):
    """Persist one symbolic main-branch selection for a marked repository.

    `RepoMarkStore` replaces this one-to-one row when the user changes the
    default Branch Review base, then returns `RepoMainBranchRecord` to callers.

    Remote and branch names remain separate. This table stores no commit id and
    does not assert that the symbolic branch currently resolves.
    """

    __tablename__ = "repo_main_branch"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("repo_mark.id"),
        primary_key=True,
    )
    """Repository mark whose default base this row configures.

    Its one-to-one key makes replacement the complete saved selection for that
    project rather than an append-only history.
    """

    source: Mapped[str] = mapped_column(String, nullable=False)
    """Branch-source discriminator, either `local` or `remote`.

    Callers interpret the remaining fields according to this value; persistence
    keeps the symbolic choice and does not resolve it to a commit.
    """

    remote: Mapped[str | None] = mapped_column(String, nullable=True)
    """Remote name for a remote selection, otherwise `None`.

    A non-null value is meaningful only with the remote source; local selections
    must not invent a remote name.
    """

    branch: Mapped[str] = mapped_column(String, nullable=False)
    """Symbolic branch name relative to its selected source.

    It is stored without ref prefixes and may later fail Git resolution if the
    repository changes.
    """


@dataclass(frozen=True)
class RepoMarkRecord:
    """Expose one active repository mark to CLI and HTTP callers.

    `RepoMarkStore.new_mark`, `get`, and `list` return this immutable record.
    Use `id` in later operations, `path` to open the workspace backend, and the
    remaining fields for presentation.

    The record does not include Git refs, branch defaults, Room state, or an
    assertion that the path remains readable after the registry query.
    """

    id: int
    """Stable database id used by FastAPI requests for this repository.

    Follow-up calls use it instead of trusting a browser-supplied workspace path;
    only active marks are exposed through ordinary reads.
    """

    path: str
    """Filesystem path string stored for repo-backed diff requests.

    Backend construction may validate current accessibility later; this record
    reports registration state only.
    """

    name: str
    """Display name shown by CLI listings and the repo picker.

    It is mutable presentation metadata and must not be used as repository
    identity or a filesystem path.
    """

    marked_at: datetime
    """UTC timestamp for the most recent mark or reactivation.

    Consumers may display or order it, but it carries no Git commit or workspace
    freshness guarantee.
    """


@dataclass(frozen=True)
class RepoMainBranchRecord:
    """Expose the saved default Branch Review base for one repository.

    The store returns this record to default-selection routes. Convert its
    source, remote, and branch fields into a structured branch selection before
    asking a backend to resolve it.

    It is symbolic configuration, not a commit, Git ref string, or repository
    identity beyond `project_id`.
    """

    project_id: int
    """Repository id this branch selection belongs to.

    It binds the symbolic default to one mark; callers must use that same mark
    when asking a backend to resolve the selection.
    """

    source: str
    """Ref source distinguishing local from remote branch selection.

    It determines whether `remote` must be absent or present and how callers
    construct the structured backend input.
    """

    remote: str | None
    """Remote name when `source` identifies a remote branch; otherwise `None`.

    It is symbolic configuration and does not assert that the remote still exists.
    """

    branch: str
    """Branch name to use as the default Branch Review base.

    Callers combine it with `source` and `remote`; it is not a resolved ref or
    immutable commit identity by itself.
    """


class RepoMarkStore:
    """Provide the SQLite repository registry used by CLI and server code.

    # Usage
    Construct one store from the application engine. Use `new_mark` to register
    or reactivate a path, `list` and `get` for active marks, and the main-branch
    methods for saved Branch Review defaults.

    # Boundaries
    Operations own short-lived sessions and return immutable records. The store
    does not inspect Git, resolve refs, load workspace content, create Rooms, or
    decide whether a saved path is currently usable.
    """

    def __init__(self, engine: Engine) -> None:
        """
        Bind the store to a concrete SQLAlchemy engine.

        Each operation opens a short-lived session against this engine.
        """

        self.engine: Engine = engine

    def new_mark(self, path: Path, name: str) -> RepoMarkRecord:
        """
        Persist or reactivate a repository mark.

        The database assigns the synthetic project id for a new path.  A path
        that was previously deactivated keeps its original id and registry
        state while receiving the supplied display name and a new mark time.

        # Parameters

        - `path`: Existing absolute repository directory. Its string form is
          the stable registry key.
        - `name`: Display name; surrounding whitespace is removed and the
          remaining value must not be empty.

        # Usage

        Resolve and validate the repository directory before calling. Keep the
        returned id as the value used by later CLI commands and repository HTTP
        parameters.

        # Failures

        - Asserts when `path` is not an existing absolute directory or `name`
          becomes empty after trimming.
        - The database rejects a second mark of an already active path.
        """

        assert path.is_absolute(), f"repo path must be absolute: {path}"
        assert path.is_dir(), f"repo path must be a directory: {path}"
        display_name = name.strip()
        assert display_name != "", "repo name cannot be empty"
        marked_at = datetime.now(UTC)
        with Session(self.engine) as session, session.begin():
            existing_mark = session.execute(
                select(RepoMark.id, RepoMark.active).where(
                    RepoMark.path == str(path)
                )
            ).one_or_none()
            if existing_mark is not None and existing_mark.active is False:
                project_id = existing_mark.id
                session.execute(
                    update(RepoMark)
                    .where(RepoMark.id == project_id)
                    .values(active=True)
                )
                session.execute(
                    update(RepoMarkMeta)
                    .where(RepoMarkMeta.project_id == project_id)
                    .values(name=display_name, marked_at=marked_at)
                )
                return RepoMarkRecord(
                    id=project_id,
                    path=str(path),
                    name=display_name,
                    marked_at=marked_at,
                )
            project_id = session.execute(
                insert(RepoMark).values(path=str(path)).returning(RepoMark.id)
            ).scalar_one()
            session.execute(
                insert(RepoMarkMeta).values(
                    project_id=project_id,
                    name=display_name,
                    marked_at=marked_at,
                )
            )
            return RepoMarkRecord(
                id=project_id,
                path=str(path),
                name=display_name,
                marked_at=marked_at,
            )

    def list(self) -> Sequence[RepoMarkRecord]:
        """
        Return all active marked repositories for selection.

        Results are ordered by display name, path, and stable id.

        # Usage

        Use the returned order directly for repository selection. Inactive marks
        are deliberately absent even though their retained data still exists.

        # Returns

        - Each item is one active Mark's stable id, path, display name, and
          marking time; inactive retained Marks are absent.
        - Items are ordered by display name, then path, then id. An empty
          sequence means the registry has no active Marks.

        """

        with Session(self.engine) as session:
            rows = (
                session.execute(
                    select(
                        RepoMark.id,
                        RepoMark.path,
                        RepoMarkMeta.name,
                        RepoMarkMeta.marked_at,
                    )
                    .join_from(
                        RepoMark,
                        RepoMarkMeta,
                        RepoMarkMeta.project_id == RepoMark.id,
                    )
                    .where(RepoMark.active.is_(True))
                    .order_by(
                        RepoMarkMeta.name.asc(),
                        RepoMark.path.asc(),
                        RepoMark.id.asc(),
                    )
                )
                .tuples()
                .all()
            )
            return tuple(
                RepoMarkRecord(
                    id=project_id,
                    path=path,
                    name=name,
                    marked_at=marked_at,
                )
                for project_id, path, name, marked_at in rows
            )

    def get(self, project_id: int) -> RepoMarkRecord | None:
        """
        Return one marked repository by synthetic id.

        Returns `None` when the id is absent or inactive.

        # Usage

        Use this at the boundary of an operation that requires an active mark.
        Treat `None` as an unknown project rather than trying to open a path from
        retained Room data.

        # Returns

        - The active mark record for the exact id.
        - `None`: No active mark has that id. The caller must handle it as an
          unknown repository, not reconstruct a mark from retained Room data.

        """

        with Session(self.engine) as session:
            res = (
                session.execute(
                    select(
                        RepoMark.id,
                        RepoMark.path,
                        RepoMarkMeta.name,
                        RepoMarkMeta.marked_at,
                    )
                    .join_from(
                        RepoMark,
                        RepoMarkMeta,
                        RepoMarkMeta.project_id == RepoMark.id,
                    )
                    .where(
                        RepoMark.id == project_id,
                        RepoMark.active.is_(True),
                    )
                )
                .tuples()
                .one_or_none()
            )
            if res is None:
                return None
            return RepoMarkRecord(
                id=res[0],
                path=res[1],
                name=res[2],
                marked_at=res[3],
            )

    def delete(self, project_id: int) -> bool:
        """
        Deactivate one marked repository.

        Returns `True` when an active mark became inactive, or `False` when the
        id was absent or already inactive.  Registry metadata, Rooms,
        Snapshots, and repository files on disk are never touched.

        # Usage

        Call this for the registry's remove-mark operation. The boolean tells a
        CLI or route whether this call changed active state.
        """

        with Session(self.engine) as session, session.begin():
            deactivated_id = session.execute(
                update(RepoMark)
                .where(
                    RepoMark.id == project_id,
                    RepoMark.active.is_(True),
                )
                .values(active=False)
                .returning(RepoMark.id)
            ).scalar_one_or_none()
            return deactivated_id is not None

    def get_main_branch(self, project_id: int) -> RepoMainBranchRecord | None:
        """Read the saved symbolic Branch Review base for one active Mark.

        The result keeps source, optional remote, and branch separate. `None`
        means the Mark is missing, inactive, or has no saved selection; this
        lookup neither discovers Git defaults nor creates registry state.

        # Parameters

        - `project_id`: Stable active Mark identity whose optional default is read.

        # Usage

        Use this only after selecting a repository id. When it returns `None`,
        ask backend discovery for a suggested base rather than inventing stored
        configuration.

        # Returns

        - The saved structured base selection for the active Mark.
        - `None`: The Mark is unavailable or has no persisted choice. The caller
          may ask the backend for a suggestion but must not claim it was saved.

        """

        with Session(self.engine) as session:
            row = session.execute(
                select(
                    RepoMainBranch.project_id,
                    RepoMainBranch.source,
                    RepoMainBranch.remote,
                    RepoMainBranch.branch,
                )
                .join_from(
                    RepoMainBranch,
                    RepoMark,
                    RepoMark.id == RepoMainBranch.project_id,
                )
                .where(
                    RepoMainBranch.project_id == project_id,
                    RepoMark.active.is_(True),
                )
            ).one_or_none()
            if row is None:
                return None
            return RepoMainBranchRecord(
                project_id=row[0],
                source=row[1],
                remote=row[2],
                branch=row[3],
            )

    def set_main_branch(
        self,
        project_id: int,
        *,
        source: str,
        remote: str | None,
        branch: str,
    ) -> RepoMainBranchRecord:
        """Persist the symbolic Branch Review base for an active mark.

        # Parameters

        - `project_id`: Active repository mark whose default is replaced.
        - `source`: Branch namespace, validated by the HTTP boundary as local
          or remote.
        - `remote`: Remote name for a remote source, otherwise `None`.
        - `branch`: Symbolic branch name within the selected source.

        # Usage

        Convert and validate the client's structured branch selection first.
        Store the symbolic names, not a resolved ref or commit, so later review
        startup can re-evaluate the branch.

        # Failures

        - Asserts when `project_id` does not name an active mark.
        - Propagates database constraints if the source, remote, and branch
          relationship is invalid.
        """

        mark = self.get(project_id)
        assert mark is not None, f"repo mark must exist: {project_id}"
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                sqlite_insert(RepoMainBranch)
                .values(
                    project_id=project_id,
                    source=source,
                    remote=remote,
                    branch=branch,
                )
                .on_conflict_do_update(
                    index_elements=[RepoMainBranch.project_id],
                    set_={
                        "source": source,
                        "remote": remote,
                        "branch": branch,
                    },
                )
                .returning(
                    RepoMainBranch.project_id,
                    RepoMainBranch.source,
                    RepoMainBranch.remote,
                    RepoMainBranch.branch,
                )
            ).one()
            return RepoMainBranchRecord(
                project_id=row[0],
                source=row[1],
                remote=row[2],
                branch=row[3],
            )
