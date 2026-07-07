"""Native text-rendering behavior tests.

These tests exercise `helpers.build_loaded_diff` as the public test boundary
for rendered text payloads.  They assert user-visible row statuses, token
boundaries, summaries, and syntax/fold metadata without going through HTTP or a
browser session.
"""

import json

from helpers import build_loaded_diff

__all__: list[str] = []


def test_counts_whitespace_only_changes_as_modified() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="    value = 1\n",
        right_text="\tvalue = 1\n",
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["summary"]["changed_lines"] == 1
    assert diff["summary"]["modified_lines"] == 1
    assert diff["rows"][0]["status"] == "equal"
    assert diff["rows"][0]["left_tokens"] != []


def test_inline_diff_keeps_camel_case_boundaries_intact() -> None:
    diff = build_loaded_diff(
        display_name="demo.js",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="function findNearestIndex(positions, viewportCenter) {}\n",
        right_text="function positionsSignature(positions) {}\n",
        left_path_hint="demo.js",
        right_path_hint="demo.js",
    )

    left_name_tokens = []
    for token in diff["rows"][0]["left_tokens"]:
        if token["text"] == "(":
            break
        if token["text"] != "function" and not token["is_ws"]:
            left_name_tokens.append(
                (token["text"], (token["status"] != "unchanged"))
            )

    right_name_tokens = []
    for token in diff["rows"][0]["right_tokens"]:
        if token["text"] == "(":
            break
        if token["text"] != "function" and not token["is_ws"]:
            right_name_tokens.append(
                (token["text"], (token["status"] != "unchanged"))
            )

    assert left_name_tokens == [
        ("find", True),
        ("Nearest", True),
        ("Index", True),
    ]
    assert right_name_tokens == [
        ("positions", True),
        ("Signature", True),
    ]


def test_inline_diff_keeps_identifier_parts_whole_in_method_renames() -> None:
    diff = build_loaded_diff(
        display_name="demo.js",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="await expectVisibleRow(page, 0);\n",
        right_text="await expectSelectedRowIndex(page, 0);\n",
        left_path_hint="demo.js",
        right_path_hint="demo.js",
    )

    left_tokens = [
        (token["text"], (token["status"] != "unchanged"))
        for token in diff["rows"][0]["left_tokens"]
        if token["text"] not in {"await", " ", "(", "page", ",", "0", ");"}
    ]
    right_tokens = [
        (token["text"], (token["status"] != "unchanged"))
        for token in diff["rows"][0]["right_tokens"]
        if token["text"] not in {"await", " ", "(", "page", ",", "0", ");"}
    ]

    assert left_tokens == [
        ("expect", False),
        ("Visible", True),
        ("Row", False),
    ]
    assert right_tokens == [
        ("expect", False),
        ("Selected", True),
        ("Row", False),
        ("Index", True),
    ]


def test_tree_sitter_highlights_multiline_python_strings() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text='value = """hello\nworld"""\n',
        right_text='value = """hello\nworld"""\n',
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    first_line_classes = {
        css_class
        for span in diff["rows"][0]["left_syntax"]
        for css_class in span["classes"]
    }
    second_line_classes = {
        css_class
        for span in diff["rows"][1]["left_syntax"]
        for css_class in span["classes"]
    }

    assert "ts-string" in first_line_classes
    assert "ts-string" in second_line_classes


def test_tree_sitter_highlights_clojure_strings() -> None:
    diff = build_loaded_diff(
        display_name="demo.clj",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text='(defn greet [] "hello")\n',
        right_text='(defn greet [] "hello")\n',
        left_path_hint="demo.clj",
        right_path_hint="demo.clj",
    )

    first_line_classes = {
        css_class
        for span in diff["rows"][0]["left_syntax"]
        for css_class in span["classes"]
    }

    assert "ts-string" in first_line_classes


def test_large_tree_sitter_diff_keeps_rich_render_mode() -> None:
    repeated_line = "value = 1234567890\n"
    left_text = repeated_line * 12050
    right_text = left_text.replace("1234567890", "1234567891", 1)

    diff = build_loaded_diff(
        display_name="large.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="large.py",
        right_path_hint="large.py",
    )

    assert "render_mode" not in diff
    assert "truncated_rows" not in diff
    assert any(row.get("left_syntax") for row in diff["rows"])
    assert any(row.get("right_syntax") for row in diff["rows"])
    assert all(row["status"] != "fold" for row in diff["rows"])
    changed_rows = [
        row
        for row in diff["rows"]
        if row.get("left_text") != row.get("right_text")
    ]
    assert changed_rows != []
    assert "right_syntax" in changed_rows[0] or "left_syntax" in changed_rows[0]


