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
    assert_never,
)
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from fastapi.responses import (
    Response,
)
from pydantic import (
    Field,
    model_validator,
)

from dirdiff.backend import (
    BranchSelection,
    BranchSource,
    LazyReason,
    PresetBackend,
    PresetCatalogDir,
    RepoDiffPath,
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
    EngineKind,
    InlineTokenStatus,
    engine,
)
from dirdiff.formats import (
    BayContext,
    CapturedLink,
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
    BranchReviewCaptureSelection,
    CapturedFileSide,
    CaptureSelection,
    FileMeta,
    PresetCaptureSelection,
    PullRequestCaptureSelection,
    RevisionsCaptureSelection,
    Room,
    RoomLord,
)
from dirdiff.server.base import (
    ApiModel,
    ErrorResponse,
    capture_snapshot,
    preset_catalog_dirs,
)
from dirdiff.server.magic import ClassRoutes

__all__ = ["ComposedDiffResponse", "DiffRoutes"]

LOGGER = logging.getLogger(__name__)
"""Record unexpected failures at this HTTP boundary."""

TabParam = Literal[
    "head",
    "refs",
    "branch-review",
    "pull-request",
    "preset",
]
"""Select the manifest correspondence and capture flow used by the HUD.

- `head` compares HEAD with the worktree.
- `refs` compares two explicit sides.
- `branch-review` resolves symbolic base and review branches.
- `pull-request` uses prepared immutable commits.
- `preset` opens one fixture group.

Route validation uses this value with the parameters required by the selected
Tab. It is not a persisted Room identity by itself.
"""
BranchSourceParam = BranchSource
"""Select local or remote branch parameters at the HTTP boundary.

`selected_branch_selections` combines this discriminator with the matching
branch and remote parameters. It does not carry either name by itself.
"""
ChangeType = Literal["modify", "add", "delete", "rename", "copy"]
"""Validate backend File relationships before HTTP conversion.

- `modify` keeps one File path relationship.
- `add` and `delete` are one-sided.
- `rename` and `copy` retain cross-path provenance.

This value describes File identity, not rendered line status.
"""
BranchSelections = tuple[BranchSelection | None, BranchSelection | None]
"""Validated base and review selections in that order.

Both entries are present only for the Branch Review Tab. Other Tabs return two
`None` values so callers can unpack the result without inventing selections.
"""


class PresetGroupResponse(ApiModel):
    """Return one selectable fixture group from a preset catalog.

    The preset picker shows `display_name` and sends `id` back in a manifest
    request to choose the directory.

    The response contains no fixtures, catalog identity, or File content.
    """

    id: str
    """Catalog-local directory token identifying this fixture group.

    The Preset Tab sends it as `preset_subset` with its catalog id. Consumers
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

    The backend supplies their display order. Each group id is meaningful only
    with this catalog, and the HUD must preserve the association.
    """


