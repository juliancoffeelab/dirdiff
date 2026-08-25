"""Integration coverage for FastAPI diff endpoints against real Git repos.

These tests create temporary repositories, register them through the normal
store layer, and exercise HTTP routes through `TestClient`.  They are allowed
to use local Git subprocesses and disposable SQLite files, but they should not
mock backend loading or bypass request/response contracts.
"""

import json
import subprocess
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, create_engine, select

from alembic import command
from dirdiff.db import (
    RepoMarkStore,
    RoomStore,
    UserProfileStore,
    open_sqlite_engine,
)
from dirdiff.room_lord import RoomLord
from dirdiff.server import create_app

__all__: list[str] = []


def create_repo_client(repo_path: Path) -> tuple[TestClient, int]:
    # Persistent dirdiff state is not valid worktree input. Keep this API
    # fixture outside the reviewed repository, as production configuration must.
    engine = open_sqlite_engine(
        repo_path.parent / f".{repo_path.name}-dirdiff-test.sqlite"
    )
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    mark = repo_marks.new_mark(path=repo_path, name=repo_path.name)
    room_lord = RoomLord(
        RoomStore(engine),
        repo_path.parent / f".{repo_path.name}-dirdiff-test-store",
    )
    return (
        TestClient(
            create_app(
                repo_marks,
                user_profile,
                room_lord=room_lord,
            )
        ),
        mark.id,
    )


def run_git(cwd: Path, *args: str) -> None:
    """Run one Git command while building integration-test repositories."""
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def create_committed_repo(repo_path: Path, *, branch: str) -> None:
    """Create a one-commit Git repo used as a local or remote test source."""
    run_git(repo_path, "init", "-b", branch)
    run_git(repo_path, "config", "user.name", "Test User")
    run_git(repo_path, "config", "user.email", "test@example.com")
    tracked_file = repo_path / "alpha.txt"
    tracked_file.write_text("one\n", encoding="utf-8")
    run_git(repo_path, "add", "alpha.txt")
    run_git(repo_path, "commit", "-m", "initial")


def clone_test_remote(
    tmp_path: Path,
    *,
    source_name: str = "remote-source",
    bare_name: str = "remote.git",
    worktree_name: str = "worktree",
    branch: str = "master",
) -> Path:
    """Create a bare remote from a source repo and return a normal clone."""
    source_repo = tmp_path / source_name
    source_repo.mkdir()
    create_committed_repo(source_repo, branch=branch)
    run_git(tmp_path, "clone", "--bare", str(source_repo), bare_name)
    run_git(tmp_path, "clone", str(tmp_path / bare_name), worktree_name)
    return tmp_path / worktree_name


