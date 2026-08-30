"""Integration coverage for FastAPI diff endpoints against real Git repos.

These tests create temporary repositories, register them through the normal
store layer, and exercise HTTP routes through `TestClient`.  They are allowed
to use local Git subprocesses and disposable SQLite files, but they should not
mock backend loading or bypass request/response contracts.
"""

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
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
from dirdiff.util import JsonValue


def create_repo_client(repo_path: Path) -> tuple[TestClient, int]:
    """Create a real API client and active Mark for one disposable repository.

    `repo_path` is the reviewed worktree. Database and Snapshot storage are
    placed beside it, preserving the production rule that dirdiff state cannot
    become worktree input. The caller receives the Mark id for manifest calls.

    # Returns

    - `First`: A client bound to the real application and disposable Room storage
      beside the repository.
    - `Second`: The active repository Mark id accepted by manifest endpoints.
    """
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
    """Run one Git command while building integration-test repositories.

    # Parameters

    - `cwd`: Disposable repository or parent directory in which Git runs.
    - `args`: Exact Git arguments after the executable name.

    Nonzero exit fails the test and captured output remains attached to the
    exception.
    """
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def create_committed_repo(repo_path: Path, *, branch: str) -> None:
    """Create a one-commit Git repo used as a local or remote test source.

    # Parameters

    - `repo_path`: Existing empty directory to initialize and mutate.
    - `branch`: Exact initial branch name needed by the scenario.
    """
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
    """Create a bare remote from a source repo and return a normal clone.

    # Parameters

    - `tmp_path`: Empty test root receiving all three repositories.
    - `source_name`: Directory for the committed source repository.
    - `bare_name`: Directory for the bare remote clone.
    - `worktree_name`: Directory for the ordinary returned clone.
    - `branch`: Initial source branch advertised by the remote.
    """
    source_repo = tmp_path / source_name
    source_repo.mkdir()
    create_committed_repo(source_repo, branch=branch)
    run_git(tmp_path, "clone", "--bare", str(source_repo), bare_name)
    run_git(tmp_path, "clone", str(tmp_path / bare_name), worktree_name)
    return tmp_path / worktree_name


def clone_test_remote_with_unknown_head(tmp_path: Path) -> Path:
    """Create a clone whose remote cannot report a default branch.

    The fixture isolates default-discovery failure without substituting a guessed ref.
    """
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
    """Retain empty, binary, and non-UTF8 File origins without fabrication.

    Review reads must preserve each valid File-level origin and never invent text coordinates.
    """
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
    assert [
        "excerpt" in thread["origin_target"] for thread in payload["threads"]
    ] == [False, False, False]
    client.close()
    current_engine.dispose()


