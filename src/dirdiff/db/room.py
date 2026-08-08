"""Relational persistence for Rooms, Snapshots, and captured Files.

`RoomStore` is the public database interface. A `SnapshotFile` row represents
one affected File and records the absolute path of its capture directory.
Separate left and right relations record whichever captured sides exist,
without nullable side columns or a discriminator.

This module owns SQLAlchemy tables, queries, and transactions only. It does not
apply Tab rules, call workspace backends, hash or load contents, manage
directories or locks, derive manifest output, or change repository-mark
deletion behavior. Those responsibilities remain outside `dirdiff.db`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Engine,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    insert,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import TableBase

__all__ = [
    "RoomIdentity",
    "RoomStore",
    "SnapshotFileLoadRecord",
    "SnapshotFileRecord",
    "SnapshotFileSideRecord",
    "SnapshotMetaRecord",
    "SnapshotRecord",
]


class Room(TableBase):
    """Persist one Room identity selected by a Tab's correspondence law.

    Repository Rooms reference a Mark; preset Rooms omit it. `backend_key` is
    opaque to SQLite and is used only for exact equality inside a Tab.
    """

    __tablename__ = "room"
    __table_args__ = (
        CheckConstraint(
            "tab IN ('head', 'refs', 'branch-review', "
            "'pull-request', 'preset')",
            name="ck_room_tab",
        ),
        CheckConstraint(
            "(mark_id IS NULL) = (tab = 'preset')",
            name="ck_room_mark_tab",
        ),
        CheckConstraint(
            "length(backend_key) > 0",
            name="ck_room_backend_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mark_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("repo_mark.id"),
        nullable=True,
    )
    tab: Mapped[str] = mapped_column(String, nullable=False)
    backend_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


Index(
    "uq_room_mark_tab_backend_key",
    Room.mark_id,
    Room.tab,
    Room.backend_key,
    unique=True,
    sqlite_where=Room.mark_id.is_not(None),
)
Index(
    "uq_room_preset_tab_backend_key",
    Room.tab,
    Room.backend_key,
    unique=True,
    sqlite_where=Room.mark_id.is_(None),
)


class Snapshot(TableBase):
    """Persist one immutable content state inside a Room.

    The id addresses follow-up reads globally. `content_hash` deduplicates
    equal captures inside the Room. File contents, repository paths, and
    Snapshot-wide metadata live in their own dependent relations.
    """

    __tablename__ = "snapshot"
    __table_args__ = (
        UniqueConstraint(
            "room_id",
            "content_hash",
            name="uq_snapshot_content",
        ),
        CheckConstraint(
            "length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'",
            name="ck_snapshot_id",
        ),
        CheckConstraint(
            "length(content_hash) = 32",
            name="ck_snapshot_content_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("room.id"),
        nullable=False,
    )
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


class SnapshotMeta(TableBase):
    """Persist presentation labels and backend totals for one Snapshot.

    Labels are always present. Added and removed line counts are either both
    nonnegative or both absent when the backend cannot state them.
    """

    __tablename__ = "snapshot_meta"
    __table_args__ = (
        CheckConstraint(
            "length(left_label) > 0 AND length(right_label) > 0",
            name="ck_snapshot_meta_labels",
        ),
        CheckConstraint(
            "(added_lines IS NULL AND removed_lines IS NULL) OR "
            "(added_lines IS NOT NULL AND removed_lines IS NOT NULL AND "
            "added_lines >= 0 AND removed_lines >= 0)",
            name="ck_snapshot_meta_line_counts",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"),
        primary_key=True,
    )
    left_label: Mapped[str] = mapped_column(String, nullable=False)
    right_label: Mapped[str] = mapped_column(String, nullable=False)
    added_lines: Mapped[Optional[int]] = mapped_column(nullable=True)
    removed_lines: Mapped[Optional[int]] = mapped_column(nullable=True)


class SnapshotFile(TableBase):
    """Persist one affected File and its absolute capture-directory path.

    Side presence and side-specific repository paths and digests live in
    `SnapshotFileLeft` and `SnapshotFileRight`. This row retains tracked and
    change classification and any capture failure, but no manifest ordering,
    display name, line count, or loading-policy output.
    """

    __tablename__ = "snapshot_file"
    __table_args__ = (
        UniqueConstraint("path", name="uq_snapshot_file_path"),
        CheckConstraint(
            "length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'",
            name="ck_snapshot_file_id",
        ),
        CheckConstraint(
            "substr(path, 1, 1) = '/'",
            name="ck_snapshot_file_path",
        ),
        CheckConstraint(
            "change_type IN ('modify', 'add', 'delete', 'rename', 'copy')",
            name="ck_snapshot_file_change_type",
        ),
        CheckConstraint(
            "error IS NULL OR length(error) > 0",
            name="ck_snapshot_file_error",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)


Index("ix_snapshot_file_snapshot_id", SnapshotFile.snapshot_id)


class SnapshotFileLeft(TableBase):
    """Persist a captured File's left repository path and content digest.

    Absence of this row means the File has no left side; the relation never
    represents that absence with nullable columns.
    """

    __tablename__ = "snapshot_file_left"
    __table_args__ = (
        CheckConstraint(
            "length(repository_path) > 0 AND "
            "substr(repository_path, 1, 1) != '/' AND "
            "repository_path != '.' AND repository_path != '..' AND "
            "repository_path NOT LIKE '../%' AND "
            "repository_path NOT LIKE '%/../%' AND "
            "repository_path NOT LIKE '%/..'",
            name="ck_snapshot_file_left_repository_path",
        ),
        CheckConstraint(
            "length(content_hash) = 32",
            name="ck_snapshot_file_left_content_hash",
        ),
    )

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    repository_path: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


class SnapshotFileRight(TableBase):
    """Persist a captured File's right repository path and content digest.

    Absence of this row means the File has no right side; the relation never
    represents that absence with nullable columns.
    """

    __tablename__ = "snapshot_file_right"
    __table_args__ = (
        CheckConstraint(
            "length(repository_path) > 0 AND "
            "substr(repository_path, 1, 1) != '/' AND "
            "repository_path != '.' AND repository_path != '..' AND "
            "repository_path NOT LIKE '../%' AND "
            "repository_path NOT LIKE '%/../%' AND "
            "repository_path NOT LIKE '%/..'",
            name="ck_snapshot_file_right_repository_path",
        ),
        CheckConstraint(
            "length(content_hash) = 32",
            name="ck_snapshot_file_right_content_hash",
        ),
    )

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    repository_path: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


class SnapshotFileLazyReason(TableBase):
    """Persist an explicit loading-policy override captured for one File.

    The row exists only when the backend supplied a reason. It stores neither
    rendered output nor the optional source metadata content.
    """

    __tablename__ = "snapshot_file_lazy_reason"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('too_big', 'generated', 'deleted', "
            "'untracked', 'pure_renamed')",
            name="ck_snapshot_file_lazy_reason",
        ),
    )

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)


class SnapshotFileLazyReasonContent(TableBase):
    """Persist complete source metadata supplied with a lazy override.

    The row exists for preset metadata whose content participates in Snapshot
    identity. Backend overrides without source content have no row rather than
    a nullable content column.
    """

    __tablename__ = "snapshot_file_lazy_reason_content"
    __table_args__ = (
        CheckConstraint(
            "length(content) > 0",
            name="ck_snapshot_file_lazy_reason_content",
        ),
    )

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file_lazy_reason.file_id"),
        primary_key=True,
    )
    content: Mapped[str] = mapped_column(String, nullable=False)


@dataclass(frozen=True)
class RoomIdentity:
    """Database address produced after a Tab law has selected one Room.

    Application logic constructs this value. `RoomStore` treats the key as an
    opaque equality value and never interprets correspondence rules.
    """

    mark_id: Optional[int]
    """Mark id for repository Rooms, or `None` for preset Rooms."""

    tab: str
    """Persisted HUD Tab category."""

    correspondence_key: bytes
    """Opaque Room key compared for equality by SQLite."""


@dataclass(frozen=True)
class SnapshotFileSideRecord:
    """Non-null repository identity and digest for one captured File side."""

    repository_path: str
    """Repository-relative path used to address this side."""

    content_hash: bytes
    """SHA-256 digest of this side's captured contents."""


