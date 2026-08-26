"""Integration checks for Git-backed manifest construction.

The tests build real repositories on disk and assert the manifest shape exposed
by `GitBackend` through the test service adapter.  They cover Git status,
lazy-file, and branch-review behavior at the backend boundary rather than
testing renderer internals.
"""

import subprocess
from pathlib import Path

from helpers import TextDiffService

from dirdiff.backend import GitBackend
from dirdiff.engines import GitDiffEngine, TextDiffEngine
from dirdiff.formats import ComposeContext, Composer

__all__: list[str] = []


def test_build_repo_manifest_lists_changed_tracked_files(
    tmp_path: Path,
) -> None:
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

    tracked_file.write_text("one\ntwo changed\n", encoding="utf-8")
    untracked_file = tmp_path / "beta.txt"
    untracked_file.write_text("new file\n", encoding="utf-8")

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    manifest = service.build_repo_manifest(
        left="index",
        right="worktree",
    )

    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["added_lines"] == 1
    assert manifest["summary"]["removed_lines"] == 1
    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "alpha.txt",
            "entry": {
                "file_kind": {"type": "git", "status": "modified"},
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
                "lazy": None,
            },
        }
    ]


def test_build_repo_manifest_can_include_untracked_files_as_lazy(
    tmp_path: Path,
) -> None:
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

    untracked_file = tmp_path / "beta.txt"
    untracked_file.write_text("new file\n", encoding="utf-8")

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    manifest = service.build_repo_manifest(
        left="HEAD",
        right="worktree",
        show_untracked=True,
    )

    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["added_files"] == 1
    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "beta.txt",
            "entry": {
                "file_kind": {"type": "untracked"},
                "left_path": None,
                "right_path": "beta.txt",
                "lazy": "untracked",
            },
        }
    ]