def test_repo_defaults_base_to_remote_and_review_to_local(
    tmp_path: Path,
) -> None:
    """Repository defaults expose structured Branch Review choices without legacy refs.

    The HTTP contract keeps source, remote, and branch separate for frontend controls.
    """
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
    """A saved repository main branch controls future Branch Review base defaults.

    Persistence must win over rediscovery while remaining a symbolic structured choice.
    """
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
    """Repository refs expose autocomplete metadata separately from defaults.

    Listing available choices must not silently change the saved Branch Review base.
    """
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
    """Remote main-branch saves require both remote and branch fields.

    Partial symbolic configuration is rejected rather than repaired with an invented value.
    """
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
    """User preferences are keyed by Profile identity, not table position.

    Reads and writes for one Profile must leave every other Profile's complete row unchanged.
    """
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
    """Remote defaults fail when local and remote HEAD discovery both fail.

    The endpoint must expose the missing contract instead of choosing an unrelated branch.
    """
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
    """Remote defaults use `git remote show` when origin/HEAD is absent.

    The discovered symbolic branch remains paired with its remote in the structured result.
    """
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
    """Default remote follows the current branch upstream before origin.

    This preserves repository-specific tracking configuration rather than privileging a name.
    """
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
    """Local-only defaults use main/master policy, not current HEAD guessing.

    An arbitrary checked-out feature branch must not become the implicit review base.
    """
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
    """Branch Review dependency validation returns 400, not an uncaught 500.

    Invalid structured choices are caller errors and must not escape the HTTP boundary.
    """
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
    """A generated-file policy delays loading without truncating later rows.

    The manifest marks a changed lockfile lazy. An explicit File diff then
    renders its complete captured contents, proving laziness is presentation
    policy rather than content loss.
    """
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
    assert bay["kind_data"]["kind"] == "text"
    assert bay["bay_key"] == "flatfile"
    assert bay["kind_data"]["rows"] != []

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
    """Preset mode is a checked-in fixture workflow, not a marked-repository workflow.

    Its endpoint must not require or expose a repository registration identity.
    """
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets"),
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
    assert bay["kind_data"]["kind"] == "text"
    assert bay["bay_key"] == "flatfile"
    rows = bay["kind_data"]["rows"]
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
    """Every preset catalog is repository-less despite using different fixture roots.

    Catalog selection changes fixture discovery only, never repository registry state.
    """
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets"),
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
    """Preset metadata models lazy placement without giant fixture Files.

    Explicit fixture metadata must drive the same manifest contract as size-based deferral.
    """
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    client = TestClient(
        create_app(
            RepoMarkStore(engine),
            UserProfileStore(engine),
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets"),
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
    """Repository-less preset loading still validates preset-specific inputs.

    Removing repository dependencies must not weaken catalog or fixture identity checks.
    """
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets"),
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
    """A deleted File keeps only its left identity and deferred-load reason.

    This guards the HTTP tree shape for a genuinely absent worktree side; the
    manifest must not invent a right path or require content rendering.
    """
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
    """One agent batch addressing several Threads folds and persists atomically.

    Every accepted action must observe prior actions in order, with no partial publication.
    """
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
    """A batch with one invalid action leaves zero review rows behind.

    Validation and persistence share one transaction so earlier valid actions cannot leak.
    """
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
    """Mixed-problem batches report the earliest action's exact failure.

    Ordered validation must not mask it with a later error or a generic batch response.
    """
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
    """An agent names a cell bay on write and reads the same one back.

    Public bay identity must survive persistence without being reduced to a
    File-level target.
    """
    cell: dict[str, JsonValue] = {
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
    cell_a: dict[str, JsonValue] = {
        "cell_type": "code",
        "id": "cell-a",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": ["x = 0\n"],
    }
    cell_b: dict[str, JsonValue] = {
        "cell_type": "code",
        "id": "cell-b",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": ["def beta():\n", "    total = 1\n", "    return total\n"],
    }
    notebook: dict[str, JsonValue] = {
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
        (thread["origin_target"]["bay"]["bay_key"], thread["placement"])
        for thread in origin_read["threads"]
    ] == [
        (
            "cell-b",
            {"kind": "region-kept", "range": {"start_line": 2, "end_line": 2}},
        )
    ]

    # Six worktree losses, each captured as its own Snapshot. Every entry is
    # (agent uuid, notebook bytes or None to delete the file, the expected
    # (browser placement, agent bay, agent outdated reason) of the derived
    # landing). The two boundaries are stated separately because the agent
    # shape keeps five reason names for eight placement kinds.
    losses: list[
        tuple[
            str,
            bytes | None,
            tuple[dict[str, JsonValue], dict[str, str] | None, str],
        ]
    ] = [
        # The origin's own bay is gone; the File's first right-carrying bay
        # is the unchanged first cell, chosen and stored at derivation.
        (
            "f" * 32,
            json.dumps({**notebook, "cells": [cell_a]}, indent=1).encode(),
            (
                {"kind": "bay-lost", "bay": {"bay_key": "cell-a"}},
                {"bay_key": "cell-a"},
                "bay_not_found",
            ),
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
            (
                {"kind": "region-lost"},
                {"bay_key": "cell-b"},
                "region_not_found",
            ),
        ),
        # A claimed notebook whose JSON cannot be read preserves its raw
        # text in the notebook boundary, so the cell origin lands on that
        # first right-carrying bay.
        (
            "2" * 32,
            b"{ this is not a notebook\n",
            (
                {
                    "kind": "bay-lost",
                    "bay": {"bay_key": "notebook:raw"},
                },
                {"bay_key": "notebook:raw"},
                "bay_not_found",
            ),
        ),
        # Non-UTF-8 bytes are not a notebook and are not text either, so
        # classification reaches the blob terminal. That composes a bay, so
        # the cell origin lands on its start exactly as the flatfile case
        # above does — the File stopped being a notebook, and the Thread
        # lands on whatever terminal it became.
        (
            "3" * 32,
            b"\x80\xfe\xffnot text",
            (
                {"kind": "bay-lost", "bay": {"bay_key": "blob"}},
                {"bay_key": "blob"},
                "bay_not_found",
            ),
        ),
        # A notebook whose cells were all removed has only left-carrying
        # cell bays, so the right-side origin falls to File start.
        (
            "4" * 32,
            json.dumps({**notebook, "cells": []}, indent=1).encode(),
            ({"kind": "side-lost"}, None, "bay_not_found"),
        ),
        # The exact File pair is gone: a deletion pairs (left, None), which
        # is not the origin's (left, right) pair.
        ("5" * 32, None, ({"kind": "file-absent"}, None, "file_missing")),
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
        expected_placement, expected_bay, expected_reason = expected
        assert [thread["placement"] for thread in threads] == [
            expected_placement
        ], f"unexpected landing for uuid {agent_uuid}"
        # The text origin keeps its excerpt through every loss.
        assert ["excerpt" in thread["origin_target"] for thread in threads] == [
            True
        ]

        # The agent boundary reports the same stored landing under its own
        # five-name vocabulary: a bay-level landing is a bare bay key, a
        # File-level one is no bay at all.
        agent_read = client.get(
            f"/api/agent/thread/{thread_id}",
            params={"snapshot_id": snapshot_id, "page": 1, "limit": 20},
        )
        assert agent_read.status_code == 200
        agent_body = agent_read.json()
        assert agent_body["outdated_reason"] == expected_reason
        assert agent_body["bay"] == expected_bay
        if expected_bay is not None:
            assert agent_body["file"] is not None


def test_unreadable_file_lands_its_thread_without_hiding_the_others(
    tmp_path: Path,
) -> None:
    """An uncapturable File reports why, and costs the Snapshot nothing else.

    A capture failure persists an `error` on the File and fills its capture
    directory with dirdiff's placeholder text, so composing it would quote a
    fabrication back to the reviewer. Derivation must therefore land that
    File's Thread with no code location at all, naming `file_unreadable` — not
    the `bay_not_found` a lost bay would report, and not a File start whose
    side record digests dirdiff's own prose — while every other Thread in the
    same Snapshot keeps its exact range.

    The unreadable side is an untracked worktree file the process cannot open.
    That is the reachable shape of the failure: only object-id-less sides are
    read eagerly, and `git ls-files --others` lists an untracked file by its
    stat alone, so listing succeeds and the read that follows is the one thing
    that fails. A tracked file would abort the whole capture inside `git diff`
    instead, which is a different contract and not this one.
    """
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file, so no capture can fail")
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "loud.py").write_text("def loud():\n    return 1\n")
    run_git(tmp_path, "add", "loud.py")
    run_git(tmp_path, "commit", "-m", "one module")
    (tmp_path / "loud.py").write_text("def loud():\n    return 2\n")
    (tmp_path / "shy.py").write_text("def shy():\n    return 2\n")
    client, _project_id = create_repo_client(tmp_path)

    joined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "a" * 32,
            "name": "capture reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    ).json()
    captured = sorted(Path(joined["snapshot_path"]).glob("*/right"))
    for name in ("shy.py", "loud.py"):
        captured_side = next(
            side
            for side in captured
            if side.read_text(encoding="utf-8").startswith(f"def {name[:-3]}")
        )
        created = client.post(
            "/api/agent/actions",
            json={
                "snapshot_id": joined["snapshot_id"],
                "profile_id": joined["profile_id"],
                "actions": [
                    {
                        "kind": "create-finding",
                        "file": str(captured_side),
                        "bay": {
                            "bay_key": "flatfile",
                            "start_line": 2,
                            "end_line": 2,
                        },
                        "body": f"the {name} return changed",
                    },
                ],
            },
        )
        assert created.status_code == 200

    # The next capture lists shy.py and then cannot read it.
    (tmp_path / "shy.py").chmod(0o000)
    rejoined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "b" * 32,
            "name": "capture reviewer two",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    )
    assert rejoined.status_code == 200
    snapshot_id = rejoined.json()["snapshot_id"]
    assert snapshot_id != joined["snapshot_id"]
    (tmp_path / "shy.py").chmod(0o644)

    read = client.get(
        "/api/review/threads",
        params={"snapshot_id": snapshot_id, "page": 1, "limit": 20},
    )
    assert read.status_code == 200
    landings = {
        thread["origin_target"]["file"]["right_path"]: thread["placement"]
        for thread in read.json()["threads"]
    }
    assert landings == {
        # Every coordinate this File could offer describes dirdiff's own
        # placeholder text, so the Thread lands nowhere.
        "shy.py": {"kind": "file-unreadable"},
        # The unreadable File cost this one nothing: same Snapshot, same read,
        # exact original range.
        "loud.py": {
            "kind": "region-kept",
            "range": {"start_line": 2, "end_line": 2},
        },
    }


def test_symlink_loop_stops_before_loading_a_link_twice(tmp_path: Path) -> None:
    """Capture one finite synthetic target from a real repository link loop.

    Each visited link must appear exactly once and the repeated path must become
    the immediate terminal diagnosis. Reaching through the API proves the
    iterative walk, Snapshot sidecars, and composer agree; a timeout-based test
    would only prove that an arbitrary limit eventually fired.
    """
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "old-target.txt").write_text("old target\n", encoding="utf-8")
    os.symlink("old-target.txt", tmp_path / "portal")
    run_git(tmp_path, "add", "old-target.txt", "portal")
    run_git(tmp_path, "commit", "-m", "safe link")
    (tmp_path / "portal").unlink()
    os.symlink("hop", tmp_path / "portal")
    os.symlink("portal", tmp_path / "hop")

    client, project_id = create_repo_client(tmp_path)
    manifest = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    assert manifest.status_code == 200
    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": manifest.json()["snapshot_id"],
            "engine": "dirdiff",
            "left_path": "portal",
            "right_path": "portal",
        },
    )

    assert diff.status_code == 200
    bays = diff.json()["frames"][0]["bays"]
    assert [bay["bay_key"] for bay in bays] == [
        "symlink",
        "symlink-target",
    ]
    link = bays[0]["kind_data"]
    assert link["kind"] == "text"
    assert [
        row["right_text"]
        for row in link["rows"]
        if row["right_text"] not in (None, "")
    ] == ["hop"]
    target = bays[1]["kind_data"]
    assert target["kind"] == "text"
    right_lines = [
        row["right_text"]
        for row in target["rows"]
        if row["right_text"] not in (None, "")
    ]
    assert right_lines == [
        "# %% hop",
        "portal",
        "# loop: portal was already visited",
    ]
    assert right_lines.count("# %% hop") == 1


