"""Align source lines and retain GumTree's exact edit and move ranges.

`GumTreeJson` and `is_gumtree_json` expose validated subprocess facts.
`build_gumtree_rows_from_json` consumes raw GumTree JSON for its exact
source pair. `GumTreeDiffEngine` supplies that JSON through the subprocess
adapter and builds unified-diff rows locally for absent sides or unsupported
languages.

Unchanged lines anchor alignment; changed gaps pair in source order. GumTree
alone classifies inline edits and move endpoints. The private driver contains
executable discovery, temporary inputs, and raw JSON validation. This module
retains no workspace files, syntax decoration, folds, HTTP state, or persistent
data.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from difflib import SequenceMatcher, unified_diff
from heapq import heappop, heappush
from itertools import pairwise, zip_longest
from pathlib import Path
from typing import (
    Literal,
    final,
    override,
)

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffSummary,
    DirdiffError,
    GumTreeMatcher,
    InlineToken,
    InlineTokenStatus,
    MoveSpan,
    PairedMove,
)
from dirdiff.engines.gumtree._logic.driver import (
    GumTreeJson,
    GumTreeUnsupportedError,
    gumtree_executable_for_cwd,
    is_gumtree_json,
    run_gumtree_json,
)

__all__ = [
    "GumTreeDiffEngine",
    "GumTreeJson",
    "build_gumtree_rows_from_json",
    "is_gumtree_json",
]


@dataclass(frozen=True)
class _Line:
    """Exact displayed line and its absolute character start, excluding CR/LF.

    Source order supplies its one-based line number. Empty lines are real
    lines; terminators advance the next start without becoming visible tokens.
    """

    start: int
    """Inclusive Python character offset in the complete source document."""

    text: str
    """Exact displayed source slice; line terminators are excluded."""


@dataclass(frozen=True)
class _Decoration:
    """Nonempty action interval with deterministic precedence and move facts.

    Offsets address the complete supplied Python string. Narrower actions win
    token status; edits beat equally wide moves, then input order breaks ties.
    A move's endpoints remain
    available independently of an edited descendant's winning status.
    """

    start: int
    """Inclusive source character offset, validated against the supplied text."""

    end: int
    """Exclusive source character offset, strictly greater than start."""

    status: InlineTokenStatus
    """GumTree action classification, independent of the row relationship."""

    ordinal: int
    """Input action order, breaking ties after span width and edit priority."""

    move: PairedMove | None
    """Both endpoints for a move action; other actions have no move of their own."""


def build_gumtree_rows_from_json(
    *,
    diff_json: GumTreeJson,
    left_text: str,
    right_text: str,
    left_path_hint: str,
    right_path_hint: str,
) -> list[DiffEngineRow]:
    """Return lossless, ordered rows from exact GumTree source facts.

    Filename hints select the generator coordinate convention. External UTF-16
    and tree-sitter offsets are converted to exact Python character positions.
    Every action range must be in bounds, and moves and updates require a
    one-to-one destination mapping. Invalid facts assert at this boundary;
    ambiguous external Unicode coordinates raise `DirdiffError`.
    Unchanged complete lines anchor alignment, excluding moved or edited lines.
    Changed gaps pair by position without inventing textual token changes.

    # Parameters

    - `diff_json`: Validated raw GumTree matches and actions for this source pair.
    - `left_text`: Complete, unnormalized old source supplied to GumTree.
    - `right_text`: Complete, unnormalized new source supplied to GumTree.
    - `left_path_hint`: Old filename selecting the generator offset convention.
    - `right_path_hint`: New filename under the same parser-selection contract.
    """

    def lines(text: str) -> list[_Line]:
        """Split one complete source while retaining exact character starts."""
        result: list[_Line] = []
        cursor = 0
        for line in text.splitlines(keepends=True):
            result.append(_Line(cursor, line.rstrip("\r\n")))
            cursor += len(line)
        return result

    left_lines, right_lines = lines(left_text), lines(right_text)
    left_starts = [line.start for line in left_lines]
    right_starts = [line.start for line in right_lines]

    def source_offsets(
        text: str, path_hint: str
    ) -> dict[int, list[int]] | None:
        """Invert the selected generator's source coordinates without guessing.

        Tree-sitter-ng sums prior UTF-16 line lengths plus normalized LF, then
        adds a UTF-8 byte column. Its coordinates can collide across lines.
        YAML addresses Unicode characters; other bundled generators address
        Java UTF-16 strings. Each map retains
        every candidate so a collision cannot silently pick the wrong line.

        # Parameters

        - `text`: Complete original source, including its exact line terminators.
        - `path_hint`: Parser-selecting filename passed to the subprocess adapter.

        # Returns

        - A map from each valid external character boundary to all source positions.
        - `None`: External offsets already equal Python character offsets.
        """
        suffix = Path(path_hint).suffix
        if suffix in {".yaml", ".yml"}:
            return None
        tree_sitter = path_hint.endswith("CMakeLists.txt") or suffix in {
            ".py",
            ".rs",
            ".ts",
            ".tsx",
            ".js",
            ".go",
            ".hs",
            ".kt",
            ".ml",
            ".php",
            ".rb",
            ".r",
            ".R",
            ".swift",
            ".c",
            ".h",
            ".cs",
            ".C",
            ".CC",
            ".cpp",
            ".cc",
            ".hh",
            ".hpp",
            ".cmake",
        }
        if text.isascii() and (not tree_sitter or "\r" not in text):
            return None
        offsets: dict[int, list[int]] = {}
        if not tree_sitter:
            offset = 0
            for index, character in enumerate(text):
                offsets[offset] = [index]
                offset += len(character.encode("utf-16-le")) // 2
            offsets[offset] = [len(text)]
            return offsets
        java_start = 0
        for line in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", text):
            if text != "" and line.start() == len(text):
                continue
            content = line.group().rstrip("\r\n")
            byte_column = 0
            for column in range(len(content) + 1):
                offsets.setdefault(java_start + byte_column, []).append(
                    line.start() + column
                )
                if column < len(content):
                    byte_column += len(content[column].encode("utf-8"))
            java_start += len(content.encode("utf-16-le")) // 2 + 1
        return offsets

    left_offsets = source_offsets(left_text, left_path_hint)
    right_offsets = source_offsets(right_text, right_path_hint)

    def source_range(
        tree: str, text: str, offsets: dict[int, list[int]] | None
    ) -> tuple[int, int]:
        """Read a terminal character interval and reject impossible source facts.

        # Parameters

        - `tree`: GumTree tree spelling with its terminal half-open range.
        - `text`: Exact source addressed by this tree.
        - `offsets`: Boundary map for that source, or None for identical units.

        # Returns

        - `First`: Inclusive Python character offset.
        - `Second`: Exclusive Python character offset in the same source.
        """
        match = re.search(r"\[(\d+),(\d+)\]\s*$", tree)
        assert match is not None, f"GumTree tree has no range: {tree!r}"
        raw_start, raw_end = map(int, match.groups())
        assert raw_start <= raw_end, tree
        if offsets is None:
            assert 0 <= raw_start <= raw_end <= len(text), (tree, len(text))
            return raw_start, raw_end
        starts, ends = offsets.get(raw_start, []), offsets.get(raw_end, [])
        assert starts != [] and ends != [], (
            f"GumTree range is outside source or splits a character: {tree}"
        )
        if len(starts) != 1 or len(ends) != 1:
            raise GumTreeAmbiguousOffsetsError(
                "GumTree returned ambiguous Unicode offsets; this source range "
                f"cannot be located reliably: {tree}. Showing unified diff instead."
            )
        start, end = starts[0], ends[0]
        assert 0 <= start <= end <= len(text), (tree, len(text))
        return start, end

    def move_span(start: int, end: int, starts: list[int]) -> MoveSpan:
        """Express one nonempty move in bay-local Unicode line coordinates.

        # Parameters

        - `start`: Validated inclusive character offset of the move.
        - `end`: Validated exclusive character offset of the move.
        - `starts`: Ordered character starts for the same source's display lines.
        """
        start_index = bisect_right(starts, start) - 1
        end_index = bisect_right(starts, end) - 1
        assert start_index >= 0 and end_index >= start_index
        return {
            "start_line": start_index + 1,
            "start_column": start - starts[start_index],
            "end_line": end_index + 1,
            "end_column": end - starts[end_index],
        }

    destinations: dict[str, str] = {}
    seen_destinations: set[str] = set()
    for match in diff_json.get("matches", []):
        assert match["src"] not in destinations, match
        assert match["dest"] not in seen_destinations, match
        destinations[match["src"]] = match["dest"]
        seen_destinations.add(match["dest"])
    left_ranges: list[_Decoration] = []
    right_ranges: list[_Decoration] = []
    statuses: dict[str, InlineTokenStatus] = {
        "insert-node": "insert",
        "insert-tree": "insert",
        "delete-node": "delete",
        "delete-tree": "delete",
        "update-node": "replace",
        "move-tree": "move",
    }
    for ordinal, action in enumerate(diff_json.get("actions", [])):
        assert action["action"] in statuses, action
        status = statuses[action["action"]]
        tree = action["tree"]
        if status == "insert":
            start, end = source_range(tree, right_text, right_offsets)
            if start < end:
                right_ranges.append(
                    _Decoration(start, end, status, ordinal, None)
                )
        elif status == "delete":
            start, end = source_range(tree, left_text, left_offsets)
            if start < end:
                left_ranges.append(
                    _Decoration(start, end, status, ordinal, None)
                )
        else:
            assert tree in destinations, (
                f"GumTree action has no destination: {tree}"
            )
            left_start, left_end = source_range(tree, left_text, left_offsets)
            right_start, right_end = source_range(
                destinations[tree], right_text, right_offsets
            )
            move: PairedMove | None = None
            if status == "move":
                assert left_start < left_end and right_start < right_end
                move = {
                    "kind": "paired",
                    "left": move_span(left_start, left_end, left_starts),
                    "right": move_span(right_start, right_end, right_starts),
                }
            if left_start < left_end:
                left_ranges.append(
                    _Decoration(left_start, left_end, status, ordinal, move)
                )
            if right_start < right_end:
                right_ranges.append(
                    _Decoration(right_start, right_end, status, ordinal, move)
                )

    left_tokens = _tokens_for_lines(left_lines, left_ranges)
    right_tokens = _tokens_for_lines(right_lines, right_ranges)
    # Remove changed lines before matching. Otherwise a long repeated suffix
    # can steal the prefix's identical lines across an insertion. The original
    # indices retain every changed gap for the final source-order walk.
    left_context = [
        index
        for index, tokens in enumerate(left_tokens)
        if all(t["status"] == "unchanged" for t in tokens)
    ]
    right_context = [
        index
        for index, tokens in enumerate(right_tokens)
        if all(t["status"] == "unchanged" for t in tokens)
    ]
    left_keys = [left_lines[index].text for index in left_context]
    right_keys = [right_lines[index].text for index in right_context]
    if left_keys == right_keys:
        anchors = list(zip(left_context, right_context, strict=True))
    else:
        matcher = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)
        anchors = [
            (left_context[block.a + offset], right_context[block.b + offset])
            for block in matcher.get_matching_blocks()
            for offset in range(block.size)
        ]
    anchors.append((len(left_lines), len(right_lines)))
    rows: list[DiffEngineRow] = []
    left_cursor = right_cursor = 0
    for left_anchor, right_anchor in anchors:
        # Include the anchor after its preceding gap, but never pair it with
        # an unmatched line from that gap.
        pairs = list(
            zip_longest(
                range(left_cursor, left_anchor),
                range(right_cursor, right_anchor),
            )
        )
        if left_anchor < len(left_lines):
            pairs.append((left_anchor, right_anchor))
        for left, right in pairs:
            old_text = "" if left is None else left_lines[left].text
            new_text = "" if right is None else right_lines[right].text
            # Alignment gaps do not imply edits. Only a complete insertion or
            # deletion gives a row changed status; other GumTree facts remain
            # on their exact token ranges.
            tokens = ([] if left is None else left_tokens[left]) + (
                [] if right is None else right_tokens[right]
            )
            row_status: Literal["equal", "insert", "delete"]
            if tokens != [] and all(
                token["status"] == "insert" for token in tokens
            ):
                row_status = "insert"
            elif tokens != [] and all(
                token["status"] == "delete" for token in tokens
            ):
                row_status = "delete"
            else:
                row_status = "equal"
            rows.append(
                {
                    "status": row_status,
                    "left_no": None if left is None else left + 1,
                    "right_no": None if right is None else right + 1,
                    "left_text": old_text,
                    "right_text": new_text,
                    "left_tokens": [] if left is None else left_tokens[left],
                    "right_tokens": []
                    if right is None
                    else right_tokens[right],
                }
            )
        left_cursor, right_cursor = left_anchor + 1, right_anchor + 1
    return rows


def _tokens_for_lines(
    lines: list[_Line], ranges: list[_Decoration]
) -> list[list[InlineToken]]:
    """Partition one source side at action boundaries without rescanning actions.

    Two local heaps track the narrowest active action and containing move.
    They live only for this source walk. Sorted endpoints and lazy expiry cost
    O((lines + actions) log(lines + actions) + tokens log(actions + 1)).
    Adjacent slices merge only when both status and move identity agree.

    # Parameters

    - `lines`: Complete source-ordered display lines with absolute starts.
    - `ranges`: Validated action intervals in that same source's coordinates.
    """
    ranges = sorted(ranges, key=lambda item: item.start)
    boundaries = sorted(
        {point for item in ranges for point in (item.start, item.end)}
    )
    active: list[tuple[int, bool, int, int, _Decoration]] = []
    moves: list[tuple[int, bool, int, int, _Decoration]] = []
    position = 0
    result: list[list[InlineToken]] = []
    for line in lines:
        tokens: list[InlineToken] = []
        end = line.start + len(line.text)
        cuts = [
            line.start,
            *boundaries[
                bisect_right(boundaries, line.start) : bisect_right(
                    boundaries, end - 1
                )
            ],
            end,
        ]
        for start, stop in pairwise(cuts):
            if start == stop:
                continue
            while position < len(ranges) and ranges[position].start <= start:
                item = ranges[position]
                entry = (
                    item.end - item.start,
                    item.status == "move",
                    item.ordinal,
                    item.end,
                    item,
                )
                heappush(active, entry)
                if item.move is not None:
                    heappush(moves, entry)
                position += 1
            while active != [] and active[0][3] <= start:
                heappop(active)
            while moves != [] and moves[0][3] <= start:
                heappop(moves)
            status: InlineTokenStatus = (
                active[0][4].status if active != [] else "unchanged"
            )
            move = moves[0][4].move if moves != [] else None
            text = line.text[start - line.start : stop - line.start]
            if (
                tokens != []
                and tokens[-1]["status"] == status
                and tokens[-1].get("move") == move
            ):
                tokens[-1]["text"] += text
                tokens[-1]["is_ws"] = tokens[-1]["text"].isspace()
            else:
                token: InlineToken = {
                    "text": text,
                    "is_ws": text.isspace(),
                    "status": status,
                }
                if move is not None:
                    token["move"] = move
                tokens.append(token)
        result.append(tokens)
    return result


@final
@dataclass
class GumTreeDiffEngine(DiffEngineProtocol):
    """Structural renderer backed by GumTree JSON ranges.

    The engine compares already-loaded source texts. Path hints select the
    parser and its offset convention; they never authorize a source read.
    Missing sides produce complete insertion or deletion rows. Unsupported
    languages and ambiguous source offsets use a local unified diff with a
    visible engine warning.

    The engine retains only its matcher selection. It locates the GumTree executable itself when a
    render needs one, so callers do not supply an executable path.
    """

    matcher: GumTreeMatcher
    """Exact upstream matcher used for syntax-node correspondence."""

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
        `AssertionError` for GumTree facts that contradict the supplied source.
        """

        def _unified_diff_rows(
            *,
            left_text: str,
            right_text: str,
            left_label: str,
            right_label: str,
        ) -> list[DiffEngineRow]:
            """Return textual rows when GumTree cannot produce structural rows.

            The caller supplies both complete text sides and their display labels.
            This operation parses Python's unified-diff output directly into the
            complete neutral engine rows consumed directly by rendering. Identical
            inputs produce no patch hunks, so they are emitted explicitly as equal
            rows rather than mistaken for empty documents.

            # Parameters

            - `left_text`: Complete old text passed to `unified_diff`.
            - `right_text`: Complete new text passed to `unified_diff`.
            - `left_label`: Old-side header label for the temporary patch.
            - `right_label`: New-side header label for the temporary patch.
            """
            left_lines = left_text.splitlines()
            right_lines = right_text.splitlines()
            if left_lines == right_lines:
                return [
                    {
                        "status": "equal",
                        "left_no": index,
                        "right_no": index,
                        "left_text": line,
                        "right_text": line,
                    }
                    for index, line in enumerate(left_lines, start=1)
                ]
            patch_lines = unified_diff(
                left_lines,
                right_lines,
                fromfile=left_label,
                tofile=right_label,
                lineterm="",
                n=max(len(left_lines), len(right_lines)),
            )
            hunk_header_pattern = re.compile(
                r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
                r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
            )
            rows: list[DiffEngineRow] = []
            left_no = 1
            right_no = 1
            in_hunk = False

            for line in patch_lines:
                hunk_match = hunk_header_pattern.match(line)
                if hunk_match is not None:
                    left_no = int(hunk_match.group("left_start"))
                    right_no = int(hunk_match.group("right_start"))
                    in_hunk = True
                    continue
                if not in_hunk or line.startswith("\\"):
                    continue

                prefix = " "
                text = ""
                if line != "":
                    prefix = line[0]
                    text = line[1:]

                if prefix == " ":
                    rows.append(
                        {
                            "status": "equal",
                            "left_no": left_no,
                            "right_no": right_no,
                            "left_text": text,
                            "right_text": text,
                        }
                    )
                    left_no += 1
                    right_no += 1
                    continue
                if prefix == "-":
                    rows.append(
                        {
                            "status": "delete",
                            "left_no": left_no,
                            "right_no": None,
                            "left_text": text,
                            "right_text": "",
                        }
                    )
                    left_no += 1
                    continue
                if prefix == "+":
                    rows.append(
                        {
                            "status": "insert",
                            "left_no": None,
                            "right_no": right_no,
                            "left_text": "",
                            "right_text": text,
                        }
                    )
                    right_no += 1
            return rows

        assert old.exists == (old.text is not None)
        assert new.exists == (new.text is not None)
        assert old.exists or new.exists
        left_text = "" if old.text is None else old.text
        right_text = "" if new.text is None else new.text
        left_label = "" if old.path_hint is None else old.path_hint
        right_label = "" if new.path_hint is None else new.path_hint
        if not old.exists or not new.exists:
            rows = _unified_diff_rows(
                left_text=left_text,
                right_text=right_text,
                left_label=left_label,
                right_label=right_label,
            )
            return {
                "rows": rows,
                "summary": _summary(rows),
                "interleave_inline_rows": False,
            }
        assert old.text is not None and new.text is not None
        try:
            diff_json = self._run_gumtree_json(
                left_text=old.text,
                right_text=new.text,
                left_path_hint=left_label,
                right_path_hint=right_label,
            )
            rows = build_gumtree_rows_from_json(
                diff_json=diff_json,
                left_text=old.text,
                right_text=new.text,
                left_path_hint="" if old.path_hint is None else old.path_hint,
                right_path_hint="" if new.path_hint is None else new.path_hint,
            )
        except (GumTreeUnsupportedError, GumTreeAmbiguousOffsetsError) as error:
            rows = _unified_diff_rows(
                left_text=left_text,
                right_text=right_text,
                left_label=left_label,
                right_label=right_label,
            )
            return {
                "rows": rows,
                "summary": _summary(rows),
                "interleave_inline_rows": False,
                "engine_warning": {
                    "type": (
                        "gumtree_ambiguous_offsets"
                        if isinstance(error, GumTreeAmbiguousOffsetsError)
                        else "gumtree_unsupported_language"
                    ),
                    "message": str(error),
                },
            }

        return {
            "summary": _summary(rows),
            "rows": rows,
            "interleave_inline_rows": False,
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
            matcher=self.matcher,
            gumtree_bin=gumtree_executable_for_cwd(Path.cwd()),
            left_text=left_text,
            right_text=right_text,
            left_path_hint=left_path_hint,
            right_path_hint=right_path_hint,
        )


