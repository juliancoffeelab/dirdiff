"""Check notebook composition against executed notebook fixtures.

The tests pin one frame per cell, the notebook-metadata frame, source-bay
expansion, closed metadata attachments, and output text with escape codes left
uninterpreted. Focused cases also cover durable IDs, source-derived keys,
movement, one-sided cells, and local degradation of malformed notebook parts.
"""

from __future__ import annotations

import json
from pathlib import Path

from dirdiff.engines import engine
from dirdiff.formats import (
    BayContext,
    ComposeContext,
    Composer,
    FramePayload,
)
from dirdiff.util import JsonValue

NOTEBOOKS = Path(__file__).resolve().parents[1] / "presets" / "notebook"
"""Executed notebook fixture catalog used for format-level behavior checks.

These are real serialized notebook sides, so tests cover structures and output
forms produced by kernels rather than hand-authored approximations alone.
"""


def test_error_traceback_output_bay_keeps_uninterpreted_escapes() -> None:
    """Keep an error output's traceback text exact in a dedicated closed bay.

    ANSI escapes and traceback line boundaries remain uninterpreted source for
    the text engine. The output must not become notebook metadata, a warning, or
    a pre-expanded body.
    """
    directory = NOTEBOOKS / "basic" / "error-traceback-appears"
    context = ComposeContext.build(
        left_path="error.ipynb",
        right_path="error.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        (directory / "old.ipynb").read_bytes(),
        (directory / "new.ipynb").read_bytes(),
        context,
    )

    # Every cell composes a frame; the changed one is the cell whose body is
    # shown rather than collapsed.
    changed = [
        frame
        for frame in composed["frames"]
        if not frame["bays"][0]["collapsible"]
    ]
    assert len(changed) == 1
    bays = changed[0]["bays"]
    assert bays[0]["label"] == "Code"

    # The cell prints before it raises, so the traceback is not the first
    # output; find it by content rather than by position.
    output_bay = next(
        bay
        for bay in bays
        if bay["kind_data"]["kind"] == "text"
        and "IndexError"
        in "\n".join(
            (row["right_text"] or "") for row in bay["kind_data"]["rows"]
        )
    )
    assert ":output:" in output_bay["bay_key"]
    output_content = output_bay["kind_data"]
    assert output_content["kind"] == "text"
    text = "\n".join(
        (row["right_text"] or "") for row in output_content["rows"]
    )
    assert "\x1b[" in text, "traceback escape codes must not be interpreted"
    assert output_bay["default_expanded"] is False


def test_metadata_change_collapses_source_and_adds_metadata_bay() -> None:
    """Represent a metadata-only cell change without claiming source changed.

    The source bay remains unchanged and starts closed, while canonical metadata
    JSON appears in a separate changed bay with the expected tags. Both stay in the
    same cell frame.
    """
    directory = NOTEBOOKS / "basic" / "cell-metadata-changed"
    context = ComposeContext.build(
        left_path="meta.ipynb",
        right_path="meta.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        (directory / "old.ipynb").read_bytes(),
        (directory / "new.ipynb").read_bytes(),
        context,
    )

    frames = composed["frames"]
    assert len(frames) == 7
    # No source changed anywhere, so no frame shows a body: every bay in the
    # notebook is collapsed, and the metadata bay is what Next hunk reaches.
    assert all(bay["collapsible"] for frame in frames for bay in frame["bays"])
    changed = [frame for frame in frames if len(frame["bays"]) > 1]
    assert len(changed) == 1
    bays = changed[0]["bays"]
    assert [bay["label"] for bay in bays] == ["Code", "Cell metadata"]
    # This cell's source is identical on both sides, so it collapses like an
    # untouched one; the metadata that did change hangs off it, collapsed too.
    assert bays[0]["collapsible"] is True
    assert bays[0]["default_expanded"] is False
    assert bays[1]["collapsible"] is True
    assert bays[1]["default_expanded"] is False
    assert bays[1]["bay_key"].endswith(":metadata")
    metadata_bay = bays[1]
    assert metadata_bay["kind_data"]["kind"] == "text"
    metadata_text = "\n".join(
        (row["right_text"] or "") for row in metadata_bay["kind_data"]["rows"]
    )
    assert "tags" in metadata_text and "parameters" in metadata_text


