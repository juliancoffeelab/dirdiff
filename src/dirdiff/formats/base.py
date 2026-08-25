"""Package-internal contracts for the composed-diff formats subsystem.

Every `/api/file-diff` response is one *composed diff*: File-level metadata plus
an ordered list of frames, each holding an ordered list of bays. A frame is
presentational grouping (a notebook cell's card and heading are one frame; an
ordinary text file is one frame with no heading). A bay is one renderable
unit with its own identity, navigable and commentable on its own.

This module owns the enduring contracts every sibling depends on:

- `BayContext` and `ComposeContext`, the two inputs composition reads;
- `TextBay`, the pre-render bay a format builder yields, before any diff
  engine has touched it;
- `render_text_bay`, the one shared text-bay renderer that turns a
  `TextBay` into its serialized wire form through the selected engine and the
  display-enrichment pipeline;
- the serialized payload shapes (`BayPayload`, `FramePayload`,
  `ComposedFilePayload`) that survive to the wire.

Sibling modules import these from `base.py`, never from the package facade. This
module owns no format-specific decision: which bays a file composes into is
`composer.py`'s ordered classification and the per-format builders it calls. It
owns no request state, no Room vocabulary, and no HTTP concern.

It also owns no hunk numbering. Enrichment marks which rows begin a changed run,
bay by bay, and `change` says what happened to a bay. Turning those
facts into one navigable sequence is a decision about how a reviewer steps
through a File, so the frontend makes it.

Purity is the required contract. The same two byte sides and the same
context always produce the same frames, bay keys, and order. Nothing here
reads a clock, a database, a Room, or a file outside the bytes it is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from dirdiff.engines import (
    DiffEngineProtocol,
    DiffSide,
    DiffSummary,
    EngineWarning,
)
from dirdiff.rendering import (
    DiffRow,
    FoldHint,
    enrich_rows_for_display,
)

__all__ = [
    "FLATFILE_BAY_KEY",
    "BayChange",
    "BayContext",
    "BayPayload",
    "ChangeStatus",
    "ComposeContext",
    "ComposedFilePayload",
    "ComposedSummary",
    "FramePayload",
    "MovedChangeStatus",
    "TextBay",
    "render_text_bay",
]


class ChangeStatus(TypedDict):
    """A bay outcome that is fully told by its kind: the bay was added,
    removed, changed in place, or left alone. Moves are not plain — they
    carry coordinates and live in `MovedChangeStatus`."""

    kind: Literal["added", "removed", "changed", "unchanged"]


class MovedChangeStatus(TypedDict):
    """The bay changed position between the two documents.

    The two fields are the names the bay wore on each side: `from_heading` in
    the old document, `to_heading` in the new one. They are the same names
    the frames are headed by, so a reader can find both ends on screen —
    positions are not among them, because nothing displays a position. A side
    the builder cannot name carries `None` there, which is what a notebook's
    prose cells are: they have no prompt, so a moved one can say only that it
    moved. Whether the content was also edited is what the rows show, not
    what this shape encodes.
    """

    kind: Literal["moved"]
    from_heading: str | None
    to_heading: str | None


BayChange = ChangeStatus | MovedChangeStatus
"""What happened to one bay, decided by the builder that composed it.

This is semantic, not visual and not navigational. Only a format builder can
answer it: a notebook cell that moved and a cell whose output changed beyond its
rendered text produce identical rows, and nothing downstream can tell them apart
from those rows. The frontend renders this — a tint, a status — and never
infers it.

A `moved` status means the bay changed position and carries the name it wore
at each end; its rows may still show an edit. `unchanged` means nothing
happened to the bay, which is also what tells hunk numbering that it needs no
navigation stop of its own.
"""


FLATFILE_BAY_KEY = "flatfile"
"""Bay key for the single text bay of a flatfile.

`bay_key` is the universal sub-file coordinate shared with line pins and
review text targets. A flatfile always uses this exact key, so a review target
or pin URL naming one names a stable coordinate.