@dataclass(frozen=True)
class SnapshotFileRecord:
    """Immutable relational facts for one affected File.

    `path` is the absolute capture-directory path. At least one of `left` and
    `right` is present in a valid record; absence is represented by the absence
    of a row in the corresponding side table, not by a nullable database field.
    """

    id: str
    """Opaque File id."""

    snapshot_id: str
    """Snapshot containing this File."""

    path: str
    """Absolute filesystem path of this File's capture directory."""

    tracked: bool
    """Whether this File belongs to the backend's tracked input."""

    change_type: str
    """Backend classification retained without deriving renderer output."""

    error: Optional[str]
    """Persisted capture failure, or `None` when physical contents are valid."""

    left: Optional[SnapshotFileSideRecord]
    """Captured left side, when it exists."""

    right: Optional[SnapshotFileSideRecord]
    """Captured right side, when it exists."""


@dataclass(frozen=True)
class SnapshotRecord:
    """Complete relational input required to publish one Snapshot.

    The record contains Snapshot-wide metadata and the complete unordered set
    of Files. `RoomStore` validates membership before inserting any row.
    """

    id: str
    """Opaque Snapshot id."""

    content_hash: bytes
    """Digest identifying equal captured content inside one Room."""

    meta: SnapshotMetaRecord
    """Labels and aggregate line counts captured with this Snapshot."""

    files: tuple[SnapshotFileRecord, ...]
    """Complete set of captured Files, with no implied order."""


