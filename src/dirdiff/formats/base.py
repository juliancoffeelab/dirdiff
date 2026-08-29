"""Shared contracts and operations for File composition.

## Public interface

This module defines the contexts, pre-render bays, warnings, and serialized
payload types used by every format builder. Its functions classify whole-bay
change, decode project text, describe media, and turn text or image bays into
their wire content.

## Purpose and boundaries

Format builders need one vocabulary before `Composer` can group and serialize
their results. Keeping that vocabulary here lets engine-free consumers inspect
bay identity and content without importing the format that produced it. This
module does not select a format or load a File. Exact media bytes may exist in a
pre-render `ImageBay`, but never in a serialized payload.
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
    "IMAGE_METADATA_BAY_KEY",
    "MEDIA_FACTS_PATH_HINT",
    "Bay",
    "BayChange",
    "BayContext",
    "BayKindPayload",
    "BayPayload",
    "BayWarning",
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
    "TextRejection",
    "image_kind_payload",
    "media_facts",
    "media_ref",
    "text_kind_payload",
    "try_decode_text",
    "whole_file_change",
]


class ChangeStatus(TypedDict):
    """Describe a bay that did not move between document positions.

    A moved bay uses `MovedChangeStatus` because it needs both headings. This
    value does not describe row-level changes.
    """

    kind: Literal["added", "removed", "changed", "unchanged"]
    """Whole-bay outcome decided before rendering.

    `added` and `removed` are one-sided, `changed` retains the bay's position
    with different content, and `unchanged` reports no semantic change.
    """


class MovedChangeStatus(TypedDict):
    """Describe one bay that changed document position.

    Format builders attach this variant when they can establish movement, and
    the HUD uses its headings to identify the old and new frame context.

    Either heading is `None` when that side has no useful name. The value does
    not encode numeric positions or whether the bay's content also changed;
    rendered rows carry the content difference.
    """

    kind: Literal["moved"]
    """Stable discriminator selecting the movement variant on the wire.

    It says nothing about content equality; rendered text rows carry any edit
    that happened while the bay moved.
    """

    from_heading: str | None
    """Old frame heading shown as the movement origin.

    `None` means the format has no useful old-side heading, not that the old bay
    was absent. Added bays use a different change variant.
    """

    to_heading: str | None
    """New frame heading shown as the movement destination.

    `None` is an unnamed destination rather than a removed bay. Builders provide
    both headings from their document structure, never from row alignment.
    """


BayChange = ChangeStatus | MovedChangeStatus
"""What happened to one bay, decided by the builder that composed it.

- `ChangeStatus` describes added, removed, changed, or unchanged content at the
  same document position.
- `MovedChangeStatus` carries the old and new headings of a moved bay; its rows
  may still show edits.

Format builders assign this semantic fact before rendering because rows cannot
recover it. A moved cell and a cell whose non-rendered output changed can
produce the same rows, so the HUD must present this value rather than infer it.
`unchanged` also tells hunk numbering that the bay needs no navigation stop of
its own. This is not a row status or navigation coordinate.
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


