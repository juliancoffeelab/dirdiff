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
    "SnapshotFileRecord",
    "SnapshotFileSideRecord",
    "SnapshotFileSymlinkRecord",
    "SnapshotMetaRecord",
    "SnapshotRecord",
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
class SnapshotFileSideRecord:
    """Expose the repository identity and digest of one present File side.

    `RoomStore` creates this record while reading a `SnapshotFileRecord`.
    Callers use the repository path for exact File identity and the digest for
    Snapshot comparison.

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


@dataclass(frozen=True)
class SnapshotFileSymlinkRecord:
    """Expose one symbolic-link side's authenticated physical sidecars.

    `RoomStore` constructs this record for a `SnapshotFileRecord` only when the
    relational symlink row exists. Callers read exactly these paths and compare
    exact bytes with these digests; they must not infer sidecar names or link
    kind from filesystem state.

    The optional target pair represents a stopped walk by total absence. Link
    chain, diagnosis, and repository target path remain inside authenticated
    metadata and are not fields of this relational descriptor.
    """

    metadata_path: str
    """Absolute path to the immutable JSON metadata sidecar."""

    metadata_hash: bytes
    """SHA-256 digest of exact metadata bytes at `metadata_path`."""

    target_capture_path: Optional[str]
    """Absolute path to reached target bytes, or `None` after a stopped walk."""

    target_hash: Optional[bytes]
    """SHA-256 target digest with the same presence as its physical path."""

    def __post_init__(self) -> None:
        """Reject descriptors that cannot name one authenticated capture.

        Both database publication and hydration construct this type, so it
        guards absolute path shape, digest length, and the optional target
        pair at both boundaries.

        # Failures

        - Raises `AssertionError` for a relative metadata or target path, a
          digest not exactly 32 bytes long, or unequal target path/hash
          presence.
        """
        assert Path(self.metadata_path).is_absolute(), (
            "Snapshot link metadata path must be absolute"
        )
        assert len(self.metadata_hash) == 32, (
            "Snapshot link metadata hash must have length 32"
        )
        assert (self.target_capture_path is None) == (
            self.target_hash is None
        ), "Snapshot link target path and hash must have equal presence"
        if self.target_capture_path is not None:
            assert Path(self.target_capture_path).is_absolute(), (
                "Snapshot link target capture path must be absolute"
            )
            assert self.target_hash is not None
            assert len(self.target_hash) == 32, (
                "Snapshot link target hash must have length 32"
            )


@dataclass(frozen=True)
class SnapshotFileRecord:
    """Immutable relational facts for one affected File.

    `RoomStore` returns this immutable record to Room for manifest, File lookup,
    delta, and review operations.

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
    """Whether this File belongs to the backend's tracked input.

    The record preserves the capture-time backend fact without inspecting Git again.
    """

    change_type: str
    """Backend classification retained without deriving renderer output.

    Consumers combine it with side presence and errors when building presentation.
    """

    error: Optional[str]
    """Persisted capture failure, or `None` when physical contents are valid.

    A non-null value makes the File unreadable even if side path metadata is retained.
    """

    left: Optional[SnapshotFileSideRecord]
    """Captured left side, when it exists.

    `None` means that side is absent, which differs from present empty captured bytes.
    """

    right: Optional[SnapshotFileSideRecord]
    """Captured right side, when it exists.

    Consumers preserve absence instead of substituting the other side or an empty file.
    """

    left_symlink: Optional[SnapshotFileSymlinkRecord]
    """Authoritative left link sidecars, or `None` for an ordinary/absent side."""

    right_symlink: Optional[SnapshotFileSymlinkRecord]
    """Authoritative right link sidecars, or `None` for an ordinary/absent side."""


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

    It supports recapture reuse but is not a globally addressable Snapshot key.
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
class ReviewThreadRecord:
    """Immutable placement facts for one Thread and Snapshot pair.

    Review derivation passes this value to `RoomStore`, and persistence reads
    return the same validated shape to `dirdiff.review`.

    Private locator bytes occur only on a current text range and never leave the
    persistence/review boundary.
    """

    thread_id: str
    """Stable logical discussion id shared by all Snapshot placements.

    Actions use it to preserve one discussion while selected-Snapshot coordinates change.
    """

    snapshot_id: str
    """Exact captured code universe in which this placement applies.

    File and range fields are invalid when combined with records from another Snapshot.
    """

    snapshot_file_id: Optional[str]
    """File containing the landing, or `None` when absent or unreadable.

    Its absence requires all bay, side, and range coordinates to be absent as well.
    """

    is_origin: bool
    """Whether this record is the Thread's immutable creation placement.

    This is a query label supplied by the store, not a persisted column.
    """

    target_kind: Optional[Literal["range", "bay-start", "file-start"]]
    """Shape of a located target, or `None` for an unlocated Thread.

    A `range` carries bay, side, and lines; `bay-start` omits the lines; and
    `file-start` carries only the side.
    """

    bay_key: Optional[str]
    """Public bay identity for range and bay-start placements.

    It is meaningful only with the placed File and side; File-start shapes leave it null.
    """

    side: Optional[Literal["left", "right"]]
    """Captured side for every placement that still reaches a File.

    Unlocated records leave it null rather than preserving a coordinate with no File.
    """

    start_line: Optional[int]
    """Positive one-based inclusive start of a range placement.

    Bay-start and File-start placements have no selected range and leave it null.
    """

    end_line: Optional[int]
    """One-based inclusive range end, no earlier than `start_line`.

    It must be present exactly when the start is present.
    """

    outdated_reason: Optional[
        Literal[
            "region_changed",
            "region_not_found",
            "bay_not_found",
            "file_unreadable",
            "file_missing",
        ]
    ]
    """Why the origin did not land unchanged, or `None` when current.

    `file_missing` and `file_unreadable` distinguish the unlocated shapes;
    the remaining reasons qualify range, bay-start, or File-start landings.
    """

    private_locator: Optional[bytes]
    """Opaque structural coordinates retained only for a current text range.

    Reattachment code may decode it; HTTP and ordinary persistence callers must not.
    """

    def __post_init__(self) -> None:
        """Reject any placement shape review derivation cannot produce.

        Placement is a tagged union in `dirdiff.review`: a range, bay start,
        File start, missing File, or unreadable File. One flat row
        holds all five, so the tag and the fields it implies are checked here
        to agree. Both directions of persistence build this record:
        `_record_of` before an insert and `_thread_record` after a select. One
        check therefore guards the write and the read, and no stored row can
        hold a shape review cannot interpret.

        It raises instead of asserting because it is the only guard this
        invariant has. `assert` disappears under `-O`, and a stripped check
        would admit a row every later reader then fails on.

        # Failures

        - Raises `AssertionError` when an unlocated placement carries fields
          other than a missing or unreadable reason; a range lacks its bay,
          side, ordered line span, or valid changed-region reason; a bay-start
          or File-start carries fields outside its shape; or a placement on a
          File has no recognized target kind.
        - Raises `AssertionError` when private locator bytes appear on anything
          except a current range placement.
        """
        where = f"thread {self.thread_id} in Snapshot {self.snapshot_id}"
        if self.snapshot_file_id is None:
            if (
                self.target_kind is not None
                or self.bay_key is not None
                or self.side is not None
                or self.start_line is not None
                or self.end_line is not None
                or self.outdated_reason
                not in ("file_missing", "file_unreadable")
            ):
                raise AssertionError(
                    f"an unlocated placement carries nothing but its "
                    f"reason: {where}"
                )
        elif self.target_kind == "range":
            if (
                self.bay_key is None
                or len(self.bay_key) == 0
                or self.side is None
                or self.start_line is None
                or self.start_line < 1
                or self.end_line is None
                or self.end_line < self.start_line
                or self.outdated_reason not in (None, "region_changed")
            ):
                raise AssertionError(
                    f"a range placement names a bay and an ordered line "
                    f"span: {where}"
                )
        elif self.target_kind == "bay-start":
            if (
                self.bay_key is None
                or len(self.bay_key) == 0
                or self.side is None
                or self.start_line is not None
                or self.end_line is not None
                or self.outdated_reason
                not in ("region_not_found", "bay_not_found")
            ):
                raise AssertionError(
                    f"a bay-start placement names a bay, no line span, and a "
                    f"lost-region reason: {where}"
                )
        elif self.target_kind == "file-start":
            if (
                self.bay_key is not None
                or self.side is None
                or self.start_line is not None
                or self.end_line is not None
                or self.outdated_reason not in (None, "bay_not_found")
            ):
                raise AssertionError(
                    f"a File-start placement names a side and nothing "
                    f"narrower: {where}"
                )
        else:
            raise AssertionError(
                f"a placement on a File states its target kind: {where}"
            )
        # Private coordinates retain where their author put a live range, so
        # they belong to a range placement that has not gone outdated. Every
        # other shape either never had them or has lost what they addressed.
        if self.private_locator is not None and not (
            self.target_kind == "range" and self.outdated_reason is None
        ):
            raise AssertionError(
                f"only a current range placement retains private "
                f"coordinates: {where}"
            )


