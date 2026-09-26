"""Relational persistence for Rooms, Snapshots, Files, and review discussions.

## Classes

`RoomStore` is the module's database interface. Its immutable record types carry
complete Snapshot publication input, captured File facts, and review rows
between the store and `dirdiff.room_lord` or `dirdiff.review`.

## Purpose and boundaries

The store keeps each Snapshot tied to one Room, each File side tied to one
published File, and each review action tied to an existing Profile and Thread
placement. Operations that span several rows expose one transaction when the
caller needs atomic publication or an atomic review batch.

This module stores identities, paths, digests, metadata, and review actions. It
does not choose a Room from a Tab, capture or read File contents, acquire the
filesystem publication lock, derive Thread placement, or render a diff. Those
decisions belong to `dirdiff.room_lord`, `dirdiff.review`, and the rendering
flow.
"""

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
    or_,
    select,
    tuple_,
)
from sqlalchemy.orm import Mapped, Session, aliased, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from dirdiff.db.base import (
    TableBase,
    UserProfile,
    UserProfileRecord,
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
    "ReviewActionRecord",
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


class Room(TableBase):
    """Persist one Room identity selected by a Tab's correspondence law.

    `RoomStore` creates or finds this row from a `RoomIdentity` after RoomLord
    has applied the active Tab's correspondence law.

    This table does not store a selected Snapshot or interpret the key.
    """

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
    """Generated relational identity referenced by this Room's Snapshots.

    It remains stable across recaptures while correspondence selects the Room.
    """

    mark_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("repo_mark.id"),
        nullable=True,
    )
    """Repository mark for a repository Room, or `None` for a preset Room.

    Its nullability must agree with the persisted Tab and correspondence law.
    """

    tab: Mapped[str] = mapped_column(String, nullable=False)
    """Persisted Tab whose correspondence law produced this Room.

    Callers use it to decode the opaque key and choose recapture inputs.
    """

    backend_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """Opaque, non-empty correspondence key compared only for equality.

    Its internal bytes belong to the Tab-specific law and are never a display value.
    """


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

    Snapshot publication inserts or reuses this row after capture has computed
    the complete content hash. Follow-up routes use `id`; recapture uses
    `content_hash` to reuse equal state inside the Room.

    File contents, repository paths, and Snapshot-wide metadata live in
    dependent relations. The row has no mutable current-state flag.
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
    """Globally unique 32-character lowercase hexadecimal Snapshot key.

    HTTP follow-up operations retain it as the opaque identity of captured content.
    """

    room_id: Mapped[int] = mapped_column(
        ForeignKey("room.id"),
        nullable=False,
    )
    """Room containing this immutable content state.

    Content reuse and sequence comparisons are meaningful only within this relation.
    """

    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """SHA-256 identity used to reuse equal captured state within the Room.

    Equal hashes suppress duplicate Snapshots only inside the same Room.
    """


class SnapshotMeta(TableBase):
    """Persist presentation labels and backend totals for one Snapshot.

    Publication writes this one-to-one row with its Snapshot. Reads return the
    labels and backend totals through `SnapshotMetaRecord`.

    Added and removed line counts are either both nonnegative or both absent.
    They are backend facts, not totals derived from rendered bays.
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
    """Snapshot whose one-to-one metadata this row stores.

    The shared primary key prevents labels or totals from multiplying per capture.
    """

    left_label: Mapped[str] = mapped_column(String, nullable=False)
    """Non-empty human-readable label retained for the captured left side.

    It describes the capture and is not recomputed from later repository state.
    """

    right_label: Mapped[str] = mapped_column(String, nullable=False)
    """Non-empty human-readable label retained for the captured right side.

    Readers present it with this Snapshot even after its source ref changes.
    """

    added_lines: Mapped[Optional[int]] = mapped_column(nullable=True)
    """Nonnegative backend-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` always have equal presence.
    """

    removed_lines: Mapped[Optional[int]] = mapped_column(nullable=True)
    """Nonnegative backend-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` always have equal presence.
    """


class SnapshotFile(TableBase):
    """Persist one affected File and its absolute capture-directory path.

    Publication creates one row for every affected File and stores its absolute
    capture-directory path. File lookup joins it to the optional side rows and
    returns `SnapshotFileRecord`.

    Side presence, repository paths, and digests live in `SnapshotFileLeft` and
    `SnapshotFileRight`. This row has no manifest order, display name, line
    count, or final loading policy.
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
            column("error").is_(None) | (func.length(column("error")) > 0),
            name="ck_snapshot_file_error",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    """Globally unique 32-character lowercase hexadecimal captured-File key.

    Side rows, overrides, and placements reference it rather than repository path.
    """

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"),
        nullable=False,
    )
    """Snapshot containing this affected File.

    File lookup and placement joins must remain inside this captured code universe.
    """

    path: Mapped[str] = mapped_column(String, nullable=False)
    """Unique absolute path of the File's immutable capture directory.

    Persistence uses it to recover bytes; it is not the repository-relative identity.
    """

    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """Whether the backend reported this File as tracked input.

    The value is retained from capture and is not inferred from later Git state.
    """

    change_type: Mapped[str] = mapped_column(String, nullable=False)
    """Backend classification of the captured side-path relationship.

    Consumers interpret it with nullable side rows rather than deriving it anew.
    """

    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    """Exact non-empty capture failure, or `None` for valid captured bytes.

    A failure forbids treating side digests or renderer content as available.
    """


Index("ix_snapshot_file_snapshot_id", SnapshotFile.snapshot_id)
Index("ix_snapshot_room", Snapshot.room_id)


def _captured_side_constraints(side: str) -> tuple[CheckConstraint, ...]:
    """Build the identical validity constraints of one captured-side table.

    Both side tables persist a repository-relative path (non-empty, relative,
    free of `..` traversal) and a 32-byte content digest; only the constraint
    names differ by side. One builder keeps the two tables' contracts from
    drifting apart.

    # Returns

    - First, a named constraint requiring a non-empty relative repository path
      without `..` traversal.
    - Second, a named constraint requiring the content digest to be 32 bytes.
      Both side tables install the constraints under side-specific names.
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

    Publication inserts this row when the captured File has a left side. Reads
    expose it as `SnapshotFileSideRecord`.

    Absence of the row means the side is absent. The relation never uses
    nullable path or digest columns to represent that state.
    """

    __tablename__ = "snapshot_file_left"
    __table_args__ = _captured_side_constraints("left")

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    """Captured File whose left side this one-to-one row describes.

    Its primary key enforces at most one left source per captured File.
    """

    repository_path: Mapped[str] = mapped_column(String, nullable=False)
    """Non-empty repository-relative path of the captured left side.

    It is the public File-side identity and is never replaced by the capture path.
    """

    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """SHA-256 digest of the captured left-side bytes.

    Snapshot comparison uses it without reopening mutable repository content.
    """


class SnapshotFileRight(TableBase):
    """Persist a captured File's right repository path and content digest.

    Publication inserts this row when the captured File has a right side. Reads
    expose it as `SnapshotFileSideRecord`.

    Absence of the row means the side is absent. The relation never uses
    nullable path or digest columns to represent that state.
    """

    __tablename__ = "snapshot_file_right"
    __table_args__ = _captured_side_constraints("right")

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    """Captured File whose right side this one-to-one row describes.

    Its primary key enforces at most one right source per captured File.
    """

    repository_path: Mapped[str] = mapped_column(String, nullable=False)
    """Non-empty repository-relative path of the captured right side.

    It remains nullable only through absence of the whole side row.
    """

    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """SHA-256 digest of the captured right-side bytes.

    It identifies immutable captured bytes rather than current worktree contents.
    """


class SnapshotFileSymlink(TableBase):
    """Persist the authoritative sidecars for one symbolic-link File side.

    Publication inserts one row exactly when a captured left or right side is a
    symbolic link. Readers use the stored absolute paths directly and verify
    both byte sequences against their digests before parsing link facts.

    The row does not duplicate the link chain, terminal diagnosis, or final
    repository target path held by the authenticated metadata sidecar. Absence
    means the corresponding captured side is an ordinary File.
    """

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
    """Captured File containing this link side.

    Together with `side`, it permits at most one authoritative link record for
    either side while allowing a File whose old and new sides are both links.
    """

    side: Mapped[str] = mapped_column(String, primary_key=True)
    """Captured side named `left` or `right`.

    `RoomStore` validates this Python vocabulary on publication and hydration;
    it is not duplicated as an enumerated SQL check.
    """

    metadata_path: Mapped[str] = mapped_column(String, nullable=False)
    """Unique absolute path to the immutable JSON metadata sidecar.

    Readers use this value directly; they never derive it from the raw side.
    """

    metadata_hash: Mapped[bytes] = mapped_column(
        LargeBinary(32), nullable=False
    )
    """SHA-256 digest of the exact metadata-sidecar bytes.

    Authentication happens before JSON parsing so the database, not a nearby
    filename, identifies both the capture and its immutable content.
    """

    target_capture_path: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    """Unique absolute path to reached target bytes, or `None` after damage.

    Its presence must equal `target_hash`; this physical path is distinct from
    the final repository target path described inside the metadata sidecar.
    """

    target_hash: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary(32), nullable=True
    )
    """SHA-256 digest of reached target bytes, or `None` after damage.

    The target path and digest form one optional fact and never vary
    independently.
    """


