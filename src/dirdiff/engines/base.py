"""Public diff-engine contract.

Diff engines implement one boundary: render an already-loaded left/right text
pair into a dirdiff result.  Backend loading, ref resolution, manifest
construction, lazy metadata, and notebook routing live outside
`dirdiff.engines`.

This module owns the public data transfer shapes at that boundary.  Engines
produce strict `DiffEngineRow` values; display rendering enriches those into
`DiffRow` values; the server validates and serializes the same shapes for
HTTP.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict, TypeIs

__all__ = [
    "DiffEngineProtocol",
    "DiffEngineResult",
    "DiffEngineRow",
    "DiffRow",
    "DiffSide",
    "DiffSummary",
    "EngineWarning",
    "FoldHint",
    "InlineToken",
    "SyntaxSpan",
    "strict_engine_rows",
]

type InlineTokenStatus = Literal[
    "unchanged",
    "replace",
    "insert",
    "delete",
    "move",
]

type DiffEngineRowStatus = Literal[
    "equal",
    "replace",
    "insert",
    "delete",
    "move",
]


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


class SyntaxSpan(TypedDict):
    """Highlighted token span for one rendered line."""

    start: int
    """
    Start offset within the rendered line text.
    """

    end: int
    """
    End offset within the rendered line text.
    """

    classes: list[str]
    """
    CSS-ish syntax classes consumed by the frontend renderer.
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


class FoldHint(TypedDict):
    """Foldable source region discovered while rendering a file diff.

    Fold hints are optional metadata for the frontend.  They do not change row
    alignment.
    """

    start_row: int
    """
    First rendered row covered by the fold hint.
    """

    end_row: int
    """
    One-past-the-last rendered row covered by the fold hint.
    """

    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    """
    Source-region category used by the frontend folding policy.
    """

    label: str
    """
    Human-readable fold label derived from the source region.
    """


class DiffEngineRow(TypedDict):
    """One aligned diff row returned by a diff engine.

    Engine rows describe only the semantic comparison between two already
    loaded text sides.  Display-only transport fields are added after engine
    rendering by the API/display layer.
    """

    status: DiffEngineRowStatus
    """
    Line-level row status produced by the engine.

    This is limited to real aligned diff rows.  Synthetic UI statuses such as
    `fold` and `elided` are not legal engine output.
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


class DiffRow(TypedDict):
    """One row in the rendered text diff grid.

    This is the display/API row shape after engine rows have been enriched for
    syntax highlighting, folding, and plain-render degradation.
    """

    status: Literal[
        "equal",
        "replace",
        "insert",
        "delete",
        "move",
        "fold",
        "elided",
    ]
    """
    Display row status.

    TODO: part of fold micro-optimisation, investigate for removal.

    `fold` is a client-expandable placeholder for hidden rows that are still
    included in `foldedRows`.  `elided` is a non-expandable placeholder for
    rows omitted from a large plain render.  These synthetic row statuses should
    probably be removed from the core diff row shape.
    """

    left_no: NotRequired[int | None]
    """
    One-based old/left line number, when this row has a left side.
    """

    right_no: NotRequired[int | None]
    """
    One-based new/right line number, when this row has a right side.
    """

    left_text: NotRequired[str | None]
    """
    Rendered old/left line text.
    """

    right_text: NotRequired[str | None]
    """
    Rendered new/right line text.
    """

    left_tokens: NotRequired[list[InlineToken]]
    """
    Inline diff tokens for the old/left side.

    TODO: token spans and syntax spans are parallel decorations today.  We
    should probably unify them into one server-side decorated text model
    before the frontend has to merge overlapping ranges itself.
    """

    right_tokens: NotRequired[list[InlineToken]]
    """
    Inline diff tokens for the new/right side.
    """

    left_syntax: NotRequired[list[SyntaxSpan]]
    """
    Syntax-highlight spans for the old/left line.

    See `left_tokens` for the TODO about unifying token and syntax
    decorations before frontend rendering.
    """

    right_syntax: NotRequired[list[SyntaxSpan]]
    """
    Syntax-highlight spans for the new/right line.
    """

    count: NotRequired[int]
    """
    Number of hidden rows represented by a synthetic fold/elided row.
    """

    foldedRows: NotRequired[list[DiffRow]]
    """
    Hidden rows kept in the payload for fold mode.
    """

    label: NotRequired[str]
    """
    Label displayed during fold/elided rendering.
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