def test_build_repo_manifest_returns_explicit_tree_with_root_files_last(
    tmp_path: Path,
) -> None:
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
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "alpha.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "src" / "beta.txt").write_text("two\n", encoding="utf-8")
    (tmp_path / "src" / "nested" / "gamma.txt").write_text(
        "three\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "."], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    (tmp_path / "alpha.txt").write_text("one changed\n", encoding="utf-8")
    (tmp_path / "src" / "beta.txt").write_text(
        "two changed\n", encoding="utf-8"
    )
    (tmp_path / "src" / "nested" / "gamma.txt").write_text(
        "three changed\n", encoding="utf-8"
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="index", right="worktree")

    assert manifest["tree"] == [
        {
            "type": "directory",
            "name": "src",
            "path": "src",
            "entries": [
                {
                    "type": "directory",
                    "name": "nested",
                    "path": "src/nested",
                    "entries": [
                        {
                            "type": "file",
                            "name": "gamma.txt",
                            "entry": {
                                "file_kind": {
                                    "type": "git",
                                    "status": "modified",
                                },
                                "left_path": "src/nested/gamma.txt",
                                "right_path": "src/nested/gamma.txt",
                                "lazy": None,
                            },
                        }
                    ],
                },
                {
                    "type": "file",
                    "name": "beta.txt",
                    "entry": {
                        "file_kind": {"type": "git", "status": "modified"},
                        "left_path": "src/beta.txt",
                        "right_path": "src/beta.txt",
                        "lazy": None,
                    },
                },
            ],
        },
        {
            "type": "file",
            "name": "alpha.txt",
            "entry": {
                "file_kind": {"type": "git", "status": "modified"},
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
                "lazy": None,
            },
        },
    ]


def test_build_repo_manifest_compacts_single_directory_chains(
    tmp_path: Path,
) -> None:
    """Manifest tree collapses directory segments with only one child dir."""
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
    source_file = tmp_path / "frontend" / "src" / "App.tsx"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("old\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "frontend/src/App.tsx"],
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
    source_file.write_text("new\n", encoding="utf-8")

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="index", right="worktree")

    assert manifest["tree"] == [
        {
            "type": "directory",
            "name": "frontend/src",
            "path": "frontend/src",
            "entries": [
                {
                    "type": "file",
                    "name": "App.tsx",
                    "entry": {
                        "file_kind": {"type": "git", "status": "modified"},
                        "left_path": "frontend/src/App.tsx",
                        "right_path": "frontend/src/App.tsx",
                        "lazy": None,
                    },
                }
            ],
        }
    ]


def test_untracked_lazy_file_can_be_loaded_from_worktree(
    tmp_path: Path,
) -> None:
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

    untracked_file = tmp_path / "beta.txt"
    untracked_file.write_text("new file\n", encoding="utf-8")

    backend = GitBackend.discover(cwd=tmp_path)
    # An untracked File exists only in the worktree, so the left side is
    # absent and composition receives no bytes for it at all.
    composed = Composer().compose(
        None,
        backend.load_version("beta.txt", "worktree"),
        ComposeContext.build(
            left_path=None,
            right_path="beta.txt",
            left_label="HEAD",
            right_label="worktree",
            renderer=TextDiffEngine(),
        ),
    )
    (frame,) = composed["frames"]
    (bay,) = frame["bays"]
    kind_data = bay["kind_data"]
    assert kind_data["kind"] == "text"

    assert composed["summary"]["added_lines"] == 1
    assert kind_data["rows"][0]["status"] == "insert"
    assert kind_data["rows"][0]["right_text"] == "new file"


def test_branch_review_diff_uses_merge_base_with_master(tmp_path: Path) -> None:
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

    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    tracked_file.write_text("one\nfeature change\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "feature change"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    feature_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subprocess.run(
        ["git", "checkout", "master"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    tracked_file.write_text("one\nmaster-only change\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "master change"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "feature"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    resolved_base_branch, merge_base, normalized_branch, review_commit = (
        service.resolve_branch_diff_sides(
            base_selection={"source": "local", "branch": "master"},
            review_selection={"source": "local", "branch": "feature"},
        )
    )
    subprocess.run(
        ["git", "branch", "--force", "feature", "master"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    manifest = service.build_repo_manifest(
        left=merge_base,
        right=review_commit,
    )

    assert resolved_base_branch == "master"
    assert normalized_branch == "feature"
    assert review_commit == feature_commit
    assert manifest["left_label"] == merge_base
    assert manifest["right_label"] == review_commit
    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "alpha.txt",
            "entry": {
                "file_kind": {"type": "git", "status": "modified"},
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
                "lazy": None,
            },
        }
    ]
    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["added_lines"] == 1
    assert manifest["summary"]["removed_lines"] == 0


def test_git_diff_service_uses_git_style_delete_insert_rows(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
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
    changed_file = tmp_path / "alpha.txt"
    changed_file.write_text("one\ntwo\nthree\n", encoding="utf-8")
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
    changed_file.write_text("one\ntwo changed\nthree\n", encoding="utf-8")

    repo = GitBackend.discover(cwd=tmp_path)
    head_content, worktree_content = repo.load_versions(
        (("alpha.txt", "HEAD"), ("alpha.txt", "worktree"))
    )
    assert isinstance(head_content, bytes)
    assert isinstance(worktree_content, bytes)

    # The same two byte sides, composed three times: the ordinary text engine,
    # the Git-style engine, and the Git-style engine with the sides swapped.
    rich_composed = Composer().compose(
        head_content,
        worktree_content,
        ComposeContext.build(
            left_path="alpha.txt",
            right_path="alpha.txt",
            left_label="HEAD",
            right_label="worktree",
            renderer=TextDiffEngine(),
        ),
    )
    git_composed = Composer().compose(
        head_content,
        worktree_content,
        ComposeContext.build(
            left_path="alpha.txt",
            right_path="alpha.txt",
            left_label="HEAD",
            right_label="worktree",
            renderer=GitDiffEngine(),
        ),
    )
    reversed_composed = Composer().compose(
        worktree_content,
        head_content,
        ComposeContext.build(
            left_path="alpha.txt",
            right_path="alpha.txt",
            left_label="worktree",
            right_label="HEAD",
            renderer=GitDiffEngine(),
        ),
    )

    # A flat text File composes into one text bay, and that bay holds the
    # rendered rows each engine produced.
    (rich_frame,) = rich_composed["frames"]
    (rich_bay,) = rich_frame["bays"]
    rich_kind = rich_bay["kind_data"]
    assert rich_kind["kind"] == "text"
    (git_frame,) = git_composed["frames"]
    (git_bay,) = git_frame["bays"]
    git_kind = git_bay["kind_data"]
    assert git_kind["kind"] == "text"
    (reversed_frame,) = reversed_composed["frames"]
    (reversed_bay,) = reversed_frame["bays"]
    reversed_kind = reversed_bay["kind_data"]
    assert reversed_kind["kind"] == "text"

    assert [row["status"] for row in rich_kind["rows"]] == [
        "equal",
        "replace",
        "equal",
    ]
    assert [row["status"] for row in git_kind["rows"]] == [
        "equal",
        "delete",
        "insert",
        "equal",
    ]
    assert git_composed["summary"]["modified_lines"] == 0
    assert git_composed["summary"]["removed_lines"] == 1
    assert git_composed["summary"]["added_lines"] == 1
    assert [row["status"] for row in reversed_kind["rows"]] == [
        "equal",
        "delete",
        "insert",
        "equal",
    ]
    assert reversed_kind["rows"][1]["left_text"] == "two changed"
    assert reversed_kind["rows"][2]["right_text"] == "two"


def test_build_repo_manifest_summarizes_changed_files(
    tmp_path: Path,
) -> None:
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
    (tmp_path / "alpha.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    (tmp_path / "alpha.txt").write_text("one changed\n", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("two changed\n", encoding="utf-8")

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="index", right="worktree")

    assert manifest["summary"]["changed_files"] == 2
    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "alpha.txt",
            "entry": {
                "file_kind": {"type": "git", "status": "modified"},
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
                "lazy": None,
            },
        },
        {
            "type": "file",
            "name": "beta.txt",
            "entry": {
                "file_kind": {"type": "git", "status": "modified"},
                "left_path": "beta.txt",
                "right_path": "beta.txt",
                "lazy": None,
            },
        },
    ]


def test_build_repo_manifest_marks_lockfiles_lazy(tmp_path: Path) -> None:
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
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text("version = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "Cargo.lock"],
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
    lockfile.write_text("version = 2\n", encoding="utf-8")

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="index", right="worktree")

    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "Cargo.lock",
            "entry": {
                "lazy": "generated",
                "left_path": "Cargo.lock",
                "right_path": "Cargo.lock",
                "file_kind": {"type": "git", "status": "modified"},
            },
        }
    ]
    assert manifest["summary"]["changed_files"] == 1


