from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal, Optional
from uuid import UUID

from dirdiff.db import (
    AuthoredComment,
    BayStartPlacement,
    CommentCreatedAction,
    CommentDeletedAction,
    CommentEditedAction,
    FileMissingPlacement,
    FileStartPlacement,
    FileUnreadablePlacement,
    Placement,
    RangePlacement,
    ReviewActionInput,
    ReviewActionPayload,
    ReviewProfileRecord,
    ReviewScopePlacement,
    ReviewThreadRecord,
    ReviewThreadsRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    SnapshotFileSideRecord,
    SnapshotSymlinkFileSideRecord,
    ThreadDeletedAction,
    ThreadStateAction,
)
from dirdiff.engines import DirdiffError
from dirdiff.review._thread.placement import (
    ReviewReadCache,
    build_original_excerpt,
    derive_room_threads,
    file_pair,
    origin_target_view,
    plan_thread_creation,
)
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
    CreateThread,
    DeleteComment,
    EditComment,
    FilePair,
    ProfileAuthor,
    ReviewBatchAction,
    ReviewBatchResult,
    ReviewCommentView,
    ReviewError,
    ReviewOriginView,
    ReviewProfileView,
    ReviewRole,
    ThreadDiscussionView,
    ThreadLocationSourceView,
    ThreadPlacementView,
    ThreadSummaryView,
    ThreadUpdateView,
    action_timestamp,
    room_write_lock,
    validate_author,
    validate_comment_body,
)
from dirdiff.util import Nothing, Ok, Option, Some

__all__ = [
    "Thread",
    "apply_review_batch",
    "create_thread",
    "derive_room_threads",
    "get_thread",
    "thread_objects",
]


@dataclass
class _CommentState:
    comment_id: str
    sequence: int
    profile_id: int
    revision: int
    body: Optional[str]
    deleted: bool
    created_at: str
    updated_at: str


