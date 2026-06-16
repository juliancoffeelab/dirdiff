from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, override

from sqlalchemy import (
    DateTime,
    Engine,
    ForeignKey,
    String,
    create_engine,
    insert,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


class RepoRegistryBase(DeclarativeBase):
    pass


class RepoMark(RepoRegistryBase):
    """
    Operational repository mark table.

    Stores the synthetic repo id and the filesystem path used by repo-backed
    diff requests.
    """

    __tablename__ = "repo_mark"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class RepoMarkMeta(RepoRegistryBase):
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


class RepoMarkStoreProtocol(Protocol):
    """
    Read protocol consumed by the app layer.

    This keeps tests able to provide a small in-memory registry.
    """

    def list(self) -> Sequence[RepoMarkRecord]: ...

    def get(self, repo_id: int) -> RepoMarkRecord | None: ...


class RepoMarkStore(RepoMarkStoreProtocol):
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

    @classmethod
    def open(cls, db_path: Path) -> RepoMarkStore:
        """
        Open a registry store for a SQLite path.
        """

        expanded_path = db_path.expanduser()
        expanded_path.parent.mkdir(parents=True, exist_ok=True)
        store = cls(create_engine(f"sqlite:///{expanded_path}"))
        store.bootstrap()
        return store

    @classmethod
    def ephemeral_open(cls) -> RepoMarkStore:
        """
        Open an in-memory registry store (for tests).
        """

        store = cls(
            create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        )
        store.bootstrap()
        return store

    def bootstrap(self) -> None:
        """
        Ensure the registry tables exist in the configured database.

        This is called once by `open`.
        """

        RepoRegistryBase.metadata.create_all(self.engine)

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
        saved_mark = self.get(repo_id)
        assert saved_mark is not None, "marked repo missing"
        return saved_mark

    @override
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

    @override
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
                .one()
            )
            return RepoMarkRecord(
                id=res[0],
                path=res[1],
                name=res[2],
                marked_at=res[3],
            )
