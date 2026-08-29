"""Provide bound persistent review Threads and ordinary review writes.

## Public interface

`Thread` is the bound discussion handle re-exported by `dirdiff.review`.
`thread_objects`, `get_thread`, and `create_thread` are the package
operations used by `Room` to construct those handles.

## Purpose and boundaries

This module reduces append-only actions into discussion views and applies
single-Thread Comment and lifecycle operations under the Room write lock. It
loads only the persisted rows and captured Files needed by a bound Thread. It
does not derive placements for newly captured Snapshots, implement agent batch
instruments, publish Snapshot contents, or serialize HTTP entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal, Optional
from uuid import UUID

from dirdiff.db import (
    ReviewActionRecord,
    ReviewThreadRecord,
    ReviewThreadsRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    UserProfileRecord,
)
from dirdiff.engines import DirdiffError
from dirdiff.review.base import (
    AddComment,
    ChangeThreadState,
    CreateThread,
    DeleteComment,
    EditComment,
    ProfileAuthor,
    ReviewCommentView,
    ReviewError,
    ReviewProfileView,
    ThreadDiscussionView,
    ThreadPlacementView,
    ThreadSummaryView,
    ThreadUpdateView,
    action_timestamp,
    room_write_lock,
    validate_author,
    validate_comment_body,
)
from dirdiff.review.placement import (
    BayStartPlacement,
    FileMissingPlacement,
    FileStartPlacement,
    FileUnreadablePlacement,
    Placement,
    RangePlacement,
    ReviewReadCache,
    build_original_excerpt,
    file_pair,
    origin_target_view,
    placement_from_record,
    plan_thread_creation,
)

__all__ = [
    "Thread",
    "create_thread",
    "fold_actions",
    "get_thread",
    "thread_objects",
]


@dataclass
class _CommentState:
    """Reduce immutable Comment actions into one current Comment view.

    Discussion materialization creates this private mutable value on Comment creation,
    then applies edits or deletion in sequence before exporting
    `ReviewCommentView`.

    It exists only during one read and is not persisted or shared between
    Threads.
    """

    comment_id: str
    """Stable global identity of the Comment being materialized.

    It keys this mutable state while every edit and deletion retains the same
    sequence entry.
    """

    sequence: int
    """Zero-based Comment creation position assigned during reduction.

    Later mutations do not reorder this state, so exported views remain in
    durable discussion order.
    """

    profile_id: int
    """Durable identity of the Profile that originally authored the Comment.

    Action reduction uses it for edit authorization invariants and resolves the current
    display name only when exporting the public view.
    """

    revision: int
    """Current zero-based Comment version accumulated by action reduction.

    It starts at creation and increments once for every accepted edit or
    tombstone action that targets this Comment.
    """

    body: Optional[str]
    """Latest Comment text, or `None` after a tombstone action.

    Before deletion every assigned value is nonblank because persisted action
    validation treats a blank body as corruption.
    """

    deleted: bool
    """Whether the action sequence has terminally tombstoned this Comment.

    Once true, later edit or deletion actions violate persisted history rather
    than reviving or deleting the Comment again.
    """

    created_at: str
    """Immutable serialized UTC time from the Comment creation action.

    Export converts it to `datetime`; later actions never replace this value.
    """

    updated_at: str
    """Serialized UTC time of the latest applied edit or deletion.

    It begins equal to `created_at` and changes only when this Comment is the
    target of a mutating action.
    """


def fold_actions(
    actions: tuple[ReviewActionRecord, ...],
    profiles: dict[int, UserProfileRecord],
) -> tuple[
    Literal["open", "resolved", "deleted"],
    Literal["author", "reviewer", "both", "none"],
    list[ReviewCommentView],
]:
    """Reduce ordered authored actions into current discussion state.

    # Parameters

    - `actions`: Complete contiguous sequence beginning with Thread creation.
    - `profiles`: Current Profile records for every action author.

    # Usage

    Pass the complete persisted history and every referenced current Profile.
    Use the returned state, attention, revision, and Comments as the
    authoritative current discussion; do not merge them with cached values.

    # Returns

    - `First`: The discussion status after the last action.
    - `Second`: The attention value after the last action.
    - `Third`: Comments in creation order, including deleted Comments as
      tombstones with their final revision and timestamps.

    # Failures

    - Raises `AssertionError` when creation, transition, revision, body, or
      attribution facts form an impossible persisted history. Reduction never
      repairs or skips incomplete actions.
    """

    assert actions != () and actions[0].kind == "thread-created"
    assert [action.sequence for action in actions] == list(
        range(len(actions))
    ), "review action sequence must be contiguous"
    state = actions[-1].status_after
    attention = actions[-1].attention_after
    comments: dict[str, _CommentState] = {}
    order: list[str] = []
    for action in actions:
        profile_id = action.profile_id
        assert profile_id in profiles, "review action has no Profile"
        if action.comment_id is not None and action.kind in {
            "thread-created",
            "comment-created",
            "thread-resolved",
            "thread-reopened",
        }:
            assert action.comment_id is not None and action.body is not None
            assert action.expected_revision is None
            assert action.comment_id not in comments
            assert action.body.strip() != "", "persisted Comment body is blank"
            comments[action.comment_id] = _CommentState(
                action.comment_id,
                len(order),
                profile_id,
                0,
                action.body,
                False,
                action.created_at,
                action.created_at,
            )
            order.append(action.comment_id)
        elif action.kind == "comment-edited":
            assert action.comment_id is not None and action.body is not None
            assert action.body.strip() != "", "persisted Comment body is blank"
            comment = comments[action.comment_id]
            assert not comment.deleted, "deleted Comment was edited"
            assert profile_id == comment.profile_id, (
                "Comment was edited by another author"
            )
            assert action.expected_revision == comment.revision, (
                "Comment edit has a stale revision"
            )
            comment.revision += 1
            comment.body = action.body
            comment.updated_at = action.created_at
        elif action.kind == "comment-deleted":
            assert action.comment_id is not None
            comment = comments[action.comment_id]
            assert not comment.deleted, "Comment was deleted twice"
            assert action.expected_revision == comment.revision, (
                "Comment deletion has a stale revision"
            )
            comment.revision += 1
            comment.body = None
            comment.deleted = True
            comment.updated_at = action.created_at
        else:
            assert action.kind in {
                "thread-resolved",
                "thread-reopened",
                "thread-deleted",
            }
    views: list[ReviewCommentView] = []
    for comment_id in order:
        comment = comments[comment_id]
        profile = profiles[comment.profile_id]
        author = ReviewProfileView(
            profile_id=profile.id,
            display_name=profile.username,
        )
        views.append(
            {
                "comment_id": comment.comment_id,
                "sequence": comment.sequence,
                "author": author,
                "revision": comment.revision,
                "body": comment.body,
                "deleted": comment.deleted,
                "created_at": datetime.fromisoformat(comment.created_at),
                "updated_at": datetime.fromisoformat(comment.updated_at),
            }
        )
    return state, attention, views


def append_review_action(
    *,
    database: RoomStore,
    snapshot_id: UUID,
    thread_id: UUID,
    operation_id: UUID,
    author: ProfileAuthor,
    kind: Literal[
        "comment-created",
        "comment-edited",
        "comment-deleted",
        "thread-resolved",
        "thread-reopened",
        "thread-deleted",
    ],
    comment_id: Optional[UUID],
    body: Optional[str],
    comment_attention: Literal["inert", "alert"],
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[
    tuple[ReviewActionRecord, ...],
    dict[int, UserProfileRecord],
]:
    """Validate and append one action without loading captured Snapshot Files.

    Returns the appended authoritative action sequence and current Profiles;
    the caller that reports an update view folds them itself, so this write
    builds nothing its other caller discards.

    # Parameters

    - `database`: Persistence used for authoritative Profile and action reads
      and the final append.
    - `snapshot_id`: Exact placement through which the Thread is addressed.
    - `thread_id`: Existing live discussion receiving the action.
    - `operation_id`: Fresh backend-generated identity for this accepted write.
    - `author`: Existing Profile attributed to the action.
    - `kind`: Comment or lifecycle transition to validate against current state.
    - `comment_id`: New or existing Comment identity for kinds that create,
      edit, or delete a Comment, otherwise `None`.
    - `body`: Nonblank Comment text for creating or editing, optional text for
      lifecycle actions, or `None` when that kind carries no body.
    - `comment_attention`: Whether a new Comment preserves attention or alerts
      both roles.
    - `lock_path`: Cross-process Room write lock shared with publication.
    - `thread_lock`: In-process Room write lock held for the same lifetime.

    # Usage

    Call through a bound `Thread` while holding no external database session.
    Supply a fresh operation id and the exact command fields for one operation;
    this function acquires the Room locks and reloads current state before the
    append.

    # Returns

    - `First`: The complete prior action history with the accepted action
      appended at the next contiguous sequence number.
    - `Second`: Current Profiles for every author in that returned history,
      keyed by Profile id.

    # Failures

    - Raises `ReviewError` when the author or Thread is missing, the Thread is
      deleted, the expected lifecycle transition is invalid, a Comment is
      missing or already deleted, or the operation violates authorship rules.
    - Raises `AssertionError` for an impossible internal operation shape or
      persisted history.
    """
    with room_write_lock(thread_lock, lock_path):
        profile_id = author.profile_id
        profile = validate_author(database, author)
        persisted = database.review_actions(snapshot_id.hex, thread_id.hex)
        if persisted is None:
            raise ReviewError(
                "thread_not_found", f"Unknown Thread: {thread_id.hex}"
            )
        actions, persisted_profiles = persisted
        profiles = {
            persisted_profile.id: persisted_profile
            for persisted_profile in persisted_profiles
        }
        state, attention, comments = fold_actions(actions, profiles)
        if state == "deleted":
            raise ReviewError("state_conflict", "Thread is deleted.")
        comment_by_id = {comment["comment_id"]: comment for comment in comments}

        if kind == "comment-created":
            assert comment_id is not None and body is not None
            validate_comment_body(body)
            accepted_revision = None
        elif kind in {"comment-edited", "comment-deleted"}:
            assert comment_id is not None
            comment = comment_by_id.get(comment_id.hex)
            if comment is None:
                raise ReviewError(
                    "comment_not_found", f"Unknown Comment: {comment_id.hex}"
                )
            if (
                kind == "comment-edited"
                and comment["author"]["profile_id"] != profile_id
            ):
                raise ReviewError(
                    "forbidden", "Only the Comment author may edit it."
                )
            if comment["deleted"]:
                raise ReviewError("state_conflict", "Comment is deleted.")
            accepted_revision = comment["revision"]
            if kind == "comment-edited":
                assert body is not None
                validate_comment_body(body)
        else:
            accepted_revision = None
            if kind == "thread-resolved" and state != "open":
                raise ReviewError(
                    "state_conflict", "Only an open Thread may be resolved."
                )
            if kind == "thread-reopened" and state != "resolved":
                raise ReviewError(
                    "state_conflict", "Only a resolved Thread may be reopened."
                )

        record = ReviewActionRecord(
            operation_id=operation_id.hex,
            thread_id=thread_id.hex,
            snapshot_id=snapshot_id.hex,
            sequence=len(actions),
            kind=kind,
            profile_id=profile_id,
            comment_id=comment_id.hex if comment_id is not None else None,
            expected_revision=accepted_revision,
            body=body,
            created_at=action_timestamp(),
            status_after=(
                "resolved"
                if kind == "thread-resolved"
                else "open"
                if kind == "thread-reopened"
                else "deleted"
                if kind == "thread-deleted"
                else state
            ),
            attention_after=(
                "none"
                if kind in {"thread-resolved", "thread-deleted"}
                else "both"
                if kind == "thread-reopened"
                or (kind == "comment-created" and comment_attention == "alert")
                else attention
            ),
        )
        database.append_review_action(record)
        profiles[profile.id] = profile
        return (*actions, record), profiles


@dataclass
class _ThreadFiles:
    """Hold the Snapshot File records one bound Thread locates code against.

    `origin_file` is the origin Snapshot File behind the discussion;
    `selected_file` is the selected-Snapshot File the placement locates, or
    `None` for a file-missing placement whose absence the loading read has
    verified. The cache bounds repeated captured-text reads for excerpts.
    """

    origin_file: SnapshotFileRecord
    """Authenticated captured File referenced by the immutable origin row.

    It supplies the public path pair and, for full reads, original bay content.
    The record may belong to an earlier Snapshot than the bound handle.
    """

    selected_file: Optional[SnapshotFileRecord]
    """Authenticated File referenced by the selected placement, when located.

    `None` is valid for explicit file-missing and file-unreadable placements;
    located variants require a record whose pair equals `origin_file`.
    """

    cache: ReviewReadCache
    """Composition cache shared by handles hydrated in the same read operation.

    It avoids decoding a File twice while building views and is discarded with
    the bound handles; it is never authoritative state.
    """


class Thread:
    """Operate on one live discussion through one exact Snapshot.

    The bound keys are immutable. The object is a lightweight handle: reads
    that interpret placement load their Snapshot Files on first use, while
    writes never load Files at all. Every write reloads authoritative actions
    under the Room publication lock, validates the requested action, appends
    it, and returns the bounded authoritative update view the HTTP boundary
    reports.
    """

    def __init__(
        self,
        *,
        database: RoomStore,
        identity: RoomIdentity,
        snapshot_id: UUID,
        thread_id: UUID,
        lock_path: Path,
        thread_lock: Lock,
        placement: ReviewThreadRecord,
        origin: ReviewThreadRecord,
        actions: tuple[ReviewActionRecord, ...],
        profiles: dict[int, UserProfileRecord],
        files: Optional[_ThreadFiles],
    ) -> None:
        """Bind one Thread to its placement and optionally preloaded Files.

        `files` carries the referenced Snapshot Files when the caller already
        loaded them in bulk; `None` defers that single focused read to the
        first placement-interpreting read on this handle.

        # Parameters

        - `database`: Store used for fresh actions and deferred File hydration.
        - `identity`: Room boundary that limits every deferred read.
        - `snapshot_id`: Exact selected placement bound for this handle's life.
        - `thread_id`: Stable discussion identity bound for this handle's life.
        - `lock_path`: Cross-process Room write lock used by methods that append.
        - `thread_lock`: In-process lock paired with `lock_path`.
        - `placement`: Selected-Snapshot flat row converted once on construction.
        - `origin`: Unique immutable origin row for the discussion.
        - `actions`: Complete ordered action sequence at construction time.
        - `profiles`: Current records for every author in `actions`.
        - `files`: Already validated referenced Files and shared cache, or
          `None` to load them only when a read interprets placement.
        """
        self.snapshot_id = snapshot_id
        self.thread_id = thread_id
        self._database = database
        self._identity = identity
        self._lock_path = lock_path
        self._thread_lock = thread_lock
        # The store returns the flat row shape; every read on this handle wants
        # the proven one, so both are converted once here rather than at each
        # interpreting read.
        self._placement = placement_from_record(placement)
        origin_placement = placement_from_record(origin)
        assert isinstance(
            origin_placement, (RangePlacement, FileStartPlacement)
        ), "a discussion origin is a stored range or File-start row"
        self._origin: RangePlacement | FileStartPlacement = origin_placement
        self._action_records = actions
        self._profiles = profiles
        # Mutated once by `_located_files` when constructed deferred; every
        # later locating read reuses the same loaded records and cache.
        self._files = files

    def _records(
        self,
    ) -> tuple[Placement, RangePlacement | FileStartPlacement]:
        """Return the proven selected placement and unique immutable origin.

        Both values were validated from flat persistence rows during handle
        construction, so callers need not repeat discriminator narrowing.

        # Returns

        - `First`: The Thread's placement in the bound Snapshot.
        - `Second`: Its immutable origin placement, narrowed to the two valid
          origin shapes.
        """
        return self._placement, self._origin

    def _actions(self) -> tuple[ReviewActionRecord, ...]:
        """Return the handle's current complete contiguous action sequence.

        Construction and accepted writes keep this tuple authoritative for the
        handle. A missing creation action is persisted corruption and fails.

        # Returns

        - `Members`: The handle's complete action records, beginning with Thread
          creation and including every accepted later write.
        - `Order`: Records form contiguous sequence order; an empty history is
          persisted corruption and raises instead of returning.
        """
        assert self._action_records != (), (
            "persisted Thread has no first Comment"
        )
        return self._action_records

    def _located_files(self) -> _ThreadFiles:
        """Load and retain the placement's Snapshot Files on first use.

        Handles constructed without preloaded Files pay this one focused read
        the first time a read interprets placement; the same read doubles as
        the file-missing absence proof. Writes never call this.
        """
        if self._files is None:
            placement, origin = self._records()
            origin_ref = (origin.snapshot_id, origin.snapshot_file_id)
            selected_ids: tuple[str, ...] = ()
            absent_refs: tuple[tuple[str, str], ...] = ()
            # An unreadable File is present and deliberately unreferenced, so
            # it asks for neither: proving its absence would fail, and loading
            # it would offer bytes no read may use.
            if isinstance(placement, FileMissingPlacement):
                absent_refs = (origin_ref,)
            elif not isinstance(placement, FileUnreadablePlacement):
                selected_ids = (placement.snapshot_file_id,)
            origin_files, selected_files, conflicts = (
                self._database.review_thread_files(
                    self.snapshot_id.hex,
                    (origin_ref,),
                    selected_ids,
                    absent_refs,
                )
            )
            assert conflicts == (), (
                "file_missing placement has an exact Snapshot File"
            )
            selected_file: Optional[SnapshotFileRecord] = None
            if selected_ids != ():
                assert not isinstance(
                    placement,
                    FileMissingPlacement | FileUnreadablePlacement,
                )
                selected_file = selected_files.get(placement.snapshot_file_id)
                assert selected_file is not None, (
                    "located placement has no exact Snapshot File"
                )
            self._files = _ThreadFiles(
                origin_file=origin_files[origin_ref],
                selected_file=selected_file,
                cache=ReviewReadCache(),
            )
        return self._files

    def _placement_view(self) -> ThreadPlacementView:
        """Fold placement facts into the public placement.

        The returned shape names one derivation outcome and states only what
        the origin does not: the File pair and side are the origin's in every
        variant, and the bay is the origin's in all but a `bay-lost` landing,
        which names the bay derivation chose instead. `region-kept` and
        `whole-file` report nothing wrong; the six others are the complete
        public outdated vocabulary, one name per state.

        The two unlocated variants state nothing but their kind. For the
        absent File the File-loading read has already verified no
        selected-Snapshot File carries the origin pair, so absence there is an
        invariant, not a substitute.
        """
        placement, origin = self._records()
        files = self._located_files()
        if isinstance(placement, FileUnreadablePlacement):
            return {"kind": "file-unreadable"}
        if isinstance(placement, FileMissingPlacement):
            assert files.selected_file is None, (
                "file_missing placement has an exact Snapshot File"
            )
            return {"kind": "file-absent"}
        target_file = files.selected_file
        assert target_file is not None, (
            "located placement has no exact Snapshot File"
        )
        assert target_file.id == placement.snapshot_file_id, (
            "placement references the wrong Snapshot File"
        )
        # The File pair travels once, on the origin. A placement that named
        # another File would be read under the origin's paths with nothing
        # left to contradict it, so the equality is proven here instead.
        assert file_pair(target_file) == file_pair(files.origin_file), (
            "placement references the wrong Snapshot File pair"
        )
        assert placement.side == origin.side, (
            "placement selects the side the origin did not"
        )
        match placement:
            case RangePlacement():
                # A matched region stays inside the bay it was written in, so
                # the bay the wire omits here is exactly the origin's.
                assert isinstance(origin, RangePlacement), (
                    "a File-level origin never matches a region"
                )
                assert placement.bay_key == origin.bay_key, (
                    "a matched region left its origin's bay"
                )
                return {
                    "kind": (
                        "region-changed"
                        if placement.outdated_reason == "region_changed"
                        else "region-kept"
                    ),
                    "range": {
                        "start_line": placement.start_line,
                        "end_line": placement.end_line,
                    },
                }
            case BayStartPlacement():
                if placement.outdated_reason == "region_not_found":
                    # Only the region inside the bay was lost, so this landing
                    # also sits in the origin's own bay.
                    assert isinstance(origin, RangePlacement), (
                        "a File-level origin never loses a region"
                    )
                    assert placement.bay_key == origin.bay_key, (
                        "a region-lost landing left its origin's bay"
                    )
                    return {"kind": "region-lost"}
                return {
                    "kind": "bay-lost",
                    "bay": {"bay_key": placement.bay_key},
                }
            case FileStartPlacement():
                if placement.outdated_reason is None:
                    assert isinstance(origin, FileStartPlacement), (
                        "a text origin never rests on its File unchanged"
                    )
                    return {"kind": "whole-file"}
                return {"kind": "side-lost"}

    def discussion(self) -> ThreadDiscussionView:
        """Fold the complete discussion with its bounded original excerpt.

        The excerpt travels inside the origin it is cut from, so a File-level
        origin carries none. Index-style callers read the placement for where
        the Thread landed, and explicitly render that File when it reports
        `region-changed`.

        # Usage

        Use this for a complete Thread page or browser response. It may read
        captured File contents to build the origin excerpt; use `summary` when
        discovery facts are enough.

        # Failures

        - Raises `AssertionError` when persisted placement, origin, File, or
          action data cannot form one valid discussion.
        """
        _placement, origin = self._records()
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        files = self._located_files()
        origin_target = origin_target_view(origin, files.origin_file)
        if isinstance(origin, RangePlacement):
            # Only a discussion read builds an excerpt, and it belongs to the
            # origin it is cut from. The summary path reads no captured text,
            # so the key is attached here rather than by the shared builder.
            assert origin_target["kind"] == "text"
            origin_target["excerpt"] = build_original_excerpt(
                origin, files.origin_file, files.cache
            )
        return ThreadDiscussionView(
            thread_id=self.thread_id.hex,
            snapshot_id=self.snapshot_id.hex,
            created_at=datetime.fromisoformat(actions[0].created_at),
            state=state,
            attention=attention,
            discussion_revision=len(actions) - 1,
            origin_target=origin_target,
            placement=self._placement_view(),
            comments=comments,
        )

    def summary(self) -> ThreadSummaryView:
        """Materialize discovery facts without reading any captured text.

        The same action reduction and placement checks as `discussion`, minus the
        original-excerpt construction and the complete Comment list. The
        origin still travels: it is where the File pair, bay, and side a
        caller needs to name captured code are stated.

        # Usage

        Use this for Thread indexes and agent discovery pages. Call
        `discussion` only after the caller selects a Thread and needs Comments
        or original source context.

        # Failures

        - Raises `AssertionError` when persisted placement, origin, or action
          data cannot form one valid summary.
        """
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        assert comments != [], "persisted Thread folded to zero Comments"
        _placement, origin = self._records()
        files = self._located_files()
        return ThreadSummaryView(
            thread_id=self.thread_id.hex,
            state=state,
            attention=attention,
            origin_target=origin_target_view(origin, files.origin_file),
            placement=self._placement_view(),
            first_comment=comments[0],
            latest_comment=comments[-1],
            comment_count=len(comments),
        )

    def _append(
        self,
        *,
        operation_id: UUID,
        author: ProfileAuthor,
        kind: Literal[
            "comment-created",
            "comment-edited",
            "comment-deleted",
            "thread-resolved",
            "thread-reopened",
            "thread-deleted",
        ],
        comment_id: Optional[UUID],
        body: Optional[str],
        comment_attention: Literal["inert", "alert"],
    ) -> ThreadUpdateView:
        """Validate, append, and return the write's bounded update view.

        # Parameters

        - `operation_id`: Fresh identity for the one attempted append.
        - `author`: Existing Profile whose permissions are checked.
        - `kind`: Exact Comment or lifecycle transition to perform.
        - `comment_id`: Affected Comment identity, or `None` for a body-less
          Thread lifecycle action.
        - `body`: New Comment text when the transition carries one.
        - `comment_attention`: Attention rule used only for Comment creation.
        """
        actions, profiles = append_review_action(
            database=self._database,
            snapshot_id=self.snapshot_id,
            thread_id=self.thread_id,
            operation_id=operation_id,
            author=author,
            kind=kind,
            comment_id=comment_id,
            body=body,
            comment_attention=comment_attention,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )
        # Placement and captured code are immutable. Only authored action history
        # and current Profile names change after an accepted write.
        self._action_records = actions
        self._profiles = profiles
        # The HTTP boundary is the one consumer of the update view, so every
        # write materializes the bounded view it reports instead of rehydrating
        # placement.
        state, attention, comments = fold_actions(actions, profiles)
        comment = (
            next(
                folded
                for folded in comments
                if folded["comment_id"] == comment_id.hex
            )
            if comment_id is not None
            else None
        )
        return ThreadUpdateView(
            thread_id=self.thread_id.hex,
            snapshot_id=self.snapshot_id.hex,
            state=state,
            attention=attention,
            discussion_revision=len(actions) - 1,
            comment=comment,
        )

    def add_comment(
        self,
        command: AddComment,
        *,
        attention: Literal["inert", "alert"],
    ) -> ThreadUpdateView:
        """Append one Comment and return the authoritative update view.

        `attention` is the posting instrument: `alert` raises both-role
        attention with the new Comment, `inert` leaves current attention
        unchanged.

        # Parameters

        - `command`: Fresh Comment identity, existing author, and nonblank body.
        - `attention`: Instrument-specific attention transition for this append.

        # Usage

        Create `AddComment` with a fresh operation and Comment id. Choose
        `alert` for an ordinary reply and `inert` only when another instrument
        controls attention for the same logical action.

        # Failures

        - Raises `ReviewError` when the author is missing, the body is blank, an
          id is reused, or the Thread is deleted.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-created",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention=attention,
        )

    def edit_comment(
        self, comment_id: UUID, command: EditComment
    ) -> ThreadUpdateView:
        """Edit one authored Comment and return the update view.

        # Parameters

        - `comment_id`: Existing non-deleted Comment to replace.
        - `command`: Acting author, fresh operation id, and nonblank replacement
          body. Only the original author may edit; the current revision is
          loaded by the Thread rather than supplied by the caller.

        # Usage

        Bind the Thread through the Comment's Snapshot placement, then pass the
        target Comment id and a fresh operation id. The returned Comment carries
        the newly assigned revision.

        # Failures

        - Raises `ReviewError` when the Comment or author is missing, the body is
          blank, the Comment is deleted, the actor is not its original author,
          or the Thread is deleted.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-edited",
            comment_id=comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def delete_comment(
        self, comment_id: UUID, command: DeleteComment
    ) -> ThreadUpdateView:
        """Tombstone one Comment and retain the acting Profile attribution.

        # Parameters

        - `comment_id`: Existing non-deleted Comment to tombstone.
        - `command`: Acting Profile and fresh operation id. The Thread loads the
          current revision; the actor need not be the original author.

        # Usage

        Bind the Thread through the Comment's Snapshot placement and pass a
        fresh operation id. Use the returned tombstone and revision instead of
        mutating a previously read Comment locally.

        # Failures

        - Raises `ReviewError` when the Comment or actor is missing, the Comment
          is already deleted, an id is reused, or the Thread is deleted.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-deleted",
            comment_id=comment_id,
            body=None,
            comment_attention="inert",
        )

    def resolve(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Resolve this bound open Thread and return its authoritative outcome.

        The command may include a paired nonblank explanation Comment. The
        method reloads current actions under the Room lock, rejects any non-open
        state, appends atomically, then returns state, attention, revision, and
        the optional created Comment.

        # Usage

        A reviewer uses this on an open bound Thread. Supply a fresh operation
        id and either both explanation fields or neither.

        # Failures

        - Raises `ReviewError` when the author is missing, the Thread is not
          open, explanation fields are invalid, or an identity is reused.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-resolved",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def reopen(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Reopen this bound resolved Thread and return its authoritative outcome.

        A paired explanation Comment is optional. Current actions are reloaded
        under the Room lock; any state other than resolved fails before append,
        and an accepted transition sets attention to both roles.

        # Usage

        Use this on a resolved bound Thread. Supply a fresh operation id and,
        when explaining the reopen, both a fresh Comment id and nonblank body.

        # Failures

        - Raises `ReviewError` when the author is missing, the Thread is not
          resolved, explanation fields are invalid, or an identity is reused.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-reopened",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def delete(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Record terminal deletion of this bound live Thread.

        The command must carry no Comment id or body. Current history is
        revalidated under the Room lock, the deletion action is appended with
        attention `none`, and later writes through the Thread are rejected.

        # Usage

        Use this exceptional reviewer instrument only when the whole Thread
        must become a retained terminal tombstone. Supply a fresh operation id
        and no explanation fields.

        # Failures

        - Raises `ReviewError` when the author is missing, the Thread is already
          deleted, explanation fields are present, or an identity is reused.
        """
        assert command.comment_id is None, (
            "Thread deletion never carries a Comment."
        )
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-deleted",
            comment_id=None,
            body=None,
            comment_attention="inert",
        )


def thread_objects(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
    offset: int,
    limit: int,
    state: Literal["all", "open"],
    attention: Optional[Literal["author", "reviewer"]],
    through_activity_id: Optional[int],
) -> tuple[tuple[Thread, ...], int, int]:
    """Bulk-hydrate one bounded Thread page at one inclusive activity pivot.

    # Parameters

    - `database`: Room persistence supplying the bounded relational page.
    - `identity`: Room containing the selected Snapshot and discussions.
    - `snapshot_id`: Exact code universe whose placements are bound.
    - `lock_path`: Cross-process write lock carried by returned handles.
    - `thread_lock`: In-process write lock carried by returned handles.
    - `offset`: Zero-based number of ordered matching Threads to skip.
    - `limit`: Positive maximum number of Threads to return.
    - `state`: Include every lifecycle state or only open discussions.
    - `attention`: Optional agent-role attention filter.
    - `through_activity_id`: Inclusive stable page pivot, or `None` to choose
      the current Room boundary with this persistence read.

    # Usage

    Room paging calls this once per page. Retain the returned concrete activity
    pivot and pass it to later pages so every `Thread` reflects the same bounded
    action history.

    # Returns

    - `First`: Bound Thread handles in the store's page order.
    - `Second`: Total Threads matching the filters, independent of page length.
    - `Third`: The concrete inclusive activity pivot used for this page and any
      continuation pages.

    # Failures

    - Raises `DirdiffError` when the Snapshot does not belong to the Room.
    - Raises `AssertionError` when persisted placements, origins, actions,
      Profiles, or referenced Files are incomplete or contradictory.
    """
    result = database.review_threads(
        identity,
        snapshot_id.hex,
        offset=offset,
        limit=limit,
        state=state,
        attention=attention,
        through_activity_id=through_activity_id,
    )
    if result is None:
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    data, concrete_activity_id = result
    return (
        _bind_threads(
            database=database,
            identity=identity,
            snapshot_id=snapshot_id,
            data=data,
            lock_path=lock_path,
            thread_lock=thread_lock,
        ),
        data.total_threads,
        concrete_activity_id,
    )


def _bind_threads(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    data: ReviewThreadsRecord,
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[Thread, ...]:
    """Bind hydrated Thread rows to exactly the Files they reference.

    One focused store read loads every referenced origin File, every located
    selected-Snapshot File, and the file-missing absence proof, replacing the
    former complete-Snapshot loads and thrown-away indexes.

    # Parameters

    - `database`: Persistence used for the one focused File hydration.
    - `identity`: Room identity carried by every returned handle.
    - `snapshot_id`: Exact placement Snapshot selected by the page.
    - `data`: Complete mutually consistent rows returned by one store read.
    - `lock_path`: Cross-process write lock carried by returned handles.
    - `thread_lock`: In-process write lock carried by returned handles.

    # Returns

    - `Members`: One bound handle per origin in `data`, each carrying its exact
      selected placement, origin, action history, Profiles, and focused Files.
    - `Order and sharing`: Handles preserve persistence order and share one
      read-scoped composed-bay cache.
    """
    placements = {row.thread_id: row for row in data.threads}
    origins = {row.thread_id: row for row in data.origins}
    actions: dict[str, list[ReviewActionRecord]] = {
        thread_id: [] for thread_id in origins
    }
    for action in data.actions:
        actions[action.thread_id].append(action)
    assert (
        len(placements) == len(origins) == len(data.origins)
        and placements.keys() == origins.keys()
    ), "review read contains duplicate Thread rows"
    profiles = {profile.id: profile for profile in data.profiles}
    assert len(profiles) == len(data.profiles), (
        "review read contains duplicate Profiles"
    )
    for origin in data.origins:
        assert origin.snapshot_file_id is not None, (
            "review origin has no Snapshot File"
        )
    origin_refs = tuple(
        dict.fromkeys(
            (origin.snapshot_id, origin.snapshot_file_id)
            for origin in data.origins
            if origin.snapshot_file_id is not None
        )
    )
    located_file_ids = tuple(
        dict.fromkeys(
            placement.snapshot_file_id
            for placement in data.threads
            if placement.snapshot_file_id is not None
        )
    )
    absent_origin_refs = tuple(
        dict.fromkeys(
            (
                origins[placement.thread_id].snapshot_id,
                origin_file_id,
            )
            for placement in data.threads
            # An unreadable File is unreferenced but present, so it is not an
            # absence to prove.
            if placement.snapshot_file_id is None
            and placement.outdated_reason != "file_unreadable"
            and (
                origin_file_id := origins[placement.thread_id].snapshot_file_id
            )
            is not None
        )
    )
    origin_files, selected_files, conflicts = database.review_thread_files(
        snapshot_id.hex,
        origin_refs,
        located_file_ids,
        absent_origin_refs,
    )
    assert conflicts == (), "file_missing placement has an exact Snapshot File"
    cache = ReviewReadCache()
    threads: list[Thread] = []
    for origin in data.origins:
        placement = placements[origin.thread_id]
        assert origin.snapshot_file_id is not None
        selected_file: Optional[SnapshotFileRecord] = None
        if placement.snapshot_file_id is not None:
            selected_file = selected_files.get(placement.snapshot_file_id)
            assert selected_file is not None, (
                "located placement has no exact Snapshot File"
            )
        threads.append(
            Thread(
                database=database,
                identity=identity,
                snapshot_id=snapshot_id,
                thread_id=UUID(hex=origin.thread_id),
                lock_path=lock_path,
                thread_lock=thread_lock,
                placement=placement,
                origin=origin,
                actions=tuple(actions[origin.thread_id]),
                profiles=profiles,
                files=_ThreadFiles(
                    origin_file=origin_files[
                        (origin.snapshot_id, origin.snapshot_file_id)
                    ],
                    selected_file=selected_file,
                    cache=cache,
                ),
            )
        )
    return tuple(threads)


def get_thread(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    thread_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
) -> Thread:
    """Return one exact bound Thread or report that it does not exist.

    The returned handle carries placement and the authored actions; it loads its
    Snapshot Files only when a read interprets placement, so write-only
    callers never pay for File hydration.

    # Parameters

    - `database`: Persistence used to load the focused discussion.
    - `identity`: Room that must contain the Snapshot and Thread origin.
    - `snapshot_id`: Exact placement to bind.
    - `thread_id`: Stable discussion identity to bind.
    - `lock_path`: Cross-process write lock carried by the handle.
    - `thread_lock`: In-process write lock carried by the handle.

    # Usage

    Use this for a focused discussion read or write after the Room and Snapshot
    are known. The handle remains bound to that placement for its lifetime.

    # Failures

    - Raises `ReviewError` when the Snapshot is unknown in the Room or the
      Thread has no placement in that Snapshot.
    - Raises `AssertionError` when persisted discussion rows violate review
      invariants.
    """
    data = database.review_thread(
        identity,
        snapshot_id.hex,
        thread_id.hex,
    )
    if data is None:
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    if data.origins == ():
        raise ReviewError(
            "thread_not_found", f"Unknown Thread: {thread_id.hex}"
        )
    assert len(data.threads) == len(data.origins) == 1
    profiles = {profile.id: profile for profile in data.profiles}
    assert len(profiles) == len(data.profiles), (
        "review read contains duplicate Profiles"
    )
    return Thread(
        database=database,
        identity=identity,
        snapshot_id=snapshot_id,
        thread_id=thread_id,
        lock_path=lock_path,
        thread_lock=thread_lock,
        placement=data.threads[0],
        origin=data.origins[0],
        actions=data.actions,
        profiles=profiles,
        files=None,
    )


def create_thread(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    command: CreateThread,
    lock_path: Path,
    thread_lock: Lock,
) -> Thread:
    """Create one discussion in its immutable origin Snapshot.

    # Parameters

    - `database`: Persistence for Profile validation, focused File lookup, and
      the atomic origin/action insert.
    - `identity`: Room that must contain the origin Snapshot.
    - `snapshot_id`: Exact Snapshot whose selected range becomes the origin.
    - `command`: Fresh identities, author, valid target, and first Comment.
    - `lock_path`: Cross-process Room write lock shared with publication.
    - `thread_lock`: In-process Room write lock held for the same lifetime.

    # Usage

    Construct the command from a validated public target and fresh Thread,
    Comment, and operation ids. Call through `Room.create_thread`, which supplies
    the Room identity and shared locks.

    # Failures

    - Raises `ReviewError` when the Snapshot or Profile is missing, the target
      File or bay cannot be reviewed, the range is invalid, or an identity has
      already been used.
    - Raises `AssertionError` when captured or persisted data violates an
      internal review invariant.
    """

    with room_write_lock(thread_lock, lock_path):
        profile = validate_author(database, command.author)
        # One focused File read replaces hydrating the whole Snapshot: the
        # creation needs exactly its target File and Snapshot visibility.
        snapshot_exists, loaded_target = database.snapshot_file(
            identity,
            snapshot_id=snapshot_id.hex,
            left_path=command.target.file.left_path,
            right_path=command.target.file.right_path,
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        cache = ReviewReadCache()
        created_at = action_timestamp()
        rows, first_action = plan_thread_creation(
            command=command,
            created_at=created_at,
            snapshot_id=snapshot_id.hex,
            target_file=loaded_target.file
            if loaded_target is not None
            else None,
            cache=cache,
        )
        database.create_review_thread(
            rows,
            first_action,
        )
        assert loaded_target is not None
        assert len(rows) == 1 and rows[0].is_origin
        assert rows[0].snapshot_file_id == loaded_target.file.id
        created_file = loaded_target.file
        return Thread(
            database=database,
            identity=identity,
            snapshot_id=snapshot_id,
            thread_id=command.thread_id,
            lock_path=lock_path,
            thread_lock=thread_lock,
            placement=rows[0],
            origin=rows[0],
            actions=(first_action,),
            profiles={profile.id: profile},
            # A new Thread's origin Snapshot is the selected Snapshot, so one
            # File serves both bindings.
            files=_ThreadFiles(
                origin_file=created_file,
                selected_file=created_file,
                cache=cache,
            ),
        )
