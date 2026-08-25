"""Drive stored placement rows through the `b3d8f4a19c26` bay-key migration.

The revision rewrites an `ordinary` placement's key to `flatfile`, strips the
two dead notebook fields from every stored locator, moves derived File-start
rows from `region_not_found` to the split `bay_not_found` reason, and leaves
every other stored shape untouched. Its downgrade refuses while a notebook
placement exists, because the locator fields it discarded cannot be recovered,
and otherwise collapses both split shapes back to the pre-split vocabulary,
dropping a bay-start row's bay coordinate.

Schema parity proves the migrated schema text matches the model; this module
proves the data moves touch exactly the intended rows, that the refusal leaves
the database untouched rather than half-downgraded, and that the recreated
check constraints actually reject the shapes each vocabulary forbids — in
particular that a NULL reason cannot slip through the bay-start branch via
SQL's `NULL IN (...)` semantics.
"""

import json
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, delete, select
from sqlalchemy.exc import IntegrityError

from alembic import command

__all__ = ["test_bay_key_migration_moves_stored_placements"]


def test_bay_key_migration_moves_stored_placements(tmp_path: Path) -> None:
    """Upgrade re-keys and splits in place; downgrade refuses, then restores."""
    database_path = tmp_path / "prebay.sqlite"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.attributes["db_path"] = database_path
    command.upgrade(config, "a4d9f0c2e711")

    engine = create_engine(f"sqlite:///{database_path}")
    prebay = MetaData()
    prebay.reflect(bind=engine)
    snapshot_id = "a" * 32
    file_id = "b" * 32
    with engine.begin() as connection:
        connection.execute(
            prebay.tables["repo_mark"]
            .insert()
            .values(id=1, path=str(tmp_path), active=True)
        )
        connection.execute(
            prebay.tables["room"]
            .insert()
            .values(id=1, mark_id=1, tab="head", backend_key=b"prebay")
        )
        connection.execute(
            prebay.tables["snapshot"]
            .insert()
            .values(id=snapshot_id, room_id=1, content_hash=b"s" * 32)
        )
        connection.execute(
            prebay.tables["snapshot_file"]
            .insert()
            .values(
                id=file_id,
                snapshot_id=snapshot_id,
                path=str(tmp_path / "capture"),
                tracked=True,
                change_type="modify",
                error=None,
            )
        )
        for thread_id in (f"{digit}" * 32 for digit in "1234567"):
            connection.execute(
                prebay.tables["review_thread"]
                .insert()
                .values(thread_id=thread_id, origin_snapshot_id=snapshot_id)
            )
        # One row per pre-bay shape. The first is re-keyed and has its locator
        # stripped, the second keeps the key it already stored, and the third —
        # a derived File-start row, recognizable by its reason — takes the
        # split reason. The last two must come through untouched.
        for thread_id, values in (
            (
                "1" * 32,
                {
                    "snapshot_file_id": file_id,
                    "target_kind": "range",
                    "region_kind": "ordinary",
                    "region_key": None,
                    "side": "right",
                    "start_line": 3,
                    "end_line": 4,
                    "outdated_reason": None,
                    "private_locator": json.dumps(
                        {
                            "notebook_cell_id": "c1",
                            "notebook_source_hash": "h1",
                            "side": "right",
                            "start_line": 3,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                },
            ),
            (
                "2" * 32,
                {
                    "snapshot_file_id": file_id,
                    "target_kind": "range",
                    "region_kind": "notebook-cell-source",
                    "region_key": "cell-1-1",
                    "side": "left",
                    "start_line": 7,
                    "end_line": 7,
                    "outdated_reason": "region_changed",
                    "private_locator": None,
                },
            ),
            (
                "3" * 32,
                {
                    "snapshot_file_id": file_id,
                    "target_kind": "file-start",
                    "region_kind": None,
                    "region_key": None,
                    "side": "left",
                    "start_line": None,
                    "end_line": None,
                    "outdated_reason": "region_not_found",
                    "private_locator": None,
                },
            ),
            (
                "4" * 32,
                {
                    "snapshot_file_id": file_id,
                    "target_kind": "file-start",
                    "region_kind": None,
                    "region_key": None,
                    "side": "right",
                    "start_line": None,
                    "end_line": None,
                    "outdated_reason": None,
                    "private_locator": None,
                },
            ),
            (
                "5" * 32,
                {
                    "snapshot_file_id": None,
                    "target_kind": None,
                    "region_kind": None,
                    "region_key": None,
                    "side": None,
                    "start_line": None,
                    "end_line": None,
                    "outdated_reason": "file_missing",
                    "private_locator": None,
                },
            ),
        ):
            connection.execute(
                prebay.tables["review_thread_placement"]
                .insert()
                .values(thread_id=thread_id, snapshot_id=snapshot_id, **values)
            )
    engine.dispose()

    command.upgrade(config, "b3d8f4a19c26")
    bay_engine = create_engine(f"sqlite:///{database_path}")
    bay = MetaData()
    bay.reflect(bind=bay_engine)
    placement = bay.tables["review_thread_placement"]
    assert "region_kind" not in placement.c
    with bay_engine.connect() as connection:
        migrated = [
            tuple(row)
            for row in connection.execute(
                select(
                    placement.c.target_kind,
                    placement.c.bay_key,
                    placement.c.side,
                    placement.c.start_line,
                    placement.c.end_line,
                    placement.c.outdated_reason,
                    placement.c.private_locator,
                ).order_by(placement.c.thread_id)
            )
        ]
    assert migrated == [
        (
            "range",
            "flatfile",
            "right",
            3,
            4,
            None,
            b'{"side":"right","start_line":3}',
        ),
        ("range", "cell-1-1", "left", 7, 7, "region_changed", None),
        ("file-start", None, "left", None, None, "bay_not_found", None),
        ("file-start", None, "right", None, None, None, None),
        (None, None, None, None, None, "file_missing", None),
    ]

    # The recreated checks must accept both split bay-start reasons...
    with bay_engine.begin() as connection:
        for thread_id, bay_key, reason in (
            ("6" * 32, "cell-b", "region_not_found"),
            ("7" * 32, "cell-a", "bay_not_found"),
        ):
            connection.execute(
                placement.insert().values(
                    thread_id=thread_id,
                    snapshot_id=snapshot_id,
                    snapshot_file_id=file_id,
                    target_kind="bay-start",
                    bay_key=bay_key,
                    side="right",
                    start_line=None,
                    end_line=None,
                    outdated_reason=reason,
                    private_locator=None,
                )
            )
    # ...and reject every shape the bay vocabulary forbids. The NULL reason is
    # the adversarial case: `outdated_reason IN (...)` alone evaluates to NULL
    # for it and would pass a SQLite CHECK.
    forbidden_shapes: tuple[dict[str, object], ...] = (
        {"target_kind": "bay-start", "bay_key": "cell-a", "side": "right"},
        {
            "target_kind": "bay-start",
            "bay_key": "cell-a",
            "side": "right",
            "outdated_reason": "region_changed",
        },
        {
            "target_kind": "bay-start",
            "bay_key": "cell-a",
            "side": "right",
            "start_line": 1,
            "end_line": 1,
            "outdated_reason": "bay_not_found",
        },
        {
            "target_kind": "file-start",
            "side": "right",
            "outdated_reason": "region_not_found",
        },
        {
            "target_kind": "range",
            "bay_key": "flatfile",
            "side": "right",
            "start_line": 1,
            "end_line": 1,
            "outdated_reason": "bay_not_found",
        },
    )
    for values in forbidden_shapes:
        with pytest.raises(IntegrityError), bay_engine.begin() as connection:
            connection.execute(
                placement.insert().values(
                    thread_id="8" * 32,
                    snapshot_id=snapshot_id,
                    snapshot_file_id=file_id,
                    **values,
                )
            )
    bay_engine.dispose()

    # The notebook placement's locator fields were discarded irrecoverably, so
    # the downgrade must refuse rather than hand the previous revision a
    # locator its own reads reject.
    with pytest.raises(RuntimeError, match="notebook review placement"):
        command.downgrade(config, "a4d9f0c2e711")
    refused_engine = create_engine(f"sqlite:///{database_path}")
    refused = MetaData()
    refused.reflect(bind=refused_engine)
    # A batch rebuild leaves its `_alembic_tmp_*` table behind when the
    # surrounding transaction rolls back, so a refusal raised after any
    # rebuild would wedge the database against every later attempt. These
    # pin that the refusal touched nothing at all.
    assert "bay_key" in refused.tables["review_thread_placement"].c
    assert "ck_review_action_snapshot_id" in {
        constraint.name
        for constraint in refused.tables["review_action"].constraints
    }
    assert [
        name for name in refused.tables if name.startswith("_alembic_tmp_")
    ] == []
    with refused_engine.begin() as connection:
        connection.execute(
            delete(refused.tables["review_thread_placement"]).where(
                refused.tables["review_thread_placement"].c.thread_id
                == "2" * 32
            )
        )
    refused_engine.dispose()

    command.downgrade(config, "a4d9f0c2e711")
    downgraded_engine = create_engine(f"sqlite:///{database_path}")
    downgraded = MetaData()
    downgraded.reflect(bind=downgraded_engine)
    old_placement = downgraded.tables["review_thread_placement"]
    with downgraded_engine.connect() as connection:
        restored = [
            tuple(row)
            for row in connection.execute(
                select(
                    old_placement.c.target_kind,
                    old_placement.c.region_kind,
                    old_placement.c.region_key,
                    old_placement.c.side,
                    old_placement.c.start_line,
                    old_placement.c.end_line,
                    old_placement.c.outdated_reason,
                    old_placement.c.private_locator,
                ).order_by(old_placement.c.thread_id)
            )
        ]
    # The re-keyed range placement returns to `ordinary` with no key and its
    # locator carries the two restored fields as null; both bay-start rows
    # collapse to the exact shape the pre-split derivation produced for a lost
    # bay, dropping the bay coordinate.
    assert restored == [
        (
            "range",
            "ordinary",
            None,
            "right",
            3,
            4,
            None,
            b'{"notebook_cell_id":null,"notebook_source_hash":null,'
            b'"side":"right","start_line":3}',
        ),
        (
            "file-start",
            None,
            None,
            "left",
            None,
            None,
            "region_not_found",
            None,
        ),
        ("file-start", None, None, "right", None, None, None, None),
        (None, None, None, None, None, None, "file_missing", None),
        (
            "file-start",
            None,
            None,
            "right",
            None,
            None,
            "region_not_found",
            None,
        ),
        (
            "file-start",
            None,
            None,
            "right",
            None,
            None,
            "region_not_found",
            None,
        ),
    ]
    # The restored checks refuse the bay vocabulary again.
    with (
        pytest.raises(IntegrityError),
        downgraded_engine.begin() as connection,
    ):
        connection.execute(
            old_placement.insert().values(
                thread_id="8" * 32,
                snapshot_id=snapshot_id,
                snapshot_file_id=file_id,
                target_kind="file-start",
                side="right",
                outdated_reason="bay_not_found",
            )
        )
    downgraded_engine.dispose()
