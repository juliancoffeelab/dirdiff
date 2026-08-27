"""Project GumTree JSON ranges into dirdiff rows.

This module is the GumTree engine's row-building boundary.  It accepts raw
`gumtree textdiff -f JSON` output plus the two complete source texts, then
returns dirdiff engine rows whose visible decorations come from GumTree source
ranges.  The source texts remain the authority for displayed characters and
line breaks; GumTree ranges are the authority for changed spans and statuses.

The module intentionally does not perform text-diff alignment or line-level
change painting.  GumTree's browser view renders both files from their own
source text and applies GumTree-classified ranges over those files.  Dirdiff's
row grid needs rows, so this module pairs source lines by ordinal position
only and keeps those rows visually neutral; it does not use similarity matching
or reconstruct changed tokens from text differences.

`dirdiff.engines.gumtree.gumtree` owns subprocess execution and JSON schema
validation.  Server-side display enrichment, syntax highlighting, folds, and
HTTP payload assembly happen outside this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise, zip_longest
from pathlib import Path
from typing import Literal, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffSummary,
    InlineToken,
    InlineTokenStatus,
)
from dirdiff.engines.gumtree.gumtree import (
    GumTreeJson,
    gumtree_executable_for_cwd,
    run_gumtree_json,
)

__all__ = [
    "GumTreeDiffEngine",
    "build_gumtree_rows_from_json",
]

type _Side = Literal["left", "right"]


@dataclass(frozen=True)
class _SourceRange:
    """Half-open source range reported by GumTree.

    GumTree ranges are absolute offsets into one source document.  Callers must
    pass only ranges where `start <= end`; empty ranges are ignored by token
    projection because no visible character can carry a decoration.
    """

    start: int
    end: int


@dataclass(frozen=True)
class _LineSegment:
    """One displayed source line plus its absolute source offsets.

    `start` and `content_end` delimit the text displayed for the line.
    `segment_end` also includes the line break when the original source had
    one, allowing range iteration to skip efficiently across newline offsets.
    """

    index: int
    start: int
    content_end: int
    segment_end: int
    text: str


@dataclass(frozen=True)
class _DecorationRange:
    """Single GumTree decoration range after action classification.

    The status uses dirdiff's public inline-token vocabulary.  `ordinal` keeps
    range selection stable when two GumTree ranges have identical spans.
    """

    start: int
    end: int
    status: InlineTokenStatus
    ordinal: int


def build_gumtree_rows_from_json(
    *,
    diff_json: GumTreeJson,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    """Return dirdiff rows whose token statuses come from GumTree JSON.

    `diff_json` must be the JSON emitted for `left_text` and `right_text`.
    GumTree action tree ranges are projected directly to token ranges.  For
    update and move actions, the source tree must have a matching destination
    tree in `diff_json.matches`; missing mappings are treated as invalid engine
    data because dirdiff cannot invent the destination range.
    """

    left_ranges, right_ranges = _classified_ranges(diff_json)
    left_lines = _line_segments(left_text)
    right_lines = _line_segments(right_text)
    rows: list[DiffEngineRow] = []

    for left_segment, right_segment in zip_longest(left_lines, right_lines):
        left_tokens = (
            _tokens_for_line(segment=left_segment, ranges=left_ranges)
            if left_segment is not None
            else []
        )
        right_tokens = (
            _tokens_for_line(segment=right_segment, ranges=right_ranges)
            if right_segment is not None
            else []
        )
        rows.append(
            {
                "status": "equal",
                "left_no": (
                    None if left_segment is None else left_segment.index + 1
                ),
                "right_no": (
                    None if right_segment is None else right_segment.index + 1
                ),
                "left_text": "" if left_segment is None else left_segment.text,
                "right_text": (
                    "" if right_segment is None else right_segment.text
                ),
                "left_tokens": left_tokens,
                "right_tokens": right_tokens,
            }
        )

    return rows


@final
class GumTreeDiffEngine(DiffEngineProtocol):
    """Structural renderer backed by GumTree JSON ranges.

    The engine compares already-loaded source texts.  Path hints are used only
    to let GumTree select a parser for temporary files; they are not read as
    source content.  Missing-file diffs are rendered as whole-side insertions or
    deletions because GumTree requires both source documents.

    The engine holds no state.  It locates the GumTree executable itself when a
    render needs one, so callers neither supply nor own that discovery.
    """

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render a dirdiff engine result from loaded source sides.

        When both sides exist, GumTree JSON ranges drive the row decorations.
        When one side is absent, every displayed line on the present side is a
        whole-line insertion or deletion.
        """

        left_text = "" if old.text is None else old.text
        right_text = "" if new.text is None else new.text

        if old.exists and new.exists:
            diff_json = self._run_gumtree_json(
                left_text=left_text,
                right_text=right_text,
                left_path_hint="" if old.path_hint is None else old.path_hint,
                right_path_hint="" if new.path_hint is None else new.path_hint,
            )
            rows = build_gumtree_rows_from_json(
                diff_json=diff_json,
                left_text=left_text,
                right_text=right_text,
            )
        elif old.exists:
            rows = _whole_side_rows(text=left_text, side="left")
        else:
            rows = _whole_side_rows(text=right_text, side="right")

        return {
            "summary": _summary(rows),
            "rows": rows,
        }

    def _run_gumtree_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str,
        right_path_hint: str,
    ) -> GumTreeJson:
        """Run GumTree for tests and the public renderer entrypoint.

        The method exists so behavior tests can inspect the same raw JSON that
        powers rendering without duplicating executable discovery.
        """

        return run_gumtree_json(
            gumtree_bin=gumtree_executable_for_cwd(Path.cwd()),
            left_text=left_text,
            right_text=right_text,
            left_path_hint=left_path_hint,
            right_path_hint=right_path_hint,
        )


