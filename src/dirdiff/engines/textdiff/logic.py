"""Dirdiff text-engine logic.

`TextDiffEngine` aligns lines, tokenizes inline changes, assigns token statuses,
and returns text summary counts. This module does not attach syntax
highlighting, fold hints, hunk identities, request labels, repository paths, or
HTTP payload metadata.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffSide,
    DiffSummary,
    strict_engine_rows,
)

__all__ = [
    "TextDiffEngine",
]

INLINE_TOKEN_PATTERN = re.compile(r"\w+|\s+|[^\w\s]+", flags=re.UNICODE)
INLINE_IDENTIFIER_PART_PATTERN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|_|$)|[A-Z]?[a-z]+|[0-9]+|_+|[^A-Za-z0-9_]+",
    flags=re.UNICODE,
)
ALIGNMENT_WORD_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
ALIGNMENT_NOISE_WORDS = frozenset({"none", "true", "false", "null"})
MIN_SIMILAR_LINE_RATIO = 0.45


def _text_summary(rows: list[dict[str, Any]]) -> DiffSummary:
    """Return line-count summary for TextDiff rows.

    The TextDiff algorithm does not report moved lines. GumTree owns move
    detection, so this summary keeps `moved_lines` at zero while counting
    replace/insert/delete rows exactly as the native text renderer produced
    them.
    """
    # A paired row carries changed inline tokens exactly when its raw texts
    # differ (an lstrip-equal pair with leading-whitespace change stays
    # status "equal" but still counts as modified), so the texts already in
    # the row decide the count without touching token lists — which lets the
    # summary-only path below skip building tokens entirely.
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
class TextDiffEngine(DiffEngineProtocol):
    """TextDiff renderer for already-loaded text sides."""

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Build a TextDiff engine result from already-loaded sides.

        Source loading and request metadata are handled before this engine is
        called.  Display enrichment such as syntax highlighting and folding is
        applied later by server-side payload assembly.
        """
        rows = _build_text_rows(
            old.text or "", new.text or "", with_inline_tokens=True
        )
        return {
            "summary": _text_summary(rows),
            "rows": strict_engine_rows(rows),
        }


def text_diff_summary(left_text: str, right_text: str) -> DiffSummary:
    """Count TextDiff's summary without building or validating tokens.

    The rows walk, line alignment, and status decisions are exactly
    `render_diff`'s, so the counts are identical; only inline tokenization
    is skipped. Consumers that keep nothing but the counts use this path —
    building tokens for a discarded surface measured 2.4 seconds on one
    2.7MB single-line notebook output whose response carried five integers.
    """
    return _text_summary(
        _build_text_rows(left_text, right_text, with_inline_tokens=False)
    )


