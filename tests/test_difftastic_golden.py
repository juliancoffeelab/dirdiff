"""Golden row-output tests for difftastic preset fixtures.

This module is the snapshot boundary for exact difftastic row projection.  Each
non-borked preset directory supplies old/new source files, and the snapshot name
is the preset path relative to `tests/presets/difftastic`.  It tests
projection output only; subprocess invocation details belong to the engine, and
broad semantic invariants live in `test_difftastic_proptest`.
"""

from pathlib import Path
from typing import Any

import pytest
from helpers import GoldenJsonSnapshotExtension

from dirdiff.engines.difftastic import DifftasticDiffEngine
from dirdiff.engines.difftastic.logic import _difftastic_rows_from_json

PRESETS_ROOT = Path(__file__).parent / "presets" / "difftastic"
GOLDEN_ROOT = Path(__file__).parent / "golden" / "difftastic"
BROKEN_PRESET_GROUPS: set[str] = {
    "borked",
}

__all__: list[str] = []


class DifftasticGoldenSnapshotExtension(GoldenJsonSnapshotExtension):
    preset_root = PRESETS_ROOT
    golden_root = GOLDEN_ROOT
    snapshot_function_name = "test_difftastic_preset_rows_match_golden"


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
        extension_class=DifftasticGoldenSnapshotExtension
    )


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_rows_match_golden(
    preset_dir: Path,
    snapshot_json: Any,
) -> None:
    old_files = sorted(preset_dir.glob("old.*"))
    new_files = sorted(preset_dir.glob("new.*"))
    assert len(old_files) == 1, preset_dir
    assert len(new_files) == 1, preset_dir
    old_path = old_files[0]
    new_path = new_files[0]
    service = DifftasticDiffEngine()
    old_text = old_path.read_text()
    new_text = new_path.read_text()

    diff_json = service._run_difftastic_json(
        left_text=old_text,
        right_text=new_text,
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )
    rows = _difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert (
        snapshot_json(name=preset_dir.relative_to(PRESETS_ROOT).as_posix())
        == rows
    )
