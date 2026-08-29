"""Loading and composition of Jupyter notebooks into cell frames.

## Public interface

`try_load_notebook_document` validates the notebook subset composition needs.
`notebook_bays` pairs cells, assigns validated-ID or source-derived keys, and
yields source, changed metadata, and changed output bays in document order.

## Purpose and boundaries

Notebook structure determines which content belongs together and which identity
can survive across Snapshots. Invalid cells and outputs remain visible as
canonical JSON with a warning instead of invalidating usable siblings. The
module yields decoded text bays and image bays carrying decoded PNG bytes;
engines and payload reduction run later.
"""

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
    TextRejection,
    try_decode_text,
    whole_file_change,
)
from dirdiff.formats.blob import blob_bays
from dirdiff.util import JsonValue

__all__ = [
    "notebook_bays",
]


@dataclass(frozen=True)
class StreamOutput:
    """Load text written by a code cell to a named stream.

    Notebook loading creates this variant from `output_type="stream"`.
    Composition compares `raw` for change identity and renders `text` for the
    reviewer.

    The value does not interpret ANSI escapes, stream names, or line structure.
    """

    raw: JsonValue
    """Complete parsed stream-output mapping before typed extraction.

    Pairing compares this value as one JSON unit, so changes to `name`, unknown
    fields, or the original multiline representation remain visible even when
    rendered `text` is unchanged. Composition must not mutate or discard it.
    """

    text: str
    """
    The stream's `text` as one string, with a multiline sequence of parts
    already joined. An empty string is a real, empty stream.
    """


@dataclass(frozen=True)
class ErrorOutput:
    """Load the traceback of an exception raised by a code cell.

    Notebook loading creates this variant from `output_type="error"`.
    Composition compares `raw` and chooses how to render the traceback entries.

    The value does not parse traceback frames, ANSI escapes, or exception types.
    """

    raw: JsonValue
    """Complete parsed error-output mapping before traceback extraction.

    Pairing compares it whole, preserving changes to exception name, value, and
    unknown fields that rendered traceback lines may not show. Composition reads
    the typed traceback separately and must retain this value unchanged.
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
    """Load a value displayed as the result of executing a code cell.

    Notebook loading creates this variant from `output_type="execute_result"`.
    It preserves the complete entry for semantic equality and exposes the
    supported MIME representations to composition. Representation selection
    remains a builder decision.
    """

    raw: JsonValue
    """Complete parsed execute-result mapping before plain-text extraction.

    Pairing compares it whole so execution metadata and rich media changes are
    not hidden when the selected representation stays equal or is absent.
    Composition must preserve this value for semantic equality.
    """

    text_plain: str | None
    """Joined `text/plain` representation from the result's data bundle.

    `None` means the bundle has no plain-text entry, as with an image-only
    result; `""` is a present empty representation and must remain distinct.
    Composition renders only a present value while `raw` preserves other media.
    """

    image_png: bytes | None = field(compare=False)
    """Decoded `image/png` representation from the result's data bundle.

    `None` means the bundle has no PNG entry. The bytes are the exact value
    represented by valid base64 in the captured notebook; composition does not
    inspect, transcode, or repair them. Equality already compares the complete
    raw entry, so it does not compare the decoded bytes a second time.
    """


@dataclass(frozen=True)
class DisplayDataOutput:
    """Load a display bundle emitted while a code cell ran.

    Notebook loading creates this variant from `output_type="display_data"`.
    It preserves the complete entry for semantic equality and exposes the
    supported MIME representations to composition. A missing representation
    remains distinct from an empty one.
    """

    raw: JsonValue
    """Complete parsed display-data mapping before plain-text extraction.

    Pairing compares the full bundle so MIME entries, transient metadata, and
    unknown fields participate in change identity even when rendered plain text
    does not. Composition must not reduce equality to `text_plain`.
    """

    text_plain: str | None
    """Joined `text/plain` representation from the display data bundle.

    `None` means no plain-text entry exists, while `""` is present empty text.
    Composition may render a present value, but equality continues to use `raw`
    so changes to rich media remain reviewable.
    """

    image_png: bytes | None = field(compare=False)
    """Decoded `image/png` representation from the display bundle.

    `None` means no PNG representation was supplied. A present value contains
    the exact decoded bytes and is never inferred from another MIME entry.
    Equality uses the complete raw entry and skips this duplicate form.
    """


NotebookOutput = (
    StreamOutput | ErrorOutput | ExecuteResultOutput | DisplayDataOutput
)
"""One code-cell output, loaded as the variant its `output_type` declares.

