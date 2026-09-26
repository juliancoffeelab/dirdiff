"""Expose preset, manifest, lazy File, and composed diff routes.

DiffRoutes keeps the HUD capture and File-rendering wire models beside the
handlers that produce them. It selects concrete capture inputs, reads immutable
Snapshots through RoomLord, and delegates composition to dirdiff.formats.

Instances retain the repository registry, Room service, and optional preset
root used by those HTTP entities. This module does not persist review Threads,
serve external agents, manage Profiles, or construct the FastAPI application.
"""

import logging
from http import HTTPStatus
from pathlib import Path
from typing import (
    Annotated,
    Literal,
    Optional,
)
from uuid import UUID

from fastapi import HTTPException, Query
from fastapi.responses import (
    Response,
)
from pydantic import (
    Field,
    Json,
    model_validator,
)

from dirdiff.change_source import (
    LazyReason,
    ManifestPath,
    PresetCatalog,
    PresetCatalogDir,
    build_lazy_info_for_paths,
    build_repo_manifest_for_paths,
    display_name_for_repo_paths,
    file_kind_for_change_type,
)
from dirdiff.db import (
    RepoMarkStore,
)
from dirdiff.engines import (
    DirdiffError,
    EngineConfig,
    InlineTokenStatus,
    PairedMove,
    UnpairedMove,
    engine,
)
from dirdiff.formats import (
    BayContext,
    ComposeContext,
    Composer,
    ImageBay,
)
from dirdiff.rendering import (
    SyntaxClass,
)
from dirdiff.review import (
    FilePair,
)
from dirdiff.room_lord import (
    CapturedFileSide,
    FileMeta,
    Room,
    RoomLord,
)
from dirdiff.server.base import (
    ApiModel,
    ClassRoutes,
    ErrorResponse,
    ManifestSelection,
    PreparedCapture,
    PresetManifestSelection,
    preset_catalog_dirs,
)
from dirdiff.util import Nothing, Option, Some, maybe

__all__ = ["ComposedDiffResponse", "DiffRoutes"]

LOGGER = logging.getLogger(__name__)
"""Record unexpected failures at this HTTP boundary."""

ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
"""Validate source File relationships before HTTP conversion.

- `modify` keeps one File path relationship.
- `add` and `delete` are one-sided.
- `rename` and `copy` retain the source and destination paths.

This value describes File identity, not rendered line status.
"""


class PresetGroupResponse(ApiModel):
    """Return one selectable fixture group from a preset catalog.

    The preset picker shows `display_name` and sends `id` back in a manifest
    request to choose the directory.

    The response contains no fixtures, catalog identity, or File content.
    """

    id: str
    """Catalog-local directory token identifying this fixture group.

    The Preset Tab sends it as `selection.subset` with `selection.catalog`. Consumers
    must not treat the human-readable name as the selection key.
    """

    display_name: str
    """Presentation name read from the fixture group's metadata.

    It may be shown in the picker but does not identify a directory or manifest
    request on its own.
    """


class PresetCatalogResponse(ApiModel):
    """Describe one selectable preset catalog and its fixture groups.

    `/api/presets` returns one value per catalog directory. The picker gets its
    display text, manifest selection token, initial group choice, and offered
    groups from this value.

    This response describes catalog choices only. It contains no fixture paths,
    loaded bytes, or Snapshot identity.
    """

    id: str
    """Preset-root-relative directory token identifying this catalog.

    The HUD sends it as the Preset manifest `project_id`. It is not a repository
    mark id or display name.
    """

    name: str
    """Presentation name for the catalog selector.

    It does not participate in directory lookup. The stable selection token is
    `id` even when the displayed name changes.
    """

    default_preset: str
    """Catalog-local group id the picker should select initially.

    It must correspond to one entry in `groups`. Callers send the id rather than
    copying that group's display name.
    """

    groups: list[PresetGroupResponse]
    """Complete selectable fixture groups for this catalog.

    The catalog supplies their display order. Each group id is meaningful only
    with this catalog, and the HUD must preserve the association.
    """


class DecoratedPartResponse(ApiModel):
    """One ordered, lossless slice of a rendered line.

    Diff-row conversion returns a complete partition for each present side. The
    HUD concatenates `text` in order and applies the supplied diff, syntax, and
    whitespace decoration directly.

    Parts carry no own offsets or review identity. Optional move endpoints
    describe their containing structural move. Text must not be reordered or
    independently edited.
    """

    text: str
    """Exact source characters covered by this decoration slice.

    Concatenating sibling parts in order must reproduce the present side's full
    line text. Consumers must preserve whitespace and empty-looking content.
    """

    syntax_classes: list[SyntaxClass]
    """Renderer syntax categories active across every character in `text`.

    The list may be empty when no syntax category applies. Its order and values
    are presentation metadata, not source-token identity.
    """

    move: PairedMove | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    """Exact structural move endpoints, omitted when the engine supplies none.

    Both spans use one-based source lines and zero-based Unicode columns.
    Edited descendants keep their containing move independently of diff status.
    """

    diff_status: InlineTokenStatus
    """Inline comparison relationship assigned to this source slice.

    The HUD renders it directly within the row-level status. It does not replace
    or determine the enclosing row's alignment status.
    """

    is_whitespace: bool
    """Whether this complete slice contains only whitespace characters.

    The renderer computes the flag from the preserved source text. Consumers use
    it for display and must not strip the slice when true.
    """

    is_leading_whitespace: bool
    """Whether this slice is whitespace before the line's first content.

    True implies `is_whitespace`; later whitespace stays false. The distinction
    lets the HUD present indentation separately without changing source text.
    """


class FoldHintResponse(ApiModel):
    """Return one foldable rendered-row interval to the HUD.

    Text-bay conversion validates renderer fold hints through this model. The
    HUD may hide the zero-based half-open row interval under policy selected by
    `kind` and present `label`.

    A hint does not change row alignment, contain a hunk, or represent current
    DOM expansion state.
    """

    start_row: int
    """First rendered-row index included in the foldable interval.

    The coordinate is zero-based and inclusive. It belongs to one bay's row
    list and must precede `end_row`.
    """

    end_row: int
    """Exclusive rendered-row bound of the foldable interval.

    It is strictly greater than `start_row` and no greater than the enclosing bay's
    row count. Consumers fold exactly the half-open interval.
    """

    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    """Renderer's structural category for the foldable interval.

    The HUD uses the closed vocabulary to choose folding policy and styling. It
    does not alter the interval or imply current expanded state.
    """

    label: str
    """Presentation text describing the hidden structural interval.

    The HUD shows it while rows are folded. It is not a source line, stable
    identity, or navigation coordinate.
    """