class DecoratedPartResponse(ApiModel):
    """One ordered, lossless slice of a rendered line.

    Diff-row conversion returns a complete partition for each present side. The
    HUD concatenates `text` in order and applies the supplied diff, syntax, and
    whitespace decoration directly.

    Parts carry no offsets, line identity, or review coordinates. Their text
    must not be reordered or independently edited.
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
    """Return one aligned row with complete display decoration.

    Present sides carry their source line number, text, and lossless ordered
    parts. An absent side has no line number or decorated parts. Current
    renderers encode its text as an empty string, while the transport model
    also accepts `None`. `status` summarizes the row relationship.
    """

    status: Literal["equal", "replace", "insert", "delete", "move"]
    """Relationship the engine assigned to this aligned source row.

    `equal`, `replace`, and `move` pair present lines from both sides; they mean
    unchanged text, changed aligned text, and structurally moved text respectively.
    `insert` is right-only and therefore has no left line, while `delete` is
    left-only and has no right line. The HUD uses this closed vocabulary for row
    styling and change accounting; token statuses may still describe finer
    changes within a paired row.
    """

    left_no: int | None
    """One-based left source coordinate represented by this row.

    `None` means the alignment has no left line. Current renderer output pairs
    it with empty text and no left parts. A present value identifies source,
    not rendered order.
    """

    right_no: int | None
    """One-based right source coordinate represented by this row.

    `None` means the alignment has no right line. Current renderer output pairs
    it with empty text and no right parts. A present value identifies source,
    not rendered order.
    """

    left_text: str | None
    """Complete left source line without a newline, when one is aligned.

    With a present `left_no`, concatenating `left_parts` reproduces this exact
    string. Current renderer output uses `""` when `left_no` is absent; the
    nullable model also accepts `None` from other constructors.
    """

    right_text: str | None
    """Complete right source line without a newline, when one is aligned.

    With a present `right_no`, concatenating `right_parts` reproduces this exact
    string. Current renderer output uses `""` when `right_no` is absent; the
    nullable model also accepts `None` from other constructors.
    """

    left_parts: list[DecoratedPartResponse]
    """Ordered decoration slices covering the entire present left line.

    For a present side, their text concatenates to `left_text` without loss or
    overlap. Current renderer output leaves the list empty when `left_no` is
    absent.
    """

    right_parts: list[DecoratedPartResponse]
    """Ordered decoration slices covering the entire present right line.

    For a present side, their text concatenates to `right_text` without loss or
    overlap. Current renderer output leaves the list empty when `right_no` is
    absent.
    """
    hunk_index: int | None
    """
    Zero-based bay-local hunk identity on a hunk's first rendered row.

    Other rows carry `None`. Display enrichment numbers each bay's own rows from
    zero, so a hunk coordinate is this index plus the enclosing bay's key; the
    backend never numbers a File as a whole. The value is assigned before
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
    additions that the backend did not render as lines.
    """

    removed_lines: int
    """Count of rendered rows that have only a left source line.

    It aggregates text bays for this File and does not count an absent File side
    independently of the renderer's produced rows.
    """

    moved_lines: int = 0
    """Count of rows the selected engine classified as moved.

    The value excludes inline token movement and defaults to zero for engines
    that report no line moves. It remains part of `changed_lines` accounting.
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
    """Validate manifest-wide File totals and optional backend line totals.

    Added and removed line counts are either both backend-reported integers or
    both absent. They need not include additional untracked Files. Cell totals
    remain optional because only notebook-aware payloads can provide them.
    """

    changed_files: int
    """Total affected File relationships reported for the manifest.

    It equals `added_files + removed_files + updated_files`, including untracked
    Files that became manifest leaves. Skipped entries are counted separately.
    """

    added_files: int
    """Count of affected File pairs with only a right side.

    The backend computes this before rendering. Additional untracked entries
    follow the backend's manifest policy rather than line totals.
    """

    removed_files: int
    """Count of affected File pairs with only a left side.

    The backend computes this before rendering. The value is independent of how
    many deleted source rows an engine later produces.
    """

    updated_files: int
    """Count of affected File relationships present on both sides.

    Modified, renamed, and copied tracked Files contribute here. The value does
    not claim the side paths are equal.
    """

    added_lines: Optional[int]
    """Backend-wide added-line total, or `None` when unavailable.

    This field and `removed_lines` always have equal presence.
    """

    removed_lines: Optional[int]
    """Backend-wide removed-line total, or `None` when unavailable.

    This field and `added_lines` always have equal presence.
    """

    skipped_files: int
    """Count of backend-reported entries not included as manifest Files.

    This records backend summary damage or filtering and is not a lazy-File
    count. Consumers must not add it to the manifest tree.
    """

    changed_cells: int | None = None
    """Aggregate count of notebook cells with any reported change.

    `None` means the selected backend supplied no cell summary, not zero. A
    present value is repository-wide and separate from File line counts.
    """

    added_cells: int | None = None
    """Aggregate count of notebook cells present only on the right.

    `None` means no cell summary was supplied. Consumers must preserve that
    distinction instead of presenting an unavailable count as zero.
    """

    removed_cells: int | None = None
    """Aggregate count of notebook cells present only on the left.

    `None` means no cell summary was supplied. The value is metadata, not a
    count derived from composed notebook bays.
    """

    modified_cells: int | None = None
    """Aggregate count of paired notebook cells whose content changed.

    `None` means the backend supplied no cell summary. Moved-only or format
    details remain governed by the backend's summary contract.
    """

    @model_validator(mode="after")
    def validate_line_count_presence(self) -> RepoDiffSummaryResponse:
        """Require repository added and removed line totals as one unit.

        Both backend totals must be integers or both must be unavailable. Return
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


class GitFileKindResponse(ApiModel):
    """Return tracked Git provenance for one manifest or composed File.

    The `git` discriminator selects this variant; `status` describes the File
    relationship reported by the backend.

    It does not identify side paths, loading state, or line changes.
    """

    type: Literal["git"]
    """Select the tracked Git provenance variant.

    Only the literal `git` is valid. Consumers may then rely on `status`; the
    value does not mean the File currently exists on both sides.
    """

    status: Literal["modified", "added", "deleted", "renamed", "copied"]
    """Tracked relationship the Git backend reported for this File pair.

    The closed value distinguishes one-sided, same-path, and cross-path changes.
    It does not summarize rendered line or bay status.
    """


class UntrackedFileKindResponse(ApiModel):
    """Return untracked provenance for one worktree File.

    The `untracked` discriminator lets the HUD present content absent from Git's
    tracked state.

    Side paths and lazy reason remain on the enclosing File response.
    """

    type: Literal["untracked"]
    """Select provenance for a File not present in Git's tracked state.

    Only the literal `untracked` is valid. No tracked change status accompanies
    it; path presence and lazy loading stay on the enclosing File model.
    """


FileKindResponse = GitFileKindResponse | UntrackedFileKindResponse
"""Return one complete File provenance variant to the HUD.

