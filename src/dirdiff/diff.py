from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Literal

from dirdiff.fold import fold_hints_for_path
from dirdiff.syntax import highlight_lines_for_path


BuiltinSideName = Literal["head", "index", "worktree"]
SideName = str
BUILTIN_SIDES = frozenset({"head", "index", "worktree"})

INLINE_TOKEN_PATTERN = re.compile(r"\w+|\s+|[^\w\s]+", flags=re.UNICODE)
INLINE_IDENTIFIER_PART_PATTERN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|_|$)|[A-Z]?[a-z]+|[0-9]+|_+|[^A-Za-z0-9_]+",
    flags=re.UNICODE,
)
ALIGNMENT_WORD_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
ALIGNMENT_NOISE_WORDS = frozenset({"none", "true", "false", "null"})
MIN_SIMILAR_LINE_RATIO = 0.45
ENABLE_PERF_LOGS = os.environ.get("DIRDIFF_DEBUG_PERF") == "1"
PLAIN_RENDER_CONTEXT_ROWS = 3
PLAIN_RENDER_MIN_FOLD_ROWS = 24
PLAIN_RENDER_MAX_VISIBLE_ROWS = 1000
GENERATED_FILES = frozenset(
    {
        "cargo.lock",
        "composer.lock",
        "flake.lock",
        "go.sum",
        "package-lock.json",
        "pdm.lock",
        "pipfile.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextVersion:
    label: str
    exists: bool
    text: str | None
    error: str | None = None


@dataclass(frozen=True)
class RepoDiffPath:
    left_path: str | None
    right_path: str | None
    display_name: str
    change_type: str


@dataclass(frozen=True)
class RepoDiffProgress:
    entry: dict[str, Any]
    summary: dict[str, int]


class TextDiffError(ValueError):
    """Raised when a diff request cannot be fulfilled safely."""


def _perf_log(message: str) -> None:
    if not ENABLE_PERF_LOGS:
        return
    LOGGER.info("[dirdiff-perf] %s", message)


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


def _looks_generated_path(path: str | None) -> bool:
    if not path:
        return False
    return PurePosixPath(path).name.casefold() in GENERATED_FILES


def _should_lazy_load_repo_entry(entry: RepoDiffPath) -> bool:
    return _looks_generated_path(entry.right_path) or _looks_generated_path(entry.left_path)


def _to_lazy_repo_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    return {
        "lazy": True,
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "change_type": entry.change_type,
    }


def _normalize_notebook_document(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    cells = loaded.get("cells")
    if not isinstance(cells, list):
        return None
    metadata = loaded.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "cells": [cell for cell in cells if isinstance(cell, dict)],
        "metadata": metadata,
    }


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
        middle = run_rows[context_rows:len(run_rows) - len(trailing)]

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
            for left_line, right_line in zip(left_block, right_block):
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


def _notebook_source_path(cell_type: str) -> str | None:
    if cell_type == "code":
        return "cell.py"
    if cell_type == "markdown":
        return "cell.md"
    if cell_type == "raw":
        return "cell.txt"
    return None


def _cell_source(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return ""
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return str(source)


def _cell_metadata(cell: dict[str, Any] | None) -> dict[str, Any]:
    if cell is None:
        return {}
    metadata = cell.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _cell_outputs(cell: dict[str, Any] | None) -> list[Any]:
    if cell is None or cell.get("cell_type") != "code":
        return []
    outputs = cell.get("outputs", [])
    return outputs if isinstance(outputs, list) else []


def _cell_type_name(
    left_cell: dict[str, Any] | None,
    right_cell: dict[str, Any] | None,
) -> str:
    if right_cell is not None:
        return str(right_cell.get("cell_type", "unknown"))
    if left_cell is not None:
        return str(left_cell.get("cell_type", "unknown"))
    return "unknown"


def _usable_ids(cells: list[dict[str, Any]]) -> list[str] | None:
    ids = [str(cell.get("id", "")).strip() for cell in cells]
    if not ids or any(not cell_id for cell_id in ids):
        return None
    if len(ids) != len(set(ids)):
        return None
    return ids


def _cell_identity(
    *,
    left_cell: dict[str, Any] | None,
    right_cell: dict[str, Any] | None,
    left_index: int | None,
    right_index: int | None,
) -> dict[str, Any]:
    left_source = _cell_source(left_cell)
    right_source = _cell_source(right_cell)
    left_id = str(left_cell.get("id")) if left_cell is not None else None
    right_id = str(right_cell.get("id")) if right_cell is not None else None
    cell_id = right_id or left_id

    cell_key = str(cell_id) if cell_id else None
    if cell_key is None and right_index is not None:
        cell_key = f"right-{right_index}"
    if cell_key is None and left_index is not None:
        cell_key = f"left-{left_index}"
    if cell_key is None:
        cell_key = "cell-unknown"

    return {
        "cell_type": _cell_type_name(left_cell, right_cell),
        "cell_id": cell_id,
        "cell_key": cell_key,
        "left_id": left_id,
        "right_id": right_id,
        "left_source": left_source,
        "right_source": right_source,
    }


def _pair_notebook_cells(
    left_cells: list[dict[str, Any]],
    right_cells: list[dict[str, Any]],
) -> list[tuple[str, int | None, int | None]]:
    left_ids = _usable_ids(left_cells)
    right_ids = _usable_ids(right_cells)

    if left_ids is not None and right_ids is not None:
        pairs: list[tuple[str, int | None, int | None]] = []
        matcher = SequenceMatcher(a=left_ids, b=right_ids, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    pairs.append(("paired", i1 + offset, j1 + offset))
            elif tag == "replace":
                for left_index in range(i1, i2):
                    pairs.append(("left_only", left_index, None))
                for right_index in range(j1, j2):
                    pairs.append(("right_only", None, right_index))
            elif tag == "delete":
                for left_index in range(i1, i2):
                    pairs.append(("left_only", left_index, None))
            elif tag == "insert":
                for right_index in range(j1, j2):
                    pairs.append(("right_only", None, right_index))
        return pairs

    left_sources = [_cell_source(cell) for cell in left_cells]
    right_sources = [_cell_source(cell) for cell in right_cells]
    pairs = []
    matcher = SequenceMatcher(a=left_sources, b=right_sources, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                pairs.append(("paired", i1 + offset, j1 + offset))
        elif tag == "replace":
            for left_index in range(i1, i2):
                pairs.append(("left_only", left_index, None))
            for right_index in range(j1, j2):
                pairs.append(("right_only", None, right_index))
        elif tag == "delete":
            for left_index in range(i1, i2):
                pairs.append(("left_only", left_index, None))
        elif tag == "insert":
            for right_index in range(j1, j2):
                pairs.append(("right_only", None, right_index))
    return pairs


def _find_notebook_cell_pair(
    left_cells: list[dict[str, Any]],
    right_cells: list[dict[str, Any]],
    *,
    cell_key: str,
) -> tuple[str, int | None, int | None, dict[str, Any] | None, dict[str, Any] | None]:
    for pair_kind, left_index, right_index in _pair_notebook_cells(
        left_cells,
        right_cells,
    ):
        left_cell = left_cells[left_index] if left_index is not None else None
        right_cell = right_cells[right_index] if right_index is not None else None
        identity = _cell_identity(
            left_cell=left_cell,
            right_cell=right_cell,
            left_index=left_index,
            right_index=right_index,
        )
        if str(identity["cell_key"]) == cell_key:
            return pair_kind, left_index, right_index, left_cell, right_cell
    raise TextDiffError(f"Unknown notebook cell: {cell_key}")


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
    fold_hints: list[dict[str, object]] = []

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
        _collapse_equal_rows_for_large_diff(rows)
        if plain_render
        else rows
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


def _build_notebook_cell_diff(
    *,
    left_cell: dict[str, Any] | None,
    right_cell: dict[str, Any] | None,
    left_index: int | None,
    right_index: int | None,
    pair_kind: str,
    include_metadata_rows: bool = False,
    include_outputs_rows: bool = False,
) -> dict[str, Any] | None:
    identity = _cell_identity(
        left_cell=left_cell,
        right_cell=right_cell,
        left_index=left_index,
        right_index=right_index,
    )
    cell_type = str(identity["cell_type"])
    left_source = str(identity["left_source"])
    right_source = str(identity["right_source"])
    left_metadata = _cell_metadata(left_cell)
    right_metadata = _cell_metadata(right_cell)
    left_outputs = _cell_outputs(left_cell)
    right_outputs = _cell_outputs(right_cell)

    source_changed = (
        pair_kind != "paired"
        or left_source != right_source
        or (left_cell or {}).get("cell_type") != (right_cell or {}).get("cell_type")
    )
    metadata_changed = left_metadata != right_metadata
    outputs_changed = left_outputs != right_outputs

    if not any((source_changed, metadata_changed, outputs_changed)):
        return None

    source_payload = _build_rows_payload(
        left_text=left_source,
        right_text=right_source,
        left_path_hint=_notebook_source_path(cell_type),
        right_path_hint=_notebook_source_path(cell_type),
    )
    metadata_stats = (
        _count_changed_rows_and_hunks(
            _canonical_json(left_metadata),
            _canonical_json(right_metadata),
        )
        if metadata_changed
        else None
    )
    outputs_stats = (
        _count_changed_rows_and_hunks(
            _canonical_json(left_outputs),
            _canonical_json(right_outputs),
        )
        if outputs_changed
        else None
    )
    metadata_payload = (
        _build_rows_payload(
            left_text=_canonical_json(left_metadata),
            right_text=_canonical_json(right_metadata),
            left_path_hint="cell-metadata.json",
            right_path_hint="cell-metadata.json",
        )
        if metadata_changed and include_metadata_rows
        else None
    )
    outputs_payload = (
        _build_rows_payload(
            left_text=_canonical_json(left_outputs),
            right_text=_canonical_json(right_outputs),
            left_path_hint="cell-outputs.json",
            right_path_hint="cell-outputs.json",
        )
        if outputs_changed and include_outputs_rows
        else None
    )

    payload = {
        "kind": (
            "removed"
            if pair_kind == "left_only"
            else "added"
            if pair_kind == "right_only"
            else "modified"
        ),
        "cell_type": cell_type,
        "cell_id": identity["cell_id"],
        "cell_key": identity["cell_key"],
        "left_index": left_index,
        "right_index": right_index,
        "left_id": identity["left_id"],
        "right_id": identity["right_id"],
        "source_changed": source_changed,
        "metadata_changed": metadata_changed,
        "outputs_changed": outputs_changed,
        "source_rows": source_payload["rows"],
        "source_changed_lines": source_payload["changed_lines"],
        "source_modified_lines": source_payload["modified_lines"],
        "source_added_lines": source_payload["added_lines"],
        "source_removed_lines": source_payload["removed_lines"],
        "source_fold_hints": source_payload.get("fold_hints", []),
        "metadata_rows": metadata_payload["rows"] if metadata_payload else [],
        "outputs_rows": outputs_payload["rows"] if outputs_payload else [],
        "metadata_changed_lines": (
            metadata_stats["changed_lines"] if metadata_stats else 0
        ),
        "metadata_modified_lines": (
            metadata_stats["modified_lines"] if metadata_stats else 0
        ),
        "metadata_added_lines": (
            metadata_stats["added_lines"] if metadata_stats else 0
        ),
        "metadata_removed_lines": (
            metadata_stats["removed_lines"] if metadata_stats else 0
        ),
        "metadata_hunk_count": (
            metadata_stats["hunk_count"] if metadata_stats else 0
        ),
        "metadata_lazy": metadata_changed and not include_metadata_rows,
        "outputs_changed_lines": (
            outputs_stats["changed_lines"] if outputs_stats else 0
        ),
        "outputs_modified_lines": (
            outputs_stats["modified_lines"] if outputs_stats else 0
        ),
        "outputs_added_lines": (
            outputs_stats["added_lines"] if outputs_stats else 0
        ),
        "outputs_removed_lines": (
            outputs_stats["removed_lines"] if outputs_stats else 0
        ),
        "outputs_hunk_count": (
            outputs_stats["hunk_count"] if outputs_stats else 0
        ),
        "outputs_lazy": outputs_changed and not include_outputs_rows,
    }
    if "render_mode" in source_payload:
        payload["source_render_mode"] = source_payload["render_mode"]
    if "truncated_rows" in source_payload:
        payload["source_truncated_rows"] = source_payload["truncated_rows"]
    if metadata_payload and "render_mode" in metadata_payload:
        payload["metadata_render_mode"] = metadata_payload["render_mode"]
    if metadata_payload and "truncated_rows" in metadata_payload:
        payload["metadata_truncated_rows"] = metadata_payload["truncated_rows"]
    if outputs_payload and "render_mode" in outputs_payload:
        payload["outputs_render_mode"] = outputs_payload["render_mode"]
    if outputs_payload and "truncated_rows" in outputs_payload:
        payload["outputs_truncated_rows"] = outputs_payload["truncated_rows"]
    return payload


def _build_notebook_diff_payload(
    *,
    display_name: str,
    mode: str,
    left_label: str,
    right_label: str,
    left_exists: bool,
    right_exists: bool,
    left_text: str | None,
    right_text: str | None,
) -> dict[str, Any] | None:
    left_notebook = _normalize_notebook_document(left_text) if left_exists else None
    right_notebook = _normalize_notebook_document(right_text) if right_exists else None

    if left_exists and left_notebook is None:
        return None
    if right_exists and right_notebook is None:
        return None

    left_cells = list(left_notebook["cells"]) if left_notebook is not None else []
    right_cells = list(right_notebook["cells"]) if right_notebook is not None else []

    notebook_metadata_stats = None
    left_metadata = left_notebook["metadata"] if left_notebook is not None else {}
    right_metadata = right_notebook["metadata"] if right_notebook is not None else {}
    if left_metadata != right_metadata:
        notebook_metadata_stats = _count_changed_rows_and_hunks(
            _canonical_json(left_metadata),
            _canonical_json(right_metadata),
        )

    cells: list[dict[str, Any]] = []
    changed_lines = 0
    modified_lines = 0
    added_lines = 0
    removed_lines = 0

    if notebook_metadata_stats is not None:
        changed_lines += notebook_metadata_stats["changed_lines"]
        modified_lines += notebook_metadata_stats["modified_lines"]
        added_lines += notebook_metadata_stats["added_lines"]
        removed_lines += notebook_metadata_stats["removed_lines"]

    for pair_kind, left_index, right_index in _pair_notebook_cells(left_cells, right_cells):
        left_cell = left_cells[left_index] if left_index is not None else None
        right_cell = right_cells[right_index] if right_index is not None else None
        cell_diff = _build_notebook_cell_diff(
            left_cell=left_cell,
            right_cell=right_cell,
            left_index=left_index,
            right_index=right_index,
            pair_kind=pair_kind,
        )
        if cell_diff is None:
            continue
        cells.append(cell_diff)
        changed_lines += (
            cell_diff["source_changed_lines"]
            + cell_diff["metadata_changed_lines"]
            + cell_diff["outputs_changed_lines"]
        )
        modified_lines += (
            cell_diff["source_modified_lines"]
            + cell_diff["metadata_modified_lines"]
            + cell_diff["outputs_modified_lines"]
        )
        added_lines += (
            cell_diff["source_added_lines"]
            + cell_diff["metadata_added_lines"]
            + cell_diff["outputs_added_lines"]
        )
        removed_lines += (
            cell_diff["source_removed_lines"]
            + cell_diff["metadata_removed_lines"]
            + cell_diff["outputs_removed_lines"]
        )

    payload = {
        "display_name": display_name,
        "mode": mode,
        "render_kind": "notebook",
        "left_label": left_label,
        "right_label": right_label,
        "summary": {
            "changed_lines": changed_lines,
            "modified_lines": modified_lines,
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "left_exists": left_exists,
            "right_exists": right_exists,
            "changed_cells": len(cells),
            "added_cells": sum(1 for cell in cells if cell["kind"] == "added"),
            "removed_cells": sum(1 for cell in cells if cell["kind"] == "removed"),
            "modified_cells": sum(1 for cell in cells if cell["kind"] == "modified"),
            "notebook_metadata_changed": notebook_metadata_stats is not None,
        },
        "notebook_metadata_rows": [],
        "notebook_metadata_changed_lines": (
            notebook_metadata_stats["changed_lines"]
            if notebook_metadata_stats is not None
            else 0
        ),
        "notebook_metadata_hunk_count": (
            notebook_metadata_stats["hunk_count"]
            if notebook_metadata_stats is not None
            else 0
        ),
        "notebook_metadata_lazy": notebook_metadata_stats is not None,
        "cells": cells,
    }
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
                    {"text": text, "changed": False, "is_ws": is_ws}
                )
                right_tokens.append(
                    {"text": text, "changed": False, "is_ws": is_ws}
                )
        elif tag == "delete":
            text = left_text[i1:i2]
            if text:
                left_tokens.append(
                    {"text": text, "changed": True, "is_ws": is_ws}
                )
        elif tag == "insert":
            text = right_text[j1:j2]
            if text:
                right_tokens.append(
                    {"text": text, "changed": True, "is_ws": is_ws}
                )
        else:
            left_piece = left_text[i1:i2]
            right_piece = right_text[j1:j2]
            if left_piece:
                left_tokens.append(
                    {"text": left_piece, "changed": True, "is_ws": is_ws}
                )
            if right_piece:
                right_tokens.append(
                    {"text": right_piece, "changed": True, "is_ws": is_ws}
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
            for li, ri in zip(range(i1, i2), range(j1, j2)):
                text = left_parts[li]
                left_tokens.append(
                    {"text": text, "changed": False, "is_ws": False}
                )
                right_tokens.append(
                    {"text": right_parts[ri], "changed": False, "is_ws": False}
                )
        elif tag == "delete":
            for li in range(i1, i2):
                left_tokens.append(
                    {"text": left_parts[li], "changed": True, "is_ws": False}
                )
        elif tag == "insert":
            for ri in range(j1, j2):
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "changed": True,
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
                        "changed": True,
                        "is_ws": False,
                    }
                )
                right_tokens.append(
                    {
                        "text": right_parts[j1],
                        "changed": True,
                        "is_ws": False,
                    }
                )
                continue

            for li in range(i1, i2):
                left_tokens.append(
                    {"text": left_parts[li], "changed": True, "is_ws": False}
                )
            for ri in range(j1, j2):
                right_tokens.append(
                    {
                        "text": right_parts[ri],
                        "changed": True,
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
                    pair_ratio
                    + scores[left_index + 1][right_index + 1]
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
            for li, ri in zip(range(i1, i2), range(j1, j2)):
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
                            "changed": False,
                            "is_ws": left_token["is_ws"],
                        }
                    )
                    right_tokens.append(
                        {
                            "text": right_token["text"],
                            "changed": False,
                            "is_ws": right_token["is_ws"],
                        }
                    )
        elif tag == "delete":
            for li in range(i1, i2):
                token = left_data[li]
                left_tokens.append(
                    {
                        "text": token["text"],
                        "changed": True,
                        "is_ws": token["is_ws"],
                    }
                )
        elif tag == "insert":
            for ri in range(j1, j2):
                token = right_data[ri]
                right_tokens.append(
                    {
                        "text": token["text"],
                        "changed": True,
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
                    for lrel, rrel in zip(range(ii1, ii2), range(jj1, jj2)):
                        left_token = left_slice[lrel]
                        right_token = right_slice[rrel]
                        left_tokens.append(
                            {
                                "text": left_token["text"],
                                "changed": False,
                                "is_ws": left_token["is_ws"],
                            }
                        )
                        right_tokens.append(
                            {
                                "text": right_token["text"],
                                "changed": False,
                                "is_ws": right_token["is_ws"],
                            }
                        )
                elif inner_tag == "delete":
                    for lrel in range(ii1, ii2):
                        token = left_slice[lrel]
                        left_tokens.append(
                            {
                                "text": token["text"],
                                "changed": True,
                                "is_ws": token["is_ws"],
                            }
                        )
                elif inner_tag == "insert":
                    for rrel in range(jj1, jj2):
                        token = right_slice[rrel]
                        right_tokens.append(
                            {
                                "text": token["text"],
                                "changed": True,
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
                            left_parts = _identifier_diff_parts(left_token["text"])
                            right_parts = _identifier_diff_parts(right_token["text"])
                            if (
                                left_parts != [left_token["text"]]
                                or right_parts != [right_token["text"]]
                            ):
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
                                        "changed": True,
                                        "is_ws": False,
                                    }
                                )
                                right_tokens.append(
                                    {
                                        "text": right_token["text"],
                                        "changed": True,
                                        "is_ws": False,
                                    }
                                )
                            continue

                    for lrel in range(ii1, ii2):
                        token = left_slice[lrel]
                        left_tokens.append(
                            {
                                "text": token["text"],
                                "changed": True,
                                "is_ws": token["is_ws"],
                            }
                        )
                    for rrel in range(jj1, jj2):
                        token = right_slice[rrel]
                        right_tokens.append(
                            {
                                "text": token["text"],
                                "changed": True,
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
            "equal"
            if left_line.lstrip() == right_line.lstrip()
            else "replace"
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
            for left_line, right_line in zip(left_block, right_block):
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


def _row_has_any_change(row: dict[str, Any]) -> bool:
    if row.get("status") != "equal":
        return True
    if row.get("left_text") != row.get("right_text"):
        return True
    return any(
        token.get("changed")
        for token in row.get("left_tokens", []) + row.get("right_tokens", [])
    )


def _decode_text(data: bytes, *, label: str) -> str:
    if b"\x00" in data:
        raise TextDiffError(f"{label} appears to be a binary file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TextDiffError(
            f"{label} is not valid UTF-8 text: {exc}"
        ) from exc


def _display_name_for_repo_paths(
    left_path: str | None,
    right_path: str | None,
) -> str:
    if left_path and right_path:
        return left_path if left_path == right_path else f"{left_path} -> {right_path}"
    return left_path or right_path or "(unknown)"


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
    if _looks_like_notebook_path(right_path_hint) or _looks_like_notebook_path(left_path_hint):
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
    if "render_mode" in rows_payload:
        payload["render_mode"] = rows_payload["render_mode"]
    if "truncated_rows" in rows_payload:
        payload["truncated_rows"] = rows_payload["truncated_rows"]
    if "fold_hints" in rows_payload:
        payload["fold_hints"] = rows_payload["fold_hints"]
    return payload


def _empty_repo_diff(
    *,
    left_label: str,
    right_label: str,
) -> dict[str, Any]:
    return {
        "display_name": "Repository diff",
        "mode": "repo",
        "left_label": left_label,
        "right_label": right_label,
        "summary": {
            **_empty_repo_summary(),
        },
        "files": [],
    }


def _empty_repo_summary() -> dict[str, int]:
    return {
        "changed_files": 0,
        "added_files": 0,
        "removed_files": 0,
        "updated_files": 0,
        "changed_lines": 0,
        "modified_lines": 0,
        "added_lines": 0,
        "removed_lines": 0,
        "skipped_files": 0,
    }


class TextDiffService:
    def __init__(self, repo_root: Path | None, *, cwd: Path | None = None) -> None:
        self.repo_root = repo_root.resolve() if repo_root is not None else None
        self.cwd = (cwd or Path.cwd()).resolve()

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        repo_root: Path | None = None,
    ) -> "TextDiffService":
        working_dir = (cwd or Path.cwd()).resolve()
        if repo_root is not None:
            return cls(Path(repo_root).expanduser().resolve(), cwd=working_dir)

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=working_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        discovered_root = (
            Path(result.stdout.strip()).resolve()
            if result.returncode == 0 and result.stdout.strip()
            else None
        )
        return cls(discovered_root, cwd=working_dir)

    def _run_git(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=check,
            capture_output=True,
        )

    def _run_git_text(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=check,
            capture_output=True,
            text=True,
        )

    def normalize_side(self, raw_side: str) -> SideName:
        side = raw_side.strip()
        if not side:
            raise TextDiffError("Diff side is required.")
        if side in BUILTIN_SIDES:
            return side
        if self.repo_root is None:
            raise TextDiffError("Custom refs require a Git repo.")

        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{side}^{{commit}}"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if resolved.returncode != 0:
            raise TextDiffError(f"Unknown Git ref: {side}")
        return side

    def discover_default_path(self) -> str:
        if self.repo_root is None:
            raise TextDiffError("No Git repo found for automatic path discovery.")

        modified = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        candidates = [
            line.strip()
            for line in modified.stdout.splitlines()
            if line.strip() and not line.endswith("/")
        ]
        if candidates:
            return candidates[0]

        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        tracked_candidates = [
            line.strip()
            for line in tracked.stdout.splitlines()
            if line.strip() and not line.endswith("/")
        ]
        if tracked_candidates:
            return tracked_candidates[0]

        raise TextDiffError("No files found in the current Git repo.")

    def current_branch_name(self) -> str:
        if self.repo_root is None:
            return ""
        result = self._run_git_text(["branch", "--show-current"], check=False)
        return result.stdout.strip()

    def list_branch_names(self) -> list[str]:
        if self.repo_root is None:
            return []
        result = self._run_git_text(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
            check=False,
        )
        if result.returncode != 0:
            return []
        return sorted(
            {
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip()
            }
        )

    def list_remote_ref_names(self) -> list[str]:
        if self.repo_root is None:
            return []
        result = self._run_git_text(
            ["for-each-ref", "--format=%(refname:short)", "refs/remotes"],
            check=False,
        )
        if result.returncode != 0:
            return []
        return sorted(
            {
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and not line.strip().endswith("/HEAD")
            }
        )

    def list_remote_names(self) -> list[str]:
        return sorted(
            {
                ref.split("/", 1)[0]
                for ref in self.list_remote_ref_names()
                if "/" in ref
            }
        )

    def list_ref_choices(self) -> dict[str, list[str]]:
        return {
            "builtins": ["head", "index", "worktree"],
            "locals": self.list_branch_names(),
            "remotes": self.list_remote_ref_names(),
            "remote_names": self.list_remote_names(),
        }

    def default_remote_name(self) -> str:
        remote_names = self.list_remote_names()
        if "origin" in remote_names:
            return "origin"
        return remote_names[0] if remote_names else ""

    def branch_upstream_name(self, branch_name: str) -> str:
        normalized_branch = branch_name.strip()
        if not normalized_branch or self.repo_root is None:
            return ""
        result = self._run_git_text(
            [
                "for-each-ref",
                "--format=%(upstream:short)",
                f"refs/heads/{normalized_branch}",
            ],
            check=False,
        )
        return result.stdout.strip()

    def default_base_branch(self) -> str:
        branch_names = self.list_branch_names()
        if "master" in branch_names:
            return "master"
        if "main" in branch_names:
            return "main"

        if self.repo_root is not None:
            result = self._run_git_text(
                ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
                check=False,
            )
            remote_head = result.stdout.strip()
            if remote_head.startswith("origin/"):
                candidate = remote_head.removeprefix("origin/")
                if candidate:
                    return candidate

        current = self.current_branch_name()
        if current:
            return current
        return branch_names[0] if branch_names else ""

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        branch_names = self.list_branch_names()
        if not branch_names:
            return ""

        normalized_base = (base_branch or self.default_base_branch()).strip()
        current = self.current_branch_name()

        if current and current != normalized_base:
            return current

        for branch_name in branch_names:
            if branch_name != normalized_base:
                return branch_name

        return current or branch_names[0]

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]:
        normalized_base = self.normalize_side(base_branch)
        normalized_branch = self.normalize_side(branch)
        merge_base = self._run_git_text(
            ["merge-base", normalized_base, normalized_branch],
            check=False,
        )
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            raise TextDiffError(
                f"Could not find a merge base between {normalized_base} and {normalized_branch}."
            )
        return merge_base.stdout.strip(), normalized_branch

    def _git_tree_spec(self, side: SideName) -> str:
        if side == "head":
            return "HEAD"
        return side

    def _parse_name_status_output(self, output: bytes) -> list[RepoDiffPath]:
        tokens = output.split(b"\0")
        if tokens and not tokens[-1]:
            tokens = tokens[:-1]

        entries: list[RepoDiffPath] = []
        index = 0
        while index < len(tokens):
            status_token = tokens[index].decode("utf-8")
            index += 1
            if not status_token:
                continue

            change_kind = status_token[0]
            if change_kind in {"R", "C"}:
                if index + 1 >= len(tokens):
                    break
                left_path = tokens[index].decode("utf-8")
                right_path = tokens[index + 1].decode("utf-8")
                index += 2
                entries.append(
                    RepoDiffPath(
                        left_path=left_path,
                        right_path=right_path,
                        display_name=_display_name_for_repo_paths(left_path, right_path),
                        change_type="rename" if change_kind == "R" else "copy",
                    )
                )
                continue

            if index >= len(tokens):
                break
            path = tokens[index].decode("utf-8")
            index += 1

            left_path = path if change_kind != "A" else None
            right_path = path if change_kind != "D" else None
            entries.append(
                RepoDiffPath(
                    left_path=left_path,
                    right_path=right_path,
                    display_name=_display_name_for_repo_paths(left_path, right_path),
                    change_type={
                        "A": "add",
                        "D": "delete",
                    }.get(change_kind, "modify"),
                )
            )

        return entries

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
    ) -> list[RepoDiffPath]:
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        if left == right:
            return []

        diff_args: list[str]
        if "worktree" in {left, right}:
            other = right if left == "worktree" else left
            diff_args = (
                ["diff", "--name-status", "-z", "-M"]
                if other == "index"
                else ["diff", "--name-status", "-z", "-M", self._git_tree_spec(other)]
            )
        elif "index" in {left, right}:
            other = right if left == "index" else left
            diff_args = (
                ["diff", "--cached", "--name-status", "-z", "-M"]
                if other == "head"
                else ["diff", "--cached", "--name-status", "-z", "-M", self._git_tree_spec(other)]
            )
        else:
            diff_args = [
                "diff",
                "--name-status",
                "-z",
                "-M",
                self._git_tree_spec(left),
                self._git_tree_spec(right),
            ]

        diff_output = self._run_git(diff_args)
        entries = self._parse_name_status_output(diff_output.stdout)
        return sorted(entries, key=lambda entry: (entry.display_name, entry.change_type))

    def normalize_repo_path(self, raw_path: str) -> str:
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")
        if not raw_path.strip():
            raise TextDiffError("Repo path is required.")
        if raw_path.endswith("/"):
            raise TextDiffError("Repo path must point to a file.")

        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise TextDiffError("Use a repo-relative path.")

        normalized = candidate.as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise TextDiffError("Repo path must stay inside the repo.")
        return normalized

    def load_git_version(self, path: str, side: SideName) -> TextVersion:
        if self.repo_root is None:
            raise TextDiffError("Git-backed diff mode requires a Git repo.")

        if side == "worktree":
            file_path = self.repo_root / path
            if not file_path.exists():
                return TextVersion(label=side, exists=False, text=None)
            if file_path.is_dir():
                raise TextDiffError(f"{path} is a directory, not a file.")
            return TextVersion(
                label=side,
                exists=True,
                text=_decode_text(file_path.read_bytes(), label=f"{side}:{path}"),
            )

        git_target = (
            f"HEAD:{path}"
            if side == "head"
            else f":{path}"
            if side == "index"
            else f"{side}:{path}"
        )
        result = self._run_git(["show", git_target], check=False)
        if result.returncode != 0:
            return TextVersion(label=side, exists=False, text=None)
        return TextVersion(
            label=side,
            exists=True,
            text=_decode_text(result.stdout, label=f"{side}:{path}"),
        )

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: str = "modify",
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        normalized_left = (
            self.normalize_repo_path(left_path) if left_path is not None else None
        )
        normalized_right = (
            self.normalize_repo_path(right_path) if right_path is not None else None
        )
        normalized_left_side = self.normalize_side(left)
        normalized_right_side = self.normalize_side(right)
        left_version = (
            self.load_git_version(normalized_left, normalized_left_side)
            if normalized_left is not None
            else TextVersion(label=normalized_left_side, exists=False, text=None)
        )
        right_version = (
            self.load_git_version(normalized_right, normalized_right_side)
            if normalized_right is not None
            else TextVersion(label=normalized_right_side, exists=False, text=None)
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
        payload["change_type"] = change_type
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
            self.normalize_repo_path(left_path) if left_path is not None else None
        )
        normalized_right = (
            self.normalize_repo_path(right_path) if right_path is not None else None
        )
        normalized_left_side = self.normalize_side(left)
        normalized_right_side = self.normalize_side(right)
        left_version = (
            self.load_git_version(normalized_left, normalized_left_side)
            if normalized_left is not None
            else TextVersion(label=normalized_left_side, exists=False, text=None)
        )
        right_version = (
            self.load_git_version(normalized_right, normalized_right_side)
            if normalized_right is not None
            else TextVersion(label=normalized_right_side, exists=False, text=None)
        )

        if left_version.error:
            raise TextDiffError(left_version.error)
        if right_version.error:
            raise TextDiffError(right_version.error)
        if not left_version.exists and not right_version.exists:
            raise TextDiffError("The selected file is missing on both sides.")

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
        section: str,
        cell_key: str | None = None,
    ) -> dict[str, Any]:
        context = self._load_git_notebook_context(
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
        )
        left_notebook = context["left_notebook"]
        right_notebook = context["right_notebook"]
        left_label = context["left_label"]
        right_label = context["right_label"]

        if section == "notebook-metadata":
            left_metadata = (
                left_notebook["metadata"] if left_notebook is not None else {}
            )
            right_metadata = (
                right_notebook["metadata"] if right_notebook is not None else {}
            )
            if left_metadata == right_metadata:
                raise TextDiffError("Notebook metadata is unchanged.")
            payload = _build_rows_payload(
                left_text=_canonical_json(left_metadata),
                right_text=_canonical_json(right_metadata),
                left_path_hint="notebook-metadata.json",
                right_path_hint="notebook-metadata.json",
            )
            return {
                "section": section,
                "left_label": left_label,
                "right_label": right_label,
                "rows": payload["rows"],
                "render_mode": payload.get("render_mode"),
                "truncated_rows": payload.get("truncated_rows", 0),
                "fold_hints": payload.get("fold_hints", []),
            }

        if not cell_key:
            raise TextDiffError("Notebook cell key is required.")

        left_cells = list(left_notebook["cells"]) if left_notebook is not None else []
        right_cells = (
            list(right_notebook["cells"]) if right_notebook is not None else []
        )
        _, left_index, right_index, left_cell, right_cell = _find_notebook_cell_pair(
            left_cells,
            right_cells,
            cell_key=cell_key,
        )

        if section == "cell-metadata":
            left_value = _cell_metadata(left_cell)
            right_value = _cell_metadata(right_cell)
            left_hint = "cell-metadata.json"
            right_hint = "cell-metadata.json"
        elif section == "cell-outputs":
            left_value = _cell_outputs(left_cell)
            right_value = _cell_outputs(right_cell)
            left_hint = "cell-outputs.json"
            right_hint = "cell-outputs.json"
        else:
            raise TextDiffError(f"Unknown notebook section: {section}")

        if left_value == right_value:
            raise TextDiffError("Notebook section is unchanged.")

        payload = _build_rows_payload(
            left_text=_canonical_json(left_value),
            right_text=_canonical_json(right_value),
            left_path_hint=left_hint,
            right_path_hint=right_hint,
        )
        return {
            "section": section,
            "cell_key": cell_key,
            "left_index": left_index,
            "right_index": right_index,
            "left_label": left_label,
            "right_label": right_label,
            "rows": payload["rows"],
            "render_mode": payload.get("render_mode"),
            "truncated_rows": payload.get("truncated_rows", 0),
            "fold_hints": payload.get("fold_hints", []),
        }

    def build_repo_diff(
        self,
        *,
        left: str,
        right: str,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        normalized_left = self.normalize_side(left)
        normalized_right = self.normalize_side(right)
        paths = self.list_repo_diff_paths(left=normalized_left, right=normalized_right)
        _perf_log(
            "repo-start"
            f" left={normalized_left!r}"
            f" right={normalized_right!r}"
            f" changed_paths={len(paths)}"
        )
        if not paths:
            return _empty_repo_diff(
                left_label=normalized_left,
                right_label=normalized_right,
            )

        files: list[dict[str, Any]] = []
        summary = _empty_repo_summary()

        for progress in self.iter_repo_diff_progress(
            left=normalized_left,
            right=normalized_right,
            paths=paths,
        ):
            files.append(progress.entry)
            summary = progress.summary

        payload = {
            "display_name": "Repository diff",
            "mode": "repo",
            "left_label": normalized_left,
            "right_label": normalized_right,
            "summary": summary,
            "files": files,
        }
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _perf_log(
            "repo-done"
            f" left={normalized_left!r}"
            f" right={normalized_right!r}"
            f" files={len(files)}"
            f" changed_files={summary['changed_files']}"
            f" skipped_files={summary['skipped_files']}"
            f" payload_bytes={_payload_size_bytes(payload)}"
            f" elapsed_ms={elapsed_ms:.1f}"
        )
        return payload

    def iter_repo_diff_progress(
        self,
        *,
        left: str,
        right: str,
        paths: list[RepoDiffPath] | None = None,
    ) -> Iterator[RepoDiffProgress]:
        normalized_left = self.normalize_side(left)
        normalized_right = self.normalize_side(right)
        entries = (
            paths
            if paths is not None
            else self.list_repo_diff_paths(
                left=normalized_left,
                right=normalized_right,
            )
        )
        summary = _empty_repo_summary()
        for entry in entries:
            _perf_log(
                "repo-file-start"
                f" name={entry.display_name!r}"
                f" change={entry.change_type}"
            )
            if _should_lazy_load_repo_entry(entry):
                summary["changed_files"] += 1
                if entry.change_type == "add":
                    summary["added_files"] += 1
                elif entry.change_type == "delete":
                    summary["removed_files"] += 1
                else:
                    summary["updated_files"] += 1
                yield RepoDiffProgress(
                    entry=_to_lazy_repo_file_entry(entry),
                    summary=dict(summary),
                )
                continue
            try:
                file_diff = self.build_git_diff_paths(
                    left_path=entry.left_path,
                    right_path=entry.right_path,
                    left=normalized_left,
                    right=normalized_right,
                    display_name=entry.display_name,
                    change_type=entry.change_type,
                )
            except TextDiffError as exc:
                summary["skipped_files"] += 1
                _perf_log(
                    "repo-file-skip"
                    f" name={entry.display_name!r}"
                    f" change={entry.change_type}"
                    f" reason={str(exc)!r}"
                )
                yield RepoDiffProgress(
                    entry={
                        "display_name": entry.display_name,
                        "mode": "git",
                        "left_label": normalized_left,
                        "right_label": normalized_right,
                        "change_type": entry.change_type,
                        "error": str(exc),
                    },
                    summary=dict(summary),
                )
                continue

            if (
                file_diff["summary"]["changed_lines"] <= 0
                and file_diff.get("change_type") not in {"rename", "copy"}
            ):
                continue

            summary["changed_files"] += 1
            if entry.change_type == "add":
                summary["added_files"] += 1
            elif entry.change_type == "delete":
                summary["removed_files"] += 1
            else:
                summary["updated_files"] += 1
            summary["changed_lines"] += file_diff["summary"]["changed_lines"]
            summary["modified_lines"] += file_diff["summary"]["modified_lines"]
            summary["added_lines"] += file_diff["summary"]["added_lines"]
            summary["removed_lines"] += file_diff["summary"]["removed_lines"]
            _perf_log(
                "repo-file-done"
                f" name={entry.display_name!r}"
                f" change={entry.change_type}"
                f" changed_files={summary['changed_files']}"
                f" changed_lines={summary['changed_lines']}"
            )
            yield RepoDiffProgress(entry=file_diff, summary=dict(summary))

    def build_branch_diff(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> dict[str, Any]:
        merge_base, normalized_branch = self.resolve_branch_diff_sides(
            base_branch=base_branch,
            branch=branch,
        )
        left_label = f"{base_branch.strip()}...{normalized_branch}"

        payload = self.build_repo_diff(left=merge_base, right=normalized_branch)
        payload["left_label"] = left_label
        payload["right_label"] = normalized_branch
        for entry in payload.get("files", []):
            if "left_label" in entry:
                entry["left_label"] = left_label
            if "right_label" in entry:
                entry["right_label"] = normalized_branch
        return payload

    def build_diff(
        self,
        *,
        left: str,
        right: str,
        base_branch: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        if branch and branch.strip():
            return self.build_branch_diff(
                base_branch=base_branch or self.default_base_branch(),
                branch=branch,
            )
        if self.repo_root is not None:
            return self.build_repo_diff(left=left, right=right)
        raise TextDiffError("Git-backed diff mode requires a Git repo.")
