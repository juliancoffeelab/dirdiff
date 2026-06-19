from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from dirdiff.services.base import (
    DiffServiceProtocol,
    _file_kind_for_change_type,
    _perf_log,
    build_lazy_info_for_service,
    build_repo_manifest_for_service,
)
from dirdiff.services.textdiff import (
    _build_git_rows_payload,
    _default_expanded_for_payload,
    _git_style_line_rows,
    _parse_git_patch_rows,
    _payload_size_bytes,
    _plain_line_rows_for_side,
)
from dirdiff.sources import (
    GitBackend,
    PresetBackend,
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackend,
    _decode_text,
    _display_name_for_repo_paths,
    git_diff_args_with_direction,
)


class GitDiffService(DiffServiceProtocol):
    repo: WorkspaceBackend

    def __init__(self, repo: WorkspaceBackend) -> None:
        self.repo = repo

    @property
    def repo_root(self) -> Path | None:
        return self.repo.repo_root

    @property
    def cwd(self) -> Path:
        return self.repo.cwd

    def normalize_side(self, raw_side: str) -> SideName:
        return self.repo.normalize_side(raw_side)

    def discover_default_path(self) -> str:
        return self.repo.discover_default_path()

    def current_branch_name(self) -> str:
        return self.repo.current_branch_name()

    def list_branch_names(self) -> list[str]:
        return self.repo.list_branch_names()

    def list_remote_ref_names(self) -> list[str]:
        return self.repo.list_remote_ref_names()

    def list_remote_names(self) -> list[str]:
        return self.repo.list_remote_names()

    def list_ref_choices(self) -> dict[str, list[str]]:
        return self.repo.list_ref_choices()

    def default_remote_name(self) -> str:
        return self.repo.default_remote_name()

    def branch_upstream_name(self, branch_name: str) -> str:
        return self.repo.branch_upstream_name(branch_name)

    def default_base_branch(self) -> str:
        return self.repo.default_base_branch()

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        return self.repo.preferred_review_branch(base_branch=base_branch)

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]:
        return self.repo.resolve_branch_diff_sides(
            base_branch=base_branch,
            branch=branch,
        )

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        return self.repo.list_repo_diff_paths(
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def normalize_repo_path(self, raw_path: str) -> str:
        return self.repo.normalize_repo_path(raw_path)

    def load_version(self, path: str, side: SideName) -> TextVersion:
        return self.repo.load_version(path, side)

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_repo_manifest_for_service(
            self,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def build_lazy_info(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_lazy_info_for_service(
            self,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def _load_repo_git_patch(
        self,
        *,
        left: SideName,
        right: SideName,
        left_path: str | None,
        right_path: str | None,
    ) -> str:
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        pathspecs = [
            path for path in [left_path, right_path] if path is not None
        ]
        if not pathspecs:
            raise TextDiffError("Git patch requires at least one repo path.")
        diff_args, reverse = git_diff_args_with_direction(
            left=left,
            right=right,
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
        patch_args.extend(["--", *list(dict.fromkeys(pathspecs))])
        result = subprocess.run(
            ["git", *patch_args],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise TextDiffError(
                _decode_text(result.stderr, label="git diff stderr").strip()
                or "Could not load Git patch."
            )
        return _decode_text(
            result.stdout,
            label=("git diff:" + " -> ".join(pathspecs)),
        )

    def _load_preset_git_patch(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
    ) -> str:
        if self.repo_root is None:
            raise TextDiffError("Preset diff mode requires a presets root.")
        if left_path is None or right_path is None:
            raise TextDiffError(
                "Preset Git patch requires both old and new preset paths."
            )
        result = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--no-ext-diff",
                "--no-color",
                "--unified=100000000",
                "--",
                str(self.repo_root / left_path),
                str(self.repo_root / right_path),
            ],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
        )
        if result.returncode not in {0, 1}:
            raise TextDiffError(
                _decode_text(result.stderr, label="git diff stderr").strip()
                or "Could not load preset Git patch."
            )
        return _decode_text(
            result.stdout,
            label=f"git diff --no-index:{left_path} -> {right_path}",
        )

    def _load_git_patch(
        self,
        *,
        left: SideName,
        right: SideName,
        left_path: str | None,
        right_path: str | None,
    ) -> str:
        if isinstance(self.repo, GitBackend):
            return self._load_repo_git_patch(
                left=left,
                right=right,
                left_path=left_path,
                right_path=right_path,
            )
        if isinstance(self.repo, PresetBackend):
            return self._load_preset_git_patch(
                left_path=left_path,
                right_path=right_path,
            )
        raise TextDiffError(
            f"Git engine is not supported for backend {type(self.repo).__name__}."
        )

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str = "modify",
        file_kind: str | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        normalized_left = (
            self.normalize_repo_path(left_path)
            if left_path is not None
            else None
        )
        normalized_right = (
            self.normalize_repo_path(right_path)
            if right_path is not None
            else None
        )
        normalized_left_side = self.normalize_side(left)
        normalized_right_side = self.normalize_side(right)
        left_version = (
            self.load_version(normalized_left, normalized_left_side)
            if normalized_left is not None
            else TextVersion(
                label=normalized_left_side, exists=False, text=None
            )
        )
        right_version = (
            self.load_version(normalized_right, normalized_right_side)
            if normalized_right is not None
            else TextVersion(
                label=normalized_right_side, exists=False, text=None
            )
        )

        if left_version.error:
            raise TextDiffError(left_version.error)
        if right_version.error:
            raise TextDiffError(right_version.error)
        if not left_version.exists and not right_version.exists:
            raise TextDiffError("The selected file is missing on both sides.")

        left_text = left_version.text or ""
        right_text = right_version.text or ""
        rows = (
            _parse_git_patch_rows(
                self._load_git_patch(
                    left=normalized_left_side,
                    right=normalized_right_side,
                    left_path=normalized_left,
                    right_path=normalized_right,
                )
            )
            if normalized_left is not None or normalized_right is not None
            else []
        )
        if not rows and left_version.exists and not right_version.exists:
            rows = _plain_line_rows_for_side(text=left_text, side="left")
        elif not rows and right_version.exists and not left_version.exists:
            rows = _plain_line_rows_for_side(text=right_text, side="right")
        elif not rows:
            rows = _git_style_line_rows(left_text, right_text)

        rows_payload = _build_git_rows_payload(
            rows=rows,
            left_text=left_text,
            right_text=right_text,
            left_path_hint=normalized_left,
            right_path_hint=normalized_right,
        )
        payload = {
            "display_name": display_name
            or _display_name_for_repo_paths(normalized_left, normalized_right),
            "mode": "git",
            "left_label": normalized_left_side,
            "right_label": normalized_right_side,
            "summary": {
                "changed_lines": rows_payload["changed_lines"],
                "modified_lines": rows_payload["modified_lines"],
                "added_lines": rows_payload["added_lines"],
                "removed_lines": rows_payload["removed_lines"],
                "left_exists": left_version.exists,
                "right_exists": right_version.exists,
            },
            "rows": rows_payload["rows"],
            "file_kind": _file_kind_for_change_type(
                change_type,
                file_kind=file_kind,
            ),
            "left_path": normalized_left,
            "right_path": normalized_right,
        }
        payload["default_expanded"] = _default_expanded_for_payload(payload)
        if "render_mode" in rows_payload:
            payload["render_mode"] = rows_payload["render_mode"]
        if "truncated_rows" in rows_payload:
            payload["truncated_rows"] = rows_payload["truncated_rows"]
        if "fold_hints" in rows_payload:
            payload["fold_hints"] = rows_payload["fold_hints"]

        row_count = len(rows)
        syntax_span_count = sum(
            len(row.get("left_syntax", ())) + len(row.get("right_syntax", ()))
            for row in rows
        )
        payload_bytes = _payload_size_bytes(payload)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _perf_log(
            "git-file"
            f" name={payload['display_name']!r}"
            f" change={change_type}"
            f" rows={row_count}"
            f" left_chars={len(left_text)}"
            f" right_chars={len(right_text)}"
            f" syntax_spans={syntax_span_count}"
            f" payload_bytes={payload_bytes}"
            f" elapsed_ms={elapsed_ms:.1f}"
        )
        return payload

    def build_notebook_section_diff(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        section: str | None,
        cell_key: str | None = None,
    ) -> dict[str, Any]:
        raise TextDiffError(
            "Notebook sections are not available in the Git engine."
        )
