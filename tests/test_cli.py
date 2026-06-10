from __future__ import annotations

import json
import socket
import subprocess
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
from dirdiff.diff import TextDiffService
from dirdiff.server import create_app


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

    service = TextDiffService.discover(cwd=tmp_path)
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
    store_runtime_config(
        RuntimeConfig(
            repo_root=str(tmp_path),
        )
    )

    client = TestClient(create_app_from_runtime_config())
    response = client.get("/api/diff-stream")
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert events[0][0] == "init"
    assert events[0][1]["mode"] == "repo"


def test_build_defaults_keeps_review_branch_selected_even_on_master(
    tmp_path: Path,
) -> None:
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
    subprocess.run(["git", "checkout", "master"], cwd=tmp_path, check=True, capture_output=True)

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)

    assert defaults["mode"] == "files"
    assert defaults["base_branch"] == "master"
    assert defaults["review_branch"] == "feature"
    assert defaults["ref_choices"]["locals"] == ["feature", "master"]


def test_build_defaults_prefers_remote_qualified_branch_review_refs(
    tmp_path: Path,
) -> None:
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
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/master", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)

    assert defaults["base_branch"] == "origin/master"
    assert defaults["review_branch"] == "origin/feature"


def test_diff_stream_endpoint_emits_progress_events(tmp_path: Path) -> None:
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
    tracked_file.write_text("one changed\n", encoding="utf-8")

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)
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


def test_diff_stream_endpoint_streams_compare_refs_mode(tmp_path: Path) -> None:
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
    tracked_file.write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "second"], cwd=tmp_path, check=True, capture_output=True)

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)
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

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_openapi_exposes_diff_models(tmp_path: Path) -> None:
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

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.get("/openapi.json")
    spec = response.json()

    assert response.status_code == 200
    assert "/api/diff-stream" in spec["paths"]
    assert "/api/file-diff" in spec["paths"]
    assert "TextFileDiffResponse" in spec["components"]["schemas"]
    assert "NotebookSectionDiffResponse" in spec["components"]["schemas"]
    diff_params = spec["paths"]["/api/diff-stream"]["get"]["parameters"]
    assert next(param for param in diff_params if param["name"] == "mode")["schema"]["default"] == "files"
    assert next(param for param in diff_params if param["name"] == "left")["schema"]["default"] == "index"
    assert next(param for param in diff_params if param["name"] == "right")["schema"]["default"] == "worktree"


def test_save_log_endpoint_writes_to_launch_directory(tmp_path: Path) -> None:
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

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.post("/api/save-log", json={"text": "hello log\n"})
    payload = response.json()
    saved_path = Path(payload["path"])

    assert response.status_code == 200
    assert saved_path.parent == tmp_path.resolve()
    assert saved_path.read_text(encoding="utf-8") == "hello log\n"


def test_file_diff_endpoint_returns_full_generated_file_rows(tmp_path: Path) -> None:
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
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "Cargo.lock"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    lockfile.write_text("version = 2\n", encoding="utf-8")

    service = TextDiffService.discover(cwd=tmp_path)
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


def test_repo_diff_endpoint_emits_minimal_generated_lockfile_entry(tmp_path: Path) -> None:
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
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "Cargo.lock"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    lockfile.write_text("version = 2\n", encoding="utf-8")

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.get("/api/diff-stream")
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert events[1][1]["entry"] == {
        "lazy": True,
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "change_type": "modify",
    }


def test_repo_diff_stream_emits_minimal_deleted_file_entry(tmp_path: Path) -> None:
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
    deleted_file = tmp_path / "alpha.txt"
    deleted_file.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "alpha.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    deleted_file.unlink()

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(service)
    client = TestClient(create_app(service, defaults))

    response = client.get("/api/diff-stream")
    events = parse_sse_events(response.text)

    assert response.status_code == 200
    assert events[1][1]["entry"] == {
        "lazy": True,
        "left_path": "alpha.txt",
        "right_path": None,
        "change_type": "delete",
    }
