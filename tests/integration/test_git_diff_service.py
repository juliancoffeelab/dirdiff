import json
import subprocess
from pathlib import Path

from dirdiff.services import GitDiffService, TextDiffService
from dirdiff.sources import GitBackend


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

    assert manifest["mode"] == "repo"
    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["changed_lines"] == 1
    assert manifest["summary"]["removed_lines"] == 0
    assert manifest["files"] == [
        {
            "file_kind": {"type": "git", "status": "modified"},
            "left_path": "alpha.txt",
            "right_path": "alpha.txt",
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
        left="head",
        right="worktree",
        show_untracked=True,
    )

    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["added_files"] == 1
    assert manifest["summary"]["changed_lines"] == 0
    assert manifest["files"] == [
        {
            "file_kind": {"type": "untracked"},
            "left_path": None,
            "right_path": "beta.txt",
            "lazy": "untracked",
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

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    payload = service.build_git_diff_paths(
        left_path=None,
        right_path="beta.txt",
        left="head",
        right="worktree",
        change_type="add",
        file_kind="untracked",
    )

    assert payload["file_kind"] == {"type": "untracked"}
    assert payload["summary"]["added_lines"] == 1
    assert payload["rows"][0]["status"] == "insert"
    assert payload["rows"][0]["right_text"] == "new file"


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

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    merge_base, normalized_branch = service.resolve_branch_diff_sides(
        base_branch="master",
        branch="feature",
    )
    manifest = service.build_repo_manifest(
        left=merge_base,
        right=normalized_branch,
    )

    assert manifest["mode"] == "repo"
    assert manifest["left_label"] == merge_base
    assert manifest["right_label"] == "feature"
    assert manifest["files"] == [
        {
            "file_kind": {"type": "git", "status": "modified"},
            "left_path": "alpha.txt",
            "right_path": "alpha.txt",
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
    rich_service = TextDiffService(repo)
    git_service = GitDiffService(repo)

    rich_diff = rich_service.build_git_diff_paths(
        left_path="alpha.txt",
        right_path="alpha.txt",
        left="head",
        right="worktree",
    )
    git_diff = git_service.build_git_diff_paths(
        left_path="alpha.txt",
        right_path="alpha.txt",
        left="head",
        right="worktree",
    )
    reversed_git_diff = git_service.build_git_diff_paths(
        left_path="alpha.txt",
        right_path="alpha.txt",
        left="worktree",
        right="head",
    )

    assert [row["status"] for row in rich_diff["rows"]] == [
        "equal",
        "replace",
        "equal",
    ]
    assert [row["status"] for row in git_diff["rows"]] == [
        "equal",
        "delete",
        "insert",
        "equal",
    ]
    assert git_diff["summary"]["modified_lines"] == 0
    assert git_diff["summary"]["removed_lines"] == 1
    assert git_diff["summary"]["added_lines"] == 1
    assert [row["status"] for row in reversed_git_diff["rows"]] == [
        "equal",
        "delete",
        "insert",
        "equal",
    ]
    assert reversed_git_diff["rows"][1]["left_text"] == "two changed"
    assert reversed_git_diff["rows"][2]["right_text"] == "two"


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
    assert manifest["summary"]["changed_lines"] == 2
    assert {entry["right_path"] for entry in manifest["files"]} == {
        "alpha.txt",
        "beta.txt",
    }


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

    assert len(manifest["files"]) == 1
    entry = manifest["files"][0]
    assert entry == {
        "lazy": "generated",
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "file_kind": {"type": "git", "status": "modified"},
    }
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
        "".join(f"line {index}\n" for index in range(1001)),
        encoding="utf-8",
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    manifest = service.build_repo_manifest(left="index", right="worktree")

    assert len(manifest["files"]) == 1
    entry = manifest["files"][0]
    assert entry["lazy"] == "too_big"
    assert entry["left_path"] == "large.txt"
    assert entry["right_path"] == "large.txt"
    assert entry["file_kind"] == {"type": "git", "status": "modified"}
    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["changed_lines"] == 1001
    assert manifest["summary"]["modified_lines"] == 1
    assert manifest["summary"]["added_lines"] == 1000
    assert manifest["summary"]["removed_lines"] == 0


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

    assert len(manifest["files"]) == 1
    entry = manifest["files"][0]
    assert entry == {
        "lazy": "deleted",
        "left_path": "alpha.txt",
        "right_path": None,
        "file_kind": {"type": "git", "status": "deleted"},
    }
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

    manifest = service.build_repo_manifest(left="head", right="worktree")

    assert len(manifest["files"]) == 1
    entry = manifest["files"][0]
    assert entry == {
        "lazy": "pure_renamed",
        "left_path": "alpha.txt",
        "right_path": "beta.txt",
        "file_kind": {"type": "git", "status": "renamed"},
    }
    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["changed_lines"] == 0


def test_repo_diff_uses_lazy_entries_for_notebooks(tmp_path: Path) -> None:
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
    notebook = tmp_path / "demo.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "markdown",
                        "id": "intro",
                        "metadata": {},
                        "source": ["# Title\n"],
                    }
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "demo.ipynb"],
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
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "markdown",
                        "id": "intro",
                        "metadata": {},
                        "source": ["# Title\n\nUpdated body\n"],
                    }
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    notebook_diff = service.build_git_diff_paths(
        left_path="demo.ipynb",
        right_path="demo.ipynb",
        left="index",
        right="worktree",
        display_name="demo.ipynb",
        change_type="modify",
    )

    assert notebook_diff["render_kind"] == "notebook"
    assert notebook_diff["display_name"] == "demo.ipynb"
    assert notebook_diff["summary"]["changed_cells"] == 1
    assert notebook_diff["summary"]["modified_cells"] == 1
    assert notebook_diff["summary"]["added_cells"] == 0
    assert notebook_diff["summary"]["removed_cells"] == 0
    assert notebook_diff["cells"][0]["cell_type"] == "markdown"
    assert any(
        row["right_text"] == "Updated body"
        for row in notebook_diff["cells"][0]["source_rows"]
    )


def test_build_notebook_section_diff_loads_lazy_sections_on_demand(
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

    notebook = tmp_path / "demo.ipynb"
    notebook.write_text(
        json.dumps(
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
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "demo.ipynb"],
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

    notebook.write_text(
        json.dumps(
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
        ),
        encoding="utf-8",
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    notebook_diff = service.build_git_diff_paths(
        left_path="demo.ipynb",
        right_path="demo.ipynb",
        left="index",
        right="worktree",
        display_name="demo.ipynb",
        change_type="modify",
    )
    cell_key = notebook_diff["cells"][0]["cell_key"]

    assert notebook_diff["notebook_metadata_rows"] == []
    assert notebook_diff["cells"][0]["metadata_rows"] == []
    assert notebook_diff["cells"][0]["outputs_rows"] == []

    notebook_metadata = service.build_notebook_section_diff(
        left_path="demo.ipynb",
        right_path="demo.ipynb",
        left="index",
        right="worktree",
        section="notebook-metadata",
    )
    cell_metadata = service.build_notebook_section_diff(
        left_path="demo.ipynb",
        right_path="demo.ipynb",
        left="index",
        right="worktree",
        section="cell-metadata",
        cell_key=cell_key,
    )
    cell_outputs = service.build_notebook_section_diff(
        left_path="demo.ipynb",
        right_path="demo.ipynb",
        left="index",
        right="worktree",
        section="cell-outputs",
        cell_key=cell_key,
    )

    assert any(
        "language_info" in (row.get("right_text") or "")
        for row in notebook_metadata["rows"]
    )
    assert any(
        '"collapsed": true' in (row.get("right_text") or "")
        for row in cell_metadata["rows"]
    )
    assert any(
        '"output_type": "stream"' in (row.get("right_text") or "")
        for row in cell_outputs["rows"]
    )
