import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from dirdiff.db.base import open_sqlite_engine
from dirdiff.db.repo_registry import RepoMarkStore
from dirdiff.db.user_profile import UserProfileStore
from dirdiff.server import create_app


def create_repo_client(repo_path: Path) -> tuple[TestClient, int]:
    engine = open_sqlite_engine(repo_path / ".dirdiff-test.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    mark = repo_marks.new_mark(path=repo_path, name=repo_path.name)
    return TestClient(create_app(repo_marks, user_profile)), mark.id


def test_file_diff_endpoint_returns_full_generated_file_rows(
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

    client, repo_id = create_repo_client(tmp_path)

    repo_response = client.get(
        "/api/manifest",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "files",
            "left": "index",
            "right": "worktree",
        },
    )
    repo_entry = repo_response.json()["files"][0]
    assert repo_entry == {
        "lazy": "generated",
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "file_kind": {"type": "git", "status": "modified"},
    }
    lazy_info_response = client.get(
        "/api/lazy-info",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "files",
            "left": "index",
            "right": "worktree",
        },
    )
    lazy_info = lazy_info_response.json()["files"][0]
    assert lazy_info_response.status_code == 200
    assert lazy_info == {
        "lazy": "generated",
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "display_name": "Cargo.lock",
        "changed_lines": 2,
        "added_lines": 1,
        "removed_lines": 1,
        "file_kind": {"type": "git", "status": "modified"},
    }

    response = client.get(
        "/api/file-diff",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "files",
            "left": "index",
            "right": "worktree",
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
            "change_type": "modify",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["display_name"] == "Cargo.lock"
    assert payload.get("lazy") is None
    assert payload["file_kind"] == {"type": "git", "status": "modified"}
    assert payload["rows"]


def test_repo_manifest_endpoint_returns_minimal_deleted_file_entry(
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

    client, repo_id = create_repo_client(tmp_path)

    response = client.get(
        "/api/manifest",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "files",
            "left": "index",
            "right": "worktree",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["files"][0] == {
        "lazy": "deleted",
        "left_path": "alpha.txt",
        "right_path": None,
        "file_kind": {"type": "git", "status": "deleted"},
    }