def test_file_media_serves_captured_symlink_target_images(
    tmp_path: Path,
) -> None:
    """Serve image targets only through relationally named link sidecars.

    The media coordinate belongs to the outer link File, while its response is
    the exact final target bytes retained during capture. Changing the live
    targets after the manifest must not alter either answer. Moving the right
    sidecars away from their publication names and updating only their database
    paths proves readers use relational authority rather than filename probes.
    """
    fixtures = Path(__file__).parents[1] / "presets" / "formats" / "images"
    old_png = (fixtures / "image-changed" / "old.png").read_bytes()
    new_png = (fixtures / "image-changed" / "new.png").read_bytes()
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "old.png").write_bytes(old_png)
    (tmp_path / "new.png").write_bytes(new_png)
    os.symlink("old.png", tmp_path / "logo")
    run_git(tmp_path, "add", "old.png", "new.png", "logo")
    run_git(tmp_path, "commit", "-m", "linked image")
    (tmp_path / "logo").unlink()
    os.symlink("new.png", tmp_path / "logo")

    client, project_id = create_repo_client(tmp_path)
    manifest = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    assert manifest.status_code == 200
    snapshot_id = manifest.json()["snapshot_id"]

    database_path = tmp_path.parent / f".{tmp_path.name}-dirdiff-test.sqlite"
    database = create_engine(f"sqlite:///{database_path}")
    schema = MetaData()
    schema.reflect(bind=database, only=["snapshot_file_symlink"])
    symlinks = schema.tables["snapshot_file_symlink"]
    with database.begin() as connection:
        rows = connection.execute(
            select(
                symlinks.c.file_id,
                symlinks.c.side,
                symlinks.c.metadata_path,
                symlinks.c.metadata_hash,
                symlinks.c.target_capture_path,
                symlinks.c.target_hash,
            )
        ).all()
        assert {row.side for row in rows} == {"left", "right"}
        for row in rows:
            metadata_path = Path(row.metadata_path)
            assert metadata_path.is_absolute()
            assert hashlib.sha256(metadata_path.read_bytes()).digest() == (
                row.metadata_hash
            )
            assert b"target_digest" not in metadata_path.read_bytes()
            assert row.target_capture_path is not None
            assert row.target_hash is not None
            target_capture_path = Path(row.target_capture_path)
            assert target_capture_path.is_absolute()
            assert hashlib.sha256(
                target_capture_path.read_bytes()
            ).digest() == (row.target_hash)

        right_row = next(row for row in rows if row.side == "right")
        moved_metadata = Path(right_row.metadata_path).with_name(
            "database-named-metadata"
        )
        moved_target = Path(right_row.target_capture_path).with_name(
            "database-named-target"
        )
        Path(right_row.metadata_path).rename(moved_metadata)
        Path(right_row.target_capture_path).rename(moved_target)
        connection.execute(
            symlinks.update()
            .where(
                symlinks.c.file_id == right_row.file_id,
                symlinks.c.side == "right",
            )
            .values(
                metadata_path=str(moved_metadata),
                target_capture_path=str(moved_target),
            )
        )
    database.dispose()

    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": "dirdiff",
            "left_path": "logo",
            "right_path": "logo",
        },
    )
    assert diff.status_code == 200
    target_bay = diff.json()["frames"][0]["bays"][1]
    assert target_bay["bay_key"] == "symlink-target"
    assert target_bay["kind_data"]["kind"] == "image"

    (tmp_path / "old.png").write_bytes(b"later old bytes")
    (tmp_path / "new.png").write_bytes(b"later new bytes")
    for side, expected in (("left", old_png), ("right", new_png)):
        served = client.get(
            "/api/file-media",
            params={
                "snapshot_id": snapshot_id,
                "bay_key": "symlink-target",
                "side": side,
                "left_path": "logo",
                "right_path": "logo",
            },
        )
        assert served.status_code == 200, side
        assert served.content == expected, side
        assert served.headers["content-type"] == "image/png", side

    moved_metadata.write_bytes(moved_metadata.read_bytes() + b" ")
    damaged = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": "dirdiff",
            "left_path": "logo",
            "right_path": "logo",
        },
    )
    assert damaged.status_code == 500