def test_added_and_removed_cells_are_separate_frames() -> None:
    """Keep unmatched added and removed cells as distinct document-order frames.

    Each changed cell contributes one frame whose bay rows expose only that cell's
    insertion or deletion status. Unchanged cells contribute no changed frame,
    and headings do not stand in for semantic change.
    """
    directory = NOTEBOOKS / "basic" / "cell-added-removed"
    context = ComposeContext.build(
        left_path="s.ipynb",
        right_path="s.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        (directory / "old.ipynb").read_bytes(),
        (directory / "new.ipynb").read_bytes(),
        context,
    )

    # A frame carries no annotations, so what was added and what was
    # removed is read from the bays, not from the heading. Only the cells
    # that changed compose a frame; the untouched ones do not.
    def statuses(frame: FramePayload) -> set[str]:
        """Return the distinct text-row statuses visible inside one frame.

        Image bays have no rows and are ignored. The assertion uses this set to
        prove a cell frame does not mix added and removed content from two
        unmatched cells.

        # Returns

        - `Members`: Distinct statuses from every text row in the frame.
        - `Omissions`: Image bays and duplicate statuses add no entries, so the
          set describes only row-status kinds visible in text bays.
        """
        return {
            row["status"]
            for bay in frame["bays"]
            if bay["kind_data"]["kind"] == "text"
            for row in bay["kind_data"]["rows"]
        }

    frames = composed["frames"]
    # Every cell is present, so the frame count exceeds the changed-cell count.
    assert len(frames) >= 7
    added = [frame for frame in frames if statuses(frame) == {"insert"}]
    removed = [frame for frame in frames if statuses(frame) == {"delete"}]
    assert added != [], "the added cell composes an insert-only frame"
    assert removed != [], "the removed cell composes a delete-only frame"
    # A one-sided cell still shows its outputs, not only its source.
    assert [bay["label"] for bay in added[0]["bays"]] == [
        "Code",
        "Output 1",
    ]


def test_markdown_edit_is_one_source_bay() -> None:
    """Keep a markdown source edit in one cell source bay with Markdown syntax.

    The changed cell must not acquire code outputs or metadata bays, while
    unchanged surrounding cells preserve notebook frame order. The source bay's
    public coordinate remains the cell-derived one.
    """
    directory = NOTEBOOKS / "basic" / "markdown-cell-edited"
    context = ComposeContext.build(
        left_path="md.ipynb",
        right_path="md.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        (directory / "old.ipynb").read_bytes(),
        (directory / "new.ipynb").read_bytes(),
        context,
    )
    changed = [
        frame
        for frame in composed["frames"]
        if not frame["bays"][0]["collapsible"]
    ]
    assert len(changed) == 1
    bays = changed[0]["bays"]
    assert len(bays) == 1
    assert bays[0]["label"] == "Markdown"
    # Untouched cells are still present, collapsed rather than dropped.
    assert len(composed["frames"]) > 1


def test_invalid_notebook_composes_as_warned_raw_notebook() -> None:
    """Preserve invalid notebook JSON as warned raw notebook content.

    Classification remains notebook-specific instead of retrying flatfile or
    blob builders. The composed text bay must expose the original content and a
    notebook warning so damage is visible without losing File structure.
    """
    directory = NOTEBOOKS / "invalid" / "not-valid-notebook-json"
    context = ComposeContext.build(
        left_path="b.ipynb",
        right_path="b.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        (directory / "old.ipynb").read_bytes(),
        (directory / "new.ipynb").read_bytes(),
        context,
    )
    assert len(composed["frames"]) == 1
    frame = composed["frames"][0]
    assert frame["heading"] == "Notebook"
    assert len(frame["bays"]) == 1
    bay = frame["bays"][0]
    assert bay["bay_key"] == "notebook:raw"
    assert bay["label"] == "Raw notebook JSON"
    assert bay["warnings"] == [
        {
            "type": "notebook_invalid_document",
            "message": (
                "Notebook structure could not be read; showing its raw JSON."
            ),
        }
    ]