- `GitFileKindResponse` includes tracked Git status.
- `UntrackedFileKindResponse` identifies worktree-only content.

Consumers branch on `type`. This union is not a File identity or rendered diff
classification.
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
    """Backend-authored name shown for the bay as a whole.

    It is presentation text and may repeat across bays, so callers must not use it
    as the stable coordinate.
    """

    detail: str | None = None
    """Optional secondary backend-authored description.

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
    """Backend-authored frame heading, or `None` for a heading-less frame.

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
    """Human-readable label for the complete left comparison side.

    Individual text bays may supply their own column labels, so this value names
    the File envelope rather than every widget column.
    """

    right_label: str
    """Human-readable label for the complete right comparison side.

    It remains valid when the File's right path is absent because it describes
    the comparison side, not side presence.
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
    """Tracked or untracked provenance copied from manifest metadata.

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
    its exact path pair and provenance for later File operations.

    At least one side path is present. `lazy` explains deferred loading or is
    `None` for ordinary loading. The entry has no captured path, display name,
    or rendered content.
    """

    file_kind: FileKindResponse
    """Tracked or untracked provenance and backend change classification.

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
    """Exact side paths, provenance, and loading policy for the File.

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
    """File and directory children in backend-supplied order.

    The order reflects manifest construction; the HUD must not sort it and thereby
    separate it from backend File sequence.
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

    The path pair and provenance identify the File; display name, known counts,
    and reason describe the placeholder. It contains no captured bytes, frames,
    or bays.
    """

    # This response must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    file_kind: FileKindResponse
    """Tracked or untracked provenance and backend change classification.

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
    """Human-readable name of the complete comparison.

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

    The label describes the comparison side even when an individual File is
    removed and has no right-side content.
    """

    summary: RepoDiffSummaryResponse
    """Repository-wide File totals and optional backend line totals.

    File counts are manifest-authoritative while nullable line totals remain
    unknown when the backend deferred or could not calculate them.
    """

    tree: list[RepoManifestTreeEntryResponse]
    """Every affected File arranged in the recursive directory tree.

    Each manifest File appears once in backend order; the tree contains metadata
    only and no composed File contents.
    """


