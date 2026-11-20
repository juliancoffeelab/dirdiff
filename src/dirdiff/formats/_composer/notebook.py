from __future__ import annotations

import base64
import binascii
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
    ImageBay,
    MediaSide,
    MovedChangeStatus,
    TextBay,
    byte_facts_bay,
    try_decode_text,
    whole_file_change,
)
from dirdiff.util import (
    Err,
    JsonValue,
    Nothing,
    Ok,
    Option,
    Result,
    Some,
    maybe,
)

__all__ = [
    "NotebookDocument",
    "notebook_bays",
    "try_load_notebook_document",
]


@dataclass(frozen=True)
class StreamOutput:
    raw: JsonValue

    text: str


@dataclass(frozen=True)
class ErrorOutput:
    raw: JsonValue

    traceback: list[str]


@dataclass(frozen=True)
class ExecuteResultOutput:
    raw: JsonValue

    text_plain: Option[str]

    image_png: Option[bytes] = field(compare=False)


@dataclass(frozen=True)
class DisplayDataOutput:
    raw: JsonValue

    text_plain: Option[str]

    image_png: Option[bytes] = field(compare=False)


NotebookOutput = (
    StreamOutput | ErrorOutput | ExecuteResultOutput | DisplayDataOutput
)


@dataclass(frozen=True)
class RejectedNotebookPart:
    raw: JsonValue

    warning: BayWarning


type NotebookOutputEntry = Result[NotebookOutput, RejectedNotebookPart]


@dataclass(frozen=True)
class NotebookCell:
    id: str

    source_bay_key: str

    cell_type: str

    source: str

    metadata: JsonValue

    outputs: list[NotebookOutputEntry]

    execution_count: Option[int] = field(compare=False)

    warnings: tuple[BayWarning, ...] = field(compare=False)


type NotebookCellEntry = Result[NotebookCell, RejectedNotebookPart]


@dataclass(frozen=True)
class NotebookDocument:
    cells: list[NotebookCellEntry]

    document: JsonValue