class DiffRowResponse(ApiModel):
    """Return one engine row with complete display decoration.

    Each side carries display text and its lossless ordered parts. A source line
    number is present when the engine identifies one for that display row. Git
    word fragments containing only whitespace may have text and parts without
    a number. A genuinely absent side has no number, text, or parts. Current
    renderers encode absent text as an empty string, while the transport model
    also accepts `None`. `status` summarizes the row relationship.
    """

    move: UnpairedMove | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    """Optional unpaired move classification for this complete row.

    The populated source side supplies old/new presentation. `group` selects
    one of two presentation groups and `dimmed` marks unchanged block
    interiors. The value never supplies or implies a counterpart range.
    """

    status: Literal["equal", "replace", "insert", "delete", "move"]
    """Relationship the engine assigned to this aligned source row.

    `equal`, `replace`, and `move` classify unchanged, changed, and moved text.
    Alignment is independent; an unpaired move row has exactly one present side.
    `insert` is right-only and therefore has no left line, while `delete` is
    left-only and has no right line. The HUD uses this closed vocabulary for row
    styling and change accounting; token statuses may still describe finer
    changes within a paired row.
    """

    left_no: int | None
    """One-based left source coordinate identified for this display row.

    `None` means the engine supplied no coordinate. Git word mode may still
    carry a wordless left fragment. A present value identifies source, not
    rendered order, and may repeat across Git word fragments.
    """

    right_no: int | None
    """One-based right source coordinate identified for this display row.

    `None` means the engine supplied no coordinate. Git word mode may still
    carry a wordless right fragment. A present value identifies source, not
    rendered order, and may repeat across Git word fragments.
    """

    left_text: str | None
    """Complete left display text for this row, without a newline.

    Concatenating `left_parts` reproduces this exact string. Physical-line
    engines supply source lines; Git word mode may supply a source fragment or
    Git-authored whitespace. The nullable model also accepts `None` from other
    constructors.
    """

    right_text: str | None
    """Complete right display text for this row, without a newline.

    Concatenating `right_parts` reproduces this exact string. Physical-line
    engines supply source lines; Git word mode may supply a source fragment or
    Git-authored whitespace. The nullable model also accepts `None` from other
    constructors.
    """

    left_parts: list[DecoratedPartResponse]
    """Ordered decoration slices covering the entire left display text.

    Their text concatenates to `left_text` without loss or overlap. An empty
    string has no parts; a Git word fragment may have parts without a line
    number.
    """

    right_parts: list[DecoratedPartResponse]
    """Ordered decoration slices covering the entire right display text.

    Their text concatenates to `right_text` without loss or overlap. An empty
    string has no parts; a Git word fragment may have parts without a line
    number.
    """
    hunk_index: int | None
    """
    Zero-based bay-local hunk identity on a hunk's first rendered row.

    Other rows carry `None`. Display enrichment numbers each bay's own rows from
    zero, so a hunk coordinate is this index plus the enclosing bay's key; the
    renderer never numbers a File as a whole. The value is assigned before
    frontend folding and virtualization and is therefore independent of DOM
    layout.
    """


class DiffSummaryResponse(ApiModel):
    """Return File-wide line totals and side existence.

    Composed File conversion validates the aggregate engine counts and captured
    side existence through this model. The HUD uses it for File-level totals.

    Counts cover text bays only. They do not describe repository-wide totals or
    image differences; existence distinguishes an absent side from an empty
    File.
    """

    changed_lines: int
    """Number of non-equal aligned rows across all rendered text bays.

    It is the File-wide aggregate of modified, added, removed, and moved row
    accounting supplied by the renderer, not a repository total.
    """

    modified_lines: int
    """Count of changed rows with both left and right source lines.

    The value covers the File's text bays only. It excludes inserted, deleted,
    and line-level moved rows counted in their own fields.
    """

    added_lines: int
    """Count of rendered rows that have only a right source line.

    It aggregates text bays for this File and does not include repository
    additions that the renderer did not render as lines.
    """

    removed_lines: int
    """Count of rendered rows that have only a left source line.

    It aggregates text bays for this File and does not count an absent File side
    independently of the renderer's produced rows.
    """

    moved_lines: int = 0
    """Count of rows the selected engine classified as moved.

    Structural engines may derive this count from inline move facts. Each
    aligned position contributes once to `changed_lines` accounting.
    """

    left_exists: bool
    """Whether capture retained a left File side.

    True includes an empty file whose renderer produced no rows. False denotes
    side absence and must not be inferred from line totals.
    """

    right_exists: bool
    """Whether capture retained a right File side.

    True includes an empty file whose renderer produced no rows. False denotes
    side absence and must not be inferred from line totals.
    """


class RepoDiffSummaryResponse(ApiModel):
    """Validate manifest-wide File totals and optional source line totals.

    Added and removed line counts are either both source-reported integers or
    both absent. They need not include additional untracked Files. The cell-summary fields are currently absent from manifest payloads.
    """

    changed_files: int
    """Total affected File relationships reported for the manifest.

    It equals `added_files + removed_files + updated_files`, including untracked
    Files that became manifest leaves. Skipped entries are counted separately.
    """

    added_files: int
    """Count of affected File pairs with only a right side.

    Manifest construction counts right-only path pairs, including additional
    untracked entries. Rendering does not change this count.
    """

    removed_files: int
    """Count of affected File pairs with only a left side.

    Manifest construction counts left-only path pairs. The value is independent
    of how many deleted source rows an engine later produces.
    """

    updated_files: int
    """Count of affected File relationships present on both sides.

    Modified, renamed, and copied Files contribute here. The value does
    not claim the side paths are equal.
    """

    added_lines: Optional[int]
    """Source-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` always have equal presence.
    """

    removed_lines: Optional[int]
    """Source-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` always have equal presence.
    """

    skipped_files: int
    """Count of source entries deliberately omitted from the manifest.

    Invalid path pairs fail the build instead of incrementing this value; lazy
    Files remain included.

    *Implementation detail:* Current builders omit no Files and report zero.
    """

    changed_cells: int | None = None
    """Aggregate count of notebook cells with any reported change.

    Current manifest payloads omit this field, so it remains `None`. It must
    not be presented as zero changed cells.
    """

    added_cells: int | None = None
    """Aggregate count of notebook cells present only on the right.

    Current manifest payloads omit this field. Consumers must preserve `None`
    instead of presenting an unavailable count as zero.
    """

    removed_cells: int | None = None
    """Aggregate count of notebook cells present only on the left.

    Current manifest payloads omit this field. Rendering does not derive a
    manifest-wide count from composed notebook bays.
    """

    modified_cells: int | None = None
    """Aggregate count of paired notebook cells whose content changed.

    Current manifest payloads omit this field. Consumers retain `None` rather
    than infer a count from moved or modified notebook bays.
    """

    @model_validator(mode="after")
    def validate_line_count_presence(self) -> RepoDiffSummaryResponse:
        """Require repository added and removed line totals as one unit.

        Both source totals must be integers or both must be unavailable. Return
        the summary unchanged after rejecting a partial pair.

        # Usage

        Pydantic invokes this callback after validating repository summary
        fields. Callers validate `RepoDiffSummaryResponse` as one complete value.

        # Failures

        - Raises `ValueError` when exactly one repository line total is present.
        """
        if (self.added_lines is None) != (self.removed_lines is None):
            raise ValueError(
                "added_lines and removed_lines must have equal presence"
            )
        return self