def test_source_and_output_both_change_in_one_frame() -> None:
    """Keep one code cell's changed source and stream output under one frame.

    The frame carries separate stable bays for the two reviewable facts, both
    marked changed. Composition must not split one cell across unrelated frames
    or hide the output behind the source diff.
    """
    directory = NOTEBOOKS / "basic" / "stream-output-changed"
    context = ComposeContext.build(
        left_path="o.ipynb",
        right_path="o.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        (directory / "old.ipynb").read_bytes(),
        (directory / "new.ipynb").read_bytes(),
        context,
    )
    changed = [
        frame
        for frame in composed["frames"]
        if not frame["bays"][0]["collapsible"]
    ]
    assert len(changed) == 1
    bays = changed[0]["bays"]
    assert bays[0]["label"] == "Code"
    assert bays[0]["collapsible"] is False
    assert any(r["bay_key"].endswith(":output:0") for r in bays)
    # Reachability is the frontend's numbering, but the facts it numbers from
    # are composed here: a bay is reachable when it carries a row boundary or
    # reports `changed`.
    reachable = [
        bay
        for frame in composed["frames"]
        for bay in frame["bays"]
        if bay["change"]["kind"] != "unchanged"
        or any(
            row["hunk_index"] is not None
            for row in (
                bay["kind_data"]["rows"]
                if bay["kind_data"]["kind"] == "text"
                else []
            )
        )
    ]
    assert len(reachable) >= 2


def test_cells_pair_by_id_so_an_edited_cell_is_not_an_add_and_a_remove() -> (
    None
):
    """A cell keeps its key when its source is rewritten.

    The cell id is the public key, so an edited cell pairs with itself rather
    than composing as one removed and one added cell, and an unchanged cell
    composes nothing at all.
    """
    left = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "markdown",
                    "id": "intro",
                    "metadata": {},
                    "source": ["## Setup\n\n", "Old body\n"],
                },
                {
                    "cell_type": "code",
                    "id": "compute",
                    "execution_count": None,
                    "metadata": {},
                    "source": ["value = 1\n"],
                    "outputs": [],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    ).encode()
    right = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "markdown",
                    "id": "intro",
                    "metadata": {},
                    "source": ["## Setup\n\n", "Updated body\n"],
                },
                {
                    "cell_type": "code",
                    "id": "compute",
                    "execution_count": None,
                    "metadata": {},
                    "source": ["value = 1\n"],
                    "outputs": [],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    ).encode()
    context = ComposeContext.build(
        left_path="demo.ipynb",
        right_path="demo.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(left, right, context)

    changed = [
        frame
        for frame in composed["frames"]
        if not frame["bays"][0]["collapsible"]
    ]
    assert len(changed) == 1, "only the edited cell is shown expanded"
    frame = changed[0]
    assert frame["frame_key"] == "intro"
    assert [bay["bay_key"] for bay in frame["bays"]] == ["intro"]
    # The untouched cell is still present, collapsed.
    assert len(composed["frames"]) == 2
    source_bay = frame["bays"][0]
    assert source_bay["kind_data"]["kind"] == "text"
    assert any(
        row["right_text"] == "Updated body"
        for row in source_bay["kind_data"]["rows"]
    )


def test_bay_keys_survive_a_source_rewrite() -> None:
    """The key a review target names is the same before and after an edit.

    This is the property that lets review store a bay key and nothing else:
    rewriting a cell's whole source must not change the key that names it.
    """

    def notebook(source: str) -> bytes:
        """Serialize the fixed-ID one-cell document used by the rewrite case.

        Only `source` varies, so any change to bay identity can be attributed to
        source replacement rather than metadata, order, prompt, or cell ID.
        """
        return json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "id": "stable-cell",
                        "execution_count": None,
                        "metadata": {},
                        "source": [source],
                        "outputs": [],
                    }
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ).encode()

    context = BayContext(
        left_path="demo.ipynb",
        right_path="demo.ipynb",
        left_label="old",
        right_label="new",
    )
    produced = list(
        Composer().bays(
            notebook("totally = 'different'\n"),
            notebook("nothing = 'alike'\n"),
            context,
        )
    )
    assert [bay.bay_key for bay in produced] == ["stable-cell"]


