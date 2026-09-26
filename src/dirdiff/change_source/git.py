"""Prepare Git refs and read one selected Git Tab's File sides.

## Public interface

`GitRepository` resolves refs and Branch Review sides without exposing File
storage. `GitChangeSource` lists and loads the Files selected by prepared sides.
`ref_choices` and `preferred_review_selection` turn one metadata read into
Branch Review picker values.

## Purpose and boundaries

Git is the VCS implemented by this source.
The source returns materialized bytes; RoomLord decides Snapshot identity and
publication. This module does not decode formats, render content, or download
LFS objects.

*Implementation detail:* Private storage handles Git output, batch object
loading, and local Git LFS pointers.
"""

from __future__ import annotations

import os
import posixpath
import re
import shlex
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal, override

from dirdiff.change_source.base import (
    BUILTIN_SIDES,
    BranchSelection,
    ChangeSourceProtocol,
    DefaultBaseSelection,
    FileChange,
    FileProperties,
    ListedChanges,
    ListedPath,
    LoadedFile,
    RefChoices,
    RefMetadata,
    SideName,
    SourceFiles,
    StorageFile,
    StructuredRemoteBranchRef,
    WorkspaceChanges,
    WorkspaceStorageProtocol,
    display_name_for_repo_paths,
    git_executable,
)
from dirdiff.engines import DirdiffError
from dirdiff.util import Err, Ok, Result, maybe

__all__ = [
    "GitChangeSource",
    "GitRepository",
    "preferred_review_selection",
    "ref_choices",
]

_LARGE_CHANGED_LINES_LAZY_THRESHOLD = 5000
"""Changed-line count above which Git-backed Files start lazy.

`list_changes` compares each tracked File's numstat additions plus removals against
this strict threshold. Binary Files have no numeric count and are unaffected.
"""

SYMLINK_MODE = "120000"
"""Git mode identifying raw link payloads during parsing and LFS inspection."""

GITLINK_MODE = "160000"
"""Git's tree mode for a gitlink: a submodule or embedded repository entry.

A gitlink names one commit of another repository and has no blob. Git storage
loads such a side as that commit id, so the mode never leaves `_GitWorkspace`
and is translated into semantic repository-reference properties at discovery.
"""

_LFS_POINTER_RE = re.compile(
    rb"version (https://(?:git-lfs|hawser)\.github\.com/spec/v[0-9]+)\n"
    rb"((?:ext-[0-9]+-[a-z0-9.-]+ sha256:[0-9a-f]{64}\n)*)"
    rb"oid sha256:([0-9a-f]{64})\nsize (0|[1-9][0-9]*)\n"
)
"""Recognize complete SHA-256 pointers before deciding whether to expand them.

Groups hold the version URL, extension lines, object id, and declared size.
Only canonical v1 without extensions is expanded; other recognized versions
and transformations fail explicitly. Nonmatching bytes remain raw File content,
including text that merely starts with an LFS version line. Callers limit
pointer inspection to files smaller than 1024 bytes and preserve raw links.
"""


