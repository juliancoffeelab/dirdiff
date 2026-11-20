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
    # Persistent dirdiff state is not valid worktree input. Keep this API
    # fixture outside the reviewed repository, as production configuration must.
    engine = open_sqlite_engine(
        repo_path.parent / f".{repo_path.name}-dirdiff-test.sqlite",
        Path(__file__).parents[2] / "alembic.ini",
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
                agent_skills_root=Path(__file__).parents[2]
                / ".agents"
                / "skills",
                room_lord=room_lord,
                store_path=repo_path.parent
                / f".{repo_path.name}-dirdiff-test-store",
                presets_root=str(
                    Path(__file__).parents[2] / "tests" / "presets"
                ),
            )
        ),
        mark.id,
    )


def run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def create_committed_repo(repo_path: Path, *, branch: str) -> None:
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
    source_repo = tmp_path / source_name
    source_repo.mkdir()
    create_committed_repo(source_repo, branch=branch)
    run_git(tmp_path, "clone", "--bare", str(source_repo), bare_name)
    run_git(tmp_path, "clone", str(tmp_path / bare_name), worktree_name)
    return tmp_path / worktree_name


def test_historical_file_review_migration_never_reads_captured_text(
    tmp_path: Path,
) -> None:
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
            agent_skills_root=Path(__file__).parents[2] / ".agents" / "skills",
            store_path=tmp_path / "store",
            room_lord=RoomLord(RoomStore(current_engine), tmp_path / "store"),
            presets_root=str(Path(__file__).parents[2] / "tests" / "presets"),
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


def test_browser_can_edit_a_comment_twice_then_delete_it(
    tmp_path: Path,
) -> None:
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "alpha.txt").write_text("one\nchanged\n", encoding="utf-8")
    client, _project_id = create_repo_client(tmp_path)

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "1" * 32,
            "name": "comment author",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    captured = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()
    created = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "alpha.txt",
                    "side": "right",
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 2,
                        "end_line": 2,
                    },
                    "body": "original",
                }
            ],
        },
    )
    assert created.status_code == 200
    threads = client.get(
        "/api/review/threads",
        params={
            "snapshot_id": captured["snapshot_id"],
            "page": 1,
            "limit": 20,
        },
    ).json()["threads"]
    assert len(threads) == 1
    comment_id = threads[0]["comments"][0]["comment_id"]

    for revision, body in enumerate(("first edit", "second edit"), start=1):
        edited = client.post(
            "/api/review/edit_comment",
            json={
                "snapshot_id": captured["snapshot_id"],
                "comment_id": comment_id,
                "profile_id": profile["profile_id"],
                "body": body,
            },
        )
        assert edited.status_code == 200
        comment = edited.json()["comment"]
        assert comment["revision"] == revision
        assert comment["body"] == body
        assert comment["deleted"] is False

    deleted = client.post(
        "/api/review/delete_comment",
        json={
            "snapshot_id": captured["snapshot_id"],
            "comment_id": comment_id,
            "profile_id": profile["profile_id"],
        },
    )
    assert deleted.status_code == 200
    comment = deleted.json()["comment"]
    assert comment["revision"] == 3
    assert comment["body"] is None
    assert comment["deleted"] is True


