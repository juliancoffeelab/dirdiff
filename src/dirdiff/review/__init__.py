"""Expose persistent review discussions and external-agent instruments.

The facade preserves the complete public interface formerly defined by
`dirdiff.review`. Shared contracts, bound Thread behavior, Snapshot placement,
and external-agent batches live in separate sibling modules. SQL, Snapshot
publication, and HTTP serialization remain outside this package.
"""

from dirdiff.review.base import (
    AddComment,
    ChangeThreadState,
    CreateThread,
    DeleteComment,
    EditComment,
    FilePair,
    LineRange,
    ProfileAuthor,
    ReviewError,
    ReviewErrorCode,
    ReviewOriginView,
    ReviewTarget,
    TextTarget,
    ThreadDiscussionView,
    ThreadPlacementView,
    ThreadSummaryView,
)
from dirdiff.review.external_agent import (
    DeleteThread,
    ReplyToThread,
    ResolveThread,
    ReviewBatchAction,
    ReviewBatchResult,
    apply_review_batch,
)
from dirdiff.review.placement import derive_room_threads
from dirdiff.review.thread import (
    Thread,
    create_thread,
    get_thread,
    thread_objects,
)

__all__ = [
    "AddComment",
    "ChangeThreadState",
    "CreateThread",
    "DeleteComment",
    "DeleteThread",
    "EditComment",
    "FilePair",
    "LineRange",
    "ProfileAuthor",
    "ReplyToThread",
    "ResolveThread",
    "ReviewBatchAction",
    "ReviewBatchResult",
    "ReviewError",
    "ReviewErrorCode",
    "ReviewOriginView",
    "ReviewTarget",
    "TextTarget",
    "Thread",
    "ThreadDiscussionView",
    "ThreadPlacementView",
    "ThreadSummaryView",
    "apply_review_batch",
    "create_thread",
    "derive_room_threads",
    "get_thread",
    "thread_objects",
]
