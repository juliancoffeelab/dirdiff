from dirdiff.diff import build_loaded_diff


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
            "start_row": 0,
            "end_row": 3,
            "kind": "top_level",
            "label": "1 unchanged declaration",
        },
        {
            "start_row": 1,
            "end_row": 3,
            "kind": "function_like",
            "label": "def helper():",
        },
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
            "start_row": 0,
            "end_row": 4,
            "kind": "top_level",
            "label": "1 unchanged declaration",
        },
        {
            "start_row": 1,
            "end_row": 4,
            "kind": "container",
            "label": "CONFIG = {",
        },
    ]


def test_unchanged_top_level_class_folds_class_and_methods() -> None:
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
            "start_row": 0,
            "end_row": 6,
            "kind": "top_level",
            "label": "1 unchanged declaration",
        },
        {
            "start_row": 1,
            "end_row": 6,
            "kind": "class_like",
            "label": "class Example:",
        },
        {
            "start_row": 2,
            "end_row": 3,
            "kind": "function_like",
            "label": "def a(self):",
        },
        {
            "start_row": 5,
            "end_row": 6,
            "kind": "function_like",
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
            "kind": "function_like",
            "label": "def a(self):",
        }
    ]


def test_changed_function_still_folds_unchanged_nested_functions() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "def outer():\n"
            "    def unchanged_inner():\n"
            "        value = 1\n"
            "        return value\n\n"
            "    def changed_inner():\n"
            "        value = 2\n"
            "        return value\n\n"
            "    return unchanged_inner() + changed_inner()\n"
        ),
        right_text=(
            "def outer():\n"
            "    def unchanged_inner():\n"
            "        value = 1\n"
            "        return value\n\n"
            "    def changed_inner():\n"
            "        value = 3\n"
            "        return value\n\n"
            "    return unchanged_inner() + changed_inner()\n"
        ),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 4,
            "kind": "function_like",
            "label": "def unchanged_inner():",
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


def test_javascript_classes_fold_class_and_methods() -> None:
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
            "start_row": 0,
            "end_row": 5,
            "kind": "top_level",
            "label": "1 unchanged declaration",
        },
        {
            "start_row": 1,
            "end_row": 5,
            "kind": "class_like",
            "label": "class Example {",
        },
        {
            "start_row": 2,
            "end_row": 4,
            "kind": "function_like",
            "label": "a() {",
        },
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
            "start_row": 0,
            "end_row": 4,
            "kind": "top_level",
            "label": "1 unchanged declaration",
        },
        {
            "start_row": 1,
            "end_row": 4,
            "kind": "container",
            "label": ".card {",
        },
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
            "kind": "container",
            "label": ".card {",
        }
    ]


def test_changed_css_middle_rule_still_folds_unchanged_siblings() -> None:
    diff = build_loaded_diff(
        display_name="demo.css",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            ".toolbar {\n"
            "  display: flex;\n"
            "  gap: 8px;\n"
            "}\n\n"
            ".sidebar {\n"
            "  width: 240px;\n"
            "  padding: 16px;\n"
            "}\n\n"
            ".content {\n"
            "  color: #24231f;\n"
            "  line-height: 1.5;\n"
            "}\n\n"
            ".footer {\n"
            "  border-top: 1px solid #ded8cc;\n"
            "  padding: 12px;\n"
            "}\n"
        ),
        right_text=(
            ".toolbar {\n"
            "  display: flex;\n"
            "  gap: 8px;\n"
            "}\n\n"
            ".sidebar {\n"
            "  width: 240px;\n"
            "  padding: 16px;\n"
            "}\n\n"
            ".content {\n"
            "  color: #1f3f8a;\n"
            "  line-height: 1.5;\n"
            "}\n\n"
            ".footer {\n"
            "  border-top: 1px solid #ded8cc;\n"
            "  padding: 12px;\n"
            "}\n"
        ),
        left_path_hint="demo.css",
        right_path_hint="demo.css",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 0,
            "end_row": 9,
            "kind": "top_level",
            "label": "2 unchanged declarations",
        },
        {
            "start_row": 1,
            "end_row": 4,
            "kind": "container",
            "label": ".toolbar {",
        },
        {
            "start_row": 6,
            "end_row": 9,
            "kind": "container",
            "label": ".sidebar {",
        },
        {
            "start_row": 15,
            "end_row": 19,
            "kind": "top_level",
            "label": "1 unchanged declaration",
        },
        {
            "start_row": 16,
            "end_row": 19,
            "kind": "container",
            "label": ".footer {",
        },
    ]


def test_rust_impl_blocks_fold_impl_and_methods() -> None:
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
            "start_row": 0,
            "end_row": 5,
            "kind": "top_level",
            "label": "1 unchanged declaration",
        },
        {
            "start_row": 1,
            "end_row": 5,
            "kind": "class_like",
            "label": "impl Thing {",
        },
        {
            "start_row": 2,
            "end_row": 4,
            "kind": "function_like",
            "label": "fn a(&self) {",
        },
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
            "kind": "container",
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
            "kind": "container",
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
            "kind": "container",
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
            "kind": "section",
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
            "kind": "section",
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
            "kind": "section",
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
            "kind": "section",
            "label": "# One",
        },
        {
            "start_row": 8,
            "end_row": 11,
            "kind": "section",
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
