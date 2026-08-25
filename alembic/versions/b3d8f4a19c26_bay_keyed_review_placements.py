"""Key review placements by composed bay, and reconcile the declared model.

Revision ID: b3d8f4a19c26
Revises: a4d9f0c2e711

A review target used to name a region by a format-shaped kind: `ordinary` text,
or `notebook-cell-source` plus a cell key. Composition now produces one public
key per bay for every format, and that key is durable identity, so a target
needs nothing but the key. This revision brings the placement table to that
contract, splits the lost-bay vocabulary derivation now produces, and finishes
the constraint reconciliation `f63b8a1d2e40` left half-done.

`review_thread_placement` ends with:

- `region_kind` dropped. It carried no information once every format composed
  keyed bays, and the two constraints expressing its agreement with the key —
  `ck_review_thread_region_kind` and `ck_review_thread_region` — go with it.
  The requirement that mattered, that a range placement names a non-empty bay,
  moves into `ck_review_thread_location`, which already distinguishes the
  placement shapes.
- `region_key` renamed to `bay_key`. The composed unit a frame holds is a bay,
  not a region: `region` now names only a span of source content — the origin a
  Thread reattaches against, a fold hint, an engine's internals.
- `bay-start` added to `ck_review_thread_target_kind` and `bay_not_found` to
  `ck_review_thread_outdated_reason`. Derivation places every lost region on a
  composed bay when one exists: `bay-start` with `region_not_found` keeps the
  origin's own bay, and `bay_not_found` lands on the File's first bay carrying
  the side after the origin's bay is gone. Only a File with nothing to land on
  falls to `file-start`, whose reason is now `bay_not_found`.
- `ck_review_thread_id` renamed to `ck_review_thread_placement_id` and
  `fk_review_thread_snapshot_file` to
  `fk_review_thread_placement_snapshot_file`, the names `room.py` declares.

`review_action` gains `ck_review_action_snapshot_id` and restates
`ck_review_action_variant` with the `thread_id`/`sequence` identity terms the
declared model states; those terms are implied by the columns' NOT NULL, so the
one enforcement this adds is the 32-hex shape of `snapshot_id`.
`review_thread.origin_snapshot_id` takes the VARCHAR(32) type the model derives
from the `snapshot.id` it references.

Three data moves accompany the schema:

- A placement whose `region_kind` was `ordinary` gains `flatfile`, the key a
  structureless File composes its one bay under, so its Thread stays anchored
  where its author put it. A notebook placement keeps the key it already
  stored.
- Every stored locator loses `notebook_cell_id` and `notebook_source_hash`. The
  bay key now finds a cell again after its source changed, so both fields are
  dead — and a locator is private JSON read back under an exact-field-set
  assertion, so a field left behind fails every later read of that Thread
  rather than being quietly ignored.
- A `file-start` row reading `region_not_found` becomes `bay_not_found`. Stored
  rows predate the split: the pre-split derivation collapsed both losses into
  one shape and never stored the bay coordinate, so `bay_not_found` is true but
  coarser. A `file-start` row with no reason is a historical File-level origin
  and is untouched, as is every other shape. No stored row becomes `bay-start`;
  only future derivation produces that kind.

Notebook cell keys themselves changed from positional (`cell-1-1`) to the
notebook's own cell ids. A notebook placement created before this revision
therefore names a key its File no longer composes, and review reports it as
unplaceable rather than guessing. That is the documented drift outcome, and
notebook review targets have no users today.

Dropping the two locator fields makes the downgrade conditional: their values
are kept nowhere, so once a notebook placement exists no downgrade can restore
the locator the previous revision's code asserts on. `downgrade()` therefore
refuses while any notebook placement exists instead of backfilling `None` and
handing the previous revision data that fails its own reads. A database whose
placements are all File-level or flatfile downgrades completely.

Batch mode rebuilds each table on SQLite. Check constraints survive that
rebuild through reflection, but `PRAGMA foreign_key_list` does not report
constraint names, so a reflected foreign key comes back anonymous and would be
rebuilt without its name. Every rebuild here is therefore driven by a reflected
table with the declared foreign-key names restored on top: reflection supplies
every column and check exactly as the database has them, and only the names
SQLite cannot report are supplied by this module. Reflection also does not
report index column ordering, so each `review_action` rebuild ends by restating
`ix_review_action_thread_activity` with its DESC.

The placement rebuilds come in three passes because a constraint and the column
it names cannot change together: a reflected constraint referencing
`region_key` would be re-emitted against a table that no longer has it. The
first pass drops every check whose current form forbids the rows this revision
writes or names a column it removes, the data moves run while nothing
constrains that vocabulary, the second pass changes the columns and the two
leftover names, and the third pass creates the checks in their new form. That
last copy re-validates every stored row, so a row outside the expected shapes
aborts this revision instead of being migrated silently.
"""

