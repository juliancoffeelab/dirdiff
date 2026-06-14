from __future__ import annotations

import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher, unified_diff
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

BuiltinSideName = Literal["head", "index", "worktree"]
SideName = str
BUILTIN_SIDES = frozenset({"head", "index", "worktree"})


@dataclass(frozen=True)
class TextVersion:
    label: str
    exists: bool
    text: str | None
    error: str | None = None


@dataclass(frozen=True)
class RepoDiffPath:
    left_path: str | None
    right_path: str | None
    display_name: str
    change_type: str
    changed_lines: int | None = None
    added_lines: int | None = None
    removed_lines: int | None = None
    untracked: bool = False


class TextDiffError(ValueError):
    """Raised when a diff request cannot be fulfilled safely."""


def _decode_text(data: bytes, *, label: str) -> str:
    if b"\x00" in data:
        raise TextDiffError(f"{label} appears to be a binary file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TextDiffError(f"{label} is not valid UTF-8 text: {exc}") from exc


def _display_name_for_repo_paths(
    left_path: str | None,
    right_path: str | None,
) -> str:
    if left_path and right_path:
        return (
            left_path
            if left_path == right_path
            else f"{left_path} -> {right_path}"
        )
    return left_path or right_path or "(unknown)"


def _count_changed_line_stats(
    left_text: str, right_text: str
) -> tuple[int, int, int]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = SequenceMatcher(
        a=[line.lstrip() for line in left_lines],
        b=[line.lstrip() for line in right_lines],
        autojunk=False,
    )
    added = 0
    removed = 0
    replaced = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_count = i2 - i1
        right_count = j2 - j1
        if tag == "equal":
            replaced += sum(
                1
                for left_line, right_line in zip(
                    left_lines[i1:i2],
                    right_lines[j1:j2],
                    strict=True,
                )
                if left_line != right_line
            )
        elif tag == "delete":
            removed += left_count
        elif tag == "insert":
            added += right_count
        else:
            paired = min(left_count, right_count)
            replaced += paired
            removed += left_count - paired
            added += right_count - paired
    return added, removed, replaced


class WorkspaceBackend(Protocol):
    @property
    def repo_root(self) -> Path | None: ...

    @property
    def cwd(self) -> Path: ...

    def normalize_side(self, raw_side: str) -> SideName: ...

    def discover_default_path(self) -> str: ...

    def current_branch_name(self) -> str: ...

    def list_branch_names(self) -> list[str]: ...

    def list_remote_ref_names(self) -> list[str]: ...

    def list_remote_names(self) -> list[str]: ...

    def list_ref_choices(self) -> dict[str, list[str]]: ...

    def default_remote_name(self) -> str: ...

    def branch_upstream_name(self, branch_name: str) -> str: ...

    def default_base_branch(self) -> str: ...

    def preferred_review_branch(
        self, *, base_branch: str | None = None
    ) -> str: ...

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]: ...

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]: ...

    def normalize_repo_path(self, raw_path: str) -> str: ...

    def load_version(self, path: str, side: SideName) -> TextVersion: ...


class PatchBackend(WorkspaceBackend, Protocol):
    def load_unified_patch(
        self, *, left: SideName, right: SideName, path: str
    ) -> str: ...


