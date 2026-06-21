from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dirdiff.cli.server_launch import choose_port_pair, require_bindable_port
from dirdiff.db.base import open_ephemeral_engine
from dirdiff.db.repo_registry import RepoMarkStore
from dirdiff.server import TextFileDiffResponse, create_app


def repo_mark_store() -> RepoMarkStore:
    engine = open_ephemeral_engine()
    store = RepoMarkStore(engine)
    store.new_mark(path=Path("/tmp"), name="repo")
    return store


def test_require_bindable_port_rejects_busy_port() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    requested_port = occupied.getsockname()[1]

    try:
        with pytest.raises(SystemExit) as exc_info:
            require_bindable_port(requested_port, label="Backend")
    finally:
        occupied.close()

    assert f"Backend port {requested_port} is already in use." in str(
        exc_info.value
    )


def test_choose_port_pair_skips_to_fresh_backend_frontend_pair() -> None:
    occupied_backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied_frontend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied_backend.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied_frontend.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied_backend.bind(("127.0.0.1", 0))
    requested_backend_port = occupied_backend.getsockname()[1]
    occupied_frontend.bind(("127.0.0.1", 0))
    requested_frontend_port = occupied_frontend.getsockname()[1]
    occupied_backend.listen()
    occupied_frontend.listen()

    try:
        actual_backend_port, actual_frontend_port = choose_port_pair(
            requested_backend_port,
            requested_frontend_port,
        )
    finally:
        occupied_backend.close()
        occupied_frontend.close()

    assert actual_backend_port > requested_backend_port
    assert actual_frontend_port > requested_frontend_port
    assert actual_backend_port - requested_backend_port == (
        actual_frontend_port - requested_frontend_port
    )


def test_root_explains_vite_frontend_is_required() -> None:
    client = TestClient(create_app(repo_mark_store()))

    response = client.get("/")

    assert response.status_code == 503
    assert "Vite frontend is not running" in response.text
    assert "--no-frontend-dev" in response.text


def test_fastapi_docs_are_enabled() -> None:
    client = TestClient(create_app(repo_mark_store()))

    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_file_diff_response_schema_rejects_unknown_fields() -> None:
    TEXT_SUMMARY = {
        "changed_lines": 1,
        "modified_lines": 1,
        "added_lines": 0,
        "removed_lines": 0,
        "left_exists": True,
        "right_exists": True,
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TextFileDiffResponse.model_validate(
            {
                "display_name": "alpha.txt",
                "mode": "git",
                "left_label": "head",
                "right_label": "worktree",
                "summary": TEXT_SUMMARY,
                "rows": [],
                "file_kind": {"type": "git", "status": "modified"},
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
                "random_backend_surprise": True,
            }
        )
