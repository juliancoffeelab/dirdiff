"""Contracts shared by every diff engine.

## Public interface

`DiffEngineProtocol` compares an already-loaded pair of `DiffSide` values and
returns `DiffEngineResult`. The module also defines the row, token, summary,
warning, engine-name, and failure types used at that boundary.

## Purpose and boundaries

Every engine receives the same text-side contract and produces neutral rows,
so composition can select an implementation without changing its rendering
flow. Engines may use path hints to select a parser or temporary-file suffix,
but the supplied text remains authoritative. File loading and format
classification happen before this boundary; display decoration happens after
it.
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
    """Report an expected inability to produce a valid dirdiff result.

    Raise this when valid input cannot produce a requested dirdiff result, such
    as an unavailable ref or a failed external engine. HTTP and CLI boundaries
    may expose its concrete message.

    Programming errors and broken invariants must use distinct exceptions.
    `DirdiffError` is not a catch-all or permission to continue with invented
    data.
    """


type InlineTokenStatus = Literal[
    "unchanged",
    "replace",
    "insert",
    "delete",
    "move",
]
"""
Token-level change classification emitted by diff engines.

- `unchanged` matches text on both sides.
- `replace` is one side of a paired inline replacement.
- `insert` and `delete` are one-sided text.
- `move` identifies text moved by a structural engine.

Use the value on `InlineToken.status`. It does not classify the complete row or
carry syntax styling.
"""

type DiffEngineRowStatus = Literal[
    "equal",
    "replace",
    "insert",
    "delete",
    "move",
]
"""Row-level relationship between the aligned left and right source lines.

- `equal` pairs unchanged lines.
- `replace` pairs changed lines.
- `insert` and `delete` are one-sided rows.
- `move` pairs source text classified as moved.

Use this on `DiffEngineRow.status`. Inline spans keep a separate
`InlineTokenStatus`, and fold placeholders are not engine rows.
"""


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

    # Usage

    Use the returned string as argv element zero for every Git subprocess.
    Callers should not cache another Git path or invoke `xcrun` themselves.
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

    Composer builds this value after backend loading and text decoding, then
    passes old and new sides to `DiffEngineProtocol.render_diff`.

    `text` is the content authority. `path_hint` may help parser selection but
    never authorizes a filesystem read. Human-facing labels and repository refs
    stay outside the engine input.
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

    Engines compute this from their complete output rows and inline change
    classifications. Composer aggregates bay summaries into the File summary
    returned by the server.

    Counts describe line-level change categories. They do not describe Git
    status, side existence, or repository-wide totals.
    """

    changed_lines: int
    """Total changed line positions represented by this engine result.

    It equals `modified_lines + added_lines + removed_lines + moved_lines`.
    Composer may sum this value across text bays, so engines must not count an
    aligned position in more than one component category. A visually neutral
    structural row may still contribute when its inline facts carry a change.
    """

    modified_lines: int
    """Number of aligned line positions classified as modified.

    Each position contributes once to `changed_lines`, regardless of how many
    inline tokens changed. Structural engines may derive this from inline
    classifications while keeping the transport row visually neutral.
    """

    added_lines: int
    """Number of aligned line positions classified as added.

    A right-only insert contributes here. Structural engines may also count a
    paired neutral row whose changed inline facts are purely inserted; every
    counted position contributes once, not once per token or source byte.
    """

    removed_lines: int
    """Number of aligned line positions classified as removed.

    A left-only delete contributes here. Structural engines may also count a
    paired neutral row whose changed inline facts are purely deleted; every
    counted position contributes once, not once per token or source byte.
    """

    moved_lines: int
    """Number of aligned line positions classified as moved.

    Structural engines may derive the classification from inline move facts
    while leaving the transport row visually neutral. Each position contributes
    once regardless of how many moved tokens it contains.
    """


class InlineToken(TypedDict):
    """Inline token emitted for a rendered diff row.

    Engines attach these values to changed row sides. Concatenating token text
    must reproduce that side's complete row text in order.

    Tokens describe diff status only. Syntax classes and other display
    decoration are added later by `dirdiff.rendering`.
    """

    text: str
    """Nonempty source slice represented by this token.

    Tokens on one row side appear in source order and their texts concatenate
    to that side's complete row text. Rendering asserts the reconstruction and
    never clips or inserts characters.
    """

    is_ws: bool
    """Whether every character of `text` is whitespace.

    The value must equal `text.isspace()`. Display enrichment trusts it when
    distinguishing leading indentation from other changed token content.
    """

    status: InlineTokenStatus
    """
    Token-level diff status.

    GumTree uses `move` for moved ranges.  Non-structural renderers normally
    emit `unchanged`, `replace`, `insert`, or `delete`.
    """


