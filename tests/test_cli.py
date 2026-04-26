from __future__ import annotations

import argparse
import socket
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

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
        "left": "index",
        "right": "worktree",
        "base_branch": "",
        "branch": "",
        "ref_choices": {
            "builtins": [],
            "locals": [],
            "remotes": [],
        },
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
            branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )

    assert defaults["mode"] == "files"
    assert defaults["base_branch"] == "master"
    assert defaults["branch"] == "feature"
    assert defaults["ref_choices"]["locals"] == ["feature", "master"]
    assert defaults["ref_choices"]["builtins"] == ["head", "index", "worktree"]
    assert defaults["ref_choices"]["remotes"] == []


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
            branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )

    assert defaults["mode"] == "files"
    assert defaults["base_branch"] == "master"
    assert defaults["branch"] == "feature"
    assert defaults["ref_choices"]["locals"] == ["feature", "master"]


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
            branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )
    server = create_server(0, service, defaults)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/diff-stream?mode=files&left=index&right=worktree") as response:
            lines = []
            while len(lines) < 6:
                raw_line = response.readline()
                assert raw_line
                line = raw_line.decode("utf-8").strip()
                if line:
                    lines.append(line)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

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
            branch=None,
            repo_root=None,
            port=5052,
            no_open_browser=False,
            headless=False,
        ),
    )
    server = create_server(0, service, defaults)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        try:
            urlopen(f"http://127.0.0.1:{port}/api/diff?mode=files&left=index&right=worktree&check=1")
        except HTTPError as exc:
            payload = exc.read().decode("utf-8")
            assert exc.code == 400
        else:
            raise AssertionError("expected large diff check to be rejected")

        with urlopen(f"http://127.0.0.1:{port}/api/diff?mode=files&left=index&right=worktree&force=1&check=1") as response:
            forced_payload = response.read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert '"can_force": true' in payload
    assert "11 changed files" in payload
    assert '"ok": true' in forced_payload
