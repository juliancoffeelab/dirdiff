"""Git no-index-backed diff engine.

The package follows the same split as the other external diff engines:

* `git.py` owns subprocess execution and temporary files;
* `logic.py` owns projection from Git's unified patch text into rows;
* this module owns the public `GitDiffEngine` class and summary assembly.

The engine remains repo-agnostic.  `dirdiff.backend` loads old/new text from
Git refs or preset fixtures before this engine is called; this engine only asks
Git to compare two temporary files containing that text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, cast, final

from dirdiff.engines.contract import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    InlineToken,
)
from dirdiff.engines.git.git import run_git_no_index_diff
from dirdiff.engines.git.logic import (
    git_diff_rows_from_patch,
    plain_line_rows_for_side,
)

__all__ = ["GitDiffEngine"]


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


@final
class GitDiffEngine(DiffEngineProtocol):
    """Renderer for already-loaded text using `git diff --no-index`."""

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
