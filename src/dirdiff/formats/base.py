"""Package-internal contracts for the composed-diff formats subsystem.

Every `/api/file-diff` response is one *composed diff*: File-level metadata plus
an ordered list of frames, each holding an ordered list of bays. A frame is
presentational grouping (a notebook cell's card and heading are one frame; an
ordinary text file is one frame with no heading). A bay is one renderable
unit with its own identity, navigable and commentable on its own.

This module owns the enduring contracts every sibling depends on:

- `BayContext` and `ComposeContext`, the two inputs composition reads;
- `Bay`, the pre-render bay a format builder yields, before any diff engine has
  touched it. It splits on what the bay is made of, because that is the
  distinction its consumers act on: a `TextBay` carries two decoded text sides,
  an `ImageBay` carries two sides of exact bytes;
- `text_kind_payload` and `image_kind_payload`, the two shared renderers that
  turn those into the varying half of a bay's wire form — the first through the
  selected engine and the display-enrichment pipeline, the second by reducing
  bytes to a `MediaRef`;
- `media_facts`, the three things that can honestly be said about a side of
  bytes — its media type, its size, its digest — written one per line. An image
  File's facts bay and a blob File's only bay both hold that text, which is two
  callers in sibling modules and is why it lives here;
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

Bytes never reach the wire. An `ImageBay` holds its payload while composing, so
the media endpoint can serve it from the same enumeration; by the time it is
serialized it carries only the `MediaRef` describing that payload.

Purity is the required contract. The same two byte sides and the same
context always produce the same frames, bay keys, and order. Nothing here
reads a clock, a database, a Room, or a file outside the bytes it is given.
"""

from __future__ import annotations

import hashlib
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
    "BLOB_BAY_KEY",
    "FLATFILE_BAY_KEY",
    "IMAGE_BAY_KEY",
    "IMAGE_FACTS_BAY_KEY",
    "MEDIA_FACTS_PATH_HINT",
    "Bay",
    "BayChange",
    "BayContext",
    "BayKindPayload",
    "BayPayload",
    "ChangeStatus",
    "ComposeContext",
    "ComposedFilePayload",
    "ComposedSummary",
    "FramePayload",
    "ImageBay",
    "ImageKindPayload",
    "MediaRef",
    "MediaSide",
    "MovedChangeStatus",
    "TextBay",
    "TextKindPayload",
    "image_kind_payload",
    "media_facts",
    "media_ref",
    "text_kind_payload",
    "whole_file_change",
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


IMAGE_BAY_KEY = "image"
"""Bay key for the picture bay of an image File.

An image File composes one heading-less frame holding its picture and the facts
about its bytes, so it needs its own total coordinate, and it must be a
*different* key from `FLATFILE_BAY_KEY`. A File that was text in one Snapshot
and an image in a later one then composes different bay keys in each, and a
stored line-range target against the text one is reported as `bay_not_found`
instead of landing on a bay that holds no lines.
"""


IMAGE_FACTS_BAY_KEY = "image-facts"
"""Bay key for the text bay stating what is known about an image's bytes.

A bay key names the classification that produced the bay rather than the kind of
the bay, which is why this is not simply `"facts"`. A blob File's facts bay
holds the very same three lines under `BLOB_BAY_KEY`; keying both `"facts"`
would let a target survive a File changing classification, landing a comment
written about a picture on the bytes that replaced it.
"""


BLOB_BAY_KEY = "blob"
"""Bay key for the single text bay of a File nothing else claimed.

Blob is a classification and not a bay kind: what can honestly be shown for
unreadable bytes is their media type, size, and digest, and those are lines, so
the bay holding them is an ordinary `text` bay. The key is distinct from
`FLATFILE_BAY_KEY` and `IMAGE_BAY_KEY` for exactly the reason given there: the
key changing is what makes a File that stopped being text visible to review
placement as a coordinate that no longer exists.
"""


def whole_file_change[Content](
    left: Content | None,
    right: Content | None,
) -> BayChange:
    """Decide what happened to a File that composes into a single bay.

    Such a File has no positions — one bay is the whole of it — so its change is
    read from the two sides themselves: one side absent is an addition or a
    removal, two equal sides are `unchanged`, and anything else is `changed`. A
    whole-File bay is never `moved`, because a move is a position within a
    document and this bay has no document to move within.

    `Content` is whatever the calling builder already validated — decoded text
    for a flatfile, the stated facts for a facts bay, exact bytes for an image
    File's picture — and is compared
    with `==`, so equal content on both sides is `unchanged` in either case.
    Absent means the File was not captured on that side; a captured empty side
    is content, not absence, and callers must not conflate the two.
    """
    if left is None:
        return ChangeStatus(kind="added")
    if right is None:
        return ChangeStatus(kind="removed")
    if left == right:
        return ChangeStatus(kind="unchanged")
    return ChangeStatus(kind="changed")


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


