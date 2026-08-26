"""Persistent review discussions bound to exact Room Snapshots.

`Thread` is the public discussion object. It is always bound to one
`(snapshot_id, thread_id)` pair and performs Comment and lifecycle operations
through that exact placement. Public commands and views describe Files,
rendered bays, attribution, and live discussion state only.

This module privately derives missing immutable placements for a new Snapshot,
interprets structural source coordinates, reconstructs snippets from captured
Files, and folds append-only actions. It does not expose matching coordinates
to Room, HTTP, or frontend callers; does not create a second history entity;
and does not select, capture, or mutate Snapshot contents.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Literal, Optional, TypedDict
from uuid import UUID

from tree_sitter import Language, Node, Parser

from dirdiff.db import (
    ReviewActionRecord,
    ReviewThreadRecord,
    ReviewThreadsRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    SnapshotRecord,
    UserProfileRecord,
)
from dirdiff.engines import DirdiffError
from dirdiff.formats import (
    BayContext,
    Composer,
    MediaSide,
    TextBay,
    media_ref,
)

__all__ = [
    "AddComment",
    "ChangeThreadState",
    "CreateThread",
    "DeleteComment",
    "DeleteThread",
    "EditComment",
    "FilePair",
    "LineRange",
    "ProfileAuthor",
    "ReplyToThread",
    "ResolveThread",
    "ReviewBatchAction",
    "ReviewBatchResult",
    "ReviewError",
    "ReviewErrorCode",
    "ReviewTarget",
    "TextTarget",
    "Thread",
    "ThreadDiscussionView",
    "ThreadSummaryView",
    # Room-facade internals: implemented here, consumed only by room_lord's
    # Room methods; every other module goes through the Room facade.
    "apply_review_batch",
    "create_thread",
    "derive_room_threads",
    "get_thread",
    "thread_objects",
]

LOGGER = logging.getLogger(__name__)
"""Report a contained File failure that no HTTP status can carry.

Derivation refuses to fail a whole Snapshot's Threads over one File dirdiff
could not capture, so the placement it stores names the failure and the
operator still gets the captured `error` text here.
"""

ReviewErrorCode = Literal[
    "profile_not_found",
    "thread_not_found",
    "comment_not_found",
    "invalid_target",
    "revision_conflict",
    "state_conflict",
    "forbidden",
]
"""Stable machine-readable review failures used by HTTP boundaries."""


class ReviewError(DirdiffError):
    """Report one expected review failure without requiring prose parsing."""

    def __init__(self, code: ReviewErrorCode, message: str) -> None:
        """Create a failure with stable `code` and human-readable `message`."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FilePair:
    """Identify one File by its exact nullable left and right captured paths."""

    left_path: Optional[str]
    right_path: Optional[str]

    def __post_init__(self) -> None:
        """Reject absent, non-canonical, absolute, or traversing identities."""
        if self.left_path is None and self.right_path is None:
            raise ValueError("A review File pair requires at least one side.")
        for value in (self.left_path, self.right_path):
            if value is None:
                continue
            path = PurePosixPath(value)
            if (
                value in {"", ".", ".."}
                or path.is_absolute()
                or ".." in path.parts
                or value != path.as_posix()
            ):
                raise ValueError(
                    "Review File sides must be normalized relative names."
                )


@dataclass(frozen=True)
class LineRange:
    """Identify one positive, one-based inclusive line range."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        """Require a positive ordered range."""
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("Review line range must be positive and ordered.")


@dataclass(frozen=True)
class TextTarget:
    """Address one selected-side line range in one composed bay.

    `bay_key` is the universal sub-file coordinate: the same key the composed
    diff gives that bay, `"flatfile"` for a File with no internal structure
    and the composer's own key for anything else. Review stores it and never
    interprets it, so no format needs a target shape of its own.
    """

    file: FilePair
    bay_key: str
    side: Literal["left", "right"]
    range: LineRange
    kind: Literal["text"] = "text"

    def __post_init__(self) -> None:
        """Require a named bay and a selected side present in the File pair."""
        if self.bay_key == "":
            raise ValueError("Review bay key cannot be empty.")
        if self.side == "left" and self.file.left_path is None:
            raise ValueError("The selected left side is absent.")
        if self.side == "right" and self.file.right_path is None:
            raise ValueError("The selected right side is absent.")


ReviewTarget = TextTarget
"""Identify one rendered text range."""


@dataclass(frozen=True)
class ProfileAuthor:
    """Identify one author through an ordinary durable Profile row."""

    profile_id: int

    def __post_init__(self) -> None:
        """Require the positive ids generated by Profile persistence."""
        if self.profile_id < 1:
            raise ValueError("Review Profile id must be positive.")


@dataclass(frozen=True)
class CreateThread:
    """Create one globally identified Thread and its first Comment."""

    thread_id: UUID
    operation_id: UUID
    comment_id: UUID
    author: ProfileAuthor
    target: ReviewTarget
    body: str


@dataclass(frozen=True)
class AddComment:
    """Append one globally identified Comment to a bound Thread."""

    operation_id: UUID
    comment_id: UUID
    author: ProfileAuthor
    body: str


@dataclass(frozen=True)
class ReplyToThread:
    """Apply one role-directed Comment instrument in an atomic batch."""

    thread_id: UUID
    command: AddComment
    instrument: Literal["author-response", "reviewer-return", "inert-comment"]


@dataclass(frozen=True)
class ResolveThread:
    """Resolve one existing Thread with a required reviewer Comment."""

    thread_id: UUID
    command: AddComment


@dataclass(frozen=True)
class DeleteThread:
    """Delete one existing Thread through the exceptional reviewer instrument."""

    thread_id: UUID
    command: ChangeThreadState


ReviewBatchAction = CreateThread | ReplyToThread | ResolveThread | DeleteThread
"""Describe the role-specific write variants accepted by an agent batch."""


@dataclass(frozen=True)
class ReviewBatchResult:
    """Return identifiers created or addressed by one applied batch action."""

    kind: Literal[
        "create-finding",
        "author-response",
        "reviewer-return",
        "reviewer-resolve",
        "inert-comment",
        "reviewer-delete",
    ]
    thread_id: UUID
    comment_id: Optional[UUID]
    status: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]

    def __post_init__(self) -> None:
        """Require Comments for every instrument except reviewer deletion."""
        assert (self.comment_id is not None) == (self.kind != "reviewer-delete")


@dataclass(frozen=True)
class EditComment:
    """Replace one authored Comment body."""

    operation_id: UUID
    author: ProfileAuthor
    body: str


@dataclass(frozen=True)
class DeleteComment:
    """Attribute one Comment tombstone to a valid acting Profile."""

    operation_id: UUID
    author: ProfileAuthor


@dataclass(frozen=True)
class ChangeThreadState:
    """Apply one Thread lifecycle transition, optionally with an explanation.

    `comment_id` and `body` together carry one new Comment recorded with the
    transition; both are `None` for a bare transition. Terminal deletion never
    carries a Comment.
    """

    operation_id: UUID
    author: ProfileAuthor
    comment_id: Optional[UUID]
    body: Optional[str]

    def __post_init__(self) -> None:
        """Require the explanation Comment id and body to arrive together."""
        assert (self.comment_id is None) == (self.body is None)


class ReviewProfileView(TypedDict):
    """Return current Profile attribution with a Comment."""

    profile_id: int
    display_name: str


class ReviewCommentView(TypedDict):
    """Return one current Comment or retained deletion tombstone."""

    comment_id: str
    sequence: int
    author: ReviewProfileView
    revision: int
    body: Optional[str]
    deleted: bool
    created_at: datetime
    updated_at: datetime


class ThreadDiscussionView(TypedDict):
    """Return bounded discussion and placement facts for one Snapshot."""

    thread_id: str
    snapshot_id: str
    created_at: datetime
    state: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    discussion_revision: int
    origin_target: dict[str, object]
    placement: dict[str, object]
    comments: list[ReviewCommentView]


class ThreadSummaryView(TypedDict):
    """Return discovery facts for one placed Thread without its excerpt.

    The lightweight agent-summary contract: the same action fold and
    placement semantics as `ThreadDiscussionView`, but no original excerpt
    is constructed (so no captured text is read from disk) and only the
    first and latest Comments travel with their total count. The origin
    travels because a placement states no File pair, bay, or side of its own:
    every coordinate a caller needs to name captured code comes from here.
    """

    thread_id: str
    state: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    origin_target: dict[str, object]
    placement: dict[str, object]
    first_comment: ReviewCommentView
    latest_comment: ReviewCommentView
    comment_count: int


class ThreadUpdateView(TypedDict):
    """Return the revision, state, and Comment changed by one action."""

    thread_id: str
    snapshot_id: str
    state: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    discussion_revision: int
    comment: Optional[ReviewCommentView]


@dataclass(frozen=True)
class _Segment:
    """Describe one private structural container in source order."""

    node_type: str
    name: Optional[str]


@dataclass(frozen=True)
class _Locator:
    """Retain only the private facts required to find an origin region.

    These are private source coordinates: they never cross the HTTP boundary and
    the store never interprets them. `_RangePlacement` owns one, and the side the
    coordinates address is the placement's own `side` rather than a field here,
    because a locator that disagreed with its placement would be unusable.

    The persisted JSON does still carry `side`, and `_locator_of()` requires it to
    agree. This type is the decoded domain value, not the storage format.
    """

    region_hash: bytes
    """SHA-256 of the origin region's bytes when the Thread was created."""

    region_start_byte: int
    """Start of the origin region within the origin side's decoded bytes."""

    region_end_byte: int
    """End of that region, exclusive; always greater than the start."""

    segments: tuple[_Segment, ...]
    """The structural containers enclosing the region, outermost first."""


