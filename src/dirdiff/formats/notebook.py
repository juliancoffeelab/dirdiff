"""Notebook bay builder: compose a `.ipynb` into per-cell frames.

A notebook composes into one frame per cell, in document order — every cell,
not only the changed ones. A notebook is shown whole for the same reason a text
File is: a reviewer reads a change in the context that surrounds it, and a cell
that is missing from the diff cannot be read at all. An untouched cell composes
a collapsed source bay carrying no hunk, so it costs the reviewer nothing to
skip and is still there to open.

Each frame holds, in order:

- one `text` bay for the cell source, keyed by the cell's public key. It is
  expanded when the source changed and collapsed when only the cell's metadata
  or outputs changed, so a reviewer never expands unchanged source to find the
  change.
- one `text` bay for the cell's metadata as canonical JSON, emitted only when
  the metadata changed, collapsed by default.
- one `text` bay per changed output, carrying the text this builder
  chooses for the output's variant, collapsed by default.

## Cell keys

A valid distinct `nbformat` id is the cell's public key. A cell without one
uses a pseudo-cell frame key derived from its source hash and its occurrence
among identical source, with explicit source, metadata, and output suffixes.
Source is the pseudo-cell identity: unchanged source remains findable, while an
edit changes the key and takes normal `bay_not_found` placement. This is a
visible degradation, but it preserves the notebook's readable structure rather
than replacing every cell with raw JSON.

This module owns everything notebook-shaped: parsing the notebook subset dirdiff
renders, pairing cells across the two sides, minting public keys, and choosing
each bay's content. Loading is the one boundary that checks shapes; past it,
composition reads typed fields and chooses the text shown for each output's
variant. It touches no diff engine — composition renders the bays it yields.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    BayChange,
    BayContext,
    BayWarning,
    ChangeStatus,
    MovedChangeStatus,
    TextBay,
    TextRejection,
    try_decode_text,
    whole_file_change,
)
from dirdiff.formats.blob import blob_bays

__all__ = [
    "NotebookDocument",
    "RejectedNotebookPart",
    "notebook_bays",
    "try_load_notebook_document",
]


@dataclass(frozen=True)
class StreamOutput:
    """A `stream` output: text a code cell wrote to a named stream."""

    raw: object
    """
    The output entry verbatim, compared whole to detect a change.
    """

    text: str
    """
    The stream's `text` as one string, with a multiline list of parts
    already joined. An empty string is a real, empty stream.
    """


@dataclass(frozen=True)
class ErrorOutput:
    """An `error` output: the exception a code cell raised."""

    raw: object
    """
    The output entry verbatim, compared whole to detect a change.
    """

    traceback: list[str]
    """
    The `traceback` array's strings, in order, escape codes left
    uninterpreted.

    The schema gives these strings no line convention, so joining them is
    a rendering choice, and it belongs to composition, not to loading.
    """


@dataclass(frozen=True)
class ExecuteResultOutput:
    """An `execute_result` output: the bundle a cell's result displays as."""

    raw: object
    """
    The output entry verbatim, compared whole to detect a change.
    """

    text_plain: str | None
    """
    The `data` bundle's `text/plain` entry as one string, or `None` when
    the bundle carries none — such as an image-only result — which the
    empty string must not stand in for.
    """


@dataclass(frozen=True)
class DisplayDataOutput:
    """A `display_data` output: a bundle displayed while the cell ran."""

    raw: object
    """
    The output entry verbatim, compared whole to detect a change.
    """

    text_plain: str | None
    """
    The `data` bundle's `text/plain` entry as one string, or `None` when
    the bundle carries none — such as an image-only display — which the
    empty string must not stand in for.
    """


NotebookOutput = (
    StreamOutput | ErrorOutput | ExecuteResultOutput | DisplayDataOutput
)
"""One code-cell output, loaded as the variant its `output_type` declares.

Every variant keeps its raw entry beside the fields composition reads from
it. Raw equality is what "this output changed" means — two outputs whose
shown text agrees can still differ, and that difference must stay visible.
The variant records what the output is; the text shown for it is
composition's choice, not a fact of the loaded data.
"""