def _build_text_rows(
    left_text: str,
    right_text: str,
    *,
    with_inline_tokens: bool,
) -> list[dict[str, Any]]:
    """Build neutral TextDiff rows before display enrichment."""
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    rows: list[dict[str, Any]] = []
    left_no = 1
    right_no = 1

    left_keys = [line.lstrip() for line in left_lines]
    right_keys = [line.lstrip() for line in right_lines]
    matcher = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_block = left_lines[i1:i2]
        right_block = right_lines[j1:j2]

        if tag == "equal":
            for left_line, right_line in zip(
                left_block,
                right_block,
                strict=True,
            ):
                rows.append(
                    _paired_line_row(
                        left_line,
                        right_line,
                        left_no,
                        right_no,
                        with_inline_tokens=with_inline_tokens,
                    )
                )
                left_no += 1
                right_no += 1
            continue

        if tag == "delete":
            for left_line in left_block:
                rows.append(
                    {
                        "status": "delete",
                        "left_no": left_no,
                        "right_no": None,
                        "left_text": left_line,
                        "right_text": "",
                        "left_tokens": [],
                        "right_tokens": [],
                    }
                )
                left_no += 1
            continue

        if tag == "insert":
            for right_line in right_block:
                rows.append(
                    {
                        "status": "insert",
                        "left_no": None,
                        "right_no": right_no,
                        "left_text": "",
                        "right_text": right_line,
                        "left_tokens": [],
                        "right_tokens": [],
                    }
                )
                right_no += 1
            continue

        similar_pairs = _align_similar_lines(left_block, right_block)
        left_cursor = 0
        right_cursor = 0

        for left_index, right_index in similar_pairs:
            for delete_index in range(left_cursor, left_index):
                rows.append(
                    {
                        "status": "delete",
                        "left_no": left_no,
                        "right_no": None,
                        "left_text": left_block[delete_index],
                        "right_text": "",
                        "left_tokens": [],
                        "right_tokens": [],
                    }
                )
                left_no += 1

            for insert_index in range(right_cursor, right_index):
                rows.append(
                    {
                        "status": "insert",
                        "left_no": None,
                        "right_no": right_no,
                        "left_text": "",
                        "right_text": right_block[insert_index],
                        "left_tokens": [],
                        "right_tokens": [],
                    }
                )
                right_no += 1

            rows.append(
                _paired_line_row(
                    left_block[left_index],
                    right_block[right_index],
                    left_no,
                    right_no,
                    with_inline_tokens=with_inline_tokens,
                )
            )
            left_no += 1
            right_no += 1
            left_cursor = left_index + 1
            right_cursor = right_index + 1

        for delete_index in range(left_cursor, len(left_block)):
            rows.append(
                {
                    "status": "delete",
                    "left_no": left_no,
                    "right_no": None,
                    "left_text": left_block[delete_index],
                    "right_text": "",
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
            left_no += 1

        for insert_index in range(right_cursor, len(right_block)):
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": right_no,
                    "left_text": "",
                    "right_text": right_block[insert_index],
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
            right_no += 1

    return rows


def _paired_line_row(
    left_line: str,
    right_line: str,
    left_no: int,
    right_no: int,
    *,
    with_inline_tokens: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": (
            "equal" if left_line.lstrip() == right_line.lstrip() else "replace"
        ),
        "left_no": left_no,
        "right_no": right_no,
        "left_text": left_line,
        "right_text": right_line,
        "left_tokens": [],
        "right_tokens": [],
    }
    if with_inline_tokens and left_line != right_line:
        left_tokens, right_tokens = _inline_diff(left_line, right_line)
        row["left_tokens"] = left_tokens
        row["right_tokens"] = right_tokens
    return row


def _append_char_level_diff(
    left_text: str,
    right_text: str,
    left_tokens: list[dict[str, Any]],
    right_tokens: list[dict[str, Any]],
    *,
    is_ws: bool = False,
) -> None:
    matcher = SequenceMatcher(a=left_text, b=right_text, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = left_text[i1:i2]
            if text != "":
                left_tokens.append(
                    {"text": text, "status": "unchanged", "is_ws": is_ws}
                )
                right_tokens.append(
                    {"text": text, "status": "unchanged", "is_ws": is_ws}
                )
        elif tag == "delete":
            text = left_text[i1:i2]
            if text != "":
                left_tokens.append(
                    {"text": text, "status": "delete", "is_ws": is_ws}
                )
        elif tag == "insert":
            text = right_text[j1:j2]
            if text != "":
                right_tokens.append(
                    {"text": text, "status": "insert", "is_ws": is_ws}
                )
        else:
            left_piece = left_text[i1:i2]
            right_piece = right_text[j1:j2]
            if left_piece != "":
                left_tokens.append(
                    {"text": left_piece, "status": "replace", "is_ws": is_ws}
                )
            if right_piece != "":
                right_tokens.append(
                    {"text": right_piece, "status": "replace", "is_ws": is_ws}
                )


def _identifier_diff_parts(text: str) -> list[str]:
    parts = INLINE_IDENTIFIER_PART_PATTERN.findall(text)
    return parts if parts != [] else [text]


def _append_identifier_level_diff(
    left_text: str,
    right_text: str,
    left_tokens: list[dict[str, Any]],
    right_tokens: list[dict[str, Any]],
) -> None:
    left_parts = _identifier_diff_parts(left_text)
    right_parts = _identifier_diff_parts(right_text)
    if left_parts == [left_text] and right_parts == [right_text]:
        _append_char_level_diff(
            left_text,
            right_text,
            left_tokens,
            right_tokens,
        )
        return

    matcher = SequenceMatcher(a=left_parts, b=right_parts, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for li, ri in zip(range(i1, i2), range(j1, j2), strict=True):
                text = left_parts[li]
                left_tokens.append(
                    {"text": text, "status": "unchanged", "is_ws": False}
                )
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "status": "unchanged",
                        "is_ws": False,
                    }
                )
        elif tag == "delete":
            for li in range(i1, i2):
                left_tokens.append(
                    {"text": left_parts[li], "status": "delete", "is_ws": False}
                )
        elif tag == "insert":
            for ri in range(j1, j2):
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "status": "insert",
                        "is_ws": False,
                    }
                )
        else:
            left_count = i2 - i1
            right_count = j2 - j1
            if left_count == 1 and right_count == 1:
                left_tokens.append(
                    {
                        "text": left_parts[i1],
                        "status": "replace",
                        "is_ws": False,
                    }
                )
                right_tokens.append(
                    {
                        "text": right_parts[j1],
                        "status": "replace",
                        "is_ws": False,
                    }
                )
                continue

            for li in range(i1, i2):
                left_tokens.append(
                    {
                        "text": left_parts[li],
                        "status": "replace",
                        "is_ws": False,
                    }
                )
            for ri in range(j1, j2):
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "status": "replace",
                        "is_ws": False,
                    }
                )