def clone_test_remote_with_unknown_head(tmp_path: Path) -> Path:
    """Create a clone whose remote cannot report a default branch."""
    source_repo = tmp_path / "remote-source"
    source_repo.mkdir()
    create_committed_repo(source_repo, branch="main")
    run_git(tmp_path, "clone", "--bare", str(source_repo), "remote.git")
    run_git(
        tmp_path / "remote.git", "symbolic-ref", "HEAD", "refs/heads/missing"
    )
    subprocess.run(
        ["git", "clone", str(tmp_path / "remote.git"), "worktree"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path / "worktree"


def test_historical_file_review_migration_never_reads_captured_text(
    tmp_path: Path,
) -> None:
    """Retain empty, binary, and non-UTF8 File origins without fabrication."""
    database_path = tmp_path / "legacy.sqlite"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.attributes["db_path"] = database_path
    command.upgrade(config, "b74d52f083c1")

    captures = []
    for name, side, content in (
        ("empty", "right", b""),
        ("binary", "left", b"\x00\x01\xff"),
        ("non-utf8", "right", b"\x80\xfe\xff"),
    ):
        capture = tmp_path / name
        capture.mkdir()
        (capture / side).write_bytes(content)
        captures.append((capture, side))

    engine = create_engine(f"sqlite:///{database_path}")
    legacy = MetaData()
    legacy.reflect(bind=engine)
    room = legacy.tables["room"]
    snapshot = legacy.tables["snapshot"]
    snapshot_meta = legacy.tables["snapshot_meta"]
    snapshot_file = legacy.tables["snapshot_file"]
    snapshot_file_left = legacy.tables["snapshot_file_left"]
    snapshot_file_right = legacy.tables["snapshot_file_right"]
    review_thread = legacy.tables["review_thread"]
    review_action = legacy.tables["review_action"]
    repo_mark = legacy.tables["repo_mark"]
    user_profile = legacy.tables["user_profile"]
    snapshot_id = "1" * 32
    with engine.begin() as connection:
        connection.execute(
            repo_mark.insert().values(id=1, path=str(tmp_path), active=True)
        )
        connection.execute(
            room.insert().values(
                id=1,
                mark_id=1,
                tab="head",
                backend_key=b"legacy",
            )
        )
        connection.execute(
            snapshot.insert().values(
                id=snapshot_id,
                room_id=1,
                content_hash=b"s" * 32,
            )
        )
        connection.execute(
            snapshot_meta.insert().values(
                snapshot_id=snapshot_id,
                left_label="old",
                right_label="new",
                added_lines=0,
                removed_lines=0,
            )
        )
        connection.execute(user_profile.insert().values(id=1, username="old"))
        for index, (capture, side) in enumerate(captures, start=1):
            file_id = f"{index + 1:x}" * 32
            thread_id = f"{index + 4:x}" * 32
            comment_id = f"{index + 7:x}" * 32
            operation_id = f"{index + 10:x}" * 32
            connection.execute(
                snapshot_file.insert().values(
                    id=file_id,
                    snapshot_id=snapshot_id,
                    path=str(capture),
                    tracked=True,
                    change_type="modify",
                    error=None,
                )
            )
            side_table = (
                snapshot_file_left if side == "left" else snapshot_file_right
            )
            connection.execute(
                side_table.insert().values(
                    file_id=file_id,
                    repository_path=f"{capture.name}.dat",
                    content_hash=b"f" * 32,
                )
            )
            connection.execute(
                review_thread.insert().values(
                    thread_id=thread_id,
                    snapshot_id=snapshot_id,
                    snapshot_file_id=file_id,
                    is_origin=True,
                    target_kind="file",
                    # The database is at `b74d52f083c1`, before the composed
                    # key column was renamed, so this names the historical
                    # column rather than today's `bay_key`.
                    region_key=None,
                    side=None,
                    start_line=None,
                    end_line=None,
                    outdated_reason=None,
                    private_locator=None,
                )
            )
            connection.execute(
                review_action.insert().values(
                    operation_id=operation_id,
                    activity_id=index,
                    thread_id=thread_id,
                    snapshot_id=snapshot_id,
                    sequence=0,
                    kind="comment-created",
                    profile_id=1,
                    comment_id=comment_id,
                    expected_revision=None,
                    body=capture.name,
                    created_at="2026-08-16T00:00:00+00:00",
                )
            )

    engine.dispose()
    command.upgrade(config, "c8154d91a7e2")
    migrated_engine = create_engine(f"sqlite:///{database_path}")
    migrated_schema = MetaData()
    migrated_schema.reflect(bind=migrated_engine)
    migrated_threads = migrated_schema.tables["review_thread"]
    with migrated_engine.connect() as connection:
        retained = [
            tuple(row)
            for row in connection.execute(
                select(
                    migrated_threads.c.target_kind,
                    migrated_threads.c.side,
                ).order_by(migrated_threads.c.thread_id)
            )
        ]
    assert retained == [
        ("file-start", "right"),
        ("file-start", "left"),
        ("file-start", "right"),
    ]
    migrated_engine.dispose()

    command.downgrade(config, "b74d52f083c1")
    downgraded_engine = create_engine(f"sqlite:///{database_path}")
    downgraded = MetaData()
    downgraded.reflect(bind=downgraded_engine)
    old_threads = downgraded.tables["review_thread"]
    with downgraded_engine.connect() as connection:
        restored = [
            tuple(row)
            for row in connection.execute(
                select(old_threads.c.target_kind, old_threads.c.side).order_by(
                    old_threads.c.thread_id
                )
            )
        ]
    assert restored == [("file", None), ("file", None), ("file", None)]
    downgraded_engine.dispose()

    command.upgrade(config, "head")

    current_engine = create_engine(f"sqlite:///{database_path}")
    current = MetaData()
    current.reflect(bind=current_engine)
    placements = current.tables["review_thread_placement"]
    with current_engine.connect() as connection:
        migrated = [
            tuple(row)
            for row in connection.execute(
                select(
                    placements.c.target_kind,
                    placements.c.side,
                    placements.c.start_line,
                    placements.c.end_line,
                    placements.c.outdated_reason,
                    placements.c.private_locator,
                ).order_by(placements.c.thread_id)
            )
        ]

    assert migrated == [
        ("file-start", "right", None, None, None, None),
        ("file-start", "left", None, None, None, None),
        ("file-start", "right", None, None, None, None),
    ]
    client = TestClient(
        create_app(
            RepoMarkStore(current_engine),
            UserProfileStore(current_engine),
            room_lord=RoomLord(RoomStore(current_engine), tmp_path / "store"),
        )
    )
    response = client.get(
        "/api/review/threads",
        params={"snapshot_id": snapshot_id, "page": 1, "limit": 20},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_threads"] == 3
    assert [
        thread["origin_target"]["kind"] for thread in payload["threads"]
    ] == [
        "file-start",
        "file-start",
        "file-start",
    ]
    assert [thread["original_excerpt"] for thread in payload["threads"]] == [
        None,
        None,
        None,
    ]
    client.close()
    current_engine.dispose()


def test_repo_defaults_base_to_remote_and_review_to_local(
    tmp_path: Path,
) -> None:
    """Repo defaults expose structured branch-review defaults without legacy refs."""
    repo_path = clone_test_remote(tmp_path)
    run_git(repo_path, "config", "user.name", "Test User")
    run_git(repo_path, "config", "user.email", "test@example.com")
    run_git(repo_path, "checkout", "-b", "feature")
    tracked_file = repo_path / "alpha.txt"
    tracked_file.write_text("two\n", encoding="utf-8")
    run_git(repo_path, "commit", "-am", "feature")

    client, project_id = create_repo_client(repo_path)

    response = client.get(
        "/api/repo-defaults", params={"project_id": project_id}
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["default_base_selection"] == {
        "source": "remote",
        "remote": "origin",
        "branch": "master",
    }
    assert payload["preferred_review_selection"] == {
        "source": "local",
        "branch": "feature",
    }
    assert "default_base_branch" not in payload
    assert "preferred_review_branch" not in payload


def test_repo_main_branch_save_overrides_repo_defaults(
    tmp_path: Path,
) -> None:
    """Saved repo main branch controls future branch-review base defaults."""
    repo_path = clone_test_remote(tmp_path)
    client, project_id = create_repo_client(repo_path)

    save_response = client.post(
        f"/api/repos/{project_id}/main-branch",
        json={"selection": {"source": "local", "branch": "master"}},
    )
    assert save_response.status_code == 200
    assert save_response.json() == {
        "project_id": project_id,
        "selection": {"source": "local", "branch": "master"},
    }

    defaults_response = client.get(
        "/api/repo-defaults", params={"project_id": project_id}
    )
    payload = defaults_response.json()

    assert defaults_response.status_code == 200
    assert payload["default_base_selection"] == {
        "source": "local",
        "branch": "master",
    }


def test_repo_refs_returns_ref_choices_without_defaults(tmp_path: Path) -> None:
    """Repo refs expose autocomplete metadata separately from repo defaults."""
    repo_path = clone_test_remote(tmp_path)
    client, project_id = create_repo_client(repo_path)

    response = client.get("/api/repo-refs", params={"project_id": project_id})
    payload = response.json()

    assert response.status_code == 200
    assert set(payload) == {"ref_choices"}
    assert payload["ref_choices"]["builtins"] == ["HEAD", "index", "worktree"]
    assert "master" in payload["ref_choices"]["local_branches"]
    assert payload["ref_choices"]["remotes"] == ["origin"]
    assert {
        "structured": {"remote": "origin", "branch": "master"},
        "gitref": "origin/master",
    } in payload["ref_choices"]["remote_branches"]


def test_repo_main_branch_save_rejects_remote_without_remote_name(
    tmp_path: Path,
) -> None:
    """Remote main branch saves require both remote and branch fields."""
    repo_path = clone_test_remote(tmp_path)
    client, project_id = create_repo_client(repo_path)

    response = client.post(
        f"/api/repos/{project_id}/main-branch",
        json={
            "selection": {
                "source": "remote",
                "remote": "",
                "branch": "master",
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "remote is required for remote selections."
    )


def test_preferences_are_scoped_to_user_profile(tmp_path: Path) -> None:
    """User preferences are keyed by user profile id, not first row."""
    create_committed_repo(tmp_path, branch="main")
    client, _project_id = create_repo_client(tmp_path)

    first_user = client.post(
        "/api/user-profile", json={"username": "first"}
    ).json()
    second_user = client.post(
        "/api/user-profile", json={"username": "second"}
    ).json()

    first_preferences = client.get(
        f"/api/user-profile/{first_user['id']}/preferences"
    )
    second_preferences = client.get(
        f"/api/user-profile/{second_user['id']}/preferences"
    )

    assert first_preferences.status_code == 200
    assert first_preferences.json() == {
        "user_profile_id": first_user["id"],
        "aggressive_folds": True,
    }
    assert second_preferences.status_code == 200
    assert second_preferences.json() == {
        "user_profile_id": second_user["id"],
        "aggressive_folds": True,
    }

    update_response = client.patch(
        f"/api/user-profile/{first_user['id']}/preferences",
        json={"aggressive_folds": False},
    )
    reloaded_second_preferences = client.get(
        f"/api/user-profile/{second_user['id']}/preferences"
    )

    assert update_response.status_code == 200
    assert update_response.json() == {
        "user_profile_id": first_user["id"],
        "aggressive_folds": False,
    }
    assert reloaded_second_preferences.status_code == 200
    assert reloaded_second_preferences.json() == {
        "user_profile_id": second_user["id"],
        "aggressive_folds": True,
    }


def test_repo_defaults_reports_unresolved_base_when_remote_head_is_missing(
    tmp_path: Path,
) -> None:
    """Remote defaults fail when local and remote HEAD discovery both fail."""
    repo_path = clone_test_remote_with_unknown_head(tmp_path)
    client, project_id = create_repo_client(repo_path)

    response = client.get(
        "/api/repo-defaults", params={"project_id": project_id}
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["default_base_selection"] == {
        "kind": "error",
        "error": "heuristic_fail",
    }


def test_repo_defaults_uses_remote_show_when_local_remote_head_is_missing(
    tmp_path: Path,
) -> None:
    """Remote defaults fall back to `git remote show` for missing origin/HEAD."""
    repo_path = clone_test_remote(tmp_path, branch="main")
    run_git(repo_path, "remote", "set-head", "origin", "-d")
    client, project_id = create_repo_client(repo_path)

    response = client.get(
        "/api/repo-defaults", params={"project_id": project_id}
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["default_base_selection"] == {
        "source": "remote",
        "remote": "origin",
        "branch": "main",
    }


def test_repo_defaults_prefers_current_branch_upstream_remote(
    tmp_path: Path,
) -> None:
    """Default remote follows current branch upstream before origin."""
    origin_worktree = clone_test_remote(
        tmp_path,
        source_name="origin-source",
        bare_name="origin.git",
        worktree_name="worktree",
        branch="master",
    )
    upstream_source = tmp_path / "upstream-source"
    upstream_source.mkdir()
    create_committed_repo(upstream_source, branch="main")
    run_git(upstream_source, "checkout", "-b", "feature")
    (upstream_source / "alpha.txt").write_text("two\n", encoding="utf-8")
    run_git(upstream_source, "commit", "-am", "feature")
    run_git(upstream_source, "checkout", "main")
    run_git(tmp_path, "clone", "--bare", str(upstream_source), "upstream.git")

    run_git(
        origin_worktree,
        "remote",
        "add",
        "upstream",
        str(tmp_path / "upstream.git"),
    )
    run_git(origin_worktree, "fetch", "upstream")
    run_git(origin_worktree, "remote", "set-head", "upstream", "--auto")
    run_git(origin_worktree, "checkout", "-b", "feature", "upstream/feature")

    client, project_id = create_repo_client(origin_worktree)

    response = client.get(
        "/api/repo-defaults", params={"project_id": project_id}
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["default_base_selection"] == {
        "source": "remote",
        "remote": "upstream",
        "branch": "main",
    }


def test_repo_defaults_local_only_repo_to_main(
    tmp_path: Path,
) -> None:
    """Local-only defaults use main/master policy, not current HEAD guessing."""
    create_committed_repo(tmp_path, branch="main")
    run_git(tmp_path, "checkout", "-b", "feature")
    client, project_id = create_repo_client(tmp_path)

    response = client.get(
        "/api/repo-defaults", params={"project_id": project_id}
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["default_base_selection"] == {
        "source": "local",
        "branch": "main",
    }


def test_branch_review_query_validation_returns_bad_request(
    tmp_path: Path,
) -> None:
    """Branch-review dependency validation returns 400, not an uncaught 500."""
    subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    client, project_id = create_repo_client(tmp_path)

    response = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "branch-review",
            "base_branch": "master",
            "review_source": "local",
            "review_branch": "feature",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "base_source is required for the Branch Review Tab."
    )


def test_file_diff_endpoint_returns_full_generated_file_rows(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
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
        ["git", "add", "Cargo.lock"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    lockfile.write_text("version = 2\n", encoding="utf-8")

    client, project_id = create_repo_client(tmp_path)

    repo_response = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    repo_payload = repo_response.json()
    snapshot_id = repo_payload["snapshot_id"]
    assert isinstance(snapshot_id, str)
    assert snapshot_id != ""
    assert "files" not in repo_payload
    assert repo_payload["tree"] == [
        {
            "type": "file",
            "name": "Cargo.lock",
            "entry": {
                "lazy": "generated",
                "left_path": "Cargo.lock",
                "right_path": "Cargo.lock",
                "file_kind": {"type": "git", "status": "modified"},
            },
        }
    ]
    lazy_info_response = client.get(
        "/api/lazy-info",
        params={
            "snapshot_id": snapshot_id,
        },
    )
    lazy_info = lazy_info_response.json()["files"][0]
    assert lazy_info_response.status_code == 200
    assert lazy_info == {
        "lazy": "generated",
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "display_name": "Cargo.lock",
        "changed_lines": None,
        "added_lines": None,
        "removed_lines": None,
        "file_kind": {"type": "git", "status": "modified"},
    }

    response = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": "dirdiff",
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["display_name"] == "Cargo.lock"
    assert payload["file_kind"] == {"type": "git", "status": "modified"}
    bay = payload["frames"][0]["bays"][0]
    assert bay["kind"] == "text"
    assert bay["bay_key"] == "flatfile"
    assert bay["rows"] != []

    reloaded_manifest_response = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    reloaded_snapshot_id = reloaded_manifest_response.json()["snapshot_id"]

    assert reloaded_manifest_response.status_code == 200
    assert reloaded_snapshot_id == snapshot_id

    repeated_response = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": "dirdiff",
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
        },
    )

    assert repeated_response.status_code == 200


def test_preset_manifest_and_file_diff_do_not_require_a_mark(
    tmp_path: Path,
) -> None:
    """Preset mode is a checked-in fixture workflow, not a marked-repo workflow."""
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets" / "difftastic"),
        )
    )

    manifest_response = client.get(
        "/api/manifest",
        params={
            "tab": "preset",
            "project_id": "diff",
            "preset_subset": "python",
        },
    )
    manifest = manifest_response.json()

    assert manifest_response.status_code == 200
    assert manifest["display_name"] == "python"
    assert isinstance(manifest["snapshot_id"], str)
    assert manifest["snapshot_id"] != ""
    assert manifest["tree"] != []

    stack = list(manifest["tree"])
    file_entry = None
    while len(stack) > 0:
        node = stack.pop()
        if node["type"] == "file":
            file_entry = node["entry"]
            break
        stack.extend(node["entries"])
    assert file_entry is not None
    assert "hunk_count" not in file_entry
    assert "hunks" not in file_entry

    lazy_info_response = client.get(
        "/api/lazy-info",
        params={
            "snapshot_id": manifest["snapshot_id"],
        },
    )
    file_diff_response = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": manifest["snapshot_id"],
            "engine": "dirdiff",
            "left_path": file_entry["left_path"],
            "right_path": file_entry["right_path"],
        },
    )
    file_diff = file_diff_response.json()

    assert lazy_info_response.status_code == 200
    assert lazy_info_response.json() == {"files": []}
    assert file_diff_response.status_code == 200
    assert file_diff["display_name"] != ""
    assert len(file_diff["frames"]) == 1
    bay = file_diff["frames"][0]["bays"][0]
    assert bay["kind"] == "text"
    assert bay["bay_key"] == "flatfile"
    rows = bay["rows"]
    assert rows != []
    assert "render_mode" not in file_diff
    assert "truncated_rows" not in file_diff
    assert all(
        row["status"] in {"equal", "replace", "insert", "delete", "move"}
        for row in rows
    )
    assert all(
        "foldedRows" not in row and "count" not in row and "label" not in row
        for row in rows
    )
    # The composed payload carries no File hunk total; the frontend derives it
    # from the bays it received. A row's index is bay-local, and this File
    # composes exactly one bay, so its indices run from zero without a gap.
    assert "hunk_count" not in file_diff
    hunk_indices = [
        row["hunk_index"] for row in rows if row["hunk_index"] is not None
    ]
    assert hunk_indices == list(range(len(hunk_indices)))
    assert hunk_indices != []


def test_all_preset_catalogs_load_without_project_id(tmp_path: Path) -> None:
    """Every preset catalog is repo-less, even though catalogs use different roots."""
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets" / "difftastic"),
        )
    )

    for project_id, preset_subset in [
        ("diff", "python"),
        ("fold", "python"),
        ("gumtree", "python"),
        ("scroll", "mixed-file-sizes"),
    ]:
        response = client.get(
            "/api/manifest",
            params={
                "tab": "preset",
                "project_id": project_id,
                "preset_subset": preset_subset,
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["display_name"] == preset_subset
        assert isinstance(payload["snapshot_id"], str)
        assert payload["snapshot_id"] != ""
        assert payload["tree"] != []


def test_scroll_preset_can_force_compact_files_lazy(tmp_path: Path) -> None:
    """Preset metadata should model lazy placement without giant fixture files."""
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    client = TestClient(
        create_app(
            RepoMarkStore(engine),
            UserProfileStore(engine),
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets" / "difftastic"),
        )
    )

    manifest_response = client.get(
        "/api/manifest",
        params={
            "tab": "preset",
            "project_id": "scroll",
            "preset_subset": "lazy-files",
        },
    )
    snapshot_id = manifest_response.json()["snapshot_id"]
    lazy_info_response = client.get(
        "/api/lazy-info",
        params={
            "snapshot_id": snapshot_id,
        },
    )

    assert manifest_response.status_code == 200
    assert isinstance(snapshot_id, str)
    assert snapshot_id != ""
    assert lazy_info_response.status_code == 200
    lazy_files = lazy_info_response.json()["files"]
    assert [file["display_name"] for file in lazy_files] == [
        "lazy-files/02-uv-lock/new.lock",
        "lazy-files/04-frontend-src-RepoPicker/new.tsx",
    ]
    assert [file["lazy"] for file in lazy_files] == [
        "generated",
        "too_big",
    ]


def test_preset_manifest_validates_required_preset_fields(
    tmp_path: Path,
) -> None:
    """Repo-less preset loading still validates the preset-specific inputs."""
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets" / "difftastic"),
        )
    )

    missing_subset = client.get(
        "/api/manifest",
        params={
            "tab": "preset",
            "project_id": "diff",
        },
    )
    missing_project = client.get(
        "/api/manifest",
        params={
            "tab": "preset",
            "preset_subset": "python",
        },
    )
    traversal = client.get(
        "/api/manifest",
        params={
            "tab": "preset",
            "project_id": "diff",
            "preset_subset": "../python",
        },
    )

    assert missing_subset.status_code == 400
    assert missing_subset.json()["detail"] == (
        "preset_subset is required for the Preset Tab."
    )
    assert missing_project.status_code == 422
    assert traversal.status_code == 400
    assert traversal.json()["detail"] == (
        "Preset name must stay inside the presets root."
    )


def test_repo_manifest_endpoint_returns_minimal_deleted_file_entry(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
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
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    deleted_file.unlink()

    client, project_id = create_repo_client(tmp_path)

    response = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["tree"] == [
        {
            "type": "file",
            "name": "alpha.txt",
            "entry": {
                "lazy": "deleted",
                "left_path": "alpha.txt",
                "right_path": None,
                "file_kind": {"type": "git", "status": "deleted"},
            },
        }
    ]


def test_agent_batch_applies_set_reads_across_threads(tmp_path: Path) -> None:
    """One agent batch addressing several Threads folds and persists atomically."""
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "alpha.txt").write_text("one\nchanged\n", encoding="utf-8")
    client, _project_id = create_repo_client(tmp_path)

    joined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "a" * 32,
            "name": "batch reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    ).json()
    sides = sorted(Path(joined["snapshot_path"]).glob("*/right"))
    assert sides != []

    created = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(sides[0]),
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "first finding",
                },
                {
                    "kind": "create-finding",
                    "file": str(sides[0]),
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 2,
                        "end_line": 2,
                    },
                    "body": "second finding",
                },
            ],
        },
    )
    assert created.status_code == 200
    thread_ids = [result["thread_id"] for result in created.json()["results"]]
    assert len(set(thread_ids)) == 2

    # One follow-up batch addresses both existing Threads. The bulk history
    # read must fold each Thread independently and in authored order: the
    # reviewer-return is only legal because the same batch's author-response
    # moved that Thread's attention to the reviewer first.
    replied = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "author-response",
                    "thread_id": thread_ids[0],
                    "body": "first response",
                },
                {
                    "kind": "author-response",
                    "thread_id": thread_ids[1],
                    "body": "second response",
                },
                {
                    "kind": "reviewer-return",
                    "thread_id": thread_ids[1],
                    "body": "returned",
                },
                {
                    "kind": "inert-comment",
                    "thread_id": thread_ids[0],
                    "body": "extra context",
                },
            ],
        },
    )
    assert replied.status_code == 200
    outcomes = [
        (result["kind"], result["status"], result["attention"])
        for result in replied.json()["results"]
    ]
    assert outcomes == [
        ("author-response", "open", "reviewer"),
        ("author-response", "open", "reviewer"),
        ("reviewer-return", "open", "author"),
        ("inert-comment", "open", "reviewer"),
    ]

    # Changing the worktree and joining again publishes a new Snapshot whose
    # capture must relocate both range Threads into the fresh capture (the
    # derivation reads the new Snapshot's own bytes at their final address).
    (tmp_path / "alpha.txt").write_text(
        "one\nchanged\nagain\n", encoding="utf-8"
    )
    rejoined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "c" * 32,
            "name": "recapture reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    )
    assert rejoined.status_code == 200
    recaptured = rejoined.json()
    assert recaptured["snapshot_id"] != joined["snapshot_id"]
    threads = client.get(
        "/api/agent/threads",
        params={
            "snapshot_id": recaptured["snapshot_id"],
            "page": 1,
            "limit": 20,
        },
    ).json()
    assert sorted(item["thread_id"] for item in threads["items"]) == sorted(
        thread_ids
    )


