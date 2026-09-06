"""Run Diffsitter and validate the JSON facts dirdiff consumes.

## Public interface

`run_diffsitter_json` locates the `diffsitter` executable, runs it over one
supplied text pair, and returns the validated `DiffsitterJson` result.
`is_diffsitter_json` validates that raw shape on its own, and
`DiffsitterUnsupportedError` reports Diffsitter's refusal to parse the hinted
language.

## Purpose and boundaries

Temporary files and subprocess lifetime stop here. The driver checks the JSON
shape and field types only; source coordinates are validated by row building,
which has the original texts. The driver does not build engine rows, choose
textual degradation, configure Diffsitter's own fallback command, or retain
state between runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NotRequired, TypedDict, TypeIs

from dirdiff.engines.base import DirdiffError

__all__ = [
    "DiffsitterJson",
    "DiffsitterUnsupportedError",
    "is_diffsitter_json",
    "run_diffsitter_json",
]

DIFFSITTER_BIN_ENV = "DIRDIFF_DIFFSITTER_BIN"
"""Environment variable for an explicit Diffsitter executable path.

When set, the value is the only executable tried. A missing file is reported
as an error instead of silently continuing with `diffsitter` from `PATH`.
"""
UNSUPPORTED_LANGUAGE_STDERR = (
    "Error: Unsupported file type with no fallback command specified."
)
"""Exact stderr line Diffsitter prints when it cannot parse an input name.

Diffsitter exits unsuccessfully with this diagnosis when either temporary file
has no extension or an extension without a compiled grammar and no fallback
command is configured. Dirdiff never configures that command, so the line is
the complete evidence for `DiffsitterUnsupportedError`.
"""


class DiffsitterUnsupportedError(DirdiffError):
    """Diffsitter has no grammar for one of the hinted input names.

    The engine catches this diagnosis and renders locally built unified-diff
    rows with the `diffsitter_unsupported_language` warning. Process, output,
    and coordinate failures keep their ordinary error path.
    """


class DiffsitterJsonPoint(TypedDict):
    """One tree-sitter position echoed by Diffsitter.

    Row building addresses entry text through two of these values. The point
    is a raw Diffsitter coordinate, not a dirdiff review coordinate.
    """

    row: int
    """Zero-based source line counted by `\\n` terminators only.

    A carriage return stays inside its line. Row building checks the row against
    the `\\n`-split source before slicing.
    """

    column: int
    """Zero-based UTF-8 byte offset within `row`.

    Bytes, not characters, so multibyte graphemes occupy several columns. Row
    building converts the offset against the exact source line.
    """


def _is_diffsitter_point(value: object) -> TypeIs[DiffsitterJsonPoint]:
    """Narrow an unknown JSON value to one row/column point of two integers.

    Booleans are JSON values of another type and are rejected even though
    Python treats them as integers.
    """
    if not isinstance(value, dict):
        return False
    row = value.get("row")
    column = value.get("column")
    return (
        isinstance(row, int)
        and not isinstance(row, bool)
        and isinstance(column, int)
        and not isinstance(column, bool)
    )


class DiffsitterJsonEntry(TypedDict):
    """One changed grapheme reported by Diffsitter.

    Diffsitter splits every syntax leaf into graphemes and reports each
    unmatched grapheme as one entry. Row building turns each entry into exactly
    one inline token without merging neighbours or inferring pairs.
    """

    text: str
    """Exact grapheme text, which must equal the addressed source bytes.

    Row building rejects an entry whose text disagrees with the source slice
    between `start_position` and `end_position`.
    """

    start_position: DiffsitterJsonPoint
    """Inclusive start of the grapheme in Diffsitter's byte coordinates."""

    end_position: DiffsitterJsonPoint
    """Exclusive end of the grapheme, on the same row as the start."""

    kind_id: int
    """Numeric tree-sitter node kind of the leaf containing the grapheme.

    Dirdiff validates the type only. The value never selects token status or
    any other engine meaning.
    """


