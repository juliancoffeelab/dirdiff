from __future__ import annotations

import json
import pytest
import socket
import subprocess
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient

from dirdiff.cli import (
    RUNTIME_CONFIG_ENV,
    RuntimeConfig,
    build_defaults,
    choose_port,
    create_app_from_runtime_config,
    load_runtime_config,
    store_runtime_config,
)
from dirdiff.diff import GitRepository, RepoDiffPath, TextDiffService, TextVersion
from dirdiff.server import create_app


SUMMARY = {
    "changed_files": 1,
    "added_files": 0,
    "removed_files": 0,
    "updated_files": 1,
    "changed_lines": 1,
    "modified_lines": 1,
    "added_lines": 0,
    "removed_lines": 0,
    "skipped_files": 0,
}


TEXT_SUMMARY = {
    "changed_lines": 1,
    "modified_lines": 1,
    "added_lines": 0,
    "removed_lines": 0,
    "left_exists": True,
    "right_exists": True,
}


def default_bootstrap() -> dict:
    return {
        "engine": "dirdiff",
        "mode": "files",
        "left": "index",
        "right": "worktree",
        "base_branch": "master",
        "review_branch": "feature",
        "ref_choices": {
            "builtins": ["head", "index", "worktree"],
            "locals": ["feature", "master"],
            "remotes": [],
            "remote_names": [],
        },
        "repo_available": True,
    }


class FakeDiffService:
    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = cwd or Path.cwd()
        self.repo_root = self.cwd

    def default_base_branch(self) -> str:
        return "master"

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        return "feature"

    def default_remote_name(self) -> str | None:
        return None

    def branch_upstream_name(self, branch: str | None) -> str | None:
        return None

    def list_ref_choices(self) -> dict[str, list[str]]:
        return {
            "builtins": ["head", "index", "worktree"],
            "locals": ["feature", "master"],
            "remotes": [],
            "remote_names": [],
        }

    def normalize_side(self, side: str) -> str:
        return side

    def iter_repo_diff_progress(self, *, left: str, right: str):
        yield SimpleNamespace(
            entry={
                "display_name": "alpha.txt",
                "mode": "git",
                "left_label": left,
                "right_label": right,
                "summary": TEXT_SUMMARY,
                "rows": [],
                "change_type": "modify",
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
            },
            summary=SUMMARY,
        )

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str | None = None,
    ) -> dict:
        return {
            "display_name": display_name or left_path or right_path or "alpha.txt",
            "mode": "git",
            "left_label": left,
            "right_label": right,
            "summary": TEXT_SUMMARY,
            "rows": [
                {
                    "status": "replace",
                    "left_no": 1,
                    "right_no": 1,
                    "left_text": "one",
                    "right_text": "two",
                }
            ],
            "change_type": change_type,
            "left_path": left_path,
            "right_path": right_path,
            "lazy": False,
            "fold_hints": [],
        }


class FakeGitRepository(FakeDiffService):
    def list_repo_diff_paths(self, *, left: str, right: str) -> list[RepoDiffPath]:
        return [
            RepoDiffPath(
                left_path="alpha.txt",
                right_path="alpha.txt",
                display_name="alpha.txt",
                change_type="modify",
            )
        ]

    def normalize_repo_path(self, raw_path: str) -> str:
        return raw_path

    def load_git_version(self, path: str, side: str) -> TextVersion:
        text = "one\n" if side == "index" else "two\n"
        return TextVersion(label=side, exists=True, text=text)


class FakeEngineService(FakeDiffService):
    def __init__(self, cwd: Path | None = None, *, row_status: str) -> None:
        super().__init__(cwd)
        self.row_status = row_status

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str | None = None,
    ) -> dict:
        payload = super().build_git_diff_paths(
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
            display_name=display_name,
            change_type=change_type,
        )
        payload["rows"][0]["status"] = self.row_status
        return payload


def parse_sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name: str | None = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
            continue
        if line.startswith("data: ") and event_name is not None:
            events.append((event_name, json.loads(line.removeprefix("data: "))))
            event_name = None
    return events


