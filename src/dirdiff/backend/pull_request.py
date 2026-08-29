"""Prepare forge changes for capture from a marked Git repository.

## Public interface

`prepare_pull_request` accepts a GitHub Pull Request or GitLab Merge Request URL
and the active repository marks. It returns `PreparedPullRequest`, which names
the matching mark, the trimmed URL, and immutable commits for capture.

## Purpose and boundaries

Preparation is separate from manifest loading because it may call a forge API
and fetch refs. It updates remote-tracking refs but never checks out a branch.
The server chooses when to run it; Snapshot capture and diff rendering consume
the result without repeating forge work.
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

from dirdiff.backend.base import git_executable
from dirdiff.engines import DirdiffError

__all__ = [
    "PreparedPullRequest",
    "prepare_pull_request",
]

GITHUB_PULL_REQUEST_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:[/?#].*)?$"
)
"""Recognize public GitHub Pull Request URLs accepted by preparation.

The match retains owner, repository, and decimal Pull Request number. Only
HTTPS `github.com` URLs qualify; trailing path, query, and fragment text does
not change the Pull Request identity.
"""
GITLAB_MERGE_REQUEST_RE = re.compile(
    r"^(?P<scheme>https?)://(?P<host>[^/]+)/(?P<project>.+)/-/merge_requests/(?P<iid>\d+)(?:[/?#].*)?$"
)
"""Recognize GitLab Merge Request URLs accepted by preparation.