class GitBackend:
    def __init__(
        self, repo_root: Path | None, *, cwd: Path | None = None
    ) -> None:
        self.repo_root = repo_root.resolve() if repo_root is not None else None
        self.cwd = (cwd or Path.cwd()).resolve()

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        repo_root: Path | None = None,
    ) -> GitBackend:
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
        if self.repo_root is None:
            return ""
        result = self._run_git_text(["branch", "--show-current"], check=False)
        return result.stdout.strip()

    def list_branch_names(self) -> list[str]:
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
        return sorted(
            {
                ref.split("/", 1)[0]
                for ref in self.list_remote_ref_names()
                if "/" in ref
            }
        )

    def list_ref_choices(self) -> dict[str, list[str]]:
        return {
            "builtins": ["head", "index", "worktree"],
            "locals": self.list_branch_names(),
            "remotes": self.list_remote_ref_names(),
            "remote_names": self.list_remote_names(),
        }

    def default_remote_name(self) -> str:
        remote_names = self.list_remote_names()
        if "origin" in remote_names:
            return "origin"
        return remote_names[0] if remote_names else ""

    def branch_upstream_name(self, branch_name: str) -> str:
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

    def default_base_branch(self) -> str:
        branch_names = self.list_branch_names()
        if "master" in branch_names:
            return "master"
        if "main" in branch_names:
            return "main"

        if self.repo_root is not None:
            result = self._run_git_text(
                [
                    "symbolic-ref",
                    "--quiet",
                    "--short",
                    "refs/remotes/origin/HEAD",
                ],
                check=False,
            )
            remote_head = result.stdout.strip()
            if remote_head.startswith("origin/"):
                candidate = remote_head.removeprefix("origin/")
                if candidate:
                    return candidate

        current = self.current_branch_name()
        if current:
            return current
        return branch_names[0] if branch_names else ""

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        branch_names = self.list_branch_names()
        if not branch_names:
            return ""

        normalized_base = (base_branch or self.default_base_branch()).strip()
        current = self.current_branch_name()

        if current and current != normalized_base:
            return current

        for branch_name in branch_names:
            if branch_name != normalized_base:
                return branch_name

        return current or branch_names[0]

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]:
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
        return merge_base.stdout.strip(), normalized_branch

    def _git_tree_spec(self, side: SideName) -> str:
        if side == "head":
            return "HEAD"
        return side

    def _diff_args(
        self,
        *,
        left: SideName,
        right: SideName,
        kind: str,
    ) -> list[str]:
        args, _ = self._diff_args_with_direction(
            left=left, right=right, kind=kind
        )
        return args

    def _diff_args_with_direction(
        self,
        *,
        left: SideName,
        right: SideName,
        kind: str,
    ) -> tuple[list[str], bool]:
        if "worktree" in {left, right}:
            other = right if left == "worktree" else left
            args = (
                ["diff", kind, "-z", "-M"]
                if other == "index"
                else ["diff", kind, "-z", "-M", self._git_tree_spec(other)]
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
                    self._git_tree_spec(other),
                ]
            )
            return args, left == "index"
        return [
            "diff",
            kind,
            "-z",
            "-M",
            self._git_tree_spec(left),
            self._git_tree_spec(right),
        ], False

    def load_unified_patch(
        self,
        *,
        left: SideName,
        right: SideName,
        path: str,
    ) -> str:
        normalized_left = self.normalize_side(left)
        normalized_right = self.normalize_side(right)
        normalized_path = self.normalize_repo_path(path)
        diff_args, reverse = self._diff_args_with_direction(
            left=normalized_left,
            right=normalized_right,
            kind="--patch",
        )
        patch_args = [arg for arg in diff_args if arg not in {"-z", "--patch"}]
        patch_args.extend(
            [
                "--patch",
                "--no-ext-diff",
                "--no-color",
                "--unified=100000000",
            ]
        )
        if reverse:
            patch_args.append("-R")
        patch_args.extend(["--", normalized_path])
        result = self._run_git(patch_args, check=False)
        if result.returncode != 0:
            raise TextDiffError(
                _decode_text(result.stderr, label="git diff stderr").strip()
                or "Could not load Git patch."
            )
        return _decode_text(result.stdout, label=f"git diff:{normalized_path}")

    def _parse_name_status_output(self, output: bytes) -> list[RepoDiffPath]:
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
                        display_name=_display_name_for_repo_paths(
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
            entries.append(
                RepoDiffPath(
                    left_path=current_left_path,
                    right_path=current_right_path,
                    display_name=_display_name_for_repo_paths(
                        current_left_path, current_right_path
                    ),
                    change_type={
                        "A": "add",
                        "D": "delete",
                    }.get(change_kind, "modify"),
                )
            )

        return entries

    def _parse_numstat_output(
        self, output: bytes
    ) -> dict[str, tuple[int, int]]:
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


class PresetBackend:
    def __init__(self, presets_root: Path, *, cwd: Path | None = None) -> None:
        self.presets_root = presets_root.expanduser().resolve()
        self.repo_root = self.presets_root
        self.cwd = (cwd or Path.cwd()).resolve()

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        presets_root: Path | None = None,
    ) -> PresetBackend:
        working_dir = (cwd or Path.cwd()).resolve()
        root = presets_root or working_dir / "tests" / "presets" / "difftastic"
        return cls(root, cwd=working_dir)

    def _preset_dirs(self) -> list[Path]:
        if not self.presets_root.exists():
            return []
        return sorted(
            path
            for path in self.presets_root.iterdir()
            if path.is_dir()
            and len(list(path.glob("old.*"))) == 1
            and len(list(path.glob("new.*"))) == 1
        )

    def _list_preset_names(self) -> list[str]:
        return [path.name for path in self._preset_dirs()]

    def _preset_dir(self, preset_name: str) -> Path:
        normalized = preset_name.strip()
        if not normalized:
            names = self._list_preset_names()
            if not names:
                raise TextDiffError(f"No presets found in {self.presets_root}.")
            normalized = names[0]
        if "/" in normalized or normalized in {".", ".."}:
            raise TextDiffError("Preset name must be a single directory name.")
        preset_dir = self.presets_root / normalized
        if not preset_dir.is_dir():
            raise TextDiffError(f"Unknown preset: {normalized}")
        return preset_dir

    def _preset_pair(self, preset_name: str) -> tuple[Path, Path]:
        preset_dir = self._preset_dir(preset_name)
        old_files = sorted(preset_dir.glob("old.*"))
        new_files = sorted(preset_dir.glob("new.*"))
        if len(old_files) != 1 or len(new_files) != 1:
            raise TextDiffError(
                f"Preset {preset_dir.name} must contain exactly one old.* and one new.* file."
            )
        return old_files[0], new_files[0]

    def _path_for_side(self, path: str, side: SideName) -> Path:
        normalized_path = self.normalize_repo_path(path)
        full_path = self.presets_root / normalized_path
        if full_path.is_file():
            return full_path

        preset = (
            side
            if side not in {"presets", "new"}
            else PurePosixPath(normalized_path).parts[0]
        )
        old_path, new_path = self._preset_pair(preset)
        wanted_name = PurePosixPath(normalized_path).name
        if wanted_name == old_path.name:
            return old_path
        if wanted_name == new_path.name:
            return new_path
        raise TextDiffError(f"Preset file is missing: {normalized_path}")

    def normalize_side(self, raw_side: str) -> SideName:
        side = raw_side.strip()
        if side in {"presets", "new"}:
            return side
        if side in self._list_preset_names():
            return side
        raise TextDiffError(f"Unknown preset: {side}")

    def discover_default_path(self) -> str:
        names = self._list_preset_names()
        if not names:
            raise TextDiffError(f"No presets found in {self.presets_root}.")
        old_path, _ = self._preset_pair(names[0])
        return f"{names[0]}/{old_path.name}"

    def current_branch_name(self) -> str:
        raise TextDiffError(
            "Preset backend does not have a current Git branch."
        )

    def list_branch_names(self) -> list[str]:
        raise TextDiffError("Preset backend does not have Git branches.")

    def list_remote_ref_names(self) -> list[str]:
        raise TextDiffError("Preset backend does not have Git remote refs.")

    def list_remote_names(self) -> list[str]:
        raise TextDiffError("Preset backend does not have Git remotes.")

    def list_ref_choices(self) -> dict[str, list[str]]:
        return {
            "builtins": [],
            "locals": [],
            "remotes": [],
            "remote_names": [],
        }

    def default_remote_name(self) -> str:
        raise TextDiffError(
            "Preset backend does not have a default Git remote."
        )

    def branch_upstream_name(self, branch_name: str) -> str:
        raise TextDiffError(
            "Preset backend does not have Git branch upstreams."
        )

    def default_base_branch(self) -> str:
        raise TextDiffError(
            "Preset backend does not have a default base branch."
        )

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        raise TextDiffError("Preset backend does not support branch review.")

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]:
        raise TextDiffError("Preset backend does not support branch review.")

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        normalized_left = self.normalize_side(left)
        if right != "new":
            raise TextDiffError(
                "Preset diffs compare a preset's old.* and new.* files."
            )
        preset_names = (
            self._list_preset_names()
            if normalized_left == "presets"
            else [normalized_left]
        )
        entries: list[RepoDiffPath] = []
        for preset_name in preset_names:
            old_path, new_path = self._preset_pair(preset_name)
            old_text = old_path.read_text(encoding="utf-8")
            new_text = new_path.read_text(encoding="utf-8")
            added, removed, replaced = _count_changed_line_stats(
                old_text,
                new_text,
            )
            entries.append(
                RepoDiffPath(
                    left_path=f"{preset_name}/{old_path.name}",
                    right_path=f"{preset_name}/{new_path.name}",
                    display_name=f"{preset_name}/{new_path.name}",
                    change_type="modify",
                    changed_lines=added + removed + replaced,
                    added_lines=added + replaced,
                    removed_lines=removed + replaced,
                )
            )
        return entries

    def normalize_repo_path(self, raw_path: str) -> str:
        if not raw_path.strip():
            raise TextDiffError("Preset path is required.")
        if raw_path.endswith("/"):
            raise TextDiffError("Preset path must point to a file.")
        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise TextDiffError("Use a preset-relative path.")
        normalized = candidate.as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise TextDiffError(
                "Preset path must stay inside the presets root."
            )
        parts = candidate.parts
        if len(parts) != 2:
            raise TextDiffError(
                "Preset path must look like <preset>/<old-or-new-file>."
            )
        return normalized

    def load_version(self, path: str, side: SideName) -> TextVersion:
        normalized_path = self.normalize_repo_path(path)
        file_path = self._path_for_side(normalized_path, side)
        if not file_path.exists():
            return TextVersion(label=side, exists=False, text=None)
        return TextVersion(
            label=side,
            exists=True,
            text=file_path.read_text(encoding="utf-8"),
        )

    def load_unified_patch(
        self,
        *,
        left: SideName,
        right: SideName,
        path: str,
    ) -> str:
        preset_name = PurePosixPath(path).parts[0]
        self.normalize_side(left)
        old_path, new_path = self._preset_pair(preset_name)
        old_text = old_path.read_text(encoding="utf-8")
        new_text = new_path.read_text(encoding="utf-8")
        return "".join(
            unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{preset_name}/{old_path.name}",
                tofile=f"b/{preset_name}/{new_path.name}",
                n=100000000,
            )
        )