_TREE_RANGE_RE = re.compile(r"\[(?P<start>\d+),(?P<end>\d+)\]\s*$")


def _range_from_tree(tree: str) -> _SourceRange:
    """Extract the absolute half-open range from a GumTree tree string."""

    match = _TREE_RANGE_RE.search(tree)
    if match is None:
        raise ValueError(f"GumTree tree is missing a source range: {tree!r}")

    source_range = _SourceRange(
        start=int(match.group("start")),
        end=int(match.group("end")),
    )
    if source_range.start > source_range.end:
        raise ValueError(f"GumTree tree has an invalid range: {tree!r}")
    return source_range


def _line_segments(text: str) -> list[_LineSegment]:
    """Split source text into displayed lines with absolute offsets."""

    segments: list[_LineSegment] = []
    cursor = 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        content = line.rstrip("\r\n")
        content_end = cursor + len(content)
        segment_end = cursor + len(line)
        segments.append(
            _LineSegment(
                index=index,
                start=cursor,
                content_end=content_end,
                segment_end=segment_end,
                text=content,
            )
        )
        cursor = segment_end
    return segments


def _classified_ranges(
    diff_json: GumTreeJson,
) -> tuple[list[_DecorationRange], list[_DecorationRange]]:
    """Return left and right GumTree decoration ranges from JSON actions."""

    def _required_dest(dest_by_src: dict[str, str], src_tree: str) -> str:
        """Return the destination tree paired with one source tree."""
        dest_tree = dest_by_src.get(src_tree)
        if dest_tree is None:
            raise ValueError(
                f"GumTree action range has no destination mapping: {src_tree!r}"
            )
        return dest_tree

    dest_by_src = {
        match["src"]: match["dest"] for match in diff_json.get("matches", [])
    }
    left_ranges: list[_DecorationRange] = []
    right_ranges: list[_DecorationRange] = []

    for ordinal, action in enumerate(diff_json.get("actions", [])):
        action_name = action["action"]
        tree = action["tree"]
        if action_name.startswith("insert"):
            _append_range(right_ranges, tree, "insert", ordinal)
            continue
        if action_name.startswith("delete"):
            _append_range(left_ranges, tree, "delete", ordinal)
            continue
        if action_name.startswith("update"):
            _append_range(left_ranges, tree, "replace", ordinal)
            _append_range(
                right_ranges,
                _required_dest(dest_by_src, tree),
                "replace",
                ordinal,
            )
            continue
        if action_name.startswith("move"):
            _append_range(left_ranges, tree, "move", ordinal)
            _append_range(
                right_ranges, _required_dest(dest_by_src, tree), "move", ordinal
            )

    return (
        sorted(
            left_ranges, key=lambda item: (item.start, item.end, item.ordinal)
        ),
        sorted(
            right_ranges, key=lambda item: (item.start, item.end, item.ordinal)
        ),
    )


def _append_range(
    ranges: list[_DecorationRange],
    tree: str,
    status: InlineTokenStatus,
    ordinal: int,
) -> None:
    """Append one non-empty GumTree range to a side's decoration list."""

    source_range = _range_from_tree(tree)
    if source_range.start == source_range.end:
        return
    ranges.append(
        _DecorationRange(
            start=source_range.start,
            end=source_range.end,
            status=status,
            ordinal=ordinal,
        )
    )