IMAGE_METADATA_BAY_KEY = "image-metadata"
"""Public coordinate for the optional image dimensions and EXIF text bay.

The key distinguishes parsed metadata from the picture and byte-facts bays, so
review targets survive changes inside that representation without landing on a
different kind of content. Builders omit the bay when neither side yields
metadata; they never reuse this key for opaque blob facts.
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

    Such a File has no internal positions, so one bay is the whole of it and its change is
    read from the two sides themselves: one side absent is an addition or a
    removal, two equal sides are `unchanged`, and anything else is `changed`. A
    whole-File bay is never `moved`, because a move is a position within a
    document and this bay has no document to move within.

    `Content` is whatever the calling builder already validated, such as decoded
    text for a flatfile, stated facts for a facts bay, or exact image bytes. It is
    compared with `==`, so equal content on both sides is `unchanged`.
    Absent means the File was not captured on that side; a captured empty side
    is content, not absence, and callers must not conflate the two. At least one
    side must be present; `(None, None)` is outside this function's contract.

    # Parameters

    - `left`: Old-side content, or `None` when that side was not captured.
    - `right`: New-side content under the same convention.

    # Usage

    Format builders call this after establishing the content that represents
    each side of a single-bay File. Use `BayChange` directly for formats with
    internal positions, where movement can be meaningful.
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
    """Old File path used for classification and later as a syntax hint.

    `None` means that side is absent. Builders may narrow a present path for
    internal text, such as a notebook cell's language-specific source.
    """

    right_path: str | None
    """New File path under the same classification and hint contract.

    A rename may provide two different paths; composition preserves both rather
    than choosing one canonical File path.
    """

    left_label: str
    """Reviewer-facing old-side heading attached to composed text bays.

    It names the comparison side, not the File path or the bay itself. Engines
    never author or change it.
    """

    right_label: str
    """Reviewer-facing new-side heading under the same contract as `left_label`.

    Full composition also re-emits it on the File envelope so headers and bay
    grids cannot disagree.
    """


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
    """Exact classification and heading inputs passed to `Composer.bays`.

    `compose()` does not duplicate or rewrite these facts before bay building,
    keeping rendered and non-rendering consumers on one classification path.
    """

    renderer: DiffEngineProtocol
    """Selected engine used by full composition for every text bay in order.

    `Composer.compose` invokes `render_diff(old=bay.left, new=bay.right)` exactly
    once as it visits each yielded `TextBay`; image bays never invoke it. Each
    argument is the builder's decoded `DiffSide`, including explicit absence and
    its parser-only path hint. The returned rows go immediately through display
    enrichment with those same texts and hints, while summary and warning data
    enter the text-bay payload.

    The engine may return a declared degraded warning with honest rows. Callers
    must let invalid results and raised engine failures propagate; composition
    neither retries the call nor selects a different renderer.
    """

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

        # Parameters

        - `left_path`: Old File path, or `None` for an absent side.
        - `right_path`: New File path, or `None` for an absent side.
        - `left_label`: Heading for old-side text bays.
        - `right_label`: Heading for new-side text bays.
        - `renderer`: Selected engine used only by full composition.

        # Usage

        HTTP rendering constructs this after loading a captured File pair and
        selecting an engine. Pass the result unchanged to `Composer.compose`.
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
    """This bay's public coordinate, unique within the File.

    Review targets and line pins retain it. A builder should preserve the key
    across Snapshots while it still identifies the same logical bay; otherwise
    review placement reports the old bay as lost.
    """

    label: str
    """Names the whole bay where it is shown by name, which is its closed
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
    """Builder-selected initial visibility for a bay with `collapsible=true`.

    The HUD consults it only when `collapsible` is true and may later retain the
    reviewer's explicit choice. A bay with `collapsible=false` remains shown
    regardless of this value, so builders must not use it to hide required frame
    content.
    """

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
    """Human-facing heading for the new-side grid column.

    The composition context supplies it alongside `left_label`; engines never
    infer or rewrite either heading from file paths or source contents.
    """

    left: DiffSide
    """The left side's decoded text, or a side whose `exists` is false. A
    one-sided File and a cell present on only one side are the same shape."""

    right: DiffSide
    """Decoded new-side engine input, including explicit absence.

    It belongs to the same File or cell as `left`. Builders preserve one-sided
    existence here instead of replacing a missing side with invented text.
    """

    warnings: tuple[BayWarning, ...] = ()
    """Visible degradation specific to this text bay's representation.

    Composition forwards warnings in order beside this bay. They do not turn a
    successfully represented bay into an engine failure or a File-wide warning.
    """