def test_choose_port_uses_next_port_when_requested_port_is_busy() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    requested_port = occupied.getsockname()[1]

    try:
        actual_port = choose_port(requested_port)
    finally:
        occupied.close()

    assert actual_port > requested_port


def test_runtime_config_round_trips_through_environment(monkeypatch) -> None:
    monkeypatch.delenv(RUNTIME_CONFIG_ENV, raising=False)
    config = RuntimeConfig(
        left="HEAD~1",
        right="HEAD",
        base_branch="origin/main",
        review_branch="origin/feature",
        repo_root="/tmp/repo",
    )

    store_runtime_config(config)

    assert load_runtime_config() == config


def test_build_defaults_keeps_branch_review_available_without_defaulting_to_it(
    tmp_path: Path,
) -> None:
    service = FakeDiffService(tmp_path)
    defaults = build_defaults(service)

    assert defaults["mode"] == "files"
    assert defaults["base_branch"] == "master"
    assert defaults["review_branch"] == "feature"
    assert defaults["ref_choices"]["locals"] == ["feature", "master"]
    assert defaults["ref_choices"]["builtins"] == ["head", "index", "worktree"]
    assert defaults["ref_choices"]["remotes"] == []
    assert defaults["ref_choices"]["remote_names"] == []


def test_create_app_from_runtime_config_uses_stored_repo_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered_repo_root: Path | None = None

    def discover(
        *, repo_root: Path | None = None, cwd: Path | None = None
    ) -> FakeGitRepository:
        nonlocal discovered_repo_root
        discovered_repo_root = repo_root
        return FakeGitRepository(repo_root or cwd or tmp_path)

    monkeypatch.setattr(GitRepository, "discover", staticmethod(discover))
    store_runtime_config(
        RuntimeConfig(
            repo_root=str(tmp_path),
        )
    )

    client = TestClient(create_app_from_runtime_config())
    response = client.get("/api/diff-stream")
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert discovered_repo_root == tmp_path
    assert events[0][0] == "init"
    assert events[0][1]["mode"] == "repo"


def test_defaults_endpoint_returns_frontend_bootstrap_state(
    tmp_path: Path,
) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))

    response = client.get("/api/defaults")

    assert response.status_code == 200
    assert response.json() == defaults


def test_build_defaults_keeps_review_branch_selected_even_on_master(
    tmp_path: Path,
) -> None:
    service = FakeDiffService(tmp_path)
    defaults = build_defaults(service)

    assert defaults["mode"] == "files"
    assert defaults["base_branch"] == "master"
    assert defaults["review_branch"] == "feature"
    assert defaults["ref_choices"]["locals"] == ["feature", "master"]


def test_build_defaults_prefers_remote_qualified_branch_review_refs(
    tmp_path: Path,
) -> None:
    class RemoteFakeDiffService(FakeDiffService):
        def default_remote_name(self) -> str | None:
            return "origin"

        def branch_upstream_name(self, branch: str | None) -> str | None:
            return f"origin/{branch}" if branch else None

    service = RemoteFakeDiffService(tmp_path)
    defaults = build_defaults(service)

    assert defaults["base_branch"] == "origin/master"
    assert defaults["review_branch"] == "origin/feature"


def test_diff_stream_endpoint_emits_progress_events(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))
    response = client.get(
        "/api/diff-stream",
        params={"mode": "files", "left": "index", "right": "worktree"},
    )
    events = parse_sse_events(response.text)

    assert events[0][0] == "init"
    assert events[1][0] == "file"
    assert events[1][1]["entry"]["display_name"] == "alpha.txt"
    assert events[2][0] == "done"
    assert events[2][1]["summary"] == events[1][1]["summary"]
    assert events[2][1]["summary"]["changed_files"] == 1


