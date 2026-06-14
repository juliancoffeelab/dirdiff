from pathlib import Path
import subprocess

from dirdiff.diff import GitBackend, TextDiffService


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
    subprocess.run(
        ["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True
    )
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

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    paths = service.list_repo_diff_paths(left="head", right="worktree")

    assert len(paths) == 1
    assert paths[0].display_name == "alpha.txt -> renamed.txt"
    assert paths[0].change_type == "rename"
    assert paths[0].left_path == "alpha.txt"
    assert paths[0].right_path == "renamed.txt"


def test_branch_review_uses_explicit_remote_refs(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True
    )
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
    subprocess.run(
        ["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/upstream/main", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    tracked_file.write_text("two\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "second"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    branch_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/upstream/rich-text", branch_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/rich-text", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/cjgrand1/rich-text", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    ref_choices = service.list_ref_choices()
    merge_base, normalized_branch = service.resolve_branch_diff_sides(
        base_branch="upstream/main",
        branch="upstream/rich-text",
    )

    assert ref_choices["remote_names"] == ["cjgrand1", "origin", "upstream"]
    assert "cjgrand1/rich-text" in ref_choices["remotes"]
    assert "origin/rich-text" in ref_choices["remotes"]
    assert "upstream/main" in ref_choices["remotes"]
    assert "upstream/rich-text" in ref_choices["remotes"]
    assert merge_base == base_commit
    assert normalized_branch == "upstream/rich-text"

    try:
        service.normalize_side("rich-text")
    except ValueError as exc:
        assert str(exc) == "Unknown Git ref: rich-text"
    else:
        raise AssertionError("expected remote ref to require explicit remote name")


def test_numstat_parser_reads_changed_rename_records(tmp_path: Path) -> None:
    repository = GitBackend(tmp_path)

    counts = repository._parse_numstat_output(b"2\t1\t\0old/name.txt\0new/name.txt\0")

    assert counts == {"new/name.txt": (2, 1)}
