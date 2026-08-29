"""Build dirdiff engine rows from Difftastic alignment facts.

## Public interface

`DifftasticDiffEngine` implements the common engine protocol, including
one-sided Files and textual rows when a known Difftastic limit produces no
structural rows. Its local AST builder returns validated structural rows plus
any recognized degraded-mode warning.

## Purpose and boundaries

The supplied source strings decide every displayed character. Difftastic JSON
decides alignment and changed spans only after this module validates those
facts against the text. The result remains neutral engine output; syntax,
folds, and hunk indexes are added later by `dirdiff.rendering`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import unified_diff
from typing import Literal, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    EngineWarning,
    InlineToken,
)
from dirdiff.engines.difftastic.difft import (
    DifftasticJson,
    DifftasticJsonSide,
    run_difftastic_json,
)

type DifftasticRowStatus = Literal["equal", "replace", "insert", "delete"]
"""Row statuses that Difftastic row building can produce.

- `equal` pairs lines without changed spans.
- `replace` pairs lines carrying changed spans.
- `insert` and `delete` are one-sided.

Row building uses these values when producing public engine rows. Difftastic does
not produce dirdiff's separate `move` row classification.
"""
type DifftasticTokenStatus = Literal["unchanged", "replace", "insert", "delete"]
"""Classify one exact text span while building Difftastic rows.

- `unchanged` fills gaps around reported spans.
- `replace` pairs changed spans across sides.
- `insert` and `delete` classify one-sided changed spans.