def _summary(rows: list[DiffEngineRow]) -> DiffSummary:
    """Count semantic row classifications once per aligned position.

    Structural context remains unchanged even when its text wraps differently;
    textual fallback rows already carry their unified-diff classifications.
    """
    summary: DiffSummary = {
        "changed_lines": 0,
        "modified_lines": 0,
        "added_lines": 0,
        "removed_lines": 0,
        "moved_lines": 0,
    }
    for row in rows:
        category: (
            Literal[
                "modified_lines", "added_lines", "removed_lines", "moved_lines"
            ]
            | None
        )
        tokens = [
            *row.get("left_tokens", []),
            *row.get("right_tokens", []),
        ]
        if tokens == []:
            if row["status"] == "insert":
                category = "added_lines"
            elif row["status"] == "delete":
                category = "removed_lines"
            else:
                category = None
        else:
            changes = {
                token["status"]
                for token in tokens
                if token["status"] != "unchanged"
            }
            has_changed_text = any(
                token["status"] != "unchanged" and not token["is_ws"]
                for token in tokens
            )
            has_context = any(
                token["status"] == "unchanged" and not token["is_ws"]
                for token in tokens
            )
            if not has_changed_text:
                category = None
            elif not has_context and changes == {"insert"}:
                category = "added_lines"
            elif not has_context and changes == {"delete"}:
                category = "removed_lines"
            elif not has_context and changes == {"move"}:
                category = "moved_lines"
            else:
                category = "modified_lines"
        if category is not None:
            summary[category] += 1
            summary["changed_lines"] += 1
    return summary


class GumTreeAmbiguousOffsetsError(DirdiffError):
    """A consumed GumTree range maps to multiple Unicode source positions.

    Raw row construction rejects these coordinates. The engine catches this
    specific failure and returns unified-diff rows with an explanatory warning.
    Other invalid source facts are not represented by this exception.
    """
