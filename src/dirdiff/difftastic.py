from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dirdiff.sources import TextDiffError
from dirdiff.textdiff import _paired_line_row

DFT_GRAPH_LIMIT = "10000000"


def _difftastic_changed_token_parts(text: str) -> list[str]:
    if not text:
        return []
    if len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]:
        return [text]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\(\)", text):
        return [text]
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|\s+|.", text)


def _changed_tokens_for_ranges(
    line: str,
    ranges: list[tuple[int, int]],
    *,
    status: Literal["replace", "insert", "delete"] = "replace",
) -> list[dict[str, Any]]:
    if not ranges:
        return []

    tokens: list[dict[str, Any]] = []
    cursor = 0
    for raw_start, raw_end in sorted(ranges):
        start = max(cursor, 0, min(raw_start, len(line)))
        end = max(start, min(raw_end, len(line)))
        if start > cursor:
            text = line[cursor:start]
            tokens.append(
                {"text": text, "status": "unchanged", "is_ws": text.isspace()}
            )
        if end > start:
            text = line[start:end]
            for part in _difftastic_changed_token_parts(text):
                tokens.append(
                    {
                        "text": part,
                        "status": status,
                        "is_ws": part.isspace(),
                    }
                )
        cursor = max(cursor, end)

    if cursor < len(line):
        text = line[cursor:]
        tokens.append(
            {"text": text, "status": "unchanged", "is_ws": text.isspace()}
        )
    return tokens


def _changed_tokens_for_ranges_with_statuses(
    line: str,
    ranges: list[tuple[int, int]],
    statuses: list[Literal["replace", "insert", "delete"]],
) -> list[dict[str, Any]]:
    if not ranges:
        return []

    tokens: list[dict[str, Any]] = []
    cursor = 0
    for index, (raw_start, raw_end) in enumerate(sorted(ranges)):
        start = max(cursor, 0, min(raw_start, len(line)))
        end = max(start, min(raw_end, len(line)))
        if start > cursor:
            text = line[cursor:start]
            tokens.append(
                {"text": text, "status": "unchanged", "is_ws": text.isspace()}
            )
        if end > start:
            text = line[start:end]
            status = statuses[index] if index < len(statuses) else statuses[-1]
            for part in _difftastic_changed_token_parts(text):
                tokens.append(
                    {
                        "text": part,
                        "status": status,
                        "is_ws": part.isspace(),
                    }
                )
        cursor = max(cursor, end)

    if cursor < len(line):
        text = line[cursor:]
        tokens.append(
            {"text": text, "status": "unchanged", "is_ws": text.isspace()}
        )
    return tokens


def _paired_difftastic_range_statuses(
    *,
    own_ranges: list[tuple[int, int]],
    counterpart_ranges: list[tuple[int, int]],
    extra_status: Literal["insert", "delete"],
) -> list[Literal["replace", "insert", "delete"]]:
    paired_count = min(len(own_ranges), len(counterpart_ranges))
    statuses: list[Literal["replace", "insert", "delete"]] = [
        "replace"
    ] * paired_count
    statuses.extend([extra_status] * (len(own_ranges) - paired_count))
    return statuses


def _difftastic_row_status_from_tokens(
    tokens: list[dict[str, Any]],
) -> Literal["equal", "replace", "insert", "delete"]:
    significant_tokens = [token for token in tokens if not token.get("is_ws")]
    changed_statuses = [
        token.get("status")
        for token in significant_tokens
        if token.get("status") != "unchanged"
    ]
    all_statuses = [token.get("status") for token in significant_tokens]
    if all_statuses and all(status == "insert" for status in all_statuses):
        return "insert"
    if all_statuses and all(status == "delete" for status in all_statuses):
        return "delete"
    if changed_statuses:
        return "replace"
    return "equal"


def _difftastic_changed_ranges_by_line(
    diff_json: dict[str, Any],
    *,
    side: Literal["lhs", "rhs"],
) -> dict[int, list[tuple[int, int]]]:
    ranges: dict[int, list[tuple[int, int]]] = {}
    chunks = diff_json.get("chunks", [])
    if not isinstance(chunks, list):
        return ranges

    for chunk in chunks:
        if not isinstance(chunk, list):
            continue
        for entry in chunk:
            if not isinstance(entry, dict):
                continue
            side_entry = entry.get(side)
            if not isinstance(side_entry, dict):
                continue
            line_number = side_entry.get("line_number")
            changes = side_entry.get("changes", [])
            if not isinstance(line_number, int) or not isinstance(
                changes, list
            ):
                continue
            line_ranges = ranges.setdefault(line_number, [])
            for change in changes:
                if not isinstance(change, dict):
                    continue
                start = change.get("start")
                end = change.get("end")
                if isinstance(start, int) and isinstance(end, int):
                    line_ranges.append((start, end))
    return ranges


