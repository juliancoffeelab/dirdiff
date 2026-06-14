from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from dirdiff.cli import build_defaults
from dirdiff.diff import GitBackend, TextDiffService
from dirdiff.server import create_app


def test_file_diff_endpoint_returns_full_generated_file_rows(tmp_path: Path) -> None:
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
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text("version = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "Cargo.lock"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    lockfile.write_text("version = 2\n", encoding="utf-8")

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    repo_response = client.get("/api/diff")
    repo_entry = repo_response.json()["files"][0]
    assert repo_entry == {
        "lazy": "generated",
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "file_kind": {"type": "git", "status": "modified"},
    }

    response = client.get(
        "/api/file-diff",
        params={
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


def test_repo_diff_endpoint_returns_minimal_deleted_file_entry(tmp_path: Path) -> None:
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
    deleted_file = tmp_path / "alpha.txt"
    deleted_file.write_text("one\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    deleted_file.unlink()

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.get("/api/diff")
    payload = response.json()

    assert response.status_code == 200
    assert payload["files"][0] == {
        "lazy": "deleted",
        "left_path": "alpha.txt",
        "file_kind": {"type": "git", "status": "deleted"},
    }
