from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Literal, NotRequired, Optional, TypedDict
from uuid import UUID

from dirdiff.db import ReviewProfileRecord, RoomStore
from dirdiff.engines import DirdiffError
from dirdiff.util import Option, Some

__all__ = [
    "AddComment",
    "BatchComment",
    "BatchCreate",
    "BatchDeleteComment",
    "BatchDeleteThread",
    "BatchEditComment",
    "BatchReopen",
    "BatchResolve",
    "ChangeThreadState",
    "CreateReviewThread",
    "CreateThread",
    "DeleteComment",
    "EditComment",
    "FilePair",
    "LineRange",
    "ProfileAuthor",
    "ReviewBatchAction",
    "ReviewBatchResult",
    "ReviewCommentView",
    "ReviewError",
    "ReviewErrorCode",
    "ReviewExcerptView",
    "ReviewFilePairView",
    "ReviewOriginView",
    "ReviewProfileView",
    "ReviewRole",
    "TextTarget",
    "ThreadDiscussionView",
    "ThreadLocationSourceView",
    "ThreadPlacementView",
    "ThreadSummaryView",
    "ThreadUpdateView",
    "action_timestamp",
    "room_write_lock",
    "validate_author",
    "validate_comment_body",
]

ReviewErrorCode = Literal[
    "profile_not_found",
    "thread_not_found",
    "comment_not_found",
    "invalid_target",
    "revision_conflict",
    "state_conflict",
    "forbidden",
]


