"""Expose persistent review discussions and agent batches.

The facade publishes the review contracts and operations used outside this
package. Shared contracts, bound Thread behavior, Snapshot placement, and
agent batches live in separate sibling modules. SQL, Snapshot
publication, and HTTP serialization remain outside this package.
"""

from dirdiff.review.base import (
    AddComment,
    BatchComment,
    BatchCreate,
    BatchDeleteComment,
    BatchDeleteThread,
    BatchEditComment,
    BatchReopen,
    BatchResolve,
    ChangeThreadState,
    CreateReviewThread,
    CreateThread,
    DeleteComment,
    EditComment,
    FilePair,
    LineRange,
    ProfileAuthor,
    ReviewBatchAction,
    ReviewBatchResult,
    ReviewCommentView,
    ReviewError,
    ReviewErrorCode,
    ReviewRole,
    TextTarget,
    ThreadDiscussionView,
    ThreadSummaryView,
)
from dirdiff.review.thread import (
    Thread,
    apply_review_batch,
    create_thread,
    derive_room_threads,
    get_thread,
    thread_objects,
)

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
    "ReviewRole",
    "TextTarget",
    "Thread",
    "ThreadDiscussionView",
    "ThreadSummaryView",
    "apply_review_batch",
    "create_thread",
    "derive_room_threads",
    "get_thread",
    "thread_objects",
]