def _tokens_for_line(
    *,
    segment: _LineSegment,
    ranges: list[_DecorationRange],
) -> list[InlineToken]:
    """Return one line's tokens split exactly at GumTree range boundaries."""

    boundaries = {segment.start, segment.content_end}
    relevant: list[_DecorationRange] = []
    for item in ranges:
        if item.end <= segment.start:
            continue
        if item.start >= segment.content_end:
            break
        relevant.append(item)
        boundaries.add(max(segment.start, item.start))
        boundaries.add(min(segment.content_end, item.end))

    if segment.text == "":
        return []

    tokens: list[InlineToken] = []
    ordered_boundaries = sorted(boundaries)
    for start, end in pairwise(ordered_boundaries):
        if start == end:
            continue
        text = segment.text[start - segment.start : end - segment.start]
        tokens.append(
            {
                "text": text,
                "is_ws": text.isspace(),
                "status": _visible_status(start, end, relevant),
            }
        )
    return _merge_adjacent_tokens(tokens)


def _visible_status(
    start: int,
    end: int,
    ranges: list[_DecorationRange],
) -> InlineTokenStatus:
    """Return the visible dirdiff status for one source slice.

    Monaco receives overlapping GumTree decorations and uses z-index to show
    deeper tree decorations above wider ancestors.  GumTree JSON does not carry
    the tree depth used by the original web view, so this projection chooses the
    narrowest covering range as the closest available representation.
    """

    covering = [
        item for item in ranges if item.start <= start and item.end >= end
    ]
    if covering == []:
        return "unchanged"
    return min(
        covering,
        key=lambda item: (item.end - item.start, item.ordinal),
    ).status


def _merge_adjacent_tokens(tokens: list[InlineToken]) -> list[InlineToken]:
    """Merge neighboring tokens that share the same visible status."""

    merged: list[InlineToken] = []
    for token in tokens:
        if merged != [] and merged[-1]["status"] == token["status"]:
            merged[-1] = {
                "text": merged[-1]["text"] + token["text"],
                "is_ws": merged[-1]["is_ws"] and token["is_ws"],
                "status": token["status"],
            }
            continue
        merged.append(token)
    return merged


def _token_row_status(
    left_tokens: list[InlineToken],
    right_tokens: list[InlineToken],
) -> Literal["equal", "replace", "insert", "delete", "move"]:
    """Summarize GumTree token classes for non-visual summary counts."""

    statuses = {
        token["status"]
        for token in left_tokens + right_tokens
        if token["status"] != "unchanged"
    }
    if statuses == set():
        return "equal"
    if statuses == {"insert"}:
        return "insert"
    if statuses == {"delete"}:
        return "delete"
    if statuses == {"move"}:
        return "move"
    return "replace"


def _whole_side_rows(*, text: str, side: _Side) -> list[DiffEngineRow]:
    """Render a missing-side diff as whole-line GumTree-compatible rows."""

    rows: list[DiffEngineRow] = []
    for segment in _line_segments(text):
        row_status: Literal["delete", "insert"] = (
            "delete" if side == "left" else "insert"
        )
        tokens: list[InlineToken] = (
            []
            if segment.text == ""
            else [
                {
                    "text": segment.text,
                    "is_ws": segment.text.isspace(),
                    "status": row_status,
                }
            ]
        )
        rows.append(
            {
                "status": row_status,
                "left_no": segment.index + 1 if side == "left" else None,
                "right_no": segment.index + 1 if side == "right" else None,
                "left_text": segment.text if side == "left" else "",
                "right_text": segment.text if side == "right" else "",
                "left_tokens": tokens if side == "left" else [],
                "right_tokens": tokens if side == "right" else [],
            }
        )
    return rows


def _summary(rows: list[DiffEngineRow]) -> DiffSummary:
    """Count changed rows while keeping GumTree visual rows neutral."""

    modified_lines = 0
    added_lines = 0
    removed_lines = 0
    moved_lines = 0
    for row in rows:
        status = row["status"]
        if status == "equal":
            status = _token_row_status(row["left_tokens"], row["right_tokens"])
        if status == "replace":
            modified_lines += 1
        elif status == "insert":
            added_lines += 1
        elif status == "delete":
            removed_lines += 1
        elif status == "move":
            moved_lines += 1
    return {
        "changed_lines": (
            modified_lines + added_lines + removed_lines + moved_lines
        ),
        "modified_lines": modified_lines,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "moved_lines": moved_lines,
    }