def _properties_for_mode(mode: str) -> FileProperties:
    """Translate Git's closed entry-mode set at the storage boundary.

    Raw listing records and reached-path inspection both use this mapping.
    Unsupported Git modes assert rather than becoming invented File kinds.
    """
    assert mode in {"100644", "100755", "120000", "160000"}
    return FileProperties(
        "symbolic_link"
        if mode == SYMLINK_MODE
        else "repository_reference"
        if mode == GITLINK_MODE
        else "file",
        mode == "100755",
    )


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

    Pass the value from `dirdiff.change_source.GitRepository.read_ref_metadata`. The server shares that
    same value with default-selection derivation so both controls describe one
    caller operation.

    The typed record must contain every `RefMetadata` field. This function
    performs no I/O and has no expected domain failures.
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
    - `base_selection`: `Ok` containing the saved or inferred base, or `Err`
      containing the heuristic failure shown by the HUD.

    # Usage

    Pass the same metadata used to choose `base_selection`. The server publishes
    the result as the initial review-side control value.

    # Returns

    - With no branch metadata, an empty local branch selection.
    - Otherwise, a populated local branch selection. If every branch matches
      the base, the result may name that branch.
    """
    branch_names = metadata["local_branches"]
    if branch_names == []:
        return {"source": "local", "branch": ""}

    normalized_base = _local_default_base_branch_name(metadata)
    if Ok.good(base_selection):
        normalized_base = base_selection.value["branch"].strip()
    current = metadata["current_branch"]

    if current != "" and current != normalized_base:
        return {"source": "local", "branch": current}

    for branch_name in branch_names:
        if branch_name != normalized_base:
            return {"source": "local", "branch": branch_name}

    fallback_branch = current if current != "" else branch_names[0]
    return {"source": "local", "branch": fallback_branch}


class _GitWorkspace(WorkspaceStorageProtocol):
    """Expose one Git repository through `WorkspaceStorageProtocol`.

    # Usage

    Construct it with `discover`, normalize user input with `normalize_side`,
    then pass those sides to `list_changes`. Load the returned path sides with
    `load_version` or `load_versions`. Branch Review uses
    `read_ref_metadata`, `default_base_selection`, and
    `resolve_branch_diff_sides` before source capture.

    The instance stores its repository and command directories. It does not
    cache Git state, change refs, publish Snapshots, or classify file formats.
    """

    def __init__(self, repo_root: Path | None) -> None:
        """Bind storage to an exact Git root, or absence after discovery."""
        self._repo_root = repo_root.resolve() if repo_root is not None else None

    @property
    def repo_root(self) -> Path | None:
        """The Git worktree root supplied to this repository storage, if any.

        Uses a stored variable, never changes and never does any work.

        # Usage
        You will want to construct `_GitWorkspace` using `_GitWorkspace.discover`
        class method. It uses explicit `repo_root` when provided, or calls git
        to infer a repository root when omitted.

        Then you can just access the field.

        # Returns
        - An absolute path to the repository root.
        - `None` if we we failed to find repository root.
        """
        # TODO: probably extremely unsafe.
        # We must ensure that `_GitWorkspace.discover` returns a real root we
        # expect, and asserts invalid combinations.
        # TODO: we should not accept None in `_GitWorkspace.discover`.
        return self._repo_root

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        repo_root: Path | None = None,
    ) -> _GitWorkspace:
        """Discover a Git repository from an explicit root or working directory.

        # Parameters

        - `cwd`: Directory in which implicit Git repository discovery runs.
        - `repo_root`: Explicit repository root that skips Git discovery.

        # Usage

        Server and test setup normally pass `cwd` and let Git find the worktree.
        Pass `repo_root` only when the caller already selected the repository.

        # Returns

        A storage instance bound to the selected root. Failed implicit
        discovery returns an instance with `repo_root=None`, not an exception.

        # Failures

        - Filesystem resolution or process-launch failures propagate from
          `Path.resolve` or `subprocess.run`.
        """
        working_dir = (cwd or Path.cwd()).resolve()
        if repo_root is not None:
            return cls(Path(repo_root).expanduser().resolve())

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
        return cls(discovered_root)

    def _run_git(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run Git at this storage's selected root and return bytes.

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
        """Run Git at this storage's selected root and return decoded text.

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

    def normalize_side(self, raw_side: str) -> SideName:
        """Normalize a built-in side or verify an explicit commit-like Git ref.

        Whitespace is stripped. Built-in sides need no repository, while custom
        refs require one and must resolve through `rev-parse`.

        # Usage

        Normalize each user-facing side once, then pass the result back to this
        storage's listing and loading methods.

        # Failures

        - Blank input, a custom ref without a repository, or an unknown ref
          raises `DirdiffError`.
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

    def commit_id(self, side: SideName, *, allow_unborn: bool = False) -> str:
        """Return a commit id, or the empty-tree hash for allowed unborn HEAD.

        The empty-tree hash uses the repository's object format and does not
        write an object.

        # Parameters

        - `side`: Commit-like Git side to resolve.
        - `allow_unborn`: Permit the Head Tab's absent HEAD as Empty.

        # Usage

        Concrete source setup uses this after side normalization to replace
        a symbolic commit-like side with immutable identity.

        # Failures

        - Blank input, mutable `index` or `worktree`, a missing repository,
          and unknown or non-commit refs raise `DirdiffError`, except an
          allowed unresolved HEAD.
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
            if allow_unborn and normalized == "HEAD":
                empty_tree = self._run_git_text(
                    ["hash-object", "-t", "tree", os.devnull]
                ).stdout.strip()
                assert empty_tree != "", "Git returned no empty-tree hash"
                return empty_tree
            raise DirdiffError(f"Unknown Git ref: {side}")
        return commit_id

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

        - Without a repository, or when either non-checking Git probe fails,
          the affected fields are empty rather than exceptional.
        - Process-launch and decoding failures still propagate.
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

        - If metadata cannot identify one safe base, return
          `Err({"kind": "error", "error": "heuristic_fail"})`.
        - When a remote lacks a local HEAD symbolic ref, `git remote show` may
          contact the network; command failure also produces the error result.
        - A caller-supplied metadata record naming remotes on storage without
          a repository raises `DirdiffError` when that remote lookup is
          needed.
        """
        if metadata["remote_names"] != []:
            default_remote = _default_branch_review_remote_name(metadata)
            if default_remote == "":
                return Err({"kind": "error", "error": "heuristic_fail"})
            base_branch = metadata["remote_head_branches"].get(
                default_remote, ""
            )
            if base_branch == "":
                base_branch = self._remote_show_head_branch_name(default_remote)
            if base_branch == "":
                return Err({"kind": "error", "error": "heuristic_fail"})
            return Ok(
                {
                    "source": "remote",
                    "remote": default_remote,
                    "branch": base_branch,
                }
            )

        base_branch = _local_default_base_branch_name(metadata)
        if base_branch == "":
            return Err({"kind": "error", "error": "heuristic_fail"})
        return Ok({"source": "local", "branch": base_branch})

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

        - Missing repositories, unresolved refs, non-commit targets, and
          branches without a merge base raise `DirdiffError`.
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

    def _list_untracked_worktree_paths(self) -> list[ListedPath]:
        """List untracked, non-ignored worktree Files as right-only additions.

        Git's NUL-delimited path order is preserved. An untracked embedded
        repository is listed like any other entry, with gitlink mode. A missing
        repository or Git command failure propagates instead of returning an
        incomplete list.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")

        result = self._run_git(
            ["ls-files", "--others", "--exclude-standard", "-z"]
        )
        tokens = result.stdout.split(b"\0")
        if tokens != [] and tokens[-1] == b"":
            tokens = tokens[:-1]

        entries: list[ListedPath] = []
        for token in tokens:
            # Git marks an untracked embedded repository with a trailing '/'.
            # The entry itself is the gitlink path, so drop the marker.
            path = token.decode("utf-8").removesuffix("/")
            if path == "":
                continue
            entries.append(
                ListedPath(
                    left_path=None,
                    right_path=path,
                    display_name=path,
                    change_type="add",
                    lazy_reason_override=None,
                    untracked=True,
                    right_properties=self.file_properties(path, "worktree"),
                )
            )
        return entries

    def list_changes(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> ListedChanges:
        """Return Git-changed paths and aggregate tracked line counts.

        `show_untracked` applies when the worktree is the right side. The left
        side may already be a frozen commit id supplied by source setup;
        callers must not need to retain the symbolic `HEAD` spelling merely to
        include untracked worktree files.

        # Parameters

        - `left`: Normalized Git side used as the left input.
        - `right`: Normalized Git side used as the right input.
        - `show_untracked`: Whether a right-side worktree adds untracked Files.

        # Usage

        Pass sides normalized by this instance. `SourceFiles` uses the returned
        properties and object ids to acquire exactly the observed versions.

        # Returns

        - `ListedChanges.paths` contains Git changes and any selected untracked
          Files in presentation order.
        - `ListedChanges.added_lines` and `removed_lines` contain tracked totals
          or are both absent when complete counts are unavailable.

        *Implementation detail:* Git numstat supplies tracked line totals; a
        binary record makes both totals unavailable.

        # Failures

        - A missing repository, failed Git command, malformed raw or numstat
          output, or invalid UTF-8 path raises.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        if left == right:
            return ListedChanges((), 0, 0)

        # --no-abbrev keeps the raw records' object ids full-length so they
        # can serve as exact per-side content identity during capture.
        # A gitlink side is its referenced commit id; a submodule's own
        # uncommitted state is not part of the outer repository's diff.
        diff_args = [
            "diff",
            "--raw",
            "--no-abbrev",
            "--numstat",
            "-z",
            "-M",
            "--ignore-submodules=dirty",
        ]
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

        # Git emits every raw record before the numstat section. Each record
        # supplies both paths and storage facts for one complete ListedPath.
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
        entries: list[ListedPath] = []
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
            status_token = status.decode("utf-8")
            change_kind = status_token[0]
            path = raw_paths[0].decode("utf-8")
            left_path = path if change_kind != "A" else None
            right_path = (
                raw_paths[1].decode("utf-8")
                if change_kind in {"R", "C"}
                else path
                if change_kind != "D"
                else None
            )
            change_type: Literal["modify", "add", "delete", "rename", "copy"]
            if change_kind == "R":
                change_type = "rename"
            elif change_kind == "C":
                change_type = "copy"
            elif change_kind == "A":
                change_type = "add"
            elif change_kind == "D":
                change_type = "delete"
            else:
                change_type = "modify"
            entries.append(
                ListedPath(
                    left_path=left_path,
                    right_path=right_path,
                    display_name=display_name_for_repo_paths(
                        maybe(left_path), maybe(right_path)
                    ),
                    change_type=change_type,
                    lazy_reason_override=(
                        "pure_renamed"
                        if change_kind == "R" and status_token[1:] == "100"
                        else None
                    ),
                    left_properties=(
                        _properties_for_mode(raw_fields[0].decode("ascii"))
                        if left_path is not None
                        else None
                    ),
                    right_properties=(
                        _properties_for_mode(raw_fields[1].decode("ascii"))
                        if right_path is not None
                        else None
                    ),
                    left_object_id=(
                        object_id_or_none(raw_fields[2])
                        if left_path is not None
                        else None
                    ),
                    right_object_id=(
                        object_id_or_none(raw_fields[3])
                        if right_path is not None
                        else None
                    ),
                )
            )
            raw_index += path_count

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
        return ListedChanges(
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

        - Missing repository state and the rejected shapes above raise
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

    def file_mode(self, path: str, side: SideName) -> str:
        """Return one normalized path's Git-compatible File mode.

        Worktree inspection uses `lstat`, so a link is classified without
        following it. A worktree directory holding a `.git` entry is an
        embedded repository and reports gitlink mode `160000`. Index and tree
        sides ask Git for the exact literal path and report its recorded mode,
        including a gitlink; plain directories, missing paths, and malformed
        output are refused.

        # Parameters

        - `path`: Normalized repository path to inspect without following it.
        - `side`: Worktree, index, or Git tree/ref containing the path.

        # Failures

        - Raises `DirdiffError` when the path does not identify a regular
          file, executable file, symbolic link, or gitlink on the selected
          side.
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
            if stat.S_ISDIR(mode) and (file_path / ".git").exists():
                return GITLINK_MODE
            if not stat.S_ISREG(mode):
                raise DirdiffError(
                    f"{normalized} is not a regular file, symbolic link, "
                    "or embedded repository."
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
            # `ls-files --stage` records carry no object type; the index mode
            # alone distinguishes a gitlink from a blob.
            object_type = (
                ("commit" if git_mode == GITLINK_MODE else "blob")
                if side == "index"
                else parts[1].decode("ascii")
            )
            decoded_path = returned_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DirdiffError(
                f"Git returned invalid mode data for {label}."
            ) from exc
        expected_type = "commit" if git_mode == GITLINK_MODE else "blob"
        if decoded_path != normalized or object_type != expected_type:
            raise DirdiffError(f"{label} is not a capturable File.")
        if git_mode not in {"100644", "100755", SYMLINK_MODE, GITLINK_MODE}:
            raise DirdiffError(f"Unsupported File mode {git_mode} for {label}.")
        return git_mode

    @override
    def file_properties(self, path: str, side: SideName) -> FileProperties:
        """Return semantic properties for one reached Git path without following links.

        # Parameters

        - `path`: Canonical listed or reached repository path.
        - `side`: Selected Git state containing that path.

        Missing or unsupported entries raise `DirdiffError` through Git mode
        inspection. Raw Git modes remain private to this implementation.
        """
        return _properties_for_mode(self.file_mode(path, side))

    @override
    def file_size(self, path: str, side: SideName) -> int:
        """Return materialized size without loading a File's LFS payload.

        Worktree inspection uses `lstat`, so regular-file size does not follow
        links; link size is the byte length of Git's raw link payload. Index and
        tree sides ask `cat-file --batch-check` for blob metadata. Small regular
        blobs are inspected for an LFS pointer; its declared size is verified
        against the actual payload when content is loaded. Links stay raw. A
        gitlink reports the length of its commit id line on every side.

        # Parameters

        - `path`: Normalized repository path to inspect without reading it.
        - `side`: Worktree, index, or Git tree/ref containing the path.

        # Failures

        - Missing repository state, missing paths, unsupported File kinds,
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
                    if metadata.st_size < 1024:
                        # Size inspection must stay bounded even if another
                        # process grows the worktree file after lstat.
                        with file_path.open("rb") as stream:
                            content = stream.read(1024)
                        pointer = _LFS_POINTER_RE.fullmatch(content)
                        if pointer is not None:
                            if (
                                pointer[1]
                                != b"https://git-lfs.github.com/spec/v1"
                                or pointer[2] != b""
                            ):
                                raise DirdiffError(
                                    f"Unsupported Git LFS pointer version or extensions: worktree:{normalized}"
                                )
                            return int(pointer[4])
                    return metadata.st_size
                if (
                    stat.S_ISDIR(metadata.st_mode)
                    and (file_path / ".git").exists()
                ):
                    # A gitlink materializes as its commit id line; reading
                    # that is bounded, so its size is the loaded length.
                    return len(self.load_version(normalized, side))
            except OSError as exc:
                raise DirdiffError(
                    f"Could not inspect worktree file {normalized}: {exc}"
                ) from exc
            raise DirdiffError(
                f"{normalized} is not a regular file, symbolic link, or embedded repository."
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
            # A gitlink names a commit the outer repository usually lacks, so
            # cat-file reports it missing; when it is present it is a commit.
            # Either way its size is the materialized commit id line.
            if self.file_mode(normalized, side) == GITLINK_MODE:
                return len(self.load_version(normalized, side))
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
        if size < 1024:
            blob = self._run_git(["cat-file", "blob", git_target], check=False)
            if blob.returncode != 0:
                raise DirdiffError(
                    f"Git could not inspect {git_target}: {blob.stderr.decode().strip()}"
                )
            pointer = _LFS_POINTER_RE.fullmatch(blob.stdout)
            if (
                pointer is not None
                and self.file_mode(normalized, side) != SYMLINK_MODE
            ):
                if (
                    pointer[1] != b"https://git-lfs.github.com/spec/v1"
                    or pointer[2] != b""
                ):
                    raise DirdiffError(
                        f"Unsupported Git LFS pointer version or extensions: {git_target}"
                    )
                return int(pointer[4])
        return size

    @override
    def load_version(self, path: str, side: SideName) -> bytes:
        """Return materialized contents from the worktree, index, or a Git tree.

        On every side, a symlink loads as Git records it, a blob holding the raw
        link target. A gitlink loads as its referenced commit id, one text
        line. Regular LFS pointers load their size-checked local objects.
        This delegates to the batch operation so both entry points share the
        same storage and failure rules.

        # Parameters

        - `path`: Normalized repository path reported by `list_changes` or reached
          through a captured link.
        - `side`: Worktree, index, or Git tree/ref to read from.

        # Usage

        Load a present side path from this repository storage. Capture may use
        `load_versions` instead when several outer sides are needed.

        # Failures

        - Missing repository state, missing or plain-directory worktree
          content, unreadable regular-file bytes or links, an embedded
          repository without a HEAD commit, and non-blob, non-gitlink or
          absent Git objects raise `DirdiffError`.
        """
        result = self.load_versions(((path, side, None, None),))[0]
        return result.or_raise()

    @override
    def load_versions(
        self, sides: tuple[StorageFile, ...]
    ) -> tuple[Result[bytes, DirdiffError], ...]:
        """Load captured sides with one Git object-content subprocess.

        Worktree bytes are read directly. Listed immutable sides use their
        exact observed object ids in one `cat-file --batch` invocation, even
        when the index has changed. Unlisted inputs use path expressions.
        Discovered properties preserve raw links; only unlisted LFS candidates
        need additional batched mode inspection through literal pathspecs.
        Regular LFS pointers load local objects after bounded size checks.
        Storage configuration is read once per batch containing LFS content;
        no downloading, filters, or worktree writes occur.

        A gitlink side loads as its referenced commit id followed by a newline,
        the exact content Git records for that entry. A worktree embedded
        repository reads its own HEAD through its explicit Git directory; a
        tree or index gitlink takes the id from the tree record, because the
        referenced commit is usually absent from this repository's objects.
        Nothing inside the referenced repository is read.

        # Usage

        Source observation supplies `dirdiff.change_source.base.StorageFile` values with canonical paths,
        selected states, discovered properties, and any exact object ids.
        Reached or raw unlisted reads leave the last two facts absent.

        # Returns

        - Each item corresponds to one input side, preserving input order so
          capture can attach each result without another identity key.
        - An `Ok[bytes]` item holds exact content for that File side.
        - An `Err[DirdiffError]` item describes only that unavailable side;
          successful sibling results remain available.

        # Failures

        - Missing individual sides become `Err[DirdiffError]` values in input
          order.
        - Missing repository state, batch-process failure, or malformed batch
          output raises `DirdiffError` for the complete operation.
        """
        if self.repo_root is None:
            raise DirdiffError("Git-backed diff mode requires a Git repo.")
        # None marks an unfilled batch slot; every input must produce a Result.
        results: list[Result[bytes, DirdiffError] | None] = [None] * len(sides)
        symlink_indexes: set[int] = set()
        object_sides: list[tuple[int, bytes, str]] = []
        for index, (path, side, properties, object_id) in enumerate(sides):
            if properties is not None and properties.kind == "symbolic_link":
                symlink_indexes.add(index)
            if (
                object_id is not None
                and properties is not None
                and properties.kind == "repository_reference"
            ):
                results[index] = Ok(object_id.encode() + b"\n")
                continue
            if side == "worktree":
                file_path = self.repo_root / path
                try:
                    # Inspect the link itself: even a broken or directory link
                    # is valid raw content, never a candidate LFS pointer.
                    mode = file_path.lstat().st_mode
                    if stat.S_ISLNK(mode):
                        results[index] = Ok(os.readlink(os.fsencode(file_path)))
                        symlink_indexes.add(index)
                    elif stat.S_ISREG(mode):
                        results[index] = Ok(file_path.read_bytes())
                    elif stat.S_ISDIR(mode) and (file_path / ".git").exists():
                        # NOTE: this commit id becomes the side's whole
                        # content. Formats classify it by the outer path, so a
                        # repository named like a notebook or image composes
                        # as one; the id stays readable there, just oddly
                        # framed. Passing the Git directory explicitly keeps
                        # Git from discovering the outer repository instead.
                        head = self._run_git(
                            [
                                "--git-dir",
                                str(file_path / ".git"),
                                "rev-parse",
                                "--verify",
                                "HEAD^{commit}",
                            ],
                            check=False,
                        )
                        if head.returncode != 0:
                            results[index] = Err(
                                DirdiffError(
                                    f"Embedded repository {path} has no HEAD "
                                    f"commit: {head.stderr.decode().strip()}"
                                )
                            )
                        else:
                            results[index] = Ok(head.stdout)
                    else:
                        results[index] = Err(
                            DirdiffError(
                                f"{path} is not a regular file, symbolic link, "
                                "or embedded repository."
                            )
                        )
                except OSError as exc:
                    results[index] = Err(
                        DirdiffError(
                            f"Could not read worktree file {path}: {exc}"
                        )
                    )
            else:
                target_label = (
                    f":./{path}" if side == "index" else f"{side}:{path}"
                )
                target_expression = (
                    object_id if object_id is not None else target_label
                )
                object_sides.append(
                    (index, target_expression.encode(), target_label)
                )

        if object_sides != []:
            process = subprocess.run(
                [git_executable(), "cat-file", "--batch", "-z"],
                cwd=self.repo_root,
                check=False,
                input=b"\0".join(
                    target for _index, target, _label in object_sides
                )
                + b"\0",
                capture_output=True,
            )
            if process.returncode != 0:
                details = process.stderr.decode().strip()
                raise DirdiffError(f"Git object batch failed: {details}")
            # A gitlink whose commit is absent from this repository's objects
            # is reported missing exactly like an absent path. Those indexes
            # are told apart below by the tree record's mode.
            missing_entries: list[tuple[int, str]] = []
            offset = 0
            for index, target, label in object_sides:
                missing = target + b" missing\n"
                if process.stdout.startswith(missing, offset):
                    if sides[index][3] is not None:
                        # A listed version stays that version even if today's
                        # path or index now points at a different entry kind.
                        results[index] = Err(
                            DirdiffError(
                                f"Git could not load {label}: File is missing."
                            )
                        )
                    else:
                        missing_entries.append((index, label))
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
                loaded_object_id, object_type, raw_size = fields
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
                    results[index] = Ok(
                        process.stdout[content_start:content_end]
                    )
                elif object_type == b"commit":
                    # A tree path only resolves to a commit through a gitlink.
                    # Its content is that commit id; the commit body is the
                    # referenced repository's, not this side's.
                    results[index] = Ok(loaded_object_id + b"\n")
                else:
                    results[index] = Err(
                        DirdiffError(
                            f"Git could not load {label}: expected a File blob, "
                            f"got {object_type.decode('ascii')}."
                        )
                    )
                offset = content_end + 1
            if offset != len(process.stdout):
                raise DirdiffError(
                    "Git returned unexpected trailing object data."
                )

            missing_by_side: dict[str, list[tuple[int, str]]] = {}
            for index, label in missing_entries:
                missing_by_side.setdefault(sides[index][1], []).append(
                    (index, label)
                )
            for side, entries in missing_by_side.items():
                paths = {sides[index][0] for index, _label in entries}
                record_result = self._run_git(
                    [
                        *(
                            ["ls-files", "--stage", "-z"]
                            if side == "index"
                            else ["ls-tree", "-z", side]
                        ),
                        "--",
                        *(f":(literal){path}" for path in sorted(paths)),
                    ],
                    check=False,
                )
                if record_result.returncode != 0:
                    raise DirdiffError(
                        f"Git could not inspect {side} entries: "
                        f"{record_result.stderr.decode().strip()}"
                    )
                # `ls-tree` records are "mode type id", `ls-files --stage`
                # records are "mode id stage"; the id sits after the type only
                # in the former.
                gitlink_ids: dict[str, bytes] = {}
                for record in record_result.stdout.split(b"\0"):
                    if record == b"":
                        continue
                    metadata, separator, raw_path = record.partition(b"\t")
                    assert separator == b"\t", "Git record requires a path"
                    fields = metadata.split(b" ")
                    if fields[0] == GITLINK_MODE.encode():
                        gitlink_ids[raw_path.decode()] = fields[
                            1 if side == "index" else 2
                        ]
                for index, label in entries:
                    path = sides[index][0]
                    if path in gitlink_ids:
                        results[index] = Ok(gitlink_ids[path] + b"\n")
                    else:
                        results[index] = Err(
                            DirdiffError(
                                f"Git could not load {label}: File is missing."
                            )
                        )

        # Tree/index blob reads do not carry modes. Inspect only complete pointer
        # candidates, grouped by side, so preserving symlinks adds no per-file
        # Git subprocess. Batches without pointer candidates need no additional
        # commands.
        candidates: dict[str, list[int]] = {}
        for index, outcome in enumerate(results):
            if (
                outcome is not None
                and Ok.good(outcome)
                and len(outcome.value) < 1024
                and _LFS_POINTER_RE.fullmatch(outcome.value) is not None
                and index not in symlink_indexes
            ):
                candidates.setdefault(sides[index][1], []).append(index)
        for side, indexes in candidates.items():
            if side == "worktree":
                continue
            unknown_indexes = [
                index for index in indexes if sides[index][2] is None
            ]
            if unknown_indexes == []:
                continue
            paths = {sides[index][0] for index in unknown_indexes}
            mode_result = self._run_git(
                [
                    *(
                        ["ls-files", "--stage", "-z"]
                        if side == "index"
                        else ["ls-tree", "-z", side]
                    ),
                    "--",
                    *(f":(literal){path}" for path in sorted(paths)),
                ],
                check=False,
            )
            if mode_result.returncode != 0:
                for index in unknown_indexes:
                    results[index] = Err(
                        DirdiffError(
                            f"Git could not inspect {side}:{sides[index][0]}: "
                            f"{mode_result.stderr.decode().strip()}"
                        )
                    )
                continue
            modes: dict[str, bytes] = {}
            for record in mode_result.stdout.split(b"\0"):
                if record == b"":
                    continue
                metadata, separator, raw_path = record.partition(b"\t")
                assert separator == b"\t", "Git mode record requires a path"
                modes[raw_path.decode()] = metadata.split(b" ")[0]
            for index in unknown_indexes:
                path = sides[index][0]
                if path not in modes:
                    results[index] = Err(
                        DirdiffError(f"Git could not inspect {side}:{path}.")
                    )
                elif modes[path] == b"120000":
                    symlink_indexes.add(index)

        lfs_objects: dict[int, tuple[str, int]] = {}
        for indexes in candidates.values():
            for index in indexes:
                outcome = results[index]
                assert outcome is not None, (
                    "LFS candidate requires a loaded side"
                )
                if not Ok.good(outcome) or index in symlink_indexes:
                    continue
                content = outcome.value
                pointer = _LFS_POINTER_RE.fullmatch(content)
                assert pointer is not None, (
                    "LFS candidates must be complete pointers"
                )
                if (
                    pointer[1] != b"https://git-lfs.github.com/spec/v1"
                    or pointer[2] != b""
                ):
                    path, side, _properties, _object_id = sides[index]
                    results[index] = Err(
                        DirdiffError(
                            f"Unsupported Git LFS pointer version or extensions: {side}:{path}"
                        )
                    )
                else:
                    lfs_objects[index] = (
                        pointer[3].decode("ascii"),
                        int(pointer[4]),
                    )

        if lfs_objects != {}:
            # Git LFS storage is relative to the shared Git directory, not a
            # linked worktree's private directory. Configuration is an input
            # to this load only; no cache survives the operation.
            try:
                config = self._run_git(
                    ["config", "--null", "--get", "lfs.storage"], check=False
                )
                if config.returncode == 1:
                    storage = Path("lfs")
                elif config.returncode == 0:
                    value = os.fsdecode(config.stdout.removesuffix(b"\0"))
                    storage = Path(value) if value != "" else Path("lfs")
                else:
                    raise DirdiffError(
                        f"Could not read Git LFS storage: {config.stderr.decode().strip()}"
                    )
                if not storage.is_absolute():
                    common = self._run_git(
                        [
                            "rev-parse",
                            "--path-format=absolute",
                            "--git-common-dir",
                        ],
                        check=False,
                    )
                    if common.returncode != 0:
                        raise DirdiffError(
                            f"Could not locate Git LFS storage: {common.stderr.decode().strip()}"
                        )
                    storage = (
                        Path(os.fsdecode(common.stdout.removesuffix(b"\n")))
                        / storage
                    )
            except DirdiffError as exc:
                for index, (oid, _size) in lfs_objects.items():
                    path, side, _properties, _object_id = sides[index]
                    results[index] = Err(
                        DirdiffError(
                            f"Could not load Git LFS object {oid} for {side}:{path}: {exc}"
                        )
                    )
            else:
                for index, (oid, size) in lfs_objects.items():
                    path, side, _properties, _object_id = sides[index]
                    object_path = storage / "objects" / oid[:2] / oid[2:4] / oid
                    try:
                        # Bound reads by the declared size even when the cached
                        # object has the wrong length. Trust Git LFS's object
                        # digest rather than hashing every payload again.
                        with object_path.open("rb") as stream:
                            if os.fstat(stream.fileno()).st_size != size:
                                raise DirdiffError(
                                    f"size mismatch, expected {size} bytes"
                                )
                            content = stream.read(size + 1)
                        if len(content) != size:
                            raise DirdiffError(
                                f"size mismatch, expected {size} bytes, read {len(content)}"
                            )
                    except FileNotFoundError:
                        fetch = (
                            "git lfs fetch <remote>"
                            if side in {"index", "worktree"}
                            else f"git lfs fetch <remote> {shlex.quote(side)}"
                        )
                        results[index] = Err(
                            DirdiffError(
                                f"{side}:{path} points to Git LFS object {oid} "
                                f"({size} bytes), which is missing from local storage. "
                                f"Fetch it with `{fetch}`."
                            )
                        )
                    except (OSError, DirdiffError) as exc:
                        results[index] = Err(
                            DirdiffError(
                                f"Could not load Git LFS object {oid} for {side}:{path}: {exc}"
                            )
                        )
                    else:
                        results[index] = Ok(content)

        assert all(result is not None for result in results)
        return tuple(result for result in results if result is not None)


class GitRepository:
    """Prepare Git revision inputs and picker choices for a selected repository.

    # Usage

    Obtain an instance with `discover` or construct one from a known repository
    root. Picker callers read `read_ref_metadata` once, then pass that record to
    `ref_choices`, `default_base_selection`, and `preferred_review_selection`.

    Capture setup validates revision controls with `normalize_side` and freezes
    commit-backed selections with `commit_id`. Branch Review uses
    `resolve_branch_diff_sides` to prepare both commits together. Pass the resulting
    commits or explicit `index`/`worktree` sides to `GitChangeSource`.

    The instance keeps the repository location, not a selected Tab or an observation.
    Preparation reads current Git state as needed; the instance does not retain
    ref metadata between calls.
    """

    def __init__(self, repo_root: Path | None) -> None:
        """Bind ref preparation to a supplied repository location.

        # Parameters

        - `repo_root`: Repository root, or `None` after a failed implicit
          discovery. Direct construction does not inspect Git state; later
          operations that require a repository report its absence.
        """
        self._workspace = _GitWorkspace(repo_root)

    @classmethod
    def discover(
        cls, cwd: Path | None = None, *, repo_root: Path | None = None
    ) -> GitRepository:
        """Discover a Git worktree or bind an explicit root.

        # Parameters

        - `cwd`: Directory in which implicit `git rev-parse` runs. Without it,
          discovery starts in the process working directory.
        - `repo_root`: Explicit root that skips the Git discovery command.

        # Returns

        - `GitRepository` with an absolute root when discovery succeeds or an
          explicit root was supplied.
        - `GitRepository` with `repo_root=None` when implicit discovery finds
          no worktree. Callers needing capture must reject that state.

        # Failures

        - Filesystem resolution and process-launch failures propagate.
        """
        workspace = _GitWorkspace.discover(cwd=cwd, repo_root=repo_root)
        return cls(workspace.repo_root)

    @property
    def repo_root(self) -> Path | None:
        """Return the stable absolute root without filesystem work.

        Git-specific callers can use the selected or discovered location for
        source construction. The value cannot change after construction.

        # Returns

        - `Path`: Absolute selected or discovered Git worktree root.
        - `None`: Implicit discovery found no Git worktree. Access itself does
          not fail or probe the filesystem.
        """
        return self._workspace.repo_root

    def normalize_side(self, raw_side: str) -> SideName:
        """Validate a revision control and return its trimmed spelling.

        # Parameters

        - `raw_side`: `HEAD`, `index`, `worktree`, or a Git ref resolving to a commit.
          Leading and trailing whitespace is removed.

        # Usage

        Validate each Refs control before source construction. Commit-backed controls
        still need `commit_id` to freeze their contents; the returned spelling can move
        as Git refs change. `index` and `worktree` can be supplied directly to
        `GitChangeSource`.

        # Returns

        The validated spelling, not its resolved object id. Built-in sides are accepted
        without checking their current availability.

        # Failures

        - Blank or unknown refs raise `DirdiffError`.
        - A custom ref without a repository raises `DirdiffError`.
        - Process-launch failures propagate.
        """
        return self._workspace.normalize_side(raw_side)

    def commit_id(self, side: SideName, *, allow_unborn: bool = False) -> str:
        """Freeze a commit-backed revision control to its complete object id.

        # Parameters

        - `side`: Commit-backed Git ref or id to freeze. Whitespace is stripped.
        - `allow_unborn`: Allow unresolved `HEAD` and return the repository-format
          empty-tree id for an empty baseline. Other missing refs still fail.

        # Usage

        Call after validating revision controls, then construct `GitChangeSource` with
        the returned immutable id. Do not call for explicit `index` or `worktree` sides.

        # Returns

        A complete commit id, or the empty-tree id for allowed unresolved `HEAD`.
        Computing that empty-tree id writes no Git object.

        # Failures

        - Blank input, `index`, `worktree`, or an absent repository raises `DirdiffError`.
        - An unknown or non-commit ref raises `DirdiffError`, except allowed unresolved
          `HEAD`.
        - Process-launch or decoding failures propagate.
        """
        return self._workspace.commit_id(side, allow_unborn=allow_unborn)

    def read_ref_metadata(self) -> RefMetadata:
        """Read current Git choices for revision and Branch Review controls.

        # Usage

        Use one returned record for `ref_choices`, `default_base_selection`, and
        `preferred_review_selection`, so those derivations share the same metadata.
        A new call reads Git again; the object does not refresh an earlier record.

        # Returns

        A `RefMetadata` with local branches, named remotes, remote branches, current
        branch, upstream, and locally known default-branch facts. Without a repository,
        all fields are empty. A failed non-checking Git probe leaves its affected fields
        empty. The underlying reads can observe different moments if Git changes.

        # Failures

        - Process-launch and decoding failures propagate.
        """
        return self._workspace.read_ref_metadata()

    def default_base_selection(
        self, metadata: RefMetadata
    ) -> DefaultBaseSelection:
        """Choose a Branch Review base from one `read_ref_metadata` result.

        A remote without a locally known default branch may require
        `git remote show` and can contact the network. When no safe base is known,
        the result is the explicit `heuristic_fail` variant.

        # Returns

        - `Ok` contains the suggested local or named-remote base branch.
        - `Err` contains `heuristic_fail` when no safe default could be chosen;
          the picker must leave that choice to the user.
        """
        return self._workspace.default_base_selection(metadata)

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str, str]:
        """Freeze symbolic Branch Review selections to labels and commits.

        # Parameters

        - `base_selection`: Local or named-remote base branch.
        - `review_selection`: Local or named-remote reviewed branch.

        # Returns

        - First, the selected base label for the HUD.
        - Second, the complete merge-base commit for capture's left side.
        - Third, the selected review label for the HUD.
        - Fourth, the complete review-head commit for capture's right side.

        # Failures

        - Missing branches, missing history, and absent merge base raise
          `DirdiffError`.
        """
        return self._workspace.resolve_branch_diff_sides(
            base_selection=base_selection,
            review_selection=review_selection,
        )


class GitChangeSource(ChangeSourceProtocol):
    """Observe one selected Git Tab and supply its exact File data.

    Implements `dirdiff.change_source.ChangeSourceProtocol`. See that protocol
    for the observation-to-load workflow, result ordering, and source lifetime.

    # Usage

    Here `root` is the selected repository root, and `left_ref` and `right_ref`
    are selected commit-backed revision controls. The active Mark for that root
    supplies `mark_id` to RoomLord:

    ```python
    from dirdiff.change_source import GitChangeSource, GitRepository
    from dirdiff.room_lord import RevisionsCaptureSelection

    repository = GitRepository(root)
    left = repository.commit_id(repository.normalize_side(left_ref))
    right = repository.commit_id(repository.normalize_side(right_ref))
    source = GitChangeSource(
        root,
        left=left,
        right=right,
        show_untracked=False,
        max_link_target_bytes=1024 * 1024,
    )
    selection = RevisionsCaptureSelection(left, right)
    left_label, right_label = left, right
    ```

    Pass this source to `dirdiff.room_lord.RoomLord.corresponding_room` with
    this matching `selection`, the active `mark_id`, and these labels. Direct source
    consumers call `changes()` before `load()` as documented by the protocol.

    # Git materialization

    Git modes and object addresses stay private. The public properties describe
    regular Files, symbolic links, and references to another repository revision.
    A repository reference supplies that revision as readable data without
    reading the other repository's Files.

    Canonical supported LFS pointers in regular Files expand from local storage.
    No LFS download or filter runs. Content identities trust Git and the local
    LFS store's content-addressed objects. If a listed object is unavailable when
    acquired, the result has a distinct failure identity.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool,
        max_link_target_bytes: int,
    ) -> None:
        """Bind prepared Git sides and materialization policy for one capture.

        # Parameters

        - `repo_root`: Root of the repository selected for this Tab.
        - `left`: Complete lowercase commit id, unborn-HEAD empty-tree id,
          `index`, or `worktree` for the left source slot.
        - `right`: Prepared input under the same contract for the right slot.
        - `show_untracked`: Include extra untracked Files when the right side is
          `worktree`. They do not contribute to tracked aggregate line totals.
        - `max_link_target_bytes`: Nonnegative size limit for the reached
          non-link target, checked before and after reading. Outer content and
          raw link payloads are unbounded.

        # Usage

        Use `GitRepository.commit_id` to freeze commit-like ref spellings.
        Construction normalizes the root but does not discover Files or verify
        object availability. Call `changes()` to establish the observation.

        # Failures

        - An unprepared ref spelling or negative bound raises
          `AssertionError`.
        - Filesystem path-resolution failures propagate.
        """
        for side in (left, right):
            assert side in {"index", "worktree"} or (
                len(side) in {40, 64}
                and all(character in "0123456789abcdef" for character in side)
            ), "GitChangeSource requires a prepared id, index, or worktree"
        assert max_link_target_bytes >= 0
        self._workspace = _GitWorkspace(repo_root)
        self._left = left
        self._right = right
        self._show_untracked = show_untracked
        self._max_link_target_bytes = max_link_target_bytes
        self._files: SourceFiles | None = None

    @override
    def changes(self) -> WorkspaceChanges:
        """Observe Git's affected Files with complete public side facts.

        # Usage

        Call before `load()`. The returned records describe this observation
        even if the worktree or index later changes. Callers use the shared
        `dirdiff.change_source.ChangeSourceProtocol.changes` contract without
        scheduling storage reads themselves.

        # Returns

        A `dirdiff.change_source.WorkspaceChanges` with Git's File classification
        and semantic properties. Tracked aggregate totals are present unless a
        binary record prevents complete totals. Extra untracked Files leave
        those totals unchanged. Repeated successful calls return the same record.

        # Failures

        - Git command failures raise `dirdiff.engines.DirdiffError`.
        - Invalid parsed invariants raise `AssertionError`.
        - Unexpected decoding or parsing failures propagate.
        - Expected individual outer read failures and link target diagnoses
          remain contained under the shared protocol contract.
        """
        if self._files is None:
            self._files = SourceFiles(
                self._workspace,
                self._workspace.list_changes(
                    left=self._left,
                    right=self._right,
                    show_untracked=self._show_untracked,
                ),
                left=self._left,
                right=self._right,
                identity_namespace=b"git-materialized-lfs-v1-links-v1",
                max_link_target_bytes=self._max_link_target_bytes,
            )
        return self._files.changes

    @override
    def load(self, files: Sequence[FileChange]) -> Sequence[LoadedFile]:
        """Supply paired data from this Git observation.

        # Parameters

        - `files`: Exact File records from this source's successful `changes()`;
          membership and ordering follow
          `dirdiff.change_source.ChangeSourceProtocol.load`.

        # Usage

        Listed index entries retain their observed contents even if the index
        later changes. Already acquired worktree bytes and link facts are
        returned without rereading. Missing local LFS objects include manual
        fetch hints; this operation does not fetch them.

        # Returns

        A sequence of `dirdiff.change_source.LoadedFile` in input order, each
        retaining its exact observed File and named side outcomes. Present sides
        have these content outcomes:

        - `bytes` for successful acquisition of the observed version.
        - `dirdiff.engines.DirdiffError` for that side's expected acquisition
          failure, with a final failure identity and no link facts.

        The shared protocol documents identity changes, duplicate inputs, and
        successful link walks that stop with a target diagnosis.

        # Failures

        - Calling before successful observation or passing invalid File
          records asserts.
        - A Git failure that prevents loading the submitted Files raises
          `dirdiff.engines.DirdiffError`.
        - Unexpected storage failures propagate.
        """
        assert self._files is not None, "changes() must precede load()"
        return self._files.load(files)