@dataclass(frozen=True)
class _RangePlacement:
    """A Thread placed on a selected line range inside one composed bay.

    This is the shape every newly created Thread takes. `bay_key` names the
    bay composition produces; `start_line` and `end_line` are one-based,
    inclusive, and bay-local.

    The private locator is deliberately not a field. Only derivation reads one,
    and decoding it costs several times what the rest of this conversion does, so
    a placement carries no coordinates and `derive_room_threads()` decodes the
    single origin locator it is about to use.
    """

    thread_id: str
    snapshot_id: str
    snapshot_file_id: str
    bay_key: str
    side: Literal["left", "right"]
    start_line: int
    end_line: int
    outdated_reason: Optional[Literal["region_changed"]]


@dataclass(frozen=True)
class _BayStartPlacement:
    """A Thread placed at the start of one composed bay, its region lost.

    `region_not_found` means the bay the origin named still composes in this
    Snapshot's File but the origin region inside it matched no candidate or
    matched ambiguously, so `bay_key` is the origin's own bay.
    `bay_not_found` means the origin's bay is gone entirely, and `bay_key` is
    the File's first composed bay carrying `side` — chosen at derivation
    time, when the composed bays are already in hand, and stored so reads
    never recompute it. Derivation is the only producer; origins never take
    this shape.
    """

    thread_id: str
    snapshot_id: str
    snapshot_file_id: str
    bay_key: str
    side: Literal["left", "right"]
    outdated_reason: Literal["region_not_found", "bay_not_found"]


@dataclass(frozen=True)
class _FileStartPlacement:
    """A Thread placed at File start, with no bay coordinate to land on.

    It has no bay and no line range, so it is never navigable; History is
    its home. A reason of None marks a retained historical File-level
    origin — the only origins of this shape — and every placement derived
    from such an origin. `bay_not_found` marks a placement derived from a
    range origin whose File composes no bay carrying the side.
    """

    thread_id: str
    snapshot_id: str
    snapshot_file_id: str
    side: Literal["left", "right"]
    outdated_reason: Optional[Literal["bay_not_found"]]


@dataclass(frozen=True)
class _FileMissingPlacement:
    """A Thread with no code location, because its exact File pair is absent.

    It references no Snapshot File, and its public outdated reason is always
    `file_missing`, so neither is carried as a field.
    """

    thread_id: str
    snapshot_id: str


@dataclass(frozen=True)
class _FileUnreadablePlacement:
    """A Thread with no code location, because its File could not be captured.

    The exact File pair is present in this Snapshot — the backend listed it —
    but capture failed, so the only bytes beneath its capture directory are
    the ones dirdiff generated to stand in for the File. Nothing here can hold
    a Thread: a bay would name composed placeholder text, and File start would
    name a side record whose digest describes that same text. It references no
    Snapshot File, and its public outdated reason is always `file_unreadable`,
    so neither is carried as a field.

    This is not `_FileMissingPlacement`. That one states the File pair is
    absent from the Snapshot, and the read boundary verifies that absence;
    this one states the opposite about the same Snapshot.
    """

    thread_id: str
    snapshot_id: str


_Placement = (
    _RangePlacement
    | _BayStartPlacement
    | _FileStartPlacement
    | _FileMissingPlacement
    | _FileUnreadablePlacement
)
"""One Thread's immutable location in one Snapshot, in the shape review needs.

`RoomStore` returns the flat `ReviewThreadRecord`, whose eight optional fields can
describe any of these five shapes and cannot say which. `_placement_of()` proves
the shape once, at the read boundary, so no later consumer re-proves it;
`_record_of()` converts back for persistence. `is_origin` is deliberately absent:
it is not a stored column but a per-query label, and a discussion's origin is
already the record the store returns in its `origins` tuple.
"""


@dataclass(frozen=True)
class _SourceRegion:
    """Pair one candidate byte span with its private structural sequence."""

    source: bytes
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    segments: tuple[_Segment, ...]


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
    kind: Literal["text", "image"]
    left_text: Optional[str]
    right_text: Optional[str]
    left_hint: Optional[str]
    right_hint: Optional[str]

    def text_for(self, side: Literal["left", "right"]) -> Optional[str]:
        """Return the text this bay exposes to review on one side."""
        return self.left_text if side == "left" else self.right_text

    def hint_for(self, side: Literal["left", "right"]) -> Optional[str]:
        """Return the parser path hint for one side of this bay."""
        return self.left_hint if side == "left" else self.right_hint


@dataclass
class _ReviewReadCache:
    """Share composed bay identity across one review read.

    Composing a File's bays decodes both of its sides, so one read covering
    several Threads against the same File pays that cost once.
    """

    bays: dict[str, dict[str, _ComposedBay]] = field(default_factory=dict)


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


def _now() -> str:
    """Return one stable UTC timestamp string for immutable records."""
    return datetime.now(UTC).isoformat()


def _nonblank(body: str) -> None:
    """Reject empty or whitespace-only authored Comment text."""
    if body.strip() == "":
        raise ReviewError("invalid_target", "Comment body cannot be blank.")


def _validate_author(
    database: RoomStore, author: ProfileAuthor
) -> UserProfileRecord:
    """Return the exact durable Profile or reject the write."""
    profile = database.review_profile(author.profile_id)
    if profile is None:
        raise ReviewError(
            "profile_not_found", f"Unknown Profile: {author.profile_id}"
        )
    return profile


