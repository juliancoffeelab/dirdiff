from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from dirdiff.fold import FoldHint, fold_hints_for_path
from dirdiff.highlight import highlight_lines_for_path
from dirdiff.services.base import (
    DiffServiceProtocol,
    _file_kind_for_change_type,
    _perf_log,
    build_lazy_info_for_service,
    build_repo_manifest_for_service,
)
from dirdiff.sources import (
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackend,
    _display_name_for_repo_paths,
)

INLINE_TOKEN_PATTERN = re.compile(r"\w+|\s+|[^\w\s]+", flags=re.UNICODE)
INLINE_IDENTIFIER_PART_PATTERN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|_|$)|[A-Z]?[a-z]+|[0-9]+|_+|[^A-Za-z0-9_]+",
    flags=re.UNICODE,
)
ALIGNMENT_WORD_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
ALIGNMENT_NOISE_WORDS = frozenset({"none", "true", "false", "null"})
MIN_SIMILAR_LINE_RATIO = 0.45
PLAIN_RENDER_CONTEXT_ROWS = 3
PLAIN_RENDER_MIN_FOLD_ROWS = 24
PLAIN_RENDER_MAX_VISIBLE_ROWS = 1000


def _payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _strip_rich_row_markup(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row.pop("left_tokens", None)
        row.pop("right_tokens", None)
        row.pop("left_syntax", None)
        row.pop("right_syntax", None)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _looks_like_notebook_path(path: str | None) -> bool:
    return bool(path and path.endswith(".ipynb"))


def _default_expanded_for_payload(payload: dict[str, Any]) -> bool:
    return not payload.get("lazy")


def _collapse_equal_rows_for_large_diff(
    rows: list[dict[str, Any]],
    *,
    context_rows: int = PLAIN_RENDER_CONTEXT_ROWS,
    min_fold_rows: int = PLAIN_RENDER_MIN_FOLD_ROWS,
) -> list[dict[str, Any]]:
    collapsed: list[dict[str, Any]] = []
    index = 0

    while index < len(rows):
        if rows[index].get("status") != "equal":
            collapsed.append(rows[index])
            index += 1
            continue

        run_start = index
        while index < len(rows) and rows[index].get("status") == "equal":
            index += 1
        run_end = index
        run_rows = rows[run_start:run_end]

        if len(run_rows) < min_fold_rows:
            collapsed.extend(run_rows)
            continue

        leading = run_rows[:context_rows]
        trailing = run_rows[-context_rows:] if context_rows else []
        middle = run_rows[context_rows : len(run_rows) - len(trailing)]

        collapsed.extend(leading)
        if middle:
            collapsed.append(
                {
                    "status": "fold",
                    "count": len(middle),
                    "foldedRows": middle,
                    "label": "unchanged context",
                }
            )
        collapsed.extend(trailing)

    return collapsed


def _truncate_large_render_rows(
    rows: list[dict[str, Any]],
    *,
    max_visible_rows: int = PLAIN_RENDER_MAX_VISIBLE_ROWS,
) -> tuple[list[dict[str, Any]], int]:
    if len(rows) <= max_visible_rows:
        return rows, 0

    head_count = max_visible_rows // 2
    tail_count = max_visible_rows - head_count
    omitted_count = len(rows) - max_visible_rows
    truncated_rows = [
        *rows[:head_count],
        {
            "status": "elided",
            "count": omitted_count,
            "label": "rows omitted for performance",
        },
        *rows[-tail_count:],
    ]
    return truncated_rows, omitted_count


def _count_changed_rows_and_hunks(
    left_text: str,
    right_text: str,
) -> dict[str, int]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()

    left_keys = [line.lstrip() for line in left_lines]
    right_keys = [line.lstrip() for line in right_lines]
    matcher = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)

    modified_lines = 0
    added_lines = 0
    removed_lines = 0
    hunk_count = 0
    in_changed_run = False

    def mark_changed(changed: bool) -> None:
        nonlocal hunk_count, in_changed_run
        if changed and not in_changed_run:
            hunk_count += 1
        in_changed_run = changed

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_block = left_lines[i1:i2]
        right_block = right_lines[j1:j2]

        if tag == "equal":
            for left_line, right_line in zip(
                left_block,
                right_block,
                strict=True,
            ):
                changed = left_line != right_line
                if changed:
                    modified_lines += 1
                mark_changed(False)
            continue

        if tag == "delete":
            removed_lines += len(left_block)
            for _ in left_block:
                mark_changed(True)
            continue

        if tag == "insert":
            added_lines += len(right_block)
            for _ in right_block:
                mark_changed(True)
            continue

        similar_pairs = _align_similar_lines(left_block, right_block)
        left_cursor = 0
        right_cursor = 0

        for left_index, right_index in similar_pairs:
            removed_slice = left_block[left_cursor:left_index]
            added_slice = right_block[right_cursor:right_index]

            if removed_slice:
                removed_lines += len(removed_slice)
                for _ in removed_slice:
                    mark_changed(True)
            if added_slice:
                added_lines += len(added_slice)
                for _ in added_slice:
                    mark_changed(True)

            left_line = left_block[left_index]
            right_line = right_block[right_index]
            if left_line.lstrip() != right_line.lstrip():
                modified_lines += 1
                mark_changed(True)
            else:
                mark_changed(False)

            left_cursor = left_index + 1
            right_cursor = right_index + 1

        removed_tail = left_block[left_cursor:]
        added_tail = right_block[right_cursor:]
        if removed_tail:
            removed_lines += len(removed_tail)
            for _ in removed_tail:
                mark_changed(True)
        if added_tail:
            added_lines += len(added_tail)
            for _ in added_tail:
                mark_changed(True)

    return {
        "changed_lines": modified_lines + added_lines + removed_lines,
        "modified_lines": modified_lines,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "hunk_count": hunk_count,
    }


