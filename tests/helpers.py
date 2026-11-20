from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import (
    ClassVar,
    Literal,
    NotRequired,
    TypedDict,
    assert_never,
    overload,
    override,
)

from syrupy.data import Snapshot, SnapshotCollection
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode
from syrupy.location import PyTestLocation
from syrupy.types import (
    PropertyFilter,
    PropertyMatcher,
    SerializableData,
    SnapshotIndex,
)

from dirdiff.change_source import (
    BranchSelection,
    GitChangeSource,
    GitRepository,
    ManifestPath,
    RefChoices,
    RepoManifest,
    build_repo_manifest_for_paths,
    ref_choices,
)
from dirdiff.engines import (
    DiffEngineProtocol,
    DiffSide,
    DiffsitterDiffEngine,
    DiffsitterJson,
    DiffSummary,
    DifftasticDiffEngine,
    DifftasticJson,
    GumTreeDiffEngine,
    GumTreeJson,
    TextDiffEngine,
    is_diffsitter_json,
    is_difftastic_json,
    is_gumtree_json,
)
from dirdiff.rendering import (
    DiffRow,
    FoldHint,
    enrich_rows_for_display,
)
from dirdiff.util import JsonValue

__all__ = [
    "GoldenJsonSnapshotExtension",
    "TextDiffService",
    "ToolText",
    "build_loaded_diff",
    "tool_json",
]


@dataclass(frozen=True)
class ToolText:
    text: str
    name: str


type ToolSide = Path | ToolText


@overload
def tool_json(
    engine: Literal["difftastic"],
    cache_key: str,
    *,
    left: ToolSide,
    right: ToolSide,
    allow_mismatches: bool = False,
) -> DifftasticJson: ...


@overload
def tool_json(
    engine: Literal["gumtree"],
    cache_key: str,
    *,
    left: ToolSide,
    right: ToolSide,
    allow_mismatches: bool = False,
) -> GumTreeJson: ...


@overload
def tool_json(
    engine: Literal["diffsitter"],
    cache_key: str,
    *,
    left: ToolSide,
    right: ToolSide,
    allow_mismatches: bool = False,
) -> DiffsitterJson: ...


