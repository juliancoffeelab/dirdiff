"""Apply the review instruments available only to external agents.

## Public interface

The command and result types describe one ordered external-agent review batch.
`apply_review_batch` validates the complete batch against one Snapshot and
persists it atomically. `dirdiff.review` re-exports these names for the Room
facade and HTTP adapter.

## Purpose and boundaries

This module contains role-directed agent replies, reviewer resolution and
deletion, and the multi-Thread transaction that applies them. It shares ordinary
Thread creation and action reduction with the rest of the review package. It
does not define browser review writes, agent HTTP models, pagination, Snapshot
recapture, or Profile registration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Optional
from uuid import UUID

from dirdiff.db import (
    ReviewActionRecord,
    ReviewThreadRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
)
from dirdiff.engines import DirdiffError
from dirdiff.review.base import (
    AddComment,
    ChangeThreadState,
    CreateThread,
    FilePair,
    ReviewError,
    action_timestamp,
    room_write_lock,
    validate_comment_body,
)
from dirdiff.review.placement import (
    ReviewReadCache,
    plan_thread_creation,
)
from dirdiff.review.thread import fold_actions

__all__ = [
    "DeleteThread",
    "ReplyToThread",
    "ResolveThread",
    "ReviewBatchAction",
    "ReviewBatchResult",
    "apply_review_batch",
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


def apply_review_batch(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    batch: tuple[ReviewBatchAction, ...],
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[ReviewBatchResult, ...]:
    """Validate and apply one ordered multi-Thread batch atomically.

    The agent boundary generates fresh identifiers before calling. Every
    semantic check runs while holding the same Room lock used by browser
    writes. Only after all items are valid are their placement and action rows
    inserted in one database transaction.

    # Parameters

    - `database`: Persistence used for set-based validation and one final write.
    - `identity`: Room containing every addressed Thread and Snapshot.
    - `snapshot_id`: Exact code universe against which all actions apply.
    - `batch`: Ordered non-empty instruments; later items observe earlier items
      in memory before anything is committed.
    - `lock_path`: Cross-process Room write lock shared with publication.
    - `thread_lock`: In-process Room write lock held for the complete batch.

    # Usage

    Call through `Room.apply_review_batch` with the agent's full ordered batch.
    Every command must use the same author Profile. Results preserve input order
    and contain the authoritative state after each accepted command.

    # Returns

    - `Members`: One authoritative result for every accepted input action,
      containing its instrument, identities, resulting state, and attention.
    - `Order`: Results match input order after later actions have observed
      earlier batch state; an invalid batch returns nothing and writes nothing.

    # Failures

    - Raises `ReviewError` when the batch is empty, mixes authors, repeats an
      operation id, addresses invalid code, or contains an operation invalid for
      the current in-memory Thread state. Nothing is written on failure.
    - Raises `AssertionError` when persistence or command shapes contradict the
      review invariants.
    """
    if batch == ():
        raise ReviewError("invalid_target", "Review batch cannot be empty.")
    with room_write_lock(thread_lock, lock_path):
        # One set-based File read replaces hydrating the whole Snapshot:
        # only the batch's distinct creation targets are loaded, in a single
        # query and transaction that also answers Snapshot visibility.
        # Absent pairs stay unmapped so planning rejects exactly the invalid
        # creation.
        creation_pairs = tuple(
            dict.fromkeys(
                action.target.file
                for action in batch
                if isinstance(action, CreateThread)
            )
        )
        snapshot_exists, found_by_pair = database.snapshot_files_by_pairs(
            identity,
            snapshot_id=snapshot_id.hex,
            pairs=tuple(
                (pair.left_path, pair.right_path) for pair in creation_pairs
            ),
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        selected_files_by_pair: dict[FilePair, SnapshotFileRecord] = {
            pair: found_by_pair[(pair.left_path, pair.right_path)]
            for pair in creation_pairs
            if (pair.left_path, pair.right_path) in found_by_pair
        }
        cache = ReviewReadCache()
        placements: list[ReviewThreadRecord] = []
        records: list[ReviewActionRecord] = []
        results: list[ReviewBatchResult] = []

        # Set-based reads replace per-action queries: every author in one
        # Profile read, every addressed existing Thread history in one
        # placement/action read. The ordered in-memory reduction below still lets
        # later actions observe earlier ones from the same batch, including a
        # Thread the batch itself creates.
        author_ids = [
            action.author.profile_id
            if isinstance(action, CreateThread)
            else action.command.author.profile_id
            for action in batch
        ]
        known_profiles = {
            profile.id: profile
            for profile in database.review_profiles(
                tuple(dict.fromkeys(author_ids))
            )
        }
        for author_id in author_ids:
            if author_id not in known_profiles:
                raise ReviewError(
                    "profile_not_found", f"Unknown Profile: {author_id}"
                )
        addressed = tuple(
            dict.fromkeys(
                action.thread_id.hex
                for action in batch
                if not isinstance(action, CreateThread)
            )
        )
        histories, history_profiles = database.review_actions_many(
            snapshot_id.hex, addressed
        )
        fold_profiles = {
            profile.id: profile for profile in history_profiles
        } | known_profiles
        simulated: dict[str, list[ReviewActionRecord]] = {}

        for action in batch:
            if isinstance(action, CreateThread):
                rows, first_action = plan_thread_creation(
                    command=action,
                    created_at=action_timestamp(),
                    snapshot_id=snapshot_id.hex,
                    target_file=selected_files_by_pair.get(action.target.file),
                    cache=cache,
                )
                placements.extend(rows)
                records.append(first_action)
                # Seed the materialized state so later batch actions can address the
                # Thread this batch just created.
                simulated[action.thread_id.hex] = [first_action]
                results.append(
                    ReviewBatchResult(
                        "create-finding",
                        action.thread_id,
                        action.comment_id,
                        "open",
                        "author",
                    )
                )
                continue

            thread_key = action.thread_id.hex
            thread_actions = simulated.get(thread_key)
            if thread_actions is None:
                persisted_actions = histories.get(thread_key)
                if persisted_actions is None:
                    raise ReviewError(
                        "thread_not_found",
                        f"Unknown Thread: {thread_key}",
                    )
                thread_actions = list(persisted_actions)
                simulated[thread_key] = thread_actions

            command = action.command
            profile_id = command.author.profile_id
            state, attention, _comments = fold_actions(
                tuple(thread_actions), fold_profiles
            )
            if state == "deleted":
                raise ReviewError("state_conflict", "Thread is deleted.")
            if isinstance(action, ReplyToThread):
                reply = action.command
                validate_comment_body(reply.body)
                allowed_attention = {
                    "author-response": {"author", "both"},
                    "reviewer-return": {"reviewer", "both"},
                    "inert-comment": {"author", "reviewer", "both", "none"},
                }[action.instrument]
                if action.instrument != "inert-comment" and (
                    state != "open" or attention not in allowed_attention
                ):
                    raise ReviewError(
                        "state_conflict",
                        f"{action.instrument} is not valid for this Thread outcome.",
                    )
                next_attention: Literal["author", "reviewer", "both", "none"]
                if action.instrument == "author-response":
                    next_attention = "reviewer"
                elif action.instrument == "reviewer-return":
                    next_attention = "author"
                else:
                    next_attention = attention
                record = ReviewActionRecord(
                    operation_id=reply.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="comment-created",
                    profile_id=profile_id,
                    comment_id=reply.comment_id.hex,
                    expected_revision=None,
                    body=reply.body,
                    created_at=action_timestamp(),
                    status_after=state,
                    attention_after=next_attention,
                )
                result = ReviewBatchResult(
                    action.instrument,
                    action.thread_id,
                    reply.comment_id,
                    state,
                    next_attention,
                )
            elif isinstance(action, ResolveThread):
                resolve = action.command
                if state != "open" or attention not in {"reviewer", "both"}:
                    raise ReviewError(
                        "state_conflict",
                        "reviewer-resolve requires an open reviewer-attention Thread.",
                    )
                validate_comment_body(resolve.body)
                record = ReviewActionRecord(
                    operation_id=resolve.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="thread-resolved",
                    profile_id=profile_id,
                    comment_id=resolve.comment_id.hex,
                    expected_revision=None,
                    body=resolve.body,
                    created_at=action_timestamp(),
                    status_after="resolved",
                    attention_after="none",
                )
                result = ReviewBatchResult(
                    "reviewer-resolve",
                    action.thread_id,
                    resolve.comment_id,
                    "resolved",
                    "none",
                )
            else:
                assert isinstance(action, DeleteThread)
                deletion = action.command
                record = ReviewActionRecord(
                    operation_id=deletion.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="thread-deleted",
                    profile_id=profile_id,
                    comment_id=None,
                    expected_revision=None,
                    body=None,
                    created_at=action_timestamp(),
                    status_after="deleted",
                    attention_after="none",
                )
                result = ReviewBatchResult(
                    "reviewer-delete",
                    action.thread_id,
                    None,
                    "deleted",
                    "none",
                )
            thread_actions.append(record)
            records.append(record)
            results.append(result)

        database.apply_review_batch(tuple(placements), tuple(records))
        return tuple(results)