def test_agent_batch_failure_commits_no_rows(tmp_path: Path) -> None:
    """A batch with one invalid action must leave zero review rows behind."""
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "alpha.txt").write_text("one\nchanged\n", encoding="utf-8")
    client, _project_id = create_repo_client(tmp_path)

    joined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "b" * 32,
            "name": "atomic reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    ).json()
    sides = sorted(Path(joined["snapshot_path"]).glob("*/right"))
    assert sides != []

    rejected = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(sides[0]),
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "valid finding",
                },
                {
                    "kind": "author-response",
                    "thread_id": "f" * 32,
                    "body": "reply to a Thread that does not exist",
                },
            ],
        },
    )
    assert rejected.status_code == 400

    threads = client.get(
        "/api/agent/threads",
        params={"snapshot_id": joined["snapshot_id"], "page": 1, "limit": 20},
    ).json()
    assert threads["items"] == []


def test_agent_batch_reports_first_invalid_action_in_batch_order(
    tmp_path: Path,
) -> None:
    """Mixed-problem batches report the earliest action's exact failure."""
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "alpha.txt").write_text("one\nchanged\n", encoding="utf-8")
    client, _project_id = create_repo_client(tmp_path)

    joined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "c" * 32,
            "name": "ordering reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    ).json()
    sides = sorted(Path(joined["snapshot_path"]).glob("*/right"))
    assert sides != []

    # An absent (but well-formed) captured path precedes a malformed relative
    # path: the earlier action's failure must win regardless of validation
    # phases inside the route.
    absent = str(Path(sides[0]).parent.parent / ("d" * 32) / "right")
    rejected = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": absent,
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "targets a missing capture",
                },
                {
                    "kind": "create-finding",
                    "file": "relative/left",
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "malformed path",
                },
            ],
        },
    )
    assert rejected.status_code == 400
    assert "File is absent from the Snapshot." in rejected.text

    # A creation that is doubly invalid (absent target pair and blank body)
    # reports the absent target, matching the pre-focused-read precedence.
    doubly_invalid = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": absent,
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": " ",
                },
            ],
        },
    )
    assert doubly_invalid.status_code == 400
    assert "File is absent from the Snapshot." in doubly_invalid.text


