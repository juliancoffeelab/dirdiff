"""Persistent review discussions bound to exact Room Snapshots.

`Thread` is the public discussion object. It is always bound to one
`(snapshot_id, thread_id)` pair and performs Comment and lifecycle operations
through that exact placement. Public commands and views describe Files,
rendered regions, attribution, and live discussion state only.

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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, Literal, Optional, TypedDict
from uuid import UUID

from tree_sitter import Language, Node, Parser

from dirdiff.backend import decode_text_content
from dirdiff.db import (
    ReviewActionRecord,
    ReviewThreadRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    SnapshotRecord,
    UserProfileRecord,
)
from dirdiff.engines import DirdiffError
from dirdiff.notebooks import (
    normalize_notebook_document,
    notebook_cell_pairs,
    rendered_notebook_cell_pairs,
)

__all__ = [
    "AddComment",
    "ChangeThreadState",
    "CreateThread",
    "DeleteComment",
    "EditComment",
    "FilePair",
    "LineRange",
    "NotebookCellSourceRegion",
    "OrdinaryRegion",
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
    "ThreadUpdateView",
]

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
class OrdinaryRegion:
    """Identify the ordinary rendered text of one captured File."""

    kind: Literal["ordinary"] = "ordinary"


@dataclass(frozen=True)
class NotebookCellSourceRegion:
    """Identify rendered source inside one public notebook cell."""

    cell_key: str
    kind: Literal["notebook-cell-source"] = "notebook-cell-source"

    def __post_init__(self) -> None:
        """Require the nonempty cell key emitted by notebook rendering."""
        if self.cell_key == "":
            raise ValueError("Notebook cell key cannot be empty.")


ReviewTextRegion = OrdinaryRegion | NotebookCellSourceRegion
"""Identify one public rendered text region accepted at Thread creation."""


@dataclass(frozen=True)
class TextTarget:
    """Address one selected-side line range in a rendered text region."""

    file: FilePair
    region: ReviewTextRegion
    side: Literal["left", "right"]
    range: LineRange
    kind: Literal["text"] = "text"

    def __post_init__(self) -> None:
        """Require the selected side to exist in the exact File pair."""
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
    """Append one Comment to an existing Thread within an atomic batch."""

    thread_id: UUID
    command: AddComment


@dataclass(frozen=True)
class ResolveThread:
    """Resolve one existing Thread within an atomic batch."""

    thread_id: UUID
    command: ChangeThreadState


ReviewBatchAction = CreateThread | ReplyToThread | ResolveThread
"""Describe the three ordered write variants accepted by an agent batch."""


@dataclass(frozen=True)
class ReviewBatchResult:
    """Return identifiers created or addressed by one applied batch action."""

    kind: Literal["create", "reply", "resolve"]
    thread_id: UUID
    comment_id: Optional[UUID]

    def __post_init__(self) -> None:
        """Require Comments exactly for create and reply results."""
        assert (self.comment_id is not None) == (self.kind != "resolve")


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
    """Apply one Thread lifecycle transition."""

    operation_id: UUID
    author: ProfileAuthor


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
    state_revision: int
    discussion_revision: int
    origin_target: dict[str, object]
    code_location: Optional[dict[str, object]]
    outdated_reason: Optional[
        Literal["region_changed", "region_not_found", "file_missing"]
    ]
    original_excerpt: dict[str, object]
    comments: list[ReviewCommentView]


class ThreadUpdateView(TypedDict):
    """Return the revision, state, and Comment changed by one action."""

    thread_id: str
    snapshot_id: str
    state: Literal["open", "resolved", "deleted"]
    state_revision: int
    discussion_revision: int
    comment: Optional[ReviewCommentView]


@dataclass(frozen=True)
class _Segment:
    """Describe one private structural container in source order."""

    node_type: str
    name: Optional[str]


@dataclass(frozen=True)
class _Locator:
    """Retain only the private facts required to find an origin region."""

    side: Literal["left", "right"]
    region_hash: bytes
    region_start_byte: int
    region_end_byte: int
    segments: tuple[_Segment, ...]
    notebook_cell_id: Optional[str]
    notebook_source_hash: Optional[bytes]


@dataclass(frozen=True)
class _SourceRegion:
    """Pair one candidate byte span with its private structural sequence."""

    source: bytes
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    segments: tuple[_Segment, ...]


@dataclass
class _ReviewReadCache:
    """Share immutable text and parsed source regions in one review read."""

    text: dict[tuple[str, Literal["left", "right"]], str]
    source_regions: dict[tuple[str, str], tuple[_SourceRegion, ...]]


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


def _read_text(
    file: SnapshotFileRecord,
    side: Literal["left", "right"],
    cache: _ReviewReadCache,
) -> str:
    """Read and strictly decode one present immutable captured side."""
    key = file.id, side
    cached = cache.text.get(key)
    if cached is not None:
        return cached
    side_record = file.left if side == "left" else file.right
    if side_record is None:
        raise ReviewError("invalid_target", f"Selected {side} side is absent.")
    if file.error is not None:
        raise ReviewError("invalid_target", file.error)
    path = Path(file.path) / side
    content = path.read_bytes()
    assert hashlib.sha256(content).digest() == side_record.content_hash, (
        f"Snapshot File content hash mismatch: {path}"
    )
    try:
        text = decode_text_content(content, label=str(path))
    except DirdiffError as exc:
        raise ReviewError(
            "invalid_target",
            str(exc),
        ) from exc
    cache.text[key] = text
    return text


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


def _cell_source(cell: dict[str, Any]) -> str:
    """Return notebook cell source using the renderer's normalization."""
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return str(source)


