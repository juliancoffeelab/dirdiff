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

The lower-level `_difftastic_rows_from_json` takes an already produced
`DifftasticJson` plus the same complete source texts. Raw `dict[str, Any]` is not
accepted at this layer; unknown JSON enters through `difft.py`.

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
* row text must come from the supplied source text;
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
from typing import Any, Literal, TypedDict, cast

from dirdiff.services.difftastic.difft import (
    DifftasticJson,
    DifftasticJsonSideName,
    run_difftastic_json,
)
from dirdiff.services.textdiff import _paired_line_row

type DifftasticRowStatus = Literal["equal", "replace", "insert", "delete"]
type DifftasticTokenStatus = Literal["unchanged", "replace", "insert", "delete"]


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


def _row(value: object) -> DifftasticRow:
    return cast("DifftasticRow", value)


def _difftastic_paired_line_row(
    left_text: str,
    right_text: str,
    left_no: int,
    right_no: int,
) -> DifftasticRow:
    return _row(_paired_line_row(left_text, right_text, left_no, right_no))


def _row_string(row: DifftasticRow, key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


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
) -> list[DifftasticInlineToken]:
    if not ranges:
        return []

    tokens: list[DifftasticInlineToken] = []
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
) -> list[DifftasticInlineToken]:
    if not ranges:
        return []

    tokens: list[DifftasticInlineToken] = []
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


def _common_prefix_length_from_cursor(
    left: str,
    right: str,
    right_cursor: int,
) -> int:
    limit = min(len(left), len(right) - right_cursor)
    index = 0
    while index < limit and left[index] == right[right_cursor + index]:
        index += 1
    return index


def _mark_unmatched_reconstructed_token_tail(
    tokens: list[DifftasticInlineToken],
    *,
    counterpart_text: str,
    extra_status: Literal["insert", "delete"],
) -> list[DifftasticInlineToken]:
    normalized: list[DifftasticInlineToken] = []
    counterpart_cursor = 0
    for index, token in enumerate(tokens):
        token_text = token.get("text")
        if not isinstance(token_text, str):
            normalized.append(token)
            continue
        if token.get("status") != "unchanged":
            normalized.append(token)
            continue
        if index != len(tokens) - 1:
            found_at = counterpart_text.find(token_text, counterpart_cursor)
            if found_at >= 0:
                counterpart_cursor = found_at + len(token_text)
            else:
                counterpart_cursor += _common_prefix_length_from_cursor(
                    token_text,
                    counterpart_text,
                    counterpart_cursor,
                )
            normalized.append(token)
            continue

        shared_length = _common_prefix_length_from_cursor(
            token_text,
            counterpart_text,
            counterpart_cursor,
        )
        if shared_length == len(token_text):
            normalized.append(token)
            counterpart_cursor += shared_length
            continue
        if shared_length > 0:
            unchanged_text = token_text[:shared_length]
            normalized.append(
                {
                    **token,
                    "text": unchanged_text,
                    "is_ws": unchanged_text.isspace(),
                }
            )
            counterpart_cursor += shared_length
        extra_text = token_text[shared_length:]
        normalized.append(
            {
                **token,
                "text": extra_text,
                "status": extra_status,
                "is_ws": extra_text.isspace(),
            }
        )
    return normalized


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
    tokens: list[DifftasticInlineToken],
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


def _normalize_difftastic_replace_tokens_in_mixed_rows(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    normalized: list[DifftasticRow] = []
    for row in rows:
        left_tokens = row.get("left_tokens", [])
        right_tokens = row.get("right_tokens", [])
        all_tokens = [*left_tokens, *right_tokens]
        token_statuses = {token.get("status") for token in all_tokens}
        has_side_status_tokens = bool({"insert", "delete"} & token_statuses)
        if not (has_side_status_tokens and "replace" in token_statuses):
            normalized.append(row)
            continue

        next_row = _row(dict(row))
        next_row["left_tokens"] = [
            (
                {**token, "status": "delete"}
                if token.get("status") == "replace"
                else token
            )
            for token in left_tokens
        ]
        next_row["right_tokens"] = [
            (
                {**token, "status": "insert"}
                if token.get("status") == "replace"
                else token
            )
            for token in right_tokens
        ]
        normalized.append(next_row)
    return normalized


def _difftastic_semantic_words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", text)
        if word
    }


def _difftastic_unchanged_semantic_words(
    tokens: list[DifftasticInlineToken],
) -> set[str]:
    words: set[str] = set()
    for token in tokens:
        if token.get("status") != "unchanged":
            continue
        token_text = token.get("text")
        if not isinstance(token_text, str):
            continue
        words.update(_difftastic_semantic_words(token_text))
    return words


def _difftastic_changed_semantic_words(
    tokens: list[DifftasticInlineToken],
) -> set[str]:
    words: set[str] = set()
    for token in tokens:
        if token.get("status") == "unchanged":
            continue
        token_text = token.get("text")
        if not isinstance(token_text, str):
            continue
        words.update(_difftastic_semantic_words(token_text))
    return words