The pattern permits HTTP or HTTPS and self-hosted domains. It retains the
scheme, host, nested project path, and decimal project-local id needed for the
matching GitLab API endpoint.
"""


class RepoMarkLike(Protocol):
    """Provide the repository facts needed to prepare a Pull Request.

    Pass records from the repository registry to `prepare_pull_request`; it
    reads the stable mark id and local path through this protocol.

    The protocol deliberately excludes display metadata, Git state, and Room
    identity. Pull Request preparation discovers the matching remote itself.
    """

    @property
    def id(self) -> int:
        """Stable active-mark id returned with prepared commit state.

        Reading it must perform no repository or database work. The value is
        later used to capture from the same marked repository.

        # Usage

        `prepare_pull_request` reads this only after one of the mark's remotes
        matches the forge repository, then writes it to `PreparedPullRequest`.
        Reading the property must not fail.
        """
        ...

    @property
    def path(self) -> str:
        """Absolute workspace path whose configured Git remotes are inspected.

        Preparation may fetch into this repository but never changes its
        checked-out branch. Reading the property itself performs no I/O.

        # Usage

        `prepare_pull_request` converts this value to `Path` before inspecting
        remotes and fetching refs. Reading the property must not fail.
        """
        ...


@dataclass(frozen=True)
class PreparedPullRequest:
    """Complete Pull Request state prepared for manifest observation.

    # Usage

    `prepare_pull_request` returns this value to the server. The mark id selects
    the repository and the two commit ids become the immutable capture sides;
    the URL remains the Pull Request correspondence value.

    The value contains no Branch Review selections, mutable refs, or rendered
    state.
    """

    project_id: int
    """Active repository mark whose remote matched the forge base repository.

    The HTTP caller sends this stable id into Snapshot capture for the commits
    prepared alongside it.
    """

    pull_request_url: str
    """Trimmed forge URL retained as Pull Request Room correspondence.

    Recapture uses this identity but requires newly prepared commits separately.
    """

    left_commit: str
    """Complete merge-base object id frozen after both remote refs are fetched.

    Snapshot capture verifies and loads this commit instead of resolving a mutable
    target branch again.
    """

    right_commit: str
    """Complete fetched review-head object id used as the right capture side.

    It freezes the prepared state even if the forge ref advances afterward.
    """


@dataclass(frozen=True)
class _GitHubPullRequest:
    """GitHub API facts needed to fetch and identify one Pull Request.

    GitHub URL and API parsing create this private value before matching a local
    repository and fetching the review ref.

    It is not returned outside Pull Request preparation.
    """

    url: str
    """Trimmed GitHub URL returned as the public correspondence identity.

    API loading does not replace it with a separately reported forge URL.
    """

    owner: str
    """Percent-decoded account or organization segment used in the API path.

    It comes from the recognized URL, not from untrusted response display data.
    """

    repo: str
    """Percent-decoded repository segment used in the GitHub API path.

    A terminal `.git` spelling from the URL is removed before the API call.
    """

    number: int
    """Decimal repository-local id parsed from the URL.

    Preparation uses it for both the API lookup and `pull/<number>/head` refspec.
    """

    base_branch: str
    """Non-empty target branch reported by GitHub's base payload.

    Preparation fetches it into the matching remote namespace before merge-base
    calculation.
    """

    base_repo_key: str
    """Case-normalized host and path derived from the base repository URL.

    Local remote URL spellings are normalized to the same form for matching.
    """


@dataclass(frozen=True)
class _GitLabMergeRequest:
    """GitLab API facts needed to fetch and identify one Merge Request.

    GitLab URL and API parsing create this private value before matching a local
    repository and fetching the review ref.

    It is not a public manifest input or forge-generic record.
    """

    url: str
    """Trimmed GitLab URL returned as the public correspondence identity.

    It retains the self-hosted scheme and host selected by the caller.
    """

    scheme: str
    """Recognized `http` or `https` scheme reused for the API endpoint.

    Preparation does not force self-hosted GitLab installations onto HTTPS.
    """

    host: str
    """URL authority used for both API access and repository identity.

    Normalized remote matching lowercases it later through `_repo_key`.
    """

    project_path: str
    """Percent-decoded nested project path without a terminal `.git`.

    API loading percent-encodes the complete value as one project identifier.
    """

    iid: int
    """Decimal project-local id parsed from the Merge Request URL.

    Preparation uses it in the API path and `merge-requests/<iid>/head` refspec.
    """

    target_branch: str
    """Non-empty target branch reported by the GitLab API.

    It is fetched into the matching remote namespace before merge-base calculation.
    """

    target_repo_key: str
    """Case-normalized host and target project path used for remote matching.

    This implementation assumes the URL project identifies the target repository.
    """


def prepare_pull_request(
    *,
    url: str,
    repo_marks: Iterable[RepoMarkLike],
) -> PreparedPullRequest:
    """Fetch a forge pull request into a matching marked repository.

    The URL must point at a supported GitHub pull request or GitLab merge
    request.  The request base repository must match at least one configured
    remote in the registered repo list.  The review ref is fetched into a
    remote-tracking ref without checking out or creating a local branch. This
    operation also freezes the merge base and review head to commit ids so
    manifest does not perform Pull Request preparation.

    # Parameters

    - `url`: GitHub Pull Request or GitLab Merge Request URL to prepare.
    - `repo_marks`: Current registered repositories eligible for remote matching.

    # Usage

    The Pull Request preparation endpoint passes the active marks from
    `RepoMarkStore.list`. Recapture calls it again because forge refs may have
    advanced since the previous Snapshot.

    # Failures

    The iterable is consumed once. Unsupported URLs, forge or transport errors,
    missing expected response fields, absent matching remotes, failed fetches,
    and commit resolution failures raise `DirdiffError` with the concrete
    reason. Malformed JSON and response fields of the wrong container type
    propagate their parsing or type exception.
    """
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
    """Prepare a GitHub Pull Request in its matching marked repository.

    # Parameters

    - `url`: Already recognized GitHub Pull Request URL.
    - `repo_marks`: Repository marks searched in iteration order.

    The first mark with a matching remote receives updated base and Pull Request
    remote-tracking refs. The function does not check out a branch. API, fetch,
    matching, and commit-resolution failures raise `DirdiffError`.
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
        _fetch_refs(
            repo_path=repo_path,
            remote=remote,
            refspecs=(
                (
                    pull_request.base_branch,
                    f"refs/remotes/{remote}/{pull_request.base_branch}",
                ),
                (
                    f"pull/{pull_request.number}/head",
                    f"refs/remotes/{remote}/{review_branch}",
                ),
            ),
        )
        base_ref = f"refs/remotes/{remote}/{pull_request.base_branch}"
        review_ref = f"refs/remotes/{remote}/{review_branch}"
        merge_base = _run_git_text(
            repo_path,
            ["merge-base", base_ref, review_ref],
            check=False,
        )
        if merge_base.returncode != 0 or merge_base.stdout.strip() == "":
            raise DirdiffError(
                "Could not find a merge base for the prepared pull request."
            )
        review_commit = _run_git_text(
            repo_path,
            ["rev-parse", "--verify", f"{review_ref}^{{commit}}"],
            check=False,
        )
        if review_commit.returncode != 0 or review_commit.stdout.strip() == "":
            raise DirdiffError(
                "Could not read the prepared pull request head commit."
            )
        return PreparedPullRequest(
            project_id=mark.id,
            pull_request_url=pull_request.url,
            left_commit=merge_base.stdout.strip(),
            right_commit=review_commit.stdout.strip(),
        )
    raise DirdiffError(
        "No marked repository has a remote for this pull request."
    )


