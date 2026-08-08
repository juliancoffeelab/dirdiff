"""Integration checks for repository path and ref loading.

These tests use real Git repositories to verify rename detection and explicit
remote-ref comparisons through the backend/service boundary.  They should keep
Git setup local to temporary directories and assert public diff-path contracts,
not private command strings.
"""

import subprocess
from pathlib import Path

from helpers import TextDiffService

from dirdiff.backend import GitBackend

__all__: list[str] = []


def test_detects_git_reported_repo_renames(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
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
    tracked_file.write_text("one\ntwo\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
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
    paths = service.list_repo_diff_paths(left="HEAD", right="worktree")

    assert len(paths) == 1
    assert paths[0].display_name == "alpha.txt -> renamed.txt"
    assert paths[0].change_type == "rename"
    assert paths[0].left_path == "alpha.txt"
    assert paths[0].right_path == "renamed.txt"


def test_branch_review_uses_explicit_remote_refs(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
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
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
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
    for remote_name in ["upstream", "origin", "cjgrand1"]:
        subprocess.run(
            [
                "git",
                "remote",
                "add",
                remote_name,
                f"https://example.invalid/{remote_name}.git",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
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
    resolved_base_branch, merge_base, normalized_branch = (
        service.resolve_branch_diff_sides(
            base_selection={
                "source": "remote",
                "remote": "upstream",
                "branch": "main",
            },
            review_selection={
                "source": "remote",
                "remote": "upstream",
                "branch": "rich-text",
            },
        )
    )

    assert ref_choices["remotes"] == ["cjgrand1", "origin", "upstream"]
    remote_branches = ref_choices["remote_branches"]
    assert {
        "structured": {
            "remote": "cjgrand1",
            "branch": "rich-text",
        },
        "gitref": "cjgrand1/rich-text",
    } in remote_branches
    assert {
        "structured": {
            "remote": "origin",
            "branch": "rich-text",
        },
        "gitref": "origin/rich-text",
    } in remote_branches
    assert {
        "structured": {
            "remote": "upstream",
            "branch": "main",
        },
        "gitref": "upstream/main",
    } in remote_branches
    assert {
        "structured": {
            "remote": "upstream",
            "branch": "rich-text",
        },
        "gitref": "upstream/rich-text",
    } in remote_branches
    assert resolved_base_branch == "upstream/main"
    assert merge_base == base_commit
    assert normalized_branch == "upstream/rich-text"

    try:
        service.normalize_side("rich-text")
    except ValueError as exc:
        assert str(exc) == "Unknown Git ref: rich-text"
    else:
        raise AssertionError(
            "expected remote ref to require explicit remote name"
        )


def test_ref_choices_use_configured_remote_names_with_slashes(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
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
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
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
        [
            "git",
            "remote",
            "add",
            "team/origin",
            "https://example.invalid/repo.git",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/team/origin/main", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    ref_choices = service.list_ref_choices()

    assert ref_choices["remotes"] == ["team/origin"]
    assert {
        "structured": {
            "remote": "team/origin",
            "branch": "main",
        },
        "gitref": "team/origin/main",
    } in ref_choices["remote_branches"]


def test_numstat_parser_reads_changed_rename_records(tmp_path: Path) -> None:
    repository = GitBackend(tmp_path)

    counts = repository._parse_numstat_output(
        b"2\t1\t\0old/name.txt\0new/name.txt\0"
    )

    assert counts == {"new/name.txt": (2, 1)}