def _cell_id(cell: dict[str, Any]) -> Optional[str]:
    """Return a nonempty notebook cell id when one exists."""
    value = str(cell.get("id", "")).strip()
    return value if value != "" else None


def _notebook_cells(text: str) -> list[dict[str, Any]]:
    """Parse notebook cells or reject a non-renderable notebook target."""
    document = normalize_notebook_document(text)
    if document is None:
        raise ReviewError(
            "invalid_target",
            "Notebook source target requires a valid notebook.",
        )
    return list(document["cells"])


def _rendered_notebook_cells(
    file: SnapshotFileRecord,
    cache: _ReviewReadCache,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Return cell lists only when this File uses the notebook render branch."""
    pair = _file_pair(file)
    if not any(
        path is not None and path.endswith(".ipynb")
        for path in (pair.left_path, pair.right_path)
    ):
        return None
    documents: list[list[dict[str, Any]]] = []
    sides: tuple[Literal["left", "right"], ...] = ("left", "right")
    for side in sides:
        present = file.left if side == "left" else file.right
        if present is None:
            documents.append([])
            continue
        document = normalize_notebook_document(_read_text(file, side, cache))
        if document is None:
            return None
        documents.append(list(document["cells"]))
    return documents[0], documents[1]


def _origin_cell(
    file: SnapshotFileRecord,
    *,
    side: Literal["left", "right"],
    cell_key: str,
    cache: _ReviewReadCache,
) -> tuple[dict[str, Any], Optional[str], bytes]:
    """Find the public origin cell and derive its private durable identity."""
    rendered_cells = _rendered_notebook_cells(file, cache)
    if rendered_cells is None:
        raise ReviewError(
            "invalid_target",
            "Notebook source target requires a rendered notebook File.",
        )
    left_cells, right_cells = rendered_cells
    matches = [
        pair
        for pair in rendered_notebook_cell_pairs(left_cells, right_cells)
        if pair.cell_key == cell_key
    ]
    if len(matches) != 1:
        raise ReviewError("invalid_target", "Unknown notebook cell key.")
    pair = matches[0]
    cell = pair.left_cell if side == "left" else pair.right_cell
    if cell is None:
        raise ReviewError(
            "invalid_target", "Notebook cell is absent on the selected side."
        )
    cells = left_cells if side == "left" else right_cells
    source_hash = hashlib.sha256(_cell_source(cell).encode()).digest()
    identifier = _cell_id(cell)
    if (
        identifier is not None
        and sum(1 for candidate in cells if _cell_id(candidate) == identifier)
        != 1
    ):
        identifier = None
    return cell, identifier, source_hash


def _target_cell(
    cells: list[dict[str, Any]],
    locator: _Locator,
) -> tuple[Optional[dict[str, Any]], bool]:
    """Find one target cell and report whether its complete source is unchanged."""
    assert locator.notebook_source_hash is not None
    if locator.notebook_cell_id is not None:
        candidates = [
            cell for cell in cells if _cell_id(cell) == locator.notebook_cell_id
        ]
        matching = [
            cell
            for cell in candidates
            if hashlib.sha256(_cell_source(cell).encode()).digest()
            == locator.notebook_source_hash
        ]
        if len(matching) == 1:
            return matching[0], True
        return (candidates[0], False) if len(candidates) == 1 else (None, False)
    matching = [
        cell
        for cell in cells
        if hashlib.sha256(_cell_source(cell).encode()).digest()
        == locator.notebook_source_hash
    ]
    return (matching[0], True) if len(matching) == 1 else (None, False)


def _cell_path(cell: dict[str, Any]) -> str:
    """Return the parser hint used for one notebook cell source."""
    match str(cell.get("cell_type", "unknown")):
        case "code":
            return "cell.py"
        case "markdown":
            return "cell.md"
        case _:
            return "cell.txt"


def _decode_locator(record: ReviewThreadRecord, *, text: str) -> _Locator:
    """Decode one locator and prove it identifies its immutable origin source."""
    assert record.is_origin and record.target_kind == "range"
    assert record.region_kind is not None
    assert record.side is not None
    assert record.start_line is not None and record.end_line is not None
    assert record.private_locator is not None
    try:
        value = json.loads(record.private_locator)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("private locator is not valid JSON") from exc
    assert isinstance(value, dict), "private locator must be an object"
    assert set(value) == {
        "side",
        "region_hash",
        "region_start_byte",
        "region_end_byte",
        "segments",
        "notebook_cell_id",
        "notebook_source_hash",
    }, "invalid private locator fields"
    side_value = value["side"]
    assert side_value in {"left", "right"}, "invalid private locator side"
    side: Literal["left", "right"] = side_value
    assert side == record.side, "private locator side disagrees with origin"
    hash_value = value["region_hash"]
    assert isinstance(hash_value, str) and len(hash_value) == 64
    assert all(character in "0123456789abcdef" for character in hash_value)
    start_value = value["region_start_byte"]
    end_value = value["region_end_byte"]
    assert type(start_value) is int and type(end_value) is int
    source = text.encode()
    assert 0 <= start_value < end_value <= len(source), (
        "private locator byte span exceeds its immutable origin source"
    )
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
    notebook_hash_value = value["notebook_source_hash"]
    assert notebook_hash_value is None or (
        isinstance(notebook_hash_value, str)
        and len(notebook_hash_value) == 64
        and all(
            character in "0123456789abcdef" for character in notebook_hash_value
        )
    )
    notebook_cell_id = value["notebook_cell_id"]
    assert notebook_cell_id is None or (
        isinstance(notebook_cell_id, str) and notebook_cell_id.strip() != ""
    )
    if record.region_kind == "ordinary":
        assert record.region_key is None
        assert notebook_cell_id is None and notebook_hash_value is None
    else:
        assert record.region_key is not None and record.region_key != ""
        assert notebook_hash_value is not None
    locator = _Locator(
        side=side,
        region_hash=bytes.fromhex(hash_value),
        region_start_byte=start_value,
        region_end_byte=end_value,
        segments=tuple(segments),
        notebook_cell_id=notebook_cell_id,
        notebook_source_hash=(
            bytes.fromhex(notebook_hash_value)
            if notebook_hash_value is not None
            else None
        ),
    )
    assert len(locator.region_hash) == 32
    assert locator.notebook_source_hash is None or (
        len(locator.notebook_source_hash) == 32
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
    assert origin_region_start <= record.start_line <= record.end_line
    assert record.end_line <= origin_region_end
    return locator


def _derive_record(
    *,
    origin: ReviewThreadRecord,
    origin_snapshot: SnapshotRecord,
    target_snapshot: SnapshotRecord,
    origin_files_by_id: dict[str, SnapshotFileRecord],
    target_files_by_pair: dict[FilePair, SnapshotFileRecord],
    cache: _ReviewReadCache,
) -> ReviewThreadRecord:
    """Derive one immutable Thread placement directly from its unique origin."""

    def file_start_record(
        snapshot_id: str,
        file_id: str,
        side: Literal["left", "right"],
    ) -> ReviewThreadRecord:
        """Return one File-start placement after region identification fails."""
        return ReviewThreadRecord(
            origin.thread_id,
            snapshot_id,
            file_id,
            False,
            "file-start",
            None,
            None,
            side,
            None,
            None,
            "region_not_found",
            None,
        )

    def file_missing_record() -> ReviewThreadRecord:
        """Return an unlocated placement when the exact File pair is absent."""
        return ReviewThreadRecord(
            origin.thread_id,
            target_snapshot.id,
            None,
            False,
            None,
            None,
            None,
            None,
            None,
            None,
            "file_missing",
            None,
        )

    assert origin.snapshot_file_id is not None
    origin_file = origin_files_by_id.get(origin.snapshot_file_id)
    assert origin_file is not None, (
        "origin Thread references a missing Snapshot File"
    )
    target_file = target_files_by_pair.get(_file_pair(origin_file))
    if target_file is None:
        return file_missing_record()
    assert origin.target_kind == "range"
    assert origin.side is not None
    origin_text = _read_text(origin_file, origin.side, cache)
    if origin.region_kind == "notebook-cell-source":
        assert origin.region_key is not None
        origin_cell, _, _ = _origin_cell(
            origin_file,
            side=origin.side,
            cell_key=origin.region_key,
            cache=cache,
        )
        origin_text = _cell_source(origin_cell)
    locator = _decode_locator(origin, text=origin_text)
    target_region_key = origin.region_key
    try:
        if (
            origin.region_kind == "ordinary"
            and _rendered_notebook_cells(target_file, cache) is not None
        ):
            return file_start_record(
                target_snapshot.id, target_file.id, locator.side
            )
        text = _read_text(target_file, locator.side, cache)
        path = _path_hint(target_file, locator.side)
        if origin.region_kind == "notebook-cell-source":
            selected_cells = _notebook_cells(text)
            cell, _cell_unchanged = _target_cell(selected_cells, locator)
            if cell is None:
                return file_start_record(
                    target_snapshot.id, target_file.id, locator.side
                )
            opposite_side: Literal["left", "right"] = (
                "right" if locator.side == "left" else "left"
            )
            opposite_record = (
                target_file.right
                if opposite_side == "right"
                else target_file.left
            )
            opposite_cells = (
                _notebook_cells(_read_text(target_file, opposite_side, cache))
                if opposite_record is not None
                else []
            )
            left_cells = (
                selected_cells if locator.side == "left" else opposite_cells
            )
            right_cells = (
                opposite_cells if locator.side == "left" else selected_cells
            )
            pairs = [
                pair
                for pair in rendered_notebook_cell_pairs(
                    left_cells, right_cells
                )
                if (
                    pair.left_cell is cell
                    if locator.side == "left"
                    else pair.right_cell is cell
                )
            ]
            if len(pairs) != 1:
                return file_start_record(
                    target_snapshot.id, target_file.id, locator.side
                )
            target_region_key = pairs[0].cell_key
            text = _cell_source(cell)
            path = _cell_path(cell)
        candidates = [
            region
            for region in _regions_for_source(path, text)
            if region.segments == locator.segments
        ]
    except ReviewError:
        return file_start_record(
            target_snapshot.id, target_file.id, locator.side
        )
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
        assert origin.start_line is not None and origin.end_line is not None
        start_offset = origin.start_line - origin_region_start
        end_offset = origin.end_line - origin_region_start
        return ReviewThreadRecord(
            origin.thread_id,
            target_snapshot.id,
            target_file.id,
            False,
            "range",
            origin.region_kind,
            target_region_key,
            locator.side,
            candidate.start_line + start_offset,
            candidate.start_line + end_offset,
            None,
            None,
        )
    if len(candidates) == 1 and len(matching) == 0:
        candidate = candidates[0]
        return ReviewThreadRecord(
            origin.thread_id,
            target_snapshot.id,
            target_file.id,
            False,
            "range",
            origin.region_kind,
            target_region_key,
            locator.side,
            candidate.start_line,
            candidate.start_line,
            "region_changed",
            None,
        )
    return file_start_record(target_snapshot.id, target_file.id, locator.side)


def _pair_dict(pair: FilePair) -> dict[str, Optional[str]]:
    """Serialize one exact File pair without adding presentation fields."""
    return {"left_path": pair.left_path, "right_path": pair.right_path}


def _region_dict(
    kind: Literal["ordinary", "notebook-cell-source"],
    key: Optional[str],
) -> dict[str, str]:
    """Serialize one public rendered-region identity."""
    if kind == "ordinary":
        assert key is None
        return {"kind": "ordinary"}
    assert key is not None
    return {"kind": "notebook-cell-source", "cell_key": key}


def _origin_target_dict(
    origin: ReviewThreadRecord,
    file: SnapshotFileRecord,
) -> dict[str, object]:
    """Reconstruct the immutable public creation target from retained facts."""
    pair = _pair_dict(_file_pair(file))
    assert origin.target_kind == "range"
    assert origin.region_kind is not None
    assert origin.side is not None
    assert origin.start_line is not None and origin.end_line is not None
    return {
        "kind": "text",
        "file": pair,
        "region": _region_dict(origin.region_kind, origin.region_key),
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


def _fold_actions(
    actions: tuple[ReviewActionRecord, ...],
    profiles: dict[int, UserProfileRecord],
) -> tuple[
    Literal["open", "resolved", "deleted"],
    int,
    list[ReviewCommentView],
]:
    """Fold ordered authored actions into current discussion state."""

    assert actions != () and actions[0].kind == "comment-created"
    assert [action.sequence for action in actions] == list(
        range(len(actions))
    ), "review action sequence must be contiguous"
    state: Literal["open", "resolved", "deleted"] = "open"
    state_revision = 0
    comments: dict[str, _CommentState] = {}
    order: list[str] = []
    for action in actions:
        profile_id = action.profile_id
        assert profile_id in profiles, "review action has no Profile"
        assert state != "deleted", "deleted Thread has later actions"
        if action.kind == "comment-created":
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
        elif action.kind == "thread-resolved":
            assert action.expected_revision == state_revision, (
                "Thread resolve has a stale revision"
            )
            assert state == "open", "only an open Thread may be resolved"
            state = "resolved"
            state_revision += 1
        elif action.kind == "thread-reopened":
            assert action.expected_revision == state_revision, (
                "Thread reopen has a stale revision"
            )
            assert state == "resolved", "only a resolved Thread may be reopened"
            state = "open"
            state_revision += 1
        else:
            assert action.kind == "thread-deleted"
            assert action.expected_revision == state_revision, (
                "Thread deletion has a stale revision"
            )
            state = "deleted"
            state_revision += 1
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
    return state, state_revision, views


def _build_original_excerpt(
    origin: ReviewThreadRecord,
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

    def cell_pair_sources() -> tuple[str, str]:
        """Return sources paired by the origin's public cell key."""
        assert origin.region_key is not None and origin.side is not None
        left_cells = (
            _notebook_cells(_read_text(origin_file, "left", cache))
            if origin_file.left is not None
            else []
        )
        right_cells = (
            _notebook_cells(_read_text(origin_file, "right", cache))
            if origin_file.right is not None
            else []
        )
        matches = [
            pair
            for pair in notebook_cell_pairs(left_cells, right_cells)
            if pair.cell_key == origin.region_key
        ]
        assert len(matches) == 1, "origin notebook cell pair disappeared"
        pair = matches[0]
        selected = pair.left_cell if origin.side == "left" else pair.right_cell
        assert selected is not None, "origin selected notebook side disappeared"
        return (
            _cell_source(pair.left_cell) if pair.left_cell is not None else "",
            _cell_source(pair.right_cell)
            if pair.right_cell is not None
            else "",
        )

    assert origin.target_kind == "range"
    assert origin.region_kind is not None and origin.side is not None
    assert origin.start_line is not None and origin.end_line is not None
    selected_start = origin.start_line
    selected_end = origin.end_line
    if origin.region_kind == "ordinary":
        left_text = (
            _read_text(origin_file, "left", cache)
            if origin_file.left is not None
            else ""
        )
        right_text = (
            _read_text(origin_file, "right", cache)
            if origin_file.right is not None
            else ""
        )
    else:
        left_text, right_text = cell_pair_sources()
    selected_lines = (
        left_text.splitlines()
        if origin.side == "left"
        else right_text.splitlines()
    )
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


def _append_review_action(
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
    lock_path: Path,
    thread_lock: Lock,
) -> tuple[
    tuple[ReviewActionRecord, ...],
    dict[int, UserProfileRecord],
    ThreadUpdateView,
]:
    """Validate and append one action without loading captured Snapshot Files."""
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
        state, state_revision, comments = _fold_actions(actions, profiles)
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
            accepted_revision = state_revision
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
        )
        database.append_review_action(record)
        profiles[profile.id] = profile
        updated_actions = (*actions, record)
        updated_state, updated_revision, updated_comments = _fold_actions(
            updated_actions, profiles
        )
        updated_comment = (
            next(
                comment
                for comment in updated_comments
                if comment["comment_id"] == comment_id.hex
            )
            if comment_id is not None
            else None
        )
        return (
            updated_actions,
            profiles,
            ThreadUpdateView(
                thread_id=thread_id.hex,
                snapshot_id=snapshot_id.hex,
                state=updated_state,
                state_revision=updated_revision,
                discussion_revision=len(updated_actions) - 1,
                comment=updated_comment,
            ),
        )