@dataclass(frozen=True)
class MediaSide:
    """One captured side of a File's bytes: the bytes and their media type.

    The bytes are exactly what capture retained. No transcoding, re-encoding,
    or thumbnailing takes place anywhere in composition because
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
    """Shared frame heading displayed above this image and its related bays.

    Whole-File images use `None` because their frame needs no separate name.
    Bays group together only when both `frame_key` and this heading agree.
    """

    bay_key: str
    """Public File-local coordinate of the image widget.

    The image builder keeps this key across Snapshots while the File remains an image. It is
    distinct from its facts and metadata bay keys. Review placement uses that
    distinction to report a File classification change rather than land on
    unrelated text.
    """

    label: str
    """Presentation name shown in the image widget's bay header.

    It names this bay rather than either captured side or its frame. Builders
    supply it directly; the HUD must not derive a replacement from media type.
    """

    detail: str | None
    """Builder-authored explanation of an image change not visible in the widget.

    `None` means the two rendered pictures and `change` state tell the complete
    story. Callers present a value as supporting text and do not parse it to
    infer change identity.
    """

    collapsible: bool
    """Whether the HUD may replace this image body with closed chrome.

    Builders set false when the picture is the frame's required body. The field
    grants presentation control only; it never removes the bay or its hunk stop.
    """

    default_expanded: bool
    """Initial open state consulted only when `collapsible` is true.

    An image bay with `collapsible=false` is always shown regardless of this
    value. The
    frontend may later retain an explicit reviewer choice outside the payload.
    """

    change: BayChange
    """What happened to this bay, semantically. An image bay produces no rows at
    all, so this is the *only* thing that tells navigation the bay needs a stop,
    and the only thing that tells the frontend how to tint it."""

    left: MediaSide | None
    """The left side's captured bytes, or `None` when the File was not captured
    on the left. `None` is how an added File is expressed."""

    right: MediaSide | None
    """Exact new-side captured bytes, or `None` when that side is absent.

    The value is paired with `left` in one File frame and reaches media serving
    unchanged. `None` must render as an absent side, not an empty byte stream.
    """

    warnings: tuple[BayWarning, ...] = ()
    """Visible degradation specific to this image bay.

    Builders attach damage at the smallest bay whose picture representation is
    affected. The warnings do not describe separate facts or fail composition.
    """


Bay = TextBay | ImageBay
"""One bay a format builder yields, before rendering and before serialization.

- `TextBay` carries decoded sides for engine rendering.
- `ImageBay` carries exact media bytes for reference conversion and serving.

Consumers branch on the concrete type only when content requires different
handling. There is no base class or redundant `kind` field, and neither variant
is a serialized payload. `compose()` renders `TextBay` and reduces `ImageBay`
to references; the media endpoint serves only `ImageBay`, while review can
reconstruct excerpts from either.
"""


class BayWarning(TypedDict):
    """A non-fatal degradation attached to the smallest affected bay.

    `type` is a stable machine-readable discriminator. `message` is the full
    explanation shown to the reviewer. Engine warnings and format warnings use
    this common wire shape without claiming the same source.
    """

    type: str
    """Stable machine-readable discriminator shared with the frontend.

    Format and engine producers choose from their documented warning vocabulary;
    consumers must not derive behavior by parsing `message`.
    """

    message: str
    """Complete reviewer-facing explanation of the degraded bay result.

    It must identify what could not be represented and what the reviewer sees
    instead. It is not an exception traceback or a substitute status code.
    """


class MediaRef(TypedDict):
    """What one side of an image bay looks like once the bytes are left behind.

    This is the whole of what the frontend learns about a captured picture
    without asking for it: enough for the widget to know the side exists and to
    request it. The bytes themselves come from `/api/file-media`, addressed by
    Snapshot, side, and the File's path pair. They never come from this value.

    The same three facts, written one per line by `media_facts`, are what the
    reviewer reads as rows in the facts bay beside the picture.
    """

    media_type: str
    """IANA media type concluded for this exact captured side.

    The media endpoint writes it as `Content-Type`, while the HUD may use it to
    choose presentation. It describes builder classification and is paired with
    the digest and byte size; consumers must not sniff a different type later.
    """

    byte_size: int
    """The exact captured size in bytes. Shown to the reviewer, and the one
    number that makes a binary change legible at all."""

    digest: str
    """Lowercase hex SHA-256 of the captured bytes. It identifies the content
    the endpoint will serve for this side, and comparing the two sides' digests
    is what tells a reviewer whether the content actually changed."""


class TextKindPayload(TypedDict):
    """The varying half of a serialized `text` bay: what an engine produced.

    Rows, fold hints, and stats are text-only by nature:
    they are what an engine produced from two decoded sides, and a bay with no
    rows has none of them. They live on this arm rather than on `BayPayload`
    so that a consumer holding an image bay cannot ask for them.

    The name ends in `KindPayload` rather than `BayPayload` because this is not
    a whole bay's payload: it is the value of one field of one.
    """

    kind: Literal["text"]
    """Discriminator selecting the HUD text-diff grid.

    It also narrows the remaining fields to rendered rows, folds, and text-side
    headings; it never identifies the File format that produced the bay.
    """

    left_label: str
    """Old-side grid heading copied from the composition context.

    It is distinct from the bay `label`, which names the content as a whole.
    """

    right_label: str
    """New-side grid heading paired with `left_label`.

    The renderer cannot change it because engines compare content without
    reviewer-facing side names.
    """

    rows: list[DiffRow]
    """The rendered rows, in document order. A row whose `hunk_index` is not
    `None` begins a changed run, numbered from zero within this bay; the
    frontend turns those into the File's one navigable sequence."""

    fold_hints: list[FoldHint]
    """Ranges the frontend may fold. A fold never contains a hunk boundary,
    which the frontend enforces by throwing."""

    stats: DiffSummary
    """Engine counts for this text bay's complete `rows` sequence.

    The HUD shows them in bay chrome, and Composer adds them once to
    the File summary. Image bays contribute no line counts, so callers must not
    infer File totals from this value alone.
    """


