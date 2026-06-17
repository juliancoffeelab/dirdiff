from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
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
    left_text: str,
    right_text: str,
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
