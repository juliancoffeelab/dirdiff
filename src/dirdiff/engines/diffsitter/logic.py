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

type DiffsitterRowStatus = Literal["equal", "insert", "delete"]
type DiffsitterTokenStatus = Literal["unchanged", "insert", "delete"]


def _source_lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines != [] and lines[-1] == "":
        lines.pop()
    return lines


def _aligned_line_pairs(
    left_lines: list[str],
    right_lines: list[str],
) -> list[tuple[int | None, int | None]]:
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
    if all(token["status"] == "delete" for token in tokens):
        return "delete"
    if all(token["status"] == "insert" for token in tokens):
        return "insert"
    return "equal"


def build_diffsitter_rows_from_json(
    *,
    diff_json: DiffsitterJson,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
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
    def _run_diffsitter_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str | None,
        right_path_hint: str | None,
    ) -> DiffsitterJson:
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

        modified_lines = added_lines = removed_lines = 0
        for row in rows:
            tokens = [
                *row.get("left_tokens", []),
                *row.get("right_tokens", []),
            ]
            if tokens == []:
                if row["status"] == "insert":
                    added_lines += 1
                elif row["status"] == "delete":
                    removed_lines += 1
                continue
            changed = {
                token["status"]
                for token in tokens
                if token["status"] != "unchanged"
            }
            changed_meaningful = [
                token
                for token in tokens
                if token["status"] != "unchanged" and not token["is_ws"]
            ]
            unchanged_meaningful = any(
                token["status"] == "unchanged" and not token["is_ws"]
                for token in tokens
            )
            if changed_meaningful == []:
                continue
            if changed == {"delete"} and not unchanged_meaningful:
                removed_lines += 1
            elif changed == {"insert"} and not unchanged_meaningful:
                added_lines += 1
            else:
                modified_lines += 1
        payload: DiffEngineResult = {
            "summary": {
                "changed_lines": modified_lines + added_lines + removed_lines,
                "modified_lines": modified_lines,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "moved_lines": 0,
            },
            "rows": rows,
            "interleave_inline_rows": False,
        }
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        return payload
