"""Token-first comparison of already-loaded text.

## Public interface

`TokenDiffEngine` tokenizes complete documents, matches content across line
boundaries, then arranges the result into neutral engine rows. It preserves all
source text and reports an explicit warning when a region exceeds the bounded
matching limit.

## Purpose and boundaries

Token-first matching lets unchanged content survive movement across line
boundaries before rows are paired. Oversized regions become explicit one-sided
rows rather than an unbounded comparison. The module receives loaded text and
produces neutral engine output; `dirdiff.rendering` adds display metadata.
"""

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
r"""Split source text into the ordered atoms consumed by `_tokenize`.

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

_REGION_ATOM_LIMIT = 4_000_000
"""Maximum left-atoms x right-atoms product one changed region may match.

Above this the region renders as plain one-sided rows with an engine
warning; token matching over such a region (for example two multi-megabyte
single-line documents) would take seconds for a payload nobody can read.
"""

_MAX_DEMOTED_MATCH_TEXT = 3
"""Largest matched word text, in characters, a stranded island may hold.

Isolated word matches at or below this size between changed surroundings
are demoted; larger ones are real shared content and stay matched. Matched
separators and whitespace never count and never demote.
"""

type _TokenPiece = tuple[str, InlineTokenStatus]
"""Pair one exact source slice with its final inline status.

Row construction produces these private pairs, then converts them to
`InlineToken` values. The text remains in source order and may be empty only
where the surrounding helper explicitly permits it.

