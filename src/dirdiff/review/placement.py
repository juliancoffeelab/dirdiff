"""Derive and interpret review Thread placements.

## Public interface

`derive_room_threads` computes the immutable placement rows published with a
new Snapshot. The remaining exported names are package-internal operations used
to construct Thread origins and interpret persisted placement rows.

## Purpose and boundaries

This module composes captured Files without a diff engine, records private
structural locators, derives later Snapshot placements, and reconstructs bounded
origin excerpts. It reads immutable captured Files and returns domain or
persistence values. It does not mutate Thread action history, expose private
locators through public views, publish Snapshots, or perform HTTP serialization.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from tree_sitter import Language, Node, Parser

from dirdiff.db import (
    ReviewActionRecord,
    ReviewThreadRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    SnapshotRecord,
)
from dirdiff.formats import (
    BayContext,
    CapturedLink,
    Composer,
    MediaSide,
    TextBay,
    media_ref,
    read_captured_link,
)
from dirdiff.review.base import (
    CreateThread,
    FilePair,
    LineRange,
    ReviewError,
    ReviewExcerptView,
    ReviewFilePairView,
    ReviewOriginView,
    validate_comment_body,
)

__all__ = [
    "BayStartPlacement",
    "FileMissingPlacement",
    "FileStartPlacement",
    "FileUnreadablePlacement",
    "Placement",
    "RangePlacement",
    "ReviewReadCache",
    "build_original_excerpt",
    "derive_room_threads",
    "file_pair",
    "origin_target_view",
    "placement_from_record",
    "plan_thread_creation",
]

LOGGER = logging.getLogger(__name__)
"""Report a captured File failure contained by placement derivation."""


@dataclass(frozen=True)
class _Segment:
    """Name one structural container enclosing a private source region.

    Locator construction records these outermost first; reattachment compares
    the sequence when choosing candidate regions.

    This value never crosses persistence except inside encoded private locator
    bytes and never becomes public syntax metadata.
    """

    node_type: str
    """Nonempty Tree-sitter kind of one eligible enclosing container.

    Reattachment compares it positionally with the other outer-to-inner
    segments; it is private syntax identity, not public language metadata.
    """

    name: Optional[str]
    """Declared container name extracted from the grammar, when available.

    `None` means the syntax node exposes no usable name. Matching preserves that
    absence rather than inventing text from another child.
    """


@dataclass(frozen=True)
class _Locator:
    """Retain only the private facts required to find an origin region.

    These are private source coordinates: they never cross the HTTP boundary and
    the store never interprets them. `RangePlacement` contains one, and the side the
    coordinates address is the placement's own `side` rather than a field here,
    because a locator that disagreed with its placement would be unusable.

    The persisted JSON does still carry `side`, and `_locator_of()` requires it to
    agree. This type is the decoded domain value, not the storage format.
    """

    region_hash: bytes
    """SHA-256 digest of the exact origin region bytes.

    Derivation uses it to distinguish unchanged structural candidates; the
    digest is verified against immutable origin content before matching.
    """

    region_start_byte: int
    """Inclusive byte offset of the region in UTF-8 encoded origin bay text.

    It is interpreted only with `region_end_byte` and the placement's selected
    side; it is not a public source coordinate.
    """

    region_end_byte: int
    """Exclusive byte offset of the region in encoded origin bay text.

    It must exceed `region_start_byte` and remain within immutable origin bytes;
    verification treats disagreement as persisted corruption.
    """

    segments: tuple[_Segment, ...]
    """Eligible structural ancestry of the origin region, outermost first.

    Candidate relocation requires exact sequence equality. An empty tuple means
    only the complete-text root supplies a region identity.
    """


@dataclass(frozen=True)
class RangePlacement:
    """A Thread placed on a selected line range inside one composed bay.

    This is the shape every newly created Thread takes. Its public coordinate
    comes from composition and its inclusive lines remain local to that bay.

    The private locator is deliberately not a field. Only derivation reads one,
    and decoding it costs several times what the rest of this conversion does, so
    a placement carries no coordinates and `derive_room_threads()` decodes the
    single origin locator it is about to use.
    """

    thread_id: str
    """Global logical Thread identity to which this immutable landing belongs.

    The same id may have one placement per Snapshot but never two placements in
    the same Snapshot.
    """

    snapshot_id: str
    """Immutable Snapshot whose composed content contains this landing.

    It must agree with the referenced File and is the code universe used for
    all public placement reads.
    """

    snapshot_file_id: str
    """Opaque File id within `snapshot_id` containing the landing bay.

    Reads authenticate it and prove that its nullable path pair equals the
    origin File pair before exposing the placement.
    """

    bay_key: str
    """Composition-issued bay key containing the current line range.

    A matched range must retain the immutable origin bay key; derivation does
    not relocate a region across bays.
    """

    side: Literal["left", "right"]
    """Captured File side containing this bay-local range.

    It must equal the origin side. Loss of that side produces another placement
    variant instead of changing this value.
    """

    start_line: int
    """Positive one-based first included line in the current bay-side source.

    For an unchanged match it preserves the origin's offset within the matched
    region; for changed content it is the candidate's first line.
    """

    end_line: int
    """One-based last included line in the current bay-side source.

    It is no earlier than `start_line`; changed-region placement may narrow
    the range to that one structural landing line.
    """

    outdated_reason: Optional[Literal["region_changed"]]
    """Whether the unique structural candidate changed its source bytes.

    `None` means both structure and region digest matched exactly. No other
    outdated reason is valid for a placement that still has a range.
    """


@dataclass(frozen=True)
class BayStartPlacement:
    """A Thread placed at the start of one composed bay, its region lost.

    `region_not_found` means the bay the origin named still composes in this
    Snapshot's File but the origin region inside it matched no candidate or
    matched ambiguously, so `bay_key` is the origin's own bay.
    `bay_not_found` means the origin's bay is gone entirely, and `bay_key` is
    the File's first composed bay carrying `side`, chosen at derivation
    time, when the composed bays are already in hand, and stored so reads
    never recompute it. Derivation is the only producer; origins never take
    this shape.
    """

    thread_id: str
    """Global logical Thread identity receiving this bay-start landing.

    It relates the immutable placement to its unique origin and action history.
    """

    snapshot_id: str
    """Immutable Snapshot whose composition supplied the landing bay.

    Publication persists this choice before the Snapshot becomes visible.
    """

    snapshot_file_id: str
    """Opaque selected-Snapshot File id containing the landing bay.

    The File must retain the origin's exact nullable path pair and selected side.
    """

    bay_key: str
    """Composition-issued bay key whose start is the stored landing.

    It is the origin bay for region loss or the first replacement bay that
    contains the origin side for bay loss, as distinguished by `outdated_reason`.
    """

    side: Literal["left", "right"]
    """Origin-selected side on which the landing bay still composes.

    Derivation never changes sides to obtain a usable bay.
    """

    outdated_reason: Literal["region_not_found", "bay_not_found"]
    """Exact loss that reduced navigation to a bay start.

    `region_not_found` retains the origin bay; `bay_not_found` requires
    `bay_key` to hold the replacement chosen during derivation.
    """


@dataclass(frozen=True)
class FileStartPlacement:
    """A Thread placed at File start, with no bay coordinate to land on.

    It has no bay and no line range, so it is never navigable; History is
    its home. A reason of None marks a retained historical File-level
    origin. These are the only origins of this shape. Every placement derived
    from such an origin also has this shape. `bay_not_found` marks a placement
    derived from a range origin whose File composes no bay carrying the side.
    """

    thread_id: str
    """Global logical Thread identity receiving this File-side landing.

    It joins the placement to its immutable origin and append-only discussion.
    """

    snapshot_id: str
    """Immutable Snapshot in which only the File-side coordinate remains.

    The placement is not reusable for another Snapshot even when paths match.
    """

    snapshot_file_id: str
    """Opaque File id validated within `snapshot_id` for discovery.

    It identifies the exact path pair but offers no bay or line coordinate.
    """

    side: Literal["left", "right"]
    """Origin-selected side retained for the historical File landing.

    The side exists on the exact File pair, but no composed bay on it is valid
    for navigation in this placement.
    """

    outdated_reason: Optional[Literal["bay_not_found"]]
    """Why only a File-side landing remains, when the placement is outdated.

    `None` is reserved for retained historical File-level origins;
    `bay_not_found` means a range origin's selected side composes no bay.
    """


@dataclass(frozen=True)
class FileMissingPlacement:
    """A Thread with no code location, because its exact File pair is absent.

    It references no Snapshot File, and its public outdated reason is always
    `file_missing`, so neither is carried as a field.
    """

    thread_id: str
    """Global logical Thread identity whose origin pair has no current File.

    Discussion history remains addressable by this id even though code
    navigation has no selected-Snapshot target.
    """

    snapshot_id: str
    """Immutable Snapshot proven not to contain the origin File pair.

    Focused File loading checks this absence before the public placement is
    returned, so the value is not an unchecked claim.
    """


@dataclass(frozen=True)
class FileUnreadablePlacement:
    """A Thread with no code location, because its File could not be captured.

    The exact File pair is present in this Snapshot because the backend listed
    it, but capture failed. The only bytes beneath its capture directory are
    the ones dirdiff generated to stand in for the File. Nothing here can hold
    a Thread: a bay would name composed placeholder text, and File start would
    name a side record whose digest describes that same text. It references no
    Snapshot File, and its public outdated reason is always `file_unreadable`,
    so neither is carried as a field.

    This is not `FileMissingPlacement`. That one states the File pair is
    absent from the Snapshot, and the read boundary verifies that absence;
    this one states the opposite about the same Snapshot.
    """

    thread_id: str
    """Global logical Thread identity whose matching File is unreadable.

    The discussion remains intact while review withholds generated capture-error
    content from placement and excerpts.
    """

    snapshot_id: str
    """Immutable Snapshot in which the origin pair exists but capture failed.

    This distinguishes the placement from true File absence without storing a
    reference to placeholder bytes.
    """


Placement = (
    RangePlacement
    | BayStartPlacement
    | FileStartPlacement
    | FileMissingPlacement
    | FileUnreadablePlacement
)
"""One Thread's immutable location in one Snapshot, in the shape review needs.

