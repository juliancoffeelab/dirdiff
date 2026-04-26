from pathlib import Path
import subprocess

from dirdiff.diff import TextDiffService, build_loaded_diff


def test_counts_whitespace_only_changes_as_modified() -> None:
    diff = build_loaded_diff(
        display_name="demo.txt",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="    value = 1\n",
        right_text="\tvalue = 1\n",
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
            left_name_tokens.append((token["text"], token["changed"]))

    right_name_tokens = []
    for token in diff["rows"][0]["right_tokens"]:
        if token["text"] == "(":
            break
        if token["text"] != "function" and not token["is_ws"]:
            right_name_tokens.append((token["text"], token["changed"]))

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
        left_text="await expectActiveHunk(page, 0);\n",
        right_text="await expectSelectedHunkIndex(page, 0);\n",
        left_path_hint="demo.js",
        right_path_hint="demo.js",
    )

    left_tokens = [
        (token["text"], token["changed"])
        for token in diff["rows"][0]["left_tokens"]
        if token["text"] not in {"await", " ", "(", "page", ",", "0", ");"}
    ]
    right_tokens = [
        (token["text"], token["changed"])
        for token in diff["rows"][0]["right_tokens"]
        if token["text"] not in {"await", " ", "(", "page", ",", "0", ");"}
    ]

    assert left_tokens == [
        ("expect", False),
        ("Active", True),
        ("Hunk", False),
    ]
    assert right_tokens == [
        ("expect", False),
        ("Selected", True),
        ("Hunk", False),
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
            "def helper():\n"
            "    config = {\n"
            '        "a": 1,\n'
            "    }\n"
            "    return None\n"
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
        left_text=(
            "CONFIG = {\n"
            '    "a": 1,\n'
            '    "b": 2,\n'
            "}\n\n"
            "value = 1\n"
        ),
        right_text=(
            "CONFIG = {\n"
            '    "a": 1,\n'
            '    "b": 2,\n'
            "}\n\n"
            "value = 2\n"
        ),
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
            "class Example {\n"
            "  a() {\n"
            "    return 1;\n"
            "  }\n"
            "}\n\n"
            "const value = 1;\n"
        ),
        right_text=(
            "class Example {\n"
            "  a() {\n"
            "    return 1;\n"
            "  }\n"
            "}\n\n"
            "const value = 2;\n"
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


def test_markdown_changed_parent_section_allows_unchanged_child_heading_fold() -> None:
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


def test_markdown_added_later_sibling_section_keeps_prior_section_folded() -> None:
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


def test_markdown_added_sibling_section_keeps_all_prior_unchanged_sections_folded() -> None:
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


def test_builds_direct_file_diff(tmp_path: Path) -> None:
    left_file = tmp_path / "left.txt"
    right_file = tmp_path / "right.txt"
    left_file.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    right_file.write_text("alpha\nbeta changed\ngamma\n", encoding="utf-8")

    service = TextDiffService(repo_root=None, cwd=tmp_path)
    diff = service.build_diff(
        path=None,
        left="index",
        right="worktree",
        left_file="left.txt",
        right_file="right.txt",
    )

    assert diff["display_name"] == "left.txt vs right.txt"
    assert diff["summary"]["changed_lines"] == 1
    assert diff["summary"]["modified_lines"] == 1
    assert diff["summary"]["added_lines"] == 0
    assert diff["summary"]["removed_lines"] == 0


def test_builds_whole_repo_diff_by_default(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    tracked_file = tmp_path / "alpha.txt"
    tracked_file.write_text("one\ntwo\n", encoding="utf-8")
    subprocess.run(["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    tracked_file.write_text("one\ntwo changed\n", encoding="utf-8")
    untracked_file = tmp_path / "beta.txt"
    untracked_file.write_text("new file\n", encoding="utf-8")

    service = TextDiffService.discover(cwd=tmp_path)
    diff = service.build_diff(
        path=None,
        left="index",
        right="worktree",
    )

    assert diff["mode"] == "repo"
    assert diff["summary"]["changed_files"] == 1
    assert diff["summary"]["changed_lines"] == 1
    assert [entry["display_name"] for entry in diff["files"]] == ["alpha.txt"]


def test_detects_git_reported_repo_renames(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    tracked_file = tmp_path / "alpha.txt"
    tracked_file.write_text("one\ntwo\n", encoding="utf-8")
    subprocess.run(["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "mv", "alpha.txt", "renamed.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService.discover(cwd=tmp_path)
    diff = service.build_diff(
        path=None,
        left="head",
        right="worktree",
    )

    assert diff["mode"] == "repo"
    assert diff["summary"]["changed_files"] == 1
    assert diff["summary"]["changed_lines"] == 0
    assert diff["files"][0]["display_name"] == "alpha.txt -> renamed.txt"
    assert diff["files"][0]["change_type"] == "rename"
    assert diff["files"][0]["left_path"] == "alpha.txt"
    assert diff["files"][0]["right_path"] == "renamed.txt"


def test_branch_review_diff_uses_merge_base_with_master(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    tracked_file = tmp_path / "alpha.txt"
    tracked_file.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True, capture_output=True)
    tracked_file.write_text("one\nfeature change\n", encoding="utf-8")
    subprocess.run(["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feature change"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    subprocess.run(["git", "checkout", "master"], cwd=tmp_path, check=True, capture_output=True)
    tracked_file.write_text("one\nmaster-only change\n", encoding="utf-8")
    subprocess.run(["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "master change"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService.discover(cwd=tmp_path)
    diff = service.build_diff(
        path=None,
        left="index",
        right="worktree",
        base_branch="master",
        branch="feature",
    )

    assert diff["mode"] == "repo"
    assert diff["left_label"] == "master...feature"
    assert diff["right_label"] == "feature"
    assert [entry["display_name"] for entry in diff["files"]] == ["alpha.txt"]
    assert diff["summary"]["changed_files"] == 1
    assert diff["summary"]["added_lines"] == 1
    assert diff["summary"]["removed_lines"] == 0
