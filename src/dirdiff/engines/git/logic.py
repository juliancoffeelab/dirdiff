from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import accumulate
from typing import Literal, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    GitWordBoundary,
    GitWordBoundaryDefault,
    InlineToken,
    UnpairedMove,
)
from dirdiff.engines.git._logic.driver import run_git_no_index_diff

GIT_HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
    r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
)

__all__ = [
    "GitDiffEngine",
]


def plain_line_rows_for_side(
    *,
    text: str,
    side: Literal["left", "right"],
) -> list[DiffEngineRow]:
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
    ignore_space_change: bool,
) -> tuple[int, int]:
    assert (target_left_no - left_no) == (target_right_no - right_no)
    while left_no < target_left_no:
        left_text = left_lines[left_no - 1]
        right_text = right_lines[right_no - 1]
        if ignore_space_change:
            # Git -b ignores trailing ASCII whitespace and treats other nonempty
            # runs alike. Only comparison uses these keys; rows keep exact text.
            assert re.sub(
                r"[ \t\r\v\f]+", " ", left_text.rstrip(" \t\r\v\f")
            ) == re.sub(r"[ \t\r\v\f]+", " ", right_text.rstrip(" \t\r\v\f"))
        else:
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
    ignore_space_change: bool,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    rows: list[DiffEngineRow] = []
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    # Git preserves CRLF spelling; display lines omit line terminators.
    left_patch_lines = left_text.split("\n")
    right_patch_lines = right_text.split("\n")
    left_no = 1
    right_no = 1
    in_hunk = False

    # Only Git's envelope is decoded. Source escape sequences are compared as
    # literal text, never stripped, so source cannot masquerade as decoration.
    move_colors: dict[str, UnpairedMove] = {
        "33": {"kind": "unpaired", "group": "primary", "dimmed": False},
        "34": {"kind": "unpaired", "group": "primary", "dimmed": False},
        "35": {"kind": "unpaired", "group": "alternative", "dimmed": False},
        "36": {"kind": "unpaired", "group": "alternative", "dimmed": False},
        "37": {"kind": "unpaired", "group": "primary", "dimmed": True},
        "38": {"kind": "unpaired", "group": "primary", "dimmed": True},
        "39": {"kind": "unpaired", "group": "alternative", "dimmed": True},
        "40": {"kind": "unpaired", "group": "alternative", "dimmed": True},
    }
    for line in patch.split("\n"):
        if line == "":
            continue
        hunk_match = GIT_HUNK_HEADER_PATTERN.match(line)
        if hunk_match is not None:
            target_left_no = int(hunk_match.group("left_start"))
            target_right_no = int(hunk_match.group("right_start"))
            # Empty ranges name the preceding line, not the next source line.
            target_left_no += hunk_match.group("left_count") == "0"
            target_right_no += hunk_match.group("right_count") == "0"
            left_no, right_no = _append_equal_rows(
                rows=rows,
                left_lines=left_lines,
                right_lines=right_lines,
                left_no=left_no,
                right_no=right_no,
                target_left_no=target_left_no,
                target_right_no=target_right_no,
                ignore_space_change=ignore_space_change,
            )
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("\\"):
            continue

        envelope = re.match(r"^(\x1b\[38;5;(\d+)m)?([ +-])", line)
        assert envelope is not None, f"Unsupported Git patch row: {line!r}"
        color = envelope.group(2)
        prefix = envelope.group(3)
        text = (
            left_lines[left_no - 1]
            if prefix == "-"
            else right_lines[right_no - 1]
        )
        payload = line[envelope.end() :]
        patch_text = (
            left_patch_lines[left_no - 1]
            if prefix == "-"
            else right_patch_lines[right_no - 1]
        )
        # Git emits a CRLF source's CR after its reset, outside the color span.
        if patch_text.endswith("\r"):
            assert payload.endswith("\r")
            payload = payload[:-1]
            patch_text = patch_text[:-1]
        # Git resets after every line and may separately color an added '+'.
        assert payload in (
            patch_text + "\x1b[m",
            "\x1b[m" + (envelope.group(1) or "") + patch_text + "\x1b[m",
        ), f"Git patch contradicts source at {left_no}/{right_no}"
        moved = move_colors.get(color)
        assert (
            color is None
            if prefix == " "
            else color in {"31", "32", *move_colors}
        )
        if color is not None:
            assert (int(color) % 2 == 1) == (prefix == "-")

        if prefix == " ":
            # Patch context contains only one side's spelling. Read both sources
            # so ignored whitespace does not replace the other side's content.
            left_no, right_no = _append_equal_rows(
                rows=rows,
                left_lines=left_lines,
                right_lines=right_lines,
                left_no=left_no,
                right_no=right_no,
                target_left_no=left_no + 1,
                target_right_no=right_no + 1,
                ignore_space_change=ignore_space_change,
            )
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
            if moved is not None:
                rows[-1]["status"] = "move"
                rows[-1]["move"] = moved
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
            if moved is not None:
                rows[-1]["status"] = "move"
                rows[-1]["move"] = moved
            right_no += 1

    left_no, right_no = _append_equal_rows(
        rows=rows,
        left_lines=left_lines,
        right_lines=right_lines,
        left_no=left_no,
        right_no=right_no,
        target_left_no=len(left_lines) + 1,
        target_right_no=len(right_lines) + 1,
        ignore_space_change=ignore_space_change,
    )
    assert left_no == len(left_lines) + 1
    assert right_no == len(right_lines) + 1
    return rows