@contextmanager
def _room_write_lock(thread_lock: Lock, lock_path: Path) -> Iterator[None]:
    """Hold the process and File locks shared with Snapshot publication."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with thread_lock, lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _file_pair(file: SnapshotFileRecord) -> FilePair:
    """Return the exact public File identity retained by one Snapshot row."""
    return FilePair(
        file.left.repository_path if file.left is not None else None,
        file.right.repository_path if file.right is not None else None,
    )


def _file_indexes(
    snapshot: SnapshotRecord,
) -> tuple[dict[str, SnapshotFileRecord], dict[FilePair, SnapshotFileRecord]]:
    """Index one already-loaded Snapshot by File id and exact nullable pair."""
    by_id = {file.id: file for file in snapshot.files}
    by_pair = {_file_pair(file): file for file in snapshot.files}
    assert len(by_id) == len(by_pair) == len(snapshot.files), (
        "Snapshot contains duplicate review File identities"
    )
    return by_id, by_pair


def _path_hint(file: SnapshotFileRecord, side: Literal["left", "right"]) -> str:
    """Return the selected side path used only to select a parser."""
    record = file.left if side == "left" else file.right
    assert record is not None, "selected review side must be present"
    return record.repository_path


@lru_cache(maxsize=3)
def _regions_for_source(path: str, text: str) -> tuple[_SourceRegion, ...]:
    """Return candidate structural regions, including the complete text root.

    The process retains only the three most recent exact path/text results.
    Thread derivation groups equal target sources so repeated placements reuse
    their current File parse without retaining historical source collections.
    """

    def parser_for_path() -> Optional[Parser]:
        """Return the parser selected by this source's path family."""
        lower = path.lower()
        for suffixes, module_name, attribute in _LANGUAGES:
            if not lower.endswith(suffixes):
                continue
            module = importlib.import_module(module_name)
            factory = getattr(module, attribute)
            return Parser(Language(factory()))
        return None

    def node_name(node: Node, source: bytes) -> Optional[str]:
        """Return a stable declared name when one syntax node exposes it."""
        name = node.child_by_field_name("name")
        if name is None:
            return None
        value = source[name.start_byte : name.end_byte].decode().strip()
        return value if value != "" else None

    def eligible_ancestors(node: Node, source: bytes) -> tuple[_Segment, ...]:
        """Return this node's eligible ancestry in outer-to-inner order."""
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
        """Return a syntax node's positive inclusive source-line span."""
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
    cache: _ReviewReadCache,
) -> dict[str, _ComposedBay]:
    """Return every bay this File composes into, indexed by public key.

    This is the review bridge. The bay keys a target may name are exactly the
    keys composition produces, never an independent approximation of what the
    renderer shows, so validation and rendering cannot disagree about which
    bays exist. `Composer.bays()` takes a `BayContext`, which carries no
    renderer, so reconstructing an origin still involves no diff engine.

    Composition is total — every pair of byte sides reaches the blob terminal —
    so the one failure this can report is its own: a File whose capture failed
    retains dirdiff's placeholder text rather than the File's bytes, and reading
    it as review content would quote a fabrication back to the reviewer. That
    raises `ReviewError("invalid_target", ...)` carrying the persisted reason.
    A caller that must survive such a File checks `SnapshotFileRecord.error`
    before calling; there is nothing else here to catch.
    """
    cached = cache.bays.get(file.id)
    if cached is not None:
        return cached
    pair = _file_pair(file)

    def side_bytes(side: Literal["left", "right"]) -> Optional[bytes]:
        """Read one present captured side as the exact bytes it retained."""
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
        from the media side's own facts — its media type, its size, and its
        digest — which makes it do real work rather than stand in for missing
        text: when the content changes the line changes, so the region hash
        retained at creation stops matching and the Thread is reported
        outdated, which is precisely what a comment on a replaced image
        deserves.

        `None` is a side the File was not captured on, matching how a text bay
        reports the same thing.
        """
        if side is None:
            return None
        ref = media_ref(side)
        return (
            f"{ref['media_type']}, {ref['byte_size']} bytes, "
            f"sha256 {ref['digest']}"
        )

    composed = Composer().bays(
        side_bytes("left"),
        side_bytes("right"),
        BayContext(
            left_path=pair.left_path,
            right_path=pair.right_path,
            left_label="left",
            right_label="right",
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
    cache: _ReviewReadCache,
) -> _ComposedBay:
    """Return one named bay, requiring it to exist and hold the side."""
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

    `side` is the owning placement's side. The persisted JSON repeats it and must
    agree, because a locator addressing the other side of its File would search
    the wrong source. The field set is exact: an unknown or missing key means the
    payload was written by a revision this code does not understand, and reading
    it as if it were current would silently mislocate the Thread.

    This proves the payload alone. Whether the coordinates still describe the
    origin's captured bytes is `_verify_locator()`'s question, because only that
    caller has the text.
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

    `side` comes from the owning placement, which is where the decoded type keeps
    it. The two functions are a pair: a field added here without being accepted
    there fails every later read of that Thread.
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
    placement: _RangePlacement, locator: _Locator, *, text: str
) -> None:
    """Prove one origin's locator still identifies its immutable source.

    `text` is the origin side's decoded captured text. The Snapshot File a
    locator addresses is immutable, so a disagreement here is corruption rather
    than drift, and the caller has no valid result to fall back to.
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


