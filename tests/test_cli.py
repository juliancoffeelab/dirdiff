from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dirdiff.cli import (
    RUNTIME_CONFIG_ENV,
    RuntimeConfig,
    choose_port_pair,
    ensure_port_available,
    load_runtime_config,
    store_runtime_config,
)
from dirdiff.repo_registry import RepoMarkRecord, RepoMarkStoreProtocol
from dirdiff.server import TextFileDiffResponse, create_app

TEXT_SUMMARY = {
    "changed_lines": 1,
    "modified_lines": 1,
    "added_lines": 0,
    "removed_lines": 0,
    "left_exists": True,
    "right_exists": True,
}


class FakeRepoMarkStore(RepoMarkStoreProtocol):
    def list(self) -> tuple[RepoMarkRecord, ...]:
        return (
            RepoMarkRecord(
                id=1,
                path="/tmp/repo",
                name="repo",
                marked_at="2026-01-01T00:00:00+00:00",
            ),
        )

    def get(self, repo_id: int) -> RepoMarkRecord | None:
        if repo_id == 1:
            return self.list()[0]
        return None


def test_ensure_port_available_rejects_busy_port() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    requested_port = occupied.getsockname()[1]

    try:
        try:
            ensure_port_available(requested_port, label="Backend")
        except SystemExit as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected occupied port to be rejected")
    finally:
        occupied.close()

    assert f"Backend port {requested_port} is already in use." in message


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


def test_runtime_config_round_trips_through_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNTIME_CONFIG_ENV, raising=False)
    config = RuntimeConfig(
        db_path="/tmp/dirdiff.sqlite",
        left="HEAD~1",
        right="HEAD",
        base_branch="origin/main",
        review_branch="origin/feature",
    )

    store_runtime_config(config)

    assert load_runtime_config() == config


def test_root_explains_vite_frontend_is_required() -> None:
    client = TestClient(create_app(FakeRepoMarkStore()))

    response = client.get("/")

    assert response.status_code == 503
    assert "Vite frontend is not running" in response.text
    assert "--no-frontend-dev" in response.text


def test_fastapi_docs_are_enabled() -> None:
    client = TestClient(create_app(FakeRepoMarkStore()))

    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_openapi_exposes_diff_models() -> None:
    client = TestClient(create_app(FakeRepoMarkStore()))

    response = client.get("/openapi.json")
    spec = response.json()

    assert response.status_code == 200
    assert "/api/diff" in spec["paths"]
    assert "/api/file-diff" in spec["paths"]
    assert "TextFileDiffResponse" in spec["components"]["schemas"]
    assert "NotebookSectionDiffResponse" in spec["components"]["schemas"]
    diff_params = spec["paths"]["/api/diff"]["get"]["parameters"]
    required_names = {
        param["name"] for param in diff_params if param["required"]
    }
    assert required_names >= {
        "repo_id",
        "engine",
        "mode",
        "left",
        "right",
    }


def test_file_diff_response_schema_rejects_unknown_fields() -> None:
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