- `RangePlacement` locates a usable line range.
- `BayStartPlacement` locates a bay whose original region was lost.
- `FileStartPlacement` retains a File-side landing without a bay.
- `FileMissingPlacement` states that the exact File pair is absent.
- `FileUnreadablePlacement` states that capture could not retain the File.

`RoomStore` returns a flat record whose optional fields can describe all five
variants without proving which one applies. `placement_from_record()` validates that
record into exactly one variant at the read boundary; `_record_of()` converts it
back. The union omits query-local origin labels and private reattachment
coordinates.
"""


@dataclass(frozen=True)
class _SourceRegion:
    """Describe one candidate region during private Thread reattachment.

    Structural scanning creates these values from one composed bay side.
    Matching compares source bytes, line bounds, and enclosing segments against
    the origin locator.

    It is temporary derivation state, not a public region or persisted record.
    """

    source: bytes
    """Complete UTF-8 encoded bay-side source containing this candidate.

    The byte offsets slice this shared value for digest comparison; it is
    operation-local and never becomes persisted review content.
    """

    start_byte: int
    """Inclusive UTF-8 byte offset at which the candidate region begins.

    It is interpreted against `source` and participates in choosing the
    smallest containing origin region.
    """

    end_byte: int
    """Exclusive UTF-8 byte offset at which the candidate region ends.

    It is strictly greater than `start_byte`; their slice is the content hashed
    for immutable origin matching.
    """

    start_line: int
    """Positive one-based first line covered by the syntax candidate.

    Selected ranges must begin no earlier than this coordinate for the region
    to contain them.
    """

    end_line: int
    """One-based last line covered by the syntax candidate, included.

    The coordinate accounts for parsers whose end point begins a following line
    and is never earlier than `start_line`.
    """

    segments: tuple[_Segment, ...]
    """Eligible syntax ancestry used as the candidate's structural identity.

    Reattachment first requires exact equality with the persisted origin
    sequence before considering the candidate's content digest.
    """


@dataclass(frozen=True)
class _ComposedBay:
    """One composed bay's identity and the text review reads on each side.

    This is what review needs from composition and all it needs: the public
    bay key a target may name, its kind, the text each side holds, and the path
    hint that selects a parser for structural matching. It carries nothing an
    engine produced, because `Composer.bays()` has no renderer in reach.

    An image bay reaches review through the same shape. Its text is the one
    pseudo-line it exposes, reconstructed from the picture's own facts, so a
    target against it runs through validation, placement, and excerpt reads
    without a second variant. `kind` is retained because one thing does still
    depend on it: which line ranges are valid.
    """

    bay_key: str
    """Public File-local identity emitted by the composer for this bay.

    Targets and placements retain this exact value; review does not derive a
    parallel key from paths or content.
    """

    kind: Literal["text", "image"]
    """Composition kind controlling the review coordinate contract.

    Text bays accept ranges within decoded lines. Image bays expose exactly one
    content-derived pseudo-line and therefore accept only line 1 through 1.
    """

    left_text: Optional[str]
    """Decoded left source or content-derived media pseudo-line when present.

    `None` means the composed bay has no left side. Review never substitutes
    right-side content for that absence.
    """

    right_text: Optional[str]
    """Decoded right source or content-derived media pseudo-line when present.

    `None` means the composed bay has no right side. The text is used for target
    validation, excerpts, and private relocation only.
    """

    left_hint: Optional[str]
    """Optional left-side path hint supplied by composition for parser choice.

    Absence delegates to the captured repository path. Media uses a neutral hint
    so source-language parsers do not interpret its pseudo-line.
    """

    right_hint: Optional[str]
    """Optional right-side path hint supplied by composition for parser choice.

    It follows the same absence and media rules as `left_hint` and carries no
    public placement meaning.
    """

    def text_for(self, side: Literal["left", "right"]) -> Optional[str]:
        """Return the review source for the exact requested bay side.

        `None` reports true side absence. The method performs no substitution,
        decoding, or validation beyond selecting the matching stored field.

        # Returns

        - `str`: The exact stored review source for the selected side.
        - `None`: The composed bay has no content on that side. The caller must
          preserve the absence rather than borrow the other side's text.
        """
        return self.left_text if side == "left" else self.right_text

    def hint_for(self, side: Literal["left", "right"]) -> Optional[str]:
        """Return the structural parser hint associated with one exact side.

        `None` tells the caller to use the captured repository path; it does not
        mean that the side itself is absent.

        # Returns

        - `str`: The composition-supplied parser hint for the selected side.
        - `None`: Composition supplied no override. The caller must use the
          selected side's captured repository path for parser choice.
        """
        return self.left_hint if side == "left" else self.right_hint


@dataclass
class ReviewReadCache:
    """Share composed bay identity across one review read.

    Composing a File's bays decodes both of its sides, so one read covering
    several Threads against the same File pays that cost once. Review read
    functions create and discard the cache within one operation.

    It does not persist composed content, cross Snapshot boundaries, or become
    an authoritative copy of File or Thread state.
    """

    bays: dict[str, dict[str, _ComposedBay]] = field(default_factory=dict)
    """Read-local composition results indexed by File id and public bay key.

    Entries are added on first composition and reused only within the enclosing
    review operation. The cache never crosses a Snapshot read or persists bytes.
    """


_ELIGIBLE_NODE_TYPES = frozenset(
    {
        "array",
        "array_expression",
        "arrow_function",
        "block_mapping",
        "block_sequence",
        "class_declaration",
        "class_definition",
        "decorated_definition",
        "dictionary",
        "enum_item",
        "function_declaration",
        "function_definition",
        "function_expression",
        "function_item",
        "generator_function_declaration",
        "impl_item",
        "interface_declaration",
        "keyframes_statement",
        "list",
        "media_statement",
        "method_definition",
        "object",
        "pair",
        "rule_set",
        "set",
        "struct_item",
        "supports_statement",
        "table",
        "table_array_element",
        "trait_item",
        "tuple",
    }
)
"""Tree-sitter containers that may identify a review origin structurally.