def test_repo_main_branch_save_overrides_repo_defaults(
    tmp_path: Path,
) -> None:
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
        "git_algorithm": "myers",
        "git_ignore_space_change": False,
        "git_word_diff": False,
        "git_word_boundary": "default",
        "git_word_diff_regex": None,
        "git_color_moved": "no",
        "git_color_moved_ws": "no",
        "difftastic_ignore_comments": False,
        "gumtree_matcher": "gumtree-simple",
    }
    assert second_preferences.status_code == 200
    assert second_preferences.json() == {
        "user_profile_id": second_user["id"],
        "aggressive_folds": True,
        "git_algorithm": "myers",
        "git_ignore_space_change": False,
        "git_word_diff": False,
        "git_word_boundary": "default",
        "git_word_diff_regex": None,
        "git_color_moved": "no",
        "git_color_moved_ws": "no",
        "difftastic_ignore_comments": False,
        "gumtree_matcher": "gumtree-simple",
    }

    update_response = client.patch(
        f"/api/user-profile/{first_user['id']}/preferences",
        json={
            "aggressive_folds": False,
            "git_algorithm": "myers",
            "git_ignore_space_change": False,
            "git_word_diff": True,
            "git_word_boundary": "custom",
            "git_word_diff_regex": "[[:alnum:]_]+",
            "git_color_moved": "no",
            "git_color_moved_ws": "no",
            "difftastic_ignore_comments": True,
            "gumtree_matcher": "gumtree-classic",
        },
    )
    reloaded_second_preferences = client.get(
        f"/api/user-profile/{second_user['id']}/preferences"
    )

    assert update_response.status_code == 200
    assert update_response.json() == {
        "user_profile_id": first_user["id"],
        "aggressive_folds": False,
        "git_algorithm": "myers",
        "git_ignore_space_change": False,
        "git_word_diff": True,
        "git_word_boundary": "custom",
        "git_word_diff_regex": "[[:alnum:]_]+",
        "git_color_moved": "no",
        "git_color_moved_ws": "no",
        "difftastic_ignore_comments": True,
        "gumtree_matcher": "gumtree-classic",
    }
    assert reloaded_second_preferences.status_code == 200
    assert reloaded_second_preferences.json() == {
        "user_profile_id": second_user["id"],
        "aggressive_folds": True,
        "git_algorithm": "myers",
        "git_ignore_space_change": False,
        "git_word_diff": False,
        "git_word_boundary": "default",
        "git_word_diff_regex": None,
        "git_color_moved": "no",
        "git_color_moved_ws": "no",
        "difftastic_ignore_comments": False,
        "gumtree_matcher": "gumtree-simple",
    }


def test_repo_defaults_reports_unresolved_base_when_remote_head_is_missing(
    tmp_path: Path,
) -> None:
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
    repo_path = tmp_path / "worktree"
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


def test_branch_review_query_validation_returns_unprocessable_entity(
    tmp_path: Path,
) -> None:
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
            "selection": json.dumps(
                {
                    "tab": "branch-review",
                    "project_id": project_id,
                    "base": {"branch": "master"},
                    "review": {"source": "local", "branch": "feature"},
                }
            )
        },
    )

    assert response.status_code == 422
    assert any(
        error["type"] == "missing" and error["loc"][-1] == "source"
        for error in response.json()["detail"]
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
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
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
                "file_kind": {"type": "tracked", "status": "modified"},
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
        "file_kind": {"type": "tracked", "status": "modified"},
    }

    response = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": '{"engine":"dirdiff"}',
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["display_name"] == "Cargo.lock"
    assert payload["file_kind"] == {"type": "tracked", "status": "modified"}
    bay = payload["frames"][0]["bays"][0]
    assert bay["kind_data"]["kind"] == "text"
    assert bay["bay_key"] == "flatfile"
    assert bay["kind_data"]["rows"] != []

    reloaded_manifest_response = client.get(
        "/api/manifest",
        params={
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
        },
    )
    reloaded_snapshot_id = reloaded_manifest_response.json()["snapshot_id"]

    assert reloaded_manifest_response.status_code == 200
    assert reloaded_snapshot_id == snapshot_id

    repeated_response = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": '{"engine":"dirdiff"}',
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
        },
    )

    assert repeated_response.status_code == 200