def _shared_semantic_line_text(
    left_line: str, right_line: str
) -> tuple[str, str]:
    if left_line == right_line:
        return left_line, right_line
    if left_line.startswith(right_line):
        return right_line, right_line
    if right_line.startswith(left_line):
        return left_line, left_line
    return left_line, right_line


def _remove_line_ranges(line: str, ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return line

    pieces: list[str] = []
    cursor = 0
    for raw_start, raw_end in sorted(ranges):
        start = max(cursor, 0, min(raw_start, len(line)))
        end = max(start, min(raw_end, len(line)))
        if start > cursor:
            pieces.append(line[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(line):
        pieces.append(line[cursor:])
    return "".join(pieces)


def _clip_one_sided_semantic_line_text(
    *,
    left_line: str,
    right_line: str,
    left_ranges: list[tuple[int, int]],
    right_ranges: list[tuple[int, int]],
) -> tuple[str, str]:
    if not left_ranges and right_ranges:
        semantic_left = _remove_line_ranges(right_line, right_ranges)
        if left_line.startswith(semantic_left):
            return semantic_left, right_line
    if left_ranges and not right_ranges:
        semantic_right = _remove_line_ranges(left_line, left_ranges)
        if right_line.startswith(semantic_right):
            return left_line, semantic_right
    return left_line, right_line


@dataclass(frozen=True)
class DifftasticAlignedLine:
    left_index: int | None
    right_index: int | None


@dataclass(frozen=True)
class DifftasticLineFragment:
    source_index: int
    text: str
    kind: Literal["suffix", "residual"]


def _difftastic_aligned_lines(value: Any) -> list[DifftasticAlignedLine]:
    if not isinstance(value, list):
        return []

    aligned: list[DifftasticAlignedLine] = []
    for pair in value:
        if not isinstance(pair, list | tuple) or len(pair) != 2:
            continue
        raw_left, raw_right = pair
        aligned.append(
            DifftasticAlignedLine(
                left_index=raw_left if isinstance(raw_left, int) else None,
                right_index=raw_right if isinstance(raw_right, int) else None,
            )
        )
    return aligned


def _difftastic_engine_warning(
    diff_json: dict[str, Any],
) -> dict[str, str] | None:
    language = diff_json.get("language")
    if isinstance(language, str) and "exceeded DFT_GRAPH_LIMIT" in language:
        return {
            "type": "difftastic_graph_limit",
            "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
        }
    return None


def _difftastic_line_anchor_bounds(
    aligned_lines: list[DifftasticAlignedLine],
    row_index: int,
    *,
    side: Literal["left", "right"],
) -> tuple[int, int]:
    def side_index(line: DifftasticAlignedLine) -> int | None:
        return line.left_index if side == "left" else line.right_index

    lower = -1
    for line in reversed(aligned_lines[:row_index]):
        index = side_index(line)
        if index is not None:
            lower = index
            break

    upper = 10**12
    for line in aligned_lines[row_index + 1 :]:
        index = side_index(line)
        if index is not None:
            upper = index
            break

    return lower, upper


def _find_difftastic_reconstructed_line(
    lines: list[str],
    candidate: str,
    *,
    lower_bound: int,
    upper_bound: int,
    used: set[int],
) -> int | None:
    if not candidate:
        return None

    for index, line in enumerate(lines):
        if index in used:
            continue
        if not lower_bound < index < upper_bound:
            continue
        if line == candidate:
            return index
    return None


def _difftastic_line_item_key(line: str) -> str:
    return line.strip().rstrip(",").strip()


def _difftastic_fragment_key_is_matchable(key: str) -> bool:
    return bool(key) and key not in {"{", "}", ":"}


def _difftastic_fragment_tokens(text: str) -> list[str]:
    return [
        part
        for part in _difftastic_changed_token_parts(text)
        if not part.isspace()
    ]


def _difftastic_token_is_syntax(token: str) -> bool:
    return not re.fullmatch(r"\w+", token, flags=re.UNICODE)


def _difftastic_tokens_are_syntax(tokens: list[str]) -> bool:
    return bool(tokens) and all(
        _difftastic_token_is_syntax(token) for token in tokens
    )


def _difftastic_tokens_are_assignment(tokens: list[str]) -> bool:
    return "=" in tokens


def _difftastic_tokens_have_member_access(tokens: list[str]) -> bool:
    return "." in tokens


def _difftastic_token_can_start_safe_residual_key(token: str) -> bool:
    return token == ":"


def _difftastic_tokens_are_safe_residual_gap(tokens: list[str]) -> bool:
    return all(_difftastic_token_is_syntax(token) for token in tokens)


def _difftastic_residual_fragment_covers_window(
    fragment_text: str,
    candidate_texts: list[str],
) -> bool:
    fragment_tokens = _difftastic_fragment_tokens(fragment_text)
    cursor = 0
    for candidate_text in candidate_texts:
        key = _difftastic_line_item_key(candidate_text)
        if not _difftastic_fragment_key_is_matchable(key):
            continue
        key_tokens = _difftastic_fragment_tokens(key)
        if not key_tokens:
            continue

        matched = False
        last_start = len(fragment_tokens) - len(key_tokens)
        for start in range(cursor, last_start + 1):
            if fragment_tokens[start : start + len(key_tokens)] != key_tokens:
                continue
            if not _difftastic_tokens_are_safe_residual_gap(
                fragment_tokens[cursor:start]
            ):
                return False
            cursor = start + len(key_tokens)
            matched = True
            break

        if not matched:
            continue

    return _difftastic_tokens_are_safe_residual_gap(fragment_tokens[cursor:])


def _difftastic_right_only_window_candidate_texts(
    aligned_lines: list[DifftasticAlignedLine],
    row_index: int,
    right_lines: list[str],
    right_ranges: dict[int, list[tuple[int, int]]],
) -> list[str]:
    start = row_index
    while start > 0 and aligned_lines[start - 1].left_index is None:
        start -= 1

    end = row_index
    while (
        end + 1 < len(aligned_lines)
        and aligned_lines[end + 1].left_index is None
    ):
        end += 1

    candidates: list[str] = []
    for pair in aligned_lines[start : end + 1]:
        right_index = pair.right_index
        if right_index is None or not (0 <= right_index < len(right_lines)):
            continue
        candidates.append(
            _remove_line_ranges(
                right_lines[right_index], right_ranges.get(right_index, [])
            )
        )
    return candidates


def _difftastic_right_only_window_bounds(
    aligned_lines: list[DifftasticAlignedLine],
    row_index: int,
) -> tuple[int, int]:
    start = row_index
    while start > 0 and aligned_lines[start - 1].left_index is None:
        start -= 1

    end = row_index
    while (
        end + 1 < len(aligned_lines)
        and aligned_lines[end + 1].left_index is None
    ):
        end += 1

    return start, end


def _difftastic_leading_whitespace(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


def _difftastic_split_residual_items(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    quote: str | None = None
    escape = False

    def flush_current() -> None:
        item = "".join(current).strip()
        if item:
            items.append(item)
        current.clear()

    for char in text:
        if quote is not None:
            current.append(char)
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue

        if char == "," and paren_depth == bracket_depth == brace_depth == 0:
            current.append(char)
            flush_current()
            continue

        if char in ")]}" and paren_depth == bracket_depth == brace_depth == 0:
            flush_current()
            items.append(char)
            continue

        current.append(char)
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)

    flush_current()
    return items


def _difftastic_coalesce_trailing_syntax_items(
    items: list[str],
) -> list[str]:
    if not items:
        return []

    trailing_syntax: list[str] = []
    cursor = len(items)
    while cursor > 0:
        item = items[cursor - 1]
        item_tokens = _difftastic_fragment_tokens(item)
        if not _difftastic_tokens_are_syntax(item_tokens):
            break
        trailing_syntax.append(item)
        cursor -= 1

    if not trailing_syntax:
        return items

    return items[:cursor] + ["".join(reversed(trailing_syntax))]


def _difftastic_plan_right_only_residual_window(
    *,
    aligned_lines: list[DifftasticAlignedLine],
    row_index: int,
    left_fragments: list[DifftasticLineFragment],
    lower_bound: int,
    upper_bound: int,
    right_lines: list[str],
    right_ranges: dict[int, list[tuple[int, int]]],
) -> dict[int, tuple[int, str]] | None:
    start, end = _difftastic_right_only_window_bounds(aligned_lines, row_index)
    window_pairs = aligned_lines[start : end + 1]
    right_indexes = [
        pair.right_index
        for pair in window_pairs
        if pair.right_index is not None
        and 0 <= pair.right_index < len(right_lines)
    ]
    if len(right_indexes) != len(window_pairs):
        return None

    for fragment in left_fragments:
        if fragment.kind != "residual":
            continue
        if not lower_bound <= fragment.source_index < upper_bound:
            continue

        items = _difftastic_coalesce_trailing_syntax_items(
            _difftastic_split_residual_items(fragment.text)
        )
        item_keys = [_difftastic_line_item_key(item) for item in items]
        row_candidates = [
            (
                right_index,
                _difftastic_line_item_key(
                    _remove_line_ranges(
                        right_lines[right_index],
                        right_ranges.get(right_index, []),
                    )
                ),
                bool(right_ranges.get(right_index, [])),
            )
            for right_index in right_indexes
        ]

        def match_window(
            row_position: int,
            item_position: int,
        ) -> dict[int, tuple[int, str]] | None:
            if item_position == len(items):
                for _, candidate_key, has_ranges in row_candidates[
                    row_position:
                ]:
                    if candidate_key and not has_ranges:
                        return None
                return {}

            if row_position == len(row_candidates):
                return None

            right_index, candidate_key, has_ranges = row_candidates[
                row_position
            ]
            item = items[item_position]
            item_key = item_keys[item_position]

            if candidate_key == item_key:
                remainder = match_window(row_position + 1, item_position + 1)
                if remainder is not None:
                    return {
                        right_index: (
                            fragment.source_index,
                            f"{_difftastic_leading_whitespace(right_lines[right_index])}{item}",
                        ),
                        **remainder,
                    }

            if has_ranges:
                remainder = match_window(row_position + 1, item_position)
                if remainder is not None:
                    return remainder

                remainder = match_window(row_position + 1, item_position + 1)
                if remainder is not None:
                    return {
                        right_index: (
                            fragment.source_index,
                            f"{_difftastic_leading_whitespace(right_lines[right_index])}{item}",
                        ),
                        **remainder,
                    }

            return None

        mappings = match_window(0, 0)
        if mappings is None:
            continue

        return mappings

    return None


def _difftastic_fragment_contains_key(
    fragment_text: str,
    key: str,
    *,
    allow_syntax_only: bool = False,
    allow_assignment: bool = True,
    fragment_kind: Literal["suffix", "residual"] = "suffix",
    allow_syntax_leading_residual: bool = False,
) -> bool:
    fragment_tokens = _difftastic_fragment_tokens(fragment_text)
    key_tokens = _difftastic_fragment_tokens(key)
    if not key_tokens or len(key_tokens) > len(fragment_tokens):
        return False

    key_is_syntax_only = _difftastic_tokens_are_syntax(key_tokens)
    key_is_assignment = _difftastic_tokens_are_assignment(key_tokens)
    key_starts_with_closing_syntax = key_tokens[0] in {")", "]", "}", ">"}
    key_starts_with_syntax = _difftastic_token_is_syntax(key_tokens[0])
    if key_is_syntax_only and not allow_syntax_only:
        return False
    if key_is_assignment and not allow_assignment:
        return False
    if (
        not key_is_assignment
        and not key_starts_with_syntax
        and len(key_tokens) > 1
        and fragment_text[:1].isspace()
    ):
        return False

    last_start = len(fragment_tokens) - len(key_tokens)
    for index in range(last_start + 1):
        if fragment_tokens[index : index + len(key_tokens)] != key_tokens:
            continue
        if (
            fragment_kind == "residual"
            and key_starts_with_syntax
            and not key_is_syntax_only
            and not key_starts_with_closing_syntax
        ):
            if not allow_syntax_leading_residual:
                continue
            if index == 0 and not _difftastic_token_can_start_safe_residual_key(
                key_tokens[0]
            ):
                continue
        if (
            fragment_kind == "suffix"
            and index == 0
            and (len(key_tokens) == 1 or key_starts_with_syntax)
        ):
            continue
        if (
            fragment_kind == "suffix"
            and len(key_tokens) == 1
            and index > 0
            and fragment_tokens[index - 1] == ","
        ):
            continue
        if (
            not key_is_syntax_only
            and not key_is_assignment
            and not key_starts_with_syntax
            and index > 0
            and not _difftastic_token_is_syntax(fragment_tokens[index - 1])
        ):
            continue
        if not key_is_syntax_only and _difftastic_tokens_have_member_access(
            fragment_tokens[:index]
        ):
            continue
        if not key_is_syntax_only and "=" in fragment_tokens[:index]:
            continue
        tail_tokens = fragment_tokens[index + len(key_tokens) :]
        if not tail_tokens or _difftastic_token_is_syntax(tail_tokens[0]):
            return True
    return False


def _common_prefix_length(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _collapse_reconstructed_gap_spaces(text: str) -> str:
    return re.sub(r"(?<=\S) {2,}(?=\S)", " ", text)


def _difftastic_suffix_fragment_after_ranges(
    *,
    line: str,
    ranges: list[tuple[int, int]],
    counterpart_line: str,
    counterpart_ranges: list[tuple[int, int]],
) -> str:
    if not ranges:
        return ""

    suffix_start = max(max(0, min(end, len(line))) for _, end in ranges)
    suffix = line[suffix_start:]
    if not suffix.strip():
        return ""

    counterpart_suffix = ""
    if counterpart_ranges:
        counterpart_suffix_start = max(
            max(0, min(end, len(counterpart_line)))
            for _, end in counterpart_ranges
        )
        counterpart_suffix = counterpart_line[counterpart_suffix_start:]

    shared_prefix_length = _common_prefix_length(suffix, counterpart_suffix)
    return suffix[shared_prefix_length:]


def _find_difftastic_reconstructed_fragment(
    fragments: list[DifftasticLineFragment],
    candidate: str,
    *,
    lower_bound: int,
    upper_bound: int,
    used: set[tuple[int, str]],
    allow_assignment: bool = True,
    allow_syntax_only: bool = True,
    residual_candidate_window: list[str] | None = None,
) -> DifftasticLineFragment | None:
    key = _difftastic_line_item_key(candidate)
    if not _difftastic_fragment_key_is_matchable(key):
        return None

    for fragment in fragments:
        used_key = (fragment.source_index, key)
        if used_key in used:
            continue
        if not lower_bound <= fragment.source_index < upper_bound:
            continue
        if (
            fragment.kind == "residual"
            and residual_candidate_window is not None
            and _difftastic_residual_fragment_covers_window(
                fragment.text, residual_candidate_window
            )
        ):
            safe_residual_window = True
        else:
            safe_residual_window = False
        if (
            fragment.kind == "residual"
            and residual_candidate_window is not None
            and not safe_residual_window
        ):
            continue
        if _difftastic_fragment_contains_key(
            fragment.text,
            key,
            allow_syntax_only=allow_syntax_only
            and any(
                used_fragment_index == fragment.source_index
                for used_fragment_index, _ in used
            ),
            allow_assignment=allow_assignment,
            fragment_kind=fragment.kind,
            allow_syntax_leading_residual=safe_residual_window,
        ):
            return fragment
    return None


def _difftastic_fragment_key(
    fragment: DifftasticLineFragment,
) -> tuple[int, str]:
    return (fragment.source_index, _difftastic_line_item_key(fragment.text))


def _delete_only_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "delete",
        "left_no": row.get("left_no"),
        "right_no": None,
        "left_text": row.get("left_text", ""),
        "right_text": "",
    }


def _insert_only_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "insert",
        "left_no": None,
        "right_no": row.get("right_no"),
        "left_text": "",
        "right_text": row.get("right_text", ""),
    }


def _equal_row_from_crossed_replacements(
    left_row: dict[str, Any],
    right_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "equal",
        "left_no": left_row.get("left_no"),
        "right_no": right_row.get("right_no"),
        "left_text": left_row.get("left_text", ""),
        "right_text": right_row.get("right_text", ""),
    }


def _repair_shifted_difftastic_replacements(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        if (
            next_row is not None
            and row.get("status") == "replace"
            and next_row.get("status") == "replace"
        ):
            if row.get("right_text") and row.get("right_text") == next_row.get(
                "left_text"
            ):
                repaired.append(_delete_only_row(row))
                repaired.append(
                    _equal_row_from_crossed_replacements(next_row, row)
                )
                repaired.append(_insert_only_row(next_row))
                index += 2
                continue
            if row.get("left_text") and row.get("left_text") == next_row.get(
                "right_text"
            ):
                repaired.append(_insert_only_row(row))
                repaired.append(
                    _equal_row_from_crossed_replacements(row, next_row)
                )
                repaired.append(_delete_only_row(next_row))
                index += 2
                continue
        repaired.append(row)
        index += 1
    return repaired


def _difftastic_rows_from_json(
    diff_json: dict[str, Any],
    *,
    left_text: str,
    right_text: str,
) -> list[dict[str, Any]]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    left_ranges = _difftastic_changed_ranges_by_line(diff_json, side="lhs")
    right_ranges = _difftastic_changed_ranges_by_line(diff_json, side="rhs")
    aligned_lines = _difftastic_aligned_lines(
        diff_json.get("aligned_lines", [])
    )
    if not aligned_lines:
        return []

    rows: list[dict[str, Any]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()
    left_fragments: list[DifftasticLineFragment] = []
    right_fragments: list[DifftasticLineFragment] = []
    used_left_fragments: set[tuple[int, str]] = set()
    used_right_fragments: set[tuple[int, str]] = set()
    planned_left_window_rows: dict[int, tuple[int, str]] = {}
    for aligned_index, pair in enumerate(aligned_lines):
        left_index = pair.left_index
        right_index = pair.right_index
        if left_index in used_left or right_index in used_right:
            continue
        left_exists = left_index is not None and 0 <= left_index < len(
            left_lines
        )
        right_exists = right_index is not None and 0 <= right_index < len(
            right_lines
        )

        if not left_exists and not right_exists:
            continue

        if left_exists and right_exists:
            assert left_index is not None
            assert right_index is not None
            original_left_line = left_lines[left_index]
            original_right_line = right_lines[right_index]
            left_line = original_left_line
            right_line = original_right_line
            left_line_ranges = left_ranges.get(left_index, [])
            right_line_ranges = right_ranges.get(right_index, [])
            if not left_line_ranges and not right_line_ranges:
                left_line, right_line = _shared_semantic_line_text(
                    left_line, right_line
                )
            else:
                left_line, right_line = _clip_one_sided_semantic_line_text(
                    left_line=left_line,
                    right_line=right_line,
                    left_ranges=left_line_ranges,
                    right_ranges=right_line_ranges,
                )
            if original_left_line.startswith(left_line):
                residual_left = original_left_line[len(left_line) :]
                if residual_left.strip():
                    left_fragments.append(
                        DifftasticLineFragment(
                            source_index=left_index,
                            text=residual_left,
                            kind="residual",
                        )
                    )
            if original_right_line.startswith(right_line):
                residual_right = original_right_line[len(right_line) :]
                if residual_right.strip():
                    right_fragments.append(
                        DifftasticLineFragment(
                            source_index=right_index,
                            text=residual_right,
                            kind="residual",
                        )
                    )
            left_suffix_fragment = _difftastic_suffix_fragment_after_ranges(
                line=original_left_line,
                ranges=left_line_ranges,
                counterpart_line=original_right_line,
                counterpart_ranges=right_line_ranges,
            )
            if left_suffix_fragment.strip():
                left_fragments.append(
                    DifftasticLineFragment(
                        source_index=left_index,
                        text=left_suffix_fragment,
                        kind="suffix",
                    )
                )
            right_suffix_fragment = _difftastic_suffix_fragment_after_ranges(
                line=original_right_line,
                ranges=right_line_ranges,
                counterpart_line=original_left_line,
                counterpart_ranges=left_line_ranges,
            )
            if right_suffix_fragment.strip():
                right_fragments.append(
                    DifftasticLineFragment(
                        source_index=right_index,
                        text=right_suffix_fragment,
                        kind="suffix",
                    )
                )
            row = _paired_line_row(
                left_line,
                right_line,
                left_index + 1,
                right_index + 1,
            )
            left_status: Literal["replace", "delete"] = (
                "replace" if right_line_ranges else "delete"
            )
            right_status: Literal["replace", "insert"] = (
                "replace" if left_line_ranges else "insert"
            )
            if left_line_ranges and right_line_ranges:
                left_tokens = _changed_tokens_for_ranges_with_statuses(
                    left_line,
                    left_line_ranges,
                    _paired_difftastic_range_statuses(
                        own_ranges=left_line_ranges,
                        counterpart_ranges=right_line_ranges,
                        extra_status="delete",
                    ),
                )
                right_tokens = _changed_tokens_for_ranges_with_statuses(
                    right_line,
                    right_line_ranges,
                    _paired_difftastic_range_statuses(
                        own_ranges=right_line_ranges,
                        counterpart_ranges=left_line_ranges,
                        extra_status="insert",
                    ),
                )
            else:
                left_tokens = _changed_tokens_for_ranges(
                    left_line,
                    left_line_ranges,
                    status=left_status,
                )
                right_tokens = _changed_tokens_for_ranges(
                    right_line,
                    right_line_ranges,
                    status=right_status,
                )
            if left_tokens or right_tokens:
                row["status"] = "replace"
                row["left_tokens"] = left_tokens
                row["right_tokens"] = right_tokens
            rows.append(row)
            used_left.add(left_index)
            used_right.add(right_index)
            continue

        if left_exists:
            assert left_index is not None
            left_line_ranges = left_ranges.get(left_index, [])
            candidate_right = _remove_line_ranges(
                left_lines[left_index], left_line_ranges
            )
            lower, upper = _difftastic_line_anchor_bounds(
                aligned_lines, aligned_index, side="right"
            )
            if left_line_ranges:
                reconstructed_right_index = _find_difftastic_reconstructed_line(
                    right_lines,
                    candidate_right,
                    lower_bound=lower,
                    upper_bound=upper,
                    used=used_right,
                )
                if reconstructed_right_index is not None:
                    row = _paired_line_row(
                        left_lines[left_index],
                        candidate_right,
                        left_index + 1,
                        reconstructed_right_index + 1,
                    )
                    row["status"] = "replace"
                    row["left_tokens"] = _changed_tokens_for_ranges(
                        left_lines[left_index],
                        left_line_ranges,
                        status="delete",
                    )
                    row.pop("right_tokens", None)
                    rows.append(row)
                    used_left.add(left_index)
                    used_right.add(reconstructed_right_index)
                    continue
                reconstructed_right_fragment = (
                    _find_difftastic_reconstructed_fragment(
                        right_fragments,
                        candidate_right,
                        lower_bound=lower,
                        upper_bound=upper,
                        used=used_right_fragments,
                    )
                )
                if reconstructed_right_fragment is not None:
                    row = _paired_line_row(
                        left_lines[left_index],
                        candidate_right,
                        left_index + 1,
                        reconstructed_right_fragment.source_index + 1,
                    )
                    row["status"] = "replace"
                    row["left_tokens"] = _changed_tokens_for_ranges(
                        left_lines[left_index],
                        left_line_ranges,
                        status="delete",
                    )
                    row.pop("right_tokens", None)
                    rows.append(row)
                    used_left.add(left_index)
                    used_right_fragments.add(
                        (
                            reconstructed_right_fragment.source_index,
                            _difftastic_line_item_key(candidate_right),
                        )
                    )
                    continue
            if not left_line_ranges:
                reconstructed_right_fragment = (
                    _find_difftastic_reconstructed_fragment(
                        right_fragments,
                        candidate_right,
                        lower_bound=lower,
                        upper_bound=upper,
                        used=used_right_fragments,
                    )
                )
                if reconstructed_right_fragment is not None:
                    rows.append(
                        _paired_line_row(
                            left_lines[left_index],
                            candidate_right,
                            left_index + 1,
                            reconstructed_right_fragment.source_index + 1,
                        )
                    )
                    used_left.add(left_index)
                    used_right_fragments.add(
                        _difftastic_fragment_key(reconstructed_right_fragment)
                    )
                    continue
            left_tokens = _changed_tokens_for_ranges(
                left_lines[left_index],
                left_line_ranges,
                status="delete",
            )
            row = {
                "status": (
                    _difftastic_row_status_from_tokens(left_tokens)
                    if left_tokens
                    else "replace"
                ),
                "left_no": left_index + 1,
                "right_no": None,
                "left_text": left_lines[left_index],
                "right_text": "",
            }
            if left_tokens:
                row["left_tokens"] = left_tokens
            rows.append(row)
            used_left.add(left_index)
            continue

        assert right_index is not None
        right_line_ranges = right_ranges.get(right_index, [])
        candidate_left = _remove_line_ranges(
            right_lines[right_index], right_line_ranges
        )
        lower, upper = _difftastic_line_anchor_bounds(
            aligned_lines, aligned_index, side="left"
        )
        if right_index not in planned_left_window_rows:
            planned_window_rows = _difftastic_plan_right_only_residual_window(
                aligned_lines=aligned_lines,
                row_index=aligned_index,
                left_fragments=left_fragments,
                lower_bound=lower,
                upper_bound=upper,
                right_lines=right_lines,
                right_ranges=right_ranges,
            )
            if planned_window_rows is not None:
                planned_left_window_rows.update(planned_window_rows)
        planned_left_row = planned_left_window_rows.get(right_index)
        if planned_left_row is not None:
            planned_source_index, planned_left_text = planned_left_row
            row = _paired_line_row(
                planned_left_text,
                right_lines[right_index],
                planned_source_index + 1,
                right_index + 1,
            )
            if right_line_ranges:
                row["status"] = "replace"
                row["right_tokens"] = _changed_tokens_for_ranges(
                    right_lines[right_index],
                    right_line_ranges,
                    status="insert",
                )
                row.pop("left_tokens", None)
            rows.append(row)
            used_right.add(right_index)
            continue
        right_only_window_candidates = (
            _difftastic_right_only_window_candidate_texts(
                aligned_lines,
                aligned_index,
                right_lines,
                right_ranges,
            )
        )
        if right_line_ranges:
            reconstructed_left_index = _find_difftastic_reconstructed_line(
                left_lines,
                candidate_left,
                lower_bound=lower,
                upper_bound=upper,
                used=used_left,
            )
            if reconstructed_left_index is not None:
                row = _paired_line_row(
                    candidate_left,
                    right_lines[right_index],
                    reconstructed_left_index + 1,
                    right_index + 1,
                )
                row["status"] = "replace"
                row["right_tokens"] = _changed_tokens_for_ranges(
                    right_lines[right_index],
                    right_line_ranges,
                    status="insert",
                )
                row.pop("left_tokens", None)
                rows.append(row)
                used_left.add(reconstructed_left_index)
                used_right.add(right_index)
                continue
            reconstructed_left_fragment = (
                _find_difftastic_reconstructed_fragment(
                    left_fragments,
                    candidate_left,
                    lower_bound=lower,
                    upper_bound=upper,
                    used=used_left_fragments,
                    allow_assignment=False,
                    allow_syntax_only=False,
                    residual_candidate_window=right_only_window_candidates,
                )
            )
            if reconstructed_left_fragment is not None:
                row = _paired_line_row(
                    _collapse_reconstructed_gap_spaces(candidate_left),
                    right_lines[right_index],
                    reconstructed_left_fragment.source_index + 1,
                    right_index + 1,
                )
                row["status"] = "replace"
                row["right_tokens"] = _changed_tokens_for_ranges(
                    right_lines[right_index],
                    right_line_ranges,
                    status="insert",
                )
                row.pop("left_tokens", None)
                rows.append(row)
                used_left_fragments.add(
                    (
                        reconstructed_left_fragment.source_index,
                        _difftastic_line_item_key(candidate_left),
                    )
                )
                used_right.add(right_index)
                continue
        if not right_line_ranges:
            reconstructed_left_fragment = (
                _find_difftastic_reconstructed_fragment(
                    left_fragments,
                    candidate_left,
                    lower_bound=lower,
                    upper_bound=upper,
                    used=used_left_fragments,
                    allow_assignment=False,
                    allow_syntax_only=True,
                    residual_candidate_window=right_only_window_candidates,
                )
            )
            if reconstructed_left_fragment is not None:
                rows.append(
                    _paired_line_row(
                        _collapse_reconstructed_gap_spaces(candidate_left),
                        right_lines[right_index],
                        reconstructed_left_fragment.source_index + 1,
                        right_index + 1,
                    )
                )
                used_left_fragments.add(
                    _difftastic_fragment_key(reconstructed_left_fragment)
                )
                used_right.add(right_index)
                continue
        right_tokens = _changed_tokens_for_ranges(
            right_lines[right_index],
            right_line_ranges,
            status="insert",
        )
        rows.append(
            {
                "status": _difftastic_row_status_from_tokens(right_tokens),
                "left_no": None,
                "right_no": right_index + 1,
                "left_text": "",
                "right_text": right_lines[right_index],
                "right_tokens": right_tokens,
            }
        )
        used_right.add(right_index)

    return _repair_shifted_difftastic_replacements(rows)


def run_difftastic_json(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> dict[str, Any]:
    left_suffix = Path(left_path_hint or "left.txt").suffix or ".txt"
    right_suffix = Path(right_path_hint or left_path_hint or "right.txt").suffix
    right_suffix = right_suffix or left_suffix

    with tempfile.TemporaryDirectory(prefix="dirdiff-difftastic-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / f"left{left_suffix}"
        right_path = tmp / f"right{right_suffix}"
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")

        env = {
            **os.environ,
            "DFT_GRAPH_LIMIT": DFT_GRAPH_LIMIT,
            "DFT_UNSTABLE": "yes",
        }
        try:
            result = subprocess.run(
                [
                    "difft",
                    "--display",
                    "json",
                    "--context",
                    "100000000",
                    str(left_path),
                    str(right_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise TextDiffError(
                "Difftastic engine requires the `difft` executable on PATH."
            ) from exc

    if result.returncode != 0:
        raise TextDiffError(
            result.stderr.strip() or "Difftastic could not build this diff."
        )
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TextDiffError("Difftastic returned invalid JSON.") from exc

    if isinstance(parsed, list):
        if not parsed:
            return {"aligned_lines": [], "chunks": []}
        first = parsed[0]
        if isinstance(first, dict):
            return first
    if isinstance(parsed, dict):
        return parsed
    raise TextDiffError("Difftastic returned an unexpected JSON payload.")
