"""Convert Git unified patches into complete engine rows.

## Public interface

`git_diff_rows_from_patch` combines one patch with the exact source pair that
produced it. `plain_line_rows_for_side` produces the same row shape for an added
or removed File.

## Purpose and boundaries

A unified patch omits unchanged text outside its hunks, while dirdiff rows must
cover both complete sources. This module restores those gaps from the supplied
strings and rejects a patch that contradicts them. It does not invoke Git or
add display decoration.
"""

from __future__ import annotations

import re
from typing import Literal

from dirdiff.engines.base import DiffEngineRow

GIT_HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
    r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
)
"""Recognize Git unified-hunk starts and capture both source coordinates.

The optional counts do not affect row placement. Row building uses the two start
coordinates to restore unchanged gaps from the supplied source texts.
"""

__all__ = [
    "git_diff_rows_from_patch",
    "plain_line_rows_for_side",
]


def plain_line_rows_for_side(
    *,
    text: str,
    side: Literal["left", "right"],
) -> list[DiffEngineRow]:
    """Build engine rows for one-sided added or deleted files.

    There is no old/new pair to ask Git to compare for an added or deleted
    file.  The engine still returns the same strict row shape, with every source
    line mapped to either an insert or delete row.

    # Parameters

    - `text`: Complete text of the only existing side.
    - `side`: Whether the text belongs to the old or new side.

    # Usage

    Git and Difftastic engines use this shape when exactly one side exists.
    Pass the complete present-side text; do not call Git for the absent side.
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
                    "left_tokens": [],
                    "right_tokens": [],
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
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
    return rows


def _append_equal_rows(
    *,
    rows: list[DiffEngineRow],
    left_lines: list[str],
    right_lines: list[str],
    left_no: int,
    right_no: int,
    target_left_no: int,
    target_right_no: int,
) -> tuple[int, int]:
    """Append source-backed equal rows until the next patch hunk starts.

    Git patches omit unchanged file regions outside hunk context.  Those gaps
    are still part of the rendered source, so callers provide the
    original left/right lines and the next hunk's line numbers.  The two gaps
    must have the same length and text because Git only omits unchanged
    regions; a mismatch means the patch no longer matches its source inputs.

    # Parameters

    - `rows`: Output list that receives restored unchanged rows.
    - `left_lines`: Complete old source split into display lines.
    - `right_lines`: Complete new source split into display lines.
    - `left_no`: Next one-based old line number not yet emitted.
    - `right_no`: Next one-based new line number not yet emitted.
    - `target_left_no`: Old line number at the next hunk boundary.
    - `target_right_no`: New line number at the next hunk boundary.

    # Returns

    - `First`: The next one-based old line number after the restored gap.
    - `Second`: The corresponding next new line number; callers use both values
      as cursors for the following patch hunk.

    # Failures

    Asserts when Git's omitted old and new gaps have different lengths or text,
    because the patch would no longer describe the supplied sources.
    """
    assert (target_left_no - left_no) == (target_right_no - right_no)
    while left_no < target_left_no:
        left_text = left_lines[left_no - 1]
        right_text = right_lines[right_no - 1]
        assert left_text == right_text
        rows.append(
            {
                "status": "equal",
                "left_no": left_no,
                "right_no": right_no,
                "left_text": left_text,
                "right_text": right_text,
                "left_tokens": [],
                "right_tokens": [],
            }
        )
        left_no += 1
        right_no += 1
    return left_no, right_no


def git_diff_rows_from_patch(
    *,
    patch: str,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    """Parse Git unified patch content into complete dirdiff engine rows.

    File headers and metadata are ignored.  Hunk headers identify where Git's
    sparse patch rows rejoin the original source texts; lines omitted before,
    between, and after hunks are emitted as equal rows from those source texts.
    Git's `\\ No newline at end of file` marker is metadata about the preceding
    content line, not a row, so it is skipped.

    # Parameters

    - `patch`: Unified patch produced for the two supplied source texts.
    - `left_text`: Complete old text used to restore omitted equal lines.
    - `right_text`: Complete new text used to restore omitted equal lines.

    # Usage

    Call this with the patch and the exact source strings used by
    `run_git_no_index_diff`. The returned rows cover both sources completely,
    including unchanged text omitted from the patch.

    # Failures

    Raises `AssertionError` when hunk coordinates or omitted equal text do not
    agree with the supplied source pair.
    """
    rows: list[DiffEngineRow] = []
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    left_no = 1
    right_no = 1
    in_hunk = False

    for line in patch.splitlines():
        hunk_match = GIT_HUNK_HEADER_PATTERN.match(line)
        if hunk_match is not None:
            target_left_no = int(hunk_match.group("left_start"))
            target_right_no = int(hunk_match.group("right_start"))
            left_no, right_no = _append_equal_rows(
                rows=rows,
                left_lines=left_lines,
                right_lines=right_lines,
                left_no=left_no,
                right_no=right_no,
                target_left_no=target_left_no,
                target_right_no=target_right_no,
            )
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("\\"):
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
                    "left_tokens": [],
                    "right_tokens": [],
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
                    "left_tokens": [],
                    "right_tokens": [],
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
                    "left_tokens": [],
                    "right_tokens": [],
                }
            )
            right_no += 1

    left_no, right_no = _append_equal_rows(
        rows=rows,
        left_lines=left_lines,
        right_lines=right_lines,
        left_no=left_no,
        right_no=right_no,
        target_left_no=len(left_lines) + 1,
        target_right_no=len(right_lines) + 1,
    )
    assert left_no == len(left_lines) + 1
    assert right_no == len(right_lines) + 1
    return rows