def test_agent_addresses_a_notebook_cell_bay_in_both_directions(
    tmp_path: Path,
) -> None:
    """An agent names a cell bay on write and reads the same one back."""
    cell: dict[str, object] = {
        "cell_type": "code",
        "id": "stable-cell",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": ["a = 1\n", "b = 2\n", "c = 3\n"],
    }
    notebook = {
        "cells": [cell],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "demo.ipynb").write_text(
        json.dumps(notebook, indent=1), encoding="utf-8"
    )
    run_git(tmp_path, "add", "demo.ipynb")
    run_git(tmp_path, "commit", "-m", "notebook")
    cell["source"] = ["a = 1\n", "b = 22\n", "c = 3\n"]
    (tmp_path / "demo.ipynb").write_text(
        json.dumps(notebook, indent=1), encoding="utf-8"
    )
    client, _project_id = create_repo_client(tmp_path)

    joined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "d" * 32,
            "name": "notebook reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    ).json()

    # The agent finds the notebook the way it finds any captured side: by
    # inspecting the bytes, since File-id directories carry no repository path.
    captured = [
        side
        for side in sorted(Path(joined["snapshot_path"]).glob("*/right"))
        if "stable-cell" in side.read_text(encoding="utf-8")
    ]
    assert len(captured) == 1
    notebook_side = captured[0]

    # Line two of the cell's joined source is `b = 22`. Line two of the
    # captured `.ipynb` is JSON structure, which is what the old flatfile
    # hardcode silently addressed instead.
    assert notebook_side.read_text(encoding="utf-8").splitlines()[
        1
    ].strip() != ("b = 22")
    created = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(notebook_side),
                    "bay": {
                        "bay_key": "stable-cell",
                        "start_line": 2,
                        "end_line": 2,
                    },
                    "body": "the cell changed",
                },
            ],
        },
    )
    assert created.status_code == 200
    thread_id = created.json()["results"][0]["thread_id"]

    read = client.get(
        f"/api/agent/thread/{thread_id}",
        params={"snapshot_id": joined["snapshot_id"], "page": 1, "limit": 20},
    )
    assert read.status_code == 200
    body = read.json()
    assert body["bay"] == {
        "bay_key": "stable-cell",
        "start_line": 2,
        "end_line": 2,
    }
    assert body["file"] == str(notebook_side)
    # The excerpt is cut from the cell's own text, so it holds the cell source
    # rather than a window of the surrounding `.ipynb` JSON.
    excerpt = body["original_excerpt"]
    assert excerpt["lines"] == ["a = 1", "b = 22", "c = 3"]
    assert excerpt["start_line"] == 1
    assert excerpt["selected_start_line"] == 2
    assert excerpt["selected_end_line"] == 2

    summary = client.get(
        "/api/agent/thread_summary",
        params={"snapshot_id": joined["snapshot_id"], "page": 1, "limit": 20},
    ).json()
    assert [item["bay"] for item in summary["items"]] == [
        {"bay_key": "stable-cell", "start_line": 2, "end_line": 2}
    ]

    # A notebook composes no `flatfile` bay, so the key the agent boundary
    # used to hardcode is now rejected rather than silently mis-addressed.
    rejected = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(notebook_side),
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 2,
                        "end_line": 2,
                    },
                    "body": "wrong bay",
                },
            ],
        },
    )
    assert rejected.status_code == 400
    assert "Unknown rendered bay." in rejected.text

    # A line past the end of the cell's own three lines is out of range, even
    # though the captured `.ipynb` has far more lines than that.
    overrun = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(notebook_side),
                    "bay": {
                        "bay_key": "stable-cell",
                        "start_line": 4,
                        "end_line": 4,
                    },
                    "body": "past the cell",
                },
            ],
        },
    )
    assert overrun.status_code == 400


