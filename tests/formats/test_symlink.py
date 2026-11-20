from __future__ import annotations

import json

from dirdiff.change_source import CapturedLink
from dirdiff.engines import TextDiffEngine
from dirdiff.formats import ComposeContext, Composer
from dirdiff.util import maybe


def test_link_payload_precedes_one_collapsed_text_target() -> None:
    composed = Composer().compose(
        maybe(b"before-hop"),
        maybe(b"after-hop"),
        ComposeContext.build(
            left_path=maybe("guide"),
            right_path=maybe("guide"),
            left_label="old",
            right_label="new",
            left_link=maybe(
                CapturedLink(
                    nested_links=(("before-hop", "before.txt"),),
                    diagnosis=maybe(None),
                    target_path=maybe("before.txt"),
                    target_data=maybe(b"old guidance\n"),
                )
            ),
            right_link=maybe(
                CapturedLink(
                    nested_links=(("after-hop", "after.txt"),),
                    diagnosis=maybe(None),
                    target_path=maybe("after.txt"),
                    target_data=maybe(b"new guidance\n"),
                )
            ),
            renderer=TextDiffEngine(),
        ),
    )

    assert len(composed["frames"]) == 1
    bays = composed["frames"][0]["bays"]
    assert [bay["bay_key"] for bay in bays] == [
        "symlink",
        "symlink-target",
    ]
    assert bays[0]["collapsible"] is False
    link = bays[0]["kind_data"]
    assert link["kind"] == "text"
    assert [row["left_text"] for row in link["rows"]] == ["before-hop"]
    assert [row["right_text"] for row in link["rows"]] == ["after-hop"]
    assert "->" not in repr(link)
    assert bays[1]["collapsible"] is True
    assert bays[1]["default_expanded"] is False
    target = bays[1]["kind_data"]
    assert target["kind"] == "text"
    assert any(row["left_text"] == "# %% before-hop" for row in target["rows"])
    assert any(row["left_text"] == "before.txt" for row in target["rows"])
    assert any(row["right_text"] == "# %% after-hop" for row in target["rows"])
    assert any(row["right_text"] == "after.txt" for row in target["rows"])
    assert any(row["left_text"] == "old guidance" for row in target["rows"])
    assert any(row["right_text"] == "new guidance" for row in target["rows"])


def test_broken_link_puts_diagnosis_in_synthetic_target() -> None:
    composed = Composer().compose(
        maybe(b"missing-before"),
        maybe(b"missing-after"),
        ComposeContext.build(
            left_path=maybe("guide"),
            right_path=maybe("guide"),
            left_label="old",
            right_label="new",
            left_link=maybe(
                CapturedLink(
                    nested_links=(),
                    diagnosis=maybe("stopped: missing target"),
                    target_path=maybe(None),
                    target_data=maybe(None),
                )
            ),
            right_link=maybe(
                CapturedLink(
                    nested_links=(),
                    diagnosis=maybe("stopped: missing target"),
                    target_path=maybe(None),
                    target_data=maybe(None),
                )
            ),
            renderer=TextDiffEngine(),
        ),
    )

    bays = composed["frames"][0]["bays"]
    assert [bay["bay_key"] for bay in bays] == ["symlink", "symlink-target"]
    link = bays[0]["kind_data"]
    assert link["kind"] == "text"
    assert [row["left_text"] for row in link["rows"]] == ["missing-before"]
    target = bays[1]["kind_data"]
    assert target["kind"] == "text"
    assert any(
        row["left_text"] == "# stopped: missing target"
        for row in target["rows"]
    )


def test_notebook_target_flattens_cells_and_omits_outputs() -> None:
    old_notebook = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "markdown",
                    "id": "intro",
                    "metadata": {},
                    "source": ["Old title\n"],
                },
                {
                    "cell_type": "code",
                    "execution_count": 1,
                    "id": "calculation",
                    "metadata": {},
                    "outputs": [
                        {
                            "name": "stdout",
                            "output_type": "stream",
                            "text": ["SECRET OLD OUTPUT\n"],
                        }
                    ],
                    "source": ["value = 1\n"],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    ).encode()
    new_notebook = old_notebook.replace(b"Old title", b"New title").replace(
        b"value = 1", b"value = 2"
    )
    composed = Composer().compose(
        maybe(b"before.ipynb"),
        maybe(b"after.ipynb"),
        ComposeContext.build(
            left_path=maybe("analysis"),
            right_path=maybe("analysis"),
            left_label="old",
            right_label="new",
            left_link=maybe(
                CapturedLink(
                    nested_links=(),
                    diagnosis=maybe(None),
                    target_path=maybe("before.ipynb"),
                    target_data=maybe(old_notebook),
                )
            ),
            right_link=maybe(
                CapturedLink(
                    nested_links=(),
                    diagnosis=maybe(None),
                    target_path=maybe("after.ipynb"),
                    target_data=maybe(new_notebook),
                )
            ),
            renderer=TextDiffEngine(),
        ),
    )

    bays = composed["frames"][0]["bays"]
    assert [bay["bay_key"] for bay in bays] == [
        "symlink",
        "symlink-target",
    ]
    target = bays[1]["kind_data"]
    assert target["kind"] == "text"
    text = "\n".join(
        value
        for row in target["rows"]
        for value in (row["left_text"], row["right_text"])
        if value is not None
    )
    assert "# %% [markdown]" in text
    assert "# Old title" in text
    assert "# New title" in text
    assert "value = 1" in text
    assert "value = 2" in text
    assert "SECRET OLD OUTPUT" not in text