class SnapshotFileLazyReason(TableBase):
    """Persist an explicit loading-policy override captured for one File.

    Publication inserts this row only when the backend supplied a reason.
    Manifest and File lookup read it as an override to combine with derived
    loading policy.

    It stores neither rendered output nor the optional source metadata content.
    """

    __tablename__ = "snapshot_file_lazy_reason"

    file_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot_file.id"),
        primary_key=True,
    )
    """Captured File whose explicit lazy override this row stores.

    One-to-one identity means absence of the row, not an empty reason, is ordinary loading.
    """

    reason: Mapped[str] = mapped_column(String, nullable=False)
    """Backend-supplied reason to defer File rendering.

    The server presents it as capture metadata and does not reinterpret the text.
    """


class SnapshotFileLazyReasonContent(TableBase):
    """Persist complete source metadata supplied with a lazy override.

    Preset capture inserts this row when the metadata text itself participates
    in Snapshot identity. Recapture reads it while comparing retained state.

    Backend overrides without source content have no row. This relation does
    not store the lazy reason or any File bytes.
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
    """Lazy-override row whose source metadata participates in identity.

    The foreign-key primary key permits at most one complete metadata value per override.
    """

    content: Mapped[str] = mapped_column(String, nullable=False)
    """Complete non-empty metadata text retained from the preset fixture.

    Preset reads return it unchanged; it is not parsed into implicit loading behavior.
    """


class ReviewThread(TableBase):
    """Persist one logical Thread and the Snapshot where it originated.

    Thread creation inserts this identity before its origin placement and first
    action. Later Snapshots add placements that reuse the same `thread_id`.

    `origin_snapshot_id` never changes. The row does not hold current placement,
    lifecycle, attention, or Comments; those come from placements and actions.
    """

    __tablename__ = "review_thread"
    __table_args__ = (
        CheckConstraint(
            (func.length(column("thread_id")) == 32)
            & column("thread_id").op("NOT GLOB")("*[^0-9a-f]*"),
            name="ck_review_thread_id",
        ),
    )

    thread_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    """Globally unique logical discussion id shared by all its placements.

    Recapture adds placement rows while every authored action keeps this identity.
    """

    origin_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"), nullable=False
    )
    """Immutable Snapshot in which the Thread was created.

    It fixes origin code even when later selected-Snapshot placement moves or disappears.
    """


Index("ix_review_thread_origin_snapshot", ReviewThread.origin_snapshot_id)


class ReviewThreadPlacement(TableBase):
    """Persist one immutable code placement for a Thread in one Snapshot.

    Review derivation writes one row for each Thread represented in a Snapshot;
    reads reconstruct `ReviewThreadRecord` and validate its tagged shape.

    The target discriminator and outdated reason define a closed set of valid
    placement shapes, which `ReviewThreadRecord` validates at both read and
    write boundaries. Private coordinates never cross the persistence/review
    boundary.
    """

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
    """Logical Thread id, paired with `snapshot_id` as placement identity.

    The pair permits one current landing for each Thread in each captured state.
    """

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshot.id"), primary_key=True
    )
    """Exact captured code universe in which this placement applies.

    Every File, bay, side, and range coordinate must belong to this Snapshot.
    """

    snapshot_file_id: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    """File containing the landing, or `None` when absent or unreadable.

    The composite foreign key guarantees that a referenced File belongs to
    `snapshot_id`.
    """

    target_kind: Mapped[
        Optional[Literal["range", "bay-start", "file-start"]]
    ] = mapped_column(String, nullable=True)
    """Shape of a located target, or `None` for an unlocated Thread.

    A `range` carries a bay, side, and inclusive line span; `bay-start` omits
    the span; `file-start` carries only the side.
    """

    bay_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    """Public bay identity for range and bay-start placements.

    It is absent for File-start or unlocated shapes and meaningful with File and side.
    """

    side: Mapped[Optional[Literal["left", "right"]]] = mapped_column(
        String, nullable=True
    )
    """Captured side for every placement that still reaches a File.

    It must be absent only when the placement has no File coordinate at all.
    """

    start_line: Mapped[Optional[int]] = mapped_column(nullable=True)
    """Positive one-based inclusive start of a range placement.

    Non-range placement shapes must leave it null rather than inventing a line.
    """

    end_line: Mapped[Optional[int]] = mapped_column(nullable=True)
    """One-based inclusive range end, no earlier than `start_line`.

    It arrives and disappears with the start as one range-coordinate invariant.
    """

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
    """Why the origin did not land unchanged, or `None` when current.

    The reason also distinguishes the two unlocated shapes: `file_missing`
    means the path pair is absent, while `file_unreadable` means capture failed.
    """

    private_locator: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    """Opaque structural coordinates retained only for a current text range.

    Review code interprets these bytes; persistence and public responses do not.
    """


Index("ix_review_thread_placement_snapshot", ReviewThreadPlacement.snapshot_id)


class ReviewAction(TableBase):
    """Persist one append-only authored operation and its resulting state.

    Review writes append these rows through `RoomStore`; discussion reads
    reduce them in `sequence` order to recover current state.

    `kind` controls the nullable Comment fields. Each row records lifecycle and
    attention after the operation, so no mutable current-state row exists.
    """

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

    operation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    """Globally unique internal id of this accepted operation.

    Every authored operation must supply a fresh value. Reuse conflicts with the
    primary key and never means retry, replay, or successful deduplication.
    """

    activity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    """Durable increasing order of authored activity across the database.

    Room feeds use it as a cursor independently of per-Thread sequence.
    """

    thread_id: Mapped[str] = mapped_column(String(32), nullable=False)
    """Logical Thread changed by this operation.

    Together with `snapshot_id`, it selects the placement against which validation ran.
    """

    snapshot_id: Mapped[str] = mapped_column(String(32), nullable=False)
    """Thread placement against which the operation was accepted.

    It records the exact code universe seen by the author, not the latest recapture.
    """

    sequence: Mapped[int] = mapped_column(nullable=False)
    """Zero-based append order within `thread_id`.

    It is contiguous and determines discussion revision independently of global activity.
    """

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
    """Operation variant that determines the nullable Comment fields.

    Validation uses it to require or forbid comment id, revision, and body as one shape.
    """

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"), nullable=False
    )
    """Durable Profile attributed as the operation's author.

    Reads join its current display name while preserving this stable identity.
    """

    comment_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    """Created or changed Comment id when this operation addresses one.

    Bare lifecycle actions leave it null rather than manufacturing a Comment.
    """

    expected_revision: Mapped[Optional[int]] = mapped_column(nullable=True)
    """Comment revision required by edit and deletion operations.

    It is an optimistic concurrency gate and is null for creation or lifecycle-only actions.
    """

    body: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    """Non-empty authored Comment body when the operation carries one.

    Deletion and bare lifecycle variants leave it null; whitespace-only input is invalid.
    """

    created_at: Mapped[str] = mapped_column(String, nullable=False)
    """Non-empty authored-action timestamp in the public serialized form.

    Persistence retains the accepted event time rather than recomputing it on reads.
    """

    status_after: Mapped[Literal["open", "resolved", "deleted"]] = (
        mapped_column(String, nullable=False)
    )
    """Authoritative Thread lifecycle state after this operation.

    Folding actions through this value reconstructs state at any inclusive activity boundary.
    """

    attention_after: Mapped[Literal["author", "reviewer", "both", "none"]] = (
        mapped_column(String, nullable=False)
    )
    """Authoritative attention state after this operation.

    It records the resulting role demand, not merely the acting Profile's role.
    """


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

    RoomLord constructs this value after applying one Tab's correspondence law,
    then passes it to `RoomStore` for exact Room lookup and creation.

    `correspondence_key` stays opaque to persistence. The value does not address
    a Snapshot or expose the logical inputs from which the key was derived.
    """

    mark_id: Optional[int]
    """Mark id for repository Rooms, or `None` for preset Rooms.

    Consumers interpret absence with `tab`; it never means an unknown repository Room.
    """

    tab: str
    """Persisted HUD Tab category.

    It selects the correspondence-key law and the recapture inputs retained by the Room.
    """

    correspondence_key: bytes
    """Opaque Room key compared for equality by SQLite.

    Callers must not decode it without the corresponding Tab contract.
    """


