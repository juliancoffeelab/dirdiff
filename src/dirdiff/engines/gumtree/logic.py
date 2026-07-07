"""GumTree JSON to dirdiff row AST contract.

This module defines the boundary between raw GumTree JSON actions and the
rendered row AST used by the GumTree service. It accepts GumTree-shaped JSON
plus the original source text and returns dirdiff-shaped rows with inline token
statuses projected from GumTree action ranges.

This module must not own raw GumTree execution or final API payload assembly:

* `dirdiff.engines.gumtree.gumtree` owns invoking `gumtree` and parsing JSON.
* the server/request layer owns backend loading, syntax highlighting, fold
  hints, and frontend payload assembly.

Input contract
--------------
The main entrypoint is `build_gumtree_rows_from_json`:

* `left_text` and `right_text` are the complete source documents. They are the
  authority for line text, line counts, and user-visible content.
* `diff_json.actions` contains GumTree edit actions. The `tree` value must end
  with a byte/character range of the form `[start,end]`.
* `diff_json.matches` maps source tree strings to destination tree strings for
  actions that need a paired destination range, such as updates and moves.

Accepted GumTree facts
----------------------
GumTree action names are interpreted by prefix:

* `insert-*`: destination/right range, rendered as `insert`;
* `delete-*`: source/left range, rendered as `delete`;
* `update-*`: source and matched destination ranges, rendered as `replace`;
* `move-*`: source and matched destination ranges, rendered as `move`.

Overlapping action ranges are resolved as a single token status. `move` wins
over `replace`, and `replace` wins over `insert`/`delete`, matching the
first-class visual priority dirdiff expects for GumTree rendering.

Output contract
---------------
`build_gumtree_rows_from_json` returns display rows compatible with the shared
dirdiff row payload. GumTree rows whose changed tokens are purely inserted,
deleted, or moved get the matching row status; mixed token changes are marked
as `replace`. That gives hunk navigation a backend signal without asking the
frontend to infer hunks from token arrays.

Required invariants
-------------------
* row text must come from the supplied source text;
* token text must concatenate back to the displayed row text whenever tokens are
  emitted for a side;
* GumTree ranges are projected onto one-based source line numbers;
* matched update and move actions must have destination ranges in `matches`;
* invalid or malformed GumTree JSON facts fail at this boundary instead of
  silently degrading into misleading token output.

Non-goals
---------
This module does not validate the full GumTree JSON schema, does not shell out
to GumTree, does not perform syntax highlighting, does not build fold hints,
and does not assemble the final HTTP/API payload.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, cast, final, override

from dirdiff.backend import TextDiffError, unified_diff_lines
from dirdiff.engines.base import (
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

type GumTreeTokenStatus = Literal[
    "unchanged", "insert", "delete", "replace", "move"
]

GUMTREE_TREE_RANGE_PATTERN = re.compile(r"\[(?P<start>\d+),(?P<end>\d+)\]\s*$")
GUMTREE_TOKEN_STATUS_PRIORITY = {
    "unchanged": 0,
    "insert": 1,
    "delete": 1,
    "replace": 2,
    "move": 3,
}

__all__ = [
    "GUMTREE_TOKEN_STATUS_PRIORITY",
    "GUMTREE_TREE_RANGE_PATTERN",
    "GumTreeDiffEngine",
    "LineSegment",
    "SourceRange",
    "StatusLineInterval",
    "StatusRange",
    "build_gumtree_rows_from_json",
    "unified_diff_rows",
]


@dataclass(frozen=True)
class SourceRange:
    start: int
    end: int


@dataclass(frozen=True)
class StatusRange:
    side: Literal["left", "right"]
    status: GumTreeTokenStatus
    source_range: SourceRange


@dataclass(frozen=True)
class StatusLineInterval:
    start: int
    end: int
    status: GumTreeTokenStatus


@dataclass(frozen=True)
class LineSegment:
    index: int
    start: int
    content_end: int
    segment_end: int


StatusLineIntervalMap = dict[int, list[StatusLineInterval]]


def _range_from_tree(raw_tree: str) -> SourceRange:
    match = GUMTREE_TREE_RANGE_PATTERN.search(raw_tree)
    if match is None:
        raise TextDiffError(f"GumTree tree range is missing: {raw_tree}")
    return SourceRange(
        start=int(match.group("start")),
        end=int(match.group("end")),
    )


def _gumtree_destinations_by_source(diff_json: GumTreeJson) -> dict[str, str]:
    matches = diff_json.get("matches")
    if matches is None:
        matches = []

    dest_by_src: dict[str, str] = {}
    for match in matches:
        dest_by_src[match["src"]] = match["dest"]
    return dest_by_src


def _gumtree_status_ranges(diff_json: GumTreeJson) -> list[StatusRange]:
    actions = diff_json.get("actions")
    if actions is None:
        actions = []
    dest_by_src = _gumtree_destinations_by_source(diff_json)

    ranges: list[StatusRange] = []
    for action in actions:
        action_name = action["action"]
        tree = action["tree"]
        if action_name.startswith("insert"):
            ranges.append(
                StatusRange(
                    side="right",
                    status="insert",
                    source_range=_range_from_tree(tree),
                )
            )
            continue
        if action_name.startswith("delete"):
            ranges.append(
                StatusRange(
                    side="left",
                    status="delete",
                    source_range=_range_from_tree(tree),
                )
            )
            continue
        if action_name.startswith("update"):
            ranges.append(
                StatusRange(
                    side="left",
                    status="replace",
                    source_range=_range_from_tree(tree),
                )
            )
            dest_tree = dest_by_src.get(tree)
            if dest_tree is None:
                raise TextDiffError(
                    f"GumTree update action has no destination mapping: {tree}"
                )
            ranges.append(
                StatusRange(
                    side="right",
                    status="replace",
                    source_range=_range_from_tree(dest_tree),
                )
            )
            continue
        if action_name.startswith("move"):
            ranges.append(
                StatusRange(
                    side="left",
                    status="move",
                    source_range=_range_from_tree(tree),
                )
            )
            dest_tree = dest_by_src.get(tree)
            if dest_tree is None:
                raise TextDiffError(
                    f"GumTree move action has no destination mapping: {tree}"
                )
            ranges.append(
                StatusRange(
                    side="right",
                    status="move",
                    source_range=_range_from_tree(dest_tree),
                )
            )

    return ranges


def _content_length(line_segment: str) -> int:
    if line_segment.endswith("\r\n"):
        return len(line_segment) - 2
    if line_segment.endswith("\n"):
        return len(line_segment) - 1
    if line_segment.endswith("\r"):
        return len(line_segment) - 1
    return len(line_segment)


def _line_segments(text: str) -> list[LineSegment]:
    segments: list[LineSegment] = []
    cursor = 0
    for index, raw_line in enumerate(text.splitlines(keepends=True)):
        content_end = cursor + _content_length(raw_line)
        segment_end = cursor + len(raw_line)
        segments.append(
            LineSegment(
                index=index,
                start=cursor,
                content_end=content_end,
                segment_end=segment_end,
            )
        )
        cursor = segment_end
    return segments


def _add_range_to_status_line_intervals(
    intervals_by_line: StatusLineIntervalMap,
    text: str,
    status_range: StatusRange,
) -> None:
    source_range = status_range.source_range
    if source_range.end <= source_range.start:
        return

    for segment in _line_segments(text):
        if source_range.end <= segment.start:
            break
        if source_range.start >= segment.segment_end:
            continue
        overlap_start = max(source_range.start, segment.start)
        overlap_end = min(source_range.end, segment.content_end)
        if overlap_start >= overlap_end:
            continue
        line_intervals = intervals_by_line.get(segment.index)
        if line_intervals is None:
            line_intervals = []
            intervals_by_line[segment.index] = line_intervals
        line_intervals.append(
            StatusLineInterval(
                start=overlap_start - segment.start,
                end=overlap_end - segment.start,
                status=status_range.status,
            )
        )


def _status_line_intervals(
    *,
    text: str,
    ranges: list[StatusRange],
) -> StatusLineIntervalMap:
    intervals_by_line: StatusLineIntervalMap = {}
    for status_range in ranges:
        _add_range_to_status_line_intervals(
            intervals_by_line,
            text,
            status_range,
        )
    return intervals_by_line


def _token(text: str, status: GumTreeTokenStatus) -> InlineToken:
    return {"text": text, "status": status, "is_ws": text.isspace()}


def _stronger_status(
    left: GumTreeTokenStatus,
    right: GumTreeTokenStatus,
) -> GumTreeTokenStatus:
    left_priority = GUMTREE_TOKEN_STATUS_PRIORITY[left]
    right_priority = GUMTREE_TOKEN_STATUS_PRIORITY[right]
    if right_priority > left_priority:
        return right
    return left


def _status_tokens_for_line(
    *,
    text: str,
    intervals: list[StatusLineInterval],
) -> list[InlineToken]:
    boundaries = {0, len(text)}
    clipped: list[StatusLineInterval] = []
    for interval in intervals:
        start = max(interval.start, 0)
        end = min(interval.end, len(text))
        if start >= end:
            continue
        clipped.append(
            StatusLineInterval(
                start=start,
                end=end,
                status=interval.status,
            )
        )
        boundaries.add(start)
        boundaries.add(end)

    if not clipped:
        return []

    tokens: list[InlineToken] = []
    sorted_boundaries = sorted(boundaries)
    for start, end in pairwise(sorted_boundaries):
        if start >= end:
            continue
        status: GumTreeTokenStatus = "unchanged"
        for interval in clipped:
            if interval.start <= start and end <= interval.end:
                status = _stronger_status(status, interval.status)
        if tokens != [] and tokens[-1]["status"] == status:
            tokens[-1]["text"] += text[start:end]
            tokens[-1]["is_ws"] = tokens[-1]["text"].isspace()
            continue
        tokens.append(_token(text[start:end], status))
    return tokens


def _apply_gumtree_statuses_to_rows(
    *,
    rows: list[dict[str, Any]],
    left_text: str,
    right_text: str,
    ranges: list[StatusRange],
) -> None:
    left_ranges = [
        status_range for status_range in ranges if status_range.side == "left"
    ]
    right_ranges = [
        status_range for status_range in ranges if status_range.side == "right"
    ]
    left_intervals = _status_line_intervals(
        text=left_text,
        ranges=left_ranges,
    )
    right_intervals = _status_line_intervals(
        text=right_text,
        ranges=right_ranges,
    )

    for row in rows:
        left_no = row.get("left_no")
        if isinstance(left_no, int):
            line_intervals = left_intervals.get(left_no - 1)
            if line_intervals is not None:
                left_text_value = row.get("left_text")
                if not isinstance(left_text_value, str):
                    raise TextDiffError("GumTree row is missing left text.")
                row["left_tokens"] = _status_tokens_for_line(
                    text=left_text_value,
                    intervals=line_intervals,
                )

        right_no = row.get("right_no")
        if isinstance(right_no, int):
            line_intervals = right_intervals.get(right_no - 1)
            if line_intervals is not None:
                right_text_value = row.get("right_text")
                if not isinstance(right_text_value, str):
                    raise TextDiffError("GumTree row is missing right text.")
                row["right_tokens"] = _status_tokens_for_line(
                    text=right_text_value,
                    intervals=line_intervals,
                )


def _changed_token_statuses(row: dict[str, Any]) -> set[GumTreeTokenStatus]:
    statuses: set[GumTreeTokenStatus] = set()
    for key in ("left_tokens", "right_tokens"):
        tokens = row.get(key)
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            if not isinstance(token, dict):
                continue
            status = token.get("status")
            if status in {"insert", "delete", "replace", "move"}:
                statuses.add(status)
    return statuses


def _project_gumtree_line_statuses(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        changed_statuses = _changed_token_statuses(row)
        if not changed_statuses:
            if row["status"] in {"insert", "delete"}:
                continue
            row["status"] = "equal"
            continue
        if changed_statuses == {"insert"}:
            row["status"] = "insert"
            continue
        if changed_statuses == {"delete"}:
            row["status"] = "delete"
            continue
        if changed_statuses == {"move"}:
            row["status"] = "move"
            continue
        row["status"] = "replace"


def _gumtree_aligned_line_rows(
    left_text: str,
    right_text: str,
) -> list[dict[str, Any]]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = SequenceMatcher(
        a=left_lines,
        b=right_lines,
        autojunk=False,
    )
    rows: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, left_line in enumerate(left_lines[i1:i2]):
                rows.append(
                    {
                        "status": "equal",
                        "left_no": i1 + offset + 1,
                        "right_no": j1 + offset + 1,
                        "left_text": left_line,
                        "right_text": left_line,
                    }
                )
            continue

        for offset, left_line in enumerate(left_lines[i1:i2]):
            rows.append(
                {
                    "status": "delete",
                    "left_no": i1 + offset + 1,
                    "right_no": None,
                    "left_text": left_line,
                    "right_text": "",
                }
            )
        for offset, right_line in enumerate(right_lines[j1:j2]):
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": j1 + offset + 1,
                    "left_text": "",
                    "right_text": right_line,
                }
            )
    return rows


def build_gumtree_rows_from_json(
    *,
    diff_json: GumTreeJson,
    left_text: str,
    right_text: str,
) -> list[dict[str, Any]]:
    ranges = _gumtree_status_ranges(diff_json)
    rows = _gumtree_aligned_line_rows(left_text, right_text)
    _apply_gumtree_statuses_to_rows(
        rows=rows,
        left_text=left_text,
        right_text=right_text,
        ranges=ranges,
    )
    _project_gumtree_line_statuses(rows)
    return rows


def unified_diff_rows(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str,
    right_path_hint: str,
) -> list[dict[str, Any]]:
    diff_lines = unified_diff_lines(
        left_text=left_text,
        right_text=right_text,
        left_label=left_path_hint,
        right_label=right_path_hint,
    )
    rows: list[dict[str, Any]] = []
    for diff_line in diff_lines:
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

    GumTree's primary signal is token status, especially `move`.  After
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

    GumTree behavior belongs in `render_diff` and this module's projection
    layer.  Workspace state is only used to choose a working directory for
    GumTree executable discovery.
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
        `run_gumtree_json` writes temporary files and parses GumTree's JSON
        stream.  Callers handle `GumTreeInvalidJsonError` by producing an
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

    @override
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
