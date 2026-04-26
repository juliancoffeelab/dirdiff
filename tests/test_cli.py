from __future__ import annotations

import socket

from dirdiff.cli import create_server
from dirdiff.diff import TextDiffService


def test_create_server_uses_next_port_when_requested_port_is_busy() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    requested_port = occupied.getsockname()[1]

    service = TextDiffService(repo_root=None)
    defaults = {
        "path": "",
        "left": "index",
        "right": "worktree",
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
