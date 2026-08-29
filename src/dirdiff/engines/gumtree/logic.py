"""Build dirdiff engine rows from GumTree source ranges.

## Public interface

`build_gumtree_rows_from_json` maps validated GumTree actions onto the supplied
source strings. `GumTreeDiffEngine` implements the common engine protocol and
handles one-sided Files without invoking GumTree.

## Purpose and boundaries

The source strings decide displayed characters and line breaks; GumTree ranges
decide token status. Because GumTree does not supply dirdiff row alignment, this
module pairs lines by ordinal position and keeps row status neutral. It does not
invent similarity matches or derive changed tokens from textual resemblance.
Executable discovery and JSON validation happen before this boundary, and
display decoration happens after it.
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
"""Select the source document for private GumTree range processing.

- `left` addresses the old text.
- `right` addresses the new text.

Helpers use this value only to choose source ranges and token status. It never
crosses into public engine or HTTP contracts.
"""


@dataclass(frozen=True)
class _SourceRange:
    """Half-open source range reported by GumTree.

    GumTree parsing creates these values before row building. `start` is
    inclusive and `end` is exclusive in one complete source document.

    Empty ranges carry no visible decoration and are ignored. The offsets are
    not line numbers, byte spans, or public review coordinates.
    """

    start: int
    """Inclusive Python-string offset in the complete addressed document.

    Construction accepts zero and ignores a range only when this equals `end`;
    token mapping later intersects it with line segments without rebasing it.
    """

    end: int
    """Exclusive document offset parsed from GumTree's terminal range.

    Reversed values are rejected at the JSON-to-range boundary. The value may
    equal `start`, producing an empty range that carries no visible token.
    """


@dataclass(frozen=True)
class _LineSegment:
    """One displayed source line plus its absolute source offsets.

    `_line_segments` constructs these in source order. Token mapping uses
    the absolute offsets to intersect GumTree ranges with the displayed line.

    `content_end` excludes the line break; `segment_end` includes it when
    present. This private value is not an engine row.
    """

    index: int
    """Zero-based position in the ordered displayed-line sequence.

    Row building adds one when producing public line numbers; the absolute
    offsets below remain independent of this display coordinate.
    """

    start: int
    """Inclusive document offset of this line including any empty content.

    It equals the preceding segment's `segment_end`, preserving total source
    coverage across LF, CRLF, and unterminated final lines.
    """

    content_end: int
    """Exclusive document offset after visible text but before its terminator.

    Decoration slices stop here so newline bytes never become inline tokens.
    Empty displayed lines legitimately have `content_end == start`.
    """

    segment_end: int
    """Exclusive document offset after this line's complete terminator.

    It advances the next segment's start while keeping CRLF together. For an
    unterminated final line it equals `content_end`.
    """

    text: str
    """Exact source slice rendered for this line, excluding CR/LF characters.

    Token concatenation must reproduce this value. Public row numbering comes
    from `index`, not from content inspection.
    """


@dataclass(frozen=True)
class _DecorationRange:
    """Single GumTree decoration range after action classification.

    Action classification creates these values before token mapping. The
    status already uses dirdiff's inline vocabulary; `ordinal` makes selection
    deterministic when GumTree emits identical spans.

    The range remains absolute to one source document and never becomes a
    public review region.
    """

    start: int
    """Inclusive document offset where this classified action begins.

    It stays absolute until intersection with `_LineSegment`; callers never use
    it as a byte, row, or review coordinate.
    """

    end: int
    """Exclusive document offset after this action range.

    Empty GumTree ranges are discarded before decoration. Overlap remains valid
    because the narrowest covering classified range wins for each token slice.
    """

    status: InlineTokenStatus
    """Public token classification derived from the action kind and side.

    Inserts, deletes, updates, and moves map before row building. The value
    decorates tokens only; GumTree rows retain neutral visual row status.
    """

    ordinal: int
    """Zero-based GumTree action order retained for deterministic precedence.

    When equally narrow ranges cover the same slice, the earlier action wins.
    This tie-breaker is private and never reaches public engine rows.
    """


def build_gumtree_rows_from_json(
    *,
    diff_json: GumTreeJson,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    """Return dirdiff rows whose token statuses come from GumTree JSON.

    `diff_json` must be the JSON emitted for `left_text` and `right_text`.
    GumTree action tree ranges map directly to token ranges. For
    update and move actions, the source tree must have a matching destination
    tree in `diff_json.matches`; missing mappings are treated as invalid engine
    data because dirdiff cannot invent the destination range.

    # Parameters

    - `diff_json`: Validated GumTree matches and actions for this source pair.
    - `left_text`: Complete old source addressed by source-tree ranges.
    - `right_text`: Complete new source addressed by destination ranges.

    # Usage

    Pass the validated JSON and the exact source strings used for that GumTree
    invocation. `GumTreeDiffEngine.render_diff` is the application entrypoint.

    # Failures

    Raises `ValueError` when an action range is malformed, outside its source,
    or lacks a required destination mapping.
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
    render needs one, so callers do not supply an executable path.
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

        # Parameters

        - `old`: Already-loaded old source and its optional parser hint.
        - `new`: Already-loaded new source under the same contract.

        # Usage

        Obtain this renderer through `dirdiff.engines.engine` and normally let
        `dirdiff.formats.Composer` call it for a text bay.

        # Failures

        Propagates `DirdiffError` for executable and parser failures and
        `ValueError` for GumTree ranges that contradict the supplied source.
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

        # Parameters

        - `left_text`: Complete old source for GumTree.
        - `right_text`: Complete new source for GumTree.
        - `left_path_hint`: Old source name used for parser selection.
        - `right_path_hint`: New source name used for parser selection.
        """

        return run_gumtree_json(
            gumtree_bin=gumtree_executable_for_cwd(Path.cwd()),
            left_text=left_text,
            right_text=right_text,
            left_path_hint=left_path_hint,
            right_path_hint=right_path_hint,
        )


_TREE_RANGE_RE = re.compile(r"\[(?P<start>\d+),(?P<end>\d+)\]\s*$")
"""Extract GumTree's terminal half-open source range from a tree description.