def _is_diffsitter_entry(value: object) -> TypeIs[DiffsitterJsonEntry]:
    """Validate the four fields of one reported grapheme entry.

    Text is any string; positions are points; the kind is an integer. Source
    agreement is left to row building.
    """
    if not isinstance(value, dict):
        return False
    kind_id = value.get("kind_id")
    return (
        isinstance(value.get("text"), str)
        and _is_diffsitter_point(value.get("start_position"))
        and _is_diffsitter_point(value.get("end_position"))
        and isinstance(kind_id, int)
        and not isinstance(kind_id, bool)
    )


class DiffsitterJsonLine(TypedDict):
    """The changed entries Diffsitter grouped on one source line.

    A hunk lists these in ascending line order. Row building attaches the
    entries to the physical row holding that line on the hunk's side.
    """

    line_index: int
    """Zero-based `\\n`-counted source line every entry in the line addresses.

    Row building requires each entry's rows to equal this value.
    """

    entries: list[DiffsitterJsonEntry]
    """Changed graphemes on the line in Diffsitter's reported order.

    Row building preserves this order and requires ascending, non-overlapping
    byte columns.
    """


def _is_diffsitter_line(value: object) -> TypeIs[DiffsitterJsonLine]:
    """Validate one line record and every entry inside it."""
    if not isinstance(value, dict):
        return False
    line_index = value.get("line_index")
    entries = value.get("entries")
    return (
        isinstance(line_index, int)
        and not isinstance(line_index, bool)
        and isinstance(entries, list)
        and all(_is_diffsitter_entry(entry) for entry in entries)
    )


class DiffsitterJsonHunk(TypedDict):
    """A group of consecutive changed lines from exactly one document.

    Diffsitter serializes its `Old`/`New` document enum as an object with the
    variant name as its single key. Validation requires exactly one of the two
    keys, so a hunk never carries lines from both sides.
    """

    Old: NotRequired[list[DiffsitterJsonLine]]
    """Lines of the old document, each of whose entries becomes a `delete` token."""

    New: NotRequired[list[DiffsitterJsonLine]]
    """Lines of the new document, each of whose entries becomes an `insert` token."""


def _is_diffsitter_hunk(value: object) -> TypeIs[DiffsitterJsonHunk]:
    """Validate a single-variant document hunk and every line inside it."""
    if not isinstance(value, dict) or len(value) != 1:
        return False
    for side in ("Old", "New"):
        lines = value.get(side)
        if lines is not None:
            return isinstance(lines, list) and all(
                _is_diffsitter_line(line) for line in lines
            )
    return False


class DiffsitterJsonDocument(TypedDict):
    """One input document as Diffsitter read it back.

    The text is the transport check that Diffsitter compared the supplied
    sources. The filename is metadata about the temporary transport file.
    """

    filename: str
    """Name of the temporary file Diffsitter read; never authoritative."""

    text: str
    """Complete document text Diffsitter compared.

    Row building requires it to equal dirdiff's supplied source exactly.
    """


def _is_diffsitter_document(value: object) -> TypeIs[DiffsitterJsonDocument]:
    """Validate the filename and text echoed for one document."""
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("filename"), str) and isinstance(
        value.get("text"), str
    )


class DiffsitterJson(TypedDict):
    """Validated shape of one `diffsitter --renderer json` result.

    `run_diffsitter_json` returns this value to `build_diffsitter_rows_from_json`,
    which validates its coordinates against the same texts. The type models the
    complete serialized result and is not a public engine result.
    """

    hunks: list[DiffsitterJsonHunk]
    """Changed-line groups in Diffsitter's reported order.

    Old and new hunks interleave as Diffsitter emitted them. An empty list
    means Diffsitter reported no edits.
    """

    old: DiffsitterJsonDocument
    """Old document as read back from the left temporary file."""

    new: DiffsitterJsonDocument
    """New document as read back from the right temporary file."""