- `StreamOutput` carries stream text.
- `ErrorOutput` carries traceback entries.
- `ExecuteResultOutput` and `DisplayDataOutput` carry optional plain text and
  decoded PNG bytes from their display bundles.

Every variant retains raw JSON for change identity. Two outputs whose selected
representation agrees can still differ in raw content, and that difference must
remain visible. Composition chooses what to show; the loaded value does not
claim that representation is the complete output.
"""


@dataclass(frozen=True)
class RejectedNotebookPart:
    """Preserve one malformed cell or output without hiding its content.

    Notebook loading creates this value when the document boundary is valid but
    one nested value violates the supported shape. Composition renders `raw` as
    canonical JSON and shows `warning` beside it.

    It does not repair the value or pretend it belongs to a structured variant.
    """

    raw: JsonValue
    """Unmodified JSON subtree that failed the supported cell/output shape.

    Composition serializes this value canonically so no user content disappears.
    It must not be repaired or reinterpreted as a structured notebook variant.
    """

    warning: BayWarning
    """Stable visible explanation attached to the raw replacement bay.

    The warning is scoped to this rejected cell or output. It preserves damage
    without turning the otherwise valid notebook document into a flat blob.
    """


NotebookOutputEntry = NotebookOutput | RejectedNotebookPart
"""Hold one code-cell output after notebook boundary validation.

- `NotebookOutput` is a supported structured output variant.
- `RejectedNotebookPart` preserves malformed output as raw JSON with a warning.

Composition handles both without dropping content. The union does not repair a
rejected output or treat it as structured data.
"""


@dataclass(frozen=True)
class NotebookCell:
    """Provide one validated notebook cell to composition.

    Notebook loading constructs this value after normalizing source text,
    assigning a distinct public bay key, and validating or preserving each
    output. Composition uses it to build the cell's source, metadata, and output
    bays in document order.

    The record retains only notebook facts composition needs. It does not establish
    document ordering, align cells across sides, or interpret arbitrary metadata
    and rich output payloads.
    """

    id: str
    """
    The cell's public key, distinct within its document.

    A valid distinct schema `cell_id` is retained. Otherwise the loader derives
    a pseudo-cell key from source and its occurrence among identical sources,
    and records a visible warning on the cell.
    """

    source_bay_key: str
    """Public coordinate reserved for this cell's source bay.

    Valid cell IDs use their established source coordinate. Derived pseudo-cell
    IDs add `:src` explicitly so source never collides with raw rejected content
    or the cell's metadata and output bays.
    """

    cell_type: str
    """
    One of the three `nbformat` cell types: `code`, `markdown`, or `raw`.

    Composition maps each to its source label and syntax hint.
    """

    source: str
    """Complete cell source after joining the notebook multiline representation.

    The join preserves part order and exact characters. Composition renders this
    value in the source bay and compares it as reviewed content; it never
    re-reads or reconstructs source from the retained raw cell mapping.
    """

    metadata: JsonValue
    """
    The cell's metadata value verbatim.

    Composition compares it whole and renders it as canonical JSON, and never
    reads inside it.
    """

    outputs: list[NotebookOutputEntry]
    """Every accepted or rejected code-cell output in notebook order.

    Non-code cells require an empty list. Pairing preserves position and variant
    identity, while rejected entries retain their raw mapping and warning;
    callers must not discard outputs whose supported representation is absent.
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
    """Ordered cell-level damage displayed beside the source bay.

    Warnings cover derived identity and prompt-heading defects. They are excluded
    from equality so warning prose cannot make unchanged notebook content differ.
    """


NotebookCellEntry = NotebookCell | RejectedNotebookPart
"""Hold one notebook cell after document boundary validation.

- `NotebookCell` exposes the supported cell fields composition reads.
- `RejectedNotebookPart` preserves a malformed cell as raw JSON with a warning.

Cell pairing and rendering consume this union. Rejected values never masquerade
as valid cells.
"""


