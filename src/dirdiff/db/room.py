"""Relational persistence for Rooms, Snapshots, Files, and review discussions.

`RoomStore` is the public database interface. A `SnapshotFile` row represents
one affected File and records the absolute path of its capture directory.
Separate left and right relations record whichever captured sides exist,
without nullable side columns or a discriminator.

Review relations retain represented Thread/Snapshot pairs and append-only
actions authored by an existing Profile. This
module owns SQLAlchemy tables, queries, and transactions only. It does not
apply Tab rules, call workspace backends, hash or load contents, manage
directories or locks, derive manifest output, or change repository-mark
deletion behavior. Those responsibilities remain outside `dirdiff.db`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Engine,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    and_,
    case,
    column,
    func,
    insert,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy.engine import Row
from sqlalchemy.orm import Mapped, Session, aliased, mapped_column

from dirdiff.db.base import (
    TableBase,
    UserProfile,
    UserProfileRecord,
    profile_record,
)

__all__ = [
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
]


class Room(TableBase):
    """Persist one Room identity selected by a Tab's correspondence law.

    Repository Rooms reference a Mark; preset Rooms omit it. `backend_key` is
    opaque to SQLite and is used only for exact equality inside a Tab.
    """

    __tablename__ = "room"
    __table_args__ = (
        CheckConstraint(
            column("tab").in_(
                ("head", "refs", "branch-review", "pull-request", "preset")
            ),
            name="ck_room_tab",
        ),
        CheckConstraint(
            column("mark_id").is_(None) == (column("tab") == "preset"),
            name="ck_room_mark_tab",
        ),
        CheckConstraint(
            func.length(column("backend_key")) > 0,
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
            (func.length(column("id")) == 32)
            & column("id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_snapshot_id",
        ),
        CheckConstraint(
            func.length(column("content_hash")) == 32,
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
            (func.length(column("left_label")) > 0)
            & (func.length(column("right_label")) > 0),
            name="ck_snapshot_meta_labels",
        ),
        CheckConstraint(
            (
                column("added_lines").is_(None)
                & column("removed_lines").is_(None)
            )
            | (
                column("added_lines").is_not(None)
                & column("removed_lines").is_not(None)
                & (column("added_lines") >= 0)
                & (column("removed_lines") >= 0)
            ),
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
        UniqueConstraint(
            "id",
            "snapshot_id",
            name="uq_snapshot_file_id_snapshot",
        ),
        CheckConstraint(
            (func.length(column("id")) == 32)
            & column("id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_snapshot_file_id",
        ),
        CheckConstraint(
            func.substr(column("path"), 1, 1) == "/",
            name="ck_snapshot_file_path",
        ),
        CheckConstraint(
            column("change_type").in_(
                ("modify", "add", "delete", "rename", "copy")
            ),
            name="ck_snapshot_file_change_type",
        ),
        CheckConstraint(
            column("error").is_(None) | (func.length(column("error")) > 0),
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
Index("ix_snapshot_room", Snapshot.room_id)


def _captured_side_constraints(side: str) -> tuple[CheckConstraint, ...]:
    """Build the identical validity constraints of one captured-side table.

    Both side tables persist a repository-relative path (non-empty, relative,
    free of `..` traversal) and a 32-byte content digest; only the constraint
    names differ by side. One builder keeps the two tables' contracts from
    drifting apart.
    """
    return (
        CheckConstraint(
            (func.length(column("repository_path")) > 0)
            & (func.substr(column("repository_path"), 1, 1) != "/")
            & (column("repository_path") != ".")
            & (column("repository_path") != "..")
            & column("repository_path").not_like("../%")
            & column("repository_path").not_like("%/../%")
            & column("repository_path").not_like("%/.."),
            name=f"ck_snapshot_file_{side}_repository_path",
        ),
        CheckConstraint(
            func.length(column("content_hash")) == 32,
            name=f"ck_snapshot_file_{side}_content_hash",
        ),
    )


class SnapshotFileLeft(TableBase):
    """Persist a captured File's left repository path and content digest.

    Absence of this row means the File has no left side; the relation never
    represents that absence with nullable columns.
    """

    __tablename__ = "snapshot_file_left"
    __table_args__ = _captured_side_constraints("left")

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
    __table_args__ = _captured_side_constraints("right")

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
            column("reason").in_(
                ("too_big", "generated", "deleted", "untracked", "pure_renamed")
            ),
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
            func.length(column("content")) > 0,
            name="ck_snapshot_file_lazy_reason_content",
        ),
    )

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file_lazy_reason.file_id"),
        primary_key=True,
    )
    content: Mapped[str] = mapped_column(String, nullable=False)


class ReviewThread(TableBase):
    """Persist one logical Thread and the Snapshot where it originated."""

    __tablename__ = "review_thread"
    __table_args__ = (
        CheckConstraint(
            (func.length(column("thread_id")) == 32)
            & column("thread_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_thread_id",
        ),
    )

    thread_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    origin_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"), nullable=False
    )


Index("ix_review_thread_origin_snapshot", ReviewThread.origin_snapshot_id)


class ReviewThreadPlacement(TableBase):
    """Persist one immutable code placement for a Thread in one Snapshot."""

    __tablename__ = "review_thread_placement"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_file_id", "snapshot_id"],
            ["snapshot_file.id", "snapshot_file.snapshot_id"],
            name="fk_review_thread_placement_snapshot_file",
        ),
        CheckConstraint(
            (func.length(column("thread_id")) == 32)
            & column("thread_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_thread_placement_id",
        ),
        CheckConstraint(
            column("target_kind").is_(None)
            | column("target_kind").in_(("range", "file-start")),
            name="ck_review_thread_target_kind",
        ),
        CheckConstraint(
            column("region_kind").is_(None)
            | column("region_kind").in_(("ordinary", "notebook-cell-source")),
            name="ck_review_thread_region_kind",
        ),
        CheckConstraint(
            column("side").is_(None) | column("side").in_(("left", "right")),
            name="ck_review_thread_side",
        ),
        CheckConstraint(
            column("outdated_reason").is_(None)
            | column("outdated_reason").in_(
                ("region_changed", "region_not_found", "file_missing")
            ),
            name="ck_review_thread_outdated_reason",
        ),
        CheckConstraint(
            case(
                (
                    column("region_kind") == "ordinary",
                    column("region_key").is_(None),
                ),
                (
                    column("region_kind") == "notebook-cell-source",
                    column("region_key").is_not(None)
                    & (func.length(column("region_key")) > 0),
                ),
                (
                    column("region_kind").is_(None),
                    column("region_key").is_(None),
                ),
                else_=False,
            ),
            name="ck_review_thread_region",
        ),
        CheckConstraint(
            case(
                (
                    column("snapshot_file_id").is_(None),
                    column("target_kind").is_(None)
                    & column("region_kind").is_(None)
                    & column("region_key").is_(None)
                    & column("side").is_(None)
                    & column("start_line").is_(None)
                    & column("end_line").is_(None)
                    & column("outdated_reason").is_not(None)
                    & (column("outdated_reason") == "file_missing"),
                ),
                (
                    column("target_kind") == "range",
                    column("region_kind").is_not(None)
                    & column("side").is_not(None)
                    & column("start_line").is_not(None)
                    & (column("start_line") >= 1)
                    & column("end_line").is_not(None)
                    & (column("end_line") >= column("start_line"))
                    & (
                        column("outdated_reason").is_(None)
                        | (column("outdated_reason") == "region_changed")
                    ),
                ),
                (
                    column("target_kind") == "file-start",
                    column("region_kind").is_(None)
                    & column("region_key").is_(None)
                    & column("side").is_not(None)
                    & column("start_line").is_(None)
                    & column("end_line").is_(None)
                    & (
                        column("outdated_reason").is_(None)
                        | (column("outdated_reason") == "region_not_found")
                    ),
                ),
                else_=False,
            ),
            name="ck_review_thread_location",
        ),
        CheckConstraint(
            column("private_locator").is_(None)
            | (
                (column("target_kind") == "range")
                & column("outdated_reason").is_(None)
            ),
            name="ck_review_thread_placement_locator",
        ),
    )

    thread_id: Mapped[str] = mapped_column(
        ForeignKey("review_thread.thread_id"), primary_key=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"), primary_key=True
    )
    snapshot_file_id: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    target_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    region_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    region_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    start_line: Mapped[Optional[int]] = mapped_column(nullable=True)
    end_line: Mapped[Optional[int]] = mapped_column(nullable=True)
    outdated_reason: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    private_locator: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )


Index("ix_review_thread_placement_snapshot", ReviewThreadPlacement.snapshot_id)


class ReviewAction(TableBase):
    """Persist one authored operation in global Room activity order."""

    __tablename__ = "review_action"
    __table_args__ = (
        ForeignKeyConstraint(
            ["thread_id", "snapshot_id"],
            [
                "review_thread_placement.thread_id",
                "review_thread_placement.snapshot_id",
            ],
            name="fk_review_action_thread",
        ),
        UniqueConstraint("activity_id", name="uq_review_action_activity"),
        UniqueConstraint(
            "thread_id",
            "sequence",
            name="uq_review_action_sequence",
        ),
        CheckConstraint(
            (func.length(column("operation_id")) == 32)
            & column("operation_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_action_operation_id",
        ),
        CheckConstraint(
            (func.length(column("thread_id")) == 32)
            & column("thread_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_action_thread_id",
        ),
        CheckConstraint(
            column("snapshot_id").is_not(None)
            & (func.length(column("snapshot_id")) == 32)
            & column("snapshot_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_action_snapshot_id",
        ),
        CheckConstraint(
            column("profile_id") > 0,
            name="ck_review_action_profile_id",
        ),
        CheckConstraint(
            column("comment_id").is_(None)
            | (
                (func.length(column("comment_id")) == 32)
                & column("comment_id").op("NOT GLOB")("*[^0-9a-f]*")
            ),
            name="ck_review_action_comment_id",
        ),
        CheckConstraint(
            (column("sequence") >= 0)
            & (
                column("expected_revision").is_(None)
                | (column("expected_revision") >= 0)
            ),
            name="ck_review_action_revisions",
        ),
        CheckConstraint(
            case(
                (
                    column("kind").in_(("thread-created", "comment-created")),
                    column("thread_id").is_not(None)
                    & column("sequence").is_not(None)
                    & column("comment_id").is_not(None)
                    & column("expected_revision").is_(None)
                    & column("body").is_not(None)
                    & (func.length(column("body")) > 0),
                ),
                (
                    column("kind") == "comment-edited",
                    column("thread_id").is_not(None)
                    & column("sequence").is_not(None)
                    & column("comment_id").is_not(None)
                    & column("expected_revision").is_not(None)
                    & column("body").is_not(None)
                    & (func.length(column("body")) > 0),
                ),
                (
                    column("kind") == "comment-deleted",
                    column("thread_id").is_not(None)
                    & column("sequence").is_not(None)
                    & column("comment_id").is_not(None)
                    & column("expected_revision").is_not(None)
                    & column("body").is_(None),
                ),
                (
                    column("kind").in_(("thread-resolved", "thread-reopened")),
                    column("thread_id").is_not(None)
                    & column("sequence").is_not(None)
                    & column("expected_revision").is_(None)
                    & (
                        (
                            column("comment_id").is_(None)
                            & column("body").is_(None)
                        )
                        | (
                            column("comment_id").is_not(None)
                            & column("body").is_not(None)
                            & (func.length(column("body")) > 0)
                        )
                    ),
                ),
                (
                    column("kind") == "thread-deleted",
                    column("thread_id").is_not(None)
                    & column("sequence").is_not(None)
                    & column("comment_id").is_(None)
                    & column("expected_revision").is_(None)
                    & column("body").is_(None),
                ),
                else_=False,
            ),
            name="ck_review_action_variant",
        ),
        CheckConstraint(
            column("status_after").in_(("open", "resolved", "deleted")),
            name="ck_review_action_status_after",
        ),
        CheckConstraint(
            column("attention_after").in_(
                ("author", "reviewer", "both", "none")
            ),
            name="ck_review_action_attention_after",
        ),
        CheckConstraint(
            func.length(column("created_at")) > 0,
            name="ck_review_action_created_at",
        ),
    )

    operation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"), nullable=False
    )
    comment_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    expected_revision: Mapped[Optional[int]] = mapped_column(nullable=True)
    body: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    status_after: Mapped[str] = mapped_column(String, nullable=False)
    attention_after: Mapped[str] = mapped_column(String, nullable=False)


Index(
    "uq_review_action_comment_created",
    ReviewAction.comment_id,
    unique=True,
    sqlite_where=ReviewAction.kind.in_(
        (
            "thread-created",
            "comment-created",
            "thread-resolved",
            "thread-reopened",
        )
    ),
)
Index(
    "ix_review_action_thread_activity",
    ReviewAction.thread_id,
    ReviewAction.activity_id.desc(),
)
Index(
    "ix_review_action_snapshot_activity",
    ReviewAction.snapshot_id,
    ReviewAction.activity_id,
)


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


@dataclass(frozen=True)
class ReviewThreadRecord:
    """Immutable placement facts for one Thread and Snapshot pair.

    A missing `snapshot_file_id` is valid only for `file_missing`.  Private
    locator fields occur only on a text Thread's unique origin record.
    """

    thread_id: str
    snapshot_id: str
    snapshot_file_id: Optional[str]
    is_origin: bool
    target_kind: Optional[Literal["range", "file-start"]]
    region_kind: Optional[Literal["ordinary", "notebook-cell-source"]]
    region_key: Optional[str]
    side: Optional[Literal["left", "right"]]
    start_line: Optional[int]
    end_line: Optional[int]
    outdated_reason: Optional[
        Literal["region_changed", "region_not_found", "file_missing"]
    ]
    private_locator: Optional[bytes]


@dataclass(frozen=True)
class ReviewActionRecord:
    """One immutable authored operation in a live Thread discussion."""

    operation_id: str
    thread_id: str
    snapshot_id: str
    sequence: int
    kind: Literal[
        "thread-created",
        "comment-created",
        "comment-edited",
        "comment-deleted",
        "thread-resolved",
        "thread-reopened",
        "thread-deleted",
    ]
    profile_id: int
    comment_id: Optional[str]
    expected_revision: Optional[int]
    body: Optional[str]
    created_at: str
    status_after: Literal["open", "resolved", "deleted"]
    attention_after: Literal["author", "reviewer", "both", "none"]
    activity_id: Optional[int] = None
    """Global Room order after persistence; `None` only before insertion."""

    def __post_init__(self) -> None:
        """Require one valid relational Profile identity."""
        assert self.profile_id > 0


@dataclass(frozen=True)
class ReviewThreadsRecord:
    """Bulk persistence result for all Threads visible in one Snapshot.

    `threads` contains exactly one selected placement per discussion. `origins`
    contains the unique origin placements for those discussions, while actions,
    Profiles contain every fact needed to fold their live state.
    """

    threads: tuple[ReviewThreadRecord, ...]
    origins: tuple[ReviewThreadRecord, ...]
    actions: tuple[ReviewActionRecord, ...]
    profiles: tuple[UserProfileRecord, ...]
    total_threads: int
    """Number of placements before page bounds are applied."""


class RoomStore:
    """Provide the complete relational interface for Room persistence.

    Each operation opens a short-lived SQLAlchemy session. The store publishes
    complete relational state transactionally but never touches captured files,
    applies correspondence rules, folds discussion actions, interprets private
    source coordinates, or performs rendering.
    """

    def __init__(self, engine: Engine) -> None:
        """Use `engine` for every short-lived Room persistence session.

        The caller controls engine and database lifetime; constructing the
        store performs no query and creates no tables.
        """
        self.engine = engine

    @staticmethod
    def _review_thread_values(record: ReviewThreadRecord) -> dict[str, object]:
        """Translate one immutable Thread placement into insert values."""
        return {
            "thread_id": record.thread_id,
            "snapshot_id": record.snapshot_id,
            "snapshot_file_id": record.snapshot_file_id,
            "target_kind": record.target_kind,
            "region_kind": record.region_kind,
            "region_key": record.region_key,
            "side": record.side,
            "start_line": record.start_line,
            "end_line": record.end_line,
            "outdated_reason": record.outdated_reason,
            "private_locator": record.private_locator,
        }

    @staticmethod
    def _review_action_values(record: ReviewActionRecord) -> dict[str, object]:
        """Translate one immutable authored action into insert values."""
        values: dict[str, object] = {
            "operation_id": record.operation_id,
            "thread_id": record.thread_id,
            "snapshot_id": record.snapshot_id,
            "sequence": record.sequence,
            "kind": record.kind,
            "profile_id": record.profile_id,
            "comment_id": record.comment_id,
            "expected_revision": record.expected_revision,
            "body": record.body,
            "created_at": record.created_at,
            "status_after": record.status_after,
            "attention_after": record.attention_after,
        }
        if record.activity_id is not None:
            values["activity_id"] = record.activity_id
        return values

    @staticmethod
    def _next_review_activity_id(session: Session) -> int:
        """Return the next durable authored-action order in this database."""
        latest = session.execute(
            select(func.max(ReviewAction.activity_id))
        ).scalar_one()
        return 1 if latest is None else latest + 1

    @staticmethod
    def _thread_record(row: Row[Any]) -> ReviewThreadRecord:
        """Validate one selected database row as a Thread placement record."""
        target_kind_value = row.target_kind
        match target_kind_value:
            case "range" | "file-start" | None:
                target_kind = target_kind_value
            case _:
                raise AssertionError(
                    f"invalid persisted review target kind: {target_kind_value!r}"
                )
        region_kind_value = row.region_kind
        match region_kind_value:
            case "ordinary" | "notebook-cell-source" | None:
                region_kind = region_kind_value
            case _:
                raise AssertionError(
                    f"invalid persisted review region kind: {region_kind_value!r}"
                )
        side_value = row.side
        match side_value:
            case "left" | "right" | None:
                side = side_value
            case _:
                raise AssertionError(
                    f"invalid persisted review side: {side_value!r}"
                )
        reason_value = row.outdated_reason
        match reason_value:
            case "region_changed" | "region_not_found" | "file_missing" | None:
                outdated_reason = reason_value
            case _:
                raise AssertionError(
                    f"invalid persisted outdated reason: {reason_value!r}"
                )
        return ReviewThreadRecord(
            thread_id=row.thread_id,
            snapshot_id=row.snapshot_id,
            snapshot_file_id=row.snapshot_file_id,
            is_origin=row.is_origin,
            target_kind=target_kind,
            region_kind=region_kind,
            region_key=row.region_key,
            side=side,
            start_line=row.start_line,
            end_line=row.end_line,
            outdated_reason=outdated_reason,
            private_locator=row.private_locator,
        )

    @staticmethod
    def _action_record(row: Row[Any]) -> ReviewActionRecord:
        """Validate one selected database row as an authored action record."""
        assert row.thread_id is not None
        assert row.sequence is not None
        kind_value = row.kind
        match kind_value:
            case (
                "comment-created"
                | "thread-created"
                | "comment-edited"
                | "comment-deleted"
                | "thread-resolved"
                | "thread-reopened"
                | "thread-deleted"
            ):
                kind = kind_value
            case _:
                raise AssertionError(
                    f"invalid persisted review action kind: {kind_value!r}"
                )
        return ReviewActionRecord(
            operation_id=row.operation_id,
            thread_id=row.thread_id,
            snapshot_id=row.snapshot_id,
            sequence=row.sequence,
            kind=kind,
            profile_id=row.profile_id,
            comment_id=row.comment_id,
            expected_revision=row.expected_revision,
            body=row.body,
            created_at=row.created_at,
            status_after=row.status_after,
            attention_after=row.attention_after,
            activity_id=row.activity_id,
        )

    @staticmethod
    def _file_record(row: Row[Any]) -> SnapshotFileRecord:
        """Validate one joined File/side row into the shared record shape."""
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
        return SnapshotFileRecord(
            id=row.id,
            snapshot_id=row.snapshot_id,
            path=row.path,
            tracked=row.tracked,
            change_type=row.change_type,
            error=row.error,
            left=left,
            right=right,
        )

    def review_profile(self, profile_id: int) -> Optional[UserProfileRecord]:
        """Return one current Profile identity, or `None` when absent.

        Review writes use this boundary to reject a missing browser author
        before attempting an action whose foreign key could not be satisfied.
        """
        with Session(self.engine) as session:
            row = session.execute(
                select(
                    UserProfile.id,
                    UserProfile.username,
                ).where(UserProfile.id == profile_id)
            ).one_or_none()
        if row is None:
            return None
        return profile_record(row.id, row.username)

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
        review_threads: tuple[ReviewThreadRecord, ...],
    ) -> None:
        """Commit one complete Snapshot and every dependent immutable row.

        `lazy_reasons` maps Snapshot File ids to the parsed reason and complete
        metadata content. Every review placement must address this Snapshot and
        may reference only one of its Files. The one transaction makes the
        complete Snapshot universe visible at once.
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
        for thread in review_threads:
            assert thread.snapshot_id == snapshot.id, (
                "published review Thread must address the new Snapshot"
            )
            assert not thread.is_origin, (
                "a new Snapshot cannot contain an existing Thread origin"
            )
            assert (
                thread.snapshot_file_id is None
                or thread.snapshot_file_id in file_ids
            ), "published review Thread references a File outside its Snapshot"

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
            if review_threads != ():
                session.execute(
                    insert(ReviewThreadPlacement),
                    [self._review_thread_values(row) for row in review_threads],
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

        files = [self._file_record(row) for row in file_rows]
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

        return True, SnapshotFileLoadRecord(
            file=self._file_record(row),
            lazy_reason=row.lazy_reason,
        )

    def snapshot_files_by_ids(
        self,
        identity: RoomIdentity,
        *,
        snapshot_id: str,
        file_ids: tuple[str, ...],
    ) -> dict[str, SnapshotFileRecord]:
        """Return the id-addressed Files of a Snapshot visible in one Room.

        One read serves every requested id; ids naming no File of that
        visible Snapshot are simply absent from the result, and the caller
        decides which failure an absence is.
        """
        if file_ids == ():
            return {}
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            rows = session.execute(
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
                .where(
                    Snapshot.id == snapshot_id,
                    SnapshotFile.id.in_(set(file_ids)),
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).all()
        return {row.id: self._file_record(row) for row in rows}

    def snapshot_files_by_pairs(
        self,
        identity: RoomIdentity,
        *,
        snapshot_id: str,
        pairs: tuple[tuple[Optional[str], Optional[str]], ...],
    ) -> tuple[
        bool, dict[tuple[Optional[str], Optional[str]], SnapshotFileRecord]
    ]:
        """Return Snapshot existence and its pair-addressed Files in one read.

        Each pair is an exact nullable left/right repository-path pair; pairs
        naming no File of the visible Snapshot are absent from the result.
        `(False, {})` means the Snapshot is not visible in this Room. Nullable
        sides make a tuple IN unusable, so the pairs become one OR of exact
        per-pair conditions inside a single query and transaction.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        pair_clauses = [
            and_(
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
            for left_path, right_path in dict.fromkeys(pairs)
        ]
        with Session(self.engine) as session:
            rows = (
                session.execute(
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
                    .where(
                        Snapshot.id == snapshot_id,
                        Room.tab == identity.tab,
                        Room.backend_key == identity.correspondence_key,
                        mark_clause,
                        or_(*pair_clauses),
                    )
                ).all()
                if pair_clauses != []
                else []
            )
            found = {}
            for row in rows:
                record = self._file_record(row)
                left = (
                    record.left.repository_path
                    if record.left is not None
                    else None
                )
                right = (
                    record.right.repository_path
                    if record.right is not None
                    else None
                )
                found[(left, right)] = record
            # The one-row query this replaced ended in one_or_none(), which
            # raised on duplicate pairs; the schema does not make the pair
            # unique, so a collision must still fail instead of silently
            # binding to an arbitrary duplicate.
            assert len(found) == len(rows), (
                "Snapshot contains duplicate File pairs"
            )
            if rows == []:
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
                return exists, {}
        return True, found

    def review_thread_files(
        self,
        selected_snapshot_id: str,
        origin_refs: tuple[tuple[str, str], ...],
        located_file_ids: tuple[str, ...],
        absent_origin_refs: tuple[tuple[str, str], ...],
    ) -> tuple[
        dict[tuple[str, str], SnapshotFileRecord],
        dict[str, SnapshotFileRecord],
        tuple[str, ...],
    ]:
        """Load exactly the Snapshot Files one Thread page references.

        `origin_refs` are `(origin_snapshot_id, snapshot_file_id)` pairs for
        the page's Thread origins; `located_file_ids` are the placement File
        ids inside `selected_snapshot_id`. `absent_origin_refs` identify
        origins of file-missing placements: for each, the selected Snapshot
        is searched for a File with the identical left/right repository-path
        pair, and any match is returned as a conflict id so the caller can
        reject the placement invariant violation instead of substituting
        data. Every requested origin must exist; missing rows fail the read.
        Room visibility of the Snapshot is the caller's responsibility, as
        with `review_threads`.
        """

        def file_query() -> Any:
            return (
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
            )

        with Session(self.engine) as session:
            origin_rows = (
                session.execute(
                    file_query().where(
                        tuple_(SnapshotFile.snapshot_id, SnapshotFile.id).in_(
                            set(origin_refs)
                        )
                    )
                ).all()
                if origin_refs != ()
                else []
            )
            selected_rows = (
                session.execute(
                    file_query().where(
                        SnapshotFile.snapshot_id == selected_snapshot_id,
                        SnapshotFile.id.in_(set(located_file_ids)),
                    )
                ).all()
                if located_file_ids != ()
                else []
            )
            origin_files = {
                (row.snapshot_id, row.id): self._file_record(row)
                for row in origin_rows
            }
            assert origin_files.keys() == set(origin_refs), (
                "review origin references a missing Snapshot File"
            )
            conflict_ids: tuple[str, ...] = ()
            absent_pairs: set[tuple[Optional[str], Optional[str]]] = set()
            for ref in absent_origin_refs:
                origin_left = origin_files[ref].left
                origin_right = origin_files[ref].right
                absent_pairs.add(
                    (
                        origin_left.repository_path
                        if origin_left is not None
                        else None,
                        origin_right.repository_path
                        if origin_right is not None
                        else None,
                    )
                )
            if absent_pairs != set():
                pair_clauses = [
                    and_(
                        SnapshotFileLeft.file_id.is_(None)
                        if left_path is None
                        else SnapshotFileLeft.repository_path == left_path,
                        SnapshotFileRight.file_id.is_(None)
                        if right_path is None
                        else SnapshotFileRight.repository_path == right_path,
                    )
                    for left_path, right_path in absent_pairs
                ]
                conflict_ids = tuple(
                    session.execute(
                        select(SnapshotFile.id)
                        .outerjoin(
                            SnapshotFileLeft,
                            SnapshotFileLeft.file_id == SnapshotFile.id,
                        )
                        .outerjoin(
                            SnapshotFileRight,
                            SnapshotFileRight.file_id == SnapshotFile.id,
                        )
                        .where(
                            SnapshotFile.snapshot_id == selected_snapshot_id,
                            or_(*pair_clauses),
                        )
                    ).scalars()
                )
        return (
            origin_files,
            {row.id: self._file_record(row) for row in selected_rows},
            conflict_ids,
        )

    def review_threads(
        self,
        identity: RoomIdentity,
        snapshot_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        state: Literal["all", "open"] = "all",
        attention: Optional[Literal["author", "reviewer"]] = None,
        *,
        through_activity_id: Optional[int],
    ) -> Optional[tuple[ReviewThreadsRecord, int]]:
        """Load one ordered, bounded set of Threads from a Snapshot.

        `identity` must identify the Room containing `snapshot_id`.
        `offset` and `limit` select a slice after ordering open, resolved, and
        deleted Threads  by lifecycle state and creation activity.
        `state="open"` excludes resolved and deleted Threads.

        `through_activity_id` is an inclusive pivot over the Room's append-only
        review actions.
        Thread existence, lifecycle state, ordering, count, and returned actions
        are all reconstructed using only actions at or before that pivot, so
        separate page reads observe one stable discussion state.
        `None` chooses the greatest current Room activity in this same database
        session. The concrete pivot is returned for use by subsequent pages.

        This function returns `None` when the Snapshot does not belong to the
        supplied Room.
        Otherwise returns the selected placements, origins, actions, Profiles,
        total matching Thread count, and concrete inclusive pivot.
        """
        assert through_activity_id is None or through_activity_id >= 0, (
            "review activity pivot must be nonnegative"
        )
        # Preset Rooms have no Mark; SQL NULL equality requires an explicit
        # predicate.
        matching_mark = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            room_id = session.execute(
                select(Snapshot.room_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    Snapshot.id == snapshot_id,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    matching_mark,
                )
            ).scalar_one_or_none()
            if room_id is None:
                return None
            if through_activity_id is None:
                latest_activity_id = session.execute(
                    select(func.max(ReviewAction.activity_id))
                    .join(Snapshot, Snapshot.id == ReviewAction.snapshot_id)
                    .where(Snapshot.room_id == room_id)
                ).scalar_one()
                through_activity_id = (
                    0 if latest_activity_id is None else latest_activity_id
                )
            # One grouped pass over the pivot-bounded actions yields each
            # Thread's latest activity and creation activity; joining the
            # latest action row then supplies status and attention. The
            # previous shape probed several correlated subqueries per
            # candidate placement, so page cost grew with total review
            # history rather than page size.
            latest = (
                select(
                    ReviewAction.thread_id.label("thread_id"),
                    func.max(ReviewAction.activity_id).label(
                        "last_activity_id"
                    ),
                    func.min(
                        case(
                            (
                                ReviewAction.sequence == 0,
                                ReviewAction.activity_id,
                            )
                        )
                    ).label("first_activity"),
                )
                .where(ReviewAction.activity_id <= through_activity_id)
                .group_by(ReviewAction.thread_id)
                .subquery()
            )
            last_action = aliased(ReviewAction)
            state_rank = case(
                (last_action.status_after == "deleted", 2),
                (last_action.status_after == "resolved", 1),
                else_=0,
            )
            selected_where = [
                ReviewThreadPlacement.snapshot_id == snapshot_id,
                latest.c.first_activity.is_not(None),
            ]
            if state == "open":
                selected_where.append(last_action.status_after == "open")
            else:
                assert state == "all"
            if attention is not None:
                selected_where.append(
                    last_action.attention_after.in_((attention, "both"))
                )

            def filtered(*columns: Any) -> Any:
                """Select from placements joined to their latest actions."""
                return (
                    select(*columns)
                    .select_from(ReviewThreadPlacement)
                    .join(
                        latest,
                        latest.c.thread_id == ReviewThreadPlacement.thread_id,
                    )
                    .join(
                        last_action,
                        (
                            last_action.thread_id
                            == ReviewThreadPlacement.thread_id
                        )
                        & (
                            last_action.activity_id == latest.c.last_activity_id
                        ),
                    )
                    .where(*selected_where)
                )

            total_threads = session.execute(
                select(func.count()).select_from(
                    filtered(ReviewThreadPlacement.thread_id).subquery()
                )
            ).scalar_one()
            selected_query = (
                filtered(
                    *ReviewThreadPlacement.__table__.c,
                    literal(False).label("is_origin"),
                )
                .order_by(
                    state_rank,
                    latest.c.first_activity,
                    ReviewThreadPlacement.thread_id,
                )
                .offset(offset)
            )
            if limit is not None:
                selected_query = selected_query.limit(limit)
            selected_rows = session.execute(selected_query).all()
            thread_ids = [row.thread_id for row in selected_rows]
            if thread_ids == []:
                return (
                    ReviewThreadsRecord((), (), (), (), total_threads),
                    through_activity_id,
                )
            origin_rows = session.execute(
                select(
                    *ReviewThreadPlacement.__table__.c,
                    literal(True).label("is_origin"),
                )
                .join(
                    ReviewThread,
                    (ReviewThread.thread_id == ReviewThreadPlacement.thread_id)
                    & (
                        ReviewThread.origin_snapshot_id
                        == ReviewThreadPlacement.snapshot_id
                    ),
                )
                .where(ReviewThreadPlacement.thread_id.in_(thread_ids))
            ).all()
            origins_by_thread = {row.thread_id: row for row in origin_rows}
            assert origins_by_thread.keys() == set(thread_ids), (
                "Snapshot review placement exists without a Thread origin"
            )
            origin_rows = [
                origins_by_thread[thread_id] for thread_id in thread_ids
            ]
            action_rows = session.execute(
                select(*ReviewAction.__table__.c)
                .where(
                    ReviewAction.thread_id.in_(thread_ids),
                    ReviewAction.activity_id <= through_activity_id,
                )
                .order_by(ReviewAction.thread_id, ReviewAction.sequence)
            ).all()
            profile_ids = {row.profile_id for row in action_rows}
            profile_rows = session.execute(
                select(
                    UserProfile.id,
                    UserProfile.username,
                ).where(UserProfile.id.in_(profile_ids))
            ).all()
        profiles = [
            profile_record(row.id, row.username) for row in profile_rows
        ]
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return (
            ReviewThreadsRecord(
                threads=tuple(
                    self._thread_record(row) for row in selected_rows
                ),
                origins=tuple(self._thread_record(row) for row in origin_rows),
                actions=tuple(self._action_record(row) for row in action_rows),
                profiles=tuple(profiles),
                total_threads=total_threads,
            ),
            through_activity_id,
        )

    def review_thread(
        self,
        identity: RoomIdentity,
        snapshot_id: str,
        thread_id: str,
    ) -> Optional[ReviewThreadsRecord]:
        """Load one discussion at one exact Snapshot without bulk hydration.

        `None` means the Snapshot is absent or belongs to another Room. An empty
        record means that Snapshot contains no such Thread. A present result
        contains exactly its selected placement, unique origin, complete action
        sequence, and referenced Profile authors.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            room_id = session.execute(
                select(Snapshot.room_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    Snapshot.id == snapshot_id,
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).scalar_one_or_none()
            if room_id is None:
                return None
            selected_row = session.execute(
                select(
                    *ReviewThreadPlacement.__table__.c,
                    literal(False).label("is_origin"),
                ).where(
                    ReviewThreadPlacement.snapshot_id == snapshot_id,
                    ReviewThreadPlacement.thread_id == thread_id,
                )
            ).one_or_none()
            origin_row = session.execute(
                select(
                    *ReviewThreadPlacement.__table__.c,
                    literal(True).label("is_origin"),
                )
                .join(
                    ReviewThread,
                    ReviewThread.thread_id == ReviewThreadPlacement.thread_id,
                )
                .join(
                    Snapshot, Snapshot.id == ReviewThreadPlacement.snapshot_id
                )
                .where(
                    Snapshot.room_id == room_id,
                    ReviewThreadPlacement.thread_id == thread_id,
                    ReviewThread.origin_snapshot_id
                    == ReviewThreadPlacement.snapshot_id,
                )
            ).one_or_none()
            if origin_row is None:
                assert selected_row is None, (
                    "Snapshot placement exists without a Room Thread origin"
                )
                return ReviewThreadsRecord((), (), (), (), 0)
            if selected_row is None:
                return ReviewThreadsRecord((), (), (), (), 0)
            action_rows = session.execute(
                select(*ReviewAction.__table__.c)
                .where(ReviewAction.thread_id == thread_id)
                .order_by(ReviewAction.sequence)
            ).all()
            assert action_rows != [], "review Thread has no creation action"
            profile_ids = {row.profile_id for row in action_rows}
            profile_rows = session.execute(
                select(
                    UserProfile.id,
                    UserProfile.username,
                ).where(UserProfile.id.in_(profile_ids))
            ).all()
        profiles = tuple(
            profile_record(row.id, row.username) for row in profile_rows
        )
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return ReviewThreadsRecord(
            threads=(self._thread_record(selected_row),),
            origins=(self._thread_record(origin_row),),
            actions=tuple(self._action_record(row) for row in action_rows),
            profiles=profiles,
            total_threads=1,
        )

    def review_actions(
        self,
        snapshot_id: str,
        thread_id: str,
    ) -> Optional[
        tuple[tuple[ReviewActionRecord, ...], tuple[UserProfileRecord, ...]]
    ]:
        """Load authoritative actions and authors for one placed Thread.

        This write-validation read deliberately excludes Snapshots, Files, and
        placement rendering. `None` means the exact Thread/Snapshot pair does
        not exist.
        """
        with Session(self.engine) as session:
            placed = session.execute(
                select(ReviewThreadPlacement.thread_id).where(
                    ReviewThreadPlacement.snapshot_id == snapshot_id,
                    ReviewThreadPlacement.thread_id == thread_id,
                )
            ).scalar_one_or_none()
            if placed is None:
                return None
            action_rows = session.execute(
                select(*ReviewAction.__table__.c)
                .where(ReviewAction.thread_id == thread_id)
                .order_by(ReviewAction.sequence)
            ).all()
            assert action_rows != [], "review Thread has no creation action"
            profile_ids = {row.profile_id for row in action_rows}
            profile_rows = session.execute(
                select(UserProfile.id, UserProfile.username).where(
                    UserProfile.id.in_(profile_ids)
                )
            ).all()
        profiles = tuple(
            profile_record(row.id, row.username) for row in profile_rows
        )
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return (
            tuple(self._action_record(row) for row in action_rows),
            profiles,
        )

    def review_profiles(
        self, profile_ids: tuple[int, ...]
    ) -> tuple[UserProfileRecord, ...]:
        """Load the existing Profiles among `profile_ids` in one set query.

        Missing ids are simply absent from the result; the caller owns the
        rejection contract for authors that do not exist.
        """
        if profile_ids == ():
            return ()
        with Session(self.engine) as session:
            rows = session.execute(
                select(UserProfile.id, UserProfile.username).where(
                    UserProfile.id.in_(set(profile_ids))
                )
            ).all()
        return tuple(profile_record(row.id, row.username) for row in rows)

    def review_actions_many(
        self,
        snapshot_id: str,
        thread_ids: tuple[str, ...],
    ) -> tuple[
        dict[str, tuple[ReviewActionRecord, ...]],
        tuple[UserProfileRecord, ...],
    ]:
        """Load actions for every placed Thread among `thread_ids` at once.

        The batch-validation counterpart of `review_actions`: one placement
        check, one ordered action read, and one Profile read cover every
        addressed Thread. A Thread without a placement in `snapshot_id` is
        absent from the returned mapping; the caller owns that rejection.
        Every present Thread has at least one action, and every acting
        Profile is present in the returned Profiles.
        """
        if thread_ids == ():
            return {}, ()
        with Session(self.engine) as session:
            placed = set(
                session.execute(
                    select(ReviewThreadPlacement.thread_id).where(
                        ReviewThreadPlacement.snapshot_id == snapshot_id,
                        ReviewThreadPlacement.thread_id.in_(set(thread_ids)),
                    )
                ).scalars()
            )
            if placed == set():
                return {}, ()
            action_rows = session.execute(
                select(*ReviewAction.__table__.c)
                .where(ReviewAction.thread_id.in_(placed))
                .order_by(ReviewAction.thread_id, ReviewAction.sequence)
            ).all()
            profile_ids = {row.profile_id for row in action_rows}
            profile_rows = session.execute(
                select(UserProfile.id, UserProfile.username).where(
                    UserProfile.id.in_(profile_ids)
                )
            ).all()
        actions_by_thread: dict[str, list[ReviewActionRecord]] = {}
        for row in action_rows:
            actions_by_thread.setdefault(row.thread_id, []).append(
                self._action_record(row)
            )
        assert actions_by_thread.keys() == placed, (
            "review Thread has no creation action"
        )
        profiles = tuple(
            profile_record(row.id, row.username) for row in profile_rows
        )
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return (
            {
                thread_id: tuple(actions)
                for thread_id, actions in actions_by_thread.items()
            },
            profiles,
        )

    def review_origins_missing(
        self,
        identity: RoomIdentity,
        target_snapshot_id: str,
    ) -> tuple[ReviewThreadRecord, ...]:
        """Return Room Thread origins without a target-Snapshot placement."""
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    *ReviewThreadPlacement.__table__.c,
                    literal(True).label("is_origin"),
                )
                .join(
                    ReviewThread,
                    ReviewThread.thread_id == ReviewThreadPlacement.thread_id,
                )
                .join(
                    Snapshot, Snapshot.id == ReviewThreadPlacement.snapshot_id
                )
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    ReviewThread.origin_snapshot_id
                    == ReviewThreadPlacement.snapshot_id,
                    ReviewThreadPlacement.thread_id.not_in(
                        select(ReviewThreadPlacement.thread_id).where(
                            ReviewThreadPlacement.snapshot_id
                            == target_snapshot_id
                        )
                    ),
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).all()
        return tuple(self._thread_record(row) for row in rows)

    def insert_review_threads(
        self,
        rows: tuple[ReviewThreadRecord, ...],
    ) -> None:
        """Insert missing immutable placements and validate existing pairs.

        The caller holds the shared Room lock. Existing equal records make the
        operation idempotent; an existing different record is an invariant
        violation because placements are immutable.
        """
        if rows == ():
            return
        pairs = [(row.thread_id, row.snapshot_id) for row in rows]
        assert len(pairs) == len(set(pairs)), (
            "duplicate review Thread pair supplied for insertion"
        )
        with Session(self.engine) as session, session.begin():
            persisted_rows = session.execute(
                select(
                    *ReviewThreadPlacement.__table__.c,
                    (
                        ReviewThread.origin_snapshot_id
                        == ReviewThreadPlacement.snapshot_id
                    ).label("is_origin"),
                )
                .join(
                    ReviewThread,
                    ReviewThread.thread_id == ReviewThreadPlacement.thread_id,
                )
                .where(
                    tuple_(
                        ReviewThreadPlacement.thread_id,
                        ReviewThreadPlacement.snapshot_id,
                    ).in_(pairs)
                )
            ).all()
            persisted = {
                (row.thread_id, row.snapshot_id): self._thread_record(row)
                for row in persisted_rows
            }
            additions = []
            for record in rows:
                previous = persisted.get((record.thread_id, record.snapshot_id))
                if previous is None:
                    additions.append(self._review_thread_values(record))
                else:
                    assert previous == record, (
                        "immutable review Thread placement changed"
                    )
            if additions != []:
                session.execute(insert(ReviewThreadPlacement), additions)

    def create_review_thread(
        self,
        rows: tuple[ReviewThreadRecord, ...],
        first_action: ReviewActionRecord,
    ) -> None:
        """Atomically create one discussion in its origin Snapshot."""
        assert rows != (), "Thread creation requires at least its origin row"
        assert sum(1 for row in rows if row.is_origin) == 1, (
            "Thread creation requires exactly one origin row"
        )
        assert {row.thread_id for row in rows} == {first_action.thread_id}, (
            "Thread creation rows and first action must share one Thread id"
        )
        assert first_action.kind == "thread-created"
        assert first_action.sequence == 0
        assert first_action.activity_id is None
        with Session(self.engine) as session, session.begin():
            session.execute(
                insert(ReviewThread).values(
                    thread_id=first_action.thread_id,
                    origin_snapshot_id=next(
                        row.snapshot_id for row in rows if row.is_origin
                    ),
                )
            )
            session.execute(
                insert(ReviewThreadPlacement),
                [self._review_thread_values(row) for row in rows],
            )
            session.execute(
                insert(ReviewAction).values(
                    **self._review_action_values(first_action),
                    activity_id=self._next_review_activity_id(session),
                )
            )

    def apply_review_batch(
        self,
        thread_rows: tuple[ReviewThreadRecord, ...],
        actions: tuple[ReviewActionRecord, ...],
    ) -> None:
        """Insert prevalidated Thread placements and actions atomically.

        The caller holds the Room write lock and supplies actions in authored
        order. Placement rows belong only to Threads created by this batch;
        actions may address those new Threads or existing Snapshot-bound
        Threads.
        """
        assert actions != (), "review batch must contain at least one action"
        pairs = [(row.thread_id, row.snapshot_id) for row in thread_rows]
        assert len(pairs) == len(set(pairs)), (
            "review batch contains duplicate Thread placements"
        )
        with Session(self.engine) as session, session.begin():
            if thread_rows != ():
                origins = [row for row in thread_rows if row.is_origin]
                if origins != []:
                    session.execute(
                        insert(ReviewThread),
                        [
                            {
                                "thread_id": row.thread_id,
                                "origin_snapshot_id": row.snapshot_id,
                            }
                            for row in origins
                        ],
                    )
                session.execute(
                    insert(ReviewThreadPlacement),
                    [self._review_thread_values(row) for row in thread_rows],
                )
            next_activity_id = self._next_review_activity_id(session)
            for action in actions:
                assert action.activity_id is None, (
                    "new review action must not supply activity order"
                )
            # One executemany insert preserves the authored order through the
            # explicit consecutive activity ids.
            session.execute(
                insert(ReviewAction),
                [
                    {
                        **self._review_action_values(action),
                        "activity_id": next_activity_id + offset,
                    }
                    for offset, action in enumerate(actions)
                ],
            )

    def review_latest_activity_id(self, identity: RoomIdentity) -> int:
        """Return the Room's greatest review activity id, or 0 when empty.

        This is the focused pivot read for callers that need only the
        activity boundary, without hydrating any Thread.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            latest = session.execute(
                select(func.max(ReviewAction.activity_id))
                .join(Snapshot, Snapshot.id == ReviewAction.snapshot_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).scalar_one()
        return 0 if latest is None else latest

    def _review_open_thread_count(
        self,
        session: Session,
        identity: RoomIdentity,
        through_activity_id: int,
    ) -> int:
        """Count open logical Threads in one Room at one inclusive pivot.

        Threads whose first action lies after the pivot do not exist yet at
        that pivot; their latest-action subquery finds nothing, so the join
        excludes them.
        """
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        room_threads = (
            select(ReviewThread.thread_id)
            .join(Snapshot, Snapshot.id == ReviewThread.origin_snapshot_id)
            .join(Room, Room.id == Snapshot.room_id)
            .where(
                Room.tab == identity.tab,
                Room.backend_key == identity.correspondence_key,
                mark_clause,
            )
            .cte("open_count_room_threads")
            .prefix_with("MATERIALIZED")
        )
        latest_for_thread = (
            select(func.max(ReviewAction.activity_id))
            .where(
                ReviewAction.thread_id == room_threads.c.thread_id,
                ReviewAction.activity_id <= through_activity_id,
            )
            .correlate(room_threads)
            .scalar_subquery()
        )
        return session.execute(
            select(func.count())
            .select_from(room_threads)
            .join(
                ReviewAction,
                ReviewAction.activity_id == latest_for_thread,
            )
            .where(ReviewAction.status_after == "open")
        ).scalar_one()

    def review_continuation(
        self,
        identity: RoomIdentity,
        activity_id: int,
        limit: int,
    ) -> tuple[
        tuple[ReviewActionRecord, ...],
        bool,
        int,
        tuple[UserProfileRecord, ...],
    ]:
        """Return one bounded page of later actions with consistent context.

        One database session reads the ordered action page after
        `activity_id`, its has-more marker, the open logical Thread count,
        and the acting Profiles, so a continuation response cannot mix
        observations from different review universes. The count is evaluated
        at the page's inclusive end boundary: the last returned action's
        activity id, or `activity_id` when the page is empty. Every returned
        action's Profile is present in the returned Profiles; a missing
        Profile row fails the read.
        """
        assert activity_id >= 0, "review activity boundary must be nonnegative"
        assert limit > 0, "review activity limit must be positive"
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            rows = session.execute(
                select(*ReviewAction.__table__.c)
                .join(Snapshot, Snapshot.id == ReviewAction.snapshot_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    ReviewAction.activity_id > activity_id,
                    ReviewAction.kind.in_(
                        (
                            "thread-created",
                            "comment-created",
                            "comment-edited",
                            "comment-deleted",
                            "thread-resolved",
                            "thread-reopened",
                            "thread-deleted",
                        )
                    ),
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
                .order_by(ReviewAction.activity_id)
                .limit(limit + 1)
            ).all()
            page = rows[:limit]
            boundary = activity_id
            if page != []:
                last_activity = page[-1].activity_id
                assert last_activity is not None
                boundary = last_activity
            open_count = self._review_open_thread_count(
                session, identity, boundary
            )
            profile_ids = {row.profile_id for row in page}
            profile_rows = session.execute(
                select(UserProfile.id, UserProfile.username).where(
                    UserProfile.id.in_(profile_ids)
                )
            ).all()
        profiles = tuple(
            profile_record(row.id, row.username) for row in profile_rows
        )
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return (
            tuple(self._action_record(row) for row in page),
            len(rows) > limit,
            open_count,
            profiles,
        )

    def review_attention_counts(
        self, identity: RoomIdentity, through_activity_id: int
    ) -> dict[Literal["author", "reviewer", "both"], int]:
        """Count open logical Threads by actionable attention at one pivot."""
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        room_threads = (
            select(ReviewThread.thread_id)
            .join(Snapshot, Snapshot.id == ReviewThread.origin_snapshot_id)
            .join(Room, Room.id == Snapshot.room_id)
            .where(
                Room.tab == identity.tab,
                Room.backend_key == identity.correspondence_key,
                mark_clause,
            )
            .cte("room_threads")
            .prefix_with("MATERIALIZED")
        )
        latest_for_thread = (
            select(func.max(ReviewAction.activity_id))
            .where(
                ReviewAction.thread_id == room_threads.c.thread_id,
                ReviewAction.activity_id <= through_activity_id,
            )
            .correlate(room_threads)
            .scalar_subquery()
        )
        latest_outcomes = (
            select(
                ReviewAction.status_after,
                ReviewAction.attention_after,
            )
            .select_from(room_threads)
            .join(
                ReviewAction,
                ReviewAction.activity_id == latest_for_thread,
            )
            .cte("latest_outcomes")
            .prefix_with("MATERIALIZED")
        )
        with Session(self.engine) as session:
            rows = session.execute(
                select(latest_outcomes.c.attention_after, func.count())
                .where(
                    latest_outcomes.c.status_after == "open",
                    latest_outcomes.c.attention_after.in_(
                        ("author", "reviewer", "both")
                    ),
                )
                .group_by(latest_outcomes.c.attention_after)
            ).all()
        counts: dict[Literal["author", "reviewer", "both"], int] = {
            "author": 0,
            "reviewer": 0,
            "both": 0,
        }
        for attention, count in rows:
            assert attention in counts
            counts[attention] = count
        return counts

    def review_thread_for_comment(
        self, snapshot_id: str, comment_id: str
    ) -> Optional[str]:
        """Return the placed Thread containing one created Comment."""
        with Session(self.engine) as session:
            return session.execute(
                select(ReviewAction.thread_id)
                .join(
                    ReviewThreadPlacement,
                    ReviewThreadPlacement.thread_id == ReviewAction.thread_id,
                )
                .where(
                    ReviewThreadPlacement.snapshot_id == snapshot_id,
                    ReviewAction.comment_id == comment_id,
                    ReviewAction.comment_id.is_not(None),
                )
            ).scalar_one_or_none()

    def append_review_action(self, record: ReviewActionRecord) -> None:
        """Append one already-validated action to its Snapshot-bound Thread."""
        assert record.activity_id is None
        with Session(self.engine) as session, session.begin():
            session.execute(
                insert(ReviewAction).values(
                    **self._review_action_values(record),
                    activity_id=self._next_review_activity_id(session),
                )
            )