def test_lost_bay_and_lost_region_threads_land_on_stored_bay_starts(
    tmp_path: Path,
) -> None:
    """Each derivation stores its landing: origin bay, first bay, File start.

    One notebook Thread rides through every landing the placement matrix
    names. The worktree mutates between captures — the origin's cell is
    deleted, rewritten, the notebook is corrupted to non-JSON, to non-UTF-8
    bytes, emptied of cells, and finally removed — and each capture's stored
    placement must be exactly the shape derivation promised for that loss.
    """
    cell_a: dict[str, object] = {
        "cell_type": "code",
        "id": "cell-a",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": ["x = 0\n"],
    }
    cell_b: dict[str, object] = {
        "cell_type": "code",
        "id": "cell-b",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": ["def beta():\n", "    total = 1\n", "    return total\n"],
    }
    notebook: dict[str, object] = {
        "cells": [cell_a, cell_b],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "demo.ipynb").write_text(
        json.dumps(notebook, indent=1), encoding="utf-8"
    )
    run_git(tmp_path, "add", "demo.ipynb")
    run_git(tmp_path, "commit", "-m", "notebook")
    cell_b["source"] = [
        "def beta():\n",
        "    total = 2\n",
        "    return total\n",
    ]
    (tmp_path / "demo.ipynb").write_text(
        json.dumps(notebook, indent=1), encoding="utf-8"
    )
    client, _project_id = create_repo_client(tmp_path)

    joined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "e" * 32,
            "name": "landing reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    ).json()
    captured = [
        side
        for side in sorted(Path(joined["snapshot_path"]).glob("*/right"))
        if "cell-b" in side.read_text(encoding="utf-8")
    ]
    assert len(captured) == 1
    created = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(captured[0]),
                    "bay": {
                        "bay_key": "cell-b",
                        "start_line": 2,
                        "end_line": 2,
                    },
                    "body": "the total changed",
                },
            ],
        },
    )
    assert created.status_code == 200
    thread_id = created.json()["results"][0]["thread_id"]

    # The origin Snapshot itself reads back as the exact stored range.
    origin_read = client.get(
        "/api/review/threads",
        params={"snapshot_id": joined["snapshot_id"], "page": 1, "limit": 20},
    ).json()
    assert [
        (
            thread["code_location"]["kind"],
            thread["code_location"]["bay"]["bay_key"],
            thread["outdated_reason"],
        )
        for thread in origin_read["threads"]
    ] == [("range", "cell-b", None)]

    # Six worktree losses, each captured as its own Snapshot. Every entry is
    # (agent uuid, notebook bytes or None to delete the file, the expected
    # (location kind, bay key, outdated reason) of the derived placement).
    losses: list[
        tuple[str, bytes | None, tuple[str | None, str | None, str]]
    ] = [
        # The origin's own bay is gone; the File's first right-carrying bay
        # is the unchanged first cell, chosen and stored at derivation.
        (
            "f" * 32,
            json.dumps({**notebook, "cells": [cell_a]}, indent=1).encode(),
            ("bay-start", "cell-a", "bay_not_found"),
        ),
        # The bay survives while nothing inside it matches the origin region.
        (
            "1" * 32,
            json.dumps(
                {
                    **notebook,
                    "cells": [
                        cell_a,
                        {
                            **cell_b,
                            "source": [
                                "def omega():\n",
                                "    return None\n",
                            ],
                        },
                    ],
                },
                indent=1,
            ).encode(),
            ("bay-start", "cell-b", "region_not_found"),
        ),
        # Non-JSON bytes are not a notebook: the File composes the flatfile
        # terminal, so the cell origin lands on that first right-carrying bay.
        (
            "2" * 32,
            b"{ this is not a notebook\n",
            ("bay-start", "flatfile", "bay_not_found"),
        ),
        # Non-UTF-8 bytes fail composition itself: no bay exists to land on.
        (
            "3" * 32,
            b"\x80\xfe\xffnot text",
            ("file-start", None, "bay_not_found"),
        ),
        # A valid notebook whose bays carry no right side at all: every cell
        # pair is left-only, so the placement falls to File start.
        (
            "4" * 32,
            json.dumps({**notebook, "cells": []}, indent=1).encode(),
            ("file-start", None, "bay_not_found"),
        ),
        # The exact File pair is gone: a deletion pairs (left, None), which
        # is not the origin's (left, right) pair.
        ("5" * 32, None, (None, None, "file_missing")),
    ]
    for agent_uuid, notebook_bytes, expected in losses:
        if notebook_bytes is None:
            (tmp_path / "demo.ipynb").unlink()
        else:
            (tmp_path / "demo.ipynb").write_bytes(notebook_bytes)
        rejoined = client.post(
            "/api/agent/join_review",
            json={
                "agent_uuid": agent_uuid,
                "name": f"landing reviewer {agent_uuid[:2]}",
                "tab": {"kind": "head", "repo_path": str(tmp_path)},
            },
        )
        assert rejoined.status_code == 200
        snapshot_id = rejoined.json()["snapshot_id"]
        assert snapshot_id != joined["snapshot_id"]
        read = client.get(
            "/api/review/threads",
            params={"snapshot_id": snapshot_id, "page": 1, "limit": 20},
        )
        assert read.status_code == 200
        threads = read.json()["threads"]
        assert [
            (
                thread["code_location"]["kind"]
                if thread["code_location"] is not None
                else None,
                (
                    thread["code_location"]["bay"]["bay_key"]
                    if thread["code_location"] is not None
                    and "bay" in thread["code_location"]
                    else None
                ),
                thread["outdated_reason"],
            )
            for thread in threads
        ] == [expected], f"unexpected landing for uuid {agent_uuid}"
        # The text origin keeps its excerpt through every loss.
        assert [
            thread["original_excerpt"] is not None for thread in threads
        ] == [True]

        # The agent boundary reports the same stored landing: a bay-start
        # placement is a bare bay key, a File start is no bay at all.
        agent_read = client.get(
            f"/api/agent/thread/{thread_id}",
            params={"snapshot_id": snapshot_id, "page": 1, "limit": 20},
        )
        assert agent_read.status_code == 200
        expected_kind, expected_bay_key, expected_reason = expected
        agent_body = agent_read.json()
        assert agent_body["outdated_reason"] == expected_reason
        if expected_kind == "bay-start":
            assert agent_body["bay"] == {"bay_key": expected_bay_key}
            assert agent_body["file"] is not None
        else:
            assert agent_body["bay"] is None