class Thread:
    """Operate on one live discussion through one exact Snapshot.

    The bound keys are immutable. Reads return placement for that Snapshot and
    the latest shared discussion state. Every write reloads authoritative
    actions under the Room publication lock, validates the requested action,
    appends it, and returns a fresh complete view.
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
        origin_files_by_id: dict[str, SnapshotFileRecord],
        selected_files_by_pair: dict[FilePair, SnapshotFileRecord],
        cache: _ReviewReadCache,
    ) -> None:
        """Bind one Thread to indexed read facts and its construction outcome."""
        self.snapshot_id = snapshot_id
        self.thread_id = thread_id
        self._database = database
        self._identity = identity
        self._lock_path = lock_path
        self._thread_lock = thread_lock
        self._placement = placement
        self._origin = origin
        self._action_records = actions
        self._profiles = profiles
        self._origin_files_by_id = origin_files_by_id
        self._selected_files_by_pair = selected_files_by_pair
        self._cache = cache

    def _records(self) -> tuple[ReviewThreadRecord, ReviewThreadRecord]:
        """Return this bound placement and the discussion's unique origin."""
        return self._placement, self._origin

    def _actions(self) -> tuple[ReviewActionRecord, ...]:
        """Return this discussion's complete ordered action sequence."""
        assert self._action_records != (), (
            "persisted Thread has no first Comment"
        )
        return self._action_records

    def _materialize(self) -> ThreadDiscussionView:
        """Fold one discussion with its bounded original excerpt."""
        placement, origin = self._records()
        assert origin.snapshot_file_id is not None
        origin_file = self._origin_files_by_id.get(origin.snapshot_file_id)
        assert origin_file is not None, (
            "review origin references a missing Snapshot File"
        )

        actions = self._actions()
        state, state_revision, comments = _fold_actions(actions, self._profiles)
        code_location: Optional[dict[str, object]]
        target_file: Optional[SnapshotFileRecord] = None
        expected_file = self._selected_files_by_pair.get(
            _file_pair(origin_file)
        )
        if placement.snapshot_file_id is None:
            assert placement.outdated_reason == "file_missing"
            assert expected_file is None, (
                "file_missing placement has an exact Snapshot File"
            )
            code_location = None
        else:
            assert expected_file is not None, (
                "located placement has no exact Snapshot File"
            )
            assert expected_file.id == placement.snapshot_file_id, (
                "placement references the wrong Snapshot File pair"
            )
            target_file = expected_file
            pair = _pair_dict(_file_pair(target_file))
            if placement.target_kind == "range":
                assert placement.region_kind is not None
                assert placement.side is not None
                assert placement.start_line is not None
                assert placement.end_line is not None
                code_location = {
                    "kind": "range",
                    "file": pair,
                    "region": _region_dict(
                        placement.region_kind, placement.region_key
                    ),
                    "side": placement.side,
                    "range": {
                        "start_line": placement.start_line,
                        "end_line": placement.end_line,
                    },
                }
            else:
                assert placement.target_kind == "file-start"
                assert placement.side is not None
                code_location = {
                    "kind": "file-start",
                    "file": pair,
                    "side": placement.side,
                }
        assert origin.target_kind == "range"
        return ThreadDiscussionView(
            thread_id=self.thread_id.hex,
            snapshot_id=self.snapshot_id.hex,
            created_at=datetime.fromisoformat(actions[0].created_at),
            state=state,
            state_revision=state_revision,
            discussion_revision=len(actions) - 1,
            origin_target=_origin_target_dict(origin, origin_file),
            code_location=code_location,
            outdated_reason=placement.outdated_reason,
            original_excerpt=_build_original_excerpt(
                origin, origin_file, self._cache
            ),
            comments=comments,
        )

    def discussion(self) -> ThreadDiscussionView:
        """Return bounded discussion context without a changed-region render.

        Index-style callers use the public location and explicitly render that
        File when an outdated Thread reports `region_changed`.
        """
        return self._materialize()

    def summary(self) -> ThreadDiscussionView:
        """Return the same bounded discussion view used by full reads."""
        return self._materialize()

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
    ) -> Thread:
        """Validate, append, and return the bound Thread with write outcome."""
        actions, profiles, _ = _append_review_action(
            database=self._database,
            snapshot_id=self.snapshot_id,
            thread_id=self.thread_id,
            operation_id=operation_id,
            author=author,
            kind=kind,
            comment_id=comment_id,
            body=body,
            lock_path=self._lock_path,
            thread_lock=self._thread_lock,
        )
        # Placement and captured code are immutable. Only folded actions and
        # current Profile names change after an accepted write.
        self._action_records = actions
        self._profiles = profiles
        return self

    def add_comment(self, command: AddComment) -> Thread:
        """Append one Comment and return the authoritative bound Thread."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-created",
            comment_id=command.comment_id,
            body=command.body,
        )

    def edit_comment(self, comment_id: UUID, command: EditComment) -> Thread:
        """Edit one authored Comment and return the authoritative Thread."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-edited",
            comment_id=comment_id,
            body=command.body,
        )

    def delete_comment(
        self, comment_id: UUID, command: DeleteComment
    ) -> Thread:
        """Tombstone one Comment and retain the acting Profile attribution."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="comment-deleted",
            comment_id=comment_id,
            body=None,
        )

    def resolve(self, command: ChangeThreadState) -> Thread:
        """Resolve an open discussion and return the authoritative Thread."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-resolved",
            comment_id=None,
            body=None,
        )

    def reopen(self, command: ChangeThreadState) -> Thread:
        """Reopen a resolved discussion and return the authoritative Thread."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-reopened",
            comment_id=None,
            body=None,
        )

    def delete(self, command: ChangeThreadState) -> Thread:
        """Record terminal deletion and return the authoritative Thread."""
        return self._append(
            operation_id=command.operation_id,
            author=command.author,
            kind="thread-deleted",
            comment_id=None,
            body=None,
        )


def _thread_objects(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
    offset: int,
    limit: int,
    state: Literal["all", "open"],
    activity_id: int,
) -> tuple[tuple[Thread, ...], int]:
    """Bulk-hydrate one bounded Thread page at one activity boundary."""
    data = database.review_snapshot(
        identity,
        snapshot_id.hex,
        offset=offset,
        limit=limit,
        state=state,
        activity_id=activity_id,
    )
    if data is None:
        raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
    required_snapshot_ids = {snapshot_id.hex} | {
        origin.snapshot_id for origin in data.origins
    }
    snapshots: dict[str, SnapshotRecord] = {}
    for required_snapshot_id in required_snapshot_ids:
        snapshot = database.snapshot(identity, required_snapshot_id)
        assert snapshot is not None, (
            "review placement references a missing Room Snapshot"
        )
        snapshots[required_snapshot_id] = snapshot
    file_indexes = {
        required_snapshot_id: _file_indexes(snapshot)
        for required_snapshot_id, snapshot in snapshots.items()
    }
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
    cache = _ReviewReadCache({}, {})
    return tuple(
        Thread(
            database=database,
            identity=identity,
            snapshot_id=snapshot_id,
            thread_id=UUID(hex=origin.thread_id),
            lock_path=lock_path,
            thread_lock=thread_lock,
            placement=placements[origin.thread_id],
            origin=origin,
            actions=tuple(actions[origin.thread_id]),
            profiles=profiles,
            origin_files_by_id=file_indexes[origin.snapshot_id][0],
            selected_files_by_pair=file_indexes[snapshot_id.hex][1],
            cache=cache,
        )
        for origin in data.origins
    ), data.total_threads


def _get_thread(
    *,
    database: RoomStore,
    identity: RoomIdentity,
    snapshot_id: UUID,
    thread_id: UUID,
    lock_path: Path,
    thread_lock: Lock,
) -> Thread:
    """Return one exact bound Thread or report that it does not exist."""
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
    origin = data.origins[0]
    required_snapshot_ids = {snapshot_id.hex, origin.snapshot_id}
    snapshots: dict[str, SnapshotRecord] = {}
    for required_snapshot_id in required_snapshot_ids:
        snapshot = database.snapshot(identity, required_snapshot_id)
        assert snapshot is not None, (
            "review placement references a missing Room Snapshot"
        )
        snapshots[required_snapshot_id] = snapshot
    file_indexes = {
        required_snapshot_id: _file_indexes(snapshot)
        for required_snapshot_id, snapshot in snapshots.items()
    }
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
        origin=origin,
        actions=data.actions,
        profiles=profiles,
        origin_files_by_id=file_indexes[origin.snapshot_id][0],
        selected_files_by_pair=file_indexes[snapshot_id.hex][1],
        cache=_ReviewReadCache({}, {}),
    )


def _derive_room_threads(
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
    snapshots = {target_snapshot.id: target_snapshot}
    for origin in origins.values():
        if origin.snapshot_id not in snapshots:
            snapshot = database.snapshot(identity, origin.snapshot_id)
            assert snapshot is not None, (
                "review origin references a missing Room Snapshot"
            )
            snapshots[origin.snapshot_id] = snapshot
    file_indexes = {
        snapshot_id: _file_indexes(snapshot)
        for snapshot_id, snapshot in snapshots.items()
    }
    cache = _ReviewReadCache({}, {})
    grouped_origins: list[
        tuple[tuple[str, str, str, str, str], ReviewThreadRecord]
    ] = []
    for origin in origins.values():
        assert origin.snapshot_file_id is not None
        assert origin.side is not None
        origin_file = file_indexes[origin.snapshot_id][0].get(
            origin.snapshot_file_id
        )
        assert origin_file is not None, (
            "origin Thread references a missing Snapshot File"
        )
        pair = _file_pair(origin_file)
        grouped_origins.append(
            (
                (
                    pair.left_path or "",
                    pair.right_path or "",
                    origin.side,
                    origin.region_key or "",
                    origin.thread_id,
                ),
                origin,
            )
        )
    # Adjacent target sources stay resident in the three-entry region cache.
    grouped_origins.sort(key=lambda item: item[0])
    placements: list[ReviewThreadRecord] = []
    for _group, origin in grouped_origins:
        placements.append(
            origin
            if origin.snapshot_id == target_snapshot.id
            else _derive_record(
                origin=origin,
                origin_snapshot=snapshots[origin.snapshot_id],
                target_snapshot=target_snapshot,
                origin_files_by_id=file_indexes[origin.snapshot_id][0],
                target_files_by_pair=file_indexes[target_snapshot.id][1],
                cache=cache,
            )
        )
    return tuple(placements)


def _origin_record(
    command: CreateThread,
    snapshot: SnapshotRecord,
    file: SnapshotFileRecord,
    cache: _ReviewReadCache,
) -> ReviewThreadRecord:
    """Build one unique origin from an already-selected Snapshot File."""

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

    def encode_locator(locator: _Locator) -> bytes:
        """Serialize one private locator deterministically."""
        return json.dumps(
            {
                "side": locator.side,
                "region_hash": locator.region_hash.hex(),
                "region_start_byte": locator.region_start_byte,
                "region_end_byte": locator.region_end_byte,
                "segments": [
                    {"node_type": segment.node_type, "name": segment.name}
                    for segment in locator.segments
                ],
                "notebook_cell_id": locator.notebook_cell_id,
                "notebook_source_hash": (
                    locator.notebook_source_hash.hex()
                    if locator.notebook_source_hash is not None
                    else None
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    if (
        isinstance(command.target.region, OrdinaryRegion)
        and _rendered_notebook_cells(file, cache) is not None
    ):
        raise ReviewError(
            "invalid_target",
            "Ordinary text target requires an ordinary rendered File.",
        )

    text = _read_text(file, command.target.side, cache)
    notebook_cell_id: Optional[str] = None
    notebook_source_hash: Optional[bytes] = None
    path = _path_hint(file, command.target.side)
    if isinstance(command.target.region, NotebookCellSourceRegion):
        cell, notebook_cell_id, notebook_source_hash = _origin_cell(
            file,
            side=command.target.side,
            cell_key=command.target.region.cell_key,
            cache=cache,
        )
        text = _cell_source(cell)
        path = _cell_path(cell)
    region = origin_region(path, text, command.target.range)
    locator = _Locator(
        side=command.target.side,
        region_hash=hashlib.sha256(
            region.source[region.start_byte : region.end_byte]
        ).digest(),
        region_start_byte=region.start_byte,
        region_end_byte=region.end_byte,
        segments=region.segments,
        notebook_cell_id=notebook_cell_id,
        notebook_source_hash=notebook_source_hash,
    )
    return ReviewThreadRecord(
        command.thread_id.hex,
        snapshot.id,
        file.id,
        True,
        "range",
        command.target.region.kind,
        (
            command.target.region.cell_key
            if isinstance(command.target.region, NotebookCellSourceRegion)
            else None
        ),
        command.target.side,
        command.target.range.start_line,
        command.target.range.end_line,
        None,
        encode_locator(locator),
    )


def _plan_thread_creation(
    *,
    command: CreateThread,
    created_at: str,
    selected_snapshot: SnapshotRecord,
    selected_files_by_pair: dict[FilePair, SnapshotFileRecord],
    cache: _ReviewReadCache,
) -> tuple[tuple[ReviewThreadRecord, ...], ReviewActionRecord]:
    """Validate and build immutable rows for one new discussion.

    The caller holds the Room write lock. This operation performs no insert so
    one or several planned creations can join a larger database transaction.
    """
    _nonblank(command.body)
    profile_id = command.author.profile_id
    target_file = selected_files_by_pair.get(command.target.file)
    if target_file is None:
        raise ReviewError(
            "invalid_target",
            "Review target File is absent from the Snapshot.",
        )
    origin = _origin_record(command, selected_snapshot, target_file, cache)
    _build_original_excerpt(origin, target_file, cache)
    return (
        (origin,),
        ReviewActionRecord(
            operation_id=command.operation_id.hex,
            thread_id=command.thread_id.hex,
            snapshot_id=selected_snapshot.id,
            sequence=0,
            kind="comment-created",
            profile_id=profile_id,
            comment_id=command.comment_id.hex,
            expected_revision=None,
            body=command.body,
            created_at=created_at,
        ),
    )


def _create_thread(
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
        selected_snapshot = database.snapshot(identity, snapshot_id.hex)
        if selected_snapshot is None:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        files_by_id, files_by_pair = _file_indexes(selected_snapshot)
        cache = _ReviewReadCache({}, {})
        created_at = _now()
        rows, first_action = _plan_thread_creation(
            command=command,
            created_at=created_at,
            selected_snapshot=selected_snapshot,
            selected_files_by_pair=files_by_pair,
            cache=cache,
        )
        database.create_review_thread(
            rows,
            first_action,
        )
        assert len(rows) == 1 and rows[0].is_origin
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
            origin_files_by_id=files_by_id,
            selected_files_by_pair=files_by_pair,
            cache=cache,
        )


def _apply_review_batch(
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
        selected_snapshot = database.snapshot(identity, snapshot_id.hex)
        if selected_snapshot is None:
            raise DirdiffError(f"Unknown snapshot id: {snapshot_id.hex}")
        selected_files_by_pair = _file_indexes(selected_snapshot)[1]
        cache = _ReviewReadCache({}, {})
        placements: list[ReviewThreadRecord] = []
        records: list[ReviewActionRecord] = []
        results: list[ReviewBatchResult] = []
        simulated: dict[str, list[ReviewActionRecord]] = {}
        profiles: dict[str, dict[int, UserProfileRecord]] = {}

        for action in batch:
            if isinstance(action, CreateThread):
                _validate_author(database, action.author)
                rows, first_action = _plan_thread_creation(
                    command=action,
                    created_at=_now(),
                    selected_snapshot=selected_snapshot,
                    selected_files_by_pair=selected_files_by_pair,
                    cache=cache,
                )
                placements.extend(rows)
                records.append(first_action)
                results.append(
                    ReviewBatchResult(
                        "create", action.thread_id, action.comment_id
                    )
                )
                continue

            thread_key = action.thread_id.hex
            thread_actions = simulated.get(thread_key)
            thread_profiles = profiles.get(thread_key)
            if thread_actions is None or thread_profiles is None:
                persisted = database.review_actions(
                    snapshot_id.hex,
                    thread_key,
                )
                if persisted is None:
                    raise ReviewError(
                        "thread_not_found",
                        f"Unknown Thread: {thread_key}",
                    )
                persisted_actions, persisted_profiles = persisted
                thread_actions = list(persisted_actions)
                thread_profiles = {
                    profile.id: profile for profile in persisted_profiles
                }
                simulated[thread_key] = thread_actions
                profiles[thread_key] = thread_profiles

            command = action.command
            profile_id = command.author.profile_id
            profile = database.review_profile(profile_id)
            if profile is None:
                raise ReviewError(
                    "profile_not_found", f"Unknown Profile: {profile_id}"
                )
            thread_profiles[profile.id] = profile
            state, state_revision, _comments = _fold_actions(
                tuple(thread_actions), thread_profiles
            )
            if state == "deleted":
                raise ReviewError("state_conflict", "Thread is deleted.")
            if isinstance(action, ReplyToThread):
                reply = action.command
                _nonblank(reply.body)
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
                )
                result = ReviewBatchResult(
                    "reply", action.thread_id, reply.comment_id
                )
            else:
                assert isinstance(action, ResolveThread)
                if state != "open":
                    raise ReviewError(
                        "state_conflict", "Only an open Thread may be resolved."
                    )
                record = ReviewActionRecord(
                    operation_id=command.operation_id.hex,
                    thread_id=thread_key,
                    snapshot_id=snapshot_id.hex,
                    sequence=len(thread_actions),
                    kind="thread-resolved",
                    profile_id=profile_id,
                    comment_id=None,
                    expected_revision=state_revision,
                    body=None,
                    created_at=_now(),
                )
                result = ReviewBatchResult("resolve", action.thread_id, None)
            thread_actions.append(record)
            records.append(record)
            results.append(result)

        database.apply_review_batch(tuple(placements), tuple(records))
        return tuple(results)
