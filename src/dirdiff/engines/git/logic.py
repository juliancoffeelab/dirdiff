"""Convert Git line patches and word-display patches into engine rows.

## Public interface

`GitDiffEngine` runs Git through its private driver and returns the common
engine result. Its line-row builders reconstruct complete sources from Git's
colored unified patch or from one present side. Word mode preserves porcelain
display fragments between Git's explicit row boundaries.

## Purpose and boundaries

Git patches omit unchanged text outside their hunks, so this module restores
those gaps from the supplied strings. It rejects patches that contradict the
source and calculates the result summary. Git color slots become row
presentation data without paired move identities; porcelain records become
inline word statuses without physical-line reconstruction. The private driver
contains executable and temporary-file mechanics; syntax enrichment remains
outside the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    InlineToken,
    UnpairedMove,
)
from dirdiff.engines.git._logic.driver import run_git_no_index_diff

GIT_HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
    r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
)
"""Recognize Git unified-hunk starts and capture both source coordinates.

An omitted count means one. An explicit zero count names the line preceding an
empty range, so row building advances that side's start coordinate before
restoring unchanged gaps from the supplied source texts.
"""

__all__ = [
    "GitDiffEngine",
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
    ignore_space_change: bool,
) -> tuple[int, int]:
    """Append source-backed equal rows up to an exclusive source boundary.

    Callers provide the original lines and exclusive endpoints for a patch gap
    or a context row. The ranges must have the same length and equivalent text
    under the selected whitespace policy. Both original spellings are preserved,
    including ignored spacing.

    # Parameters

    - `rows`: Output list that receives restored unchanged rows.
    - `left_lines`: Complete old source split into display lines.
    - `right_lines`: Complete new source split into display lines.
    - `left_no`: Next one-based old line number not yet emitted.
    - `right_no`: Next one-based new line number not yet emitted.
    - `target_left_no`: Exclusive old-side line number for this range.
    - `target_right_no`: Exclusive new-side line number for this range.
    - `ignore_space_change`: Compare nonempty whitespace runs as Git `-b` does.

    # Returns

    - `First`: The next one-based old line number after the restored gap.
    - `Second`: The corresponding next new line number; callers use both values
      as cursors for the following patch hunk.

    # Failures

    Asserts when the ranges differ in length or are not equivalent under the
    selected whitespace policy, so the patch cannot describe these sources.
    """
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
    """Parse Git unified patch content into complete dirdiff engine rows.

    File headers and metadata are ignored.  Hunk headers identify where Git's
    sparse patch rows rejoin the original source texts; lines omitted before,
    between, and after hunks are emitted as equal rows from those source texts.
    Git's `\\ No newline at end of file` marker is metadata about the preceding
    content line, not a row, so it is skipped.

    # Parameters

    - `patch`: Colored unified patch using the private driver's controlled slots.
    - `ignore_space_change`: Whitespace policy used to produce this patch.
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
    """Translate Git's porcelain word patch directly into engine rows.

    Each ``~`` record terminates one upstream display row. Records beginning
    with a space are copied to both sides as unchanged text; ``-`` and ``+``
    records are copied only to their reported side. Hunk coordinates supply
    source ranges and validate the meaningful words attached to each fragment.
    The adapter does not realign, regroup, or infer relationships between rows.

    # Parameters

    - `patch`: Exact zero-context ``--word-diff=porcelain`` output from Git.
    - `ignore_space_change`: Whitespace policy used for unchanged source gaps.
    - `left_text`: Complete old source supplied to Git.
    - `right_text`: Complete new source supplied to Git.

    # Returns

    Complete unchanged source gaps surrounding patch-backed display rows. Every
    decorated row preserves one upstream ``~`` boundary and the record order,
    text, whitespace, and statuses Git emitted inside it.

    # Failures

    Raises `AssertionError` when a record kind, hunk range, or meaningful patch
    word contradicts the source supplied to Git.
    """
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
    word_pattern = re.compile(r"\S+")
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
        left_source_words = [
            (word, source_no)
            for source_no, source_line in enumerate(
                left_lines[left_start - 1 : left_start - 1 + left_count],
                start=left_start,
            )
            for word in word_pattern.findall(source_line)
        ]
        right_source_words = [
            (word, source_no)
            for source_no, source_line in enumerate(
                right_lines[right_start - 1 : right_start - 1 + right_count],
                start=right_start,
            )
            for word in word_pattern.findall(source_line)
        ]
        left_word_index = 0
        right_word_index = 0

        for group in groups:
            left_tokens: list[InlineToken] = []
            right_tokens: list[InlineToken] = []
            left_numbers: list[int] = []
            right_numbers: list[int] = []
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
                if left_status is not None:
                    for word in word_pattern.findall(payload):
                        assert left_word_index < len(left_source_words)
                        source_word, source_no = left_source_words[
                            left_word_index
                        ]
                        assert word == source_word
                        left_numbers.append(source_no)
                        left_word_index += 1
                    if payload != "":
                        left_tokens.append(
                            {
                                "text": payload,
                                "is_ws": payload.isspace(),
                                "status": left_status,
                            }
                        )
                if right_status is not None:
                    for word in word_pattern.findall(payload):
                        assert right_word_index < len(right_source_words)
                        source_word, source_no = right_source_words[
                            right_word_index
                        ]
                        assert word == source_word
                        right_numbers.append(source_no)
                        right_word_index += 1
                    if payload != "":
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
                    "left_no": None if left_numbers == [] else left_numbers[0],
                    "right_no": None
                    if right_numbers == []
                    else right_numbers[0],
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

        assert left_word_index == len(left_source_words)
        assert right_word_index == len(right_source_words)
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
    """Compare supplied text with `git diff --no-index`.

    Path hints give temporary files useful names but are never loaded. Git's
    patch is converted into the common engine rows. If one side is absent, the
    engine emits complete insertion or deletion rows without invoking Git.

    The engine retains no repository or HTTP state and works with text loaded
    by any workspace backend.
    """

    algorithm: GitAlgorithm = "myers"
    """Line-matching choice for all bays; Myers is the engine default."""

    ignore_space_change: bool = False
    """Ignore changes in whitespace amount when enabled; preserve source text."""

    word_diff: bool = False
    """Decorate changed lines with Git's porcelain word classifications."""

    color_moved: GitColorMoved = "no"
    """Git's move highlighting mode, without inferred move relationships."""

    color_moved_ws: GitColorMovedWs = "no"
    """Whitespace ignored only while Git detects moves."""

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render already-loaded text with Git's no-index diff algorithm.

        # Parameters

        - `old`: Old text side and its optional temporary-file naming hint.
        - `new`: New text side under the same contract.

        # Usage

        Obtain this renderer through `dirdiff.engines.engine` and normally let
        `dirdiff.formats.Composer` call it for a text bay.

        # Failures

        Propagates `DirdiffError` when Git cannot produce a patch. A patch that
        contradicts the supplied source violates an invariant and raises
        `AssertionError` while rows are reconstructed.
        """
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text
        if old.exists and new.exists:
            patch = run_git_no_index_diff(
                algorithm=self.algorithm,
                word_diff=self.word_diff,
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
        }
