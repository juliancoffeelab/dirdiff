from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import unified_diff
from typing import Literal, TypedDict, final, override

from dirdiff.engines.base import (
    DiffEngineProtocol,
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    EngineWarning,
    InlineToken,
)
from dirdiff.engines.difftastic._logic.driver import (
    DifftasticJson,
    DifftasticJsonSide,
    is_difftastic_json,
    run_difftastic_json,
)

type DifftasticRowStatus = Literal["equal", "insert", "delete"]
type _DifftasticNovelStatus = Literal["insert", "delete"]
type _DifftasticPartitionStatus = Literal["synthesised", "insert", "delete"]

__all__ = [
    "DifftasticDiffEngine",
    "DifftasticJson",
    "difftastic_engine_warning",
    "difftastic_rows_from_json",
    "is_difftastic_json",
]


@dataclass(frozen=True)
class DifftasticAst:
    rows: list[DiffEngineRow]

    engine_warning: EngineWarning | None


@dataclass(frozen=True)
class _Span:
    start: int

    end: int

    status: _DifftasticNovelStatus


class _DifftasticPartitionToken(TypedDict):
    text: str

    is_ws: bool

    status: _DifftasticPartitionStatus


def difftastic_engine_warning(
    diff_json: DifftasticJson,
    *,
    left_text: str | None = None,
    right_text: str | None = None,
) -> EngineWarning | None:
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


def _line_spans(
    side: DifftasticJsonSide,
    *,
    source_lines: list[str],
    status: _DifftasticNovelStatus,
) -> list[_Span]:
    changes = side["changes"]
    if changes == []:
        return []
    line_number = side["line_number"]
    if not 0 <= line_number < len(source_lines):
        raise ValueError(
            f"Difftastic change addresses missing line {line_number}."
        )
    line = source_lines[line_number]
    # Difftastic spans are UTF-8 byte columns; convert to character offsets.
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
    left: dict[int, list[_Span]]

    right: dict[int, list[_Span]]


def _span_index(
    diff_json: DifftasticJson,
    *,
    left_lines: list[str],
    right_lines: list[str],
) -> _SpanIndex:
    index = _SpanIndex(left={}, right={})

    def record(
        target: dict[int, list[_Span]],
        side: DifftasticJsonSide,
        *,
        source_lines: list[str],
        status: _DifftasticNovelStatus,
    ) -> None:
        spans = _line_spans(side, source_lines=source_lines, status=status)
        line_number = side["line_number"]
        # Whole-file context repeats every entry in each chunk, so an identical
        # repeat is expected; a different one is a contract violation.
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
            if lhs is not None:
                record(
                    index.left,
                    lhs,
                    source_lines=left_lines,
                    status="delete",
                )
            if rhs is not None:
                record(
                    index.right,
                    rhs,
                    source_lines=right_lines,
                    status="insert",
                )
    return index


def _line_tokens(
    line: str, spans: list[_Span]
) -> list[_DifftasticPartitionToken]:
    if spans == []:
        return []

    def token(
        text: str, status: _DifftasticPartitionStatus
    ) -> _DifftasticPartitionToken:
        return {"text": text, "status": status, "is_ws": text.isspace()}

    tokens: list[_DifftasticPartitionToken] = []
    cursor = 0
    for span in spans:
        if span.start > cursor:
            tokens.append(token(line[cursor : span.start], "synthesised"))
        tokens.append(token(line[span.start : span.end], span.status))
        cursor = span.end
    if cursor < len(line):
        tokens.append(token(line[cursor:], "synthesised"))
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
    tokens = left_tokens + right_tokens
    if tokens == []:
        # Difftastic cannot span a blank one-sided line; its presence is the
        # change.
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


def _real_line_pairs(
    aligned: list[list[int | None]],
    *,
    left_count: int,
    right_count: int,
) -> Iterator[tuple[int | None, int | None]]:
    next_left = 0
    next_right = 0
    for pair in aligned:
        left_index, right_index = pair
        if left_index is not None and left_index >= left_count:
            left_index = None
        if right_index is not None and right_index >= right_count:
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
        yield left_index, right_index

    if next_left != left_count or next_right != right_count:
        raise ValueError(
            "Difftastic alignment did not cover every source line."
        )


