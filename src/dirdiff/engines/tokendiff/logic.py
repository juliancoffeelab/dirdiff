"""Dirdiff token-first text engine.

`TokenDiffEngine` diffs two already-loaded text documents as one stream of
fine-grained atoms — identifier parts, numbers, punctuation characters,
whitespace runs, and newlines — instead of diffing lines first. The engine
exists because line-first diffing cannot see content that moves across line
boundaries: comment reflow, docstring rewrapping, and line joins or splits all
churn a line-first diff, while a token-first diff matches the moved words and
reports exactly the whitespace and words that actually moved.

The public interface is `TokenDiffEngine` implementing `DiffEngineProtocol`.
The module owns tokenization, the token edit script, line pairing, and
row/token emission. It does not load files, attach syntax highlighting, build
fold hints, or assemble HTTP payloads — those belong to the backend loader,
the rendering layer, and the server (see `dirdiff.engines.base`).

Pipeline
--------
1. Tokenization: each document becomes a total sequence of atoms whose
   concatenation reproduces the document exactly. Identifiers split at
   snake_case and CamelCase boundaries during tokenization; whole
   identifiers are the matching unit, and the parts surface only when two
   replaced identifiers are re-diffed against each other.
2. Edit script: line runs equal after left-strip anchor the comparison —
   such lines differ at most in leading whitespace and pair one to one —
   and every other region is diffed once as a whole token stream, newlines
   included, which is where cross-line matches happen. Whitespace atoms
   never drive matching:
   they pair positionally between matched neighbors, and a changed
   whitespace pair is trimmed to its differing middle, so an indentation
   change highlights exactly the added or removed characters.
3. Rows: each side's scripted atoms split back into lines at newline atoms;
   left and right lines pair through a monotone maximum-weight assignment
   over shared matched content. A match whose endpoints land on rows that
   did not pair with each other is demoted to a delete/insert pair of
   identical text, so a one-sided row is always entirely its own status and
   row status stays a pure function of row tokens.

Guarantees
----------
* Totality: concatenating each side's row texts (with newlines) reproduces
  that side's document exactly; whitespace is always diffed, never ignored.
* Source order: every source line appears exactly once per side, in order.
* Consistency: per paired row, the concatenated unchanged token text is
  identical on both sides, and row status derives from token statuses.
* Honesty: a region too large to match within `_REGION_ATOM_LIMIT` renders
  as plain one-sided rows with an explicit engine warning instead of
  silently degrading.

The only row-invisible change is a line-terminator-only difference (for
example a removed final newline): the row model is line-based, so it carries
no rendered token and no summary count, matching the other engines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffSide,
    DiffSummary,
    EngineWarning,
    strict_engine_rows,
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
"""Total atom alternation, tried in order at every position.

Newline, same-line whitespace run, digit run, underscore run, CamelCase
acronym, capitalized or lower-case ASCII word part, non-ASCII word run, then
one punctuation character. Every character of any string matches exactly one
alternative, which `_tokenize` asserts.
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


@dataclass
class _Op:
    """One edit-script step consuming exact text from one or both sides.

    `left` and `right` are exact consecutive slices of their documents;
    `None` means the step consumes nothing on that side, and at least one
    side is always present and non-empty. A step whose present sides are
    equal is a match; `demoted` marks a match that later renders as a
    delete/insert pair of the same text instead. Steps never invent or
    reorder text, so the script replays either document from the other.
    """

    left: str | None
    right: str | None
    demoted: bool = False