def test_preset_manifest_and_file_diff_do_not_require_a_mark(
    tmp_path: Path,
) -> None:
    engine = open_sqlite_engine(
        tmp_path / "dirdiff.sqlite", Path(__file__).parents[2] / "alembic.ini"
    )
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            agent_skills_root=Path(__file__).parents[2] / ".agents" / "skills",
            store_path=tmp_path / "store",
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets"),
        )
    )

    manifest_response = client.get(
        "/api/manifest",
        params={
            "selection": json.dumps(
                {"tab": "preset", "catalog": "diff", "subset": "python"}
            )
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
            "engine": '{"engine":"dirdiff"}',
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
    engine = open_sqlite_engine(
        tmp_path / "dirdiff.sqlite", Path(__file__).parents[2] / "alembic.ini"
    )
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            agent_skills_root=Path(__file__).parents[2] / ".agents" / "skills",
            store_path=tmp_path / "store",
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
                "selection": json.dumps(
                    {
                        "tab": "preset",
                        "catalog": project_id,
                        "subset": preset_subset,
                    }
                )
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["display_name"] == preset_subset
        assert isinstance(payload["snapshot_id"], str)
        assert payload["snapshot_id"] != ""
        assert payload["tree"] != []


def test_scroll_preset_can_force_compact_files_lazy(tmp_path: Path) -> None:
    engine = open_sqlite_engine(
        tmp_path / "dirdiff.sqlite", Path(__file__).parents[2] / "alembic.ini"
    )
    client = TestClient(
        create_app(
            RepoMarkStore(engine),
            UserProfileStore(engine),
            agent_skills_root=Path(__file__).parents[2] / ".agents" / "skills",
            store_path=tmp_path / "store",
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets"),
        )
    )

    manifest_response = client.get(
        "/api/manifest",
        params={
            "selection": json.dumps(
                {"tab": "preset", "catalog": "scroll", "subset": "lazy-files"}
            )
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
    engine = open_sqlite_engine(
        tmp_path / "dirdiff.sqlite", Path(__file__).parents[2] / "alembic.ini"
    )
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
            agent_skills_root=Path(__file__).parents[2] / ".agents" / "skills",
            store_path=tmp_path / "store",
            room_lord=RoomLord(RoomStore(engine), tmp_path / "store"),
            presets_root=str(Path.cwd() / "tests" / "presets"),
        )
    )

    missing_subset = client.get(
        "/api/manifest",
        params={"selection": json.dumps({"tab": "preset", "catalog": "diff"})},
    )
    missing_project = client.get(
        "/api/manifest",
        params={"selection": json.dumps({"tab": "preset", "subset": "python"})},
    )
    traversal = client.get(
        "/api/manifest",
        params={
            "selection": json.dumps(
                {"tab": "preset", "catalog": "diff", "subset": "../python"}
            )
        },
    )

    assert missing_subset.status_code == 422
    assert missing_subset.json()["detail"][0]["loc"][-1] == "subset"
    assert missing_project.status_code == 422
    assert missing_project.json()["detail"][0]["loc"][-1] == "catalog"
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
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
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
                "file_kind": {"type": "tracked", "status": "deleted"},
            },
        }
    ]


def test_agent_batch_applies_set_reads_across_threads(tmp_path: Path) -> None:
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "alpha.txt").write_text("one\nchanged\n", encoding="utf-8")
    client, project_id = create_repo_client(tmp_path)

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "a" * 32,
            "name": "batch reviewer",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    captured = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()
    assert captured["project_id"] == project_id
    assert [
        (file["right_path"], file["capture_error"])
        for file in captured["files"]
    ] == [("alpha.txt", None)]

    created = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "alpha.txt",
                    "side": "right",
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": line,
                        "end_line": line,
                    },
                    "body": f"finding {line}",
                }
                for line in (1, 2)
            ],
        },
    )
    assert created.status_code == 200
    thread_ids = [result["thread_id"] for result in created.json()["results"]]
    assert len(set(thread_ids)) == 2
    assert [
        (result["kind"], result["status"], result["waiting_on"])
        for result in created.json()["results"]
    ] == [("create-thread", "open", "author")] * 2

    # One follow-up batch addresses both existing Threads. The bulk history
    # read must fold each Thread independently and in authored order: the
    # reviewer comment on the second Thread is only meaningful because the
    # author comment before it moved that Thread's wait to the reviewer.
    replied = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "comment",
                    "thread_id": thread_ids[0],
                    "role": "author",
                    "body": "first response",
                },
                {
                    "kind": "comment",
                    "thread_id": thread_ids[1],
                    "role": "author",
                    "body": "second response",
                },
                {
                    "kind": "comment",
                    "thread_id": thread_ids[1],
                    "role": "reviewer",
                    "body": "returned",
                },
                {
                    "kind": "comment",
                    "thread_id": thread_ids[0],
                    "role": "author",
                    "body": "extra context",
                },
            ],
        },
    )
    assert replied.status_code == 200
    outcomes = [
        (result["kind"], result["status"], result["waiting_on"])
        for result in replied.json()["results"]
    ]
    assert outcomes == [
        ("comment", "open", "reviewer"),
        ("comment", "open", "reviewer"),
        ("comment", "open", "author"),
        ("comment", "open", "reviewer"),
    ]

    # Changing the worktree and capturing again publishes a new Snapshot whose
    # capture must relocate both range Threads into the fresh capture (the
    # derivation reads the new Snapshot's own bytes at their final address).
    (tmp_path / "alpha.txt").write_text(
        "one\nchanged\nagain\n", encoding="utf-8"
    )
    recaptured = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    )
    assert recaptured.status_code == 200
    assert recaptured.json()["snapshot_id"] != captured["snapshot_id"]
    threads = client.get(
        "/api/agent/threads",
        params={
            "snapshot_id": recaptured.json()["snapshot_id"],
            "status": "open",
            "page": 1,
            "limit": 20,
        },
    ).json()
    assert sorted(item["thread_id"] for item in threads["items"]) == sorted(
        thread_ids
    )


