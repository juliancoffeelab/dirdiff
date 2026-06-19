"""Difftastic JSON to dirdiff row AST contract.

This module defines the boundary between raw difftastic output and the rendered
row AST used by the difftastic service. It accepts difftastic-shaped JSON plus
the original source text and returns dirdiff-shaped rows.

This module must not own raw difftastic execution or final API payload assembly:

* `dirdiff.services.difftastic.difft` owns invoking `difft` and parsing its JSON.
* the service/textdiff layer owns syntax highlighting, fold hints, and frontend
  payload assembly.

Input contract
--------------
The main entrypoint is `build_difftastic_ast`:

* `left_text` and `right_text` are the complete source documents. They are the
  authority for line text, line counts, and user-visible content.
* `left_path_hint` and `right_path_hint` are file-name hints for difftastic
  parser selection.

Accepted difftastic facts
-------------------------
`DifftasticJson.aligned_lines` contains zero-based line index pairs. `None` means
there is no line on that side.

`DifftasticJson.chunks` contains changed ranges keyed by difftastic side names:
`lhs` for the left/old document and `rhs` for the right/new document. The range
offsets are treated as Python string slice offsets into the corresponding source
line.

The `language` field is opaque except for known difftastic fallback labels that
may be exposed through `DifftasticAst.engine_warning`.

Output contract
---------------
`build_difftastic_ast` returns `DifftasticAst`:

* `rows`: a list of `DifftasticRow` values.
* `engine_warning`: optional metadata for known difftastic fallback modes.

Each `DifftasticRow` is a display row, not a difftastic JSON row. Its fields are:

* `status`: one of `equal`, `replace`, `insert`, or `delete`.
* `left_no` and `right_no`: one-based source line numbers, or `None` for
  one-sided rendered rows.
* `left_text` and `right_text`: the exact text shown on each side for this row.
* `left_tokens` and `right_tokens`: optional inline token lists. If present, the
  concatenated token text for a side must correspond to that side's displayed
  row text.

Each `DifftasticInlineToken` has:

* `text`: the rendered token text.
* `status`: `unchanged`, `replace`, `insert`, or `delete`.
* `is_ws`: whether the token is whitespace.

The row list is the exported AST for difftastic rendering. The service layer may
cast it back to the generic row shape at the boundary where shared textdiff
payload code takes over, but inside this module the row contract is explicit.

Required invariants
-------------------
* row semantic text must come from the supplied source text;
* split-fragment row text may project whitespace to match difftastic display
  layout, but must not invent non-whitespace source content;
* token text should not invent source content;
* one-based row line numbers should always refer back to source lines;
* unchanged semantic tokens should not appear as pure one-sided changes;
* changed semantic tokens on one side should have a corresponding changed token
  on the other side when difftastic supplies a semantic counterpart;
* empty `aligned_lines` returns an empty row list so the service can choose a
  fallback renderer.

Non-goals
---------
This module does not validate the full difftastic JSON schema, does not shell
out to difftastic, does not perform syntax highlighting, does not build fold
hints, and does not assemble the final HTTP/API payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, TypedDict

from dirdiff.services.difftastic.difft import (
    DifftasticJson,
    DifftasticJsonChange,
    DifftasticJsonChunkEntry,
    DifftasticJsonSide,
    run_difftastic_json,
)

type DifftasticRowStatus = Literal["equal", "replace", "insert", "delete"]
type DifftasticTokenStatus = Literal["unchanged", "replace", "insert", "delete"]
type _Side = Literal["left", "right"]

_ATOM_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|\S")
_WORD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_EMPTY_CALL_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\(\)")
_GAP_PAIR_MIN_RATIO = 0.15
_CONTEXT_PUNCTUATION_LITERALS = (
    "={{",
    "={()",
    "().",
    "(),",
    "()}>",
    "()}",
    "()",
    "))",
    "),",
    ")}`",
    ")}",
    "${",
    "}`",
    ");",
    "};",
    "}>",
    "}}",
    "/>",
    "...",
    "</",
    "{(",
    "!==",
    "===",
    "=>",
    '="',
    '":',
    '",',
    '">',
)
_LOOSE_CONTEXT_PUNCTUATION_EXCLUDES = frozenset(
    {"().", "()", "))", "}>", '",', "!=="}
)
_PAIRING_LOW_VALUE_ATOMS = {
    "None",
    "True",
    "False",
    "and",
    "as",
    "else",
    "if",
    "in",
    "is",
    "not",
    "or",
}


class DifftasticInlineToken(TypedDict):
    text: str
    status: DifftasticTokenStatus
    is_ws: bool


class DifftasticRow(TypedDict, total=False):
    """Rendered row shape exported from difftastic logic to the service."""

    status: DifftasticRowStatus
    left_no: int | None
    right_no: int | None
    left_text: str
    right_text: str
    left_tokens: list[DifftasticInlineToken]
    right_tokens: list[DifftasticInlineToken]


@dataclass(frozen=True)
class DifftasticAst:
    rows: list[DifftasticRow]
    engine_warning: dict[str, str] | None


@dataclass(frozen=True)
class _ChangeInterval:
    start: int
    end: int
    status: DifftasticTokenStatus


@dataclass(frozen=True)
class _ChangePair:
    left_line: int | None
    right_line: int | None
    left_changes: tuple[DifftasticJsonChange, ...]
    right_changes: tuple[DifftasticJsonChange, ...]
    left_status: DifftasticTokenStatus | None
    right_status: DifftasticTokenStatus | None


@dataclass(frozen=True)
class _ChangeIndex:
    left_intervals: dict[int, list[_ChangeInterval]]
    right_intervals: dict[int, list[_ChangeInterval]]
    touched_left_lines: set[int]
    touched_right_lines: set[int]
    pairs: tuple[_ChangePair, ...]
    left_has_unpaired_delete: set[int]
    has_change_content: bool


@dataclass(frozen=True)
class _RowSpec:
    left: int | None
    right: int | None


@dataclass
class _PendingLeftLine:
    index: int
    cursor: int = 0


@dataclass
class _PendingRightLine:
    index: int
    cursor: int = 0


@dataclass(frozen=True)
class _FragmentMatch:
    display_text: str
    start: int
    end: int


def _difftastic_engine_warning(
    diff_json: DifftasticJson,
) -> dict[str, str] | None:
    language = diff_json.get("language")
    if isinstance(language, str) and "exceeded DFT_GRAPH_LIMIT" in language:
        return {
            "type": "difftastic_graph_limit",
            "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
        }
    return None


def _source_lines(text: str) -> list[str]:
    return text.splitlines()


def _side_statuses_for_entry(
    lhs_changes: tuple[DifftasticJsonChange, ...],
    rhs_changes: tuple[DifftasticJsonChange, ...],
) -> tuple[DifftasticTokenStatus | None, DifftasticTokenStatus | None]:
    if lhs_changes and rhs_changes:
        if len(lhs_changes) == len(rhs_changes):
            return "replace", "replace"
        return "delete", "insert"
    if lhs_changes:
        return "delete", None
    if rhs_changes:
        return None, "insert"
    return None, None


def _change_tuple(
    side: DifftasticJsonSide | None,
) -> tuple[DifftasticJsonChange, ...]:
    if side is None:
        return ()
    changes = side.get("changes")
    if not isinstance(changes, list):
        return ()
    return tuple(changes)


def _line_number(side: DifftasticJsonSide | None) -> int | None:
    if side is None:
        return None
    line_number = side.get("line_number")
    if isinstance(line_number, int):
        return line_number
    return None


def _entry_side(
    entry: DifftasticJsonChunkEntry,
    side: Literal["lhs", "rhs"],
) -> DifftasticJsonSide | None:
    value = entry.get(side)
    if isinstance(value, dict):
        return value
    return None


def _change_index(diff_json: DifftasticJson) -> _ChangeIndex:
    left_intervals: dict[int, list[_ChangeInterval]] = {}
    right_intervals: dict[int, list[_ChangeInterval]] = {}
    touched_left_lines: set[int] = set()
    touched_right_lines: set[int] = set()
    pairs: list[_ChangePair] = []
    left_has_unpaired_delete: set[int] = set()
    has_change_content = False

    for chunk in diff_json.get("chunks", []):
        for entry in chunk:
            lhs = _entry_side(entry, "lhs")
            rhs = _entry_side(entry, "rhs")
            left_line = _line_number(lhs)
            right_line = _line_number(rhs)
            left_changes = _change_tuple(lhs)
            right_changes = _change_tuple(rhs)
            if _changes_have_content(left_changes):
                has_change_content = True
            if _changes_have_content(right_changes):
                has_change_content = True
            left_status, right_status = _side_statuses_for_entry(
                left_changes,
                right_changes,
            )

            if left_line is not None:
                touched_left_lines.add(left_line)
                if left_status is not None:
                    _append_intervals(
                        left_intervals,
                        line_number=left_line,
                        changes=left_changes,
                        status=left_status,
                    )
                if left_changes and right_status is None:
                    left_has_unpaired_delete.add(left_line)

            if right_line is not None:
                touched_right_lines.add(right_line)
                if right_status is not None:
                    _append_intervals(
                        right_intervals,
                        line_number=right_line,
                        changes=right_changes,
                        status=right_status,
                    )

            pairs.append(
                _ChangePair(
                    left_line=left_line,
                    right_line=right_line,
                    left_changes=left_changes,
                    right_changes=right_changes,
                    left_status=left_status,
                    right_status=right_status,
                )
            )

    return _ChangeIndex(
        left_intervals=left_intervals,
        right_intervals=right_intervals,
        touched_left_lines=touched_left_lines,
        touched_right_lines=touched_right_lines,
        pairs=tuple(pairs),
        left_has_unpaired_delete=left_has_unpaired_delete,
        has_change_content=has_change_content,
    )


def _append_intervals(
    intervals_by_line: dict[int, list[_ChangeInterval]],
    *,
    line_number: int,
    changes: tuple[DifftasticJsonChange, ...],
    status: DifftasticTokenStatus,
) -> None:
    intervals = intervals_by_line.setdefault(line_number, [])
    for change in changes:
        start = change.get("start")
        end = change.get("end")
        if not isinstance(start, int):
            continue
        if not isinstance(end, int):
            continue
        if start >= end:
            continue
        intervals.append(_ChangeInterval(start=start, end=end, status=status))


def _normalized_row_specs(
    diff_json: DifftasticJson,
    *,
    left_count: int,
    right_count: int,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> list[_RowSpec]:
    aligned_lines = diff_json.get("aligned_lines", [])
    if not aligned_lines:
        return []

    specs: list[_RowSpec] = []
    next_left = 0
    next_right = 0

    for pair in aligned_lines:
        left_index, right_index = _pair_indices(pair)
        if left_index is not None and left_index >= left_count:
            left_index = None
        if right_index is not None and right_index >= right_count:
            right_index = None
        if left_index is None and right_index is None:
            continue
        missing_left = _missing_range(next_left, left_index, left_count)
        missing_right = _missing_range(next_right, right_index, right_count)
        specs.extend(
            _align_gap_specs(
                missing_left,
                missing_right,
            )
        )
        specs.append(_RowSpec(left=left_index, right=right_index))
        if left_index is not None:
            next_left = left_index + 1
        if right_index is not None:
            next_right = right_index + 1

    specs.extend(
        _align_gap_specs(
            list(range(next_left, left_count)),
            list(range(next_right, right_count)),
        )
    )
    split_specs = _split_dissimilar_pairs(
        specs,
        left_lines=left_lines,
        right_lines=right_lines,
        change_index=change_index,
    )
    return _repair_one_sided_blocks(
        split_specs,
        left_lines=left_lines,
        right_lines=right_lines,
        change_index=change_index,
    )


def _split_dissimilar_pairs(
    specs: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> list[_RowSpec]:
    split: list[_RowSpec] = []
    for index, spec in enumerate(specs):
        if spec.left is None or spec.right is None:
            split.append(spec)
            continue
        if _has_shifted_neighbor(
            index,
            specs,
            left_lines=left_lines,
            right_lines=right_lines,
        ):
            split.append(spec)
            continue
        if _has_current_replace_pair(
            left_index=spec.left,
            right_index=spec.right,
            change_index=change_index,
        ):
            split.append(spec)
            continue
        if _has_current_one_sided_change_pair(
            left_index=spec.left,
            right_index=spec.right,
            change_index=change_index,
        ):
            split.append(spec)
            continue
        if _has_current_change_pair(
            left_index=spec.left,
            right_index=spec.right,
            change_index=change_index,
        ) and not _has_better_following_left_match(
            index,
            specs,
            left_lines=left_lines,
            right_lines=right_lines,
        ):
            split.append(spec)
            continue
        if _should_split_reanchorable_change_pair(
            index,
            specs,
            left_lines=left_lines,
            right_lines=right_lines,
            change_index=change_index,
        ):
            split.append(_RowSpec(left=spec.left, right=None))
            split.append(_RowSpec(left=None, right=spec.right))
            continue
        if _has_current_pair_shared_context(
            left_index=spec.left,
            right_index=spec.right,
            left_lines=left_lines,
            right_lines=right_lines,
            change_index=change_index,
        ):
            split.append(spec)
            continue
        split.append(spec)
    return split


def _should_split_reanchorable_change_pair(
    index: int,
    specs: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> bool:
    spec = specs[index]
    if spec.left is None:
        return False
    if spec.right is None:
        return False
    has_current_change_pair = _has_current_change_pair(
        left_index=spec.left,
        right_index=spec.right,
        change_index=change_index,
    )
    has_better_following_left_match = _has_better_following_left_match(
        index,
        specs,
        left_lines=left_lines,
        right_lines=right_lines,
    )
    has_no_shared_atoms = (
        _line_similarity(left_lines[spec.left], right_lines[spec.right]) == 0.0
    )
    has_current_pair_shared_context = _has_current_pair_shared_context(
        left_index=spec.left,
        right_index=spec.right,
        left_lines=left_lines,
        right_lines=right_lines,
        change_index=change_index,
    )
    return (
        has_current_change_pair
        and has_better_following_left_match
        and has_no_shared_atoms
        and not has_current_pair_shared_context
    )


def _has_current_pair_shared_context(
    *,
    left_index: int,
    right_index: int,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> bool:
    left_line = left_lines[left_index]
    right_line = right_lines[right_index]
    for pair in change_index.pairs:
        if pair.left_line != left_index:
            continue
        if pair.right_line != right_index:
            continue
        if not pair.left_changes:
            continue
        if not pair.right_changes:
            continue
        left_start = _first_change_start(pair.left_changes)
        right_start = _first_change_start(pair.right_changes)
        if left_start is None:
            continue
        if right_start is None:
            continue
        left_prefix = left_line[:left_start]
        right_prefix = right_line[:right_start]
        if left_prefix != right_prefix:
            continue
        if _WORD_PATTERN.search(left_prefix):
            return True
    return False


def _first_change_start(
    changes: tuple[DifftasticJsonChange, ...],
) -> int | None:
    starts: list[int] = []
    for change in changes:
        start = change.get("start")
        if isinstance(start, int):
            starts.append(start)
    if not starts:
        return None
    return min(starts)


def _has_shifted_neighbor(
    index: int,
    specs: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
) -> bool:
    spec = specs[index]
    if spec.left is None or spec.right is None:
        return False
    if index > 0:
        previous = specs[index - 1]
        if (
            previous.left is not None
            and previous.right is not None
            and right_lines[previous.right] == left_lines[spec.left]
        ):
            return True
    if index + 1 < len(specs):
        following = specs[index + 1]
        if (
            following.left is not None
            and following.right is not None
            and right_lines[spec.right] == left_lines[following.left]
        ):
            return True
    return False


def _pair_indices(pair: list[int | None]) -> tuple[int | None, int | None]:
    left_index: int | None = None
    right_index: int | None = None
    if len(pair) >= 1:
        left_value = pair[0]
        if isinstance(left_value, int) and left_value >= 0:
            left_index = left_value
    if len(pair) >= 2:
        right_value = pair[1]
        if isinstance(right_value, int) and right_value >= 0:
            right_index = right_value
    return left_index, right_index


def _missing_range(
    cursor: int,
    target: int | None,
    count: int,
) -> list[int]:
    if target is None:
        return []
    if target <= cursor:
        return []
    stop = min(target, count)
    return list(range(cursor, stop))


def _align_gap_specs(
    left_indices: list[int],
    right_indices: list[int],
) -> list[_RowSpec]:
    return [
        *[_RowSpec(left=index, right=None) for index in left_indices],
        *[_RowSpec(left=None, right=index) for index in right_indices],
    ]


def _repair_one_sided_blocks(
    specs: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> list[_RowSpec]:
    # Keep this local to one-sided blocks: difftastic already chose the
    # surrounding aligned rows, and these repairs only recover compact context
    # lines omitted from the two-sided alignment.
    repaired: list[_RowSpec] = []
    block: list[_RowSpec] = []

    for spec in specs:
        if spec.left is not None and spec.right is not None:
            repaired.extend(
                _repair_one_sided_block(
                    block,
                    left_lines=left_lines,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            block = []
            repaired.append(spec)
            continue
        block.append(spec)

    repaired.extend(
        _repair_one_sided_block(
            block,
            left_lines=left_lines,
            right_lines=right_lines,
            change_index=change_index,
        )
    )
    return repaired


def _repair_one_sided_block(
    block: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> list[_RowSpec]:
    left_items = [
        (position, spec.left)
        for position, spec in enumerate(block)
        if spec.left is not None and spec.right is None
    ]
    right_items = [
        (position, spec.right)
        for position, spec in enumerate(block)
        if spec.left is None and spec.right is not None
    ]
    if not left_items:
        return block
    if not right_items:
        return block

    structural_pairs = _adjacent_structural_context_pairs(
        block,
        left_lines=left_lines,
        right_lines=right_lines,
    )
    adjacent_pairs = _adjacent_following_left_pairs(
        block,
        left_lines=left_lines,
        right_lines=right_lines,
        existing_pairs=structural_pairs,
        change_index=change_index,
    )
    pairs = [*structural_pairs, *adjacent_pairs]
    if not pairs:
        return block

    left_position = {index: position for position, index in left_items}
    right_position = {index: position for position, index in right_items}
    matched_left = {left for left, _right in pairs}
    matched_right = {right for _left, right in pairs}
    events: list[tuple[int, int, _RowSpec]] = []

    for order, spec in enumerate(block):
        if spec.left is not None:
            if spec.left in matched_left:
                continue
            events.append((order, order, spec))
            continue
        if spec.right is not None and spec.right not in matched_right:
            events.append((order, order, spec))

    for pair_order, (left_index, right_index) in enumerate(pairs):
        left_pos = left_position[left_index]
        right_pos = right_position[right_index]
        events.append(
            (
                min(left_pos, right_pos),
                len(block) + pair_order,
                _RowSpec(left=left_index, right=right_index),
            )
        )

    events.sort(key=lambda event: (event[0], event[1]))
    return [spec for _position, _order, spec in events]


def _adjacent_structural_context_pairs(
    block: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()

    for position, spec in enumerate(block):
        if spec.left is not None:
            if spec.left in used_left:
                continue
            following = _immediate_right_after(block, position)
            if following is None:
                continue
            if following in used_right:
                continue
            if _is_structural_context_match(
                left_lines[spec.left], right_lines[following]
            ):
                pairs.append((spec.left, following))
                used_left.add(spec.left)
                used_right.add(following)
            continue

        if spec.right is None:
            continue
        if spec.right in used_right:
            continue
        following_left = _immediate_left_after(block, position)
        if following_left is None:
            continue
        if following_left in used_left:
            continue
        if _is_structural_context_match(
            left_lines[following_left], right_lines[spec.right]
        ):
            pairs.append((following_left, spec.right))
            used_left.add(following_left)
            used_right.add(spec.right)

    return pairs


def _adjacent_following_left_pairs(
    block: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
    existing_pairs: list[tuple[int, int]],
    change_index: _ChangeIndex,
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    used_left = {left for left, _right in existing_pairs}
    used_right = {right for _left, right in existing_pairs}

    for position, spec in enumerate(block):
        if spec.left is not None:
            continue
        if spec.right is None:
            continue
        if spec.right in used_right:
            continue

        previous_left = _immediate_left_before(block, position)
        following_left = _nearest_left_after(block, position)

        previous_score = 0.0
        if previous_left is not None:
            previous_score = _line_similarity(
                left_lines[previous_left],
                right_lines[spec.right],
            )

        if following_left is None:
            if previous_left is None:
                continue
            if previous_left in used_left:
                continue
            if not _has_touched_candidate_line(
                left_index=previous_left,
                right_index=spec.right,
                change_index=change_index,
            ):
                continue
            if previous_score < _GAP_PAIR_MIN_RATIO:
                continue
            pairs.append((previous_left, spec.right))
            used_left.add(previous_left)
            used_right.add(spec.right)
            continue

        if following_left in used_left:
            continue
        if not _has_touched_candidate_line(
            left_index=following_left,
            right_index=spec.right,
            change_index=change_index,
        ):
            continue

        following_score = _line_similarity(
            left_lines[following_left],
            right_lines[spec.right],
        )
        if following_score < _GAP_PAIR_MIN_RATIO:
            continue

        if following_score <= previous_score:
            continue

        pairs.append((following_left, spec.right))
        used_left.add(following_left)
        used_right.add(spec.right)

    return pairs


def _has_touched_candidate_line(
    *,
    left_index: int,
    right_index: int,
    change_index: _ChangeIndex,
) -> bool:
    return (
        left_index in change_index.touched_left_lines
        or right_index in change_index.touched_right_lines
    )


def _is_structural_context_match(left_line: str, right_line: str) -> bool:
    left_text = left_line.strip()
    right_text = right_line.strip()
    if left_text == "":
        return False
    if left_text != right_text:
        return False
    return _WORD_PATTERN.search(left_text) is None


def _immediate_left_before(block: list[_RowSpec], position: int) -> int | None:
    if position == 0:
        return None
    spec = block[position - 1]
    if spec.left is not None:
        return spec.left
    return None


def _immediate_right_after(block: list[_RowSpec], position: int) -> int | None:
    next_position = position + 1
    if next_position >= len(block):
        return None
    spec = block[next_position]
    if spec.right is not None:
        return spec.right
    return None


def _immediate_left_after(block: list[_RowSpec], position: int) -> int | None:
    next_position = position + 1
    if next_position >= len(block):
        return None
    spec = block[next_position]
    if spec.left is not None:
        return spec.left
    return None


def _nearest_left_after(block: list[_RowSpec], position: int) -> int | None:
    cursor = position + 1
    while cursor < len(block):
        spec = block[cursor]
        if spec.left is not None:
            return spec.left
        if spec.right is not None:
            return None
        cursor += 1
    return None


def _line_similarity(left_line: str, right_line: str) -> float:
    left_atoms = _semantic_atoms(left_line)
    right_atoms = _semantic_atoms(right_line)
    if not left_atoms:
        return 1.0 if left_line == right_line else 0.0
    if not right_atoms:
        return 1.0 if left_line == right_line else 0.0
    if set(left_atoms).isdisjoint(right_atoms):
        return 0.0
    return SequenceMatcher(a=left_atoms, b=right_atoms, autojunk=False).ratio()


def _semantic_atoms(text: str) -> list[str]:
    return [
        atom
        for atom in _WORD_PATTERN.findall(text)
        if atom not in _PAIRING_LOW_VALUE_ATOMS
    ]


def _tokens_for_slice(
    line: str,
    *,
    start: int,
    end: int,
    intervals: list[_ChangeInterval],
) -> list[DifftasticInlineToken]:
    if start >= end:
        return []

    tokens: list[DifftasticInlineToken] = []
    cursor = start
    clipped = [
        _ChangeInterval(
            start=max(interval.start, start),
            end=min(interval.end, end),
            status=interval.status,
        )
        for interval in sorted(
            intervals, key=lambda item: (item.start, item.end)
        )
        if interval.end > start and interval.start < end
    ]
    for interval in clipped:
        if interval.start > cursor:
            _append_token(
                tokens,
                text=line[cursor : interval.start],
                status="unchanged",
            )
        _append_changed_token(
            tokens,
            text=line[interval.start : interval.end],
            status=interval.status,
        )
        cursor = interval.end

    if cursor < end:
        _append_token(tokens, text=line[cursor:end], status="unchanged")
    return tokens


def _has_interval_overlap(
    intervals: list[_ChangeInterval],
    *,
    start: int,
    end: int,
) -> bool:
    return any(
        interval.end > start and interval.start < end for interval in intervals
    )


def _append_token(
    tokens: list[DifftasticInlineToken],
    *,
    text: str,
    status: DifftasticTokenStatus,
) -> None:
    if text == "":
        return
    tokens.append({"text": text, "status": status, "is_ws": text.isspace()})


def _append_changed_token(
    tokens: list[DifftasticInlineToken],
    *,
    text: str,
    status: DifftasticTokenStatus,
) -> None:
    if status == "unchanged":
        _append_token(tokens, text=text, status=status)
        return
    if not _should_split_changed_text(text):
        _append_token(tokens, text=text, status=status)
        return

    cursor = 0
    for match in _ATOM_PATTERN.finditer(text):
        if match.start() > cursor:
            _append_token(
                tokens,
                text=text[cursor : match.start()],
                status=status,
            )
        _append_token(tokens, text=match.group(0), status=status)
        cursor = match.end()
    if cursor < len(text):
        _append_token(tokens, text=text[cursor:], status=status)


def _should_split_changed_text(text: str) -> bool:
    if text == "":
        return False
    if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        return False
    if _EMPTY_CALL_PATTERN.fullmatch(text) is not None:
        return False
    return _WORD_PATTERN.fullmatch(text) is None


def _row_status(
    *,
    left_text: str,
    right_text: str,
    left_no: int | None,
    right_no: int | None,
    left_tokens: list[DifftasticInlineToken] | None,
    right_tokens: list[DifftasticInlineToken] | None,
    allow_ws_only_equal: bool,
) -> DifftasticRowStatus:
    if left_no is not None and right_no is not None:
        return _paired_row_status(
            left_text=left_text,
            right_text=right_text,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            allow_ws_only_equal=allow_ws_only_equal,
        )

    if left_no is not None:
        return _left_only_row_status(left_tokens)

    if right_no is not None:
        return _right_only_row_status(right_tokens)

    return "equal"


def _paired_row_status(
    *,
    left_text: str,
    right_text: str,
    left_tokens: list[DifftasticInlineToken] | None,
    right_tokens: list[DifftasticInlineToken] | None,
    allow_ws_only_equal: bool,
) -> DifftasticRowStatus:
    if (
        left_text == right_text
        and not _has_changed_tokens(left_tokens)
        and not _has_changed_tokens(right_tokens)
    ):
        return "equal"
    if (
        allow_ws_only_equal
        and left_text.strip() == right_text.strip()
        and _has_only_whitespace_changes(left_tokens)
        and _has_only_whitespace_changes(right_tokens)
    ):
        return "equal"
    return "replace"


def _left_only_row_status(
    left_tokens: list[DifftasticInlineToken] | None,
) -> DifftasticRowStatus:
    if not _has_changed_tokens(left_tokens):
        return "delete"
    if _has_unchanged_semantic_tokens(left_tokens):
        return "replace"
    if _has_unchanged_block_opener_context(left_tokens):
        return "replace"
    return "delete"


def _right_only_row_status(
    right_tokens: list[DifftasticInlineToken] | None,
) -> DifftasticRowStatus:
    if not _has_changed_tokens(right_tokens):
        return "equal"
    if not _has_unchanged_meaningful_tokens(right_tokens):
        return "insert"
    return "replace"


def _has_changed_tokens(tokens: list[DifftasticInlineToken] | None) -> bool:
    if tokens is None:
        return False
    return any(token["status"] != "unchanged" for token in tokens)


def _has_only_whitespace_changes(
    tokens: list[DifftasticInlineToken] | None,
) -> bool:
    if tokens is None:
        return False
    for token in tokens:
        if token["status"] == "unchanged":
            continue
        if not token["is_ws"]:
            return False
    return True


def _has_unchanged_meaningful_tokens(
    tokens: list[DifftasticInlineToken] | None,
) -> bool:
    if tokens is None:
        return False
    for token in tokens:
        if token["status"] != "unchanged":
            continue
        if token["is_ws"]:
            continue
        if _ATOM_PATTERN.search(token["text"]):
            return True
    return False


def _has_unchanged_semantic_tokens(
    tokens: list[DifftasticInlineToken] | None,
) -> bool:
    if tokens is None:
        return False
    for token in tokens:
        if token["status"] != "unchanged":
            continue
        if token["is_ws"]:
            continue
        if _WORD_PATTERN.search(token["text"]):
            return True
    return False


def _has_unchanged_block_opener_context(
    tokens: list[DifftasticInlineToken] | None,
) -> bool:
    if tokens is None:
        return False
    for token in tokens:
        if token["status"] != "unchanged":
            continue
        if "{" in token["text"]:
            return True
        if ":" in token["text"]:
            return True
    return False


def _right_line_template_for_left(
    *,
    left_index: int,
    right_index: int,
    left_lines: list[str],
    right_line: str,
    change_index: _ChangeIndex,
) -> str:
    replacement_by_start: dict[int, str] = {}
    skip_intervals: list[_ChangeInterval] = []

    for pair in change_index.pairs:
        if pair.right_line != right_index:
            continue
        if (
            pair.left_line == left_index
            and pair.left_status == "replace"
            and pair.right_status == "replace"
            and len(pair.left_changes) == 1
            and len(pair.right_changes) == 1
        ):
            rhs = pair.right_changes[0]
            lhs = pair.left_changes[0]
            replacement_by_start[int(rhs["start"])] = _change_content(
                lhs,
                line=left_lines[left_index],
            )
        for change in pair.right_changes:
            start = change.get("start")
            end = change.get("end")
            if not isinstance(start, int):
                continue
            if not isinstance(end, int):
                continue
            if start >= end:
                continue
            skip_intervals.append(
                _ChangeInterval(start=start, end=end, status="insert")
            )

    if not skip_intervals:
        return right_line

    pieces: list[str] = []
    cursor = 0
    for interval in sorted(
        skip_intervals, key=lambda item: (item.start, item.end)
    ):
        if interval.start > cursor:
            pieces.append(right_line[cursor : interval.start])
        replacement = replacement_by_start.get(interval.start)
        if replacement is not None:
            pieces.append(replacement)
        cursor = interval.end
    pieces.append(right_line[cursor:])
    return "".join(pieces)


def _match_left_fragment(
    *,
    pending: _PendingLeftLine,
    right_index: int,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> _FragmentMatch | None:
    left_line = left_lines[pending.index]
    right_line = right_lines[right_index]
    template = _right_line_template_for_left(
        left_index=pending.index,
        right_index=right_index,
        left_lines=left_lines,
        right_line=right_line,
        change_index=change_index,
    )
    template_atoms = _atoms_with_offsets(template)
    if not template_atoms:
        return None

    left_atoms = _atoms_with_offsets(left_line[pending.cursor :])
    if len(template_atoms) > len(left_atoms):
        return None

    for offset, (template_atom, _start, _end) in enumerate(template_atoms):
        left_atom = left_atoms[offset][0]
        if template_atom != left_atom:
            return None

    source_start = pending.cursor + left_atoms[0][1]
    source_end = pending.cursor + left_atoms[len(template_atoms) - 1][2]
    source_end = _extend_matching_trailing_whitespace(
        source=left_line,
        source_end=source_end,
        template=template,
    )
    source_text = left_line[source_start:source_end]
    leading = _leading_whitespace(right_line)
    display_text = _projected_fragment_text(
        source_text=source_text,
        leading=leading,
        source_prefix=left_line[pending.cursor : source_start],
        allow_projection=pending.cursor > 0,
    )
    return _FragmentMatch(
        display_text=display_text,
        start=source_start,
        end=source_end,
    )


def _match_left_fragment_by_subsequence(
    *,
    pending: _PendingLeftLine,
    right_index: int,
    left_lines: list[str],
    right_lines: list[str],
) -> _FragmentMatch | None:
    left_line = left_lines[pending.index]
    right_line = right_lines[right_index]
    left_atoms = _atoms_with_offsets(left_line[pending.cursor :])
    right_atoms = _atoms_with_offsets(right_line)
    if not left_atoms:
        return None
    if not right_atoms:
        return None
    skipped_leading_opener = False
    if left_atoms[0][0] != right_atoms[0][0]:
        if (
            right_atoms[0][0] in {"(", "[", "{"}
            and len(right_atoms) > 1
            and left_atoms[0][0] == right_atoms[1][0]
        ):
            right_atoms = right_atoms[1:]
            skipped_leading_opener = True
        else:
            return None

    consumed = _matching_prefix_atom_count(left_atoms, right_atoms)
    if consumed == 0:
        return None

    source_start = pending.cursor + left_atoms[0][1]
    source_end = pending.cursor + left_atoms[consumed - 1][2]
    if skipped_leading_opener and left_line[source_end - 1 : source_end] == ")":
        source_end -= 1
    source_text = left_line[source_start:source_end]
    leading = _leading_whitespace(right_line)
    display_text = _projected_fragment_text(
        source_text=source_text,
        leading=leading,
        source_prefix=left_line[pending.cursor : source_start],
        allow_projection=pending.cursor > 0,
    )
    return _FragmentMatch(
        display_text=display_text, start=source_start, end=source_end
    )


def _matching_prefix_atom_count(
    source_atoms: list[tuple[str, int, int]],
    target_atoms: list[tuple[str, int, int]],
) -> int:
    target_position = 0
    consumed = 0
    for source_atom, _source_start, _source_end in source_atoms:
        found = False
        while target_position < len(target_atoms):
            target_atom = target_atoms[target_position][0]
            target_position += 1
            if source_atom == target_atom:
                found = True
                break
        if not found:
            break
        consumed += 1
    return consumed


def _match_right_fragment(
    *,
    pending: _PendingRightLine,
    left_index: int,
    left_lines: list[str],
    right_lines: list[str],
) -> _FragmentMatch | None:
    right_line = right_lines[pending.index]
    left_line = left_lines[left_index]
    right_atoms = _atoms_with_offsets(right_line[pending.cursor :])
    left_atoms = _atoms_with_offsets(left_line)
    if not right_atoms:
        return None
    if not left_atoms:
        return None
    if right_atoms[0][0] != left_atoms[0][0]:
        return None

    consumed = _matching_prefix_atom_count(right_atoms, left_atoms)
    if consumed == 0:
        return None

    source_start = pending.cursor + right_atoms[0][1]
    source_end = pending.cursor + right_atoms[consumed - 1][2]
    source_text = right_line[source_start:source_end]
    leading = _leading_whitespace(left_line)
    display_text = _projected_fragment_text(
        source_text=source_text,
        leading=leading,
        source_prefix=right_line[pending.cursor : source_start],
        allow_projection=pending.cursor > 0,
    )
    return _FragmentMatch(
        display_text=display_text, start=source_start, end=source_end
    )


def _change_content(change: DifftasticJsonChange, *, line: str) -> str:
    content = change.get("content")
    if isinstance(content, str):
        return content
    start = change.get("start")
    end = change.get("end")
    if not isinstance(start, int):
        return ""
    if not isinstance(end, int):
        return ""
    return line[start:end]


def _extend_matching_trailing_whitespace(
    *,
    source: str,
    source_end: int,
    template: str,
) -> int:
    template_atoms = _atoms_with_offsets(template)
    if not template_atoms:
        return source_end
    trailing = template[template_atoms[-1][2] :]
    if trailing == "":
        return source_end
    if not trailing.isspace():
        return source_end
    cursor = source_end
    for character in trailing:
        if cursor >= len(source):
            return cursor
        if source[cursor] != character:
            return cursor
        cursor += 1
    return cursor


def _projected_fragment_text(
    *,
    source_text: str,
    leading: str,
    source_prefix: str,
    allow_projection: bool,
) -> str:
    # Difftastic renders fragments from one physical source line at the
    # indentation of the structural counterpart. Only whitespace is projected;
    # all semantic text stays in `source_text`.
    if source_text.startswith((" ", "\t")):
        return source_text
    if len(source_prefix) >= 2 and source_prefix.isspace():
        return f"{source_prefix}{source_text}"
    if source_prefix == leading:
        return f"{source_prefix}{source_text}"
    if allow_projection:
        return f"{leading}{source_text}"
    return source_text


def _leading_whitespace(text: str) -> str:
    stripped = text.lstrip(" \t")
    return text[: len(text) - len(stripped)]


def _atoms_with_offsets(text: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(0), match.start(), match.end())
        for match in _ATOM_PATTERN.finditer(text)
    ]


def _line_can_split(left_index: int, change_index: _ChangeIndex) -> bool:
    if left_index in change_index.left_has_unpaired_delete:
        return False
    return left_index not in change_index.left_intervals


def _pending_left_match(
    *,
    pending: _PendingLeftLine,
    right_index: int,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> _FragmentMatch | None:
    match: _FragmentMatch | None = None
    if _line_can_split(pending.index, change_index):
        match = _match_left_fragment(
            pending=pending,
            right_index=right_index,
            left_lines=left_lines,
            right_lines=right_lines,
            change_index=change_index,
        )
    if (
        match is None
        and pending.cursor > 0
        and pending.index not in change_index.left_has_unpaired_delete
    ):
        match = _match_left_fragment_by_subsequence(
            pending=pending,
            right_index=right_index,
            left_lines=left_lines,
            right_lines=right_lines,
        )
    return match


def _has_current_replace_pair(
    *,
    left_index: int,
    right_index: int,
    change_index: _ChangeIndex,
) -> bool:
    for pair in change_index.pairs:
        if pair.left_line != left_index:
            continue
        if pair.right_line != right_index:
            continue
        if pair.left_status == "replace" and pair.right_status == "replace":
            return True
    return False


def _has_current_change_pair(
    *,
    left_index: int,
    right_index: int,
    change_index: _ChangeIndex,
) -> bool:
    for pair in change_index.pairs:
        if pair.left_line != left_index:
            continue
        if pair.right_line != right_index:
            continue
        if pair.left_changes or pair.right_changes:
            return True
    return False


def _has_empty_side_change_pair(
    *,
    side: _Side,
    left_index: int,
    right_index: int,
    change_index: _ChangeIndex,
) -> bool:
    for pair in change_index.pairs:
        if pair.left_line != left_index:
            continue
        if pair.right_line != right_index:
            continue
        if side == "left":
            return not pair.left_changes and bool(pair.right_changes)
        return bool(pair.left_changes) and not pair.right_changes
    return False


def _has_better_following_left_match(
    index: int,
    specs: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
) -> bool:
    spec = specs[index]
    if spec.left is None or spec.right is None:
        return False
    current_score = _line_similarity(
        left_lines[spec.left], right_lines[spec.right]
    )
    following_index = index + 1
    if following_index >= len(specs):
        return False
    following = specs[following_index]
    if following.left is None:
        return False
    following_score = _line_similarity(
        left_lines[following.left],
        right_lines[spec.right],
    )
    return following_score > current_score


def _has_current_one_sided_change_pair(
    *,
    left_index: int,
    right_index: int,
    change_index: _ChangeIndex,
) -> bool:
    for pair in change_index.pairs:
        if pair.left_line != left_index:
            continue
        if pair.right_line != right_index:
            continue
        if (pair.left_status is None) != (pair.right_status is None):
            return True
    return False


def _has_current_replace_pair_content(
    *,
    left_index: int,
    right_index: int,
    change_index: _ChangeIndex,
) -> bool:
    for pair in change_index.pairs:
        if pair.left_line != left_index:
            continue
        if pair.right_line != right_index:
            continue
        if pair.left_status != "replace":
            continue
        if pair.right_status != "replace":
            continue
        if _changes_have_content(pair.left_changes):
            return True
        if _changes_have_content(pair.right_changes):
            return True
    return False


def _changes_have_content(
    changes: tuple[DifftasticJsonChange, ...],
) -> bool:
    return any(isinstance(change.get("content"), str) for change in changes)


def _build_row(
    *,
    left_no: int | None,
    right_no: int | None,
    left_text: str,
    right_text: str,
    left_tokens: list[DifftasticInlineToken] | None,
    right_tokens: list[DifftasticInlineToken] | None,
    allow_ws_only_equal: bool = True,
) -> DifftasticRow:
    left_tokens, right_tokens = _synthesized_context_tokens(
        left_text=left_text,
        right_text=right_text,
        left_tokens=left_tokens,
        right_tokens=right_tokens,
    )
    left_tokens, right_tokens = _mark_trailing_comma_delta(
        left_text=left_text,
        right_text=right_text,
        left_tokens=left_tokens,
        right_tokens=right_tokens,
    )
    row: DifftasticRow = {
        "status": _row_status(
            left_text=left_text,
            right_text=right_text,
            left_no=left_no,
            right_no=right_no,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            allow_ws_only_equal=allow_ws_only_equal,
        ),
        "left_no": left_no,
        "right_no": right_no,
        "left_text": left_text,
        "right_text": right_text,
    }
    if left_tokens is not None:
        row["left_tokens"] = left_tokens
    if right_tokens is not None:
        row["right_tokens"] = right_tokens
    return row


def _synthesized_context_tokens(
    *,
    left_text: str,
    right_text: str,
    left_tokens: list[DifftasticInlineToken] | None,
    right_tokens: list[DifftasticInlineToken] | None,
) -> tuple[
    list[DifftasticInlineToken] | None,
    list[DifftasticInlineToken] | None,
]:
    if left_tokens is not None or right_tokens is not None:
        return left_tokens, right_tokens
    if left_text == "" or right_text == "":
        return left_tokens, right_tokens

    left_body = left_text.lstrip(" \t")
    right_body = right_text.lstrip(" \t")
    left_leading = left_text[: len(left_text) - len(left_body)]
    right_leading = right_text[: len(right_text) - len(right_body)]
    if left_body == right_body:
        if left_leading == right_leading:
            return left_tokens, right_tokens
        return _context_tokens_with_indent_delta(left_text, right_text)
    if (
        left_body.startswith(right_body)
        and right_body != ""
        and left_leading != right_leading
    ):
        return _context_tokens_with_indent_delta(
            left_text,
            right_text,
            left_body_override=left_body,
            right_body_override=right_body,
            compact_punctuation=False,
        )
    return left_tokens, right_tokens


def _context_tokens_with_indent_delta(
    left_text: str,
    right_text: str,
    *,
    left_body_override: str | None = None,
    right_body_override: str | None = None,
    compact_punctuation: bool = True,
) -> tuple[list[DifftasticInlineToken], list[DifftasticInlineToken]]:
    left_leading = _leading_whitespace(left_text)
    right_leading = _leading_whitespace(right_text)
    left_body = left_body_override or left_text[len(left_leading) :]
    right_body = right_body_override or right_text[len(right_leading) :]
    left_tokens: list[DifftasticInlineToken] = []
    right_tokens: list[DifftasticInlineToken] = []

    if len(left_leading) <= len(right_leading):
        _append_token(left_tokens, text=left_leading, status="unchanged")
        _append_token(right_tokens, text=left_leading, status="unchanged")
        _append_token(
            right_tokens,
            text=right_leading[len(left_leading) :],
            status="insert",
        )
    else:
        _append_token(right_tokens, text=right_leading, status="unchanged")
        _append_token(left_tokens, text=right_leading, status="unchanged")
        _append_token(
            left_tokens,
            text=left_leading[len(right_leading) :],
            status="delete",
        )

    _append_unchanged_context_tokens(
        left_tokens,
        left_body,
        compact_punctuation=compact_punctuation,
    )
    _append_unchanged_context_tokens(
        right_tokens,
        right_body,
        compact_punctuation=compact_punctuation,
    )
    return left_tokens, right_tokens


def _append_unchanged_context_tokens(
    tokens: list[DifftasticInlineToken],
    text: str,
    *,
    compact_punctuation: bool = True,
) -> None:
    cursor = 0
    while cursor < len(text):
        previous_text = tokens[-1]["text"] if tokens else ""
        literals = list(_CONTEXT_PUNCTUATION_LITERALS)
        if not compact_punctuation:
            literals = [
                literal
                for literal in literals
                if literal not in _LOOSE_CONTEXT_PUNCTUATION_EXCLUDES
            ]
        if not _should_separate_equals_brace_after(
            previous_text,
            compact_punctuation=compact_punctuation,
        ):
            literals.append("={")
        for literal in literals:
            if text.startswith(literal, cursor):
                _append_token(tokens, text=literal, status="unchanged")
                cursor += len(literal)
                break
        else:
            character = text[cursor]
            if character.isspace():
                end = cursor + 1
                while end < len(text) and text[end].isspace():
                    end += 1
                _append_token(tokens, text=text[cursor:end], status="unchanged")
                cursor = end
                continue
            match = _WORD_PATTERN.match(text, cursor)
            if match is not None:
                _append_token(tokens, text=match.group(0), status="unchanged")
                cursor = match.end()
                continue
            _append_token(tokens, text=character, status="unchanged")
            cursor += 1


def _should_separate_equals_brace_after(
    previous_text: str,
    *,
    compact_punctuation: bool,
) -> bool:
    return (
        not compact_punctuation
        and _WORD_PATTERN.fullmatch(previous_text) is not None
    )


def _mark_trailing_comma_delta(
    *,
    left_text: str,
    right_text: str,
    left_tokens: list[DifftasticInlineToken] | None,
    right_tokens: list[DifftasticInlineToken] | None,
) -> tuple[
    list[DifftasticInlineToken] | None,
    list[DifftasticInlineToken] | None,
]:
    if (
        right_tokens is not None
        and left_text != ""
        and right_text.endswith(",")
        and not left_text.endswith(",")
    ):
        right_tokens = _mark_last_unchanged_suffix(
            right_tokens,
            suffix=",",
            status="insert",
        )
    elif (
        right_tokens is None
        and left_text != ""
        and right_text.endswith(",")
        and not left_text.endswith(",")
    ):
        right_tokens = _suffix_delta_tokens(
            right_text,
            suffix=",",
            status="insert",
        )
    if (
        left_tokens is not None
        and right_text != ""
        and left_text.endswith(",")
        and not right_text.endswith(",")
    ):
        left_tokens = _mark_last_unchanged_suffix(
            left_tokens,
            suffix=",",
            status="delete",
        )
    elif (
        left_tokens is None
        and right_text != ""
        and left_text.endswith(",")
        and not right_text.endswith(",")
    ):
        left_tokens = _suffix_delta_tokens(
            left_text,
            suffix=",",
            status="delete",
        )
    return left_tokens, right_tokens


def _suffix_delta_tokens(
    text: str,
    *,
    suffix: str,
    status: DifftasticTokenStatus,
) -> list[DifftasticInlineToken]:
    prefix = text[: -len(suffix)]
    tokens: list[DifftasticInlineToken] = []
    _append_token(tokens, text=prefix, status="unchanged")
    _append_token(tokens, text=suffix, status=status)
    return tokens


def _mark_last_unchanged_suffix(
    tokens: list[DifftasticInlineToken],
    *,
    suffix: str,
    status: DifftasticTokenStatus,
) -> list[DifftasticInlineToken]:
    if not tokens:
        return tokens
    last = tokens[-1]
    if last["status"] != "unchanged":
        return tokens
    if not last["text"].endswith(suffix):
        return tokens
    prefix = last["text"][: -len(suffix)]
    rewritten = tokens[:-1]
    _append_copied_token(rewritten, last, prefix)
    _append_token(rewritten, text=suffix, status=status)
    return rewritten


def _rows_from_specs(
    specs: list[_RowSpec],
    *,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> list[DifftasticRow]:
    rows: list[DifftasticRow] = []
    pending_left: list[_PendingLeftLine] = []
    pending_right: _PendingRightLine | None = None

    for spec_index, spec in enumerate(specs):
        if (
            pending_right is not None
            and spec.right is not None
            and spec.right != pending_right.index
        ):
            rows.append(
                _right_residual_row(
                    pending_right,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            pending_right = None

        if spec.left is not None:
            while pending_left and pending_left[0].index != spec.left:
                pending = pending_left.pop(0)
                if pending.cursor > 0:
                    rows.append(
                        _left_residual_row(
                            pending,
                            left_lines=left_lines,
                            change_index=change_index,
                        )
                    )
                else:
                    rows.append(
                        _full_left_row(
                            pending.index,
                            left_lines=left_lines,
                            change_index=change_index,
                        )
                    )
            pending_left.append(_PendingLeftLine(index=spec.left))

        if spec.right is None:
            if not pending_left:
                continue
            pending = pending_left.pop(0)
            if pending_right is not None:
                match = _match_right_fragment(
                    pending=pending_right,
                    left_index=pending.index,
                    left_lines=left_lines,
                    right_lines=right_lines,
                )
                if match is not None:
                    rows.append(
                        _right_fragment_pair_row(
                            pending.index,
                            pending_right.index,
                            match=match,
                            left_lines=left_lines,
                            right_lines=right_lines,
                            change_index=change_index,
                        )
                    )
                    pending_right.cursor = match.end
                    if pending_right.cursor >= len(
                        right_lines[pending_right.index]
                    ):
                        pending_right = None
                    continue
            rows.append(
                _full_left_row(
                    pending.index,
                    left_lines=left_lines,
                    change_index=change_index,
                )
            )
            continue

        current_pending: _PendingLeftLine | None = (
            pending_left[0] if pending_left else None
        )
        if current_pending is None:
            rows.append(
                _right_only_row(
                    spec.right,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            continue

        pending = current_pending
        if _line_can_split(pending.index, change_index):
            match = _match_left_fragment(
                pending=pending,
                right_index=spec.right,
                left_lines=left_lines,
                right_lines=right_lines,
                change_index=change_index,
            )
        else:
            match = None

        if (
            match is None
            and pending.cursor > 0
            and spec.left is None
            and pending.index not in change_index.left_has_unpaired_delete
        ):
            match = _match_left_fragment_by_subsequence(
                pending=pending,
                right_index=spec.right,
                left_lines=left_lines,
                right_lines=right_lines,
            )

        if (
            match is not None
            and match.end < len(left_lines[pending.index])
            and spec.left is not None
            and _next_spec_has_left(specs, spec_index)
        ):
            match = None

        if match is not None and match.end < len(left_lines[pending.index]):
            rows.append(
                _fragment_pair_row(
                    pending.index,
                    spec.right,
                    match=match,
                    left_lines=left_lines,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            pending.cursor = match.end
            continue

        if match is not None and pending.cursor > 0:
            rows.append(
                _fragment_pair_row(
                    pending.index,
                    spec.right,
                    match=match,
                    left_lines=left_lines,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            pending_left.pop(0)
            continue

        residual_match: _FragmentMatch | None = None
        if _has_current_replace_pair(
            left_index=pending.index,
            right_index=spec.right,
            change_index=change_index,
        ):
            residual_match = _match_left_fragment(
                pending=pending,
                right_index=spec.right,
                left_lines=left_lines,
                right_lines=right_lines,
                change_index=change_index,
            )
        if residual_match is not None and residual_match.end < len(
            left_lines[pending.index]
        ):
            if _has_current_replace_pair_content(
                left_index=pending.index,
                right_index=spec.right,
                change_index=change_index,
            ):
                rows.append(
                    _fragment_pair_row(
                        pending.index,
                        spec.right,
                        match=residual_match,
                        left_lines=left_lines,
                        right_lines=right_lines,
                        change_index=change_index,
                    )
                )
            else:
                rows.append(
                    _full_pair_row(
                        pending.index,
                        spec.right,
                        left_lines=left_lines,
                        right_lines=right_lines,
                        change_index=change_index,
                    )
                )
            pending.cursor = residual_match.end
            continue

        if pending.cursor > 0 and spec.left is None:
            rows.append(
                _right_only_row(
                    spec.right,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            continue

        right_match = _match_right_fragment(
            pending=_PendingRightLine(index=spec.right),
            left_index=pending.index,
            left_lines=left_lines,
            right_lines=right_lines,
        )
        can_split_right = (
            pending.index not in change_index.touched_left_lines
            and spec.right not in change_index.touched_right_lines
        )
        if (
            can_split_right
            and right_match is not None
            and right_match.end < len(right_lines[spec.right])
        ):
            rows.append(
                _right_fragment_pair_row(
                    pending.index,
                    spec.right,
                    match=right_match,
                    left_lines=left_lines,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            pending_left.pop(0)
            pending_right = _PendingRightLine(
                index=spec.right,
                cursor=right_match.end,
            )
            continue

        right_padding_match = _match_right_leading_padding(
            left_index=pending.index,
            right_index=spec.right,
            left_lines=left_lines,
            right_lines=right_lines,
            change_index=change_index,
        )
        if right_padding_match is not None:
            rows.append(
                _right_fragment_pair_row(
                    pending.index,
                    spec.right,
                    match=right_padding_match,
                    left_lines=left_lines,
                    right_lines=right_lines,
                    change_index=change_index,
                )
            )
            pending_left.pop(0)
            pending_right = _PendingRightLine(
                index=spec.right,
                cursor=right_padding_match.end,
            )
            continue

        rows.append(
            _full_pair_row(
                pending.index,
                spec.right,
                left_lines=left_lines,
                right_lines=right_lines,
                change_index=change_index,
            )
        )
        pending_left.pop(0)

    for pending in pending_left:
        if pending.cursor > 0:
            rows.append(
                _left_residual_row(
                    pending,
                    left_lines=left_lines,
                    change_index=change_index,
                )
            )
        else:
            rows.append(
                _full_left_row(
                    pending.index,
                    left_lines=left_lines,
                    change_index=change_index,
                )
            )
    if pending_right is not None:
        rows.append(
            _right_residual_row(
                pending_right,
                right_lines=right_lines,
                change_index=change_index,
            )
        )
    return _repair_structural_context_rows(
        _repair_inserted_structural_closers(_repair_shifted_rows(rows))
    )


def _repair_inserted_structural_closers(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    repaired = list(rows)
    for index, row in enumerate(repaired):
        if row["status"] != "equal":
            continue
        if row.get("left_no") is not None:
            continue
        if row.get("right_no") is None:
            continue
        if row.get("right_tokens") != []:
            continue
        text = row["right_text"].strip()
        if text not in {")", "}", "]", ">"}:
            continue
        if index == 0 or index + 1 >= len(repaired):
            continue
        if repaired[index - 1]["status"] != "insert":
            continue
        if text != ">" and repaired[index + 1]["status"] != "insert":
            continue
        repaired[index] = _build_row(
            left_no=None,
            right_no=row["right_no"],
            left_text="",
            right_text=row["right_text"],
            left_tokens=None,
            right_tokens=_changed_tokens_for_text(row["right_text"], "insert"),
        )
    return repaired


def _changed_tokens_for_text(
    text: str,
    status: DifftasticTokenStatus,
) -> list[DifftasticInlineToken]:
    tokens: list[DifftasticInlineToken] = []
    _append_changed_token(tokens, text=text, status=status)
    return tokens


def _next_spec_has_left(specs: list[_RowSpec], index: int) -> bool:
    next_index = index + 1
    return next_index < len(specs) and specs[next_index].left is not None


def _match_right_leading_padding(
    *,
    left_index: int,
    right_index: int,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> _FragmentMatch | None:
    if not _has_empty_side_change_pair(
        side="right",
        left_index=left_index,
        right_index=right_index,
        change_index=change_index,
    ):
        return None
    if (
        _line_similarity(left_lines[left_index], right_lines[right_index])
        != 0.0
    ):
        return None
    if right_lines[right_index].strip() in {")", "}", "]"}:
        return None
    leading = _leading_whitespace(right_lines[right_index])
    if len(leading) <= 1:
        return None
    end = len(leading) - 1
    if end >= len(right_lines[right_index]):
        return None
    return _FragmentMatch(
        display_text=right_lines[right_index][:end], start=0, end=end
    )


def _side_tokens_for_line(
    line_index: int,
    *,
    line: str,
    intervals: dict[int, list[_ChangeInterval]],
    touched_lines: set[int],
) -> list[DifftasticInlineToken] | None:
    line_intervals = intervals.get(line_index)
    if line_intervals is not None:
        return _tokens_for_slice(
            line,
            start=0,
            end=len(line),
            intervals=line_intervals,
        )
    if line_index in touched_lines:
        return []
    return None


def _one_sided_intervals(
    intervals: list[_ChangeInterval],
    *,
    status: DifftasticTokenStatus,
) -> list[_ChangeInterval]:
    return [
        _ChangeInterval(
            start=interval.start,
            end=interval.end,
            status=status if interval.status == "replace" else interval.status,
        )
        for interval in intervals
    ]


def _full_pair_row(
    left_index: int,
    right_index: int,
    *,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> DifftasticRow:
    left_line = left_lines[left_index]
    right_line = right_lines[right_index]
    left_tokens = _side_tokens_for_line(
        left_index,
        line=left_line,
        intervals=change_index.left_intervals,
        touched_lines=change_index.touched_left_lines,
    )
    right_tokens = _side_tokens_for_line(
        right_index,
        line=right_line,
        intervals=change_index.right_intervals,
        touched_lines=change_index.touched_right_lines,
    )
    return _build_row(
        left_no=left_index + 1,
        right_no=right_index + 1,
        left_text=left_line,
        right_text=right_line,
        left_tokens=left_tokens,
        right_tokens=right_tokens,
    )


def _fragment_pair_row(
    left_index: int,
    right_index: int,
    *,
    match: _FragmentMatch,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> DifftasticRow:
    left_line = left_lines[left_index]
    right_line = right_lines[right_index]
    left_intervals = change_index.left_intervals.get(left_index, [])
    right_tokens = _side_tokens_for_line(
        right_index,
        line=right_line,
        intervals=change_index.right_intervals,
        touched_lines=change_index.touched_right_lines,
    )
    left_has_overlap = _has_interval_overlap(
        left_intervals,
        start=match.start,
        end=match.end,
    )
    sliced_left_tokens = _tokens_for_slice(
        left_line,
        start=match.start,
        end=match.end,
        intervals=left_intervals,
    )
    left_tokens: list[DifftasticInlineToken] | None
    if not left_has_overlap:
        if _has_empty_side_change_pair(
            side="left",
            left_index=left_index,
            right_index=right_index,
            change_index=change_index,
        ):
            left_tokens = []
        else:
            left_tokens = None
    elif (
        "".join(token["text"] for token in sliced_left_tokens)
        == match.display_text
    ):
        left_tokens = sliced_left_tokens
    elif left_index in change_index.touched_left_lines:
        left_tokens = []
    else:
        left_tokens = None
    return _build_row(
        left_no=left_index + 1,
        right_no=right_index + 1,
        left_text=match.display_text,
        right_text=right_line,
        left_tokens=left_tokens,
        right_tokens=right_tokens,
        allow_ws_only_equal=False,
    )


def _full_left_row(
    left_index: int,
    *,
    left_lines: list[str],
    change_index: _ChangeIndex,
) -> DifftasticRow:
    left_line = left_lines[left_index]
    left_intervals = change_index.left_intervals.get(left_index)
    if left_line == "":
        left_tokens: list[DifftasticInlineToken] | None = None
    elif left_intervals is None:
        if left_index in change_index.touched_left_lines:
            left_tokens = []
        else:
            left_tokens = None
    else:
        left_tokens = _tokens_for_slice(
            left_line,
            start=0,
            end=len(left_line),
            intervals=_one_sided_intervals(left_intervals, status="delete"),
        )
    return _build_row(
        left_no=left_index + 1,
        right_no=None,
        left_text=left_line,
        right_text="",
        left_tokens=left_tokens,
        right_tokens=None,
    )


def _left_residual_row(
    pending: _PendingLeftLine,
    *,
    left_lines: list[str],
    change_index: _ChangeIndex,
) -> DifftasticRow:
    left_line = left_lines[pending.index]
    left_text = left_line[pending.cursor :]
    left_intervals = change_index.left_intervals.get(pending.index)
    if left_intervals is None:
        if pending.index in change_index.touched_left_lines:
            left_tokens: list[DifftasticInlineToken] | None = []
        else:
            left_tokens = None
    else:
        left_tokens = _tokens_for_slice(
            left_line,
            start=pending.cursor,
            end=len(left_line),
            intervals=_one_sided_intervals(left_intervals, status="delete"),
        )
    return _build_row(
        left_no=pending.index + 1,
        right_no=None,
        left_text=left_text,
        right_text="",
        left_tokens=left_tokens,
        right_tokens=None,
    )


def _right_residual_row(
    pending: _PendingRightLine,
    *,
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> DifftasticRow:
    right_line = right_lines[pending.index]
    right_text = right_line[pending.cursor :]
    right_intervals = change_index.right_intervals.get(pending.index)
    right_tokens: list[DifftasticInlineToken] | None
    if right_intervals is None:
        if pending.cursor > 0:
            right_tokens = None
        elif pending.index in change_index.touched_right_lines:
            right_tokens = []
        else:
            right_tokens = None
    else:
        right_tokens = _tokens_for_slice(
            right_line,
            start=pending.cursor,
            end=len(right_line),
            intervals=_one_sided_intervals(right_intervals, status="insert"),
        )
    return _build_row(
        left_no=None,
        right_no=pending.index + 1,
        left_text="",
        right_text=right_text,
        left_tokens=None,
        right_tokens=right_tokens,
    )


def _right_only_row(
    right_index: int,
    *,
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> DifftasticRow:
    right_line = right_lines[right_index]
    right_intervals = change_index.right_intervals.get(right_index)
    if right_intervals is None:
        if right_index in change_index.touched_right_lines:
            right_tokens: list[DifftasticInlineToken] | None = []
        else:
            right_tokens = None
    else:
        right_tokens = _tokens_for_slice(
            right_line,
            start=0,
            end=len(right_line),
            intervals=_one_sided_intervals(right_intervals, status="insert"),
        )
    if right_tokens is None:
        right_tokens = []
    return _build_row(
        left_no=None,
        right_no=right_index + 1,
        left_text="",
        right_text=right_line,
        left_tokens=None,
        right_tokens=right_tokens,
    )


def _right_fragment_pair_row(
    left_index: int,
    right_index: int,
    *,
    match: _FragmentMatch,
    left_lines: list[str],
    right_lines: list[str],
    change_index: _ChangeIndex,
) -> DifftasticRow:
    left_line = left_lines[left_index]
    right_line = right_lines[right_index]
    left_tokens = _side_tokens_for_line(
        left_index,
        line=left_line,
        intervals=change_index.left_intervals,
        touched_lines=change_index.touched_left_lines,
    )
    right_intervals = change_index.right_intervals.get(right_index, [])
    right_has_overlap = _has_interval_overlap(
        right_intervals,
        start=match.start,
        end=match.end,
    )
    sliced_right_tokens = _tokens_for_slice(
        right_line,
        start=match.start,
        end=match.end,
        intervals=right_intervals,
    )
    right_tokens: list[DifftasticInlineToken] | None
    if not right_has_overlap:
        if _has_empty_side_change_pair(
            side="right",
            left_index=left_index,
            right_index=right_index,
            change_index=change_index,
        ):
            right_tokens = []
        else:
            right_tokens = None
    elif (
        "".join(token["text"] for token in sliced_right_tokens)
        == match.display_text
    ):
        right_tokens = sliced_right_tokens
    elif right_index in change_index.touched_right_lines:
        right_tokens = []
    else:
        right_tokens = None

    return _build_row(
        left_no=left_index + 1,
        right_no=right_index + 1,
        left_text=left_line,
        right_text=match.display_text,
        left_tokens=left_tokens,
        right_tokens=right_tokens,
        allow_ws_only_equal=False,
    )


def _repair_shifted_rows(rows: list[DifftasticRow]) -> list[DifftasticRow]:
    # Difftastic can emit adjacent replace rows whose texts show a one-line
    # shift. Preserve the CLI display shape by rendering that as
    # delete/equal/insert, but only when the row texts prove the overlap.
    repaired: list[DifftasticRow] = []
    index = 0
    while index < len(rows):
        if index + 1 >= len(rows):
            repaired.append(rows[index])
            index += 1
            continue

        current = rows[index]
        following = rows[index + 1]
        if _can_repair_shifted_pair(current, following):
            repaired.extend(_shifted_pair_rows(current, following))
            index += 2
            continue

        repaired.append(current)
        index += 1
    return repaired


def _repair_structural_context_rows(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    repaired = list(rows)
    for index, row in enumerate(repaired):
        structural_text = _one_sided_right_structural_context(row)
        if structural_text is None:
            continue
        candidate_index = _previous_left_suffix_candidate(
            repaired,
            before=index,
            suffix=structural_text,
        )
        if candidate_index is None:
            continue

        split = _split_left_row_suffix(
            repaired[candidate_index],
            suffix=structural_text,
        )
        if split is None:
            continue

        prefix_row, suffix_text, suffix_tokens = split
        repaired[candidate_index] = prefix_row
        repaired[index] = _build_row(
            left_no=prefix_row["left_no"],
            right_no=row["right_no"],
            left_text=suffix_text,
            right_text=row["right_text"],
            left_tokens=suffix_tokens,
            right_tokens=_unchanged_tokens_for_text(row["right_text"]),
        )
    return repaired


def _one_sided_right_structural_context(
    row: DifftasticRow,
) -> str | None:
    is_one_sided_context = (
        row["status"] == "equal"
        and row.get("left_no") is None
        and row.get("right_no") is not None
        and row.get("right_tokens") == []
    )
    if not is_one_sided_context:
        return None

    text = row["right_text"].strip()
    is_single_structural_atom = (
        len(text) == 1
        and _WORD_PATTERN.search(text) is None
        and _ATOM_PATTERN.fullmatch(text) is not None
    )
    if is_single_structural_atom:
        return text
    return None


def _previous_left_suffix_candidate(
    rows: list[DifftasticRow],
    *,
    before: int,
    suffix: str,
) -> int | None:
    run_start = before
    while run_start > 0 and _is_right_only_row(rows[run_start - 1]):
        run_start -= 1
    if run_start in (0, before):
        return None

    candidate_index = run_start - 1
    candidate = rows[candidate_index]
    is_left_only_candidate = (
        candidate.get("right_no") is None
        and candidate.get("left_no") is not None
    )
    if (
        is_left_only_candidate
        and _right_only_run_has_unclosed_opener(
            rows,
            start=run_start,
            end=before,
            closer=suffix,
        )
        and _can_split_left_row_suffix(candidate, suffix=suffix)
    ):
        return candidate_index
    return None


def _is_right_only_row(row: DifftasticRow) -> bool:
    return row.get("left_no") is None and row.get("right_no") is not None


def _right_only_run_has_unclosed_opener(
    rows: list[DifftasticRow],
    *,
    start: int,
    end: int,
    closer: str,
) -> bool:
    opener = _opener_for_closer(closer)
    if opener is None:
        return False

    balance = 0
    for row in rows[start:end]:
        text = row["right_text"]
        balance += text.count(opener)
        balance -= text.count(closer)
    return balance > 0


def _opener_for_closer(closer: str) -> str | None:
    match closer:
        case ")":
            return "("
        case "]":
            return "["
        case "}":
            return "{"
        case ">":
            return "<"
        case _:
            return None


def _can_split_left_row_suffix(
    row: DifftasticRow,
    *,
    suffix: str,
) -> bool:
    text = row["left_text"]
    end = len(text.rstrip(" \t"))
    start = end - len(suffix)
    if start <= 0:
        return False
    if text[start:end] != suffix:
        return False
    return bool(_ATOM_PATTERN.search(text[:start]))


def _split_left_row_suffix(
    row: DifftasticRow,
    *,
    suffix: str,
) -> tuple[DifftasticRow, str, list[DifftasticInlineToken] | None] | None:
    text = row["left_text"]
    end = len(text.rstrip(" \t"))
    start = end - len(suffix)
    if start <= 0:
        return None
    if text[start:end] != suffix:
        return None

    prefix_tokens: list[DifftasticInlineToken] | None
    suffix_tokens: list[DifftasticInlineToken] | None
    tokens = row.get("left_tokens")
    if tokens is None:
        prefix_tokens = None
        suffix_tokens = None
    else:
        prefix_tokens, suffix_tokens = _split_tokens_at(tokens, start)

    prefix_row = _build_row(
        left_no=row["left_no"],
        right_no=None,
        left_text=text[:start],
        right_text="",
        left_tokens=prefix_tokens,
        right_tokens=None,
    )
    return prefix_row, text[start:], suffix_tokens


def _split_tokens_at(
    tokens: list[DifftasticInlineToken],
    offset: int,
) -> tuple[list[DifftasticInlineToken], list[DifftasticInlineToken]]:
    before: list[DifftasticInlineToken] = []
    after: list[DifftasticInlineToken] = []
    cursor = 0
    for token in tokens:
        text = token["text"]
        end = cursor + len(text)
        if end <= offset:
            before.append(token)
        elif cursor >= offset:
            after.append(token)
        else:
            split_at = offset - cursor
            _append_copied_token(before, token, text[:split_at])
            _append_copied_token(after, token, text[split_at:])
        cursor = end
    return before, after


def _append_copied_token(
    tokens: list[DifftasticInlineToken],
    token: DifftasticInlineToken,
    text: str,
) -> None:
    if text == "":
        return
    tokens.append(
        {
            "text": text,
            "status": token["status"],
            "is_ws": text.isspace(),
        }
    )


def _unchanged_tokens_for_text(text: str) -> list[DifftasticInlineToken]:
    if text == "":
        return []
    return [{"text": text, "status": "unchanged", "is_ws": text.isspace()}]


def _can_repair_shifted_pair(
    current: DifftasticRow,
    following: DifftasticRow,
) -> bool:
    return (
        current["status"] == "replace"
        and following["status"] == "replace"
        and current.get("left_no") is not None
        and current.get("right_no") is not None
        and following.get("left_no") is not None
        and following.get("right_no") is not None
        and current["right_text"] == following["left_text"]
    )


def _shifted_pair_rows(
    current: DifftasticRow,
    following: DifftasticRow,
) -> list[DifftasticRow]:
    delete_row: DifftasticRow = {
        "status": "delete",
        "left_no": current["left_no"],
        "right_no": None,
        "left_text": current["left_text"],
        "right_text": "",
    }

    equal_row: DifftasticRow = {
        "status": "equal",
        "left_no": following["left_no"],
        "right_no": current["right_no"],
        "left_text": following["left_text"],
        "right_text": current["right_text"],
    }

    insert_row: DifftasticRow = {
        "status": "insert",
        "left_no": None,
        "right_no": following["right_no"],
        "left_text": "",
        "right_text": following["right_text"],
    }
    return [delete_row, equal_row, insert_row]


def _tokens_with_replaced_status(
    tokens: list[DifftasticInlineToken],
    *,
    status: DifftasticTokenStatus,
) -> list[DifftasticInlineToken]:
    rewritten: list[DifftasticInlineToken] = []
    for token in tokens:
        token_status = token["status"]
        if token_status == "replace":
            token_status = status
        rewritten.append(
            {
                "text": token["text"],
                "status": token_status,
                "is_ws": token["is_ws"],
            }
        )
    return rewritten


def _difftastic_rows_from_json(
    diff_json: DifftasticJson,
    *,
    left_text: str,
    right_text: str,
) -> list[DifftasticRow]:
    left_lines = _source_lines(left_text)
    right_lines = _source_lines(right_text)
    change_index = _change_index(diff_json)
    specs = _normalized_row_specs(
        diff_json,
        left_count=len(left_lines),
        right_count=len(right_lines),
        left_lines=left_lines,
        right_lines=right_lines,
        change_index=change_index,
    )
    return _rows_from_specs(
        specs,
        left_lines=left_lines,
        right_lines=right_lines,
        change_index=change_index,
    )


def build_difftastic_ast(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> DifftasticAst:
    diff_json = run_difftastic_json(
        left_text=left_text,
        right_text=right_text,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    rows = _difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )
    return DifftasticAst(
        rows=rows,
        engine_warning=_difftastic_engine_warning(diff_json),
    )