class LazyInfoResponse(ApiModel):
    """Return deferred File-card metadata for one Snapshot.

    The HUD consumes `files` after manifest loading to create placeholders for
    every File awaiting explicit reviewer action.

    The response contains no eager File entries, captured bytes, or rendered
    bays.
    """

    files: list[LazyInfoFileResponse]
    """Deferred File entries in the manifest's backend path order.

    The collection excludes eager Files and lets the HUD create placeholders
    without joining partial data from the manifest tree.
    """


def selected_branch_selections(
    tab: TabParam = Query(description="HUD Tab."),
    base_source: BranchSourceParam | None = Query(
        default=None,
        description="Base branch source for a branch-backed Tab.",
    ),
    base_remote: str | None = Query(
        default=None,
        description="Base remote for remote branch-backed selections.",
    ),
    base_branch: str | None = Query(
        default=None,
        description="Base branch name for a branch-backed Tab.",
    ),
    review_source: BranchSourceParam | None = Query(
        default=None,
        description="Review branch source for a branch-backed Tab.",
    ),
    review_remote: str | None = Query(
        default=None,
        description="Review remote for remote branch-backed selections.",
    ),
    review_branch: str | None = Query(
        default=None,
        description="Review branch name for a branch-backed Tab.",
    ),
) -> BranchSelections:
    """Return structured branch selections for Branch Review.

    The UI can keep base/review branch controls populated while the user moves
    between Tabs. API handlers call this helper so branch parameters do not
    accidentally influence another Tab's manifest.

    # Parameters

    - `tab`: Selected HUD Tab; only Branch Review consumes the other values.
    - `base_source`: Local or remote namespace for the base branch.
    - `base_remote`: Required remote name when the base source is remote.
    - `base_branch`: Required symbolic base branch name.
    - `review_source`: Local or remote namespace for the review branch.
    - `review_remote`: Required remote name when the review source is remote.
    - `review_branch`: Required symbolic review branch name.

    Non-Branch-Review Tabs return two absent selections without validating
    controls they do not use.

    # Usage

    Install this function as the manifest route dependency. Callers unpack the
    returned base and review selections in that order.

    # Failures

    - Raises `HTTPException` with status 400 when Branch Review parameters are
      missing, blank, or internally inconsistent.
    """
    if tab != "branch-review":
        return None, None

    return (
        _branch_selection_from_query(
            label="base",
            source=base_source,
            remote=base_remote,
            branch=base_branch,
        ),
        _branch_selection_from_query(
            label="review",
            source=review_source,
            remote=review_remote,
            branch=review_branch,
        ),
    )


def _preset_project_parts(
    *,
    project_id: str | None,
    preset_subset: str | None,
) -> tuple[str, str]:
    """Parse the preset catalog id and subset used to prepare a manifest.

    The Preset Tab uses `project_id` as the catalog id. It is the name of a
    directory under the presets root. `preset_subset` is the selected
    group within that catalog. Both are required; whether the catalog exists is
    settled by `preset_backend_for_catalog`, which is the one place that reads
    the presets root, so this parser does not carry a second copy of the
    catalog set. Follow-up endpoints find the prepared Room by Snapshot key and
    do not call this parser. The preset backend still validates traversal and
    unknown-group errors while preparing the manifest.

    # Parameters

    - `project_id`: Nonblank catalog directory id carried by the common
      manifest parameter.
    - `preset_subset`: Nonblank fixture-group id within that catalog.

    # Usage

    Call only for the Preset Tab while translating manifest parameters. Use the
    returned catalog id to choose a backend and the subset as its capture side.

    # Returns

    - First, the unchanged catalog id used to select `PresetBackend`.
    - Second, the unchanged fixture-group id used as that backend's capture side.

    # Failures

    - Raises `DirdiffError` when either value is absent or blank.
    """
    if project_id is None or project_id.strip() == "":
        raise DirdiffError("project_id is required for the Preset Tab.")
    if preset_subset is None or preset_subset.strip() == "":
        raise DirdiffError("preset_subset is required for the Preset Tab.")
    return project_id, preset_subset


