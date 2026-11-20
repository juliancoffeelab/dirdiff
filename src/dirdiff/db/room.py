from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NotRequired, Optional, TypedDict

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Engine,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Select,
    String,
    UniqueConstraint,
    and_,
    case,
    column,
    func,
    insert,
    literal,
    literal_column,
    or_,
    select,
    tuple_,
)
from sqlalchemy.engine import Row
from sqlalchemy.orm import Mapped, Session, aliased, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from dirdiff.db.base import (
    AgentDetails,
    AgentProfile,
    TableBase,
    UserProfile,
    profile_record,
)
from dirdiff.util import Err, Nothing, Ok, Option, Result, Some, maybe

__all__ = [
    "AuthoredComment",
    "BayStartPlacement",
    "CommentCreatedAction",
    "CommentDeletedAction",
    "CommentEditedAction",
    "FileMissingPlacement",
    "FileStartPlacement",
    "FileUnreadablePlacement",
    "Placement",
    "RangePlacement",
    "ReviewActionInput",
    "ReviewActionPayload",
    "ReviewMarkActionRecord",
    "ReviewProfileRecord",
    "ReviewScopePlacement",
    "ReviewThreadHistory",
    "ReviewThreadRecord",
    "ReviewThreadsRecord",
    "RoomIdentity",
    "RoomStore",
    "SnapshotFileRecord",
    "SnapshotFileSideRecord",
    "SnapshotFileSymlinkRecord",
    "SnapshotLinkTargetRecord",
    "SnapshotMetaRecord",
    "SnapshotRecord",
    "SnapshotRegularFileSideRecord",
    "SnapshotSymlinkFileSideRecord",
    "ThreadDeletedAction",
    "ThreadStateAction",
]


@dataclass(frozen=True)
class ReviewProfileRecord:
    id: int
    username: str
    agent: AgentDetails | None


def _review_profile_select(
    where: ColumnElement[bool],
) -> Select[tuple[int, str, int, Optional[str], Optional[str]]]:
    return (
        select(
            UserProfile.id,
            UserProfile.username,
            AgentProfile.profile_id.label("agent_profile_id"),
            AgentProfile.model,
            AgentProfile.harness,
        )
        .select_from(UserProfile)
        .outerjoin(AgentProfile, AgentProfile.profile_id == UserProfile.id)
        .where(where)
    )


def _review_profile(
    row: Row[tuple[int, str, int, Optional[str], Optional[str]]],
) -> ReviewProfileRecord:
    profile = profile_record(row.id, row.username)
    return ReviewProfileRecord(
        profile.id,
        profile.username,
        None
        if row.agent_profile_id is None
        else AgentDetails(row.model, row.harness),
    )


class Room(TableBase):
    __tablename__ = "room"
    __table_args__ = (
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
    __tablename__ = "snapshot_file_left"
    __table_args__ = _captured_side_constraints("left")
    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    repository_path: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


class SnapshotFileRight(TableBase):
    __tablename__ = "snapshot_file_right"
    __table_args__ = _captured_side_constraints("right")
    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    repository_path: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


class SnapshotFileSymlink(TableBase):
    __tablename__ = "snapshot_file_symlink"
    __table_args__ = (
        UniqueConstraint(
            "metadata_path",
            name="uq_snapshot_file_symlink_metadata_path",
        ),
        UniqueConstraint(
            "target_capture_path",
            name="uq_snapshot_file_symlink_target_capture_path",
        ),
        CheckConstraint(
            func.substr(column("metadata_path"), 1, 1) == "/",
            name="ck_snapshot_file_symlink_metadata_path",
        ),
        CheckConstraint(
            func.length(column("metadata_hash")) == 32,
            name="ck_snapshot_file_symlink_metadata_hash",
        ),
        CheckConstraint(
            column("target_capture_path").is_(None)
            == column("target_hash").is_(None),
            name="ck_snapshot_file_symlink_target_presence",
        ),
        CheckConstraint(
            column("target_capture_path").is_(None)
            | (func.substr(column("target_capture_path"), 1, 1) == "/"),
            name="ck_snapshot_file_symlink_target_capture_path",
        ),
        CheckConstraint(
            column("target_hash").is_(None)
            | (func.length(column("target_hash")) == 32),
            name="ck_snapshot_file_symlink_target_hash",
        ),
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    side: Mapped[str] = mapped_column(String, primary_key=True)
    metadata_path: Mapped[str] = mapped_column(String, nullable=False)
    metadata_hash: Mapped[bytes] = mapped_column(
        LargeBinary(32), nullable=False
    )
    target_capture_path: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    target_hash: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary(32), nullable=True
    )


class SnapshotFileLazyReason(TableBase):
    __tablename__ = "snapshot_file_lazy_reason"
    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)


class SnapshotFileLazyReasonContent(TableBase):
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
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey(
            "review_thread.thread_id",
            name="fk_review_thread_placement_thread",
        ),
        primary_key=True,
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"), primary_key=True
    )
    snapshot_file_id: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    target_kind: Mapped[
        Optional[Literal["range", "bay-start", "file-start", "review"]]
    ] = mapped_column(String, nullable=True)
    bay_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    side: Mapped[Optional[Literal["left", "right"]]] = mapped_column(
        String, nullable=True
    )
    start_line: Mapped[Optional[int]] = mapped_column(nullable=True)
    end_line: Mapped[Optional[int]] = mapped_column(nullable=True)
    outdated_reason: Mapped[
        Optional[
            Literal[
                "region_changed",
                "region_not_found",
                "bay_not_found",
                "file_unreadable",
                "file_missing",
            ]
        ]
    ] = mapped_column(String, nullable=True)
    private_locator: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )


Index("ix_review_thread_placement_snapshot", ReviewThreadPlacement.snapshot_id)


class ReviewAction(TableBase):
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
            func.length(column("created_at")) > 0,
            name="ck_review_action_created_at",
        ),
    )
    # Reuse conflicts with the primary key; it is never a retry/dedup key.
    operation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[
        Literal[
            "comment-created",
            "thread-created",
            "comment-edited",
            "comment-deleted",
            "thread-resolved",
            "thread-reopened",
            "thread-deleted",
        ]
    ] = mapped_column(String, nullable=False)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"), nullable=False
    )
    comment_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Optimistic concurrency gate for comment edit and deletion.
    expected_revision: Mapped[Optional[int]] = mapped_column(nullable=True)
    body: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    status_after: Mapped[Literal["open", "resolved", "deleted"]] = (
        mapped_column(String, nullable=False)
    )
    attention_after: Mapped[Literal["author", "reviewer", "both", "none"]] = (
        mapped_column(String, nullable=False)
    )


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
    mark_id: Optional[int]
    tab: str
    correspondence_key: bytes


@dataclass(frozen=True)
class SnapshotRegularFileSideRecord:
    repository_path: str
    content_hash: bytes

    def __post_init__(self) -> None:
        assert isinstance(self.repository_path, str), (
            "present side requires a repository path"
        )
        path = PurePosixPath(self.repository_path)
        assert (
            self.repository_path not in {"", ".", ".."}
            and not path.is_absolute()
            and ".." not in path.parts
        ), "Snapshot File path must be repository-relative"
        assert (
            isinstance(self.content_hash, bytes)
            and len(self.content_hash) == 32
        ), "present side requires a SHA-256 digest"