def _placement_of(record: ReviewThreadRecord) -> _Placement:
    """Prove one stored placement's shape, once, at the read boundary.

    `RoomStore` returns every placement as the same flat row because the schema
    is one table. `target_kind` distinguishes the located shapes, and the
    outdated reason separates the two untagged ones — a File that is absent
    from a File that is present and unreadable. `ReviewThreadRecord` has
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
            return _RangePlacement(
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
            return _BayStartPlacement(
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
            return _FileStartPlacement(
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
                return _FileUnreadablePlacement(
                    thread_id=record.thread_id,
                    snapshot_id=record.snapshot_id,
                )
            assert record.outdated_reason == "file_missing"
            return _FileMissingPlacement(
                thread_id=record.thread_id,
                snapshot_id=record.snapshot_id,
            )


def _record_of(
    placement: _Placement,
    *,
    is_origin: bool,
    locator: Optional[_Locator],
) -> ReviewThreadRecord:
    """Convert one placement back into the flat row the store persists.

    `is_origin` and `locator` are the caller's facts, not the placement's.
    `is_origin` is a per-query label rather than a stored column, and the store
    uses it only to reject an existing origin republished into a new Snapshot.
    `locator` belongs only to a range origin; every other row stores none.
    """
    assert locator is None or isinstance(placement, _RangePlacement), (
        "only a range placement stores private coordinates"
    )
    match placement:
        case _RangePlacement():
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
        case _BayStartPlacement():
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
        case _FileStartPlacement():
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
        case _FileMissingPlacement():
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
        case _FileUnreadablePlacement():
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
    origin: _RangePlacement | _FileStartPlacement,
    locator: Optional[_Locator],
    origin_file: SnapshotFileRecord,
    target_snapshot_id: str,
    target_files_by_pair: dict[FilePair, SnapshotFileRecord],
    cache: _ReviewReadCache,
) -> _Placement:
    """Derive one immutable Thread placement directly from its unique origin.

    The caller has already resolved the origin's Snapshot File and decoded the
    origin's private coordinates. `locator` is required for a range origin and is
    `None` for a File-start one, which retains none. An origin is never
    `_FileMissingPlacement`, because a discussion is created against a File that
    exists.
    """

    def file_start(side: Literal["left", "right"]) -> _FileStartPlacement:
        """Return one File-start placement when no composed bay can land."""
        assert target_file is not None
        return _FileStartPlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
            snapshot_file_id=target_file.id,
            side=side,
            outdated_reason="bay_not_found",
        )

    target_file = target_files_by_pair.get(_file_pair(origin_file))
    if target_file is None:
        return _FileMissingPlacement(
            thread_id=origin.thread_id, snapshot_id=target_snapshot_id
        )
    if isinstance(origin, _FileStartPlacement):
        assert locator is None, "a File-start origin retains no coordinates"
        assert origin.outdated_reason is None
        selected_side = (
            target_file.left if origin.side == "left" else target_file.right
        )
        assert selected_side is not None, (
            "historical File-start side disappeared from an exact File pair"
        )
        return _FileStartPlacement(
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
        target_pair = _file_pair(target_file)
        LOGGER.error(
            "Thread %s has no code location: %s could not be captured in "
            "Snapshot %s: %s",
            origin.thread_id,
            target_pair.right_path or target_pair.left_path,
            target_snapshot_id,
            target_file.error,
        )
        return _FileUnreadablePlacement(
            thread_id=origin.thread_id,
            snapshot_id=target_snapshot_id,
        )
    # The bay key is durable identity, so the same key names the same
    # bay in the target Snapshot. A File that offers no bay coordinate at all
    # lands at File start instead.
    target_bays = _composed_bays(target_file, cache)
    target_bay = target_bays.get(target_bay_key)
    target_text = (
        target_bay.text_for(origin_side) if target_bay is not None else None
    )
    if target_bay is None or target_text is None:
        # The origin's bay (or its side) is gone, but the composed bays are
        # already in hand, so the landing — the first bay carrying the
        # side — is chosen and stored here rather than recomputed by reads.
        for bay in target_bays.values():
            if bay.text_for(origin_side) is not None:
                return _BayStartPlacement(
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
        return _RangePlacement(
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
        return _RangePlacement(
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
    return _BayStartPlacement(
        thread_id=origin.thread_id,
        snapshot_id=target_snapshot_id,
        snapshot_file_id=target_file.id,
        bay_key=target_bay_key,
        side=origin_side,
        outdated_reason="region_not_found",
    )


def _origin_target_dict(
    origin: _RangePlacement | _FileStartPlacement,
    file: SnapshotFileRecord,
) -> dict[str, object]:
    """Reconstruct the immutable public creation target from retained facts."""
    origin_pair = _file_pair(file)
    pair = {
        "left_path": origin_pair.left_path,
        "right_path": origin_pair.right_path,
    }
    if isinstance(origin, _FileStartPlacement):
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


@dataclass
class _CommentState:
    """Mutable fold state for one Comment while processing immutable actions."""

    comment_id: str
    sequence: int
    profile_id: int
    revision: int
    body: Optional[str]
    deleted: bool
    created_at: str
    updated_at: str


def fold_actions(
    actions: tuple[ReviewActionRecord, ...],
    profiles: dict[int, UserProfileRecord],
) -> tuple[
    Literal["open", "resolved", "deleted"],
    Literal["author", "reviewer", "both", "none"],
    list[ReviewCommentView],
]:
    """Fold ordered authored actions into current discussion state."""

    assert actions != () and actions[0].kind == "thread-created"
    assert [action.sequence for action in actions] == list(
        range(len(actions))
    ), "review action sequence must be contiguous"
    state = actions[-1].status_after
    attention = actions[-1].attention_after
    comments: dict[str, _CommentState] = {}
    order: list[str] = []
    for action in actions:
        profile_id = action.profile_id
        assert profile_id in profiles, "review action has no Profile"
        if action.comment_id is not None and action.kind in {
            "thread-created",
            "comment-created",
            "thread-resolved",
            "thread-reopened",
        }:
            assert action.comment_id is not None and action.body is not None
            assert action.expected_revision is None
            assert action.comment_id not in comments
            assert action.body.strip() != "", "persisted Comment body is blank"
            comments[action.comment_id] = _CommentState(
                action.comment_id,
                len(order),
                profile_id,
                0,
                action.body,
                False,
                action.created_at,
                action.created_at,
            )
            order.append(action.comment_id)
        elif action.kind == "comment-edited":
            assert action.comment_id is not None and action.body is not None
            assert action.body.strip() != "", "persisted Comment body is blank"
            comment = comments[action.comment_id]
            assert not comment.deleted, "deleted Comment was edited"
            assert profile_id == comment.profile_id, (
                "Comment was edited by another author"
            )
            assert action.expected_revision == comment.revision, (
                "Comment edit has a stale revision"
            )
            comment.revision += 1
            comment.body = action.body
            comment.updated_at = action.created_at
        elif action.kind == "comment-deleted":
            assert action.comment_id is not None
            comment = comments[action.comment_id]
            assert not comment.deleted, "Comment was deleted twice"
            assert action.expected_revision == comment.revision, (
                "Comment deletion has a stale revision"
            )
            comment.revision += 1
            comment.body = None
            comment.deleted = True
            comment.updated_at = action.created_at
        else:
            assert action.kind in {
                "thread-resolved",
                "thread-reopened",
                "thread-deleted",
            }
    views: list[ReviewCommentView] = []
    for comment_id in order:
        comment = comments[comment_id]
        profile = profiles[comment.profile_id]
        author = ReviewProfileView(
            profile_id=profile.id,
            display_name=profile.username,
        )
        views.append(
            {
                "comment_id": comment.comment_id,
                "sequence": comment.sequence,
                "author": author,
                "revision": comment.revision,
                "body": comment.body,
                "deleted": comment.deleted,
                "created_at": datetime.fromisoformat(comment.created_at),
                "updated_at": datetime.fromisoformat(comment.updated_at),
            }
        )
    return state, attention, views


def _build_original_excerpt(
    origin: _RangePlacement,
    origin_file: SnapshotFileRecord,
    cache: _ReviewReadCache,
) -> dict[str, object]:
    """Return the selected origin lines with three surrounding lines.

    Creation calls this before persistence so every accepted text target can
    satisfy the mandatory Thread response. Thread reads call the same operation
    so the response cannot drift from the creation boundary. Absolute line
    coordinates identify the selected subrange inside this bounded selected-
    side source without involving a diff renderer or line alignment.
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