class ImageKindPayload(TypedDict):
    """The varying half of a serialized `image` bay: two optional references.

    It carries no bytes and no dimensions. The widget requests each side from
    `/api/file-media` and lets the browser decode it, which is the one thing the
    browser does better than any backend here.
    """

    kind: Literal["image"]
    """Discriminator selecting the HUD image widget.

    This variant carries media references only. Dimensions and byte facts are
    separate text bays rather than hidden fields on the image widget.
    """

    left: MediaRef | None
    """The left side's captured image, or `None` when the File was not captured
    on the left. `None` is an absent side, which the widget must show as absent
    rather than as an empty frame."""

    right: MediaRef | None
    """New-side media address, or `None` when the File has no right side.

    The reference identifies captured bytes served by the media endpoint. The
    HUD must show absence for `None` and must not substitute the left reference.
    """


BayKindPayload = TextKindPayload | ImageKindPayload
"""The content of one serialized bay, discriminated by `kind`.

- `TextKindPayload` carries decorated rows, folds, and bay statistics.
- `ImageKindPayload` carries media references for the image widget.

Blob and image facts are text and need no separate variant. Bytes never reach
this union; image composition reduces them to `MediaRef` first, and no text
variant may contain a media reference. The frontend declares the same wire
union independently. Adding a kind requires a matching frontend variant but
does not change the bay envelope.
"""


class BayPayload(TypedDict):
    """One serialized bay as the frontend receives it.

    The fields every bay carries whatever it holds, plus `kind_data`, the single
    field that varies. The discriminator therefore sits one level down:
    placement, identity, expansion, and status read this record directly and
    never learn the kind, and only the frontend's widget dispatch descends into
    `kind_data` and switches on its `kind`. Adding a kind adds one arm to
    `BayKindPayload` and touches nothing here.
    """

    bay_key: str
    """This bay's public coordinate, unique in the File.

    Review targets and line pins retain it. Builders keep it stable while it
    still identifies the same logical bay; a changed key makes the old bay
    unavailable to later placement.
    """

    label: str
    """Builder-provided presentation name for this serialized bay.

    The HUD uses it in closed chrome and inline-grid content labels. It is not a
    coordinate, side heading, or substitute for `bay_key` when retaining state.
    """

    detail: str | None
    """Optional builder explanation for semantic change absent from bay content.

    The value survives serialization verbatim and supplements `change`; `None`
    means no extra explanation is required. Consumers must not derive behavior
    by parsing this presentation text.
    """

    collapsible: bool
    """Whether the reviewer may hide this bay's body. False means the bay
    is the frame's body and is always shown."""

    default_expanded: bool
    """Initial open state for a bay the payload permits reviewers to close.

    The value is ignored when `collapsible` is false and never records a later
    reviewer choice; that state remains in the mounted ChangeSet.
    """

    change: BayChange
    """What happened to this bay. Only the format builder can answer it, and
    the frontend colours from it and infers nothing. Anything but `unchanged`
    needs somewhere for the reviewer to land."""

    warnings: list[BayWarning]
    """Ordered visible damage notices scoped to this serialized bay.

    Engine warnings and format warnings meet here after successful composition.
    Consumers display them without converting the bay into a failed File.
    """

    kind_data: BayKindPayload
    """What this bay holds, and the only field that varies by kind. It is
    backend-authored like every other wire field: the frontend chooses a widget
    from it and never authors or rewrites it."""