@dataclass(frozen=True)
class SnapshotLinkTargetRecord:
    path: str
    content_hash: bytes

    def __post_init__(self) -> None:
        assert Path(self.path).is_absolute(), (
            "Snapshot link target capture path must be absolute"
        )
        assert len(self.content_hash) == 32, (
            "Snapshot link target hash must have length 32"
        )


@dataclass(frozen=True)
class SnapshotFileSymlinkRecord:
    metadata_path: str
    metadata_hash: bytes
    target: Option[SnapshotLinkTargetRecord]

    def __post_init__(self) -> None:
        assert Path(self.metadata_path).is_absolute(), (
            "Snapshot link metadata path must be absolute"
        )
        assert len(self.metadata_hash) == 32, (
            "Snapshot link metadata hash must have length 32"
        )


@dataclass(frozen=True)
class SnapshotSymlinkFileSideRecord:
    repository_path: str
    content_hash: bytes
    link: SnapshotFileSymlinkRecord

    def __post_init__(self) -> None:
        assert isinstance(self.repository_path, str), (
            "present side requires a repository path"
        )
        path = PurePosixPath(self.repository_path)
        assert (
            self.repository_path not in {"", ".", ".."}
            and not path.is_absolute()
            and ".." not in path.parts
        ), "Snapshot File path must be repository-relative"
        assert (
            isinstance(self.content_hash, bytes)
            and len(self.content_hash) == 32
        ), "present side requires a SHA-256 digest"
        assert isinstance(self.link, SnapshotFileSymlinkRecord), (
            "link side requires its descriptor"
        )


SnapshotFileSideRecord = (
    SnapshotRegularFileSideRecord | SnapshotSymlinkFileSideRecord
)


@dataclass(frozen=True)
class SnapshotFileRecord:
    id: str
    snapshot_id: str
    path: str
    tracked: bool
    change_type: str
    error: Optional[str]
    left: Option[SnapshotFileSideRecord]
    right: Option[SnapshotFileSideRecord]

    def __post_init__(self) -> None:
        assert Some.present(self.left) or Some.present(self.right), (
            "Snapshot File must have at least one captured side"
        )


@dataclass(frozen=True)
class SnapshotRecord:
    id: str
    content_hash: bytes
    meta: SnapshotMetaRecord
    files: tuple[SnapshotFileRecord, ...]


@dataclass(frozen=True)
class SnapshotMetaRecord:
    left_label: str
    right_label: str
    added_lines: Optional[int]
    removed_lines: Optional[int]


@dataclass(frozen=True)
class SnapshotFileLoadRecord:
    file: SnapshotFileRecord
    lazy_reason: Optional[str]


@dataclass(frozen=True)
class RangePlacement:
    thread_id: str
    snapshot_id: str
    snapshot_file_id: str
    bay_key: str
    side: Literal["left", "right"]
    start_line: int
    end_line: int
    outdated_reason: Optional[Literal["region_changed"]]

    def __post_init__(self) -> None:
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"
        assert (
            isinstance(self.snapshot_file_id, str)
            and self.snapshot_file_id != ""
        )
        assert isinstance(self.bay_key, str) and self.bay_key != ""
        assert self.side in {"left", "right"}
        assert isinstance(self.start_line, int) and isinstance(
            self.end_line, int
        )
        assert 1 <= self.start_line <= self.end_line
        assert self.outdated_reason in {None, "region_changed"}


@dataclass(frozen=True)
class BayStartPlacement:
    thread_id: str
    snapshot_id: str
    snapshot_file_id: str
    bay_key: str
    side: Literal["left", "right"]
    outdated_reason: Literal["region_not_found", "bay_not_found"]

    def __post_init__(self) -> None:
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"
        assert (
            isinstance(self.snapshot_file_id, str)
            and self.snapshot_file_id != ""
        )
        assert isinstance(self.bay_key, str) and self.bay_key != ""
        assert self.side in {"left", "right"}
        assert self.outdated_reason in {"region_not_found", "bay_not_found"}


@dataclass(frozen=True)
class FileStartPlacement:
    thread_id: str
    snapshot_id: str
    snapshot_file_id: str
    side: Literal["left", "right"]
    outdated_reason: Optional[Literal["bay_not_found"]]

    def __post_init__(self) -> None:
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"
        assert (
            isinstance(self.snapshot_file_id, str)
            and self.snapshot_file_id != ""
        )
        assert self.side in {"left", "right"}
        assert self.outdated_reason in {None, "bay_not_found"}


@dataclass(frozen=True)
class FileMissingPlacement:
    thread_id: str
    snapshot_id: str

    def __post_init__(self) -> None:
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"


@dataclass(frozen=True)
class FileUnreadablePlacement:
    thread_id: str
    snapshot_id: str

    def __post_init__(self) -> None:
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"


@dataclass(frozen=True)
class ReviewScopePlacement:
    thread_id: str
    snapshot_id: str

    def __post_init__(self) -> None:
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"


Placement = (
    RangePlacement
    | BayStartPlacement
    | FileStartPlacement
    | FileMissingPlacement
    | FileUnreadablePlacement
    | ReviewScopePlacement
)


@dataclass(frozen=True)
class ReviewThreadRecord:
    placement: Placement
    is_origin: bool
    private_locator: bytes | None

    def __post_init__(self) -> None:
        assert isinstance(
            self.placement,
            (
                RangePlacement,
                BayStartPlacement,
                FileStartPlacement,
                FileMissingPlacement,
                FileUnreadablePlacement,
                ReviewScopePlacement,
            ),
        ), "Thread record requires a placement variant"
        assert self.private_locator is None or isinstance(
            self.private_locator, bytes
        ), "private coordinates must be bytes"
        assert self.private_locator is None or (
            isinstance(self.placement, RangePlacement)
            and self.placement.outdated_reason is None
        ), "only a current range placement retains private coordinates"


@dataclass(frozen=True)
class AuthoredComment:
    id: str
    body: str

    def __post_init__(self) -> None:
        assert isinstance(self.id, str), "authored Comment requires an id"
        assert isinstance(self.body, str) and len(self.body) > 0, (
            "authored Comment requires a body"
        )


@dataclass(frozen=True)
class CommentCreatedAction:
    kind: Literal["thread-created", "comment-created"]
    comment: AuthoredComment

    def __post_init__(self) -> None:
        assert self.kind in {"thread-created", "comment-created"}
        assert isinstance(self.comment, AuthoredComment)


@dataclass(frozen=True)
class CommentEditedAction:
    comment: AuthoredComment
    expected_revision: int
    kind: Literal["comment-edited"] = "comment-edited"

    def __post_init__(self) -> None:
        assert isinstance(self.comment, AuthoredComment)
        assert isinstance(self.expected_revision, int)
        assert self.kind == "comment-edited"


@dataclass(frozen=True)
class CommentDeletedAction:
    comment_id: str
    expected_revision: int
    kind: Literal["comment-deleted"] = "comment-deleted"

    def __post_init__(self) -> None:
        assert isinstance(self.comment_id, str)
        assert isinstance(self.expected_revision, int)
        assert self.kind == "comment-deleted"