def test_diff_stream_endpoint_streams_compare_refs_mode(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))
    response = client.get(
        "/api/diff-stream",
        params={"mode": "refs", "left": "HEAD~1", "right": "HEAD"},
    )
    events = parse_sse_events(response.text)

    assert events[0] == (
        "init",
        {
            "display_name": "Repository diff",
            "mode": "repo",
            "left_label": "HEAD~1",
            "right_label": "HEAD",
            "summary": {
                "changed_files": 0,
                "added_files": 0,
                "removed_files": 0,
                "updated_files": 0,
                "changed_lines": 0,
                "modified_lines": 0,
                "added_lines": 0,
                "removed_lines": 0,
                "skipped_files": 0,
            },
        },
    )
    assert events[1][0] == "file"
    assert events[1][1]["entry"]["display_name"] == "alpha.txt"
    assert events[2][0] == "done"


def test_fastapi_docs_are_enabled(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))

    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_openapi_exposes_diff_models(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))

    response = client.get("/openapi.json")
    spec = response.json()

    assert response.status_code == 200
    assert "/api/diff-stream" in spec["paths"]
    assert "/api/file-diff" in spec["paths"]
    assert "TextFileDiffResponse" in spec["components"]["schemas"]
    assert "NotebookSectionDiffResponse" in spec["components"]["schemas"]
    diff_params = spec["paths"]["/api/diff-stream"]["get"]["parameters"]
    assert (
        next(param for param in diff_params if param["name"] == "mode")["schema"][
            "default"
        ]
        == "files"
    )
    assert (
        next(param for param in diff_params if param["name"] == "left")["schema"][
            "default"
        ]
        == "index"
    )
    assert (
        next(param for param in diff_params if param["name"] == "right")["schema"][
            "default"
        ]
        == "worktree"
    )


def test_save_log_endpoint_writes_to_launch_directory(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))

    response = client.post("/api/save-log", json={"text": "hello log\n"})
    payload = response.json()
    saved_path = Path(payload["path"])

    assert response.status_code == 200
    assert saved_path.parent == tmp_path.resolve()
    assert saved_path.read_text(encoding="utf-8") == "hello log\n"


def test_file_diff_endpoint_routes_to_requested_engine(tmp_path: Path) -> None:
    service = FakeEngineService(tmp_path, row_status="replace")
    git_service = FakeEngineService(tmp_path, row_status="delete")
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults, services={"git": git_service}))

    response = client.get(
        "/api/file-diff",
        params={
            "engine": "git",
            "left": "head",
            "right": "worktree",
            "left_path": "alpha.txt",
            "right_path": "alpha.txt",
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"][0]["status"] == "delete"


def test_numstat_parser_reads_changed_rename_records(tmp_path: Path) -> None:
    repository = GitRepository(tmp_path)

    counts = repository._parse_numstat_output(
        b"2\t1\t\0old/name.txt\0new/name.txt\0"
    )

    assert counts == {"new/name.txt": (2, 1)}


@pytest.mark.git
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

    service = TextDiffService(GitRepository.discover(cwd=tmp_path))
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    repo_response = client.get("/api/diff-stream")
    repo_events = parse_sse_events(repo_response.text)
    repo_entry = repo_events[1][1]["entry"]
    assert repo_entry == {
        "lazy": True,
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "change_type": "modify",
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
    assert payload.get("lazy") is False
    assert payload["rows"]


@pytest.mark.git
def test_repo_diff_endpoint_emits_minimal_generated_lockfile_entry(
    tmp_path: Path,
) -> None:
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

    service = TextDiffService(GitRepository.discover(cwd=tmp_path))
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.get("/api/diff-stream")
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert events[1][1]["entry"] == {
        "lazy": True,
        "display_name": "Cargo.lock",
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "change_type": "modify",
        "changed_lines": 2,
        "added_lines": 1,
        "removed_lines": 1,
    }


@pytest.mark.git
def test_repo_diff_stream_emits_minimal_deleted_file_entry(tmp_path: Path) -> None:
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

    service = TextDiffService(GitRepository.discover(cwd=tmp_path))
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.get("/api/diff-stream")
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert events[1][1]["entry"] == {
        "lazy": True,
        "display_name": "alpha.txt",
        "left_path": "alpha.txt",
        "right_path": None,
        "change_type": "delete",
        "changed_lines": 1,
        "added_lines": 0,
        "removed_lines": 1,
    }