@dataclass(frozen=True)
class ReviewActionRecord:
    """Carry one authored Thread operation across the persistence boundary.

    Thread methods construct this record before append; `RoomStore` assigns its
    activity id on insertion, and later reads return records for discussion
    materialization.

    `status_after` and `attention_after` are authoritative after this operation.
    The record does not represent mutable current Thread state by itself.
    """

    operation_id: str
    """Globally unique internal id of this accepted operation.

    Callers create it once for a new action and must not reuse it for retry or
    replay; persistence treats any duplicate as a conflict.
    """

    thread_id: str
    """Logical Thread changed by this operation.

    It binds every variant to one durable discussion across placements.
    """

    snapshot_id: str
    """Thread placement against which the operation was accepted.

    It records the author's exact Snapshot boundary for conflict validation.
    """

    sequence: int
    """Zero-based append order within `thread_id`.

    The value is contiguous and acts as the discussion revision exposed to clients.
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
    """Operation variant governing the nullable Comment fields.

    Creation and replies carry a new Comment and body. Edits and Comment
    deletion also carry an expected revision. Resolve and reopen may carry a
    newly authored Comment; Thread deletion carries no Comment fields.
    """

    profile_id: int
    """Durable Profile attributed as the operation's author.

    Current Profile display data is joined separately and may change later.
    """

    comment_id: Optional[str]
    """Created or changed Comment id when `kind` addresses one.

    Lifecycle-only variants leave it null and therefore create no hidden Comment.
    """

    expected_revision: Optional[int]
    """Comment revision required by edit and deletion operations.

    It prevents a stale actor from changing a newer Comment revision.
    """

    body: Optional[str]
    """Non-empty authored Comment body when `kind` carries one.

    Its presence is governed by the operation variant, not inferred from Comment identity.
    """

    created_at: str
    """Authored-action timestamp in the public serialized form.

    Reads preserve the accepted value as event history rather than producing a fresh time.
    """

    status_after: Literal["open", "resolved", "deleted"]
    """Authoritative Thread lifecycle state after this operation.

    Boundary reads take this value from the latest included action.
    """

    attention_after: Literal["author", "reviewer", "both", "none"]
    """Authoritative attention state after this operation.

    It expresses who needs action after the event, independently of lifecycle state.
    """

    activity_id: Optional[int] = None
    """Global Room order after persistence; `None` only before insertion.

    Input records omit it. Records reconstructed by later reads carry the assigned
    cursor used by activity feeds.
    """

    def __post_init__(self) -> None:
        """Reject any action whose fields disagree with the operation it names.

        Each `kind` implies exactly which of `comment_id`, `expected_revision`,
        and `body` are present: an authoring operation carries a Comment and a
        non-empty body, an edit or a delete carries the revision it expected,
        and a lifecycle operation carries a Comment and a body together or
        neither. `thread_id` and `sequence` are required by their own types.

        Both directions of persistence build this record, so one check guards
        the insert and the select, and no stored action can name an operation
        its fields do not describe. It raises rather than asserting for the
        same reason the placement record does: `-O` strips `assert`, and this
        is the only guard the invariant has.

        # Failures

        - Raises `AssertionError` when `profile_id` is not positive.
        - Raises `AssertionError` when Comment identity, expected revision, or
          body presence disagrees with `kind`. Creation requires a nonempty
          Comment body, edit and Comment deletion require a revision, lifecycle
          Comments require both id and body, and terminal deletion carries none.
        """
        assert self.profile_id > 0, (
            "review actions require a relational Profile"
        )
        where = f"action {self.operation_id} on thread {self.thread_id}"
        has_body = self.body is not None and len(self.body) > 0
        match self.kind:
            case "thread-created" | "comment-created":
                valid = (
                    self.comment_id is not None
                    and self.expected_revision is None
                    and has_body
                )
            case "comment-edited":
                valid = (
                    self.comment_id is not None
                    and self.expected_revision is not None
                    and has_body
                )
            case "comment-deleted":
                valid = (
                    self.comment_id is not None
                    and self.expected_revision is not None
                    and self.body is None
                )
            case "thread-resolved" | "thread-reopened":
                # A lifecycle operation may carry a Comment written in the same
                # action. Then it carries that Comment's body too; alone, it
                # carries neither.
                valid = self.expected_revision is None and (
                    (self.comment_id is None and self.body is None)
                    or (self.comment_id is not None and has_body)
                )
            case "thread-deleted":
                valid = (
                    self.comment_id is None
                    and self.expected_revision is None
                    and self.body is None
                )
        if not valid:
            raise AssertionError(
                f"{self.kind} does not carry these fields: {where}"
            )


@dataclass(frozen=True)
class ReviewThreadsRecord:
    """Bulk persistence result for all Threads visible in one Snapshot.

    `RoomStore` returns this value to one bounded review read. The review layer
    combines each selected placement with its origin, authored actions, and
    Profile attribution to build public Thread views.

    `total_threads` is measured before page bounds. This record contains raw
    relational facts, not materialized discussion state or excerpts.
    """

    threads: tuple[ReviewThreadRecord, ...]
    """Selected-Snapshot placements in requested page order.

    Each entry represents one logical Thread exactly once for the page boundary.
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
    """Number of placements before page bounds are applied.

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

    `RoomStore._review_action_values` converts a validated `ReviewActionRecord`
    to this private shape, adding `activity_id` when persistence assigns it.

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

        The mapping preserves nullable target-shape invariants and excludes no
        public field that participates in later placement materialization.
        """
        return {
            "thread_id": record.thread_id,
            "snapshot_id": record.snapshot_id,
            "snapshot_file_id": record.snapshot_file_id,
            "target_kind": record.target_kind,
            "bay_key": record.bay_key,
            "side": record.side,
            "start_line": record.start_line,
            "end_line": record.end_line,
            "outdated_reason": record.outdated_reason,
            "private_locator": record.private_locator,
        }

    @staticmethod
    def _review_action_values(
        record: ReviewActionRecord,
    ) -> _ReviewActionInsertValues:
        """Translate one immutable authored action into insert values.

        The caller supplies an already validated record; this conversion retains
        its discriminator-controlled nulls for the atomic insert.
        """
        values: _ReviewActionInsertValues = {
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
        - `ReviewThreadRecord` raises `AssertionError` when those discriminators
          are individually known but their nullable fields do not form one valid
          placement shape.
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
        return ReviewThreadRecord(
            thread_id=placement.thread_id,
            snapshot_id=placement.snapshot_id,
            snapshot_file_id=placement.snapshot_file_id,
            is_origin=is_origin,
            target_kind=target_kind,
            bay_key=placement.bay_key,
            side=side,
            start_line=placement.start_line,
            end_line=placement.end_line,
            outdated_reason=outdated_reason,
            private_locator=placement.private_locator,
        )

    @staticmethod
    def _action_record(action: ReviewAction) -> ReviewActionRecord:
        """Validate one selected database row as an authored action record.

        Persisted discriminator and nullable fields are checked together before
        any caller receives the immutable record.

        # Failures

        - Raises `AssertionError` when persisted action kind, lifecycle outcome,
          or attention outcome is outside the declared review values.
        - `ReviewActionRecord` raises `AssertionError` when known discriminator
          values disagree with Profile id, Comment identity, expected revision,
          or body presence.
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
        return ReviewActionRecord(
            operation_id=action.operation_id,
            thread_id=action.thread_id,
            snapshot_id=action.snapshot_id,
            sequence=action.sequence,
            kind=kind,
            profile_id=action.profile_id,
            comment_id=action.comment_id,
            expected_revision=action.expected_revision,
            body=action.body,
            created_at=action.created_at,
            status_after=status_after,
            attention_after=attention_after,
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
            result[key] = SnapshotFileSymlinkRecord(
                metadata_path=row.metadata_path,
                metadata_hash=row.metadata_hash,
                target_capture_path=row.target_capture_path,
                target_hash=row.target_hash,
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
            SnapshotFileSideRecord(left_path, left_hash)
            if left_path is not None and left_hash is not None
            else None
        )
        right = (
            SnapshotFileSideRecord(right_path, right_hash)
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
            if link.target_capture_path is not None:
                assert Path(link.target_capture_path).parent == directory, (
                    "Snapshot link target must share its File capture directory"
                )
                assert link.target_capture_path != link.metadata_path, (
                    "Snapshot link metadata and target paths must differ"
                )
        return SnapshotFileRecord(
            id=file.id,
            snapshot_id=file.snapshot_id,
            path=file.path,
            tracked=file.tracked,
            change_type=file.change_type,
            error=file.error,
            left=left,
            right=right,
            left_symlink=left_symlink,
            right_symlink=right_symlink,
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
            assert file.left is not None or file.right is not None, (
                "Snapshot File must have at least one captured side"
            )
            assert file.tracked or file.left is None, (
                "an intruding File cannot have a left side"
            )
            assert file.left_symlink is None or file.left is not None, (
                "a left symlink requires a captured left side"
            )
            assert file.right_symlink is None or file.right is not None, (
                "a right symlink requires a captured right side"
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
            file_directory = Path(file.path)
            for link in (file.left_symlink, file.right_symlink):
                if link is None:
                    continue
                metadata_path = Path(link.metadata_path)
                assert metadata_path.parent == file_directory, (
                    "Snapshot link metadata must share its File capture directory"
                )
                assert link.metadata_path not in symlink_storage_paths, (
                    "duplicate Snapshot link metadata path"
                )
                symlink_storage_paths.add(link.metadata_path)
                if link.target_capture_path is not None:
                    target_capture_path = Path(link.target_capture_path)
                    assert target_capture_path.parent == file_directory, (
                        "Snapshot link target must share its File capture directory"
                    )
                    assert (
                        link.target_capture_path not in symlink_storage_paths
                    ), "duplicate Snapshot link target path"
                    symlink_storage_paths.add(link.target_capture_path)
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
            symlink_rows = [
                {
                    "file_id": file.id,
                    "side": side,
                    "metadata_path": link.metadata_path,
                    "metadata_hash": link.metadata_hash,
                    "target_capture_path": link.target_capture_path,
                    "target_hash": link.target_hash,
                }
                for file in snapshot.files
                for side, link in (
                    ("left", file.left_symlink),
                    ("right", file.right_symlink),
                )
                if link is not None
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
    ) -> tuple[bool, Optional[SnapshotFileLoadRecord]]:
        """Return Snapshot existence and one repository-path-addressed File.

        `(False, None)` means the Snapshot is not visible in this Room.
        `(True, None)` means it is visible but has no matching File.

        # Parameters

        - `identity`: Room that must contain the addressed Snapshot.
        - `snapshot_id`: Exact Snapshot to search.
        - `left_path`: Exact left repository path, or `None` for an absent side.
        - `right_path`: Exact right repository path, or `None` for an absent
          side.

        # Usage

        Preserve both result values: an unknown Snapshot and a missing File are
        different failures. Supply the exact nullable path pair emitted by the
        manifest.

        # Returns

        - First, whether the Snapshot belongs to `identity`.
        - Second, the matching File and its lazy reason, or `None` when no File
          has the exact nullable path pair. `(False, None)` means the Snapshot
          itself is unavailable; `(True, None)` means only the File is absent.
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
                return exists, None
            symlinks = self._file_symlink_records(
                session.execute(
                    select(SnapshotFileSymlink).where(
                        SnapshotFileSymlink.file_id == row[0].id
                    )
                ).scalars()
            )

        return True, SnapshotFileLoadRecord(
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
    ) -> tuple[
        bool, dict[tuple[Optional[str], Optional[str]], SnapshotFileRecord]
    ]:
        """Return Snapshot existence and its pair-addressed Files in one read.

        Each pair is an exact nullable left/right repository-path pair; pairs
        naming no File of the visible Snapshot are absent from the result.
        `(False, {})` means the Snapshot is not visible in this Room. Nullable
        sides make a tuple IN unusable, so the pairs become one OR of exact
        per-pair conditions inside a single query and transaction.

        # Parameters

        - `identity`: Room that must contain the addressed Snapshot.
        - `snapshot_id`: Exact Snapshot to search.
        - `pairs`: Exact nullable left/right repository-path pairs to load.

        # Usage

        Use this when review placement starts with public File pairs rather than
        internal File ids. Preserve the returned Snapshot-exists flag separately
        from the mapping.

        # Returns

        - First, whether the Snapshot belongs to `identity`.
        - Second, a mapping from each found path pair to its unique File record.
          Each key's first item is the nullable left path and its second is the
          nullable right path. Requested pairs with no File are absent, and the
          mapping is empty when the first returned item is false.
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

        # Parameters

        - `selected_snapshot_id`: Snapshot holding all selected placements.
        - `origin_refs`: Exact origin Snapshot/File pairs needed to reconstruct
          original context.
        - `located_file_ids`: Selected-placement File ids in the selected
          Snapshot.
        - `absent_origin_refs`: Origin references whose supposedly absent File
          pair must be checked against the selected Snapshot.

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
            file_ids = {row[0].id for row in (*origin_rows, *selected_rows)}
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
                (row[0].snapshot_id, row[0].id): self._file_record(
                    *row,
                    left_symlink=symlinks.get((row[0].id, "left")),
                    right_symlink=symlinks.get((row[0].id, "right")),
                )
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
            {
                row[0].id: self._file_record(
                    *row,
                    left_symlink=symlinks.get((row[0].id, "left")),
                    right_symlink=symlinks.get((row[0].id, "right")),
                )
                for row in selected_rows
            },
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

        # Parameters

        - `identity`: Room containing the selected Snapshot and discussions.
        - `snapshot_id`: Exact code universe whose placements are returned.
        - `offset`: Zero-based number of ordered matching Threads to skip.
        - `limit`: Maximum Threads to hydrate, or `None` for no SQL limit.
        - `state`: `all` for every lifecycle state or `open` for open Threads.
        - `attention`: Optional actionable role filter for open agent inboxes.
        - `through_activity_id`: Inclusive discussion pivot, or `None` to choose
          the current Room boundary in this read transaction.

        # Usage

        Use the concrete returned pivot for every later page of the same read so
        pagination cannot mix Thread states. Pass `None` only for the first page
        when selecting the current boundary.

        # Returns

        - First, the selected placements, origins, ordered actions, authors, and
          count before pagination in one `ReviewThreadsRecord`.
        - Second, the concrete inclusive activity pivot used for every row in
          that record; later pages must repeat it.
        - `None`: The Snapshot is missing or belongs to another Room. The
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
                return None
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

            def filtered[*T](
                query: Select[tuple[*T]],
            ) -> Select[tuple[*T]]:
                """Select from placements joined to their latest actions.

                The relation supplies one boundary state per Thread while
                retaining placement columns needed by the outer page query.

                # Returns

                - Selected columns and their order remain exactly those of the
                  supplied `query`.
                - The returned selection adds the common placement and
                  latest-action joins plus the page's state and attention filters.
                """
                return (
                    query.select_from(ReviewThreadPlacement)
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
                    filtered(select(ReviewThreadPlacement.thread_id)).subquery()
                )
            ).scalar_one()
            selected_query = (
                filtered(
                    select(
                        ReviewThreadPlacement,
                        literal(False).label("is_origin"),
                    )
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
            thread_ids = [row[0].thread_id for row in selected_rows]
            if thread_ids == []:
                return (
                    ReviewThreadsRecord((), (), (), (), total_threads),
                    through_activity_id,
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
        return (
            ReviewThreadsRecord(
                threads=tuple(
                    self._thread_record(*row) for row in selected_rows
                ),
                origins=tuple(self._thread_record(*row) for row in origin_rows),
                actions=tuple(
                    self._action_record(action) for action in actions
                ),
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

        # Parameters

        - `identity`: Room that must contain the Snapshot and Thread origin.
        - `snapshot_id`: Exact Snapshot placement to bind.
        - `thread_id`: Stable discussion identity to load.

        # Usage

        Use this for one discussion page or write preparation. Distinguish
        `None` from an empty `ReviewThreadsRecord`: the former means the Snapshot
        is outside the Room, while the latter means the Thread is absent.

        # Returns

        - A record containing exactly one selected placement, its origin,
          ordered actions, and authors when the Thread is present.
        - An empty `ReviewThreadsRecord` when the Snapshot is valid but the
          Thread has no selected placement there.
        - `None`: The Snapshot is missing or outside `identity`. Callers must
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
                return None
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
                return ReviewThreadsRecord((), (), (), (), 0)
            if selected_row is None:
                return ReviewThreadsRecord((), (), (), (), 0)
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
        return ReviewThreadsRecord(
            threads=(self._thread_record(*selected_row),),
            origins=(self._thread_record(*origin_row),),
            actions=tuple(self._action_record(action) for action in actions),
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

        # Parameters

        - `snapshot_id`: Snapshot in which the Thread must have a placement.
        - `thread_id`: Discussion whose complete ordered actions are required.

        # Usage

        Use this immediately before validating one Thread write. It avoids File
        hydration while still returning the complete action history and current
        author records needed by the review model.

        # Returns

        - First, the Thread's complete action tuple in sequence order.
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

        - First, a mapping from every placed requested Thread id to its complete
          action tuple in sequence order. Unplaced ids are absent.
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
        pairs = [(row.thread_id, row.snapshot_id) for row in rows]
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

        # Parameters

        - `thread_rows`: Placements for Threads created by this batch.
        - `actions`: Non-empty prevalidated actions in authored order.

        # Usage

        Validate the complete ordered batch and allocate any new Thread
        placements before calling under the shared Room lock. Use this single
        transaction rather than appending actions individually.

        # Failures

        - Asserts for an empty action batch, duplicate placement keys, or actions
          that already carry a persistence-assigned activity id.
        - Any constraint failure rolls back the entire batch.
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

        - First, at most `limit` later actions ordered by activity id.
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
        """Append one already-validated action to its Snapshot-bound Thread.

        The method opens and commits its own short-lived transaction. The database
        enforces identity, placement, sequence, and column constraints around the
        record's already-validated operation shape. Success returns `None`, while
        constraint or persistence failures propagate.

        # Parameters

        - `record`: Fresh action with no assigned `activity_id`; persistence assigns
          the next database-wide cursor to the inserted row.

        # Usage

        Review code must validate the author, expected revision, lifecycle, and
        operation shape first. Call under the shared Room lock so sequence and
        activity allocation stay ordered with other writes.

        # Failures

        - Asserts when the caller supplies an assigned `activity_id`.
        - Database constraints reject a missing placement, duplicate sequence or
          operation id, invalid author, or inconsistent operation fields.
        """
        assert record.activity_id is None
        with Session(self.engine) as session, session.begin():
            session.execute(
                insert(ReviewAction).values(
                    **self._review_action_values(record),
                    activity_id=self._next_review_activity_id(session),
                )
            )