def test_symlink_target_cannot_escape_the_repository(tmp_path: Path) -> None:
    """Stop a real worktree link before it can read a parent-directory File.

    The outside File deliberately exists and contains recognizable bytes. The
    composed result must retain only the raw link and explicit jail diagnosis,
    proving failure came from the repository boundary rather than a missing
    target that happened to be harmless.
    """
    outside = tmp_path.parent / f"{tmp_path.name}-outside-secret.txt"
    outside.write_text("DO NOT CAPTURE THIS\n", encoding="utf-8")
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "inside.txt").write_text("inside\n", encoding="utf-8")
    os.symlink("inside.txt", tmp_path / "portal")
    run_git(tmp_path, "add", "inside.txt", "portal")
    run_git(tmp_path, "commit", "-m", "inside link")
    (tmp_path / "portal").unlink()
    os.symlink(f"../{outside.name}", tmp_path / "portal")

    client, project_id = create_repo_client(tmp_path)
    manifest = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    assert manifest.status_code == 200
    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": manifest.json()["snapshot_id"],
            "engine": "dirdiff",
            "left_path": "portal",
            "right_path": "portal",
        },
    )

    assert diff.status_code == 200
    bays = diff.json()["frames"][0]["bays"]
    assert [bay["bay_key"] for bay in bays] == [
        "symlink",
        "symlink-target",
    ]
    link = bays[0]["kind_data"]
    assert link["kind"] == "text"
    right_link_text = "\n".join(
        row["right_text"]
        for row in link["rows"]
        if row["right_text"] not in (None, "")
    )
    assert right_link_text == f"../{outside.name}"
    target = bays[1]["kind_data"]
    assert target["kind"] == "text"
    right_target_text = "\n".join(
        row["right_text"]
        for row in target["rows"]
        if row["right_text"] not in (None, "")
    )
    assert (
        right_target_text == "# stopped: Repo path must stay inside the repo."
    )
    assert "DO NOT CAPTURE THIS" not in json.dumps(diff.json())