class TrackedFileKindResponse(ApiModel):
    """Return tracked VCS content and its File change status.

    Manifest and composed File responses share this contract. The discriminator
    does not identify a particular VCS.

    *Implementation detail:* preset fixtures use this variant because VCS
    tracking does not apply to them.
    """

    type: Literal["tracked"]
    """Select the tracked content variant."""

    status: Literal["modified", "added", "deleted", "renamed", "copied"]
    """Source-reported File relationship, independent of rendered line changes."""


class UntrackedFileKindResponse(ApiModel):
    """Return classification for content not tracked by the VCS.

    The source supplied this fact. Paths and lazy policy remain on the enclosing
    File response, and no tracked change status accompanies this variant.
    """

    type: Literal["untracked"]
    """Select content not tracked by the VCS."""


FileKindResponse = TrackedFileKindResponse | UntrackedFileKindResponse
"""Return source-independent File classification to the HUD.

- `TrackedFileKindResponse` includes the tracked File change status.
- `UntrackedFileKindResponse` identifies content not tracked by the VCS.

Consumers branch on `type` without inferring a VCS implementation. This union
is not a File identity, loading state, or rendered diff classification.
"""


class BayWarningResponse(ApiModel):
    """Return one visible degradation attached to a usable bay.

    Format conversion preserves engine and builder warnings through this common
    shape. `type` is stable for consumers; `message` explains the exact damage.

    A warning cannot stand in for missing or invalid bay output.
    """

    type: str = Field(min_length=1)
    """Non-empty warning category supplied by the engine or format builder.

    Consumers may group known categories without parsing `message`. A warning
    records degraded usable output, not a successful result kind.
    """

    message: str
    """Concrete explanation of the damage affecting this bay.

    The HUD may present it directly. Behavior must use the warning category or
    kind data rather than depend on wording.
    """


class BayStatsResponse(ApiModel):
    """Per-bay engine line counts, before File-level aggregation.

    Text-kind conversion validates one engine summary through this model; the
    HUD shows it on the corresponding bay.

    It counts that bay's rows only. Side existence is a File fact on the File
    summary, and repository totals remain outside this response.
    """

    changed_lines: int
    """Number of non-equal aligned rows in this text bay.

    It summarizes only the enclosing bay and feeds File aggregation. The value is
    not a source-line span or repository-wide count.
    """

    modified_lines: int
    """Count of changed bay rows with both source sides present.

    Inserted, deleted, and line-moved rows use their own counts. Inline token
    changes do not add another row to this total.
    """

    added_lines: int
    """Count of bay rows containing only a right source line.

    It comes from the engine alignment for this bay. It does not state that the
    complete File exists only on the right.
    """

    removed_lines: int
    """Count of bay rows containing only a left source line.

    It comes from the engine alignment for this bay. It does not state that the
    complete File exists only on the left.
    """

    moved_lines: int = 0
    """Count of rows this bay's engine classified as moved.

    Inline token movement is excluded. Engines without line-move output use the
    zero default, which still participates in File aggregation.
    """


class ChangeStatusResponse(ApiModel):
    """Return the semantic outcome of a bay that did not move.

    Moved bays use `MovedChangeStatusResponse`. This value does not classify
    individual rows.
    """

    kind: Literal["added", "removed", "changed", "unchanged"]
    """Whole-bay semantic outcome decided by the format builder.

    `added` and `removed` are one-sided, `changed` retains document position
    with different content, and `unchanged` reports no semantic change.
    """


class MovedChangeStatusResponse(ApiModel):
    """Return the old and new headings of one moved bay.

    Format builders use this variant when semantic document position changed.
    Either heading may be absent when that side has no useful name.

    Movement does not imply unchanged content; the bay's rows may still show an
    edit. This value carries no numeric position.
    """

    kind: Literal["moved"]
    """Select the semantic status for a bay whose frame position changed.

    Only the literal `moved` is valid. Content may also differ, so consumers must
    still render the bay's own rows or media.
    """

    from_heading: str | None
    """Old frame heading that located the bay on the left side.

    `None` means that side had no useful heading. It does not mean the left File
    side or bay was absent.
    """

    to_heading: str | None
    """New frame heading that locates the bay on the right side.

    `None` means that side has no useful heading. Callers present absence rather
    than substituting `from_heading`.
    """


