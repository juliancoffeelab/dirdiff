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

from dirdiff.change_source import CapturedLink
from dirdiff.db import (
    AuthoredComment,
    BayStartPlacement,
    CommentCreatedAction,
    FileMissingPlacement,
    FileStartPlacement,
    FileUnreadablePlacement,
    Placement,
    RangePlacement,
    ReviewActionInput,
    ReviewScopePlacement,
    ReviewThreadRecord,
    RoomIdentity,
    RoomStore,
    SnapshotFileRecord,
    SnapshotRecord,
    SnapshotSymlinkFileSideRecord,
)
from dirdiff.formats import (
    BayContext,
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
from dirdiff.util import Nothing, Option, Some

__all__ = [
    "ReviewReadCache",
    "build_original_excerpt",
    "derive_room_threads",
    "file_pair",
    "origin_target_view",
    "plan_thread_creation",
]

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Segment:
    node_type: str

    name: Optional[str]


@dataclass(frozen=True)
class _Locator:
    # Byte offsets index the UTF-8 encoded bay-side text; the hash covers
    # exactly that slice.
    region_hash: bytes

    region_start_byte: int

    region_end_byte: int

    segments: tuple[_Segment, ...]


@dataclass(frozen=True)
class _SourceRegion:
    source: bytes

    start_byte: int

    end_byte: int

    start_line: int

    # Inclusive; a node ending at column zero does not include the following
    # line.
    end_line: int

    segments: tuple[_Segment, ...]


@dataclass(frozen=True)
class _ComposedBay:
    bay_key: str

    kind: Literal["text", "image"]

    left_text: Optional[str]

    right_text: Optional[str]

    # None means "use the captured repository path", not that the side is
    # absent.
    left_hint: Optional[str]

    right_hint: Optional[str]

    def text_for(self, side: Literal["left", "right"]) -> Optional[str]:
        return self.left_text if side == "left" else self.right_text

    def hint_for(self, side: Literal["left", "right"]) -> Optional[str]:
        return self.left_hint if side == "left" else self.right_hint


@dataclass
class ReviewReadCache:
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


def file_pair(file: SnapshotFileRecord) -> FilePair:
    return FilePair(
        file.left.map(lambda side: side.repository_path),
        file.right.map(lambda side: side.repository_path),
    )


def _file_indexes(
    snapshot: SnapshotRecord,
) -> tuple[dict[str, SnapshotFileRecord], dict[FilePair, SnapshotFileRecord]]:
    by_id = {file.id: file for file in snapshot.files}
    by_pair = {file_pair(file): file for file in snapshot.files}
    assert len(by_id) == len(by_pair) == len(snapshot.files), (
        "Snapshot contains duplicate review File identities"
    )
    return by_id, by_pair


def _path_hint(file: SnapshotFileRecord, side: Literal["left", "right"]) -> str:
    record = file.left if side == "left" else file.right
    return record.must().repository_path


@lru_cache(maxsize=3)
def _regions_for_source(path: str, text: str) -> tuple[_SourceRegion, ...]:
    def parser_for_path() -> Optional[Parser]:
        lower = path.lower()
        for suffixes, module_name, attribute in _LANGUAGES:
            if not lower.endswith(suffixes):
                continue
            module = importlib.import_module(module_name)
            factory = getattr(module, attribute)
            return Parser(Language(factory()))
        return None

    def node_name(node: Node, source: bytes) -> Optional[str]:
        name = node.child_by_field_name("name")
        if name is None:
            return None
        value = source[name.start_byte : name.end_byte].decode().strip()
        return value if value != "" else None

    def eligible_ancestors(node: Node, source: bytes) -> tuple[_Segment, ...]:
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
    cached = cache.bays.get(file.id)
    if cached is not None:
        return cached
    pair = file_pair(file)

    def side_bytes(side: Literal["left", "right"]) -> Option[bytes]:
        record = file.left if side == "left" else file.right
        if not Some.present(record):
            return Nothing()
        if file.error is not None:
            raise ReviewError("invalid_target", file.error)
        content = (Path(file.path) / side).read_bytes()
        assert hashlib.sha256(content).digest() == record.value.content_hash, (
            f"Snapshot File content hash mismatch: {file.path}/{side}"
        )
        return Some(content)

    def pseudo_line(side: MediaSide) -> str:
        # Content-derived, so replacing the image changes the line and the
        # Thread's region hash stops matching (outdated).
        ref = media_ref(side)
        return (
            f"{ref['media_type']}, {ref['byte_size']} bytes, "
            f"sha256 {ref['digest']}"
        )

    def side_link(side: Literal["left", "right"]) -> Option[CapturedLink]:
        selected_side = file.left if side == "left" else file.right
        if not Some.present(selected_side) or not isinstance(
            selected_side.value, SnapshotSymlinkFileSideRecord
        ):
            return Nothing()
        record = selected_side.value.link
        return Some(
            read_captured_link(
                metadata_path=Path(record.metadata_path),
                metadata_hash=record.metadata_hash,
                target_capture_path=record.target.map(
                    lambda target: Path(target.path)
                ),
                target_hash=record.target.map(
                    lambda target: target.content_hash
                ),
            )
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
                left_text=bay.left.get_map(pseudo_line),
                right_text=bay.right.get_map(pseudo_line),
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
    bay = _composed_bays(file, cache).get(bay_key)
    if bay is None:
        raise ReviewError("invalid_target", "Unknown rendered bay.")
    if bay.text_for(side) is None:
        raise ReviewError(
            "invalid_target", "Bay is absent on the selected side."
        )
    return bay


def _locator_of(payload: bytes, *, side: Literal["left", "right"]) -> _Locator:
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


def _record_of(
    placement: Placement,
    *,
    is_origin: bool,
    locator: Optional[_Locator],
) -> ReviewThreadRecord:
    private_locator = None
    if locator is not None:
        assert isinstance(placement, RangePlacement)
        private_locator = _locator_bytes(locator, side=placement.side)
    return ReviewThreadRecord(placement, is_origin, private_locator)


def _derive_record(
    *,
    origin: RangePlacement | FileStartPlacement,
    locator: Optional[_Locator],
    origin_file: SnapshotFileRecord,
    target_snapshot_id: str,
    target_files_by_pair: dict[FilePair, SnapshotFileRecord],
    cache: ReviewReadCache,
) -> Placement:
    def file_start(side: Literal["left", "right"]) -> FileStartPlacement:
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
        assert Some.present(selected_side), (
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
            target_pair.right_path.get_or(target_pair.left_path.get()),
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
    origin_pair = file_pair(file)
    pair: ReviewFilePairView = {
        "left_path": origin_pair.left_path.get(),
        "right_path": origin_pair.right_path.get(),
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
    origins = {
        origin.placement.thread_id: origin
        for origin in database.review_origins_missing(
            identity,
            target_snapshot.id,
        )
    }
    # One set-based read loads exactly the origin Files these placements
    # reference; the origin Snapshots themselves are never hydrated. The
    # target Snapshot arrives fully loaded from the capture that triggered
    # this derivation.
    # A review-scoped Thread is about the review, not captured code, so it is
    # copied into the target Snapshot as-is and takes no part in File
    # derivation.
    review_scope: list[ReviewThreadRecord] = []
    origin_refs_list: list[tuple[str, str]] = []
    for record in origins.values():
        origin = record.placement
        if isinstance(origin, ReviewScopePlacement):
            review_scope.append(
                ReviewThreadRecord(
                    ReviewScopePlacement(origin.thread_id, target_snapshot.id),
                    False,
                    None,
                )
            )
            continue
        assert isinstance(origin, (RangePlacement, FileStartPlacement)), (
            "invalid discussion origin"
        )
        origin_refs_list.append((origin.snapshot_id, origin.snapshot_file_id))
    origin_refs = tuple(dict.fromkeys(origin_refs_list))
    origin_files, _selected_files, _conflicts, _comparison_files = (
        database.review_thread_files(
            target_snapshot.id, origin_refs, (), (), ()
        )
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
        origin = record.placement
        if isinstance(origin, ReviewScopePlacement):
            continue
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
        # Empty strings are sorting sentinels only: absent sides precede valid
        # paths, and File-start placements precede named bays for the same pair.
        grouped_origins.append(
            (
                (
                    pair.left_path.get_or(""),
                    pair.right_path.get_or(""),
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
    return (*placements, *review_scope)


def _origin_record(
    command: CreateThread,
    snapshot_id: str,
    file: SnapshotFileRecord,
    cache: ReviewReadCache,
) -> tuple[RangePlacement, _Locator]:
    def origin_region(
        path: str, text: str, selected: LineRange
    ) -> _SourceRegion:
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
) -> tuple[tuple[ReviewThreadRecord, ...], ReviewActionInput]:
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
        ReviewActionInput(
            operation_id=command.operation_id.hex,
            thread_id=command.thread_id.hex,
            snapshot_id=snapshot_id,
            sequence=0,
            profile_id=profile_id,
            created_at=created_at,
            status_after="open",
            attention_after="author",
            payload=CommentCreatedAction(
                "thread-created",
                AuthoredComment(command.comment_id.hex, command.body),
            ),
        ),
    )
