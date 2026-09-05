"""Shared test adapters around the production diff pipeline.

Tests may use this module to assemble backend, engine, and rendering
objects without reintroducing retired production service classes.  Helpers here
must stay thin adapters over public dirdiff contracts, so behavior assertions
remain in the calling tests and not hidden behind convenience fixtures.
`tool_json` maintains local raw third-party output files keyed by exact source
hashes. It caches no rows, assertions, rendered payloads, or production state.
"""

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

from dirdiff.backend import (
    BranchSelection,
    GitBackend,
    RefChoices,
    RepoDiffPath,
    RepoManifest,
    WorkspaceBackendProtocol,
    build_repo_manifest_for_backend,
    ref_choices,
)
from dirdiff.engines import (
    DiffEngineProtocol,
    DiffSide,
    DiffSummary,
    DifftasticDiffEngine,
    DifftasticJson,
    GitDiffEngine,
    GumTreeDiffEngine,
    GumTreeJson,
    TextDiffEngine,
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
    """Exact inline tool input and its required parser-selecting filename.

    `text` preserves whitespace and line endings. `name` is a filename hint,
    never a path to read; callers use the same name for the same cache input.
    """

    text: str
    """Complete source text, preserving whitespace and line endings."""

    name: str
    """Nonempty basename used for parser selection, never loaded from disk."""


type ToolSide = Path | ToolText
"""A source file to read exactly, or inline text with a required filename hint.

Paths contribute their basename and UTF-8 contents, so moving a fixture does
not invalidate its cache entry.
"""


@overload
def tool_json(
    engine: Literal["difftastic"],
    cache_key: str,
    *,
    left: ToolSide,
    right: ToolSide,
    allow_mismatches: bool = False,
) -> DifftasticJson:
    """Return validated Difftastic JSON for one cached source pair.

    # Parameters

    - `engine`: Literal `difftastic`, selecting Difftastic JSON.
    - `cache_key`: Stable slash-separated kebab-case identity for this pair.
    - `left`: Old source path or exact inline text with a filename hint.
    - `right`: New source under the same required input contract.
    - `allow_mismatches`: Permit replacing changed worktree inputs only.
    """
    ...


@overload
def tool_json(
    engine: Literal["gumtree"],
    cache_key: str,
    *,
    left: ToolSide,
    right: ToolSide,
    allow_mismatches: bool = False,
) -> GumTreeJson:
    """Return validated GumTree JSON for one cached source pair.

    # Parameters

    - `engine`: Literal `gumtree`, selecting GumTree JSON.
    - `cache_key`: Stable slash-separated kebab-case identity for this pair.
    - `left`: Old source path or exact inline text with a filename hint.
    - `right`: New source under the same required input contract.
    - `allow_mismatches`: Permit replacing changed worktree inputs only.
    """
    ...


def tool_json(
    engine: Literal["difftastic", "gumtree"],
    cache_key: str,
    *,
    left: ToolSide,
    right: ToolSide,
    allow_mismatches: bool = False,
) -> DifftasticJson | GumTreeJson:
    """Read local tool JSON or run the real executable on a cache miss.

    Keys are slash-separated lowercase kebab-case segments. Each engine/key
    identifies one exact text and filename pair. Mismatches assert unless the
    caller explicitly permits replacement for changing worktree inputs.
    Corrupt entries, subprocess failures, and filesystem errors propagate even
    when replacement is allowed. Hits never discover or inspect executables.

    Files beneath `.test_cache/tool` contain only side hashes and tool output.
    Atomic replacement lets concurrent misses run independently without a lock.
    Remove that directory explicitly to invalidate results after tool updates.

    # Parameters

    - `engine`: External JSON producer, Difftastic or GumTree.
    - `cache_key`: Stable slash-separated kebab-case identity for this pair.
    - `left`: Old source Path or exact inline text with a filename hint.
    - `right`: New source under the same required input contract.
    - `allow_mismatches`: Permit replacing changed worktree inputs only.

    # Returns

    - `Difftastic`: Validated alignment, changed spans and tool metadata.
    - `GumTree`: Validated matches, actions and tool metadata.
    """

    def valid_cache_key(value: str) -> bool:
        """Report whether the whole key is valid slash-separated segments.

        Every segment is nonempty lowercase ASCII letters or digits, optionally
        separated by single internal hyphens.
        """
        return (
            re.fullmatch(
                r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*",
                value,
            )
            is not None
        )

    def validate_cached_entry(
        value: JsonValue,
    ) -> DifftasticJson | GumTreeJson:
        """Return the engine output from a structurally valid cache entry.

        # Returns

        - `Difftastic`: Validated alignment, changed spans and tool metadata.
        - `GumTree`: Validated matches, actions and tool metadata.
        """
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
            case _ as unreachable:
                assert_never(unreachable)

    assert valid_cache_key(cache_key), cache_key
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
            output = DifftasticDiffEngine()._run_difftastic_json(
                left_text=old.text,
                right_text=new.text,
                left_path_hint=old.name,
                right_path_hint=new.name,
            )
        case "gumtree":
            output = GumTreeDiffEngine()._run_gumtree_json(
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
            # Replacement consumes the temporary path; failed writes leave it.
            temporary.unlink(missing_ok=True)
    return output


class _LoadedDiffSummary(DiffSummary):
    """Add source-side presence to an engine's line-count summary.

    Rendering tests need to distinguish an absent side from an existing empty
    side; production `DiffSummary` counts alone cannot represent that boundary.
    """

    left_exists: bool
    """Whether the test supplied a left source side.

    False means absence, not an existing empty string, and lets adapters retain
    the production side-presence contract.
    """

    right_exists: bool
    """Whether the test supplied a right source side.

    Tests consume it separately from added-line counts because an empty present
    side and no side render differently.
    """


class _LoadedDiff(TypedDict):
    """Describe the legacy single-text-bay payload used by rendering tests.

    The adapter preserves the old assertion shape while all derived rows, folds,
    and counts still come from current production engines and enrichment.
    """

    display_name: str
    """Caller-supplied File heading retained in the test payload.

    It is presentation input only and carries no repository path or File identity.
    """

    left_label: str
    """Caller-supplied left-side heading.

    Tests assert it unchanged; the adapter does not infer it from source presence.
    """

    right_label: str
    """Caller-supplied right-side heading.

    It remains independent of engine rows so labeling assertions do not hide
    rendering behavior.
    """

    summary: _LoadedDiffSummary
    """Engine line counts plus explicit source-side presence.

    The adapter enriches the production summary only with facts required to
    distinguish absent and empty test inputs.
    """

    rows: list[DiffRow]
    """Engine rows after ordinary display enrichment.

    Calling tests receive the same hunk assignment and row shaping as production,
    not hand-built expected rows.
    """

    hunk_count: int
    """Number of changed runs assigned by display enrichment.

    It is derived from the returned rows and lets legacy assertions check the
    production hunk boundary without rerunning enrichment.
    """

    default_expanded: bool
    """Legacy test field fixed to `True` in the completed payload.

    It preserves snapshot compatibility only and must not be interpreted as a
    current File-lane expansion decision.
    """

    fold_hints: NotRequired[list[FoldHint]]
    """Structural fold hints, omitted when enrichment produced none.

    Absence preserves the legacy payload shape; a present list contains the
    production-derived hints without test-specific synthesis.
    """


class GoldenJsonSnapshotExtension(SingleFileSnapshotExtension):
    """Store preset golden snapshots as one JSON file per preset directory.

    Test modules provide `preset_root` and `golden_root`; assertions pass a
    preset-root-relative path as the snapshot name.  The extension maps that
    key to `golden_root/<key>/<test-module>.json`, serializes JSON with stable
    ordering, and reconstructs the same parametrized pytest snapshot name while
    syrupy scans existing golden files for unused-snapshot reporting.
    """

    _write_mode = WriteMode.TEXT
    file_extension = "json"
    preset_root: ClassVar[Path]
    """Fixture root against which string snapshot keys are interpreted.

    Test extensions set it before use; relative keys outside this root are invalid
    inputs rather than alternate snapshot locations.
    """

    golden_root: ClassVar[Path]
    """Root holding one JSON snapshot below each fixture-relative key.

    The extension derives paths beneath it and never uses it as fixture input.
    """

    snapshot_function_name: ClassVar[str]
    """Pytest function name reconstructed while scanning stored snapshots.

    Syrupy uses it to associate on-disk JSON with the originating parametrized
    assertion during unused-snapshot reporting.
    """

    @override
    def serialize(
        self,
        data: SerializableData,
        *,
        exclude: PropertyFilter | None = None,
        include: PropertyFilter | None = None,
        matcher: PropertyMatcher | None = None,
    ) -> str:
        """Serialize one assertion value as deterministic readable JSON.

        # Parameters

        - `data`: JSON-compatible assertion value to store.
        - `exclude`: Syrupy property filter; accepted for the override contract
          but not applied by this whole-value JSON serializer.
        - `include`: Syrupy inclusion filter, likewise unused here.
        - `matcher`: Syrupy matcher, which snapshot assertion applies before
          this extension receives the final value.
        """
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    @override
    def matches(
        self,
        *,
        serialized_data: str,
        snapshot_data: str,
    ) -> bool:
        """Compare stored and current snapshots as parsed JSON values.

        # Parameters

        - `serialized_data`: Current JSON emitted by `serialize`.
        - `snapshot_data`: Existing golden file contents read by Syrupy.

        Formatting and object-key order do not affect the match. Invalid JSON
        propagates because a corrupt golden or serializer result is not equal.
        """
        serialized_json: JsonValue = json.loads(serialized_data)
        snapshot_json: JsonValue = json.loads(snapshot_data)
        return serialized_json == snapshot_json

    @classmethod
    @override
    def dirname(cls, *, test_location: PyTestLocation) -> str:
        """Direct every assertion in the subclass to its configured golden root.

        `test_location` is intentionally irrelevant because preset-relative
        placement is handled by `get_location`; no per-module directory is
        created here.
        """
        return str(cls.golden_root)

    @classmethod
    @override
    def get_snapshot_name(
        cls, *, test_location: PyTestLocation, index: SnapshotIndex = 0
    ) -> str:
        """Reconstruct the parametrized pytest identity for one preset key.

        # Parameters

        - `test_location`: Current test module and function identity.
        - `index`: Preset-root-relative string key used by these tests, or a
          standard Syrupy index delegated to the base extension.
        """
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
        """Map a preset key to its one JSON golden file.

        # Parameters

        - `test_location`: Test module supplying the golden filename.
        - `index`: Preset-root-relative string key, or a standard Syrupy index
          delegated unchanged.
        """
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
        """Recover the pytest snapshot name represented by one golden file.

        `snapshot_location` must lie below `golden_root` in the layout produced
        by `get_location`. Syrupy uses the returned collection for unused-file
        reporting, so the reconstructed preset path must match collection-time
        parametrization exactly.
        """
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
    """Build the ordinary text payload shape expected by legacy logic tests.

    The production helper with this name was removed when rendering was split
    into backend loading, engine rendering, and display enrichment. Tests still
    need a compact way to exercise that combined behavior without reintroducing
    the old production service surface, so this helper wires the new public
    pieces together locally. Composed formats are exercised through
    `dirdiff.formats` directly, not here.

    # Parameters

    - `display_name`: File heading retained only in the assembled test payload.
    - `left_label`: Human label for the old side.
    - `right_label`: Human label for the new side.
    - `left_exists`: Whether the engine treats the old side as present.
    - `right_exists`: Whether the engine treats the new side as present.
    - `left_text`: Complete old-side text, or `None` with an absent side.
    - `right_text`: Complete new-side text, or `None` with an absent side.
    - `left_path_hint`: Optional old-side suffix hint for syntax processing.
    - `right_path_hint`: Optional new-side suffix hint for syntax processing.
    """

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
    """Preserve the former service-shaped test API over current public pieces.

    Older backend integration tests construct this adapter and exercise
    manifest, path, ref, and branch behavior through one object. It delegates
    each operation directly to the supplied backend and keeps a renderer only
    for subclasses matching the historical construction shape.

    This is test compatibility, not a production abstraction. It must not add
    behavior, retain workspace results, or hide assertions from calling tests.
    """

    def __init__(
        self,
        backend: WorkspaceBackendProtocol,
        renderer: DiffEngineProtocol,
    ) -> None:
        """Bind the adapter to one backend and historical renderer slot.

        # Parameters

        - `backend`: Public workspace implementation delegated to on every call.
        - `renderer`: Renderer retained only for subclasses matching the former
          service construction shape; adapter methods do not invoke it.
        """
        self.backend = backend
        self.renderer = renderer

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> RepoManifest:
        """Build a manifest through the production backend adapter.

        # Parameters

        - `left`: Backend side handle for the old state.
        - `right`: Backend side handle for the new state.
        - `show_untracked`: Whether backend discovery includes worktree-only
          Files.
        """
        return build_repo_manifest_for_backend(
            self.backend,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def list_repo_diff_paths(
        self, *, left: str, right: str
    ) -> tuple[RepoDiffPath, ...]:
        """Return backend path facts without building a manifest tree.

        # Parameters

        - `left`: Backend side handle for the old state.
        - `right`: Backend side handle for the new state.

        # Returns

        - `Members`: The backend's path records without manifest-tree nodes or
          captured content.
        - `Order`: Records retain backend discovery order; an empty tuple means
          the selected sides have no File-local differences.
        """
        return self.backend.repo_diff(left=left, right=right).paths

    def list_ref_choices(self) -> RefChoices:
        """Derive branch controls from one current Git metadata read.

        Only Git-backed tests may call this method. It asserts that boundary
        instead of inventing ref choices for another backend.
        """
        # Branch-control derivations are Git-specific; only Git-backed tests
        # exercise this adapter method.
        assert isinstance(self.backend, GitBackend)
        return ref_choices(self.backend.read_ref_metadata())

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str, str]:
        """Resolve structured Branch Review choices through the backend.

        # Parameters

        - `base_selection`: Explicit local or remote symbolic base branch.
        - `review_selection`: Explicit local or remote symbolic review branch.

        # Returns

        - First, the base display label.
        - Second, the immutable merge-base commit used for capture's left side.
        - Third, the review display label.
        - Fourth, the immutable review-head commit used for capture's right side.
        """
        return self.backend.resolve_branch_diff_sides(
            base_selection=base_selection,
            review_selection=review_selection,
        )

    def normalize_side(self, raw_side: str) -> str:
        """Delegate one user-facing side spelling to backend normalization.

        `raw_side` is passed unchanged, so backend validation failures remain
        visible to the calling test.
        """
        return self.backend.normalize_side(raw_side)


class TextDiffService(WorkspaceDiffServiceAdapter):
    """Retain `TextDiffEngine` in the legacy adapter slot.

    Repository operations remain delegated to the supplied production backend.
    Adapter methods do not render, but older tests still inspect or expect this
    historical construction shape.
    """

    def __init__(self, backend: WorkspaceBackendProtocol) -> None:
        """Bind the legacy adapter to a workspace and `TextDiffEngine`.

        `backend` remains the sole source for manifest and branch operations.
        The engine value only preserves the construction shape expected by old
        integration tests.
        """
        super().__init__(backend, TextDiffEngine())


class GitDiffService(WorkspaceDiffServiceAdapter):
    """Retain the Git no-index renderer in the legacy adapter slot.

    Adapter methods do not render. Repository discovery and branch resolution
    remain direct backend operations.
    """

    def __init__(self, backend: WorkspaceBackendProtocol) -> None:
        """Bind the legacy adapter to a workspace and Git no-index rendering.

        `backend` remains the sole source for manifest and branch operations.
        The engine value only preserves the construction shape expected by old
        integration tests.
        """
        super().__init__(backend, GitDiffEngine())