def test_file_media_serves_each_captured_side_exactly(tmp_path: Path) -> None:
    """Serve the captured bytes themselves, and refuse what there are none of.

    The whole point of the endpoint is that the reviewer sees the picture the
    Snapshot holds, so the assertions compare the response body against the
    fixture on disk byte for byte rather than checking a length or a prefix.
    The refusals matter just as much: a side that was never captured and a
    File that composes no media at all must both be errors, because either one
    answered with empty bytes would render as a broken image.
    """
    formats = Path(__file__).parents[1] / "presets" / "formats"
    old_png = (formats / "images" / "image-changed" / "old.png").read_bytes()
    new_png = (formats / "images" / "image-changed" / "new.png").read_bytes()
    old_ogg = (
        formats / "unsupported" / "blob-content-changed" / "old.ogg"
    ).read_bytes()

    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "logo.png").write_bytes(old_png)
    (tmp_path / "gone.png").write_bytes(old_png)
    (tmp_path / "clip.ogg").write_bytes(old_ogg)
    (tmp_path / "untouched.png").write_bytes(old_png)
    run_git(
        tmp_path, "add", "logo.png", "gone.png", "clip.ogg", "untouched.png"
    )
    run_git(tmp_path, "commit", "-m", "assets")
    (tmp_path / "logo.png").write_bytes(new_png)
    (tmp_path / "gone.png").unlink()
    (tmp_path / "clip.ogg").unlink()
    # Touched so the text File is captured too: the Snapshot only holds what
    # changed, and the endpoint must have a text bay to refuse.
    (tmp_path / "alpha.txt").write_text("one\ntwo\n", encoding="utf-8")

    client, project_id = create_repo_client(tmp_path)
    manifest = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    assert manifest.status_code == 200
    snapshot_id = manifest.json()["snapshot_id"]

    # The composed diff is where a widget learns a side exists at all, so the
    # references it carries must describe the same bytes the endpoint serves.
    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": "dirdiff",
            "left_path": "logo.png",
            "right_path": "logo.png",
        },
    )
    assert diff.status_code == 200
    image_bay = diff.json()["frames"][0]["bays"][0]
    assert image_bay["kind_data"]["kind"] == "image"
    assert image_bay["bay_key"] == "image"
    assert image_bay["kind_data"]["left"] == {
        "media_type": "image/png",
        "byte_size": len(old_png),
        "digest": hashlib.sha256(old_png).hexdigest(),
    }
    assert image_bay["kind_data"]["right"] == {
        "media_type": "image/png",
        "byte_size": len(new_png),
        "digest": hashlib.sha256(new_png).hexdigest(),
    }

    for side, expected in (("left", old_png), ("right", new_png)):
        served = client.get(
            "/api/file-media",
            params={
                "snapshot_id": snapshot_id,
                "bay_key": "image",
                "side": side,
                "left_path": "logo.png",
                "right_path": "logo.png",
            },
        )
        assert served.status_code == 200, side
        assert served.content == expected, side
        assert served.headers["content-type"] == "image/png", side
        # A Snapshot id is never reused, so one address always names one
        # answer and the response says so outright.
        assert served.headers["cache-control"] == (
            "private, max-age=31536000, immutable"
        ), side

    # A removed image is captured on one side, and that side is served under
    # the media type composition concluded.
    removed = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "bay_key": "image",
            "side": "left",
            "left_path": "gone.png",
        },
    )
    assert removed.status_code == 200
    assert removed.content == old_png
    assert removed.headers["content-type"] == "image/png"

    absent = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "bay_key": "image",
            "side": "right",
            "left_path": "gone.png",
        },
    )
    assert absent.status_code == 400
    assert "has no image on the right side" in absent.text

    # A blob File composes its facts as rows and no bay carrying bytes, so
    # there is nothing here to serve: the endpoint refuses rather than
    # inventing a download route of its own.
    blob = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "bay_key": "blob",
            "side": "left",
            "left_path": "clip.ogg",
        },
    )
    assert blob.status_code == 400
    assert "does not carry media content" in blob.text

    # A text File composes a text bay, which holds no bytes to serve. The
    # endpoint says so rather than returning the source as a download.
    textual = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "bay_key": "flatfile",
            "side": "left",
            "left_path": "alpha.txt",
            "right_path": "alpha.txt",
        },
    )
    assert textual.status_code == 400
    assert "does not carry media content" in textual.text

    # An image the Snapshot never captured is not readable through it, even
    # though it sits in the repository and would have composed a media bay.
    unchanged = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "bay_key": "image",
            "side": "left",
            "left_path": "untouched.png",
            "right_path": "untouched.png",
        },
    )
    assert unchanged.status_code == 400
    assert "Snapshot manifest path is missing." in unchanged.text

    unknown_snapshot = client.get(
        "/api/file-media",
        params={
            "snapshot_id": "0" * 32,
            "bay_key": "image",
            "side": "left",
            "left_path": "logo.png",
            "right_path": "logo.png",
        },
    )
    assert unknown_snapshot.status_code == 400

    # A path that climbs out of the repository is refused where every other
    # File address is, which is what stops the endpoint from becoming a way to
    # read the machine it runs on.
    outside = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "bay_key": "image",
            "side": "left",
            "left_path": "../escape.png",
            "right_path": "../escape.png",
        },
    )
    assert outside.status_code == 400
    assert "must be normalized relative names" in outside.text


