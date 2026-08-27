"""Golden fold-hint tests for fold preset fixtures.

This module snapshots the fold hints produced by display enrichment for every
non-borked fold preset.  Presets provide compact source examples; the custom
syrupy extension stores one JSON snapshot per preset path.  Focused behavioral
cases that are easier to read inline belong in `test_fold_logic`.
"""

from pathlib import Path

import pytest
from helpers import GoldenJsonSnapshotExtension, build_loaded_diff
from syrupy.assertion import SnapshotAssertion

from dirdiff.rendering import DiffRow

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "fold"
GOLDEN_ROOT = Path(__file__).parents[1] / "golden" / "folds"
BROKEN_PRESETS = {
    "borked",
}

__all__: list[str] = []


class FoldGoldenSnapshotExtension(GoldenJsonSnapshotExtension):
    preset_root = PRESETS_ROOT
    golden_root = GOLDEN_ROOT
    snapshot_function_name = "test_fold_preset_hints_match_golden"


@pytest.fixture
def snapshot_json(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.with_defaults(extension_class=FoldGoldenSnapshotExtension)


@pytest.mark.parametrize(
    "preset_dir",
    [
        path
        for path in sorted(PRESETS_ROOT.glob("*/*"))
        if path.is_dir()
        and path.relative_to(PRESETS_ROOT).parts[0] not in BROKEN_PRESETS
    ],
    ids=str,
)
def test_fold_preset_hints_match_golden(
    preset_dir: Path,
    snapshot_json: SnapshotAssertion,
) -> None:
    def display_row_has_change(row: DiffRow) -> bool:
        """Classify change from an enriched row's display fields."""
        if row["status"] != "equal":
            return True
        return any(
            part["diff_status"] != "unchanged"
            for part in (*row["left_parts"], *row["right_parts"])
        )

    old_files = sorted(preset_dir.glob("old.*"))
    new_files = sorted(preset_dir.glob("new.*"))
    assert len(old_files) == 1, preset_dir
    assert len(new_files) == 1, preset_dir
    old_path = old_files[0]
    new_path = new_files[0]

    diff = build_loaded_diff(
        display_name=new_path.name,
        left_label="old",
        right_label="new",
        left_exists=True,
        right_exists=True,
        left_text=old_path.read_text(),
        right_text=new_path.read_text(),
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )

    assert snapshot_json(
        name=preset_dir.relative_to(PRESETS_ROOT).as_posix()
    ) == diff.get("fold_hints", [])

    for hint in diff.get("fold_hints", []):
        folded_rows = diff["rows"][hint["start_row"] : hint["end_row"]]
        assert all(not display_row_has_change(row) for row in folded_rows), hint
