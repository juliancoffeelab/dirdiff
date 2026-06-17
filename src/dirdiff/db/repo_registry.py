from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Engine, ForeignKey, String, insert, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import TableBase


class RepoMark(TableBase):
    """
    Operational repository mark table.

    Stores the synthetic repo id and the filesystem path used by repo-backed
    diff requests.
    """

    __tablename__ = "repo_mark"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class RepoMarkMeta(TableBase):
    """
    Display metadata for a marked repository.

    Stores the name and timestamp shown by the repo picker.
    """

    __tablename__ = "repo_mark_meta"

    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repo_mark.id"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


@dataclass(frozen=True)
class RepoMarkRecord:
    """
    Read model returned by the repo registry.

    Combines the repo path with its display metadata for API responses.
    """

    id: int
    path: str
    name: str
    marked_at: datetime


class RepoMarkStore:
    """
    SQLite-backed repository registry.

    Maps synthetic integer repo ids to filesystem paths and display metadata.
    """

    def __init__(self, engine: Engine) -> None:
        """
        Bind the store to a concrete SQLAlchemy engine.

        Each operation opens a short-lived session against this engine.
        """

        self.engine: Engine = engine

    def new_mark(self, path: Path, name: str) -> RepoMarkRecord:
        """
        Persist a repository mark.

        The database assigns the synthetic repo id.
        """

        assert path.is_absolute(), f"repo path must be absolute: {path}"
        assert path.is_dir(), f"repo path must be a directory: {path}"
        display_name = name.strip()
        assert display_name, "repo name cannot be empty"
        marked_at = datetime.now(UTC)
        with Session(self.engine) as session, session.begin():
            repo_id = session.execute(
                insert(RepoMark).values(path=str(path)).returning(RepoMark.id)
            ).scalar_one()
            session.execute(
                insert(RepoMarkMeta).values(
                    repo_id=repo_id,
                    name=display_name,
                    marked_at=marked_at,
                )
            )
            return RepoMarkRecord(
                id=repo_id,
                path=str(path),
                name=display_name,
                marked_at=marked_at,
            )

    def list(self) -> Sequence[RepoMarkRecord]:
        """
        Return all marked repositories.

        Results are ordered by newest mark first.
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
                        RepoMarkMeta.repo_id == RepoMark.id,
                    )
                    .order_by(RepoMarkMeta.marked_at.desc())
                )
                .tuples()
                .all()
            )
            return tuple(
                RepoMarkRecord(
                    id=repo_id,
                    path=path,
                    name=name,
                    marked_at=marked_at,
                )
                for repo_id, path, name, marked_at in rows
            )

    def get(self, repo_id: int) -> RepoMarkRecord | None:
        """
        Return one marked repository by synthetic id.

        Returns `None` when the id is not present.
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
                        RepoMarkMeta.repo_id == RepoMark.id,
                    )
                    .where(RepoMark.id == repo_id)
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
