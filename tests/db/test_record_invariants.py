"""Prove the record dataclasses reject what the schema no longer refuses.

Revision `e2a71c6b5d94` dropped the eleven check constraints that restated a
Python vocabulary or spelled a variant shape as a SQL `CASE`. Those contracts
now live in `ReviewThreadRecord.__post_init__`, `ReviewActionRecord`'s, and the
matches `RoomStore` applies to persisted enum columns, and nothing else guards
them. This module is the adversarial half of that move: for every shape the
dropped constraints forbade, it constructs the record that would carry it and
requires the construction to fail — and, for the shapes review genuinely
produces, requires it to succeed.

Emphasis falls on the shapes SQL admitted only by accident of NULL semantics
and on the fields that had no Python check at all before the migration,
`status_after` and `attention_after`, since those two columns were guarded by
their constraint alone.

Some cases intentionally cross typed boundaries with invalid field shapes or
persisted values. Those calls carry narrow type-checker suppressions because
accepting the values in the surrounding test code would hide the contract the
test is meant to violate.
"""

from types import SimpleNamespace

import pytest

from dirdiff.db import (
    ReviewActionRecord,
    ReviewThreadRecord,
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
"""Valid live range origin varied by every placement invariant case.

Each parametrized case changes only the fields involved in its claim, so a
failure identifies one tagged-placement relationship rather than rebuilding a
large unrelated record per case.
"""

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
"""Valid authored Comment action varied by every action invariant case.

The baseline supplies complete persisted context. Each case changes the one
variant or lifecycle relationship it intends to accept or reject.
"""


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({}, id="live-range"),
        pytest.param(
            {"outdated_reason": "region_changed", "private_locator": None},
            id="outdated-range",
        ),
        pytest.param(
            {
                "target_kind": "bay-start",
                "start_line": None,
                "end_line": None,
                "outdated_reason": "region_not_found",
                "private_locator": None,
            },
            id="bay-start-region-lost",
        ),
        pytest.param(
            {
                "target_kind": "bay-start",
                "start_line": None,
                "end_line": None,
                "outdated_reason": "bay_not_found",
                "private_locator": None,
            },
            id="bay-start-bay-lost",
        ),
        pytest.param(
            {
                "target_kind": "file-start",
                "bay_key": None,
                "start_line": None,
                "end_line": None,
                "private_locator": None,
            },
            id="file-start-current",
        ),
        pytest.param(
            {
                "target_kind": "file-start",
                "bay_key": None,
                "start_line": None,
                "end_line": None,
                "outdated_reason": "bay_not_found",
                "private_locator": None,
            },
            id="file-start-bay-lost",
        ),
        pytest.param(
            {
                "snapshot_file_id": None,
                "target_kind": None,
                "bay_key": None,
                "side": None,
                "start_line": None,
                "end_line": None,
                "outdated_reason": "file_missing",
                "private_locator": None,
            },
            id="file-missing",
        ),
    ],
)
def test_valid_placements_construct(
    changes: dict[str, str | int | None],
) -> None:
    """Every placement review derivation emits must still be constructible.

    The four tagged-union variants and both outdated forms of a range are the
    whole set `dirdiff.review` produces, so a check that rejected one of them
    would break persistence outright rather than merely tighten it.
    """
    record = ReviewThreadRecord(
        **{**_RANGE, **changes}  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    )
    assert record.thread_id == _RANGE["thread_id"]


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
    """No placement outside the tagged union may be constructed.

    Each case is a row the dropped `ck_review_thread_location` or
    `ck_review_thread_placement_locator` rejected. Construction failing here is
    what now stops such a row from being inserted and what stops a hand-edited
    one from being read as if it made sense.
    """
    with pytest.raises(AssertionError):
        ReviewThreadRecord(
            **{**_RANGE, **changes}  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({}, id="comment-created"),
        pytest.param({"kind": "thread-created"}, id="thread-created"),
        pytest.param(
            {"kind": "comment-edited", "expected_revision": 2},
            id="comment-edited",
        ),
        pytest.param(
            {
                "kind": "comment-deleted",
                "expected_revision": 2,
                "body": None,
            },
            id="comment-deleted",
        ),
        pytest.param(
            {"kind": "thread-resolved", "comment_id": None, "body": None},
            id="resolved-alone",
        ),
        pytest.param({"kind": "thread-resolved"}, id="resolved-with-a-comment"),
        pytest.param(
            {"kind": "thread-reopened", "comment_id": None, "body": None},
            id="reopened-alone",
        ),
        pytest.param(
            {"kind": "thread-deleted", "comment_id": None, "body": None},
            id="thread-deleted",
        ),
    ],
)
def test_valid_actions_construct(changes: dict[str, str | int | None]) -> None:
    """Every action variant the review API writes must stay constructible.

    The cases exercise discriminator-specific nullable fields so record validation
    cannot accidentally reject a server-supported operation shape.
    """
    record = ReviewActionRecord(
        **{**_ACTION, **changes}  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    )
    assert record.operation_id == _ACTION["operation_id"]


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
    """No action whose fields contradict its kind may be constructed.

    Each case is a row `ck_review_action_variant` rejected, plus the Profile
    identity that `__post_init__` has always required.
    """
    with pytest.raises(AssertionError):
        ReviewActionRecord(
            **{**_ACTION, **changes}  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )


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
    """A stored lifecycle value outside its vocabulary must fail the read.

    Mapped string columns cannot express the record's narrower lifecycle
    vocabulary, so the read boundary must still reject values outside it.

    # Parameters

    - `status_after`: Persisted lifecycle value substituted into the test row.
    - `attention_after`: Persisted attention value substituted into that row.
    - `message`: Expected invalid-field fragment proving which boundary failed.
    """
    columns = {**_ACTION, "status_after": status_after}
    columns["attention_after"] = attention_after
    action = SimpleNamespace(**columns)
    with pytest.raises(
        AssertionError, match=f"invalid persisted thread {message}"
    ):
        RoomStore._action_record(
            action  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )
