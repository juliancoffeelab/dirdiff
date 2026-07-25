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
    assert any(
        part["diff_status"] != "unchanged"
        for part in diff["rows"][0]["left_parts"]
    )
    assert diff["hunk_count"] == 1
    assert diff["rows"][0]["hunk_index"] == 0


def test_file_diff_assigns_ordered_file_local_hunk_indices() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="first = 1\ncontext = 0\nsecond = 1\n",
        right_text="first = 2\ncontext = 0\nsecond = 2\n",
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["hunk_count"] == 2
    assert [row["hunk_index"] for row in diff["rows"]] == [0, None, 1]


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

    left_name_parts = []
    for part in diff["rows"][0]["left_parts"]:
        if part["text"] == "(":
            break
        left_is_whitespace: bool = part["is_whitespace"]
        if part["text"] != "function" and not left_is_whitespace:
            left_name_parts.append(
                (part["text"], (part["diff_status"] != "unchanged"))
            )

    right_name_parts = []
    for part in diff["rows"][0]["right_parts"]:
        if part["text"] == "(":
            break
        right_is_whitespace: bool = part["is_whitespace"]
        if part["text"] != "function" and not right_is_whitespace:
            right_name_parts.append(
                (part["text"], (part["diff_status"] != "unchanged"))
            )

    assert left_name_parts == [("findNearestIndex", True)]
    assert right_name_parts == [("positionsSignature", True)]


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

    left_parts = [
        (part["text"], (part["diff_status"] != "unchanged"))
        for part in diff["rows"][0]["left_parts"]
        if part["text"]
        not in {"await", " ", "(", "page", ",", "0", ")", ");", ";"}
    ]
    right_parts = [
        (part["text"], (part["diff_status"] != "unchanged"))
        for part in diff["rows"][0]["right_parts"]
        if part["text"]
        not in {"await", " ", "(", "page", ",", "0", ")", ");", ";"}
    ]

    assert left_parts == [
        ("expect", False),
        ("Visible", True),
        ("Row", False),
    ]
    assert right_parts == [
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
        for part in diff["rows"][0]["left_parts"]
        for css_class in part["syntax_classes"]
    }
    second_line_classes = {
        css_class
        for part in diff["rows"][1]["left_parts"]
        for css_class in part["syntax_classes"]
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
        for part in diff["rows"][0]["left_parts"]
        for css_class in part["syntax_classes"]
    }

    assert "ts-string" in first_line_classes


def test_large_tree_sitter_diff_preserves_rows_and_syntax() -> None:
    repeated_line = "value = 1234567890\n"
    left_text = repeated_line * 1101
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
    assert any(
        part["syntax_classes"]
        for row in diff["rows"]
        for part in row["left_parts"]
    )
    assert any(
        part["syntax_classes"]
        for row in diff["rows"]
        for part in row["right_parts"]
    )
    assert all(row["status"] != "fold" for row in diff["rows"])
    changed_rows = [
        row
        for row in diff["rows"]
        if row.get("left_text") != row.get("right_text")
    ]
    assert changed_rows != []
    assert any(
        part["syntax_classes"]
        for part in [
            *changed_rows[0]["left_parts"],
            *changed_rows[0]["right_parts"],
        ]
    )


def test_large_plaintext_diff_preserves_middle_rows_and_hunks() -> None:
    left_text = "".join(f"line {index:04d}\n" for index in range(1101))
    right_text = "".join(
        f"{'changed' if index % 10 == 0 else 'line'} {index:04d}\n"
        for index in range(1101)
    )

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

    assert "render_mode" not in diff
    assert "truncated_rows" not in diff
    assert all(
        row["status"] in {"equal", "replace", "insert", "delete", "move"}
        for row in diff["rows"]
    )
    middle_hunk_rows = [
        row
        for row in diff["rows"]
        if row.get("left_text") == "line 0550" and row["hunk_index"] == 55
    ]
    assert len(middle_hunk_rows) == 1
    assert any(row.get("right_text") == "changed 0550" for row in diff["rows"])
    assert diff["hunk_count"] == 111
    assert not any(
        part["syntax_classes"]
        for row in diff["rows"]
        for part in row["left_parts"]
    )


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


def test_build_loaded_diff_keeps_notebook_secondary_changes_summary_only() -> (
    None
):
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
    assert "notebook_metadata_rows" not in diff
    assert "notebook_metadata_hunk_count" not in diff

    cell = diff["cells"][0]
    assert cell["metadata_changed"] is True
    assert cell["outputs_changed"] is True
    assert "metadata_rows" not in cell
    assert "outputs_rows" not in cell
    assert "metadata_hunk_count" not in cell
    assert "outputs_hunk_count" not in cell
    assert diff["hunk_count"] == cell["source_hunk_count"] == 0


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
