"""Public diff-engine contract.

Diff engines implement one boundary: render an already-loaded left/right text
pair into a dirdiff result.  Backend loading, ref resolution, manifest
construction, lazy metadata, and format classification live outside
`dirdiff.engines`.

This module owns the public data transfer shapes at that boundary. Engines
produce strict `DiffEngineRow` values and `DiffEngineResult` values. Display
rendering enriches those neutral results through its own contracts.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, NotRequired, Protocol, TypedDict

__all__ = [
    "DiffEngineProtocol",
    "DiffEngineResult",
    "DiffEngineRow",
    "DiffSide",
    "DiffSummary",
    "DirdiffError",
    "EngineKind",
    "EngineWarning",
    "InlineToken",
    "InlineTokenStatus",
    "git_executable",
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

    left_text: str
    """
    Rendered old/left line text, or the empty string when the side is absent.
    """

    right_text: str
    """
    Rendered new/right line text, or the empty string when the side is absent.
    """

    left_tokens: NotRequired[list[InlineToken]]
    """
    Inline diff tokens for the old/left side.

    Absent or empty means the line has no token-level decoration on that side.
    """

    right_tokens: NotRequired[list[InlineToken]]
    """
    Inline diff tokens for the new/right side.

    Absent or empty means the line has no token-level decoration on that side.
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
        "tokendiff_region_limit",
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


EngineKind = Literal["dirdiff", "git", "difftastic", "gumtree", "tokendiff"]
"""Which diff engine a caller selects.

This is the complete set of engines dirdiff can render with, and the engines
package owns it because the package owns the implementations behind it. The
HTTP layer validates an incoming query value against this type, so code that
has one has already been given a kind that maps to a real engine.
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
        loading file contents.  The returned result is the rendered core of one
        composed bay; which bays a File has, and the HTTP envelope around them,
        are built by `dirdiff.formats` and the API layer around this call.
        """
        ...
