from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffSummary,
    EngineWarning,
    InlineToken,
    InlineTokenStatus,
)

__all__ = [
    "TokenDiffEngine",
]

_ATOM_PATTERN = re.compile(
    r"\n"
    r"|[^\S\n]+"
    r"|\d+"
    r"|_+"
    r"|[A-Z]+(?![a-z])"
    r"|[A-Z]?[a-z]+"
    r"|[^\W\d_]+"
    r"|[^\w\s]"
)
r"""Split source text into the ordered atoms consumed by `_region_rows`.

The alternatives recognize newlines, other whitespace runs, digits,
underscores, acronym and ASCII word parts, remaining Unicode word runs, and
single punctuation characters. Their order is significant: the acronym branch
splits `HTTPServer` into `HTTP` and `Server`, while the underscore and digit
branches split `value_2` into three atoms.

Every alternative consumes at least one character. Together they cover the
complete input, so joining all matches must reproduce the original text.

# Examples

>>> before = "retry_count = parseHTTPServer(userID, 42)\n"
>>> after = "retry_count = parseHTTPServer(userID, 43)\n"
>>> _ATOM_PATTERN.findall(before)  # doctest: +NORMALIZE_WHITESPACE
['retry', '_', 'count', ' ', '=', ' ', 'parse', 'HTTP', 'Server', '(',
 'user', 'ID', ',', ' ', '42', ')', '\n']
>>> _ATOM_PATTERN.findall(after)  # doctest: +NORMALIZE_WHITESPACE
['retry', '_', 'count', ' ', '=', ' ', 'parse', 'HTTP', 'Server', '(',
 'user', 'ID', ',', ' ', '43', ')', '\n']

# Lossless join invariant

>>> source = "userID = 42\n"
>>> atoms = _ATOM_PATTERN.findall(source)
>>> "".join(atoms) == source
True

# Warning

ASCII word branches run before the remaining Unicode word branch. Identifiers
that mix ASCII and non-ASCII letters may therefore split asymmetrically:

>>> _ATOM_PATTERN.findall("café = Δvalue\n")
['caf', 'é', ' ', '=', ' ', 'Δvalue', '\n']
"""

# Max left-atoms x right-atoms product matched per changed region. Above it the
# region renders as one-sided rows with a warning (multi-megabyte single-line
# documents would take seconds to match for an unreadable payload).
_REGION_ATOM_LIMIT = 4_000_000

# Largest matched word text (characters) a stranded island may hold and still be
# demoted; matched separators and whitespace never count and never demote.
_MAX_DEMOTED_MATCH_TEXT = 3

type _TokenPiece = tuple[str, InlineTokenStatus]


@dataclass
class _Op:
    left: str | None

    right: str | None

    # Text-equal pair deliberately shown as changed; set only by the demotion
    # pass, never alters the texts, so replay of either side still holds.
    demoted: bool = False