def test_agent_profile_is_idempotent_and_normalizes_hyphenated_uuid(
    tmp_path: Path,
) -> None:
    create_committed_repo(tmp_path, branch="main")
    client, _project_id = create_repo_client(tmp_path)

    first = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "name": "hyphenated reviewer",
            "model": "model-one",
            "harness": "harness-one",
        },
    )
    assert first.status_code == 200

    again = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa",
            "name": "renamed reviewer",
            "model": "model-two",
            "harness": "harness-two",
        },
    )
    assert again.status_code == 200
    assert again.json() == {
        "profile_id": first.json()["profile_id"],
        "name": "renamed reviewer",
        "model": "model-two",
        "harness": "harness-two",
    }

    other = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "b" * 32,
            "name": "renamed reviewer",
            "model": "model-two",
            "harness": "harness-two",
        },
    )
    assert other.status_code == 400
    assert other.text == "Username already exists."


def test_agent_batch_failure_commits_no_rows(tmp_path: Path) -> None:
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "alpha.txt").write_text("one\nchanged\n", encoding="utf-8")
    client, _project_id = create_repo_client(tmp_path)

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "b" * 32,
            "name": "atomic reviewer",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    captured = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()

    rejected = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "alpha.txt",
                    "side": "right",
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "valid finding",
                },
                {
                    "kind": "comment",
                    "thread_id": "f" * 32,
                    "role": "author",
                    "body": "reply to a Thread that does not exist",
                },
            ],
        },
    )
    assert rejected.status_code == 400

    threads = client.get(
        "/api/agent/threads",
        params={
            "snapshot_id": captured["snapshot_id"],
            "status": "all",
            "page": 1,
            "limit": 20,
        },
    ).json()
    assert threads["items"] == []


def test_agent_batch_with_absent_paths_is_rejected(
    tmp_path: Path,
) -> None:
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "alpha.txt").write_text("one\nchanged\n", encoding="utf-8")
    client, _project_id = create_repo_client(tmp_path)

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "c" * 32,
            "name": "ordering reviewer",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    captured = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()

    # Two absent (but well-formed) repository paths: the route currently
    # reports the first one it meets while preparing the batch.
    rejected = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "missing-first.txt",
                    "side": "right",
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "targets a missing File",
                },
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "missing-second.txt",
                    "side": "right",
                    "bay": {
                        "bay_key": "flatfile",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    "body": "targets another missing File",
                },
            ],
        },
    )
    assert rejected.status_code == 400
    assert "missing-first.txt" in rejected.text
    assert "missing-second.txt" not in rejected.text

    # A creation that is doubly invalid (absent target and blank body)
    # reports the absent target.
    doubly_invalid = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "missing-first.txt",
                    "side": "right",
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
    assert "missing-first.txt" in doubly_invalid.text


def test_agent_addresses_a_notebook_cell_bay_in_both_directions(
    tmp_path: Path,
) -> None:
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

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "d" * 32,
            "name": "notebook reviewer",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    captured = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()

    # The manifest names the notebook by repository path and locates its
    # captured bytes, so the agent reads the notebook like any other side.
    [notebook_file] = [
        file for file in captured["files"] if file["right_path"] == "demo.ipynb"
    ]
    notebook_side = Path(notebook_file["right_file"])

    # Line two of the cell's joined source is `b = 22`. Line two of the
    # captured `.ipynb` is JSON structure, which is what the old flatfile
    # hardcode silently addressed instead.
    assert notebook_side.read_text(encoding="utf-8").splitlines()[
        1
    ].strip() != ("b = 22")
    created = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "demo.ipynb",
                    "side": "right",
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

    read = client.get(f"/api/agent/thread/{thread_id}")
    assert read.status_code == 200
    body = read.json()
    assert body["location"] == {
        "path": "demo.ipynb",
        "side": "right",
        "bay_key": "stable-cell",
        "start_line": 2,
        "end_line": 2,
    }
    # The excerpt is cut from the cell's own text, so it holds the cell source
    # rather than a window of the surrounding `.ipynb` JSON.
    excerpt = body["original_excerpt"]
    assert excerpt["lines"] == ["a = 1", "b = 22", "c = 3"]
    assert excerpt["start_line"] == 1
    assert excerpt["selected_start_line"] == 2
    assert excerpt["selected_end_line"] == 2

    summary = client.get(
        "/api/agent/threads",
        params={
            "snapshot_id": captured["snapshot_id"],
            "status": "open",
            "page": 1,
            "limit": 20,
        },
    ).json()
    assert [
        (item["location"]["bay_key"], item["location"]["start_line"])
        for item in summary["items"]
    ] == [("stable-cell", 2)]

    # A notebook composes no `flatfile` bay, so the key the agent boundary
    # used to hardcode is now rejected rather than silently mis-addressed.
    rejected = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "demo.ipynb",
                    "side": "right",
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
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "demo.ipynb",
                    "side": "right",
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

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "e" * 32,
            "name": "landing reviewer",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    joined = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()
    created = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": joined["snapshot_id"],
                    "role": "reviewer",
                    "path": "demo.ipynb",
                    "side": "right",
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
    # (label, notebook bytes or None to delete the file, the expected
    # (browser placement, agent bay, agent outdated reason) of the derived
    # landing). The two boundaries are stated separately because the agent
    # shape keeps five reason names for eight placement kinds.
    losses: list[
        tuple[
            str,
            bytes | None,
            tuple[dict[str, JsonValue], str, str],
        ]
    ] = [
        # The origin's own bay is gone; the File's first right-carrying bay
        # is the unchanged first cell, chosen and stored at derivation.
        (
            "lost origin bay",
            json.dumps({**notebook, "cells": [cell_a]}, indent=1).encode(),
            (
                {"kind": "bay-lost", "bay": {"bay_key": "cell-a"}},
                "cell-a",
                "bay_not_found",
            ),
        ),
        # The bay survives while nothing inside it matches the origin region.
        (
            "lost region",
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
                "cell-b",
                "region_not_found",
            ),
        ),
        # A claimed notebook whose JSON cannot be read preserves its raw
        # text in the notebook boundary, so the cell origin lands on that
        # first right-carrying bay.
        (
            "corrupt json",
            b"{ this is not a notebook\n",
            (
                {
                    "kind": "bay-lost",
                    "bay": {"bay_key": "notebook:raw"},
                },
                "notebook:raw",
                "bay_not_found",
            ),
        ),
        # Non-UTF-8 bytes are not a notebook and are not text either, so
        # classification reaches the blob terminal. That composes a bay, so
        # the cell origin lands on its start exactly as the flatfile case
        # above does — the File stopped being a notebook, and the Thread
        # lands on whatever terminal it became.
        (
            "non utf-8",
            b"\x80\xfe\xffnot text",
            (
                {"kind": "bay-lost", "bay": {"bay_key": "blob"}},
                "blob",
                "bay_not_found",
            ),
        ),
        # A notebook whose cells were all removed has only left-carrying
        # cell bays, so the right-side origin falls to File start.
        (
            "no cells",
            json.dumps({**notebook, "cells": []}, indent=1).encode(),
            ({"kind": "side-lost"}, "cell-b", "bay_not_found"),
        ),
        # The exact File pair is gone: a deletion pairs (left, None), which
        # is not the origin's (left, right) pair.
        (
            "removed file",
            None,
            ({"kind": "file-absent"}, "cell-b", "file_missing"),
        ),
    ]
    for label, notebook_bytes, expected in losses:
        if notebook_bytes is None:
            (tmp_path / "demo.ipynb").unlink()
        else:
            (tmp_path / "demo.ipynb").write_bytes(notebook_bytes)
        rejoined = client.post(
            "/api/agent/capture",
            json={
                "tab": {
                    "kind": "head",
                    "repo_path": str(tmp_path),
                    "baseline": subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=tmp_path,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip(),
                }
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
        expected_placement, expected_bay_key, expected_reason = expected
        assert [thread["placement"] for thread in threads] == [
            expected_placement
        ], f"unexpected landing for capture {label}"
        # The text origin keeps its excerpt through every loss.
        assert ["excerpt" in thread["origin_target"] for thread in threads] == [
            True
        ]

        # The agent boundary reports the same stored landing under its own
        # five-name vocabulary: a replacement bay is named by its key, and a
        # landing with no code keeps the origin bay it was created on.
        agent_read = client.get(
            "/api/agent/threads",
            params={
                "snapshot_id": snapshot_id,
                "status": "open",
                "page": 1,
                "limit": 20,
            },
        )
        assert agent_read.status_code == 200
        [agent_item] = agent_read.json()["items"]
        assert agent_item["thread_id"] == thread_id
        assert agent_item["outdated_reason"] == expected_reason
        assert agent_item["location"]["path"] == "demo.ipynb"
        assert agent_item["location"]["bay_key"] == expected_bay_key


def test_unreadable_file_lands_its_thread_without_hiding_the_others(
    tmp_path: Path,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file, so no capture can fail")
    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "loud.py").write_text("def loud():\n    return 1\n")
    run_git(tmp_path, "add", "loud.py")
    run_git(tmp_path, "commit", "-m", "one module")
    (tmp_path / "loud.py").write_text("def loud():\n    return 2\n")
    (tmp_path / "shy.py").write_text("def shy():\n    return 2\n")
    client, _project_id = create_repo_client(tmp_path)

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "a" * 32,
            "name": "capture reviewer",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    joined = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()
    for name in ("shy.py", "loud.py"):
        created = client.post(
            "/api/agent/actions",
            json={
                "profile_id": profile["profile_id"],
                "actions": [
                    {
                        "kind": "create-thread",
                        "snapshot_id": joined["snapshot_id"],
                        "role": "reviewer",
                        "path": name,
                        "side": "right",
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
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    )
    assert rejoined.status_code == 200
    snapshot_id = rejoined.json()["snapshot_id"]
    assert snapshot_id != joined["snapshot_id"]
    (tmp_path / "shy.py").chmod(0o644)
    # The manifest keeps the unreadable File and reports why it has no bytes.
    [shy_file] = [
        file
        for file in rejoined.json()["files"]
        if file["right_path"] == "shy.py"
    ]
    assert shy_file["capture_error"] is not None

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
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
        },
    )
    assert manifest.status_code == 200
    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": manifest.json()["snapshot_id"],
            "engine": '{"engine":"dirdiff"}',
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
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
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
            "engine": '{"engine":"dirdiff"}',
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
            "engine": '{"engine":"dirdiff"}',
            "left_path": "logo",
            "right_path": "logo",
        },
    )
    assert damaged.status_code == 500


def test_symlink_target_cannot_escape_the_repository(tmp_path: Path) -> None:
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
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
        },
    )
    assert manifest.status_code == 200
    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": manifest.json()["snapshot_id"],
            "engine": '{"engine":"dirdiff"}',
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


@pytest.mark.parametrize("failed_side", ["left", "right"])
@pytest.mark.parametrize("damage", ["missing", "wrong-size"])
def test_lfs_capture_contains_errors_and_recovers(
    tmp_path: Path, failed_side: str, damage: str
) -> None:
    formats = Path(__file__).parents[1] / "presets" / "formats"
    payloads = {
        "left": (formats / "images/image-changed/old.png").read_bytes(),
        "right": (formats / "images/image-changed/new.png").read_bytes(),
    }
    oids = {
        side: hashlib.sha256(content).hexdigest()
        for side, content in payloads.items()
    }
    create_committed_repo(tmp_path, branch="main")
    object_paths = {}
    for side, content in payloads.items():
        oid = oids[side]
        (tmp_path / "logo.png").write_bytes(
            b"version https://git-lfs.github.com/spec/v1\n"
            + f"oid sha256:{oid}\nsize {len(content)}\n".encode()
        )
        (tmp_path / "alpha.txt").write_text(f"{side} sibling\n")
        run_git(tmp_path, "add", "logo.png", "alpha.txt")
        run_git(tmp_path, "commit", "-m", side)
        object_path = tmp_path / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(content)
        object_paths[side] = object_path

    failed_path = object_paths[failed_side]
    if damage == "missing":
        failed_path.unlink()
    else:
        failed_path.write_bytes(payloads[failed_side] + b"extra")
    client, _project_id = create_repo_client(tmp_path)
    with client:
        refs_tab = {
            "kind": "refs",
            "repo_path": str(tmp_path),
            "left": "HEAD~1",
            "right": "HEAD",
        }
        joined = client.post("/api/agent/capture", json={"tab": refs_tab})
        assert joined.status_code == 200, joined.text
        failed = joined.json()
        snapshot_path = Path(failed["snapshot_path"])
        assert len(list(snapshot_path.iterdir())) == 2
        successful_side = "right" if failed_side == "left" else "left"
        for side in ("left", "right"):
            contents = [
                path.read_bytes() for path in snapshot_path.glob(f"*/{side}")
            ]
            assert f"{side} sibling\n".encode() in contents
            if side == successful_side:
                assert payloads[side] in contents
            else:
                assert payloads[side] not in contents
                assert any(
                    oids[side].encode() in content for content in contents
                )
        for path, expected_status in (("logo.png", 400), ("alpha.txt", 200)):
            diff = client.get(
                "/api/file-diff",
                params={
                    "snapshot_id": failed["snapshot_id"],
                    "engine": '{"engine":"tokendiff"}',
                    "left_path": path,
                    "right_path": path,
                },
            )
            assert diff.status_code == expected_status, diff.text
            if path == "logo.png":
                assert oids[failed_side] in diff.text

        [failed_logo] = [
            file for file in failed["files"] if file["right_path"] == "logo.png"
        ]
        assert failed_logo["capture_error"] is not None
        repeated = client.post("/api/agent/capture", json={"tab": refs_tab})
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["snapshot_id"] == failed["snapshot_id"]

        failed_path.write_bytes(payloads[failed_side])
        recovered = client.post("/api/agent/capture", json={"tab": refs_tab})
        assert recovered.status_code == 200, recovered.text
        success = recovered.json()
        assert success["snapshot_id"] != failed["snapshot_id"]
        for object_path in object_paths.values():
            object_path.unlink()
        for side, expected in payloads.items():
            media = client.get(
                "/api/file-media",
                params={
                    "snapshot_id": success["snapshot_id"],
                    "bay_key": "image",
                    "side": side,
                    "left_path": "logo.png",
                    "right_path": "logo.png",
                },
            )
            assert media.status_code == 200, media.text
            assert media.content == expected
        repeated = client.post("/api/agent/capture", json={"tab": refs_tab})
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["snapshot_id"] == success["snapshot_id"]


def test_file_media_serves_each_captured_side_exactly(tmp_path: Path) -> None:
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
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
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
            "engine": '{"engine":"dirdiff"}',
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
            "selection": json.dumps(
                {
                    "tab": "refs",
                    "project_id": project_id,
                    "left": "index",
                    "right": "worktree",
                    "show_untracked": False,
                }
            )
        },
    )
    assert manifest.status_code == 200
    snapshot_id = manifest.json()["snapshot_id"]
    diff = client.get(
        "/api/file-diff",
        params={
            "snapshot_id": snapshot_id,
            "engine": '{"engine":"dirdiff"}',
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
    fixtures = Path(__file__).parents[1] / "presets" / "formats" / "images"
    old_png = (fixtures / "image-changed" / "old.png").read_bytes()
    new_png = (fixtures / "image-changed" / "new.png").read_bytes()

    create_committed_repo(tmp_path, branch="main")
    (tmp_path / "logo.png").write_bytes(old_png)
    run_git(tmp_path, "add", "logo.png")
    run_git(tmp_path, "commit", "-m", "logo")
    (tmp_path / "logo.png").write_bytes(new_png)
    client, _project_id = create_repo_client(tmp_path)

    profile = client.post(
        "/api/agent/profile",
        json={
            "agent_uuid": "e" * 32,
            "name": "image reviewer",
            "model": "test-model",
            "harness": "pytest",
        },
    ).json()
    captured = client.post(
        "/api/agent/capture",
        json={
            "tab": {
                "kind": "head",
                "repo_path": str(tmp_path),
                "baseline": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=tmp_path,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            }
        },
    ).json()

    created = client.post(
        "/api/agent/actions",
        json={
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "logo.png",
                    "side": "right",
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

    read = client.get(f"/api/agent/thread/{thread_id}")
    assert read.status_code == 200
    body = read.json()
    assert body["location"] == {
        "path": "logo.png",
        "side": "right",
        "bay_key": "image",
        "start_line": 1,
        "end_line": 1,
    }
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
                "profile_id": profile["profile_id"],
                "actions": [
                    {
                        "kind": "create-thread",
                        "snapshot_id": captured["snapshot_id"],
                        "role": "reviewer",
                        "path": "logo.png",
                        "side": "right",
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
            "profile_id": profile["profile_id"],
            "actions": [
                {
                    "kind": "create-thread",
                    "snapshot_id": captured["snapshot_id"],
                    "role": "reviewer",
                    "path": "logo.png",
                    "side": "right",
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


def test_gitlinks_review_as_commit_ids_through_the_head_tab(
    tmp_path: Path,
) -> None:
    inner = tmp_path / "inner"
    outer = tmp_path / "outer"
    inner.mkdir()
    outer.mkdir()
    create_committed_repo(inner, branch="main")
    (inner / "alpha.txt").write_text("two\n", encoding="utf-8")
    run_git(inner, "commit", "-qam", "second")
    create_committed_repo(outer, branch="main")
    # The nested clone ignores the outer repository configuration, so the file
    # transport must be allowed on the command itself.
    run_git(
        outer,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(inner),
        "sub",
    )
    run_git(outer, "commit", "-qm", "submodule")
    run_git(outer / "sub", "checkout", "-q", "HEAD~1")
    (outer / "embedded").mkdir()
    create_committed_repo(outer / "embedded", branch="main")
    old, new, embedded = (
        subprocess.run(
            ["git", "rev-parse", ref],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
        for cwd, ref in (
            (inner, "HEAD"),
            (inner, "HEAD~1"),
            (outer / "embedded", "HEAD"),
        )
    )

    client, _project_id = create_repo_client(outer)
    with client:
        joined = client.post(
            "/api/agent/capture",
            json={
                "tab": {
                    "kind": "head",
                    "repo_path": str(outer),
                    "baseline": subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=outer,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip(),
                }
            },
        )
        assert joined.status_code == 200, joined.text
        snapshot_id = joined.json()["snapshot_id"]

        moved = client.get(
            "/api/file-diff",
            params={
                "snapshot_id": snapshot_id,
                "engine": '{"engine":"dirdiff"}',
                "left_path": "sub",
                "right_path": "sub",
            },
        )
        assert moved.status_code == 200, moved.text
        assert "alpha" not in moved.text
        (frame,) = moved.json()["frames"]
        (bay,) = frame["bays"]
        assert bay["bay_key"] == "flatfile"
        assert bay["kind_data"]["kind"] == "text"
        rows = bay["kind_data"]["rows"]
        assert [row["left_text"] for row in rows if row["left_no"]] == [old]
        assert [row["right_text"] for row in rows if row["right_no"]] == [new]

        added = client.get(
            "/api/file-diff",
            params={
                "snapshot_id": snapshot_id,
                "engine": '{"engine":"dirdiff"}',
                "right_path": "embedded",
            },
        )
        assert added.status_code == 200, added.text
        assert "alpha" not in added.text
        assert added.json()["file_kind"] == {"type": "untracked"}
        (frame,) = added.json()["frames"]
        (bay,) = frame["bays"]
        assert bay["kind_data"]["kind"] == "text"
        assert [
            (row["status"], row["right_text"])
            for row in bay["kind_data"]["rows"]
        ] == [("insert", embedded)]


def test_symlink_to_a_submodule_captures_its_commit_id(tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    outer = tmp_path / "outer"
    inner.mkdir()
    outer.mkdir()
    create_committed_repo(inner, branch="main")
    create_committed_repo(outer, branch="main")
    run_git(
        outer,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(inner),
        "sub",
    )
    os.symlink("sub", outer / "portal")
    run_git(outer, "add", "portal")
    run_git(outer, "commit", "-qm", "submodule and link")
    (outer / "portal").unlink()
    os.symlink("hop", outer / "portal")
    os.symlink("sub", outer / "hop")
    inner_id = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=inner,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )

    client, project_id = create_repo_client(outer)
    with client:
        manifest = client.get(
            "/api/manifest",
            params={
                "selection": json.dumps(
                    {
                        "tab": "refs",
                        "project_id": project_id,
                        "left": "HEAD",
                        "right": "worktree",
                        "show_untracked": False,
                    }
                )
            },
        )
        assert manifest.status_code == 200, manifest.text
        diff = client.get(
            "/api/file-diff",
            params={
                "snapshot_id": manifest.json()["snapshot_id"],
                "engine": '{"engine":"dirdiff"}',
                "left_path": "portal",
                "right_path": "portal",
            },
        )
        assert diff.status_code == 200, diff.text
        bays = diff.json()["frames"][0]["bays"]
        assert [bay["bay_key"] for bay in bays] == [
            "symlink",
            "symlink-target",
        ]
        target = bays[1]["kind_data"]
        assert target["kind"] == "text"
        assert [
            row["left_text"]
            for row in target["rows"]
            if row["left_text"] not in (None, "")
        ] == [inner_id]
        assert [
            row["right_text"]
            for row in target["rows"]
            if row["right_text"] not in (None, "")
        ] == ["# %% hop", "sub", "# %% sub", inner_id]