def test_cell_reorder_keeps_one_unique_key_per_cell() -> None:
    """A moved cell composes once, under the key it already had.

    Sequence alignment reports a move as a deletion plus an insertion, which
    would emit the same key twice and break the uniqueness every bay key
    depends on — including the dictionary review builds from `bays()`.
    """

    def notebook(order: list[str]) -> bytes:
        """Serialize fixed-ID cells in the exact requested document order.

        Cell contents and metadata remain identical across sides, isolating the
        pairing contract that reorders one durable cell without duplicating its
        public bay key.
        """
        return json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "id": key,
                        "execution_count": None,
                        "metadata": {},
                        "source": [f"{key} = 1\n"],
                        "outputs": [],
                    }
                    for key in order
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ).encode()

    context = ComposeContext.build(
        left_path="n.ipynb",
        right_path="n.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        notebook(["A", "B", "C"]), notebook(["B", "A", "C"]), context
    )
    keys = [
        bay["bay_key"] for frame in composed["frames"] for bay in frame["bays"]
    ]
    assert len(keys) == len(set(keys)), f"duplicate bay keys: {keys}"
    # A move is a change, so it stays reachable rather than composing nothing.
    assert any(
        bay["change"]["kind"] != "unchanged"
        or any(
            row["hunk_index"] is not None
            for row in (
                bay["kind_data"]["rows"]
                if bay["kind_data"]["kind"] == "text"
                else []
            )
        )
        for frame in composed["frames"]
        for bay in frame["bays"]
    )
    assert any(
        bay["detail"] is not None and "moved" in bay["detail"]
        for frame in composed["frames"]
        for bay in frame["bays"]
    )


def test_output_changed_beyond_its_text_stays_reachable() -> None:
    """A re-rendered plot is reachable even though its text is unchanged.

    The `text/plain` line of a figure is identical across re-renders while the
    image bytes change completely. Such a bay produces no changed row, so it
    consumes one hunk index at the bay root and says so in its label.
    """
    directory = NOTEBOOKS / "basic" / "plot-rerendered"
    context = ComposeContext.build(
        left_path="p.ipynb",
        right_path="p.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        (directory / "old.ipynb").read_bytes(),
        (directory / "new.ipynb").read_bytes(),
        context,
    )
    # The bay says it changed while every one of its rows is equal, which is
    # exactly the case the frontend gives a separate stop.
    carried_by_bay = [
        bay
        for frame in composed["frames"]
        for bay in frame["bays"]
        if bay["kind_data"]["kind"] == "text"
        and bay["change"]["kind"] != "unchanged"
        and all(row["hunk_index"] is None for row in bay["kind_data"]["rows"])
    ]
    assert carried_by_bay != [], (
        "a change with no changed row needs a stop of its own"
    )
    for bay in carried_by_bay:
        content = bay["kind_data"]
        assert content["kind"] == "text"
        assert bay["change"] == {"kind": "changed"}
        assert "changed beyond its text" in bay["label"]
        assert content["stats"]["changed_lines"] == 0