def _split_lines(text: str) -> list[str]:
    if text == "":
        return []
    # The empty tail after a trailing newline is not a line.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _region_ops(left_atoms: list[str], right_atoms: list[str]) -> list[_Op]:
    # Maximal word-atom runs match as single units so a fragment of one
    # identifier never matches inside another; punctuation matches per
    # character; whitespace (newlines included) is junk to the matcher and
    # pairs positionally between matched neighbors.

    def word_like(atom: str) -> bool:
        return not atom.isspace() and (atom[0].isalnum() or atom[0] == "_")

    def to_elements(atoms: list[str]) -> list[list[str]]:
        elements: list[list[str]] = []
        chunk_open = False
        for atom in atoms:
            if word_like(atom):
                if chunk_open:
                    elements[-1].append(atom)
                else:
                    elements.append([atom])
                chunk_open = True
            else:
                elements.append([atom])
                chunk_open = False
        return elements

    def paired_element_ops(
        left_element: list[str],
        right_element: list[str],
    ) -> list[_Op]:
        left_text = "".join(left_element)
        right_text = "".join(right_element)
        if left_text == right_text:
            return [_Op(left_text, right_text)]
        if left_text.isspace() and right_text.isspace():
            # Both whitespace: keep the shared frame unchanged so an
            # indentation change marks exactly the characters that appeared
            # or vanished.
            limit = min(len(left_text), len(right_text))
            prefix = 0
            while prefix < limit and left_text[prefix] == right_text[prefix]:
                prefix += 1
            suffix = 0
            while (
                suffix < limit - prefix
                and left_text[len(left_text) - 1 - suffix]
                == right_text[len(right_text) - 1 - suffix]
            ):
                suffix += 1
            trimmed: list[_Op] = []
            if prefix > 0:
                trimmed.append(_Op(left_text[:prefix], left_text[:prefix]))
            left_middle = left_text[prefix : len(left_text) - suffix]
            right_middle = right_text[prefix : len(right_text) - suffix]
            if left_middle != "" or right_middle != "":
                trimmed.append(
                    _Op(
                        left_middle if left_middle != "" else None,
                        right_middle if right_middle != "" else None,
                    )
                )
            if suffix > 0:
                trimmed.append(_Op(left_text[-suffix:], left_text[-suffix:]))
            return trimmed
        if word_like(left_element[0]) and word_like(right_element[0]):
            # A replaced identifier pair re-diffs over its parts so a rename
            # shows only the changed segments, snake/Camel joints included.
            parts: list[_Op] = []
            part_matcher = SequenceMatcher(
                a=left_element,
                b=right_element,
                autojunk=False,
            )
            for tag, i1, i2, j1, j2 in part_matcher.get_opcodes():
                if tag == "equal":
                    for part_left, part_right in zip(
                        left_element[i1:i2],
                        right_element[j1:j2],
                        strict=True,
                    ):
                        parts.append(_Op(part_left, part_right))
                elif tag == "delete":
                    for part in left_element[i1:i2]:
                        parts.append(_Op(part, None))
                elif tag == "insert":
                    for part in right_element[j1:j2]:
                        parts.append(_Op(None, part))
                else:
                    shared = min(i2 - i1, j2 - j1)
                    for offset in range(shared):
                        parts.append(
                            _Op(
                                left_element[i1 + offset],
                                right_element[j1 + offset],
                            )
                        )
                    for part in left_element[i1 + shared : i2]:
                        parts.append(_Op(part, None))
                    for part in right_element[j1 + shared : j2]:
                        parts.append(_Op(None, part))
            return parts
        return [_Op(left_text, right_text)]

    left_elements = to_elements(left_atoms)
    right_elements = to_elements(right_atoms)
    left_keys = [
        "" if element[0].isspace() else "".join(element)
        for element in left_elements
    ]
    right_keys = [
        "" if element[0].isspace() else "".join(element)
        for element in right_elements
    ]
    matcher = SequenceMatcher(
        isjunk=lambda key: key == "",
        a=left_keys,
        b=right_keys,
        autojunk=False,
    )
    ops: list[_Op] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for left_element, right_element in zip(
                left_elements[i1:i2],
                right_elements[j1:j2],
                strict=True,
            ):
                ops.extend(paired_element_ops(left_element, right_element))
        elif tag == "delete":
            for element in left_elements[i1:i2]:
                ops.append(_Op("".join(element), None))
        elif tag == "insert":
            for element in right_elements[j1:j2]:
                ops.append(_Op(None, "".join(element)))
        else:
            left_slice = left_elements[i1:i2]
            right_slice = right_elements[j1:j2]
            shared = min(len(left_slice), len(right_slice))
            for offset in range(shared):
                ops.extend(
                    paired_element_ops(
                        left_slice[offset],
                        right_slice[offset],
                    )
                )
            for element in left_slice[shared:]:
                ops.append(_Op("".join(element), None))
            for element in right_slice[shared:]:
                ops.append(_Op(None, "".join(element)))
    return ops


def _demote_isolated_matches(ops: list[_Op]) -> None:

    def content_changed(op: _Op) -> bool:
        if op.left is not None and op.left == op.right and not op.demoted:
            return False
        present = [text for text in (op.left, op.right) if text is not None]
        return any(not text.isspace() for text in present)

    def word_match(op: _Op) -> bool:
        return (
            op.left is not None
            and op.left == op.right
            and not op.left.isspace()
            and (op.left[0].isalnum() or op.left[0] == "_")
        )

    island: list[_Op] = []
    after_change = False
    for op in ops:
        if not content_changed(op):
            if after_change:
                island.append(op)
            continue
        matched = sum(
            len(member.left)
            for member in island
            if word_match(member) and member.left is not None
        )
        if 0 < matched <= _MAX_DEMOTED_MATCH_TEXT:
            for member in island:
                if word_match(member):
                    member.demoted = True
        island = []
        after_change = True
    # An island touching the region edge is not surrounded by changes and
    # keeps its matches, so the loop ends without demoting the remainder.


def _pair_lines(
    weights: dict[tuple[int, int], int],
    left_count: int,
    right_count: int,
) -> list[tuple[int, int]]:
    if weights == {}:
        return []
    scores = [[0] * (right_count + 1) for _ in range(left_count + 1)]
    decisions = [["skip_left"] * right_count for _ in range(left_count)]
    for left_index in range(left_count - 1, -1, -1):
        for right_index in range(right_count - 1, -1, -1):
            best = scores[left_index + 1][right_index]
            decision = "skip_left"
            if scores[left_index][right_index + 1] > best:
                best = scores[left_index][right_index + 1]
                decision = "skip_right"
            weight = weights.get((left_index, right_index), 0)
            if weight > 0:
                pair_score = weight + scores[left_index + 1][right_index + 1]
                # Prefer pairing on ties so shared text stays on the
                # earliest rows instead of drifting to a later line.
                if pair_score >= best:
                    best = pair_score
                    decision = "pair"
            scores[left_index][right_index] = best
            decisions[left_index][right_index] = decision

    pairs: list[tuple[int, int]] = []
    left_index = 0
    right_index = 0
    while left_index < left_count and right_index < right_count:
        decision = decisions[left_index][right_index]
        if decision == "pair":
            pairs.append((left_index, right_index))
            left_index += 1
            right_index += 1
        elif decision == "skip_left":
            left_index += 1
        else:
            right_index += 1
    return pairs


def _merge_tokens(pieces: list[_TokenPiece]) -> list[InlineToken]:
    merged: list[_TokenPiece] = []
    for text, status in pieces:
        if merged != [] and merged[-1][1] == status:
            merged[-1] = (merged[-1][0] + text, status)
        else:
            merged.append((text, status))
    return [
        {"text": text, "status": status, "is_ws": text.isspace()}
        for text, status in merged
    ]


def _anchored_line_row(
    left_line: str,
    right_line: str,
    left_no: int,
    right_no: int,
) -> DiffEngineRow:
    if left_line == right_line:
        return {
            "status": "equal",
            "left_no": left_no,
            "right_no": right_no,
            "left_text": left_line,
            "right_text": right_line,
            "left_tokens": [],
            "right_tokens": [],
        }
    stripped = left_line.lstrip()
    assert stripped == right_line.lstrip(), "anchored lines lost their anchor"
    left_lead = left_line[: len(left_line) - len(stripped)]
    right_lead = right_line[: len(right_line) - len(stripped)]
    limit = min(len(left_lead), len(right_lead))
    prefix = 0
    while prefix < limit and left_lead[prefix] == right_lead[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and left_lead[len(left_lead) - 1 - suffix]
        == right_lead[len(right_lead) - 1 - suffix]
    ):
        suffix += 1
    left_middle = left_lead[prefix : len(left_lead) - suffix]
    right_middle = right_lead[prefix : len(right_lead) - suffix]

    def lead_pieces(
        middle: str, one_sided: Literal["delete", "insert"]
    ) -> list[_TokenPiece]:
        pieces: list[_TokenPiece] = []
        if prefix > 0:
            pieces.append((left_lead[:prefix], "unchanged"))
        if middle != "":
            both_present = left_middle != "" and right_middle != ""
            pieces.append((middle, "replace" if both_present else one_sided))
        tail = (left_lead[len(left_lead) - suffix :] if suffix > 0 else "") + (
            stripped
        )
        if tail != "":
            pieces.append((tail, "unchanged"))
        return pieces

    return {
        "status": "equal",
        "left_no": left_no,
        "right_no": right_no,
        "left_text": left_line,
        "right_text": right_line,
        "left_tokens": _merge_tokens(lead_pieces(left_middle, "delete")),
        "right_tokens": _merge_tokens(lead_pieces(right_middle, "insert")),
    }


def _one_sided_row(
    status: Literal["delete", "insert"], line_no: int, text: str
) -> DiffEngineRow:
    is_left = status == "delete"
    return {
        "status": status,
        "left_no": line_no if is_left else None,
        "right_no": None if is_left else line_no,
        "left_text": text if is_left else "",
        "right_text": "" if is_left else text,
        "left_tokens": [],
        "right_tokens": [],
    }


def _op_lines(
    ops: list[_Op],
) -> tuple[list[tuple[int | None, int | None]], int, int]:
    # Region-local line of each op side, then the number of newlines seen on
    # each side. The terminating newline belongs to the line it ends.
    op_lines: list[tuple[int | None, int | None]] = []
    left_line = 0
    right_line = 0
    for op in ops:
        op_lines.append(
            (
                left_line if op.left is not None else None,
                right_line if op.right is not None else None,
            )
        )
        if op.left == "\n":
            left_line += 1
        if op.right == "\n":
            right_line += 1
    return op_lines, left_line, right_line


