"""Pull request preparation for repo-backed diffs.

This module is the backend boundary for turning a forge pull request URL into
local Git state that dirdiff can compare.  Its public interface accepts a pull
request URL plus the registered repository marks, finds the matching marked Git
repository by remote URL, fetches the review ref into that repository, and
returns the prepared repository id plus branch data.

The module does not render diffs, build manifests, own FastAPI routes, or mutate
the user's checked-out branch.  It prepares remote-tracking refs only; the
existing repository diff pipeline remains responsible for listing changed files
and loading file versions.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dirdiff.backend.base import TextDiffError

__all__ = [
    "PreparedPullRequest",
    "PreparedPullRequestBranch",
    "prepare_pull_request",
]

GITHUB_PULL_REQUEST_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:[/?#].*)?$"
)


class RepoMarkLike(Protocol):
    """Repository mark shape consumed by pull request preparation."""

    @property
    def id(self) -> int:
        """Stable repository id returned to the frontend."""
        ...

    @property
    def path(self) -> str:
        """Filesystem path for a marked repository."""
        ...


@dataclass(frozen=True)
class PreparedPullRequestBranch:
    """Remote branch prepared for a pull request comparison."""

    remote: str
    branch: str


@dataclass(frozen=True)
class PreparedPullRequest:
    """Prepared pull request state returned to the API layer."""

    repo_id: int
    pull_request_url: str
    base_branch: PreparedPullRequestBranch
    review_branch: PreparedPullRequestBranch


@dataclass(frozen=True)
class _GitHubPullRequest:
    url: str
    owner: str
    repo: str
    number: int
    base_branch: str
    base_repo_key: str


def prepare_pull_request(
    *,
    url: str,
    repo_marks: Iterable[RepoMarkLike],
) -> PreparedPullRequest:
    """Fetch a GitHub pull request into a matching marked repository.

    The URL must point at a GitHub pull request.  The pull request base
    repository must match at least one configured remote in the registered repo
    list.  The review ref is fetched into `refs/remotes/<remote>/pull/<number>`
    so existing branch comparison code can diff it without checking out or
    creating a local branch.
    """
    pull_request = _load_github_pull_request(url)
    for mark in repo_marks:
        repo_path = Path(mark.path)
        remote = _matching_remote(
            repo_path=repo_path,
            remote_repo_key=pull_request.base_repo_key,
        )
        if remote is None:
            continue
        review_branch = f"pull/{pull_request.number}"
        _fetch_ref(
            repo_path=repo_path,
            remote=remote,
            source_ref=pull_request.base_branch,
            target_ref=f"refs/remotes/{remote}/{pull_request.base_branch}",
        )
        _fetch_ref(
            repo_path=repo_path,
            remote=remote,
            source_ref=f"pull/{pull_request.number}/head",
            target_ref=f"refs/remotes/{remote}/{review_branch}",
        )
        return PreparedPullRequest(
            repo_id=mark.id,
            pull_request_url=pull_request.url,
            base_branch=PreparedPullRequestBranch(
                remote=remote,
                branch=pull_request.base_branch,
            ),
            review_branch=PreparedPullRequestBranch(
                remote=remote,
                branch=review_branch,
            ),
        )
    raise TextDiffError(
        "No marked repository has a remote for this pull request."
    )


def _load_github_pull_request(url: str) -> _GitHubPullRequest:
    parsed = _parse_github_pull_request_url(url)
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
        raise TextDiffError(
            f"GitHub pull request request failed: {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise TextDiffError(
            f"GitHub pull request request failed: {exc.reason}"
        ) from exc

    try:
        base = payload["base"]
        base_repo = base["repo"]
        base_branch = base["ref"]
        base_repo_url = base_repo["html_url"]
    except KeyError as exc:
        raise TextDiffError(
            "GitHub pull request response is missing base data."
        ) from exc
    if not isinstance(base_branch, str) or base_branch.strip() == "":
        raise TextDiffError(
            "GitHub pull request response has an empty base branch."
        )
    if not isinstance(base_repo_url, str) or base_repo_url.strip() == "":
        raise TextDiffError(
            "GitHub pull request response has an empty base repo URL."
        )
    return _GitHubPullRequest(
        url=url.strip(),
        owner=owner,
        repo=repo,
        number=number,
        base_branch=base_branch,
        base_repo_key=_repo_key_from_git_url(base_repo_url),
    )


def _parse_github_pull_request_url(url: str) -> tuple[str, str, int]:
    value = url.strip()
    match = GITHUB_PULL_REQUEST_RE.match(value)
    if match is None:
        raise TextDiffError("Only GitHub pull request URLs are supported.")
    owner = urllib.parse.unquote(match.group("owner"))
    repo = urllib.parse.unquote(match.group("repo")).removesuffix(".git")
    number = int(match.group("number"))
    return owner, repo, number


def _matching_remote(*, repo_path: Path, remote_repo_key: str) -> str | None:
    for remote, remote_url in _remote_urls(repo_path).items():
        try:
            repo_key = _repo_key_from_git_url(remote_url)
        except TextDiffError:
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


def _fetch_ref(
    *,
    repo_path: Path,
    remote: str,
    source_ref: str,
    target_ref: str,
) -> None:
    result = _run_git_text(
        repo_path,
        ["fetch", remote, f"+{source_ref}:{target_ref}"],
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        if message == "":
            message = f"git fetch failed with exit code {result.returncode}."
        raise TextDiffError(message)


def _run_git_text(
    repo_path: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=check,
        capture_output=True,
        text=True,
    )


def _repo_key_from_git_url(url: str) -> str:
    stripped = url.strip()
    if stripped == "":
        raise TextDiffError("Remote URL is empty.")
    if stripped.startswith("git@"):
        without_user = stripped.removeprefix("git@")
        host, separator, path = without_user.partition(":")
        if separator == "":
            raise TextDiffError(f"Unsupported Git remote URL: {url}")
        return _repo_key(host=host, path=path)

    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme in {"http", "https", "ssh", "git"}:
        host = parsed.hostname or ""
        path = parsed.path
        return _repo_key(host=host, path=path)
    raise TextDiffError(f"Unsupported Git remote URL: {url}")


def _repo_key(*, host: str, path: str) -> str:
    normalized_host = host.lower().strip()
    normalized_path = urllib.parse.unquote(path).strip().strip("/")
    normalized_path = normalized_path.removesuffix(".git")
    if normalized_host == "" or normalized_path == "":
        raise TextDiffError("Git remote URL is missing host or path.")
    return f"{normalized_host}/{normalized_path.lower()}"
