"""Check native text rows after display enrichment.

The tests use `helpers.build_loaded_diff` to assert row status, inline token
boundaries, summaries, syntax classes, fold hints, and bay-local hunk indexes.
They stop at the composed payload rather than exercising HTTP or browser
interaction.
"""

from helpers import build_loaded_diff

__all__: list[str] = []


def test_counts_whitespace_only_changes_as_modified() -> None:
    """Count changed indentation even when the row remains visually equal.

    Inline whitespace tokens carry the change, so summaries must not rely on
    row status alone.
    """
    diff = build_loaded_diff(
        display_name="demo.py",
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
    """Start one hunk at each changed run and number them in File order.

    Equal context separates hunks; continuation rows in a changed run do not
    receive another start index.
    """
    diff = build_loaded_diff(
        display_name="demo.py",
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
    """Decorate CamelCase replacements at identifier-part boundaries.

    Stable name parts remain whole and unchanged instead of fragmenting into a
    character-level diff.
    """
    diff = build_loaded_diff(
        display_name="demo.js",
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
    """Keep shared method-name parts intact through a targeted rename.

    The inline partition isolates the changed identifier part while replaying
    both complete method calls.
    """
    diff = build_loaded_diff(
        display_name="demo.js",
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
    """Split one multiline Python capture into valid line-local syntax spans.

    Tree-sitter byte ranges may cross newlines; the HUD contract may not.
    """
    diff = build_loaded_diff(
        display_name="demo.py",
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
    """Load the Clojure grammar and map strings to declared syntax classes.

    This guards the real language module and query-resource path.
    """
    diff = build_loaded_diff(
        display_name="demo.clj",
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
    """Preserve every row and syntax span on a large parsed source pair.

    The optimized capture path must not drop middle rows or decoration.
    """
    repeated_line = "value = 1234567890\n"
    left_text = repeated_line * 1101
    right_text = left_text.replace("1234567890", "1234567891", 1)

    diff = build_loaded_diff(
        display_name="large.py",
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
    """Preserve middle rows and hunk boundaries without syntax support.

    Large plaintext remains lossless and keeps changed-run navigation intact.
    """
    left_text = "".join(f"line {index:04d}\n" for index in range(1101))
    right_text = "".join(
        f"{'changed' if index % 10 == 0 else 'line'} {index:04d}\n"
        for index in range(1101)
    )

    diff = build_loaded_diff(
        display_name="large.txt",
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


def test_build_loaded_diff_falls_back_to_text_for_invalid_notebook_json() -> (
    None
):
    """Represent invalid notebook structure through the raw-text damage path.

    Captured text remains reviewable and the result must not claim a valid
    notebook payload.
    """
    diff = build_loaded_diff(
        display_name="broken.ipynb",
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