@dataclass(frozen=True)
class NotebookDocument:
    """Hold one loaded notebook side in the shape composition consumes.

    `try_load_notebook_document` returns this value after accepting the document
    boundary. `notebook_bays` pairs its cells and compares the remaining
    top-level document value.

    The value does not pair sides, render outputs, or discard fields the loader
    does not understand.
    """

    cells: list[NotebookCellEntry]
    """Every accepted or locally rejected cell in original document order.

    Valid entries carry stable or derived cell identities; rejected entries
    retain their raw JSON and warning instead of disappearing. Cross-side
    pairing consumes this order and identity, so callers must not filter,
    reorder, or coerce entries before `notebook_bays` runs.
    """

    document: JsonValue
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
    `execute_result` or `display_data` its required `data` bundle. Composition
    keeps the document mapping, cell metadata, and each raw output entry
    verbatim, whatever shape they have.

    A unique schema-valid cell id remains its key. Missing, invalid, or
    duplicate ids receive source-derived pseudo-cell keys and a warning.

    # Usage

    `notebook_bays` calls this independently for each captured side. A returned
    document is ready for cell pairing; callers must preserve rejected entries
    and their order.

    # Returns

    - `NotebookDocument`: The accepted top-level document, with cells kept in
      source order and invalid individual parts preserved as rejections.
    - `None`: The bytes fail the notebook document boundary. The caller must
      show the complete File as raw text or byte facts instead.

    # Failures

    Returns `None` when bytes are not UTF-8 JSON or the top-level value is not a
    mapping with a cell list. Invalid individual cells and outputs remain in a
    returned document as `RejectedNotebookPart` values.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    try:
        # `json.loads` is annotated upstream as returning `Any`; pinning the
        # result to `JsonValue` stops that `Any` from leaking while preserving
        # every value the JSON data model permits.
        parsed: JsonValue = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    cell_values: JsonValue = parsed.get("cells")
    if not isinstance(cell_values, list):
        return None

    def try_multistring(value: JsonValue) -> str | None:
        """Join one `nbformat` multiline value into one string.

        A string is returned unchanged. A list must contain only strings and is
        joined in order. Every other shape returns `None`.

        # Usage

        The cell and output loaders use this for `nbformat` fields that permit
        either spelling. `None` means that local notebook part must be rejected.

        # Returns

        - `str`: The original string or all string-list elements joined in
          their source order.
        - `None`: The value is neither a string nor a list containing only
          strings. The caller must reject that local notebook part.
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

    def try_load_output(entry: JsonValue) -> NotebookOutputEntry:
        """Load one output entry or preserve its raw rejected value.

        The raw entry is kept verbatim beside the fields the entry's
        `output_type` says to read: a `stream` requires its `text`, an
        `error` its `traceback` of strings, and an `execute_result` or
        `display_data` its `data` bundle, whose `text/plain` and `image/png`
        entries are optional. Any other `output_type` is not an `nbformat`
        output, so the entry is rejected.

        # Usage

        Code-cell loading calls this for every output entry. Keep a returned
        `RejectedNotebookPart` in the output list so damage stays visible and
        neighboring outputs remain usable.
        """

        def rejected(reason: str) -> RejectedNotebookPart:
            """Wrap the untouched output JSON with a scoped validation warning.

            The nested loader calls this for every unsupported output shape.
            `reason` becomes explanatory prose only; the raw entry remains the
            content composition later renders and compares.
            """
            return RejectedNotebookPart(
                raw=entry,
                warning={
                    "type": "notebook_invalid_output",
                    "message": f"Notebook output shown as raw JSON: {reason}.",
                },
            )

        if not isinstance(entry, dict):
            return rejected("output is not a mapping")
        output_type: JsonValue = entry.get("output_type")
        if output_type == "stream":
            stream_text = try_multistring(entry.get("text"))
            if stream_text is None:
                return rejected("stream text is not a string or string list")
            return StreamOutput(raw=entry, text=stream_text)
        if output_type == "error":
            traceback: JsonValue = entry.get("traceback")
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
        data: JsonValue = entry.get("data")
        if not isinstance(data, dict):
            return rejected("display data is not a mapping")
        if "text/plain" in data:
            text_plain = try_multistring(data["text/plain"])
            if text_plain is None:
                return rejected("text/plain is not a string or string list")
        else:
            text_plain = None
        if "image/png" in data:
            encoded_png = try_multistring(data["image/png"])
            if encoded_png is None:
                return rejected("image/png is not a string or string list")
            try:
                image_png = base64.b64decode(encoded_png, validate=True)
            except binascii.Error, ValueError:
                return rejected("image/png is not valid base64")
        else:
            image_png = None
        if output_type == "execute_result":
            return ExecuteResultOutput(
                raw=entry,
                text_plain=text_plain,
                image_png=image_png,
            )
        return DisplayDataOutput(
            raw=entry,
            text_plain=text_plain,
            image_png=image_png,
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
                RejectedNotebookPart(
                    raw=cell,
                    warning={
                        "type": "notebook_invalid_cell",
                        "message": "Notebook cell shown as raw JSON: cell is not a mapping.",
                    },
                )
            )
            continue
        cell_type: JsonValue = cell.get("cell_type")
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
        execution_count: int | None = None
        if cell_type == "code":
            output_values: JsonValue = cell.get("outputs")
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
    must produce exactly one pair. Sequence alignment supplies the order. It
    keeps added and removed cells where the notebook puts them, but alignment
    alone reports a moved cell as a deletion and an unrelated insertion, which
    would emit that cell's key twice. Bay keys are unique within one composed
    diff, so the two halves of a move are rejoined here into the single pair the
    key already says they are. Such a pair is reported as moved: alignment broke
    for it precisely because the cell changed position, and a move is a change
    the reviewer must be able to reach even when the cell's contents are equal.

    # Parameters

    - `left_cells`: Old notebook cells in document order.
    - `right_cells`: New notebook cells in document order.

    # Returns

    - `First in each pair`: The cell's public key, emitted once.
    - `Second in each pair`: Its old document index.
    - `Third in each pair`: Its new document index.
    - `Fourth in each pair`: Whether alignment identified a move.
    - `None`: The second or third item is absent when the cell exists only on
      the other side. At least one index is present in every pair.
    - `Order`: Pairs follow composed document order. A moved pair occupies its
      new-side position; one-sided pairs remain at their surviving position.
    """

    def entry_keys(entries: list[NotebookCellEntry]) -> list[str]:
        """Return one collision-free pairing key per entry in document order.

        Structured cells retain their validated or derived ID. Rejected values
        use a digest plus occurrence count, so identical malformed cells remain
        distinct while the two notebook sides can still align equal raw content.

        # Usage

        `_paired_cells` computes both side sequences with this helper before
        alignment. The returned order must remain the input document order.
        """
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
) -> Iterator[TextBay | ImageBay]:
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

    # Parameters

    - `left_bytes`: Captured old notebook bytes, or `None` when absent.
    - `right_bytes`: Captured new notebook bytes under the same convention.
    - `context`: File paths and labels copied or narrowed for yielded bays.

    # Usage

    `Composer.bays` calls this after both present paths classify as notebooks.
    Iterate the result in order; later composition groups adjacent bays by the
    frame key and heading supplied here.

    # Returns

    - `Yielded bays`: Changed notebook metadata and cell content represented as
      text bays with stable public bay and frame keys.
    - `Order`: Notebook metadata comes first, followed by each cell's source,
      metadata, and output bays under that cell's frame.

    # Failures

    Iteration raises `AssertionError` when both byte sides are absent. Invalid
    document boundaries, cells, and outputs become raw text or byte-facts bays
    with warnings rather than exceptions.
    """

    def canonical_json(value: JsonValue) -> str:
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
        show_document = left.document != right.document or (
            left.cells == [] and right.cells == []
        )
    else:
        show_document = present_side.document != {} or present_side.cells == []
    if show_document:
        left_document_text = (
            None if left is None else canonical_json(left.document)
        )
        right_document_text = (
            None if right is None else canonical_json(right.document)
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
                exists=left is not None,
                text=left_document_text,
                path_hint="notebook-metadata.json",
            ),
            right=DiffSide(
                exists=right is not None,
                text=right_document_text,
                path_hint="notebook-metadata.json",
            ),
        )

    def cell_heading(cell: NotebookCell) -> str | None:
        """Name one cell, or report that it has no name.

        A cell's only name is the Jupyter prompt shown to its user. An executed
        code cell is named `In [4]`, and one that never ran is `In [ ]`.
        Prose carries no prompt, so markdown and raw cells have no name at all.
        A caller with nothing to name says only what happened to the cell.

        # Returns

        - `str`: The code cell's Jupyter input prompt, including an empty prompt
          for a code cell that has never executed.
        - `None`: Markdown and raw cells have no prompt. Callers must describe
          the cell without adding a heading name.
        """
        if cell.cell_type != "code":
            return None
        count = cell.execution_count
        return "In [ ]" if count is None else f"In [{count}]"

    def source_hint(cell: NotebookCell) -> str:
        """Choose the renderer-only syntax path for a supported cell type.

        Code, markdown, and raw cells map to Python, Markdown, and plain-text
        suffixes respectively. The synthetic path selects highlighting only and
        never becomes the cell's public key or notebook path.
        """
        if cell.cell_type == "code":
            return "cell.py"
        if cell.cell_type == "markdown":
            return "cell.md"
        return "cell.txt"

    def rendered_text(out: NotebookOutputEntry | None) -> str | None:
        """Choose the text shown for one side of an output bay.

        This is the builder's representation choice: a stream shows its
        text. An error shows its traceback strings joined as lines while
        leaving escape codes uninterpreted. A bundle shows its
        `text/plain`. A `DiffSide` reserves `text=None` for a missing
        side, so an output that exists without a text representation
        renders as empty text here, at the widget boundary; the model
        keeps the two apart.

        # Usage

        `notebook_bays` calls this for paired output entries before deciding
        whether their text or raw JSON change requires a bay.

        # Returns

        - `str`: The output's text representation. A present rich output with
          no `text/plain` value returns `""` so it remains distinct from absence.
        - `None`: This side has no output entry. The caller must construct a
          missing `DiffSide`, not an empty present output.
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

    def rendered_png(out: NotebookOutputEntry | None) -> MediaSide | None:
        """Return one output side's selected PNG representation when present.

        Only display bundles can carry PNG data. A missing entry, a text output,
        and an absent output side all return `None`; the caller retains the
        output entry and distinguishes those cases when it assigns change.

        # Returns

        - `MediaSide`: Exact decoded PNG representation supplied by this output.
        - `None`: The output is absent, is not a display bundle, or supplies no
          PNG representation.
        """
        match out:
            case ExecuteResultOutput() | DisplayDataOutput() if (
                out.image_png is not None
            ):
                return MediaSide(media_type="image/png", data=out.image_png)
            case _:
                return None

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
        # an edit. Where the cell went says more than which line changed, and
        # the rows still show the edit when there is one.
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
            # An unchanged source still carries every readable row when opened.
            # It starts closed when only an output or metadata changed, leaving
            # the changed attachment prominent without discarding cell context.
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
            output_equal = left_out == right_out
            if output_equal and not isinstance(left_out, RejectedNotebookPart):
                continue
            left_png = rendered_png(left_out)
            right_png = rendered_png(right_out)
            rejected = isinstance(left_out, RejectedNotebookPart) or isinstance(
                right_out, RejectedNotebookPart
            )
            if not rejected and (left_png is not None or right_png is not None):
                image_identical = (
                    not output_equal
                    and left_png is not None
                    and right_png is not None
                    and left_png.data == right_png.data
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
                            if left_out is None
                            else "removed"
                            if right_out is None
                            else "unchanged"
                            if output_equal
                            else "changed"
                        )
                    ),
                    left=left_png,
                    right=right_png,
                )
                continue
            left_text = rendered_text(left_out)
            right_text = rendered_text(right_out)
            # This output differs, but its rendered text may not: a
            # re-rendered plot keeps the same `<Figure ...>` line while its
            # image bytes change entirely, and an image-only output renders no
            # text at all. The bay still has to say it changed, because a
            # change nothing can land on is a hidden change.
            text_identical = (
                not output_equal
                and left_out is not None
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
                # closed label to say before opening it.
                change=ChangeStatus(
                    kind=(
                        "added"
                        if left_out is None
                        else "removed"
                        if right_out is None
                        else "unchanged"
                        if output_equal
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