Region extraction records only these nodes as outer-to-inner segments. Leaf
syntax and punctuation cannot become identity, which keeps relocation tied to
declared source structures and the whole-text root.
"""

_LANGUAGES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    ((".py", ".pyi", ".pyw"), "tree_sitter_python", "language"),
    ((".js", ".jsx", ".mjs", ".cjs"), "tree_sitter_javascript", "language"),
    ((".ts", ".mts", ".cts"), "tree_sitter_typescript", "language_typescript"),
    ((".tsx",), "tree_sitter_typescript", "language_tsx"),
    ((".rs",), "tree_sitter_rust", "language"),
    ((".css",), "tree_sitter_css", "language"),
    ((".json",), "tree_sitter_json", "language"),
    ((".toml",), "tree_sitter_toml", "language"),
    ((".yaml", ".yml"), "tree_sitter_yaml", "language"),
    ((".md", ".markdown"), "tree_sitter_markdown", "language"),
)
"""Ordered path-suffix map for optional Tree-sitter parser selection.

Each entry names accepted lowercase suffixes, an importable language module,
and its factory. Paths matching no entry use the complete source as one region;
module loading happens only after a suffix matches.
"""


def file_pair(file: SnapshotFileRecord) -> FilePair:
    """Convert one captured File record to its exact public nullable path pair.

    Side absence is preserved and the `FilePair` boundary rechecks canonical
    path invariants. Physical capture paths and File ids do not escape.
    """
    return FilePair(
        file.left.repository_path if file.left is not None else None,
        file.right.repository_path if file.right is not None else None,
    )


def _file_indexes(
    snapshot: SnapshotRecord,
) -> tuple[dict[str, SnapshotFileRecord], dict[FilePair, SnapshotFileRecord]]:
    """Index a complete loaded Snapshot by File id and exact public pair.

    The two indexes cover every File exactly once. Duplicate ids or path pairs
    are persisted corruption and fail here before derivation chooses a target.

    # Returns

    - `First`: Every Snapshot File keyed by its durable File id.
    - `Second`: The same records keyed by exact nullable old/new repository paths.
    """
    by_id = {file.id: file for file in snapshot.files}
    by_pair = {file_pair(file): file for file in snapshot.files}
    assert len(by_id) == len(by_pair) == len(snapshot.files), (
        "Snapshot contains duplicate review File identities"
    )
    return by_id, by_pair


def _path_hint(file: SnapshotFileRecord, side: Literal["left", "right"]) -> str:
    """Return the selected side path used only to select a parser.

    # Parameters

    - `file`: Captured pair containing the selected side record.
    - `side`: Present side whose repository suffix guides parsing.
    """
    record = file.left if side == "left" else file.right
    assert record is not None, "selected review side must be present"
    return record.repository_path


@lru_cache(maxsize=3)
def _regions_for_source(path: str, text: str) -> tuple[_SourceRegion, ...]:
    """Return candidate structural regions, including the complete text root.

    The process retains only the three most recent exact path/text results.
    Thread derivation groups equal target sources so repeated placements reuse
    their current File parse without retaining historical source collections.

    # Parameters

    - `path`: Repository or bay hint used only for language selection.
    - `text`: Complete decoded bay source to partition losslessly.

    # Returns

    - `Root`: The complete text as the first and always-present candidate region.
    - `Descendants`: Eligible syntax regions in source traversal order;
      unsupported paths contribute no descendants.
    """

    def parser_for_path() -> Optional[Parser]:
        """Load the optional Tree-sitter parser selected by the source suffix.

        It is called once per uncached path/text input. An unsupported suffix
        returns `None`, which makes the complete text the sole candidate region.

        # Returns

        - `Parser`: A Tree-sitter parser for the first configured suffix match.
        - `None`: No configured language claims the path. The caller must use
          the complete text as the sole candidate region.
        """
        lower = path.lower()
        for suffixes, module_name, attribute in _LANGUAGES:
            if not lower.endswith(suffixes):
                continue
            module = importlib.import_module(module_name)
            factory = getattr(module, attribute)
            return Parser(Language(factory()))
        return None

    def node_name(node: Node, source: bytes) -> Optional[str]:
        """Return a stable declared name when one syntax node exposes it.

        # Parameters

        - `node`: Tree-sitter container whose language may define a name field.
        - `source`: UTF-8 bytes that the node's byte coordinates index.

        # Returns

        - `str`: The nonblank source text of the node's `name` field.
        - `None`: The node has no name field or its name is blank. The caller
          must retain the structural segment without a declared name.
        """
        name = node.child_by_field_name("name")
        if name is None:
            return None
        value = source[name.start_byte : name.end_byte].decode().strip()
        return value if value != "" else None

    def eligible_ancestors(node: Node, source: bytes) -> tuple[_Segment, ...]:
        """Return this node's eligible ancestry in outer-to-inner order.

        # Parameters

        - `node`: Syntax node whose enclosing structural identity is recorded.
        - `source`: UTF-8 bytes used to recover optional declared names.

        # Returns

        - `Members`: Each eligible ancestor carries its syntax kind and optional
          declared name.
        - `Order`: Ancestors run from the outermost eligible node through the
          supplied node; an empty tuple means no eligible ancestor exists.
        """
        ancestors: list[Node] = []
        current: Optional[Node] = node
        while current is not None:
            if current.type in _ELIGIBLE_NODE_TYPES:
                ancestors.append(current)
            current = current.parent
        ancestors.reverse()
        return tuple(
            _Segment(item.type, node_name(item, source)) for item in ancestors
        )

    def line_span(node: Node) -> tuple[int, int]:
        """Convert Tree-sitter points to a positive inclusive line span.

        A node ending at column zero does not include that following line; other
        end points do. The returned end is never earlier than the start.

        # Returns

        - `First`: The node's positive one-based inclusive starting line.
        - `Second`: Its positive inclusive final line, with a column-zero end
          kept on the preceding line and never earlier than the start.
        """
        start = node.start_point.row + 1
        end = node.end_point.row + (1 if node.end_point.column > 0 else 0)
        return start, max(start, end)

    source = text.encode("utf-8")
    parser = parser_for_path()
    if parser is None:
        line_count = max(1, len(text.splitlines()))
        return (_SourceRegion(source, 0, len(source), 1, line_count, ()),)
    root = parser.parse(source).root_node
    regions = [
        _SourceRegion(
            source,
            root.start_byte,
            root.end_byte,
            *line_span(root),
            (),
        )
    ]
    stack = list(reversed(root.children))
    while stack != []:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type not in _ELIGIBLE_NODE_TYPES:
            continue
        start_line, end_line = line_span(node)
        regions.append(
            _SourceRegion(
                source,
                node.start_byte,
                node.end_byte,
                start_line,
                end_line,
                eligible_ancestors(node, source),
            )
        )
    return tuple(regions)


def _composed_bays(
    file: SnapshotFileRecord,
    cache: ReviewReadCache,
) -> dict[str, _ComposedBay]:
    """Return every bay this File composes into, indexed by public key.

    This is the review bridge. The bay keys a target may name are exactly the
    keys composition produces, never an independent approximation of what the
    renderer shows, so validation and rendering cannot disagree about which
    bays exist. `Composer.bays()` takes a `BayContext`, which carries no
    renderer, so reconstructing an origin still involves no diff engine.

    Composition is total. Every pair of byte sides reaches the blob terminal,
    so the one failure this can report is its own: a File whose capture failed
    retains dirdiff's placeholder text rather than the File's bytes, and reading
    it as review content would quote a fabrication back to the reviewer. That
    raises `ReviewError("invalid_target", ...)` carrying the persisted reason.
    A caller that must survive such a File checks `SnapshotFileRecord.error`
    before calling; there is nothing else here to catch.

    # Parameters

    - `file`: Captured File whose exact sides composition reads.
    - `cache`: Read-scoped store reused by Threads addressing the same File.

    # Returns

    - `Keys`: Every public bay key produced by composition for this exact File.
    - `Values`: Text bays retain decoded sides and parser hints; image bays
      expose their content-derived pseudo-lines.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when capture recorded a
      failure for the File. Placeholder error bytes are never composed as
      reviewable content.
    - Raises `AssertionError` when retained side bytes disagree with their
      persisted SHA-256 digest. Reading a missing or inaccessible captured side
      propagates its filesystem exception.
    """
    cached = cache.bays.get(file.id)
    if cached is not None:
        return cached
    pair = file_pair(file)

    def side_bytes(side: Literal["left", "right"]) -> Optional[bytes]:
        """Read and authenticate one requested captured File side.

        The helper is invoked once per side when this File is first composed.
        It returns `None` only for true side absence, raises the persisted
        capture failure for unreadable Files, and asserts digest equality.

        # Returns

        - `bytes`: The authenticated captured bytes for the requested side.
        - `None`: The Snapshot File has no record for that side. The caller must
          preserve true side absence during composition.

        # Failures

        - Raises `ReviewError` when capture recorded a failure for the File.
        - Raises `AssertionError` when retained bytes no longer match their
          persisted digest. File reads propagate their I/O failures.
        """
        record = file.left if side == "left" else file.right
        if record is None:
            return None
        if file.error is not None:
            raise ReviewError("invalid_target", file.error)
        content = (Path(file.path) / side).read_bytes()
        assert hashlib.sha256(content).digest() == record.content_hash, (
            f"Snapshot File content hash mismatch: {file.path}/{side}"
        )
        return content

    def pseudo_line(side: Optional[MediaSide]) -> Optional[str]:
        """Render the one line an image bay exposes to review, or `None`.

        An image bay has no lines, and a target against one is defined to name
        the single line `1..1`, so review needs exactly one line of text to
        place that target in and to quote back as its excerpt. It is built
        from the media side's own media type, size, and digest. This makes it do
        real work rather than stand in for missing text. When the content
        changes, the line changes, so the region hash
        retained at creation stops matching and the Thread is reported
        outdated, which is precisely what a comment on a replaced image
        deserves.

        `None` is a side the File was not captured on, matching how a text bay
        reports the same thing.

        # Returns

        - `str`: One line containing media type, byte size, and SHA-256 digest
          for the captured image side.
        - `None`: The image bay has no side here. The caller must preserve that
          absence during target validation.
        """
        if side is None:
            return None
        ref = media_ref(side)
        return (
            f"{ref['media_type']}, {ref['byte_size']} bytes, "
            f"sha256 {ref['digest']}"
        )

    def side_link(side: Literal["left", "right"]) -> CapturedLink | None:
        """Read the relationally identified link capture for one File side.

        Side absence and an ordinary captured side both have no symlink row and
        return `None`. A present row supplies the exact physical paths and
        digests; malformed or damaged sidecars raise at the format boundary.

        # Returns

        - `CapturedLink`: Authenticated link facts for the selected side.
        - `None`: The side is absent or is an ordinary File.
        """
        record = file.left_symlink if side == "left" else file.right_symlink
        if record is None:
            return None
        return read_captured_link(
            metadata_path=Path(record.metadata_path),
            metadata_hash=record.metadata_hash,
            target_capture_path=(
                Path(record.target_capture_path)
                if record.target_capture_path is not None
                else None
            ),
            target_hash=record.target_hash,
        )

    composed = Composer().bays(
        side_bytes("left"),
        side_bytes("right"),
        BayContext(
            left_path=pair.left_path,
            right_path=pair.right_path,
            left_label="left",
            right_label="right",
            left_link=side_link("left"),
            right_link=side_link("right"),
        ),
    )
    bays = {
        bay.bay_key: (
            _ComposedBay(
                bay_key=bay.bay_key,
                kind="text",
                left_text=bay.left.text,
                right_text=bay.right.text,
                left_hint=bay.left.path_hint,
                right_hint=bay.right.path_hint,
            )
            if isinstance(bay, TextBay)
            else _ComposedBay(
                bay_key=bay.bay_key,
                kind="image",
                left_text=pseudo_line(bay.left),
                right_text=pseudo_line(bay.right),
                # A pseudo-line is not source in any language, so it must
                # not select a parser. `media` names no suffix any parser
                # claims, which keeps structural matching over the whole
                # line and off whatever the File's real extension implies.
                left_hint="media",
                right_hint="media",
            )
        )
        for bay in composed
    }
    cache.bays[file.id] = bays
    return bays


def _selected_bay(
    file: SnapshotFileRecord,
    *,
    side: Literal["left", "right"],
    bay_key: str,
    cache: ReviewReadCache,
) -> _ComposedBay:
    """Return one named bay, requiring it to exist and hold the side.

    # Parameters

    - `file`: Captured File whose composition defines valid bay identity.
    - `side`: Required present side of the bay.
    - `bay_key`: Exact public key produced by composition.
    - `cache`: Read-scoped composed-bay cache.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when composition produced
      no `bay_key` or when the named bay has no content on `side`.
    - Propagates capture, digest, and filesystem failures from `_composed_bays`.
    """
    bay = _composed_bays(file, cache).get(bay_key)
    if bay is None:
        raise ReviewError("invalid_target", "Unknown rendered bay.")
    if bay.text_for(side) is None:
        raise ReviewError(
            "invalid_target", "Bay is absent on the selected side."
        )
    return bay


def _locator_of(payload: bytes, *, side: Literal["left", "right"]) -> _Locator:
    """Decode one persisted locator, proving the payload is well formed.

    `side` is the placement's selected side. The persisted JSON repeats it and must
    agree, because a locator addressing the other side of its File would search
    the wrong source. The field set is exact: an unknown or missing key means the
    payload was written by a revision this code does not understand, and reading
    it as if it were current would silently mislocate the Thread.

    This proves the payload alone. Whether the coordinates still describe the
    origin's captured bytes is `_verify_locator()`'s question, because only that
    caller has the text.

    # Parameters

    - `payload`: Persisted private JSON bytes with the exact supported shape.
    - `side`: Origin-placement side the repeated persisted value must match.

    # Failures

    - Raises `AssertionError` when the payload is not UTF-8 JSON, is not an
      object with the exact supported fields, repeats another side, contains an
      invalid digest or byte range, or has malformed structural segments. These
      failures identify incompatible or corrupt persisted coordinates.
    """
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("private locator is not valid JSON") from exc
    assert isinstance(value, dict), "private locator must be an object"
    assert set(value) == {
        "side",
        "region_hash",
        "region_start_byte",
        "region_end_byte",
        "segments",
    }, "invalid private locator fields"
    assert value["side"] == side, "private locator side disagrees with origin"
    hash_value = value["region_hash"]
    assert isinstance(hash_value, str) and len(hash_value) == 64
    assert all(character in "0123456789abcdef" for character in hash_value)
    start_value = value["region_start_byte"]
    end_value = value["region_end_byte"]
    assert type(start_value) is int and type(end_value) is int
    assert 0 <= start_value < end_value
    segment_values = value["segments"]
    assert isinstance(segment_values, list), "private segments must be a list"
    segments: list[_Segment] = []
    for segment in segment_values:
        assert isinstance(segment, dict) and set(segment) == {
            "node_type",
            "name",
        }, "invalid private segment"
        assert (
            isinstance(segment["node_type"], str)
            and segment["node_type"].strip() != ""
        )
        assert segment["name"] is None or (
            isinstance(segment["name"], str) and segment["name"].strip() != ""
        )
        segments.append(_Segment(segment["node_type"], segment["name"]))
    locator = _Locator(
        region_hash=bytes.fromhex(hash_value),
        region_start_byte=start_value,
        region_end_byte=end_value,
        segments=tuple(segments),
    )
    assert len(locator.region_hash) == 32
    return locator


def _locator_bytes(
    locator: _Locator, *, side: Literal["left", "right"]
) -> bytes:
    """Encode one locator in the exact field set `_locator_of()` requires.

    `side` comes from the placement, which is where the decoded type keeps
    it. The two functions are a pair: a field added here without being accepted
    there fails every later read of that Thread.

    # Parameters

    - `locator`: Valid private structural coordinate for the origin region.
    - `side`: Selected placement side written into the storage payload.
    """
    return json.dumps(
        {
            "side": side,
            "region_hash": locator.region_hash.hex(),
            "region_start_byte": locator.region_start_byte,
            "region_end_byte": locator.region_end_byte,
            "segments": [
                {"node_type": segment.node_type, "name": segment.name}
                for segment in locator.segments
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _verify_locator(
    placement: RangePlacement, locator: _Locator, *, text: str
) -> None:
    """Prove one origin's locator still identifies its immutable source.

    `text` is the origin side's decoded captured text. The Snapshot File a
    locator addresses is immutable, so a disagreement here is corruption rather
    than drift, and the caller has no valid result to fall back to.

    # Parameters

    - `placement`: Origin range whose selected lines must lie in the region.
    - `locator`: Decoded byte span and digest to verify.
    - `text`: Exact decoded origin-bay text retained by the Snapshot.
    """
    source = text.encode()
    assert locator.region_end_byte <= len(source), (
        "private locator byte span exceeds its immutable origin source"
    )
    origin_slice = source[locator.region_start_byte : locator.region_end_byte]
    assert hashlib.sha256(origin_slice).digest() == locator.region_hash, (
        "private locator hash disagrees with its immutable origin region"
    )
    origin_region_start = source[: locator.region_start_byte].count(b"\n") + 1
    origin_region_prefix = source[: locator.region_end_byte]
    origin_region_end = origin_region_prefix.count(b"\n") + (
        0 if origin_region_prefix.endswith(b"\n") else 1
    )
    assert origin_region_start <= placement.start_line <= placement.end_line
    assert placement.end_line <= origin_region_end


def placement_from_record(record: ReviewThreadRecord) -> Placement:
    """Prove one stored placement's shape, once, at the read boundary.

    `RoomStore` returns every placement as the same flat row because the schema
    is one table. `target_kind` distinguishes the located shapes. The outdated
    reason distinguishes an absent File from one that is present and
    unreadable. `ReviewThreadRecord` has
    already refused any row whose
    fields disagree with its tag, so the assertions here re-state that shape to
    narrow the record's optional fields into this module's variants; a
    violation is a corrupt database rather than an input this code can place.
    """
    match record.target_kind:
        case "range":
            assert record.snapshot_file_id is not None
            assert record.bay_key is not None and record.bay_key != ""
            assert record.side is not None
            assert record.start_line is not None
            assert record.end_line is not None
            assert 1 <= record.start_line <= record.end_line
            reason = record.outdated_reason
            assert reason is None or reason == "region_changed"
            return RangePlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
                snapshot_file_id=record.snapshot_file_id,
                bay_key=record.bay_key,
                side=record.side,
                start_line=record.start_line,
                end_line=record.end_line,
                outdated_reason=reason,
            )
        case "bay-start":
            assert record.snapshot_file_id is not None
            assert record.bay_key is not None and record.bay_key != ""
            assert record.side is not None
            assert record.start_line is None and record.end_line is None
            assert record.private_locator is None
            bay_reason = record.outdated_reason
            assert (
                bay_reason == "region_not_found"
                or bay_reason == "bay_not_found"
            )
            return BayStartPlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
                snapshot_file_id=record.snapshot_file_id,
                bay_key=record.bay_key,
                side=record.side,
                outdated_reason=bay_reason,
            )
        case "file-start":
            assert record.snapshot_file_id is not None
            assert record.bay_key is None
            assert record.side is not None
            assert record.start_line is None and record.end_line is None
            assert record.private_locator is None
            start_reason = record.outdated_reason
            assert start_reason is None or start_reason == "bay_not_found"
            return FileStartPlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
                snapshot_file_id=record.snapshot_file_id,
                side=record.side,
                outdated_reason=start_reason,
            )
        case None:
            assert record.snapshot_file_id is None
            assert record.bay_key is None and record.side is None
            assert record.start_line is None and record.end_line is None
            assert record.private_locator is None
            # Both unlocated shapes persist as the same untagged row. The
            # reason is what separates a File that is gone from one that is
            # here and unreadable, so it selects the variant.
            if record.outdated_reason == "file_unreadable":
                return FileUnreadablePlacement(
                    thread_id=record.thread_id,
                    snapshot_id=record.snapshot_id,
                )
            assert record.outdated_reason == "file_missing"
            return FileMissingPlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
            )


def _record_of(
    placement: Placement,
    *,
    is_origin: bool,
    locator: Optional[_Locator],
) -> ReviewThreadRecord:
    """Convert one placement back into the flat row the store persists.

    `is_origin` and `locator` are the caller's facts, not the placement's.
    `is_origin` is a per-query label rather than a stored column, and the store
    uses it only to reject an existing origin republished into a new Snapshot.
    `locator` belongs only to a range origin; every other row stores none.

    # Parameters

    - `placement`: Proven domain variant to flatten for persistence.
    - `is_origin`: Whether this Snapshot pair is the discussion's unique origin.
    - `locator`: Private coordinates only for a range origin, otherwise `None`.
    """
    assert locator is None or isinstance(placement, RangePlacement), (
        "only a range placement stores private coordinates"
    )
    match placement:
        case RangePlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=placement.snapshot_file_id,
                is_origin=is_origin,
                target_kind="range",
                bay_key=placement.bay_key,
                side=placement.side,
                start_line=placement.start_line,
                end_line=placement.end_line,
                outdated_reason=placement.outdated_reason,
                private_locator=(
                    None
                    if locator is None
                    else _locator_bytes(locator, side=placement.side)
                ),
            )
        case BayStartPlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=placement.snapshot_file_id,
                is_origin=is_origin,
                target_kind="bay-start",
                bay_key=placement.bay_key,
                side=placement.side,
                start_line=None,
                end_line=None,
                outdated_reason=placement.outdated_reason,
                private_locator=None,
            )
        case FileStartPlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=placement.snapshot_file_id,
                is_origin=is_origin,
                target_kind="file-start",
                bay_key=None,
                side=placement.side,
                start_line=None,
                end_line=None,
                outdated_reason=placement.outdated_reason,
                private_locator=None,
            )
        case FileMissingPlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=None,
                is_origin=is_origin,
                target_kind=None,
                bay_key=None,
                side=None,
                start_line=None,
                end_line=None,
                outdated_reason="file_missing",
                private_locator=None,
            )
        case FileUnreadablePlacement():
            return ReviewThreadRecord(
                thread_id=placement.thread_id,
                snapshot_id=placement.snapshot_id,
                snapshot_file_id=None,
                is_origin=is_origin,
                target_kind=None,
                bay_key=None,
                side=None,
                start_line=None,
                end_line=None,
                outdated_reason="file_unreadable",
                private_locator=None,
            )


def _derive_record(
    *,
    origin: RangePlacement | FileStartPlacement,
    locator: Optional[_Locator],
    origin_file: SnapshotFileRecord,
    target_snapshot_id: str,
    target_files_by_pair: dict[FilePair, SnapshotFileRecord],
    cache: ReviewReadCache,
) -> Placement:
    """Derive one immutable Thread placement directly from its unique origin.

    The caller has already resolved the origin's Snapshot File and decoded the
    origin's private coordinates. `locator` is required for a range origin and is
    `None` for a File-start one, which retains none. An origin is never
    `FileMissingPlacement`, because a discussion is created against a File that
    exists.

    # Parameters

    - `origin`: Unique immutable range or retained historical File origin.
    - `locator`: Verified private coordinates required for a range origin.
    - `origin_file`: Captured File the origin row references.
    - `target_snapshot_id`: New code universe receiving the derived placement.
    - `target_files_by_pair`: Target Files indexed by the exact origin pair.
    - `cache`: Operation-scoped composed-bay cache shared across derivations.
    """

    def file_start(side: Literal["left", "right"]) -> FileStartPlacement:
        """Build the File-side landing used when the selected side has no bay.

        It is invoked only after a matching target File has been found and
        composition supplied no bay containing that side. The result records
        `bay_not_found` and retains the origin side without inventing a key.
        """
        assert target_file is not None
        return FileStartPlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            side=side,
            outdated_reason="bay_not_found",
        )

    target_file = target_files_by_pair.get(file_pair(origin_file))
    if target_file is None:
        return FileMissingPlacement(
            thread_id=origin.thread_id, snapshot_id=target_snapshot_id
        )
    if isinstance(origin, FileStartPlacement):
        assert locator is None, "a File-start origin retains no coordinates"
        assert origin.outdated_reason is None
        selected_side = (
            target_file.left if origin.side == "left" else target_file.right
        )
        assert selected_side is not None, (
            "historical File-start side disappeared from an exact File pair"
        )
        return FileStartPlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            side=origin.side,
            outdated_reason=None,
        )
    target_bay_key = origin.bay_key
    # The origin's own bytes produced this key when the Thread was created, so
    # composing them again yields it. A lookup that fails here means the Room
    # disagrees with itself, and raising is the honest report of that.
    origin_side = origin.side
    origin_bay_text = _selected_bay(
        origin_file,
        side=origin_side,
        bay_key=target_bay_key,
        cache=cache,
    ).text_for(origin_side)
    assert origin_bay_text is not None
    origin_text = origin_bay_text
    assert locator is not None, "a range origin retains its coordinates"
    _verify_locator(origin, locator, text=origin_text)
    # A File whose capture failed retains dirdiff's placeholder text, not the
    # File's own bytes, so every coordinate it could offer describes something
    # dirdiff wrote. The Thread therefore lands nowhere, which is the damage
    # boundary: raising instead would fail the whole Snapshot's Threads over
    # one unreadable File and hide every other discussion in the review. The
    # `error` text cannot travel in a placement, so it is logged here.
    if target_file.error is not None:
        target_pair = file_pair(target_file)
        LOGGER.error(
            "Thread %s has no code location: %s could not be captured in "
            "Snapshot %s: %s",
            origin.thread_id,
            target_pair.right_path or target_pair.left_path,
            target_snapshot_id,
            target_file.error,
        )
        return FileUnreadablePlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
        )
    # Placement carries the origin bay key unchanged into the target Snapshot.
    # A File that offers no bay coordinate at all lands at File start instead.
    target_bays = _composed_bays(target_file, cache)
    target_bay = target_bays.get(target_bay_key)
    target_text = (
        target_bay.text_for(origin_side) if target_bay is not None else None
    )
    if target_bay is None or target_text is None:
        # The origin's bay (or its side) is gone, but the composed bays are
        # already in hand. The first bay carrying the side is chosen and stored
        # here rather than recomputed by reads.
        for bay in target_bays.values():
            if bay.text_for(origin_side) is not None:
                return BayStartPlacement(
                    thread_id=origin.thread_id,
                    snapshot_id=target_snapshot_id,
                    snapshot_file_id=target_file.id,
                    bay_key=bay.bay_key,
                    side=origin_side,
                    outdated_reason="bay_not_found",
                )
        return file_start(origin_side)
    text = target_text
    path = target_bay.hint_for(origin_side) or _path_hint(
        target_file, origin_side
    )
    candidates = [
        region
        for region in _regions_for_source(path, text)
        if region.segments == locator.segments
    ]
    matching = [
        region
        for region in candidates
        if hashlib.sha256(
            region.source[region.start_byte : region.end_byte]
        ).digest()
        == locator.region_hash
    ]
    if len(matching) == 1:
        candidate = matching[0]
        origin_region_start = (
            origin_text.encode()[: locator.region_start_byte].count(b"\n") + 1
        )
        start_offset = origin.start_line - origin_region_start
        end_offset = origin.end_line - origin_region_start
        return RangePlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            bay_key=target_bay_key,
            side=origin_side,
            start_line=candidate.start_line + start_offset,
            end_line=candidate.start_line + end_offset,
            outdated_reason=None,
        )
    if len(candidates) == 1 and len(matching) == 0:
        candidate = candidates[0]
        return RangePlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            bay_key=target_bay_key,
            side=origin_side,
            start_line=candidate.start_line,
            end_line=candidate.start_line,
            outdated_reason="region_changed",
        )
    # The bay survives while the region inside it matched nothing or matched
    # ambiguously, so only the bay coordinate is retained.
    return BayStartPlacement(
        thread_id=origin.thread_id,
        snapshot_id=target_snapshot_id,
        snapshot_file_id=target_file.id,
        bay_key=target_bay_key,
        side=origin_side,
        outdated_reason="region_not_found",
    )


def origin_target_view(
    origin: RangePlacement | FileStartPlacement,
    file: SnapshotFileRecord,
) -> ReviewOriginView:
    """Reconstruct the immutable public creation target from retained facts.

    # Parameters

    - `origin`: Proven range or historical File-start origin placement.
    - `file`: Exact captured origin File providing its public path pair.
    """
    origin_pair = file_pair(file)
    pair: ReviewFilePairView = {
        "left_path": origin_pair.left_path,
        "right_path": origin_pair.right_path,
    }
    if isinstance(origin, FileStartPlacement):
        assert origin.outdated_reason is None
        return {"kind": "file-start", "file": pair, "side": origin.side}
    return {
        "kind": "text",
        "file": pair,
        "bay": {"bay_key": origin.bay_key},
        "side": origin.side,
        "range": {
            "start_line": origin.start_line,
            "end_line": origin.end_line,
        },
    }


def build_original_excerpt(
    origin: RangePlacement,
    origin_file: SnapshotFileRecord,
    cache: ReviewReadCache,
) -> ReviewExcerptView:
    """Return the selected origin lines with three surrounding lines.

    Creation calls this before persistence so every accepted text target can
    satisfy the mandatory Thread response. Thread reads call the same operation
    so the response cannot drift from the creation boundary. Absolute line
    coordinates identify the selected subrange inside this bounded selected-
    side source without involving a diff renderer or line alignment.

    # Parameters

    - `origin`: Immutable selected range and bay coordinate.
    - `origin_file`: Captured File retaining the selected side bytes.
    - `cache`: Read-scoped composition shared with other Thread views.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when the origin bay or side
      is absent or the selected end line exceeds the original bay text.
    - Raises `AssertionError` if `_selected_bay` returns a bay without the side
      it just required. Capture and filesystem failures propagate while the bay
      is composed.
    """

    selected_start = origin.start_line
    selected_end = origin.end_line
    # An excerpt is the origin bay's own text, never an alignment of two
    # sides, so no diff engine takes part in building one. `bays()` is the
    # engine-free entry point, reading decoded text without a renderer.
    origin_bay = _selected_bay(
        origin_file,
        side=origin.side,
        bay_key=origin.bay_key,
        cache=cache,
    )
    # `_selected_bay` above required this bay to carry the selected side, so
    # the text is present. The unselected side is not read: an excerpt is one
    # side's own text.
    selected_text = origin_bay.text_for(origin.side)
    assert selected_text is not None, (
        "_selected_bay accepted a bay absent on the selected side."
    )
    selected_lines = selected_text.splitlines()
    if selected_end > len(selected_lines):
        raise ReviewError(
            "invalid_target",
            "Review range exceeds the selected original text.",
        )
    excerpt_start = max(1, selected_start - 3)
    excerpt_end = min(len(selected_lines), selected_end + 3)
    return {
        "side": origin.side,
        "start_line": excerpt_start,
        "selected_start_line": selected_start,
        "selected_end_line": selected_end,
        "lines": selected_lines[excerpt_start - 1 : excerpt_end],
    }


def derive_room_threads(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    target_snapshot: SnapshotRecord,
) -> tuple[ReviewThreadRecord, ...]:
    """Place only Room Threads absent from one target Snapshot.

    # Parameters

    - `database`: Persistence supplying missing origins and referenced Files.
    - `identity`: Room whose complete discussion set is considered.
    - `target_snapshot`: Fully captured new Snapshot receiving placements.

    The function returns immutable rows for publication and writes nothing.

    # Usage

    Call during publication of a genuinely new Snapshot, after its complete
    File records exist in memory and before the database publication. Pass the
    returned rows to `RoomStore.publish` with that same Snapshot.

    # Returns

    - `Members`: One immutable target-Snapshot placement for each discussion
      missing there; discussions already placed in the Snapshot are absent.
    - `Order`: Placements follow File pair, side, bay, and Thread id so adjacent
      derivations can reuse source parses.

    # Failures

    - Raises `AssertionError` when an origin File is missing, a derived placement
      contradicts the target Snapshot, or persisted origin data is malformed.
    """
    origins = {
        origin.thread_id: origin
        for origin in database.review_origins_missing(
            identity,
            target_snapshot.id,
        )
    }
    # One set-based read loads exactly the origin Files these placements
    # reference; the origin Snapshots themselves are never hydrated. The
    # target Snapshot arrives fully loaded from the capture that triggered
    # this derivation.
    origin_refs = tuple(
        dict.fromkeys(
            (origin.snapshot_id, origin.snapshot_file_id)
            for origin in origins.values()
            if origin.snapshot_file_id is not None
        )
    )
    origin_files, _selected_files, _conflicts = database.review_thread_files(
        target_snapshot.id, origin_refs, (), ()
    )
    target_files_by_pair = _file_indexes(target_snapshot)[1]
    cache = ReviewReadCache()
    grouped_origins: list[
        tuple[
            tuple[str, str, str, str, str],
            RangePlacement | FileStartPlacement,
            Optional[_Locator],
            SnapshotFileRecord,
        ]
    ] = []
    for record in origins.values():
        origin = placement_from_record(record)
        assert isinstance(origin, (RangePlacement, FileStartPlacement)), (
            "a discussion origin is a stored range or File-start row"
        )
        # Derivation is the only reader of private coordinates, so this is the
        # one place that decodes them; reads never pay for it.
        locator = (
            None
            if record.private_locator is None
            else _locator_of(record.private_locator, side=origin.side)
        )
        origin_file = origin_files[
            (origin.snapshot_id, origin.snapshot_file_id)
        ]
        pair = file_pair(origin_file)
        grouped_origins.append(
            (
                (
                    pair.left_path or "",
                    pair.right_path or "",
                    origin.side,
                    origin.bay_key
                    if isinstance(origin, RangePlacement)
                    else "",
                    origin.thread_id,
                ),
                origin,
                locator,
                origin_file,
            )
        )
    # Adjacent target sources stay resident in the three-entry region cache.
    grouped_origins.sort(key=lambda item: item[0])
    placements: list[ReviewThreadRecord] = []
    for _group, origin, locator, origin_file in grouped_origins:
        # An origin already addressing the target Snapshot is its own placement
        # there and needs no derivation.
        is_origin = origin.snapshot_id == target_snapshot.id
        placed = (
            origin
            if is_origin
            else _derive_record(
                origin=origin,
                locator=locator,
                origin_file=origin_file,
                target_snapshot_id=target_snapshot.id,
                target_files_by_pair=target_files_by_pair,
                cache=cache,
            )
        )
        placements.append(
            _record_of(
                placed,
                is_origin=is_origin,
                locator=locator if is_origin else None,
            )
        )
    return tuple(placements)


def _origin_record(
    command: CreateThread,
    snapshot_id: str,
    file: SnapshotFileRecord,
    cache: ReviewReadCache,
) -> tuple[RangePlacement, _Locator]:
    """Build one unique origin and the private coordinates that retain it.

    The coordinates are returned beside the placement rather than inside it,
    because only persistence and later derivation read them.

    # Parameters

    - `command`: Validated new-Thread target and generated identities.
    - `snapshot_id`: Exact immutable Snapshot in which the origin is created.
    - `file`: Captured File matching the command's exact path pair.
    - `cache`: Operation-scoped composition shared with excerpt validation.

    # Returns

    - `First`: The immutable public origin coordinates in the selected Snapshot
      File and bay.
    - `Second`: Private region hash, byte bounds, and structural segments used
      only to derive later placements.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when the bay or selected
      side is absent, a non-text bay is addressed by anything except `1..1`, the
      range exceeds the bay text, or no source region contains the range.
    - Raises `AssertionError` if a bay accepted for the selected side yields no
      text. Capture, digest, and filesystem failures propagate from composition.
    """

    def origin_region(
        path: str, text: str, selected: LineRange
    ) -> _SourceRegion:
        """Choose the smallest source region containing the selected range.

        # Parameters

        - `path`: Selected bay hint used for structural parser choice.
        - `text`: Complete selected-side bay source.
        - `selected`: Positive bay-local range that the region must contain.

        # Usage

        Call only after composition has selected the exact bay-side text. The
        returned region becomes part of the immutable origin locator.

        # Failures

        - Raises `ReviewError` when the range exceeds the source or no parser
          region contains it.
        """
        line_count = len(text.splitlines())
        if selected.end_line > line_count:
            raise ReviewError(
                "invalid_target",
                "Review range exceeds the selected rendered text.",
            )
        candidates = [
            region
            for region in _regions_for_source(path, text)
            if region.start_line <= selected.start_line
            and region.end_line >= selected.end_line
        ]
        if candidates == []:
            raise ReviewError(
                "invalid_target",
                "Review range has no containing text region.",
            )
        return min(
            candidates,
            key=lambda region: (
                region.end_byte - region.start_byte,
                region.segments == (),
            ),
        )

    # The bay must be one composition actually produced for this File. A
    # File that composes no such bay cannot carry the target, which is what
    # rejects an ordinary target against a notebook and any stale key alike.
    selected = _selected_bay(
        file,
        side=command.target.side,
        bay_key=command.target.bay_key,
        cache=cache,
    )
    # An image bay exposes exactly one pseudo-line, so `1..1` is the only
    # coordinate that describes anything in it. The kind comes from
    # composition, so this rejects a stale line range against a File that used
    # to be text rather than trusting the range the client sent.
    if selected.kind != "text" and command.target.range != LineRange(1, 1):
        raise ReviewError(
            "invalid_target",
            "A non-text bay accepts only the single line 1 to 1.",
        )
    text = selected.text_for(command.target.side)
    assert text is not None, "selected bay text was already required"
    path = selected.hint_for(command.target.side) or _path_hint(
        file, command.target.side
    )
    region = origin_region(path, text, command.target.range)
    locator = _Locator(
        region_hash=hashlib.sha256(
            region.source[region.start_byte : region.end_byte]
        ).digest(),
        region_start_byte=region.start_byte,
        region_end_byte=region.end_byte,
        segments=region.segments,
    )
    return (
        RangePlacement(
            thread_id=command.thread_id.hex,
            snapshot_id=snapshot_id,
            snapshot_file_id=file.id,
            bay_key=command.target.bay_key,
            side=command.target.side,
            start_line=command.target.range.start_line,
            end_line=command.target.range.end_line,
            outdated_reason=None,
        ),
        locator,
    )


def plan_thread_creation(
    *,
    command: CreateThread,
    created_at: str,
    snapshot_id: str,
    target_file: Optional[SnapshotFileRecord],
    cache: ReviewReadCache,
) -> tuple[tuple[ReviewThreadRecord, ...], ReviewActionRecord]:
    """Validate and build immutable rows for one new discussion.

    The caller holds the Room write lock and supplies the focused lookup
    result for the command's target pair; `None` means the pair named no
    captured File and is rejected here, so the absence check lives in one
    place. This operation performs no insert so one or several planned
    creations can join a larger database transaction.

    # Parameters

    - `command`: New discussion identities, author, target, and first Comment.
    - `created_at`: One serialized UTC time shared by the first action.
    - `snapshot_id`: Exact origin Snapshot key in persistence form.
    - `target_file`: Focused pair lookup result, or `None` when absent.
    - `cache`: Operation-scoped composition reused by target validation and
      original-excerpt proof.

    # Returns

    - `First`: The one origin placement row, wrapped for the persistence API that
      also accepts derived placement collections.
    - `Second`: Sequence-zero Thread creation and first Comment, using the same
      identities and timestamp as the validated command.

    # Failures

    - Raises `ReviewError` with code `invalid_target` when the target File is
      absent, the first Comment is blank, or the target bay, side, or range is
      not reviewable in the origin Snapshot.
    - Propagates capture, digest, filesystem, and persisted-data failures from
      origin construction and mandatory excerpt proof. No row has been inserted
      when validation fails.
    """
    # The absence rejection precedes body validation: callers historically
    # resolved the target before planning, so a doubly-invalid creation
    # reports the absent File, not the blank body.
    if target_file is None:
        raise ReviewError(
            "invalid_target",
            "Review target File is absent from the Snapshot.",
        )
    validate_comment_body(command.body)
    profile_id = command.author.profile_id
    origin, locator = _origin_record(command, snapshot_id, target_file, cache)
    build_original_excerpt(origin, target_file, cache)
    return (
        (_record_of(origin, is_origin=True, locator=locator),),
        ReviewActionRecord(
            operation_id=command.operation_id.hex,
            thread_id=command.thread_id.hex,
            snapshot_id=snapshot_id,
            sequence=0,
            kind="thread-created",
            profile_id=profile_id,
            comment_id=command.comment_id.hex,
            expected_revision=None,
            body=command.body,
            created_at=created_at,
            status_after="open",
            attention_after="author",
        ),
    )
