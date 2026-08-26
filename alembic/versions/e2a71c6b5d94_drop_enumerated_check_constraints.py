"""Drop every check constraint that restates a Python vocabulary.

A column holding one of a fixed set of strings was constrained twice: once by
the `Literal` the record carries and the read boundary validates, and once by a
SQL `IN (...)` list. The two say the same thing in two languages, and only the
SQL copy costs a table rebuild to change, so adding one vocabulary value meant
writing a migration for a fact Python already enforced.

This revision removes the SQL copy. Eleven constraints go, in two groups.

The plain vocabulary lists — `ck_room_tab`, `ck_snapshot_file_change_type`,
`ck_snapshot_file_lazy_reason`, `ck_review_thread_target_kind`,
`ck_review_thread_side`, `ck_review_thread_outdated_reason`,
`ck_review_action_status_after`, `ck_review_action_attention_after`. Writes
reach these columns from typed records built out of tagged unions, so the
`Literal` is real there. Reads reach them through `Row[Any]`, where a `Literal`
annotation asserts nothing, so `RoomStore._thread_record` and
`RoomStore._action_record` match each persisted value and raise on anything
outside the vocabulary. `status_after` and `attention_after` had no such match
before this change and were guarded by their check constraint alone; they have
one now.

The shape contracts — `ck_review_thread_location`,
`ck_review_thread_placement_locator`, `ck_review_action_variant`. These are not
vocabulary lists, but each names vocabulary values inside a `CASE`, so a new
placement reason or action kind rebuilt these tables too. Their contract moves
to `ReviewThreadRecord.__post_init__` and `ReviewActionRecord.__post_init__`,
which both directions of persistence construct: one check there guards the
insert and the select alike. Those methods raise rather than assert, because
`-O` strips `assert` and they are now the only guard these invariants have.

What this gives up: an out-of-band writer — a hand-written `UPDATE`, some
future tool that is not this application — can store a row Python would have
refused. Every writer today goes through `RoomStore`, and every reader
constructs the record that checks it, so such a row is caught the first time it
is read rather than never.

What stays: identifier shape checks, non-empty text checks, ordinal ranges,
presence pairings such as `ck_room_mark_tab` and `ck_snapshot_meta_line_counts`,
foreign keys, and unique constraints. None of those restate a vocabulary, and
none of them cost a migration when a vocabulary grows.

`downgrade()` restores all eleven exactly as `b3d8f4a19c26` left them. The
rebuild re-validates every stored row, so a database holding a row the restored
constraints reject fails the downgrade instead of migrating silently.

Batch mode rebuilds each table on SQLite. Reflection supplies columns and
checks as the database has them, but `PRAGMA foreign_key_list` does not report
constraint names, so every rebuild is driven by a reflected table with the
declared foreign-key names restored on top. Reflection also does not report
index column ordering, so each `review_action` rebuild ends by restating
`ix_review_action_thread_activity` with its DESC.
"""

from collections.abc import Mapping, Sequence

import sqlalchemy as sa

from alembic import op

__all__ = [
    "branch_labels",
    "depends_on",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]

revision: str = "e2a71c6b5d94"
down_revision: str | Sequence[str] | None = "b3d8f4a19c26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _named_table(
    table_name: str, foreign_key_names: Mapping[tuple[str, ...], str]
) -> sa.Table:
    """Reflect one table with the given foreign-key names restored.

    Reflection is the source of truth for columns and check constraints, so
    this cannot drift from the database the way a transcribed definition would.
    Only the foreign-key names, which SQLite does not report, are supplied by
    the caller; a foreign key absent from the mapping stays anonymous, exactly
    as the database declares it.
    """
    table = sa.Table(table_name, sa.MetaData(), autoload_with=op.get_bind())
    for constraint in table.foreign_key_constraints:
        name = foreign_key_names.get(tuple(constraint.column_keys))
        if name is not None:
            constraint.name = name
    return table


def _placement_table() -> sa.Table:
    """Reflect `review_thread_placement` ready for `copy_from`.

    The foreign key on `snapshot_id` alone is deliberately left anonymous: the
    database declares that one without a name, so inventing one here would
    change the schema rather than preserve it.
    """
    return _named_table(
        "review_thread_placement",
        {
            ("snapshot_file_id", "snapshot_id"): (
                "fk_review_thread_placement_snapshot_file"
            ),
            ("thread_id",): "fk_review_thread_placement_thread",
        },
    )