def tool_json(
    engine: Literal["difftastic", "gumtree", "diffsitter"],
    cache_key: str,
    *,
    left: ToolSide,
    right: ToolSide,
    allow_mismatches: bool = False,
) -> DifftasticJson | GumTreeJson | DiffsitterJson:
    def valid_cache_key(value: str) -> bool:
        return (
            re.fullmatch(
                r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*",
                value,
            )
            is not None
        )

    def validate_cached_entry(
        value: JsonValue,
    ) -> DifftasticJson | GumTreeJson | DiffsitterJson:
        assert isinstance(value, dict) and set(value) == {
            "left_hash",
            "right_hash",
            "output",
        }, path
        for field in ("left_hash", "right_hash"):
            field_value = value[field]
            assert (
                isinstance(field_value, str)
                and re.fullmatch(r"[0-9a-f]{64}", field_value) is not None
            ), path
        match engine:
            case "difftastic":
                assert is_difftastic_json(value["output"]), path
                return value["output"]
            case "gumtree":
                assert is_gumtree_json(value["output"]), path
                return value["output"]
            case "diffsitter":
                assert is_diffsitter_json(value["output"]), path
                return value["output"]
            case _ as unreachable:
                assert_never(unreachable)

    assert valid_cache_key(cache_key), cache_key
    # Entries are keyed by source hashes only; delete .test_cache/tool by hand
    # to invalidate them after upgrading an external tool.
    inputs: list[ToolText] = []
    hashes: list[str] = []
    for side in (left, right):
        if isinstance(side, Path):
            # read_text's universal newline conversion would hide CRLF drift.
            source = ToolText(side.read_bytes().decode("utf-8"), side.name)
        else:
            source = side
        assert source.name != "" and Path(source.name).name == source.name, (
            source.name
        )
        inputs.append(source)
        hashes.append(
            hashlib.sha256(
                json.dumps(
                    [source.text, source.name], ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest()
        )
    path = (
        Path(__file__).parents[1]
        / ".test_cache"
        / "tool"
        / engine
        / f"{cache_key}.json"
    )
    try:
        serialized = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Absence alone is a cache miss; permission and other I/O errors fail.
        serialized = None
    if serialized is not None:
        entry = json.loads(serialized)
        # Validate before checking hashes: permission to refresh stale inputs
        # never grants permission to overwrite malformed cached output.
        output = validate_cached_entry(entry)
        if [entry["left_hash"], entry["right_hash"]] == hashes:
            return output
        assert allow_mismatches, (
            f"Tool cache input mismatch: {path}. Investigate conflicting key reuse or changed test inputs and fix the calling code."
        )
    old, new = inputs
    match engine:
        case "difftastic":
            output = DifftasticDiffEngine(
                ignore_comments=False
            )._run_difftastic_json(
                left_text=old.text,
                right_text=new.text,
                left_path_hint=old.name,
                right_path_hint=new.name,
            )
        case "gumtree":
            output = GumTreeDiffEngine(
                matcher="gumtree-simple"
            )._run_gumtree_json(
                left_text=old.text,
                right_text=new.text,
                left_path_hint=old.name,
                right_path_hint=new.name,
            )
        case "diffsitter":
            output = DiffsitterDiffEngine()._run_diffsitter_json(
                left_text=old.text,
                right_text=new.text,
                left_path_hint=old.name,
                right_path_hint=new.name,
            )
        case _ as unreachable:
            assert_never(unreachable)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(
                {
                    "left_hash": hashes[0],
                    "right_hash": hashes[1],
                    "output": output,
                },
                stream,
                ensure_ascii=False,
            )
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return output


class _LoadedDiffSummary(DiffSummary):
    left_exists: bool
    right_exists: bool


class _LoadedDiff(TypedDict):
    display_name: str
    left_label: str
    right_label: str
    summary: _LoadedDiffSummary
    rows: list[DiffRow]
    hunk_count: int
    default_expanded: bool
    fold_hints: NotRequired[list[FoldHint]]


class GoldenJsonSnapshotExtension(SingleFileSnapshotExtension):
    _write_mode = WriteMode.TEXT
    file_extension = "json"
    preset_root: ClassVar[Path]
    golden_root: ClassVar[Path]
    snapshot_function_name: ClassVar[str]

    @override
    def serialize(
        self,
        data: SerializableData,
        *,
        exclude: PropertyFilter | None = None,
        include: PropertyFilter | None = None,
        matcher: PropertyMatcher | None = None,
    ) -> str:
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    @override
    def matches(
        self,
        *,
        serialized_data: str,
        snapshot_data: str,
    ) -> bool:
        serialized_json: JsonValue = json.loads(serialized_data)
        snapshot_json: JsonValue = json.loads(snapshot_data)
        return serialized_json == snapshot_json

    @classmethod
    @override
    def dirname(cls, *, test_location: PyTestLocation) -> str:
        return str(cls.golden_root)

    @classmethod
    @override
    def get_snapshot_name(
        cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0
    ) -> str:
        if isinstance(index, str):
            preset_path = cls.preset_root / index
            return f"{test_location.methodname}[{preset_path}]"
        return super().get_snapshot_name(
            test_location=test_location,
            index=index,
        )

    @classmethod
    @override
    def get_location(
        cls, *, test_location: PyTestLocation, index: SnapshotIndex
    ) -> str:
        if isinstance(index, str):
            return str(
                cls.golden_root
                / index
                / f"{test_location.basename}.{cls.file_extension}"
            )
        return super().get_location(test_location=test_location, index=index)

    @override
    def read_snapshot_collection(
        self, *, snapshot_location: str
    ) -> SnapshotCollection:
        snapshot_path = Path(snapshot_location)
        preset_key = snapshot_path.parent.relative_to(self.golden_root)
        preset_path = self.preset_root / preset_key
        snapshot_name = f"{self.snapshot_function_name}[{preset_path}]"
        snapshot_collection = SnapshotCollection(location=snapshot_location)
        snapshot_collection.add(Snapshot(name=snapshot_name))
        return snapshot_collection


def build_loaded_diff(
    *,
    display_name: str,
    left_label: str,
    right_label: str,
    left_exists: bool,
    right_exists: bool,
    left_text: str | None,
    right_text: str | None,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> _LoadedDiff:
    renderer = TextDiffEngine()
    rendered = renderer.render_diff(
        old=DiffSide(
            exists=left_exists,
            text=left_text,
            path_hint=left_path_hint,
        ),
        new=DiffSide(
            exists=right_exists,
            text=right_text,
            path_hint=right_path_hint,
        ),
    )
    left_text_value = "" if left_text is None else left_text
    right_text_value = "" if right_text is None else right_text
    display = enrich_rows_for_display(
        rows=rendered["rows"],
        left_text=left_text_value,
        right_text=right_text_value,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    payload: _LoadedDiff = {
        "display_name": display_name,
        "left_label": left_label,
        "right_label": right_label,
        "summary": {
            **rendered["summary"],
            "left_exists": left_exists,
            "right_exists": right_exists,
        },
        "rows": display["rows"],
        "hunk_count": display["hunk_count"],
        "default_expanded": False,
    }
    if "fold_hints" in display:
        payload["fold_hints"] = display["fold_hints"]
    payload["default_expanded"] = True
    return payload


class WorkspaceDiffServiceAdapter:
    def __init__(
        self,
        backend: GitRepository,
        renderer: DiffEngineProtocol,
    ) -> None:
        self.backend = backend
        self.renderer = renderer

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> RepoManifest:
        assert self.backend.repo_root is not None
        left_side = self.backend.normalize_side(left)
        right_side = self.backend.normalize_side(right)
        prepared_left = (
            left_side
            if left_side in {"index", "worktree"}
            else self.backend.commit_id(
                left_side, allow_unborn=left_side == "HEAD"
            )
        )
        prepared_right = (
            right_side
            if right_side in {"index", "worktree"}
            else self.backend.commit_id(
                right_side, allow_unborn=right_side == "HEAD"
            )
        )
        changes = GitChangeSource(
            self.backend.repo_root,
            left=prepared_left,
            right=prepared_right,
            show_untracked=show_untracked,
            max_link_target_bytes=1024 * 1024,
        ).changes()
        return build_repo_manifest_for_paths(
            left_label=left_side,
            right_label=right_side,
            paths=tuple(
                ManifestPath(
                    file.left.map(lambda side: side.key.path),
                    file.right.map(lambda side: side.key.path),
                    file.display_name,
                    file.change_type,
                    file.lazy_reason_override,
                    file.untracked,
                )
                for file in changes.files
            ),
            added_lines=changes.added_lines,
            removed_lines=changes.removed_lines,
        )

    def list_ref_choices(self) -> RefChoices:
        # Branch-control derivations are Git-specific; only Git-backed tests
        # exercise this adapter method.
        assert isinstance(self.backend, GitRepository)
        return ref_choices(self.backend.read_ref_metadata())

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str, str]:
        return self.backend.resolve_branch_diff_sides(
            base_selection=base_selection,
            review_selection=review_selection,
        )

    def normalize_side(self, raw_side: str) -> str:
        return self.backend.normalize_side(raw_side)


class TextDiffService(WorkspaceDiffServiceAdapter):
    def __init__(self, backend: GitRepository) -> None:
        super().__init__(backend, TextDiffEngine())