import json
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

revision: str = "b3d8f4a19c26"
down_revision: str | Sequence[str] | None = "a4d9f0c2e711"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLATFILE_BAY_KEY = "flatfile"
"""`FLATFILE_BAY_KEY` in `dirdiff.formats`, restated so this revision is
independent of the application's current constants."""

_OLD_SNAPSHOT_FILE_FK = "fk_review_thread_snapshot_file"
"""The composite foreign-key name a database at `a4d9f0c2e711` carries."""

_NEW_SNAPSHOT_FILE_FK = "fk_review_thread_placement_snapshot_file"
"""The composite foreign-key name `room.py` declares."""

_OLD_PLACEMENT = sa.table(
    "review_thread_placement",
    sa.column("thread_id"),
    sa.column("snapshot_id"),
    sa.column("target_kind"),
    sa.column("region_kind"),
    sa.column("region_key"),
    sa.column("outdated_reason"),
    sa.column("private_locator"),
)
"""Update-statement handle in the pre-bay column vocabulary.

Not a schema declaration: it names only the columns the data moves read and
write, and it is valid only while the table still carries `region_kind` and
`region_key`.
"""

_BAY_PLACEMENT = sa.table(
    "review_thread_placement",
    sa.column("target_kind"),
    sa.column("bay_key"),
)
"""Statement handle for the one downgrade read taken before the rename back."""


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


def _placement_table(snapshot_file_fk: str) -> sa.Table:
    """Reflect `review_thread_placement` ready for `copy_from`.

    The composite foreign key is carried under the name it has at the moment of
    the rebuild, so a pass that drops it by name can find it. The foreign key
    on `snapshot_id` alone is deliberately left anonymous: the database
    declares that one without a name, so inventing one here would change the
    schema rather than preserve it.
    """
    return _named_table(
        "review_thread_placement",
        {
            ("snapshot_file_id", "snapshot_id"): snapshot_file_fk,
            ("thread_id",): "fk_review_thread_placement_thread",
        },
    )


def _action_table() -> sa.Table:
    """Reflect `review_action` ready for `copy_from`."""
    return _named_table(
        "review_action",
        {("thread_id", "snapshot_id"): "fk_review_action_thread"},
    )


def _thread_id_shape() -> sa.ColumnElement[bool]:
    """Return the 32-hex shape check both placement id names constrain."""
    return (sa.func.length(sa.column("thread_id")) == 32) & sa.column(
        "thread_id"
    ).op("NOT GLOB")("*[^0-9a-f]*")