class TextKindResponse(ApiModel):
    """What a `text` bay holds: decorated rows from the shared text renderer.

    The `text` discriminator sends this variant to the HUD text grid. Side
    labels name its columns; rows, fold hints, and stats come from the shared
    renderer. Hunk indexes are bay-local.

    Engine and format warnings stay on the bay envelope. This value contains no
    File identity, expansion policy, or image bytes.
    """

    kind: Literal["text"]
    """Select line-oriented rendering for this bay's content.

    Only the literal `text` is valid. Consumers may then use rows, fold hints,
    and line statistics; the enclosing bay retains identity and warnings.
    """

    left_label: str
    """Presentation heading for the text bay's left-side column.

    It may name a notebook cell source or another format-local side. It is not a
    captured path, bay key, or proof that a left row exists.
    """

    right_label: str
    """Presentation heading for the text bay's right-side column.

    It is format-authored independently of `left_label` and must not be used as
    a side identity or review coordinate.
    """

    rows: list[DiffRowResponse]
    """Complete aligned and decorated rows for this bay.

    Order is the renderer's source order and defines row coordinates used by
    fold hints. Consumers must not regroup rows by status.
    """

    fold_hints: list[FoldHintResponse] = Field(default_factory=list)
    """Renderer-provided half-open intervals eligible for folding.

    Every interval indexes `rows`. An empty list means no structural folds were
    supplied and does not authorize the HUD to invent them.
    """

    stats: BayStatsResponse
    """Line-status totals computed from this bay's engine result.

    They summarize `rows` before File aggregation. They contain no File-side
    existence or image-byte facts.
    """

    interleave_inline_rows: bool
    """Whether the inline grid interleaves a changed equal row's sides into one line.

    True, which Git word mode sets, means each row is one line the tool printed
    and the inline grid interleaves an equal row's two sides back into it. False
    means rows are aligned physical source lines shown one line per side. It is
    required so the HUD never guesses the row shape from content.
    """


class MediaRefResponse(ApiModel):
    """One composed image side, described without its bytes.

    Image-kind conversion sends this value to the HUD so its widget can request
    the exact side from `/api/file-media`.

    The reference contains no bytes, dimensions, captured path, or authorization
    to read a local file. Snapshot, File pair, bay key, and side address the
    media route.
    """

    media_type: str = Field(min_length=1)
    """Non-empty media type of the composed picture bytes.

    `/api/file-media` returns the same value as `Content-Type` for this side.
    Consumers must not redetect the format from path extension.
    """

    byte_size: int = Field(ge=0)
    """Length of the immutable composed media payload.

    Zero is valid if the format builder produced such a reference. The value is
    a byte count, not decoded dimensions or transfer size.
    """

    digest: str = Field(min_length=1)
    """SHA-256 digest computed from the exact media bytes.

    The non-empty lowercase hexadecimal value changes with content and lets the
    HUD compare side facts. It is not an HTTP address by itself.
    """


class ImageKindResponse(ApiModel):
    """What an `image` bay holds: two optional picture references.

    The `image` discriminator sends this variant to the HUD image widget. A
    reference may be absent because the File side is absent or because a
    notebook output has no PNG there. The widget requests present sides from
    `/api/file-media`.

    It contains no bytes or dimensions and must not turn an absent side into an
    empty picture.
    """

    kind: Literal["image"]
    """Select picture rendering for this bay's content.

    Only the literal `image` is valid. Consumers request bytes for each present
    side and do not expect text rows or fold hints in this variant.
    """

    left: MediaRefResponse | None = None
    """Facts describing the old picture side, if present.

    `None` means no old image representation exists, not an empty or failed
    image. A present reference needs Snapshot, File, and bay context to address
    its bytes.
    """

    right: MediaRefResponse | None = None
    """Facts describing the new picture side, if present.

    `None` means no new image representation exists. At least one side is
    present for a valid image bay, and callers must preserve the absent state.
    """


BayKindResponse = Annotated[
    TextKindResponse | ImageKindResponse,
    Field(discriminator="kind"),
]
"""The content of one bay, discriminated by the widget that renders it.

- `TextKindResponse` supplies decorated lines, folds, labels, and statistics.
- `ImageKindResponse` supplies references for present picture sides.

Named byte facts remain text because reviewers read them as lines. Consumers
dispatch on `kind`; this union does not contain bay identity, frame placement,
change status, or warnings, which remain on `BayResponse`. A new kind must
represent content that cannot honestly be read as lines and requires a matching
frontend variant; frames and the bay envelope do not change to admit it.
"""


class BayResponse(ApiModel):
    """Expose one bay's public coordinate, presentation, and content.

    Its stable sub-File coordinate is shared with line pins and review targets.
    Format-authored text names the whole bay in its placeholder and inline-grid
    content column.

    Only the format builder reports what happened to the bay. A notebook cell
    that moved and one whose output changed beyond its rendered text may have
    equal rows, so nothing downstream can recover that status.

    The widget discriminator stays inside the content value. Placement,
    identity, expansion, and status consumers never need to know the widget kind.
    """

    bay_key: str = Field(min_length=1)
    """Stable public sub-File coordinate used by navigation and review.

    Its meaning is scoped by the enclosing File pair; clients return it unchanged
    for line pins and Thread targets rather than deriving it from labels or order.
    """

    label: str
    """Server-authored name shown for the bay as a whole.

    It is presentation text and may repeat across bays, so callers must not use it
    as the stable coordinate.
    """

    detail: str | None = None
    """Optional secondary server-authored description.

    Null means the format supplied no additional text; consumers omit the detail
    instead of substituting change status or widget metadata.
    """

    collapsible: bool
    """Whether the reviewer may hide this bay's body.

    False makes the body permanently present and therefore makes initial expansion
    policy non-actionable.
    """

    default_expanded: bool
    """Initial expansion state when `collapsible` is true.

    It seeds presentation only; later reviewer interaction remains frontend state
    and is not written back into this response.
    """

    change: Annotated[
        ChangeStatusResponse | MovedChangeStatusResponse,
        Field(discriminator="kind"),
    ]
    """Format-authored semantic outcome for this bay.

    The HUD presents it directly because row equality cannot recover moves or
    format-specific changes; widget content must not reinterpret it.
    """

    warnings: list[BayWarningResponse]
    """Visible non-fatal damage confined to this bay.

    An empty list means composition completed without localized warnings, while
    entries preserve usable content rather than turning the whole File into failure.
    """

    kind_data: BayKindResponse
    """Kind-specific content consumed only by widget dispatch.

    Its discriminator selects text or image rendering; identity, placement, and
    semantic outcome remain on the enclosing bay.
    """


class FrameResponse(ApiModel):
    """One presentational frame: an optional heading over ordered bays.

    Composed File conversion groups contiguous bays with the same frame identity
    into this response. The HUD renders the optional heading over bays in
    document order.

    A frame carries no change state, navigation target, or Comment coordinate of
    its own. Those belong to bays.
    """

    frame_key: str = Field(min_length=1)
    """Stable frame identity emitted by the format builder.

    Contiguous bays sharing it compose into this frame; it is not a review or
    navigation coordinate by itself.
    """

    heading: str | None = None
    """Server-authored frame heading, or `None` for a heading-less frame.

    Absence instructs the HUD to render no heading and must not be replaced with
    a File name or frame key.
    """

    bays: list[BayResponse]
    """Bays contained by the frame in document order.

    Their order is format-authored and consumers must not regroup them by widget
    kind, change status, or review activity.
    """