class FramePayload(TypedDict):
    """One presentational frame: an optional heading over ordered bays.

    A frame carries no annotations of its own. Everything a reviewer needs to
    know about a change belongs to the bay that changed, because a bay can
    be navigated to, closed, and commented on, and a frame cannot.
    """

    frame_key: str
    """Public File-local identity shared by all bays in this frame.

    Composer groups only contiguous bays whose key and heading both match. The
    builder decides whether a logical frame can retain this key across
    Snapshots. The key is not a bay coordinate by itself.
    """

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
    """Whether capture supplied any old-side bytes for the composed File.

    `False` with `right_exists=True` identifies an added File. An empty captured
    File is still present, so consumers must not derive this value from text rows
    or aggregate counts.
    """

    right_exists: bool
    """Whether capture supplied any new-side bytes for the composed File.

    `False` with `left_exists=True` identifies a removed File. Presence remains
    independent of empty content and of whether a File composes text or images.
    """


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
    """Human-facing new-side comparison label supplied by the request context.

    Composition re-emits it unchanged beside `left_label`; it is not derived
    from `right_path` and remains meaningful for a removed File.
    """

    left_path: str | None
    """The File's path on the left side, or `None` when it was added. Together
    with `right_path` it is the File pair review targets are stored against."""

    right_path: str | None
    """Canonical new-side path, or `None` for a File absent on that side.

    Together with `left_path` it is the durable File pair used by review and
    line-pin identity. At least one side path must be present.
    """

    summary: ComposedSummary
    """File-level line totals and side-existence facts derived during composition.

    Counts aggregate rendered text bays without inventing line statistics for
    image content. Side existence comes from captured inputs, not bay contents.
    """

    default_expanded: bool
    """Backend-selected initial FileCard expansion when no explicit choice exists.

    The payload does not retain later reviewer expansion. Lazy and explicit
    ChangeSet policy may temporarily take precedence before FullFile rendering.
    """

    frames: list[FramePayload]
    """The File's frames in document order. Always at least one: a File with no
    internal structure composes a single heading-less frame holding a single
    bay, which is what makes `bay_key` a total coordinate."""


@dataclass(frozen=True)
class TextRejection:
    """Explain why captured bytes fail the project's text contract.

    `try_decode_text` returns this value to a format builder instead of raising.
    The builder preserves the bytes as blob facts and turns `detail` into a
    visible bay warning.

    This is a classification result, not a recoverable decoded string or an
    engine failure.
    """

    reason: Literal["nul-byte", "invalid-utf8"]
    """Machine-readable boundary failure chosen by `try_decode_text`.

    Builders may branch on the two exhaustive causes without parsing `detail`;
    the value is not a media type or a recoverable decoder mode.
    """

    detail: str
    """Reviewer-facing explanation of where or why decoding was rejected.

    Builders may place it in a bay warning. Callers must not inspect its prose
    to recover bytes or classify the stable `reason`.
    """