def _location_over_bay_key() -> sa.Case[bool]:
    """Return the placement-shape contract in the bay vocabulary.

    A File-missing placement carries no key, a `range` placement carries a
    non-empty one with a line span, a `bay-start` placement carries a non-empty
    one with no span and a lost-region reason, and a `file-start` placement
    carries none. Each branch tests its reason explicitly rather than by
    membership alone, because `outdated_reason IN (...)` evaluates to NULL for
    a NULL reason and a SQLite CHECK admits NULL.
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


def _location_over_region_kind() -> sa.Case[bool]:
    """Return the placement-shape contract this revision replaces.

    A range placement is named by `region_kind` alone here; the key it may
    carry is constrained by `ck_review_thread_region` instead.
    """
    return sa.case(
        (
            sa.column("snapshot_file_id").is_(None),
            sa.column("target_kind").is_(None)
            & sa.column("region_kind").is_(None)
            & sa.column("region_key").is_(None)
            & sa.column("side").is_(None)
            & sa.column("start_line").is_(None)
            & sa.column("end_line").is_(None)
            & sa.column("outdated_reason").is_not(None)
            & (sa.column("outdated_reason") == "file_missing"),
        ),
        (
            sa.column("target_kind") == "range",
            sa.column("region_kind").is_not(None)
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
            sa.column("target_kind") == "file-start",
            sa.column("region_kind").is_(None)
            & sa.column("region_key").is_(None)
            & sa.column("side").is_not(None)
            & sa.column("start_line").is_(None)
            & sa.column("end_line").is_(None)
            & (
                sa.column("outdated_reason").is_(None)
                | (sa.column("outdated_reason") == "region_not_found")
            ),
        ),
        else_=False,
    )


def _action_variant(with_identity_terms: bool) -> sa.Case[bool]:
    """Return the `review_action` per-kind shape contract.

    Both sides of this revision state the same five kind branches; the declared
    form additionally requires `thread_id` and `sequence` in each, which the
    columns' NOT NULL already implies. Building both forms from one expression
    keeps the branches from being transcribed twice.
    """
    identity = (
        sa.column("thread_id").is_not(None) & sa.column("sequence").is_not(None)
        if with_identity_terms
        else sa.true()
    )
    return sa.case(
        (
            sa.column("kind").in_(("thread-created", "comment-created")),
            identity
            & sa.column("comment_id").is_not(None)
            & sa.column("expected_revision").is_(None)
            & sa.column("body").is_not(None)
            & (sa.func.length(sa.column("body")) > 0),
        ),
        (
            sa.column("kind") == "comment-edited",
            identity
            & sa.column("comment_id").is_not(None)
            & sa.column("expected_revision").is_not(None)
            & sa.column("body").is_not(None)
            & (sa.func.length(sa.column("body")) > 0),
        ),
        (
            sa.column("kind") == "comment-deleted",
            identity
            & sa.column("comment_id").is_not(None)
            & sa.column("expected_revision").is_not(None)
            & sa.column("body").is_(None),
        ),
        (
            sa.column("kind").in_(("thread-resolved", "thread-reopened")),
            identity
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
            identity
            & sa.column("comment_id").is_(None)
            & sa.column("expected_revision").is_(None)
            & sa.column("body").is_(None),
        ),
        else_=False,
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


def _rewrite_locators(*, drop_notebook_fields: bool) -> None:
    """Rewrite every stored locator to the field set its revision requires.

    A locator is private JSON read back under an exact-field-set assertion, so
    a field left behind by the wrong revision fails every later read of that
    Thread rather than being quietly ignored.
    """
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            _OLD_PLACEMENT.c.thread_id,
            _OLD_PLACEMENT.c.snapshot_id,
            _OLD_PLACEMENT.c.private_locator,
        ).where(_OLD_PLACEMENT.c.private_locator.is_not(None))
    ).fetchall()
    for thread_id, snapshot_id, payload in rows:
        value = json.loads(bytes(payload))
        if drop_notebook_fields:
            value.pop("notebook_cell_id", None)
            value.pop("notebook_source_hash", None)
        else:
            value.setdefault("notebook_cell_id", None)
            value.setdefault("notebook_source_hash", None)
        connection.execute(
            _OLD_PLACEMENT.update()
            .where(
                (_OLD_PLACEMENT.c.thread_id == thread_id)
                & (_OLD_PLACEMENT.c.snapshot_id == snapshot_id)
            )
            .values(
                private_locator=json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                ).encode()
            )
        )


def upgrade() -> None:
    """Key every review placement by its composed bay alone."""
    with op.batch_alter_table(
        "review_thread_placement",
        copy_from=_placement_table(_OLD_SNAPSHOT_FILE_FK),
    ) as batch:
        batch.drop_constraint("ck_review_thread_region_kind", type_="check")
        batch.drop_constraint("ck_review_thread_region", type_="check")
        batch.drop_constraint("ck_review_thread_location", type_="check")
        batch.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch.drop_constraint("ck_review_thread_outdated_reason", type_="check")

    connection = op.get_bind()
    # A structureless File composes its one bay under this exact key, so an
    # existing ordinary placement keeps naming the bay it was created against.
    connection.execute(
        _OLD_PLACEMENT.update()
        .where(_OLD_PLACEMENT.c.region_kind == "ordinary")
        .values(region_key=FLATFILE_BAY_KEY)
    )
    _rewrite_locators(drop_notebook_fields=True)
    connection.execute(
        _OLD_PLACEMENT.update()
        .where(
            (_OLD_PLACEMENT.c.target_kind == "file-start")
            & (_OLD_PLACEMENT.c.outdated_reason == "region_not_found")
        )
        .values(outdated_reason="bay_not_found")
    )

    with op.batch_alter_table(
        "review_thread_placement",
        copy_from=_placement_table(_OLD_SNAPSHOT_FILE_FK),
    ) as batch:
        batch.drop_column("region_kind")
        batch.alter_column("region_key", new_column_name="bay_key")
        batch.drop_constraint("ck_review_thread_id", type_="check")
        batch.create_check_constraint(
            "ck_review_thread_placement_id", _thread_id_shape()
        )
        batch.drop_constraint(_OLD_SNAPSHOT_FILE_FK, type_="foreignkey")
        batch.create_foreign_key(
            _NEW_SNAPSHOT_FILE_FK,
            "snapshot_file",
            ["snapshot_file_id", "snapshot_id"],
            ["id", "snapshot_id"],
        )

    with op.batch_alter_table(
        "review_thread_placement",
        copy_from=_placement_table(_NEW_SNAPSHOT_FILE_FK),
    ) as batch:
        batch.create_check_constraint(
            "ck_review_thread_target_kind",
            sa.column("target_kind").is_(None)
            | sa.column("target_kind").in_(
                ("range", "bay-start", "file-start")
            ),
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
            "ck_review_thread_location", _location_over_bay_key()
        )

    with op.batch_alter_table(
        "review_action", copy_from=_action_table()
    ) as batch:
        batch.drop_constraint("ck_review_action_variant", type_="check")
        batch.create_check_constraint(
            "ck_review_action_variant",
            _action_variant(with_identity_terms=True),
        )
        batch.create_check_constraint(
            "ck_review_action_snapshot_id",
            sa.column("snapshot_id").is_not(None)
            & (sa.func.length(sa.column("snapshot_id")) == 32)
            & sa.column("snapshot_id").op("NOT GLOB")("*[^0-9a-f]*"),
        )
    _restore_action_activity_index()

    with op.batch_alter_table(
        "review_thread", copy_from=_named_table("review_thread", {})
    ) as batch:
        batch.alter_column("origin_snapshot_id", type_=sa.String(32))


def downgrade() -> None:
    """Restore format-shaped region kinds and the pre-split vocabulary.

    A bay key that is not the flatfile one is a notebook cell source, the only
    keyed region the previous revision could express. That revision requires
    every notebook cell source locator to carry a `notebook_source_hash`, and
    `upgrade()` discarded those values irrecoverably, so this refuses while any
    such placement exists rather than restore rows the previous revision cannot
    read. A `bay-start` row collapses to a File-start row without its bay
    coordinate — exactly the shape the pre-split derivation produced for it.
    """
    # The refusal is the first thing this function does. A batch rebuild
    # leaves its `_alembic_tmp_*` table behind when the surrounding
    # transaction rolls back, so a refusal raised after any rebuild would
    # wedge the database against every later attempt.
    connection = op.get_bind()
    notebook_placements = connection.execute(
        sa.select(sa.func.count())
        .select_from(_BAY_PLACEMENT)
        .where(
            (_BAY_PLACEMENT.c.target_kind == "range")
            & (_BAY_PLACEMENT.c.bay_key != FLATFILE_BAY_KEY)
        )
    ).scalar_one()
    if notebook_placements:
        raise RuntimeError(
            f"{notebook_placements} notebook review placement(s) exist, and "
            "their locators cannot be restored to the previous revision's "
            "shape: upgrade discarded notebook_cell_id and "
            "notebook_source_hash irrecoverably. Delete those Threads before "
            "downgrading, or keep this revision."
        )

    with op.batch_alter_table(
        "review_thread", copy_from=_named_table("review_thread", {})
    ) as batch:
        batch.alter_column("origin_snapshot_id", type_=sa.String())

    with op.batch_alter_table(
        "review_action", copy_from=_action_table()
    ) as batch:
        batch.drop_constraint("ck_review_action_snapshot_id", type_="check")
        batch.drop_constraint("ck_review_action_variant", type_="check")
        batch.create_check_constraint(
            "ck_review_action_variant",
            _action_variant(with_identity_terms=False),
        )
    _restore_action_activity_index()

    with op.batch_alter_table(
        "review_thread_placement",
        copy_from=_placement_table(_NEW_SNAPSHOT_FILE_FK),
    ) as batch:
        batch.drop_constraint("ck_review_thread_location", type_="check")
        batch.drop_constraint("ck_review_thread_target_kind", type_="check")
        batch.drop_constraint("ck_review_thread_outdated_reason", type_="check")
        batch.alter_column("bay_key", new_column_name="region_key")
        batch.add_column(sa.Column("region_kind", sa.String, nullable=True))
        batch.drop_constraint("ck_review_thread_placement_id", type_="check")
        batch.create_check_constraint("ck_review_thread_id", _thread_id_shape())
        batch.drop_constraint(_NEW_SNAPSHOT_FILE_FK, type_="foreignkey")
        batch.create_foreign_key(
            _OLD_SNAPSHOT_FILE_FK,
            "snapshot_file",
            ["snapshot_file_id", "snapshot_id"],
            ["id", "snapshot_id"],
        )

    connection.execute(
        _OLD_PLACEMENT.update()
        .where(
            (_OLD_PLACEMENT.c.target_kind == "file-start")
            & (_OLD_PLACEMENT.c.outdated_reason == "bay_not_found")
        )
        .values(outdated_reason="region_not_found")
    )
    connection.execute(
        _OLD_PLACEMENT.update()
        .where(_OLD_PLACEMENT.c.target_kind == "bay-start")
        .values(
            target_kind="file-start",
            region_key=None,
            outdated_reason="region_not_found",
        )
    )
    _rewrite_locators(drop_notebook_fields=False)
    # The restored contract requires the kind before it can be enforced, and
    # the refusal above proves every surviving range placement is flatfile.
    connection.execute(
        _OLD_PLACEMENT.update()
        .where(_OLD_PLACEMENT.c.target_kind == "range")
        .values(region_kind="ordinary", region_key=None)
    )

    with op.batch_alter_table(
        "review_thread_placement",
        copy_from=_placement_table(_OLD_SNAPSHOT_FILE_FK),
    ) as batch:
        batch.create_check_constraint(
            "ck_review_thread_target_kind",
            sa.column("target_kind").is_(None)
            | sa.column("target_kind").in_(("range", "file-start")),
        )
        batch.create_check_constraint(
            "ck_review_thread_outdated_reason",
            sa.column("outdated_reason").is_(None)
            | sa.column("outdated_reason").in_(
                ("region_changed", "region_not_found", "file_missing")
            ),
        )
        batch.create_check_constraint(
            "ck_review_thread_region_kind",
            sa.column("region_kind").is_(None)
            | sa.column("region_kind").in_(
                ("ordinary", "notebook-cell-source")
            ),
        )
        batch.create_check_constraint(
            "ck_review_thread_region",
            sa.case(
                (
                    sa.column("region_kind") == "ordinary",
                    sa.column("region_key").is_(None),
                ),
                (
                    sa.column("region_kind") == "notebook-cell-source",
                    sa.column("region_key").is_not(None)
                    & (sa.func.length(sa.column("region_key")) > 0),
                ),
                (
                    sa.column("region_kind").is_(None),
                    sa.column("region_key").is_(None),
                ),
                else_=False,
            ),
        )
        batch.create_check_constraint(
            "ck_review_thread_location", _location_over_region_kind()
        )