def _action_table() -> sa.Table:
    """Reflect `review_action` ready for `copy_from`."""
    return _named_table(
        "review_action",
        {("thread_id", "snapshot_id"): "fk_review_action_thread"},
    )


def _restore_action_activity_index() -> None:
    """Recreate `ix_review_action_thread_activity` with its DESC ordering.

    SQLite reflection does not report per-column sort order, so a batch rebuild
    of `review_action` re-emits this index without the `DESC` the database
    declares. Every rebuild of that table therefore ends by restating it.
    """
    op.drop_index("ix_review_action_thread_activity", "review_action")
    op.create_index(
        "ix_review_action_thread_activity",
        "review_action",
        [sa.literal_column("thread_id"), sa.literal_column("activity_id DESC")],
    )


def _placement_location() -> sa.Case[bool]:
    """Return the placement-shape contract as `b3d8f4a19c26` declared it.

    Restated here rather than imported so this revision stays independent of
    the application's current model. Each branch tests its reason explicitly
    rather than by membership alone, because `outdated_reason IN (...)`
    evaluates to NULL for a NULL reason and a SQLite CHECK admits NULL.
    """
    return sa.case(
        (
            sa.column("snapshot_file_id").is_(None),
            sa.column("target_kind").is_(None)
            & sa.column("bay_key").is_(None)
            & sa.column("side").is_(None)
            & sa.column("start_line").is_(None)
            & sa.column("end_line").is_(None)
            & sa.column("outdated_reason").is_not(None)
            & (sa.column("outdated_reason") == "file_missing"),
        ),
        (
            sa.column("target_kind") == "range",
            sa.column("bay_key").is_not(None)
            & (sa.func.length(sa.column("bay_key")) > 0)
            & sa.column("side").is_not(None)
            & sa.column("start_line").is_not(None)
            & (sa.column("start_line") >= 1)
            & sa.column("end_line").is_not(None)
            & (sa.column("end_line") >= sa.column("start_line"))
            & (
                sa.column("outdated_reason").is_(None)
                | (sa.column("outdated_reason") == "region_changed")
            ),
        ),
        (
            sa.column("target_kind") == "bay-start",
            sa.column("bay_key").is_not(None)
            & (sa.func.length(sa.column("bay_key")) > 0)
            & sa.column("side").is_not(None)
            & sa.column("start_line").is_(None)
            & sa.column("end_line").is_(None)
            & sa.column("outdated_reason").is_not(None)
            & sa.column("outdated_reason").in_(
                ("region_not_found", "bay_not_found")
            ),
        ),
        (
            sa.column("target_kind") == "file-start",
            sa.column("bay_key").is_(None)
            & sa.column("side").is_not(None)
            & sa.column("start_line").is_(None)
            & sa.column("end_line").is_(None)
            & (
                sa.column("outdated_reason").is_(None)
                | (sa.column("outdated_reason") == "bay_not_found")
            ),
        ),
        else_=False,
    )


def _action_variant() -> sa.Case[bool]:
    """Return the action-shape contract as `b3d8f4a19c26` declared it."""
    return sa.case(
        (
            sa.column("kind").in_(("thread-created", "comment-created")),
            sa.column("thread_id").is_not(None)
            & sa.column("sequence").is_not(None)
            & sa.column("comment_id").is_not(None)
            & sa.column("expected_revision").is_(None)
            & sa.column("body").is_not(None)
            & (sa.func.length(sa.column("body")) > 0),
        ),
        (
            sa.column("kind") == "comment-edited",
            sa.column("thread_id").is_not(None)
            & sa.column("sequence").is_not(None)
            & sa.column("comment_id").is_not(None)
            & sa.column("expected_revision").is_not(None)
            & sa.column("body").is_not(None)
            & (sa.func.length(sa.column("body")) > 0),
        ),
        (
            sa.column("kind") == "comment-deleted",
            sa.column("thread_id").is_not(None)
            & sa.column("sequence").is_not(None)
            & sa.column("comment_id").is_not(None)
            & sa.column("expected_revision").is_not(None)
            & sa.column("body").is_(None),
        ),
        (
            sa.column("kind").in_(("thread-resolved", "thread-reopened")),
            sa.column("thread_id").is_not(None)
            & sa.column("sequence").is_not(None)
            & sa.column("expected_revision").is_(None)
            & (
                (
                    sa.column("comment_id").is_(None)
                    & sa.column("body").is_(None)
                )
                | (
                    sa.column("comment_id").is_not(None)
                    & sa.column("body").is_not(None)
                    & (sa.func.length(sa.column("body")) > 0)
                )
            ),
        ),
        (
            sa.column("kind") == "thread-deleted",
            sa.column("thread_id").is_not(None)
            & sa.column("sequence").is_not(None)
            & sa.column("comment_id").is_(None)
            & sa.column("expected_revision").is_(None)
            & sa.column("body").is_(None),
        ),
        else_=False,
    )