def _line_pair_weights(
    ops: list[_Op],
    op_lines: list[tuple[int | None, int | None]],
) -> dict[tuple[int, int], int]:
    weights: dict[tuple[int, int], int] = {}
    for index, op in enumerate(ops):
        if (
            op.demoted
            or op.left is None
            or op.left != op.right
            or op.left.isspace()
        ):
            continue
        line_left, line_right = op_lines[index]
        assert line_left is not None and line_right is not None
        key = (line_left, line_right)
        weights[key] = weights.get(key, 0) + len(op.left)
    return weights


def _line_pieces(
    ops: list[_Op],
    op_lines: list[tuple[int | None, int | None]],
    *,
    paired: set[tuple[int, int]],
    left_count: int,
    right_count: int,
) -> tuple[list[list[_TokenPiece]], list[list[_TokenPiece]]]:
    # A match whose endpoints are not on rows paired with each other is
    # demoted here to a one-sided pair, keeping every one-sided row entirely
    # its own status; newline steps shape lines but are never rendered as
    # tokens.
    left_line_pieces: list[list[_TokenPiece]] = [[] for _ in range(left_count)]
    right_line_pieces: list[list[_TokenPiece]] = [
        [] for _ in range(right_count)
    ]
    for index, op in enumerate(ops):
        line_left, line_right = op_lines[index]
        kept = (
            op.left is not None
            and op.right is not None
            and not op.demoted
            and (line_left, line_right) in paired
        )
        if op.left is not None and op.left != "\n":
            assert line_left is not None
            if kept:
                left_status: InlineTokenStatus = (
                    "unchanged" if op.left == op.right else "replace"
                )
            else:
                left_status = "delete"
            left_line_pieces[line_left].append((op.left, left_status))
        if op.right is not None and op.right != "\n":
            assert line_right is not None
            if kept:
                right_status: InlineTokenStatus = (
                    "unchanged" if op.left == op.right else "replace"
                )
            else:
                right_status = "insert"
            right_line_pieces[line_right].append((op.right, right_status))
    return left_line_pieces, right_line_pieces


def _paired_region_rows(
    pairs: list[tuple[int, int]],
    left_line_pieces: list[list[_TokenPiece]],
    right_line_pieces: list[list[_TokenPiece]],
    *,
    left_no: int,
    right_no: int,
) -> list[DiffEngineRow]:
    rows: list[DiffEngineRow] = []
    left_cursor = 0
    right_cursor = 0

    def append_unpaired(left_end: int, right_end: int) -> None:
        for line in range(left_cursor, left_end):
            pieces = left_line_pieces[line]
            assert all(status == "delete" for _, status in pieces), (
                "unpaired left row kept shared text"
            )
            text = "".join(text for text, _ in pieces)
            rows.append(_one_sided_row("delete", left_no + line, text))
        for line in range(right_cursor, right_end):
            pieces = right_line_pieces[line]
            assert all(status == "insert" for _, status in pieces), (
                "unpaired right row kept shared text"
            )
            text = "".join(text for text, _ in pieces)
            rows.append(_one_sided_row("insert", right_no + line, text))

    for pair_left, pair_right in pairs:
        append_unpaired(pair_left, pair_right)
        left_pieces = left_line_pieces[pair_left]
        right_pieces = right_line_pieces[pair_right]
        has_change = any(
            status != "unchanged" for _, status in left_pieces + right_pieces
        )
        content_changed = any(
            status != "unchanged" and not text.isspace()
            for text, status in left_pieces + right_pieces
        )
        rows.append(
            {
                "status": "replace" if content_changed else "equal",
                "left_no": left_no + pair_left,
                "right_no": right_no + pair_right,
                "left_text": "".join(text for text, _ in left_pieces),
                "right_text": "".join(text for text, _ in right_pieces),
                "left_tokens": _merge_tokens(left_pieces) if has_change else [],
                "right_tokens": (
                    _merge_tokens(right_pieces) if has_change else []
                ),
            }
        )
        left_cursor = pair_left + 1
        right_cursor = pair_right + 1
    append_unpaired(len(left_line_pieces), len(right_line_pieces))
    return rows