@dataclass(frozen=True)
class MediaSide:
    """One captured side of a File's bytes: the bytes and their media type.

    The bytes are the ones capture retained, unchanged — no transcoding,
    re-encoding, or thumbnailing takes place anywhere in composition, because
    the media endpoint must serve exactly what the Snapshot holds. The media
    type is what the frontend needs to decide how to display it and what the
    endpoint writes as its `Content-Type`.

    There is no `exists` flag, unlike `DiffSide`: a side that was not captured is
    represented by `None` wherever a side of bytes is held, because there is no
    such thing as bytes that are present but absent.
    """

    media_type: str
    """The IANA media type of these bytes, decided by the format builder that
    classified the File. It describes what the builder concluded, not what the
    bytes were sniffed to be."""

    data: bytes
    """The exact captured bytes. Never serialized: `image_kind_payload` reduces
    this to a `MediaRef` before the payload crosses the wire."""


@dataclass(frozen=True)
class ImageBay:
    """One composed bay whose content is two pictures, as exact bytes.

    The identity fields repeat `TextBay`'s rather than sharing a base class,
    because the two are alternatives rather than a hierarchy: consumers
    discriminate on which one they hold, and every one of them acts on the
    content, which is the part that differs. Repeating six declarations is
    cheaper than an inheritance chain that has to be re-read to know what a bay
    carries.

    This is what the media endpoint reads. It holds the payload while composing
    so that one enumeration answers both "which bays exist" and "what bytes does
    this one hold", and no second lookup path into the capture store exists.
    """

    frame_key: str
    """Groups contiguous bays into one frame, under `TextBay.frame_key`'s
    contract. An image File composes one frame, holding this bay and the text
    bay stating what its bytes are."""

    heading: str | None
    """The frame's heading, or `None` for a frame that needs no name — which is
    every frame a whole-File image composes."""

    bay_key: str
    """This bay's public coordinate, unique within the File and durable across
    Snapshots, under `TextBay.bay_key`'s contract."""

    label: str
    """Names the whole bay where it is shown by name, which for an image bay is
    its widget's header."""

    detail: str | None
    """The builder's sentence explaining a change the widget does not show, or
    `None` when the widget tells the whole story."""

    collapsible: bool
    """Whether the reviewer may hide this bay's body."""

    default_expanded: bool
    """Whether a collapsible bay starts open. Ignored when `collapsible` is
    false."""

    change: BayChange
    """What happened to this bay, semantically. An image bay produces no rows at
    all, so this is the *only* thing that tells navigation the bay needs a stop,
    and the only thing that tells the frontend how to tint it."""

    left: MediaSide | None
    """The left side's captured bytes, or `None` when the File was not captured
    on the left. `None` is how an added File is expressed."""

    right: MediaSide | None
    """The right side's captured bytes, under the same contract as `left`."""


Bay = TextBay | ImageBay
"""One bay a format builder yields, before rendering and before serialization.

There is no base class and no `kind` field inside one type: the type *is* the
distinction, so a consumer that must act differently on a picture writes two
branches the type checker enforces, and one that must not act differently
writes none. `compose()` renders a text bay through the engine and reduces an
image bay to references, the media endpoint serves only the second, and review
reconstructs an excerpt from either.
"""


class MediaRef(TypedDict):
    """What one side of an image bay looks like once the bytes are left behind.

    This is the whole of what the frontend learns about a captured picture
    without asking for it: enough for the widget to know the side exists and to
    request it. The bytes themselves come from `/api/file-media`, addressed by
    Snapshot, side, and the File's path pair — never from here.

    The same three facts, written one per line by `media_facts`, are what the
    reviewer reads as rows in the facts bay beside the picture.
    """

    media_type: str
    """The IANA media type the builder concluded, which the media endpoint
    writes as its `Content-Type` for this side."""

    byte_size: int
    """The exact captured size in bytes. Shown to the reviewer, and the one
    number that makes a binary change legible at all."""

    digest: str
    """Lowercase hex SHA-256 of the captured bytes. It identifies the content
    the endpoint will serve for this side, and comparing the two sides' digests
    is what tells a reviewer whether the content actually changed."""


class TextKindPayload(TypedDict):
    """The varying half of a serialized `text` bay: what an engine produced.

    Rows, fold hints, stats, and the engine warning are text-only by nature:
    they are what an engine produced from two decoded sides, and a bay with no
    rows has none of them. They live on this arm rather than on `BayPayload`
    so that a consumer holding an image bay cannot ask for them.

    The name ends in `KindPayload` rather than `BayPayload` because this is not
    a whole bay's payload: it is the value of one field of one.
    """

    kind: Literal["text"]
    """The widget that renders this bay: the existing text diff grid."""

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