def fold_actions(
    actions: tuple[ReviewActionInput, ...],
    profiles: dict[int, ReviewProfileRecord],
) -> tuple[
    Literal["open", "resolved", "deleted"],
    Literal["author", "reviewer", "both", "none"],
    list[ReviewCommentView],
]:
    # Impossible persisted history asserts; it is never repaired or skipped.
    assert actions != () and actions[0].payload.kind == "thread-created"
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
        payload = action.payload
        authored = (
            payload.comment
            if isinstance(payload, (CommentCreatedAction, ThreadStateAction))
            else None
        )
        if authored is not None:
            assert authored.id not in comments
            assert authored.body.strip() != "", (
                "persisted Comment body is blank"
            )
            comments[authored.id] = _CommentState(
                authored.id,
                len(order),
                profile_id,
                0,
                authored.body,
                False,
                action.created_at,
                action.created_at,
            )
            order.append(authored.id)
        elif isinstance(payload, CommentEditedAction):
            assert payload.comment.body.strip() != "", (
                "persisted Comment body is blank"
            )
            comment = comments[payload.comment.id]
            assert not comment.deleted, "deleted Comment was edited"
            assert profile_id == comment.profile_id, (
                "Comment was edited by another author"
            )
            assert payload.expected_revision == comment.revision, (
                "Comment edit has a stale revision"
            )
            comment.revision += 1
            comment.body = payload.comment.body
            comment.updated_at = action.created_at
        elif isinstance(payload, CommentDeletedAction):
            comment = comments[payload.comment_id]
            assert not comment.deleted, "Comment was deleted twice"
            assert payload.expected_revision == comment.revision, (
                "Comment deletion has a stale revision"
            )
            comment.revision += 1
            comment.body = None
            comment.deleted = True
            comment.updated_at = action.created_at
        else:
            assert isinstance(payload, (ThreadStateAction, ThreadDeletedAction))
    views: list[ReviewCommentView] = []
    for comment_id in order:
        comment = comments[comment_id]
        profile = profiles[comment.profile_id]
        author = ReviewProfileView(
            profile_id=profile.id,
            display_name=profile.username,
            is_agent=profile.agent is not None,
            model=None if profile.agent is None else profile.agent.model,
            harness=None if profile.agent is None else profile.agent.harness,
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
    tuple[ReviewActionInput, ...],
    dict[int, ReviewProfileRecord],
]:
    with room_write_lock(thread_lock, lock_path):
        # State is reloaded under the lock; the handle's cached actions may be
        # stale.
        profile_id = author.profile_id
        profile = validate_author(database, author)
        persisted = database.review_actions(snapshot_id.hex, thread_id.hex)
        if persisted is None:
            raise ReviewError(
                "thread_not_found", f"Unknown Thread: {thread_id.hex}"
            )
        persisted_actions, persisted_profiles = persisted
        actions = tuple(record.action for record in persisted_actions)
        profiles = {
            persisted_profile.id: persisted_profile
            for persisted_profile in persisted_profiles
        }
        state, attention, comments = fold_actions(actions, profiles)
        if state == "deleted":
            raise ReviewError("state_conflict", "Thread is deleted.")
        comment_by_id = {comment["comment_id"]: comment for comment in comments}

        payload: ReviewActionPayload
        if kind == "comment-created":
            assert comment_id is not None and body is not None
            validate_comment_body(body)
            payload = CommentCreatedAction(
                kind, AuthoredComment(comment_id.hex, body)
            )
        elif kind == "comment-edited" or kind == "comment-deleted":
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
                payload = CommentEditedAction(
                    AuthoredComment(comment_id.hex, body), accepted_revision
                )
            else:
                assert body is None
                payload = CommentDeletedAction(
                    comment_id.hex, accepted_revision
                )
        elif kind == "thread-resolved" or kind == "thread-reopened":
            if kind == "thread-resolved" and state != "open":
                raise ReviewError(
                    "state_conflict", "Only an open Thread may be resolved."
                )
            if kind == "thread-reopened" and state != "resolved":
                raise ReviewError(
                    "state_conflict", "Only a resolved Thread may be reopened."
                )
            assert (comment_id is None) == (body is None)
            payload = ThreadStateAction(
                kind,
                AuthoredComment(comment_id.hex, body)
                if comment_id is not None and body is not None
                else None,
            )
        else:
            assert (
                kind == "thread-deleted" and comment_id is None and body is None
            )
            payload = ThreadDeletedAction()

        record = ReviewActionInput(
            operation_id=operation_id.hex,
            thread_id=thread_id.hex,
            snapshot_id=snapshot_id.hex,
            sequence=len(actions),
            profile_id=profile_id,
            created_at=action_timestamp(),
            status_after="resolved"
            if kind == "thread-resolved"
            else "open"
            if kind == "thread-reopened"
            else "deleted"
            if kind == "thread-deleted"
            else state,
            attention_after="none"
            if kind in {"thread-resolved", "thread-deleted"}
            else "both"
            if kind == "thread-reopened"
            or (kind == "comment-created" and comment_attention == "alert")
            else attention,
            payload=payload,
        )
        database.append_review_action(record)
        profiles[profile.id] = profile
        return (*actions, record), profiles


@dataclass
class _ThreadFiles:
    origin_file: SnapshotFileRecord
    # None only for file-absent placements, whose absence the loading read
    # has already proven.
    selected_file: Optional[SnapshotFileRecord]
    cache: ReviewReadCache


