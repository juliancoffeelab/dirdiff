"""Integration coverage for FastAPI diff endpoints against real Git repos.

These tests create temporary repositories, register them through the normal
store layer, and exercise HTTP routes through `TestClient`.  They are allowed
to use local Git subprocesses and disposable SQLite files, but they should not
mock backend loading or bypass request/response contracts.
"""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from dirdiff.db import RepoMarkStore, UserProfileStore, open_sqlite_engine
from dirdiff.server import create_app

__all__: list[str] = []


def create_repo_client(repo_path: Path) -> tuple[TestClient, int]:
    engine = open_sqlite_engine(repo_path / ".dirdiff-test.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    mark = repo_marks.new_mark(path=repo_path, name=repo_path.name)
    return TestClient(create_app(repo_marks, user_profile)), mark.id


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

    client, repo_id = create_repo_client(repo_path)

    response = client.get("/api/repo-defaults", params={"repo_id": repo_id})
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
    client, repo_id = create_repo_client(repo_path)

    save_response = client.post(
        f"/api/repos/{repo_id}/main-branch",
        json={"selection": {"source": "local", "branch": "master"}},
    )
    assert save_response.status_code == 200
    assert save_response.json() == {
        "repo_id": repo_id,
        "selection": {"source": "local", "branch": "master"},
    }

    defaults_response = client.get(
        "/api/repo-defaults", params={"repo_id": repo_id}
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
    client, repo_id = create_repo_client(repo_path)

    response = client.get("/api/repo-refs", params={"repo_id": repo_id})
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
    client, repo_id = create_repo_client(repo_path)

    response = client.post(
        f"/api/repos/{repo_id}/main-branch",
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
    client, _repo_id = create_repo_client(tmp_path)

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
    client, repo_id = create_repo_client(repo_path)

    response = client.get("/api/repo-defaults", params={"repo_id": repo_id})
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
    client, repo_id = create_repo_client(repo_path)

    response = client.get("/api/repo-defaults", params={"repo_id": repo_id})
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

    client, repo_id = create_repo_client(origin_worktree)

    response = client.get("/api/repo-defaults", params={"repo_id": repo_id})
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
    client, repo_id = create_repo_client(tmp_path)

    response = client.get("/api/repo-defaults", params={"repo_id": repo_id})
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
    client, repo_id = create_repo_client(tmp_path)

    response = client.get(
        "/api/manifest",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "branch-review",
            "base_branch": "master",
            "review_source": "local",
            "review_branch": "feature",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "base_source is required for branch-review mode."
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

    client, repo_id = create_repo_client(tmp_path)

    repo_response = client.get(
        "/api/manifest",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "files",
            "left": "index",
            "right": "worktree",
        },
    )
    repo_payload = repo_response.json()
    cache_id = repo_payload["cache_id"]
    assert isinstance(cache_id, str)
    assert cache_id != ""
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
            "repo_id": repo_id,
            "cache_id": cache_id,
        },
    )
    lazy_info = lazy_info_response.json()["files"][0]
    assert lazy_info_response.status_code == 200
    assert lazy_info == {
        "lazy": "generated",
        "left_path": "Cargo.lock",
        "right_path": "Cargo.lock",
        "display_name": "Cargo.lock",
        "changed_lines": 2,
        "added_lines": 1,
        "removed_lines": 1,
        "file_kind": {"type": "git", "status": "modified"},
    }

    response = client.get(
        "/api/file-diff",
        params={
            "repo_id": repo_id,
            "cache_id": cache_id,
            "engine": "dirdiff",
            "mode": "files",
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["display_name"] == "Cargo.lock"
    assert payload.get("lazy") is None
    assert payload["file_kind"] == {"type": "git", "status": "modified"}
    assert payload["rows"] != []

    reloaded_manifest_response = client.get(
        "/api/manifest",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "files",
            "left": "index",
            "right": "worktree",
        },
    )
    reloaded_cache_id = reloaded_manifest_response.json()["cache_id"]

    assert reloaded_manifest_response.status_code == 200
    assert reloaded_cache_id != cache_id

    stale_response = client.get(
        "/api/file-diff",
        params={
            "repo_id": repo_id,
            "cache_id": cache_id,
            "engine": "dirdiff",
            "mode": "files",
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
        },
    )
    fresh_response = client.get(
        "/api/file-diff",
        params={
            "repo_id": repo_id,
            "cache_id": reloaded_cache_id,
            "engine": "dirdiff",
            "mode": "files",
            "left_path": "Cargo.lock",
            "right_path": "Cargo.lock",
        },
    )

    assert stale_response.status_code == 400
    assert stale_response.json()["detail"] == f"Unknown cache id: {cache_id}"
    assert fresh_response.status_code == 200


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

    client, repo_id = create_repo_client(tmp_path)

    response = client.get(
        "/api/manifest",
        params={
            "repo_id": repo_id,
            "engine": "dirdiff",
            "mode": "files",
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