It lives here rather than in `flatfile.py` because consumers outside this
package compare against it, and they must not import a format module to do so.
"""


@dataclass(frozen=True)
class BayContext:
    """The inputs composing bays reads, and nothing an engine produces.

    The paths drive classification and become each bay's syntax path hint; a
    per-format builder may narrow them (a notebook cell source hint is
    `cell.py`, not the notebook's path). The labels become each text bay's
    two side headings, which no engine produces, so bay building sets them.

    It carries no renderer. That a bay-yielding consumer runs no engine is a
    type-level fact here, not a convention: `bays()` takes only this.
    """

    left_path: str | None
    right_path: str | None
    left_label: str
    right_label: str


@dataclass(frozen=True)
class ComposeContext:
    """The inputs `compose()` reads: bay inputs plus the selected renderer.

    Containment rather than inheritance keeps the bay inputs defined in
    exactly one place (`bays`) and makes reaching a renderer impossible
    without `compose()`. `compose()` passes `bays` down verbatim, so what
    composition classified on and what the frontend receives cannot drift apart.

    `display_name` and `file_kind` are deliberately absent: composition never
    reads them, and they are the two envelope fields the HTTP boundary attaches
    from the manifest rather than composition producing them.
    """

    bays: BayContext
    renderer: DiffEngineProtocol

    @classmethod
    def build(
        cls,
        *,
        left_path: str | None,
        right_path: str | None,
        left_label: str,
        right_label: str,
        renderer: DiffEngineProtocol,
    ) -> ComposeContext:
        """Build one `ComposeContext` from plain facts, not a Room type.

        This is the named constructor a request handler calls: both paths, both
        side labels, and the renderer. It exists so a handler does not assemble
        a context field by field, and so composition never imports the Room
        vocabulary to reach the two facts it needs.
        """
        return cls(
            bays=BayContext(
                left_path=left_path,
                right_path=right_path,
                left_label=left_label,
                right_label=right_label,
            ),
            renderer=renderer,
        )


@dataclass(frozen=True)
class TextBay:
    """One composed bay whose content is two decoded text sides.

    This is what a format builder yields, before any engine has rendered it. The
    decoded sides here are the exact sides `compose()` renders, so nothing is
    decoded or parsed twice, and the consumers that never render read identity
    and content from here without an engine in reach.
    """

    frame_key: str
    """Groups contiguous bays into one frame. Bays sharing a key and
    heading are one frame; a notebook cell's source, metadata and outputs share
    the cell's key."""

    heading: str | None
    """The frame's heading, rendered above its bays. `None` for a frame that
    needs no name, which is every frame of a flatfile."""

    bay_key: str
    """This bay's public coordinate, unique within the File and durable
    across Snapshots. Review targets and line pins store it, so it must identify
    the same bay in a later capture or a Thread anchored here goes
    unplaceable."""

    label: str
    """Names the whole bay where it is shown by name, which is its collapsed
    placeholder and the content column of the inline grid. Distinct from the two
    side headings below."""

    detail: str | None
    """The builder's sentence explaining a change the rows do not show. `None`
    when the rows tell the whole story. Only the builder knows why it emitted
    such a bay, so it says so rather than leaving the reviewer to guess from
    a label."""

    collapsible: bool
    """Whether the reviewer may hide this bay's body. A cell's source is what
    identifies the cell and everything else in the frame hangs off it, so source
    is always shown while metadata and outputs can be hidden. Set here so the
    frontend renders the hierarchy the format defines instead of inventing one
    from bay order."""

    default_expanded: bool
    """Whether a collapsible bay starts open. Ignored when `collapsible` is
    false, which is always shown."""

    change: BayChange
    """What happened to this bay, semantically. Only the builder can answer:
    a cell that moved and a cell whose output changed beyond its rendered text
    both produce rows that are entirely equal. Anything but `unchanged` needs
    somewhere for the reviewer to land."""

    left_label: str
    """Heading for the left column of the rendered grid. It lives here rather
    than on the `DiffSide`, because an engine input carries no human-facing
    label by design."""

    right_label: str
    """Heading for the right column of the rendered grid."""

    left: DiffSide
    """The left side's decoded text, or a side whose `exists` is false. A
    one-sided File and a cell present on only one side are the same shape."""

    right: DiffSide
    """The right side's decoded text, under the same contract as `left`."""


class BayPayload(TypedDict):
    """The serialized `text` bay that survives to the wire.

    This is what the frontend receives and dispatches on by `kind`. Bytes never
    reach here: a future blob bay is reduced to a reference before
    serialization, so only these shapes cross the boundary.
    """

    bay_key: str
    """This bay's public coordinate, unique in the File and durable across
    Snapshots. Review targets and line pins are stored against it."""

    label: str
    """Names the whole bay where it is shown by name: its collapsed
    placeholder, and the content column of the inline grid."""

    detail: str | None
    """A sentence from the builder explaining a change the rows do not show, or
    `None` when the rows tell the whole story."""

    collapsible: bool
    """Whether the reviewer may hide this bay's body. False means the bay
    is the frame's body and is always shown."""

    default_expanded: bool
    """Whether a collapsible bay starts open."""

    kind: Literal["text"]
    """The widget that renders this bay. The frontend switches on it and
    nothing else; `image` and `binary` join it in later stages."""

    left_label: str
    """Heading for the grid's left column, distinct from `label`."""

    right_label: str
    """Heading for the grid's right column."""

    rows: list[DiffRow]
    """The rendered rows, in document order. A row whose `hunk_index` is not
    `None` begins a changed run, numbered from zero within this bay; the
    frontend turns those into the File's one navigable sequence."""

    fold_hints: list[FoldHint]
    """Ranges the frontend may fold. A fold never contains a hunk boundary,
    which the frontend enforces by throwing."""

    stats: DiffSummary
    """This bay's own line counts, shown in the header of a collapsible
    bay so a reviewer can judge whether opening it is worthwhile."""

    engine_warning: EngineWarning | None
    """The engine's report that it gave up matching these rows, or `None`. It
    belongs to the bay whose rows it describes, not to the File, since one
    File holds many bays and only some may carry one."""

    change: BayChange
    """What happened to this bay. Only the format builder can answer it, and
    the frontend colours from it and infers nothing. Anything but `unchanged`
    needs somewhere for the reviewer to land."""


class FramePayload(TypedDict):
    """One presentational frame: an optional heading over ordered bays.

    A frame carries no annotations of its own. Everything a reviewer needs to
    know about a change belongs to the bay that changed, because a bay can
    be navigated to, collapsed, and commented on, and a frame cannot.
    """

    frame_key: str
    """Identifies the frame within its File. Bays sharing this key and
    heading were grouped into one frame."""

    heading: str | None
    """Rendered above the frame's bays, or `None` for a frame that needs no
    name. A flatfile's one frame has none."""

    bays: list[BayPayload]
    """The frame's bays in document order. The first is the frame's body, the
    thing the frame is about; the rest are attached to it."""


class ComposedSummary(DiffSummary):
    """File-level line-count summary plus loaded-side existence flags.

    The line counts aggregate every bay's engine summary. Side existence is
    a File fact (whether the File was captured on each side), attached by
    `compose()` from the byte sides it was given rather than from any bay.
    """

    left_exists: bool
    """Whether the File was captured on the left side. False means the File was
    added."""

    right_exists: bool
    """Whether the File was captured on the right side. False means the File was
    removed."""


class ComposedFilePayload(TypedDict):
    """Everything `compose()` produces: the envelope minus its two attached fields.

    `display_name` and `file_kind` are absent because composition never reads or
    produces them; the HTTP boundary attaches both from the manifest before
    serialization. Every other envelope field is produced here.
    """

    left_label: str
    """Names the left side being compared, such as a ref or `HEAD`. Re-emitted
    from the request context, not derived."""

    right_label: str
    """Names the right side, such as `worktree`."""

    left_path: str | None
    """The File's path on the left side, or `None` when it was added. Together
    with `right_path` it is the File pair review targets are stored against."""

    right_path: str | None
    """The File's path on the right side, or `None` when it was removed."""

    summary: ComposedSummary
    """Line counts aggregated across every bay, plus which sides exist."""

    default_expanded: bool
    """Whether the File's body starts open in the ChangeSet."""

    frames: list[FramePayload]
    """The File's frames in document order. Always at least one: a File with no
    internal structure composes a single heading-less frame holding a single
    bay, which is what makes `bay_key` a total coordinate."""


def render_text_bay(
    bay: TextBay,
    renderer: DiffEngineProtocol,
) -> BayPayload:
    """Render one `TextBay` into its serialized wire form.

    This is the one shared text-bay renderer, extracted so ordinary text and
    every per-format text bay reach the engine and the display-enrichment
    pipeline through exactly one path. It calls the selected engine, then
    `enrich_rows_for_display`, so rows, parts, fold hints, and hunk boundaries
    keep exactly the file-meat contracts. The engine stays text-only and never
    learns that bays or formats exist.

    Row `hunk_index` values are bay-local and stay that way. Numbering the
    File's hunks into one sequence is navigation, and the frontend owns it.
    """
    rendered = renderer.render_diff(old=bay.left, new=bay.right)
    left_text = "" if bay.left.text is None else bay.left.text
    right_text = "" if bay.right.text is None else bay.right.text
    display = enrich_rows_for_display(
        rows=[dict(row) for row in rendered["rows"]],
        left_text=left_text,
        right_text=right_text,
        left_path_hint=bay.left.path_hint,
        right_path_hint=bay.right.path_hint,
    )
    return {
        "bay_key": bay.bay_key,
        "label": bay.label,
        "detail": bay.detail,
        "collapsible": bay.collapsible,
        "default_expanded": bay.default_expanded,
        "kind": "text",
        "left_label": bay.left_label,
        "right_label": bay.right_label,
        "rows": display["rows"],
        "fold_hints": display.get("fold_hints", []),
        "stats": rendered["summary"],
        "engine_warning": rendered.get("engine_warning"),
        "change": bay.change,
    }