def _build_rows_payload(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> dict[str, Any]:
    rows = _line_rows(left_text, right_text)
    left_syntax_lines = highlight_lines_for_path(left_path_hint, left_text)
    right_syntax_lines = highlight_lines_for_path(right_path_hint, right_text)
    plain_render = left_syntax_lines is None and right_syntax_lines is None
    fold_hints: list[FoldHint] = []

    if plain_render:
        _strip_rich_row_markup(rows)
    else:
        fold_hints = fold_hints_for_path(right_path_hint, right_text, rows)

        for row in rows:
            left_no = row.get("left_no")
            if (
                isinstance(left_no, int)
                and left_syntax_lines
                and left_no - 1 < len(left_syntax_lines)
                and left_syntax_lines[left_no - 1]
            ):
                row["left_syntax"] = left_syntax_lines[left_no - 1]

            right_no = row.get("right_no")
            if (
                isinstance(right_no, int)
                and right_syntax_lines
                and right_no - 1 < len(right_syntax_lines)
                and right_syntax_lines[right_no - 1]
            ):
                row["right_syntax"] = right_syntax_lines[right_no - 1]

    modified_lines = sum(
        1
        for row in rows
        if row["status"] == "replace"
        or (row["status"] == "equal" and _row_has_any_change(row))
    )
    added_lines = sum(1 for row in rows if row["status"] == "insert")
    removed_lines = sum(1 for row in rows if row["status"] == "delete")

    payload_rows = (
        _collapse_equal_rows_for_large_diff(rows) if plain_render else rows
    )
    truncated_rows = 0
    if plain_render:
        payload_rows, truncated_rows = _truncate_large_render_rows(payload_rows)

    payload = {
        "rows": payload_rows,
        "changed_lines": modified_lines + added_lines + removed_lines,
        "modified_lines": modified_lines,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
    }
    if plain_render:
        payload["render_mode"] = "plain"
    if truncated_rows:
        payload["truncated_rows"] = truncated_rows
    if fold_hints:
        payload["fold_hints"] = fold_hints
    return payload


def _append_char_level_diff(
    left_text: str,
    right_text: str,
    left_tokens: list[dict[str, Any]],
    right_tokens: list[dict[str, Any]],
    *,
    is_ws: bool = False,
) -> None:
    matcher = SequenceMatcher(a=left_text, b=right_text, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = left_text[i1:i2]
            if text:
                left_tokens.append(
                    {"text": text, "status": "unchanged", "is_ws": is_ws}
                )
                right_tokens.append(
                    {"text": text, "status": "unchanged", "is_ws": is_ws}
                )
        elif tag == "delete":
            text = left_text[i1:i2]
            if text:
                left_tokens.append(
                    {"text": text, "status": "delete", "is_ws": is_ws}
                )
        elif tag == "insert":
            text = right_text[j1:j2]
            if text:
                right_tokens.append(
                    {"text": text, "status": "insert", "is_ws": is_ws}
                )
        else:
            left_piece = left_text[i1:i2]
            right_piece = right_text[j1:j2]
            if left_piece:
                left_tokens.append(
                    {"text": left_piece, "status": "replace", "is_ws": is_ws}
                )
            if right_piece:
                right_tokens.append(
                    {"text": right_piece, "status": "replace", "is_ws": is_ws}
                )


def _identifier_diff_parts(text: str) -> list[str]:
    parts = INLINE_IDENTIFIER_PART_PATTERN.findall(text)
    return parts or [text]


def _append_identifier_level_diff(
    left_text: str,
    right_text: str,
    left_tokens: list[dict[str, Any]],
    right_tokens: list[dict[str, Any]],
) -> None:
    left_parts = _identifier_diff_parts(left_text)
    right_parts = _identifier_diff_parts(right_text)
    if left_parts == [left_text] and right_parts == [right_text]:
        _append_char_level_diff(
            left_text,
            right_text,
            left_tokens,
            right_tokens,
        )
        return

    matcher = SequenceMatcher(a=left_parts, b=right_parts, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for li, ri in zip(range(i1, i2), range(j1, j2), strict=True):
                text = left_parts[li]
                left_tokens.append(
                    {"text": text, "status": "unchanged", "is_ws": False}
                )
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "status": "unchanged",
                        "is_ws": False,
                    }
                )
        elif tag == "delete":
            for li in range(i1, i2):
                left_tokens.append(
                    {"text": left_parts[li], "status": "delete", "is_ws": False}
                )
        elif tag == "insert":
            for ri in range(j1, j2):
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "status": "insert",
                        "is_ws": False,
                    }
                )
        else:
            left_count = i2 - i1
            right_count = j2 - j1
            if left_count == 1 and right_count == 1:
                left_tokens.append(
                    {
                        "text": left_parts[i1],
                        "status": "replace",
                        "is_ws": False,
                    }
                )
                right_tokens.append(
                    {
                        "text": right_parts[j1],
                        "status": "replace",
                        "is_ws": False,
                    }
                )
                continue

            for li in range(i1, i2):
                left_tokens.append(
                    {
                        "text": left_parts[li],
                        "status": "replace",
                        "is_ws": False,
                    }
                )
            for ri in range(j1, j2):
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "status": "replace",
                        "is_ws": False,
                    }
                )


