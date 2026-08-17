"""Notebook pairing and payload construction for the dirdiff REST API.

Notebook diffs are an API-level representation, not a diff-engine feature.
The server loads the left and right `.ipynb` texts through a
`WorkspaceBackendProtocol`, asks this module to parse and compare notebook structure,
and returns a `render_kind: "notebook"` payload from the existing
`/api/file-diff` endpoint.

`rendered_notebook_cell_pairs` is the shared public contract for the cell
regions that the notebook File surface actually emits. Review target validation
uses that exact sequence rather than independently approximating renderer
visibility.

This module depends on display payload helpers from `dirdiff.rendering` to
enrich rows after a renderer has produced text rows.  It uses the selected
engine for cell source, but notebook metadata, cell metadata, and outputs stay
on the native text renderer so structural engines do not report moves for JSON
bookkeeping surfaces.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from dirdiff.engines import (
    DiffEngineProtocol,
    DiffSide,
    text_diff_summary,
)
from dirdiff.rendering import (
    canonical_json,
    default_expanded_for_payload,
    enrich_rows_for_display,
)

__all__ = [
    "NotebookCellPair",
    "build_notebook_diff_payload",
    "normalize_notebook_document",
    "notebook_cell_pairs",
    "rendered_notebook_cell_pairs",
]


@dataclass(frozen=True)
class NotebookCellPair:
    """Describe one renderer-defined notebook cell pair and its public key.

    The cell dictionaries are the exact objects supplied to
    `notebook_cell_pairs`.  A missing dictionary represents a one-sided cell;
    indices retain the corresponding position in each supplied cell list.
    `cell_key` is the identity emitted to HTTP and frontend callers.
    """

    pair_kind: Literal["paired", "left_only", "right_only"]
    left_index: int | None
    right_index: int | None
    left_cell: dict[str, Any] | None
    right_cell: dict[str, Any] | None
    cell_key: str


def _cell_metadata(cell: dict[str, Any] | None) -> dict[str, Any]:
    """Return one cell's structurally valid metadata mapping."""
    if cell is None:
        return {}
    metadata = cell.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _cell_outputs(cell: dict[str, Any] | None) -> list[Any]:
    """Return the outputs of one structurally valid code cell."""
    if cell is None or cell.get("cell_type") != "code":
        return []
    outputs = cell.get("outputs", [])
    return outputs if isinstance(outputs, list) else []


def rendered_notebook_cell_pairs(
    left_cells: list[dict[str, Any]],
    right_cells: list[dict[str, Any]],
) -> list[NotebookCellPair]:
    """Return exactly the cell pairs emitted by the notebook File surface.

    A paired cell with identical source, type, metadata, and outputs produces no
    notebook cell region. One-sided or otherwise changed pairs remain in the
    renderer-defined order with their public keys unchanged.
    """

    def is_rendered(pair: NotebookCellPair) -> bool:
        """Apply the renderer's complete changed-cell admission contract."""
        return (
            pair.pair_kind != "paired"
            or _cell_source(pair.left_cell) != _cell_source(pair.right_cell)
            or (pair.left_cell or {}).get("cell_type")
            != (pair.right_cell or {}).get("cell_type")
            or _cell_metadata(pair.left_cell) != _cell_metadata(pair.right_cell)
            or _cell_outputs(pair.left_cell) != _cell_outputs(pair.right_cell)
        )

    rendered: list[NotebookCellPair] = []
    for pair in notebook_cell_pairs(left_cells, right_cells):
        if is_rendered(pair):
            rendered.append(pair)
    return rendered


def normalize_notebook_document(text: str) -> dict[str, Any] | None:
    """Parse the subset of a Jupyter notebook that dirdiff renders.

    Invalid notebook JSON returns `None` so callers can choose whether to
    fall back to a plain text diff or report a notebook-specific error.  Missing
    sides are a caller concern: if a file does not exist on one side, the caller
    should pass `None` as the parsed notebook value for that side instead of
    asking this parser to accept absent text.

    Valid notebooks are normalized to the fields needed by the renderer: a list
    of cell dictionaries and a metadata dictionary.  Unknown top-level fields
    are intentionally ignored because the UI exposes them only through the
    canonicalized metadata/output sections it knows how to render.

    This parser is deliberately permissive about cell contents.  It filters out
    non-dictionary cells and normalizes malformed top-level metadata to an
    empty mapping.  That keeps notebook rendering focused on the structures the
    frontend can display while leaving text fallback available for files that
    are not notebooks at all.
    """
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


def _cell_source(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return ""
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return str(source)


def _notebook_cell_id(cell: dict[str, Any]) -> str | None:
    cell_id = str(cell.get("id", "")).strip()
    return cell_id if cell_id != "" else None


def _cell_identity(
    *,
    left_cell: dict[str, Any] | None,
    right_cell: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive the paired cells' type, ids, and sources for one diffed cell.

    The right side wins the surviving `cell_type` and `cell_id`; a one-sided
    pair inherits everything from its present cell. Sources are the exact
    joined cell texts and never placeholders.
    """

    def _cell_type_name(
        left_cell: dict[str, Any] | None,
        right_cell: dict[str, Any] | None,
    ) -> str:
        """Choose the resulting or surviving cell type name."""
        if right_cell is not None:
            return str(right_cell.get("cell_type", "unknown"))
        if left_cell is not None:
            return str(left_cell.get("cell_type", "unknown"))
        return "unknown"

    left_source = _cell_source(left_cell)
    right_source = _cell_source(right_cell)
    left_id = _notebook_cell_id(left_cell) if left_cell is not None else None
    right_id = _notebook_cell_id(right_cell) if right_cell is not None else None
    cell_id = right_id if right_id is not None else left_id

    return {
        "cell_type": _cell_type_name(left_cell, right_cell),
        "cell_id": cell_id,
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

    if shared_unique_ids != set():
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


def notebook_cell_pairs(
    left_cells: list[dict[str, Any]],
    right_cells: list[dict[str, Any]],
) -> tuple[NotebookCellPair, ...]:
    """Return the exact ordered cell pairs and keys used by rendering.

    Callers must supply the normalized left and right cell lists.  The result
    is the single shared bridge between paired notebook contents and public
    cell keys; consumers must not independently reconstruct positional keys.
    """
    result: list[NotebookCellPair] = []
    for pair_kind, left_index, right_index in _pair_notebook_cells(
        left_cells,
        right_cells,
    ):
        left_cell = left_cells[left_index] if left_index is not None else None
        right_cell = (
            right_cells[right_index] if right_index is not None else None
        )
        match pair_kind:
            case "paired" | "left_only" | "right_only":
                typed_pair_kind = pair_kind
            case _:
                raise AssertionError(
                    f"unknown notebook pair kind: {pair_kind!r}"
                )
        result.append(
            NotebookCellPair(
                pair_kind=typed_pair_kind,
                left_index=left_index,
                right_index=right_index,
                left_cell=left_cell,
                right_cell=right_cell,
                cell_key=(
                    f"cell-{left_index if left_index is not None else 'x'}-"
                    f"{right_index if right_index is not None else 'x'}"
                ),
            )
        )
    return tuple(result)


def _render_notebook_text_payload(
    *,
    renderer: DiffEngineProtocol,
    left_text: str,
    right_text: str,
    left_path_hint: str,
    right_path_hint: str,
) -> dict[str, Any]:
    rendered = renderer.render_diff(
        old=DiffSide(
            exists=True,
            text=left_text,
            path_hint=left_path_hint,
        ),
        new=DiffSide(
            exists=True,
            text=right_text,
            path_hint=right_path_hint,
        ),
    )
    display = enrich_rows_for_display(
        rows=[dict(row) for row in rendered["rows"]],
        left_text=left_text,
        right_text=right_text,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    return {
        **display,
        "changed_lines": rendered["summary"]["changed_lines"],
        "modified_lines": rendered["summary"]["modified_lines"],
        "added_lines": rendered["summary"]["added_lines"],
        "removed_lines": rendered["summary"]["removed_lines"],
        "moved_lines": rendered["summary"]["moved_lines"],
        "hunk_count": display["hunk_count"],
    }


def _render_notebook_secondary_stats(
    *,
    left_text: str,
    right_text: str,
) -> dict[str, Any]:
    """Return engine summary counts for one summary-only secondary surface.

    Metadata and output surfaces contribute only line counts to notebook
    payloads; their rendered rows are never returned, so display enrichment
    (highlighting, token weaving, fold hints) would be pure waste — it
    measured 124ms of backend time for a notebook whose response was 2.4KB.
    """
    return dict(text_diff_summary(left_text, right_text))


def _build_notebook_cell_diff(
    *,
    renderer: DiffEngineProtocol,
    left_cell: dict[str, Any] | None,
    right_cell: dict[str, Any] | None,
    left_index: int | None,
    right_index: int | None,
    pair_kind: str,
    cell_key: str,
) -> dict[str, Any] | None:
    def _notebook_source_path(cell_type: str) -> str | None:
        """Return the syntax path hint for one notebook cell type."""
        if cell_type == "code":
            return "cell.py"
        if cell_type == "markdown":
            return "cell.md"
        if cell_type == "raw":
            return "cell.txt"
        return None

    identity = _cell_identity(left_cell=left_cell, right_cell=right_cell)
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

    source_path_hint = _notebook_source_path(cell_type)
    if source_path_hint is None:
        source_path_hint = "cell.txt"
    source_payload = _render_notebook_text_payload(
        renderer=renderer,
        left_text=left_source,
        right_text=right_source,
        left_path_hint=source_path_hint,
        right_path_hint=source_path_hint,
    )
    # Canonical serialization copies embedded output blobs in full, so it
    # runs only for a surface that actually changed, exactly once per side.
    metadata_stats = (
        _render_notebook_secondary_stats(
            left_text=canonical_json(left_metadata),
            right_text=canonical_json(right_metadata),
        )
        if metadata_changed
        else None
    )
    outputs_stats = (
        _render_notebook_secondary_stats(
            left_text=canonical_json(left_outputs),
            right_text=canonical_json(right_outputs),
        )
        if outputs_changed
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
        "cell_key": cell_key,
        "left_index": left_index,
        "right_index": right_index,
        "left_id": identity["left_id"],
        "right_id": identity["right_id"],
        "source_changed": source_changed,
        "metadata_changed": metadata_changed,
        "outputs_changed": outputs_changed,
        "source_rows": source_payload["rows"],
        "source_hunk_count": source_payload["hunk_count"],
        "source_changed_lines": source_payload["changed_lines"],
        "source_modified_lines": source_payload["modified_lines"],
        "source_added_lines": source_payload["added_lines"],
        "source_removed_lines": source_payload["removed_lines"],
        "source_moved_lines": source_payload["moved_lines"],
        "source_fold_hints": source_payload.get("fold_hints", []),
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
    }
    return payload


def _assign_notebook_source_hunk_ranges(cells: list[dict[str, Any]]) -> int:
    """Assign one file-local hunk range across rendered cell sources.

    Notebook metadata and outputs are summary-only until notebook rendering has
    a snapshot-safe design. They therefore contribute no hunk identities and
    cannot disturb navigation through the source rows returned by file-diff.
    """

    def _offset_row_hunk_indices(
        rows: list[dict[str, Any]], offset: int
    ) -> None:
        """Move section-local markers into the file-wide notebook index."""
        for row in rows:
            hunk_index = row.get("hunk_index")
            if isinstance(hunk_index, int):
                row["hunk_index"] = hunk_index + offset

    next_hunk_index = 0
    for cell in cells:
        source_rows = cell["source_rows"]
        if not isinstance(source_rows, list):
            raise TypeError("Notebook cell source_rows must be a list.")
        _offset_row_hunk_indices(source_rows, next_hunk_index)
        next_hunk_index += int(cell["source_hunk_count"])
    return next_hunk_index


def build_notebook_diff_payload(
    *,
    renderer: DiffEngineProtocol,
    display_name: str,
    left_label: str,
    right_label: str,
    left_exists: bool,
    right_exists: bool,
    left_text: str | None,
    right_text: str | None,
) -> dict[str, Any] | None:
    """Build the top-level `render_kind: "notebook"` file payload.

    The returned dictionary is validated by `NotebookFileDiffResponse` in the
    server and consumed by `NotebookViews` in the frontend.  Cell source rows
    are included eagerly because they are the primary notebook diff surface.
    Notebook-level metadata, cell metadata, and cell outputs are summarized but
    are not renderable until notebook support has a snapshot-safe design. Only
    eager cell-source rows participate in the file-local hunk order.

    `None` means the supplied text is not a valid notebook payload. The server
    then sends the captured text to the selected ordinary-file engine.

    Pairing is done at the notebook-cell level before row rendering. Stable cell
    ids are preferred when they are unique on both sides; otherwise cell source
    ordering is used. The payload therefore represents notebook intent first,
    then renders cell source through the selected diff engine and
    secondary JSON surfaces through the native text renderer.

    `rendered_notebook_cell_pairs` is the shared bridge from normalized cell
    dictionaries to the cell regions and public keys actually emitted here.
    Review placement uses that same sequence, so it cannot accept an omitted
    cell or invent a second positional-key scheme.
    """
    left_notebook = None
    if left_exists:
        if left_text is None:
            return None
        left_notebook = normalize_notebook_document(left_text)

    right_notebook = None
    if right_exists:
        if right_text is None:
            return None
        right_notebook = normalize_notebook_document(right_text)

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
        notebook_metadata_stats = _render_notebook_secondary_stats(
            left_text=canonical_json(left_metadata),
            right_text=canonical_json(right_metadata),
        )

    cells: list[dict[str, Any]] = []
    changed_lines = 0
    modified_lines = 0
    added_lines = 0
    removed_lines = 0
    moved_lines = 0

    if notebook_metadata_stats is not None:
        changed_lines += notebook_metadata_stats["changed_lines"]
        modified_lines += notebook_metadata_stats["modified_lines"]
        added_lines += notebook_metadata_stats["added_lines"]
        removed_lines += notebook_metadata_stats["removed_lines"]

    for pair in rendered_notebook_cell_pairs(left_cells, right_cells):
        cell_diff = _build_notebook_cell_diff(
            renderer=renderer,
            left_cell=pair.left_cell,
            right_cell=pair.right_cell,
            left_index=pair.left_index,
            right_index=pair.right_index,
            pair_kind=pair.pair_kind,
            cell_key=pair.cell_key,
        )
        assert cell_diff is not None, (
            "notebook changed-cell admission disagrees with rendering"
        )
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
        moved_lines += cell_diff["source_moved_lines"]

    hunk_count = _assign_notebook_source_hunk_ranges(cells)

    payload = {
        "display_name": display_name,
        "render_kind": "notebook",
        "left_label": left_label,
        "right_label": right_label,
        "summary": {
            "changed_lines": changed_lines,
            "modified_lines": modified_lines,
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "moved_lines": moved_lines,
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
        "hunk_count": hunk_count,
        "notebook_metadata_changed_lines": (
            notebook_metadata_stats["changed_lines"]
            if notebook_metadata_stats is not None
            else 0
        ),
        "cells": cells,
    }
    payload["default_expanded"] = default_expanded_for_payload(payload)
    return payload