@dataclass(frozen=True)
class RejectedNotebookPart:
    """One cell or output preserved as raw JSON after shape rejection."""

    raw: object
    """The exact parsed JSON value at the rejected boundary."""

    warning: BayWarning
    """Why normal structured composition was impossible for this value."""


NotebookOutputEntry = NotebookOutput | RejectedNotebookPart
"""One valid notebook output or its honestly preserved raw value."""


@dataclass(frozen=True)
class NotebookCell:
    """One loaded cell, reduced to the fields composition reads.

    The format follows the Jupyter Notebook schema, at least the parts
    that we care about.
    """

    id: str
    """
    The cell's public key, distinct within its document.

    A valid distinct schema `cell_id` is retained. Otherwise the loader derives
    a pseudo-cell key from source and its occurrence among identical sources,
    and records a visible warning on the cell.
    """

    source_bay_key: str
    """The source bay's public key; pseudo-cells add the explicit `:src`."""

    cell_type: str
    """
    One of the three `nbformat` cell types: `code`, `markdown`, or `raw`.

    Composition maps each to its source label and syntax hint.
    """

    source: str
    """
    The cell's source as one string, with a multiline list of parts already
    joined.
    """

    metadata: object
    """
    The cell's metadata value verbatim.

    Composition compares it whole and renders it as canonical JSON, and never
    reads inside it.
    """

    outputs: list[NotebookOutputEntry]
    """
    One entry per output of a code cell, in output order.

    A non-code cell has none.
    """

    execution_count: int | None = field(compare=False)
    """
    The prompt number of the cell's last execution: `In [4]` is count 4.
    `None` for a code cell never executed, and for markdown and raw cells,
    which have no prompt.

    Excluded from equality: the count names the cell, it is not reviewed
    content, so a re-run that changed nothing else must not make the cell
    read as changed.
    """

    warnings: tuple[BayWarning, ...] = field(compare=False)
    """Non-fatal identity or heading damage shown on the source bay."""


NotebookCellEntry = NotebookCell | RejectedNotebookPart
"""One structured cell or the raw JSON value rejected at that boundary."""


@dataclass(frozen=True)
class NotebookDocument:
    """One side's loaded notebook: cells plus everything else in the file."""

    cells: list[NotebookCellEntry]
    """
    Every cell in the file, in document order.
    """

    document: object
    """
    The top-level mapping minus `cells`, kept verbatim.

    A change to the kernelspec, the format version, or any field this module
    does not interpret is still a change to the file, and keeping the mapping
    whole is what lets it be seen. Composition compares it whole and renders
    it as canonical JSON; it never reads inside it.
    """