def _branch_selection_from_query(
    *,
    label: str,
    source: BranchSourceParam | None,
    remote: str | None,
    branch: str | None,
) -> BranchSelection:
    """Parse one split Branch Review selection from query parameters.

    Used only while building a manifest for a branch-backed Tab. Follow-up file
    endpoints use the snapshot id returned by that manifest request.

    # Parameters

    - `label`: `base` or `review`, used to identify invalid input precisely.
    - `source`: Required local or remote branch namespace.
    - `remote`: Required nonblank remote name only for a remote source.
    - `branch`: Required nonblank symbolic branch name.

    # Usage

    `selected_branch_selections` calls this once for base and once for review.
    The label is used only to produce a precise HTTP error.

    # Failures

    - Raises `HTTPException` with status 400 when source, remote, and branch do
      not form one complete local or remote selection.
    """
    if source is None:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_source is required for the Branch Review Tab.",
        )
    if branch is None or (branch_name := branch.strip()) == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_branch is required for the Branch Review Tab.",
        )
    if source == "local":
        return {"source": source, "branch": branch_name}
    if remote is None or (remote_name := remote.strip()) == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label}_remote is required for remote Branch Review selections.",
        )
    return {
        "source": source,
        "remote": remote_name,
        "branch": branch_name,
    }


