"""Golden row-output tests for GumTree preset fixtures.

This module is the snapshot boundary for exact GumTree row projection. Each
non-borked preset directory supplies old/new source files, and the snapshot
name is the preset path relative to `tests/presets/gumtree`. It tests projection
output only; subprocess invocation details belong to the engine, and display
enrichment has its own rendering tests.
"""

from pathlib import Path
from typing import Any

import pytest
from helpers import GoldenJsonSnapshotExtension

from dirdiff.engines.gumtree import GumTreeDiffEngine
from dirdiff.engines.gumtree.logic import build_gumtree_rows_from_json

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "gumtree"
GOLDEN_ROOT = Path(__file__).parents[1] / "golden" / "gumtree"
BROKEN_PRESET_GROUPS: set[str] = {
    "borked",
}


class GumTreeGoldenSnapshotExtension(GoldenJsonSnapshotExtension):
    preset_root = PRESETS_ROOT
    golden_root = GOLDEN_ROOT
    snapshot_function_name = "test_gumtree_preset_rows_match_golden"


@pytest.fixture
def snapshot_json(snapshot: Any) -> Any:
    return snapshot.with_defaults(
        extension_class=GumTreeGoldenSnapshotExtension
    )


@pytest.mark.parametrize(
    "preset_dir",
    [
        path
        for path in sorted(PRESETS_ROOT.glob("*/*"))
        if path.is_dir()
        and path.relative_to(PRESETS_ROOT).parts[0] not in BROKEN_PRESET_GROUPS
    ],
    ids=str,
)
def test_gumtree_preset_rows_match_golden(
    preset_dir: Path,
    snapshot_json: Any,
) -> None:
    old_files = sorted(preset_dir.glob("old.*"))
    new_files = sorted(preset_dir.glob("new.*"))
    assert len(old_files) == 1, preset_dir
    assert len(new_files) == 1, preset_dir
    old_path = old_files[0]
    new_path = new_files[0]
    service = GumTreeDiffEngine(cwd=Path.cwd())
    old_text = old_path.read_text()
    new_text = new_path.read_text()

    diff_json = service._run_gumtree_json(
        left_text=old_text,
        right_text=new_text,
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )
    rows = build_gumtree_rows_from_json(
        diff_json=diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert (
        snapshot_json(name=preset_dir.relative_to(PRESETS_ROOT).as_posix())
        == rows
    )
