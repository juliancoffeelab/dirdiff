from __future__ import annotations

import argparse
import socket
import subprocess
from pathlib import Path

from dirdiff.cli import build_defaults, create_server
from dirdiff.diff import TextDiffService


def test_create_server_uses_next_port_when_requested_port_is_busy() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    requested_port = occupied.getsockname()[1]

    service = TextDiffService(repo_root=None)
    defaults = {
        "mode": "files",
        "path": "",
        "left": "index",
        "right": "worktree",
        "base_branch": "",
        "branch": "",
        "branch_choices": [],
        "left_file": "",
        "right_file": "",
        "repo_available": False,
    }

    try:
        server = create_server(requested_port, service, defaults)
    finally:
        occupied.close()

    try:
        assert server.server_address[1] > requested_port
    finally:
        server.server_close()


def test_build_defaults_prefers_branch_review_with_detected_branches(
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
            path=None,
            left="index",
            right="worktree",
            base_branch=None,
            branch=None,
            left_file=None,
            right_file=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )

    assert defaults["mode"] == "branch-review"
    assert defaults["base_branch"] == "master"
    assert defaults["branch"] == "feature"
    assert defaults["branch_choices"] == ["feature", "master"]