@dataclass(frozen=True)
class SnapshotRegularFileSideRecord:
    """Expose the repository identity and digest of one present File side.

    Capture constructs this record for publication; `RoomStore` returns the
    same shape on reads. Callers use the repository path for exact side identity
    and the digest to authenticate captured bytes.

    Absence is represented by an absent side record on the File, never by
    nullable fields here. This value does not contain the captured bytes.
    """

    repository_path: str
    """Repository-relative path used to address this side.

    It is the public File identity; physical capture lookup uses the enclosing File path.
    """

    content_hash: bytes
    """SHA-256 digest of this side's captured contents.

    Comparisons use it as immutable byte identity without reopening the repository.
    """

    def __post_init__(self) -> None:
        """Reject incomplete present-side identity or authentication facts.

        # Failures

        - Invalid repository path or content digest raises `AssertionError`.
        """
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
    """Locate and authenticate the terminal non-link bytes of a captured walk.

    A stopped walk has no target record. A reached target always supplies both
    its physical path and digest, even when its byte contents are empty.
    """

    path: str
    """Absolute immutable capture path containing the reached target bytes."""

    content_hash: bytes
    """SHA-256 digest of those exact captured bytes."""

    def __post_init__(self) -> None:
        """Reject an incomplete authenticated target.

        # Failures

        - A relative path or digest not exactly 32 bytes raises `AssertionError`.
        """
        assert Path(self.path).is_absolute(), (
            "Snapshot link target capture path must be absolute"
        )
        assert len(self.content_hash) == 32, (
            "Snapshot link target hash must have length 32"
        )


@dataclass(frozen=True)
class SnapshotFileSymlinkRecord:
    """Authenticate one symbolic-link side's metadata and optional reached bytes.

    The store supplies this descriptor only inside a present link-side variant.
    Callers authenticate its metadata before interpreting the chain, diagnosis,
    or repository target path encoded there.
    """

    metadata_path: str
    """Absolute path to the immutable JSON metadata sidecar."""

    metadata_hash: bytes
    """SHA-256 digest of the exact metadata bytes."""

    target: Option[SnapshotLinkTargetRecord]
    """Complete reached-target capture, absent when traversal stopped."""

    def __post_init__(self) -> None:
        """Reject a descriptor that cannot authenticate its metadata.

        # Failures

        - A relative metadata path or non-SHA-256 digest raises `AssertionError`.
        """
        assert Path(self.metadata_path).is_absolute(), (
            "Snapshot link metadata path must be absolute"
        )
        assert len(self.metadata_hash) == 32, (
            "Snapshot link metadata hash must have length 32"
        )


@dataclass(frozen=True)
class SnapshotSymlinkFileSideRecord:
    """Retain the raw outer link side together with its required descriptor.

    The raw bytes have their own path identity and digest. Reached target bytes
    are authenticated separately by `link`; they never replace the outer bytes.
    """

    repository_path: str
    """Repository-relative identity of this present outer link side."""

    content_hash: bytes
    """SHA-256 digest of the captured raw outer link bytes."""

    link: SnapshotFileSymlinkRecord
    """Required metadata and optional reached-target authentication facts."""

    def __post_init__(self) -> None:
        """Reject incomplete present-side identity or authentication facts.

        # Failures

        - Invalid repository path, content digest, or required link descriptor
          raises `AssertionError`.
        """
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
"""One present captured side, either regular bytes or a link with its descriptor.

File-level `Nothing` represents real absence; neither variant has nullable side
identity or content digest. Inspect the variant before reading link metadata.
"""


@dataclass(frozen=True)
class SnapshotFileRecord:
    """Immutable relational facts for one affected File.

    `RoomStore` returns this immutable record to Room for manifest, File lookup,
    delta, and review operations. Publication supplies the same record with
    regular or symbolic-link side variants; a link descriptor belongs inside
    its present side.

    At least one captured side is present. The record has no display name,
    rendered bays, or final lazy reason.
    """

    id: str
    """Opaque File id.

    Placements and loading overrides use it instead of either nullable side path.
    """

    snapshot_id: str
    """Snapshot containing this File.

    Callers must not combine its side records with another Snapshot's placement.
    """

    path: str
    """Absolute filesystem path of this File's capture directory.

    It locates immutable captured bytes and is never returned as repository identity.
    """

    tracked: bool
    """Whether the selected VCS tracks this File at capture time.

    *Implementation detail:* preset fixtures use True because VCS tracking
    does not apply to them.
    """

    change_type: str
    """Source classification retained without deriving renderer output.

    Consumers combine it with side presence and errors when building presentation.
    """

    error: Optional[str]
    """Persisted capture failure, or `None` when physical contents are valid.

    A non-null value makes the File unreadable even if side path metadata is retained.
    """

    left: Option[SnapshotFileSideRecord]
    """Captured left side, when it exists.

    `Nothing` means that side is absent, which differs from present empty captured bytes.
    """

    right: Option[SnapshotFileSideRecord]
    """Captured right side, when it exists.

    Consumers preserve absence instead of substituting the other side or an empty file.
    """

    def __post_init__(self) -> None:
        """Reject a File without any captured side.

        # Failures

        - No present side raises `AssertionError`.
        """
        assert Some.present(self.left) or Some.present(self.right), (
            "Snapshot File must have at least one captured side"
        )


@dataclass(frozen=True)
class SnapshotRecord:
    """Complete relational input required to publish one Snapshot.

    Snapshot capture constructs this value after publishing the immutable
    directory and passes it once to `RoomStore.publish_snapshot`.

    It contains Snapshot metadata and the complete unordered File set. It does
    not select the Snapshot for any caller or expose staging state.
    """

    id: str
    """Opaque Snapshot id.

    Follow-up reads use it to address this exact immutable capture.
    """

    content_hash: bytes
    """Digest identifying equal captured content inside one Room.

    It supports equal-capture reuse but is not a globally addressable Snapshot key.
    """

    meta: SnapshotMetaRecord
    """Labels and aggregate line counts captured with this Snapshot.

    Metadata shares the Snapshot lifetime and is not recalculated from `files` on reads.
    """

    files: tuple[SnapshotFileRecord, ...]
    """Complete set of captured Files, with no implied order.

    Callers that need backend or manifest order must establish it outside persistence.
    """


@dataclass(frozen=True)
class SnapshotMetaRecord:
    """One Snapshot's labels and backend-supplied aggregate line counts.

    Capture constructs this record from backend labels and totals; Room returns
    it from Snapshot metadata reads.

    Both counts are present together or absent together. They describe the
    Snapshot as a whole and never encode renderer-derived alignment.
    """

    left_label: str
    """Human-facing left label captured with the Snapshot.

    It remains paired with the immutable capture rather than following a later ref name.
    """

    right_label: str
    """Human-facing right label captured with the Snapshot.

    Consumers present it verbatim and do not treat it as repository identity.
    """

    added_lines: Optional[int]
    """Aggregate added lines supplied by the backend, when authoritative.

    `None` means the backend did not provide a total, not that no lines were added.
    """

    removed_lines: Optional[int]
    """Aggregate removed lines supplied by the backend, when authoritative.

    Consumers preserve `None` rather than substituting zero for an unknown total.
    """


@dataclass(frozen=True)
class SnapshotFileLoadRecord:
    """One exact File lookup with its explicit lazy override.

    `RoomStore.snapshot_file_for_repo_paths` returns this after validating the
    Room, Snapshot, and exact nullable path pair. Room uses it to expose physical
    handles and metadata.

    It does not load bytes, derive a final lazy reason, or soften a failed
    capture into usable content.
    """

    file: SnapshotFileRecord
    """The repository-path-addressed File returned by lookup.

    Its matching side contains the exact queried path; the record may still be unreadable.
    """

    lazy_reason: Optional[str]
    """Explicit loading override for this File, when one was captured.

    `None` means ordinary loading policy rather than an empty or inferred reason.
    """