class ReviewError(DirdiffError):
    def __init__(self, code: ReviewErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FilePair:
    left_path: Option[str]

    right_path: Option[str]

    def __post_init__(self) -> None:
        if not Some.present(self.left_path) and not Some.present(
            self.right_path
        ):
            raise ValueError("A review File pair requires at least one side.")
        for optional_path in (self.left_path, self.right_path):
            if not Some.present(optional_path):
                continue
            value = optional_path.value
            path = PurePosixPath(value)
            if (
                value in {"", ".", ".."}
                or path.is_absolute()
                or ".." in path.parts
                or value != path.as_posix()
            ):
                raise ValueError(
                    "Review File sides must be normalized relative names."
                )


@dataclass(frozen=True)
class LineRange:
    start_line: int

    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("Review line range must be positive and ordered.")


@dataclass(frozen=True)
class TextTarget:
    file: FilePair

    bay_key: str

    side: Literal["left", "right"]

    range: LineRange

    kind: Literal["text"] = "text"

    def __post_init__(self) -> None:
        if self.bay_key == "":
            raise ValueError("Review bay key cannot be empty.")
        if self.side == "left" and not Some.present(self.file.left_path):
            raise ValueError("The selected left side is absent.")
        if self.side == "right" and not Some.present(self.file.right_path):
            raise ValueError("The selected right side is absent.")


ReviewTarget = TextTarget


@dataclass(frozen=True)
class ProfileAuthor:
    profile_id: int

    def __post_init__(self) -> None:
        if self.profile_id < 1:
            raise ValueError("Review Profile id must be positive.")


@dataclass(frozen=True)
class CreateThread:
    thread_id: UUID

    operation_id: UUID

    comment_id: UUID

    author: ProfileAuthor

    target: ReviewTarget

    body: str


@dataclass(frozen=True)
class AddComment:
    operation_id: UUID

    comment_id: UUID

    author: ProfileAuthor

    body: str


@dataclass(frozen=True)
class EditComment:
    operation_id: UUID

    author: ProfileAuthor

    body: str


@dataclass(frozen=True)
class DeleteComment:
    operation_id: UUID

    author: ProfileAuthor


@dataclass(frozen=True)
class ChangeThreadState:
    operation_id: UUID

    author: ProfileAuthor

    comment_id: Optional[UUID]

    body: Optional[str]

    def __post_init__(self) -> None:
        assert (self.comment_id is None) == (self.body is None)


type ReviewRole = Literal["author", "reviewer"]


@dataclass(frozen=True)
class CreateReviewThread:
    thread_id: UUID

    operation_id: UUID

    comment_id: UUID

    author: ProfileAuthor

    body: str


@dataclass(frozen=True)
class BatchCreate:
    snapshot_id: UUID

    command: CreateThread | CreateReviewThread

    role: ReviewRole


@dataclass(frozen=True)
class BatchComment:
    thread_id: UUID

    command: AddComment

    role: ReviewRole


@dataclass(frozen=True)
class BatchResolve:
    thread_id: UUID

    command: ChangeThreadState


@dataclass(frozen=True)
class BatchReopen:
    thread_id: UUID

    command: ChangeThreadState

    role: ReviewRole


@dataclass(frozen=True)
class BatchEditComment:
    thread_id: UUID

    comment_id: UUID

    command: EditComment


@dataclass(frozen=True)
class BatchDeleteComment:
    thread_id: UUID

    comment_id: UUID

    command: DeleteComment


@dataclass(frozen=True)
class BatchDeleteThread:
    thread_id: UUID

    command: ChangeThreadState


type ReviewBatchAction = (
    BatchCreate
    | BatchComment
    | BatchResolve
    | BatchReopen
    | BatchEditComment
    | BatchDeleteComment
    | BatchDeleteThread
)


@dataclass(frozen=True)
class ReviewBatchResult:
    thread_id: UUID

    comment_id: Optional[UUID]

    activity_id: int

    status: Literal["open", "resolved", "deleted"]

    attention: Literal["author", "reviewer", "both", "none"]


class ReviewProfileView(TypedDict):
    profile_id: int

    display_name: str

    is_agent: bool

    model: Optional[str]

    harness: Optional[str]


class ReviewCommentView(TypedDict):
    comment_id: str

    sequence: int

    author: ReviewProfileView

    revision: int

    body: Optional[str]

    deleted: bool

    created_at: datetime

    updated_at: datetime


class ReviewFilePairView(TypedDict):
    left_path: str | None

    right_path: str | None


class ReviewTextBayView(TypedDict):
    bay_key: str


class ReviewLineRangeView(TypedDict):
    start_line: int

    end_line: int


class ReviewExcerptView(TypedDict):
    side: Literal["left", "right"]

    start_line: int

    selected_start_line: int

    selected_end_line: int

    lines: list[str]


class TextReviewOriginView(TypedDict):
    kind: Literal["text"]

    file: ReviewFilePairView

    bay: ReviewTextBayView

    side: Literal["left", "right"]

    range: ReviewLineRangeView

    excerpt: NotRequired[ReviewExcerptView]


class FileStartReviewOriginView(TypedDict):
    kind: Literal["file-start"]

    file: ReviewFilePairView

    side: Literal["left", "right"]


class ReviewScopeOriginView(TypedDict):
    kind: Literal["review"]


type ReviewOriginView = (
    TextReviewOriginView | FileStartReviewOriginView | ReviewScopeOriginView
)


class ThreadRegionPlacementView(TypedDict):
    kind: Literal["region-kept", "region-changed"]

    range: ReviewLineRangeView


class ThreadRegionLostPlacementView(TypedDict):
    kind: Literal["region-lost"]


class ThreadBayLostPlacementView(TypedDict):
    kind: Literal["bay-lost"]

    bay: ReviewTextBayView


class ThreadSideLostPlacementView(TypedDict):
    kind: Literal["side-lost"]


class ThreadFileAbsentPlacementView(TypedDict):
    kind: Literal["file-absent"]


class ThreadFileUnreadablePlacementView(TypedDict):
    kind: Literal["file-unreadable"]


class ThreadWholeFilePlacementView(TypedDict):
    kind: Literal["whole-file"]


class ThreadReviewScopePlacementView(TypedDict):
    kind: Literal["review-scope"]


type ThreadPlacementView = (
    ThreadRegionPlacementView
    | ThreadRegionLostPlacementView
    | ThreadBayLostPlacementView
    | ThreadSideLostPlacementView
    | ThreadFileAbsentPlacementView
    | ThreadFileUnreadablePlacementView
    | ThreadWholeFilePlacementView
    | ThreadReviewScopePlacementView
)


class ThreadDisplayedLocationSourceView(TypedDict):
    kind: Literal["displayed-snapshot"]


class ThreadOriginLocationSourceView(TypedDict):
    kind: Literal["origin-snapshot"]

    file_state: Literal["unchanged", "changed", "missing", "unreadable"]


type ThreadLocationSourceView = (
    ThreadDisplayedLocationSourceView | ThreadOriginLocationSourceView
)


class ThreadDiscussionView(TypedDict):
    thread_id: str

    snapshot_id: str

    created_at: datetime

    state: Literal["open", "resolved", "deleted"]

    attention: Literal["author", "reviewer", "both", "none"]

    discussion_revision: int

    origin_target: ReviewOriginView

    placement: ThreadPlacementView

    location_source: ThreadLocationSourceView

    comments: list[ReviewCommentView]


class ThreadSummaryView(TypedDict):
    thread_id: str

    state: Literal["open", "resolved", "deleted"]

    attention: Literal["author", "reviewer", "both", "none"]

    origin_target: ReviewOriginView

    placement: ThreadPlacementView

    first_comment: ReviewCommentView

    latest_comment: ReviewCommentView

    comment_count: int


class ThreadUpdateView(TypedDict):
    thread_id: str

    snapshot_id: str

    state: Literal["open", "resolved", "deleted"]

    attention: Literal["author", "reviewer", "both", "none"]

    discussion_revision: int

    comment: Optional[ReviewCommentView]


def action_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def validate_comment_body(body: str) -> None:
    if body.strip() == "":
        raise ReviewError("invalid_target", "Comment body cannot be blank.")


def validate_author(
    database: RoomStore, author: ProfileAuthor
) -> ReviewProfileRecord:
    profile = database.review_profile(author.profile_id)
    if profile is None:
        raise ReviewError(
            "profile_not_found", f"Unknown Profile: {author.profile_id}"
        )
    return profile


@contextmanager
def room_write_lock(thread_lock: Lock, lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with thread_lock, lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
