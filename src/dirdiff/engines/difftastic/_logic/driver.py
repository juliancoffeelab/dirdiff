from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, TypeIs

from dirdiff.engines.base import DirdiffError

DFT_GRAPH_LIMIT = "10000000"
# Whole-file context: row building needs every line in the alignment.
DFT_CONTEXT_LINES = "100000000"

__all__ = [
    "DifftasticJson",
    "DifftasticJsonSide",
    "is_difftastic_json",
    "run_difftastic_json",
]

type DifftasticAlignedPairJson = list[int | None]


def _is_aligned_pair(value: object) -> TypeIs[DifftasticAlignedPairJson]:
    if not isinstance(value, list):
        return False
    return len(value) == 2 and all(
        item is None or isinstance(item, int) for item in value
    )


type DifftasticJsonSideName = Literal["lhs", "rhs"]
type DifftasticJsonFileStatus = Literal[
    "changed", "created", "deleted", "unchanged"
]


class DifftasticJsonChange(TypedDict):
    # UTF-8 byte columns in the source line, not character offsets.
    start: int

    end: int

    content: NotRequired[str]


def _is_change(value: object) -> TypeIs[DifftasticJsonChange]:
    if not isinstance(value, dict):
        return False

    start = value.get("start")
    end = value.get("end")
    content = value.get("content")
    return (
        isinstance(start, int)
        and isinstance(end, int)
        and (content is None or isinstance(content, str))
    )


class DifftasticJsonSide(TypedDict):
    # Zero-based.
    line_number: int

    changes: list[DifftasticJsonChange]


def _is_side(value: object) -> TypeIs[DifftasticJsonSide]:
    if not isinstance(value, dict):
        return False

    line_number = value.get("line_number")
    changes = value.get("changes")
    return isinstance(line_number, int) and _is_changes(changes)


class DifftasticJsonChunkEntry(TypedDict):
    lhs: NotRequired[DifftasticJsonSide]

    rhs: NotRequired[DifftasticJsonSide]


def _is_chunk_entry(value: object) -> TypeIs[DifftasticJsonChunkEntry]:
    if not isinstance(value, dict):
        return False

    lhs = value.get("lhs")
    if lhs is not None and not _is_side(lhs):
        return False

    rhs = value.get("rhs")
    return rhs is None or _is_side(rhs)


class DifftasticJson(TypedDict):
    aligned_lines: NotRequired[list[DifftasticAlignedPairJson]]

    chunks: NotRequired[list[list[DifftasticJsonChunkEntry]]]

    language: NotRequired[str]

    path: NotRequired[str]

    status: NotRequired[DifftasticJsonFileStatus]


def is_difftastic_json(value: object) -> TypeIs[DifftasticJson]:
    if not isinstance(value, dict):
        return False

    aligned_lines = value.get("aligned_lines")
    if aligned_lines is not None and not _is_aligned_lines(aligned_lines):
        return False

    chunks = value.get("chunks")
    if chunks is not None and not _is_chunks(chunks):
        return False

    language = value.get("language")
    if language is not None and not isinstance(language, str):
        return False

    path = value.get("path")
    if path is not None and not isinstance(path, str):
        return False

    status = value.get("status")
    return status is None or status in {
        "changed",
        "created",
        "deleted",
        "unchanged",
    }


@dataclass(frozen=True)
class DifftasticTunings:
    graph_limit: str = DFT_GRAPH_LIMIT

    context_lines: str = DFT_CONTEXT_LINES

    unstable: bool = True


def run_difftastic_json(
    *,
    ignore_comments: bool,
    left_text: str,
    right_text: str,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
    tunings: DifftasticTunings = DifftasticTunings(),
) -> DifftasticJson:
    left_suffix = (
        ".txt" if left_path_hint is None else Path(left_path_hint).suffix
    )
    right_suffix = (
        left_suffix if right_path_hint is None else Path(right_path_hint).suffix
    )

    with tempfile.TemporaryDirectory(prefix="dirdiff-difftastic-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / f"left{left_suffix}"
        right_path = tmp / f"right{right_suffix}"
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")

        env = {
            **os.environ,
            "DFT_GRAPH_LIMIT": tunings.graph_limit,
            # The explicit CLI option controls this preference, not the shell.
            "DFT_IGNORE_COMMENTS": "false",
        }
        # The JSON output is only available in unstable mode.
        if tunings.unstable:
            env["DFT_UNSTABLE"] = "yes"

        try:
            result = subprocess.run(
                [
                    "difft",
                    *(["--ignore-comments"] if ignore_comments else []),
                    "--display",
                    "json",
                    "--context",
                    tunings.context_lines,
                    str(left_path),
                    str(right_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise DirdiffError(
                "Difftastic engine requires the `difft` executable on PATH."
            ) from exc

    if result.returncode != 0:
        message = result.stderr.strip()
        if message == "":
            message = "Difftastic could not build this diff."
        raise DirdiffError(message)
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DirdiffError("Difftastic returned invalid JSON.") from exc

    if isinstance(parsed, list):
        if parsed == []:
            return {"aligned_lines": [], "chunks": []}
        first = parsed[0]
        if is_difftastic_json(first):
            return first
    if is_difftastic_json(parsed):
        return parsed
    raise DirdiffError("Difftastic returned an unexpected JSON payload.")


def _is_aligned_lines(
    value: object,
) -> TypeIs[list[DifftasticAlignedPairJson]]:
    if not isinstance(value, list):
        return False
    return all(_is_aligned_pair(pair) for pair in value)


def _is_chunks(
    value: object,
) -> TypeIs[list[list[DifftasticJsonChunkEntry]]]:
    if not isinstance(value, list):
        return False
    return all(_is_chunk(chunk) for chunk in value)


def _is_chunk(value: object) -> TypeIs[list[DifftasticJsonChunkEntry]]:
    if not isinstance(value, list):
        return False
    return all(_is_chunk_entry(entry) for entry in value)


def _is_changes(value: object) -> TypeIs[list[DifftasticJsonChange]]:
    if not isinstance(value, list):
        return False
    return all(_is_change(change) for change in value)