def _line_alignment_words(text: str) -> list[str]:
    return ALIGNMENT_WORD_PATTERN.findall(text.lstrip())


def _is_informative_alignment_word(word: str) -> bool:
    folded = word.casefold()
    return not folded.isdigit() and folded not in ALIGNMENT_NOISE_WORDS


def _has_shared_informative_alignment_word(
    left_words: list[str],
    right_words: list[str],
) -> bool:
    left_informative = {
        word.casefold()
        for word in left_words
        if _is_informative_alignment_word(word)
    }
    if not left_informative:
        return False

    right_informative = {
        word.casefold()
        for word in right_words
        if _is_informative_alignment_word(word)
    }
    return bool(left_informative & right_informative)


def _line_alignment_ratio(left_line: str, right_line: str) -> float:
    left_words = _line_alignment_words(left_line)
    right_words = _line_alignment_words(right_line)
    if left_words and right_words:
        if not _has_shared_informative_alignment_word(
            left_words,
            right_words,
        ):
            return 1.0 if left_line.lstrip() == right_line.lstrip() else 0.0

        return SequenceMatcher(
            a=left_words,
            b=right_words,
            autojunk=False,
        ).ratio()
    return 1.0 if left_line.lstrip() == right_line.lstrip() else 0.0


def _align_similar_lines(
    left_lines: list[str],
    right_lines: list[str],
) -> list[tuple[int, int]]:
    if not left_lines or not right_lines:
        return []

    left_count = len(left_lines)
    right_count = len(right_lines)
    scores: list[list[float]] = [
        [0.0] * (right_count + 1) for _ in range(left_count + 1)
    ]
    decisions: list[list[str]] = [
        ["done"] * right_count for _ in range(left_count)
    ]

    for left_index in range(left_count - 1, -1, -1):
        for right_index in range(right_count - 1, -1, -1):
            skip_left = scores[left_index + 1][right_index]
            skip_right = scores[left_index][right_index + 1]
            best_score = skip_left
            decision = "skip_left"
            if skip_right > best_score:
                best_score = skip_right
                decision = "skip_right"

            pair_ratio = _line_alignment_ratio(
                left_lines[left_index],
                right_lines[right_index],
            )
            if pair_ratio >= MIN_SIMILAR_LINE_RATIO:
                pair_score = (
                    pair_ratio + scores[left_index + 1][right_index + 1]
                )
                if pair_score > best_score:
                    best_score = pair_score
                    decision = "pair"

            scores[left_index][right_index] = best_score
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


