"""Compare text with dirdiff's native line-first algorithm.

## Public interface

`TextDiffEngine` implements the common engine protocol. `text_diff_summary`
computes the same line counts without constructing inline tokens when callers
will discard the rows.

## Purpose and boundaries

The algorithm aligns lines first, then tokenizes changed pairs and classifies
their inline differences. It produces neutral engine rows and summary counts.
`dirdiff.rendering` adds syntax, folds, and hunk indexes later.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TypedDict, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DiffSummary,
    InlineToken,
)

__all__ = [
    "TextDiffEngine",
]

INLINE_TOKEN_PATTERN = re.compile(r"\w+|\s+|[^\w\s]+", flags=re.UNICODE)
"""Partition inline text into word, whitespace, and punctuation runs.

The alternatives cover the complete input, so concatenating matches reproduces
the source. Alignment compares word and punctuation runs while handling paired
whitespace at character granularity.
"""
INLINE_IDENTIFIER_PART_PATTERN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|_|$)|[A-Z]?[a-z]+|[0-9]+|_+|[^A-Za-z0-9_]+",
    flags=re.UNICODE,
)
"""Split identifier-like tokens at acronym, case, digit, and underscore edges.

Replacement runs use these boundaries before falling back to characters. This
keeps familiar identifier parts intact in names such as `HTTPServer2_value`.
"""
ALIGNMENT_WORD_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
"""Extract word evidence used to decide whether changed lines may pair.

Leading whitespace is removed before matching. Punctuation alone is not enough
to pair two otherwise unrelated lines.
"""
ALIGNMENT_NOISE_WORDS = frozenset({"none", "true", "false", "null"})
"""Common literals that cannot by themselves justify changed-line pairing.

Their presence still contributes to similarity after another informative word
matches, but a shared generic literal alone would create misleading pairs.
"""
MIN_SIMILAR_LINE_RATIO = 0.45
"""Minimum informative-word similarity accepted as a changed-line pair.

`_align_similar_lines` applies the threshold before its ordered maximum-score
selection. Lower-scoring lines remain independent deletions and insertions.
"""


class _TokenPiece(TypedDict):
    """One lexical piece before inline alignment assigns a status.

    Text and whitespace identity are complete at tokenization time. The piece
    must not represent a public inline token because its diff status does not
    exist until the two token sequences have been aligned.
    """

    text: str
    """Non-empty source slice emitted by the lexical splitter.

    Pieces remain in source order and concatenate to the original line. Inline
    alignment may pair or split them but must not normalize their spelling.
    """

    is_ws: bool
    """True only when every character in `text` is whitespace.

    The flag lets alignment retain exact whitespace while excluding it from
    informative-word evidence. It must agree with the complete stored slice.
    """


def _text_summary(rows: list[DiffEngineRow]) -> DiffSummary:
    """Return line-count summary for TextDiff rows.

    The TextDiff algorithm does not report moved lines. GumTree performs move
    detection, so this summary keeps `moved_lines` at zero while counting
    replace/insert/delete rows exactly as the native text renderer produced
    them.
    """
    # A paired row carries changed inline tokens exactly when its raw texts
    # differ (an lstrip-equal pair with leading-whitespace change stays
    # status "equal" but still counts as modified), so the texts already in
    # the row decide the count without touching token lists. This lets the
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
    """Compare supplied text with dirdiff's line-first native algorithm.

    Lines are aligned before changed line pairs receive inline tokenization.
    Missing text is compared as an empty document. The engine has no external
    executable, workspace state, or display-enrichment responsibility.
    """

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

        # Parameters

        - `old`: Already-loaded old text side.
        - `new`: Already-loaded new text side.

        # Usage

        Obtain this renderer through `dirdiff.engines.engine` and normally let
        `dirdiff.formats.Composer` call it for a text bay.
        """
        rows = _build_text_rows(
            old.text or "", new.text or "", with_inline_tokens=True
        )
        return {
            "summary": _text_summary(rows),
            "rows": rows,
        }


def text_diff_summary(left_text: str, right_text: str) -> DiffSummary:
    """Count TextDiff's summary without building or validating tokens.

    The rows walk, line alignment, and status decisions are exactly
    `render_diff`'s, so the counts are identical; only inline tokenization
    is skipped. Consumers that keep nothing but the counts use this path.
    Building tokens for discarded rows measured 2.4 seconds on one
    2.7MB single-line notebook output whose response carried five integers.

    # Parameters

    - `left_text`: Complete old text to align and count.
    - `right_text`: Complete new text to align and count.

    # Usage

    Use this when only aggregate line counts are retained. Use
    `TextDiffEngine.render_diff` when rows or inline tokens are needed.
    """
    return _text_summary(
        _build_text_rows(left_text, right_text, with_inline_tokens=False)
    )