def append_review_action(
    *,
    database: RoomStore,
    snapshot_id: UUID,
    thread_id: UUID,
    operation_id: UUID,
    author: ProfileAuthor,
    kind: Literal[
        "comment-created",
        "comment-edited",
        "comment-deleted",
        "thread-resolved",
        "thread-reopened",
        "thread-deleted",
    ],
    comment_id: Optional[UUID],
    body: Optional[str],
    comment_attention: Literal["inert", "alert"],
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[
    tuple[ReviewActionRecord, ...],
    dict[int, UserProfileRecord],
]:
    """Validate and append one action without loading captured Snapshot Files.

    Returns the appended authoritative action sequence and current Profiles;
    the caller that reports an update view folds them itself, so this write
    builds nothing its other caller discards.
    """
    with _room_write_lock(thread_lock, lock_path):
        profile_id = author.profile_id
        profile = _validate_author(database, author)
        persisted = database.review_actions(snapshot_id.hex, thread_id.hex)
        if persisted is None:
            raise ReviewError(
                "thread_not_found", f"Unknown Thread: {thread_id.hex}"
            )
        actions, persisted_profiles = persisted
        profiles = {
            persisted_profile.id: persisted_profile
            for persisted_profile in persisted_profiles
        }
        state, attention, comments = fold_actions(actions, profiles)
        if state == "deleted":
            raise ReviewError("state_conflict", "Thread is deleted.")
        comment_by_id = {comment["comment_id"]: comment for comment in comments}

        if kind == "comment-created":
            assert comment_id is not None and body is not None
            _nonblank(body)
            accepted_revision = None
        elif kind in {"comment-edited", "comment-deleted"}:
            assert comment_id is not None
            comment = comment_by_id.get(comment_id.hex)
            if comment is None:
                raise ReviewError(
                    "comment_not_found", f"Unknown Comment: {comment_id.hex}"
                )
            if (
                kind == "comment-edited"
                and comment["author"]["profile_id"] != profile_id
            ):
                raise ReviewError(
                    "forbidden", "Only the Comment author may edit it."
                )
            if comment["deleted"]:
                raise ReviewError("state_conflict", "Comment is deleted.")
            accepted_revision = comment["revision"]
            if kind == "comment-edited":
                assert body is not None
                _nonblank(body)
        else:
            accepted_revision = None
            if kind == "thread-resolved" and state != "open":
                raise ReviewError(
                    "state_conflict", "Only an open Thread may be resolved."
                )
            if kind == "thread-reopened" and state != "resolved":
                raise ReviewError(
                    "state_conflict", "Only a resolved Thread may be reopened."
                )

        record = ReviewActionRecord(
            operation_id=operation_id.hex,
            thread_id=thread_id.hex,
            snapshot_id=snapshot_id.hex,
            sequence=len(actions),
            kind=kind,
            profile_id=profile_id,
            comment_id=comment_id.hex if comment_id is not None else None,
            expected_revision=accepted_revision,
            body=body,
            created_at=_now(),
            status_after=(
                "resolved"
                if kind == "thread-resolved"
                else "open"
                if kind == "thread-reopened"
                else "deleted"
                if kind == "thread-deleted"
                else state
            ),
            attention_after=(
                "none"
                if kind in {"thread-resolved", "thread-deleted"}
                else "both"
                if kind == "thread-reopened"
                or (kind == "comment-created" and comment_attention == "alert")
                else attention
            ),
        )
        database.append_review_action(record)
        profiles[profile.id] = profile
        return (*actions, record), profiles


@dataclass
class _ThreadFiles:
    """Hold the Snapshot File records one bound Thread locates code against.

    `origin_file` is the origin Snapshot File behind the discussion;
    `selected_file` is the selected-Snapshot File the placement locates, or
    `None` for a file-missing placement whose absence the loading read has
    verified. The cache bounds repeated captured-text reads for excerpts.
    """

    origin_file: SnapshotFileRecord
    selected_file: Optional[SnapshotFileRecord]
    cache: _ReviewReadCache


class Thread:
    """Operate on one live discussion through one exact Snapshot.

    The bound keys are immutable. The object is a lightweight handle: reads
    that interpret placement load their Snapshot Files on first use, while
    writes never load Files at all. Every write reloads authoritative actions
    under the Room publication lock, validates the requested action, appends
    it, and returns the bounded authoritative update view the HTTP boundary
    reports.
    """

    def __init__(
        self,
        *,
        database: RoomStore,
        identity: RoomIdentity,
        snapshot_id: UUID,
        thread_id: UUID,
        lock_path: Path,
        thread_lock: Lock,
        placement: ReviewThreadRecord,
        origin: ReviewThreadRecord,
        actions: tuple[ReviewActionRecord, ...],
        profiles: dict[int, UserProfileRecord],
        files: Optional[_ThreadFiles],
    ) -> None:
        """Bind one Thread to its placement and optionally preloaded Files.

        `files` carries the referenced Snapshot Files when the caller already
        loaded them in bulk; `None` defers that single focused read to the
        first placement-interpreting read on this handle.
        """
        self.snapshot_id = snapshot_id
        self.thread_id = thread_id
        self._database = database
        self._identity = identity
        self._lock_path = lock_path
        self._thread_lock = thread_lock
        # The store returns the flat row shape; every read on this handle wants
        # the proven one, so both are converted once here rather than at each
        # interpreting read.
        self._placement = _placement_of(placement)
        origin_placement = _placement_of(origin)
        assert isinstance(
            origin_placement, (_RangePlacement, _FileStartPlacement)
        ), "a discussion origin is a stored range or File-start row"
        self._origin: _RangePlacement | _FileStartPlacement = origin_placement
        self._action_records = actions
        self._profiles = profiles
        # Mutated once by `_located_files` when constructed deferred; every
        # later locating read reuses the same loaded records and cache.
        self._files = files

    def _records(
        self,
    ) -> tuple[_Placement, _RangePlacement | _FileStartPlacement]:
        """Return this bound placement and the discussion's unique origin."""
        return self._placement, self._origin

    def _actions(self) -> tuple[ReviewActionRecord, ...]:
        """Return this discussion's complete ordered action sequence."""
        assert self._action_records != (), (
            "persisted Thread has no first Comment"
        )
        return self._action_records

    def _located_files(self) -> _ThreadFiles:
        """Load and retain the placement's Snapshot Files on first use.

        Handles constructed without preloaded Files pay this one focused read
        the first time a read interprets placement; the same read doubles as
        the file-missing absence proof. Writes never call this.
        """
        if self._files is None:
            placement, origin = self._records()
            origin_ref = (origin.snapshot_id, origin.snapshot_file_id)
            selected_ids: tuple[str, ...] = ()
            absent_refs: tuple[tuple[str, str], ...] = ()
            # An unreadable File is present and deliberately unreferenced, so
            # it asks for neither: proving its absence would fail, and loading
            # it would offer bytes no read may use.
            if isinstance(placement, _FileMissingPlacement):
                absent_refs = (origin_ref,)
            elif not isinstance(placement, _FileUnreadablePlacement):
                selected_ids = (placement.snapshot_file_id,)
            origin_files, selected_files, conflicts = (
                self._database.review_thread_files(
                    self.snapshot_id.hex,
                    (origin_ref,),
                    selected_ids,
                    absent_refs,
                )
            )
            assert conflicts == (), (
                "file_missing placement has an exact Snapshot File"
            )
            selected_file: Optional[SnapshotFileRecord] = None
            if selected_ids != ():
                assert not isinstance(
                    placement,
                    _FileMissingPlacement | _FileUnreadablePlacement,
                )
                selected_file = selected_files.get(placement.snapshot_file_id)
                assert selected_file is not None, (
                    "located placement has no exact Snapshot File"
                )
            self._files = _ThreadFiles(
                origin_file=origin_files[origin_ref],
                selected_file=selected_file,
                cache=_ReviewReadCache(),
            )
        return self._files

    def _placement_view(self) -> dict[str, object]:
        """Fold placement facts into the public placement.

        The returned shape names one derivation outcome and states only what
        the origin does not: the File pair and side are the origin's in every
        variant, and the bay is the origin's in all but a `bay-lost` landing,
        which names the bay derivation chose instead. `region-kept` and
        `whole-file` report nothing wrong; the six others are the complete
        public outdated vocabulary, one name per state.

        The two unlocated variants state nothing but their kind. For the
        absent File the File-loading read has already verified no
        selected-Snapshot File carries the origin pair, so absence there is an
        invariant, not a substitute.
        """
        placement, origin = self._records()
        files = self._located_files()
        if isinstance(placement, _FileUnreadablePlacement):
            return {"kind": "file-unreadable"}
        if isinstance(placement, _FileMissingPlacement):
            assert files.selected_file is None, (
                "file_missing placement has an exact Snapshot File"
            )
            return {"kind": "file-absent"}
        target_file = files.selected_file
        assert target_file is not None, (
            "located placement has no exact Snapshot File"
        )
        assert target_file.id == placement.snapshot_file_id, (
            "placement references the wrong Snapshot File"
        )
        # The File pair travels once, on the origin. A placement that named
        # another File would be read under the origin's paths with nothing
        # left to contradict it, so the equality is proven here instead.
        assert _file_pair(target_file) == _file_pair(files.origin_file), (
            "placement references the wrong Snapshot File pair"
        )
        assert placement.side == origin.side, (
            "placement selects the side the origin did not"
        )
        match placement:
            case _RangePlacement():
                # A matched region stays inside the bay it was written in, so
                # the bay the wire omits here is exactly the origin's.
                assert isinstance(origin, _RangePlacement), (
                    "a File-level origin never matches a region"
                )
                assert placement.bay_key == origin.bay_key, (
                    "a matched region left its origin's bay"
                )
                return {
                    "kind": (
                        "region-changed"
                        if placement.outdated_reason == "region_changed"
                        else "region-kept"
                    ),
                    "range": {
                        "start_line": placement.start_line,
                        "end_line": placement.end_line,
                    },
                }
            case _BayStartPlacement():
                if placement.outdated_reason == "region_not_found":
                    # Only the region inside the bay was lost, so this landing
                    # also sits in the origin's own bay.
                    assert isinstance(origin, _RangePlacement), (
                        "a File-level origin never loses a region"
                    )
                    assert placement.bay_key == origin.bay_key, (
                        "a region-lost landing left its origin's bay"
                    )
                    return {"kind": "region-lost"}
                return {
                    "kind": "bay-lost",
                    "bay": {"bay_key": placement.bay_key},
                }
            case _FileStartPlacement():
                if placement.outdated_reason is None:
                    assert isinstance(origin, _FileStartPlacement), (
                        "a text origin never rests on its File unchanged"
                    )
                    return {"kind": "whole-file"}
                return {"kind": "side-lost"}

    def discussion(self) -> ThreadDiscussionView:
        """Fold the complete discussion with its bounded original excerpt.

        The excerpt travels inside the origin it is cut from, so a File-level
        origin carries none. Index-style callers read the placement for where
        the Thread landed, and explicitly render that File when it reports
        `region-changed`.
        """
        _placement, origin = self._records()
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        files = self._located_files()
        origin_target = _origin_target_dict(origin, files.origin_file)
        if isinstance(origin, _RangePlacement):
            # Only a discussion read builds an excerpt, and it belongs to the
            # origin it is cut from. The summary path reads no captured text,
            # so the key is attached here rather than by the shared builder.
            origin_target["excerpt"] = _build_original_excerpt(
                origin, files.origin_file, files.cache
            )
        return ThreadDiscussionView(
            thread_id=self.thread_id.hex,
            snapshot_id=self.snapshot_id.hex,
            created_at=datetime.fromisoformat(actions[0].created_at),
            state=state,
            attention=attention,
            discussion_revision=len(actions) - 1,
            origin_target=origin_target,
            placement=self._placement_view(),
            comments=comments,
        )

    def summary(self) -> ThreadSummaryView:
        """Fold discovery facts without reading any captured text.

        The same action fold and placement checks as `discussion`, minus the
        original-excerpt construction and the complete Comment list. The
        origin still travels: it is where the File pair, bay, and side a
        caller needs to name captured code are stated.
        """
        actions = self._actions()
        state, attention, comments = fold_actions(actions, self._profiles)
        assert comments != [], "persisted Thread folded to zero Comments"
        _placement, origin = self._records()
        files = self._located_files()
        return ThreadSummaryView(
            thread_id=self.thread_id.hex,
            state=state,
            attention=attention,
            origin_target=_origin_target_dict(origin, files.origin_file),
            placement=self._placement_view(),
            first_comment=comments[0],
            latest_comment=comments[-1],
            comment_count=len(comments),
        )

    def _append(
        self,
        *,
        operation_id: UUID,
        author: ProfileAuthor,
        kind: Literal[
            "comment-created",
            "comment-edited",
            "comment-deleted",
            "thread-resolved",
            "thread-reopened",
            "thread-deleted",
        ],
        comment_id: Optional[UUID],
        body: Optional[str],
        comment_attention: Literal["inert", "alert"],
    ) -> ThreadUpdateView:
        """Validate, append, and return the write's bounded update view."""
        actions, profiles = append_review_action(
            database=self._database,
            snapshot_id=self.snapshot_id,
            thread_id=self.thread_id,
            operation_id=operation_id,
            author=author,
            kind=kind,
            comment_id=comment_id,
            body=body,
            comment_attention=comment_attention,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )
        # Placement and captured code are immutable. Only folded actions and
        # current Profile names change after an accepted write.
        self._action_records = actions
        self._profiles = profiles
        # The HTTP boundary is the one consumer of the update view, so every
        # write folds the bounded view it reports instead of rehydrating
        # placement.
        state, attention, comments = fold_actions(actions, profiles)
        comment = (
            next(
                folded
                for folded in comments
                if folded["comment_id"] == comment_id.hex
            )
            if comment_id is not None
            else None
        )
        return ThreadUpdateView(
            thread_id=self.thread_id.hex,
            snapshot_id=self.snapshot_id.hex,
            state=state,
            attention=attention,
            discussion_revision=len(actions) - 1,
            comment=comment,
        )

    def add_comment(
        self,
        command: AddComment,
        *,
        attention: Literal["inert", "alert"],
    ) -> ThreadUpdateView:
        """Append one Comment and return the authoritative update view.

        `attention` is the posting instrument: `alert` raises both-role
        attention with the new Comment, `inert` leaves folded attention
        unchanged.
        """
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-created",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention=attention,
        )

    def edit_comment(
        self, comment_id: UUID, command: EditComment
    ) -> ThreadUpdateView:
        """Edit one authored Comment and return the update view."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-edited",
            comment_id=comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def delete_comment(
        self, comment_id: UUID, command: DeleteComment
    ) -> ThreadUpdateView:
        """Tombstone one Comment and retain the acting Profile attribution."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-deleted",
            comment_id=comment_id,
            body=None,
            comment_attention="inert",
        )

    def resolve(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Resolve an open discussion and return the update view."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-resolved",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def reopen(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Reopen a resolved discussion and return the update view."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-reopened",
            comment_id=command.comment_id,
            body=command.body,
            comment_attention="inert",
        )

    def delete(self, command: ChangeThreadState) -> ThreadUpdateView:
        """Record terminal deletion and return the update view."""
        assert command.comment_id is None, (
            "Thread deletion never carries a Comment."
        )
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-deleted",
            comment_id=None,
            body=None,
            comment_attention="inert",
        )


def thread_objects(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
    offset: int,
    limit: int,
    state: Literal["all", "open"],
    attention: Optional[Literal["author", "reviewer"]],
    through_activity_id: Optional[int],
) -> tuple[tuple[Thread, ...], int, int]:
    """Bulk-hydrate one bounded Thread page at one inclusive activity pivot."""
    result = database.review_threads(
        identity,
        snapshot_id.hex,
        offset=offset,
        limit=limit,
        state=state,
        attention=attention,
        through_activity_id=through_activity_id,
    )
    if result is None:
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    data, concrete_activity_id = result
    return (
        _bind_threads(
            database=database,
            identity=identity,
            snapshot_id=snapshot_id,
            data=data,
            lock_path=lock_path,
            thread_lock=thread_lock,
        ),
        data.total_threads,
        concrete_activity_id,
    )


def _bind_threads(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    data: ReviewThreadsRecord,
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[Thread, ...]:
    """Bind hydrated Thread rows to exactly the Files they reference.

    One focused store read loads every referenced origin File, every located
    selected-Snapshot File, and the file-missing absence proof, replacing the
    former complete-Snapshot loads and thrown-away indexes.
    """
    placements = {row.thread_id: row for row in data.threads}
    origins = {row.thread_id: row for row in data.origins}
    actions: dict[str, list[ReviewActionRecord]] = {
        thread_id: [] for thread_id in origins
    }
    for action in data.actions:
        actions[action.thread_id].append(action)
    assert (
        len(placements) == len(origins) == len(data.origins)
        and placements.keys() == origins.keys()
    ), "review read contains duplicate Thread rows"
    profiles = {profile.id: profile for profile in data.profiles}
    assert len(profiles) == len(data.profiles), (
        "review read contains duplicate Profiles"
    )
    for origin in data.origins:
        assert origin.snapshot_file_id is not None, (
            "review origin has no Snapshot File"
        )
    origin_refs = tuple(
        dict.fromkeys(
            (origin.snapshot_id, origin.snapshot_file_id)
            for origin in data.origins
            if origin.snapshot_file_id is not None
        )
    )
    located_file_ids = tuple(
        dict.fromkeys(
            placement.snapshot_file_id
            for placement in data.threads
            if placement.snapshot_file_id is not None
        )
    )
    absent_origin_refs = tuple(
        dict.fromkeys(
            (
                origins[placement.thread_id].snapshot_id,
                origin_file_id,
            )
            for placement in data.threads
            # An unreadable File is unreferenced but present, so it is not an
            # absence to prove.
            if placement.snapshot_file_id is None
            and placement.outdated_reason != "file_unreadable"
            and (
                origin_file_id := origins[placement.thread_id].snapshot_file_id
            )
            is not None
        )
    )
    origin_files, selected_files, conflicts = database.review_thread_files(
        snapshot_id.hex,
        origin_refs,
        located_file_ids,
        absent_origin_refs,
    )
    assert conflicts == (), "file_missing placement has an exact Snapshot File"
    cache = _ReviewReadCache()
    threads: list[Thread] = []
    for origin in data.origins:
        placement = placements[origin.thread_id]
        assert origin.snapshot_file_id is not None
        selected_file: Optional[SnapshotFileRecord] = None
        if placement.snapshot_file_id is not None:
            selected_file = selected_files.get(placement.snapshot_file_id)
            assert selected_file is not None, (
                "located placement has no exact Snapshot File"
            )
        threads.append(
            Thread(
                database=database,
                identity=identity,
                snapshot_id=snapshot_id,
                thread_id=UUID(hex=origin.thread_id),
                lock_path=lock_path,
                thread_lock=thread_lock,
                placement=placement,
                origin=origin,
                actions=tuple(actions[origin.thread_id]),
                profiles=profiles,
                files=_ThreadFiles(
                    origin_file=origin_files[
                        (origin.snapshot_id, origin.snapshot_file_id)
                    ],
                    selected_file=selected_file,
                    cache=cache,
                ),
            )
        )
    return tuple(threads)


def get_thread(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    thread_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
) -> Thread:
    """Return one exact bound Thread or report that it does not exist.

    The returned handle carries placement and the action fold; it loads its
    Snapshot Files only when a read interprets placement, so write-only
    callers never pay for File hydration.
    """
    data = database.review_thread(
        identity,
        snapshot_id.hex,
        thread_id.hex,
    )
    if data is None:
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    if data.origins == ():
        raise ReviewError(
            "thread_not_found", f"Unknown Thread: {thread_id.hex}"
        )
    assert len(data.threads) == len(data.origins) == 1
    profiles = {profile.id: profile for profile in data.profiles}
    assert len(profiles) == len(data.profiles), (
        "review read contains duplicate Profiles"
    )
    return Thread(
        database=database,
        identity=identity,
        snapshot_id=snapshot_id,
        thread_id=thread_id,
        lock_path=lock_path,
        thread_lock=thread_lock,
        placement=data.threads[0],
        origin=data.origins[0],
        actions=data.actions,
        profiles=profiles,
        files=None,
    )


def derive_room_threads(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    target_snapshot: SnapshotRecord,
) -> tuple[ReviewThreadRecord, ...]:
    """Place only Room Threads absent from one target Snapshot."""
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
    cache = _ReviewReadCache()
    grouped_origins: list[
        tuple[
            tuple[str, str, str, str, str],
            _RangePlacement | _FileStartPlacement,
            Optional[_Locator],
            SnapshotFileRecord,
        ]
    ] = []
    for record in origins.values():
        origin = _placement_of(record)
        assert isinstance(origin, (_RangePlacement, _FileStartPlacement)), (
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
        pair = _file_pair(origin_file)
        grouped_origins.append(
            (
                (
                    pair.left_path or "",
                    pair.right_path or "",
                    origin.side,
                    origin.bay_key
                    if isinstance(origin, _RangePlacement)
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
    cache: _ReviewReadCache,
) -> tuple[_RangePlacement, _Locator]:
    """Build one unique origin and the private coordinates that retain it.

    The coordinates are returned beside the placement rather than inside it,
    because only persistence and later derivation read them.
    """

    def origin_region(
        path: str, text: str, selected: LineRange
    ) -> _SourceRegion:
        """Choose the smallest source region containing the selected range."""
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
        _RangePlacement(
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


def _plan_thread_creation(
    *,
    command: CreateThread,
    created_at: str,
    snapshot_id: str,
    target_file: Optional[SnapshotFileRecord],
    cache: _ReviewReadCache,
) -> tuple[tuple[ReviewThreadRecord, ...], ReviewActionRecord]:
    """Validate and build immutable rows for one new discussion.

    The caller holds the Room write lock and supplies the focused lookup
    result for the command's target pair; `None` means the pair named no
    captured File and is rejected here, so the absence check lives in one
    place. This operation performs no insert so one or several planned
    creations can join a larger database transaction.
    """
    # The absence rejection precedes body validation: callers historically
    # resolved the target before planning, so a doubly-invalid creation
    # reports the absent File, not the blank body.
    if target_file is None:
        raise ReviewError(
            "invalid_target",
            "Review target File is absent from the Snapshot.",
        )
    _nonblank(command.body)
    profile_id = command.author.profile_id
    origin, locator = _origin_record(command, snapshot_id, target_file, cache)
    _build_original_excerpt(origin, target_file, cache)
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


def create_thread(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    command: CreateThread,
    lock_path: Path,
    thread_lock: Lock,
) -> Thread:
    """Create one discussion in its immutable origin Snapshot."""

    with _room_write_lock(thread_lock, lock_path):
        profile = _validate_author(database, command.author)
        # One focused File read replaces hydrating the whole Snapshot: the
        # creation needs exactly its target File and Snapshot visibility.
        snapshot_exists, loaded_target = database.snapshot_file(
            identity,
            snapshot_id=snapshot_id.hex,
            left_path=command.target.file.left_path,
            right_path=command.target.file.right_path,
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        cache = _ReviewReadCache()
        created_at = _now()
        rows, first_action = _plan_thread_creation(
            command=command,
            created_at=created_at,
            snapshot_id=snapshot_id.hex,
            target_file=loaded_target.file
            if loaded_target is not None
            else None,
            cache=cache,
        )
        database.create_review_thread(
            rows,
            first_action,
        )
        assert loaded_target is not None
        assert len(rows) == 1 and rows[0].is_origin
        assert rows[0].snapshot_file_id == loaded_target.file.id
        created_file = loaded_target.file
        return Thread(
            database=database,
            identity=identity,
            snapshot_id=snapshot_id,
            thread_id=command.thread_id,
            lock_path=lock_path,
            thread_lock=thread_lock,
            placement=rows[0],
            origin=rows[0],
            actions=(first_action,),
            profiles={profile.id: profile},
            # A new Thread's origin Snapshot is the selected Snapshot, so one
            # File serves both bindings.
            files=_ThreadFiles(
                origin_file=created_file,
                selected_file=created_file,
                cache=cache,
            ),
        )


def apply_review_batch(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    batch: tuple[ReviewBatchAction, ...],
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[ReviewBatchResult, ...]:
    """Validate and apply one ordered multi-Thread batch atomically.

    The agent boundary generates fresh identifiers before calling. Every
    semantic check runs while holding the same Room lock used by browser
    writes. Only after all items are valid are their placement and action rows
    inserted in one database transaction.
    """
    if batch == ():
        raise ReviewError("invalid_target", "Review batch cannot be empty.")
    with _room_write_lock(thread_lock, lock_path):
        # One set-based File read replaces hydrating the whole Snapshot:
        # only the batch's distinct creation targets are loaded, in a single
        # query and transaction that also answers Snapshot visibility.
        # Absent pairs stay unmapped so planning rejects exactly the invalid
        # creation.
        creation_pairs = tuple(
            dict.fromkeys(
                action.target.file
                for action in batch
                if isinstance(action, CreateThread)
            )
        )
        snapshot_exists, found_by_pair = database.snapshot_files_by_pairs(
            identity,
            snapshot_id=snapshot_id.hex,
            pairs=tuple(
                (pair.left_path, pair.right_path) for pair in creation_pairs
            ),
        )
        if not snapshot_exists:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        selected_files_by_pair: dict[FilePair, SnapshotFileRecord] = {
            pair: found_by_pair[(pair.left_path, pair.right_path)]
            for pair in creation_pairs
            if (pair.left_path, pair.right_path) in found_by_pair
        }
        cache = _ReviewReadCache()
        placements: list[ReviewThreadRecord] = []
        records: list[ReviewActionRecord] = []
        results: list[ReviewBatchResult] = []

        # Set-based reads replace per-action queries: every author in one
        # Profile read, every addressed existing Thread history in one
        # placement/action read. The ordered in-memory fold below still lets
        # later actions observe earlier ones from the same batch, including a
        # Thread the batch itself creates.
        author_ids = [
            action.author.profile_id
            if isinstance(action, CreateThread)
            else action.command.author.profile_id
            for action in batch
        ]
        known_profiles = {
            profile.id: profile
            for profile in database.review_profiles(
                tuple(dict.fromkeys(author_ids))
            )
        }
        for author_id in author_ids:
            if author_id not in known_profiles:
                raise ReviewError(
                    "profile_not_found", f"Unknown Profile: {author_id}"
                )
        addressed = tuple(
            dict.fromkeys(
                action.thread_id.hex
                for action in batch
                if not isinstance(action, CreateThread)
            )
        )
        histories, history_profiles = database.review_actions_many(
            snapshot_id.hex, addressed
        )
        fold_profiles = {
            profile.id: profile for profile in history_profiles
        } | known_profiles
        simulated: dict[str, list[ReviewActionRecord]] = {}

        for action in batch:
            if isinstance(action, CreateThread):
                rows, first_action = _plan_thread_creation(
                    command=action,
                    created_at=_now(),
                    snapshot_id=snapshot_id.hex,
                    target_file=selected_files_by_pair.get(action.target.file),
                    cache=cache,
                )
                placements.extend(rows)
                records.append(first_action)
                # Seed the fold state so later batch actions can address the
                # Thread this batch just created.
                simulated[action.thread_id.hex] = [first_action]
                results.append(
                    ReviewBatchResult(
                        "create-finding",
                        action.thread_id,
                        action.comment_id,
                        "open",
                        "author",
                    )
                )
                continue

            thread_key = action.thread_id.hex
            thread_actions = simulated.get(thread_key)
            if thread_actions is None:
                persisted_actions = histories.get(thread_key)
                if persisted_actions is None:
                    raise ReviewError(
                        "thread_not_found",
                        f"Unknown Thread: {thread_key}",
                    )
                thread_actions = list(persisted_actions)
                simulated[thread_key] = thread_actions

            command = action.command
            profile_id = command.author.profile_id
            state, attention, _comments = fold_actions(
                tuple(thread_actions), fold_profiles
            )
            if state == "deleted":
                raise ReviewError("state_conflict", "Thread is deleted.")
            if isinstance(action, ReplyToThread):
                reply = action.command
                _nonblank(reply.body)
                allowed_attention = {
                    "author-response": {"author", "both"},
                    "reviewer-return": {"reviewer", "both"},
                    "inert-comment": {"author", "reviewer", "both", "none"},
                }[action.instrument]
                if action.instrument != "inert-comment" and (
                    state != "open" or attention not in allowed_attention
                ):
                    raise ReviewError(
                        "state_conflict",
                        f"{action.instrument} is not valid for this Thread outcome.",
                    )
                next_attention: Literal["author", "reviewer", "both", "none"]
                if action.instrument == "author-response":
                    next_attention = "reviewer"
                elif action.instrument == "reviewer-return":
                    next_attention = "author"
                else:
                    next_attention = attention
                record = ReviewActionRecord(
                    operation_id=reply.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="comment-created",
                    profile_id=profile_id,
                    comment_id=reply.comment_id.hex,
                    expected_revision=None,
                    body=reply.body,
                    created_at=_now(),
                    status_after=state,
                    attention_after=next_attention,
                )
                result = ReviewBatchResult(
                    action.instrument,
                    action.thread_id,
                    reply.comment_id,
                    state,
                    next_attention,
                )
            elif isinstance(action, ResolveThread):
                resolve = action.command
                if state != "open" or attention not in {"reviewer", "both"}:
                    raise ReviewError(
                        "state_conflict",
                        "reviewer-resolve requires an open reviewer-attention Thread.",
                    )
                _nonblank(resolve.body)
                record = ReviewActionRecord(
                    operation_id=resolve.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="thread-resolved",
                    profile_id=profile_id,
                    comment_id=resolve.comment_id.hex,
                    expected_revision=None,
                    body=resolve.body,
                    created_at=_now(),
                    status_after="resolved",
                    attention_after="none",
                )
                result = ReviewBatchResult(
                    "reviewer-resolve",
                    action.thread_id,
                    resolve.comment_id,
                    "resolved",
                    "none",
                )
            else:
                assert isinstance(action, DeleteThread)
                deletion = action.command
                record = ReviewActionRecord(
                    operation_id=deletion.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="thread-deleted",
                    profile_id=profile_id,
                    comment_id=None,
                    expected_revision=None,
                    body=None,
                    created_at=_now(),
                    status_after="deleted",
                    attention_after="none",
                )
                result = ReviewBatchResult(
                    "reviewer-delete",
                    action.thread_id,
                    None,
                    "deleted",
                    "none",
                )
            thread_actions.append(record)
            records.append(record)
            results.append(result)

        database.apply_review_batch(tuple(placements), tuple(records))
        return tuple(results)
