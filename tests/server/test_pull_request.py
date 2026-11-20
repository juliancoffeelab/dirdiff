from __future__ import annotations

from dirdiff.change_source import pull_request


def test_github_pull_request_url_parses_repo_and_number() -> None:
    parsed = pull_request.parse_github_pull_request_url(
        "https://github.com/Wilfred/difftastic/pull/1007"
    )

    assert parsed == ("Wilfred", "difftastic", 1007)


def test_github_remote_key_matches_git_remote_url() -> None:
    assert pull_request.repo_key_from_git_url(
        "https://github.com/Wilfred/difftastic"
    ) == pull_request.repo_key_from_git_url(
        "git@github.com:Wilfred/difftastic.git"
    )


def test_gitlab_merge_request_url_parses_nested_project_path() -> None:
    parsed = pull_request.parse_gitlab_merge_request_url(
        "https://gitlab.example.com/group/subgroup/project/-/merge_requests/17"
    )

    assert parsed == (
        "https",
        "gitlab.example.com",
        "group/subgroup/project",
        17,
    )


def test_gitlab_remote_key_matches_git_remote_url() -> None:
    assert pull_request.repo_key(
        host="gitlab.example.com",
        path="group/subgroup/project",
    ) == pull_request.repo_key_from_git_url(
        "git@gitlab.example.com:group/subgroup/project.git"
    )