These values become `InlineToken.status`; they do not classify complete rows or
represent Difftastic syntax nodes.
"""

__all__ = [
    "DifftasticDiffEngine",
    "difftastic_engine_warning",
    "difftastic_rows_from_json",
]


@dataclass(frozen=True)
class DifftasticAst:
    """Complete structural result for one Difftastic run.

    `build_difftastic_ast` returns this value after validating Difftastic's
    reported positions against the supplied text. `DifftasticDiffEngine` uses
    it directly or chooses textual alignment when no structural rows exist.

    `engine_warning` explains a recognized degraded mode. This type does not
    contain textual degraded rows, summary counts, or display metadata.
    """

    rows: list[DiffEngineRow]
    """Validated neutral rows in Difftastic's source alignment order.

    The engine returns these directly when non-empty. They contain no display
    syntax, folds, hunk numbering, summary, or textual substitute rows.
    """

    engine_warning: EngineWarning | None
    """Visible explanation of a recognized Difftastic degraded mode.

    `None` means the JSON did not declare graph-limit or contradictory unchanged
    output. The warning accompanies whichever rows the engine ultimately returns
    and never authorizes swallowing an invocation or validation failure.
    """


@dataclass(frozen=True)
class _Span:
    """One novel span on one source line, in Python string offsets.

    Each instance addresses a non-empty character slice and applies one token
    classification to that complete slice. It never crosses a line boundary.
    """

    start: int
    """Inclusive Python-string offset where this novel token begins.

    The value is line-local, non-negative, and interpreted against the original
    side text rather than Difftastic's byte coordinates after conversion.
    """

    end: int
    """Exclusive Python-string offset after the novel token.

    Construction rejects empty, reversed, overlapping, and out-of-line ranges,
    so row building may slice source text without clipping or repair.
    """

    status: DifftasticTokenStatus
    """Side-appropriate inline novelty assigned to the complete range.

    Row building converts it to public token status without subdividing the span;
    unchanged source between spans is emitted separately.
    """


def difftastic_engine_warning(
    diff_json: DifftasticJson,
    *,
    left_text: str | None = None,
    right_text: str | None = None,
) -> EngineWarning | None:
    """Interpret known Difftastic degraded-mode labels as an engine warning.

    The graph-limit label means difftastic abandoned structural diffing; a
    reported `unchanged` status for texts that differ means it produced no
    usable rows. Everything else is opaque metadata and returns `None`.

    # Parameters

    - `diff_json`: Validated integration result whose status and language may
      describe a known degraded mode.
    - `left_text`: Optional old text used to detect a false unchanged result.
    - `right_text`: Optional new text used with `left_text`; both must be
      present for that consistency check.

    # Returns

    - `EngineWarning`: A graph-limit or false unchanged result that requires
      textual alignment instead of structural rows.
    - `None`: Difftastic reported neither known degraded mode. The caller may
      use its rows without attaching an engine warning.
    """
    language = diff_json.get("language")
    if isinstance(language, str) and "exceeded DFT_GRAPH_LIMIT" in language:
        return {
            "type": "difftastic_graph_limit",
            "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
        }
    if (
        diff_json.get("status") == "unchanged"
        and left_text is not None
        and right_text is not None
        and left_text != right_text
    ):
        return {
            "type": "difftastic_empty_rows",
            "message": "Difftastic reported no structural changes, so dirdiff fell back to a line diff.",
        }
    return None


def _source_lines(text: str) -> list[str]:
    """Split one document into the lines difftastic positions address.

    Difftastic splits on `\\n` only; the empty element after a trailing
    newline is its phantom final line and is not a source line, so it is
    dropped here and phantom aligned pairs are dropped while building rows.
    """
    lines = text.split("\n")
    if lines != [] and lines[-1] == "":
        lines.pop()
    return lines


def _line_spans(
    side: DifftasticJsonSide,
    *,
    source_lines: list[str],
    status: DifftasticTokenStatus,
) -> list[_Span]:
    """Convert one JSON side's novel spans to string-offset `_Span` values.

    Difftastic emits UTF-8 byte columns; offsets must land on character
    boundaries of the addressed source line, and a `content` field must equal
    the addressed slice. Violations are contract errors and throw. Zero-width
    spans carry no text and are dropped.

    # Parameters

    - `side`: One Difftastic line record containing byte-column changes.
    - `source_lines`: Original document lines addressed by the record.
    - `status`: Classification assigned to every non-empty converted span.

    # Failures

    Raises `ValueError` when a line number or byte span falls outside the
    supplied source, splits a UTF-8 character, or disagrees with reported
    `content`.
    """
    changes = side["changes"]
    if changes == []:
        return []
    line_number = side["line_number"]
    if not 0 <= line_number < len(source_lines):
        raise ValueError(
            f"Difftastic change addresses missing line {line_number}."
        )
    line = source_lines[line_number]
    line_bytes = line.encode("utf-8")
    spans: list[_Span] = []
    for change in changes:
        start, end = change["start"], change["end"]
        try:
            character_start = len(line_bytes[:start].decode("utf-8"))
            character_end = len(line_bytes[:end].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Difftastic span {start}..{end} splits a character on line "
                f"{line_number}."
            ) from exc
        if start > len(line_bytes) or end > len(line_bytes):
            raise ValueError(
                f"Difftastic span {start}..{end} exceeds line {line_number}."
            )
        content = change.get("content")
        if (
            content is not None
            and content != line[character_start:character_end]
        ):
            raise ValueError(
                f"Difftastic span content {content!r} does not match source "
                f"line {line_number}."
            )
        if character_start == character_end:
            continue
        spans.append(_Span(character_start, character_end, status))
    return spans


@dataclass(frozen=True)
class _SpanIndex:
    """Novel spans for both documents, keyed by zero-based line number.

    `_span_index` derives this once from Difftastic chunks, then row building
    looks up left and right spans by source line number.

    Spans for one line are ordered by start and do not overlap. Lines without
    novel spans are absent. The index never leaves this module.
    """

    left: dict[int, list[_Span]]
    """Validated old-side novel spans keyed by zero-based source line.

    Lines absent from the mapping have no old-side novelty. Each value remains
    ordered and non-overlapping for direct row-token construction.
    """

    right: dict[int, list[_Span]]
    """Validated new-side novel spans keyed by zero-based source line.

    It is independent of `left`: paired status is chosen only when Difftastic
    reports equal span counts for the two sides of one aligned chunk entry.
    """


def _span_index(
    diff_json: DifftasticJson,
    *,
    left_lines: list[str],
    right_lines: list[str],
) -> _SpanIndex:
    """Index every chunk entry's novel spans by side and line.

    Statuses are decided per entry: spans on both sides of one aligned pair
    with equal counts render as paired `replace` tokens, unequal counts as
    `delete`/`insert`, and one-sided spans as that side's plain status. With
    whole-file context every hunk repeats the complete entry list, so an
    already-indexed identical line is accepted and a contradictory repeat is
    rejected.

    # Parameters

    - `diff_json`: Validated Difftastic chunks to index.
    - `left_lines`: Original old-side lines addressed by `lhs` records.
    - `right_lines`: Original new-side lines addressed by `rhs` records.

    # Failures

    Raises `ValueError` when a span addresses a missing line, splits a UTF-8
    character, exceeds or contradicts the supplied source, overlaps another
    span, or repeats a line with different span facts. Row building propagates
    the error because no source-faithful token index can be produced.
    """
    index = _SpanIndex(left={}, right={})

    def record(
        target: dict[int, list[_Span]],
        side: DifftasticJsonSide,
        *,
        source_lines: list[str],
        status: DifftasticTokenStatus,
    ) -> None:
        """Store one line's converted spans, rejecting contradictions.

        # Parameters

        - `target`: Side-specific mapping to update.
        - `side`: Changed-line record to convert and store.
        - `source_lines`: Original lines used to validate byte offsets.
        - `status`: Token classification for every converted span.

        # Usage

        `_span_index` calls this for each present side of every changed chunk
        entry. Repeated whole-file context may repeat the same spans exactly.

        # Failures

        Raises `ValueError` when spans overlap or a repeated line reports a
        different span set.
        """
        spans = _line_spans(side, source_lines=source_lines, status=status)
        line_number = side["line_number"]
        previous = target.get(line_number)
        if previous is None:
            ordered = sorted(spans, key=lambda span: span.start)
            for index in range(1, len(ordered)):
                if ordered[index].start < ordered[index - 1].end:
                    raise ValueError(
                        f"Difftastic spans overlap on line {line_number}."
                    )
            target[line_number] = ordered
            return
        if sorted(spans, key=lambda span: span.start) != previous:
            raise ValueError(
                f"Difftastic repeated line {line_number} with different spans."
            )

    for chunk in diff_json.get("chunks", []):
        for entry in chunk:
            lhs = entry.get("lhs")
            rhs = entry.get("rhs")
            lhs_count = 0 if lhs is None else len(lhs["changes"])
            rhs_count = 0 if rhs is None else len(rhs["changes"])
            # Difftastic pairs both sides' spans syntactically only when it
            # reports the same number of spans; unequal counts render as an
            # ordinary deletion beside an ordinary insertion.
            paired = lhs_count > 0 and rhs_count > 0 and lhs_count == rhs_count
            if lhs is not None:
                record(
                    index.left,
                    lhs,
                    source_lines=left_lines,
                    status="replace" if paired else "delete",
                )
            if rhs is not None:
                record(
                    index.right,
                    rhs,
                    source_lines=right_lines,
                    status="replace" if paired else "insert",
                )
    return index


def _line_tokens(line: str, spans: list[_Span]) -> list[InlineToken]:
    """Partition one source line into tokens at difftastic's span boundaries.

    Each novel span becomes one changed token and each non-empty gap becomes
    one unchanged token, so concatenated token text reproduces the line
    exactly. A line without novel spans returns no tokens: absent decoration
    is represented by the empty list, not by one synthetic unchanged token.

    # Parameters

    - `line`: Exact source line whose text the tokens must partition.
    - `spans`: Ordered non-overlapping novel spans within `line`.
    """

    if spans == []:
        return []

    def token(text: str, status: DifftasticTokenStatus) -> InlineToken:
        """Build one token and derive whitespace from its exact slice.

        # Parameters

        - `text`: Non-empty source slice represented by the token.
        - `status`: Difftastic classification for that slice.
        """
        return {"text": text, "status": status, "is_ws": text.isspace()}

    tokens: list[InlineToken] = []
    cursor = 0
    for span in spans:
        if span.start > cursor:
            tokens.append(token(line[cursor : span.start], "unchanged"))
        tokens.append(token(line[span.start : span.end], span.status))
        cursor = span.end
    if cursor < len(line):
        tokens.append(token(line[cursor:], "unchanged"))
    return tokens


def _row_status(
    *,
    left_no: int | None,
    right_no: int | None,
    left_text: str,
    right_text: str,
    left_tokens: list[InlineToken],
    right_tokens: list[InlineToken],
) -> DifftasticRowStatus:
    """Derive one row's status from its tokens.

    Rows with tokens classify purely by token statuses: no changed non-white
    space means `equal`; only deletions or only insertions without unchanged
    non-whitespace context keep their one-sided status; every other mix is
    `replace`. A row without any tokens is unchanged context, except a
    one-sided line with no non-whitespace content, which difftastic cannot
    mark with spans at all and whose very presence is the change, so it
    renders as its side's insertion or deletion.

    # Parameters

    - `left_no`: Old-side line number, or `None` for a new-only row.
    - `right_no`: New-side line number, or `None` for an old-only row.
    - `left_text`: Exact old-side row text, empty when absent.
    - `right_text`: Exact new-side row text, empty when absent.
    - `left_tokens`: Old-side token partition, if Difftastic reported spans.
    - `right_tokens`: New-side token partition, if Difftastic reported spans.
    """
    tokens = left_tokens + right_tokens
    if tokens == []:
        if left_no is not None and right_no is None and left_text.strip() == "":
            return "delete"
        if (
            right_no is not None
            and left_no is None
            and right_text.strip() == ""
        ):
            return "insert"
        return "equal"
    changed = {
        token["status"] for token in tokens if token["status"] != "unchanged"
    }
    if changed == set() or all(
        token["is_ws"] for token in tokens if token["status"] != "unchanged"
    ):
        return "equal"
    has_unchanged_text = any(
        token["status"] == "unchanged" and not token["is_ws"]
        for token in tokens
    )
    if changed == {"delete"} and not has_unchanged_text:
        return "delete"
    if changed == {"insert"} and not has_unchanged_text:
        return "insert"
    return "replace"


def difftastic_rows_from_json(
    diff_json: DifftasticJson,
    *,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    """Project one difftastic JSON payload into dirdiff display rows.

    One row is emitted per aligned pair whose line numbers exist in the
    sources; pairs addressing the trailing-newline phantom line are dropped.
    The alignment must cover each side's lines exactly once and in order. A
    violation means difftastic broke its alignment contract and throws
    instead of being repaired locally.

    # Parameters

    - `diff_json`: Validated Difftastic alignment and changed-span facts.
    - `left_text`: Complete old text that the alignment must cover.
    - `right_text`: Complete new text that the alignment must cover.

    # Failures

    Raises `ValueError` when Difftastic's alignment omits, repeats, reorders, or
    addresses lines outside the supplied sources, or when its span facts are
    invalid for those sources.
    """
    left_lines = _source_lines(left_text)
    right_lines = _source_lines(right_text)
    aligned = diff_json.get("aligned_lines", [])
    if aligned == []:
        return []
    spans = _span_index(
        diff_json,
        left_lines=left_lines,
        right_lines=right_lines,
    )

    rows: list[DiffEngineRow] = []
    next_left = 0
    next_right = 0
    for pair in aligned:
        left_index, right_index = pair
        if left_index is not None and left_index >= len(left_lines):
            left_index = None
        if right_index is not None and right_index >= len(right_lines):
            right_index = None
        if pair[0] is not None and left_index is None:
            # The phantom guard must only ever drop the phantom pair; pairing
            # a phantom with a real line would silently lose that line.
            if right_index is not None:
                raise ValueError(
                    "Difftastic aligned a real line with the phantom line."
                )
            continue
        if pair[1] is not None and right_index is None:
            if left_index is not None:
                raise ValueError(
                    "Difftastic aligned a real line with the phantom line."
                )
            continue
        if left_index is not None:
            if left_index != next_left:
                raise ValueError(
                    f"Difftastic alignment reached left line {left_index} "
                    f"instead of {next_left}."
                )
            next_left += 1
        if right_index is not None:
            if right_index != next_right:
                raise ValueError(
                    f"Difftastic alignment reached right line {right_index} "
                    f"instead of {next_right}."
                )
            next_right += 1

        left_line = "" if left_index is None else left_lines[left_index]
        right_line = "" if right_index is None else right_lines[right_index]
        left_tokens = (
            []
            if left_index is None
            else _line_tokens(left_line, spans.left.get(left_index, []))
        )
        right_tokens = (
            []
            if right_index is None
            else _line_tokens(right_line, spans.right.get(right_index, []))
        )
        row: DiffEngineRow = {
            "status": _row_status(
                left_no=None if left_index is None else left_index + 1,
                right_no=None if right_index is None else right_index + 1,
                left_text=left_line,
                right_text=right_line,
                left_tokens=left_tokens,
                right_tokens=right_tokens,
            ),
            "left_no": None if left_index is None else left_index + 1,
            "right_no": None if right_index is None else right_index + 1,
            "left_text": left_line,
            "right_text": right_line,
        }
        if left_index is not None:
            row["left_tokens"] = left_tokens
        if right_index is not None:
            row["right_tokens"] = right_tokens
        rows.append(row)

    if next_left != len(left_lines) or next_right != len(right_lines):
        raise ValueError(
            "Difftastic alignment did not cover every source line."
        )
    return rows


def build_difftastic_ast(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> DifftasticAst:
    """Run Difftastic on two documents and build its rows and warning.

    The texts are the content authority; path hints only steer difftastic's
    parser selection. An empty row list means Difftastic reported a known
    degraded mode. `DifftasticDiffEngine` then builds textual rows and carries
    `engine_warning` into the result.

    # Parameters

    - `left_text`: Complete old document supplied to Difftastic and row building.
    - `right_text`: Complete new document supplied likewise.
    - `left_path_hint`: Optional old filename used for parser selection.
    - `right_path_hint`: Optional new filename used for parser selection.

    # Usage

    Use this lower-level entrypoint when both sides exist and the caller needs
    Difftastic's structural result before textual degraded rows are built.
    Application composition should use `DifftasticDiffEngine.render_diff`.

    # Failures

    Raises `DirdiffError` when Difftastic cannot run or return supported JSON.
    Raises `ValueError` when its alignment or changed spans contradict the
    supplied source text.
    """
    diff_json = run_difftastic_json(
        left_text=left_text,
        right_text=right_text,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )
    return DifftasticAst(
        rows=rows,
        engine_warning=difftastic_engine_warning(
            diff_json,
            left_text=left_text,
            right_text=right_text,
        ),
    )


def _plain_line_rows_for_side(
    *,
    text: str,
    side: Literal["left", "right"],
) -> list[DiffEngineRow]:
    """Render one existing side of an added or deleted file as plain rows.

    Every line becomes one one-sided row with the side's whole-line status;
    no tokens are attached because there is nothing to pair against.

    # Parameters

    - `text`: Complete text of the only existing side.
    - `side`: Whether that text belongs to the old or new side.
    """
    rows: list[DiffEngineRow] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if side == "left":
            rows.append(
                {
                    "status": "delete",
                    "left_no": index,
                    "right_no": None,
                    "left_text": line,
                    "right_text": "",
                }
            )
        else:
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": index,
                    "left_text": "",
                    "right_text": line,
                }
            )
    return rows


def _unified_diff_rows(
    *,
    left_text: str,
    right_text: str,
    left_label: str,
    right_label: str,
) -> list[DiffEngineRow]:
    """Return textual rows when Difftastic cannot produce structural rows.

    The caller supplies both complete text sides and their display labels.
    This operation parses Python's unified-diff output directly into the
    complete neutral engine rows consumed directly by rendering.

    # Parameters

    - `left_text`: Complete old text passed to `unified_diff`.
    - `right_text`: Complete new text passed to `unified_diff`.
    - `left_label`: Old-side header label for the temporary patch.
    - `right_label`: New-side header label for the temporary patch.
    """
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
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


@final
class DifftasticDiffEngine(DiffEngineProtocol):
    """Compare text structurally using Difftastic.

    Uses `DiffSide.path_hint` to pick a right language parser for difftastic.
    When Difftastic cannot produce structural rows, the
    `DiffEngineResult.engine_warning` contains a specific warning, while the
    difftastic engine itself falls back to different kinds of textual alignment.
    Callers still receive a valid `DiffEngineResult` without mistaking it for a
    successful structural comparison.

    The engine has no workspace or request state. Obtain it through `engine()`
    when selecting an engine by name.
    """

    def _run_difftastic_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str | None,
        right_path_hint: str | None,
    ) -> DifftasticJson:
        """Run difftastic for one already-loaded text pair.

        This wrapper is intentionally small so tests or subclasses cannot
        redefine service behavior.  It isolates the subprocess integration from
        row building while keeping the public engine class final.

        # Parameters

        - `left_text`: Complete old text for the subprocess.
        - `right_text`: Complete new text for the subprocess.
        - `left_path_hint`: Optional old filename used for parser selection.
        - `right_path_hint`: Optional new filename used for parser selection.
        """
        return run_difftastic_json(
            left_text=left_text,
            right_text=right_text,
            left_path_hint=left_path_hint,
            right_path_hint=right_path_hint,
        )

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render the supplied sides with Difftastic's structural comparison.

        Path hints select the parser but are never read as files. If Difftastic
        cannot produce structural rows, return textual rows with an
        `engine_warning` that explains the degraded result.

        If one side doesn't exist, produces a trivial result of all
        insert/delete rows.

        # Parameters

        - `old`: Already-loaded old text and path hint used for parser selection.
        - `new`: Already-loaded new text and path hint used for parser selection.
        """
        # TODO: figure out if `DiffSide.exist` should exist and whether the
        # engine should be called on unpaired files in the first place.
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text
        engine_warning: EngineWarning | None = None
        rows: list[DiffEngineRow]
        if old.exists and new.exists:
            difftastic_ast = build_difftastic_ast(
                left_text=left_text_value,
                right_text=right_text_value,
                left_path_hint=old.path_hint,
                right_path_hint=new.path_hint,
            )
            engine_warning = difftastic_ast.engine_warning
            if difftastic_ast.rows == []:
                rows = _unified_diff_rows(
                    left_text=left_text_value,
                    right_text=right_text_value,
                    left_label="" if old.path_hint is None else old.path_hint,
                    right_label="" if new.path_hint is None else new.path_hint,
                )
            else:
                rows = difftastic_ast.rows
        elif old.exists:
            rows = _plain_line_rows_for_side(
                text=left_text_value,
                side="left",
            )
        else:
            rows = _plain_line_rows_for_side(
                text=right_text_value,
                side="right",
            )

        modified_lines = sum(1 for row in rows if row["status"] == "replace")
        added_lines = sum(1 for row in rows if row["status"] == "insert")
        removed_lines = sum(1 for row in rows if row["status"] == "delete")
        moved_lines = sum(1 for row in rows if row["status"] == "move")
        payload: DiffEngineResult = {
            "summary": {
                "changed_lines": (
                    modified_lines + added_lines + removed_lines + moved_lines
                ),
                "modified_lines": modified_lines,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "moved_lines": moved_lines,
            },
            "rows": rows,
        }
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        return payload