class ComposedDiffResponse(ApiModel):
    """The single `/api/file-diff` response shape: one composed diff.

    `/api/file-diff` validates the complete composition through this model. The
    HUD receives File metadata followed by frames and their bays in document
    order; a plain text File is one heading-less frame with one text bay.

    There is no File-wide render discriminator. Bay content chooses its widget,
    and this response contains no captured media bytes or review discussion
    state.
    """

    display_name: str
    """Manifest-authored human-readable name of the File pair.

    It is presentation text only; exact nullable repository paths remain the
    address used by File and review endpoints.
    """

    left_label: str
    """Human-readable label for the complete left selected state.

    Individual text bays may supply their own column labels, so this value names
    the File envelope rather than every widget column.
    """

    right_label: str
    """Human-readable label for the complete right selected state.

    It remains valid when the File's right path is absent because it describes
    the selected state, not side presence.
    """

    left_path: str | None = None
    """Repository-relative left File path, or `None` when absent.

    At least one side path exists for a valid File pair; consumers preserve
    absence instead of substituting the right path or an empty string.
    """

    right_path: str | None = None
    """Repository-relative right File path, or `None` when absent.

    Together with `left_path` it is the exact manifest identity accepted by
    follow-up operations, not merely a display label.
    """

    file_kind: FileKindResponse
    """VCS tracking copied from manifest metadata.

    Composition retains this capture-time classification and does not infer it
    from rendered content or path presence.
    """

    summary: DiffSummaryResponse
    """File-wide text-row totals and captured-side existence.

    The aggregate spans composed text bays; image-only content contributes side
    existence without inventing line counts.
    """

    default_expanded: bool = True
    """Whether the HUD initially opens the File body.

    This is initial presentation policy only; it does not persist or override a
    reviewer's later explicit expansion choice.
    """

    frames: list[FrameResponse]
    """Complete composed document in format-defined order.

    Frames and their bays are the authoritative presentation sequence; consumers
    must not reconstruct a File-wide widget discriminator from them.
    """


class RepoFileEntryResponse(ApiModel):
    """Return one manifest File leaf before it is arranged into the tree.

    Manifest conversion places this value on one File tree leaf. The HUD uses
    its exact path pair and VCS tracking for later File operations.

    At least one side path is present. `lazy` explains deferred loading or is
    `None` for ordinary loading. The entry has no captured path, display name,
    or rendered content.
    """

    file_kind: FileKindResponse
    """VCS tracking and source change classification.

    The HUD passes this capture-time metadata to File loading and must not derive
    it from path nullability or lazy policy.
    """

    left_path: str | None = None
    """Repository-relative left path, or `None` when absent.

    Absence is a real side-presence fact; at least one of the two paths is present
    and callers address the File by the exact pair.
    """

    right_path: str | None = None
    """Repository-relative right path, or `None` when absent.

    It may differ from the left path for renames and must not be normalized into
    a single path before follow-up requests.
    """

    # TODO: Lazy-info contains deferred Files only, so require the concrete
    # reason that `build_lazy_info_for_paths` always supplies.
    lazy: LazyReason | None = None
    """Reason to defer File rendering, or `None` for eager loading.

    Null means ordinary loading policy, not an unknown error; a present reason
    instructs the HUD to construct a deferred File placeholder.
    """


class RepoManifestFileNodeResponse(ApiModel):
    """Return one named File leaf in the recursive manifest tree.

    Tree consumers distinguish the leaf, show its final path component, and
    pass its exact File metadata to later File operations.

    A File node has no children and is not the File's standalone identity.
    """

    type: Literal["file"]
    """Discriminator for a manifest tree leaf.

    Consumers use it to stop recursion and read `entry`; it never denotes a
    directory whose children happen to be empty.
    """

    name: str
    """Final path component displayed for this File.

    It is presentation derived from the manifest path and is not sufficient to
    identify Files with the same basename in different directories.
    """

    entry: RepoFileEntryResponse
    """Exact side paths, VCS tracking, and loading policy for the File.

    Tree placement supplies hierarchy only; callers retain this complete value
    for later File requests and placeholder construction.
    """


class RepoManifestDirectoryNodeResponse(ApiModel):
    """Return one directory in the recursive manifest tree.

    The HUD uses the complete repository-relative path as directory identity,
    shows its possibly compacted label, and renders children recursively in
    supplied order.

    The node organizes Files only. It carries no content or aggregate summary.
    """

    type: Literal["directory"]
    """Discriminator for a manifest tree branch.

    Consumers recurse through `entries`; the node itself is not a loadable File
    and cannot be supplied to File endpoints.
    """

    name: str
    """Displayed directory name, possibly compacting a single-child chain.

    Because compaction may include separators, consumers use `path` rather than
    this label for stable directory identity.
    """

    path: str
    """Complete repository-relative directory identity.

    It remains stable regardless of compacted display text and is the value used
    for directory expansion state.
    """

    entries: list[RepoManifestTreeEntryResponse]
    """File and directory children in source-supplied order.

    The order reflects manifest construction; the HUD must not sort it and thereby
    separate it from source File sequence.
    """


RepoManifestTreeEntryResponse = (
    RepoManifestFileNodeResponse | RepoManifestDirectoryNodeResponse
)
"""Return one node in the recursive manifest tree.

- `RepoManifestFileNodeResponse` terminates a branch with a File.
- `RepoManifestDirectoryNodeResponse` contains further nodes.

Consumers branch on `type`. The union is tree structure, not a filesystem
handle.
"""