def test_large_plaintext_diff_still_falls_back_to_plain_render_mode() -> None:
    repeated_line = "value = 1234567890\n"
    left_text = repeated_line * 12050
    right_text = left_text.replace("1234567890", "1234567891", 1)

    diff = build_loaded_diff(
        display_name="large.txt",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="large.txt",
        right_path_hint="large.txt",
    )

    assert diff["render_mode"] == "plain"
    assert any(row["status"] == "fold" for row in diff["rows"])
    assert not any(row.get("left_syntax") for row in diff["rows"])


def test_build_loaded_diff_renders_notebook_cells_when_ipynb_is_valid() -> None:
    left_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {},
                    "source": ["value = 1\n", "print(value)\n"],
                    "outputs": [],
                }
            ],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    right_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {"collapsed": True},
                    "source": ["value = 2\n", "print(value)\n"],
                    "outputs": [
                        {
                            "output_type": "stream",
                            "name": "stdout",
                            "text": "2\n",
                        }
                    ],
                }
            ],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )

    diff = build_loaded_diff(
        display_name="demo.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="demo.ipynb",
        right_path_hint="demo.ipynb",
    )

    assert diff["render_kind"] == "notebook"
    assert diff["summary"]["changed_cells"] == 1
    assert diff["cells"][0]["metadata_changed"] is True
    assert diff["cells"][0]["outputs_changed"] is True
    assert any(
        row["left_text"] == "value = 1" and row["right_text"] == "value = 2"
        for row in diff["cells"][0]["source_rows"]
    )


def test_build_loaded_diff_pairs_notebook_cells_by_partial_unique_ids() -> None:
    left_text = json.dumps(
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
                    "metadata": {},
                    "source": ["value = 1\n"],
                    "outputs": [],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    right_text = json.dumps(
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
                    "metadata": {},
                    "source": ["value = 1\n"],
                    "outputs": [],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )

    diff = build_loaded_diff(
        display_name="demo.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="demo.ipynb",
        right_path_hint="demo.ipynb",
    )

    assert diff["render_kind"] == "notebook"
    assert diff["summary"]["changed_cells"] == 1
    assert diff["summary"]["modified_cells"] == 1
    assert diff["summary"]["added_cells"] == 0
    assert diff["summary"]["removed_cells"] == 0
    assert diff["cells"][0]["kind"] == "modified"
    assert diff["cells"][0]["cell_id"] == "intro"
    assert diff["cells"][0]["left_index"] == 0
    assert diff["cells"][0]["right_index"] == 0


def test_build_loaded_diff_keeps_notebook_metadata_and_outputs_lazy() -> None:
    left_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {},
                    "source": ["value = 1\n", "print(value)\n"],
                    "outputs": [],
                }
            ],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    right_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {"collapsed": True},
                    "source": ["value = 1\n", "print(value)\n"],
                    "outputs": [
                        {
                            "output_type": "stream",
                            "name": "stdout",
                            "text": "1\n",
                        }
                    ],
                }
            ],
            "metadata": {
                "kernelspec": {"name": "python3"},
                "language_info": {"name": "python"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )

    diff = build_loaded_diff(
        display_name="demo.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="demo.ipynb",
        right_path_hint="demo.ipynb",
    )

    assert diff["render_kind"] == "notebook"
    assert diff["summary"]["notebook_metadata_changed"] is True
    assert diff["notebook_metadata_rows"] == []
    assert diff["notebook_metadata_lazy"] is True
    assert diff["notebook_metadata_hunk_count"] >= 1

    cell = diff["cells"][0]
    assert cell["metadata_changed"] is True
    assert cell["outputs_changed"] is True
    assert cell["metadata_rows"] == []
    assert cell["outputs_rows"] == []
    assert cell["metadata_lazy"] is True
    assert cell["outputs_lazy"] is True
    assert cell["metadata_hunk_count"] >= 1
    assert cell["outputs_hunk_count"] >= 1


def test_build_loaded_diff_falls_back_to_text_for_invalid_notebook_json() -> (
    None
):
    diff = build_loaded_diff(
        display_name="broken.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text='{"cells": [}\n',
        right_text='{"cells": []}\n',
        left_path_hint="broken.ipynb",
        right_path_hint="broken.ipynb",
    )

    assert diff.get("render_kind") != "notebook"
    assert "rows" in diff