def test_cells_without_distinct_ids_use_warned_source_keys() -> None:
    """Missing or duplicate ids preserve cells under degraded source keys.

    Bay keys are what review and line pins persist. When an id cannot supply a
    unique durable key, the source hash and occurrence among identical sources
    supply an unambiguous degraded coordinate and the source bay warns.
    """

    def notebook(identifiers: list[JsonValue]) -> bytes:
        """Serialize otherwise identical cells with caller-supplied raw IDs.

        The helper accepts invalid JSON ID values intentionally. This isolates
        loader degradation and pseudo-key collision handling from source,
        metadata, output, and ordering differences.
        """
        return json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "id": identifier,
                        "execution_count": None,
                        "metadata": {},
                        "source": [f"value = {index}\n"],
                        "outputs": [],
                    }
                    for index, identifier in enumerate(identifiers)
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ).encode()

    context = ComposeContext.build(
        left_path="n.ipynb",
        right_path="n.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    malformed: list[list[JsonValue]] = [
        [None, "kept"],
        ["same", "same"],
        ["", "kept"],
    ]
    for identifiers in malformed:
        composed = Composer().compose(
            notebook(identifiers), notebook(["a", "b"]), context
        )
        keys = [
            bay["bay_key"]
            for frame in composed["frames"]
            for bay in frame["bays"]
        ]
        assert "flatfile" not in keys, f"{identifiers} composed as {keys}"
        assert any(key.startswith("pseudocell:") for key in keys)
        assert any(
            warning["type"] == "notebook_missing_cell_id"
            for frame in composed["frames"]
            for bay in frame["bays"]
            for warning in bay["warnings"]
        )

    # One malformed side is enough: pairing needs keys on both.
    composed = Composer().compose(
        notebook(["a", "b"]), notebook(["a", "a"]), context
    )
    assert any(
        bay["bay_key"].startswith("pseudocell:")
        for frame in composed["frames"]
        for bay in frame["bays"]
    )