class LazyInfoFileResponse(ApiModel):
    """Return everything the HUD needs to construct one deferred File card.

    Lazy-info conversion produces one value for each deferred File. The HUD uses
    it to construct a placeholder without copying fields from the manifest.

    The exact path pair identifies the File within its Snapshot. VCS tracking,
    display name, known counts, and reason describe the placeholder. It contains
    no captured bytes, frames, or bays.
    """

    # This response must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    file_kind: FileKindResponse
    """VCS tracking and source change classification.

    The deferred placeholder receives the same manifest fact as a later loaded
    File, so loading must not change classification.
    """

    left_path: str | None = None
    """Repository-relative left path, or `None` when absent.

    Together with the right path it identifies exactly the deferred manifest File;
    null remains distinct from an empty File side.
    """

    right_path: str | None = None
    """Repository-relative right path, or `None` when absent.

    Rename pairs retain both paths so explicit loading can address the same capture.
    """

    display_name: str
    """Human-readable name of the deferred File pair.

    The HUD may show it on the placeholder, but later File requests still use the
    nullable path pair rather than this label.
    """

    changed_lines: int | None = None
    """Changed-line count, `None` until the File is rendered.

    Null means the deferred metadata cannot state the value, not zero changed
    lines; consumers preserve the unknown state.
    """

    added_lines: int | None = None
    """Added-line count, `None` until the File is rendered.

    It is supplied only when authoritative placeholder metadata exists and must
    not be inferred from File kind or side presence.
    """

    removed_lines: int | None = None
    """Removed-line count, `None` until the File is rendered.

    Consumers distinguish an unknown null from an authoritative zero when showing
    deferred summary information.
    """

    lazy: LazyReason | None = None
    """Reason for deferred loading; current lazy-info entries carry one.

    The optional model shape follows manifest metadata, but conversion currently
    emits only deferred Files and therefore supplies a concrete reason.
    """


class RepoManifestResponse(ApiModel):
    """Return one complete manifest and its retained Snapshot address.

    `snapshot_id` is exactly 32 lowercase hexadecimal characters and is the
    only Room lookup input accepted by follow-up endpoints. The HUD uses the
    labels, summary, and tree to build File navigation before loading content.

    The manifest contains File identity and loading metadata, not captured
    bytes, composed bays, or review discussions.
    """

    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """Opaque retained Snapshot key used by every follow-up endpoint.

    Clients must return it unchanged; it selects the Room and immutable capture
    without exposing a repository path or correspondence key.
    """

    display_name: str
    """Human-readable name of the complete Tab.

    It labels the Tab but is not an identity and may be shared by distinct Rooms
    or recaptures.
    """

    left_label: str
    """Human-readable label for the captured left side.

    It is retained with the Snapshot and does not imply that every File has a
    present left path.
    """

    right_label: str
    """Human-readable label for the captured right side.

    The label describes the selected state even when an individual File is
    removed and has no right-side content.
    """

    summary: RepoDiffSummaryResponse
    """Repository-wide File totals and optional source line totals.

    File counts are manifest-authoritative while nullable line totals remain
    unknown when the source deferred or could not calculate them.
    """

    tree: list[RepoManifestTreeEntryResponse]
    """Every affected File arranged in the recursive directory tree.

    Each manifest File appears once, grouped by its displayed path. Directories
    precede Files at each level, and single-child directory chains are compacted.
    The tree contains metadata, not composed File contents.
    """


class LazyInfoResponse(ApiModel):
    """Return deferred File-card metadata for one Snapshot.

    The HUD consumes `files` after manifest loading to create placeholders for
    every File awaiting explicit reviewer action.

    The response contains no eager File entries, captured bytes, or rendered
    bays.
    """

    files: list[LazyInfoFileResponse]
    """Deferred File entries in the manifest's source path order.

    The collection excludes eager Files and lets the HUD create placeholders
    without joining partial data from the manifest tree.
    """


class ManifestQuery(ApiModel):
    """Validate one complete Tab selection encoded as a JSON query value.

    `/api/manifest` accepts only `selection`. The selected Tab requires its own
    controls and rejects unrelated controls before source preparation runs.

    """

    selection: Json[ManifestSelection]
    """JSON object selecting the Tab and all required source inputs."""


def captured_manifest_paths(
    room: Room,
    snapshot_id: UUID,
    *,
    preset: bool,
) -> tuple[ManifestPath, ...]:
    """Convert captured File metadata to ordered manifest presentation records.

    Manifest and lazy-info use the same path conversion, display labels, and
    ordering. The caller supplies `preset` from metadata it already read; this
    operation reads only the specified Snapshot's File metadata.

    # Parameters

    - `room`: Room containing the specified Snapshot.
    - `snapshot_id`: Exact captured state whose Files are presented.
    - `preset`: Whether the Snapshot's Tab uses fixture new-path display names.

    # Returns

    Presentation records sorted by display name and then change type. Nullable
    sides, VCS tracking, and lazy policy retain their captured values.
    Each returned record has these side fields:

    - `left_path`: Captured old-side relative path, absent for an added File.
    - `right_path`: Captured new-side relative path, absent for a deleted File.

    # Failures

    - Invalid Snapshot identity raises `DirdiffError` at the Room boundary.
    - Metadata read and path-label validation failures propagate.
    """
    paths: list[ManifestPath] = []
    for left, right, metadata in room.manifested(snapshot_id):
        left_path = left.map(Path.as_posix)
        right_path = right.map(Path.as_posix)
        display_name = (
            right_path.value
            if preset and Some.present(right_path)
            else display_name_for_repo_paths(left_path, right_path)
        )
        paths.append(
            ManifestPath(
                left_path=left_path,
                right_path=right_path,
                display_name=display_name,
                change_type=metadata["change_type"],
                lazy_reason_override=metadata["lazy_reason_override"],
                untracked=metadata["untracked"],
            )
        )
    return tuple(
        sorted(paths, key=lambda path: (path.display_name, path.change_type))
    )


class FileDiffQuery(ApiModel):
    """Address one captured File and its complete renderer configuration.

    Engine JSON validates the selected variant before capture lookup. Unknown
    fields are rejected. Absent paths represent added or deleted File sides.
    """

    snapshot_id: str
    """Opaque Snapshot id returned by manifest."""
    engine: Json[EngineConfig]
    """Complete JSON rendering choice, including Git's required algorithm."""
    left_path: str | None = None
    """Exact old path, absent only for an added File."""
    right_path: str | None = None
    """Exact new path, absent only for a deleted File."""


class DiffRoutes:
    """Bind capture and composed-diff handlers to application interfaces.

    One instance retains the repository registry, Room service, and preset root
    used to capture or read Snapshots. The route group owns no captured bytes,
    source state, or rendered result after an HTTP entity completes.
    """

    routes = ClassRoutes()
    """Import-time declarations bound to one route-group instance."""

    def __init__(
        self,
        db: RepoMarkStore,
        *,
        room_lord: RoomLord,
        presets_root: str,
        store_path: Path,
    ) -> None:
        """Retain the interfaces required by capture and File routes.

        # Parameters

        - `db`: Repository registry used by Snapshot capture.
        - `room_lord`: Room selection and Snapshot lookup interface.
        - `presets_root`: Exact root rescanned for preset catalogs.
        - `store_path`: Configured Snapshot root, matching RoomLord; source
          setup checks physical placement against it.
        """
        self.db = db
        self.room_lord = room_lord
        self.presets_root = presets_root
        self.store_path = store_path

    def preset_catalog_response(
        self,
        catalog: PresetCatalogDir,
    ) -> PresetCatalogResponse:
        """Serialize one preset catalog for the presets endpoint.

        Preset catalog selection and engine selection are independent UI
        controls, so this endpoint exposes catalog metadata without involving a
        renderer.

        # Usage

        Map each value from `preset_catalog_dirs` through this helper when
        building the preset-list response.
        """
        preset_catalog = PresetCatalog(catalog.root)
        return PresetCatalogResponse.model_validate(
            {
                "id": catalog.catalog_id,
                "name": catalog.name,
                "default_preset": preset_catalog.default_preset_name(),
                "groups": preset_catalog.list_preset_groups(),
            }
        )

    def render_loaded_snapshot_file(
        self,
        *,
        room: Room,
        snapshot_id: UUID,
        engine_config: EngineConfig,
        pair: FilePair,
        left_file: Option[CapturedFileSide],
        right_file: Option[CapturedFileSide],
        file_meta: FileMeta,
    ) -> ComposedDiffResponse:
        """Compose one focused File into its `/api/file-diff` response payload.

        The handler does HTTP work only: it checks the capture error, reads the
        two captured byte sides, builds one `ComposeContext`, calls `compose()`,
        and attaches the two envelope fields composition does not produce -- the
        File's display name and file kind, which the manifest already settled.
        Decoding, engine selection, and payload assembly belong to composition.

        # Parameters

        - `room`: Room supplying retained labels and Tab presentation rules.
        - `snapshot_id`: Exact Snapshot containing the loaded File.
        - `engine_config`: Complete rendering configuration for text bays.
        - `pair`: Exact optional repository paths identifying the File.
        - `left_file`: Authenticated captured left side, or `Nothing` when absent.
        - `right_file`: Authenticated captured right side under the same rule.
        - `file_meta`: Persisted VCS tracking, change type, lazy policy, and
          capture failure for this pair.

        # Usage

        Call after `Room.get` validates the exact File pair. Return the
        validated model directly rather than reshaping composed frames or bays.

        # Failures

        - Raises `DirdiffError` when capture recorded a File failure.
        - Propagates captured-file I/O, engine, composition, and response
          validation failures.
        """
        if file_meta["capture_error"] is not None:
            raise DirdiffError(file_meta["capture_error"])
        snapshot_meta = room.meta(snapshot_id)
        left_bytes = left_file.map(lambda side: side.path.read_bytes())
        right_bytes = right_file.map(lambda side: side.path.read_bytes())
        left_link = left_file.and_then(
            lambda side: (
                Some(side.link) if side.kind == "symlink" else Nothing()
            )
        )
        right_link = right_file.and_then(
            lambda side: (
                Some(side.link) if side.kind == "symlink" else Nothing()
            )
        )
        context = ComposeContext.build(
            left_path=pair.left_path,
            right_path=pair.right_path,
            left_label=snapshot_meta["left_label"],
            right_label=snapshot_meta["right_label"],
            left_link=left_link,
            right_link=right_link,
            renderer=engine(engine_config),
        )
        composed = Composer().compose(left_bytes, right_bytes, context)
        # TODO: the frontend should probably take the display name and file kind
        # from the manifest, which already emits one per File. Until then the
        # HTTP boundary attaches both here: the two envelope fields composition
        # deliberately does not produce.
        file_kind: Literal["tracked", "untracked"] = (
            "untracked" if file_meta["untracked"] else "tracked"
        )
        display_name = (
            pair.right_path.value
            if snapshot_meta["tab"] == "preset"
            and Some.present(pair.right_path)
            else display_name_for_repo_paths(pair.left_path, pair.right_path)
        )
        return ComposedDiffResponse.model_validate(
            {
                **composed,
                "display_name": display_name,
                "file_kind": file_kind_for_change_type(
                    file_meta["change_type"],
                    file_kind=file_kind,
                ),
            }
        )

    @routes.get(
        "/api/presets",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load grouped preset metadata",
    )
    def serve_presets(self) -> list[PresetCatalogResponse]:
        """List current preset catalogs and their selectable groups.

        The root is rescanned for every call, so hot-added catalogs appear
        without restarting the server. Invalid metadata is reported as a bad
        request; unexpected catalog failures are logged and contained.

        # Failures

        - Raises `HTTPException` with status 400 for invalid preset metadata, or
          status 500 after logging an unexpected catalog failure.
        """
        try:
            return [
                self.preset_catalog_response(catalog)
                for catalog in preset_catalog_dirs(self.presets_root)
            ]
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Preset catalog request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

    @routes.get(
        "/api/manifest",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Show the repository state selected by a Tab",
    )
    def serve_manifest(
        self,
        query: Annotated[ManifestQuery, Query()],
    ) -> RepoManifestResponse:
        """Capture a complete selection and return its manifest and Snapshot key.

        # Parameters

        - `query`: One JSON `selection` value with the selected Tab's required
          controls. Pull Request revisions must already have been prepared.

        # Usage

        Submit the selected Tab object to `/api/manifest`. Keep its returned
        `snapshot_id` for lazy-info, File rendering, and review operations; those
        operations read this Snapshot rather than repeating source preparation.

        Encode the complete object, not its individual fields, in URLSearchParams:

        ```javascript
        const selection = {
          tab: "refs", project_id: 42,
          left: "HEAD", right: "worktree", show_untracked: false,
        };
        const query = new URLSearchParams({ selection: JSON.stringify(selection) });
        const response = await fetch(`/api/manifest?${query}`);
        ```

        The project id must identify an active Mark. A successful response supplies
        the `snapshot_id` used by `/api/lazy-info` and `/api/file-diff`.

        # Failures

        - FastAPI returns 422 for missing, malformed, or unrelated selection
          fields before this handler runs.
        - Source preparation and capture `DirdiffError` failures return 400.
        - Unexpected preparation or persistence failures are logged and return 500.
        """
        try:
            prepared: PreparedCapture = query.selection.prepare(
                self.db,
                self.presets_root,
                store_path=self.store_path,
            )
            room, snapshot_id = self.room_lord.corresponding_room(
                mark_id=prepared.mark_id,
                selection=prepared.correspondence,
                source=prepared.source,
                left_label=prepared.left_label,
                right_label=prepared.right_label,
            )
            snapshot_meta = room.meta(snapshot_id)
            manifest_paths = captured_manifest_paths(
                room, snapshot_id, preset=snapshot_meta["tab"] == "preset"
            )
            manifest = build_repo_manifest_for_paths(
                left_label=snapshot_meta["left_label"],
                right_label=snapshot_meta["right_label"],
                paths=manifest_paths,
                added_lines=snapshot_meta["added_lines"],
                removed_lines=snapshot_meta["removed_lines"],
            )
            if isinstance(query.selection, PresetManifestSelection):
                manifest["display_name"] = query.selection.subset
            payload = {"snapshot_id": snapshot_id.hex, **manifest}
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Manifest request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return RepoManifestResponse.model_validate(payload)

    @routes.get(
        "/api/lazy-info",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load repository lazy file metadata",
    )
    def serve_lazy_info(
        self,
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
    ) -> LazyInfoResponse:
        """Return delayed-file metadata from one manifest Snapshot.

        `snapshot_id` is the sole Room lookup input. The containing Room's
        persisted Tab supplies presentation behavior; no live Git, worktree,
        index, or preset state is read.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid or unknown
          Snapshot id.
        - Logs unexpected persistence or manifest failures and raises status 500.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            room = self.room_lord.find_room(snapshot_key)
            snapshot_meta = room.meta(snapshot_key)
            lazy_paths = captured_manifest_paths(
                room, snapshot_key, preset=snapshot_meta["tab"] == "preset"
            )
            payload = build_lazy_info_for_paths(paths=lazy_paths)
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Lazy info request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return LazyInfoResponse.model_validate(payload)

    @routes.get(
        "/api/file-diff",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load a single file diff",
    )
    def serve_file_diff(
        self,
        params: Annotated[FileDiffQuery, Query()],
    ) -> ComposedDiffResponse:
        """Render one exact filepath pair from a manifest Snapshot.

        The opaque key finds the containing Room and is passed again with the
        repository-relative paths to select the exact captured File. Rendering
        reads only the returned absolute capture paths; it never reloads the
        live source.

        # Parameters

        - `params`: Captured File identity and validated complete engine choice.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid Snapshot id or
          File pair, missing capture, stored capture error, or rejected rendering.
        - Logs unexpected I/O, persistence, or response failures and raises
          status 500.
        """
        try:
            try:
                snapshot_key = UUID(hex=params.snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {params.snapshot_id}"
                ) from exc
            try:
                pair = FilePair(
                    maybe(params.left_path), maybe(params.right_path)
                )
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left = pair.left_path.map(Path)
            right = pair.right_path.map(Path)
            left_file, right_file, file_meta = room.get(
                snapshot_key, left, right
            )
            payload = self.render_loaded_snapshot_file(
                room=room,
                snapshot_id=snapshot_key,
                engine_config=params.engine,
                pair=pair,
                left_file=left_file,
                right_file=right_file,
                file_meta=file_meta,
            )
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("File diff request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return payload

    @routes.get(
        "/api/file-media",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Serve one composed image-bay side",
        response_class=Response,
    )
    def serve_file_media(
        self,
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
        bay_key: str = Query(
            description="Exact File-local image bay key from /api/file-diff.",
        ),
        side: Literal["left", "right"] = Query(
            description="Which composed side of the image bay to serve.",
        ),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
    ) -> Response:
        """Serve one side of one image bay as its exact composed media bytes.

        The Snapshot and File pair identify the composed File. `bay_key` selects
        one image bay inside it, which is required once a notebook can carry
        several image outputs. The selected side then identifies the exact
        media bytes.

        The route does HTTP work only: it recovers the Room, reads the two
        captured byte sides, asks `bays()` for the named image bay, and writes
        that side's bytes under its media type. `bays()` runs
        no engine, so this never renders a diff to serve a picture, and the
        media type is the one composition concluded rather than a second
        opinion formed here.

        Snapshots are immutable and a Snapshot id is never reused, so the
        response for one address can never change and is declared cacheable
        outright.

        # Parameters

        - `snapshot_id`: Opaque immutable Snapshot key returned by manifest.
        - `bay_key`: Exact image bay key from the composed diff.
        - `side`: Present image representation whose exact bytes are returned.
        - `left_path`: Exact left repository path, or absent for an added File.
        - `right_path`: Exact right repository path, or absent for a deleted
          File.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid Snapshot id or
          File pair, missing or failed capture, missing or non-image bay, or an
          absent selected representation.
        - Logs unexpected I/O, persistence, or composition failures and raises
          status 500.
        """
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            try:
                pair = FilePair(maybe(left_path), maybe(right_path))
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left_file, right_file, file_meta = room.get(
                snapshot_key,
                pair.left_path.map(Path),
                pair.right_path.map(Path),
            )
            if file_meta["capture_error"] is not None:
                raise DirdiffError(file_meta["capture_error"])
            snapshot_meta = room.meta(snapshot_key)
            left_link = left_file.and_then(
                lambda side: (
                    Some(side.link) if side.kind == "symlink" else Nothing()
                )
            )
            right_link = right_file.and_then(
                lambda side: (
                    Some(side.link) if side.kind == "symlink" else Nothing()
                )
            )
            for bay in Composer().bays(
                left_file.map(lambda side: side.path.read_bytes()),
                right_file.map(lambda side: side.path.read_bytes()),
                BayContext(
                    left_path=pair.left_path,
                    right_path=pair.right_path,
                    left_label=snapshot_meta["left_label"],
                    right_label=snapshot_meta["right_label"],
                    left_link=left_link,
                    right_link=right_link,
                ),
            ):
                if bay.bay_key != bay_key:
                    continue
                if not isinstance(bay, ImageBay):
                    raise DirdiffError(
                        f"Bay {bay_key!r} does not carry media content."
                    )
                media_bay = bay
                break
            else:
                raise DirdiffError(
                    f"The selected file has no bay named {bay_key!r}."
                )
            media_side = media_bay.left if side == "left" else media_bay.right
            if not Some.present(media_side):
                raise DirdiffError(
                    f"Bay {bay_key!r} has no image on the {side} side."
                )
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("File media request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return Response(
            content=media_side.value.data,
            media_type=media_side.value.media_type,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )
