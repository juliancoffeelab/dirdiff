"""Build dirdiff engine rows from Diffsitter's reported graphemes.

## Public interface

`DiffsitterDiffEngine` implements the common engine protocol on top of the
`diffsitter` executable. `build_diffsitter_rows_from_json` turns one validated
`DiffsitterJson` result plus the exact source pair into neutral engine rows,
and `DiffsitterJson` with `is_diffsitter_json` expose the raw validated shape
for consumers such as the test JSON cache.

## Purpose and boundaries

Diffsitter reports which graphemes of each document have no match in the
other; it reports no alignment, replacement, or move. This module therefore
aligns physical lines locally with `difflib.unified_diff`, maps every `Old`
entry to one `delete` token and every `New` entry to one `insert` token, and
derives row status from those tokens. It never merges graphemes, infers a
replacement, or reads syntax kinds. The supplied source strings decide every
displayed character; Diffsitter's coordinates are validated against them and
rejected on disagreement. The private driver contains executable discovery,
temporary inputs, and raw JSON validation. This module retains no workspace
files, syntax decoration, folds, or HTTP state.
"""

from __future__ import annotations

import re
from difflib import unified_diff
from typing import Literal, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    DirdiffError,
    EngineWarning,
    InlineToken,
)
from dirdiff.engines.diffsitter._logic.driver import (
    DiffsitterJson,
    DiffsitterUnsupportedError,
    is_diffsitter_json,
    run_diffsitter_json,
)

__all__ = [
    "DiffsitterDiffEngine",
    "DiffsitterJson",
    "build_diffsitter_rows_from_json",
    "is_diffsitter_json",
]

type DiffsitterRowStatus = Literal["equal", "replace", "insert", "delete"]
"""Row statuses Diffsitter row building can produce.

- `equal` rows carry no changed non-whitespace token.
- `delete` and `insert` rows carry only one kind of changed token and no
  unchanged non-whitespace context.
- `replace` rows carry any other changed mixture.

Diffsitter reports no moves, so the public `move` status never appears.
"""
type DiffsitterTokenStatus = Literal["unchanged", "insert", "delete"]
"""Classification of one exact text slice while building Diffsitter rows.

- `unchanged` fills the gaps around reported entries.
- `delete` is every `Old` entry; `insert` is every `New` entry.

Diffsitter reports no pairing between entries, so `replace` tokens are never
produced.
"""


def _source_lines(text: str) -> list[str]:
    """Split one document into the lines Diffsitter positions address.

    Tree-sitter counts rows by `\\n` only, so a carriage return stays inside
    its line. The empty element after a trailing newline is not a source line
    and is dropped.

    # Parameters

    - `text`: Complete source text of one side.
    """
    lines = text.split("\n")
    if lines != [] and lines[-1] == "":
        lines.pop()
    return lines


def _aligned_line_pairs(
    left_lines: list[str],
    right_lines: list[str],
) -> list[tuple[int | None, int | None]]:
    """Align two line sequences into physical row pairs with `unified_diff`.

    Every line index of both sides appears exactly once, in ascending order,
    either paired with the identical line on the other side or alone. The
    result carries zero-based indices only; token and row status are decided
    by the caller. Identical sequences produce no patch hunks, so they are
    paired explicitly rather than mistaken for empty documents.

    # Parameters

    - `left_lines`: Old-side source lines from `_source_lines`.
    - `right_lines`: New-side source lines from `_source_lines`.

    # Returns

    - Each element is one physical row in display order.
    - The first item of a pair is the zero-based old line index, or `None`
      when the row is new-only.
    - The second item is the zero-based new line index, or `None` when the
      row is old-only.
    """
    if left_lines == right_lines:
        return [(index, index) for index in range(len(left_lines))]
    patch_lines = unified_diff(
        left_lines,
        right_lines,
        lineterm="",
        n=max(len(left_lines), len(right_lines)),
    )
    hunk_header_pattern = re.compile(
        r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
        r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
    )
    pairs: list[tuple[int | None, int | None]] = []
    left_index = 0
    right_index = 0
    in_hunk = False
    for line in patch_lines:
        hunk_match = hunk_header_pattern.match(line)
        if hunk_match is not None:
            # A zero-length range names the line it follows, so an empty side
            # reports `-0,0`; a non-empty range names its first line.
            left_index = int(hunk_match.group("left_start"))
            if hunk_match.group("left_count") != "0":
                left_index -= 1
            right_index = int(hunk_match.group("right_start"))
            if hunk_match.group("right_count") != "0":
                right_index -= 1
            in_hunk = True
            continue
        if not in_hunk or line.startswith("\\"):
            continue
        prefix = " " if line == "" else line[0]
        if prefix == " ":
            pairs.append((left_index, right_index))
            left_index += 1
            right_index += 1
        elif prefix == "-":
            pairs.append((left_index, None))
            left_index += 1
        elif prefix == "+":
            pairs.append((None, right_index))
            right_index += 1
    assert left_index == len(left_lines) and right_index == len(right_lines)
    return pairs


def _row_status(
    *,
    left_no: int | None,
    right_no: int | None,
    left_text: str,
    right_text: str,
    left_tokens: list[InlineToken],
    right_tokens: list[InlineToken],
) -> DiffsitterRowStatus:
    """Derive one row's status from its tokens with the Difftastic rule.

    Rows with tokens classify purely by token statuses: no changed
    non-whitespace token means `equal`; only deletions or only insertions
    without unchanged non-whitespace context keep their one-sided status;
    every other mix is `replace`. A row without any tokens is unchanged
    context, except a one-sided line with no non-whitespace content, which
    Diffsitter cannot report at all because it strips whitespace and whose
    very presence is the change, so it renders as its side's insertion or
    deletion. Token statuses are read, never altered.

    # Parameters

    - `left_no`: Old-side line number, or `None` for a new-only row.
    - `right_no`: New-side line number, or `None` for an old-only row.
    - `left_text`: Exact old-side row text, empty when absent.
    - `right_text`: Exact new-side row text, empty when absent.
    - `left_tokens`: Old-side token partition, if Diffsitter reported entries.
    - `right_tokens`: New-side token partition, if Diffsitter reported entries.
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


def build_diffsitter_rows_from_json(
    *,
    diff_json: DiffsitterJson,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    """Build complete aligned engine rows from one Diffsitter result.

    The echoed documents must equal the supplied texts. Physical rows come
    from a local unified diff of the `\\n`-split lines, so every source line
    of both sides appears exactly once in order. Each reported entry becomes
    one token on the row holding its line: `Old` entries are `delete` tokens
    and `New` entries are `insert` tokens, in Diffsitter's reported order,
    with unchanged gaps between them. Row status follows the Difftastic rule
    over those tokens. Lines without entries carry no tokens.

    # Parameters

    - `diff_json`: Validated Diffsitter output for exactly this text pair.
    - `left_text`: Complete old source the JSON describes.
    - `right_text`: Complete new source the JSON describes.

    # Usage

    `DiffsitterDiffEngine` calls this after deciding that Diffsitter reported
    at least one entry. Tests call it with cached JSON to check the mapping
    without running the executable.

    # Failures

    Raises `DirdiffError` when an echoed document differs from the supplied
    text, a line or column lies outside the source, an entry spans rows,
    entries on one line overlap or run backwards, or entry text disagrees
    with the addressed source bytes.
    """
    if diff_json["old"]["text"] != left_text:
        raise DirdiffError(
            "Diffsitter echoed an old document that differs from the "
            "supplied source."
        )
    if diff_json["new"]["text"] != right_text:
        raise DirdiffError(
            "Diffsitter echoed a new document that differs from the "
            "supplied source."
        )
    left_lines = _source_lines(left_text)
    right_lines = _source_lines(right_text)

    def tokens_by_line(
        side: Literal["Old", "New"],
        source_lines: list[str],
        status: DiffsitterTokenStatus,
    ) -> dict[int, list[InlineToken]]:
        """Partition every line with entries on one side into tokens.

        Entries are consumed in reported order across all hunks of `side`.
        Each entry becomes one token with `status`; every non-empty gap
        before, between, and after entries becomes one `unchanged` token, so
        the tokens of a line concatenate to that exact line. Lines without
        entries are absent from the result.

        # Parameters

        - `side`: Hunk variant selecting which document's entries to read.
        - `source_lines`: Exact `\\n`-split lines of that document.
        - `status`: Token status every entry of this side receives.

        # Returns

        - Keys are zero-based indices of the lines with at least one hunk
          record on this side.
        - Values are that line's complete token partition, or an empty list
          when the record carried no entries.

        # Failures

        Raises `DirdiffError` for any coordinate the source cannot satisfy.
        """
        label = "old" if side == "Old" else "new"
        tokens: dict[int, list[InlineToken]] = {}
        # Byte offset just after the last consumed entry of each line.
        cursors: dict[int, int] = {}
        for hunk in diff_json["hunks"]:
            for line in hunk.get(side, []):
                line_index = line["line_index"]
                if not 0 <= line_index < len(source_lines):
                    raise DirdiffError(
                        f"Diffsitter reported line {line_index} outside the "
                        f"{label} source."
                    )
                source_line = source_lines[line_index]
                line_bytes = source_line.encode("utf-8")
                line_tokens = tokens.setdefault(line_index, [])
                cursor = cursors.get(line_index, 0)
                for entry in line["entries"]:
                    start = entry["start_position"]
                    end = entry["end_position"]
                    if start["row"] != line_index or end["row"] != line_index:
                        raise DirdiffError(
                            f"Diffsitter entry rows {start['row']}-{end['row']} "
                            f"disagree with {label} line {line_index}."
                        )
                    if not (
                        cursor
                        <= start["column"]
                        < end["column"]
                        <= len(line_bytes)
                    ):
                        raise DirdiffError(
                            f"Diffsitter entry columns {start['column']}-"
                            f"{end['column']} are invalid for {label} line "
                            f"{line_index}."
                        )
                    if line_bytes[start["column"] : end["column"]] != entry[
                        "text"
                    ].encode("utf-8"):
                        raise DirdiffError(
                            f"Diffsitter entry text {entry['text']!r} disagrees "
                            f"with {label} line {line_index} at column "
                            f"{start['column']}."
                        )
                    if start["column"] > cursor:
                        gap = line_bytes[cursor : start["column"]].decode(
                            "utf-8"
                        )
                        line_tokens.append(
                            {
                                "text": gap,
                                "status": "unchanged",
                                "is_ws": gap.isspace(),
                            }
                        )
                    line_tokens.append(
                        {
                            "text": entry["text"],
                            "status": status,
                            "is_ws": entry["text"].isspace(),
                        }
                    )
                    cursor = end["column"]
                cursors[line_index] = cursor
        for line_index, line_tokens in tokens.items():
            if line_tokens == []:
                continue
            line_bytes = source_lines[line_index].encode("utf-8")
            cursor = cursors[line_index]
            if cursor < len(line_bytes):
                tail = line_bytes[cursor:].decode("utf-8")
                line_tokens.append(
                    {
                        "text": tail,
                        "status": "unchanged",
                        "is_ws": tail.isspace(),
                    }
                )
        return tokens

    left_tokens_by_line = tokens_by_line("Old", left_lines, "delete")
    right_tokens_by_line = tokens_by_line("New", right_lines, "insert")

    rows: list[DiffEngineRow] = []
    for left_index, right_index in _aligned_line_pairs(left_lines, right_lines):
        left_line = "" if left_index is None else left_lines[left_index]
        right_line = "" if right_index is None else right_lines[right_index]
        left_tokens = (
            []
            if left_index is None
            else left_tokens_by_line.get(left_index, [])
        )
        right_tokens = (
            []
            if right_index is None
            else right_tokens_by_line.get(right_index, [])
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
    return rows


@final
class DiffsitterDiffEngine(DiffEngineProtocol):
    """Compare text through Diffsitter's grapheme-level edit report.

    Path hints select each side's grammar through the temporary filename and
    are never read as files. Empty or absent sides and identical texts are
    rendered locally without running the executable. An unsupported language
    or a report without edits for differing texts produces unified-diff rows
    with the corresponding warning.

    The engine holds no state. Obtain it through `engine()` when selecting an
    engine by name.
    """

    def _run_diffsitter_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str | None,
        right_path_hint: str | None,
    ) -> DiffsitterJson:
        """Run Diffsitter for one already-loaded text pair.

        The method exists so behavior tests can cache the same raw JSON that
        powers rendering without duplicating executable discovery.

        # Parameters

        - `left_text`: Complete old source for Diffsitter.
        - `right_text`: Complete new source for Diffsitter.
        - `left_path_hint`: Optional old name used for grammar selection.
        - `right_path_hint`: Optional new name used for grammar selection.
        """
        return run_diffsitter_json(
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
        """Render the supplied sides with Diffsitter's grapheme edits.

        When both texts are non-empty and differ, Diffsitter runs and
        `build_diffsitter_rows_from_json` maps its entries onto locally aligned
        rows. Otherwise the rows are local: an empty or absent side yields
        one-sided rows and identical texts yield equal rows, both without a
        warning. An unsupported language yields unified-diff rows with the
        `diffsitter_unsupported_language` warning; differing texts without
        any reported entry yield them with `diffsitter_empty_result`.

        # Parameters

        - `old`: Already-loaded old text and path hint used for grammar selection.
        - `new`: Already-loaded new text and path hint used for grammar selection.

        # Failures

        Propagates `DirdiffError` for missing executables, other process
        failures, malformed JSON, and coordinates the source cannot satisfy.
        """
        assert old.exists == (old.text is not None)
        assert new.exists == (new.text is not None)
        assert old.exists or new.exists
        left_text = "" if old.text is None else old.text
        right_text = "" if new.text is None else new.text
        left_lines = _source_lines(left_text)
        right_lines = _source_lines(right_text)

        engine_warning: EngineWarning | None = None
        rows: list[DiffEngineRow] | None = None
        if left_text != "" and right_text != "" and left_text != right_text:
            try:
                diff_json = self._run_diffsitter_json(
                    left_text=left_text,
                    right_text=right_text,
                    left_path_hint=old.path_hint,
                    right_path_hint=new.path_hint,
                )
            except DiffsitterUnsupportedError as error:
                engine_warning = {
                    "type": "diffsitter_unsupported_language",
                    "message": str(error),
                }
            else:
                if any(
                    line["entries"] != []
                    for hunk in diff_json["hunks"]
                    for lines in (hunk.get("Old", []), hunk.get("New", []))
                    for line in lines
                ):
                    rows = build_diffsitter_rows_from_json(
                        diff_json=diff_json,
                        left_text=left_text,
                        right_text=right_text,
                    )
                else:
                    engine_warning = {
                        "type": "diffsitter_empty_result",
                        "message": (
                            "Diffsitter reported no edits for texts that "
                            "differ. Showing a unified text diff."
                        ),
                    }
        if rows is None:
            # Textual rows: the unified alignment alone decides status.
            rows = []
            for left_index, right_index in _aligned_line_pairs(
                left_lines, right_lines
            ):
                if left_index is not None and right_index is not None:
                    status: DiffsitterRowStatus = "equal"
                elif left_index is not None:
                    status = "delete"
                else:
                    status = "insert"
                rows.append(
                    {
                        "status": status,
                        "left_no": None
                        if left_index is None
                        else left_index + 1,
                        "right_no": (
                            None if right_index is None else right_index + 1
                        ),
                        "left_text": (
                            "" if left_index is None else left_lines[left_index]
                        ),
                        "right_text": (
                            ""
                            if right_index is None
                            else right_lines[right_index]
                        ),
                    }
                )

        modified_lines = sum(1 for row in rows if row["status"] == "replace")
        added_lines = sum(1 for row in rows if row["status"] == "insert")
        removed_lines = sum(1 for row in rows if row["status"] == "delete")
        payload: DiffEngineResult = {
            "summary": {
                "changed_lines": modified_lines + added_lines + removed_lines,
                "modified_lines": modified_lines,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "moved_lines": 0,
            },
            "rows": rows,
        }
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        return payload