def _tokenize(text: str) -> list[str]:
    """Split text into the engine's total atom sequence.

    Atoms follow `_ATOM_PATTERN`; identifier parts split at snake_case and
    CamelCase boundaries here, whitespace runs never contain a newline, and
    a newline is always its own atom. The atoms concatenate back to `text`
    exactly.
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

    Maximal runs of word atoms — identifiers with their part boundaries —
    match as single units, so a fragment of one identifier never matches
    inside a different identifier. Punctuation matches per character, and
    whitespace atoms (newlines included) are junk to the matcher, pairing
    positionally between matched neighbors. Replaced stretches pair
    positionally: a replaced identifier pair re-diffs at part level to
    expose exactly the renamed segments, a whitespace pair trims to its
    changed middle, and the unpairable surplus stays one-sided.
    """

    def word_like(atom: str) -> bool:
        """Report whether the atom belongs to an identifier-like run."""
        return not atom.isspace() and (atom[0].isalnum() or atom[0] == "_")

    def to_elements(atoms: list[str]) -> list[list[str]]:
        """Group adjacent word atoms into identifier-run elements."""
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
        """Pair two elements: match, part re-diff, trim, or replacement."""
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

    A global token match can leave an isolated short word — `is`, `to`, a
    stray identifier part — matched between two otherwise rewritten
    stretches, and rendering such specks as unchanged turns a rewrite into
    confetti. Any maximal run of steps free of content changes whose matched
    word text is at most `_MAX_DEMOTED_MATCH_TEXT` characters, bounded on
    both sides by content-changing steps, has those word matches demoted.
    Matched separators — commas, parens, braces, dots, colons — and matched
    whitespace are structural context and always keep their match. Demotion
    never alters step texts, so replay is unaffected.
    """

    def content_changed(op: _Op) -> bool:
        """Report whether the step changes non-whitespace text."""
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


def _merge_tokens(pieces: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Merge adjacent same-status pieces into display tokens.

    Pieces arrive in line order as exact text slices; merging compresses
    runs sharing one status into one token per contiguous verdict. Token
    `is_ws` describes the merged slice exactly.
    """
    merged: list[tuple[str, str]] = []
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
) -> dict[str, Any]:
    """Render one line pair anchored by lstrip-equality.

    Anchored pairs differ at most in leading whitespace, so the row is
    always `equal`; when the lines differ, the only changed tokens are the
    trimmed middle of the leading run, and byte-equal lines carry no tokens.
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

    def lead_pieces(middle: str, one_sided: str) -> list[tuple[str, str]]:
        """Assemble one side's pieces around its trimmed leading middle."""
        pieces: list[tuple[str, str]] = []
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
) -> tuple[list[dict[str, Any]], bool]:
    """Render one changed line region into aligned rows.

    `left_region` and `right_region` are the region's exact text including
    each line's terminating newline (only a document-final line may lack
    one), and `left_no`/`right_no` are the one-based numbers of the region's
    first lines. Returns the region's rows plus True when the region
    exceeded `_REGION_ATOM_LIMIT` and rendered as plain one-sided rows.
    """
    left_atoms = _tokenize(left_region)
    right_atoms = _tokenize(right_region)
    if len(left_atoms) * len(right_atoms) > _REGION_ATOM_LIMIT:
        rows: list[dict[str, Any]] = []
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
    left_line_pieces: list[list[tuple[str, str]]] = [
        [] for _ in range(left_count)
    ]
    right_line_pieces: list[list[tuple[str, str]]] = [
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
                status = "unchanged" if op.left == op.right else "replace"
            else:
                status = "delete"
            left_line_pieces[line_left].append((op.left, status))
        if op.right is not None and op.right != "\n":
            assert line_right is not None
            if kept:
                status = "unchanged" if op.left == op.right else "replace"
            else:
                status = "insert"
            right_line_pieces[line_right].append((op.right, status))

    rows = []
    left_cursor = 0
    right_cursor = 0

    def append_one_sided(line: int, side: str) -> None:
        """Emit one unpaired region line as a fully one-sided row."""
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
) -> tuple[list[dict[str, Any]], EngineWarning | None]:
    """Build neutral tokendiff rows before display enrichment.

    Line runs equal after left-strip anchor the walk and pair one to one,
    runs present on one side render as plain one-sided rows, and every
    other run is a changed region rendered through the token pipeline. The
    returned warning reports the first region over `_REGION_ATOM_LIMIT`,
    or None.
    """
    left_lines = _split_lines(left_text)
    right_lines = _split_lines(right_text)
    rows: list[dict[str, Any]] = []
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
            # Each line owns its terminating newline; only the document's
            # final line can lack one.
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


def _token_summary(rows: list[dict[str, Any]]) -> DiffSummary:
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
    """Token-first renderer for already-loaded text sides."""

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
        """
        rows, warning = _build_token_rows(old.text or "", new.text or "")
        result: DiffEngineResult = {
            "summary": _token_summary(rows),
            "rows": strict_engine_rows(rows),
        }
        if warning is not None:
            result["engine_warning"] = warning
        return result