class DiffRoutes:
    """Bind capture and composed-diff handlers to application interfaces.

    One instance retains the repository registry, Room service, and preset root
    used to capture or read Snapshots. The route group owns no captured bytes,
    backend state, or rendered result after an HTTP entity completes.
    """

    routes = ClassRoutes()
    """Import-time declarations bound to one route-group instance."""

    def __init__(
        self,
        db: RepoMarkStore,
        *,
        room_lord: RoomLord,
        presets_root: str | None,
    ) -> None:
        """Retain the interfaces required by capture and File routes.

        # Parameters

        - `db`: Repository registry used by Snapshot capture.
        - `room_lord`: Room selection and Snapshot lookup interface.
        - `presets_root`: Optional root rescanned for preset catalogs.
        """
        self.db = db
        self.room_lord = room_lord
        self.presets_root = presets_root

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
        preset_backend = PresetBackend(catalog.root)
        return PresetCatalogResponse.model_validate(
            {
                "id": catalog.catalog_id,
                "name": catalog.name,
                "default_preset": preset_backend.default_preset_name(),
                "groups": preset_backend.list_preset_groups(),
            }
        )

    def manifest_capture_selection(
        self,
        *,
        project_id: str,
        tab: TabParam,
        branch_selections: BranchSelections,
        left: str | None,
        right: str | None,
        pull_request_url: str | None,
        left_commit: str | None,
        right_commit: str | None,
        preset_subset: str | None,
        show_untracked: bool,
    ) -> CaptureSelection:
        """Translate one manifest query into its concrete Tab selection.

        The query parameters carry every cross-Tab value, so this is the
        single boundary that rejects a missing Tab-required value and asserts
        that inapplicable values were not supplied. Downstream code receives
        one discriminated selection and never branches on nullability.

        # Parameters

        - `project_id`: Mark id or preset catalog id, interpreted by the Tab.
        - `tab`: Tab whose law determines the valid parameter combination.
        - `branch_selections`: Structured base/review pair from the dependency.
        - `left`: Left revision handle used by Head or Refs.
        - `right`: Right revision handle used by Head or Refs.
        - `pull_request_url`: Canonical Pull Request Room correspondence value.
        - `left_commit`: Prepared Pull Request merge-base capture commit.
        - `right_commit`: Prepared Pull Request head capture commit.
        - `preset_subset`: Fixture-group id for a Preset capture.
        - `show_untracked`: Whether supported repository Tabs include untracked
          worktree Files.

        # Usage

        Call once at the manifest boundary after dependency parsing, then pass
        the returned selection unchanged to `capture_snapshot`.

        # Failures

        - Raises `DirdiffError` when a required Tab value is absent or blank.
        - Asserts when parameters inapplicable to the selected Tab are present or
          the dependency supplied an impossible branch-selection pair.
        """
        selected_base, selected_review = branch_selections
        if tab == "preset":
            assert selected_base is None and selected_review is None
            assert left is None and right is None
            assert pull_request_url is None
            assert left_commit is None and right_commit is None
            assert not show_untracked, (
                "the Preset Tab does not support intruding Files"
            )
            catalog, subset = _preset_project_parts(
                project_id=project_id,
                preset_subset=preset_subset,
            )
            return PresetCaptureSelection(catalog=catalog, subset=subset)
        if tab == "pull-request":
            assert selected_base is None and selected_review is None
            assert left is None and right is None
            assert preset_subset is None
            assert not show_untracked, (
                "the Pull Request Tab does not support intruding Files"
            )
            if pull_request_url is None:
                raise DirdiffError(
                    "pull_request_url is required for the Pull Request Tab."
                )
            if left_commit is None:
                raise DirdiffError(
                    "left_commit is required for the Pull Request Tab."
                )
            if right_commit is None:
                raise DirdiffError(
                    "right_commit is required for the Pull Request Tab."
                )
            return PullRequestCaptureSelection(
                url=pull_request_url,
                left_commit=left_commit,
                right_commit=right_commit,
            )
        if tab == "branch-review":
            assert left is None and right is None
            assert pull_request_url is None
            assert left_commit is None and right_commit is None
            assert preset_subset is None
            assert not show_untracked, (
                "the Branch Review Tab does not support intruding Files"
            )
            if selected_base is None or selected_review is None:
                raise DirdiffError("branch selections are required.")
            return BranchReviewCaptureSelection(
                base=selected_base,
                review=selected_review,
            )
        assert tab == "head" or tab == "refs"
        assert selected_base is None and selected_review is None
        assert pull_request_url is None
        assert left_commit is None and right_commit is None
        assert preset_subset is None
        if tab == "head":
            if left is None or right is None:
                raise DirdiffError(
                    "Diff against HEAD requires HEAD and worktree sides."
                )
        else:
            if left is None or left.strip() == "":
                raise DirdiffError("left is required for the Refs Tab.")
            if right is None or right.strip() == "":
                raise DirdiffError("right is required for the Refs Tab.")
        return RevisionsCaptureSelection(
            tab=tab,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def render_loaded_snapshot_file(
        self,
        *,
        room: Room,
        snapshot_id: UUID,
        engine_name: EngineKind,
        pair: FilePair,
        left_file: Optional[CapturedFileSide],
        right_file: Optional[CapturedFileSide],
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
        - `engine_name`: Requested renderer selected only for text bays.
        - `pair`: Exact nullable repository paths identifying the File.
        - `left_file`: Authenticated captured left side, or `None` when absent.
        - `right_file`: Authenticated captured right side under the same rule.
        - `file_meta`: Persisted provenance, change type, lazy policy, and
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
        left_bytes = (
            left_file.path.read_bytes() if left_file is not None else None
        )
        right_bytes = (
            right_file.path.read_bytes() if right_file is not None else None
        )
        left_link: CapturedLink | None = None
        if left_file is not None:
            match left_file.kind:
                case "regular":
                    pass
                case "symlink":
                    left_link = left_file.link
                case invalid_kind:
                    assert_never(invalid_kind)
        right_link: CapturedLink | None = None
        if right_file is not None:
            match right_file.kind:
                case "regular":
                    pass
                case "symlink":
                    right_link = right_file.link
                case invalid_kind:
                    assert_never(invalid_kind)
        context = ComposeContext.build(
            left_path=pair.left_path,
            right_path=pair.right_path,
            left_label=snapshot_meta["left_label"],
            right_label=snapshot_meta["right_label"],
            left_link=left_link,
            right_link=right_link,
            renderer=engine(engine_name),
        )
        composed = Composer().compose(left_bytes, right_bytes, context)
        # TODO: the frontend should probably take the display name and file kind
        # from the manifest, which already emits one per File. Until then the
        # HTTP boundary attaches both here: the two envelope fields composition
        # deliberately does not produce.
        file_kind: Literal["git", "untracked"] = (
            "git" if file_meta["tracked"] else "untracked"
        )
        display_name = (
            pair.right_path
            if snapshot_meta["tab"] == "preset" and pair.right_path is not None
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
        without restarting the backend. Invalid metadata is reported as a bad
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
        project_id: str = Query(
            description="Manifest project id: marked project id for repo-backed Tabs, preset catalog id for Preset.",
        ),
        tab: TabParam = Query(description="HUD Tab."),
        branch_selections: BranchSelections = Depends(
            selected_branch_selections
        ),
        left: str | None = Query(
            default=None, description="Left ref or diff side."
        ),
        right: str | None = Query(
            default=None, description="Right ref or diff side."
        ),
        pull_request_url: str | None = Query(
            default=None,
            description="Pull Request URL used only for Room correspondence.",
        ),
        left_commit: str | None = Query(
            default=None,
            description="Left commit prepared for the Pull Request Tab.",
        ),
        right_commit: str | None = Query(
            default=None,
            description="Right commit prepared for the Pull Request Tab.",
        ),
        preset_subset: str | None = Query(
            default=None,
            description="Preset subset/group id for the Preset Tab.",
        ),
        show_untracked: bool = Query(
            default=False,
            description="Include untracked worktree files when supported by the selected Tab.",
        ),
    ) -> RepoManifestResponse:
        """Show the supplied repository state and provide follow-up keys.

        The handler constructs the concrete backend and supplies the complete
        Tab state to `RoomLord.corresponding_room`. The response contains the
        manifest tree, aggregate backend totals, and the opaque Snapshot key
        required by follow-up endpoints. It does not prepare Pull Requests;
        Pull Request parameters already contain the URL and capture commits.

        # Parameters

        - `project_id`: Active Mark id for repository Tabs or catalog id for
          Preset.
        - `tab`: Selected HUD Tab governing the valid parameter combination.
        - `branch_selections`: Parsed base/review selections for Branch Review.
        - `left`: Left revision handle for Head or Refs.
        - `right`: Right revision handle for Head or Refs.
        - `pull_request_url`: Canonical URL identifying a Pull Request Room.
        - `left_commit`: Already prepared Pull Request merge-base commit.
        - `right_commit`: Already prepared Pull Request head commit.
        - `preset_subset`: Selected fixture group for a Preset catalog.
        - `show_untracked`: Whether a supported repository Tab includes
          untracked worktree Files.

        # Failures

        - Raises `HTTPException` with status 400 for invalid Tab parameters,
          Mark, backend, or capture input.
        - Logs unexpected capture or persistence failures and raises status 500.
        """
        try:
            room, snapshot_id, preset_name = capture_snapshot(
                self.db,
                self.room_lord,
                self.presets_root,
                project_id=project_id,
                selection=self.manifest_capture_selection(
                    project_id=project_id,
                    tab=tab,
                    branch_selections=branch_selections,
                    left=left,
                    right=right,
                    pull_request_url=pull_request_url,
                    left_commit=left_commit,
                    right_commit=right_commit,
                    preset_subset=preset_subset,
                    show_untracked=show_untracked,
                ),
            )
            snapshot_meta = room.meta(snapshot_id)
            manifest_paths = tuple(
                sorted(
                    (
                        RepoDiffPath(
                            left_path=left_path.as_posix()
                            if left_path is not None
                            else None,
                            right_path=right_path.as_posix()
                            if right_path is not None
                            else None,
                            display_name=(
                                right_path.as_posix()
                                if snapshot_meta["tab"] == "preset"
                                and right_path is not None
                                else display_name_for_repo_paths(
                                    left_path.as_posix()
                                    if left_path is not None
                                    else None,
                                    right_path.as_posix()
                                    if right_path is not None
                                    else None,
                                )
                            ),
                            change_type=file_meta["change_type"],
                            lazy_reason_override=file_meta[
                                "lazy_reason_override"
                            ],
                            untracked=not file_meta["tracked"],
                        )
                        for left_path, right_path, file_meta in room.manifested(
                            snapshot_id
                        )
                    ),
                    key=lambda path: (path.display_name, path.change_type),
                )
            )
            manifest = build_repo_manifest_for_paths(
                left_label=snapshot_meta["left_label"],
                right_label=snapshot_meta["right_label"],
                paths=manifest_paths,
                added_lines=snapshot_meta["added_lines"],
                removed_lines=snapshot_meta["removed_lines"],
            )
            if tab == "preset":
                assert preset_name is not None
                manifest["display_name"] = preset_name
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
            lazy_paths = tuple(
                sorted(
                    (
                        RepoDiffPath(
                            left_path=left_path.as_posix()
                            if left_path is not None
                            else None,
                            right_path=right_path.as_posix()
                            if right_path is not None
                            else None,
                            display_name=(
                                right_path.as_posix()
                                if snapshot_meta["tab"] == "preset"
                                and right_path is not None
                                else display_name_for_repo_paths(
                                    left_path.as_posix()
                                    if left_path is not None
                                    else None,
                                    right_path.as_posix()
                                    if right_path is not None
                                    else None,
                                )
                            ),
                            change_type=file_meta["change_type"],
                            lazy_reason_override=file_meta[
                                "lazy_reason_override"
                            ],
                            untracked=not file_meta["tracked"],
                        )
                        for left_path, right_path, file_meta in room.manifested(
                            snapshot_key
                        )
                    ),
                    key=lambda path: (path.display_name, path.change_type),
                )
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
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
        engine: EngineKind = Query(description="Diff engine."),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
    ) -> ComposedDiffResponse:
        """Render one exact filepath pair from a manifest Snapshot.

        The opaque key finds the containing Room and is passed again with the
        repository-relative paths to select the exact captured File. Rendering
        reads only the returned absolute capture paths; it never reloads the
        workspace backend.

        # Parameters

        - `snapshot_id`: Opaque key returned by manifest.
        - `engine`: Renderer requested for composed text bays.
        - `left_path`: Exact left repository path, or absent for an added File.
        - `right_path`: Exact right repository path, or absent for a deleted
          File.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid Snapshot id or
          File pair, missing capture, stored capture error, or rejected rendering.
        - Logs unexpected I/O, persistence, or response failures and raises
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
                pair = FilePair(left_path, right_path)
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left = Path(pair.left_path) if pair.left_path is not None else None
            right = (
                Path(pair.right_path) if pair.right_path is not None else None
            )
            left_file, right_file, file_meta = room.get(
                snapshot_key, left, right
            )
            payload = self.render_loaded_snapshot_file(
                room=room,
                snapshot_id=snapshot_key,
                engine_name=engine,
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
                pair = FilePair(left_path, right_path)
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left_file, right_file, file_meta = room.get(
                snapshot_key,
                Path(pair.left_path) if pair.left_path is not None else None,
                Path(pair.right_path) if pair.right_path is not None else None,
            )
            if file_meta["capture_error"] is not None:
                raise DirdiffError(file_meta["capture_error"])
            snapshot_meta = room.meta(snapshot_key)
            left_link: CapturedLink | None = None
            if left_file is not None:
                match left_file.kind:
                    case "regular":
                        pass
                    case "symlink":
                        left_link = left_file.link
                    case invalid_kind:
                        assert_never(invalid_kind)
            right_link: CapturedLink | None = None
            if right_file is not None:
                match right_file.kind:
                    case "regular":
                        pass
                    case "symlink":
                        right_link = right_file.link
                    case invalid_kind:
                        assert_never(invalid_kind)
            for bay in Composer().bays(
                left_file.path.read_bytes() if left_file is not None else None,
                (
                    right_file.path.read_bytes()
                    if right_file is not None
                    else None
                ),
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
            if media_side is None:
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
            content=media_side.data,
            media_type=media_side.media_type,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )
