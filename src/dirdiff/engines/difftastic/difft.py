"""Raw difftastic execution and JSON contract.

This module is the only place in the difftastic service package that should
know how to invoke the external `difft` executable. It owns the contract between
our Python code and `difft --display json`.

The important boundary: this module parses difftastic JSON as-is and does no
post-processing. It does not normalize side names, infer replacements, rebuild
ASTs, recover line fragments, syntax-highlight source text, create dirdiff rows,
or assemble frontend payloads. Difftastic JSON is deliberately tiny; downstream
code must take this compact alignment/change-span data and decide how to render
it.

Input contract
--------------
`run_difftastic_json` takes two complete text documents:

* `left_text`: the old/left document contents.
* `right_text`: the new/right document contents.

The optional `left_path_hint` and `right_path_hint` values are used only to give
difftastic filenames with useful suffixes. They are not opened. The text
arguments are always written to fresh temporary files, because difftastic
chooses parsers from paths and reads files from disk.

`DifftasticTunings` contains execution-level knobs:

* `graph_limit` becomes the `DFT_GRAPH_LIMIT` environment variable.
* `context_lines` is passed to `--context`.
* `unstable=True` sets `DFT_UNSTABLE=yes`.

These tunings must stay on this side of the package boundary. Downstream logic
should consume the returned JSON shape, not know which environment variables or
CLI flags were used to obtain it.

Output contract
---------------
`run_difftastic_json` returns `DifftasticJson`, a TypedDict describing the subset
of difftastic JSON currently consumed by dirdiff:

* `aligned_lines`: pairs of zero-based line indices, where either side may be
  `None` for one-sided rows.
* `chunks`: nested change entries keyed by difftastic side names (`lhs` and
  `rhs`), with per-line changed ranges. These are the only real line-level
  actions difftastic exposes here: `lhs` entries describe old-side/deleted spans,
  `rhs` entries describe new-side/inserted spans, and an entry containing both
  sides means difftastic paired those spans syntactically. Missing sides are
  omitted by serde, not serialized as `null`. It is still not a rich before/after
  AST.
* `language`: a free-form language/fallback label from difftastic.
* `path`: the path difftastic reports for the compared file.
* `status`: the file-level action (`changed`, `created`, `deleted`, or
  `unchanged`).

The return value is intentionally still difftastic-shaped. If a consumer needs a
row model, token pairing, fallback warnings, or frontend-friendly fields, that
work belongs after this module.

Validation constraints
----------------------
The external JSON is only validated at the top-level container boundary. A
non-empty list returns its first object, an object returns as-is, and an empty
list becomes a synthetic empty diff payload with `aligned_lines` and `chunks`.
That synthetic empty payload is not a serde `File`; real difftastic file objects
always include `language`, `path`, and `status`. Nested validation is left to the
consumer because difftastic output is an external format that may grow fields we
do not care about.

Failure contract
----------------
This module raises `TextDiffError` when difftastic cannot be executed, exits
non-zero, returns invalid JSON, or returns a top-level JSON shape that cannot be
treated as one difftastic file diff.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, Required, TypedDict, cast

from dirdiff.backend import TextDiffError

DFT_GRAPH_LIMIT = "10000000"
DFT_CONTEXT_LINES = "100000000"

__all__ = [
    "DFT_CONTEXT_LINES",
    "DFT_GRAPH_LIMIT",
    "DifftasticAlignedPairJson",
    "DifftasticJson",
    "DifftasticJsonChange",
    "DifftasticJsonChunkEntry",
    "DifftasticJsonFileStatus",
    "DifftasticJsonHighlight",
    "DifftasticJsonSide",
    "DifftasticJsonSideName",
    "DifftasticTunings",
    "run_difftastic_json",
]

type DifftasticAlignedPairJson = list[int | None]
type DifftasticJsonSideName = Literal["lhs", "rhs"]
type DifftasticJsonFileStatus = Literal[
    "changed", "created", "deleted", "unchanged"
]
type DifftasticJsonHighlight = Literal[
    "delimiter",
    "normal",
    "string",
    "type",
    "comment",
    "keyword",
    "tree_sitter_error",
]


class DifftasticJsonChange(TypedDict):
    """Changed span inside one line on one side of the diff."""

    start: int
    """Start column for the changed span, as emitted by difftastic."""

    end: int
    """End column for the changed span, as emitted by difftastic."""

    content: str
    """Exact source text in `start:end` for this change span."""

    highlight: DifftasticJsonHighlight
    """Difftastic's syntax class for the span, not the line action.

    This is serialized from difftastic's `Highlight` enum with
    `rename_all = "snake_case"`.
    """


class DifftasticJsonSide(TypedDict):
    """Zero-based line number plus changed spans for one difftastic side."""

    line_number: int
    """Zero-based line number for this side."""

    changes: list[DifftasticJsonChange]
    """Changed spans on this line.

    The Rust struct always serializes this list for a present side. It may be
    empty, but the key itself is not optional when `lhs` or `rhs` is present.
    """


class DifftasticJsonChunkEntry(TypedDict):
    """One changed line entry."""

    lhs: NotRequired[DifftasticJsonSide]
    """Old-side changed spans.

    Presence of this field means difftastic reported deleted/left-hand content
    for the line entry. In difftastic's serde struct this is `Option<Side>` with
    `skip_serializing_if = "Option::is_none"`, so missing sides are omitted from
    JSON rather than emitted as `null`.
    """

    rhs: NotRequired[DifftasticJsonSide]
    """New-side changed spans.

    Presence of this field means difftastic reported inserted/right-hand content
    for the line entry. If both `lhs` and `rhs` are present, difftastic paired
    those spans syntactically; it is still not returning a rich replacement AST.
    As with `lhs`, absent sides are omitted rather than serialized as `null`.
    """


class DifftasticJson(TypedDict):
    """Subset of `difft --display json` consumed by this package."""

    aligned_lines: NotRequired[list[DifftasticAlignedPairJson]]
    """Line alignment pairs.

    The Rust serializer omits this field when the vector is empty.
    """

    chunks: NotRequired[list[list[DifftasticJsonChunkEntry]]]
    """Changed line chunks.

    The Rust serializer omits this field when the vector is empty.
    """

    language: Required[str]
    path: Required[str]
    status: Required[DifftasticJsonFileStatus]


@dataclass(frozen=True)
class DifftasticTunings:
    graph_limit: str = DFT_GRAPH_LIMIT
    context_lines: str = DFT_CONTEXT_LINES
    unstable: bool = True


def run_difftastic_json(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
    tunings: DifftasticTunings = DifftasticTunings(),
) -> DifftasticJson:
    left_suffix = Path(left_path_hint or "left.txt").suffix or ".txt"
    right_suffix = Path(right_path_hint or left_path_hint or "right.txt").suffix
    right_suffix = right_suffix or left_suffix

    with tempfile.TemporaryDirectory(prefix="dirdiff-difftastic-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / f"left{left_suffix}"
        right_path = tmp / f"right{right_suffix}"
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")

        env = {
            **os.environ,
            "DFT_GRAPH_LIMIT": tunings.graph_limit,
        }
        if tunings.unstable:
            env["DFT_UNSTABLE"] = "yes"

        try:
            result = subprocess.run(
                [
                    "difft",
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
            raise TextDiffError(
                "Difftastic engine requires the `difft` executable on PATH."
            ) from exc

    if result.returncode != 0:
        raise TextDiffError(
            result.stderr.strip() or "Difftastic could not build this diff."
        )
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TextDiffError("Difftastic returned invalid JSON.") from exc

    if isinstance(parsed, list):
        if not parsed:
            return cast("DifftasticJson", {"aligned_lines": [], "chunks": []})
        first = parsed[0]
        if isinstance(first, dict):
            return cast("DifftasticJson", first)
    if isinstance(parsed, dict):
        return cast("DifftasticJson", parsed)
    raise TextDiffError("Difftastic returned an unexpected JSON payload.")