@dataclass(frozen=True)
class RangePlacement:
    """A Thread placed on a selected line range inside one composed bay.

    This is the shape every newly created Thread takes. Its public coordinate
    comes from composition and its inclusive lines remain local to that bay.

    Private reattachment bytes belong to the enclosing `ReviewThreadRecord`,
    separate from the public line coordinate.
    """

    thread_id: str
    """Global logical Thread identity to which this immutable landing belongs.

    The same id may have one placement per Snapshot but never two placements in
    the same Snapshot.
    """

    snapshot_id: str
    """Immutable Snapshot whose composed content contains this landing.

    It must agree with the referenced File and is the code universe used for
    all public placement reads.
    """

    snapshot_file_id: str
    """Opaque File id within `snapshot_id` containing the landing bay.

    Review authenticates the referenced File and checks that its nullable path
    pair equals the origin File pair before exposing a placement view.
    """

    bay_key: str
    """Composition-issued bay key containing the current line range.

    A matched range must retain the immutable origin bay key; derivation does
    not relocate a region across bays.
    """

    side: Literal["left", "right"]
    """Captured File side containing this bay-local range.

    It must equal the origin side. Loss of that side produces another placement
    variant instead of changing this value.
    """

    start_line: int
    """Positive one-based first included line in the current bay-side source.

    For an unchanged match it preserves the origin's offset within the matched
    region; for changed content it is the candidate's first line.
    """

    end_line: int
    """One-based last included line in the current bay-side source.

    It is no earlier than `start_line`; changed-region placement may narrow
    the range to that one structural landing line.
    """

    outdated_reason: Optional[Literal["region_changed"]]
    """Whether the unique structural candidate changed its source bytes.

    `None` means both structure and region digest matched exactly. No other
    outdated reason is valid for a placement that still has a range.
    """

    def __post_init__(self) -> None:
        """Reject an invalid located placement before it crosses persistence.

        # Failures

        - Invalid File identity, side, bay, range, or reason raises `AssertionError`.
        """
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
    """A Thread placed at the start of one composed bay, its region lost.

    `region_not_found` means the bay the origin named still composes in this
    Snapshot's File but the origin region inside it matched no candidate or
    matched ambiguously, so `bay_key` is the origin's own bay.
    `bay_not_found` means the origin's bay is gone entirely, and `bay_key` is
    the File's first composed bay carrying `side`. Origins never take this shape.

    *Implementation detail:* derivation selects and persists that landing while
    composition is available; later reads use the stored bay key.
    """

    thread_id: str
    """Global logical Thread identity receiving this bay-start landing.

    It relates the immutable placement to its unique origin and action history.
    """

    snapshot_id: str
    """Immutable Snapshot whose composition supplied the landing bay.

    Publication persists this choice before the Snapshot becomes visible.
    """

    snapshot_file_id: str
    """Opaque selected-Snapshot File id containing the landing bay.

    The File must retain the origin's exact nullable path pair and selected side.
    """

    bay_key: str
    """Composition-issued bay key whose start is the stored landing.

    It is the origin bay for region loss or the first replacement bay that
    contains the origin side for bay loss, as distinguished by `outdated_reason`.
    """

    side: Literal["left", "right"]
    """Origin-selected side on which the landing bay still composes.

    Derivation never changes sides to obtain a usable bay.
    """

    outdated_reason: Literal["region_not_found", "bay_not_found"]
    """Exact loss that reduced navigation to a bay start.

    `region_not_found` retains the origin bay; `bay_not_found` requires
    `bay_key` to hold the replacement chosen during derivation.
    """

    def __post_init__(self) -> None:
        """Reject an invalid located placement before it crosses persistence.

        # Failures

        - Invalid File identity, side, bay, or loss reason raises `AssertionError`.
        """
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
    """A Thread placed at File start, with no bay coordinate to land on.

    It has no bay and no line range, so callers present it in History without
    bay navigation. A reason of None marks a retained historical File-level
    origin. These are the only origins of this shape. Every placement derived
    from such an origin also has this shape. `bay_not_found` marks a placement
    derived from a range origin whose File composes no bay carrying the side.
    """

    thread_id: str
    """Global logical Thread identity receiving this File-side landing.

    It joins the placement to its immutable origin and append-only discussion.
    """

    snapshot_id: str
    """Immutable Snapshot in which only the File-side coordinate remains.

    The placement is not reusable for another Snapshot even when paths match.
    """

    snapshot_file_id: str
    """Opaque File id validated within `snapshot_id` for discovery.

    It identifies the exact path pair but offers no bay or line coordinate.
    """

    side: Literal["left", "right"]
    """Origin-selected side retained for the historical File landing.

    The side exists on the exact File pair, but no composed bay on it is valid
    for navigation in this placement.
    """

    outdated_reason: Optional[Literal["bay_not_found"]]
    """Why only a File-side landing remains, when the placement is outdated.

    `None` is reserved for retained historical File-level origins;
    `bay_not_found` means a range origin's selected side composes no bay.
    """

    def __post_init__(self) -> None:
        """Reject an invalid located placement before it crosses persistence.

        # Failures

        - Invalid File identity, side, or loss reason raises `AssertionError`.
        """
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
    """A Thread with no code location, because its exact File pair is absent.

    It references no Snapshot File, and its public outdated reason is always
    `file_missing`, so neither is carried as a field.
    """

    thread_id: str
    """Global logical Thread identity whose origin pair has no current File.

    Discussion history remains addressable by this id even though code
    navigation has no selected-Snapshot target.
    """

    snapshot_id: str
    """Immutable Snapshot in which the stored placement declares File absence.

    Review verifies that declaration against captured File membership before
    returning its public placement view; the store validates the row shape.
    """

    def __post_init__(self) -> None:
        """Reject a landing with no Thread or Snapshot identity.

        # Failures

        - Missing identity values raise `AssertionError`.
        """
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"


@dataclass(frozen=True)
class FileUnreadablePlacement:
    """A Thread without a code location because its File capture failed.

    The source listed the exact File pair, but its capture error makes the File
    unavailable for placement and excerpts even if another side succeeded.
    No Snapshot File reference or code coordinate is carried here. Its public
    reason is always `file_unreadable`, unlike `FileMissingPlacement`, which
    declares that the exact pair is absent.
    """

    thread_id: str
    """Global logical Thread identity whose matching File is unreadable.

    The discussion remains intact while review withholds generated capture-error
    content from placement and excerpts.
    """

    snapshot_id: str
    """Immutable Snapshot in which the origin pair exists but capture failed.

    This distinguishes the placement from true File absence without storing a
    reference to placeholder bytes.
    """

    def __post_init__(self) -> None:
        """Reject a landing with no Thread or Snapshot identity.

        # Failures

        - Missing identity values raise `AssertionError`.
        """
        assert isinstance(self.thread_id, str) and isinstance(
            self.snapshot_id, str
        ), "placement requires Thread and Snapshot identities"


Placement = (
    RangePlacement
    | BayStartPlacement
    | FileStartPlacement
    | FileMissingPlacement
    | FileUnreadablePlacement
)
"""One Thread's immutable location in one Snapshot, in the shape review needs.

- `RangePlacement` locates a usable line range.
- `BayStartPlacement` locates a bay whose original region was lost.
- `FileStartPlacement` retains a File-side landing without a bay.
- `FileMissingPlacement` states that the exact File pair is absent.
- `FileUnreadablePlacement` states that capture could not retain the File.

`RoomStore` validates SQL rows into these variants before returning them.
A `ReviewThreadRecord` pairs a placement with its origin label and optional
private reattachment bytes; SQL nullable columns never become caller fields.
"""


@dataclass(frozen=True)
class ReviewThreadRecord:
    """Pair one immutable placement with its origin metadata.

    Derivation supplies these records for writes. `RoomStore` returns the same
    validated variants on reads. Consumers inspect `placement` directly; private
    reattachment bytes remain opaque outside review derivation.
    """

    placement: Placement
    """Exactly one range, bay start, File start, missing, or unreadable landing."""

    is_origin: bool
    """Whether this Thread/Snapshot pair is the unique creation placement."""

    private_locator: bytes | None
    """Opaque coordinates for a current range origin, otherwise absent."""

    def __post_init__(self) -> None:
        """Reject private coordinates that do not describe a current range.

        # Failures

        - Coordinates on any other placement raise `AssertionError`.
        """
        assert isinstance(
            self.placement,
            (
                RangePlacement,
                BayStartPlacement,
                FileStartPlacement,
                FileMissingPlacement,
                FileUnreadablePlacement,
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
    """Pair a Comment identity with the text supplied by one accepted action.

    Creation, editing and optional resolve/reopen Comments carry this whole
    value. Attribution comes from `ReviewActionInput.profile_id`; this value
    alone does not state whether the Comment is new.
    """

    id: str
    """Required identity of the new or edited Comment."""

    body: str
    """Nonempty authored text, retained exactly as accepted."""

    def __post_init__(self) -> None:
        """Reject missing identity or text at the typed write boundary.

        # Failures

        - Missing identity or empty/non-string text raises `AssertionError`.
        """
        assert isinstance(self.id, str), "authored Comment requires an id"
        assert isinstance(self.body, str) and len(self.body) > 0, (
            "authored Comment requires a body"
        )


@dataclass(frozen=True)
class CommentCreatedAction:
    """Create the first Thread Comment or a later reply."""

    kind: Literal["thread-created", "comment-created"]
    """Thread creation at sequence zero or a reply to an existing Thread."""

    comment: AuthoredComment
    """Required newly authored Comment, whose identity must be unused."""

    def __post_init__(self) -> None:
        """Reject an unknown creation tag or absent Comment.

        # Failures

        - Invalid tag or Comment value raises `AssertionError`.
        """
        assert self.kind in {"thread-created", "comment-created"}
        assert isinstance(self.comment, AuthoredComment)


@dataclass(frozen=True)
class CommentEditedAction:
    """Replace an existing Comment's text at its exact prior revision."""

    comment: AuthoredComment
    """Existing Comment identity and required replacement body."""

    expected_revision: int
    """Required prior revision checked against complete action history."""

    kind: Literal["comment-edited"] = "comment-edited"
    """Fixed discriminator for a Comment text edit."""

    def __post_init__(self) -> None:
        """Reject absent Comment/revision or an invalid edit tag.

        # Failures

        - Invalid typed edit fields raise `AssertionError`.
        """
        assert isinstance(self.comment, AuthoredComment)
        assert isinstance(self.expected_revision, int)
        assert self.kind == "comment-edited"


@dataclass(frozen=True)
class CommentDeletedAction:
    """Tombstone an existing Comment at its exact prior revision."""

    comment_id: str
    """Required existing Comment identity; no authored body is carried."""

    expected_revision: int
    """Required prior revision checked against complete action history."""

    kind: Literal["comment-deleted"] = "comment-deleted"
    """Fixed discriminator for Comment deletion."""

    def __post_init__(self) -> None:
        """Reject absent identity/revision or an invalid deletion tag.

        # Failures

        - Invalid typed deletion fields raise `AssertionError`.
        """
        assert isinstance(self.comment_id, str)
        assert isinstance(self.expected_revision, int)
        assert self.kind == "comment-deleted"


@dataclass(frozen=True)
class ThreadStateAction:
    """Resolve or reopen a Thread, optionally authoring one whole Comment."""

    kind: Literal["thread-resolved", "thread-reopened"]
    """Lifecycle transition whose legality is checked against prior state."""

    comment: AuthoredComment | None
    """Optional new Comment accompanying this transition, absent for a bare transition."""

    def __post_init__(self) -> None:
        """Reject an unknown transition or incomplete optional Comment.

        # Failures

        - Invalid transition tag or Comment value raises `AssertionError`.
        """
        assert self.kind in {"thread-resolved", "thread-reopened"}
        assert self.comment is None or isinstance(self.comment, AuthoredComment)


@dataclass(frozen=True)
class ThreadDeletedAction:
    """Delete a Thread without changing or creating a Comment."""

    kind: Literal["thread-deleted"] = "thread-deleted"
    """Fixed discriminator for the terminal Thread transition."""

    def __post_init__(self) -> None:
        """Reject a tag that does not identify Thread deletion.

        # Failures

        - An invalid tag raises `AssertionError`.
        """
        assert self.kind == "thread-deleted"


ReviewActionPayload = (
    CommentCreatedAction
    | CommentEditedAction
    | CommentDeletedAction
    | ThreadStateAction
    | ThreadDeletedAction
)
"""One accepted action's required, kind-specific fields.

Creation/edit carries a whole authored Comment; Comment deletion carries its
identity and prior revision; resolve/reopen carries an optional whole Comment;
Thread deletion carries no Comment data. SQL nullability is confined to stores.
"""


@dataclass(frozen=True)
class ReviewActionInput:
    """Supply accepted operation facts for one atomic action append.

    Review validates history, authorship and transitions before constructing an
    input. The store assigns global activity order in its write transaction.
    """

    operation_id: str
    """Fresh unique operation identity; duplicates conflict rather than replay."""

    thread_id: str
    """Logical discussion addressed by this operation."""

    snapshot_id: str
    """Exact placement Snapshot against which the action was accepted."""

    sequence: int
    """Contiguous zero-based action order within this Thread."""

    profile_id: int
    """Required positive Profile identity attributed as the author."""

    payload: ReviewActionPayload
    """Validated variant containing exactly the operation's Comment fields."""

    created_at: str
    """Accepted authored timestamp, retained unchanged on later reads."""

    status_after: Literal["open", "resolved", "deleted"]
    """Authoritative lifecycle outcome after this action."""

    attention_after: Literal["author", "reviewer", "both", "none"]
    """Authoritative role attention outcome after this action."""

    def __post_init__(self) -> None:
        """Reject an input with no relational author or invalid outcome.

        # Failures

        - Invalid Profile, payload, lifecycle or attention raises `AssertionError`.
        """
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
    """Return one persisted action together with its assigned global cursor.

    `RoomStore` returns records in per-Thread sequence or global activity order,
    as specified by each read. Review folds `action`; continuation uses the
    required cursor. This record is not an insertion input.
    """

    action: ReviewActionInput
    """Complete accepted operation facts, including its tagged payload."""

    activity_id: int
    """Required positive database-assigned global action order."""

    def __post_init__(self) -> None:
        """Reject a persisted output without its assigned activity order.

        # Failures

        - A missing or nonpositive cursor raises `AssertionError`.
        """
        assert isinstance(self.action, ReviewActionInput), (
            "persisted action requires accepted operation facts"
        )
        assert isinstance(self.activity_id, int) and self.activity_id > 0


@dataclass(frozen=True)
class ReviewThreadsRecord:
    """Bulk persistence result for one bounded Snapshot or Room Thread read.

    `RoomStore` returns this value to one bounded review read. The review layer
    combines each selected placement, or origin used for a missing displayed
    placement, with its authored actions and Profile attribution to build public
    Thread views.

    `total_threads` is measured before page bounds. This record contains raw
    relational facts, not materialized discussion state or excerpts.
    """

    threads: tuple[ReviewThreadRecord, ...]
    """Selected placements or explicit origin rows in requested page order.

    `is_origin` distinguishes the Room-scope case whose displayed placement does
    not exist. Each entry represents one logical Thread exactly once.
    """

    origins: tuple[ReviewThreadRecord, ...]
    """One origin placement corresponding positionally to every `threads` row.

    Equal lengths and indexes let materialization combine current landing with immutable origin.
    """

    actions: tuple[ReviewActionRecord, ...]
    """Pivot-bounded actions for the selected Threads, in Thread sequence order.

    No action newer than the inclusive activity boundary may appear here.
    """

    profiles: tuple[UserProfileRecord, ...]
    """Every Profile referenced by `actions`, with no active-user meaning.

    The collection supports attribution joins and must not be treated as selectable Profiles.
    """

    total_threads: int
    """Number of matching Threads before page bounds are applied.

    Consumers use it for pagination independently of the current page length.
    """


class _ReviewThreadInsertValues(TypedDict):
    """Name the exact columns used to insert one Thread placement.

    `RoomStore._review_thread_values` converts a validated
    `ReviewThreadRecord` to this private shape immediately before persistence.

    It exists for typed SQL construction only. Callers outside `RoomStore` must
    use the record type and cannot depend on this dictionary.
    """

    thread_id: str
    """Stable discussion id shared by every placement of the Thread.

    It is the join key connecting this private row to actions and origin placement.
    """

    snapshot_id: str
    """Snapshot in which this placement was derived.

    Every optional coordinate in the row refers exclusively to this capture.
    """

    snapshot_file_id: str | None
    """Placed File id, absent only when no File carries the Thread.

    Unreadable and absent placements use null rather than a synthetic File identity.
    """

    target_kind: Literal["range", "bay-start", "file-start"] | None
    """Stored placement shape, absent only for an unlocated Thread.

    It governs which bay and range columns must be present in the remaining row.
    """

    bay_key: str | None
    """Placed bay key when the placement reaches a bay.

    File-start and unlocated shapes leave it null rather than inventing a composed bay.
    """

    side: Literal["left", "right"] | None
    """Placed side when the placement reaches a captured File.

    It is required for File-start as well as bay and range placement.
    """

    start_line: int | None
    """Inclusive range start for a range placement.

    Other target kinds leave it null, preserving the distinction from line one.
    """

    end_line: int | None
    """Inclusive range end for a range placement.

    It is present with the start and cannot precede it.
    """

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
    """Why the origin no longer lands exactly, when it does not.

    Exact placements leave it null; degraded materialization maps the stored reason explicitly.
    """

    private_locator: bytes | None
    """Private origin coordinates retained only for a current range.

    They are engine input for future reattachment and never part of the public target.
    """


class _ReviewActionInsertValues(TypedDict):
    """Name the exact columns used to insert one authored review action.

    `RoomStore._review_action_values` converts a validated `ReviewActionInput`
    to this SQL shape. The transaction assigns `activity_id`.

    It is not a public command or read model and must not escape `RoomStore`.
    """

    operation_id: str
    """Fresh unique identity of the authored operation.

    The insert boundary rejects every reuse; this column does not provide an
    idempotent retry protocol.
    """

    thread_id: str
    """Discussion to which the action belongs.

    It must already have a placement in the action's Snapshot.
    """

    snapshot_id: str
    """Snapshot against which the action was authored.

    The write validates this exact placement rather than silently moving to a newer capture.
    """

    sequence: int
    """Zero-based action order within the Thread.

    It must follow the prior accepted sequence without gaps or duplication.
    """

    kind: Literal[
        "thread-created",
        "comment-created",
        "comment-edited",
        "comment-deleted",
        "thread-resolved",
        "thread-reopened",
        "thread-deleted",
    ]
    """Authored operation variant controlling the nullable fields.

    Storage validation derives each required Comment fact from this discriminator.
    """

    profile_id: int
    """Relational Profile that authored the action.

    It supplies durable attribution while usernames remain mutable display data.
    """

    comment_id: str | None
    """Comment affected or created by a Comment-carrying action.

    Bare lifecycle actions keep it null and therefore do not alter discussion text.
    """

    expected_revision: int | None
    """Revision an edit or deletion requires before it may apply.

    It is absent for creation and lifecycle operations that do not address a Comment revision.
    """

    body: str | None
    """Authored Comment text when this action carries one.

    The operation kind decides presence; persistence rejects empty text rather than omitting it.
    """

    created_at: str
    """Persisted UTC timestamp supplied for the authored action.

    It becomes immutable event history and is returned unchanged by later reads.
    """

    status_after: Literal["open", "resolved", "deleted"]
    """Thread lifecycle immediately after applying the action.

    It lets bounded reads reconstruct state without replaying transition semantics.
    """

    attention_after: Literal["author", "reviewer", "both", "none"]
    """Roles whose attention is required after applying the action.

    The value is authoritative result state, not an instruction inferred by readers.
    """

    activity_id: NotRequired[int]
    """Database-wide order used as a Room activity cursor.

    Insert input omits it; selected rows include the persistence-assigned durable order.
    """


class RoomStore:
    """Provide the complete relational interface for Room persistence.

    # Usage
    Room and RoomLord construct one store from the application engine. They pass
    `RoomIdentity` for Room-scoped reads, publish complete Snapshot records, and
    append review actions through the named operations.

    Review writes supply `ReviewThreadRecord` with a `Placement` variant and
    `ReviewActionInput` with a `ReviewActionPayload`. Reads return those same
    placement variants and `ReviewActionRecord` values: inspect `action.payload`
    for operation facts and `activity_id` for global continuation order. Input
    values have no activity field; the store assigns it transactionally.
    Snapshot reads return optional regular/link side variants whose link
    descriptors are nested in present sides.

    # Boundaries
    Each operation opens a short-lived SQLAlchemy session. The store writes
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
    def _review_thread_values(
        record: ReviewThreadRecord,
    ) -> _ReviewThreadInsertValues:
        """Translate one immutable Thread placement into insert values.

        The mapper selects SQL fields from the validated placement variant and
        keeps query-local origin labels out of persisted columns.
        """
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
        return values

    @staticmethod
    def _review_action_values(
        record: ReviewActionInput,
    ) -> _ReviewActionInsertValues:
        """Translate one immutable authored action into insert values.

        The caller supplies an already validated record; this conversion retains
        its discriminator-controlled nulls for the atomic insert.
        """
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
        """Return the next durable authored-action order in this database.

        The caller holds the write transaction so concurrent actions cannot
        observe or allocate the same Room activity cursor.
        """
        latest: int | None = session.execute(
            select(func.max(ReviewAction.activity_id))
        ).scalar_one()
        return 1 if latest is None else latest + 1

    @staticmethod
    def _thread_record(
        placement: ReviewThreadPlacement,
        is_origin: bool,
    ) -> ReviewThreadRecord:
        """Validate one selected database row as a Thread placement record.

        # Parameters

        - `placement`: ORM row loaded for one immutable Thread/Snapshot pair.
        - `is_origin`: Whether that pair is the Thread's unique origin.

        # Failures

        - Raises `AssertionError` when persisted target kind, side, or outdated
          reason is outside the review model's declared values.
        - Nullable SQL fields that disagree with the selected placement shape,
          or invalid private coordinates, raise `AssertionError`.
        """
        target_kind_value = placement.target_kind
        match target_kind_value:
            case "range" | "bay-start" | "file-start" | None:
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
        if target_kind == "range":
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
        """Validate one selected database row as an authored action record.

        Persisted discriminator and nullable fields are checked together before
        any caller receives the immutable record.

        # Failures

        - Raises `AssertionError` when persisted action kind, lifecycle outcome,
          or attention outcome is outside the declared review values.
        - Invalid Comment field presence, Profile identity, or missing assigned
          activity order raises `AssertionError`.
        """
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
        """Validate selected link rows into authoritative File descriptors.

        File-hydration operations select their rows by the narrowest available
        relational key, then call this boundary so the side vocabulary and
        descriptor construction remain shared. Ordinary sides produce no
        mapping entry; malformed or duplicate relational state raises rather
        than being ignored.

        # Parameters

        - `rows`: Symlink rows selected for the Files being hydrated.

        # Returns

        - Keys pair a selected File id with its validated `left`/`right` side;
          ordinary sides have no key.
        - Values contain the exact physical sidecar paths and stored digests
          for the corresponding symbolic-link side.
        """
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
        """Validate one joined File/side row into the shared record shape.

        # Parameters

        - `file`: Snapshot File row holding pair-wide capture facts.
        - `left_path`: Repository path from its optional left-side row.
        - `left_hash`: Captured-content digest from the same left-side row.
        - `right_path`: Repository path from its optional right-side row.
        - `right_hash`: Captured-content digest from the same right-side row.
        - `left_symlink`: Exact relational sidecars when the left side is a
          symbolic link, otherwise `None`.
        - `right_symlink`: Exact relational sidecars under the same convention.

        Each side's path and digest must have equal presence, and the File must
        retain at least one side. A link descriptor requires its matching raw
        side and remains inside that File's capture directory.
        """
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

    def review_profile(self, profile_id: int) -> Optional[UserProfileRecord]:
        """Return one current Profile identity, or `None` when absent.

        Review writes use this boundary to reject a missing browser author
        before attempting an action whose foreign key could not be satisfied.

        # Usage

        Validate an action's author with this focused lookup before loading or
        appending Thread actions. Treat `None` as a rejected author.

        # Returns

        - A current Profile record when `profile_id` names a persisted author.
        - `None`: No Profile has that id. The caller must reject the review
          write rather than inventing an author.

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

        # Usage

        Use this for Snapshot-keyed follow-up operations that no longer carry
        the original Tab selection. `None` means the key names no published
        Snapshot.

        # Returns

        - The Mark, Tab, and correspondence key that together identify the
          Snapshot's Room.
        - `None`: No published Snapshot has this key. A follow-up operation
          must report the unknown Snapshot instead of selecting another Room.

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

        # Parameters

        - `identity`: Exact Room correspondence within which equality applies.
        - `content_hash`: Capture-domain digest of the complete observed state.

        # Usage

        After capture computes its complete content hash, use this lookup to
        reuse an equal immutable Snapshot before allocating publication paths.

        # Returns

        - The existing Snapshot id with the same content digest in `identity`.
        - `None`: That Room has no equal published Snapshot, so the caller may
          continue with publication of the newly captured state.
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

        # Parameters

        - `identity`: Room that must contain the Snapshot.
        - `snapshot_id`: Globally unique Snapshot key to read.

        # Usage

        Use this when labels, Tab facts, or aggregate counts are sufficient. A
        caller that needs File membership should load `snapshot` instead.

        # Returns

        - The Snapshot's labels and optional aggregate line counts when the key
          belongs to `identity`.
        - `None`: The key is missing or belongs to another Room. The caller
          must not expose metadata from a different correspondence.
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

        # Parameters

        - `identity`: Existing or new Room correspondence receiving the
          Snapshot.
        - `snapshot`: Complete immutable Snapshot record and all File sides.
        - `lazy_reasons`: File-id keyed explicit lazy reasons and their optional
          complete source metadata.
        - `review_threads`: Missing immutable placements derived for this new
          Snapshot before publication.

        # Usage

        Finish capture, validate every File record, and derive any missing review
        placements before calling. The caller must hold the Room publication
        lock until this transaction and the corresponding filesystem move have
        completed.

        # Failures

        - Asserts when Snapshot metadata, File sides, lazy reasons, or review
          placements disagree with `identity` or `snapshot`.
        - Database constraint failures roll back the complete publication.
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
        """Load a complete Snapshot only when it belongs to `identity`.

        The result includes every captured File and side. Missing or cross-Room
        keys return `None`; no substitute Snapshot is selected.

        # Parameters

        - `identity`: Room that limits both the Snapshot and File read.
        - `snapshot_id`: Exact published Snapshot to hydrate.

        # Usage

        Use this when a caller needs the complete immutable File set. Pass the
        Room identity already selected by `RoomLord`; do not infer it from the
        returned data.

        # Returns

        - The complete immutable Snapshot record, including all File records
          and their present sides, when it belongs to `identity`.
        - `None`: The key is missing or belongs to another Room. No substitute
          Snapshot or empty File set is returned.
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
        """Return explicit lazy reasons for Files in one visible Snapshot.

        Missing, cross-Room, and reason-less Snapshots produce no rows. Callers
        that require Snapshot existence validate it through `snapshot` first.

        # Parameters

        - `identity`: Room that limits the lazy-reason read.
        - `snapshot_id`: Exact Snapshot whose File policies are requested.

        # Usage

        Load the Snapshot first when an empty mapping must be distinguished from
        an unknown key. The result contains only explicit overrides.

        # Returns

        - Each key is a File id belonging to the addressed Snapshot.
        - Each value is that File's persisted explicit lazy reason. Files
          without a reason are absent.
        - An empty mapping may also mean the Snapshot is unknown, so callers
          needing that distinction first use `snapshot`.
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
    ) -> Result[
        SnapshotFileLoadRecord, Literal["snapshot-unavailable", "file-absent"]
    ]:
        """Load one repository-path-addressed File from a visible Snapshot.

        `Err("snapshot-unavailable")` means the Snapshot is not visible in this
        Room. `Err("file-absent")` means it has no matching File.

        # Parameters

        - `identity`: Room that must contain the addressed Snapshot.
        - `snapshot_id`: Exact Snapshot to search.
        - `left_path`: Exact left repository path, or `None` for an absent side.
        - `right_path`: Exact right repository path, or `None` for an absent
          side.

        # Usage

        Preserve the error reason: an unknown Snapshot and a missing File are
        different failures. Supply the exact nullable path pair emitted by the
        manifest.

        # Returns

        - `Ok`: The matching File and its lazy reason.
        - `Err`: Whether the Snapshot is unavailable or its File is absent.
        """
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

        # Parameters

        - `identity`: Room that must contain the addressed Snapshot.
        - `snapshot_id`: Exact Snapshot from which Files may be returned.
        - `file_ids`: Snapshot File ids to load as one set; duplicates do not
          duplicate results.

        # Usage

        Batch origin or placement hydration through this method, then compare
        result keys with the ids the higher-level invariant requires.

        # Returns

        - Each key is an input File id found in the visible Snapshot; missing and
          duplicate input ids create no entry.
        - Each value is that exact File record with its retained nullable side
          records. An empty mapping means no requested id was found.
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
                    SnapshotFile.id.in_(set(file_ids)),
                    Room.tab == identity.tab,
                    Room.backend_key == identity.correspondence_key,
                    mark_clause,
                )
            ).all()
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
        return {
            row[0].id: self._file_record(
                *row,
                left_symlink=symlinks.get((row[0].id, "left")),
                right_symlink=symlinks.get((row[0].id, "right")),
            )
            for row in rows
        }

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
        """Load pair-addressed Files from a visible Snapshot in one read.

        Each pair is an exact nullable left/right repository-path pair; pairs
        naming no File of the visible Snapshot are absent from the result.
        `Err("snapshot-unavailable")` means the Snapshot is not visible in this
        Room. Nullable sides make a tuple IN unusable, so the pairs become one
        OR of exact per-pair conditions inside a single query and transaction.

        # Parameters

        - `identity`: Room that must contain the addressed Snapshot.
        - `snapshot_id`: Exact Snapshot to search.
        - `pairs`: Exact nullable left/right repository-path pairs to load.

        # Usage

        Use this when review placement starts with public File pairs rather than
        internal File ids. Check `Ok.good` before accessing the mapping.

        # Returns

        - `Ok`: A mapping from each found path pair to its unique File record.
          Each key's first item is the nullable left path and its second is the
          nullable right path. Requested pairs with no File are absent, and the
          mapping may be empty for a visible Snapshot.
        - `Err`: The Snapshot is missing or outside `identity`.
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
        """Load exactly the Snapshot Files one Thread page references.

        `origin_refs` are `(origin_snapshot_id, snapshot_file_id)` pairs for
        the page's Thread origins; `located_file_ids` are the placement File
        ids inside `selected_snapshot_id`. `absent_origin_refs` identify
        origins of file-missing placements: for each, the selected Snapshot
        is searched for a File with the identical left/right repository-path
        pair, and any match is returned as a conflict id so the caller can
        reject the placement invariant violation instead of substituting
        data. `comparison_origin_refs` request the matching selected-Snapshot
        File for origin-bound History Threads that have no selected placement.
        Every requested origin must exist; missing rows fail the read.
        Room visibility of the Snapshot is the caller's responsibility, as
        with `review_threads`.

        # Parameters

        - `selected_snapshot_id`: Displayed Snapshot holding selected placements
          and any Files compared with origin-bound Threads.
        - `origin_refs`: Exact origin Snapshot/File pairs needed to reconstruct
          original context.
        - `located_file_ids`: Selected-placement File ids in the selected
          Snapshot.
        - `absent_origin_refs`: Origin references whose supposedly absent File
          pair must be checked against the selected Snapshot.
        - `comparison_origin_refs`: Origin references whose matching displayed
          File, when present, is needed for an exact content comparison.

        # Usage

        Call once after selecting a page of Thread records. Build all three
        collections from those records so the read hydrates exactly the Files
        needed for placement and excerpt construction.

        # Returns

        - First, origin Files whose mapping key contains the origin Snapshot id
          first and origin File id second. Every requested origin is present.
        - Second, selected-placement Files keyed by File id. Only requested ids
          found in `selected_snapshot_id` appear.
        - Third, File ids in the selected Snapshot whose path pairs conflict
          with `absent_origin_refs`. A valid file-missing placement yields an
          empty tuple here.
        - Fourth, matching selected-Snapshot Files keyed by the origin reference
          that requested each comparison. Missing pairs are absent.

        # Failures

        - Asserts if any requested origin File is missing or a supposedly absent
          File pair exists in the selected Snapshot.
        """

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
        """Load one ordered, bounded set of Snapshot or Room Threads.

        `identity` must identify the Room containing `snapshot_id`.
        `scope="snapshot"` requires a placement in `snapshot_id`.
        `scope="room"` includes every Thread whose origin belongs to the Room
        and returns its origin row when no displayed placement exists. `offset`
        and `limit` select a slice after ordering open, resolved, and
        deleted Threads  by lifecycle state and creation activity.
        `state="open"` excludes resolved and deleted Threads.

        `through_activity_id` is an inclusive pivot over the Room's append-only
        review actions.
        Thread existence, lifecycle state, ordering, count, and returned actions
        are all reconstructed using only actions at or before that pivot, so
        separate page reads observe one stable discussion state.
        `None` chooses the greatest current Room activity in this same database
        session. The concrete pivot is returned for use by subsequent pages.

        This function returns `Err` when the Snapshot does not belong to the
        supplied Room.
        Otherwise returns the selected placements or origin rows, unique
        origins, actions, Profiles, total matching Thread count, and concrete
        inclusive pivot.

        # Parameters

        - `identity`: Room containing the selected Snapshot and discussions.
        - `snapshot_id`: Displayed code universe used to select placements and,
          in Room scope, identify discussion membership.
        - `offset`: Zero-based number of ordered matching Threads to skip.
        - `limit`: Maximum Threads to hydrate, or `None` for no SQL limit.
        - `state`: `all` for every lifecycle state or `open` for open Threads.
        - `attention`: Optional actionable role filter for open agent inboxes.
        - `scope`: Snapshot placement membership for agents or complete Room
          membership for browser History.
        - `through_activity_id`: Inclusive discussion pivot, or `None` to choose
          the current Room boundary in this read transaction.

        # Usage

        Use the concrete returned pivot for every later page of the same read so
        pagination cannot mix Thread states. Pass `None` only for the first page
        when selecting the current boundary.

        # Returns

        - `Ok.value[0]`: The selected placements or origin rows, unique origins,
          ordered actions, authors, and count before pagination in one
          `ReviewThreadsRecord`.
        - `Ok.value[1]`: The concrete inclusive activity pivot used for every
          row in that record; later pages must repeat it.
        - `Err`: The Snapshot is missing or belongs to another Room. The
          caller must reject the page instead of treating it as an empty one.

        # Failures

        - Asserts for a negative activity pivot or persisted Threads whose
          origin, actions, or author rows violate review invariants.
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
                """Select Room Threads joined to their latest actions.

                The relation supplies one boundary state per Thread and an
                optional placement in the displayed Snapshot. A Thread created
                after that Snapshot remains part of the Room result.

                # Returns

                - Selected columns and their order remain exactly those of the
                  supplied `query`.
                - The returned selection adds the common placement and
                  latest-action joins plus the page's state and attention filters.
                """
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
        """Load one discussion at one exact Snapshot without bulk hydration.

        `Err` means the Snapshot is absent or belongs to another Room. An empty
        record in `Ok` means that Snapshot contains no such Thread. A nonempty
        record contains exactly its selected placement, unique origin,
        complete action sequence, and referenced Profile authors.

        # Parameters

        - `identity`: Room that must contain the Snapshot and Thread origin.
        - `snapshot_id`: Exact Snapshot placement to bind.
        - `thread_id`: Stable discussion identity to load.

        # Usage

        Use this for one discussion page or write preparation. Distinguish
        `Err` means the Snapshot is outside the Room; `Ok` with an empty
        `ReviewThreadsRecord` means the Thread is absent.

        # Returns

        - `Ok`: A record containing exactly one selected placement, its origin,
          ordered actions, and authors when the Thread is present.
        - `Ok` with an empty `ReviewThreadsRecord` when the Snapshot is valid
          but the Thread has no selected placement there.
        - `Err`: The Snapshot is missing or outside `identity`. Callers must
          not collapse this into the empty-record case.
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
        tuple[tuple[ReviewActionRecord, ...], tuple[UserProfileRecord, ...]]
    ]:
        """Load authoritative actions and authors for one placed Thread.

        This write-validation read deliberately excludes Snapshots, Files, and
        placement rendering. `None` means the exact Thread/Snapshot pair does
        not exist.

        # Parameters

        - `snapshot_id`: Snapshot in which the Thread must have a placement.
        - `thread_id`: Discussion whose complete ordered actions are required.

        # Usage

        Use this immediately before validating one Thread write. It avoids File
        hydration while still returning the complete action history and current
        author records needed by the review model.

        # Returns

        - First, the Thread's complete `ReviewActionRecord` tuple in
          `action.sequence` order. Read each record's `action` for folding.
        - Second, current Profile records for every author referenced by those
          actions. Profile tuple order is not action order.
        - `None`: The Thread has no placement in `snapshot_id`. The caller must
          reject the proposed write before applying review state transitions.
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
            actions = session.scalars(
                select(ReviewAction)
                .where(ReviewAction.thread_id == thread_id)
                .order_by(ReviewAction.sequence)
            ).all()
            assert actions != [], "review Thread has no creation action"
            profile_ids = {action.profile_id for action in actions}
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
            tuple(self._action_record(action) for action in actions),
            profiles,
        )

    def review_profiles(
        self, profile_ids: tuple[int, ...]
    ) -> tuple[UserProfileRecord, ...]:
        """Load the existing Profiles among `profile_ids` in one set query.

        Missing ids are absent from the result. The caller decides whether an
        absent author is an error.

        # Usage

        Use one call for all Profile ids referenced by a review operation, then
        compare the result keys with the ids that operation requires.

        # Returns

        - Each item is the current record for one distinct existing input id;
          missing ids are omitted and duplicate ids do not duplicate records.
        - Tuple order is not tied to input order. Callers match records by their
          stable Profile ids, and an empty tuple means no input id exists.
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
        absent from the returned mapping; the caller decides whether to reject it.
        Every present Thread has at least one action, and every acting
        Profile is present in the returned Profiles.

        # Parameters

        - `snapshot_id`: Snapshot in which every returned Thread is placed.
        - `thread_ids`: Discussions to validate and hydrate as one set.

        # Usage

        Use this to validate an atomic batch without issuing one action-history
        query per Thread. Compare the returned mapping with the requested ids;
        absent keys identify Threads not placed in the Snapshot.

        # Returns

        - First, every placed Thread id maps to its complete
          `ReviewActionRecord` tuple in `action.sequence` order. Unplaced ids
          are absent; the record contract defines action and cursor fields.
        - Second, current Profile records for every author referenced by those
          actions. Profile tuple order is not an input-order guarantee.
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
            actions = session.scalars(
                select(ReviewAction)
                .where(ReviewAction.thread_id.in_(placed))
                .order_by(ReviewAction.thread_id, ReviewAction.sequence)
            ).all()
            profile_ids = {action.profile_id for action in actions}
            profile_rows = session.execute(
                select(UserProfile.id, UserProfile.username).where(
                    UserProfile.id.in_(profile_ids)
                )
            ).all()
        actions_by_thread: dict[str, list[ReviewActionRecord]] = {}
        for action in actions:
            actions_by_thread.setdefault(action.thread_id, []).append(
                self._action_record(action)
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
        """Return Room Thread origins without a target-Snapshot placement.

        # Parameters

        - `identity`: Room whose logical Threads are considered.
        - `target_snapshot_id`: New Snapshot for which derivation is pending.

        # Usage

        Snapshot publication calls this only for a genuinely new Snapshot, then
        derives and inserts one immutable placement for each returned origin.

        # Returns

        - Each item is one immutable Room Thread origin whose Thread has no
          placement in `target_snapshot_id`; each Thread appears once.
        - Tuple order has no caller-visible meaning. An empty tuple means no
          placement derivation remains for this Snapshot.
        """
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
        """Insert missing immutable placements and validate existing pairs.

        The caller holds the shared Room lock. Existing equal records make the
        operation idempotent; an existing different record is an invariant
        violation because placements are immutable.

        # Usage

        Hold the shared Room lock and pass the complete set derived for one
        target Snapshot. Repeating the same records is safe.

        # Failures

        - Asserts when input repeats a Thread/Snapshot pair or an existing pair
          has different immutable placement data.
        """
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
        """Atomically create one discussion in its origin Snapshot.

        # Parameters

        - `rows`: Immutable placements for the new Thread, including exactly
          one origin.
        - `first_action`: Sequence-zero Thread creation and first Comment.

        # Usage

        Review code validates the target and constructs the origin placement and
        sequence-zero action before calling under the shared Room lock.

        # Failures

        - Asserts unless `rows` contains one origin for the same Thread as the
          sequence-zero `thread-created` action.
        - A database failure rolls back both placement and action rows.
        """
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
    ) -> None:
        """Insert prevalidated Thread placements and actions atomically.

        The caller holds the Room write lock and supplies actions in authored
        order. Placement rows belong only to Threads created by this batch;
        actions may address those new Threads or existing Snapshot-bound
        Threads.

        # Parameters

        - `thread_rows`: Placements for Threads created by this batch.
        - `actions`: Non-empty prevalidated actions in authored order.

        # Usage

        Validate the complete ordered batch and allocate any new Thread
        placements before calling under the shared Room lock. Use this single
        transaction rather than appending actions individually.

        # Failures

        - Asserts for an empty action batch or duplicate placement keys.
        - Any constraint failure rolls back the entire batch.
        """
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

    def review_latest_activity_id(self, identity: RoomIdentity) -> int:
        """Return the Room's greatest review activity id, or 0 when empty.

        This is the focused pivot read for callers that need only the
        activity boundary, without hydrating any Thread.

        # Usage

        Record this value with a Snapshot capture or continuation response when
        the caller will later ask for actions strictly after that boundary.
        """
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

        # Parameters

        - `session`: Existing read transaction shared with the surrounding
          continuation query.
        - `identity`: Room whose logical Threads are counted.
        - `through_activity_id`: Inclusive action boundary defining state.
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

        # Parameters

        - `identity`: Room whose later authored actions are read.
        - `activity_id`: Exclusive lower activity boundary retained by the
          agent from its prior response.
        - `limit`: Positive maximum action count for this page.

        # Usage

        Pass the last activity id retained by the agent. Continue with the final
        id from each returned page until `has_more` is false; the returned open
        count describes state at that page's inclusive end.

        # Returns

        - First, at most `limit` later `ReviewActionRecord` values ordered by
          their required `activity_id`.
        - Second, whether another later action exists beyond this page.
        - Third, the count of open logical Threads at the page's inclusive end
          boundary, not necessarily at the newest database action.
        - Fourth, current Profile records for every author in the action page.

        # Failures

        - Asserts for a negative boundary, nonpositive limit, or a persisted
          action whose author Profile is missing.
        """
        assert activity_id >= 0, "review activity boundary must be nonnegative"
        assert limit > 0, "review activity limit must be positive"
        mark_clause = (
            Room.mark_id.is_(None)
            if identity.mark_id is None
            else Room.mark_id == identity.mark_id
        )
        with Session(self.engine) as session:
            rows = session.scalars(
                select(ReviewAction)
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
            tuple(self._action_record(action) for action in page),
            len(rows) > limit,
            open_count,
            profiles,
        )

    def review_attention_counts(
        self, identity: RoomIdentity, through_activity_id: int
    ) -> dict[Literal["author", "reviewer", "both"], int]:
        """Count open logical Threads by actionable attention at one pivot.

        # Parameters

        - `identity`: Room whose current logical discussions are counted.
        - `through_activity_id`: Inclusive boundary fixing each Thread outcome.

        # Usage

        Use the same inclusive pivot as the Thread page being summarized. The
        result contains a count for each supported attention value.

        # Returns

        - The keys are exactly `author`, `reviewer`, and `both`; no other
          persisted attention value appears.
        - Each value counts open Threads whose latest action at the inclusive
          pivot assigns that attention. Absent categories remain present as zero.
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
        """Return the placed Thread containing one created Comment.

        # Parameters

        - `snapshot_id`: Snapshot in which the containing Thread must be placed.
        - `comment_id`: Stable Comment identity created by one review action.

        # Usage

        Use this only after a route receives a Comment id without its Thread id.
        Bind the returned id through `Room.get_thread` before applying a write.

        # Returns

        - The stable Thread id whose placement in `snapshot_id` contains the
          Comment creation action.
        - `None`: No placed discussion in that Snapshot contains `comment_id`.
          The caller must report the Comment as unknown instead of searching a
          different Snapshot.

        """
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
        """Append one already-validated action to its Snapshot-bound Thread.

        The method opens and commits its own short-lived transaction. The database
        enforces identity, placement, sequence, and column constraints around the
        record's already-validated operation shape. Success returns `None`, while
        constraint or persistence failures propagate.

        # Parameters

        - `record`: Accepted `ReviewActionInput`; persistence assigns the next
          database-wide cursor to the inserted row.

        # Usage

        Review code must validate the author, expected revision, lifecycle, and
        operation shape first. Call under the shared Room lock so sequence and
        activity allocation stay ordered with other writes.

        # Failures

        - Database constraints reject a missing placement, duplicate sequence or
          operation id, invalid author, or inconsistent operation fields.
        """
        with Session(self.engine) as session, session.begin():
            session.execute(
                insert(ReviewAction).values(
                    **self._review_action_values(record),
                    activity_id=self._next_review_activity_id(session),
                )
            )