class DiffEngineRow(TypedDict):
    """One aligned diff row returned by a diff engine.

    Engines return these rows in source order through `DiffEngineResult`.
    Composer then sends them through display enrichment before HTTP validation.

    A row describes aligned source text only. It has no syntax classes, fold
    policy, bay identity, hunk number, or HTTP fields.
    """

    status: DiffEngineRowStatus
    """
    Line-level row status produced by the engine.

    This is limited to real aligned diff rows. Frontend fold placeholders are
    derived later from fold hints and are not legal engine output.
    """

    left_no: int | None
    """One-based old-side source line represented by this aligned row.

    It is `None` exactly for an `insert` row; paired equal, replace, and move
    rows require it, while delete rows retain it with no right-side number.
    """

    right_no: int | None
    """One-based new-side source line represented by this aligned row.

    It is `None` exactly for a `delete` row; paired equal, replace, and move
    rows require it, while insert rows retain it with no left-side number.
    """

    left_text: str
    """Complete old-side line text without its newline terminator.

    An insert row uses `""` because it has no old line. For every present
    `left_no`, this is the exact source line later reconstructed by decorated
    parts; empty source lines remain distinguishable through the line number.
    """

    right_text: str
    """Complete new-side line text without its newline terminator.

    A delete row uses `""` because it has no new line. For every present
    `right_no`, this is the exact source line later reconstructed by decorated
    parts; empty source lines remain distinguishable through the line number.
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

    Engines attach this record when a known failure still permits an honest
    textual result. The discriminator identifies the precise degraded mode and
    the message explains its effect to the reviewer.

    The warning explains degraded output. It must not hide an invalid result or
    convert an unexpected programming failure into success.
    """

    type: Literal[
        "difftastic_graph_limit",
        "difftastic_empty_rows",
        "gumtree_invalid_json",
        "tokendiff_region_limit",
    ]
    """Stable machine category for the exact degraded mode.

    The value determines presentation and must agree with the engine condition
    that produced `message`. Engines may emit only these expected modes; an
    unexpected failure must raise rather than acquire a generic category.
    """

    message: str
    """Reviewer-facing explanation of what the degraded result could not show.

    It accompanies `type` and must describe the concrete effect on returned
    rows. Consumers present it without parsing it for control flow.
    """


class DiffEngineResult(TypedDict):
    """Rendered text-diff data returned by every diff engine.

    Every `DiffEngineProtocol.render_diff` call returns this shape. Composer
    turns it into a text bay and carries any honest degraded-mode warning to
    that bay.

    The result stops before syntax decoration, folds, bay framing, and HTTP
    validation.
    """

    summary: DiffSummary
    """Counts derived from the complete `rows` sequence.

    Each component must agree with the row and inline classifications represented
    by `rows`, and `changed_lines` must equal their sum. Composer aggregates this
    value across text bays without recomputing it.
    """

    rows: list[DiffEngineRow]
    """Complete aligned source rows in monotonically increasing side order.

    The sequence must cover every present source line exactly once. Composer
    passes it unchanged to display enrichment, which may decorate but never
    repair invalid alignment or invent omitted source.
    """

    engine_warning: NotRequired[EngineWarning]
    """Expected degraded-mode explanation, omitted for an ordinary result.

    Presence means `rows` are still an honest renderable representation under
    the named limitation. Composer appends it after bay-builder warnings;
    invalid output and unexpected failures must propagate instead of using it.
    """


EngineKind = Literal["dirdiff", "git", "difftastic", "gumtree", "tokendiff"]
"""Which diff engine a caller selects.

- `dirdiff` selects the native line-first engine.
- `git` selects `git diff --no-index`.
- `difftastic` selects structural Difftastic comparison.
- `gumtree` selects GumTree range-based comparison.
- `tokendiff` selects the native token-first engine.

Pass a validated value to `dirdiff.engines.engine`. It names an implementation,
not a format, executable path, or per-engine option set.
"""


class DiffEngineProtocol(Protocol):
    """Render an already-loaded pair of text sides into a common diff result.

    All diff engines accept the same `DiffSide` inputs and return
    `DiffEngineResult`, so callers can select an engine without changing the
    rest of the rendering flow.

    Engines compare the supplied text. They may use path hints if diff is, for
    example, language-dependent, but must not use them to load file contents.
    Loading text and deciding their format happen outside this interface.

    # Usage
    The user is expected to obtain the right engine using `engine()` function,
    extract and select text chunks with the help of `Composer.bays` and then
    feed them one by one into `render_diff`.

    That said, unless you're implementing `Composer`, you should use its
    `compose` method, which does that all for you.

    # Links
    - `dirdiff.engines.engine` - dispatcher function to pick an implementation
    - `dirdiff.formats.Composer` - intended entrypoint for this interface
    """

    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Process two text handles and return the representation of the diff.

        # Parameters

        - `old`: Already-loaded text, existence, and path hint for the old side.
        - `new`: Already-loaded text, existence, and path hint for the new side.
        """
        ...