def _has_shared_informative_alignment_word(
    left_words: list[str],
    right_words: list[str],
) -> bool:
    def _is_informative_alignment_word(word: str) -> bool:
        """Reject numeric and low-value words from alignment evidence."""
        folded = word.casefold()
        return not folded.isdigit() and folded not in ALIGNMENT_NOISE_WORDS

    left_informative = {
        word.casefold()
        for word in left_words
        if _is_informative_alignment_word(word)
    }
    if left_informative == set():
        return False

    right_informative = {
        word.casefold()
        for word in right_words
        if _is_informative_alignment_word(word)
    }
    return bool(left_informative & right_informative)


def _line_alignment_ratio(left_line: str, right_line: str) -> float:
    def _line_alignment_words(text: str) -> list[str]:
        """Extract left-trimmed word atoms used for line alignment."""
        return ALIGNMENT_WORD_PATTERN.findall(text.lstrip())

    left_words = _line_alignment_words(left_line)
    right_words = _line_alignment_words(right_line)
    if left_words != [] and right_words != []:
        if not _has_shared_informative_alignment_word(
            left_words,
            right_words,
        ):
            return 1.0 if left_line.lstrip() == right_line.lstrip() else 0.0

        return SequenceMatcher(
            a=left_words,
            b=right_words,
            autojunk=False,
        ).ratio()
    return 1.0 if left_line.lstrip() == right_line.lstrip() else 0.0


def _align_similar_lines(
    left_lines: list[str],
    right_lines: list[str],
) -> list[tuple[int, int]]:
    if left_lines == [] or right_lines == []:
        return []

    left_count = len(left_lines)
    right_count = len(right_lines)
    scores: list[list[float]] = [
        [0.0] * (right_count + 1) for _ in range(left_count + 1)
    ]
    decisions: list[list[str]] = [
        ["done"] * right_count for _ in range(left_count)
    ]

    for left_index in range(left_count - 1, -1, -1):
        for right_index in range(right_count - 1, -1, -1):
            skip_left = scores[left_index + 1][right_index]
            skip_right = scores[left_index][right_index + 1]
            best_score = skip_left
            decision = "skip_left"
            if skip_right > best_score:
                best_score = skip_right
                decision = "skip_right"

            pair_ratio = _line_alignment_ratio(
                left_lines[left_index],
                right_lines[right_index],
            )
            if pair_ratio >= MIN_SIMILAR_LINE_RATIO:
                pair_score = (
                    pair_ratio + scores[left_index + 1][right_index + 1]
                )
                if pair_score > best_score:
                    best_score = pair_score
                    decision = "pair"

            scores[left_index][right_index] = best_score
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