def _inline_diff(
    left_text: str, right_text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    left_bits = INLINE_TOKEN_PATTERN.findall(left_text)
    right_bits = INLINE_TOKEN_PATTERN.findall(right_text)

    def make_tokens(bits: list[str]) -> list[dict[str, Any]]:
        tokens: list[dict[str, Any]] = []
        for bit in bits:
            tokens.append(
                {
                    "text": bit,
                    "is_ws": bool(re.fullmatch(r"\s+", bit)),
                }
            )
        return tokens

    left_data = make_tokens(left_bits)
    right_data = make_tokens(right_bits)
    left_keys = ["" if token["is_ws"] else token["text"] for token in left_data]
    right_keys = [
        "" if token["is_ws"] else token["text"] for token in right_data
    ]

    matcher = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)
    left_tokens: list[dict[str, Any]] = []
    right_tokens: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for li, ri in zip(range(i1, i2), range(j1, j2), strict=True):
                left_token = left_data[li]
                right_token = right_data[ri]
                if left_token["is_ws"] and right_token["is_ws"]:
                    _append_char_level_diff(
                        left_token["text"],
                        right_token["text"],
                        left_tokens,
                        right_tokens,
                        is_ws=True,
                    )
                else:
                    left_tokens.append(
                        {
                            "text": left_token["text"],
                            "status": "unchanged",
                            "is_ws": left_token["is_ws"],
                        }
                    )
                    right_tokens.append(
                        {
                            "text": right_token["text"],
                            "status": "unchanged",
                            "is_ws": right_token["is_ws"],
                        }
                    )
        elif tag == "delete":
            for li in range(i1, i2):
                token = left_data[li]
                left_tokens.append(
                    {
                        "text": token["text"],
                        "status": "delete",
                        "is_ws": token["is_ws"],
                    }
                )
        elif tag == "insert":
            for ri in range(j1, j2):
                token = right_data[ri]
                right_tokens.append(
                    {
                        "text": token["text"],
                        "status": "insert",
                        "is_ws": token["is_ws"],
                    }
                )
        else:
            left_slice = left_data[i1:i2]
            right_slice = right_data[j1:j2]
            inner_matcher = SequenceMatcher(
                a=[token["text"] for token in left_slice],
                b=[token["text"] for token in right_slice],
                autojunk=False,
            )
            for inner_tag, ii1, ii2, jj1, jj2 in inner_matcher.get_opcodes():
                if inner_tag == "equal":
                    for lrel, rrel in zip(
                        range(ii1, ii2),
                        range(jj1, jj2),
                        strict=True,
                    ):
                        left_token = left_slice[lrel]
                        right_token = right_slice[rrel]
                        left_tokens.append(
                            {
                                "text": left_token["text"],
                                "status": "unchanged",
                                "is_ws": left_token["is_ws"],
                            }
                        )
                        right_tokens.append(
                            {
                                "text": right_token["text"],
                                "status": "unchanged",
                                "is_ws": right_token["is_ws"],
                            }
                        )
                elif inner_tag == "delete":
                    for lrel in range(ii1, ii2):
                        token = left_slice[lrel]
                        left_tokens.append(
                            {
                                "text": token["text"],
                                "status": "delete",
                                "is_ws": token["is_ws"],
                            }
                        )
                elif inner_tag == "insert":
                    for rrel in range(jj1, jj2):
                        token = right_slice[rrel]
                        right_tokens.append(
                            {
                                "text": token["text"],
                                "status": "insert",
                                "is_ws": token["is_ws"],
                            }
                        )
                else:
                    left_count = ii2 - ii1
                    right_count = jj2 - jj1
                    if left_count == 1 and right_count == 1:
                        left_token = left_slice[ii1]
                        right_token = right_slice[jj1]
                        if not left_token["is_ws"] and not right_token["is_ws"]:
                            left_parts = _identifier_diff_parts(
                                left_token["text"]
                            )
                            right_parts = _identifier_diff_parts(
                                right_token["text"]
                            )
                            if left_parts != [
                                left_token["text"]
                            ] or right_parts != [right_token["text"]]:
                                _append_identifier_level_diff(
                                    left_token["text"],
                                    right_token["text"],
                                    left_tokens,
                                    right_tokens,
                                )
                            else:
                                left_tokens.append(
                                    {
                                        "text": left_token["text"],
                                        "status": "replace",
                                        "is_ws": False,
                                    }
                                )
                                right_tokens.append(
                                    {
                                        "text": right_token["text"],
                                        "status": "replace",
                                        "is_ws": False,
                                    }
                                )
                            continue

                    for lrel in range(ii1, ii2):
                        token = left_slice[lrel]
                        left_tokens.append(
                            {
                                "text": token["text"],
                                "status": "replace",
                                "is_ws": token["is_ws"],
                            }
                        )
                    for rrel in range(jj1, jj2):
                        token = right_slice[rrel]
                        right_tokens.append(
                            {
                                "text": token["text"],
                                "status": "replace",
                                "is_ws": token["is_ws"],
                            }
                        )

    return left_tokens, right_tokens


def _paired_line_row(
    left_line: str,
    right_line: str,
    left_no: int,
    right_no: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": (
            "equal" if left_line.lstrip() == right_line.lstrip() else "replace"
        ),
        "left_no": left_no,
        "right_no": right_no,
        "left_text": left_line,
        "right_text": right_line,
    }
    if left_line != right_line:
        left_tokens, right_tokens = _inline_diff(left_line, right_line)
        if left_tokens or right_tokens:
            row["left_tokens"] = left_tokens
            row["right_tokens"] = right_tokens
    return row


def _line_rows(left_text: str, right_text: str) -> list[dict[str, Any]]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    rows: list[dict[str, Any]] = []
    left_no = 1
    right_no = 1

    left_keys = [line.lstrip() for line in left_lines]
    right_keys = [line.lstrip() for line in right_lines]
    matcher = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_block = left_lines[i1:i2]
        right_block = right_lines[j1:j2]

        if tag == "equal":
            for left_line, right_line in zip(
                left_block,
                right_block,
                strict=True,
            ):
                rows.append(
                    _paired_line_row(left_line, right_line, left_no, right_no)
                )
                left_no += 1
                right_no += 1
            continue

        if tag == "delete":
            for left_line in left_block:
                rows.append(
                    {
                        "status": "delete",
                        "left_no": left_no,
                        "right_no": None,
                        "left_text": left_line,
                        "right_text": "",
                    }
                )
                left_no += 1
            continue

        if tag == "insert":
            for right_line in right_block:
                rows.append(
                    {
                        "status": "insert",
                        "left_no": None,
                        "right_no": right_no,
                        "left_text": "",
                        "right_text": right_line,
                    }
                )
                right_no += 1
            continue

        similar_pairs = _align_similar_lines(left_block, right_block)
        left_cursor = 0
        right_cursor = 0

        for left_index, right_index in similar_pairs:
            for delete_index in range(left_cursor, left_index):
                rows.append(
                    {
                        "status": "delete",
                        "left_no": left_no,
                        "right_no": None,
                        "left_text": left_block[delete_index],
                        "right_text": "",
                    }
                )
                left_no += 1

            for insert_index in range(right_cursor, right_index):
                rows.append(
                    {
                        "status": "insert",
                        "left_no": None,
                        "right_no": right_no,
                        "left_text": "",
                        "right_text": right_block[insert_index],
                    }
                )
                right_no += 1

            rows.append(
                _paired_line_row(
                    left_block[left_index],
                    right_block[right_index],
                    left_no,
                    right_no,
                )
            )
            left_no += 1
            right_no += 1
            left_cursor = left_index + 1
            right_cursor = right_index + 1

        for delete_index in range(left_cursor, len(left_block)):
            rows.append(
                {
                    "status": "delete",
                    "left_no": left_no,
                    "right_no": None,
                    "left_text": left_block[delete_index],
                    "right_text": "",
                }
            )
            left_no += 1

        for insert_index in range(right_cursor, len(right_block)):
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": right_no,
                    "left_text": "",
                    "right_text": right_block[insert_index],
                }
            )
            right_no += 1

    return rows


HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<left_start>\d+)(?:,(?P<left_count>\d+))? "
    r"\+(?P<right_start>\d+)(?:,(?P<right_count>\d+))? @@"
)


def _parse_git_patch_rows(patch_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    left_no = 1
    right_no = 1
    in_hunk = False

    for line in patch_text.splitlines():
        hunk_match = HUNK_HEADER_PATTERN.match(line)
        if hunk_match:
            left_no = int(hunk_match.group("left_start"))
            right_no = int(hunk_match.group("right_start"))
            in_hunk = True
            continue

        if not in_hunk:
            continue
        if line.startswith("\\"):
            continue
        if not line:
            prefix = " "
            text = ""
        else:
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


def _plain_line_rows_for_side(
    *,
    text: str,
    side: Literal["left", "right"],
) -> list[dict[str, Any]]:
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


def _git_style_line_rows(
    left_text: str, right_text: str
) -> list[dict[str, Any]]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)
    rows: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, left_line in enumerate(left_lines[i1:i2]):
                rows.append(
                    {
                        "status": "equal",
                        "left_no": i1 + offset + 1,
                        "right_no": j1 + offset + 1,
                        "left_text": left_line,
                        "right_text": left_line,
                    }
                )
            continue

        for offset, left_line in enumerate(left_lines[i1:i2]):
            rows.append(
                {
                    "status": "delete",
                    "left_no": i1 + offset + 1,
                    "right_no": None,
                    "left_text": left_line,
                    "right_text": "",
                }
            )
        for offset, right_line in enumerate(right_lines[j1:j2]):
            rows.append(
                {
                    "status": "insert",
                    "left_no": None,
                    "right_no": j1 + offset + 1,
                    "left_text": "",
                    "right_text": right_line,
                }
            )

    return rows


