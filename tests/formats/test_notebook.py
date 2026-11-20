from __future__ import annotations

import json
from pathlib import Path

from dirdiff.engines import TextDiffEngine
from dirdiff.formats import (
    BayContext,
    ComposeContext,
    Composer,
    FramePayload,
)
from dirdiff.util import JsonValue, maybe

NOTEBOOKS = Path(__file__).resolve().parents[1] / "presets" / "notebook"


def test_error_traceback_output_bay_keeps_uninterpreted_escapes() -> None:
    directory = NOTEBOOKS / "basic" / "error-traceback-appears"
    context = ComposeContext.build(
        left_path=maybe("error.ipynb"),
        right_path=maybe("error.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe((directory / "old.ipynb").read_bytes()),
        maybe((directory / "new.ipynb").read_bytes()),
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
    directory = NOTEBOOKS / "basic" / "cell-metadata-changed"
    context = ComposeContext.build(
        left_path=maybe("meta.ipynb"),
        right_path=maybe("meta.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe((directory / "old.ipynb").read_bytes()),
        maybe((directory / "new.ipynb").read_bytes()),
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
    directory = NOTEBOOKS / "basic" / "cell-added-removed"
    context = ComposeContext.build(
        left_path=maybe("s.ipynb"),
        right_path=maybe("s.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe((directory / "old.ipynb").read_bytes()),
        maybe((directory / "new.ipynb").read_bytes()),
        context,
    )

    # A frame carries no annotations, so what was added and what was
    # removed is read from the bays, not from the heading. Only the cells
    # that changed compose a frame; the untouched ones do not.
    def statuses(frame: FramePayload) -> set[str]:
        return {
            row["status"]
            for bay in frame["bays"]
            if bay["kind_data"]["kind"] == "text"
            for row in bay["kind_data"]["rows"]
        }

    frames = composed["frames"]
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
    directory = NOTEBOOKS / "basic" / "markdown-cell-edited"
    context = ComposeContext.build(
        left_path=maybe("md.ipynb"),
        right_path=maybe("md.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe((directory / "old.ipynb").read_bytes()),
        maybe((directory / "new.ipynb").read_bytes()),
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
    assert len(composed["frames"]) > 1


def test_invalid_notebook_composes_as_warned_raw_notebook() -> None:
    directory = NOTEBOOKS / "invalid" / "not-valid-notebook-json"
    context = ComposeContext.build(
        left_path=maybe("b.ipynb"),
        right_path=maybe("b.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe((directory / "old.ipynb").read_bytes()),
        maybe((directory / "new.ipynb").read_bytes()),
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
    directory = NOTEBOOKS / "basic" / "stream-output-changed"
    context = ComposeContext.build(
        left_path=maybe("o.ipynb"),
        right_path=maybe("o.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe((directory / "old.ipynb").read_bytes()),
        maybe((directory / "new.ipynb").read_bytes()),
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
        left_path=maybe("demo.ipynb"),
        right_path=maybe("demo.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(maybe(left), maybe(right), context)

    changed = [
        frame
        for frame in composed["frames"]
        if not frame["bays"][0]["collapsible"]
    ]
    assert len(changed) == 1, "only the edited cell is shown expanded"
    frame = changed[0]
    assert frame["frame_key"] == "intro"
    assert [bay["bay_key"] for bay in frame["bays"]] == ["intro"]
    assert len(composed["frames"]) == 2
    source_bay = frame["bays"][0]
    assert source_bay["kind_data"]["kind"] == "text"
    assert any(
        row["right_text"] == "Updated body"
        for row in source_bay["kind_data"]["rows"]
    )


def test_bay_keys_survive_a_source_rewrite() -> None:
    def notebook(source: str) -> bytes:
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
        left_path=maybe("demo.ipynb"),
        right_path=maybe("demo.ipynb"),
        left_label="old",
        right_label="new",
    )
    produced = list(
        Composer().bays(
            maybe(notebook("totally = 'different'\n")),
            maybe(notebook("nothing = 'alike'\n")),
            context,
        )
    )
    assert [bay.bay_key for bay in produced] == ["stable-cell"]


def test_cell_reorder_keeps_one_unique_key_per_cell() -> None:
    def notebook(order: list[str]) -> bytes:
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
        left_path=maybe("n.ipynb"),
        right_path=maybe("n.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(notebook(["A", "B", "C"])),
        maybe(notebook(["B", "A", "C"])),
        context,
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


def test_plot_output_prefers_png_and_stays_reachable() -> None:
    directory = NOTEBOOKS / "rich" / "plot-wettest-highlighted"
    context = ComposeContext.build(
        left_path=maybe("p.ipynb"),
        right_path=maybe("p.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe((directory / "old.ipynb").read_bytes()),
        maybe((directory / "new.ipynb").read_bytes()),
        context,
    )
    plot_bays = [
        bay
        for frame in composed["frames"]
        for bay in frame["bays"]
        if bay["kind_data"]["kind"] == "image"
    ]
    assert [bay["bay_key"] for bay in plot_bays] == ["d30a8ad3:output:0"]
    for bay in plot_bays:
        content = bay["kind_data"]
        assert content["kind"] == "image"
        assert bay["change"] == {"kind": "changed"}
        assert content["left"] is not None
        assert content["right"] is not None
        assert content["left"]["media_type"] == "image/png"
        assert content["right"]["media_type"] == "image/png"
        assert content["left"]["digest"] != content["right"]["digest"]


def test_png_on_only_one_output_side_stays_an_image_bay() -> None:
    directory = NOTEBOOKS / "rich" / "plot-wettest-highlighted"
    old_document: JsonValue = json.loads(
        (directory / "old.ipynb").read_text(encoding="utf-8")
    )
    new_document: JsonValue = json.loads(
        (directory / "new.ipynb").read_text(encoding="utf-8")
    )
    assert isinstance(old_document, dict)
    old_cells = old_document["cells"]
    assert isinstance(old_cells, list)
    old_plot = old_cells[5]
    assert isinstance(old_plot, dict)
    old_outputs = old_plot["outputs"]
    assert isinstance(old_outputs, list)
    old_output = old_outputs[0]
    assert isinstance(old_output, dict)
    old_data = old_output["data"]
    assert isinstance(old_data, dict)
    del old_data["image/png"]

    composed = Composer().compose(
        maybe(json.dumps(old_document).encode()),
        maybe(json.dumps(new_document).encode()),
        ComposeContext.build(
            left_path=maybe("p.ipynb"),
            right_path=maybe("p.ipynb"),
            left_label="old",
            right_label="new",
            renderer=TextDiffEngine(),
        ),
    )
    bay = next(
        bay
        for frame in composed["frames"]
        for bay in frame["bays"]
        if bay["bay_key"] == "d30a8ad3:output:0"
    )
    content = bay["kind_data"]
    assert content["kind"] == "image"
    assert content["left"] is None
    assert content["right"] is not None
    assert content["right"]["media_type"] == "image/png"
    assert bay["change"] == {"kind": "changed"}


def test_unchanged_presets_cover_added_and_removed_plot_outputs() -> None:
    for case, change, left_present in (
        ("plot-output-added", "added", False),
        ("plot-output-removed", "removed", True),
    ):
        directory = NOTEBOOKS / "unchanged" / case
        composed = Composer().compose(
            maybe((directory / "old.ipynb").read_bytes()),
            maybe((directory / "new.ipynb").read_bytes()),
            ComposeContext.build(
                left_path=maybe("p.ipynb"),
                right_path=maybe("p.ipynb"),
                left_label="old",
                right_label="new",
                renderer=TextDiffEngine(),
            ),
        )
        image_bays = [
            bay
            for frame in composed["frames"]
            for bay in frame["bays"]
            if bay["kind_data"]["kind"] == "image"
        ]

        assert len(image_bays) == 1
        assert all(bay["change"] == {"kind": change} for bay in image_bays)
        for bay in image_bays:
            content = bay["kind_data"]
            assert content["kind"] == "image"
            assert (content["left"] is not None) is left_present
            assert (content["right"] is not None) is (not left_present)


def test_cells_without_distinct_ids_use_warned_source_keys() -> None:
    def notebook(identifiers: list[JsonValue]) -> bytes:
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
        left_path=maybe("n.ipynb"),
        right_path=maybe("n.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    malformed: list[list[JsonValue]] = [
        [None, "kept"],
        ["same", "same"],
        ["", "kept"],
    ]
    for identifiers in malformed:
        composed = Composer().compose(
            maybe(notebook(identifiers)), maybe(notebook(["a", "b"])), context
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
        maybe(notebook(["a", "b"])), maybe(notebook(["a", "a"])), context
    )
    assert any(
        bay["bay_key"].startswith("pseudocell:")
        for frame in composed["frames"]
        for bay in frame["bays"]
    )


def test_schema_violations_degrade_only_the_affected_notebook_part() -> None:
    def notebook(cell: dict[str, JsonValue]) -> bytes:
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
            **valid,
            "outputs": [
                {
                    "output_type": "display_data",
                    "data": {"image/png": "not base64!"},
                }
            ],
        },
        {
            **valid,
            "outputs": [
                {
                    "output_type": "display_data",
                    "data": {"image/png": "not ascii: ☃"},
                }
            ],
        },
        {
            key: value
            for key, value in valid.items()
            if key != "execution_count"
        },
        {**valid, "execution_count": "4"},
    ]
    context = ComposeContext.build(
        left_path=maybe("n.ipynb"),
        right_path=maybe("n.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    for cell in malformed:
        composed = Composer().compose(
            maybe(notebook(valid)), maybe(notebook(cell)), context
        )
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
    def notebook(source: str) -> bytes:
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
        left_path=maybe("n.ipynb"),
        right_path=maybe("n.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(notebook("a\n")), maybe(notebook("b\n")), context
    )
    assert [
        bay["bay_key"] for frame in composed["frames"] for bay in frame["bays"]
    ] == ["b1a-2", "B1a_2"]


def test_notebook_level_metadata_change_is_reachable() -> None:
    def notebook(kernel: str, minor: int) -> bytes:
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
        left_path=maybe("n.ipynb"),
        right_path=maybe("n.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    changed = Composer().compose(
        maybe(notebook("python3", 5)), maybe(notebook("python3.12", 5)), context
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
        maybe(notebook("python3", 5)), maybe(notebook("python3", 4)), context
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
        maybe(notebook("python3", 5)), maybe(notebook("python3", 5)), context
    )
    assert all(
        bay["bay_key"] != "notebook:metadata"
        for frame in same["frames"]
        for bay in frame["bays"]
    )


def test_one_sided_notebook_never_invents_the_absent_side() -> None:
    directory = NOTEBOOKS / "basic" / "cell-metadata-changed"
    data = (directory / "new.ipynb").read_bytes()

    context = ComposeContext.build(
        left_path=maybe(None),
        right_path=maybe("new.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    added = Composer().compose(maybe(None), maybe(data), context)
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
        left_path=maybe("old.ipynb"),
        right_path=maybe(None),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    removed = Composer().compose(maybe(data), maybe(None), context)
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
    def code(key: str, count: int | None, source: str) -> dict[str, JsonValue]:
        return {
            "cell_type": "code",
            "id": key,
            "execution_count": count,
            "metadata": {},
            "source": [source],
            "outputs": [],
        }

    def notebook(cells: list[dict[str, JsonValue]]) -> bytes:
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
        left_path=maybe("n.ipynb"),
        right_path=maybe("n.ipynb"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(
            notebook(
                [
                    code("X", 1, "x = 1\n"),
                    code("A", 2, "a = 1\n"),
                    markdown,
                    code("C", 3, "c = 1\n"),
                    code("D", None, "d = 1\n"),
                ]
            )
        ),
        maybe(
            notebook(
                [
                    markdown,
                    code("C", 9, "c = 1\n"),
                    code("A", 7, "a = 2\n"),
                    code("D", None, "d = 1\n"),
                ]
            )
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
