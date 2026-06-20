from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from dirdiff.services.textdiff import (
    _build_rows_payload,
    _canonical_json,
    _count_changed_rows_and_hunks,
    _default_expanded_for_payload,
)
from dirdiff.sources import TextDiffError


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


def _notebook_cell_id(cell: dict[str, Any]) -> str | None:
    cell_id = str(cell.get("id", "")).strip()
    return cell_id or None


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


def _pair_notebook_cell_ranges_by_source(
    left_cells: list[dict[str, Any]],
    right_cells: list[dict[str, Any]],
    left_indices: list[int],
    right_indices: list[int],
) -> list[tuple[str, int | None, int | None]]:
    left_sources = [_cell_source(left_cells[index]) for index in left_indices]
    right_sources = [
        _cell_source(right_cells[index]) for index in right_indices
    ]
    pairs: list[tuple[str, int | None, int | None]] = []
    matcher = SequenceMatcher(a=left_sources, b=right_sources, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                pairs.append(
                    (
                        "paired",
                        left_indices[i1 + offset],
                        right_indices[j1 + offset],
                    )
                )
        elif tag == "replace":
            for left_offset in range(i1, i2):
                pairs.append(("left_only", left_indices[left_offset], None))
            for right_offset in range(j1, j2):
                pairs.append(("right_only", None, right_indices[right_offset]))
        elif tag == "delete":
            for left_offset in range(i1, i2):
                pairs.append(("left_only", left_indices[left_offset], None))
        elif tag == "insert":
            for right_offset in range(j1, j2):
                pairs.append(("right_only", None, right_indices[right_offset]))
    return pairs


def _pair_notebook_cells(
    left_cells: list[dict[str, Any]],
    right_cells: list[dict[str, Any]],
) -> list[tuple[str, int | None, int | None]]:
    left_ids = [_notebook_cell_id(cell) for cell in left_cells]
    right_ids = [_notebook_cell_id(cell) for cell in right_cells]
    left_id_counts = Counter(
        cell_id for cell_id in left_ids if cell_id is not None
    )
    right_id_counts = Counter(
        cell_id for cell_id in right_ids if cell_id is not None
    )
    shared_unique_ids = {
        cell_id
        for cell_id, count in left_id_counts.items()
        if count == 1 and right_id_counts.get(cell_id) == 1
    }

    if shared_unique_ids:
        left_tokens = [
            ("id", cell_id) if cell_id in shared_unique_ids else ("left", index)
            for index, cell_id in enumerate(left_ids)
        ]
        right_tokens = [
            ("id", cell_id)
            if cell_id in shared_unique_ids
            else ("right", index)
            for index, cell_id in enumerate(right_ids)
        ]
        pairs: list[tuple[str, int | None, int | None]] = []
        matcher = SequenceMatcher(a=left_tokens, b=right_tokens, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    pairs.append(("paired", i1 + offset, j1 + offset))
                continue
            pairs.extend(
                _pair_notebook_cell_ranges_by_source(
                    left_cells,
                    right_cells,
                    list(range(i1, i2)),
                    list(range(j1, j2)),
                )
            )
        return pairs

    return _pair_notebook_cell_ranges_by_source(
        left_cells,
        right_cells,
        list(range(len(left_cells))),
        list(range(len(right_cells))),
    )


def _find_notebook_cell_pair(
    left_cells: list[dict[str, Any]],
    right_cells: list[dict[str, Any]],
    *,
    cell_key: str,
) -> tuple[
    str, int | None, int | None, dict[str, Any] | None, dict[str, Any] | None
]:
    for pair_kind, left_index, right_index in _pair_notebook_cells(
        left_cells,
        right_cells,
    ):
        left_cell = left_cells[left_index] if left_index is not None else None
        right_cell = (
            right_cells[right_index] if right_index is not None else None
        )
        identity = _cell_identity(
            left_cell=left_cell,
            right_cell=right_cell,
            left_index=left_index,
            right_index=right_index,
        )
        if str(identity["cell_key"]) == cell_key:
            return pair_kind, left_index, right_index, left_cell, right_cell
    raise TextDiffError(f"Unknown notebook cell: {cell_key}")


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
        or (left_cell or {}).get("cell_type")
        != (right_cell or {}).get("cell_type")
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
    left_notebook = (
        _normalize_notebook_document(left_text) if left_exists else None
    )
    right_notebook = (
        _normalize_notebook_document(right_text) if right_exists else None
    )

    if left_exists and left_notebook is None:
        return None
    if right_exists and right_notebook is None:
        return None

    left_cells = (
        list(left_notebook["cells"]) if left_notebook is not None else []
    )
    right_cells = (
        list(right_notebook["cells"]) if right_notebook is not None else []
    )

    notebook_metadata_stats = None
    left_metadata = (
        left_notebook["metadata"] if left_notebook is not None else {}
    )
    right_metadata = (
        right_notebook["metadata"] if right_notebook is not None else {}
    )
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

    for pair_kind, left_index, right_index in _pair_notebook_cells(
        left_cells, right_cells
    ):
        left_cell = left_cells[left_index] if left_index is not None else None
        right_cell = (
            right_cells[right_index] if right_index is not None else None
        )
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
            "moved_lines": 0,
            "left_exists": left_exists,
            "right_exists": right_exists,
            "changed_cells": len(cells),
            "added_cells": sum(1 for cell in cells if cell["kind"] == "added"),
            "removed_cells": sum(
                1 for cell in cells if cell["kind"] == "removed"
            ),
            "modified_cells": sum(
                1 for cell in cells if cell["kind"] == "modified"
            ),
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
    payload["default_expanded"] = _default_expanded_for_payload(payload)
    return payload


def build_notebook_section_payload(
    *,
    left_notebook: dict[str, Any] | None,
    right_notebook: dict[str, Any] | None,
    left_label: str,
    right_label: str,
    section: str | None,
    cell_key: str | None = None,
) -> dict[str, Any]:
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

    left_cells = (
        list(left_notebook["cells"]) if left_notebook is not None else []
    )
    right_cells = (
        list(right_notebook["cells"]) if right_notebook is not None else []
    )
    _, left_index, right_index, left_cell, right_cell = (
        _find_notebook_cell_pair(
            left_cells,
            right_cells,
            cell_key=cell_key,
        )
    )

    left_value: dict[str, Any] | list[Any]
    right_value: dict[str, Any] | list[Any]
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
