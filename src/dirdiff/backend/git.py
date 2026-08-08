"""Git-backed implementation of `WorkspaceBackendProtocol`.

`GitBackend` is responsible for talking to Git: discovering the repository,
listing refs, resolving branch-review sides, listing changed paths, and loading
file versions. It returns backend metadata and exact file contents; rendering,
text decoding, and API response shaping happen outside this module.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal, Optional, TypeIs, override

from dirdiff.backend.base import (
    BUILTIN_SIDES,
    BranchSelection,
    DefaultBaseSelection,
    RefChoices,
    RemoteBranchRef,
    RepoDiffPath,
    SideName,
    WorkspaceBackendProtocol,
    display_name_for_repo_paths,
)
from dirdiff.engines import DirdiffError

__all__ = [
    "GitBackend",
]

_LARGE_CHANGED_LINES_LAZY_THRESHOLD = 1000


def _is_branch_selection(
    selection: DefaultBaseSelection,
) -> TypeIs[BranchSelection]:
    return "source" in selection


def _git_diff_args(
    *,
    left: SideName,
    right: SideName,
    kind: Literal["--name-status", "--numstat"],
) -> list[str]:
    """Build a Git diff command whose output follows left-to-right order.

    Git's worktree and index forms accept only the commit-backed side as an
    argument. When either mutable side is requested on the left, `-R` makes Git
    emit path status and line totals in the caller's requested direction.
    """

    if "worktree" in {left, right}:
        other = right if left == "worktree" else left
        args = (
            ["diff", kind, "-z", "-M"]
            if other == "index"
            else ["diff", kind, "-z", "-M", other]
        )
        if left == "worktree":
            args.insert(1, "-R")
        return args
    if "index" in {left, right}:
        other = right if left == "index" else left
        args = (
            ["diff", "--cached", kind, "-z", "-M"]
            if other == "HEAD"
            else [
                "diff",
                "--cached",
                kind,
                "-z",
                "-M",
                other,
            ]
        )
        if left == "index":
            args.insert(1, "-R")
        return args
    return [
        "diff",
        kind,
        "-z",
        "-M",
        left,
        right,
    ]


class GitBackend(WorkspaceBackendProtocol):
    """Load refs, paths, and file contents from one Git repository."""

    def __init__(
        self, repo_root: Path | None, *, cwd: Path | None = None
    ) -> None:
        """Bind the backend to a discovered repo root and caller working directory."""
        self._repo_root = repo_root.resolve() if repo_root is not None else None
        selected_cwd = cwd if cwd is not None else Path.cwd()
        self._cwd = selected_cwd.resolve()

    @property
    @override
    def repo_root(self) -> Path | None:
        """Expose the repository root used for path normalization."""
        return self._repo_root

    @property
    @override
    def cwd(self) -> Path:
        """Expose the command working directory for renderers."""
        return self._cwd

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        repo_root: Path | None = None,
    ) -> GitBackend:
        """Discover a Git repository from explicit root or current directory."""
        working_dir = (cwd or Path.cwd()).resolve()
        if repo_root is not None:
            return cls(Path(repo_root).expanduser().resolve(), cwd=working_dir)

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=working_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        discovered_root = (
            Path(result.stdout.strip()).resolve()
            if result.returncode == 0 and result.stdout.strip()
            else None
        )
        return cls(discovered_root, cwd=working_dir)

    def _run_git(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run Git inside this backend's repository root and return bytes."""
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=check,
            capture_output=True,
        )

    def _run_git_text(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run Git inside this backend's repository root and return text."""
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=check,
            capture_output=True,
            text=True,
        )

    @override
    def normalize_side(self, raw_side: str) -> SideName:
        """Normalize built-in diff sides while validating explicit Git refs."""
        side = raw_side.strip()
        if side == "":
            raise DirdiffError("Diff side is required.")
        if side in BUILTIN_SIDES:
            return side
        if self.repo_root is None:
            raise DirdiffError("Custom refs require a Git repo.")

        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{side}^{{commit}}"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if resolved.returncode != 0:
            raise DirdiffError(f"Unknown Git ref: {side}")
        return side

    def commit_id(self, side: SideName) -> str:
        """Return the immutable commit id named by one Git side.

        The caller may supply `HEAD`, a branch, a tag, or a commit expression.
        Mutable `index` and `worktree` sides and unknown refs are rejected with
        `DirdiffError`; the result is always a Git commit id.
        """
        normalized = self.normalize_side(side)
        if normalized in {"index", "worktree"}:
            raise DirdiffError(
                f"{normalized} does not identify an immutable Git commit."
            )
        result = self._run_git_text(
            ["rev-parse", "--verify", f"{normalized}^{{commit}}"],
            check=False,
        )
        commit_id = result.stdout.strip()
        if result.returncode != 0 or commit_id == "":
            raise DirdiffError(f"Unknown Git ref: {side}")
        return commit_id

    @override
    def discover_default_path(self) -> str:
        """Find a path suitable for single-file startup mode."""
        if self.repo_root is None:
            raise DirdiffError(
                "No Git repo found for automatic path discovery."
            )

        modified = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        candidates = [
            line.strip()
            for line in modified.stdout.splitlines()
            if line.strip() != "" and not line.endswith("/")
        ]
        if candidates != []:
            return candidates[0]

        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        tracked_candidates = [
            line.strip()
            for line in tracked.stdout.splitlines()
            if line.strip() != "" and not line.endswith("/")
        ]
        if tracked_candidates != []:
            return tracked_candidates[0]

        raise DirdiffError("No files found in the current Git repo.")

    @override
    def current_branch_name(self) -> str:
        """Read the current branch name, returning empty string when detached."""
        if self.repo_root is None:
            return ""
        result = self._run_git_text(["branch", "--show-current"], check=False)
        return result.stdout.strip()

    @override
    def list_branch_names(self) -> list[str]:
        """List local branch names sorted for stable API responses."""
        if self.repo_root is None:
            return []
        result = self._run_git_text(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
            check=False,
        )
        if result.returncode != 0:
            return []
        return sorted(
            {
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip()
            }
        )

    @override
    def list_remote_ref_names(self) -> list[str]:
        """List remote-tracking refs usable in freeform ref comparisons."""
        if self.repo_root is None:
            return []
        result = self._run_git_text(
            ["for-each-ref", "--format=%(refname:short)", "refs/remotes"],
            check=False,
        )
        if result.returncode != 0:
            return []
        return sorted(
            {
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and not line.strip().endswith("/HEAD")
            }
        )

    @override
    def list_remote_names(self) -> list[str]:
        """List configured Git remote names."""
        if self.repo_root is None:
            return []
        result = self._run_git_text(["remote"], check=False)
        if result.returncode != 0:
            return []
        return sorted(
            {
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip()
            }
        )

    def _list_remote_branch_names(self, remote_name: str) -> list[str]:
        """List branch names under one configured Git remote for list_ref_choices."""
        if self.repo_root is None:
            return []
        result = self._run_git_text(
            [
                "for-each-ref",
                "--format=%(refname)",
                f"refs/remotes/{remote_name}",
            ],
            check=False,
        )
        if result.returncode != 0:
            return []
        prefix = f"refs/remotes/{remote_name}/"
        return sorted(
            {
                line.strip().removeprefix(prefix)
                for line in result.stdout.splitlines()
                if line.strip().startswith(prefix)
                and line.strip().removeprefix(prefix) != "HEAD"
            }
        )

    @override
    def list_ref_choices(self) -> RefChoices:
        """Return split ref choices for compare-ref and branch-review controls."""
        remote_branches: list[RemoteBranchRef] = []
        for remote in self.list_remote_names():
            for branch in self._list_remote_branch_names(remote):
                remote_branches.append(
                    {
                        "structured": {
                            "remote": remote,
                            "branch": branch,
                        },
                        "gitref": f"{remote}/{branch}",
                    }
                )
        return {
            "builtins": ["HEAD", "index", "worktree"],
            "local_branches": self.list_branch_names(),
            "remotes": self.list_remote_names(),
            "remote_branches": remote_branches,
        }

    @override
    def branch_upstream_name(self, branch_name: str) -> str:
        """Read the upstream ref configured for a local branch."""
        normalized_branch = branch_name.strip()
        if normalized_branch == "" or self.repo_root is None:
            return ""
        result = self._run_git_text(
            [
                "for-each-ref",
                "--format=%(upstream:short)",
                f"refs/heads/{normalized_branch}",
            ],
            check=False,
        )
        return result.stdout.strip()

    def _remote_name_for_upstream(self, upstream: str) -> str:
        """Resolve the remote name from an upstream ref for default_base_selection."""
        for remote_name in sorted(
            self.list_remote_names(), key=len, reverse=True
        ):
            if upstream.startswith(f"{remote_name}/"):
                return remote_name
        return ""

    def _default_branch_review_remote_name(self) -> str:
        """Pick the remote used by default_base_selection, or empty if ambiguous."""
        current_branch = self.current_branch_name()
        upstream = self.branch_upstream_name(current_branch)
        upstream_remote = self._remote_name_for_upstream(upstream)
        if upstream_remote != "":
            return upstream_remote

        remote_names = self.list_remote_names()
        if "origin" in remote_names:
            return "origin"
        if len(remote_names) == 1:
            return remote_names[0]
        return ""

    def _remote_head_branch_name(self, remote_name: str) -> str:
        """Resolve a remote default branch for default_base_selection."""
        result = self._run_git_text(
            [
                "symbolic-ref",
                "--quiet",
                "--short",
                f"refs/remotes/{remote_name}/HEAD",
            ],
            check=False,
        )
        remote_head = result.stdout.strip()
        prefix = f"{remote_name}/"
        if result.returncode == 0 and remote_head.startswith(prefix):
            return remote_head.removeprefix(prefix)
        return self._remote_show_head_branch_name(remote_name)

    def _remote_show_head_branch_name(self, remote_name: str) -> str:
        """Read `git remote show` when local refs/remotes/<remote>/HEAD is absent."""
        result = self._run_git_text(
            ["remote", "show", remote_name],
            check=False,
        )
        if result.returncode != 0:
            return ""
        for line in result.stdout.splitlines():
            stripped = line.strip()
            prefix = "HEAD branch: "
            if stripped.startswith(prefix):
                branch_name = stripped.removeprefix(prefix).strip()
                return "" if branch_name == "(unknown)" else branch_name
        return ""

    def _local_default_base_branch_name(self) -> str:
        """Return local main/master for local-only default_base_selection."""
        branch_names = self.list_branch_names()
        if "main" in branch_names:
            return "main"
        if "master" in branch_names:
            return "master"
        return ""

    @override
    def default_base_selection(self) -> DefaultBaseSelection:
        """Choose the initial branch-review base from local Git metadata."""
        if self.list_remote_names() != []:
            default_remote = self._default_branch_review_remote_name()
            if default_remote == "":
                return {"kind": "error", "error": "heuristic_fail"}
            base_branch = self._remote_head_branch_name(default_remote)
            if base_branch == "":
                return {"kind": "error", "error": "heuristic_fail"}
            return {
                "source": "remote",
                "remote": default_remote,
                "branch": base_branch,
            }

        base_branch = self._local_default_base_branch_name()
        if base_branch == "":
            return {"kind": "error", "error": "heuristic_fail"}
        return {"source": "local", "branch": base_branch}

    @override
    def preferred_review_selection(
        self, *, base_selection: DefaultBaseSelection | None = None
    ) -> BranchSelection:
        """Choose the initial review branch relative to the selected base."""
        branch_names = self.list_branch_names()
        if branch_names == []:
            return {"source": "local", "branch": ""}

        normalized_base = self._local_default_base_branch_name()
        if base_selection is not None and _is_branch_selection(base_selection):
            normalized_base = base_selection["branch"].strip()
        current = self.current_branch_name()

        if current != "" and current != normalized_base:
            return {"source": "local", "branch": current}

        for branch_name in branch_names:
            if branch_name != normalized_base:
                return {"source": "local", "branch": branch_name}

        fallback_branch = current if current != "" else branch_names[0]
        return {"source": "local", "branch": fallback_branch}

    def _branch_selection_ref(self, selection: BranchSelection) -> str:
        """Collapse a BranchSelection to a git ref for resolve_branch_diff_sides."""
        if selection["source"] == "local":
            return selection["branch"]
        return f"{selection['remote']}/{selection['branch']}"

    @override
    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str]:
        """Resolve branch-review selections into merge-base and normalized refs."""
        base_branch = self._branch_selection_ref(base_selection)
        branch = self._branch_selection_ref(review_selection)
        normalized_base = self.normalize_side(base_branch)
        normalized_branch = self.normalize_side(branch)
        merge_base = self._run_git_text(
            ["merge-base", normalized_base, normalized_branch],
            check=False,
        )
        if merge_base.returncode != 0 or merge_base.stdout.strip() == "":
            raise DirdiffError(
                f"Could not find a merge base between {normalized_base} and {normalized_branch}."
            )
        return normalized_base, merge_base.stdout.strip(), normalized_branch

    def _parse_name_status_output(self, output: bytes) -> list[RepoDiffPath]:
        """Parse NUL-delimited `git diff --name-status` output."""
        tokens = output.split(b"\0")
        if tokens != [] and tokens[-1] == b"":
            tokens = tokens[:-1]

        entries: list[RepoDiffPath] = []
        index = 0
        while index < len(tokens):
            status_token = tokens[index].decode("utf-8")
            index += 1
            if status_token == "":
                continue

            change_kind = status_token[0]
            if change_kind in {"R", "C"}:
                if index + 1 >= len(tokens):
                    break
                left_path = tokens[index].decode("utf-8")
                right_path = tokens[index + 1].decode("utf-8")
                index += 2
                entries.append(
                    RepoDiffPath(
                        left_path=left_path,
                        right_path=right_path,
                        display_name=display_name_for_repo_paths(
                            left_path, right_path
                        ),
                        change_type="rename" if change_kind == "R" else "copy",
                        lazy_reason_override=(
                            "pure_renamed"
                            if change_kind == "R" and status_token[1:] == "100"
                            else None
                        ),
                    )
                )
                continue

            if index >= len(tokens):
                break
            path = tokens[index].decode("utf-8")
            index += 1

            current_left_path: str | None = path if change_kind != "A" else None
            current_right_path: str | None = (
                path if change_kind != "D" else None
            )
            change_type: Literal["modify", "add", "delete"]
            if change_kind == "A":
                change_type = "add"
            elif change_kind == "D":
                change_type = "delete"
            else:
                change_type = "modify"
            entries.append(
                RepoDiffPath(
                    left_path=current_left_path,
                    right_path=current_right_path,
                    display_name=display_name_for_repo_paths(
                        current_left_path, current_right_path
                    ),
                    change_type=change_type,
                    lazy_reason_override=None,
                )
            )

        return entries

    def _parse_numstat_output(
        self,
        output: bytes,
    ) -> dict[str, tuple[int, int]]:
        """Return authoritative per-path counts from Git's NUL output.

        Rename records use an empty path in the count token followed by old and
        new paths; the result is keyed by the new path used by manifest. Binary
        and malformed records are absent because they provide no line counts.
        """
        tokens = output.split(b"\0")
        if tokens != [] and tokens[-1] == b"":
            tokens = tokens[:-1]
        counts: dict[str, tuple[int, int]] = {}
        index = 0
        while index < len(tokens):
            parts = tokens[index].split(b"\t", 2)
            index += 1
            if len(parts) != 3:
                raise DirdiffError("Git returned malformed numstat output.")
            added_raw, removed_raw, path_raw = parts
            if added_raw == b"-" or removed_raw == b"-":
                if path_raw == b"":
                    if index + 1 >= len(tokens):
                        raise DirdiffError(
                            "Git returned a truncated numstat rename."
                        )
                    index += 2
                continue
            try:
                line_count = int(added_raw), int(removed_raw)
            except ValueError as exc:
                raise DirdiffError(
                    "Git returned nonnumeric numstat counts."
                ) from exc
            if path_raw == b"":
                if index + 1 >= len(tokens):
                    raise DirdiffError(
                        "Git returned a truncated numstat rename."
                    )
                index += 1
                path_raw = tokens[index]
                index += 1
            path = path_raw.decode("utf-8")
            counts[path] = line_count
        return counts

    def _list_untracked_worktree_paths(self) -> list[RepoDiffPath]:
        """List untracked worktree files as one-sided repo diff paths."""
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")

        result = self._run_git(
            ["ls-files", "--others", "--exclude-standard", "-z"]
        )
        tokens = result.stdout.split(b"\0")
        if tokens != [] and tokens[-1] == b"":
            tokens = tokens[:-1]

        entries: list[RepoDiffPath] = []
        for token in tokens:
            path = token.decode("utf-8")
            if path == "":
                continue
            entries.append(
                RepoDiffPath(
                    left_path=None,
                    right_path=path,
                    display_name=path,
                    change_type="add",
                    lazy_reason_override=None,
                    untracked=True,
                )
            )
        return entries

    @override
    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        """List Git-changed paths and their stable path metadata.

        `show_untracked` applies when the worktree is the right side. The left
        side may already be a frozen commit id supplied by Snapshot capture;
        callers must not need to retain the symbolic `HEAD` spelling merely to
        include untracked worktree files.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        if left == right:
            return []

        diff_args = _git_diff_args(left=left, right=right, kind="--name-status")

        diff_output = self._run_git(diff_args)
        entries = self._parse_name_status_output(diff_output.stdout)
        numstat_args = list(diff_args)
        numstat_args[numstat_args.index("--name-status")] = "--numstat"
        numstat_output = self._run_git(numstat_args).stdout
        line_counts_by_path = self._parse_numstat_output(numstat_output)
        entries = [
            replace(
                entry,
                lazy_reason_override="too_big",
            )
            if entry.lazy_reason_override is None
            and sum(
                line_counts_by_path.get(
                    entry.right_path or entry.left_path or "",
                    (0, 0),
                )
            )
            > _LARGE_CHANGED_LINES_LAZY_THRESHOLD
            else entry
            for entry in entries
        ]
        if show_untracked and right == "worktree":
            entries.extend(self._list_untracked_worktree_paths())
        return sorted(
            entries,
            key=lambda entry: (entry.display_name, entry.change_type),
        )

    @override
    def line_counts(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> tuple[Optional[int], Optional[int]]:
        """Return Git's aggregate totals for the selected diff.

        `show_untracked` can add Files to the manifest, but Git's numstat does
        not include them. The returned totals remain Git's tracked diff totals.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        if left == right:
            return 0, 0
        args = _git_diff_args(
            left=left,
            right=right,
            kind="--numstat",
        )
        output = self._run_git(args).stdout
        added = 0
        removed = 0
        tokens = output.split(b"\0")
        if tokens != [] and tokens[-1] == b"":
            tokens = tokens[:-1]
        index = 0
        while index < len(tokens):
            parts = tokens[index].split(b"\t", 2)
            index += 1
            if len(parts) != 3:
                raise DirdiffError("Git returned malformed numstat output.")
            added_raw, removed_raw, path_raw = parts
            if path_raw == b"":
                if index + 1 >= len(tokens):
                    raise DirdiffError(
                        "Git returned a truncated numstat rename."
                    )
                index += 2
            if added_raw == b"-" or removed_raw == b"-":
                return None, None
            try:
                added += int(added_raw)
                removed += int(removed_raw)
            except ValueError as exc:
                raise DirdiffError(
                    "Git returned nonnumeric numstat counts."
                ) from exc
        return added, removed

    @override
    def normalize_repo_path(self, raw_path: str) -> str:
        """Normalize and validate a repo-relative path without escaping root."""
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        if raw_path.strip() == "":
            raise DirdiffError("Repo path is required.")
        if raw_path.endswith("/"):
            raise DirdiffError("Repo path must point to a file.")

        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise DirdiffError("Use a repo-relative path.")

        normalized = candidate.as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise DirdiffError("Repo path must stay inside the repo.")
        return normalized

    @override
    def load_version(self, path: str, side: SideName) -> bytes:
        """Return exact contents from the worktree, index, or a Git tree.

        The path must identify a file reported by this backend. Missing or
        unreadable content raises `DirdiffError` with the underlying reason.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")

        if side == "worktree":
            file_path = self.repo_root / path
            if not file_path.exists():
                raise DirdiffError(f"Worktree file is missing: {path}")
            if file_path.is_dir():
                raise DirdiffError(f"{path} is a directory, not a file.")
            try:
                return file_path.read_bytes()
            except OSError as exc:
                raise DirdiffError(
                    f"Could not read worktree file {path}: {exc}"
                ) from exc

        git_target = (
            f"HEAD:{path}"
            if side == "HEAD"
            else f":{path}"
            if side == "index"
            else f"{side}:{path}"
        )
        result = self._run_git(["show", git_target], check=False)
        if result.returncode != 0:
            details = result.stderr.decode().strip()
            message = (
                f"Git could not load {git_target} "
                f"(exit code {result.returncode})"
            )
            if details != "":
                message = f"{message}: {details}"
            raise DirdiffError(message)
        return result.stdout
