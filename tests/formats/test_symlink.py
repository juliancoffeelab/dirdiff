"""Check symbolic-link composition from immutable captured link facts.

The tests exercise the two-bay contract directly: the outer link shows only its
payload, nested links move into synthetic target text, failed walks diagnose
there, and notebook targets flatten without outputs. Repository traversal itself
is covered through the real Git API integration test.
"""

from __future__ import annotations

import json

from dirdiff.engines import engine
from dirdiff.formats import CapturedLink, ComposeContext, Composer


def test_link_payload_precedes_one_collapsed_text_target() -> None:
    """Show raw outer payloads before one synthetic target comparison.

    The direct bay contains no repository path or arrow. The target bay keeps
    each nested link behind a comment wall, uses its link-specific coordinate,
    and remains collapsed without losing the reached text.
    """
    composed = Composer().compose(
        b"before-hop",
        b"after-hop",
        ComposeContext.build(
            left_path="guide",
            right_path="guide",
            left_label="old",
            right_label="new",
            left_link=CapturedLink(
                nested_links=(("before-hop", "before.txt"),),
                diagnosis=None,
                target_path="before.txt",
                target_data=b"old guidance\n",
            ),
            right_link=CapturedLink(
                nested_links=(("after-hop", "after.txt"),),
                diagnosis=None,
                target_path="after.txt",
                target_data=b"new guidance\n",
            ),
            renderer=engine("dirdiff"),
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
    """Keep the raw payload clean and diagnose failure in the target bay.

    Missing bytes must not become empty target content or contaminate the outer
    link's exact payload with repository paths and arrows.
    """
    composed = Composer().compose(
        b"missing-before",
        b"missing-after",
        ComposeContext.build(
            left_path="guide",
            right_path="guide",
            left_label="old",
            right_label="new",
            left_link=CapturedLink(
                nested_links=(),
                diagnosis="stopped: missing target",
                target_path=None,
                target_data=None,
            ),
            right_link=CapturedLink(
                nested_links=(),
                diagnosis="stopped: missing target",
                target_path=None,
                target_data=None,
            ),
            renderer=engine("dirdiff"),
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
    """Compose a reached notebook as one reviewable script-like target bay.

    Code stays executable-looking, prose is commented below a typed wall, and
    output records never enter the synthetic text. The target must still be one
    bay rather than reproducing notebook cell frames inside the link File.
    """
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
        b"before.ipynb",
        b"after.ipynb",
        ComposeContext.build(
            left_path="analysis",
            right_path="analysis",
            left_label="old",
            right_label="new",
            left_link=CapturedLink(
                nested_links=(),
                diagnosis=None,
                target_path="before.ipynb",
                target_data=old_notebook,
            ),
            right_link=CapturedLink(
                nested_links=(),
                diagnosis=None,
                target_path="after.ipynb",
                target_data=new_notebook,
            ),
            renderer=engine("dirdiff"),
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
