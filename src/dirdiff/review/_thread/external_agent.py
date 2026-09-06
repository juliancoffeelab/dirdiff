"""Apply the review instruments available only to external agents.

## Public interface

The command and result types describe one ordered external-agent review batch.
`dirdiff.review.thread` imports them while coordinating the batch against
placement and ordinary Thread behavior.

## Purpose and boundaries

This module defines role-directed agent replies, reviewer resolution, deletion,
and their result contract. It does not load placements, reduce Thread history,
perform persistence writes, define HTTP models, or recapture Snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
from uuid import UUID

from dirdiff.review.base import (
    AddComment,
    ChangeThreadState,
    CreateThread,
)

__all__ = [
    "DeleteThread",
    "ReplyToThread",
    "ResolveThread",
    "ReviewBatchAction",
    "ReviewBatchResult",
]


@dataclass(frozen=True)
class ReplyToThread:
    """Apply one role-directed Comment instrument in an atomic batch.

    Agent batch translation creates this value for author responses, reviewer
    returns, and inert Comments. It joins an existing discussion, one new
    attributed Comment, and the attention transition that the selected
    instrument permits.

    It cannot resolve or delete a Thread and does not weaken current-attention
    requirements.
    """

    thread_id: UUID
    """Stable identity of the existing discussion receiving the reply.

    The Thread must have a placement in the batch Snapshot. A prior action in
    the same ordered batch may have created it; otherwise persistence must.
    """

    command: AddComment
    """New Comment data applied atomically with the attention transition.

    Its author is the acting Profile for the instrument. If validation fails,
    neither this Comment nor any action in the surrounding batch is persisted.
    """

    instrument: Literal["author-response", "reviewer-return", "inert-comment"]
    """Role-directed rule governing validity and post-Comment attention.

    Author response requires author attention and hands attention to reviewer;
    reviewer return does the inverse. An inert Comment accepts any live outcome
    and leaves attention unchanged. No instrument applies to a deleted Thread.
    """


@dataclass(frozen=True)
class ResolveThread:
    """Resolve one existing Thread with a required reviewer Comment.

    Agent batch translation creates this value for `reviewer-resolve`. The
    selected Thread must be open and awaiting the reviewer; resolution and the
    required Comment are persisted atomically.

    It cannot resolve without an explanation or act on author attention.
    """

    thread_id: UUID
    """Stable identity of the discussion to close as resolved.

    At its point in the ordered batch, the Thread must be open and require
    reviewer or both-role attention. Other outcomes cause the whole batch to
    fail without writes.
    """

    command: AddComment
    """Nonblank explanation and acting Profile for the resolution action.

    The supplied Comment becomes a new ordered Comment on the Thread in the
    same transaction that changes status to resolved and attention to none.
    """


@dataclass(frozen=True)
class DeleteThread:
    """Delete one existing Thread through the exceptional reviewer instrument.

    Agent batch translation creates this value only for the exceptional
    `reviewer-delete` instrument. The state command identifies the reviewer but
    carries no Comment.

    Deletion is terminal and remains in history. This command is not ordinary
    resolution or Comment deletion.
    """

    thread_id: UUID
    """Stable identity of the live discussion to mark deleted.

    It must be placed in the batch Snapshot and not already deleted when this
    ordered action runs. Deletion retains all preceding history.
    """

    command: ChangeThreadState
    """Acting Profile and operation identity for terminal deletion.

    Both optional Comment fields must be absent because reviewer deletion
    writes no explanation Comment. The batch records the actor in its action.
    """


ReviewBatchAction = CreateThread | ReplyToThread | ResolveThread | DeleteThread
"""One role-specific write accepted by an agent review batch.

- `CreateThread` starts a discussion.
- `ReplyToThread` applies a role-directed Comment instrument.
- `ResolveThread` closes a reviewer-attention Thread with a Comment.
- `DeleteThread` applies exceptional terminal deletion.

`Room.apply_review_batch` applies values in their supplied order inside one
transaction. This union is not used by browser single-Thread operations.
"""


@dataclass(frozen=True)
class ReviewBatchResult:
    """Return the authoritative outcome of one applied batch action.

    `Room.apply_review_batch` returns one result per input action in the same
    order. `kind` echoes the instrument; state and attention are authoritative
    after it.

    Every result except `reviewer-delete` carries the created Comment id. The
    value does not contain the complete discussion or placement.
    """

    kind: Literal[
        "create-finding",
        "author-response",
        "reviewer-return",
        "reviewer-resolve",
        "inert-comment",
        "reviewer-delete",
    ]
    """Agent instrument that produced this authoritative outcome.

    It preserves input order and lets the caller interpret whether a Comment
    was created without re-inspecting the original action object.
    """

    thread_id: UUID
    """Stable discussion identity created or changed by the matching action.

    The value corresponds positionally to the input batch and is valid only
    after the complete batch transaction succeeds.
    """

    comment_id: Optional[UUID]
    """Global identity of the Comment created with the action.

    It is present for findings, replies, and reviewer resolution. Only terminal
    reviewer deletion returns `None`, because that instrument writes no Comment.
    """

    status: Literal["open", "resolved", "deleted"]
    """Persisted Thread lifecycle outcome immediately after this action.

    Later actions in the same batch may change the same Thread again, so callers
    treat this as the positional result rather than the batch's final aggregate.
    """

    attention: Literal["author", "reviewer", "both", "none"]
    """Persisted role-attention outcome immediately after this action.

    The value reflects the instrument's transition after current state was
    validated; callers must not recompute it from the action kind alone.
    """

    def __post_init__(self) -> None:
        """Prove the result's Comment presence matches its instrument.

        Construction fails if an action that creates a Comment omits its id or if
        reviewer deletion claims one, preventing an internally ambiguous result.

        # Failures

        - Raises `AssertionError` when Comment presence contradicts `kind`.
        """
        assert (self.comment_id is not None) == (self.kind != "reviewer-delete")
