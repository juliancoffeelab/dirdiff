from types import SimpleNamespace

import pytest

from dirdiff.db import (
    RoomStore,
)

_RANGE = {
    "thread_id": "a" * 32,
    "snapshot_id": "b" * 32,
    "snapshot_file_id": "c" * 32,
    "is_origin": True,
    "target_kind": "range",
    "bay_key": "flatfile",
    "side": "right",
    "start_line": 4,
    "end_line": 9,
    "outdated_reason": None,
    "private_locator": b"\x01\x02",
}

_ACTION = {
    "operation_id": "d" * 32,
    "thread_id": "a" * 32,
    "snapshot_id": "b" * 32,
    "sequence": 1,
    "kind": "comment-created",
    "profile_id": 7,
    "comment_id": "e" * 32,
    "expected_revision": None,
    "body": "a remark",
    "created_at": "2026-01-01T00:00:00Z",
    "status_after": "open",
    "attention_after": "author",
    "activity_id": 12,
}


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"bay_key": None}, id="range-without-bay"),
        pytest.param({"bay_key": ""}, id="range-with-empty-bay"),
        pytest.param({"side": None}, id="range-without-side"),
        pytest.param({"start_line": 0}, id="range-starting-below-one"),
        pytest.param({"end_line": 3}, id="range-ending-before-start"),
        pytest.param({"end_line": None}, id="range-without-end"),
        pytest.param(
            {"outdated_reason": "bay_not_found", "private_locator": None},
            id="range-with-a-bay-start-reason",
        ),
        pytest.param(
            {"outdated_reason": "region_changed"},
            id="outdated-range-keeping-its-locator",
        ),
        pytest.param(
            {
                "target_kind": "bay-start",
                "start_line": None,
                "end_line": None,
                "outdated_reason": None,
                "private_locator": None,
            },
            # SQL's `outdated_reason IN (...)` yields NULL for a NULL reason,
            # which a SQLite CHECK admits; the old constraint needed a separate
            # IS NOT NULL to close it. Python has no such hole, and this case
            # holds the door shut anyway.
            id="bay-start-without-a-reason",
        ),
        pytest.param(
            {
                "target_kind": "bay-start",
                "outdated_reason": "region_not_found",
                "private_locator": None,
            },
            id="bay-start-keeping-a-line-span",
        ),
        pytest.param(
            {
                "target_kind": "bay-start",
                "start_line": None,
                "end_line": None,
                "outdated_reason": "region_not_found",
            },
            id="bay-start-keeping-its-locator",
        ),
        pytest.param(
            {
                "target_kind": "file-start",
                "start_line": None,
                "end_line": None,
                "private_locator": None,
            },
            id="file-start-naming-a-bay",
        ),
        pytest.param(
            {
                "target_kind": "file-start",
                "bay_key": None,
                "start_line": None,
                "end_line": None,
                "outdated_reason": "region_changed",
                "private_locator": None,
            },
            id="file-start-with-a-range-reason",
        ),
        pytest.param(
            {
                "target_kind": None,
                "bay_key": None,
                "start_line": None,
                "end_line": None,
                "private_locator": None,
            },
            id="placement-on-a-file-without-a-kind",
        ),
        pytest.param(
            {
                "snapshot_file_id": None,
                "bay_key": None,
                "start_line": None,
                "end_line": None,
                "outdated_reason": "file_missing",
                "private_locator": None,
            },
            id="file-missing-still-naming-a-target",
        ),
        pytest.param(
            {
                "snapshot_file_id": None,
                "target_kind": None,
                "bay_key": None,
                "side": None,
                "start_line": None,
                "end_line": None,
                "private_locator": None,
            },
            id="file-missing-without-its-reason",
        ),
    ],
)
def test_invalid_placement_is_refused(
    changes: dict[str, str | int | None],
) -> None:
    with pytest.raises(AssertionError):
        RoomStore._thread_record(SimpleNamespace(**{**_RANGE, **changes}), True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"profile_id": 0}, id="without-a-profile"),
        pytest.param({"body": ""}, id="authored-with-an-empty-body"),
        pytest.param({"body": None}, id="authored-without-a-body"),
        pytest.param({"comment_id": None}, id="authored-without-a-comment"),
        pytest.param(
            {"expected_revision": 2}, id="authored-expecting-a-revision"
        ),
        pytest.param({"kind": "comment-edited"}, id="edit-without-a-revision"),
        pytest.param(
            {"kind": "comment-deleted", "expected_revision": 2},
            id="delete-keeping-a-body",
        ),
        pytest.param(
            {"kind": "comment-deleted", "body": None},
            id="delete-without-a-revision",
        ),
        pytest.param(
            {"kind": "thread-resolved", "body": None},
            id="resolved-with-a-comment-but-no-body",
        ),
        pytest.param(
            {"kind": "thread-resolved", "comment_id": None},
            id="resolved-with-a-body-but-no-comment",
        ),
        pytest.param(
            {"kind": "thread-reopened", "expected_revision": 2},
            id="reopened-expecting-a-revision",
        ),
        pytest.param({"kind": "thread-deleted"}, id="deleted-carrying-a-body"),
    ],
)
def test_invalid_action_is_refused(
    changes: dict[str, str | int | None],
) -> None:
    with pytest.raises(AssertionError):
        RoomStore._action_record(SimpleNamespace(**{**_ACTION, **changes}))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status_after", "attention_after", "message"),
    [
        pytest.param(
            "closed", "author", "status", id="status-outside-the-vocabulary"
        ),
        pytest.param(
            "open",
            "everyone",
            "attention",
            id="attention-outside-the-vocabulary",
        ),
        pytest.param("open", "", "attention", id="empty-attention"),
    ],
)
def test_persisted_action_enums_are_validated_on_read(
    status_after: str, attention_after: str, message: str
) -> None:
    columns = {**_ACTION, "status_after": status_after}
    columns["attention_after"] = attention_after
    action = SimpleNamespace(**columns)
    with pytest.raises(
        AssertionError, match=f"invalid persisted thread {message}"
    ):
        RoomStore._action_record(
            action  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param(
            {"metadata_path": "relative.json"}, id="relative-metadata"
        ),
        pytest.param({"metadata_hash": b"short"}, id="short-metadata-hash"),
        pytest.param(
            {"target_capture_path": "/capture/right-target"},
            id="target-path-without-hash",
        ),
        pytest.param(
            {"target_hash": b"t" * 32},
            id="target-hash-without-path",
        ),
        pytest.param(
            {
                "target_capture_path": "relative-target",
                "target_hash": b"t" * 32,
            },
            id="relative-target",
        ),
        pytest.param(
            {
                "target_capture_path": "/capture/right-target",
                "target_hash": b"short",
            },
            id="short-target-hash",
        ),
    ],
)
def test_invalid_snapshot_symlink_descriptor_is_refused(
    changes: dict[str, str | bytes | None],
) -> None:
    with pytest.raises(AssertionError):
        RoomStore._file_symlink_records(
            (  # type: ignore[arg-type]
                SimpleNamespace(
                    **{
                        "file_id": "file",
                        "side": "right",
                        "metadata_path": "/capture/right-link.json",
                        "metadata_hash": b"m" * 32,
                        "target_capture_path": None,
                        "target_hash": None,
                        **changes,
                    }
                ),
            )
        )
