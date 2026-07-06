"""Projection logic for Git unified patches.

`git_diff_rows_from_patch` parses unified patch text produced by Git and returns
dirdiff engine rows.  `plain_line_rows_for_side` builds the same row shape when
there is no opposite-side file to compare.  This module deliberately does not
run Git and does not attach syntax highlighting, fold rows, labels, or API
metadata.
"""

from __future__ import annotations

import re
from typing import Any, Literal

GIT_HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
    r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
)

__all__ = [
    "git_diff_rows_from_patch",
    "plain_line_rows_for_side",
]


def plain_line_rows_for_side(
    *,
    text: str,
    side: Literal["left", "right"],
) -> list[dict[str, Any]]:
    """Build engine rows for one-sided added or deleted files.

    There is no old/new pair to ask Git to compare for an added or deleted
    file.  The engine still returns the same strict row shape, with every source
    line mapped to either an insert or delete row.
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


def git_diff_rows_from_patch(patch: str) -> list[dict[str, Any]]:
    """Parse Git unified patch content into dirdiff engine rows.

    File headers and metadata are ignored.  Hunk headers reset the left/right
    line counters, and content lines become equal/delete/insert rows.  Git's
    `\\ No newline at end of file` marker is metadata about the preceding
    content line, not a row, so it is skipped.
    """
    rows: list[dict[str, Any]] = []
    left_no = 1
    right_no = 1
    in_hunk = False

    for line in patch.splitlines():
        hunk_match = GIT_HUNK_HEADER_PATTERN.match(line)
        if hunk_match is not None:
            left_no = int(hunk_match.group("left_start"))
            right_no = int(hunk_match.group("right_start"))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("\\"):
            continue

        prefix = " "
        text = ""
        if line:
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
