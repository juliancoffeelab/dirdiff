"""CLI and local-app startup tests.

These tests cover the command-layer contract around port selection, root-page
diagnostic behavior, OpenAPI availability, and request validation for the local
FastAPI app.  They use ephemeral stores and TestClient; they do not launch the
Vite frontend or exercise browser workflows.

Port-selection helpers are CLI implementation details, so these tests import
them from the implementation module instead of making them public `dirdiff.cli`
API.
"""

from __future__ import annotations

import json
import socket
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dirdiff.cli.server_launch import choose_port_pair, require_bindable_port
from dirdiff.db import (
    RepoMarkStore,
    RoomStore,
    open_ephemeral_engine,
    open_sqlite_engine,
)
from dirdiff.engines import engine
from dirdiff.formats import ComposeContext, Composer
from dirdiff.room_lord import RoomLord
from dirdiff.server import (
    RUNTIME_CONFIG_ENV,
    ComposedDiffResponse,
    RuntimeConfig,
    create_app,
    development_uvicorn_entrypoint,
)


def repo_mark_store() -> RepoMarkStore:
    """Return an ephemeral registry containing one active absolute-path Mark.

    Local-app tests use it when repository contents are irrelevant but app
    construction still requires the ordinary registry boundary.
    """
    engine = open_ephemeral_engine()
    store = RepoMarkStore(engine)
    store.new_mark(path=Path("/tmp"), name="repo")
    return store


def test_require_bindable_port_rejects_busy_port() -> None:
    """An explicitly requested busy backend port fails before server startup.

    The diagnostic must identify both the role and exact occupied port so the
    terminal user can act on it.
    """
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
    """Automatic selection advances both development ports by one equal offset.

    Occupying each requested port must not split the backend/frontend pair or
    return either unavailable socket.
    """
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


def test_root_explains_vite_frontend_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headless API root reports a missing development HUD as unavailable.

    Its HTML points to the Vite launch mode instead of pretending that the API
    process contains a generated frontend bundle.

    # Parameters

    - `tmp_path`: Disposable database and Snapshot root for the factory.
    - `monkeypatch`: Process environment isolation for serialized runtime config.
    """
    database_path = tmp_path / "dirdiff.sqlite"
    migration_config_path = Path(__file__).parents[2] / "alembic.ini"
    store = RepoMarkStore(
        open_sqlite_engine(database_path, migration_config_path)
    )
    store.new_mark(path=Path("/tmp"), name="repo")
    monkeypatch.setenv(
        RUNTIME_CONFIG_ENV,
        json.dumps(
            asdict(
                RuntimeConfig(
                    db_path=str(database_path),
                    migration_config_path=str(migration_config_path),
                    store_path=str(tmp_path / "store"),
                )
            )
        ),
    )
    client = TestClient(development_uvicorn_entrypoint())

    response = client.get("/")

    assert response.status_code == 503
    assert "Vite frontend is not running" in response.text
    assert "--no-frontend-dev" in response.text


def test_fastapi_docs_are_enabled(tmp_path: Path) -> None:
    """The local application keeps FastAPI's interactive API documentation.

    Startup configuration must not disable the `/docs` route used to inspect
    and exercise the development server's current HTTP contract.
    """
    store = repo_mark_store()
    client = TestClient(
        create_app(
            store,
            room_lord=RoomLord(RoomStore(store.engine), tmp_path / "store"),
        )
    )

    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_repo_list_is_sorted_by_name_and_path(tmp_path: Path) -> None:
    """Repository picker results follow registry name and path ordering.

    Insertion order must not leak through the HTTP response.
    """
    engine = open_ephemeral_engine()
    store = RepoMarkStore(engine)
    zeta_path = tmp_path / "zeta"
    alpha_path = tmp_path / "alpha"
    zeta_path.mkdir()
    alpha_path.mkdir()
    store.new_mark(path=zeta_path, name="zeta")
    store.new_mark(path=alpha_path, name="alpha")
    client = TestClient(
        create_app(
            store,
            room_lord=RoomLord(RoomStore(store.engine), tmp_path / "store"),
        )
    )

    response = client.get("/api/repos")

    assert response.status_code == 200
    assert [repo["name"] for repo in response.json()] == ["alpha", "zeta"]


def test_repo_mark_delete_deactivates_registry_state(tmp_path: Path) -> None:
    """Deleting a Mark hides it and its defaults without erasing identity rows.

    The endpoint returns no content and subsequent active-registry reads no
    longer expose the repository or saved branch selection.
    """
    engine = open_ephemeral_engine()
    store = RepoMarkStore(engine)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    mark = store.new_mark(path=repo_path, name="repo")
    store.set_main_branch(
        mark.id,
        source="local",
        remote=None,
        branch="main",
    )
    client = TestClient(
        create_app(
            store,
            room_lord=RoomLord(RoomStore(store.engine), tmp_path / "store"),
        )
    )

    response = client.delete(f"/api/repos/{mark.id}")

    assert response.status_code == 204
    assert store.get(mark.id) is None
    assert store.get_main_branch(mark.id) is None
    assert client.get("/api/repos").json() == []


def test_repo_mark_delete_reports_missing_id(tmp_path: Path) -> None:
    """Deleting an unknown active Mark returns one concrete not-found response.

    The route distinguishes absent registry state from successful deactivation
    and includes the requested id in its diagnostic.
    """
    store = repo_mark_store()
    client = TestClient(
        create_app(
            store,
            room_lord=RoomLord(RoomStore(store.engine), tmp_path / "store"),
        )
    )

    response = client.delete("/api/repos/404")

    assert response.status_code == 404
    assert response.json()["detail"] == "No marked project with id: 404"


def test_file_diff_response_schema_rejects_unknown_fields() -> None:
    """The File diff wire model refuses backend fields outside its contract.

    This prevents accidental internal data from passing validation merely
    because required composed fields are otherwise present.
    """
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ComposedDiffResponse.model_validate(
            {
                "display_name": "alpha.txt",
                "left_label": "HEAD",
                "right_label": "worktree",
                "summary": {
                    "changed_lines": 1,
                    "modified_lines": 1,
                    "added_lines": 0,
                    "removed_lines": 0,
                    "left_exists": True,
                    "right_exists": True,
                },
                "file_kind": {"type": "git", "status": "modified"},
                "left_path": "alpha.txt",
                "right_path": "alpha.txt",
                "frames": [],
                "random_backend_surprise": True,
            }
        )


def test_composed_diff_response_accepts_real_composition() -> None:
    """A real `compose()` payload validates against the wire response model.

    This closes the loop the endpoint relies on: what composition produces and
    what the HTTP boundary attaches must together satisfy `ComposedDiffResponse`,
    or `/api/file-diff` would 500 on validation for an ordinary text File.
    """
    context = ComposeContext.build(
        left_path="a.py",
        right_path="a.py",
        left_label="HEAD",
        right_label="worktree",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(b"x = 1\n", b"x = 2\n", context)
    payload = dict(composed)
    payload["display_name"] = "a.py"
    payload["file_kind"] = {"type": "git", "status": "modified"}

    response = ComposedDiffResponse.model_validate(payload)
    assert len(response.frames) == 1
    bay = response.frames[0].bays[0]
    assert bay.kind_data.kind == "text"
    assert bay.bay_key == "flatfile"
    carried = [
        row.hunk_index
        for row in bay.kind_data.rows
        if row.hunk_index is not None
    ]
    assert carried == [0], "one edit is one bay-local hunk boundary"
