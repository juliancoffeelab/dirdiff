from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dirdiff.cli import (
    RUNTIME_CONFIG_ENV,
    RuntimeConfig,
    build_defaults,
    choose_port_pair,
    ensure_port_available,
    create_app_from_runtime_config,
    load_runtime_config,
    store_runtime_config,
)
from dirdiff.diff import GitRepository, RepoDiffPath, TextVersion
from dirdiff.server import TextFileDiffResponse, create_app


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

    def build_repo_manifest(
        self, *, left: str, right: str, show_untracked: bool = False
    ) -> dict:
        return {
            "display_name": "Repository diff",
            "mode": "repo",
            "left_label": left,
            "right_label": right,
            "summary": SUMMARY,
            "files": [
                {
                    "file_kind": {"type": "git", "status": "modified"},
                    "left_path": "alpha.txt",
                    "right_path": "alpha.txt",
                }
            ],
        }

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str | None = None,
        file_kind: str | None = None,
    ) -> dict:
        kind = (
            {"type": "untracked"}
            if file_kind == "untracked"
            else {
                "type": "git",
                "status": {
                    "add": "added",
                    "delete": "deleted",
                    "rename": "renamed",
                    "copy": "copied",
                }.get(change_type, "modified"),
            }
        )
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
            "file_kind": kind,
            "left_path": left_path,
            "right_path": right_path,
            "lazy": None,
            "fold_hints": [],
        }


class FakeGitRepository(FakeDiffService):
    def list_repo_diff_paths(
        self, *, left: str, right: str, show_untracked: bool = False
    ) -> list[RepoDiffPath]:
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
    def __init__(
        self,
        cwd: Path | None = None,
        *,
        row_status: str,
        engine_warning: dict | None = None,
    ) -> None:
        super().__init__(cwd)
        self.row_status = row_status
        self.engine_warning = engine_warning

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str | None = None,
        file_kind: str | None = None,
    ) -> dict:
        payload = super().build_git_diff_paths(
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
            display_name=display_name,
            change_type=change_type,
            file_kind=file_kind,
        )
        payload["rows"][0]["status"] = self.row_status
        if self.engine_warning is not None:
            payload["engine_warning"] = self.engine_warning
        return payload


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
    response = client.get("/api/diff")
    payload = response.json()

    assert response.status_code == 200
    assert discovered_repo_root == tmp_path
    assert payload["mode"] == "repo"


def test_defaults_endpoint_returns_frontend_bootstrap_state(
    tmp_path: Path,
) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))

    response = client.get("/api/defaults")

    assert response.status_code == 200
    assert response.json() == defaults


def test_root_explains_vite_frontend_is_required(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))

    response = client.get("/")

    assert response.status_code == 503
    assert "Vite frontend is not running" in response.text
    assert "--no-frontend-dev" in response.text


def test_build_defaults_uses_local_branch_review_refs_by_default(
    tmp_path: Path,
) -> None:
    class RemoteFakeDiffService(FakeDiffService):
        def default_remote_name(self) -> str | None:
            return "origin"

        def branch_upstream_name(self, branch: str | None) -> str | None:
            return f"origin/{branch}" if branch else None

    service = RemoteFakeDiffService(tmp_path)
    defaults = build_defaults(service)

    assert defaults["base_branch"] == "master"
    assert defaults["review_branch"] == "feature"


def test_diff_endpoint_returns_repo_manifest(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))
    response = client.get(
        "/api/diff",
        params={"mode": "files", "left": "index", "right": "worktree"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["files"][0] == {
        "file_kind": {"type": "git", "status": "modified"},
        "left_path": "alpha.txt",
        "right_path": "alpha.txt",
    }
    assert payload["summary"]["changed_files"] == 1


def test_diff_endpoint_supports_compare_refs_mode(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    defaults = default_bootstrap()
    client = TestClient(create_app(service, defaults))
    response = client.get(
        "/api/diff",
        params={"mode": "refs", "left": "HEAD~1", "right": "HEAD"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["left_label"] == "HEAD~1"
    assert payload["right_label"] == "HEAD"
    assert payload["files"][0] == {
        "file_kind": {"type": "git", "status": "modified"},
        "left_path": "alpha.txt",
        "right_path": "alpha.txt",
    }


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
    assert "/api/diff" in spec["paths"]
    assert "/api/file-diff" in spec["paths"]
    assert "TextFileDiffResponse" in spec["components"]["schemas"]
    assert "NotebookSectionDiffResponse" in spec["components"]["schemas"]
    diff_params = spec["paths"]["/api/diff"]["get"]["parameters"]
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


def test_file_diff_endpoint_routes_to_requested_engine(tmp_path: Path) -> None:
    service = FakeEngineService(tmp_path, row_status="replace")
    git_service = FakeEngineService(tmp_path, row_status="delete")
    difftastic_service = FakeEngineService(tmp_path, row_status="insert")
    defaults = default_bootstrap()
    client = TestClient(
        create_app(
            service,
            defaults,
            services={"git": git_service, "difftastic": difftastic_service},
        )
    )

    response = client.get(
        "/api/file-diff",
        params={
            "engine": "difftastic",
            "left": "head",
            "right": "worktree",
            "left_path": "alpha.txt",
            "right_path": "alpha.txt",
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"][0]["status"] == "insert"


def test_file_diff_endpoint_preserves_engine_warning(tmp_path: Path) -> None:
    service = FakeDiffService(tmp_path)
    difftastic_service = FakeEngineService(
        tmp_path,
        row_status="insert",
        engine_warning={
            "type": "difftastic_graph_limit",
            "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
        },
    )
    defaults = default_bootstrap()
    client = TestClient(
        create_app(
            service,
            defaults,
            services={"difftastic": difftastic_service},
        )
    )

    response = client.get(
        "/api/file-diff",
        params={
            "engine": "difftastic",
            "left": "head",
            "right": "worktree",
            "left_path": "alpha.txt",
            "right_path": "alpha.txt",
        },
    )

    assert response.status_code == 200
    assert response.json()["engine_warning"] == {
        "type": "difftastic_graph_limit",
        "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
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