def test_build_repo_manifest_marks_large_changed_files_lazy(
    tmp_path: Path,
) -> None:
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
    large_file = tmp_path / "large.txt"
    large_file.write_text("old\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "large.txt"],
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
    large_file.write_text(
        "".join(f"line {index}\n" for index in range(5001)),
        encoding="utf-8",
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="index", right="worktree")

    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "large.txt",
            "entry": {
                "lazy": "too_big",
                "left_path": "large.txt",
                "right_path": "large.txt",
                "file_kind": {"type": "git", "status": "modified"},
            },
        }
    ]
    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["added_lines"] == 5001
    assert manifest["summary"]["removed_lines"] == 1


def test_build_repo_manifest_marks_deleted_files_lazy(tmp_path: Path) -> None:
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
    deleted_file = tmp_path / "alpha.txt"
    deleted_file.write_text("one\n", encoding="utf-8")
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
    deleted_file.unlink()

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="index", right="worktree")

    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "alpha.txt",
            "entry": {
                "lazy": "deleted",
                "left_path": "alpha.txt",
                "right_path": None,
                "file_kind": {"type": "git", "status": "deleted"},
            },
        }
    ]
    assert manifest["summary"]["changed_files"] == 1


def test_build_repo_manifest_marks_pure_renames_lazy(tmp_path: Path) -> None:
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
    source_file = tmp_path / "alpha.txt"
    source_file.write_text("one\n", encoding="utf-8")
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
        ["git", "mv", "alpha.txt", "beta.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="HEAD", right="worktree")

    assert manifest["tree"] == [
        {
            "type": "file",
            "name": "beta.txt",
            "entry": {
                "lazy": "pure_renamed",
                "left_path": "alpha.txt",
                "right_path": "beta.txt",
                "file_kind": {"type": "git", "status": "renamed"},
            },
        }
    ]
    assert manifest["summary"]["changed_files"] == 1
