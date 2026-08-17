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
from typing import Literal, TypeIs, override

from dirdiff.backend.base import (
    BUILTIN_SIDES,
    BranchSelection,
    DefaultBaseSelection,
    RefChoices,
    RefMetadata,
    RepoDiff,
    RepoDiffPath,
    SideName,
    StructuredRemoteBranchRef,
    WorkspaceBackendProtocol,
    display_name_for_repo_paths,
    git_executable,
)
from dirdiff.engines import DirdiffError

__all__ = [
    "GitBackend",
    "preferred_review_selection",
    "ref_choices",
]

_LARGE_CHANGED_LINES_LAZY_THRESHOLD = 5000


def _is_branch_selection(
    selection: DefaultBaseSelection,
) -> TypeIs[BranchSelection]:
    return "source" in selection


def _local_default_base_branch_name(metadata: RefMetadata) -> str:
    """Return local main/master for local-only default-base selection."""
    if "main" in metadata["local_branches"]:
        return "main"
    if "master" in metadata["local_branches"]:
        return "master"
    return ""


def _default_branch_review_remote_name(metadata: RefMetadata) -> str:
    """Pick the default-base remote, or empty when the choice is ambiguous."""
    upstream = metadata["upstreams"].get(metadata["current_branch"], "")
    for remote_name in sorted(metadata["remote_names"], key=len, reverse=True):
        if upstream.startswith(f"{remote_name}/"):
            return remote_name
    if "origin" in metadata["remote_names"]:
        return "origin"
    if len(metadata["remote_names"]) == 1:
        return metadata["remote_names"][0]
    return ""


def ref_choices(metadata: RefMetadata) -> RefChoices:
    """Shape one metadata snapshot into `/api/repo-refs` control choices.

    Pure derivation: callers read the snapshot once and may share it with
    other derivations so every response reflects the same repository state.
    """
    return {
        "builtins": ["HEAD", "index", "worktree"],
        "local_branches": list(metadata["local_branches"]),
        "remotes": list(metadata["remote_names"]),
        "remote_branches": [
            {
                "structured": structured,
                "gitref": f"{structured['remote']}/{structured['branch']}",
            }
            for structured in metadata["remote_branches"]
        ],
    }


