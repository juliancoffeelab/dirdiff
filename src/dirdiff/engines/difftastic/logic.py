"""Difftastic JSON to dirdiff row AST contract.

This module defines the boundary between raw difftastic output and the rendered
row AST used by the difftastic service. It accepts difftastic-shaped JSON plus
the original source text and returns dirdiff-shaped rows.

This module must not own raw difftastic execution or final API payload assembly:

* `dirdiff.engines.difftastic.difft` owns invoking `difft` and parsing its JSON.
* the service/textdiff layer owns syntax highlighting, fold hints, and frontend
  payload assembly.

Projection model
----------------
Difftastic (with dirdiff's whole-file context tuning) emits one complete
alignment of both documents plus, per aligned line, the exact spans it
considers novel. The projection is a direct transcription of those facts:

* one output row per in-range aligned pair, in alignment order;
* row text is the exact full source line on each present side (`""` marks an
  absent side);
* tokens on a present side partition its line at difftastic's span boundaries:
  each novel span becomes one changed token and each gap becomes one unchanged
  token; a line without novel spans has no tokens;
* row status is a pure function of the row's tokens (see `_row_status`).

There is deliberately no repair, reflow, fragment, or similarity machinery
here. Difftastic's alignment covers each source line exactly once and in
order; the projection asserts that contract instead of compensating for its
absence, so an upstream misbehavior surfaces as a visible failure rather than
a silent local workaround.

Input contract
--------------
The main entrypoint is `build_difftastic_ast`:

* `left_text` and `right_text` are the complete source documents. They are the
  authority for line text, line counts, and user-visible content.
* `left_path_hint` and `right_path_hint` are file-name hints for difftastic
  parser selection.

Accepted difftastic facts
-------------------------
`DifftasticJson.aligned_lines` contains zero-based line index pairs. `None`
means there is no line on that side. Pairs addressing the phantom line created
by a trailing newline are dropped.

`DifftasticJson.chunks` contains one entry per aligned pair, keyed by
difftastic side names: `lhs` for the left/old document and `rhs` for the
right/new document. Each present side lists that line's novel spans. The span
offsets are UTF-8 byte positions; the projection converts them to Python
string offsets once, at ingestion. With whole-file context every hunk repeats
the complete pair list, so identical repeated facts are accepted and
contradictory repeated facts are rejected.

The `language` field is opaque except for known difftastic fallback labels
that may be exposed through `DifftasticAst.engine_warning`.

Output contract
---------------
`build_difftastic_ast` returns `DifftasticAst`:

* `rows`: a list of `DifftasticRow` values.
* `engine_warning`: optional metadata for known difftastic fallback modes.

Each `DifftasticRow` is a display row. Its fields are:

* `status`: one of `equal`, `replace`, `insert`, or `delete`.
* `left_no` and `right_no`: one-based source line numbers, or `None` for
  one-sided rows.
* `left_text` and `right_text`: the exact source line shown on each side, or
  `""` for an absent side.
* `left_tokens` and `right_tokens`: inline token lists, present exactly for
  the sides that exist. The concatenated token text of a non-empty list is
  that side's complete line text.

Required invariants
-------------------
* row source text is the supplied source text, one full line per present side;
* every source line appears exactly once per side, in source order;
* changed tokens cover exactly the spans difftastic reported as novel;
* row status is derived from token statuses and never contradicts them;
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
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import unified_diff
from typing import Any, Literal, TypedDict, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffSide,
    EngineWarning,
    strict_engine_rows,
)
from dirdiff.engines.difftastic.difft import (
    DifftasticJson,
    DifftasticJsonSide,
    run_difftastic_json,
)

type DifftasticRowStatus = Literal["equal", "replace", "insert", "delete"]
type DifftasticTokenStatus = Literal["unchanged", "replace", "insert", "delete"]

__all__ = [
    "DifftasticAst",
    "DifftasticDiffEngine",
    "DifftasticInlineToken",
    "DifftasticRow",
    "build_difftastic_ast",
]


class DifftasticInlineToken(TypedDict):
    """One inline token of a rendered difftastic row.

    `text` is an exact slice of the owning line, `status` is difftastic's
    verdict for that slice, and `is_ws` marks a purely whitespace slice. A
    token never represents text from another line or invented content.
    """

    text: str
    status: DifftasticTokenStatus
    is_ws: bool


class DifftasticRow(TypedDict, total=False):
    """Rendered row shape exported from difftastic logic to the service.

    One row renders one aligned line pair. Absent sides carry `None` line
    numbers, `""` text, and no token key; present sides carry the full source
    line and a token list that is empty when difftastic reported no novel
    span on the line.
    """

    status: DifftasticRowStatus
    left_no: int | None
    right_no: int | None
    left_text: str
    right_text: str
    left_tokens: list[DifftasticInlineToken]
    right_tokens: list[DifftasticInlineToken]


@dataclass(frozen=True)
class DifftasticAst:
    """Complete projection result for one difftastic run.

    `rows` is the exported row AST; an empty list means difftastic produced
    no structural rows and the service chooses a fallback renderer.
    `engine_warning` reports a known difftastic fallback mode, or `None`.
    """

    rows: list[DifftasticRow]
    engine_warning: EngineWarning | None


@dataclass(frozen=True)
class _Span:
    """One novel span on one source line, in Python string offsets.

    `start`/`end` address the owning line's characters and satisfy
    `start < end`. `status` is the token status every character of the span
    renders with. The span never crosses a line boundary.
    """

    start: int
    end: int
    status: DifftasticTokenStatus


def _difftastic_engine_warning(
    diff_json: DifftasticJson,
    *,
    left_text: str | None = None,
    right_text: str | None = None,
) -> EngineWarning | None:
    """Interpret known difftastic fallback labels as an engine warning.

    The graph-limit label means difftastic abandoned structural diffing; a
    reported `unchanged` status for texts that differ means it produced no
    usable rows. Everything else is opaque metadata and returns `None`.
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
    dropped here and phantom aligned pairs are dropped during projection.
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

    Each list is ordered by span start and non-overlapping; lines without
    novel spans are absent. The index is derived once from `chunks` and read
    by row projection.
    """

    left: dict[int, list[_Span]]
    right: dict[int, list[_Span]]


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
    """
    index = _SpanIndex(left={}, right={})

    def record(
        target: dict[int, list[_Span]],
        side: DifftasticJsonSide,
        *,
        source_lines: list[str],
        status: DifftasticTokenStatus,
    ) -> None:
        """Store one line's converted spans, rejecting contradictions."""
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


def _line_tokens(line: str, spans: list[_Span]) -> list[DifftasticInlineToken]:
    """Partition one source line into tokens at difftastic's span boundaries.

    Each novel span becomes one changed token and each non-empty gap becomes
    one unchanged token, so concatenated token text reproduces the line
    exactly. A line without novel spans returns no tokens: absent decoration
    is represented by the empty list, not by one synthetic unchanged token.
    """
    if spans == []:
        return []

    def token(
        text: str, status: DifftasticTokenStatus
    ) -> DifftasticInlineToken:
        """Build one token; `is_ws` is derived from the exact slice."""
        return {"text": text, "status": status, "is_ws": text.isspace()}

    tokens: list[DifftasticInlineToken] = []
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
    left_tokens: list[DifftasticInlineToken],
    right_tokens: list[DifftasticInlineToken],
) -> DifftasticRowStatus:
    """Derive one row's status from its tokens.

    Rows with tokens classify purely by token statuses: no changed non-white
    space means `equal`; only deletions or only insertions without unchanged
    non-whitespace context keep their one-sided status; every other mix is
    `replace`. A row without any tokens is unchanged context — except a
    one-sided line with no non-whitespace content, which difftastic cannot
    mark with spans at all and whose very presence is the change, so it
    renders as its side's insertion or deletion.
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


def _difftastic_rows_from_json(
    diff_json: DifftasticJson,
    *,
    left_text: str,
    right_text: str,
) -> list[DifftasticRow]:
    """Project one difftastic JSON payload into dirdiff display rows.

    One row is emitted per aligned pair whose line numbers exist in the
    sources; pairs addressing the trailing-newline phantom line are dropped.
    The alignment must cover each side's lines exactly once and in order —
    a violation means difftastic broke its alignment contract and throws
    instead of being repaired locally.
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

    rows: list[DifftasticRow] = []
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
        row: DifftasticRow = {
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
    """Run difftastic on two documents and project its rows and warning.

    The texts are the content authority; path hints only steer difftastic's
    parser selection. An empty row list means the caller must pick a fallback
    renderer, with `engine_warning` explaining a known fallback mode.
    """
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
        engine_warning=_difftastic_engine_warning(
            diff_json,
            left_text=left_text,
            right_text=right_text,
        ),
    )


def _plain_line_rows_for_side(
    *,
    text: str,
    side: Literal["left", "right"],
) -> list[dict[str, Any]]:
    """Render one existing side of an added or deleted file as plain rows.

    Every line becomes one one-sided row with the side's whole-line status;
    no tokens are attached because there is nothing to pair against.
    """
    rows: list[dict[str, Any]] = []
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
) -> list[dict[str, Any]]:
    """Return textual rows when Difftastic cannot produce structural rows.

    The caller supplies both complete text sides and their display labels.
    This operation parses Python's unified-diff output directly into the
    neutral engine-row fields consumed by `strict_engine_rows()`.
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
    rows: list[dict[str, Any]] = []
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
    """Structural renderer backed by difftastic.

    The renderer entrypoint runs difftastic on already-loaded text and projects
    its structural output into the row model shared by the rest of dirdiff.

    Difftastic can fail or decline to produce rows for some inputs.  That is
    represented as an engine warning plus a textual fallback, keeping the REST
    response renderable while still being honest about the engine result.
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
        the payload projection while keeping the public service class final.
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
        """Render an already-loaded pair with difftastic.

        The only inputs this method trusts are the text strings, existence
        flags, labels, and path hints supplied by the caller.  Path hints are
        passed to difftastic for language/parser selection, but this method does
        not load those paths.

        If difftastic cannot produce usable rows, the renderer falls back to a
        Git-style textual alignment so the API still returns a renderable file
        diff.  Notebook detection is intentionally outside this method and
        happens in server orchestration before an engine is selected.
        """
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text
        engine_warning: EngineWarning | None = None
        rows: Iterable[object]
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

        engine_rows = strict_engine_rows(rows)
        modified_lines = sum(
            1 for row in engine_rows if row["status"] == "replace"
        )
        added_lines = sum(1 for row in engine_rows if row["status"] == "insert")
        removed_lines = sum(
            1 for row in engine_rows if row["status"] == "delete"
        )
        moved_lines = sum(1 for row in engine_rows if row["status"] == "move")
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
            "rows": engine_rows,
        }
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        return payload
