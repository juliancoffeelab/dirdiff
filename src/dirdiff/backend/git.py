"""Git-backed implementation of `WorkspaceBackendProtocol`.

`GitBackend` is responsible for talking to Git: discovering the repository,
listing refs, resolving branch-review sides, listing changed paths, and loading
file versions.  It returns backend metadata and text only; rendering and API
response shaping happen outside this module.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from dirdiff.backend.base import (
    BUILTIN_SIDES,
    BranchSelection,
    DefaultBaseSelection,
    RefChoices,
    RemoteBranchRef,
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackendProtocol,
    _decode_text,
    display_name_for_repo_paths,
)

__all__ = [
    "GitBackend",
    "git_diff_args_with_direction",
]


def _git_tree_spec(side: SideName) -> str:
    """Translate built-in dirdiff side names into Git revision syntax."""
    if side == "head":
        return "HEAD"
    return side


def git_diff_args_with_direction(
    *,
    left: SideName,
    right: SideName,
    kind: Literal["--name-status"],
) -> tuple[list[str], bool]:
    """Build Git diff args and whether output direction must be swapped."""
    if "worktree" in {left, right}:
        other = right if left == "worktree" else left
        args = (
            ["diff", kind, "-z", "-M"]
            if other == "index"
            else ["diff", kind, "-z", "-M", _git_tree_spec(other)]
        )
        return args, left == "worktree"
    if "index" in {left, right}:
        other = right if left == "index" else left
        args = (
            ["diff", "--cached", kind, "-z", "-M"]
            if other == "head"
            else [
                "diff",
                "--cached",
                kind,
                "-z",
                "-M",
                _git_tree_spec(other),
            ]
        )
        return args, left == "index"
    return [
        "diff",
        kind,
        "-z",
        "-M",
        _git_tree_spec(left),
        _git_tree_spec(right),
    ], False


class GitBackend(WorkspaceBackendProtocol):
    """Load refs, paths, and file contents from one Git repository."""

    def __init__(
        self, repo_root: Path | None, *, cwd: Path | None = None
    ) -> None:
        """Bind the backend to a discovered repo root and caller working directory."""
        self._repo_root = repo_root.resolve() if repo_root is not None else None
        self._cwd = (cwd or Path.cwd()).resolve()

    @property
    def repo_root(self) -> Path | None:
        """Expose the repository root used for path normalization."""
        return self._repo_root

    @property
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
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
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
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=check,
            capture_output=True,
            text=True,
        )

    def normalize_side(self, raw_side: str) -> SideName:
        """Normalize built-in diff sides while validating explicit Git refs."""
        side = raw_side.strip()
        if not side:
            raise TextDiffError("Diff side is required.")
        if side in BUILTIN_SIDES:
            return side
        if self.repo_root is None:
            raise TextDiffError("Custom refs require a Git repo.")

        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{side}^{{commit}}"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if resolved.returncode != 0:
            raise TextDiffError(f"Unknown Git ref: {side}")
        return side

    def discover_default_path(self) -> str:
        """Find a path suitable for single-file startup mode."""
        if self.repo_root is None:
            raise TextDiffError(
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
            if line.strip() and not line.endswith("/")
        ]
        if candidates:
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
            if line.strip() and not line.endswith("/")
        ]
        if tracked_candidates:
            return tracked_candidates[0]

        raise TextDiffError("No files found in the current Git repo.")

    def current_branch_name(self) -> str:
        """Read the current branch name, returning empty string when detached."""
        if self.repo_root is None:
            return ""
        result = self._run_git_text(["branch", "--show-current"], check=False)
        return result.stdout.strip()

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

    def branch_upstream_name(self, branch_name: str) -> str:
        """Read the upstream ref configured for a local branch."""
        normalized_branch = branch_name.strip()
        if not normalized_branch or self.repo_root is None:
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
        if upstream_remote:
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

    def default_base_selection(self) -> DefaultBaseSelection:
        """Choose the initial branch-review base from local Git metadata."""
        if self.list_remote_names():
            default_remote = self._default_branch_review_remote_name()
            if not default_remote:
                return {"kind": "error", "error": "heuristic_fail"}
            base_branch = self._remote_head_branch_name(default_remote)
            if not base_branch:
                return {"kind": "error", "error": "heuristic_fail"}
            return {
                "source": "remote",
                "remote": default_remote,
                "branch": base_branch,
            }

        base_branch = self._local_default_base_branch_name()
        if not base_branch:
            return {"kind": "error", "error": "heuristic_fail"}
        return {"source": "local", "branch": base_branch}

    def preferred_review_selection(
        self, *, base_selection: DefaultBaseSelection | None = None
    ) -> BranchSelection:
        """Choose the initial review branch relative to the selected base."""
        branch_names = self.list_branch_names()
        if not branch_names:
            return {"source": "local", "branch": ""}

        normalized_base = self._local_default_base_branch_name()
        if base_selection is not None and "source" in base_selection:
            branch_selection = cast("BranchSelection", base_selection)
            normalized_base = branch_selection["branch"].strip()
        current = self.current_branch_name()

        if current and current != normalized_base:
            return {"source": "local", "branch": current}

        for branch_name in branch_names:
            if branch_name != normalized_base:
                return {"source": "local", "branch": branch_name}

        return {"source": "local", "branch": current or branch_names[0]}

    def _branch_selection_ref(self, selection: BranchSelection) -> str:
        """Collapse a BranchSelection to a git ref for resolve_branch_diff_sides."""
        if selection["source"] == "local":
            return selection["branch"]
        return f"{selection['remote']}/{selection['branch']}"

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
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            raise TextDiffError(
                f"Could not find a merge base between {normalized_base} and {normalized_branch}."
            )
        return normalized_base, merge_base.stdout.strip(), normalized_branch

    def _diff_args(
        self,
        *,
        left: SideName,
        right: SideName,
        kind: Literal["--name-status"],
    ) -> list[str]:
        """Build a Git diff command for the requested side pair."""
        args, _ = git_diff_args_with_direction(
            left=left,
            right=right,
            kind=kind,
        )
        return args

    def _parse_name_status_output(self, output: bytes) -> list[RepoDiffPath]:
        """Parse NUL-delimited `git diff --name-status` output."""
        tokens = output.split(b"\0")
        if tokens and not tokens[-1]:
            tokens = tokens[:-1]

        entries: list[RepoDiffPath] = []
        index = 0
        while index < len(tokens):
            status_token = tokens[index].decode("utf-8")
            index += 1
            if not status_token:
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
                )
            )

        return entries

    def _parse_numstat_output(
        self, output: bytes
    ) -> dict[str, tuple[int, int]]:
        """Parse NUL-delimited `git diff --numstat` output by display path."""
        tokens = output.split(b"\0")
        if tokens and not tokens[-1]:
            tokens = tokens[:-1]

        counts: dict[str, tuple[int, int]] = {}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            parts = token.decode("utf-8").split("\t")
            if len(parts) != 3:
                continue
            added_raw, removed_raw, path = parts
            if added_raw == "-" or removed_raw == "-":
                if path == "" and index + 1 < len(tokens):
                    index += 2
                continue
            try:
                line_count = (int(added_raw), int(removed_raw))
            except ValueError:
                continue
            if path == "":
                if index + 1 >= len(tokens):
                    continue
                index += 1
                right_path = tokens[index].decode("utf-8")
                index += 1
                counts[right_path] = line_count
                continue
            counts[path] = line_count
        return counts

    def _list_untracked_worktree_paths(self) -> list[RepoDiffPath]:
        """List untracked worktree files as one-sided repo diff paths."""
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")

        result = self._run_git(
            ["ls-files", "--others", "--exclude-standard", "-z"]
        )
        tokens = result.stdout.split(b"\0")
        if tokens and not tokens[-1]:
            tokens = tokens[:-1]

        entries: list[RepoDiffPath] = []
        for token in tokens:
            path = token.decode("utf-8")
            if not path:
                continue
            entries.append(
                RepoDiffPath(
                    left_path=None,
                    right_path=path,
                    display_name=path,
                    change_type="add",
                    untracked=True,
                )
            )
        return entries

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        """List Git-changed paths and attach line statistics."""
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        if left == right:
            return []

        diff_args = self._diff_args(
            left=left, right=right, kind="--name-status"
        )

        diff_output = self._run_git(diff_args)
        entries = self._parse_name_status_output(diff_output.stdout)
        numstat_args = list(diff_args)
        numstat_args[numstat_args.index("--name-status")] = "--numstat"
        numstat_output = self._run_git(numstat_args)
        line_counts = self._parse_numstat_output(numstat_output.stdout)
        entries_with_counts: list[RepoDiffPath] = []
        for entry in entries:
            path = entry.right_path or entry.left_path or entry.display_name
            line_count = line_counts.get(path)
            if line_count is None:
                entries_with_counts.append(entry)
                continue
            added_lines, removed_lines = line_count
            entries_with_counts.append(
                RepoDiffPath(
                    left_path=entry.left_path,
                    right_path=entry.right_path,
                    display_name=entry.display_name,
                    change_type=entry.change_type,
                    changed_lines=added_lines + removed_lines,
                    added_lines=added_lines,
                    removed_lines=removed_lines,
                )
            )
        if show_untracked and left == "head" and right == "worktree":
            entries_with_counts.extend(self._list_untracked_worktree_paths())
        return sorted(
            entries_with_counts,
            key=lambda entry: (entry.display_name, entry.change_type),
        )

    def normalize_repo_path(self, raw_path: str) -> str:
        """Normalize and validate a repo-relative path without escaping root."""
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        if not raw_path.strip():
            raise TextDiffError("Repo path is required.")
        if raw_path.endswith("/"):
            raise TextDiffError("Repo path must point to a file.")

        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise TextDiffError("Use a repo-relative path.")

        normalized = candidate.as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise TextDiffError("Repo path must stay inside the repo.")
        return normalized

    def load_version(self, path: str, side: SideName) -> TextVersion:
        """Load one file version from worktree, index, or Git tree."""
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")

        if side == "worktree":
            file_path = self.repo_root / path
            if not file_path.exists():
                return TextVersion(label=side, exists=False, text=None)
            if file_path.is_dir():
                raise TextDiffError(f"{path} is a directory, not a file.")
            return TextVersion(
                label=side,
                exists=True,
                text=_decode_text(
                    file_path.read_bytes(), label=f"{side}:{path}"
                ),
            )

        git_target = (
            f"HEAD:{path}"
            if side == "head"
            else f":{path}"
            if side == "index"
            else f"{side}:{path}"
        )
        result = self._run_git(["show", git_target], check=False)
        if result.returncode != 0:
            return TextVersion(label=side, exists=False, text=None)
        return TextVersion(
            label=side,
            exists=True,
            text=_decode_text(result.stdout, label=f"{side}:{path}"),
        )