def _build_text_rows(
    left_text: str,
    right_text: str,
    *,
    with_inline_tokens: bool,
) -> list[DiffEngineRow]:
    """Build neutral TextDiff rows before display enrichment.

    The line walk is identical for summary-only and full rendering. Callers may
    suppress inline token construction when they will discard token details;
    row pairing, statuses, and source text remain unchanged.

    # Parameters

    - `left_text`: Complete old document.
    - `right_text`: Complete new document.
    - `with_inline_tokens`: Whether changed paired lines receive token details.
    """
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    rows: list[DiffEngineRow] = []
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
) -> DiffEngineRow:
    """Build one already-paired row and its optional inline token partition.

    Left-trim-equal lines stay row-level `equal` even when indentation differs;
    their changed whitespace still receives tokens and counts as modified.

    # Parameters

    - `left_line`: Exact old source line without its terminator.
    - `right_line`: Exact paired new source line.
    - `left_no`: One-based old line number.
    - `right_no`: One-based new line number.
    - `with_inline_tokens`: Whether differing text receives token decoration.
    """
    row: DiffEngineRow = {
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
    left_tokens: list[InlineToken],
    right_tokens: list[InlineToken],
    *,
    is_ws: bool = False,
) -> None:
    """Append a lossless character-level diff for one paired text fragment.

    This is the last refinement step for whitespace and unsplittable identifier
    pieces. It mutates both output lists in source order and omits empty slices.

    # Parameters

    - `left_text`: Old fragment to partition.
    - `right_text`: New fragment to partition.
    - `left_tokens`: Old-side output list to extend.
    - `right_tokens`: New-side output list to extend.
    - `is_ws`: Whether every emitted fragment is whitespace.
    """
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
    """Split one replacement token at stable identifier boundaries.

    If the pattern finds no boundary, return the original text as one part so
    downstream alignment never loses an input fragment.
    """
    parts = INLINE_IDENTIFIER_PART_PATTERN.findall(text)
    return parts if parts != [] else [text]


def _append_identifier_level_diff(
    left_text: str,
    right_text: str,
    left_tokens: list[InlineToken],
    right_tokens: list[InlineToken],
) -> None:
    """Append inline tokens after aligning meaningful identifier parts.

    A one-part pair becomes a character diff only when neither side offers
    useful substructure. Multi-part replacements retain whole case, digit, and
    underscore units instead of painting arbitrary characters.

    # Parameters

    - `left_text`: Old non-whitespace token text.
    - `right_text`: New non-whitespace token text.
    - `left_tokens`: Old-side output list to extend.
    - `right_tokens`: New-side output list to extend.
    """
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
    """Report whether two lines share a useful word for alignment.

    Numeric text and the generic literals in `ALIGNMENT_NOISE_WORDS` cannot be
    sole evidence. Comparison is case-insensitive, but returned rows retain the
    exact source spelling.

    # Parameters

    - `left_words`: Word atoms extracted from the old line.
    - `right_words`: Word atoms extracted from the new line.
    """

    def _is_informative_alignment_word(word: str) -> bool:
        """Return whether one word can justify pairing two changed lines.

        Matching is case-insensitive. Numeric-only atoms and the fixed noise
        vocabulary may still render normally, but they cannot be the sole
        evidence that a deletion and insertion form one replacement.
        """
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
    """Score whether two changed lines should appear as a replacement pair.

    Informative word atoms drive the score. Without such evidence, only exact
    left-trimmed equality scores as a match.

    # Parameters

    - `left_line`: Candidate old source line.
    - `right_line`: Candidate new source line.
    """

    def _line_alignment_words(text: str) -> list[str]:
        """Return candidate word atoms after ignoring indentation only.

        The helper preserves word spelling for later case-folded scoring. It
        intentionally excludes punctuation and does not strip trailing text,
        so exact left-trimmed equality remains a separate fast decision.
        """
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
    """Choose an ordered maximum-score pairing for two changed line blocks.

    Only candidates meeting `MIN_SIMILAR_LINE_RATIO` may pair. Unselected lines
    remain one-sided rows, and the result never crosses line order.

    # Parameters

    - `left_lines`: Consecutive old lines from one replacement opcode.
    - `right_lines`: Consecutive new lines from that opcode.

    # Returns

    - `First in each pair`: The zero-based old-line index.
    - `Second in each pair`: The zero-based new-line index chosen with it by
      maximum total similarity; omitted indexes represent one-sided lines.
    - `Order`: Both indexes increase strictly, so row emission may walk the
      result once without crossing source order.
    """
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
) -> tuple[list[InlineToken], list[InlineToken]]:
    """Return lossless inline token partitions for one paired line.

    Word, whitespace, and punctuation runs align first. Whitespace differences
    refine to characters, while changed word runs refine at identifier
    boundaries. Joining either result reproduces its input exactly.

    # Parameters

    - `left_text`: Exact old row text.
    - `right_text`: Exact new row text.

    # Returns

    - `First`: A lossless old-side partition of `left_text` with statuses for
      each aligned or changed run.
    - `Second`: The corresponding new-side partition of `right_text`, in source
      order and aligned against the old-side runs.
    """
    left_bits = INLINE_TOKEN_PATTERN.findall(left_text)
    right_bits = INLINE_TOKEN_PATTERN.findall(right_text)

    def make_tokens(bits: list[str]) -> list[_TokenPiece]:
        """Attach whitespace identity to a complete tokenizer result.

        `bits` must remain in source order and cover the source exactly. The
        returned private pieces have no diff status until both sides align.
        """
        tokens: list[_TokenPiece] = []
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
    left_tokens: list[InlineToken] = []
    right_tokens: list[InlineToken] = []

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