def _build_git_rows_payload(
    *,
    rows: list[dict[str, Any]],
    left_text: str,
    right_text: str,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> dict[str, Any]:
    left_syntax_lines = highlight_lines_for_path(left_path_hint, left_text)
    right_syntax_lines = highlight_lines_for_path(right_path_hint, right_text)
    plain_render = left_syntax_lines is None and right_syntax_lines is None
    fold_hints: list[FoldHint] = []

    if plain_render:
        _strip_rich_row_markup(rows)
    else:
        fold_hints = fold_hints_for_path(right_path_hint, right_text, rows)
        for row in rows:
            left_no = row.get("left_no")
            if (
                isinstance(left_no, int)
                and left_syntax_lines
                and left_no - 1 < len(left_syntax_lines)
                and left_syntax_lines[left_no - 1]
            ):
                row["left_syntax"] = left_syntax_lines[left_no - 1]

            right_no = row.get("right_no")
            if (
                isinstance(right_no, int)
                and right_syntax_lines
                and right_no - 1 < len(right_syntax_lines)
                and right_syntax_lines[right_no - 1]
            ):
                row["right_syntax"] = right_syntax_lines[right_no - 1]

    added_lines = sum(1 for row in rows if row["status"] == "insert")
    removed_lines = sum(1 for row in rows if row["status"] == "delete")
    payload_rows = (
        _collapse_equal_rows_for_large_diff(rows) if plain_render else rows
    )
    truncated_rows = 0
    if plain_render:
        payload_rows, truncated_rows = _truncate_large_render_rows(payload_rows)

    payload = {
        "rows": payload_rows,
        "changed_lines": added_lines + removed_lines,
        "modified_lines": 0,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
    }
    if plain_render:
        payload["render_mode"] = "plain"
    if truncated_rows:
        payload["truncated_rows"] = truncated_rows
    if fold_hints:
        payload["fold_hints"] = fold_hints
    return payload


def _row_has_any_change(row: dict[str, Any]) -> bool:
    if row.get("status") != "equal":
        return True
    if row.get("left_text") != row.get("right_text"):
        return True
    return any(
        token.get("status") != "unchanged"
        for token in row.get("left_tokens", []) + row.get("right_tokens", [])
    )


def build_loaded_diff(
    *,
    display_name: str,
    mode: str,
    left_label: str,
    right_label: str,
    left_exists: bool,
    right_exists: bool,
    left_text: str | None,
    right_text: str | None,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> dict[str, Any]:
    if _looks_like_notebook_path(right_path_hint) or _looks_like_notebook_path(
        left_path_hint
    ):
        from dirdiff.notebooks import _build_notebook_diff_payload

        notebook_payload = _build_notebook_diff_payload(
            display_name=display_name,
            mode=mode,
            left_label=left_label,
            right_label=right_label,
            left_exists=left_exists,
            right_exists=right_exists,
            left_text=left_text,
            right_text=right_text,
        )
        if notebook_payload is not None:
            return notebook_payload

    rows_payload = _build_rows_payload(
        left_text=left_text or "",
        right_text=right_text or "",
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    payload = {
        "display_name": display_name,
        "mode": mode,
        "left_label": left_label,
        "right_label": right_label,
        "summary": {
            "changed_lines": rows_payload["changed_lines"],
            "modified_lines": rows_payload["modified_lines"],
            "added_lines": rows_payload["added_lines"],
            "removed_lines": rows_payload["removed_lines"],
            "left_exists": left_exists,
            "right_exists": right_exists,
        },
        "rows": rows_payload["rows"],
    }
    payload["default_expanded"] = _default_expanded_for_payload(payload)
    if "render_mode" in rows_payload:
        payload["render_mode"] = rows_payload["render_mode"]
    if "truncated_rows" in rows_payload:
        payload["truncated_rows"] = rows_payload["truncated_rows"]
    if "fold_hints" in rows_payload:
        payload["fold_hints"] = rows_payload["fold_hints"]
    return payload


class TextDiffService(DiffServiceProtocol):
    def __init__(self, repo: WorkspaceBackend) -> None:
        self.repo = repo

    @property
    def repo_root(self) -> Path | None:
        return self.repo.repo_root

    @property
    def cwd(self) -> Path:
        return self.repo.cwd

    def normalize_side(self, raw_side: str) -> SideName:
        return self.repo.normalize_side(raw_side)

    def discover_default_path(self) -> str:
        return self.repo.discover_default_path()

    def current_branch_name(self) -> str:
        return self.repo.current_branch_name()

    def list_branch_names(self) -> list[str]:
        return self.repo.list_branch_names()

    def list_remote_ref_names(self) -> list[str]:
        return self.repo.list_remote_ref_names()

    def list_remote_names(self) -> list[str]:
        return self.repo.list_remote_names()

    def list_ref_choices(self) -> dict[str, list[str]]:
        return self.repo.list_ref_choices()

    def default_remote_name(self) -> str:
        return self.repo.default_remote_name()

    def branch_upstream_name(self, branch_name: str) -> str:
        return self.repo.branch_upstream_name(branch_name)

    def default_base_branch(self) -> str:
        return self.repo.default_base_branch()

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        return self.repo.preferred_review_branch(base_branch=base_branch)

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]:
        return self.repo.resolve_branch_diff_sides(
            base_branch=base_branch,
            branch=branch,
        )

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        return self.repo.list_repo_diff_paths(
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def normalize_repo_path(self, raw_path: str) -> str:
        return self.repo.normalize_repo_path(raw_path)

    def load_version(self, path: str, side: SideName) -> TextVersion:
        return self.repo.load_version(path, side)

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str = "modify",
        file_kind: str | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        normalized_left = (
            self.normalize_repo_path(left_path)
            if left_path is not None
            else None
        )
        normalized_right = (
            self.normalize_repo_path(right_path)
            if right_path is not None
            else None
        )
        normalized_left_side = self.normalize_side(left)
        normalized_right_side = self.normalize_side(right)
        left_version = (
            self.load_version(normalized_left, normalized_left_side)
            if normalized_left is not None
            else TextVersion(
                label=normalized_left_side, exists=False, text=None
            )
        )
        right_version = (
            self.load_version(normalized_right, normalized_right_side)
            if normalized_right is not None
            else TextVersion(
                label=normalized_right_side, exists=False, text=None
            )
        )

        if left_version.error:
            raise TextDiffError(left_version.error)
        if right_version.error:
            raise TextDiffError(right_version.error)
        if not left_version.exists and not right_version.exists:
            raise TextDiffError("The selected file is missing on both sides.")

        payload = build_loaded_diff(
            display_name=display_name
            or _display_name_for_repo_paths(normalized_left, normalized_right),
            mode="git",
            left_label=normalized_left_side,
            right_label=normalized_right_side,
            left_exists=left_version.exists,
            right_exists=right_version.exists,
            left_text=left_version.text,
            right_text=right_version.text,
            left_path_hint=normalized_left,
            right_path_hint=normalized_right,
        )
        payload["file_kind"] = _file_kind_for_change_type(
            change_type,
            file_kind=file_kind,
        )
        payload["left_path"] = normalized_left
        payload["right_path"] = normalized_right
        left_text = left_version.text or ""
        right_text = right_version.text or ""
        row_groups: list[list[dict[str, Any]]] = []
        if payload.get("render_kind") == "notebook":
            if payload.get("notebook_metadata_rows"):
                row_groups.append(payload["notebook_metadata_rows"])
            for cell in payload.get("cells", []):
                row_groups.append(cell.get("source_rows", []))
                if cell.get("metadata_rows"):
                    row_groups.append(cell["metadata_rows"])
                if cell.get("outputs_rows"):
                    row_groups.append(cell["outputs_rows"])
        else:
            row_groups.append(payload["rows"])

        row_count = sum(len(rows) for rows in row_groups)
        syntax_span_count = sum(
            len(row.get("left_syntax", ())) + len(row.get("right_syntax", ()))
            for rows in row_groups
            for row in rows
        )
        token_count = sum(
            len(row.get("left_tokens", ())) + len(row.get("right_tokens", ()))
            for rows in row_groups
            for row in rows
        )
        payload_bytes = _payload_size_bytes(payload)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _perf_log(
            "file"
            f" name={payload['display_name']!r}"
            f" change={change_type}"
            f" rows={row_count}"
            f" left_chars={len(left_text)}"
            f" right_chars={len(right_text)}"
            f" syntax_spans={syntax_span_count}"
            f" diff_tokens={token_count}"
            f" payload_bytes={payload_bytes}"
            f" elapsed_ms={elapsed_ms:.1f}"
        )
        return payload

    def _load_git_notebook_context(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
    ) -> dict[str, Any]:
        normalized_left = (
            self.normalize_repo_path(left_path)
            if left_path is not None
            else None
        )
        normalized_right = (
            self.normalize_repo_path(right_path)
            if right_path is not None
            else None
        )
        normalized_left_side = self.normalize_side(left)
        normalized_right_side = self.normalize_side(right)
        left_version = (
            self.load_version(normalized_left, normalized_left_side)
            if normalized_left is not None
            else TextVersion(
                label=normalized_left_side, exists=False, text=None
            )
        )
        right_version = (
            self.load_version(normalized_right, normalized_right_side)
            if normalized_right is not None
            else TextVersion(
                label=normalized_right_side, exists=False, text=None
            )
        )

        if left_version.error:
            raise TextDiffError(left_version.error)
        if right_version.error:
            raise TextDiffError(right_version.error)
        if not left_version.exists and not right_version.exists:
            raise TextDiffError("The selected file is missing on both sides.")

        from dirdiff.notebooks import _normalize_notebook_document

        left_notebook = (
            _normalize_notebook_document(left_version.text)
            if left_version.exists
            else None
        )
        right_notebook = (
            _normalize_notebook_document(right_version.text)
            if right_version.exists
            else None
        )
        if left_version.exists and left_notebook is None:
            raise TextDiffError(
                f"Could not parse notebook on {normalized_left_side}."
            )
        if right_version.exists and right_notebook is None:
            raise TextDiffError(
                f"Could not parse notebook on {normalized_right_side}."
            )

        return {
            "left_path": normalized_left,
            "right_path": normalized_right,
            "left_label": normalized_left_side,
            "right_label": normalized_right_side,
            "left_notebook": left_notebook,
            "right_notebook": right_notebook,
        }

    def build_notebook_section_diff(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        section: str | None,
        cell_key: str | None = None,
    ) -> dict[str, Any]:
        from dirdiff.notebooks import build_notebook_section_payload

        context = self._load_git_notebook_context(
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
        )
        return build_notebook_section_payload(
            left_notebook=context["left_notebook"],
            right_notebook=context["right_notebook"],
            left_label=context["left_label"],
            right_label=context["right_label"],
            section=section,
            cell_key=cell_key,
        )

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_repo_manifest_for_service(
            self,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def build_lazy_info(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_lazy_info_for_service(
            self,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )
