"""GumTree-backed move renderer engine.

GumTree is used here as a renderer for ordinary source files: dirdiff supplies
two loaded text sides, this service runs GumTree on a temporary old/new file
pair, parses GumTree JSON, and projects move actions into dirdiff rows and
tokens.  The service intentionally does not know about notebooks or API request
modes.  If the API sees a notebook path, it routes to notebook payload builders
before asking GumTree to render anything.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, cast, final

from dirdiff.backend import TextDiffError
from dirdiff.engines.contract import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffSummary,
    EngineWarning,
    InlineToken,
)
from dirdiff.engines.gumtree.gumtree import (
    GumTreeInvalidJsonError,
    GumTreeJson,
    gumtree_executable_for_cwd,
    run_gumtree_json,
)
from dirdiff.engines.gumtree.logic import (
    build_gumtree_rows_from_json,
    unified_diff_rows,
)

__all__ = ["GumTreeDiffEngine"]


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


def _payload_size_bytes(payload: dict[str, Any]) -> int:
    """Measure GumTree payload size using the API's JSON representation.

    GumTree rows can become token-heavy because move/update information is
    attached at token level.  The renderer uses the same serialized-size
    measurement as the other engines when recording performance diagnostics.
    """
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _plain_line_rows_for_side(
    *,
    text: str,
    side: str,
) -> list[dict[str, Any]]:
    """Build rows for one-sided GumTree file diffs.

    GumTree move detection only applies when both old and new files exist.  For
    added or deleted files, the service still needs to return a normal dirdiff
    payload, so it builds plain insert/delete rows without invoking GumTree.
    """
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if side == "left":
            rows.append(
                {
                    "status": "delete",
                    "left_no": index,
                    "right_no": None,
                    "left_text": line,
                    "right_text": "",
                }
            )
        else:
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": index,
                    "left_text": "",
                    "right_text": line,
                }
            )
    return rows


def _gumtree_summary(
    *,
    rows: list[dict[str, Any]],
) -> DiffSummary:
    """Return GumTree summary counts for already-built rows.

    GumTree's primary signal is token status, especially ``move``.  After
    projection, row statuses describe the same changed spans the frontend uses
    for hunk navigation, so the summary counts those statuses directly.  Mixed
    token changes are summarized as modified rows, while pure move rows are
    summarized as moved rows.
    """
    modified_lines = sum(1 for row in rows if row["status"] == "replace")
    added_lines = sum(1 for row in rows if row["status"] == "insert")
    removed_lines = sum(1 for row in rows if row["status"] == "delete")
    moved_lines = sum(1 for row in rows if row["status"] == "move")
    return {
        "changed_lines": (
            modified_lines + added_lines + removed_lines + moved_lines
        ),
        "modified_lines": modified_lines,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "moved_lines": moved_lines,
    }


@final
class GumTreeDiffEngine(DiffEngineProtocol):
    """Move-aware renderer backed by GumTree.

    GumTree needs old/new files on disk, but dirdiff's renderer boundary still
    supplies text.  This service bridges that gap by writing temporary file
    pairs with useful path hints, invoking GumTree for one file pair, parsing
    the JSON action stream, and projecting GumTree move/update/insert/delete
    actions into dirdiff rows and tokens.

    GumTree behavior belongs in ``render_diff`` and the
    ``engines.gumtree.logic`` projection layer.  Workspace state is only used
    to choose a working directory for GumTree executable discovery.
    """

    def __init__(self, *, cwd: Path) -> None:
        """Store the working directory used for GumTree executable discovery."""
        self.cwd = cwd

    def _run_gumtree_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str,
        right_path_hint: str,
    ) -> GumTreeJson:
        """Invoke GumTree for one old/new text pair and parse JSON output.

        This is the only subprocess boundary in the service.  Executable
        discovery is resolved relative to the workspace, then
        ``run_gumtree_json`` writes temporary files and parses GumTree's JSON
        stream.  Callers handle ``GumTreeInvalidJsonError`` by producing an
        engine warning and a unified fallback payload.
        """
        gumtree_bin = gumtree_executable_for_cwd(self.cwd)
        return run_gumtree_json(
            gumtree_bin=gumtree_bin,
            left_text=left_text,
            right_text=right_text,
            left_path_hint=left_path_hint,
            right_path_hint=right_path_hint,
        )

    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render a loaded old/new text pair with GumTree JSON as the boundary.

        GumTree requires file names so it can select a parser, so callers must
        provide both path hints when both sides exist.  The method writes the
        supplied text into a temporary old/new pair, runs GumTree only for that
        pair, and treats GumTree JSON as the integration boundary.

        Invalid GumTree JSON is converted into an honest engine warning and a
        unified textual fallback.  Valid GumTree moves are summarized as moved
        tokens/rows without making the service responsible for repo loading,
        ref resolution, preset handling, or notebook decisions.
        """
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text

        if old.exists and new.exists:
            if old.path_hint is None:
                raise TextDiffError(
                    "GumTree requires both file paths for move detection."
                )
            if new.path_hint is None:
                raise TextDiffError(
                    "GumTree requires both file paths for move detection."
                )
            engine_warning: EngineWarning | None = None
            try:
                diff_json = self._run_gumtree_json(
                    left_text=left_text_value,
                    right_text=right_text_value,
                    left_path_hint=old.path_hint,
                    right_path_hint=new.path_hint,
                )
            except GumTreeInvalidJsonError:
                rows = unified_diff_rows(
                    left_text=left_text_value,
                    right_text=right_text_value,
                    left_path_hint=old.path_hint,
                    right_path_hint=new.path_hint,
                )
                engine_warning = {
                    "type": "gumtree_invalid_json",
                    "message": (
                        "GumTree returned invalid JSON, so dirdiff could not "
                        "render GumTree move tokens for this file. Showing a "
                        "unified diff fallback instead."
                    ),
                }
            else:
                rows = build_gumtree_rows_from_json(
                    diff_json=diff_json,
                    left_text=left_text_value,
                    right_text=right_text_value,
                )
        elif old.exists:
            rows = _plain_line_rows_for_side(
                text=left_text_value,
                side="left",
            )
            engine_warning = None
        else:
            rows = _plain_line_rows_for_side(
                text=right_text_value,
                side="right",
            )
            engine_warning = None

        summary = _gumtree_summary(rows=rows)
        payload: DiffEngineResult = {
            "summary": summary,
            "rows": _strict_engine_rows(rows),
        }
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        return payload
