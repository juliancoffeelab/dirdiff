"""Shared test adapters around the production diff pipeline.

Tests may use this module to assemble backend, engine, notebook, and rendering
objects without reintroducing retired production service classes.  Helpers here
must stay thin adapters over public dirdiff contracts, so behavior assertions
remain in the calling tests and not hidden behind convenience fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Literal, override

from syrupy.data import Snapshot, SnapshotCollection
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from dirdiff.backend import (
    BranchSelection,
    GitBackend,
    RefChoices,
    WorkspaceBackendProtocol,
    build_repo_manifest_for_backend,
    display_name_for_repo_paths,
    file_kind_for_change_type,
    load_diff_sides,
    ref_choices,
)
from dirdiff.engines import (
    DiffEngineProtocol,
    DiffSide,
    GitDiffEngine,
    TextDiffEngine,
)
from dirdiff.notebooks import (
    build_notebook_diff_payload,
)
from dirdiff.rendering import (
    default_expanded_for_payload,
    enrich_rows_for_display,
)

__all__ = [
    "GitDiffService",
    "GoldenJsonSnapshotExtension",
    "TextDiffService",
    "WorkspaceDiffServiceAdapter",
    "build_loaded_diff",
    "build_workspace_file_payload",
]


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
    golden_root: ClassVar[Path]
    snapshot_function_name: ClassVar[str]

    @override
    def serialize(
        self,
        data: Any,
        *,
        exclude: Any = None,
        include: Any = None,
        matcher: Any = None,
    ) -> str:
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    @override
    def matches(
        self,
        *,
        serialized_data: str,
        snapshot_data: str,
    ) -> bool:
        serialized_json: object = json.loads(serialized_data)
        snapshot_json: object = json.loads(snapshot_data)
        return serialized_json == snapshot_json

    @classmethod
    @override
    def dirname(cls, *, test_location: Any) -> str:
        return str(cls.golden_root)

    @classmethod
    @override
    def get_snapshot_name(
        cls, *, test_location: Any, index: int | str = 0
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
    def get_location(cls, *, test_location: Any, index: int | str) -> str:
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
) -> dict[str, Any]:
    """Build the text/notebook payload shape expected by legacy logic tests.

    The production helper with this name was removed when rendering was split
    into backend loading, engine rendering, notebook handling, and display
    enrichment.  Tests still need a compact way to exercise that combined
    behavior without reintroducing the old production service surface, so this
    helper wires the new public pieces together locally.
    """

    renderer = TextDiffEngine()
    notebook_payload = build_notebook_diff_payload(
        renderer=renderer,
        display_name=display_name,
        left_label=left_label,
        right_label=right_label,
        left_exists=left_exists,
        right_exists=right_exists,
        left_text=left_text,
        right_text=right_text,
    )
    if notebook_payload is not None:
        return notebook_payload

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
        rows=[dict(row) for row in rendered["rows"]],
        left_text=left_text_value,
        right_text=right_text_value,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    payload: dict[str, Any] = {
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
    payload["default_expanded"] = default_expanded_for_payload(payload)
    return payload


def build_workspace_file_payload(
    *,
    backend: WorkspaceBackendProtocol,
    renderer: DiffEngineProtocol,
    left_path: str | None,
    right_path: str | None,
    left: str,
    right: str,
    display_name: str | None = None,
    change_type: Literal[
        "modify", "add", "delete", "rename", "copy"
    ] = "modify",
    file_kind: Literal["git", "untracked"] = "git",
) -> dict[str, Any]:
    context = load_diff_sides(
        backend=backend,
        left_path=left_path,
        right_path=right_path,
        left=left,
        right=right,
    )
    left_version = context["left_version"]
    right_version = context["right_version"]
    if display_name is None:
        resolved_display_name = display_name_for_repo_paths(
            context["left_path"],
            context["right_path"],
        )
    else:
        resolved_display_name = display_name
    notebook_payload = build_notebook_diff_payload(
        renderer=renderer,
        display_name=resolved_display_name,
        left_label=context["left_label"],
        right_label=context["right_label"],
        left_exists=left_version.exists,
        right_exists=right_version.exists,
        left_text=left_version.text,
        right_text=right_version.text,
    )
    if notebook_payload is not None:
        notebook_payload["left_path"] = context["left_path"]
        notebook_payload["right_path"] = context["right_path"]
        if file_kind == "untracked":
            notebook_payload["file_kind"] = {"type": "untracked"}
        else:
            notebook_payload["file_kind"] = file_kind_for_change_type(
                change_type,
                file_kind="git",
            )
        return notebook_payload

    rendered = renderer.render_diff(
        old=DiffSide(
            exists=left_version.exists,
            text=left_version.text,
            path_hint=context["left_path"],
        ),
        new=DiffSide(
            exists=right_version.exists,
            text=right_version.text,
            path_hint=context["right_path"],
        ),
    )
    left_text_value = "" if left_version.text is None else left_version.text
    right_text_value = "" if right_version.text is None else right_version.text
    display = enrich_rows_for_display(
        rows=[dict(row) for row in rendered["rows"]],
        left_text=left_text_value,
        right_text=right_text_value,
        left_path_hint=context["left_path"],
        right_path_hint=context["right_path"],
    )
    payload: dict[str, Any] = {
        "display_name": resolved_display_name,
        "left_label": context["left_label"],
        "right_label": context["right_label"],
        "rows": display["rows"],
        "summary": {
            **rendered["summary"],
            "left_exists": left_version.exists,
            "right_exists": right_version.exists,
        },
        "default_expanded": False,
        "left_path": context["left_path"],
        "right_path": context["right_path"],
        "file_kind": (
            {"type": "untracked"}
            if file_kind == "untracked"
            else file_kind_for_change_type(change_type, file_kind="git")
        ),
    }
    if "engine_warning" in rendered:
        payload["engine_warning"] = rendered["engine_warning"]
    if "fold_hints" in display:
        payload["fold_hints"] = display["fold_hints"]
    payload["default_expanded"] = default_expanded_for_payload(payload)
    return payload


class WorkspaceDiffServiceAdapter:
    def __init__(
        self,
        backend: WorkspaceBackendProtocol,
        renderer: DiffEngineProtocol,
    ) -> None:
        self.backend = backend
        self.renderer = renderer

    def build_git_diff_paths(
        self,
        *,
        left_path: str | None,
        right_path: str | None,
        left: str,
        right: str,
        display_name: str | None = None,
        change_type: Literal[
            "modify", "add", "delete", "rename", "copy"
        ] = "modify",
        file_kind: Literal["git", "untracked"] = "git",
    ) -> dict[str, Any]:
        return build_workspace_file_payload(
            backend=self.backend,
            renderer=self.renderer,
            left_path=left_path,
            right_path=right_path,
            left=left,
            right=right,
            display_name=display_name,
            change_type=change_type,
            file_kind=file_kind,
        )

    def build_repo_manifest(
        self,
        *,
        left: str,
        right: str,
        show_untracked: bool = False,
    ) -> dict[str, Any]:
        return build_repo_manifest_for_backend(
            self.backend,
            left=left,
            right=right,
            show_untracked=show_untracked,
        )

    def list_repo_diff_paths(self, *, left: str, right: str) -> Any:
        return self.backend.repo_diff(left=left, right=right).paths

    def list_ref_choices(self) -> RefChoices:
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
        return self.backend.resolve_branch_diff_sides(
            base_selection=base_selection,
            review_selection=review_selection,
        )

    def normalize_side(self, raw_side: str) -> str:
        return self.backend.normalize_side(raw_side)


class TextDiffService(WorkspaceDiffServiceAdapter):
    def __init__(self, backend: WorkspaceBackendProtocol) -> None:
        super().__init__(backend, TextDiffEngine())


class GitDiffService(WorkspaceDiffServiceAdapter):
    def __init__(self, backend: WorkspaceBackendProtocol) -> None:
        super().__init__(backend, GitDiffEngine())