def test_file_media_selects_a_notebook_image_by_bay_key(
    tmp_path: Path,
) -> None:
    """Serve each embedded PNG from the exact output bay that named it.

    One notebook contains two image outputs whose bytes swap across sides. The
    File pair and side are therefore insufficient by construction: only the bay
    key distinguishes the correct response. The route also refuses a missing or
    unknown key instead of choosing the first image.
    """
    fixtures = Path(__file__).parents[1] / "presets" / "formats" / "images"
    first_png = (fixtures / "image-changed" / "old.png").read_bytes()
    second_png = (fixtures / "image-changed" / "new.png").read_bytes()
    encoded_first = base64.b64encode(first_png).decode("ascii")
    encoded_second = base64.b64encode(second_png).decode("ascii")
    old_notebook = {
        "cells": [
            {
                "cell_type": "code",
                "id": "plot",
                "execution_count": 1,
                "metadata": {},
                "source": ["draw()\n"],
                "outputs": [
                    {
                        "output_type": "display_data",
                        "data": {
                            "image/png": encoded_first,
                            "text/plain": ["<first>"],
                        },
                        "metadata": {},
                    },
                    {
                        "output_type": "display_data",
                        "data": {
                            "image/png": encoded_second,
                            "text/plain": ["<second>"],
                        },
                        "metadata": {},
                    },
                ],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    new_notebook = {
        "cells": [
            {
                "cell_type": "code",
                "id": "plot",
                "execution_count": 1,
                "metadata": {},
                "source": ["draw()\n"],
                "outputs": [
                    {
                        "output_type": "display_data",
                        "data": {
                            "image/png": encoded_second,
                            "text/plain": ["<first>"],
                        },
                        "metadata": {},
                    },
                    {
                        "output_type": "display_data",
                        "data": {
                            "image/png": encoded_first,
                            "text/plain": ["<second>"],
                        },
                        "metadata": {},
                    },
                ],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }

    create_committed_repo(tmp_path, branch="main")
    notebook_path = tmp_path / "plots.ipynb"
    notebook_path.write_text(json.dumps(old_notebook), encoding="utf-8")
    run_git(tmp_path, "add", "plots.ipynb")
    run_git(tmp_path, "commit", "-m", "notebook plots")
    notebook_path.write_text(json.dumps(new_notebook), encoding="utf-8")

    client, project_id = create_repo_client(tmp_path)
    manifest = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
            "tab": "refs",
            "left": "index",
            "right": "worktree",
        },
    )
    assert manifest.status_code == 200
    snapshot_id = manifest.json()["snapshot_id"]
    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": "dirdiff",
            "left_path": "plots.ipynb",
            "right_path": "plots.ipynb",
        },
    )
    assert diff.status_code == 200
    image_bays = {
        bay["bay_key"]: bay
        for frame in diff.json()["frames"]
        for bay in frame["bays"]
        if bay["kind_data"]["kind"] == "image"
    }
    assert set(image_bays) == {"plot:output:0", "plot:output:1"}

    expected = {
        ("plot:output:0", "left"): first_png,
        ("plot:output:0", "right"): second_png,
        ("plot:output:1", "left"): second_png,
        ("plot:output:1", "right"): first_png,
    }
    for (bay_key, side), png in expected.items():
        served = client.get(
            "/api/file-media",
            params={
                "snapshot_id": snapshot_id,
                "bay_key": bay_key,
                "side": side,
                "left_path": "plots.ipynb",
                "right_path": "plots.ipynb",
            },
        )
        assert served.status_code == 200, (bay_key, side)
        assert served.content == png, (bay_key, side)
        assert served.headers["content-type"] == "image/png"

    unknown = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "bay_key": "plot:output:missing",
            "side": "right",
            "left_path": "plots.ipynb",
            "right_path": "plots.ipynb",
        },
    )
    assert unknown.status_code == 400
    assert "has no bay named" in unknown.text
    missing_key = client.get(
        "/api/file-media",
        params={
            "snapshot_id": snapshot_id,
            "side": "right",
            "left_path": "plots.ipynb",
            "right_path": "plots.ipynb",
        },
    )
    assert missing_key.status_code == 422


