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
    "RepoMarkStore",
]


class RepoMark(TableBase):
    __tablename__ = "repo_mark"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="1",
    )


class RepoMarkMeta(TableBase):
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
    __tablename__ = "repo_main_branch"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("repo_mark.id"),
        primary_key=True,
    )

    # "local" or "remote"; remote is set only for a remote source. Stored
    # symbolically, never resolved to a commit.
    source: Mapped[str] = mapped_column(String, nullable=False)

    remote: Mapped[str | None] = mapped_column(String, nullable=True)

    branch: Mapped[str] = mapped_column(String, nullable=False)


@dataclass(frozen=True)
class RepoMarkRecord:
    id: int

    path: str

    name: str

    marked_at: datetime


@dataclass(frozen=True)
class RepoMainBranchRecord:
    project_id: int

    source: str

    remote: str | None

    branch: str


class RepoMarkStore:
    def __init__(self, engine: Engine) -> None:
        self.engine: Engine = engine

    def new_mark(self, path: Path, name: str) -> RepoMarkRecord:
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
            # Reactivation keeps the original project id.
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
        # Soft delete: Rooms and review data keep referring to the row.

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