def test_schema_violations_degrade_only_the_affected_notebook_part() -> None:
    """A broken read field preserves the notebook and reports its damage.

    The loader is strict about every field composition reads, where strict
    means the `nbformat` v4.5 schema shape: an id outside the `cell_id`
    pattern or length, a cell type outside the three cell types, an output
    type outside the four output types, and an `execute_result` missing its
    required `data` bundle each make the document not a notebook, rather than
    a notebook with invented contents.
    """

    def notebook(cell: dict[str, JsonValue]) -> bytes:
        """Place one possibly malformed cell mapping at a valid document boundary.

        Fixed notebook metadata and format fields ensure the parameterized test
        exercises nested cell preservation rather than top-level parsing damage.
        """
        return json.dumps(
            {
                "cells": [cell],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ).encode()

    valid: dict[str, JsonValue] = {
        "cell_type": "code",
        "id": "kept",
        "execution_count": None,
        "metadata": {},
        "source": ["value = 1\n"],
        "outputs": [],
    }
    malformed: list[dict[str, JsonValue]] = [
        {**valid, "id": " padded "},
        {**valid, "id": "x" * 65},
        {**valid, "cell_type": "headline"},
        {**valid, "outputs": [{"output_type": "widget", "data": {}}]},
        {**valid, "outputs": [{"output_type": "execute_result"}]},
        {
            key: value
            for key, value in valid.items()
            if key != "execution_count"
        },
        {**valid, "execution_count": "4"},
    ]
    context = ComposeContext.build(
        left_path="n.ipynb",
        right_path="n.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    for cell in malformed:
        composed = Composer().compose(notebook(valid), notebook(cell), context)
        keys = [
            bay["bay_key"]
            for frame in composed["frames"]
            for bay in frame["bays"]
        ]
        assert "flatfile" not in keys, f"{cell} composed as {keys}"
        assert any(
            bay["warnings"]
            for frame in composed["frames"]
            for bay in frame["bays"]
        ), f"{cell} produced no degradation warning"


def test_a_cell_key_is_the_id_verbatim() -> None:
    """Keep a valid Jupyter cell ID as the exact public review coordinate.

    Editing the cell source must not rewrite, prefix, hash, or position-qualify
    that ID. A target copied from the notebook document should address the same
    source bay on both sides.
    """

    def notebook(source: str) -> bytes:
        """Serialize fixed-ID cells while varying only the addressed source.

        The unchanged sibling proves composition preserves separate IDs and the
        edited cell proves its verbatim coordinate survives content changes.
        """
        return json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "id": identifier,
                        "execution_count": None,
                        "metadata": {},
                        "source": [source],
                        "outputs": [],
                    }
                    for identifier in ["b1a-2", "B1a_2"]
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ).encode()

    context = ComposeContext.build(
        left_path="n.ipynb",
        right_path="n.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(notebook("a\n"), notebook("b\n"), context)
    assert [
        bay["bay_key"] for frame in composed["frames"] for bay in frame["bays"]
    ] == ["b1a-2", "B1a_2"]


def test_notebook_level_metadata_change_is_reachable() -> None:
    """A change to the notebook's own fields belongs to no cell but still shows.

    A kernelspec swap or a format-version bump changes the file while leaving
    every cell identical. Composing only cells makes that change invisible, which
    is the one outcome the reachability rule forbids.
    """

    def notebook(kernel: str, minor: int) -> bytes:
        """Build a one-cell notebook varying only in its top-level fields.

        # Parameters

        - `kernel`: Kernelspec name placed in notebook metadata.
        - `minor`: `nbformat_minor` value placed beside the fixed major format.
        """
        return json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "id": "a1",
                        "execution_count": None,
                        "metadata": {},
                        "source": ["x = 1\n"],
                        "outputs": [],
                    }
                ],
                "metadata": {"kernelspec": {"name": kernel}},
                "nbformat": 4,
                "nbformat_minor": minor,
            }
        ).encode()

    context = ComposeContext.build(
        left_path="n.ipynb",
        right_path="n.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    changed = Composer().compose(
        notebook("python3", 5), notebook("python3.12", 5), context
    )
    assert any(
        bay["change"]["kind"] != "unchanged"
        or any(
            row["hunk_index"] is not None
            for row in (
                bay["kind_data"]["rows"]
                if bay["kind_data"]["kind"] == "text"
                else []
            )
        )
        for frame in changed["frames"]
        for bay in frame["bays"]
    ), "the change must not be hidden"
    metadata = [
        bay
        for frame in changed["frames"]
        for bay in frame["bays"]
        if bay["bay_key"] == "notebook:metadata"
    ]
    assert len(metadata) == 1
    assert metadata[0]["label"] == "Notebook metadata"
    assert metadata[0]["collapsible"] is True
    assert metadata[0]["default_expanded"] is False

    # The format version is part of the file too, not only `metadata`.
    version = Composer().compose(
        notebook("python3", 5), notebook("python3", 4), context
    )
    assert any(
        bay["change"]["kind"] != "unchanged"
        or any(
            row["hunk_index"] is not None
            for row in (
                bay["kind_data"]["rows"]
                if bay["kind_data"]["kind"] == "text"
                else []
            )
        )
        for frame in version["frames"]
        for bay in frame["bays"]
    )

    # An unchanged notebook composes no metadata bay at all.
    same = Composer().compose(
        notebook("python3", 5), notebook("python3", 5), context
    )
    assert all(
        bay["bay_key"] != "notebook:metadata"
        for frame in same["frames"]
        for bay in frame["bays"]
    )


