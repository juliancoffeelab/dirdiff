from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dirdiff.change_source.base import git_executable
from dirdiff.engines import DirdiffError

LOGGER = logging.getLogger(__name__)

__all__ = [
    "PreparedPullRequest",
    "parse_github_pull_request_url",
    "parse_gitlab_merge_request_url",
    "prepare_pull_request",
    "repo_key",
    "repo_key_from_git_url",
]

COMMIT_ID_RE = re.compile(r"[0-9a-f]{40}")

GITHUB_PULL_REQUEST_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:[/?#].*)?$"
)
GITLAB_MERGE_REQUEST_RE = re.compile(
    r"^(?P<scheme>https?)://(?P<host>[^/]+)/(?P<project>.+)/-/merge_requests/(?P<iid>\d+)(?:[/?#].*)?$"
)


class RepoMarkLike(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def path(self) -> str: ...


@dataclass(frozen=True)
class PreparedPullRequest:
    """Branch names are display-only; capture uses the two commits, never the
    branches.
    """

    project_id: int
    pull_request_url: str
    left_commit: str
    right_commit: str
    base_branch: str
    head_branch: str


@dataclass(frozen=True)
class _GitHubPullRequest:
    url: str
    owner: str
    repo: str
    number: int
    base_branch: str
    base_repo_key: str
    head_branch: str
    base_commit: str
    head_commit: str


@dataclass(frozen=True)
class _GitLabMergeRequest:
    url: str
    scheme: str
    host: str
    project_path: str
    iid: int
    target_branch: str
    head_branch: str
    base_commit: str
    head_commit: str
    target_repo_key: str


def prepare_pull_request(
    *,
    url: str,
    repo_marks: Iterable[RepoMarkLike],
) -> PreparedPullRequest:
    value = url.strip()
    if GITHUB_PULL_REQUEST_RE.match(value) is not None:
        return _prepare_github_pull_request(
            url=value,
            repo_marks=repo_marks,
        )
    if GITLAB_MERGE_REQUEST_RE.match(value) is not None:
        return _prepare_gitlab_merge_request(
            url=value,
            repo_marks=repo_marks,
        )
    raise DirdiffError(
        "Only GitHub pull request and GitLab merge request URLs are supported."
    )


def _prepare_github_pull_request(
    *,
    url: str,
    repo_marks: Iterable[RepoMarkLike],
) -> PreparedPullRequest:
    pull_request = _load_github_pull_request(url)
    for mark in repo_marks:
        repo_path = Path(mark.path)
        if not repo_path.is_dir():
            LOGGER.warning(
                "Skipping unavailable marked repository during PR preparation: %s",
                repo_path,
            )
            continue
        remote = _matching_remote(
            repo_path=repo_path,
            remote_repo_key=pull_request.base_repo_key,
        )
        if remote is None:
            continue
        _fetch_commits(
            repo_path=repo_path,
            remote=remote,
            commits=(pull_request.base_commit, pull_request.head_commit),
        )
        # Fetch retains objects; only the API observation chooses the revision.
        merge_base = _run_git_text(
            repo_path,
            ["merge-base", pull_request.base_commit, pull_request.head_commit],
            check=False,
        )
        if merge_base.returncode != 0 or merge_base.stdout.strip() == "":
            raise DirdiffError(
                "Could not find a merge base for the prepared pull request. "
                + merge_base.stderr.strip()
            )
        return PreparedPullRequest(
            project_id=mark.id,
            pull_request_url=pull_request.url,
            left_commit=merge_base.stdout.strip(),
            right_commit=pull_request.head_commit,
            base_branch=pull_request.base_branch,
            head_branch=pull_request.head_branch,
        )
    raise DirdiffError(
        "No marked repository has a remote for this pull request."
    )


def _prepare_gitlab_merge_request(
    *,
    url: str,
    repo_marks: Iterable[RepoMarkLike],
) -> PreparedPullRequest:
    merge_request = _load_gitlab_merge_request(url)
    for mark in repo_marks:
        repo_path = Path(mark.path)
        if not repo_path.is_dir():
            LOGGER.warning(
                "Skipping unavailable marked repository during PR preparation: %s",
                repo_path,
            )
            continue
        remote = _matching_remote(
            repo_path=repo_path,
            remote_repo_key=merge_request.target_repo_key,
        )
        if remote is None:
            continue
        _fetch_commits(
            repo_path=repo_path,
            remote=remote,
            commits=(merge_request.base_commit, merge_request.head_commit),
        )
        # Keep both inputs from one diff version even after merging or a push.
        merge_base = _run_git_text(
            repo_path,
            [
                "merge-base",
                merge_request.base_commit,
                merge_request.head_commit,
            ],
            check=False,
        )
        if merge_base.returncode != 0 or merge_base.stdout.strip() == "":
            raise DirdiffError(
                "Could not find a merge base for the prepared merge request. "
                + merge_base.stderr.strip()
            )
        return PreparedPullRequest(
            project_id=mark.id,
            pull_request_url=merge_request.url,
            left_commit=merge_base.stdout.strip(),
            right_commit=merge_request.head_commit,
            base_branch=merge_request.target_branch,
            head_branch=merge_request.head_branch,
        )
    raise DirdiffError(
        "No marked repository has a remote for this merge request."
    )


def _load_github_pull_request(url: str) -> _GitHubPullRequest:
    parsed = parse_github_pull_request_url(url)
    owner, repo, number = parsed
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}",
        headers={
            "accept": "application/vnd.github+json",
            "user-agent": "dirdiff",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DirdiffError(
            f"GitHub pull request request failed: {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DirdiffError(
            f"GitHub pull request request failed: {exc.reason}"
        ) from exc

    try:
        base = payload["base"]
        base_repo = base["repo"]
        base_branch = base["ref"]
        base_repo_url = base_repo["html_url"]
        base_commit = base["sha"]
        head_branch = payload["head"]["ref"]
        head_commit = payload["head"]["sha"]
    except KeyError as exc:
        raise DirdiffError(
            "GitHub pull request response is missing base or head data."
        ) from exc
    if not isinstance(base_branch, str) or base_branch.strip() == "":
        raise DirdiffError(
            "GitHub pull request response has an empty base branch."
        )
    if not isinstance(base_repo_url, str) or base_repo_url.strip() == "":
        raise DirdiffError(
            "GitHub pull request response has an empty base repo URL."
        )
    assert isinstance(head_branch, str) and head_branch.strip() != "", (
        "GitHub pull request response requires a nonempty head branch."
    )
    for name, commit in (("base", base_commit), ("head", head_commit)):
        assert (
            isinstance(commit, str)
            and COMMIT_ID_RE.fullmatch(commit) is not None
        ), f"GitHub pull request {name} must be a complete commit SHA."
    return _GitHubPullRequest(
        url=url.strip(),
        owner=owner,
        repo=repo,
        number=number,
        base_branch=base_branch,
        base_repo_key=repo_key_from_git_url(base_repo_url),
        head_branch=head_branch,
        base_commit=base_commit,
        head_commit=head_commit,
    )


def parse_github_pull_request_url(url: str) -> tuple[str, str, int]:
    value = url.strip()
    match = GITHUB_PULL_REQUEST_RE.match(value)
    if match is None:
        raise DirdiffError("Only GitHub pull request URLs are supported.")
    owner = urllib.parse.unquote(match.group("owner"))
    repo = urllib.parse.unquote(match.group("repo")).removesuffix(".git")
    number = int(match.group("number"))
    return owner, repo, number


def _load_gitlab_merge_request(url: str) -> _GitLabMergeRequest:
    parsed = parse_gitlab_merge_request_url(url)
    scheme, host, project_path, iid = parsed
    encoded_project_path = urllib.parse.quote(project_path, safe="")
    request = urllib.request.Request(
        f"{scheme}://{host}/api/v4/projects/{encoded_project_path}/merge_requests/{iid}",
        headers={
            "accept": "application/json",
            "user-agent": "dirdiff",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DirdiffError(
            f"GitLab merge request request failed: {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DirdiffError(
            f"GitLab merge request request failed: {exc.reason}"
        ) from exc

    try:
        target_branch = payload["target_branch"]
        head_branch = payload["source_branch"]
        diff_refs = payload["diff_refs"]
    except KeyError as exc:
        raise DirdiffError(
            "GitLab merge request response is missing branch or diff data."
        ) from exc
    if diff_refs is None or diff_refs == {}:
        raise DirdiffError(
            "GitLab merge request diff metadata is not available yet. Retry preparation explicitly."
        )
    assert isinstance(diff_refs, dict), "GitLab diff_refs must be an object."
    try:
        base_commit = diff_refs["start_sha"]
        head_commit = diff_refs["head_sha"]
    except KeyError as exc:
        raise DirdiffError(
            "GitLab diff metadata is missing a start or head SHA."
        ) from exc
    assert isinstance(head_branch, str) and head_branch.strip() != "", (
        "GitLab merge request response requires a nonempty source branch."
    )
    for name, commit in (("start", base_commit), ("head", head_commit)):
        assert (
            isinstance(commit, str)
            and COMMIT_ID_RE.fullmatch(commit) is not None
        ), f"GitLab merge request diff {name} must be a complete commit SHA."
    if not isinstance(target_branch, str) or target_branch.strip() == "":
        raise DirdiffError(
            "GitLab merge request response has an empty target branch."
        )
    return _GitLabMergeRequest(
        url=url.strip(),
        scheme=scheme,
        host=host,
        project_path=project_path,
        iid=iid,
        target_branch=target_branch,
        head_branch=head_branch,
        base_commit=base_commit,
        head_commit=head_commit,
        target_repo_key=repo_key(host=host, path=project_path),
    )


def parse_gitlab_merge_request_url(url: str) -> tuple[str, str, str, int]:
    value = url.strip()
    match = GITLAB_MERGE_REQUEST_RE.match(value)
    if match is None:
        raise DirdiffError("Only GitLab merge request URLs are supported.")
    scheme = match.group("scheme")
    host = match.group("host")
    project_path = urllib.parse.unquote(match.group("project")).removesuffix(
        ".git"
    )
    iid = int(match.group("iid"))
    return scheme, host, project_path, iid


def _matching_remote(*, repo_path: Path, remote_repo_key: str) -> str | None:
    for remote, remote_url in _remote_urls(repo_path).items():
        try:
            repo_key = repo_key_from_git_url(remote_url)
        except DirdiffError:
            continue
        if repo_key == remote_repo_key:
            return remote
    return None


def _remote_urls(repo_path: Path) -> dict[str, str]:
    result = _run_git_text(
        repo_path,
        ["config", "--get-regexp", r"^remote\..*\.url$"],
        check=False,
    )
    if result.returncode != 0:
        # Callers cannot tell a failed query from a repository without remotes.
        return {}
    urls: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if not key.startswith("remote.") or not key.endswith(".url"):
            continue
        remote = key.removeprefix("remote.").removesuffix(".url")
        if remote != "" and value.strip() != "":
            urls[remote] = value.strip()
    return urls


def _fetch_commits(
    *,
    repo_path: Path,
    remote: str,
    commits: tuple[str, str],
) -> None:
    # Fetching by SHA works even when the forge branches are deleted. The
    # refs/dirdiff/prepared/<sha> refs keep the objects safe from git gc so
    # restored review URLs stay capturable; nothing ever cleans them up.
    result = _run_git_text(
        repo_path,
        [
            "fetch",
            "--atomic",
            "--no-tags",
            "--no-prune",
            "--refmap=",
            remote,
            # Identical target/head commits must update their shared ref once.
            *(
                f"{commit}:refs/dirdiff/prepared/{commit}"
                for commit in dict.fromkeys(commits)
            ),
        ],
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        if message == "":
            message = f"git fetch failed with exit code {result.returncode}."
        raise DirdiffError(message)


def _run_git_text(
    repo_path: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git_executable(), *args],
        cwd=repo_path,
        check=check,
        capture_output=True,
        text=True,
    )


def repo_key_from_git_url(url: str) -> str:
    """Return a repository identity used to match forge and local remotes.

    GitHub preparation passes this function its `base.repo.html_url`. Both
    GitHub and GitLab preparation pass it each URL reported by the marked
    repository's Git configuration. GitLab constructs the forge-side key from
    its parsed host and project path through `repo_key`. Different URL
    spellings for the same host and repository produce the same key.

    # Example

    >>> github_base_url = "https://github.com/openai/codex"
    >>> configured_remote_url = "git@github.com:openai/codex.git"
    >>> repo_key_from_git_url(github_base_url)
    'github.com/openai/codex'
    >>> repo_key_from_git_url(configured_remote_url)
    'github.com/openai/codex'
    >>> (
    ...     repo_key_from_git_url(github_base_url)
    ...     == repo_key_from_git_url(configured_remote_url)
    ... )
    True

    # Failures

    - Raises `DirdiffError` when a value cannot identify a supported Git host
      and repository path.
    """
    stripped = url.strip()
    if stripped == "":
        raise DirdiffError("Remote URL is empty.")
    # Git accepts this SCP-like spelling, which `urlparse` does not separate
    # into a hostname and repository path.
    if stripped.startswith("git@"):
        without_user = stripped.removeprefix("git@")
        host, separator, path = without_user.partition(":")
        if separator == "":
            raise DirdiffError(f"Unsupported Git remote URL: {url}")
        return repo_key(host=host, path=path)

    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme in {"http", "https", "ssh", "git"}:
        host = parsed.hostname or ""
        path = parsed.path
        return repo_key(host=host, path=path)
    raise DirdiffError(f"Unsupported Git remote URL: {url}")


def repo_key(*, host: str, path: str) -> str:
    normalized_host = host.lower().strip()
    normalized_path = urllib.parse.unquote(path).strip().strip("/")
    normalized_path = normalized_path.removesuffix(".git")
    if normalized_host == "" or normalized_path == "":
        raise DirdiffError("Git remote URL is missing host or path.")
    return f"{normalized_host}/{normalized_path.lower()}"