def try_load_notebook_document(data: bytes) -> Option[NotebookDocument]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return Nothing()
    try:
        # `json.loads` is annotated upstream as returning `Any`; pinning the
        # result to `JsonValue` stops that `Any` from leaking while preserving
        # every value the JSON data model permits.
        parsed: JsonValue = json.loads(text)
    except json.JSONDecodeError:
        return Nothing()
    if not isinstance(parsed, dict):
        return Nothing()
    cell_values: JsonValue = parsed.get("cells")
    if not isinstance(cell_values, list):
        return Nothing()

    def try_multistring(value: JsonValue) -> str | None:
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

    def try_load_output(entry: JsonValue) -> NotebookOutputEntry:

        def rejected(reason: str) -> Err[RejectedNotebookPart]:
            return Err(
                RejectedNotebookPart(
                    raw=entry,
                    warning={
                        "type": "notebook_invalid_output",
                        "message": f"Notebook output shown as raw JSON: {reason}.",
                    },
                )
            )

        if not isinstance(entry, dict):
            return rejected("output is not a mapping")
        output_type: JsonValue = entry.get("output_type")
        if output_type == "stream":
            stream_text = try_multistring(entry.get("text"))
            if stream_text is None:
                return rejected("stream text is not a string or string list")
            return Ok(StreamOutput(raw=entry, text=stream_text))
        if output_type == "error":
            traceback: JsonValue = entry.get("traceback")
            if not isinstance(traceback, list):
                return rejected("error traceback is not a list")
            frames: list[str] = []
            for frame in traceback:
                if not isinstance(frame, str):
                    return rejected("error traceback contains a non-string")
                frames.append(frame)
            return Ok(ErrorOutput(raw=entry, traceback=frames))
        if output_type not in ("execute_result", "display_data"):
            return rejected("output_type is missing or unsupported")
        data: JsonValue = entry.get("data")
        if not isinstance(data, dict):
            return rejected("display data is not a mapping")
        text_plain: Option[str] = Nothing()
        image_png: Option[bytes] = Nothing()
        if "text/plain" in data:
            plain_value = try_multistring(data["text/plain"])
            if plain_value is None:
                return rejected("text/plain is not a string or string list")
            text_plain = Some(plain_value)
        if "image/png" in data:
            encoded_png = try_multistring(data["image/png"])
            if encoded_png is None:
                return rejected("image/png is not a string or string list")
            try:
                image_png = Some(base64.b64decode(encoded_png, validate=True))
            except binascii.Error, ValueError:
                return rejected("image/png is not valid base64")
        if output_type == "execute_result":
            return Ok(
                ExecuteResultOutput(
                    raw=entry,
                    text_plain=text_plain,
                    image_png=image_png,
                )
            )
        return Ok(
            DisplayDataOutput(
                raw=entry,
                text_plain=text_plain,
                image_png=image_png,
            )
        )

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
                Err(
                    RejectedNotebookPart(
                        raw=cell,
                        warning={
                            "type": "notebook_invalid_cell",
                            "message": "Notebook cell shown as raw JSON: cell is not a mapping.",
                        },
                    )
                )
            )
            continue
        cell_type: JsonValue = cell.get("cell_type")
        source = try_multistring(cell.get("source"))
        if cell_type not in ("code", "markdown", "raw") or source is None:
            cells.append(
                Err(
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
            )
            continue
        assert isinstance(cell_type, str)
        warnings: list[BayWarning] = []
        identifier: JsonValue = cell.get("id")
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
        execution_count: Option[int] = Nothing()
        if cell_type == "code":
            output_values: JsonValue = cell.get("outputs")
            if isinstance(output_values, list):
                outputs.extend(
                    try_load_output(entry) for entry in output_values
                )
            else:
                outputs.append(
                    Err(
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
                )
            raw_count: JsonValue = cell.get("execution_count")
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
                execution_count = maybe(raw_count)
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
            Ok(
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
        )
    return Some(
        NotebookDocument(
            cells=cells,
            document={
                key: value for key, value in parsed.items() if key != "cells"
            },
        )
    )


def _paired_cells(
    left_cells: list[NotebookCellEntry],
    right_cells: list[NotebookCellEntry],
) -> list[tuple[str, Option[int], Option[int], bool]]:

    def entry_keys(entries: list[NotebookCellEntry]) -> list[str]:
        occurrences: dict[str, int] = {}
        keys: list[str] = []
        for entry in entries:
            if Ok.good(entry):
                keys.append(entry.value.id)
                continue
            digest = hashlib.sha256(
                json.dumps(
                    entry.error.raw,
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
    aligned: list[tuple[str, Option[int], Option[int]]] = []
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
                        Some(left_start + offset),
                        Some(right_start + offset),
                    )
                )
            continue
        for index in range(left_start, left_end):
            aligned.append((left_keys[index], Some(index), Nothing()))
        for index in range(right_start, right_end):
            aligned.append((right_keys[index], Nothing(), Some(index)))

    # A moved cell appears twice: once carrying only its left index and once
    # carrying only its right index. Keep the entry the surviving side gives it
    # so the frame follows the new document, and complete it with the other
    # side's index.
    # Only a key alignment split across two one-sided entries is a move; a key
    # already paired by alignment stayed where it was.
    left_only_by_key = {
        key: left
        for key, left, right in aligned
        if Some.present(left) and not Some.present(right)
    }
    right_only_by_key = {
        key: right
        for key, left, right in aligned
        if not Some.present(left) and Some.present(right)
    }
    pairs: list[tuple[str, Option[int], Option[int], bool]] = []
    emitted: set[str] = set()
    for key, left_index, right_index in aligned:
        if key in emitted:
            continue
        moved = key in left_only_by_key and key in right_only_by_key
        if moved and not Some.present(right_index):
            # The right-hand entry carries this key's position; emit there.
            continue
        emitted.add(key)
        pairs.append(
            (
                key,
                left_only_by_key[key] if moved else left_index,
                right_only_by_key[key] if moved else right_index,
                moved,
            )
        )
    return pairs


def notebook_bays(
    left_bytes: Option[bytes],
    right_bytes: Option[bytes],
    context: BayContext,
) -> Iterator[TextBay | ImageBay]:

    def canonical_json(value: JsonValue) -> str:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)

    left = left_bytes.and_then(try_load_notebook_document)
    right = right_bytes.and_then(try_load_notebook_document)
    if (Some.present(left_bytes) and not Some.present(left)) or (
        Some.present(right_bytes) and not Some.present(right)
    ):
        left_text = left_bytes.map(try_decode_text)
        right_text = right_bytes.map(try_decode_text)
        rejections = [
            value.value.error
            for value in (left_text, right_text)
            if Some.present(value) and not Ok.good(value.value)
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
            yield byte_facts_bay(
                left_bytes,
                right_bytes,
                context,
                left_media_type=Nothing(),
                right_media_type=Nothing(),
                warnings=warnings,
            )
            return
        left_decoded: Option[str] = Nothing()
        right_decoded: Option[str] = Nothing()
        if Some.present(left_text):
            assert Ok.good(left_text.value)
            left_decoded = Some(left_text.value.value)
        if Some.present(right_text):
            assert Ok.good(right_text.value)
            right_decoded = Some(right_text.value.value)
        yield TextBay(
            frame_key="notebook:raw",
            heading="Notebook",
            bay_key="notebook:raw",
            label="Raw notebook JSON",
            detail=None,
            collapsible=False,
            default_expanded=True,
            change=whole_file_change(left_decoded, right_decoded),
            left_label=context.left_label,
            right_label=context.right_label,
            left=DiffSide(
                exists=Some.present(left_bytes),
                text=left_decoded.get(),
                path_hint=context.left_path.get(),
            ),
            right=DiffSide(
                exists=Some.present(right_bytes),
                text=right_decoded.get(),
                path_hint=context.right_path.get(),
            ),
            warnings=warnings,
        )
        return

    present_side = (right if Some.present(right) else left).must()

    # A one-sided notebook's file story already says everything is new or
    # gone, so its metadata bay appears only when the present side carries
    # something to read, and renders one-sided, the way cell metadata does.
    if Some.present(left) and Some.present(right):
        show_document = left.value.document != right.value.document or (
            left.value.cells == [] and right.value.cells == []
        )
    else:
        show_document = present_side.document != {} or present_side.cells == []
    if show_document:
        left_document_text = left.map(
            lambda document: canonical_json(document.document)
        )
        right_document_text = right.map(
            lambda document: canonical_json(document.document)
        )
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
            change=whole_file_change(left_document_text, right_document_text),
            left_label=context.left_label,
            right_label=context.right_label,
            left=DiffSide(
                exists=Some.present(left),
                text=left_document_text.get(),
                path_hint="notebook-metadata.json",
            ),
            right=DiffSide(
                exists=Some.present(right),
                text=right_document_text.get(),
                path_hint="notebook-metadata.json",
            ),
        )

    def cell_heading(cell: NotebookCell) -> str | None:
        if cell.cell_type != "code":
            return None
        count = cell.execution_count
        return "In [ ]" if not Some.present(count) else f"In [{count.value}]"

    def source_hint(cell: NotebookCell) -> str:
        if cell.cell_type == "code":
            return "cell.py"
        if cell.cell_type == "markdown":
            return "cell.md"
        return "cell.txt"

    def rendered_text(out: NotebookOutputEntry) -> str:
        if not Ok.good(out):
            return canonical_json(out.error.raw)
        value = out.value
        match value:
            case StreamOutput():
                return value.text
            case ErrorOutput():
                return "\n".join(value.traceback)
            case ExecuteResultOutput() | DisplayDataOutput():
                return value.text_plain.get_or("")

    def rendered_png(out: NotebookOutputEntry) -> Option[MediaSide]:
        if not Ok.good(out):
            return Nothing()
        value = out.value
        match value:
            case ExecuteResultOutput() | DisplayDataOutput():
                return value.image_png.map(
                    lambda data: MediaSide(media_type="image/png", data=data)
                )
            case _:
                return Nothing()

    for key, left_index, right_index, moved in _paired_cells(
        left.value.cells if Some.present(left) else [],
        right.value.cells if Some.present(right) else [],
    ):
        left_cell = left_index.map(lambda index: left.must().cells[index])
        right_cell = right_index.map(lambda index: right.must().cells[index])
        present = (right_cell if Some.present(right_cell) else left_cell).must()
        # Pairing yields no pair without at least one side.

        if (Some.present(left_cell) and not Ok.good(left_cell.value)) or (
            Some.present(right_cell) and not Ok.good(right_cell.value)
        ):
            left_rejected_text = (
                Some(canonical_json(left_cell.value.error.raw))
                if (Some.present(left_cell) and not Ok.good(left_cell.value))
                else Nothing()
            )
            right_rejected_text = (
                Some(canonical_json(right_cell.value.error.raw))
                if (Some.present(right_cell) and not Ok.good(right_cell.value))
                else Nothing()
            )
            yield TextBay(
                frame_key=key,
                heading="Invalid notebook cell",
                bay_key=key,
                label="Raw cell JSON",
                detail=None,
                collapsible=False,
                default_expanded=True,
                change=whole_file_change(
                    left_rejected_text, right_rejected_text
                ),
                left_label=context.left_label,
                right_label=context.right_label,
                left=DiffSide(
                    exists=Some.present(left_cell),
                    text=left_rejected_text.get(),
                    path_hint="notebook-cell.json",
                ),
                right=DiffSide(
                    exists=Some.present(right_cell),
                    text=right_rejected_text.get(),
                    path_hint="notebook-cell.json",
                ),
                warnings=tuple(
                    entry.value.error.warning
                    for entry in (left_cell, right_cell)
                    if (Some.present(entry) and not Ok.good(entry.value))
                ),
            )
            continue
        left_source_cell: Option[NotebookCell] = Nothing()
        right_source_cell: Option[NotebookCell] = Nothing()
        if Some.present(left_cell):
            assert Ok.good(left_cell.value)
            left_source_cell = Some(left_cell.value.value)
        if Some.present(right_cell):
            assert Ok.good(right_cell.value)
            right_source_cell = Some(right_cell.value.value)
        assert Ok.good(present)
        present_cell = present.value
        hint = source_hint(present_cell)
        # The frame wears the name the cell has in the document the reviewer
        # ends with, and for a removed cell the one it had in the document
        # they started from.
        frame_heading = cell_heading(present_cell)
        source_label = {
            "code": "Code",
            "markdown": "Markdown",
            "raw": "Raw",
        }[present_cell.cell_type]

        source_changed = (
            not Some.present(left_source_cell)
            or not Some.present(right_source_cell)
            or left_source_cell.value.source != right_source_cell.value.source
            or left_source_cell.value.cell_type
            != right_source_cell.value.cell_type
        )
        # Only this builder can tell these apart. A moved cell and an edited
        # one differ in a fact about the notebook, not in their rows: a cell
        # that only moved has rows identical on both sides. A move outranks
        # an edit. Where the cell went says more than which line changed, and
        # the rows still show the edit when there is one.
        change: BayChange
        if not Some.present(left_source_cell):
            change = ChangeStatus(kind="added")
        elif not Some.present(right_source_cell):
            change = ChangeStatus(kind="removed")
        elif moved:
            change = MovedChangeStatus(
                kind="moved",
                from_heading=cell_heading(left_source_cell.value),
                to_heading=cell_heading(right_source_cell.value),
            )
        elif source_changed:
            change = ChangeStatus(kind="changed")
        else:
            change = ChangeStatus(kind="unchanged")
        yield TextBay(
            frame_key=key,
            heading=frame_heading,
            bay_key=present_cell.source_bay_key,
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
            # An unchanged source still carries every readable row when opened.
            # It starts closed when only an output or metadata changed, leaving
            # the changed attachment prominent without discarding cell context.
            collapsible=not source_changed,
            default_expanded=source_changed,
            change=change,
            left_label=context.left_label,
            right_label=context.right_label,
            left=DiffSide(
                exists=Some.present(left_source_cell),
                text=left_source_cell.get_map(lambda cell: cell.source),
                path_hint=hint,
            ),
            right=DiffSide(
                exists=Some.present(right_source_cell),
                text=right_source_cell.get_map(lambda cell: cell.source),
                path_hint=hint,
            ),
            warnings=tuple(
                warning
                for cell in (left_source_cell, right_source_cell)
                if Some.present(cell)
                for warning in cell.value.warnings
            ),
        )

        # A one-sided cell's frame already says the whole cell is new or gone,
        # so its metadata bay appears only when the present side carries
        # something to read, and renders one-sided, the way its outputs do.
        if Some.present(left_source_cell) and Some.present(right_source_cell):
            show_metadata = (
                left_source_cell.value.metadata
                != right_source_cell.value.metadata
            )
        else:
            show_metadata = present_cell.metadata not in (None, {})
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
                        if not Some.present(left_source_cell)
                        else "removed"
                        if not Some.present(right_source_cell)
                        else "changed"
                    )
                ),
                left_label=context.left_label,
                right_label=context.right_label,
                left=DiffSide(
                    exists=Some.present(left_source_cell),
                    text=(
                        left_source_cell.get_map(
                            lambda cell: canonical_json(cell.metadata)
                        )
                    ),
                    path_hint="cell-metadata.json",
                ),
                right=DiffSide(
                    exists=Some.present(right_source_cell),
                    text=(
                        right_source_cell.get_map(
                            lambda cell: canonical_json(cell.metadata)
                        )
                    ),
                    path_hint="cell-metadata.json",
                ),
            )

        left_outputs = left_source_cell.map_or([], lambda cell: cell.outputs)
        right_outputs = right_source_cell.map_or([], lambda cell: cell.outputs)
        for index in range(max(len(left_outputs), len(right_outputs))):
            left_out = Some[NotebookOutputEntry].then_bind(
                index < len(left_outputs),
                (left_outputs, index),
                lambda items, i: items[i],
            )
            right_out = Some[NotebookOutputEntry].then_bind(
                index < len(right_outputs),
                (right_outputs, index),
                lambda items, i: items[i],
            )
            # Compare notebook data, including rejected raw values, rather
            # than the identity of separately constructed Err wrappers.
            output_equal = left_out.map(
                lambda out: out.value if Ok.good(out) else out.error
            ) == right_out.map(
                lambda out: out.value if Ok.good(out) else out.error
            )
            if output_equal and not (
                Some.present(left_out) and not Ok.good(left_out.value)
            ):
                continue
            left_png = left_out.and_then(rendered_png)
            right_png = right_out.and_then(rendered_png)
            rejected = (
                Some.present(left_out) and not Ok.good(left_out.value)
            ) or (Some.present(right_out) and not Ok.good(right_out.value))
            if not rejected and (
                Some.present(left_png) or Some.present(right_png)
            ):
                image_identical = (
                    not output_equal
                    and Some.present(left_png)
                    and Some.present(right_png)
                    and left_png.value.data == right_png.value.data
                )
                yield ImageBay(
                    frame_key=key,
                    heading=frame_heading,
                    bay_key=f"{key}:output:{index}",
                    label=(
                        f"Output {index + 1} - changed beyond its PNG"
                        if image_identical
                        else f"Output {index + 1}"
                    ),
                    detail=(
                        (
                            "This output changed, but its PNG did not. Another "
                            "representation or output field carries the change."
                        )
                        if image_identical
                        else None
                    ),
                    collapsible=True,
                    default_expanded=False,
                    change=ChangeStatus(
                        kind=(
                            "added"
                            if not Some.present(left_out)
                            else "removed"
                            if not Some.present(right_out)
                            else "unchanged"
                            if output_equal
                            else "changed"
                        )
                    ),
                    left=left_png,
                    right=right_png,
                )
                continue
            left_output_text = left_out.map(rendered_text)
            right_output_text = right_out.map(rendered_text)
            # This output differs, but its rendered text may not: a
            # re-rendered plot keeps the same `<Figure ...>` line while its
            # image bytes change entirely, and an image-only output renders no
            # text at all. The bay still has to say it changed, because a
            # change nothing can land on is a hidden change.
            text_identical = (
                not output_equal
                and Some.present(left_out)
                and Some.present(right_out)
                and left_output_text == right_output_text
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
                # closed label to say before opening it.
                change=ChangeStatus(
                    kind=(
                        "added"
                        if not Some.present(left_out)
                        else "removed"
                        if not Some.present(right_out)
                        else "unchanged"
                        if output_equal
                        else "changed"
                    )
                ),
                left_label=context.left_label,
                right_label=context.right_label,
                left=DiffSide(
                    exists=Some.present(left_out),
                    text=left_output_text.get(),
                    path_hint=None,
                ),
                right=DiffSide(
                    exists=Some.present(right_out),
                    text=right_output_text.get(),
                    path_hint=None,
                ),
                warnings=tuple(
                    output.value.error.warning
                    for output in (left_out, right_out)
                    if (Some.present(output) and not Ok.good(output.value))
                ),
            )