def is_diffsitter_json(value: object) -> TypeIs[DiffsitterJson]:
    """Validate the complete Diffsitter JSON shape and field types.

    Every hunk, line, entry, and point is checked. Source agreement and
    coordinate bounds need the original texts and belong to row building.
    """
    if not isinstance(value, dict):
        return False
    hunks = value.get("hunks")
    return (
        isinstance(hunks, list)
        and all(_is_diffsitter_hunk(hunk) for hunk in hunks)
        and _is_diffsitter_document(value.get("old"))
        and _is_diffsitter_document(value.get("new"))
    )


def run_diffsitter_json(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> DiffsitterJson:
    """Run Diffsitter on one already-loaded text pair and validate its output.

    The executable named by `DIRDIFF_DIFFSITTER_BIN` is used when that variable
    is set; otherwise `diffsitter` is found on `PATH`. Both texts are written
    byte-exactly to temporary files whose basenames come from the path hints,
    so Diffsitter selects each side's grammar from the hinted extension. The
    temporary directory is removed before return.

    # Parameters

    - `left_text`: Complete old source written to the left temporary file.
    - `right_text`: Complete new source written to the right temporary file.
    - `left_path_hint`: Optional old name whose basename selects the grammar.
    - `right_path_hint`: Optional new name whose basename selects the grammar.

    # Usage

    `DiffsitterDiffEngine` calls this only when both sides are non-empty and
    differ. Mixed hints are passed through unchanged.

    # Failures

    Raises `DiffsitterUnsupportedError` when Diffsitter reports that it cannot
    parse one of the hinted names. Raises `DirdiffError` when the configured
    executable is missing, `diffsitter` is not on `PATH`, the process exits
    unsuccessfully for another reason, or the output is not valid
    `DiffsitterJson`.
    """
    configured = os.environ.get(DIFFSITTER_BIN_ENV)
    if configured is not None:
        diffsitter_bin = Path(configured).expanduser()
        if not diffsitter_bin.is_file():
            raise DirdiffError(
                f"Diffsitter executable from {DIFFSITTER_BIN_ENV} does not "
                f"exist: {diffsitter_bin}"
            )
    else:
        located = shutil.which("diffsitter")
        if located is None:
            raise DirdiffError(
                "Diffsitter engine requires `diffsitter` on PATH or "
                f"{DIFFSITTER_BIN_ENV} pointing to the Diffsitter executable."
            )
        diffsitter_bin = Path(located)

    with tempfile.TemporaryDirectory(prefix="dirdiff-diffsitter-") as raw_tmp:
        tmp = Path(raw_tmp)
        # Diffsitter reads only the extension, but keeping the whole hinted
        # basename passes the hint through unchanged.
        left_name = (
            "left"
            if left_path_hint is None
            else f"left-{Path(left_path_hint).name}"
        )
        right_name = (
            "right"
            if right_path_hint is None
            else f"right-{Path(right_path_hint).name}"
        )
        left_path = tmp / left_name
        right_path = tmp / right_name
        # newline="" keeps CRLF and lone CR intact so the echoed text matches.
        left_path.write_text(left_text, encoding="utf-8", newline="")
        right_path.write_text(right_text, encoding="utf-8", newline="")

        try:
            result = subprocess.run(
                [
                    str(diffsitter_bin),
                    "--no-config",
                    "--renderer",
                    "json",
                    str(left_path),
                    str(right_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise DirdiffError(
                f"Diffsitter executable does not exist: {diffsitter_bin}"
            ) from exc

    if result.returncode != 0:
        if any(
            line.strip() == UNSUPPORTED_LANGUAGE_STDERR
            for line in result.stderr.splitlines()
        ):
            raise DiffsitterUnsupportedError(
                "Diffsitter does not support this file type. "
                "Showing a unified text diff."
            )
        message = result.stderr.strip()
        if message == "":
            message = "Diffsitter could not build this diff."
        raise DirdiffError(message)

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DirdiffError("Diffsitter returned invalid JSON.") from exc
    if is_diffsitter_json(parsed):
        return parsed
    raise DirdiffError("Diffsitter returned an unexpected JSON payload.")