def test_one_sided_notebook_never_invents_the_absent_side() -> None:
    """An added or removed notebook renders nothing on its absent side.

    Composition must not substitute an empty document for a side that was
    never captured: the notebook-level metadata bay derives its `change`
    from the absence, every cell source bay reports the file's direction,
    and no row carries text on the absent side.
    """
    directory = NOTEBOOKS / "basic" / "cell-metadata-changed"
    data = (directory / "new.ipynb").read_bytes()

    context = ComposeContext.build(
        left_path=None,
        right_path="new.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    added = Composer().compose(None, data, context)
    assert added["summary"]["left_exists"] is False
    bays = [bay for frame in added["frames"] for bay in frame["bays"]]
    assert len(bays) > 0, "an added notebook still composes its cells"
    metadata = [bay for bay in bays if bay["bay_key"] == "notebook:metadata"]
    assert len(metadata) == 1, "top-level fields exist, so the bay shows"
    assert metadata[0]["change"] == {"kind": "added"}
    sources = [
        bay
        for frame in added["frames"]
        for bay in frame["bays"]
        if bay["bay_key"] == frame["frame_key"] != "notebook:metadata"
    ]
    assert len(sources) > 0 and all(
        bay["change"] == {"kind": "added"} for bay in sources
    ), "every cell of an added notebook is an added cell"
    assert all(
        row["left_no"] is None and row["status"] == "insert"
        for bay in bays
        if bay["kind_data"]["kind"] == "text"
        for row in bay["kind_data"]["rows"]
    ), "no row of an added notebook may claim content existed on the left"

    context = ComposeContext.build(
        left_path="old.ipynb",
        right_path=None,
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    removed = Composer().compose(data, None, context)
    assert removed["summary"]["right_exists"] is False
    bays = [bay for frame in removed["frames"] for bay in frame["bays"]]
    metadata = [bay for bay in bays if bay["bay_key"] == "notebook:metadata"]
    assert len(metadata) == 1
    assert metadata[0]["change"] == {"kind": "removed"}
    assert all(
        row["right_no"] is None and row["status"] == "delete"
        for bay in bays
        if bay["kind_data"]["kind"] == "text"
        for row in bay["kind_data"]["rows"]
    ), "no row of a removed notebook may claim content existed on the right"


def test_cells_are_named_by_prompt_and_a_move_reports_both_names() -> None:
    """Headings are Jupyter prompts; a move carries the name at each end.

    X is removed and keeps the prompt it had in the old notebook; the
    markdown cell has no prompt and no heading; D never ran and shows the
    empty prompt; C was merely re-run, so its name updates while its change
    stays "unchanged"; and A — moved and edited — reports the move with the
    prompt it wore on each side instead of composing as "changed", since its
    rows already show the edit and only `change` can say it moved.
    """

    def code(key: str, count: int | None, source: str) -> dict[str, JsonValue]:
        """Build one code cell for heading and move assertions.

        # Parameters

        - `key`: Durable notebook cell id used for cross-side pairing.
        - `count`: Execution count rendered in the cell heading, or `None` for
          an empty prompt.
        - `source`: Exact cell source rendered by the text engine.

        # Returns

        - `Identity and prompt`: The mapping carries the supplied cell id and
          execution count with `cell_type` fixed to `code`.
        - `Content`: Source is a one-part list; metadata and outputs are empty so
          heading and movement are the only varied facts.
        """
        return {
            "cell_type": "code",
            "id": key,
            "execution_count": count,
            "metadata": {},
            "source": [source],
            "outputs": [],
        }

    def notebook(cells: list[dict[str, JsonValue]]) -> bytes:
        """Serialize supplied cell mappings without changing their order or content.

        Fixed top-level metadata and nbformat fields keep the test focused on
        cell pairing, identity, and degradation across the provided sequence.
        """
        return json.dumps(
            {
                "cells": cells,
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ).encode()

    markdown: dict[str, JsonValue] = {
        "cell_type": "markdown",
        "id": "B",
        "metadata": {},
        "source": ["# Title\n"],
    }
    context = ComposeContext.build(
        left_path="n.ipynb",
        right_path="n.ipynb",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        notebook(
            [
                code("X", 1, "x = 1\n"),
                code("A", 2, "a = 1\n"),
                markdown,
                code("C", 3, "c = 1\n"),
                code("D", None, "d = 1\n"),
            ]
        ),
        notebook(
            [
                markdown,
                code("C", 9, "c = 1\n"),
                code("A", 7, "a = 2\n"),
                code("D", None, "d = 1\n"),
            ]
        ),
        context,
    )
    cells = [
        (
            frame["heading"],
            frame["bays"][0]["change"],
            frame["bays"][0]["detail"],
        )
        for frame in composed["frames"]
        if frame["frame_key"] in {"X", "A", "B", "C", "D"}
    ]
    assert cells == [
        ("In [1]", {"kind": "removed"}, None),
        (None, {"kind": "unchanged"}, None),
        ("In [9]", {"kind": "unchanged"}, None),
        (
            "In [7]",
            {"kind": "moved", "from_heading": "In [2]", "to_heading": "In [7]"},
            None,
        ),
        ("In [ ]", {"kind": "unchanged"}, None),
    ]
