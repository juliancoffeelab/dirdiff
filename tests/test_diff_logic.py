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
    assert diff["summary"]["changed_files"] == 2
    assert diff["summary"]["changed_lines"] == 2
    assert [entry["display_name"] for entry in diff["files"]] == ["alpha.txt", "beta.txt"]


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
