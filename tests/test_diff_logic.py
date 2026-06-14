import json

from dirdiff.diff import build_loaded_diff


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
    assert diff["rows"][0]["left_tokens"]


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


def test_fold_hints_include_unchanged_top_level_function_body() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="def helper():\n    value = 1\n    return value\n\nx = 1\n",
        right_text="def helper():\n    value = 1\n    return value\n\nx = 2\n",
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 3,
            "label": "def helper():",
        }
    ]


def test_changed_top_level_function_does_not_fold_descendants() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            'def helper():\n    config = {\n        "a": 1,\n    }\n    return None\n'
        ),
        right_text=(
            "def helper():\n"
            "    config = {\n"
            '        "a": 1,\n'
            "    }\n"
            '    return config.get("a")\n'
        ),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff.get("fold_hints", []) == []


def test_fold_hints_include_unchanged_top_level_dict_body() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=('CONFIG = {\n    "a": 1,\n    "b": 2,\n}\n\nvalue = 1\n'),
        right_text=('CONFIG = {\n    "a": 1,\n    "b": 2,\n}\n\nvalue = 2\n'),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "CONFIG = {",
        }
    ]


def test_unchanged_top_level_class_folds_methods_but_not_whole_class() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 2\n\n"
            "value = 1\n"
        ),
        right_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 2\n\n"
            "value = 2\n"
        ),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 3,
            "label": "def a(self):",
        },
        {
            "start_row": 5,
            "end_row": 6,
            "label": "def b(self):",
        },
    ]


def test_changed_class_still_folds_only_unchanged_methods() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 2\n"
        ),
        right_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 3\n"
        ),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 3,
            "label": "def a(self):",
        }
    ]


def test_whitespace_only_changes_block_folding() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="def helper():\n    value = 1\n    return value\n",
        right_text="def helper():\n\tvalue = 1\n\treturn value\n",
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff.get("fold_hints", []) == []


def test_javascript_classes_fold_unchanged_methods_only() -> None:
    diff = build_loaded_diff(
        display_name="demo.js",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "class Example {\n  a() {\n    return 1;\n  }\n}\n\nconst value = 1;\n"
        ),
        right_text=(
            "class Example {\n  a() {\n    return 1;\n  }\n}\n\nconst value = 2;\n"
        ),
        left_path_hint="demo.js",
        right_path_hint="demo.js",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 4,
            "label": "a() {",
        }
    ]


def test_css_unchanged_top_level_rule_folds_declarations() -> None:
    diff = build_loaded_diff(
        display_name="demo.css",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            ".card {\n"
            "  color: red;\n"
            "  background: blue;\n"
            "}\n\n"
            ":root {\n"
            "  color: black;\n"
            "}\n"
        ),
        right_text=(
            ".card {\n"
            "  color: red;\n"
            "  background: blue;\n"
            "}\n\n"
            ":root {\n"
            "  color: white;\n"
            "}\n"
        ),
        left_path_hint="demo.css",
        right_path_hint="demo.css",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": ".card {",
        }
    ]


def test_changed_css_media_rule_still_folds_unchanged_nested_rule() -> None:
    diff = build_loaded_diff(
        display_name="demo.css",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "@media screen {\n"
            "  .card {\n"
            "    color: red;\n"
            "    background: blue;\n"
            "  }\n"
            "}\n"
        ),
        right_text=(
            "@media screen {\n"
            "  .card {\n"
            "    color: red;\n"
            "    background: blue;\n"
            "  }\n"
            "  .badge {\n"
            "    color: white;\n"
            "  }\n"
            "}\n"
        ),
        left_path_hint="demo.css",
        right_path_hint="demo.css",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 5,
            "label": ".card {",
        }
    ]


def test_rust_impl_blocks_fold_unchanged_methods_only() -> None:
    diff = build_loaded_diff(
        display_name="demo.rs",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "impl Thing {\n"
            "    fn a(&self) {\n"
            "        1;\n"
            "    }\n"
            "}\n\n"
            "const VALUE: i32 = 1;\n"
        ),
        right_text=(
            "impl Thing {\n"
            "    fn a(&self) {\n"
            "        1;\n"
            "    }\n"
            "}\n\n"
            "const VALUE: i32 = 2;\n"
        ),
        left_path_hint="demo.rs",
        right_path_hint="demo.rs",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 4,
            "label": "fn a(&self) {",
        }
    ]


def test_json_unchanged_nested_top_level_container_folds() -> None:
    diff = build_loaded_diff(
        display_name="demo.json",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text='{\n  "config": {\n    "a": 1\n  },\n  "value": 1\n}\n',
        right_text='{\n  "config": {\n    "a": 1\n  },\n  "value": 2\n}\n',
        left_path_hint="demo.json",
        right_path_hint="demo.json",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 4,
            "label": '"config": {',
        }
    ]


def test_yaml_unchanged_nested_top_level_container_folds() -> None:
    diff = build_loaded_diff(
        display_name="demo.yaml",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="config:\n  a: 1\n  b: 2\nvalue: 1\n",
        right_text="config:\n  a: 1\n  b: 2\nvalue: 2\n",
        left_path_hint="demo.yaml",
        right_path_hint="demo.yaml",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 3,
            "label": "config:",
        }
    ]


def test_toml_unchanged_top_level_table_folds() -> None:
    diff = build_loaded_diff(
        display_name="demo.toml",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="[tool.x]\na = 1\nb = 2\n\n[other]\nvalue = 1\n",
        right_text="[tool.x]\na = 1\nb = 2\n\n[other]\nvalue = 2\n",
        left_path_hint="demo.toml",
        right_path_hint="demo.toml",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "[tool.x]",
        }
    ]


def test_markdown_unchanged_heading_section_folds_under_heading() -> None:
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# Intro\nalpha\nbeta\n\n# Tail\none\n",
        right_text="# Intro\nalpha\nbeta\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "# Intro",
        }
    ]


def test_markdown_changed_parent_section_allows_unchanged_child_heading_fold() -> (
    None
):
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# Parent\nalpha\n## Child\nbeta\n\n# Tail\none\n",
        right_text="# Parent\nchanged\n## Child\nbeta\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 4,
            "end_row": 6,
            "label": "## Child",
        }
    ]


def test_markdown_added_later_sibling_section_keeps_prior_section_folded() -> (
    None
):
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# Intro\nalpha\nbeta\n\n# Tail\none\n",
        right_text="# Intro\nalpha\nbeta\n\n# Added\nnew\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "# Intro",
        }
    ]


def test_markdown_added_sibling_section_keeps_all_prior_unchanged_sections_folded() -> (
    None
):
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# One\na1\na2\n\n# Two\nb1\nb2\n\n# Tail\none\n",
        right_text="# One\na1\na2\n\n# Added\nnew\n\n# Two\nb1\nb2\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "# One",
        },
        {
            "start_row": 8,
            "end_row": 11,
            "label": "# Two",
        },
    ]


def test_markdown_non_heading_content_does_not_fold() -> None:
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="```\nalpha\nbeta\n```\nvalue = 1\n",
        right_text="```\nalpha\nbeta\n```\nvalue = 2\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff.get("fold_hints", []) == []


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
    assert changed_rows
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
