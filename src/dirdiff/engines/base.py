"""Public diff-engine contract.

Diff engines implement one boundary: render an already-loaded left/right text
pair into a dirdiff result.  Backend loading, ref resolution, manifest
construction, lazy metadata, and notebook routing live outside
`dirdiff.engines`.

This module owns the public data transfer shapes at that boundary. Engines
produce strict `DiffEngineRow` values and `DiffEngineResult` values. Display
rendering enriches those neutral results through its own contracts.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, NotRequired, Protocol, TypedDict, TypeIs

__all__ = [
    "DiffEngineProtocol",
    "DiffEngineResult",
    "DiffEngineRow",
    "DiffSide",
    "DiffSummary",
    "DirdiffError",
    "EngineWarning",
    "InlineToken",
    "InlineTokenStatus",
    "engine_row_has_change",
    "git_executable",
    "strict_engine_rows",
]


class DirdiffError(ValueError):
    """Raised when dirdiff cannot safely produce the requested result."""


type InlineTokenStatus = Literal[
    "unchanged",
    "replace",
    "insert",
    "delete",
    "move",
]
"""
Token-level change classification emitted by diff engines.

The value describes an exact text slice and does not represent row-level
alignment or display styling.
"""

type DiffEngineRowStatus = Literal[
    "equal",
    "replace",
    "insert",
    "delete",
    "move",
]


@cache
def git_executable() -> str:
    """Resolve the concrete Git binary every dirdiff subprocess spawns.

    On macOS the bare name "git" resolves through PATH to Apple's xcrun shim,
    which adds ~14ms of pure indirection to every spawn. Asking xcrun once for
    the real binary removes that per-spawn tax; the resolution is cached for
    the process lifetime. Everywhere else, and when xcrun is unavailable, the
    bare name is the correct spawn target unchanged. The Git diff engine and
    the Git-backed workspace backends all spawn through this resolver; it
    lives here because engines are the lowest layer that runs Git.
    """
    if sys.platform != "darwin":
        return "git"
    located = subprocess.run(
        ["xcrun", "--find", "git"],
        check=False,
        capture_output=True,
        text=True,
    )
    resolved = located.stdout.strip()
    if located.returncode == 0 and resolved != "" and Path(resolved).is_file():
        return resolved
    return "git"


@dataclass(frozen=True)
class DiffSide:
    """One already-loaded side passed into a diff engine.

    A side is the engine-facing input after backend loading has resolved refs
    and loaded bytes as text.  Human-facing labels such as `HEAD`, `new`,
    or branch names are intentionally absent here because they describe how the
    API presents the side, not what the engine compares.
    """

    exists: bool
    """
    Tells the engine whether this side exists.

    Missing sides carry `text=None`.  Added/deleted file handling is still an
    engine concern, but fetching the contents or deciding that a side is absent
    belongs to `dirdiff.backend` and server orchestration.
    """

    text: str | None
    """
    Source text for this side.

    Engines compare this string.  They should not treat `path_hint` as an
    alternate source of file contents.
    """

    path_hint: str | None = None
    """
    Optional path metadata for parser and temporary-file selection.

    Difftastic and GumTree need file-like names to pick a language parser, and
    display rendering uses the hint for syntax/fold detection.  This is not
    permission for an engine to read from that path.
    """


class DiffSummary(TypedDict):
    """Line-count summary produced by diff engines.

    Counts describe engine row categories, not repository status.  Side
    existence is loader/API metadata and is attached after rendering.
    """

    changed_lines: int
    """
    Total changed rows represented by the summary.
    """

    modified_lines: int
    """
    Number of paired rows whose line-level status is modified.
    """

    added_lines: int
    """
    Number of right-only inserted rows.
    """

    removed_lines: int
    """
    Number of left-only deleted rows.
    """

    moved_lines: int
    """
    Number of line-level moved rows.

    Ordinary text and Git-style engines normally report this as zero.  GumTree
    uses move primarily at token level, so this may also stay zero there.
    """


class InlineToken(TypedDict):
    """Inline token emitted for a rendered diff row.

    Tokens preserve source slices and carry token-level diff status.
    """

    text: str
    """
    Original text slice for this token.
    """

    is_ws: bool
    """
    Whether this token is whitespace.
    """

    status: InlineTokenStatus
    """
    Token-level diff status.

    GumTree uses `move` for moved ranges.  Non-structural renderers normally
    emit `unchanged`, `replace`, `insert`, or `delete`.
    """


class DiffEngineRow(TypedDict):
    """One aligned diff row returned by a diff engine.

    Engine rows describe only the aligned text comparison between two already
    loaded sides.  Display-only transport fields are added after engine
    rendering by the API/display layer.
    """

    status: DiffEngineRowStatus
    """
    Line-level row status produced by the engine.

    This is limited to real aligned diff rows. Frontend fold placeholders are
    derived later from fold hints and are not legal engine output.
    """

    left_no: int | None
    """
    One-based line number on the old/left side, or `None` for right-only rows.
    """

    right_no: int | None
    """
    One-based line number on the new/right side, or `None` for left-only rows.
    """

    left_text: str | None
    """
    Rendered old/left line text, or `None` when the side is absent.
    """

    right_text: str | None
    """
    Rendered new/right line text, or `None` when the side is absent.
    """

    left_tokens: list[InlineToken]
    """
    Inline diff tokens for the old/left side.

    Empty means the line has no token-level decoration on that side.
    """

    right_tokens: list[InlineToken]
    """
    Inline diff tokens for the new/right side.

    Empty means the line has no token-level decoration on that side.
    """


class EngineWarning(TypedDict):
    """Honest renderer warning attached to an otherwise renderable diff.

    Warnings are for boundary failures where dirdiff can still return a useful
    fallback payload.  For example, GumTree producing invalid JSON is not hidden
    as a normal diff: the engine falls back to textual rows and attaches a
    warning so the UI can tell the user what happened.
    """

    type: Literal[
        "difftastic_graph_limit",
        "difftastic_empty_rows",
        "gumtree_invalid_json",
    ]
    """
    Stable warning discriminator consumed by the API/frontend.
    """

    message: str
    """
    Human-readable explanation of the fallback or degraded engine result.
    """


class DiffEngineResult(TypedDict):
    """Rendered text-diff data returned by every diff engine.

    The result deliberately stops before HTTP/API and display metadata.
    """

    summary: DiffSummary
    """
    Count summary for the engine rows.
    """

    rows: list[DiffEngineRow]
    """
    Strict engine rows before syntax highlighting, folds, or elision.
    """

    engine_warning: NotRequired[EngineWarning]
    """
    Optional honest warning when the engine returned a degraded fallback.
    """


class DiffEngineProtocol(Protocol):
    """Contract implemented by diff engines.

    The important word here is "render".  A diff engine does not own refs,
    branch-review semantics, preset catalog traversal, lazy file discovery, or
    notebook routing.  Those are request/input concerns.  By the time a caller
    reaches this protocol, it has already loaded the two sides and decided that
    they should be treated as ordinary text for this engine.

    This boundary is what keeps GumTree, difftastic, git-style rendering, and
    native text rendering comparable: each receives the same logical inputs and
    returns the same dirdiff rendered result shape.  Engines may use path hints
    for language detection or temporary file names, but the text arguments are
    the content source of truth.
    """

    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render an already-loaded left/right pair.

        The caller supplies two `DiffSide` values after resolving refs and
        loading file contents.  The returned result is the rendered core of the
        normal text-file branch of `/api/file-diff`; notebook payloads and
        HTTP metadata are built at the API layer before or after an engine is
        selected.
        """
        ...


def engine_row_has_change(row: Mapping[str, object]) -> bool:
    """Return the diff engine's canonical change classification for one row.

    Line status and inline-token status are the complete engine contract for
    this decision. Rendering consumers use this operation for summaries, hunk
    identity, and fold eligibility; they must not compare text independently
    or discard rows before classifying them.
    """

    status = row.get("status")
    if not _is_diff_engine_row_status(status):
        raise TypeError(f"Invalid engine row status: {status!r}")
    if status != "equal":
        return True

    for field in ("left_tokens", "right_tokens"):
        tokens = row.get(field, [])
        if not _is_inline_token_list(tokens):
            raise TypeError(f"Engine row {field} must be inline tokens.")
        if any(token["status"] != "unchanged" for token in tokens):
            return True
    return False


def strict_engine_rows(
    rows: Iterable[object],
) -> list[DiffEngineRow]:
    """Materialize public engine rows from renderer-local row mappings.

    Native renderers build rows incrementally as dictionaries because their
    projection logic is easier to express with ordinary mapping operations.
    Difftastic's internal row AST also treats absent token lists as “no inline
    token decorations.” This function is the single adapter into the public
    `DiffEngineRow` contract: required line fields and present token fields are
    validated, and absent token fields become empty token lists in the public
    engine payload.
    """

    materialized: list[DiffEngineRow] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("Engine row must be a mapping.")

        required_keys = {
            "status",
            "left_no",
            "right_no",
            "left_text",
            "right_text",
        }
        missing_keys = required_keys - row.keys()
        if missing_keys != set():
            missing_text = ", ".join(sorted(missing_keys))
            raise TypeError(f"Engine row is missing fields: {missing_text}.")

        status = row["status"]
        if not _is_diff_engine_row_status(status):
            raise TypeError(f"Invalid engine row status: {status!r}")

        left_no = row["left_no"]
        if not _is_optional_int(left_no):
            raise TypeError("Engine row left_no must be int or None.")
        right_no = row["right_no"]
        if not _is_optional_int(right_no):
            raise TypeError("Engine row right_no must be int or None.")

        left_text = row["left_text"]
        if not _is_optional_str(left_text):
            raise TypeError("Engine row left_text must be str or None.")
        right_text = row["right_text"]
        if not _is_optional_str(right_text):
            raise TypeError("Engine row right_text must be str or None.")

        left_tokens = row.get("left_tokens", [])
        if not _is_inline_token_list(left_tokens):
            raise TypeError("Engine row left_tokens must be inline tokens.")
        right_tokens = row.get("right_tokens", [])
        if not _is_inline_token_list(right_tokens):
            raise TypeError("Engine row right_tokens must be inline tokens.")

        materialized.append(
            {
                "status": status,
                "left_no": left_no,
                "right_no": right_no,
                "left_text": left_text,
                "right_text": right_text,
                "left_tokens": left_tokens,
                "right_tokens": right_tokens,
            }
        )
    return materialized


def _is_diff_engine_row_status(value: object) -> TypeIs[DiffEngineRowStatus]:
    return value in {"equal", "replace", "insert", "delete", "move"}


def _is_inline_token_status(value: object) -> TypeIs[InlineTokenStatus]:
    return value in {"unchanged", "replace", "insert", "delete", "move"}


def _is_optional_int(value: object) -> TypeIs[int | None]:
    return value is None or isinstance(value, int)


def _is_optional_str(value: object) -> TypeIs[str | None]:
    return value is None or isinstance(value, str)


def _is_inline_token_list(value: object) -> TypeIs[list[InlineToken]]:
    if not isinstance(value, list):
        return False
    return all(_is_inline_token(token) for token in value)


def _is_inline_token(value: object) -> TypeIs[InlineToken]:
    if not isinstance(value, dict):
        return False
    text = value.get("text")
    is_ws = value.get("is_ws")
    status = value.get("status")
    return (
        isinstance(text, str)
        and isinstance(is_ws, bool)
        and _is_inline_token_status(status)
    )
