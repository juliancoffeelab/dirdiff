"""Persistence for repositories that dirdiff can open by id.

`RepoMarkStore` is used by `dirdiff mark`, `dirdiff refs`, and FastAPI repo
routes in `dirdiff.server`.  It maps a stable integer project id to a filesystem
path, a user-facing repo name, the mark timestamp, and an optional main-branch
selection.  The exported records are read models for those concepts:
`RepoMarkRecord` and `RepoMainBranchRecord`.

This module is only the registry.  It does not inspect the Git worktree, resolve
refs, build manifests, load file contents, or cache diff follow-up data.
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
    """
    Operational repository mark table.

    Stores the synthetic project id, filesystem path, and whether the mark is
    available to ordinary registry operations.  Inactive rows preserve the id
    referenced by Rooms and Snapshots.
    """

    __tablename__ = "repo_mark"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="1",
    )


class RepoMarkMeta(TableBase):
    """
    Display metadata for a marked repository.

    Stores the name and timestamp shown by the repo picker.
    """

    __tablename__ = "repo_mark_meta"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("repo_mark.id"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class RepoMainBranch(TableBase):
    """
    Persisted main branch selection for one marked repository.

    This is the repo-level main branch used to seed branch-review base controls.
    """

    __tablename__ = "repo_main_branch"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("repo_mark.id"),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    remote: Mapped[str | None] = mapped_column(String, nullable=True)
    branch: Mapped[str] = mapped_column(String, nullable=False)


@dataclass(frozen=True)
class RepoMarkRecord:
    """Registered repository shown in repo lists and used by repo routes."""

    id: int
    """Stable database id used by FastAPI requests for this repository."""

    path: str
    """Filesystem path string stored for repo-backed diff requests."""

    name: str
    """Display name shown by CLI listings and the repo picker."""

    marked_at: datetime
    """UTC timestamp for when the repository was registered."""


@dataclass(frozen=True)
class RepoMainBranchRecord:
    """Persisted default branch-review base for one registered repository."""

    project_id: int
    """Repository id this branch selection belongs to."""

    source: str
    """Ref source, such as a local branch or a remote branch selection."""

    remote: str | None
    """Remote name when `source` identifies a remote branch; otherwise `None`."""

    branch: str
    """Branch name to use as the default branch-review base."""


class RepoMarkStore:
    """
    SQLite-backed repository registry.

    Maps synthetic integer project ids to filesystem paths and display metadata.
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
        """
        Return the persisted main branch for one active marked repository.
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
        """
        Persist the main branch for one marked repository.
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