def preferred_review_selection(
    metadata: RefMetadata, *, base_selection: DefaultBaseSelection
) -> BranchSelection:
    """Choose the initial review branch relative to the selected base.

    Pure derivation from one metadata snapshot. Callers pass the base
    actually shown to the user (the saved main branch or the derived
    default) so the review choice never defaults to the base itself.
    """
    branch_names = metadata["local_branches"]
    if branch_names == []:
        return {"source": "local", "branch": ""}

    normalized_base = _local_default_base_branch_name(metadata)
    if _is_branch_selection(base_selection):
        normalized_base = base_selection["branch"].strip()
    current = metadata["current_branch"]

    if current != "" and current != normalized_base:
        return {"source": "local", "branch": current}

    for branch_name in branch_names:
        if branch_name != normalized_base:
            return {"source": "local", "branch": branch_name}

    fallback_branch = current if current != "" else branch_names[0]
    return {"source": "local", "branch": fallback_branch}


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
            [git_executable(), "rev-parse", "--show-toplevel"],
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
            [git_executable(), *args],
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
            [git_executable(), *args],
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
            [git_executable(), "rev-parse", "--verify", f"{side}^{{commit}}"],
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
        normalized = side.strip()
        if normalized == "":
            raise DirdiffError("Diff side is required.")
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
            [git_executable(), "diff", "--name-only"],
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
            [git_executable(), "ls-files"],
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

    def read_ref_metadata(self) -> RefMetadata:
        """Read one consistent branch/remote metadata snapshot.

        Two Git reads (`remote` and one `for-each-ref` across `refs/heads`
        and `refs/remotes`, whose `%(HEAD)` marker also identifies the
        current branch) produce every value the branch-control derivations
        need, so `/api/repo-defaults` and `/api/repo-refs` are computed from
        a single repository observation instead of many repeated Git
        commands. Without a repository the snapshot is empty rather than an
        error, preserving the empty-control behavior of the former per-item
        readers.
        """
        if self.repo_root is None:
            return {
                "current_branch": "",
                "local_branches": [],
                "remote_names": [],
                "remote_branches": [],
                "upstreams": {},
                "remote_head_branches": {},
            }
        remote_result = self._run_git_text(["remote"], check=False)
        remote_names = sorted(
            {
                line.strip()
                for line in remote_result.stdout.splitlines()
                if line.strip() != ""
            }
            if remote_result.returncode == 0
            else set()
        )
        refs_result = self._run_git_text(
            [
                "for-each-ref",
                "--format=%(HEAD)%00%(refname)%00%(upstream:short)%00%(symref)",
                "refs/heads",
                "refs/remotes",
            ],
            check=False,
        )

        current_branch = ""
        local_branches: set[str] = set()
        upstreams: dict[str, str] = {}
        remote_branches: list[StructuredRemoteBranchRef] = []
        remote_head_branches: dict[str, str] = {}
        # Longest configured name first so a remote named "a/b" wins over "a"
        # when splitting refs/remotes/<remote>/<branch>.
        remotes_longest_first = sorted(remote_names, key=len, reverse=True)
        heads_prefix = "refs/heads/"
        remotes_prefix = "refs/remotes/"
        lines = (
            refs_result.stdout.splitlines()
            if refs_result.returncode == 0
            else []
        )
        for line in lines:
            head_marker, _, rest = line.partition("\x00")
            refname, _, rest = rest.partition("\x00")
            upstream, _, symref = rest.partition("\x00")
            if refname.startswith(heads_prefix):
                branch = refname.removeprefix(heads_prefix)
                local_branches.add(branch)
                # `%(HEAD)` is `*` only on the checked-out local branch, so a
                # detached HEAD leaves `current_branch` empty like before.
                if head_marker == "*":
                    current_branch = branch
                if upstream != "":
                    upstreams[branch] = upstream
                continue
            if not refname.startswith(remotes_prefix):
                continue
            qualified = refname.removeprefix(remotes_prefix)
            remote_name = next(
                (
                    name
                    for name in remotes_longest_first
                    if qualified.startswith(f"{name}/")
                ),
                "",
            )
            if remote_name == "":
                # A ref under an unconfigured remote is not a control choice.
                continue
            branch = qualified.removeprefix(f"{remote_name}/")
            if branch == "HEAD":
                target_prefix = f"{remotes_prefix}{remote_name}/"
                if symref.startswith(target_prefix):
                    remote_head_branches[remote_name] = symref.removeprefix(
                        target_prefix
                    )
                continue
            remote_branches.append({"remote": remote_name, "branch": branch})
        remote_branches.sort(key=lambda ref: (ref["remote"], ref["branch"]))
        return {
            "current_branch": current_branch,
            "local_branches": sorted(local_branches),
            "remote_names": remote_names,
            "remote_branches": remote_branches,
            "upstreams": upstreams,
            "remote_head_branches": remote_head_branches,
        }

    def default_base_selection(
        self, metadata: RefMetadata
    ) -> DefaultBaseSelection:
        """Choose the initial branch-review base from one metadata snapshot.

        The choice itself is pure; the only Git access is the
        `git remote show` fallback for a remote whose local
        `refs/remotes/<remote>/HEAD` symref is absent. That fallback may
        contact the network and cost seconds, exactly as before this
        consolidation; replacing it with a local guess is a user-visible
        behavior change that needs explicit approval first.
        """
        if metadata["remote_names"] != []:
            default_remote = _default_branch_review_remote_name(metadata)
            if default_remote == "":
                return {"kind": "error", "error": "heuristic_fail"}
            base_branch = metadata["remote_head_branches"].get(
                default_remote, ""
            )
            if base_branch == "":
                base_branch = self._remote_show_head_branch_name(default_remote)
            if base_branch == "":
                return {"kind": "error", "error": "heuristic_fail"}
            return {
                "source": "remote",
                "remote": default_remote,
                "branch": base_branch,
            }

        base_branch = _local_default_base_branch_name(metadata)
        if base_branch == "":
            return {"kind": "error", "error": "heuristic_fail"}
        return {"source": "local", "branch": base_branch}

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

    @override
    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str, str]:
        """Resolve branch labels once into immutable capture commits."""
        base_name = base_selection["branch"]
        base_branch = (
            base_name
            if base_selection["source"] == "local"
            else f"{base_selection['remote']}/{base_name}"
        )
        base_ref = (
            f"refs/heads/{base_name}"
            if base_selection["source"] == "local"
            else f"refs/remotes/{base_selection['remote']}/{base_name}"
        )
        branch_name = review_selection["branch"]
        branch = (
            branch_name
            if review_selection["source"] == "local"
            else f"{review_selection['remote']}/{branch_name}"
        )
        branch_ref = (
            f"refs/heads/{branch_name}"
            if review_selection["source"] == "local"
            else f"refs/remotes/{review_selection['remote']}/{branch_name}"
        )

        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        expressions = (f"{base_ref}^{{commit}}", f"{branch_ref}^{{commit}}")
        labels = (base_branch, branch)
        resolved = subprocess.run(
            [
                git_executable(),
                "cat-file",
                "--batch-check=%(objectname) %(objecttype)",
            ],
            cwd=self.repo_root,
            check=False,
            input="\n".join(expressions) + "\n",
            capture_output=True,
            text=True,
        )
        records = resolved.stdout.splitlines()
        if resolved.returncode != 0 or len(records) != 2:
            raise DirdiffError(
                f"Could not resolve branch refs {base_branch} and {branch}."
            )
        commit_ids: list[str] = []
        for label, record in zip(labels, records, strict=True):
            object_id, separator, object_type = record.partition(" ")
            if separator == "" or object_type != "commit":
                raise DirdiffError(f"Unknown Git ref: {label}")
            commit_ids.append(object_id)
        base_commit, branch_commit = commit_ids
        merge_base = self._run_git_text(
            ["merge-base", base_commit, branch_commit],
            check=False,
        )
        if merge_base.returncode != 0 or merge_base.stdout.strip() == "":
            raise DirdiffError(
                f"Could not find a merge base between {base_branch} and {branch}."
            )
        return base_branch, merge_base.stdout.strip(), branch, branch_commit

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
    ) -> tuple[dict[str, tuple[int, int]], int | None, int | None]:
        """Return authoritative per-path counts from Git's NUL output.

        Rename records use an empty path in the count token followed by old and
        new paths; the result is keyed by the new path used by manifest. Binary
        and malformed records are absent because they provide no line counts.
        """
        tokens = output.split(b"\0")
        if tokens != [] and tokens[-1] == b"":
            tokens = tokens[:-1]
        counts: dict[str, tuple[int, int]] = {}
        added_total = 0
        removed_total = 0
        has_binary = False
        index = 0
        while index < len(tokens):
            parts = tokens[index].split(b"\t", 2)
            index += 1
            if len(parts) != 3:
                raise DirdiffError("Git returned malformed numstat output.")
            added_raw, removed_raw, path_raw = parts
            if added_raw == b"-" or removed_raw == b"-":
                has_binary = True
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
            added_total += line_count[0]
            removed_total += line_count[1]
        return (
            counts,
            None if has_binary else added_total,
            None if has_binary else removed_total,
        )

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
    def repo_diff(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> RepoDiff:
        """Return Git-changed paths and aggregate tracked line counts.

        `show_untracked` applies when the worktree is the right side. The left
        side may already be a frozen commit id supplied by Snapshot capture;
        callers must not need to retain the symbolic `HEAD` spelling merely to
        include untracked worktree files.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        if left == right:
            return RepoDiff((), 0, 0)

        # --no-abbrev keeps the raw records' object ids full-length so they
        # can serve as exact per-side content identity during capture.
        diff_args = ["diff", "--raw", "--no-abbrev", "--numstat", "-z", "-M"]
        if "worktree" in {left, right}:
            other = right if left == "worktree" else left
            if other != "index":
                diff_args.append(other)
            if left == "worktree":
                diff_args.insert(1, "-R")
        elif "index" in {left, right}:
            other = right if left == "index" else left
            diff_args.insert(1, "--cached")
            if other != "HEAD":
                diff_args.append(other)
            if left == "index":
                diff_args.insert(1, "-R")
        else:
            diff_args.extend((left, right))
        combined_output = self._run_git(diff_args).stdout

        # Git emits every raw record before the numstat section. Rebuild the
        # existing name-status grammar so the established path parser remains
        # the single authority for rename/copy and one-sided File identity.
        def object_id_or_none(raw: bytes) -> str | None:
            """Read one raw-record object id; all-zero ids mean no identity."""
            text = raw.decode("ascii")
            return None if set(text) <= {"0"} else text

        tokens = combined_output.split(b"\0")
        raw_index = 0
        name_status_tokens: list[bytes] = []
        record_object_ids: list[tuple[str | None, str | None]] = []
        while raw_index < len(tokens) and tokens[raw_index].startswith(b":"):
            raw_fields = tokens[raw_index][1:].split(b" ")
            raw_index += 1
            if len(raw_fields) != 5 or raw_fields[-1] == b"":
                raise DirdiffError("Git returned malformed raw diff output.")
            status = raw_fields[-1]
            path_count = 2 if status[:1] in {b"R", b"C"} else 1
            raw_paths = tokens[raw_index : raw_index + path_count]
            if len(raw_paths) != path_count or b"" in raw_paths:
                raise DirdiffError("Git returned a truncated raw diff record.")
            record_object_ids.append(
                (
                    object_id_or_none(raw_fields[2]),
                    object_id_or_none(raw_fields[3]),
                )
            )
            name_status_tokens.append(status)
            name_status_tokens.extend(raw_paths)
            raw_index += path_count

        entries = self._parse_name_status_output(
            b"\0".join([*name_status_tokens, b""])
        )
        # The parser emits exactly one entry per rebuilt record in order, so
        # the collected side object ids pair positionally; the strict zip
        # fails loudly if that one-to-one contract ever breaks.
        entries = [
            replace(
                entry,
                left_object_id=object_ids[0]
                if entry.left_path is not None
                else None,
                right_object_id=object_ids[1]
                if entry.right_path is not None
                else None,
            )
            for entry, object_ids in zip(
                entries, record_object_ids, strict=True
            )
        ]
        numstat_output = b"\0".join(tokens[raw_index:])
        line_counts_by_path, added_lines, removed_lines = (
            self._parse_numstat_output(numstat_output)
        )
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
        return RepoDiff(
            tuple(
                sorted(
                    entries,
                    key=lambda entry: (entry.display_name, entry.change_type),
                )
            ),
            added_lines,
            removed_lines,
        )

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

        git_target = f":./{path}" if side == "index" else f"{side}:{path}"
        # cat-file blob returns exact object bytes and rejects non-blob
        # targets (a gitlink resolves to a commit, which `git show` would
        # render as a formatted log instead of File content), matching the
        # batch loader's blob-only contract.
        result = self._run_git(["cat-file", "blob", git_target], check=False)
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

    @override
    def load_versions(
        self, requests: tuple[tuple[str, SideName], ...]
    ) -> tuple[bytes | DirdiffError, ...]:
        """Load captured sides with one Git object-content subprocess.

        Worktree bytes are read directly. Immutable tree and index paths are
        sent as NUL-terminated object expressions to one `cat-file --batch`
        invocation, so exact File names never become pathspecs or argv entries.
        Missing sides remain individual `DirdiffError` results in request order.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        results: list[bytes | DirdiffError | None] = [None] * len(requests)
        object_requests: list[tuple[int, bytes, str]] = []
        for index, (path, side) in enumerate(requests):
            if side == "worktree":
                try:
                    results[index] = self.load_version(path, side)
                except DirdiffError as exc:
                    results[index] = exc
            else:
                target_label = (
                    f":./{path}" if side == "index" else f"{side}:{path}"
                )
                object_requests.append(
                    (index, target_label.encode(), target_label)
                )

        if object_requests != []:
            process = subprocess.run(
                [git_executable(), "cat-file", "--batch", "-z"],
                cwd=self.repo_root,
                check=False,
                input=b"\0".join(
                    target for _index, target, _label in object_requests
                )
                + b"\0",
                capture_output=True,
            )
            if process.returncode != 0:
                details = process.stderr.decode().strip()
                raise DirdiffError(f"Git object batch failed: {details}")
            offset = 0
            for index, target, label in object_requests:
                missing = target + b" missing\n"
                if process.stdout.startswith(missing, offset):
                    results[index] = DirdiffError(
                        f"Git could not load {label}: File is missing."
                    )
                    offset += len(missing)
                    continue
                header_end = process.stdout.find(b"\n", offset)
                if header_end < 0:
                    raise DirdiffError("Git returned a truncated object batch.")
                fields = process.stdout[offset:header_end].split(b" ")
                if len(fields) != 3:
                    raise DirdiffError(
                        "Git returned malformed object metadata."
                    )
                _object_id, object_type, raw_size = fields
                try:
                    size = int(raw_size)
                except ValueError as exc:
                    raise DirdiffError(
                        "Git returned an invalid object size."
                    ) from exc
                content_start = header_end + 1
                content_end = content_start + size
                if process.stdout[content_end : content_end + 1] != b"\n":
                    raise DirdiffError("Git returned a truncated object body.")
                if object_type == b"blob":
                    results[index] = process.stdout[content_start:content_end]
                else:
                    results[index] = DirdiffError(
                        f"Git could not load {label}: expected a File blob, "
                        f"got {object_type.decode('ascii')}."
                    )
                offset = content_end + 1
            if offset != len(process.stdout):
                raise DirdiffError(
                    "Git returned unexpected trailing object data."
                )

        assert all(result is not None for result in results)
        return tuple(result for result in results if result is not None)
