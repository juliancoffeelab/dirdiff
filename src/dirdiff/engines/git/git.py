"""Raw `git diff --no-index` execution for the Git diff engine.

This module is the subprocess boundary for Git-backed rendering.  It receives
already-loaded old/new text, writes that text to temporary files, invokes Git's
diff algorithm in no-index mode, and returns the unified patch text.  It does
not know about repository refs, manifests, lazy loading, or API response
metadata.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal, cast, final, override

from dirdiff.backend import TextDiffError
from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    InlineToken,
)
from dirdiff.engines.git.logic import (
    git_diff_rows_from_patch,
    plain_line_rows_for_side,
)

__all__ = [
    "GitDiffEngine",
    "run_git_no_index_diff",
]


def _strict_engine_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[DiffEngineRow]:
    materialized: list[DiffEngineRow] = []
    for row in rows:
        materialized.append(
            {
                "status": cast(
                    "Literal['equal', 'replace', 'insert', 'delete', 'move']",
                    row["status"],
                ),
                "left_no": cast("int | None", row.get("left_no")),
                "right_no": cast("int | None", row.get("right_no")),
                "left_text": cast("str | None", row.get("left_text")),
                "right_text": cast("str | None", row.get("right_text")),
                "left_tokens": cast(
                    "list[InlineToken]",
                    row.get("left_tokens", []),
                ),
                "right_tokens": cast(
                    "list[InlineToken]",
                    row.get("right_tokens", []),
                ),
            }
        )
    return materialized


def _temp_file_name(label: str, path_hint: str | None) -> str:
    """Return a temp filename with a useful suffix for Git's diff headers."""
    if path_hint is None:
        return f"{label}.txt"
    suffix = Path(path_hint).suffix
    if suffix == "":
        return f"{label}.txt"
    return f"{label}{suffix}"


def run_git_no_index_diff(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> str:
    """Run `git diff --no-index` for one already-loaded text pair.

    Git no-index mode exits with `0` when files are equal and `1` when a
    diff exists, so both are successful engine outcomes.  Other exit codes are
    surfaced as `TextDiffError` because they mean Git failed to produce a
    trustworthy patch.
    """
    with tempfile.TemporaryDirectory(prefix="dirdiff-git-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / _temp_file_name("left", left_path_hint)
        right_path = tmp / _temp_file_name("right", right_path_hint)
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    "git",
                    "diff",
                    "--no-index",
                    "--no-color",
                    "--no-ext-diff",
                    "--no-textconv",
                    str(left_path),
                    str(right_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise TextDiffError(
                "Git engine requires the `git` executable on PATH."
            ) from exc

    if result.returncode in {0, 1}:
        return result.stdout

    message = result.stderr.strip()
    if message == "":
        message = "Git could not build this diff."
    raise TextDiffError(message)


@final
class GitDiffEngine(DiffEngineProtocol):
    """Renderer for already-loaded text using `git diff --no-index`."""

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render already-loaded text with Git's no-index diff algorithm."""
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text
        if old.exists and new.exists:
            patch = run_git_no_index_diff(
                left_text=left_text_value,
                right_text=right_text_value,
                left_path_hint=old.path_hint,
                right_path_hint=new.path_hint,
            )
            rows = git_diff_rows_from_patch(patch)
        elif old.exists:
            rows = plain_line_rows_for_side(
                text=left_text_value,
                side="left",
            )
        else:
            rows = plain_line_rows_for_side(
                text=right_text_value,
                side="right",
            )
        added_lines = sum(1 for row in rows if row["status"] == "insert")
        removed_lines = sum(1 for row in rows if row["status"] == "delete")
        moved_lines = sum(1 for row in rows if row["status"] == "move")
        payload: DiffEngineResult = {
            "summary": {
                "changed_lines": added_lines + removed_lines + moved_lines,
                "modified_lines": 0,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "moved_lines": moved_lines,
            },
            "rows": _strict_engine_rows(rows),
        }
        return payload