Node labels may contain other punctuation, so the expression only accepts the
final bracketed integer pair. `_range_from_tree` validates its ordering.
"""


def _range_from_tree(tree: str) -> _SourceRange:
    """Parse and validate GumTree's terminal absolute source range.

    Only the final bracketed integer pair counts; punctuation inside a node
    label is ignored. Missing or reversed ranges raise `ValueError` because an
    action without exact source identity cannot be mapped to source safely.

    # Failures

    Raises `ValueError` when the tree has no terminal source range or its start
    is after its end.
    """

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
    """Partition source into ordered display lines and total document offsets.

    Terminators contribute to `segment_end` but are absent from `text` and
    `content_end`. The segments preserve enough geometry to intersect absolute
    GumTree ranges without placing line breaks in inline tokens.
    """

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
    """Classify validated GumTree actions into old- and new-side ranges.

    Insert/delete actions address one side directly; update and move actions use
    the validated match mapping for their destination. Returned lists preserve
    deterministic action ordinals and contain no empty ranges.

    # Returns

    - `First`: Nonempty old-side action ranges ordered by action ordinal.
    - `Second`: Nonempty new-side ranges in the same deterministic action order,
      including mapped destinations for updates and moves.

    # Failures

    Raises `ValueError` when an action's tree lacks a valid terminal source
    range, or when an update or move has no destination in GumTree's match
    mapping. `_gumtree_rows_from_json` propagates the error because it cannot
    place that action against either supplied source.
    """

    def _required_dest(dest_by_src: dict[str, str], src_tree: str) -> str:
        """Return the destination tree paired with one source tree.

        # Parameters

        - `dest_by_src`: Exact GumTree match mapping for this comparison.
        - `src_tree`: Source tree named by an update or move action.

        # Failures

        Raises `ValueError` when an update or move has no destination match.
        """
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
    """Append one non-empty GumTree range to a side's decoration list.

    # Parameters

    - `ranges`: Side-specific decoration list to extend.
    - `tree`: GumTree tree description containing the source range.
    - `status`: Visible classification assigned to the range.
    - `ordinal`: Source action order used to break equal-range ties.
    """

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
    """Return one line's tokens split exactly at GumTree range boundaries.

    # Parameters

    - `segment`: Display line and its absolute source offsets.
    - `ranges`: Ordered decorations for the complete source side.
    """

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
    the tree depth used by the original web view, so this mapping chooses the
    narrowest covering range as the closest available representation.

    # Parameters

    - `start`: Inclusive absolute start of the source slice.
    - `end`: Exclusive absolute end of the same slice.
    - `ranges`: Decorations intersecting the containing display line.
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
    """Coalesce consecutive projected slices with identical token status.

    Text concatenation and whitespace truth are preserved, so the result
    replays the same line while avoiding presentation-only token fragmentation.
    Non-neighboring or differently classified slices remain separate.
    """

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
    """Summarize GumTree token classes for non-visual summary counts.

    # Parameters

    - `left_tokens`: Old-side decorations for one paired row.
    - `right_tokens`: New-side decorations for that row.
    """

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
    """Render a missing-side diff as whole-line GumTree-compatible rows.

    # Parameters

    - `text`: Complete source of the only existing side.
    - `side`: Whether that source is old or new.
    """

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
    """Derive line totals from neutral GumTree rows and their inline tokens.

    Equal row status is reclassified only for counting, so updates and moves
    remain visually neutral rows while still contributing to modified or moved
    totals. The function never rewrites the supplied rows.
    """

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
