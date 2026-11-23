from __future__ import annotations

import json

from dirdiff.change_source import CapturedLink
from dirdiff.engines import TextDiffEngine
from dirdiff.formats import ComposeContext, Composer
from dirdiff.util import maybe


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
