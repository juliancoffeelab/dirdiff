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

    client, project_id = create_repo_client(tmp_path)

    repo_response = client.get(
        "/api/manifest",
        params={
            "project_id": str(project_id),
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
            "project_id": str(project_id),
            "mode": "files",
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
            "project_id": str(project_id),
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
            "project_id": str(project_id),
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
            "project_id": str(project_id),
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
            "project_id": str(project_id),
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


def test_preset_manifest_and_file_diff_do_not_require_project_id(
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
            presets_root=str(Path.cwd() / "tests" / "presets" / "difftastic"),
        )
    )

    manifest_response = client.get(
        "/api/manifest",
        params={
            "engine": "dirdiff",
            "mode": "preset",
            "project_id": "diff",
            "preset_subset": "python",
        },
    )
    manifest = manifest_response.json()

    assert manifest_response.status_code == 200
    assert manifest["display_name"] == "python"
    assert manifest["cache_id"] == ""
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
            "mode": "preset",
            "project_id": "diff",
            "preset_subset": "python",
            "cache_id": manifest["cache_id"],
        },
    )
    file_diff_response = client.get(
        "/api/file-diff",
        params={
            "cache_id": manifest["cache_id"],
            "engine": "dirdiff",
            "mode": "preset",
            "project_id": "diff",
            "preset_subset": "python",
            "left_path": file_entry["left_path"],
            "right_path": file_entry["right_path"],
        },
    )
    file_diff = file_diff_response.json()

    assert lazy_info_response.status_code == 200
    assert lazy_info_response.json() == {"files": []}
    assert file_diff_response.status_code == 200
    assert file_diff["display_name"] != ""
    assert file_diff["rows"] != []
    assert "render_mode" not in file_diff
    assert "truncated_rows" not in file_diff
    assert all(
        row["status"] in {"equal", "replace", "insert", "delete", "move"}
        for row in file_diff["rows"]
    )
    assert all(
        "foldedRows" not in row and "count" not in row and "label" not in row
        for row in file_diff["rows"]
    )
    assert file_diff["hunk_count"] >= 1
    assert [
        row["hunk_index"]
        for row in file_diff["rows"]
        if row["hunk_index"] is not None
    ] == list(range(file_diff["hunk_count"]))


def test_all_preset_catalogs_load_without_project_id(tmp_path: Path) -> None:
    """Every preset catalog is repo-less, even though catalogs use different roots."""
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    repo_marks = RepoMarkStore(engine)
    user_profile = UserProfileStore(engine)
    client = TestClient(
        create_app(
            repo_marks,
            user_profile,
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
                "engine": "dirdiff",
                "mode": "preset",
                "project_id": project_id,
                "preset_subset": preset_subset,
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["display_name"] == preset_subset
        assert payload["cache_id"] == ""
        assert payload["tree"] != []


def test_scroll_preset_can_force_compact_files_lazy(tmp_path: Path) -> None:
    """Preset metadata should model lazy placement without giant fixture files."""
    engine = open_sqlite_engine(tmp_path / "dirdiff.sqlite")
    client = TestClient(
        create_app(
            RepoMarkStore(engine),
            UserProfileStore(engine),
            presets_root=str(Path.cwd() / "tests" / "presets" / "difftastic"),
        )
    )

    manifest_response = client.get(
        "/api/manifest",
        params={
            "engine": "dirdiff",
            "mode": "preset",
            "project_id": "scroll",
            "preset_subset": "lazy-files",
        },
    )
    lazy_info_response = client.get(
        "/api/lazy-info",
        params={
            "mode": "preset",
            "project_id": "scroll",
            "preset_subset": "lazy-files",
            "cache_id": "",
        },
    )

    assert manifest_response.status_code == 200
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
            presets_root=str(Path.cwd() / "tests" / "presets" / "difftastic"),
        )
    )

    missing_subset = client.get(
        "/api/manifest",
        params={
            "engine": "dirdiff",
            "mode": "preset",
            "project_id": "diff",
        },
    )
    missing_project = client.get(
        "/api/manifest",
        params={
            "engine": "dirdiff",
            "mode": "preset",
            "preset_subset": "python",
        },
    )
    traversal = client.get(
        "/api/manifest",
        params={
            "engine": "dirdiff",
            "mode": "preset",
            "project_id": "diff",
            "preset_subset": "../python",
        },
    )

    assert missing_subset.status_code == 400
    assert missing_subset.json()["detail"] == (
        "preset_subset is required for preset mode."
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
