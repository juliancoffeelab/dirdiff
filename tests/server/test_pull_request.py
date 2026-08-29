"""Backend tests for forge pull request URL handling.

These tests avoid faking network or Git subprocess behavior.  They cover the
deterministic parsing and repository-key logic that GitLab MR support relies on;
full fetch behavior is exercised manually against real remotes rather than by
tests that monkeypatch half the backend.
"""

from __future__ import annotations

from dirdiff.backend import pull_request

__all__: list[str] = []


def test_github_pull_request_url_parses_repo_and_number() -> None:
    """GitHub PR URLs preserve owner, repository name, and PR number.

    Parsing must return the correspondence facts without retaining unrelated URL syntax.
    """

    parsed = pull_request._parse_github_pull_request_url(
        "https://github.com/Wilfred/difftastic/pull/1007"
    )

    assert parsed == ("Wilfred", "difftastic", 1007)


def test_github_remote_key_matches_git_remote_url() -> None:
    """GitHub project URLs and Git remote URLs normalize to the same repository key.

    Equivalent browser and transport spellings must therefore select the same
    marked repository during preparation.
    """

    assert pull_request._repo_key_from_git_url(
        "https://github.com/Wilfred/difftastic"
    ) == pull_request._repo_key_from_git_url(
        "git@github.com:Wilfred/difftastic.git"
    )


def test_gitlab_merge_request_url_parses_nested_project_path() -> None:
    """GitLab MR URLs preserve host, nested project path, and MR iid.

    Nested namespaces remain part of repository identity rather than being flattened.
    """

    parsed = pull_request._parse_gitlab_merge_request_url(
        "https://gitlab.example.com/group/subgroup/project/-/merge_requests/17"
    )

    assert parsed == (
        "https",
        "gitlab.example.com",
        "group/subgroup/project",
        17,
    )


def test_gitlab_remote_key_matches_git_remote_url() -> None:
    """GitLab project URLs and Git remote URLs normalize to the same repository key.

    Protocol and suffix differences must not prevent the marked repository from
    matching the forge project.
    """

    assert pull_request._repo_key(
        host="gitlab.example.com",
        path="group/subgroup/project",
    ) == pull_request._repo_key_from_git_url(
        "git@gitlab.example.com:group/subgroup/project.git"
    )