def try_decode_text(data: bytes) -> str | TextRejection:
    """Decode exact file contents as text or return the precise rejection.

    This is the single definition of what this project calls text: no NUL byte,
    and valid UTF-8 with an optional BOM. `Composer.bays` reaches it through the
    flatfile builder. A rejection hands the File to the blob builder; accepted
    text is the value the engine later receives.

    Invalid text returns `TextRejection` rather than raising a decoding error.
    Composition turns that result into blob facts and a visible warning.

    # Usage

    Pass exact captured bytes before constructing a `DiffSide`. Preserve a
    `TextRejection` for the builder to report; do not substitute decoded text.

    # Returns

    - `str`: The complete decoded contents after removing an optional UTF-8
      byte-order mark.
    - `TextRejection`: The exact NUL-byte or invalid-UTF-8 boundary failure;
      callers must represent the File as byte facts instead of using text.

    # Failures

    A NUL byte returns `TextRejection(reason="nul-byte")`. Invalid UTF-8 returns
    `TextRejection(reason="invalid-utf8")`; neither condition raises
    `UnicodeDecodeError`.
    """
    nul_offset = data.find(b"\x00")
    if nul_offset >= 0:
        return TextRejection(
            reason="nul-byte",
            detail=f"NUL byte at byte {nul_offset}",
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        return TextRejection(
            reason="invalid-utf8",
            detail=f"invalid UTF-8 at byte {error.start}",
        )


def media_ref(side: MediaSide) -> MediaRef:
    """Describe one captured media side without carrying its bytes.

    This is the single place a media side's digest is computed, so the frontend
    and review context reconstruction receive the same lowercase SHA-256 value.
    The media endpoint is addressed by Snapshot, File pair, bay, and side. The
    digest describes the returned bytes but is not an endpoint parameter.

    # Usage

    Use this when exact media bytes must become a serializable description.
    Keep `MediaSide` when a caller still needs to serve or inspect the bytes.
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
    size, and the digest of the captured bytes. They are text, so the bay holding
    them is an ordinary `text` bay and the reviewer gets a line diff of those
    facts.

    `None` is a side the File was not captured on, which is how an added or
    removed File is expressed, matching `DiffSide`'s absent side.

    # Usage

    Image and blob builders pass each captured side here, then render the
    returned text through the ordinary text-bay pipeline.

    # Returns

    - `str`: Three newline-separated media facts for a captured side, in type,
      size, and SHA-256 order.
    - `None`: The File has no captured side here. Callers must preserve the
      absence as a missing `DiffSide`, not display empty media facts.
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

    # Usage

    `Composer.compose` calls this for an `ImageBay`. Media-serving code keeps
    the original bay because this result deliberately contains no bytes.
    """
    return {
        "kind": "image",
        "left": None if bay.left is None else media_ref(bay.left),
        "right": None if bay.right is None else media_ref(bay.right),
    }


def text_kind_payload(
    bay: TextBay,
    renderer: DiffEngineProtocol,
) -> tuple[TextKindPayload, EngineWarning | None]:
    """Render one `TextBay`'s content into its serialized wire form.

    This is the one shared text-bay renderer, so ordinary text and every
    per-format text bay reach the engine and display-enrichment pipeline through
    exactly one path. Per-format bays include notebook cell source, image File
    facts, and a blob File's only bay. The function calls the selected engine,
    then `enrich_rows_for_display`, so every text bay uses the same ordered rows,
    decorated parts, half-open fold hints, and bay-local hunk boundaries. The
    engine stays text-only and never learns that bays or formats exist.

    Row `hunk_index` values are bay-local and stay that way. Numbering the
    File's hunks into one sequence is navigation, and the frontend assigns it.

    # Parameters

    - `bay`: Decoded text bay whose identity and sides must be preserved.
    - `renderer`: Selected engine for comparing the bay's two text sides.

    # Usage

    `Composer.compose` calls this once for each yielded `TextBay`, then appends
    any returned engine warning to that bay's format warnings.

    # Returns

    - `First`: The bay's labels, enriched rows, fold hints, and engine summary in
      response form.
    - `Second`: The renderer's reportable limitation for this bay.
    - `None`: The second item is absent when rendering completed without a
      reportable limitation, so the caller adds no warning to the bay.

    # Failures

    Propagates engine failures and display-enrichment invariant errors. The
    function does not select another renderer or return a partial payload.
    """
    rendered = renderer.render_diff(old=bay.left, new=bay.right)
    left_text = "" if bay.left.text is None else bay.left.text
    right_text = "" if bay.right.text is None else bay.right.text
    display = enrich_rows_for_display(
        rows=rendered["rows"],
        left_text=left_text,
        right_text=right_text,
        left_path_hint=bay.left.path_hint,
        right_path_hint=bay.right.path_hint,
    )
    return (
        {
            "kind": "text",
            "left_label": bay.left_label,
            "right_label": bay.right_label,
            "rows": display["rows"],
            "fold_hints": display.get("fold_hints", []),
            "stats": rendered["summary"],
        },
        rendered.get("engine_warning"),
    )