def upgrade() -> None:
    """Leave every vocabulary to the Python that already enforces it."""
    with op.batch_alter_table(
        "room", copy_from=_named_table("room", {})
    ) as batch:
        batch.drop_constraint("ck_room_tab", type_="check")

    with op.batch_alter_table(
        "snapshot_file", copy_from=_named_table("snapshot_file", {})
    ) as batch:
        batch.drop_constraint("ck_snapshot_file_change_type", type_="check")

    with op.batch_alter_table(
        "snapshot_file_lazy_reason",
        copy_from=_named_table("snapshot_file_lazy_reason", {}),
    ) as batch:
        batch.drop_constraint("ck_snapshot_file_lazy_reason", type_="check")

    with op.batch_alter_table(
        "review_thread_placement", copy_from=_placement_table()
    ) as batch:
        batch.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch.drop_constraint("ck_review_thread_side", type_="check")
        batch.drop_constraint("ck_review_thread_outdated_reason", type_="check")
        batch.drop_constraint("ck_review_thread_location", type_="check")
        batch.drop_constraint(
            "ck_review_thread_placement_locator", type_="check"
        )

    with op.batch_alter_table(
        "review_action", copy_from=_action_table()
    ) as batch:
        batch.drop_constraint("ck_review_action_status_after", type_="check")
        batch.drop_constraint("ck_review_action_attention_after", type_="check")
        batch.drop_constraint("ck_review_action_variant", type_="check")
    _restore_action_activity_index()


def downgrade() -> None:
    """Restate every dropped vocabulary and shape contract in SQL."""
    with op.batch_alter_table(
        "room", copy_from=_named_table("room", {})
    ) as batch:
        batch.create_check_constraint(
            "ck_room_tab",
            sa.column("tab").in_(
                ("head", "refs", "branch-review", "pull-request", "preset")
            ),
        )

    with op.batch_alter_table(
        "snapshot_file", copy_from=_named_table("snapshot_file", {})
    ) as batch:
        batch.create_check_constraint(
            "ck_snapshot_file_change_type",
            sa.column("change_type").in_(
                ("modify", "add", "delete", "rename", "copy")
            ),
        )

    with op.batch_alter_table(
        "snapshot_file_lazy_reason",
        copy_from=_named_table("snapshot_file_lazy_reason", {}),
    ) as batch:
        batch.create_check_constraint(
            "ck_snapshot_file_lazy_reason",
            sa.column("reason").in_(
                ("too_big", "generated", "deleted", "untracked", "pure_renamed")
            ),
        )

    with op.batch_alter_table(
        "review_thread_placement", copy_from=_placement_table()
    ) as batch:
        batch.create_check_constraint(
            "ck_review_thread_target_kind",
            sa.column("target_kind").is_(None)
            | sa.column("target_kind").in_(
                ("range", "bay-start", "file-start")
            ),
        )
        batch.create_check_constraint(
            "ck_review_thread_side",
            sa.column("side").is_(None)
            | sa.column("side").in_(("left", "right")),
        )
        batch.create_check_constraint(
            "ck_review_thread_outdated_reason",
            sa.column("outdated_reason").is_(None)
            | sa.column("outdated_reason").in_(
                (
                    "region_changed",
                    "region_not_found",
                    "bay_not_found",
                    "file_missing",
                )
            ),
        )
        batch.create_check_constraint(
            "ck_review_thread_location", _placement_location()
        )
        batch.create_check_constraint(
            "ck_review_thread_placement_locator",
            sa.column("private_locator").is_(None)
            | (
                (sa.column("target_kind") == "range")
                & sa.column("outdated_reason").is_(None)
            ),
        )

    with op.batch_alter_table(
        "review_action", copy_from=_action_table()
    ) as batch:
        batch.create_check_constraint(
            "ck_review_action_status_after",
            sa.column("status_after").in_(("open", "resolved", "deleted")),
        )
        batch.create_check_constraint(
            "ck_review_action_attention_after",
            sa.column("attention_after").in_(
                ("author", "reviewer", "both", "none")
            ),
        )
        batch.create_check_constraint(
            "ck_review_action_variant", _action_variant()
        )
    _restore_action_activity_index()
