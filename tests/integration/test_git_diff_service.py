import subprocess
from pathlib import Path

from helpers import TextDiffService

from dirdiff.change_source import GitChangeSource, GitRepository
from dirdiff.engines import TextDiffEngine
from dirdiff.formats import ComposeContext, Composer
from dirdiff.util import Nothing, Ok, Some


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

    backend = GitRepository.discover(cwd=tmp_path)
    source = GitChangeSource(
        tmp_path,
        left=backend.commit_id("HEAD", allow_unborn=True),
        right="worktree",
        show_untracked=True,
        max_link_target_bytes=1024 * 1024,
    )
    (change,) = source.changes().files
    assert not Some.present(change.left) and Some.present(change.right)
    loaded = source.load((change,))[0].right
    assert Some.present(loaded)
    assert Ok.good(loaded.value.content)
    composed = Composer().compose(
        Nothing(),
        Some(loaded.value.content.value),
        ComposeContext.build(
            left_path=Nothing(),
            right_path=Some("beta.txt"),
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

    service = TextDiffService(GitRepository.discover(cwd=tmp_path))
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
                "file_kind": {"type": "tracked", "status": "modified"},
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
                "lazy": None,
            },
        }
    ]
    assert manifest["summary"]["changed_files"] == 1
    assert manifest["summary"]["added_lines"] == 1
    assert manifest["summary"]["removed_lines"] == 0