@dataclass(frozen=True)
class ThreadStateAction:
    kind: Literal["thread-resolved", "thread-reopened"]
    comment: AuthoredComment | None

    def __post_init__(self) -> None:
        assert self.kind in {"thread-resolved", "thread-reopened"}
        assert self.comment is None or isinstance(self.comment, AuthoredComment)


@dataclass(frozen=True)
class ThreadDeletedAction:
    kind: Literal["thread-deleted"] = "thread-deleted"

    def __post_init__(self) -> None:
        assert self.kind == "thread-deleted"


ReviewActionPayload = (
    CommentCreatedAction
    | CommentEditedAction
    | CommentDeletedAction
    | ThreadStateAction
    | ThreadDeletedAction
)


@dataclass(frozen=True)
class ReviewActionInput:
    operation_id: str
    thread_id: str
    snapshot_id: str
    sequence: int
    profile_id: int
    payload: ReviewActionPayload
    created_at: str
    status_after: Literal["open", "resolved", "deleted"]
    attention_after: Literal["author", "reviewer", "both", "none"]

    def __post_init__(self) -> None:
        assert all(
            isinstance(value, str)
            for value in (self.operation_id, self.thread_id, self.snapshot_id)
        ), "action requires operation, Thread and Snapshot identities"
        assert isinstance(self.sequence, int), "action requires sequence order"
        assert isinstance(self.profile_id, int) and self.profile_id > 0, (
            "review actions require a relational Profile"
        )
        assert isinstance(
            self.payload,
            (
                CommentCreatedAction,
                CommentEditedAction,
                CommentDeletedAction,
                ThreadStateAction,
                ThreadDeletedAction,
            ),
        )
        assert self.status_after in {"open", "resolved", "deleted"}
        assert self.attention_after in {"author", "reviewer", "both", "none"}


@dataclass(frozen=True)
class ReviewActionRecord:
    action: ReviewActionInput
    activity_id: int

    def __post_init__(self) -> None:
        assert isinstance(self.action, ReviewActionInput), (
            "persisted action requires accepted operation facts"
        )
        assert isinstance(self.activity_id, int) and self.activity_id > 0


@dataclass(frozen=True)
class ReviewThreadsRecord:
    threads: tuple[ReviewThreadRecord, ...]
    origins: tuple[ReviewThreadRecord, ...]
    actions: tuple[ReviewActionRecord, ...]
    profiles: tuple[ReviewProfileRecord, ...]
    total_threads: int


@dataclass(frozen=True)
class ReviewThreadHistory:
    identity: RoomIdentity
    origin: ReviewThreadRecord
    origin_left_path: Optional[str]
    origin_right_path: Optional[str]
    actions: tuple[ReviewActionRecord, ...]


@dataclass(frozen=True)
class ReviewMarkActionRecord:
    record: ReviewActionRecord
    identity: RoomIdentity


class _ReviewThreadInsertValues(TypedDict):
    thread_id: str
    snapshot_id: str
    snapshot_file_id: str | None
    target_kind: Literal["range", "bay-start", "file-start", "review"] | None
    bay_key: str | None
    side: Literal["left", "right"] | None
    start_line: int | None
    end_line: int | None
    outdated_reason: (
        Literal[
            "region_changed",
            "region_not_found",
            "bay_not_found",
            "file_unreadable",
            "file_missing",
        ]
        | None
    )
    private_locator: bytes | None


class _ReviewActionInsertValues(TypedDict):
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
    comment_id: str | None
    expected_revision: int | None
    body: str | None
    created_at: str
    status_after: Literal["open", "resolved", "deleted"]
    attention_after: Literal["author", "reviewer", "both", "none"]
    activity_id: NotRequired[int]


class RoomStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _review_thread_values(
        record: ReviewThreadRecord,
    ) -> _ReviewThreadInsertValues:
        placement = record.placement
        values: _ReviewThreadInsertValues = {
            "thread_id": placement.thread_id,
            "snapshot_id": placement.snapshot_id,
            "snapshot_file_id": None,
            "target_kind": None,
            "bay_key": None,
            "side": None,
            "start_line": None,
            "end_line": None,
            "outdated_reason": None,
            "private_locator": record.private_locator,
        }
        match placement:
            case RangePlacement():
                values.update(
                    {
                        "snapshot_file_id": placement.snapshot_file_id,
                        "target_kind": "range",
                        "bay_key": placement.bay_key,
                        "side": placement.side,
                        "start_line": placement.start_line,
                        "end_line": placement.end_line,
                        "outdated_reason": placement.outdated_reason,
                    }
                )
            case BayStartPlacement():
                values.update(
                    {
                        "snapshot_file_id": placement.snapshot_file_id,
                        "target_kind": "bay-start",
                        "bay_key": placement.bay_key,
                        "side": placement.side,
                        "outdated_reason": placement.outdated_reason,
                    }
                )
            case FileStartPlacement():
                values.update(
                    {
                        "snapshot_file_id": placement.snapshot_file_id,
                        "target_kind": "file-start",
                        "side": placement.side,
                        "outdated_reason": placement.outdated_reason,
                    }
                )
            case FileMissingPlacement():
                values["outdated_reason"] = "file_missing"
            case FileUnreadablePlacement():
                values["outdated_reason"] = "file_unreadable"
            case ReviewScopePlacement():
                values["target_kind"] = "review"
        return values

    @staticmethod
    def _review_action_values(
        record: ReviewActionInput,
    ) -> _ReviewActionInsertValues:
        payload = record.payload
        values: _ReviewActionInsertValues = {
            "operation_id": record.operation_id,
            "thread_id": record.thread_id,
            "snapshot_id": record.snapshot_id,
            "sequence": record.sequence,
            "kind": payload.kind,
            "profile_id": record.profile_id,
            "comment_id": None,
            "expected_revision": None,
            "body": None,
            "created_at": record.created_at,
            "status_after": record.status_after,
            "attention_after": record.attention_after,
        }
        match payload:
            case CommentCreatedAction() | CommentEditedAction():
                values["comment_id"] = payload.comment.id
                values["body"] = payload.comment.body
                if isinstance(payload, CommentEditedAction):
                    values["expected_revision"] = payload.expected_revision
            case CommentDeletedAction():
                values["comment_id"] = payload.comment_id
                values["expected_revision"] = payload.expected_revision
            case ThreadStateAction():
                if payload.comment is not None:
                    values["comment_id"] = payload.comment.id
                    values["body"] = payload.comment.body
            case ThreadDeletedAction():
                pass
        return values

    @staticmethod
    def _next_review_activity_id(session: Session) -> int:
        # The caller must hold the write transaction so concurrent actions
        # cannot allocate the same activity id.
        latest: int | None = session.execute(
            select(func.max(ReviewAction.activity_id))
        ).scalar_one()
        return 1 if latest is None else latest + 1

    @staticmethod
    def _thread_record(
        placement: ReviewThreadPlacement,
        is_origin: bool,
    ) -> ReviewThreadRecord:
        target_kind_value = placement.target_kind
        match target_kind_value:
            case "range" | "bay-start" | "file-start" | "review" | None:
                target_kind = target_kind_value
            case _:
                raise AssertionError(
                    f"invalid persisted review target kind: {target_kind_value!r}"
                )
        side_value = placement.side
        match side_value:
            case "left" | "right" | None:
                side = side_value
            case _:
                raise AssertionError(
                    f"invalid persisted review side: {side_value!r}"
                )
        reason_value = placement.outdated_reason
        match reason_value:
            case (
                "region_changed"
                | "region_not_found"
                | "bay_not_found"
                | "file_unreadable"
                | "file_missing"
                | None
            ):
                outdated_reason = reason_value
            case _:
                raise AssertionError(
                    f"invalid persisted outdated reason: {reason_value!r}"
                )
        file_id = placement.snapshot_file_id
        bay_key = placement.bay_key
        start, end = placement.start_line, placement.end_line
        location: Placement
        if target_kind == "review":
            assert file_id is None and bay_key is None and side is None
            assert start is None and end is None and outdated_reason is None
            location = ReviewScopePlacement(
                placement.thread_id, placement.snapshot_id
            )
        elif target_kind == "range":
            assert (
                file_id is not None and bay_key is not None and side is not None
            )
            assert start is not None and end is not None
            assert (
                outdated_reason is None or outdated_reason == "region_changed"
            )
            location = RangePlacement(
                placement.thread_id,
                placement.snapshot_id,
                file_id,
                bay_key,
                side,
                start,
                end,
                outdated_reason,
            )
        elif target_kind == "bay-start":
            assert (
                file_id is not None and bay_key is not None and side is not None
            )
            assert start is None and end is None
            assert (
                outdated_reason == "region_not_found"
                or outdated_reason == "bay_not_found"
            )
            location = BayStartPlacement(
                placement.thread_id,
                placement.snapshot_id,
                file_id,
                bay_key,
                side,
                outdated_reason,
            )
        elif target_kind == "file-start":
            assert file_id is not None and side is not None and bay_key is None
            assert start is None and end is None
            assert outdated_reason is None or outdated_reason == "bay_not_found"
            location = FileStartPlacement(
                placement.thread_id,
                placement.snapshot_id,
                file_id,
                side,
                outdated_reason,
            )
        else:
            assert file_id is None and bay_key is None and side is None
            assert start is None and end is None
            assert outdated_reason in {"file_missing", "file_unreadable"}
            location = (
                FileMissingPlacement(placement.thread_id, placement.snapshot_id)
                if outdated_reason == "file_missing"
                else FileUnreadablePlacement(
                    placement.thread_id, placement.snapshot_id
                )
            )
        return ReviewThreadRecord(
            location, is_origin, placement.private_locator
        )

    @staticmethod
    def _action_record(action: ReviewAction) -> ReviewActionRecord:
        kind_value = action.kind
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
        status_value = action.status_after
        match status_value:
            case "open" | "resolved" | "deleted":
                status_after = status_value
            case _:
                raise AssertionError(
                    f"invalid persisted thread status: {status_value!r}"
                )
        attention_value = action.attention_after
        match attention_value:
            case "author" | "reviewer" | "both" | "none":
                attention_after = attention_value
            case _:
                raise AssertionError(
                    f"invalid persisted thread attention: {attention_value!r}"
                )
        comment_id, body, revision = (
            action.comment_id,
            action.body,
            action.expected_revision,
        )
        payload: ReviewActionPayload
        if kind == "thread-created" or kind == "comment-created":
            assert (
                comment_id is not None and body is not None and revision is None
            )
            payload = CommentCreatedAction(
                kind, AuthoredComment(comment_id, body)
            )
        elif kind == "comment-edited":
            assert (
                comment_id is not None
                and body is not None
                and revision is not None
            )
            payload = CommentEditedAction(
                AuthoredComment(comment_id, body), revision
            )
        elif kind == "comment-deleted":
            assert (
                comment_id is not None and body is None and revision is not None
            )
            payload = CommentDeletedAction(comment_id, revision)
        elif kind == "thread-resolved" or kind == "thread-reopened":
            assert revision is None
            assert (comment_id is None) == (body is None)
            comment = (
                AuthoredComment(comment_id, body)
                if comment_id is not None and body is not None
                else None
            )
            payload = ThreadStateAction(kind, comment)
        else:
            assert kind == "thread-deleted"
            assert comment_id is None and body is None and revision is None
            payload = ThreadDeletedAction()
        return ReviewActionRecord(
            ReviewActionInput(
                operation_id=action.operation_id,
                thread_id=action.thread_id,
                snapshot_id=action.snapshot_id,
                sequence=action.sequence,
                profile_id=action.profile_id,
                payload=payload,
                created_at=action.created_at,
                status_after=status_after,
                attention_after=attention_after,
            ),
            activity_id=action.activity_id,
        )

    @staticmethod
    def _file_symlink_records(
        rows: Iterable[SnapshotFileSymlink],
    ) -> dict[tuple[str, Literal["left", "right"]], SnapshotFileSymlinkRecord]:
        result: dict[
            tuple[str, Literal["left", "right"]],
            SnapshotFileSymlinkRecord,
        ] = {}
        for row in rows:
            match row.side:
                case "left" | "right":
                    side: Literal["left", "right"] = row.side
                case _:
                    raise AssertionError(
                        f"invalid persisted symlink side: {row.side!r}"
                    )
            key = row.file_id, side
            assert key not in result, (
                f"duplicate persisted Snapshot link side: {key!r}"
            )
            assert (row.target_capture_path is None) == (
                row.target_hash is None
            ), "Snapshot link target path and hash must have equal presence"
            target: Option[SnapshotLinkTargetRecord] = Nothing()
            if (
                row.target_capture_path is not None
                and row.target_hash is not None
            ):
                target = Some(
                    SnapshotLinkTargetRecord(
                        row.target_capture_path, row.target_hash
                    )
                )
            result[key] = SnapshotFileSymlinkRecord(
                metadata_path=row.metadata_path,
                metadata_hash=row.metadata_hash,
                target=target,
            )
        return result

    @staticmethod
    def _file_record(
        file: SnapshotFile,
        left_path: str | None,
        left_hash: bytes | None,
        right_path: str | None,
        right_hash: bytes | None,
        *,
        left_symlink: SnapshotFileSymlinkRecord | None,
        right_symlink: SnapshotFileSymlinkRecord | None,
    ) -> SnapshotFileRecord:
        assert (left_path is None) == (left_hash is None), (
            "persisted left File path and hash must have equal presence"
        )
        assert (right_path is None) == (right_hash is None), (
            "persisted right File path and hash must have equal presence"
        )
        left = (
            (
                SnapshotRegularFileSideRecord(left_path, left_hash)
                if left_symlink is None
                else SnapshotSymlinkFileSideRecord(
                    left_path, left_hash, left_symlink
                )
            )
            if left_path is not None and left_hash is not None
            else None
        )
        right = (
            (
                SnapshotRegularFileSideRecord(right_path, right_hash)
                if right_symlink is None
                else SnapshotSymlinkFileSideRecord(
                    right_path, right_hash, right_symlink
                )
            )
            if right_path is not None and right_hash is not None
            else None
        )
        assert left is not None or right is not None, (
            f"persisted Snapshot File has no sides: {file.id!r}"
        )
        assert left_symlink is None or left is not None, (
            "persisted left link has no captured File side"
        )
        assert right_symlink is None or right is not None, (
            "persisted right link has no captured File side"
        )
        directory = Path(file.path)
        for link in (left_symlink, right_symlink):
            if link is None:
                continue
            assert Path(link.metadata_path).parent == directory, (
                "Snapshot link metadata must share its File capture directory"
            )
            if Some.present(link.target):
                assert Path(link.target.value.path).parent == directory, (
                    "Snapshot link target must share its File capture directory"
                )
                assert link.target.value.path != link.metadata_path, (
                    "Snapshot link metadata and target paths must differ"
                )
        return SnapshotFileRecord(
            id=file.id,
            snapshot_id=file.snapshot_id,
            path=file.path,
            tracked=file.tracked,
            change_type=file.change_type,
            error=file.error,
            left=maybe(left),
            right=maybe(right),
        )

    def review_profile(self, profile_id: int) -> Optional[ReviewProfileRecord]:
        with Session(self.engine) as session:
            row = session.execute(
                _review_profile_select(UserProfile.id == profile_id)
            ).one_or_none()
        if row is None:
            return None
        return _review_profile(row)

    def room_identity(self, snapshot_id: str) -> Optional[RoomIdentity]:
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
        symlink_storage_paths: set[str] = set()
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
            assert Some.present(file.left) or Some.present(file.right), (
                "Snapshot File must have at least one captured side"
            )
            assert file.tracked or not Some.present(file.left), (
                "an intruding File cannot have a left side"
            )
            file_repository_paths = (
                file.left.get_map(lambda side: side.repository_path),
                file.right.get_map(lambda side: side.repository_path),
            )
            assert file_repository_paths not in repository_paths, (
                "duplicate Snapshot repository paths: "
                f"{file_repository_paths!r}"
            )
            repository_paths.add(file_repository_paths)
            file_directory = Path(file.path)
            for side in (file.left, file.right):
                if not Some.present(side) or not isinstance(
                    side.value, SnapshotSymlinkFileSideRecord
                ):
                    continue
                link = side.value.link
                metadata_path = Path(link.metadata_path)
                assert metadata_path.parent == file_directory, (
                    "Snapshot link metadata must share its File capture directory"
                )
                assert link.metadata_path not in symlink_storage_paths, (
                    "duplicate Snapshot link metadata path"
                )
                symlink_storage_paths.add(link.metadata_path)
                if Some.present(link.target):
                    target_capture_path = Path(link.target.value.path)
                    assert target_capture_path.parent == file_directory, (
                        "Snapshot link target must share its File capture directory"
                    )
                    assert (
                        link.target.value.path not in symlink_storage_paths
                    ), "duplicate Snapshot link target path"
                    symlink_storage_paths.add(link.target.value.path)
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
            assert thread.placement.snapshot_id == snapshot.id, (
                "published review Thread must address the new Snapshot"
            )
            assert not thread.is_origin, (
                "a new Snapshot cannot contain an existing Thread origin"
            )
            if isinstance(
                thread.placement,
                (RangePlacement, BayStartPlacement, FileStartPlacement),
            ):
                assert thread.placement.snapshot_file_id in file_ids, (
                    "published review Thread references a File outside its Snapshot"
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
                    "repository_path": file.left.value.repository_path,
                    "content_hash": file.left.value.content_hash,
                }
                for file in snapshot.files
                if Some.present(file.left)
            ]
            if left_rows != []:
                session.execute(insert(SnapshotFileLeft), left_rows)
            right_rows = [
                {
                    "file_id": file.id,
                    "repository_path": file.right.value.repository_path,
                    "content_hash": file.right.value.content_hash,
                }
                for file in snapshot.files
                if Some.present(file.right)
            ]
            if right_rows != []:
                session.execute(insert(SnapshotFileRight), right_rows)
            symlink_rows = [
                {
                    "file_id": file.id,
                    "side": side,
                    "metadata_path": link.metadata_path,
                    "metadata_hash": link.metadata_hash,
                    "target_capture_path": link.target.get_map(
                        lambda target: target.path
                    ),
                    "target_hash": link.target.get_map(
                        lambda target: target.content_hash
                    ),
                }
                for file in snapshot.files
                for side, record in (("left", file.left), ("right", file.right))
                if Some.present(record)
                and isinstance(record.value, SnapshotSymlinkFileSideRecord)
                for link in (record.value.link,)
            ]
            if symlink_rows != []:
                session.execute(insert(SnapshotFileSymlink), symlink_rows)
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
                    SnapshotFile,
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
            symlinks = self._file_symlink_records(
                session.execute(
                    select(SnapshotFileSymlink)
                    .join(
                        SnapshotFile,
                        SnapshotFile.id == SnapshotFileSymlink.file_id,
                    )
                    .where(SnapshotFile.snapshot_id == snapshot_id)
                ).scalars()
            )

        files = [
            self._file_record(
                *row,
                left_symlink=symlinks.get((row[0].id, "left")),
                right_symlink=symlinks.get((row[0].id, "right")),
            )
            for row in file_rows
        ]
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
    ) -> Result[
        SnapshotFileLoadRecord, Literal["snapshot-unavailable", "file-absent"]
    ]:
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            row = session.execute(
                select(
                    SnapshotFile,
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
                return Err("file-absent" if exists else "snapshot-unavailable")
            symlinks = self._file_symlink_records(
                session.execute(
                    select(SnapshotFileSymlink).where(
                        SnapshotFileSymlink.file_id == row[0].id
                    )
                ).scalars()
            )

        return Ok(
            SnapshotFileLoadRecord(
                file=self._file_record(
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    left_symlink=symlinks.get((row[0].id, "left")),
                    right_symlink=symlinks.get((row[0].id, "right")),
                ),
                lazy_reason=row[5],
            )
        )

    def snapshot_files_by_pairs(
        self,
        identity: RoomIdentity,
        *,
        snapshot_id: str,
        pairs: tuple[tuple[Optional[str], Optional[str]], ...],
    ) -> Result[
        dict[tuple[Optional[str], Optional[str]], SnapshotFileRecord],
        Literal["snapshot-unavailable"],
    ]:
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        # Nullable sides make a tuple IN unusable; OR exact per-pair conditions.
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
                        SnapshotFile,
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
            symlinks = self._file_symlink_records(
                session.execute(
                    select(SnapshotFileSymlink).where(
                        SnapshotFileSymlink.file_id.in_(
                            {row[0].id for row in rows}
                        )
                    )
                ).scalars()
                if rows != []
                else ()
            )
            found = {}
            for row in rows:
                record = self._file_record(
                    *row,
                    left_symlink=symlinks.get((row[0].id, "left")),
                    right_symlink=symlinks.get((row[0].id, "right")),
                )
                left = record.left.get_map(lambda side: side.repository_path)
                right = record.right.get_map(lambda side: side.repository_path)
                found[(left, right)] = record
            # The schema does not make the path pair unique, so a collision
            # must fail rather than bind to an arbitrary duplicate.
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
                return Ok({}) if exists else Err("snapshot-unavailable")
        return Ok(found)

    def review_thread_files(
        self,
        selected_snapshot_id: str,
        origin_refs: tuple[tuple[str, str], ...],
        located_file_ids: tuple[str, ...],
        absent_origin_refs: tuple[tuple[str, str], ...],
        comparison_origin_refs: tuple[tuple[str, str], ...],
    ) -> tuple[
        dict[tuple[str, str], SnapshotFileRecord],
        dict[str, SnapshotFileRecord],
        tuple[str, ...],
        dict[tuple[str, str], SnapshotFileRecord],
    ]:
        file_query = (
            select(
                SnapshotFile,
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
                    file_query.where(
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
                    file_query.where(
                        SnapshotFile.snapshot_id == selected_snapshot_id,
                        SnapshotFile.id.in_(set(located_file_ids)),
                    )
                ).all()
                if located_file_ids != ()
                else []
            )
            raw_origin_files = {
                (row[0].snapshot_id, row[0].id): row for row in origin_rows
            }
            assert raw_origin_files.keys() == set(origin_refs), (
                "review origin references a missing Snapshot File"
            )
            pair_by_ref: dict[
                tuple[str, str], tuple[Optional[str], Optional[str]]
            ] = {}
            for ref in (*absent_origin_refs, *comparison_origin_refs):
                row = raw_origin_files[ref]
                pair_by_ref[ref] = (row.left_path, row.right_path)
            searched_pairs = set(pair_by_ref.values())
            matching_rows = list(origin_rows[:0])
            if searched_pairs != set():
                pair_clauses = [
                    and_(
                        SnapshotFileLeft.file_id.is_(None)
                        if left_path is None
                        else SnapshotFileLeft.repository_path == left_path,
                        SnapshotFileRight.file_id.is_(None)
                        if right_path is None
                        else SnapshotFileRight.repository_path == right_path,
                    )
                    for left_path, right_path in searched_pairs
                ]
                matching_rows = list(
                    session.execute(
                        file_query.where(
                            SnapshotFile.snapshot_id == selected_snapshot_id,
                            or_(*pair_clauses),
                        )
                    ).all()
                )
            file_ids = {
                row[0].id
                for row in (*origin_rows, *selected_rows, *matching_rows)
            }
            symlinks = self._file_symlink_records(
                session.execute(
                    select(SnapshotFileSymlink).where(
                        SnapshotFileSymlink.file_id.in_(file_ids)
                    )
                ).scalars()
                if file_ids
                else ()
            )
            origin_files = {
                ref: self._file_record(
                    *row,
                    left_symlink=symlinks.get((row[0].id, "left")),
                    right_symlink=symlinks.get((row[0].id, "right")),
                )
                for ref, row in raw_origin_files.items()
            }
            matching_files_by_pair: dict[
                tuple[Optional[str], Optional[str]], SnapshotFileRecord
            ] = {}
            for row in matching_rows:
                record = self._file_record(
                    *row,
                    left_symlink=symlinks.get((row[0].id, "left")),
                    right_symlink=symlinks.get((row[0].id, "right")),
                )
                pair = (row.left_path, row.right_path)
                assert pair not in matching_files_by_pair, (
                    "Snapshot contains duplicate File pairs"
                )
                matching_files_by_pair[pair] = record
            conflict_ids = tuple(
                matching_files_by_pair[pair].id
                for pair in {pair_by_ref[ref] for ref in absent_origin_refs}
                if pair in matching_files_by_pair
            )
        return (
            origin_files,
            {
                row[0].id: self._file_record(
                    *row,
                    left_symlink=symlinks.get((row[0].id, "left")),
                    right_symlink=symlinks.get((row[0].id, "right")),
                )
                for row in selected_rows
            },
            conflict_ids,
            {
                ref: matching_files_by_pair[pair_by_ref[ref]]
                for ref in comparison_origin_refs
                if pair_by_ref[ref] in matching_files_by_pair
            },
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
        scope: Literal["snapshot", "room"],
        through_activity_id: Optional[int],
    ) -> Result[
        tuple[ReviewThreadsRecord, int], Literal["snapshot-unavailable"]
    ]:
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
                return Err("snapshot-unavailable")
            if through_activity_id is None:
                latest_activity_id: int | None = session.execute(
                    select(func.max(ReviewAction.activity_id))
                    .join(Snapshot, Snapshot.id == ReviewAction.snapshot_id)
                    .where(Snapshot.room_id == room_id)
                ).scalar_one()
                through_activity_id = (
                    0 if latest_activity_id is None else latest_activity_id
                )
            # One grouped pass over the pivot-bounded actions yields each
            # Thread's latest and creation activity; joining the latest action
            # row then supplies status and attention.
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
            selected_where: list[ColumnElement[bool]] = [
                latest.c.first_activity.is_not(None)
            ]
            if scope == "snapshot":
                selected_where.append(
                    ReviewThreadPlacement.thread_id.is_not(None)
                )
            else:
                assert scope == "room"
            if state == "open":
                selected_where.append(last_action.status_after == "open")
            else:
                assert state == "all"
            if attention is not None:
                selected_where.append(
                    last_action.attention_after.in_((attention, "both"))
                )

            def filtered[*T](
                query: Select[tuple[*T]],
            ) -> Select[tuple[*T]]:
                return (
                    query.select_from(ReviewThread)
                    .join(
                        Snapshot,
                        Snapshot.id == ReviewThread.origin_snapshot_id,
                    )
                    .outerjoin(
                        ReviewThreadPlacement,
                        (
                            ReviewThreadPlacement.thread_id
                            == ReviewThread.thread_id
                        )
                        & (ReviewThreadPlacement.snapshot_id == snapshot_id),
                    )
                    .join(
                        latest,
                        latest.c.thread_id == ReviewThread.thread_id,
                    )
                    .join(
                        last_action,
                        (last_action.thread_id == ReviewThread.thread_id)
                        & (
                            last_action.activity_id == latest.c.last_activity_id
                        ),
                    )
                    .where(Snapshot.room_id == room_id, *selected_where)
                )

            total_threads = session.execute(
                select(func.count()).select_from(
                    filtered(select(ReviewThread.thread_id)).subquery()
                )
            ).scalar_one()
            selected_query = (
                filtered(
                    select(
                        ReviewThread.thread_id,
                        ReviewThreadPlacement,
                    )
                )
                .order_by(
                    state_rank,
                    latest.c.first_activity,
                    ReviewThread.thread_id,
                )
                .offset(offset)
            )
            if limit is not None:
                selected_query = selected_query.limit(limit)
            selected_rows = session.execute(selected_query).all()
            thread_ids = [row[0] for row in selected_rows]
            if thread_ids == []:
                return Ok(
                    (
                        ReviewThreadsRecord((), (), (), (), total_threads),
                        through_activity_id,
                    )
                )
            origin_rows = session.execute(
                select(
                    ReviewThreadPlacement,
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
            origins_by_thread = {row[0].thread_id: row for row in origin_rows}
            assert origins_by_thread.keys() == set(thread_ids), (
                "Snapshot review placement exists without a Thread origin"
            )
            origin_rows = [
                origins_by_thread[thread_id] for thread_id in thread_ids
            ]
            selected_records = [
                self._thread_record(placement, False)
                if placement is not None
                else self._thread_record(*origins_by_thread[thread_id])
                for thread_id, placement in selected_rows
            ]
            actions = session.scalars(
                select(ReviewAction)
                .where(
                    ReviewAction.thread_id.in_(thread_ids),
                    ReviewAction.activity_id <= through_activity_id,
                )
                .order_by(ReviewAction.thread_id, ReviewAction.sequence)
            ).all()
            profile_ids = {action.profile_id for action in actions}
            profile_rows = session.execute(
                _review_profile_select(UserProfile.id.in_(profile_ids))
            ).all()
        profiles = [_review_profile(row) for row in profile_rows]
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return Ok(
            (
                ReviewThreadsRecord(
                    threads=tuple(selected_records),
                    origins=tuple(
                        self._thread_record(*row) for row in origin_rows
                    ),
                    actions=tuple(
                        self._action_record(action) for action in actions
                    ),
                    profiles=tuple(profiles),
                    total_threads=total_threads,
                ),
                through_activity_id,
            )
        )

    def review_thread(
        self,
        identity: RoomIdentity,
        snapshot_id: str,
        thread_id: str,
    ) -> Result[ReviewThreadsRecord, Literal["snapshot-unavailable"]]:
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
                return Err("snapshot-unavailable")
            selected_row = session.execute(
                select(
                    ReviewThreadPlacement,
                    literal(False).label("is_origin"),
                ).where(
                    ReviewThreadPlacement.snapshot_id == snapshot_id,
                    ReviewThreadPlacement.thread_id == thread_id,
                )
            ).one_or_none()
            origin_row = session.execute(
                select(
                    ReviewThreadPlacement,
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
                return Ok(ReviewThreadsRecord((), (), (), (), 0))
            if selected_row is None:
                return Ok(ReviewThreadsRecord((), (), (), (), 0))
            actions = session.scalars(
                select(ReviewAction)
                .where(ReviewAction.thread_id == thread_id)
                .order_by(ReviewAction.sequence)
            ).all()
            assert actions != [], "review Thread has no creation action"
            profile_ids = {action.profile_id for action in actions}
            profile_rows = session.execute(
                _review_profile_select(UserProfile.id.in_(profile_ids))
            ).all()
        profiles = tuple(_review_profile(row) for row in profile_rows)
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return Ok(
            ReviewThreadsRecord(
                threads=(self._thread_record(*selected_row),),
                origins=(self._thread_record(*origin_row),),
                actions=tuple(
                    self._action_record(action) for action in actions
                ),
                profiles=profiles,
                total_threads=1,
            )
        )

    def review_actions(
        self,
        snapshot_id: str,
        thread_id: str,
    ) -> Optional[
        tuple[tuple[ReviewActionRecord, ...], tuple[ReviewProfileRecord, ...]]
    ]:
        with Session(self.engine) as session:
            placed = session.execute(
                select(ReviewThreadPlacement.thread_id).where(
                    ReviewThreadPlacement.snapshot_id == snapshot_id,
                    ReviewThreadPlacement.thread_id == thread_id,
                )
            ).scalar_one_or_none()
            if placed is None:
                return None
            actions = session.scalars(
                select(ReviewAction)
                .where(ReviewAction.thread_id == thread_id)
                .order_by(ReviewAction.sequence)
            ).all()
            assert actions != [], "review Thread has no creation action"
            profile_ids = {action.profile_id for action in actions}
            profile_rows = session.execute(
                _review_profile_select(UserProfile.id.in_(profile_ids))
            ).all()
        profiles = tuple(_review_profile(row) for row in profile_rows)
        assert {profile.id for profile in profiles} == profile_ids, (
            "review action references a missing Profile"
        )
        return (
            tuple(self._action_record(action) for action in actions),
            profiles,
        )

    def review_profiles(
        self, profile_ids: tuple[int, ...]
    ) -> tuple[ReviewProfileRecord, ...]:
        if profile_ids == ():
            return ()
        with Session(self.engine) as session:
            rows = session.execute(
                _review_profile_select(UserProfile.id.in_(set(profile_ids)))
            ).all()
        return tuple(_review_profile(row) for row in rows)

    def review_actions_many(
        self,
        snapshot_id: str,
        thread_ids: tuple[str, ...],
    ) -> tuple[
        dict[str, tuple[ReviewActionRecord, ...]],
        tuple[ReviewProfileRecord, ...],
    ]:
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
            actions = session.scalars(
                select(ReviewAction)
                .where(ReviewAction.thread_id.in_(placed))
                .order_by(ReviewAction.thread_id, ReviewAction.sequence)
            ).all()
            profile_ids = {action.profile_id for action in actions}
            profile_rows = session.execute(
                _review_profile_select(UserProfile.id.in_(profile_ids))
            ).all()
        actions_by_thread: dict[str, list[ReviewActionRecord]] = {}
        for action in actions:
            actions_by_thread.setdefault(action.thread_id, []).append(
                self._action_record(action)
            )
        assert actions_by_thread.keys() == placed, (
            "review Thread has no creation action"
        )
        profiles = tuple(_review_profile(row) for row in profile_rows)
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
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    ReviewThreadPlacement,
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
        return tuple(self._thread_record(*row) for row in rows)

    def insert_review_threads(
        self,
        rows: tuple[ReviewThreadRecord, ...],
    ) -> None:
        if rows == ():
            return
        pairs = [
            (row.placement.thread_id, row.placement.snapshot_id) for row in rows
        ]
        assert len(pairs) == len(set(pairs)), (
            "duplicate review Thread pair supplied for insertion"
        )
        with Session(self.engine) as session, session.begin():
            persisted_rows = session.execute(
                select(
                    ReviewThreadPlacement,
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
                (row[0].thread_id, row[0].snapshot_id): self._thread_record(
                    *row
                )
                for row in persisted_rows
            }
            additions = []
            for record in rows:
                previous = persisted.get(
                    (record.placement.thread_id, record.placement.snapshot_id)
                )
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
        first_action: ReviewActionInput,
    ) -> None:
        assert rows != (), "Thread creation requires at least its origin row"
        assert sum(1 for row in rows if row.is_origin) == 1, (
            "Thread creation requires exactly one origin row"
        )
        assert {row.placement.thread_id for row in rows} == {
            first_action.thread_id
        }, "Thread creation rows and first action must share one Thread id"
        assert first_action.payload.kind == "thread-created"
        assert first_action.sequence == 0
        with Session(self.engine) as session, session.begin():
            session.execute(
                insert(ReviewThread).values(
                    thread_id=first_action.thread_id,
                    origin_snapshot_id=next(
                        row.placement.snapshot_id
                        for row in rows
                        if row.is_origin
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
        actions: tuple[ReviewActionInput, ...],
    ) -> tuple[int, ...]:
        assert actions != (), "review batch must contain at least one action"
        pairs = [
            (row.placement.thread_id, row.placement.snapshot_id)
            for row in thread_rows
        ]
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
                                "thread_id": row.placement.thread_id,
                                "origin_snapshot_id": row.placement.snapshot_id,
                            }
                            for row in origins
                        ],
                    )
                session.execute(
                    insert(ReviewThreadPlacement),
                    [self._review_thread_values(row) for row in thread_rows],
                )
            next_activity_id = self._next_review_activity_id(session)
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
        return tuple(range(next_activity_id, next_activity_id + len(actions)))

    def review_latest_activity_id(self, identity: RoomIdentity) -> int:
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            latest: int | None = session.execute(
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

    def rooms_for_mark(
        self, mark_id: int
    ) -> tuple[tuple[RoomIdentity, str], ...]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(Room.id, Room.tab, Room.backend_key, Snapshot.id)
                .join(Snapshot, Snapshot.room_id == Room.id)
                .where(Room.mark_id == mark_id)
                # `snapshot.rowid` grows with publication, so within one Room
                # the last row is the most recently published Snapshot.
                .order_by(Room.id, literal_column("snapshot.rowid"))
            ).all()
        rooms: dict[int, tuple[RoomIdentity, str]] = {}
        for room_id, tab, backend_key, snapshot_id in rows:
            rooms[room_id] = (
                RoomIdentity(
                    mark_id=mark_id, tab=tab, correspondence_key=backend_key
                ),
                snapshot_id,
            )
        return tuple(rooms.values())

    def review_thread_origin_snapshot(self, thread_id: str) -> Optional[str]:
        with Session(self.engine) as session:
            return session.execute(
                select(ReviewThread.origin_snapshot_id).where(
                    ReviewThread.thread_id == thread_id
                )
            ).scalar_one_or_none()

    def review_thread_histories(
        self, thread_ids: Iterable[str]
    ) -> tuple[
        dict[str, ReviewThreadHistory],
        tuple[ReviewProfileRecord, ...],
    ]:
        wanted = set(thread_ids)
        if wanted == set():
            return {}, ()
        with Session(self.engine) as session:
            origin_rows = session.execute(
                select(
                    ReviewThreadPlacement,
                    Room.mark_id,
                    Room.tab,
                    Room.backend_key,
                    SnapshotFileLeft.repository_path.label("left_path"),
                    SnapshotFileRight.repository_path.label("right_path"),
                )
                .join(
                    ReviewThread,
                    (ReviewThread.thread_id == ReviewThreadPlacement.thread_id)
                    & (
                        ReviewThread.origin_snapshot_id
                        == ReviewThreadPlacement.snapshot_id
                    ),
                )
                .join(
                    Snapshot, Snapshot.id == ReviewThreadPlacement.snapshot_id
                )
                .join(Room, Room.id == Snapshot.room_id)
                .outerjoin(
                    SnapshotFileLeft,
                    SnapshotFileLeft.file_id
                    == ReviewThreadPlacement.snapshot_file_id,
                )
                .outerjoin(
                    SnapshotFileRight,
                    SnapshotFileRight.file_id
                    == ReviewThreadPlacement.snapshot_file_id,
                )
                .where(ReviewThreadPlacement.thread_id.in_(wanted))
            ).all()
            actions = session.scalars(
                select(ReviewAction)
                .where(ReviewAction.thread_id.in_(wanted))
                .order_by(ReviewAction.thread_id, ReviewAction.sequence)
            ).all()
            profile_ids = {action.profile_id for action in actions}
            profile_rows = session.execute(
                _review_profile_select(UserProfile.id.in_(profile_ids))
            ).all()
        actions_by_thread: dict[str, list[ReviewActionRecord]] = {}
        for action in actions:
            actions_by_thread.setdefault(action.thread_id, []).append(
                self._action_record(action)
            )
        histories: dict[str, ReviewThreadHistory] = {}
        for row in origin_rows:
            placement = row[0]
            thread_actions = actions_by_thread.get(placement.thread_id)
            assert thread_actions is not None, (
                "review Thread has no creation action"
            )
            histories[placement.thread_id] = ReviewThreadHistory(
                identity=RoomIdentity(
                    mark_id=row.mark_id,
                    tab=row.tab,
                    correspondence_key=row.backend_key,
                ),
                origin=self._thread_record(placement, True),
                origin_left_path=row.left_path,
                origin_right_path=row.right_path,
                actions=tuple(thread_actions),
            )
        assert histories.keys() == actions_by_thread.keys(), (
            "review action has no origin placement"
        )
        profiles = tuple(_review_profile(row) for row in profile_rows)
        return histories, profiles

    def review_latest_activity_by_thread(
        self, thread_ids: Iterable[str], through_activity_id: int
    ) -> dict[str, int]:
        wanted = set(thread_ids)
        if wanted == set():
            return {}
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    ReviewAction.thread_id,
                    func.max(ReviewAction.activity_id),
                )
                .where(
                    ReviewAction.thread_id.in_(wanted),
                    ReviewAction.activity_id <= through_activity_id,
                )
                .group_by(ReviewAction.thread_id)
            ).all()
        return {thread_id: latest for thread_id, latest in rows}

    def review_mark_latest_activity_id(self, mark_id: int) -> int:
        with Session(self.engine) as session:
            latest: int | None = session.execute(
                select(func.max(ReviewAction.activity_id))
                .join(Snapshot, Snapshot.id == ReviewAction.snapshot_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(Room.mark_id == mark_id)
            ).scalar_one()
        return 0 if latest is None else latest

    def review_mark_actions(
        self, mark_id: int, after: int, limit: int
    ) -> tuple[ReviewMarkActionRecord, ...]:
        assert after >= 0 and limit >= 1
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    ReviewAction,
                    Room.mark_id,
                    Room.tab,
                    Room.backend_key,
                )
                .join(Snapshot, Snapshot.id == ReviewAction.snapshot_id)
                .join(Room, Room.id == Snapshot.room_id)
                .where(
                    Room.mark_id == mark_id,
                    ReviewAction.activity_id > after,
                )
                .order_by(ReviewAction.activity_id)
                .limit(limit)
            ).all()
        return tuple(
            ReviewMarkActionRecord(
                self._action_record(row[0]),
                RoomIdentity(
                    mark_id=row.mark_id,
                    tab=row.tab,
                    correspondence_key=row.backend_key,
                ),
            )
            for row in rows
        )

    def review_attention_counts(
        self, identity: RoomIdentity, through_activity_id: int
    ) -> dict[Literal["author", "reviewer", "both"], int]:
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
        with Session(self.engine) as session:
            return session.execute(
                select(ReviewAction.thread_id)
                .distinct()
                .join(
                    ReviewThreadPlacement,
                    ReviewThreadPlacement.thread_id == ReviewAction.thread_id,
                )
                .where(
                    ReviewThreadPlacement.snapshot_id == snapshot_id,
                    ReviewAction.comment_id == comment_id,
                )
            ).scalar_one_or_none()

    def append_review_action(self, record: ReviewActionInput) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(
                insert(ReviewAction).values(
                    **self._review_action_values(record),
                    activity_id=self._next_review_activity_id(session),
                )
            )