def _difftastic_changed_ranges_by_line(
    diff_json: DifftasticJson,
    *,
    side: DifftasticJsonSideName,
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
    diff_json: DifftasticJson,
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
    if len(token) >= 2 and token[0] in {'"', "'"} and token[-1] == token[0]:
        return False
    return not re.fullmatch(r"\w+", token, flags=re.UNICODE)


def _difftastic_tokens_are_syntax(tokens: list[str]) -> bool:
    return bool(tokens) and all(
        _difftastic_token_is_syntax(token) for token in tokens
    )


def _difftastic_tokens_are_assignment(tokens: list[str]) -> bool:
    return "=" in tokens


def _difftastic_semantic_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", text)


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

    return [*items[:cursor], "".join(reversed(trailing_syntax))]


def _difftastic_residual_mapping(
    *,
    right_index: int,
    source_index: int,
    right_lines: list[str],
    item: str,
    remainder: dict[int, tuple[int, str]],
) -> dict[int, tuple[int, str]]:
    return {
        right_index: (
            source_index,
            f"{_difftastic_leading_whitespace(right_lines[right_index])}{item}",
        ),
        **remainder,
    }


def _difftastic_semantic_subsequence_start(
    *,
    needles: list[str],
    haystack: list[str],
    cursor: int,
) -> int | None:
    last_start = len(haystack) - len(needles)
    for start in range(cursor, last_start + 1):
        if haystack[start : start + len(needles)] == needles:
            return start
    return None


def _difftastic_contextual_residual_mapping(
    *,
    fragment: DifftasticLineFragment,
    right_indexes: list[int],
    right_lines: list[str],
    right_ranges: dict[int, list[tuple[int, int]]],
) -> dict[int, tuple[int, str]] | None:
    fragment_semantics: list[str] = []
    if "||" in fragment.text:
        fragment_semantics = _difftastic_semantic_tokens(fragment.text)
    if not fragment_semantics:
        return None

    cursor = 0
    mappings: dict[int, tuple[int, str]] = {}
    for right_index in right_indexes:
        right_line = right_lines[right_index]
        candidate_text = _remove_line_ranges(
            right_line, right_ranges.get(right_index, [])
        )
        candidate_key = _difftastic_line_item_key(candidate_text)
        candidate_semantics = _difftastic_semantic_tokens(candidate_key)
        if not candidate_semantics:
            candidate_tokens = _difftastic_fragment_tokens(candidate_key)
            if (
                cursor == len(fragment_semantics)
                and _difftastic_tokens_are_syntax(candidate_tokens)
                and fragment.text.rstrip().endswith(candidate_key)
            ):
                mappings[right_index] = (
                    fragment.source_index,
                    f"{_difftastic_leading_whitespace(right_line)}{candidate_key}",
                )
            continue

        start = _difftastic_semantic_subsequence_start(
            needles=candidate_semantics,
            haystack=fragment_semantics,
            cursor=cursor,
        )
        if start is None:
            return None
        if start != cursor:
            return None

        cursor = start + len(candidate_semantics)
        mappings[right_index] = (
            fragment.source_index,
            f"{_difftastic_leading_whitespace(right_line)}{candidate_key}",
        )

    if cursor != len(fragment_semantics):
        return None
    if not mappings:
        return None
    return mappings


def _difftastic_match_right_only_residual_window(
    *,
    items: list[str],
    item_keys: list[str],
    row_candidates: list[tuple[int, str, bool]],
    source_index: int,
    right_lines: list[str],
    row_position: int,
    item_position: int,
) -> dict[int, tuple[int, str]] | None:
    if item_position == len(items):
        has_unmatched_key = any(
            candidate_key and not has_ranges
            for _, candidate_key, has_ranges in row_candidates[row_position:]
        )
        if has_unmatched_key:
            return None
        return {}

    if row_position == len(row_candidates):
        return None

    right_index, candidate_key, has_ranges = row_candidates[row_position]
    item = items[item_position]
    item_key = item_keys[item_position]
    matched: dict[int, tuple[int, str]] | None = None

    if candidate_key == item_key:
        remainder = _difftastic_match_right_only_residual_window(
            items=items,
            item_keys=item_keys,
            row_candidates=row_candidates,
            source_index=source_index,
            right_lines=right_lines,
            row_position=row_position + 1,
            item_position=item_position + 1,
        )
        if remainder is not None:
            matched = _difftastic_residual_mapping(
                right_index=right_index,
                source_index=source_index,
                right_lines=right_lines,
                item=item,
                remainder=remainder,
            )

    if matched is None and has_ranges:
        matched = _difftastic_match_right_only_residual_window(
            items=items,
            item_keys=item_keys,
            row_candidates=row_candidates,
            source_index=source_index,
            right_lines=right_lines,
            row_position=row_position + 1,
            item_position=item_position,
        )

    if matched is None and has_ranges:
        remainder = _difftastic_match_right_only_residual_window(
            items=items,
            item_keys=item_keys,
            row_candidates=row_candidates,
            source_index=source_index,
            right_lines=right_lines,
            row_position=row_position + 1,
            item_position=item_position + 1,
        )
        if remainder is not None:
            matched = _difftastic_residual_mapping(
                right_index=right_index,
                source_index=source_index,
                right_lines=right_lines,
                item=item,
                remainder=remainder,
            )

    return matched


def _difftastic_plan_right_only_residual_window(
    *,
    aligned_lines: list[DifftasticAlignedLine],
    row_index: int,
    left_fragments: list[DifftasticLineFragment],
    lower_bound: int,
    upper_bound: int,
    right_lines: list[str],
    right_ranges: dict[int, list[tuple[int, int]]],
) -> tuple[dict[int, tuple[int, str]], DifftasticLineFragment] | None:
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

        mappings = _difftastic_match_right_only_residual_window(
            items=items,
            item_keys=item_keys,
            row_candidates=row_candidates,
            source_index=fragment.source_index,
            right_lines=right_lines,
            row_position=0,
            item_position=0,
        )
        if mappings is None:
            continue

        return mappings, fragment

    for fragment in left_fragments:
        if fragment.kind != "residual":
            continue
        if not lower_bound <= fragment.source_index < upper_bound:
            continue

        mappings = _difftastic_contextual_residual_mapping(
            fragment=fragment,
            right_indexes=right_indexes,
            right_lines=right_lines,
            right_ranges=right_ranges,
        )
        if mappings is None:
            continue

        return mappings, fragment

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


def _delete_only_row(row: DifftasticRow) -> DifftasticRow:
    return {
        "status": "delete",
        "left_no": row.get("left_no"),
        "right_no": None,
        "left_text": row.get("left_text", ""),
        "right_text": "",
    }


def _insert_only_row(row: DifftasticRow) -> DifftasticRow:
    return {
        "status": "insert",
        "left_no": None,
        "right_no": row.get("right_no"),
        "left_text": "",
        "right_text": row.get("right_text", ""),
    }


def _equal_row_from_crossed_replacements(
    left_row: DifftasticRow,
    right_row: DifftasticRow,
) -> DifftasticRow:
    return {
        "status": "equal",
        "left_no": left_row.get("left_no"),
        "right_no": right_row.get("right_no"),
        "left_text": left_row.get("left_text", ""),
        "right_text": right_row.get("right_text", ""),
    }


def _replacement_row_from_shifted_delete_continuation(
    replace_row: DifftasticRow,
    delete_row: DifftasticRow,
) -> DifftasticRow:
    return {
        "status": "replace",
        "left_no": delete_row.get("left_no"),
        "right_no": replace_row.get("right_no"),
        "left_text": delete_row.get("left_text", ""),
        "right_text": replace_row.get("right_text", ""),
        "left_tokens": delete_row.get("left_tokens", []),
        "right_tokens": replace_row.get("right_tokens", []),
    }


def _left_delete_tokens_from_replace_row(
    tokens: list[DifftasticInlineToken],
) -> list[DifftasticInlineToken]:
    return [
        (
            {**token, "status": "delete"}
            if token.get("status") == "replace"
            else token
        )
        for token in tokens
    ]


def _replace_row_needs_following_delete_continuation(
    replace_row: DifftasticRow,
    delete_row: DifftasticRow,
) -> bool:
    left_no = replace_row.get("left_no")
    basic_shape_matches = (
        replace_row.get("status") == "replace"
        and delete_row.get("status") == "delete"
        and left_no is not None
        and replace_row.get("right_no") is not None
        and delete_row.get("left_no") == left_no + 1
    )
    if not basic_shape_matches:
        return False

    replace_right_tokens = replace_row.get("right_tokens", [])
    delete_left_tokens = delete_row.get("left_tokens", [])
    if not replace_right_tokens or not delete_left_tokens:
        return False

    shared_words = _difftastic_unchanged_semantic_words(
        replace_right_tokens
    ) & _difftastic_unchanged_semantic_words(delete_left_tokens)
    if not shared_words:
        return False

    return bool(_difftastic_changed_semantic_words(delete_left_tokens))


def _repair_shifted_difftastic_replacements(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    repaired: list[DifftasticRow] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        if next_row is not None and row.get("status") == "replace":
            if (
                next_row.get("status") == "replace"
                and row.get("right_text")
                and row.get("right_text") == next_row.get("left_text")
            ):
                repaired.append(_delete_only_row(row))
                repaired.append(
                    _equal_row_from_crossed_replacements(next_row, row)
                )
                repaired.append(_insert_only_row(next_row))
                index += 2
                continue
            if (
                next_row.get("status") == "replace"
                and row.get("left_text")
                and row.get("left_text") == next_row.get("right_text")
            ):
                repaired.append(_insert_only_row(row))
                repaired.append(
                    _equal_row_from_crossed_replacements(row, next_row)
                )
                repaired.append(_delete_only_row(next_row))
                index += 2
                continue
            if (
                next_row.get("status") == "delete"
                and row.get("right_text")
                and row.get("right_text") == next_row.get("left_text")
            ):
                repaired.append(_delete_only_row(row))
                repaired.append(
                    _equal_row_from_crossed_replacements(next_row, row)
                )
                index += 2
                continue
            if (
                next_row.get("status") == "insert"
                and row.get("left_text")
                and row.get("left_text") == next_row.get("right_text")
            ):
                repaired.append(_insert_only_row(row))
                repaired.append(
                    _equal_row_from_crossed_replacements(row, next_row)
                )
                index += 2
                continue
        repaired.append(row)
        index += 1
    return repaired


def _repair_replace_rows_with_delete_continuations(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    repaired: list[DifftasticRow] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        if (
            next_row is not None
            and _replace_row_needs_following_delete_continuation(row, next_row)
        ):
            repaired.append(
                {
                    "status": "delete",
                    "left_no": row.get("left_no"),
                    "right_no": None,
                    "left_text": row.get("left_text", ""),
                    "right_text": "",
                    "left_tokens": _left_delete_tokens_from_replace_row(
                        row.get("left_tokens", [])
                    ),
                }
            )
            repaired.append(
                _replacement_row_from_shifted_delete_continuation(row, next_row)
            )
            index += 2
            continue
        repaired.append(row)
        index += 1
    return repaired


def _is_left_only_row(row: DifftasticRow) -> bool:
    return row.get("left_no") is not None and row.get("right_no") is None


def _is_right_only_row(row: DifftasticRow) -> bool:
    return row.get("left_no") is None and row.get("right_no") is not None


def _one_sided_row_tokens(row: DifftasticRow) -> list[DifftasticInlineToken]:
    if _is_left_only_row(row):
        left_tokens = row.get("left_tokens", [])
        assert isinstance(left_tokens, list)
        return left_tokens
    if _is_right_only_row(row):
        right_tokens = row.get("right_tokens", [])
        assert isinstance(right_tokens, list)
        return right_tokens
    return []


def _difftastic_tokens_are_subsequence(
    needles: list[str],
    haystack: list[str],
) -> bool:
    cursor = 0
    for needle in needles:
        for index in range(cursor, len(haystack)):
            if haystack[index] == needle:
                cursor = index + 1
                break
        else:
            return False
    return True


def _difftastic_source_line_is_rendered(
    rows: list[DifftasticRow],
    *,
    source_line: str,
    source_index: int,
    side: Literal["left", "right"],
) -> bool:
    no_key = "left_no" if side == "left" else "right_no"
    text_key = "left_text" if side == "left" else "right_text"
    rendered_text = "".join(
        _row_string(row, text_key)
        for row in rows
        if row.get(no_key) == source_index + 1
    )
    return _difftastic_tokens_are_subsequence(
        _difftastic_fragment_tokens(source_line),
        _difftastic_fragment_tokens(rendered_text),
    )


def _one_sided_fragment_row(
    fragment: DifftasticLineFragment,
    *,
    side: Literal["left", "right"],
) -> DifftasticRow:
    status: Literal["delete", "insert"] = (
        "delete" if side == "left" else "insert"
    )
    tokens = _changed_tokens_for_ranges(
        fragment.text,
        [(0, len(fragment.text))],
        status=status,
    )
    if side == "left":
        return {
            "status": status,
            "left_no": fragment.source_index + 1,
            "right_no": None,
            "left_text": fragment.text,
            "right_text": "",
            "left_tokens": tokens,
        }
    return {
        "status": status,
        "left_no": None,
        "right_no": fragment.source_index + 1,
        "left_text": "",
        "right_text": fragment.text,
        "right_tokens": tokens,
    }


def _insert_unrendered_residual_fragments(
    rows: list[DifftasticRow],
    *,
    fragments: list[DifftasticLineFragment],
    source_lines: list[str],
    side: Literal["left", "right"],
    used_fragments: set[DifftasticLineFragment] | None = None,
) -> list[DifftasticRow]:
    no_key = "left_no" if side == "left" else "right_no"
    completed = rows
    for fragment in fragments:
        if fragment.kind != "residual":
            continue
        if used_fragments is not None and fragment in used_fragments:
            continue
        if not 0 <= fragment.source_index < len(source_lines):
            continue
        if _difftastic_source_line_is_rendered(
            completed,
            source_line=source_lines[fragment.source_index],
            source_index=fragment.source_index,
            side=side,
        ):
            continue

        insert_at = len(completed)
        for index, row in enumerate(completed):
            if row.get(no_key) == fragment.source_index + 1:
                insert_at = index + 1
        completed = [
            *completed[:insert_at],
            _one_sided_fragment_row(fragment, side=side),
            *completed[insert_at:],
        ]
    return completed


@dataclass(frozen=True)
class DifftasticTokenSpan:
    text: str
    start: int
    end: int


def _difftastic_code_token_spans(text: str) -> list[DifftasticTokenSpan]:
    return [
        DifftasticTokenSpan(
            text=match.group(0),
            start=match.start(),
            end=match.end(),
        )
        for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|\S", text)
        if not match.group(0).isspace()
    ]


def _difftastic_common_token_prefix_length(
    left: list[DifftasticTokenSpan],
    right: list[DifftasticTokenSpan],
) -> int:
    limit = min(len(left), len(right))
    cursor = 0
    while cursor < limit and left[cursor].text == right[cursor].text:
        cursor += 1
    return cursor


def _difftastic_text_through_token(
    text: str,
    spans: list[DifftasticTokenSpan],
    token_count: int,
) -> str:
    if token_count <= 0:
        return ""
    return text[: spans[token_count - 1].end]


def _difftastic_clip_tokens_to_text(
    tokens: list[DifftasticInlineToken],
    text: str,
) -> list[DifftasticInlineToken]:
    clipped: list[DifftasticInlineToken] = []
    cursor = 0
    limit = len(text)
    for token in tokens:
        token_text = token.get("text")
        if not isinstance(token_text, str):
            continue
        token_end = cursor + len(token_text)
        if token_end <= limit:
            clipped.append(token)
            cursor = token_end
            continue
        if cursor < limit:
            clipped_text = token_text[: limit - cursor]
            clipped.append(
                {
                    **token,
                    "text": clipped_text,
                    "is_ws": clipped_text.isspace(),
                }
            )
        break
    return clipped


def _difftastic_projected_fragment_text(
    *,
    reference_text: str,
    fragment_text: str,
) -> str:
    return f"{_difftastic_leading_whitespace(reference_text)}{fragment_text.strip()}"


def _difftastic_set_row_side(
    row: DifftasticRow,
    *,
    side: Literal["left", "right"],
    line_no: int,
    text: str,
) -> DifftasticRow:
    next_row = _row(dict(row))
    if side == "left":
        next_row["left_no"] = line_no
        next_row["left_text"] = text
        next_row.pop("left_tokens", None)
        return next_row
    next_row["right_no"] = line_no
    next_row["right_text"] = text
    next_row.pop("right_tokens", None)
    return next_row


def _difftastic_set_inserted_tail_tokens(
    row: DifftasticRow,
    *,
    side: Literal["left", "right"],
    extra_start: int,
) -> DifftasticRow:
    text = row.get("left_text") if side == "left" else row.get("right_text")
    if not isinstance(text, str):
        return row
    if extra_start >= len(text):
        next_row = _row(dict(row))
        if side == "left":
            next_row.pop("left_tokens", None)
        else:
            next_row.pop("right_tokens", None)
        return next_row
    next_row = _row(dict(row))
    tokens = _changed_tokens_for_ranges(
        text,
        [(extra_start, len(text))],
        status="insert" if side == "right" else "delete",
    )
    if side == "left":
        next_row["left_tokens"] = tokens
    else:
        next_row["right_tokens"] = tokens
    return next_row


def _difftastic_pair_wrapped_projection_rows_for_side(  # noqa: PLR0911
    rows: list[DifftasticRow],
    *,
    row_index: int,
    long_side: Literal["left", "right"],
) -> tuple[DifftasticRow | None, dict[int, DifftasticRow]]:
    short_side: Literal["left", "right"] = (
        "right" if long_side == "left" else "left"
    )
    long_text_key = "left_text" if long_side == "left" else "right_text"
    short_text_key = "left_text" if short_side == "left" else "right_text"
    long_no_key = "left_no" if long_side == "left" else "right_no"
    short_no_key = "left_no" if short_side == "left" else "right_no"
    long_tokens_key = "left_tokens" if long_side == "left" else "right_tokens"

    row = rows[row_index]
    long_text = row.get(long_text_key)
    short_text = row.get(short_text_key)
    long_no = row.get(long_no_key)
    if not isinstance(long_text, str):
        return None, {}
    if not isinstance(short_text, str):
        return None, {}
    if not isinstance(long_no, int):
        return None, {}

    long_spans = _difftastic_code_token_spans(long_text)
    short_spans = _difftastic_code_token_spans(short_text)
    if len(long_spans) <= len(short_spans):
        return None, {}
    prefix_count = len(short_spans)
    if prefix_count == 0:
        return None, {}

    clipped_long_text = _difftastic_text_through_token(
        long_text,
        long_spans,
        prefix_count,
    )
    if clipped_long_text == long_text:
        return None, {}

    raw_tokens = row.get(long_tokens_key)

    replacements: dict[int, DifftasticRow] = {}
    matched_rows: list[
        tuple[int, DifftasticRow, list[DifftasticTokenSpan], int, str]
    ] = []
    crosses_long_side_order = False
    cursor = prefix_count
    scan_index = row_index + 1
    while scan_index < len(rows) and cursor < len(long_spans):
        candidate = rows[scan_index]
        candidate_long_no = candidate.get(long_no_key)
        if candidate_long_no is not None:
            if candidate_long_no != long_no:
                candidate_short_text = candidate.get(short_text_key)
                if isinstance(candidate_short_text, str) and not (
                    _difftastic_code_token_spans(candidate_short_text)
                ):
                    crosses_long_side_order = True
                    scan_index += 1
                    continue
                break
            candidate_long_text = candidate.get(long_text_key)
            if not isinstance(candidate_long_text, str):
                break
            candidate_long_spans = _difftastic_code_token_spans(
                candidate_long_text
            )
            remaining_spans = long_spans[cursor:]
            match_count = _difftastic_common_token_prefix_length(
                remaining_spans,
                candidate_long_spans,
            )
            if match_count != len(candidate_long_spans):
                break
            cursor += match_count
            scan_index += 1
            continue
        if candidate.get(short_no_key) is None:
            scan_index += 1
            continue

        candidate_text = candidate.get(short_text_key)
        if not isinstance(candidate_text, str):
            scan_index += 1
            continue
        candidate_spans = _difftastic_code_token_spans(candidate_text)
        if not candidate_spans:
            scan_index += 1
            continue

        remaining_spans = long_spans[cursor:]
        match_count = _difftastic_common_token_prefix_length(
            remaining_spans,
            candidate_spans,
        )
        if match_count == 0:
            scan_index += 1
            continue

        fragment_start = long_spans[cursor].start
        fragment_end = long_spans[cursor + match_count - 1].end
        fragment_text = long_text[fragment_start:fragment_end]
        matched_rows.append(
            (scan_index, candidate, candidate_spans, match_count, fragment_text)
        )
        cursor += match_count
        scan_index += 1

    if cursor != len(long_spans):
        return None, {}

    next_row = _row(dict(row))
    if crosses_long_side_order:
        if isinstance(raw_tokens, list):
            if long_side == "left":
                next_row["left_tokens"] = _unchanged_tokens_for_text(long_text)
            else:
                next_row["right_tokens"] = _unchanged_tokens_for_text(long_text)
    else:
        if long_side == "left":
            next_row["left_text"] = clipped_long_text
        else:
            next_row["right_text"] = clipped_long_text
        if isinstance(raw_tokens, list):
            clipped_tokens = _difftastic_clip_tokens_to_text(
                raw_tokens,
                clipped_long_text,
            )
            if long_side == "left":
                next_row["left_tokens"] = clipped_tokens
            else:
                next_row["right_tokens"] = clipped_tokens

    for (
        matched_index,
        candidate,
        candidate_spans,
        match_count,
        fragment_text,
    ) in matched_rows:
        if crosses_long_side_order:
            replacement = _row(dict(candidate))
        else:
            candidate_text = candidate.get(short_text_key)
            if not isinstance(candidate_text, str):
                return None, {}
            projected_text = _difftastic_projected_fragment_text(
                reference_text=candidate_text,
                fragment_text=fragment_text,
            )
            replacement = _difftastic_set_row_side(
                candidate,
                side=long_side,
                line_no=long_no,
                text=projected_text,
            )
        if match_count == len(candidate_spans):
            replacement["status"] = "equal"
            replacement.pop("left_tokens", None)
            replacement.pop("right_tokens", None)
        else:
            replacement["status"] = "replace"
            replacement = _difftastic_set_inserted_tail_tokens(
                replacement,
                side=short_side,
                extra_start=candidate_spans[match_count].start,
            )
        replacements[matched_index] = replacement
    return next_row, replacements


def _pair_wrapped_difftastic_projection_rows(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    replacements: dict[int, DifftasticRow] = {}
    for index, row in enumerate(rows):
        if index in replacements:
            continue
        if row.get("status") != "replace":
            continue
        if row.get("left_no") is None:
            continue
        if row.get("right_no") is None:
            continue

        left_result, left_replacements = (
            _difftastic_pair_wrapped_projection_rows_for_side(
                rows,
                row_index=index,
                long_side="left",
            )
        )
        if left_result is not None:
            replacements[index] = left_result
            replacements.update(left_replacements)
            continue

        right_result, right_replacements = (
            _difftastic_pair_wrapped_projection_rows_for_side(
                rows,
                row_index=index,
                long_side="right",
            )
        )
        if right_result is not None:
            replacements[index] = right_result
            replacements.update(right_replacements)

    if not replacements:
        return rows
    return [replacements.get(index, row) for index, row in enumerate(rows)]


def _unchanged_tokens_for_text(text: str) -> list[DifftasticInlineToken]:
    return [
        {"text": part, "status": "unchanged", "is_ws": part.isspace()}
        for part in _difftastic_changed_token_parts(text)
    ]


def _normalize_redundant_left_residual_delete_rows(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    completed: list[DifftasticRow] = []
    for index, row in enumerate(rows):
        if _is_redundant_left_residual_delete_row(rows, index):
            left_text = row.get("left_text")
            assert isinstance(left_text, str)
            completed.append(
                {
                    **row,
                    "left_tokens": _unchanged_tokens_for_text(left_text),
                }
            )
            continue
        completed.append(row)
    return completed


def _is_redundant_left_residual_delete_row(
    rows: list[DifftasticRow],
    index: int,
) -> bool:
    row = rows[index]
    if row.get("status") != "delete":
        return False
    left_no = row.get("left_no")
    if not isinstance(left_no, int) or row.get("right_no") is not None:
        return False
    if not any(
        previous.get("left_no") == left_no
        and previous.get("right_no") is not None
        for previous in rows[:index]
    ):
        return False

    left_text = row.get("left_text")
    if not isinstance(left_text, str):
        return False
    left_words = _difftastic_semantic_words(left_text)
    if not left_words:
        return False

    right_words: set[str] = set()
    for next_row in rows[index + 1 :]:
        if next_row.get("left_no") is not None:
            break
        right_text = next_row.get("right_text")
        if isinstance(right_text, str):
            right_words.update(_difftastic_semantic_words(right_text))

    return left_words <= right_words


def _split_show_guard_residual(text: str) -> list[str] | None:
    stripped = text.strip()
    if not stripped.startswith("when={") or not stripped.endswith("}>"):
        return None

    body = stripped[len("when={") : -len("}>")]
    clauses: list[str] = []
    current: list[str] = []
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    index = 0
    while index < len(body):
        char = body[index]
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

        if (
            char == "&"
            and index + 1 < len(body)
            and body[index + 1] == "&"
            and paren_depth == bracket_depth == brace_depth == 0
        ):
            clause = "".join(current).strip()
            if clause:
                clauses.append(clause)
            current.clear()
            index += 2
            continue

        current.append(char)
        index += 1

    clause = "".join(current).strip()
    if clause:
        clauses.append(clause)
    if not clauses:
        return None

    return ["when={", *clauses, "}", ">"]


def _show_guard_condition_key(text: str) -> str:
    return text.strip().removesuffix("&&").strip()


def _show_guard_generated_text(
    *,
    other_text: str,
    residual_parts: list[str],
    clause_index: int,
) -> tuple[str | None, int]:
    stripped = other_text.strip()
    leading = _difftastic_leading_whitespace(other_text)
    if stripped == "when={" and residual_parts[0] == "when={":
        return f"{leading}when={{", clause_index
    if stripped in {"}", ">"}:
        if stripped in residual_parts:
            return f"{leading}{stripped}", clause_index
        return None, clause_index

    if clause_index >= len(residual_parts) - 2:
        return None, clause_index

    clause = residual_parts[clause_index]
    if _show_guard_condition_key(stripped) != clause:
        return None, clause_index

    suffix = " &&" if clause_index < len(residual_parts) - 3 else ""
    return f"{leading}{clause}{suffix}", clause_index + 1


def _tail_changed_ranges(longer: str, shorter: str) -> list[tuple[int, int]]:
    shared_length = _common_prefix_length(longer, shorter)
    if shared_length == len(longer):
        return []
    return [(shared_length, len(longer))]


def _paired_show_guard_residual_row(
    *,
    residual_row: DifftasticRow,
    other_row: DifftasticRow,
    residual_side: Literal["left", "right"],
    generated_text: str,
) -> DifftasticRow:
    other_side: Literal["left", "right"] = (
        "right" if residual_side == "left" else "left"
    )
    left_text = (
        generated_text
        if residual_side == "left"
        else other_row.get("left_text", "")
    )
    right_text = (
        generated_text
        if residual_side == "right"
        else other_row.get("right_text", "")
    )
    left_no = (
        residual_row.get("left_no")
        if residual_side == "left"
        else other_row.get("left_no")
    )
    right_no = (
        residual_row.get("right_no")
        if residual_side == "right"
        else other_row.get("right_no")
    )
    assert isinstance(left_text, str)
    assert isinstance(right_text, str)
    assert isinstance(left_no, int)
    assert isinstance(right_no, int)
    row = _difftastic_paired_line_row(
        left_text,
        right_text,
        left_no,
        right_no,
    )
    if left_text == right_text:
        return row

    row["status"] = "replace"
    if len(left_text) > len(right_text):
        row["left_tokens"] = _changed_tokens_for_ranges(
            left_text,
            _tail_changed_ranges(left_text, right_text),
            status="delete",
        )
        row.pop("right_tokens", None)
    else:
        row["right_tokens"] = _changed_tokens_for_ranges(
            right_text,
            _tail_changed_ranges(right_text, left_text),
            status="insert",
        )
        row.pop("left_tokens", None)
    if other_side == "left" and other_row.get("left_tokens"):
        row["left_tokens"] = other_row["left_tokens"]
    if other_side == "right" and other_row.get("right_tokens"):
        row["right_tokens"] = other_row["right_tokens"]
    return row


def _expand_show_guard_residual_rows(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    expanded: list[DifftasticRow] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if _is_left_only_row(row):
            residual_side: Literal["left", "right"] = "left"
            text = row.get("left_text", "")
        elif _is_right_only_row(row):
            residual_side = "right"
            text = row.get("right_text", "")
        else:
            expanded.append(row)
            index += 1
            continue

        residual_parts = _split_show_guard_residual(text)
        if residual_parts is None:
            expanded.append(row)
            index += 1
            continue

        other_side: Literal["left", "right"] = (
            "right" if residual_side == "left" else "left"
        )
        other_text_key = f"{other_side}_text"
        other_no_key = f"{other_side}_no"
        first_other_row = rows[index + 1] if index + 1 < len(rows) else None
        if (
            first_other_row is None
            or first_other_row.get(other_no_key) is None
            or _row_string(first_other_row, other_text_key).strip() != "when={"
        ):
            expanded.append(row)
            index += 1
            continue

        block_end = index + 1
        while block_end < len(rows):
            block_row = rows[block_end]
            if block_row.get(other_no_key) is None:
                break
            if (
                block_row.get(
                    "left_no" if residual_side == "left" else "right_no"
                )
                is not None
            ):
                break
            stripped = _row_string(block_row, other_text_key).strip()
            block_end += 1
            if stripped == ">":
                break

        if block_end == index + 1:
            expanded.append(row)
            index += 1
            continue

        clause_index = 1
        for other_row in rows[index + 1 : block_end]:
            generated_text, clause_index = _show_guard_generated_text(
                other_text=_row_string(other_row, other_text_key),
                residual_parts=residual_parts,
                clause_index=clause_index,
            )
            if generated_text is None:
                expanded.append(other_row)
                continue
            expanded.append(
                _paired_show_guard_residual_row(
                    residual_row=row,
                    other_row=other_row,
                    residual_side=residual_side,
                    generated_text=generated_text,
                )
            )
        index = block_end
    return expanded


def _one_sided_replace_should_use_side_status(
    rows: list[DifftasticRow], index: int
) -> bool:
    row = rows[index]
    if row.get("status") != "replace":
        return False

    if not _is_left_only_row(row):
        return False

    has_deleted_neighbor = False
    for neighbor_index in range(max(0, index - 2), min(len(rows), index + 3)):
        if neighbor_index == index:
            continue
        neighbor = rows[neighbor_index]
        if not _is_left_only_row(neighbor):
            continue

        neighbor_status = neighbor.get("status")
        if neighbor_status in {"delete", "insert"}:
            has_deleted_neighbor = True
            break
        if neighbor_status == "replace" and not _one_sided_row_tokens(neighbor):
            has_deleted_neighbor = True
            break
    if not has_deleted_neighbor:
        return False

    row_tokens = _one_sided_row_tokens(row)
    if not row_tokens:
        return True

    return len(_difftastic_changed_semantic_words(row_tokens)) > len(
        _difftastic_unchanged_semantic_words(row_tokens)
    )


def _normalize_one_sided_difftastic_replacements(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    normalized = [_row(dict(row)) for row in rows]
    while True:
        changed = False
        next_rows: list[DifftasticRow] = []
        for index, row in enumerate(normalized):
            next_row = _row(dict(row))
            if (
                _one_sided_replace_should_use_side_status(normalized, index)
                and next_row.get("status") != "delete"
            ):
                next_row["status"] = "delete"
                changed = True
            next_rows.append(next_row)
        if not changed:
            return next_rows
        normalized = next_rows


def _difftastic_context_atoms(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|\S", text)


def _difftastic_tokens_are_contiguous_subsequence(
    needles: list[str],
    haystack: list[str],
) -> bool:
    if len(needles) > len(haystack):
        return False
    last_start = len(haystack) - len(needles)
    for start in range(last_start + 1):
        if haystack[start : start + len(needles)] == needles:
            return True
    return False


def _difftastic_row_is_changed_group_member(row: DifftasticRow) -> bool:
    if row.get("status") != "equal":
        return True
    left_tokens = row.get("left_tokens")
    if isinstance(left_tokens, list) and left_tokens:
        return True
    right_tokens = row.get("right_tokens")
    if isinstance(right_tokens, list) and right_tokens:
        return True
    if row.get("left_no") is None and row.get("right_no") is not None:
        return True
    return row.get("right_no") is None and row.get("left_no") is not None


def _difftastic_changed_row_groups(
    rows: list[DifftasticRow],
) -> list[list[tuple[int, DifftasticRow]]]:
    groups: list[list[tuple[int, DifftasticRow]]] = []
    current: list[tuple[int, DifftasticRow]] = []
    for index, row in enumerate(rows):
        if _difftastic_row_is_changed_group_member(row):
            current.append((index, row))
            continue
        if current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _difftastic_changed_atoms_for_side(
    group: list[tuple[int, DifftasticRow]],
    *,
    side: Literal["left", "right"],
) -> list[str]:
    tokens_key = "left_tokens" if side == "left" else "right_tokens"
    atoms: list[str] = []
    for _index, row in group:
        raw_tokens = row.get(tokens_key)
        if not isinstance(raw_tokens, list):
            continue
        for token in raw_tokens:
            if not isinstance(token, dict):
                continue
            if token.get("status") == "unchanged":
                continue
            text = token.get("text")
            if isinstance(text, str):
                atoms.extend(_difftastic_context_atoms(text))
    return atoms


def _difftastic_one_sided_equal_context_atoms(
    row: DifftasticRow,
    *,
    side: Literal["left", "right"],
) -> list[str]:
    if row.get("status") != "equal":
        return []
    if side == "left":
        side_no = row.get("left_no")
        other_side_no = row.get("right_no")
        text = row.get("left_text")
    else:
        side_no = row.get("right_no")
        other_side_no = row.get("left_no")
        text = row.get("right_text")
    if side_no is None:
        return []
    if other_side_no is not None:
        return []
    if not isinstance(text, str):
        return []
    return _difftastic_context_atoms(text)


def _difftastic_leaky_context_row_indexes(
    rows: list[DifftasticRow],
) -> set[int]:
    leaky_indexes: set[int] = set()
    for group in _difftastic_changed_row_groups(rows):
        left_changed_atoms = _difftastic_changed_atoms_for_side(
            group,
            side="left",
        )
        right_changed_atoms = _difftastic_changed_atoms_for_side(
            group,
            side="right",
        )
        for index, row in group:
            left_context_atoms = _difftastic_one_sided_equal_context_atoms(
                row,
                side="left",
            )
            if (
                left_context_atoms
                and _difftastic_tokens_are_contiguous_subsequence(
                    left_context_atoms,
                    right_changed_atoms,
                )
            ):
                leaky_indexes.add(index)
            right_context_atoms = _difftastic_one_sided_equal_context_atoms(
                row,
                side="right",
            )
            if (
                right_context_atoms
                and _difftastic_tokens_are_contiguous_subsequence(
                    right_context_atoms,
                    left_changed_atoms,
                )
            ):
                leaky_indexes.add(index)
    return leaky_indexes


def _normalize_leaky_difftastic_context_rows(
    rows: list[DifftasticRow],
) -> list[DifftasticRow]:
    leaky_indexes = _difftastic_leaky_context_row_indexes(rows)
    if not leaky_indexes:
        return rows

    normalized: list[DifftasticRow] = []
    for index, row in enumerate(rows):
        if index in leaky_indexes:
            if row.get("left_no") is not None and row.get("right_no") is None:
                next_row = _row({**row, "status": "delete"})
                left_text = row.get("left_text")
                if isinstance(left_text, str):
                    next_row["left_tokens"] = _changed_tokens_for_ranges(
                        left_text,
                        [(0, len(left_text))],
                        status="delete",
                    )
                normalized.append(next_row)
                continue
            if row.get("right_no") is not None and row.get("left_no") is None:
                next_row = _row({**row, "status": "insert"})
                right_text = row.get("right_text")
                if isinstance(right_text, str):
                    next_row["right_tokens"] = _changed_tokens_for_ranges(
                        right_text,
                        [(0, len(right_text))],
                        status="insert",
                    )
                normalized.append(next_row)
                continue
            next_row = _row({**row, "status": "replace"})
            normalized.append(next_row)
            continue
        normalized.append(row)
    return normalized


def _difftastic_rows_from_json(
    diff_json: DifftasticJson,
    *,
    left_text: str,
    right_text: str,
) -> list[DifftasticRow]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    left_ranges = _difftastic_changed_ranges_by_line(diff_json, side="lhs")
    right_ranges = _difftastic_changed_ranges_by_line(diff_json, side="rhs")
    aligned_lines = _difftastic_aligned_lines(
        diff_json.get("aligned_lines", [])
    )
    if not aligned_lines:
        return []

    rows: list[DifftasticRow] = []
    used_left: set[int] = set()
    used_right: set[int] = set()
    left_fragments: list[DifftasticLineFragment] = []
    right_fragments: list[DifftasticLineFragment] = []
    used_left_fragments: set[tuple[int, str]] = set()
    used_right_fragments: set[tuple[int, str]] = set()
    used_left_residual_fragments: set[DifftasticLineFragment] = set()
    used_right_residual_fragments: set[DifftasticLineFragment] = set()
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
            row = _difftastic_paired_line_row(
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
                    row = _difftastic_paired_line_row(
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
                    row = _difftastic_paired_line_row(
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
                        _difftastic_paired_line_row(
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
                planned_rows, planned_fragment = planned_window_rows
                planned_left_window_rows.update(planned_rows)
                used_left_residual_fragments.add(planned_fragment)
        planned_left_row = planned_left_window_rows.get(right_index)
        if planned_left_row is not None:
            planned_source_index, planned_left_text = planned_left_row
            row = _difftastic_paired_line_row(
                planned_left_text,
                right_lines[right_index],
                planned_source_index + 1,
                right_index + 1,
            )
            if right_line_ranges:
                row["status"] = "replace"
                row["right_tokens"] = _mark_unmatched_reconstructed_token_tail(
                    _changed_tokens_for_ranges(
                        right_lines[right_index],
                        right_line_ranges,
                        status="insert",
                    ),
                    counterpart_text=planned_left_text,
                    extra_status="insert",
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
                row = _difftastic_paired_line_row(
                    candidate_left,
                    right_lines[right_index],
                    reconstructed_left_index + 1,
                    right_index + 1,
                )
                row["status"] = "replace"
                row["right_tokens"] = _mark_unmatched_reconstructed_token_tail(
                    _changed_tokens_for_ranges(
                        right_lines[right_index],
                        right_line_ranges,
                        status="insert",
                    ),
                    counterpart_text=candidate_left,
                    extra_status="insert",
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
                collapsed_candidate_left = _collapse_reconstructed_gap_spaces(
                    candidate_left
                )
                row = _difftastic_paired_line_row(
                    collapsed_candidate_left,
                    right_lines[right_index],
                    reconstructed_left_fragment.source_index + 1,
                    right_index + 1,
                )
                row["status"] = "replace"
                row["right_tokens"] = _mark_unmatched_reconstructed_token_tail(
                    _changed_tokens_for_ranges(
                        right_lines[right_index],
                        right_line_ranges,
                        status="insert",
                    ),
                    counterpart_text=collapsed_candidate_left,
                    extra_status="insert",
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
                    _difftastic_paired_line_row(
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

    rows = _insert_unrendered_residual_fragments(
        rows,
        fragments=left_fragments,
        source_lines=left_lines,
        side="left",
        used_fragments=used_left_residual_fragments,
    )
    rows = _insert_unrendered_residual_fragments(
        rows,
        fragments=right_fragments,
        source_lines=right_lines,
        side="right",
        used_fragments=used_right_residual_fragments,
    )
    rows = _pair_wrapped_difftastic_projection_rows(rows)
    rows = _normalize_redundant_left_residual_delete_rows(rows)
    rows = _expand_show_guard_residual_rows(rows)
    rows = _normalize_leaky_difftastic_context_rows(rows)

    return _normalize_difftastic_replace_tokens_in_mixed_rows(
        _repair_replace_rows_with_delete_continuations(
            _normalize_one_sided_difftastic_replacements(
                _repair_shifted_difftastic_replacements(rows)
            )
        )
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
