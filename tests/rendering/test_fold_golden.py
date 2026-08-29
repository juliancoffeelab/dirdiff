"""Snapshot fold hints for the supported-language preset corpus.

Every non-borked fold preset produces one JSON snapshot named by its relative
path. These snapshots cover parser/query output across languages; focused
policy cases that are clearer inline remain in `test_fold_logic`.
"""

from pathlib import Path

import pytest
from helpers import GoldenJsonSnapshotExtension, build_loaded_diff
from syrupy.assertion import SnapshotAssertion

from dirdiff.rendering import DiffRow

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "fold"
"""Source-pair catalog used to exercise fold discovery by language.

File suffixes select real Tree-sitter policies, and each relative case path
becomes the snapshot identity.
"""
GOLDEN_ROOT = Path(__file__).parents[1] / "golden" / "folds"
"""Stored fold-hint lists keyed by preset-relative path.

Only display-enrichment fold output belongs here; source fixtures remain under
`PRESETS_ROOT`.
"""
BROKEN_PRESETS = {
    "borked",
}
"""Known invalid fold fixtures excluded from parser-backed snapshots.

Approving their incidental failure output would hide that they do not satisfy
the source-pair contract.
"""


class FoldGoldenSnapshotExtension(GoldenJsonSnapshotExtension):
    """Bind each eligible source preset to its checked-in enriched fold hints.

    The shared golden harness uses this class's roots and assertion name to
    compare renderer output. Snapshots cover path-selected structural regions
    after row enrichment, not frontend folded state.
    """

    preset_root = PRESETS_ROOT
    golden_root = GOLDEN_ROOT
    snapshot_function_name = "test_fold_preset_hints_match_golden"


@pytest.fixture
def snapshot_json(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Bind Syrupy to the fold preset and golden directory contract.

    The configured assertion derives approval paths from preset-relative names,
    so tests cannot accidentally write beside their source inputs.
    """
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
    """Freeze fold hints for every valid source-pair fixture.

    # Parameters

    - `preset_dir`: Parametrized old/new source pair for one language case.
    - `snapshot_json`: Fold-configured snapshot assertion.
    """

    def display_row_has_change(row: DiffRow) -> bool:
        """Return whether one enriched row must interrupt a foldable region.

        A non-equal row changes directly. Equal rows still interrupt when any
        decorated part carries changed diff status, matching the production
        neutral-row classifier after token and syntax weaving.
        """
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