def test_agent_addresses_an_image_bay_by_its_single_pseudo_line(
    tmp_path: Path,
) -> None:
    """A non-text bay accepts `1..1` and nothing else.

    The one line it exposes is not source: it is the sentence describing the
    captured bytes, so a Thread on a picture stores an excerpt that still says
    something when the picture is gone. Every other range names a line that
    does not exist, and must be refused rather than clamped -- a stale target
    from a Snapshot where the File was text would otherwise land silently on
    the digest sentence.
    """
    fixtures = Path(__file__).parents[1] / "presets" / "formats" / "images"
    old_png = (fixtures / "image-changed" / "old.png").read_bytes()
    new_png = (fixtures / "image-changed" / "new.png").read_bytes()

    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "logo.png").write_bytes(old_png)
    run_git(tmp_path, "add", "logo.png")
    run_git(tmp_path, "commit", "-m", "logo")
    (tmp_path / "logo.png").write_bytes(new_png)
    client, _project_id = create_repo_client(tmp_path)

    joined = client.post(
        "/api/agent/join_review",
        json={
            "agent_uuid": "e" * 32,
            "name": "image reviewer",
            "tab": {"kind": "head", "repo_path": str(tmp_path)},
        },
    ).json()

    # The agent finds the captured side by its bytes, as it must: a File-id
    # directory carries no repository path, and these bytes are not text.
    captured = [
        side
        for side in sorted(Path(joined["snapshot_path"]).glob("*/right"))
        if side.read_bytes() == new_png
    ]
    assert len(captured) == 1
    image_side = captured[0]

    created = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(image_side),
                    "bay": {
                        "bay_key": "image",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "the logo changed",
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
    assert body["bay"] == {"bay_key": "image", "start_line": 1, "end_line": 1}
    excerpt = body["original_excerpt"]
    assert excerpt["lines"] == [
        f"image/png, {len(new_png)} bytes, "
        f"sha256 {hashlib.sha256(new_png).hexdigest()}"
    ]
    assert excerpt["start_line"] == 1
    assert excerpt["selected_start_line"] == 1
    assert excerpt["selected_end_line"] == 1

    for start_line, end_line in ((1, 2), (2, 2)):
        rejected = client.post(
            "/api/agent/actions",
            json={
                "snapshot_id": joined["snapshot_id"],
                "profile_id": joined["profile_id"],
                "actions": [
                    {
                        "kind": "create-finding",
                        "file": str(image_side),
                        "bay": {
                            "bay_key": "image",
                            "start_line": start_line,
                            "end_line": end_line,
                        },
                        "body": "past the one line there is",
                    },
                ],
            },
        )
        assert rejected.status_code == 400, (start_line, end_line)
        assert "A non-text bay accepts only the single line 1 to 1." in (
            rejected.text
        ), (start_line, end_line)

    # An image File composes no `flatfile` bay, so the key a text File would
    # have carried names a coordinate that no longer exists.
    stale = client.post(
        "/api/agent/actions",
        json={
            "snapshot_id": joined["snapshot_id"],
            "profile_id": joined["profile_id"],
            "actions": [
                {
                    "kind": "create-finding",
                    "file": str(image_side),
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "wrong bay",
                },
            ],
        },
    )
    assert stale.status_code == 400
    assert "Unknown rendered bay." in stale.text