def _aligned_row(
    left_index: int | None,
    right_index: int | None,
    *,
    left_lines: list[str],
    right_lines: list[str],
    spans: _SpanIndex,
) -> DiffEngineRow:
    def public_tokens(
        tokens: list[_DifftasticPartitionToken],
    ) -> list[InlineToken]:
        result: list[InlineToken] = []
        for token in tokens:
            public_status: Literal["unchanged", "insert", "delete"]
            match token["status"]:
                case "synthesised":
                    public_status = "unchanged"
                case "insert":
                    public_status = "insert"
                case "delete":
                    public_status = "delete"
            result.append(
                {
                    "text": token["text"],
                    "status": public_status,
                    "is_ws": token["is_ws"],
                }
            )
        return result

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
    public_left_tokens = public_tokens(left_tokens)
    public_right_tokens = public_tokens(right_tokens)
    row_status = _row_status(
        left_no=None if left_index is None else left_index + 1,
        right_no=None if right_index is None else right_index + 1,
        left_text=left_line,
        right_text=right_line,
        left_tokens=public_left_tokens,
        right_tokens=public_right_tokens,
    )
    row: DiffEngineRow = {
        "status": row_status,
        "left_no": None if left_index is None else left_index + 1,
        "right_no": None if right_index is None else right_index + 1,
        "left_text": left_line,
        "right_text": right_line,
    }
    if left_index is not None:
        row["left_tokens"] = public_left_tokens
    if right_index is not None:
        row["right_tokens"] = public_right_tokens
    return row


def difftastic_rows_from_json(
    diff_json: DifftasticJson,
    *,
    left_text: str,
    right_text: str,
) -> list[DiffEngineRow]:
    def source_lines(text: str) -> list[str]:
        # Difftastic splits on `\n` only; the empty tail after a trailing
        # newline is its phantom line, not a source line.
        lines = text.split("\n")
        if lines != [] and lines[-1] == "":
            lines.pop()
        return lines

    left_lines = source_lines(left_text)
    right_lines = source_lines(right_text)
    aligned = diff_json.get("aligned_lines", [])
    if aligned == []:
        return []
    spans = _span_index(
        diff_json,
        left_lines=left_lines,
        right_lines=right_lines,
    )
    return [
        _aligned_row(
            left_index,
            right_index,
            left_lines=left_lines,
            right_lines=right_lines,
            spans=spans,
        )
        for left_index, right_index in _real_line_pairs(
            aligned,
            left_count=len(left_lines),
            right_count=len(right_lines),
        )
    ]


def build_difftastic_ast(
    *,
    ignore_comments: bool,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> DifftasticAst:
    diff_json = run_difftastic_json(
        ignore_comments=ignore_comments,
        left_text=left_text,
        right_text=right_text,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    if ignore_comments and diff_json.get("status") == "unchanged":
        # Upstream omits alignment when no structural changes remain. Preserve
        # both documents using local line alignment, with no changed rows.
        rows = _unified_diff_rows(
            left_text=left_text,
            right_text=right_text,
            left_label="" if left_path_hint is None else left_path_hint,
            right_label="" if right_path_hint is None else right_path_hint,
        )
        for row in rows:
            row["status"] = "equal"
        return DifftasticAst(rows=rows, engine_warning=None)
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
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    if left_lines == right_lines:
        return [
            {
                "status": "equal",
                "left_no": index,
                "right_no": index,
                "left_text": line,
                "right_text": line,
            }
            for index, line in enumerate(left_lines, start=1)
        ]
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
@dataclass
class DifftasticDiffEngine(DiffEngineProtocol):
    ignore_comments: bool

    # Separate method so tests can cache the raw JSON.
    def _run_difftastic_json(
        self,
        *,
        left_text: str,
        right_text: str,
        left_path_hint: str | None,
        right_path_hint: str | None,
    ) -> DifftasticJson:
        return run_difftastic_json(
            ignore_comments=self.ignore_comments,
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
        # TODO: figure out if `DiffSide.exist` should exist and whether the
        # engine should be called on unpaired files in the first place.
        left_text_value = "" if old.text is None else old.text
        right_text_value = "" if new.text is None else new.text
        engine_warning: EngineWarning | None = None
        rows: list[DiffEngineRow]
        if old.exists and new.exists:
            difftastic_ast = build_difftastic_ast(
                ignore_comments=self.ignore_comments,
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
        moved_lines = 0
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
            "interleave_inline_rows": False,
        }
        if engine_warning is not None:
            payload["engine_warning"] = engine_warning
        return payload