def _prepare_gitlab_merge_request(
    *,
    url: str,
    repo_marks: Iterable[RepoMarkLike],
) -> PreparedPullRequest:
    """Prepare a GitLab Merge Request in its matching marked repository.

    # Parameters

    - `url`: Already recognized GitLab Merge Request URL.
    - `repo_marks`: Repository marks searched in iteration order.

    The first matching remote receives updated target and Merge Request
    remote-tracking refs. The checked-out branch is untouched. API, fetch,
    matching, and commit-resolution failures raise `DirdiffError`.
    """
    merge_request = _load_gitlab_merge_request(url)
    for mark in repo_marks:
        repo_path = Path(mark.path)
        remote = _matching_remote(
            repo_path=repo_path,
            remote_repo_key=merge_request.target_repo_key,
        )
        if remote is None:
            continue
        review_branch = f"merge-requests/{merge_request.iid}"
        _fetch_refs(
            repo_path=repo_path,
            remote=remote,
            refspecs=(
                (
                    merge_request.target_branch,
                    f"refs/remotes/{remote}/{merge_request.target_branch}",
                ),
                (
                    f"merge-requests/{merge_request.iid}/head",
                    f"refs/remotes/{remote}/{review_branch}",
                ),
            ),
        )
        base_ref = f"refs/remotes/{remote}/{merge_request.target_branch}"
        review_ref = f"refs/remotes/{remote}/{review_branch}"
        merge_base = _run_git_text(
            repo_path,
            ["merge-base", base_ref, review_ref],
            check=False,
        )
        if merge_base.returncode != 0 or merge_base.stdout.strip() == "":
            raise DirdiffError(
                "Could not find a merge base for the prepared merge request."
            )
        review_commit = _run_git_text(
            repo_path,
            ["rev-parse", "--verify", f"{review_ref}^{{commit}}"],
            check=False,
        )
        if review_commit.returncode != 0 or review_commit.stdout.strip() == "":
            raise DirdiffError(
                "Could not read the prepared merge request head commit."
            )
        return PreparedPullRequest(
            project_id=mark.id,
            pull_request_url=merge_request.url,
            left_commit=merge_base.stdout.strip(),
            right_commit=review_commit.stdout.strip(),
        )
    raise DirdiffError(
        "No marked repository has a remote for this merge request."
    )


def _load_github_pull_request(url: str) -> _GitHubPullRequest:
    """Load and validate the GitHub facts needed for preparation.

    The parsed URL selects GitHub's Pull Request API endpoint. The call has a
    20-second timeout and requires non-empty base branch and repository URL
    fields.

    # Failures

    HTTP and URL errors, missing expected keys, and empty required strings
    become `DirdiffError`. Malformed JSON and response values of an unexpected
    container type propagate their parsing or type exception.
    """
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
    except KeyError as exc:
        raise DirdiffError(
            "GitHub pull request response is missing base data."
        ) from exc
    if not isinstance(base_branch, str) or base_branch.strip() == "":
        raise DirdiffError(
            "GitHub pull request response has an empty base branch."
        )
    if not isinstance(base_repo_url, str) or base_repo_url.strip() == "":
        raise DirdiffError(
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
    """Parse a GitHub Pull Request URL into its API identity.

    Owner and repository components are percent-decoded, and a terminal `.git`
    is removed from the repository. Unsupported shapes raise `DirdiffError`.

    # Returns

    - First, the percent-decoded GitHub owner.
    - Second, the percent-decoded repository name without a terminal `.git`.
    - Third, the numeric Pull Request number within that repository.
    """
    value = url.strip()
    match = GITHUB_PULL_REQUEST_RE.match(value)
    if match is None:
        raise DirdiffError("Only GitHub pull request URLs are supported.")
    owner = urllib.parse.unquote(match.group("owner"))
    repo = urllib.parse.unquote(match.group("repo")).removesuffix(".git")
    number = int(match.group("number"))
    return owner, repo, number


def _load_gitlab_merge_request(url: str) -> _GitLabMergeRequest:
    """Load and validate the GitLab facts needed for preparation.

    The URL supplies the host and project-local Merge Request id for a 20-second
    API call. The response must contain a non-empty target branch.

    # Failures

    HTTP and URL errors, a missing target branch, and an empty target branch
    become `DirdiffError`. Malformed JSON and response values of an unexpected
    container type propagate their parsing or type exception.
    """
    parsed = _parse_gitlab_merge_request_url(url)
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
    except KeyError as exc:
        raise DirdiffError(
            "GitLab merge request response is missing target branch data."
        ) from exc
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
        target_repo_key=_repo_key(host=host, path=project_path),
    )