def try_load_notebook_document(data: bytes) -> NotebookDocument | None:
    """Load every usable notebook part, or reject the document boundary.

    Composition's classification calls this to decide the notebook branch: a
    side this rejects composes as ordinary text, where every difference is
    visible as raw JSON, so rejection can never hide a change. An absent side
    is the caller's concern: it passes an empty document, which one-sided
    pairing already expects, rather than asking this to accept absent bytes.

    The document boundary requires UTF-8 JSON mapping with a cell list. Inside
    that list, each cell and output is checked independently. A rejected part
    retains its parsed JSON and warning while valid siblings keep their typed
    structure. Nothing is coerced or silently dropped.
    The bytes must decode as UTF-8 and load as a JSON mapping whose `cells` is
    a list of mappings.
    Every cell must carry one of the three `nbformat` cell types and a source
    that is a string or a list of string parts. A code cell must carry a list
    of outputs, each a mapping declaring one of the four `nbformat` output
    types: a `stream` carries its `text`, an `error` its `traceback`, and an
    `execute_result` or `display_data` its required `data` bundle. Values
    composition treats whole — the document mapping, cell metadata, each raw
    output entry — are kept verbatim, whatever shape they have.

    A unique schema-valid cell id remains its key. Missing, invalid, or
    duplicate ids receive source-derived pseudo-cell keys and a warning.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    try:
        # `json.loads` is annotated upstream as returning `Any`; pinning the
        # result to `object` stops that `Any` from leaking, so every shape
        # below is proved by `isinstance` rather than assumed.
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    cell_values: object = parsed.get("cells")
    if not isinstance(cell_values, list):
        return None

    def try_multistring(value: object) -> str | None:
        """Join one `nbformat` multiline value into one string.

        If the `value` is a string, we return it as is.
        If the `value` is list of strings, we join them and return.
        Else, we return failure as None.
        """
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return None
        parts: list[str] = []
        for part in value:
            if not isinstance(part, str):
                return None
            parts.append(part)
        return "".join(parts)

    def try_load_output(entry: object) -> NotebookOutputEntry:
        """Load one output entry or preserve its raw rejected value.

        The raw entry is kept verbatim beside the fields the entry's
        `output_type` says to read: a `stream` requires its `text`, an
        `error` its `traceback` of strings, and an `execute_result` or
        `display_data` its `data` bundle, whose `text/plain` entry is
        optional. Any other `output_type` is not an `nbformat` output, so
        the entry is rejected.
        """

        def rejected(reason: str) -> RejectedNotebookPart:
            """Preserve this output with one stable warning."""
            return RejectedNotebookPart(
                raw=entry,
                warning={
                    "type": "notebook_invalid_output",
                    "message": f"Notebook output shown as raw JSON: {reason}.",
                },
            )

        if not isinstance(entry, dict):
            return rejected("output is not a mapping")
        output_type: object = entry.get("output_type")
        if output_type == "stream":
            stream_text = try_multistring(entry.get("text"))
            if stream_text is None:
                return rejected("stream text is not a string or string list")
            return StreamOutput(raw=entry, text=stream_text)
        if output_type == "error":
            traceback: object = entry.get("traceback")
            if not isinstance(traceback, list):
                return rejected("error traceback is not a list")
            frames: list[str] = []
            for frame in traceback:
                if not isinstance(frame, str):
                    return rejected("error traceback contains a non-string")
                frames.append(frame)
            return ErrorOutput(raw=entry, traceback=frames)
        if output_type not in ("execute_result", "display_data"):
            return rejected("output_type is missing or unsupported")
        data: object = entry.get("data")
        if not isinstance(data, dict):
            return rejected("display data is not a mapping")
        if "text/plain" in data:
            text_plain = try_multistring(data["text/plain"])
            if text_plain is None:
                return rejected("text/plain is not a string or string list")
        else:
            text_plain = None
        if output_type == "execute_result":
            return ExecuteResultOutput(raw=entry, text_plain=text_plain)
        return DisplayDataOutput(raw=entry, text_plain=text_plain)

    identifier_counts: dict[str, int] = {}
    for cell in cell_values:
        if not isinstance(cell, dict):
            continue
        claimed_identifier = cell.get("id")
        if (
            not isinstance(claimed_identifier, str)
            or re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", claimed_identifier) is None
        ):
            continue
        identifier_counts[claimed_identifier] = (
            identifier_counts.get(claimed_identifier, 0) + 1
        )
    source_occurrences: dict[str, int] = {}
    cells: list[NotebookCellEntry] = []
    for cell in cell_values:
        if not isinstance(cell, dict):
            cells.append(
                RejectedNotebookPart(
                    raw=cell,
                    warning={
                        "type": "notebook_invalid_cell",
                        "message": "Notebook cell shown as raw JSON: cell is not a mapping.",
                    },
                )
            )
            continue
        cell_type: object = cell.get("cell_type")
        source = try_multistring(cell.get("source"))
        if cell_type not in ("code", "markdown", "raw") or source is None:
            cells.append(
                RejectedNotebookPart(
                    raw=cell,
                    warning={
                        "type": "notebook_invalid_cell",
                        "message": (
                            "Notebook cell shown as raw JSON: it has no valid "
                            "cell type and source."
                        ),
                    },
                )
            )
            continue
        assert isinstance(cell_type, str)
        warnings: list[BayWarning] = []
        identifier: object = cell.get("id")
        valid_identifier = (
            isinstance(identifier, str)
            and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier) is not None
            and identifier_counts[identifier] == 1
        )
        if valid_identifier:
            assert isinstance(identifier, str)
            cell_key = identifier
            source_bay_key = identifier
        else:
            source_digest = hashlib.sha256(source.encode()).hexdigest()
            occurrence = source_occurrences.get(source_digest, 0)
            source_occurrences[source_digest] = occurrence + 1
            cell_key = f"pseudocell:{source_digest}:{occurrence}"
            source_bay_key = f"{cell_key}:src"
            warnings.append(
                {
                    "type": "notebook_missing_cell_id",
                    "message": (
                        "Cell has no unique valid id; its review key is "
                        "derived from the cell source."
                    ),
                }
            )
        outputs: list[NotebookOutputEntry] = []
        # The schema requires `execution_count` on code cells: an integer
        # prompt number, or null for a cell never executed. Other cell types
        # carry no prompt. A bool is not a prompt number, even though
        # `isinstance` counts it as an int.
        execution_count: int | None = None
        if cell_type == "code":
            output_values: object = cell.get("outputs")
            if isinstance(output_values, list):
                outputs.extend(
                    try_load_output(entry) for entry in output_values
                )
            else:
                outputs.append(
                    RejectedNotebookPart(
                        raw=output_values,
                        warning={
                            "type": "notebook_invalid_output",
                            "message": (
                                "Notebook outputs shown as raw JSON: outputs "
                                "is not a list."
                            ),
                        },
                    )
                )
            raw_count: object = cell.get("execution_count")
            if "execution_count" not in cell:
                warnings.append(
                    {
                        "type": "notebook_invalid_execution_count",
                        "message": (
                            "Cell has no execution_count; its heading omits "
                            "the prompt number."
                        ),
                    }
                )
            elif raw_count is None or (
                isinstance(raw_count, int) and not isinstance(raw_count, bool)
            ):
                execution_count = raw_count
            else:
                warnings.append(
                    {
                        "type": "notebook_invalid_execution_count",
                        "message": (
                            "Cell execution_count is invalid; its heading "
                            "omits the prompt number."
                        ),
                    }
                )
        cells.append(
            NotebookCell(
                id=cell_key,
                source_bay_key=source_bay_key,
                cell_type=cell_type,
                source=source,
                metadata=cell.get("metadata"),
                outputs=outputs,
                execution_count=execution_count,
                warnings=tuple(warnings),
            )
        )
    return NotebookDocument(
        cells=cells,
        document={
            key: value for key, value in parsed.items() if key != "cells"
        },
    )


def _paired_cells(
    left_cells: list[NotebookCellEntry],
    right_cells: list[NotebookCellEntry],
) -> list[tuple[str, int | None, int | None, bool]]:
    """Pair cells across the two sides by public key, in document order.

    Keys are durable, so a key present on both sides always names one cell and
    must produce exactly one pair. Sequence alignment supplies the order — it
    keeps added and removed cells where the notebook puts them — but alignment
    alone reports a moved cell as a deletion and an unrelated insertion, which
    would emit that cell's key twice. Bay keys are unique within one composed
    diff, so the two halves of a move are rejoined here into the single pair the
    key already says they are. Such a pair is reported as moved: alignment broke
    for it precisely because the cell changed position, and a move is a change
    the reviewer must be able to reach even when the cell's contents are equal.
    """

    def entry_keys(entries: list[NotebookCellEntry]) -> list[str]:
        """Return unique keys for structured and raw rejected cells."""
        occurrences: dict[str, int] = {}
        keys: list[str] = []
        for entry in entries:
            if isinstance(entry, NotebookCell):
                keys.append(entry.id)
                continue
            digest = hashlib.sha256(
                json.dumps(
                    entry.raw,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            occurrence = occurrences.get(digest, 0)
            occurrences[digest] = occurrence + 1
            keys.append(f"rejected-cell:{digest}:{occurrence}")
        return keys

    left_keys = entry_keys(left_cells)
    right_keys = entry_keys(right_cells)
    aligned: list[tuple[str, int | None, int | None]] = []
    matcher = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)
    for (
        tag,
        left_start,
        left_end,
        right_start,
        right_end,
    ) in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(left_end - left_start):
                aligned.append(
                    (
                        right_keys[right_start + offset],
                        left_start + offset,
                        right_start + offset,
                    )
                )
            continue
        for index in range(left_start, left_end):
            aligned.append((left_keys[index], index, None))
        for index in range(right_start, right_end):
            aligned.append((right_keys[index], None, index))

    # A moved cell appears twice: once carrying only its left index and once
    # carrying only its right index. Keep the entry the surviving side gives it
    # so the frame follows the new document, and complete it with the other
    # side's index.
    # Only a key alignment split across two one-sided entries is a move; a key
    # already paired by alignment stayed where it was.
    left_only_by_key = {
        key: left
        for key, left, right in aligned
        if left is not None and right is None
    }
    right_only_by_key = {
        key: right
        for key, left, right in aligned
        if left is None and right is not None
    }
    pairs: list[tuple[str, int | None, int | None, bool]] = []
    emitted: set[str] = set()
    for key, left_index, right_index in aligned:
        if key in emitted:
            continue
        moved = key in left_only_by_key and key in right_only_by_key
        if moved and right_index is None:
            # The right-hand entry carries this key's position; emit there.
            continue
        emitted.add(key)
        pairs.append(
            (
                key,
                left_only_by_key.get(key) if moved else left_index,
                right_only_by_key.get(key) if moved else right_index,
                moved,
            )
        )
    return pairs


def notebook_bays(
    left_bytes: bytes | None,
    right_bytes: bytes | None,
    context: BayContext,
) -> Iterator[TextBay]:
    """Parse notebook bytes and yield their bays in document order.

    A change to the notebook's own top-level fields comes first, in its own
    frame, because it belongs to no cell. Each changed cell then becomes one
    frame keyed by its public cell key: a source bay, then a metadata bay
    when the metadata changed, then one bay per changed output. Nothing here
    touches a diff engine; the loaded sides ride along for composition to
    render.

    Damage before a usable cell list exists produces raw notebook text or blob
    facts with a warning. Inside a usable cell list, loading preserves rejected
    cells and outputs at their own boundary. A `None` byte side means the File
    was added or removed; no empty document is invented for it.
    """

    def canonical_json(value: object) -> str:
        """Serialize one value composition treats whole in its stable form.

        Sorted keys and fixed indentation make equal values render equal
        text across Python dictionary ordering, so a rendered JSON diff
        changes only when the value does.
        """
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)

    left = (
        None if left_bytes is None else try_load_notebook_document(left_bytes)
    )
    right = (
        None if right_bytes is None else try_load_notebook_document(right_bytes)
    )
    if (left_bytes is not None and left is None) or (
        right_bytes is not None and right is None
    ):
        left_text = None if left_bytes is None else try_decode_text(left_bytes)
        right_text = (
            None if right_bytes is None else try_decode_text(right_bytes)
        )
        rejections = [
            value
            for value in (left_text, right_text)
            if isinstance(value, TextRejection)
        ]
        warnings: tuple[BayWarning, ...] = tuple(
            {
                "type": f"notebook_{rejection.reason.replace('-', '_')}",
                "message": (
                    f"Notebook shown as byte facts: {rejection.detail}."
                ),
            }
            for rejection in rejections
        ) or (
            {
                "type": "notebook_invalid_document",
                "message": (
                    "Notebook structure could not be read; showing its raw JSON."
                ),
            },
        )
        if len(rejections) > 0:
            yield from blob_bays(
                left_bytes,
                right_bytes,
                context,
                left_media_type=None,
                right_media_type=None,
                warnings=warnings,
            )
            return
        assert not isinstance(left_text, TextRejection)
        assert not isinstance(right_text, TextRejection)
        yield TextBay(
            frame_key="notebook:raw",
            heading="Notebook",
            bay_key="notebook:raw",
            label="Raw notebook JSON",
            detail=None,
            collapsible=False,
            default_expanded=True,
            change=whole_file_change(left_text, right_text),
            left_label=context.left_label,
            right_label=context.right_label,
            left=DiffSide(
                exists=left_bytes is not None,
                text=left_text,
                path_hint=context.left_path,
            ),
            right=DiffSide(
                exists=right_bytes is not None,
                text=right_text,
                path_hint=context.right_path,
            ),
            warnings=warnings,
        )
        return

    present_side = right if right is not None else left
    assert present_side is not None, "a File always carries at least one side"
    # A one-sided notebook's file story already says everything is new or
    # gone, so its metadata bay appears only when the present side carries
    # something to read, and renders one-sided, the way cell metadata does.
    if left is not None and right is not None:
        show_document = left.document != right.document
    else:
        show_document = present_side.document not in (None, {})
    if show_document:
        yield TextBay(
            # The colon keeps this key clear of every cell key, which is an
            # `nbformat` cell id.
            frame_key="notebook:metadata",
            heading="Notebook",
            bay_key="notebook:metadata",
            label="Notebook metadata",
            detail=None,
            collapsible=True,
            default_expanded=False,
            change=ChangeStatus(
                kind=(
                    "added"
                    if left is None
                    else "removed"
                    if right is None
                    else "changed"
                )
            ),
            left_label=context.left_label,
            right_label=context.right_label,
            left=DiffSide(
                exists=left is not None,
                text=None if left is None else canonical_json(left.document),
                path_hint="notebook-metadata.json",
            ),
            right=DiffSide(
                exists=right is not None,
                text=None if right is None else canonical_json(right.document),
                path_hint="notebook-metadata.json",
            ),
        )

    def cell_heading(cell: NotebookCell) -> str | None:
        """Name one cell, or report that it has no name.

        A cell's only name is its Jupyter prompt — the one a notebook user
        ever sees — so an executed code cell is named `In [4]` and one never
        executed is named `In [ ]`. Prose carries no prompt, so markdown and
        raw cells have no name at all, and a caller with nothing to name says
        only what happened to the cell.
        """
        if cell.cell_type != "code":
            return None
        count = cell.execution_count
        return "In [ ]" if count is None else f"In [{count}]"

    def source_hint(cell: NotebookCell) -> str:
        """The syntax path hint for one cell type's source."""
        if cell.cell_type == "code":
            return "cell.py"
        if cell.cell_type == "markdown":
            return "cell.md"
        return "cell.txt"

    def rendered_text(out: NotebookOutputEntry | None) -> str | None:
        """Choose the text shown for one side of an output bay.

        This is the builder's representation choice: a stream shows its
        text, an error shows its traceback strings joined as lines —
        escape codes still uninterpreted — and a bundle shows its
        `text/plain`. A `DiffSide` reserves `text=None` for a missing
        side, so an output that exists without a text representation
        renders as empty text here, at the widget boundary; the model
        keeps the two apart.
        """
        match out:
            case None:
                return None
            case RejectedNotebookPart():
                return canonical_json(out.raw)
            case StreamOutput():
                return out.text
            case ErrorOutput():
                return "\n".join(out.traceback)
            case ExecuteResultOutput() | DisplayDataOutput():
                return "" if out.text_plain is None else out.text_plain

    for key, left_index, right_index, moved in _paired_cells(
        left.cells if left is not None else [],
        right.cells if right is not None else [],
    ):
        left_cell = (
            left.cells[left_index]
            if left is not None and left_index is not None
            else None
        )
        right_cell = (
            right.cells[right_index]
            if right is not None and right_index is not None
            else None
        )
        present = right_cell if right_cell is not None else left_cell
        # Pairing yields no pair without at least one side.
        assert present is not None, "a cell pair always carries one side"
        if isinstance(left_cell, RejectedNotebookPart) or isinstance(
            right_cell, RejectedNotebookPart
        ):
            left_text = (
                canonical_json(left_cell.raw)
                if isinstance(left_cell, RejectedNotebookPart)
                else None
            )
            right_text = (
                canonical_json(right_cell.raw)
                if isinstance(right_cell, RejectedNotebookPart)
                else None
            )
            yield TextBay(
                frame_key=key,
                heading="Invalid notebook cell",
                bay_key=key,
                label="Raw cell JSON",
                detail=None,
                collapsible=False,
                default_expanded=True,
                change=whole_file_change(left_text, right_text),
                left_label=context.left_label,
                right_label=context.right_label,
                left=DiffSide(
                    exists=left_cell is not None,
                    text=left_text,
                    path_hint="notebook-cell.json",
                ),
                right=DiffSide(
                    exists=right_cell is not None,
                    text=right_text,
                    path_hint="notebook-cell.json",
                ),
                warnings=tuple(
                    entry.warning
                    for entry in (left_cell, right_cell)
                    if isinstance(entry, RejectedNotebookPart)
                ),
            )
            continue
        assert left_cell is None or isinstance(left_cell, NotebookCell)
        assert right_cell is None or isinstance(right_cell, NotebookCell)
        assert isinstance(present, NotebookCell)
        hint = source_hint(present)
        # The frame wears the name the cell has in the document the reviewer
        # ends with, and for a removed cell the one it had in the document
        # they started from.
        frame_heading = cell_heading(present)
        source_label = {
            "code": "Code",
            "markdown": "Markdown",
            "raw": "Raw",
        }[present.cell_type]

        source_changed = (
            left_cell is None
            or right_cell is None
            or left_cell.source != right_cell.source
            or left_cell.cell_type != right_cell.cell_type
        )
        # Only this builder can tell these apart. A moved cell and an edited
        # one differ in a fact about the notebook, not in their rows: a cell
        # that only moved has rows identical on both sides. A move outranks
        # an edit — where the cell went says more than which line changed —
        # and the rows still show the edit when there is one.
        change: BayChange
        if left_cell is None:
            change = ChangeStatus(kind="added")
        elif right_cell is None:
            change = ChangeStatus(kind="removed")
        elif moved:
            change = MovedChangeStatus(
                kind="moved",
                from_heading=cell_heading(left_cell),
                to_heading=cell_heading(right_cell),
            )
        elif source_changed:
            change = ChangeStatus(kind="changed")
        else:
            change = ChangeStatus(kind="unchanged")
        yield TextBay(
            frame_key=key,
            heading=frame_heading,
            bay_key=present.source_bay_key,
            # A cell that only moved has no changed row to land on, so its
            # `detail` says what happened and it consumes a hunk at its own root.
            label=source_label,
            detail=(
                None
                if source_changed or not moved
                else (
                    "This cell only moved. Its contents are unchanged, so "
                    "there is nothing to show as a line difference."
                )
            ),
            # The cell's source is the frame's body only when the source is
            # what changed. A cell whose outputs or metadata moved on without
            # it has nothing to read in its rows, and folds cannot hide a bay
            # that is unchanged end to end, so it collapses like an untouched
            # cell and the bay that did change carries the hunk.
            collapsible=not source_changed,
            default_expanded=source_changed,
            change=change,
            left_label=context.left_label,
            right_label=context.right_label,
            left=DiffSide(
                exists=left_cell is not None,
                text=None if left_cell is None else left_cell.source,
                path_hint=hint,
            ),
            right=DiffSide(
                exists=right_cell is not None,
                text=None if right_cell is None else right_cell.source,
                path_hint=hint,
            ),
            warnings=tuple(
                warning
                for cell in (left_cell, right_cell)
                if cell is not None
                for warning in cell.warnings
            ),
        )

        # A one-sided cell's frame already says the whole cell is new or gone,
        # so its metadata bay appears only when the present side carries
        # something to read, and renders one-sided, the way its outputs do.
        if left_cell is not None and right_cell is not None:
            show_metadata = left_cell.metadata != right_cell.metadata
        else:
            show_metadata = present.metadata not in (None, {})
        if show_metadata:
            yield TextBay(
                frame_key=key,
                heading=frame_heading,
                bay_key=f"{key}:metadata",
                label="Cell metadata",
                detail=None,
                collapsible=True,
                default_expanded=False,
                # A bay renders its own two sides, so it says what happened to
                # them: metadata that arrives with a new cell was added, not
                # changed, and the frame around it saying the same thing about
                # the whole cell does not make the bay's own word wrong.
                change=ChangeStatus(
                    kind=(
                        "added"
                        if left_cell is None
                        else "removed"
                        if right_cell is None
                        else "changed"
                    )
                ),
                left_label=context.left_label,
                right_label=context.right_label,
                left=DiffSide(
                    exists=left_cell is not None,
                    text=(
                        None
                        if left_cell is None
                        else canonical_json(left_cell.metadata)
                    ),
                    path_hint="cell-metadata.json",
                ),
                right=DiffSide(
                    exists=right_cell is not None,
                    text=(
                        None
                        if right_cell is None
                        else canonical_json(right_cell.metadata)
                    ),
                    path_hint="cell-metadata.json",
                ),
            )

        left_outputs = [] if left_cell is None else left_cell.outputs
        right_outputs = [] if right_cell is None else right_cell.outputs
        for index in range(max(len(left_outputs), len(right_outputs))):
            left_out = (
                left_outputs[index] if index < len(left_outputs) else None
            )
            right_out = (
                right_outputs[index] if index < len(right_outputs) else None
            )
            if left_out == right_out:
                continue
            left_text = rendered_text(left_out)
            right_text = rendered_text(right_out)
            # This output differs, but its rendered text may not: a
            # re-rendered plot keeps the same `<Figure ...>` line while its
            # image bytes change entirely, and an image-only output renders no
            # text at all. The bay still has to say it changed, because a
            # change nothing can land on is a hidden change.
            text_identical = (
                left_out is not None
                and right_out is not None
                and left_text == right_text
            )
            label = (
                f"Output {index + 1} — changed beyond its text"
                if text_identical
                else f"Output {index + 1}"
            )
            yield TextBay(
                frame_key=key,
                heading=frame_heading,
                bay_key=f"{key}:output:{index}",
                label=label,
                detail=(
                    (
                        "This output changed, but the text dirdiff shows for "
                        "it did not: a re-rendered figure keeps the same "
                        "summary line while its image data changes. Compare "
                        "the notebook source to see what differs."
                    )
                    if text_identical
                    else None
                ),
                collapsible=True,
                default_expanded=False,
                # One output is one bay with its own two sides: an output the
                # run produced for the first time is added and one it stopped
                # producing is removed, which is what the reviewer needs the
                # collapsed label to say before opening it.
                change=ChangeStatus(
                    kind=(
                        "added"
                        if left_out is None
                        else "removed"
                        if right_out is None
                        else "changed"
                    )
                ),
                left_label=context.left_label,
                right_label=context.right_label,
                left=DiffSide(
                    exists=left_out is not None,
                    text=left_text,
                    path_hint=None,
                ),
                right=DiffSide(
                    exists=right_out is not None,
                    text=right_text,
                    path_hint=None,
                ),
                warnings=tuple(
                    output.warning
                    for output in (left_out, right_out)
                    if isinstance(output, RejectedNotebookPart)
                ),
            )