class ImageKindPayload(TypedDict):
    """The varying half of a serialized `image` bay: two optional references.

    It carries no bytes and no dimensions. The widget requests each side from
    `/api/file-media` and lets the browser decode it, which is the one thing the
    browser does better than any backend here.
    """

    kind: Literal["image"]
    """The widget that renders this bay: the image widget."""

    left: MediaRef | None
    """The left side's captured image, or `None` when the File was not captured
    on the left. `None` is an absent side, which the widget must show as absent
    rather than as an empty frame."""

    right: MediaRef | None
    """The right side's captured image, under the same contract as `left`."""


BayKindPayload = TextKindPayload | ImageKindPayload
"""The content of one serialized bay, discriminated by `kind`.

Two arms, because there are two things a reviewer can look at: lines, and a
picture. `MediaRef` belongs to `ImageKindPayload` alone — it names the bytes the
widget must fetch, and no other kind fetches bytes. A blob File's facts and an
image File's facts are lines, so they are `text` and need no arm of their own.

Bytes never reach here: an image bay is reduced to its `MediaRef` sides before
serialization. TypeScript declares the same union independently, as it does for
everything else crossing this boundary; neither declaration is generated from
the other.
"""


class BayPayload(TypedDict):
    """One serialized bay as the frontend receives it.

    The fields every bay carries whatever it holds, plus `kind_data`, the single
    field that varies. The discriminator therefore sits one level down:
    placement, identity, collapse, and status read this record directly and
    never learn the kind, and only the frontend's widget dispatch descends into
    `kind_data` and switches on its `kind`. Adding a kind adds one arm to
    `BayKindPayload` and touches nothing here.
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

    change: BayChange
    """What happened to this bay. Only the format builder can answer it, and
    the frontend colours from it and infers nothing. Anything but `unchanged`
    needs somewhere for the reviewer to land."""

    kind_data: BayKindPayload
    """What this bay holds, and the only field that varies by kind. It is
    backend-owned like every other wire field: the frontend chooses a widget
    from it and never authors or rewrites it."""


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


def media_ref(side: MediaSide) -> MediaRef:
    """Describe one captured media side without carrying its bytes.

    This is the single place a media side's digest is computed, so the digest
    the frontend receives, the digest the media endpoint's content is
    identified by, and the digest review reconstructs its context from are the
    same string produced the same way. SHA-256 in lowercase hex, matching how
    the rest of the project spells a content hash.
    """
    return {
        "media_type": side.media_type,
        "byte_size": len(side.data),
        "digest": hashlib.sha256(side.data).hexdigest(),
    }


MEDIA_FACTS_PATH_HINT = "media"
"""The path hint a facts bay's two sides carry.

Three named facts about a file's bytes are not source in any language, so they
must not select a parser. `media` names no suffix any parser claims, which keeps
structural matching and highlighting over whole lines rather than following
whatever the File's real extension implies. Review's image pseudo-line uses the
same hint for the same reason.
"""


def media_facts(side: MediaSide | None) -> str | None:
    """State what is known about one side of bytes, one fact per line.

    The three facts are the media type composition concluded, the exact captured
    size, and the digest of the captured bytes — in the spirit of what
    `git diff` prints for a binary file, and everything that can honestly be
    said about content nothing here reads. They are text, so the bay holding
    them is an ordinary `text` bay and the reviewer gets a real diff of them:
    the size row changed, the digest row changed, the media type row did not.

    `None` is a side the File was not captured on, which is how an added or
    removed File is expressed, matching `DiffSide`'s absent side.
    """
    if side is None:
        return None
    ref = media_ref(side)
    return (
        f"type: {ref['media_type']}\n"
        f"size: {ref['byte_size']} bytes\n"
        f"sha256: {ref['digest']}"
    )


def image_kind_payload(bay: ImageBay) -> ImageKindPayload:
    """Reduce one `ImageBay`'s content to its serialized wire form.

    No engine takes part and no bytes survive: each present side becomes the
    `MediaRef` describing it, and an absent side stays `None`. This is the point
    at which the payload stops being able to leak captured content, which is why
    it is the only path from an `ImageBay` to a response.
    """
    return {
        "kind": "image",
        "left": None if bay.left is None else media_ref(bay.left),
        "right": None if bay.right is None else media_ref(bay.right),
    }


def text_kind_payload(
    bay: TextBay,
    renderer: DiffEngineProtocol,
) -> TextKindPayload:
    """Render one `TextBay`'s content into its serialized wire form.

    This is the one shared text-bay renderer, so ordinary text and every
    per-format text bay — a notebook cell's source, an image File's facts, a
    blob File's only bay — reach the engine and the display-enrichment pipeline
    through exactly one path. It calls the selected engine, then
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
        "kind": "text",
        "left_label": bay.left_label,
        "right_label": bay.right_label,
        "rows": display["rows"],
        "fold_hints": display.get("fold_hints", []),
        "stats": rendered["summary"],
        "engine_warning": rendered.get("engine_warning"),
    }