def _parse_gitlab_merge_request_url(url: str) -> tuple[str, str, str, int]:
    """Parse a GitLab Merge Request URL into API and repository identity parts.

    Percent-encoded project names are decoded and a terminal `.git` is removed.
    Unsupported shapes raise `DirdiffError` rather than producing partial keys.

    # Returns

    - First, the URL scheme used for the GitLab API call.
    - Second, the GitLab host.
    - Third, the percent-decoded project path without a terminal `.git`.
    - Fourth, the numeric Merge Request id within that project.
    """
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
    """Return the first configured remote matching one normalized forge repo.

    # Parameters

    - `repo_path`: Marked Git repository whose remote URLs are inspected.
    - `remote_repo_key`: Normalized forge host and repository path to match.

    Configured URLs that cannot identify a repository do not match. `None`
    means no usable remote has the requested identity.

    # Returns

    - The first matching remote name in Git configuration order.
    - `None`: No configured remote URL identifies `remote_repo_key`. Pull
      Request preparation must fail rather than choose an unrelated remote.
    """
    for remote, remote_url in _remote_urls(repo_path).items():
        try:
            repo_key = _repo_key_from_git_url(remote_url)
        except DirdiffError:
            continue
        if repo_key == remote_repo_key:
            return remote
    return None


def _remote_urls(repo_path: Path) -> dict[str, str]:
    """Read one usable fetch URL for each configured Git remote.

    A failed Git config query returns no remotes. Malformed lines and empty
    names or values are excluded. Later duplicate entries for one remote replace
    earlier ones in Git's output order.

    # Returns

    - Each key is a non-empty configured remote name.
    - Each value is that remote's non-empty fetch URL. A later duplicate name
      replaces its earlier URL in Git's output order.
    - An empty mapping means Git reported no usable entries or the nonchecking
      configuration command failed; the caller cannot distinguish those cases.
    """
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


def _fetch_refs(
    *,
    repo_path: Path,
    remote: str,
    refspecs: tuple[tuple[str, str], ...],
) -> None:
    """Fetch every (source, target) refspec in one Git invocation.

    One fetch negotiates the connection and pack once for all requested
    refs; preparation previously ran a separate unbounded network fetch per
    ref.

    # Parameters

    - `repo_path`: Marked repository in which Git updates refs.
    - `remote`: Configured remote name used as the fetch source.
    - `refspecs`: Non-empty source and local-target ref pairs fetched forcibly.

    Git failure raises `DirdiffError` with stderr, stdout, or the exit status.
    """
    assert refspecs != (), (
        "an empty refspec set would degrade to an unbounded default fetch"
    )
    result = _run_git_text(
        repo_path,
        [
            "fetch",
            remote,
            *(f"+{source}:{target}" for source, target in refspecs),
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
    """Run Git in one marked repository and capture decoded output.

    # Parameters

    - `repo_path`: Working directory for the Git process.
    - `args`: Git arguments after the executable name.
    - `check`: Whether a nonzero exit raises `subprocess.CalledProcessError`.

    # Returns

    - `stdout` and `stderr` contain Git's complete decoded text streams.
    - `returncode` is zero after a checked call succeeds. With `check=False`, it
      retains Git's nonzero status, which callers inspect before the streams.
    """
    return subprocess.run(
        [git_executable(), *args],
        cwd=repo_path,
        check=check,
        capture_output=True,
        text=True,
    )


def _repo_key_from_git_url(url: str) -> str:
    """Return a repository identity used to match forge and local remotes.

    GitHub preparation passes this function its `base.repo.html_url`. Both
    GitHub and GitLab preparation pass it each URL reported by the marked
    repository's Git configuration. GitLab constructs the forge-side key from
    its parsed host and project path through `_repo_key`. Different URL
    spellings for the same host and repository produce the same key.

    # Example

    >>> github_base_url = "https://github.com/openai/codex"
    >>> configured_remote_url = "git@github.com:openai/codex.git"
    >>> _repo_key_from_git_url(github_base_url)
    'github.com/openai/codex'
    >>> _repo_key_from_git_url(configured_remote_url)
    'github.com/openai/codex'
    >>> (
    ...     _repo_key_from_git_url(github_base_url)
    ...     == _repo_key_from_git_url(configured_remote_url)
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
        return _repo_key(host=host, path=path)

    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme in {"http", "https", "ssh", "git"}:
        host = parsed.hostname or ""
        path = parsed.path
        return _repo_key(host=host, path=path)
    raise DirdiffError(f"Unsupported Git remote URL: {url}")


def _repo_key(*, host: str, path: str) -> str:
    """Normalize a forge host and repository path for remote matching.

    # Parameters

    - `host`: URL hostname, normalized case-insensitively.
    - `path`: URL-decoded namespace and repository path, optionally ending `.git`.

    Empty normalized parts raise `DirdiffError`. The result contains no scheme,
    credentials, leading slash, trailing slash, or `.git` suffix.
    """
    normalized_host = host.lower().strip()
    normalized_path = urllib.parse.unquote(path).strip().strip("/")
    normalized_path = normalized_path.removesuffix(".git")
    if normalized_host == "" or normalized_path == "":
        raise DirdiffError("Git remote URL is missing host or path.")
    return f"{normalized_host}/{normalized_path.lower()}"
