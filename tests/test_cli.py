from __future__ import annotations

import argparse
import json
import socket
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from dirdiff.cli import build_defaults, choose_port
from dirdiff.diff import TextDiffService
from dirdiff.server import create_app


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
    defaults = build_defaults(
        service,
        argparse.Namespace(
            left="index",
            right="worktree",
            base_branch=None,
            review_branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )

    assert defaults["mode"] == "files"
    assert defaults["base_branch"] == "master"
    assert defaults["review_branch"] == "feature"
    assert defaults["ref_choices"]["locals"] == ["feature", "master"]
    assert defaults["ref_choices"]["builtins"] == ["head", "index", "worktree"]
    assert defaults["ref_choices"]["remotes"] == []
    assert defaults["ref_choices"]["remote_names"] == []


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
    defaults = build_defaults(
        service,
        argparse.Namespace(
            left="index",
            right="worktree",
            base_branch=None,
            review_branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )

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
    defaults = build_defaults(
        service,
        argparse.Namespace(
            left="index",
            right="worktree",
            base_branch=None,
            review_branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )

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
    defaults = build_defaults(
        service,
        argparse.Namespace(
            left="index",
            right="worktree",
            base_branch=None,
            review_branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )
    client = TestClient(create_app(service, defaults))
    response = client.get(
        "/api/diff-stream",
        params={"mode": "files", "left": "index", "right": "worktree"},
    )
    lines = [line for line in response.text.splitlines() if line]

    assert lines[0] == "event: init"
    assert lines[1].startswith("data: ")
    assert lines[2] == "event: file"
    assert '"display_name": "alpha.txt"' in lines[3]
    assert lines[4] == "event: done"
    assert lines[5].startswith("data: ")


def test_diff_check_blocks_large_repo_load_without_force(tmp_path: Path) -> None:
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
    for index in range(11):
        (tmp_path / f"file-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    for index in range(11):
        (tmp_path / f"file-{index}.txt").write_text(f"{index}\nchanged\n", encoding="utf-8")

    service = TextDiffService.discover(cwd=tmp_path)
    defaults = build_defaults(
        service,
        argparse.Namespace(
            left="index",
            right="worktree",
            base_branch=None,
            review_branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )
    client = TestClient(create_app(service, defaults))
    response = client.get(
        "/api/diff",
        params={"mode": "files", "left": "index", "right": "worktree", "check": "1"},
    )
    payload = response.json()
    assert response.status_code == 400

    forced_response = client.get(
        "/api/diff",
        params={
            "mode": "files",
            "left": "index",
            "right": "worktree",
            "force": "1",
            "check": "1",
        },
    )
    forced_payload = forced_response.json()

    assert payload["can_force"] is True
    assert "11 changed files" in payload["error"]
    assert forced_payload == {"ok": True}