class Thread:
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
        actions: tuple[ReviewActionInput, ...],
        profiles: dict[int, ReviewProfileRecord],
        files: Optional[_ThreadFiles],
        location_source: ThreadLocationSourceView,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.thread_id = thread_id
        self._database = database
        self._identity = identity
        self._lock_path = lock_path
        self._thread_lock = thread_lock
        self._placement = placement.placement
        origin_placement = origin.placement
        assert isinstance(
            origin_placement,
            (RangePlacement, FileStartPlacement, ReviewScopePlacement),
        ), "a discussion origin is a stored range, File-start, or review row"
        self._origin: (
            RangePlacement | FileStartPlacement | ReviewScopePlacement
        ) = origin_placement
        self._action_records = actions
        self._profiles = profiles
        # Mutated once by `_located_files` when constructed deferred; every
        # later locating read reuses the same loaded records and cache.
        self._files = files
        self._location_source = location_source

    def _records(
        self,
    ) -> tuple[
        Placement, RangePlacement | FileStartPlacement | ReviewScopePlacement
    ]:
        return self._placement, self._origin

    def _actions(self) -> tuple[ReviewActionInput, ...]:
        assert self._action_records != (), (
            "persisted Thread has no first Comment"
        )
        return self._action_records

    def _located_files(self) -> _ThreadFiles:
        if self._files is None:
            placement, origin = self._records()
            assert not isinstance(origin, ReviewScopePlacement), (
                "a review-scoped Thread has no Snapshot Files"
            )
            origin_ref = (origin.snapshot_id, origin.snapshot_file_id)
            selected_ids: tuple[str, ...] = ()
            absent_refs: tuple[tuple[str, str], ...] = ()
            # An unreadable File is present and deliberately unreferenced, so
            # it asks for neither: proving its absence would fail, and loading
            # it would offer bytes no read may use.
            if isinstance(placement, FileMissingPlacement):
                absent_refs = (origin_ref,)
            elif not isinstance(placement, FileUnreadablePlacement):
                assert not isinstance(placement, ReviewScopePlacement)
                selected_ids = (placement.snapshot_file_id,)
            origin_files, selected_files, conflicts, _comparison_files = (
                self._database.review_thread_files(
                    self.snapshot_id.hex,
                    (origin_ref,),
                    selected_ids,
                    absent_refs,
                    (),
                )
            )
            assert conflicts == (), (
                "file_missing placement has an exact Snapshot File"
            )
            selected_file: Optional[SnapshotFileRecord] = None
            if selected_ids != ():
                assert not isinstance(
                    placement,
                    FileMissingPlacement
                    | FileUnreadablePlacement
                    | ReviewScopePlacement,
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
        placement, origin = self._records()
        if isinstance(placement, ReviewScopePlacement):
            return {"kind": "review-scope"}
        assert not isinstance(origin, ReviewScopePlacement)
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
            case ReviewScopePlacement():
                raise AssertionError("handled before File loading")

    def discussion(self) -> ThreadDiscussionView:
        _placement, origin = self._records()
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        origin_target: ReviewOriginView
        if isinstance(origin, ReviewScopePlacement):
            origin_target = {"kind": "review"}
        else:
            files = self._located_files()
            origin_target = origin_target_view(origin, files.origin_file)
        if isinstance(origin, RangePlacement):
            # Only a discussion read builds an excerpt, and it belongs to the
            # origin it is cut from. The summary path reads no captured text,
            # so the key is attached here rather than by the shared builder.
            assert origin_target["kind"] == "text"
            files = self._located_files()
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
            location_source=self._location_source,
            comments=comments,
        )

    def summary(self) -> ThreadSummaryView:
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        assert comments != [], "persisted Thread folded to zero Comments"
        _placement, origin = self._records()
        origin_target: ReviewOriginView
        if isinstance(origin, ReviewScopePlacement):
            origin_target = {"kind": "review"}
        else:
            origin_target = origin_target_view(
                origin, self._located_files().origin_file
            )
        return ThreadSummaryView(
            thread_id=self.thread_id.hex,
            state=state,
            attention=attention,
            origin_target=origin_target,
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
        # Placement and captured code are immutable. Only authored action
        # history
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
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-deleted",
            comment_id=comment_id,
            body=None,
            comment_attention="inert",
        )

    def resolve(self, command: ChangeThreadState) -> ThreadUpdateView:
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-resolved",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def reopen(self, command: ChangeThreadState) -> ThreadUpdateView:
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-reopened",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def delete(self, command: ChangeThreadState) -> ThreadUpdateView:
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
    scope: Literal["snapshot", "room"],
    through_activity_id: Optional[int],
) -> tuple[tuple[Thread, ...], int, int]:
    result = database.review_threads(
        identity,
        snapshot_id.hex,
        offset=offset,
        limit=limit,
        state=state,
        attention=attention,
        scope=scope,
        through_activity_id=through_activity_id,
    )
    if not Ok.good(result):
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    data, concrete_activity_id = result.value
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
    def same_captured_file(
        origin_file: SnapshotFileRecord,
        displayed_file: SnapshotFileRecord,
    ) -> bool:
        def side_identity(
            side: Option[SnapshotFileSideRecord],
        ) -> Option[tuple[str, bytes, tuple[bytes, bytes | None] | None]]:
            if not Some.present(side):
                return Nothing()
            record = side.value
            link_identity = None
            if isinstance(record, SnapshotSymlinkFileSideRecord):
                link_identity = (
                    record.link.metadata_hash,
                    record.link.target.get_map(
                        lambda target: target.content_hash
                    ),
                )
            return Some(
                (record.repository_path, record.content_hash, link_identity)
            )

        # Capture errors make the displayed File unusable, never equal.
        # Symlink sides compare link metadata and target digests, not their
        # Snapshot-specific physical paths.
        return (
            origin_file.error is None
            and displayed_file.error is None
            and side_identity(origin_file.left)
            == side_identity(displayed_file.left)
            and side_identity(origin_file.right)
            == side_identity(displayed_file.right)
        )

    placements = {row.placement.thread_id: row for row in data.threads}
    origins = {row.placement.thread_id: row for row in data.origins}
    # Review-scoped Threads reference no Snapshot File, so they stay out of
    # every File lookup below and bind with no preloaded Files.
    origin_locations: dict[str, RangePlacement | FileStartPlacement] = {}
    review_scope_ids: set[str] = set()
    for record in data.origins:
        origin = record.placement
        if isinstance(origin, ReviewScopePlacement):
            review_scope_ids.add(origin.thread_id)
            continue
        assert isinstance(origin, (RangePlacement, FileStartPlacement)), (
            "review origin has no Snapshot File"
        )
        origin_locations[origin.thread_id] = origin
    actions: dict[str, list[ReviewActionInput]] = {
        thread_id: [] for thread_id in origins
    }
    for action_record in data.actions:
        action = action_record.action
        actions[action.thread_id].append(action)
    assert (
        len(placements) == len(origins) == len(data.origins)
        and placements.keys() == origins.keys()
    ), "review read contains duplicate Thread rows"
    profiles = {profile.id: profile for profile in data.profiles}
    assert len(profiles) == len(data.profiles), (
        "review read contains duplicate Profiles"
    )
    origin_refs = tuple(
        dict.fromkeys(
            (origin.snapshot_id, origin.snapshot_file_id)
            for origin in origin_locations.values()
        )
    )
    displayed_thread_ids = {
        record.placement.thread_id
        for record in data.threads
        if not record.is_origin
    }
    located_file_ids = tuple(
        dict.fromkeys(
            record.placement.snapshot_file_id
            for record in data.threads
            if record.placement.thread_id in displayed_thread_ids
            and isinstance(
                record.placement,
                (RangePlacement, BayStartPlacement, FileStartPlacement),
            )
        )
    )
    absent_origin_refs = tuple(
        dict.fromkeys(
            (
                origin_locations[record.placement.thread_id].snapshot_id,
                origin_locations[record.placement.thread_id].snapshot_file_id,
            )
            for record in data.threads
            if record.placement.thread_id in displayed_thread_ids
            and isinstance(record.placement, FileMissingPlacement)
        )
    )
    comparison_origin_refs = tuple(
        (origin.snapshot_id, origin.snapshot_file_id)
        for origin in origin_locations.values()
        if origin.thread_id not in displayed_thread_ids
    )
    origin_files, selected_files, conflicts, comparison_files = (
        database.review_thread_files(
            snapshot_id.hex,
            origin_refs,
            located_file_ids,
            absent_origin_refs,
            comparison_origin_refs,
        )
    )
    assert conflicts == (), "file_missing placement has an exact Snapshot File"
    cache = ReviewReadCache()
    threads: list[Thread] = []
    for origin_record in data.origins:
        thread_key = origin_record.placement.thread_id
        if thread_key in review_scope_ids:
            review_placement = placements[thread_key].placement
            assert review_placement.snapshot_id == snapshot_id.hex, (
                "review-scoped Thread has no placement in the displayed "
                "Snapshot"
            )
            threads.append(
                Thread(
                    database=database,
                    identity=identity,
                    snapshot_id=snapshot_id,
                    thread_id=UUID(hex=thread_key),
                    lock_path=lock_path,
                    thread_lock=thread_lock,
                    placement=placements[thread_key],
                    origin=origin_record,
                    actions=tuple(actions[thread_key]),
                    profiles=profiles,
                    files=None,
                    location_source={"kind": "displayed-snapshot"},
                )
            )
            continue
        origin = origin_locations[thread_key]
        record = placements[origin.thread_id]
        placement = record.placement
        origin_ref = origin.snapshot_id, origin.snapshot_file_id
        origin_file = origin_files[origin_ref]
        selected_file: Optional[SnapshotFileRecord] = None
        if origin.thread_id not in displayed_thread_ids:
            selected_file = origin_file
            displayed_file = comparison_files.get(origin_ref)
            file_state: Literal["unchanged", "changed", "missing", "unreadable"]
            if displayed_file is None:
                file_state = "missing"
            elif displayed_file.error is not None:
                file_state = "unreadable"
            elif same_captured_file(origin_file, displayed_file):
                file_state = "unchanged"
            else:
                file_state = "changed"
            location_source: ThreadLocationSourceView = {
                "kind": "origin-snapshot",
                "file_state": file_state,
            }
        elif isinstance(
            placement, (RangePlacement, BayStartPlacement, FileStartPlacement)
        ):
            selected_file = selected_files.get(placement.snapshot_file_id)
            assert selected_file is not None, (
                "located placement has no exact Snapshot File"
            )
            location_source = {"kind": "displayed-snapshot"}
        else:
            location_source = {"kind": "displayed-snapshot"}
        assert (placement.snapshot_id == snapshot_id.hex) == (
            location_source["kind"] == "displayed-snapshot"
        ), "location_source contradicts the placement's Snapshot"
        threads.append(
            Thread(
                database=database,
                identity=identity,
                snapshot_id=UUID(hex=placement.snapshot_id),
                thread_id=UUID(hex=origin.thread_id),
                lock_path=lock_path,
                thread_lock=thread_lock,
                placement=record,
                origin=origins[origin.thread_id],
                actions=tuple(actions[origin.thread_id]),
                profiles=profiles,
                files=_ThreadFiles(
                    origin_file=origin_file,
                    selected_file=selected_file,
                    cache=cache,
                ),
                location_source=location_source,
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
    result = database.review_thread(
        identity,
        snapshot_id.hex,
        thread_id.hex,
    )
    if not Ok.good(result):
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    data = result.value
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
        actions=tuple(record.action for record in data.actions),
        profiles=profiles,
        files=None,
        location_source={"kind": "displayed-snapshot"},
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
    with room_write_lock(thread_lock, lock_path):
        profile = validate_author(database, command.author)
        # One focused File read replaces hydrating the whole Snapshot: the
        # creation needs exactly its target File and Snapshot visibility.
        target_result = database.snapshot_file(
            identity,
            snapshot_id=snapshot_id.hex,
            left_path=command.target.file.left_path.get(),
            right_path=command.target.file.right_path.get(),
        )
        if (
            not Ok.good(target_result)
            and target_result.error == "snapshot-unavailable"
        ):
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        # The planner validates absent Files before validating the comment.
        loaded_target = target_result.value if Ok.good(target_result) else None
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
        assert isinstance(rows[0].placement, RangePlacement)
        assert rows[0].placement.snapshot_file_id == loaded_target.file.id
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
            location_source={"kind": "displayed-snapshot"},
        )


def apply_review_batch(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    batch: tuple[ReviewBatchAction, ...],
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[ReviewBatchResult, ...]:
    if batch == ():
        raise ReviewError("invalid_target", "Review batch cannot be empty.")
    with room_write_lock(thread_lock, lock_path):
        cache = ReviewReadCache()
        placements: list[ReviewThreadRecord] = []
        records: list[ReviewActionInput] = []
        # Per-record (comment id, thread id, resulting state) for the result.
        outcomes: list[
            tuple[
                UUID,
                Optional[UUID],
                Literal["open", "resolved", "deleted"],
                Literal["author", "reviewer", "both", "none"],
            ]
        ] = []

        # Set-based reads replace per-item queries: every author in one
        # Profile read, every addressed existing Thread history in one read,
        # and every creation target File per origin Snapshot in one lookup.
        author_ids = tuple(
            dict.fromkeys(item.command.author.profile_id for item in batch)
        )
        known_profiles = {
            profile.id: profile
            for profile in database.review_profiles(author_ids)
        }
        for author_id in author_ids:
            if author_id not in known_profiles:
                raise ReviewError(
                    "profile_not_found", f"Unknown Profile: {author_id}"
                )
        created_ids = {
            item.command.thread_id.hex
            for item in batch
            if isinstance(item, BatchCreate)
        }
        addressed = tuple(
            dict.fromkeys(
                item.thread_id.hex
                for item in batch
                if not isinstance(item, BatchCreate)
                and item.thread_id.hex not in created_ids
            )
        )
        histories, history_profiles = database.review_thread_histories(
            addressed
        )
        for thread_key in addressed:
            history = histories.get(thread_key)
            if history is None:
                raise ReviewError(
                    "thread_not_found", f"Unknown Thread: {thread_key}"
                )
            if history.identity != identity:
                raise ReviewError(
                    "invalid_target",
                    f"Thread {thread_key} belongs to a different Room.",
                )
        fold_profiles = {
            profile.id: profile for profile in history_profiles
        } | known_profiles

        creation_pairs: dict[str, dict[FilePair, None]] = {}
        review_snapshots: set[str] = set()
        for item in batch:
            if not isinstance(item, BatchCreate):
                continue
            if isinstance(item.command, CreateThread):
                creation_pairs.setdefault(item.snapshot_id.hex, {})[
                    item.command.target.file
                ] = None
            else:
                review_snapshots.add(item.snapshot_id.hex)
        files_by_pair: dict[tuple[str, FilePair], SnapshotFileRecord] = {}
        for snapshot_key, pairs in creation_pairs.items():
            lookup = database.snapshot_files_by_pairs(
                identity,
                snapshot_id=snapshot_key,
                pairs=tuple(
                    (pair.left_path.get(), pair.right_path.get())
                    for pair in pairs
                ),
            )
            if not Ok.good(lookup):
                raise DirdiffError(f"Unknown snapshot id: {snapshot_key}")
            for pair in pairs:
                found = lookup.value.get(
                    (pair.left_path.get(), pair.right_path.get())
                )
                if found is not None:
                    files_by_pair[(snapshot_key, pair)] = found
        for snapshot_key in review_snapshots:
            if database.room_identity(snapshot_key) != identity:
                raise DirdiffError(f"Unknown snapshot id: {snapshot_key}")
        # A review-scoped Thread is placed in every Snapshot of its Room:
        # capture copies it into later Snapshots, creation covers the existing
        # ones. Both run under the Room write lock, so no Snapshot is missed.
        room_snapshots = (
            database.room_snapshot_ids(identity) if review_snapshots else ()
        )

        # Thread key -> (origin Snapshot key, actions so far).
        simulated: dict[str, tuple[str, list[ReviewActionInput]]] = {
            thread_key: (
                history.origin.placement.snapshot_id,
                [record.action for record in history.actions],
            )
            for thread_key, history in histories.items()
        }

        # After a Comment, creation or reopen in `role` the Thread waits on the
        # other role.
        def waits_after(
            role: ReviewRole,
        ) -> Literal["author", "reviewer"]:
            return "reviewer" if role == "author" else "author"

        for item in batch:
            if isinstance(item, BatchCreate):
                command = item.command
                snapshot_key = item.snapshot_id.hex
                if isinstance(command, CreateThread):
                    rows, first_action = plan_thread_creation(
                        command=command,
                        created_at=action_timestamp(),
                        snapshot_id=snapshot_key,
                        target_file=files_by_pair.get(
                            (snapshot_key, command.target.file)
                        ),
                        cache=cache,
                    )
                else:
                    validate_comment_body(command.body)
                    rows = tuple(
                        ReviewThreadRecord(
                            ReviewScopePlacement(
                                command.thread_id.hex, room_snapshot
                            ),
                            room_snapshot == snapshot_key,
                            None,
                        )
                        for room_snapshot in room_snapshots
                    )
                    assert sum(row.is_origin for row in rows) == 1, (
                        "review-scoped origin Snapshot lies outside its Room"
                    )
                    first_action = ReviewActionInput(
                        operation_id=command.operation_id.hex,
                        thread_id=command.thread_id.hex,
                        snapshot_id=snapshot_key,
                        sequence=0,
                        profile_id=command.author.profile_id,
                        created_at=action_timestamp(),
                        status_after="open",
                        attention_after="author",
                        payload=CommentCreatedAction(
                            "thread-created",
                            AuthoredComment(
                                command.comment_id.hex, command.body
                            ),
                        ),
                    )
                first_action = replace(
                    first_action, attention_after=waits_after(item.role)
                )
                placements.extend(rows)
                records.append(first_action)
                simulated[command.thread_id.hex] = (
                    snapshot_key,
                    [first_action],
                )
                outcomes.append(
                    (
                        command.thread_id,
                        command.comment_id,
                        "open",
                        waits_after(item.role),
                    )
                )
                continue

            thread_key = item.thread_id.hex
            origin_snapshot, thread_actions = simulated[thread_key]
            profile_id = item.command.author.profile_id
            state, attention, comments = fold_actions(
                tuple(thread_actions), fold_profiles
            )
            if state == "deleted":
                raise ReviewError("state_conflict", "Thread is deleted.")
            payload: ReviewActionPayload
            comment_id: Optional[UUID] = None
            next_state: Literal["open", "resolved", "deleted"] = state
            next_attention = attention
            if isinstance(item, BatchComment):
                validate_comment_body(item.command.body)
                comment_id = item.command.comment_id
                payload = CommentCreatedAction(
                    "comment-created",
                    AuthoredComment(comment_id.hex, item.command.body),
                )
                next_attention = waits_after(item.role)
            elif isinstance(item, (BatchResolve, BatchReopen)):
                if isinstance(item, BatchResolve):
                    if state != "open":
                        raise ReviewError(
                            "state_conflict",
                            "Only an open Thread may be resolved.",
                        )
                    kind: Literal["thread-resolved", "thread-reopened"] = (
                        "thread-resolved"
                    )
                    next_state, next_attention = "resolved", "none"
                else:
                    if state != "resolved":
                        raise ReviewError(
                            "state_conflict",
                            "Only a resolved Thread may be reopened.",
                        )
                    kind = "thread-reopened"
                    next_state = "open"
                    next_attention = waits_after(item.role)
                authored: Optional[AuthoredComment] = None
                if item.command.comment_id is not None:
                    assert item.command.body is not None
                    validate_comment_body(item.command.body)
                    comment_id = item.command.comment_id
                    authored = AuthoredComment(
                        comment_id.hex, item.command.body
                    )
                payload = ThreadStateAction(kind, authored)
            elif isinstance(item, (BatchEditComment, BatchDeleteComment)):
                target = next(
                    (
                        comment
                        for comment in comments
                        if comment["comment_id"] == item.comment_id.hex
                    ),
                    None,
                )
                if target is None:
                    raise ReviewError(
                        "comment_not_found",
                        f"Unknown Comment: {item.comment_id.hex}",
                    )
                if target["deleted"]:
                    raise ReviewError("state_conflict", "Comment is deleted.")
                comment_id = item.comment_id
                if isinstance(item, BatchEditComment):
                    if target["author"]["profile_id"] != profile_id:
                        raise ReviewError(
                            "forbidden", "Only the Comment author may edit it."
                        )
                    validate_comment_body(item.command.body)
                    payload = CommentEditedAction(
                        AuthoredComment(comment_id.hex, item.command.body),
                        target["revision"],
                    )
                else:
                    payload = CommentDeletedAction(
                        comment_id.hex, target["revision"]
                    )
            else:
                assert isinstance(item, BatchDeleteThread)
                assert item.command.comment_id is None
                payload = ThreadDeletedAction()
                next_state, next_attention = "deleted", "none"
            record = ReviewActionInput(
                operation_id=item.command.operation_id.hex,
                thread_id=thread_key,
                snapshot_id=origin_snapshot,
                sequence=len(thread_actions),
                profile_id=profile_id,
                created_at=action_timestamp(),
                status_after=next_state,
                attention_after=next_attention,
                payload=payload,
            )
            thread_actions.append(record)
            records.append(record)
            outcomes.append(
                (item.thread_id, comment_id, next_state, next_attention)
            )

        # Every item validated above; one transaction writes them all, so a
        # failure earlier leaves nothing written.
        activity_ids = database.apply_review_batch(
            tuple(placements), tuple(records)
        )
        return tuple(
            ReviewBatchResult(
                thread_id, comment_id, activity_id, status, attention
            )
            for (thread_id, comment_id, status, attention), activity_id in zip(
                outcomes, activity_ids, strict=True
            )
        )