def _region_rows(
    left_region: str,
    right_region: str,
    *,
    left_no: int,
    right_no: int,
) -> tuple[list[DiffEngineRow], bool]:
    def tokenize(text: str) -> list[str]:
        atoms = _ATOM_PATTERN.findall(text)
        assert sum(map(len, atoms)) == len(text), (
            "atom pattern skipped characters"
        )
        return atoms

    left_atoms = tokenize(left_region)
    right_atoms = tokenize(right_region)
    if len(left_atoms) * len(right_atoms) > _REGION_ATOM_LIMIT:
        return [
            *(
                _one_sided_row("delete", left_no + offset, line)
                for offset, line in enumerate(_split_lines(left_region))
            ),
            *(
                _one_sided_row("insert", right_no + offset, line)
                for offset, line in enumerate(_split_lines(right_region))
            ),
        ], True

    ops = _region_ops(left_atoms, right_atoms)
    _demote_isolated_matches(ops)
    op_lines, left_newlines, right_newlines = _op_lines(ops)
    left_count = left_newlines + (0 if left_region.endswith("\n") else 1)
    right_count = right_newlines + (0 if right_region.endswith("\n") else 1)
    pairs = _pair_lines(
        _line_pair_weights(ops, op_lines), left_count, right_count
    )
    left_line_pieces, right_line_pieces = _line_pieces(
        ops,
        op_lines,
        paired=set(pairs),
        left_count=left_count,
        right_count=right_count,
    )
    rows = _paired_region_rows(
        pairs,
        left_line_pieces,
        right_line_pieces,
        left_no=left_no,
        right_no=right_no,
    )
    return rows, False


def _build_token_rows(
    left_text: str,
    right_text: str,
) -> tuple[list[DiffEngineRow], EngineWarning | None]:
    left_lines = _split_lines(left_text)
    right_lines = _split_lines(right_text)
    rows: list[DiffEngineRow] = []
    warning: EngineWarning | None = None
    left_no = 1
    right_no = 1
    matcher = SequenceMatcher(
        a=[line.lstrip() for line in left_lines],
        b=[line.lstrip() for line in right_lines],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for left_line, right_line in zip(
                left_lines[i1:i2],
                right_lines[j1:j2],
                strict=True,
            ):
                rows.append(
                    _anchored_line_row(
                        left_line,
                        right_line,
                        left_no,
                        right_no,
                    )
                )
                left_no += 1
                right_no += 1
        elif tag == "delete":
            for line in left_lines[i1:i2]:
                rows.append(
                    {
                        "status": "delete",
                        "left_no": left_no,
                        "right_no": None,
                        "left_text": line,
                        "right_text": "",
                        "left_tokens": [],
                        "right_tokens": [],
                    }
                )
                left_no += 1
        elif tag == "insert":
            for line in right_lines[j1:j2]:
                rows.append(
                    {
                        "status": "insert",
                        "left_no": None,
                        "right_no": right_no,
                        "left_text": "",
                        "right_text": line,
                        "left_tokens": [],
                        "right_tokens": [],
                    }
                )
                right_no += 1
        else:
            # A terminating newline is part of its preceding line; only the
            # document's final line can lack one.
            left_region = "\n".join(left_lines[i1:i2])
            if i2 < len(left_lines) or left_text.endswith("\n"):
                left_region += "\n"
            right_region = "\n".join(right_lines[j1:j2])
            if j2 < len(right_lines) or right_text.endswith("\n"):
                right_region += "\n"
            region_rows, limited = _region_rows(
                left_region,
                right_region,
                left_no=left_no,
                right_no=right_no,
            )
            rows.extend(region_rows)
            if limited and warning is None:
                warning = {
                    "type": "tokendiff_region_limit",
                    "message": (
                        "Tokendiff region exceeded the token matching limit;"
                        " the region is rendered as plain deletions and"
                        " insertions."
                    ),
                }
            left_no += i2 - i1
            right_no += j2 - j1
    return rows, warning


def _token_summary(rows: list[DiffEngineRow]) -> DiffSummary:
    modified_lines = sum(
        1
        for row in rows
        if row["status"] == "replace"
        or (row["status"] == "equal" and row["left_text"] != row["right_text"])
    )
    added_lines = sum(1 for row in rows if row["status"] == "insert")
    removed_lines = sum(1 for row in rows if row["status"] == "delete")
    return {
        "changed_lines": modified_lines + added_lines + removed_lines,
        "modified_lines": modified_lines,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "moved_lines": 0,
    }


@final
class TokenDiffEngine(DiffEngineProtocol):
    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        rows, warning = _build_token_rows(old.text or "", new.text or "")
        result: DiffEngineResult = {
            "summary": _token_summary(rows),
            "rows": rows,
            "interleave_inline_rows": False,
        }
        if warning is not None:
            result["engine_warning"] = warning
        return result