@dataclass(frozen=True)
class SnapshotMetaRecord:
    """One Snapshot's labels and backend-supplied aggregate line counts.

    Both counts are present together or absent together. They describe the
    selected Snapshot as a whole and never encode renderer-derived alignment.
    """

    left_label: str
    """Human-facing left label captured with the Snapshot."""

    right_label: str
    """Human-facing right label captured with the Snapshot."""

    added_lines: Optional[int]
    """Aggregate added lines supplied by the backend, when authoritative."""

    removed_lines: Optional[int]
    """Aggregate removed lines supplied by the backend, when authoritative."""


@dataclass(frozen=True)
class SnapshotFileLoadRecord:
    """One exact File lookup with its explicit lazy override.

    The record is returned only when both the Snapshot and repository filepath
    pair belong to the supplied Room identity.
    """

    file: SnapshotFileRecord
    """The repository-path-addressed File returned by lookup."""

    lazy_reason: Optional[str]
    """Explicit loading override for this File, when one was captured."""


class RoomStore:
    """Provide the complete relational interface for Room persistence.

    Each operation opens a short-lived SQLAlchemy session. The store publishes
    complete relational state transactionally but never touches captured files,
    applies correspondence rules, or performs rendering.
    """

    def __init__(self, engine: Engine) -> None:
        """Use `engine` for every short-lived Room persistence session.

        The caller controls engine and database lifetime; constructing the
        store performs no query and creates no tables.
        """
        self.engine = engine

    def room_identity(self, snapshot_id: str) -> Optional[RoomIdentity]:
        """Return the complete Room identity containing one Snapshot key.

        Snapshot ids are globally unique. The lookup intentionally requires no
        Mark, Tab, or correspondence input because follow-up operations already
        carry the immutable key returned by manifest.
        """
        with Session(self.engine) as session:
            row = session.execute(
                select(Room.mark_id, Room.tab, Room.backend_key)
                .join(Snapshot, Snapshot.room_id == Room.id)
                .where(Snapshot.id == snapshot_id)
            ).one_or_none()
        if row is None:
            return None
        return RoomIdentity(
            mark_id=row.mark_id,
            tab=row.tab,
            correspondence_key=row.backend_key,
        )

    def snapshot_id_for_content(
        self,
        identity: RoomIdentity,
        content_hash: bytes,
    ) -> Optional[str]:
        """Return the key of an equal published Snapshot in one Room.

        Equality is limited by the complete Room identity and captured-content
        digest. Absence means the caller may publish a new Snapshot.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            return session.execute(
                select(Snapshot.id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    mark_clause,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    Snapshot.content_hash == content_hash,
                )
            ).scalar_one_or_none()

    def snapshot_meta(
        self,
        identity: RoomIdentity,
        snapshot_id: str,
    ) -> Optional[SnapshotMetaRecord]:
        """Load Snapshot-wide metadata only for a key visible in one Room.

        Missing or cross-Room keys return `None`. This operation does not load
        File membership or contents, so callers needing only labels and totals
        do not scan the Snapshot's File relation.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            row = session.execute(
                select(
                    SnapshotMeta.left_label,
                    SnapshotMeta.right_label,
                    SnapshotMeta.added_lines,
                    SnapshotMeta.removed_lines,
                )
                .join(Snapshot, Snapshot.id == SnapshotMeta.snapshot_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    Snapshot.id == snapshot_id,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).one_or_none()
        if row is None:
            return None
        return SnapshotMetaRecord(
            left_label=row.left_label,
            right_label=row.right_label,
            added_lines=row.added_lines,
            removed_lines=row.removed_lines,
        )

    def publish(
        self,
        identity: RoomIdentity,
        snapshot: SnapshotRecord,
        *,
        lazy_reasons: dict[str, tuple[str, Optional[str]]],
    ) -> None:
        """Commit one complete Snapshot and its separate lazy-reason rows.

        `lazy_reasons` maps Snapshot File ids to the parsed reason and complete
        metadata content. Every key must identify a File in `snapshot`.
        """
        assert identity.tab in {
            "head",
            "refs",
            "branch-review",
            "pull-request",
            "preset",
        }, f"invalid Room Tab: {identity.tab}"
        assert (identity.mark_id is None) == (identity.tab == "preset"), (
            "only preset Rooms may omit a Mark"
        )
        assert identity.correspondence_key != b"", (
            "Room correspondence key cannot be empty"
        )
        assert len(snapshot.id) == 32 and set(snapshot.id) <= frozenset(
            "0123456789abcdef"
        ), f"invalid Snapshot id: {snapshot.id!r}"
        assert len(snapshot.content_hash) == 32, (
            "Snapshot content hash must have length 32"
        )
        assert (
            snapshot.meta.left_label != "" and snapshot.meta.right_label != ""
        ), "Snapshot labels cannot be empty"
        assert (snapshot.meta.added_lines is None) == (
            snapshot.meta.removed_lines is None
        ), "Snapshot aggregate line counts must have equal presence"
        if snapshot.meta.added_lines is not None:
            assert snapshot.meta.removed_lines is not None
            assert snapshot.meta.added_lines >= 0
            assert snapshot.meta.removed_lines >= 0
        file_ids: set[str] = set()
        storage_paths: set[str] = set()
        repository_paths: set[tuple[Optional[str], Optional[str]]] = set()
        for file in snapshot.files:
            assert file.snapshot_id == snapshot.id, (
                "Snapshot File must belong to its Snapshot"
            )
            assert len(file.id) == 32 and set(file.id) <= frozenset(
                "0123456789abcdef"
            ), f"invalid Snapshot File id: {file.id!r}"
            assert file.id not in file_ids, (
                f"duplicate Snapshot File id: {file.id!r}"
            )
            file_ids.add(file.id)
            assert Path(file.path).is_absolute(), (
                f"Snapshot File path must be absolute: {file.path!r}"
            )
            assert file.path not in storage_paths, (
                f"duplicate Snapshot File path: {file.path!r}"
            )
            storage_paths.add(file.path)
            assert isinstance(file.tracked, bool), (
                "Snapshot File tracked state must be a boolean"
            )
            assert file.change_type in {
                "modify",
                "add",
                "delete",
                "rename",
                "copy",
            }, f"invalid Snapshot File change type: {file.change_type!r}"
            assert file.error is None or file.error != "", (
                "Snapshot File error cannot be empty"
            )
            assert file.left is not None or file.right is not None, (
                "Snapshot File must have at least one captured side"
            )
            assert file.tracked or file.left is None, (
                "an intruding File cannot have a left side"
            )
            file_repository_paths = (
                file.left.repository_path if file.left is not None else None,
                file.right.repository_path if file.right is not None else None,
            )
            assert file_repository_paths not in repository_paths, (
                "duplicate Snapshot repository paths: "
                f"{file_repository_paths!r}"
            )
            repository_paths.add(file_repository_paths)
            for side in (file.left, file.right):
                if side is not None:
                    repository_path = PurePosixPath(side.repository_path)
                    assert (
                        side.repository_path not in {"", ".", ".."}
                        and not repository_path.is_absolute()
                        and ".." not in repository_path.parts
                    ), (
                        "Snapshot File path must be repository-relative: "
                        f"{side.repository_path!r}"
                    )
                    assert len(side.content_hash) == 32, (
                        "Snapshot File content hash must have length 32"
                    )
        assert lazy_reasons.keys() <= file_ids, (
            "lazy reasons must identify Files in the published Snapshot"
        )
        for reason, content in lazy_reasons.values():
            assert reason in {
                "too_big",
                "generated",
                "deleted",
                "untracked",
                "pure_renamed",
            }, f"invalid Snapshot File lazy reason: {reason!r}"
            assert content is None or content != "", (
                "Snapshot File lazy-reason content cannot be empty"
            )
            assert (identity.tab == "preset") == (content is not None), (
                "only preset lazy reasons carry complete metadata content"
            )

        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session, session.begin():
            room_id = session.execute(
                select(Room.id).where(
                    mark_clause,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                )
            ).scalar_one_or_none()
            if room_id is None:
                room_id = session.execute(
                    insert(Room)
                    .values(
                        mark_id=identity.mark_id,
                        tab=identity.tab,
                        backend_key=identity.correspondence_key,
                    )
                    .returning(Room.id)
                ).scalar_one()
            session.execute(
                insert(Snapshot).values(
                    id=snapshot.id,
                    room_id=room_id,
                    content_hash=snapshot.content_hash,
                )
            )
            session.execute(
                insert(SnapshotMeta).values(
                    snapshot_id=snapshot.id,
                    left_label=snapshot.meta.left_label,
                    right_label=snapshot.meta.right_label,
                    added_lines=snapshot.meta.added_lines,
                    removed_lines=snapshot.meta.removed_lines,
                )
            )
            if snapshot.files != ():
                session.execute(
                    insert(SnapshotFile),
                    [
                        {
                            "id": file.id,
                            "snapshot_id": snapshot.id,
                            "path": file.path,
                            "tracked": file.tracked,
                            "change_type": file.change_type,
                            "error": file.error,
                        }
                        for file in snapshot.files
                    ],
                )
            left_rows = [
                {
                    "file_id": file.id,
                    "repository_path": file.left.repository_path,
                    "content_hash": file.left.content_hash,
                }
                for file in snapshot.files
                if file.left is not None
            ]
            if left_rows != []:
                session.execute(insert(SnapshotFileLeft), left_rows)
            right_rows = [
                {
                    "file_id": file.id,
                    "repository_path": file.right.repository_path,
                    "content_hash": file.right.content_hash,
                }
                for file in snapshot.files
                if file.right is not None
            ]
            if right_rows != []:
                session.execute(insert(SnapshotFileRight), right_rows)
            lazy_reason_rows = [
                {
                    "file_id": file_id,
                    "reason": reason,
                }
                for file_id, (reason, _) in lazy_reasons.items()
            ]
            if lazy_reason_rows != []:
                session.execute(
                    insert(SnapshotFileLazyReason), lazy_reason_rows
                )
            lazy_reason_content_rows = [
                {"file_id": file_id, "content": content}
                for file_id, (_, content) in lazy_reasons.items()
                if content is not None
            ]
            if lazy_reason_content_rows != []:
                session.execute(
                    insert(SnapshotFileLazyReasonContent),
                    lazy_reason_content_rows,
                )

    def snapshot(
        self,
        identity: RoomIdentity,
        snapshot_id: str,
    ) -> Optional[SnapshotRecord]:
        """Load a complete Snapshot only when it belongs to `identity`.

        The result includes every captured File and side. Missing or cross-Room
        keys return `None`; no substitute Snapshot is selected.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            snapshot_row = session.execute(
                select(
                    Snapshot.id,
                    Snapshot.content_hash,
                    SnapshotMeta.left_label,
                    SnapshotMeta.right_label,
                    SnapshotMeta.added_lines,
                    SnapshotMeta.removed_lines,
                )
                .join(Room, Room.id == Snapshot.room_id)
                .join(
                    SnapshotMeta,
                    SnapshotMeta.snapshot_id == Snapshot.id,
                )
                .where(
                    Snapshot.id == snapshot_id,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).one_or_none()
            if snapshot_row is None:
                return None
            file_rows = session.execute(
                select(
                    SnapshotFile.id,
                    SnapshotFile.snapshot_id,
                    SnapshotFile.path,
                    SnapshotFile.tracked,
                    SnapshotFile.change_type,
                    SnapshotFile.error,
                    SnapshotFileLeft.repository_path.label("left_path"),
                    SnapshotFileLeft.content_hash.label("left_hash"),
                    SnapshotFileRight.repository_path.label("right_path"),
                    SnapshotFileRight.content_hash.label("right_hash"),
                )
                .outerjoin(
                    SnapshotFileLeft,
                    SnapshotFileLeft.file_id == SnapshotFile.id,
                )
                .outerjoin(
                    SnapshotFileRight,
                    SnapshotFileRight.file_id == SnapshotFile.id,
                )
                .where(SnapshotFile.snapshot_id == snapshot_id)
            ).all()

        files: list[SnapshotFileRecord] = []
        for row in file_rows:
            assert (row.left_path is None) == (row.left_hash is None), (
                "persisted left File path and hash must have equal presence"
            )
            assert (row.right_path is None) == (row.right_hash is None), (
                "persisted right File path and hash must have equal presence"
            )
            left = (
                SnapshotFileSideRecord(row.left_path, row.left_hash)
                if row.left_path is not None and row.left_hash is not None
                else None
            )
            right = (
                SnapshotFileSideRecord(row.right_path, row.right_hash)
                if row.right_path is not None and row.right_hash is not None
                else None
            )
            assert left is not None or right is not None, (
                f"persisted Snapshot File has no sides: {row.id!r}"
            )
            files.append(
                SnapshotFileRecord(
                    id=row.id,
                    snapshot_id=row.snapshot_id,
                    path=row.path,
                    tracked=row.tracked,
                    change_type=row.change_type,
                    error=row.error,
                    left=left,
                    right=right,
                )
            )
        return SnapshotRecord(
            id=snapshot_row.id,
            content_hash=snapshot_row.content_hash,
            meta=SnapshotMetaRecord(
                left_label=snapshot_row.left_label,
                right_label=snapshot_row.right_label,
                added_lines=snapshot_row.added_lines,
                removed_lines=snapshot_row.removed_lines,
            ),
            files=tuple(files),
        )

    def snapshot_lazy_reasons(
        self,
        identity: RoomIdentity,
        snapshot_id: str,
    ) -> dict[str, str]:
        """Return explicit lazy reasons for Files in one visible Snapshot.

        Missing, cross-Room, and reason-less Snapshots produce no rows. Callers
        that require Snapshot existence validate it through `snapshot` first.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    SnapshotFileLazyReason.file_id,
                    SnapshotFileLazyReason.reason,
                )
                .join(
                    SnapshotFile,
                    SnapshotFile.id == SnapshotFileLazyReason.file_id,
                )
                .join(Snapshot, Snapshot.id == SnapshotFile.snapshot_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    Snapshot.id == snapshot_id,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).all()
        return {row.file_id: row.reason for row in rows}

    def snapshot_file(
        self,
        identity: RoomIdentity,
        *,
        snapshot_id: str,
        left_path: Optional[str],
        right_path: Optional[str],
    ) -> tuple[bool, Optional[SnapshotFileLoadRecord]]:
        """Return Snapshot existence and one repository-path-addressed File.

        `(False, None)` means the Snapshot is not visible in this Room.
        `(True, None)` means it is visible but has no matching File.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            row = session.execute(
                select(
                    SnapshotFile.id,
                    SnapshotFile.snapshot_id,
                    SnapshotFile.path,
                    SnapshotFile.tracked,
                    SnapshotFile.change_type,
                    SnapshotFile.error,
                    SnapshotFileLeft.repository_path.label("left_path"),
                    SnapshotFileLeft.content_hash.label("left_hash"),
                    SnapshotFileRight.repository_path.label("right_path"),
                    SnapshotFileRight.content_hash.label("right_hash"),
                    SnapshotFileLazyReason.reason.label("lazy_reason"),
                )
                .join(Snapshot, Snapshot.id == SnapshotFile.snapshot_id)
                .join(Room, Room.id == Snapshot.room_id)
                .outerjoin(
                    SnapshotFileLeft,
                    SnapshotFileLeft.file_id == SnapshotFile.id,
                )
                .outerjoin(
                    SnapshotFileRight,
                    SnapshotFileRight.file_id == SnapshotFile.id,
                )
                .outerjoin(
                    SnapshotFileLazyReason,
                    SnapshotFileLazyReason.file_id == SnapshotFile.id,
                )
                .where(
                    Snapshot.id == snapshot_id,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                    (
                        SnapshotFileLeft.file_id.is_(None)
                        if left_path is None
                        else SnapshotFileLeft.repository_path == left_path
                    ),
                    (
                        SnapshotFileRight.file_id.is_(None)
                        if right_path is None
                        else SnapshotFileRight.repository_path == right_path
                    ),
                )
            ).one_or_none()
            if row is None:
                exists = (
                    session.execute(
                        select(Snapshot.id)
                        .join(Room, Room.id == Snapshot.room_id)
                        .where(
                            Snapshot.id == snapshot_id,
                            Room.tab == identity.tab,
                            Room.backend_key == identity.correspondence_key,
                            mark_clause,
                        )
                    ).one_or_none()
                    is not None
                )
                return exists, None

        assert (row.left_path is None) == (row.left_hash is None), (
            "persisted left File path and hash must have equal presence"
        )
        assert (row.right_path is None) == (row.right_hash is None), (
            "persisted right File path and hash must have equal presence"
        )
        left = (
            SnapshotFileSideRecord(row.left_path, row.left_hash)
            if row.left_path is not None and row.left_hash is not None
            else None
        )
        right = (
            SnapshotFileSideRecord(row.right_path, row.right_hash)
            if row.right_path is not None and row.right_hash is not None
            else None
        )
        assert left is not None or right is not None, (
            f"persisted Snapshot File has no sides: {row.id!r}"
        )
        return True, SnapshotFileLoadRecord(
            file=SnapshotFileRecord(
                id=row.id,
                snapshot_id=row.snapshot_id,
                path=row.path,
                tracked=row.tracked,
                change_type=row.change_type,
                error=row.error,
                left=left,
                right=right,
            ),
            lazy_reason=row.lazy_reason,
        )
