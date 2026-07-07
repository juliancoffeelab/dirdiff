"""Golden row-output tests for GumTree presets."""

from pathlib import Path
from typing import Any

import pytest
from helpers import GoldenJsonSnapshotExtension, WorkspaceDiffServiceAdapter

from dirdiff.backend import PresetBackend
from dirdiff.engines.gumtree import GumTreeDiffEngine

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parent / "presets" / "gumtree"
GOLDEN_ROOT = Path(__file__).parent / "golden" / "gumtree"
BROKEN_PRESET_GROUPS: set[str] = {
    "borked",
}


class GumTreeGoldenSnapshotExtension(GoldenJsonSnapshotExtension):
    preset_root = PRESETS_ROOT
    golden_root = GOLDEN_ROOT
    snapshot_function_name = "test_gumtree_preset_rows_match_golden"


def _preset_dirs() -> list[Path]:
    return [
        path
        for path in sorted(PRESETS_ROOT.glob("*/*"))
        if path.is_dir()
        and path.relative_to(PRESETS_ROOT).parts[0] not in BROKEN_PRESET_GROUPS
    ]


@pytest.fixture
def snapshot_json(snapshot: Any) -> Any:
    return snapshot.with_defaults(
        extension_class=GumTreeGoldenSnapshotExtension
    )


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
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
    preset_name = preset_dir.relative_to(PRESETS_ROOT).as_posix()
    service = WorkspaceDiffServiceAdapter(
        PresetBackend(PRESETS_ROOT),
        GumTreeDiffEngine(cwd=Path.cwd()),
    )

    payload = service.build_git_diff_paths(
        left_path=f"{preset_name}/{old_path.name}",
        right_path=f"{preset_name}/{new_path.name}",
        left=preset_dir.relative_to(PRESETS_ROOT).parts[0],
        right="new",
    )

    assert snapshot_json(name=preset_name) == {
        "engine_warning": payload.get("engine_warning"),
        "rows": payload["rows"],
        "summary": payload["summary"],
    }
