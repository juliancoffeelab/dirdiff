from __future__ import annotations

import json
from pathlib import Path

from dirdiff.engines import TextDiffEngine
from dirdiff.formats import (
    BayContext,
    ComposeContext,
    Composer,
)
from dirdiff.util import JsonValue, maybe

NOTEBOOKS = Path(__file__).resolve().parents[1] / "presets" / "notebook"


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
