"""Run Difftastic and validate the JSON facts dirdiff consumes.

## Public interface

`run_difftastic_json` compares two supplied strings and returns
`DifftasticJson`. `DifftasticTunings` exposes the subprocess limits used by
integration tests and production defaults. The remaining exported types spell
the validated JSON records.

## Purpose and boundaries

Difftastic requires file paths, so this module writes the supplied text to
temporary files and uses path hints only for their suffixes. It preserves the
external alignment and changed-span facts without building dirdiff rows.
`dirdiff.engines.difftastic.logic` interprets those facts against the original
text.
"""

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
"""Maximum Difftastic graph size used for one comparison.

This deliberately high value lets ordinary reviews use structural comparison.
Difftastic reports a graph-limit result when a comparison exceeds it.
"""
DFT_CONTEXT_LINES = "100000000"
"""Context limit that asks Difftastic to return whole-file alignment.

Row building requires every source line in order, rather than a patch with
unchanged gaps omitted. The subprocess receives this value through `--context`.
"""

__all__ = [
    "DFT_CONTEXT_LINES",
    "DFT_GRAPH_LIMIT",
    "DifftasticAlignedPairJson",
    "DifftasticJson",
    "DifftasticJsonChange",
    "DifftasticJsonChunkEntry",
    "DifftasticJsonFileStatus",
    "DifftasticJsonSide",
    "DifftasticJsonSideName",
    "DifftasticTunings",
    "run_difftastic_json",
]

type DifftasticAlignedPairJson = list[int | None]
"""Two zero-based line indexes in left/right order.

Row building reads index zero as the left line and index one as the right line.
Either is `None` when the row exists only on the other side.

This is raw JSON shape. Values must contain exactly two entries and use
zero-based indexes; they are not dirdiff row numbers.
"""


def _is_aligned_pair(value: object) -> TypeIs[DifftasticAlignedPairJson]:
    """Narrow an unknown JSON value to one two-sided line alignment.

    This is structural validation only. Source-bound checks happen later when
    row building has the original text.
    """
    if not isinstance(value, list):
        return False
    return len(value) == 2 and all(
        item is None or isinstance(item, int) for item in value
    )


type DifftasticJsonSideName = Literal["lhs", "rhs"]
"""Identify a changed-line side in Difftastic JSON.

- `lhs` is the old side.
- `rhs` is the new side.

Use these keys only while parsing Difftastic chunks. Dirdiff's public contracts
use left/right terminology instead.
"""
type DifftasticJsonFileStatus = Literal[
    "changed", "created", "deleted", "unchanged"
]
"""File-level status reported by Difftastic.

- `changed` has content changes on both sides.
- `created` and `deleted` are one-sided.
- `unchanged` reports no structural change.

Keep this as raw integration metadata. Dirdiff derives row status and summary
from projected rows rather than trusting this value as its result.
"""


class DifftasticJsonChange(TypedDict):
    """Describe one changed byte span inside a Difftastic source line.

    Raw JSON validation produces this shape; row building checks the offsets
    against the supplied source and uses optional `content` as a consistency
    check.

    Offsets are Difftastic byte columns, not Python character offsets or public
    review coordinates.
    """

    start: int
    """Inclusive UTF-8 byte column in the addressed source line.

    Row building must convert it at a character boundary before slicing Python
    text; values outside the supplied line are invalid engine data.
    """

    end: int
    """Exclusive UTF-8 byte column in the same source line.

    A value equal to `start` is a zero-width external fact and contributes no
    visible token. It may not exceed the source line's encoded length.
    """

    content: NotRequired[str]
    """Exact source text for this span when difftastic provides it.

    Row building uses this value when it is present. Sparse facts can omit it
    because the original source line is also supplied to the projector, which can
    recover the span text from `start` and `end`.
    """


def _is_change(value: object) -> TypeIs[DifftasticJsonChange]:
    """Narrow an unknown JSON value to a changed-span record.

    The check rejects absent offsets and non-string content. It leaves byte
    bounds and content agreement for comparison against the source line.
    """
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
    """Group the changed spans Difftastic reports for one source line.

    Chunk entries carry this value under `lhs` or `rhs`. Row building locates the
    source line by `line_number` and converts every changed span before building
    tokens.

    The value has no full line text, dirdiff line number, or row status.
    """

    line_number: int
    """Zero-based source line addressed by every span in this record.

    Validation accepts the integer shape; row building rejects negative or
    out-of-range values against the supplied document.
    """

    changes: list[DifftasticJsonChange]
    """Changed spans on this line.

    The Rust struct always serializes this sequence for a present side. It may be
    empty, but the key itself is not optional when `lhs` or `rhs` is present.
    """


def _is_side(value: object) -> TypeIs[DifftasticJsonSide]:
    """Narrow an unknown JSON value to one changed source-line side.

    Both the zero-based line number and the complete changed-span list are
    required. Source bounds remain unchecked until row building has the text.
    """
    if not isinstance(value, dict):
        return False

    line_number = value.get("line_number")
    changes = value.get("changes")
    return isinstance(line_number, int) and _is_changes(changes)


class DifftasticJsonChunkEntry(TypedDict):
    """Describe one Difftastic changed-line entry across both sides.

    Missing sides are omitted from JSON. This is not a complete replacement AST
    and does not carry unchanged source text.
    """

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


def _is_chunk_entry(value: object) -> TypeIs[DifftasticJsonChunkEntry]:
    """Narrow an unknown JSON value to an optional left/right chunk entry.

    Difftastic omits an absent side rather than serializing `null`. Each present
    side must satisfy the full changed-line record contract.
    """
    if not isinstance(value, dict):
        return False

    lhs = value.get("lhs")
    if lhs is not None and not _is_side(lhs):
        return False

    rhs = value.get("rhs")
    return rhs is None or _is_side(rhs)


class DifftasticJson(TypedDict):
    """Hold the validated Difftastic JSON facts used to build rows.

    `run_difftastic_json` returns this raw shape. `build_difftastic_ast` combines
    it with the original text to build complete dirdiff rows.

    Every field is optional because Difftastic omits empty values and dirdiff
    accepts its empty top-level result. This type is not a public engine result
    and must not be sent to the HUD.
    """

    aligned_lines: NotRequired[list[DifftasticAlignedPairJson]]
    """Complete old/new line alignment in Difftastic output order.

    Each pair contains zero-based left and right indexes with `None` for a
    one-sided row. Row building requires the sequence to cover each supplied
    source line exactly once and monotonically, except for Difftastic's
    trailing-newline phantom pair, which it validates and drops. The Rust
    serializer omits the field when no pairs exist; consumers treat omission as
    an empty alignment, not as an unknown partial result.
    """

    chunks: NotRequired[list[list[DifftasticJsonChunkEntry]]]
    """Changed-span entries grouped by Difftastic's external chunks.

    Row building walks chunks and entries in order to index each side's spans by
    zero-based source line. Repeated identical line facts are allowed because
    whole-file context may repeat them; contradictory repeats, invalid bounds,
    and overlapping spans fail instead of being merged. The serializer omits
    an empty vector, which consumers interpret as no reported inline novelty.
    """

    language: NotRequired[str]
    """Difftastic language or fallback label for the compared file.

    `build_difftastic_ast` only interprets known fallback labels for engine
    warnings; otherwise this is opaque difftastic metadata.
    """

    path: NotRequired[str]
    """Path string reported by difftastic for the compared file.

    Dirdiff does not trust this as the repository path; backend code already
    supplies the authoritative source paths. It is retained as raw difftastic
    metadata.
    """

    status: NotRequired[DifftasticJsonFileStatus]
    """File-level status reported by difftastic.

    The engine summary is computed from projected rows, so this status is raw
    difftastic metadata rather than the source of dirdiff line counts.
    """


def _is_difftastic_json(value: object) -> TypeIs[DifftasticJson]:
    """Validate the subset of Difftastic's top-level JSON that dirdiff reads.

    Unknown keys remain harmless integration metadata. Every known key must
    have the shape row building relies on.
    """
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
    """Execution settings passed to one Difftastic subprocess.

    Pass this immutable value to `run_difftastic_json` when tests or integration
    policy need non-default subprocess settings.

    These values control Difftastic resource and output behavior only. They do
    not select a parser, supply content, or change dirdiff's row contract.
    """

    graph_limit: str = DFT_GRAPH_LIMIT
    """Decimal limit supplied through Difftastic's `DFT_GRAPH_LIMIT` environment.

    Difftastic performs parsing and enforcement. Tests may lower it to exercise the
    explicit graph-limit warning without changing process-global state.
    """

    context_lines: str = DFT_CONTEXT_LINES
    """Decimal line count supplied to Difftastic's `--context` option.

    Production uses a value large enough to obtain complete alignment because
    row building does not fill source gaps omitted by the external result.
    """

    unstable: bool = True
    """Whether this subprocess receives `DFT_UNSTABLE=yes`.

    The JSON fields consumed by row building currently require that mode. Setting
    it false is an explicit integration test choice, not silent compatibility.
    """


def run_difftastic_json(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
    tunings: DifftasticTunings = DifftasticTunings(),
) -> DifftasticJson:
    """Compare two supplied texts with Difftastic and validate its JSON.

    The function creates temporary files because Difftastic accepts paths, but
    their contents come only from the supplied strings. Path hints contribute
    suffixes for parser selection and are never opened.

    # Parameters

    - `left_text`: Complete old-side text written to Difftastic's left input.
    - `right_text`: Complete new-side text written to Difftastic's right input.
    - `left_path_hint`: Optional old-side name used only for its suffix.
    - `right_path_hint`: Optional new-side name used only for its suffix. When
      absent, the left suffix keeps parser selection symmetric.
    - `tunings`: Subprocess limits and unstable-output choice for this run.

    # Usage

    `DifftasticDiffEngine` calls this for a two-sided text bay. Tests may pass
    explicit `DifftasticTunings`; ordinary callers should keep the production
    defaults so the JSON includes the complete alignment this parser expects.

    # Failures

    Raises `DirdiffError` when the executable is missing, Difftastic exits with
    an error, or its output is not valid supported JSON.
    """
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
        if _is_difftastic_json(first):
            return first
    if _is_difftastic_json(parsed):
        return parsed
    raise DirdiffError("Difftastic returned an unexpected JSON payload.")


def _is_aligned_lines(
    value: object,
) -> TypeIs[list[DifftasticAlignedPairJson]]:
    """Validate a JSON array of line-alignment pairs.

    This checks every member rather than trusting one representative pair;
    coverage and monotonicity remain source invariants checked while building rows.
    """
    if not isinstance(value, list):
        return False
    return all(_is_aligned_pair(pair) for pair in value)


def _is_chunks(
    value: object,
) -> TypeIs[list[list[DifftasticJsonChunkEntry]]]:
    """Validate Difftastic's outer array of changed chunks.

    Empty chunks are accepted because they are valid external structure. Every
    present entry must still satisfy the supported schema.
    """
    if not isinstance(value, list):
        return False
    return all(_is_chunk(chunk) for chunk in value)


def _is_chunk(value: object) -> TypeIs[list[DifftasticJsonChunkEntry]]:
    """Validate every changed-line entry in one Difftastic chunk.

    The helper performs shape narrowing only; duplicate or contradictory lines
    are rejected later while building the span index.
    """
    if not isinstance(value, list):
        return False
    return all(_is_chunk_entry(entry) for entry in value)


def _is_changes(value: object) -> TypeIs[list[DifftasticJsonChange]]:
    """Validate one source side's complete changed-span array.

    Byte ordering, overlap, and agreement with source content require the
    original line and therefore remain checks performed while building rows.
    """
    if not isinstance(value, list):
        return False
    return all(_is_change(change) for change in value)