This alias does not identify a lexical atom, row, or cross-side match.
"""


@dataclass
class _Op:
    """One edit-script step consuming exact text from one or both sides.

    At least one side contributes a non-empty consecutive source slice. Equal
    two-sided slices usually represent a match, though a later ambiguity pass
    may make the same text render as deletion beside insertion. Steps never
    invent or reorder text, so the script replays either document from the
    other.
    """

    left: str | None
    """Next consecutive old-side slice, or `None` for a pure insertion.

    Across the ordered script, every present left slice concatenates to the
    original left text exactly; an empty string is never used for absence.
    """

    right: str | None
    """Next consecutive new-side slice, or `None` for a pure deletion.

    Present right slices replay the original right text in order and are never
    normalized to match the paired left spelling.
    """

    demoted: bool = False
    """Whether a text-equal pair is deliberately presented as changed.

    The ambiguity pass sets this only for short word matches inside a rewrite.
    It changes visible token status, never the slices or replay invariant.
    """


def _tokenize(text: str) -> list[str]:
    """Split text into the engine's total atom sequence.

    Atoms follow `_ATOM_PATTERN`; identifier parts split at snake_case and
    CamelCase boundaries here, whitespace runs never contain a newline, and
    a newline is always its own atom. The atoms concatenate back to `text`
    exactly.

    # Failures

    Asserts if `_ATOM_PATTERN` ever stops consuming the complete input.
    """
    atoms = _ATOM_PATTERN.findall(text)
    assert sum(map(len, atoms)) == len(text), "atom pattern skipped characters"
    return atoms


def _split_lines(text: str) -> list[str]:
    """Split a document into the lines rows address.

    Lines split on newline only, the terminator belongs to the line before
    it, the empty tail after a trailing newline is not a line, and the empty
    document has no lines.
    """
    if text == "":
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _region_ops(left_atoms: list[str], right_atoms: list[str]) -> list[_Op]:
    """Diff one changed region's atom streams into edit-script steps.

    Maximal runs of word atoms, including identifiers with their part boundaries,
    match as single units, so a fragment of one identifier never matches
    inside a different identifier. Punctuation matches per character, and
    whitespace atoms (newlines included) are junk to the matcher, pairing
    positionally between matched neighbors. Replaced stretches pair
    positionally: a replaced identifier pair re-diffs at part level to
    expose exactly the renamed segments, a whitespace pair trims to its
    changed middle, and the unpairable surplus stays one-sided.

    # Parameters

    - `left_atoms`: Complete atom sequence for the old changed region.
    - `right_atoms`: Complete atom sequence for the new changed region.
    """

    def word_like(atom: str) -> bool:
        """Return whether an atom joins an identifier-like replacement unit.

        Letters, digits, and underscores open or extend a run. Whitespace and
        punctuation remain separate elements so pairing cannot hide separators.
        """
        return not atom.isspace() and (atom[0].isalnum() or atom[0] == "_")

    def to_elements(atoms: list[str]) -> list[list[str]]:
        """Partition atoms into identifier runs and standalone separators.

        Every atom appears once and flattening the result restores the input
        order. The later matcher can then re-diff a renamed identifier internally
        without pairing unrelated punctuation as part of that identifier.

        # Usage

        `_region_ops` applies this to both changed regions immediately before
        element-level sequence matching. Keep the returned groups intact until
        paired replacements are refined.
        """
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
        """Build edit steps for one positionally paired element pair.

        Identical elements match. Whitespace keeps its shared edges, identifier
        runs re-diff by stable parts, and other unequal elements become one
        replacement step.

        # Parameters

        - `left_element`: Consecutive old atoms grouped as one matcher element.
        - `right_element`: Consecutive new atoms paired with it.

        # Usage

        `_region_ops` calls this only for elements paired by position inside a
        replacement block. One-sided surplus elements bypass it.
        """
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
    """Demote tiny word matches stranded inside changed surroundings.

    A global token match can leave an isolated short word such as `is`, `to`,
    or a stray identifier part matched between two otherwise rewritten
    stretches, and rendering such specks as unchanged turns a rewrite into
    confetti. Any maximal run of steps free of content changes whose matched
    word text is at most `_MAX_DEMOTED_MATCH_TEXT` characters, bounded on
    both sides by content-changing steps, has those word matches demoted.
    Matched separators such as commas, parens, braces, dots, and colons always
    keep their match. Matched whitespace does too. Demotion
    never alters step texts, so replay is unaffected.
    """

    def content_changed(op: _Op) -> bool:
        """Return whether one script step changes visible non-whitespace content.

        An ordinary equal match is unchanged. One-sided, unequal, or demoted
        text counts only when a present slice contains non-whitespace, which
        keeps indentation and separators from bounding ambiguity demotion.
        """
        if op.left is not None and op.left == op.right and not op.demoted:
            return False
        present = [text for text in (op.left, op.right) if text is not None]
        return any(not text.isspace() for text in present)

    def word_match(op: _Op) -> bool:
        """Report whether the step matches word-like text on both sides.

        Atoms are homogeneous, so the first character decides: letters,
        digits, and underscores are word-like; punctuation is not.
        """
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
    """Choose the in-order line pairing with maximum shared matched text.

    `weights` maps a zero-based (left, right) line pair to the matched
    content length between those lines; absent pairs must not pair. The
    result lists chosen pairs strictly ascending on both sides, the order
    row emission interleaves around.

    # Parameters

    - `weights`: Matched-content lengths for candidate zero-based line pairs.
    - `left_count`: Number of old lines in the changed region.
    - `right_count`: Number of new lines in the changed region.

    # Returns

    - `First in each pair`: The zero-based old-line index.
    - `Second in each pair`: The zero-based new-line index selected with it by
      maximum total shared text; omitted indexes remain one-sided.
    - `Order`: Both indexes increase strictly, so later row emission never
      crosses either source's line order.
    """
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
    """Merge adjacent same-status pieces into display tokens.

    Pieces arrive in line order as exact text slices; merging compresses
    runs sharing one status into one token per contiguous verdict. Token
    `is_ws` describes the merged slice exactly.
    """
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
    """Render one line pair anchored by lstrip-equality.

    Anchored pairs differ at most in leading whitespace, so the row is
    always `equal`; when the lines differ, the only changed tokens are the
    trimmed middle of the leading run, and byte-equal lines carry no tokens.

    # Parameters

    - `left_line`: Old line known to equal `right_line` after left trimming.
    - `right_line`: New anchored line.
    - `left_no`: One-based old line number.
    - `right_no`: One-based new line number.

    # Failures

    Asserts when the pair does not share the required left-trimmed anchor.
    """
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
        """Assemble one side's pieces around its changed indentation middle.

        # Parameters

        - `middle`: Side-specific changed portion of the leading whitespace.
        - `one_sided`: Status used when only this side has a middle portion.

        # Usage

        `_anchored_line_row` calls this once per side after computing the shared
        indentation prefix and suffix. The closure supplies those shared parts.
        """
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


def _region_rows(
    left_region: str,
    right_region: str,
    *,
    left_no: int,
    right_no: int,
) -> tuple[list[DiffEngineRow], bool]:
    """Render one changed line region into aligned rows.

    `left_region` and `right_region` are the region's exact text including
    each line's terminating newline (only a document-final line may lack
    one), and `left_no`/`right_no` are the one-based numbers of the region's
    first lines.

    # Parameters

    - `left_region`: Exact old changed run, including its line terminators.
    - `right_region`: Exact new changed run under the same convention.
    - `left_no`: One-based number of the first old line in the run.
    - `right_no`: One-based number of the first new line in the run.

    # Returns

    - `First`: The complete rows for this changed region.
    - `Second`: Whether the region exceeded `_REGION_ATOM_LIMIT`. A true value
      means the rows are plain one-sided output; false means token alignment
      produced them.

    # Failures

    Asserts if row construction fails to consume both region texts exactly.
    """
    left_atoms = _tokenize(left_region)
    right_atoms = _tokenize(right_region)
    rows: list[DiffEngineRow] = []
    if len(left_atoms) * len(right_atoms) > _REGION_ATOM_LIMIT:
        for offset, line in enumerate(_split_lines(left_region)):
            rows.append(
                {
                    "status": "delete",
                    "left_no": left_no + offset,
                    "right_no": None,
                    "left_text": line,
                    "right_text": "",
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
        for offset, line in enumerate(_split_lines(right_region)):
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": right_no + offset,
                    "left_text": "",
                    "right_text": line,
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
        return rows, True

    ops = _region_ops(left_atoms, right_atoms)
    _demote_isolated_matches(ops)

    # First pass: assign each step's sides to region-local lines. The
    # terminating newline belongs to the line it ends.
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
    left_count = left_line if left_region.endswith("\n") else left_line + 1
    right_count = right_line if right_region.endswith("\n") else right_line + 1

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

    pairs = _pair_lines(weights, left_count, right_count)
    paired = set(pairs)

    # Second pass: split each side's steps into per-line pieces with final
    # statuses. A match whose endpoints are not on rows paired with each
    # other is demoted here to a one-sided pair, keeping every one-sided
    # row entirely its own status; newline steps shape lines but are never
    # rendered as tokens.
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

    left_cursor = 0
    right_cursor = 0

    def append_one_sided(line: int, side: Literal["left", "right"]) -> None:
        """Emit one unpaired region line as a fully one-sided row.

        The helper advances the cursor for the emitted side and asserts that
        line pairing did not leave any shared token on an unpaired row.

        # Parameters

        - `line`: Zero-based line index within the changed region.
        - `side`: Side whose cursor and pieces the row consumes.

        # Usage

        `_region_rows` calls this for gaps before, between, and after paired
        lines. Calls must advance each side in increasing line order.

        # Failures

        Raises `AssertionError` when an unpaired line still contains a token
        classified as shared with the other side.
        """
        nonlocal left_cursor, right_cursor
        if side == "left":
            pieces = left_line_pieces[line]
            assert all(status == "delete" for _, status in pieces), (
                "unpaired left row kept shared text"
            )
            rows.append(
                {
                    "status": "delete",
                    "left_no": left_no + line,
                    "right_no": None,
                    "left_text": "".join(text for text, _ in pieces),
                    "right_text": "",
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
            left_cursor = line + 1
        else:
            pieces = right_line_pieces[line]
            assert all(status == "insert" for _, status in pieces), (
                "unpaired right row kept shared text"
            )
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": right_no + line,
                    "left_text": "",
                    "right_text": "".join(text for text, _ in pieces),
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
            right_cursor = line + 1

    for pair_left, pair_right in pairs:
        for line_index in range(left_cursor, pair_left):
            append_one_sided(line_index, "left")
        for line_index in range(right_cursor, pair_right):
            append_one_sided(line_index, "right")
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
    for line_index in range(left_cursor, left_count):
        append_one_sided(line_index, "left")
    for line_index in range(right_cursor, right_count):
        append_one_sided(line_index, "right")
    return rows, False


def _build_token_rows(
    left_text: str,
    right_text: str,
) -> tuple[list[DiffEngineRow], EngineWarning | None]:
    """Build neutral tokendiff rows before display enrichment.

    Line runs equal after left-strip anchor the walk and pair one to one,
    runs present on one side render as plain one-sided rows, and every
    other run is a changed region rendered through the token pipeline. The
    first over-limit region determines the warning.

    # Parameters

    - `left_text`: Complete old document.
    - `right_text`: Complete new document.

    # Returns

    - `First`: The complete rows in source order.
    - `Second`: A warning identifying the first changed region that
      exceeded `_REGION_ATOM_LIMIT`.
    - `None`: The second item is absent when every changed region used token
      alignment, so the caller need not attach an engine warning.
    """
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
    """Line-count summary for tokendiff rows.

    Tokendiff does not report moved lines. A paired row counts as modified
    exactly when its two texts differ, which includes whitespace-only rows
    that keep row status `equal` while carrying changed whitespace tokens.
    """
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
    """Compare supplied text as complete token streams before pairing rows.

    Matching may cross line boundaries and preserves whitespace exactly. A
    region beyond the bounded matching limit becomes explicit one-sided rows
    with an engine warning. Missing text is an empty document.

    The engine carries no workspace or HTTP state and adds no display
    enrichment.
    """

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Build a tokendiff engine result from already-loaded sides.

        Source loading and request metadata are handled before this engine
        is called; an absent side diffs as the empty document. Display
        enrichment such as syntax highlighting and folding is applied later
        by server-side payload assembly.

        # Parameters

        - `old`: Already-loaded old text side.
        - `new`: Already-loaded new text side.

        # Usage

        Obtain this renderer through `dirdiff.engines.engine` and normally let
        `dirdiff.formats.Composer` call it for a text bay.
        """
        rows, warning = _build_token_rows(old.text or "", new.text or "")
        result: DiffEngineResult = {
            "summary": _token_summary(rows),
            "rows": rows,
        }
        if warning is not None:
            result["engine_warning"] = warning
        return result