def git_word_diff_rows_from_patch(
    *,
    patch: str,
    ignore_space_change: bool,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    hunks: list[tuple[int, int, int, int, list[list[str]]]] = []
    current_groups: list[list[str]] | None = None
    current_group: list[str] = []
    for line in patch.split("\n"):
        hunk_match = GIT_HUNK_HEADER_PATTERN.match(line)
        if hunk_match is not None:
            if current_groups is not None and current_group != []:
                current_groups.append(current_group)
            left_count_text = hunk_match.group("left_count")
            right_count_text = hunk_match.group("right_count")
            left_count = 1 if left_count_text is None else int(left_count_text)
            right_count = (
                1 if right_count_text is None else int(right_count_text)
            )
            left_start = int(hunk_match.group("left_start")) + (left_count == 0)
            right_start = int(hunk_match.group("right_start")) + (
                right_count == 0
            )
            current_groups = []
            current_group = []
            hunks.append(
                (
                    left_start,
                    left_count,
                    right_start,
                    right_count,
                    current_groups,
                )
            )
            continue
        if current_groups is None or line.startswith("\\"):
            continue
        if line == "~":
            current_groups.append(current_group)
            current_group = []
        elif line != "":
            current_group.append(line)
    if current_groups is not None and current_group != []:
        current_groups.append(current_group)

    rows: list[DiffEngineRow] = []
    left_no = 1
    right_no = 1

    # Records reproduce the source text of their hunk in order, split wherever
    # Git's word boundary fell and with newlines turned into `~` boundaries.
    # Counting non-whitespace characters, which every boundary preserves,
    # places each row on the source line its first character came from.
    for left_start, left_count, right_start, right_count, groups in hunks:
        left_no, right_no = _append_equal_rows(
            rows=rows,
            left_lines=left_lines,
            right_lines=right_lines,
            left_no=left_no,
            right_no=right_no,
            target_left_no=left_start,
            target_right_no=right_start,
            ignore_space_change=ignore_space_change,
        )
        # Cumulative non-whitespace character count per hunk line; a row
        # starts on the line holding the next unconsumed character.
        left_ends = list(
            accumulate(
                len("".join(line.split()))
                for line in left_lines[
                    left_start - 1 : left_start - 1 + left_count
                ]
            )
        )
        right_ends = list(
            accumulate(
                len("".join(line.split()))
                for line in right_lines[
                    right_start - 1 : right_start - 1 + right_count
                ]
            )
        )
        left_consumed = 0
        right_consumed = 0

        for group in groups:
            left_tokens: list[InlineToken] = []
            right_tokens: list[InlineToken] = []
            row_left_no: int | None = None
            row_right_no: int | None = None
            for record in group:
                assert record[0] in {" ", "-", "+"}, (
                    f"Unsupported Git word-diff record: {record!r}"
                )
                match record[0]:
                    case " ":
                        left_status: Literal["unchanged", "delete"] | None = (
                            "unchanged"
                        )
                        right_status: Literal["unchanged", "insert"] | None = (
                            "unchanged"
                        )
                    case "-":
                        left_status = "delete"
                        right_status = None
                    case "+":
                        left_status = None
                        right_status = "insert"
                    case _:
                        raise AssertionError(record)

                payload = record[1:]
                if payload == "":
                    continue
                length = len("".join(payload.split()))
                if left_status is not None:
                    if length > 0:
                        line_index = bisect_right(left_ends, left_consumed)
                        assert line_index < len(left_ends), (
                            "Record exceeds hunk"
                        )
                        if row_left_no is None:
                            row_left_no = left_start + line_index
                        left_consumed += length
                    left_tokens.append(
                        {
                            "text": payload,
                            "is_ws": payload.isspace(),
                            "status": left_status,
                        }
                    )
                if right_status is not None:
                    if length > 0:
                        line_index = bisect_right(right_ends, right_consumed)
                        assert line_index < len(right_ends), (
                            "Record exceeds hunk"
                        )
                        if row_right_no is None:
                            row_right_no = right_start + line_index
                        right_consumed += length
                    right_tokens.append(
                        {
                            "text": payload,
                            "is_ws": payload.isspace(),
                            "status": right_status,
                        }
                    )

            statuses = [
                token["status"] for token in [*left_tokens, *right_tokens]
            ]
            if statuses != [] and all(
                status == "delete" for status in statuses
            ):
                row_status: Literal["equal", "insert", "delete"] = "delete"
            elif statuses != [] and all(
                status == "insert" for status in statuses
            ):
                row_status = "insert"
            else:
                row_status = "equal"
            rows.append(
                {
                    "status": row_status,
                    "left_no": row_left_no,
                    "right_no": row_right_no,
                    "left_text": "".join(
                        token["text"] for token in left_tokens
                    ),
                    "right_text": "".join(
                        token["text"] for token in right_tokens
                    ),
                    "left_tokens": left_tokens,
                    "right_tokens": right_tokens,
                }
            )

        left_no = left_start + left_count
        right_no = right_start + right_count

    left_no, right_no = _append_equal_rows(
        rows=rows,
        left_lines=left_lines,
        right_lines=right_lines,
        left_no=left_no,
        right_no=right_no,
        target_left_no=len(left_lines) + 1,
        target_right_no=len(right_lines) + 1,
        ignore_space_change=ignore_space_change,
    )
    assert left_no == len(left_lines) + 1
    assert right_no == len(right_lines) + 1
    return rows


@final
@dataclass(frozen=True)
class GitDiffEngine(DiffEngineProtocol):
    algorithm: GitAlgorithm = "myers"

    ignore_space_change: bool = False

    word_diff: bool = False

    word_boundary: GitWordBoundary = field(
        default_factory=lambda: GitWordBoundaryDefault(mode="default")
    )

    color_moved: GitColorMoved = "no"

    color_moved_ws: GitColorMovedWs = "no"

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text
        if old.exists and new.exists:
            patch = run_git_no_index_diff(
                algorithm=self.algorithm,
                word_diff=self.word_diff,
                word_boundary=self.word_boundary,
                ignore_space_change=self.ignore_space_change,
                color_moved=self.color_moved,
                color_moved_ws=self.color_moved_ws,
                left_text=left_text_value,
                right_text=right_text_value,
                left_path_hint=old.path_hint,
                right_path_hint=new.path_hint,
            )
            if self.word_diff:
                rows = git_word_diff_rows_from_patch(
                    patch=patch,
                    ignore_space_change=self.ignore_space_change,
                    left_text=left_text_value,
                    right_text=right_text_value,
                )
            else:
                rows = git_diff_rows_from_patch(
                    patch=patch,
                    ignore_space_change=self.ignore_space_change,
                    left_text=left_text_value,
                    right_text=right_text_value,
                )
        elif old.exists:
            rows = plain_line_rows_for_side(
                text=left_text_value,
                side="left",
            )
        else:
            rows = plain_line_rows_for_side(
                text=right_text_value,
                side="right",
            )
        added_lines = sum(1 for row in rows if row["status"] == "insert")
        removed_lines = sum(1 for row in rows if row["status"] == "delete")
        if self.word_diff:
            modified_lines = sum(
                1
                for row in rows
                if row["status"] == "equal"
                and any(
                    token["status"] != "unchanged"
                    for token in [
                        *row.get("left_tokens", []),
                        *row.get("right_tokens", []),
                    ]
                )
            )
        else:
            modified_lines = 0
        moved_lines = sum(1 for row in rows if row["status"] == "move")
        return {
            "summary": {
                "changed_lines": (
                    added_lines + removed_lines + modified_lines + moved_lines
                ),
                "modified_lines": modified_lines,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "moved_lines": moved_lines,
            },
            "rows": rows,
            # Word mode copies Git's own `~`-terminated display lines; every
            # other mode emits physical source lines.
            "interleave_inline_rows": self.word_diff,
        }