def _inline_diff(
    left_text: str, right_text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    left_bits = INLINE_TOKEN_PATTERN.findall(left_text)
    right_bits = INLINE_TOKEN_PATTERN.findall(right_text)

    def make_tokens(bits: list[str]) -> list[dict[str, Any]]:
        tokens: list[dict[str, Any]] = []
        for bit in bits:
            tokens.append(
                {
                    "text": bit,
                    "is_ws": bool(re.fullmatch(r"\s+", bit)),
                }
            )
        return tokens

    left_data = make_tokens(left_bits)
    right_data = make_tokens(right_bits)
    left_keys = ["" if token["is_ws"] else token["text"] for token in left_data]
    right_keys = [
        "" if token["is_ws"] else token["text"] for token in right_data
    ]

    matcher = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)
    left_tokens: list[dict[str, Any]] = []
    right_tokens: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for li, ri in zip(range(i1, i2), range(j1, j2), strict=True):
                left_token = left_data[li]
                right_token = right_data[ri]
                if left_token["is_ws"] is True and right_token["is_ws"] is True:
                    _append_char_level_diff(
                        left_token["text"],
                        right_token["text"],
                        left_tokens,
                        right_tokens,
                        is_ws=True,
                    )
                else:
                    left_tokens.append(
                        {
                            "text": left_token["text"],
                            "status": "unchanged",
                            "is_ws": left_token["is_ws"],
                        }
                    )
                    right_tokens.append(
                        {
                            "text": right_token["text"],
                            "status": "unchanged",
                            "is_ws": right_token["is_ws"],
                        }
                    )
        elif tag == "delete":
            for li in range(i1, i2):
                token = left_data[li]
                left_tokens.append(
                    {
                        "text": token["text"],
                        "status": "delete",
                        "is_ws": token["is_ws"],
                    }
                )
        elif tag == "insert":
            for ri in range(j1, j2):
                token = right_data[ri]
                right_tokens.append(
                    {
                        "text": token["text"],
                        "status": "insert",
                        "is_ws": token["is_ws"],
                    }
                )
        else:
            left_slice = left_data[i1:i2]
            right_slice = right_data[j1:j2]
            inner_matcher = SequenceMatcher(
                a=[token["text"] for token in left_slice],
                b=[token["text"] for token in right_slice],
                autojunk=False,
            )
            for inner_tag, ii1, ii2, jj1, jj2 in inner_matcher.get_opcodes():
                if inner_tag == "equal":
                    for lrel, rrel in zip(
                        range(ii1, ii2),
                        range(jj1, jj2),
                        strict=True,
                    ):
                        left_token = left_slice[lrel]
                        right_token = right_slice[rrel]
                        left_tokens.append(
                            {
                                "text": left_token["text"],
                                "status": "unchanged",
                                "is_ws": left_token["is_ws"],
                            }
                        )
                        right_tokens.append(
                            {
                                "text": right_token["text"],
                                "status": "unchanged",
                                "is_ws": right_token["is_ws"],
                            }
                        )
                elif inner_tag == "delete":
                    for lrel in range(ii1, ii2):
                        token = left_slice[lrel]
                        left_tokens.append(
                            {
                                "text": token["text"],
                                "status": "delete",
                                "is_ws": token["is_ws"],
                            }
                        )
                elif inner_tag == "insert":
                    for rrel in range(jj1, jj2):
                        token = right_slice[rrel]
                        right_tokens.append(
                            {
                                "text": token["text"],
                                "status": "insert",
                                "is_ws": token["is_ws"],
                            }
                        )
                else:
                    left_count = ii2 - ii1
                    right_count = jj2 - jj1
                    if left_count == 1 and right_count == 1:
                        left_token = left_slice[ii1]
                        right_token = right_slice[jj1]
                        left_is_ws: bool = left_token["is_ws"]
                        right_is_ws: bool = right_token["is_ws"]
                        if not left_is_ws and not right_is_ws:
                            left_parts = _identifier_diff_parts(
                                left_token["text"]
                            )
                            right_parts = _identifier_diff_parts(
                                right_token["text"]
                            )
                            if left_parts != [
                                left_token["text"]
                            ] or right_parts != [right_token["text"]]:
                                _append_identifier_level_diff(
                                    left_token["text"],
                                    right_token["text"],
                                    left_tokens,
                                    right_tokens,
                                )
                            else:
                                left_tokens.append(
                                    {
                                        "text": left_token["text"],
                                        "status": "replace",
                                        "is_ws": False,
                                    }
                                )
                                right_tokens.append(
                                    {
                                        "text": right_token["text"],
                                        "status": "replace",
                                        "is_ws": False,
                                    }
                                )
                            continue

                    for lrel in range(ii1, ii2):
                        token = left_slice[lrel]
                        left_tokens.append(
                            {
                                "text": token["text"],
                                "status": "replace",
                                "is_ws": token["is_ws"],
                            }
                        )
                    for rrel in range(jj1, jj2):
                        token = right_slice[rrel]
                        right_tokens.append(
                            {
                                "text": token["text"],
                                "status": "replace",
                                "is_ws": token["is_ws"],
                            }
                        )

    return left_tokens, right_tokens
