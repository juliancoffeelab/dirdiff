"""Difftastic-backed structural renderer engine.

This service is responsible for one thing at the renderer boundary: given two
loaded text sides, ask difftastic for structural rows and convert those rows
into the dirdiff payload model.  It does not load files, resolve refs, build
manifests, or handle notebooks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, cast, final, override

from dirdiff.backend import unified_diff_lines
from dirdiff.engines.contract import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    EngineWarning,
    InlineToken,
)
from dirdiff.engines.difftastic.difft import (
    DifftasticJson,
    run_difftastic_json,
)
from dirdiff.engines.difftastic.logic import (
    build_difftastic_ast,
)

__all__ = ["DifftasticDiffEngine"]


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


def _plain_line_rows_for_side(
    *,
    text: str,
    side: Literal["left", "right"],
) -> list[dict[str, Any]]:
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


def _unified_diff_rows(
    *,
    left_text: str,
    right_text: str,
    left_label: str,
    right_label: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for diff_line in unified_diff_lines(
        left_text=left_text,
        right_text=right_text,
        left_label=left_label,
        right_label=right_label,
    ):
        if diff_line.status == "equal":
            rows.append(
                {
                    "status": "equal",
                    "left_no": diff_line.left_no,
                    "right_no": diff_line.right_no,
                    "left_text": diff_line.text,
                    "right_text": diff_line.text,
                }
            )
            continue
        if diff_line.status == "delete":
            rows.append(
                {
                    "status": "delete",
                    "left_no": diff_line.left_no,
                    "right_no": None,
                    "left_text": diff_line.text,
                    "right_text": "",
                }
            )
            continue
        if diff_line.status == "insert":
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": diff_line.right_no,
                    "left_text": "",
                    "right_text": diff_line.text,
                }
            )
    return rows


@final
class DifftasticDiffEngine(DiffEngineProtocol):
    """Structural renderer backed by difftastic.

    The renderer entrypoint runs difftastic on already-loaded text and projects
    its structural output into the row model shared by the rest of dirdiff.

    Difftastic can fail or decline to produce rows for some inputs.  That is
    represented as an engine warning plus a textual fallback, keeping the REST
    response renderable while still being honest about the engine result.
    """

    def _run_difftastic_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str | None,
        right_path_hint: str | None,
    ) -> DifftasticJson:
        """Run difftastic for one already-loaded text pair.

        This wrapper is intentionally small so tests or subclasses cannot
        redefine service behavior.  It isolates the subprocess integration from
        the payload projection while keeping the public service class final.
        """
        return run_difftastic_json(
            left_text=left_text,
            right_text=right_text,
            left_path_hint=left_path_hint,
            right_path_hint=right_path_hint,
        )

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render an already-loaded pair with difftastic.

        The only inputs this method trusts are the text strings, existence
        flags, labels, and path hints supplied by the caller.  Path hints are
        passed to difftastic for language/parser selection, but this method does
        not load those paths.

        If difftastic cannot produce usable rows, the renderer falls back to a
        Git-style textual alignment so the API still returns a renderable file
        diff.  Notebook detection is intentionally outside this method and
        happens in server orchestration before an engine is selected.
        """
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text
        engine_warning: EngineWarning | None = None
        if old.exists and new.exists:
            difftastic_ast = build_difftastic_ast(
                left_text=left_text_value,
                right_text=right_text_value,
                left_path_hint=old.path_hint,
                right_path_hint=new.path_hint,
            )
            engine_warning = difftastic_ast.engine_warning
            rows = cast("list[dict[str, Any]]", difftastic_ast.rows)
            if not rows:
                rows = _unified_diff_rows(
                    left_text=left_text_value,
                    right_text=right_text_value,
                    left_label="" if old.path_hint is None else old.path_hint,
                    right_label="" if new.path_hint is None else new.path_hint,
                )
        elif old.exists:
            rows = _plain_line_rows_for_side(
                text=left_text_value,
                side="left",
            )
        else:
            rows = _plain_line_rows_for_side(
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
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        return payload
