"""Read Git workspaces through the backend contract.

## Public interface

`GitBackend` discovers a worktree, resolves sides, lists changed Files, and
loads exact bytes. `ref_choices` and `preferred_review_selection` turn one ref
metadata read into the values used by the Branch Review controls.

## Purpose and boundaries

This is the only backend module that runs ordinary Git workspace commands.
It returns repository paths, ref facts, and bytes through
`WorkspaceBackendProtocol`; Snapshot capture decides when to retain those
facts. Content decoding, format selection, rendering, and HTTP validation
happen after this boundary.
"""

from __future__ import annotations

import os
import posixpath
import stat
import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal, TypeIs, override

from dirdiff.backend.base import (
    BUILTIN_SIDES,
    SYMLINK_MODE,
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
"""Changed-line count above which Git-backed Files start lazy.

`repo_diff` compares each tracked File's numstat additions plus removals against
this strict threshold. Binary Files have no numeric count and are unaffected.
"""


def _is_branch_selection(
    selection: DefaultBaseSelection,
) -> TypeIs[BranchSelection]:
    """Narrow a successful default-base result to a branch selection.

    Default failures have a `kind` discriminator, while both usable branch
    variants have `source`. Callers use this check before reading branch fields.
    """
    return "source" in selection


def _local_default_base_branch_name(metadata: RefMetadata) -> str:
    """Choose a conventional local base when no remotes are configured.

    `main` takes precedence over `master`. An empty result means neither branch
    appears in the supplied metadata snapshot.
    """
    if "main" in metadata["local_branches"]:
        return "main"
    if "master" in metadata["local_branches"]:
        return "master"
    return ""


def _default_branch_review_remote_name(metadata: RefMetadata) -> str:
    """Choose the remote used to derive the initial Branch Review base.

    The current branch's upstream wins, then `origin`, then the sole configured
    remote. Multiple remaining choices produce an empty result for the HUD to
    reject instead of inventing a remote.
    """
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

    # Usage

    Pass the value from `GitBackend.read_ref_metadata`. The server shares that
    same value with default-selection derivation so both controls describe one
    caller operation.

    # Failures

    This function performs no I/O and raises no expected domain failure. The
    typed record must contain every `RefMetadata` field.
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

    # Parameters

    - `metadata`: One repository observation shared with base selection.
    - `base_selection`: The usable or failed base result shown by the HUD.

    # Usage

    Pass the same metadata used to choose `base_selection`. The server publishes
    the result as the initial review-side control value.

    # Failures

    Empty branch metadata produces an empty local selection rather than an
    exception. If every branch matches the base, the result may name that branch.
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
    """Expose one Git repository through `WorkspaceBackendProtocol`.

    # Usage

    Construct it with `discover`, normalize user input with `normalize_side`,
    then pass those sides to `repo_diff`. Load the returned path sides with
    `load_version` or `load_versions`. Branch Review uses
    `read_ref_metadata`, `default_base_selection`, and
    `resolve_branch_diff_sides` before that comparison flow.

    The instance stores its repository and command directories. It does not
    cache Git state, change refs, publish Snapshots, or interpret loaded bytes.
    """

    def __init__(
        self, repo_root: Path | None, *, cwd: Path | None = None
    ) -> None:
        """Bind the backend to a repository location and command directory.

        # Parameters

        - `repo_root`: Repository root, or `None` when discovery found no Git repo.
        - `cwd`: Stable command directory, defaulting to the process directory.

        # Usage

        Prefer `discover` so implicit repository lookup follows one path. Direct
        construction is useful when the caller already has an exact root.

        # Failures

        Construction normalizes paths but does not inspect repository state.
        Filesystem resolution failures propagate.
        """
        self._repo_root = repo_root.resolve() if repo_root is not None else None
        selected_cwd = cwd if cwd is not None else Path.cwd()
        self._cwd = selected_cwd.resolve()

    @property
    @override
    def repo_root(self) -> Path | None:
        """The Git worktree root supplied to this backend, if any.

        Uses a stored variable, never changes and never does any work.

        # Usage
        You will want to construct `GitBackend` using `GitBackend.discover`
        class method. It uses explicit `repo_root` when provided, or calls git
        to infer a repository root when omitted.

        Then you can just access the field.

        # Returns
        - An absolute path to the repository root.
        - `None` if we we failed to find repository root.
        """
        # TODO: probably extremely unsafe.
        # We must ensure that `GitBackend.discover` returns a real root we
        # expect, and asserts invalid combinations.
        # TODO: we should not accept None in `GitBackend.discover`.
        return self._repo_root

    @property
    @override
    def cwd(self) -> Path:
        """Return the stored absolute command directory without filesystem work.

        The value is fixed at construction and may sit outside `repo_root`.
        Renderers use it when launching tools whose behavior follows the user's
        original working directory.

        # Usage

        Read this after construction when an external renderer needs the same
        command directory. Access performs no I/O and has no expected failure.
        """
        return self._cwd

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        repo_root: Path | None = None,
    ) -> GitBackend:
        """Discover a Git repository from an explicit root or working directory.

        # Parameters

        - `cwd`: Directory used for discovery and later renderer commands.
        - `repo_root`: Explicit repository root that skips Git discovery.

        # Usage

        Server and test setup normally pass `cwd` and let Git find the worktree.
        Pass `repo_root` only when the caller already selected the repository.

        # Failures

        A failed implicit discovery returns a backend whose `repo_root` is
        `None`; it is not an exception. Filesystem resolution or process-launch
        failures propagate from `Path.resolve` or `subprocess.run`.
        """
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
        """Run Git inside this backend's repository root and return bytes.

        # Parameters

        - `args`: Git arguments after the executable name.
        - `check`: Whether a nonzero exit raises `subprocess.CalledProcessError`.

        # Returns

        - `stdout` and `stderr` contain the process's complete captured byte
          streams without decoding.
        - `returncode` is zero after a checked invocation succeeds. With
          `check=False`, it retains Git's nonzero status for the caller to test.

        A missing repository raises `DirdiffError` before starting a process.
        """
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
        """Run Git inside this backend's repository root and return decoded text.

        # Parameters

        - `args`: Git arguments after the executable name.
        - `check`: Whether a nonzero exit raises `subprocess.CalledProcessError`.

        # Returns

        - `stdout` and `stderr` contain the complete streams decoded through
          Python's text-mode subprocess handling.
        - `returncode` is zero after a checked invocation succeeds. With
          `check=False`, it retains Git's nonzero status for the caller to test.

        The subprocess uses Python's text decoding. A missing repository raises
        `DirdiffError` before starting it.
        """
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
        """Normalize a built-in side or verify an explicit commit-like Git ref.

        Whitespace is stripped. Built-in sides need no repository, while custom
        refs require one and must resolve through `rev-parse`.

        # Usage

        Normalize each user-facing side once, then pass the result back to this
        backend's comparison and loading methods.

        # Failures

        Blank input, a custom ref without a repository, or an unknown ref raises
        `DirdiffError`.
        """
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

        # Usage

        Snapshot capture uses this after side normalization when it must replace
        a symbolic commit-like side with immutable identity.

        # Failures

        Blank input, mutable `index` or `worktree`, a missing repository, and
        unknown or non-commit refs raise `DirdiffError`.
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
        """Choose the first modified File, or otherwise the first tracked File.

        # Usage

        Single-File startup calls this when the user supplied no path. The
        returned repository-relative File can be passed to `normalize_repo_path`.

        # Failures

        Missing repository state and repositories with no modified or tracked
        File raise `DirdiffError`. Git command failures can appear as an empty
        candidate set because these discovery probes do not require zero exit.
        """
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
        """Read the branch and remote metadata used by one caller operation.

        Two Git reads (`remote` and one `for-each-ref` across `refs/heads`
        and `refs/remotes`, whose `%(HEAD)` marker also identifies the
        current branch) produce every value the branch-control derivations
        need. Callers share the returned value between default and choice
        derivation instead of repeating those reads. Repository state can change
        between the two Git commands; this method does not lock the repository.

        # Usage

        Read once per defaults or choices operation, then pass the returned
        record to `default_base_selection`, `preferred_review_selection`, and
        `ref_choices` as needed.

        # Failures

        Without a repository, or when either non-checking Git probe fails, the
        affected fields are empty rather than exceptional. Process-launch and
        decoding failures still propagate.
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

        # Usage

        Pass a fresh `read_ref_metadata` result. The server stores or publishes
        the returned selection before deriving the preferred review side.

        # Failures

        If metadata cannot identify one safe base, return
        `{"kind": "error", "error": "heuristic_fail"}`. When a remote lacks a
        local HEAD symbolic ref, `git remote show` may contact the network;
        command failure also produces the error result. A caller-supplied
        metadata record naming remotes on a backend without a repository raises
        `DirdiffError` when that remote lookup is needed.
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
        """Ask a remote for its HEAD branch when no local symbolic ref exists.

        `git remote show` may contact the network. Command failure, absent output,
        and Git's `(unknown)` marker all produce an empty result.
        """
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
        """Resolve branch labels once into immutable capture commits.

        # Parameters

        - `base_selection`: Local or remote base branch selected by the caller.
        - `review_selection`: Local or remote branch whose changes are reviewed.

        # Usage

        Branch Review capture calls this after the HUD supplies two structured
        selections. Use the returned labels for presentation and the two commit
        ids for immutable capture.

        # Returns

        - First, the base display label preserving its local or remote spelling.
        - Second, the immutable merge-base commit used as capture's left side.
        - Third, the review display label preserving its selected spelling.
        - Fourth, the immutable review-head commit used as capture's right side.

        # Failures

        Missing repositories, unresolved refs, non-commit targets, and branches
        without a merge base raise `DirdiffError`.
        """
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
        """Parse Git's NUL-delimited status records into File path pairs.

        Rename and copy records consume two UTF-8 paths; ordinary records consume
        one. The returned order matches Git output, and pure renames carry their
        backend lazy override. A truncated final record stops parsing and returns
        the complete entries read before it; invalid UTF-8 raises
        `UnicodeDecodeError`.
        """
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
        records are absent from the mapping and make both aggregate totals
        unavailable. Malformed records raise `DirdiffError`; invalid UTF-8 paths
        raise `UnicodeDecodeError`.

        # Returns

        - First, per-path counts keyed by the manifest's current path. In each
          mapping value, the first item is added lines and the second is removed
          lines; binary paths are absent.
        - Second, total additions over all records, or `None` when any binary
          record prevents Git from supplying complete line totals.
        - Third, total removals over all records, or `None` under that same
          binary condition. Both totals always have equal presence.
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
        """List untracked, non-ignored worktree Files as right-only additions.

        Git's NUL-delimited path order is preserved. A missing repository or Git
        command failure propagates instead of returning an incomplete list.
        """
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
                    right_mode=self.file_mode(path, "worktree"),
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

        # Parameters

        - `left`: Normalized Git side used as the left input.
        - `right`: Normalized Git side used as the right input.
        - `show_untracked`: Whether a right-side worktree adds untracked Files.

        # Usage

        Pass sides normalized by this instance. Snapshot capture walks the
        returned paths in order and may reuse their object ids while loading.

        # Failures

        A missing repository, failed Git command, malformed raw or numstat
        output, or invalid UTF-8 path raises. Aggregate counts cover the tracked
        diff; one binary record makes both totals unavailable.
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
            """Decode a raw Git object id when that File side has an object.

            Git writes an all-zero field for an absent or worktree-only object;
            that sentinel becomes `None`. Other fields must be ASCII object ids.

            # Returns

            - The decoded object id for a stored Git object.
            - `None`: Git supplied its all-zero sentinel, so this absent or
              worktree-only side has no stored object id for capture to retain.
            """
            text = raw.decode("ascii")
            return None if set(text) <= {"0"} else text

        tokens = combined_output.split(b"\0")
        raw_index = 0
        name_status_tokens: list[bytes] = []
        record_side_facts: list[tuple[str, str, str | None, str | None]] = []
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
            record_side_facts.append(
                (
                    raw_fields[0].decode("ascii"),
                    raw_fields[1].decode("ascii"),
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
        # the collected side modes and object ids pair positionally; the strict
        # zip fails loudly if that one-to-one contract ever breaks.
        entries = [
            replace(
                entry,
                left_mode=side_facts[0]
                if entry.left_path is not None
                else None,
                right_mode=side_facts[1]
                if entry.right_path is not None
                else None,
                left_object_id=side_facts[2]
                if entry.left_path is not None
                else None,
                right_object_id=side_facts[3]
                if entry.right_path is not None
                else None,
            )
            for entry, side_facts in zip(
                entries, record_side_facts, strict=True
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
        """Normalize the relative POSIX spelling used by Git File operations.

        Blank, directory-shaped, absolute, and repo-escaping paths raise
        `DirdiffError`. Inner `.` and `..` components are collapsed so link
        targets receive one canonical repository identity before loop checks.

        # Usage

        Pass paths returned by Git or formed while following a repository link.
        The returned spelling is safe to use as a literal Git path.

        # Failures

        Missing repository state and the rejected shapes above raise
        `DirdiffError`.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        if raw_path.strip() == "":
            raise DirdiffError("Repo path is required.")
        if raw_path.endswith("/"):
            raise DirdiffError("Repo path must point to a file.")

        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise DirdiffError("Use a repo-relative path.")

        normalized = posixpath.normpath(candidate.as_posix())
        if normalized.startswith("../") or normalized == "..":
            raise DirdiffError("Repo path must stay inside the repo.")
        if normalized == ".":
            raise DirdiffError("Repo path must point to a file.")
        return normalized

    @override
    def file_mode(self, path: str, side: SideName) -> str:
        """Return one normalized path's Git-compatible File mode.

        Worktree inspection uses `lstat`, so a link is classified without
        following it. Index and tree sides ask Git for the exact literal path;
        directories, gitlinks, missing paths, and malformed output are refused.

        # Parameters

        - `path`: Normalized repository path to inspect without following it.
        - `side`: Worktree, index, or Git tree/ref containing the path.

        # Failures

        Raises `DirdiffError` when the path does not identify a regular file,
        executable file, or symbolic link on the selected side.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        normalized = self.normalize_repo_path(path)
        if side == "worktree":
            file_path = self.repo_root / normalized
            try:
                mode = file_path.lstat().st_mode
            except OSError as exc:
                raise DirdiffError(
                    f"Could not inspect worktree file {normalized}: {exc}"
                ) from exc
            if stat.S_ISLNK(mode):
                return SYMLINK_MODE
            if not stat.S_ISREG(mode):
                raise DirdiffError(
                    f"{normalized} is not a regular file or symbolic link."
                )
            return "100755" if mode & stat.S_IXUSR else "100644"

        literal_pathspec = f":(literal){normalized}"
        if side == "index":
            result = self._run_git(
                ["ls-files", "--stage", "-z", "--", literal_pathspec],
                check=False,
            )
            label = f"index:{normalized}"
        else:
            result = self._run_git(
                ["ls-tree", "-z", side, "--", literal_pathspec],
                check=False,
            )
            label = f"{side}:{normalized}"
        if result.returncode != 0:
            details = result.stderr.decode().strip()
            message = f"Git could not inspect {label}"
            if details != "":
                message = f"{message}: {details}"
            raise DirdiffError(message)
        records = [record for record in result.stdout.split(b"\0") if record]
        if len(records) != 1:
            raise DirdiffError(f"Git could not find File {label}.")
        fields, separator, returned_path = records[0].partition(b"\t")
        parts = fields.split(b" ")
        if separator == b"" or len(parts) < 3:
            raise DirdiffError(f"Git returned malformed mode data for {label}.")
        try:
            git_mode = parts[0].decode("ascii")
            object_type = (
                "blob" if side == "index" else parts[1].decode("ascii")
            )
            decoded_path = returned_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DirdiffError(
                f"Git returned invalid mode data for {label}."
            ) from exc
        if decoded_path != normalized or object_type != "blob":
            raise DirdiffError(f"{label} is not a capturable File.")
        if git_mode not in {"100644", "100755", SYMLINK_MODE}:
            raise DirdiffError(f"Unsupported File mode {git_mode} for {label}.")
        return git_mode

    @override
    def file_size(self, path: str, side: SideName) -> int:
        """Return exact blob size without loading one normalized Git File.

        Worktree inspection uses `lstat`, so regular-file size does not follow
        links; link size is the byte length of Git's raw link payload. Index and
        tree sides ask `cat-file --batch-check` for blob metadata only.

        # Parameters

        - `path`: Normalized repository path to inspect without reading it.
        - `side`: Worktree, index, or Git tree/ref containing the path.

        # Failures

        Missing repository state, missing paths, unsupported File kinds,
        non-blob Git objects, and malformed Git output raise `DirdiffError`.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        normalized = self.normalize_repo_path(path)
        if side == "worktree":
            file_path = self.repo_root / normalized
            try:
                metadata = file_path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    return len(os.readlink(os.fsencode(file_path)))
                if stat.S_ISREG(metadata.st_mode):
                    return metadata.st_size
            except OSError as exc:
                raise DirdiffError(
                    f"Could not inspect worktree file {normalized}: {exc}"
                ) from exc
            raise DirdiffError(
                f"{normalized} is not a regular file or symbolic link."
            )

        git_target = (
            f":./{normalized}" if side == "index" else f"{side}:{normalized}"
        )
        process = subprocess.run(
            [
                git_executable(),
                "cat-file",
                "--batch-check=%(objecttype) %(objectsize)",
                "-z",
            ],
            cwd=self.repo_root,
            check=False,
            input=git_target.encode() + b"\0",
            capture_output=True,
        )
        if process.returncode != 0:
            details = process.stderr.decode().strip()
            message = f"Git could not inspect {git_target}"
            if details != "":
                message = f"{message}: {details}"
            raise DirdiffError(message)
        fields = process.stdout.removesuffix(b"\n").split(b" ")
        if len(fields) != 2 or fields[0] != b"blob":
            raise DirdiffError(
                f"Git returned malformed size data for {git_target}."
            )
        try:
            size = int(fields[1])
        except ValueError as exc:
            raise DirdiffError(
                f"Git returned invalid size data for {git_target}."
            ) from exc
        if size < 0:
            raise DirdiffError(
                f"Git returned invalid size data for {git_target}."
            )
        return size

    @override
    def load_version(self, path: str, side: SideName) -> bytes:
        """Return exact contents from the worktree, index, or a Git tree.

        On every side, a symlink loads as Git records it, a blob holding the raw
        link target.

        # Parameters

        - `path`: Normalized repository path reported by `repo_diff` or reached
          through a captured link.
        - `side`: Worktree, index, or Git tree/ref to read from.

        # Usage

        Load a present side path from this backend. Capture may use
        `load_versions` instead when several outer sides are needed.

        # Failures

        Missing repository state, missing or directory-shaped ordinary worktree
        content, unreadable regular-file bytes or links, and non-blob or absent
        Git objects raise `DirdiffError`.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")

        if side == "worktree":
            file_path = self.repo_root / path
            # Git stores a symlink as a blob holding its target string, and
            # the tree/index sides already load exactly that through
            # cat-file. Read the link itself before any following check:
            # exists() and is_dir() follow the link, so a directory target
            # would be rejected and a broken link would read as missing,
            # while both are ordinary content to Git.
            if file_path.is_symlink():
                try:
                    return os.readlink(os.fsencode(file_path))
                except OSError as exc:
                    raise DirdiffError(
                        f"Could not read worktree link {path}: {exc}"
                    ) from exc
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

        # Usage

        Snapshot capture supplies normalized `(path, side)` pairs and zips the
        returned tuple to them by position.

        # Returns

        - Each item corresponds to one input pair, preserving input order so
          capture can attach each result without another identity key.
        - A `bytes` item is the exact content loaded for that File side.
        - A `DirdiffError` item describes only that unavailable side;
          successful sibling results remain available.

        # Failures

        Missing individual sides become `DirdiffError` values in input order.
        Missing repository state, batch-process failure, or malformed batch
        output raises `DirdiffError` for the complete operation.
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
