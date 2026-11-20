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
# Printed when an input has no extension or no compiled grammar and no
# fallback command is configured; dirdiff never configures one.
UNSUPPORTED_LANGUAGE_STDERR = (
    "Error: Unsupported file type with no fallback command specified."
)


class DiffsitterUnsupportedError(DirdiffError): ...


class DiffsitterJsonPoint(TypedDict):
    # Zero-based, counting only `\n`; a `\r` stays inside its line.
    row: int

    # Zero-based UTF-8 byte offset within the row, not a character index.
    column: int


def _is_diffsitter_point(value: object) -> TypeIs[DiffsitterJsonPoint]:
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
    text: str

    start_position: DiffsitterJsonPoint

    end_position: DiffsitterJsonPoint

    kind_id: int


def _is_diffsitter_entry(value: object) -> TypeIs[DiffsitterJsonEntry]:
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
    line_index: int

    entries: list[DiffsitterJsonEntry]


def _is_diffsitter_line(value: object) -> TypeIs[DiffsitterJsonLine]:
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
    Old: NotRequired[list[DiffsitterJsonLine]]

    New: NotRequired[list[DiffsitterJsonLine]]


def _is_diffsitter_hunk(value: object) -> TypeIs[DiffsitterJsonHunk]:
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
    filename: str

    text: str


def _is_diffsitter_document(value: object) -> TypeIs[DiffsitterJsonDocument]:
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("filename"), str) and isinstance(
        value.get("text"), str
    )


class DiffsitterJson(TypedDict):
    hunks: list[DiffsitterJsonHunk]

    old: DiffsitterJsonDocument

    new: DiffsitterJsonDocument


def is_diffsitter_json(value: object) -> TypeIs[DiffsitterJson]:
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
