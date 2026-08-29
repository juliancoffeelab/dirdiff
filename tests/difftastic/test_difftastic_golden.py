"""Snapshot exact Difftastic rows for the preset corpus.

Each non-borked preset supplies one old/new source pair and one snapshot named
by its relative preset path. These tests pin exact row and token placement after
Difftastic's JSON is converted to engine rows. Broad replay and consistency
properties remain in `test_difftastic_proptest`; subprocess failures belong to
the engine integration boundary.
"""

from pathlib import Path

import pytest
from helpers import GoldenJsonSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from dirdiff.engines.difftastic import DifftasticDiffEngine
from dirdiff.engines.difftastic.logic import (
    _difftastic_rows_from_json,
)

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"
"""Shared source-pair catalog exercised by Difftastic snapshots.

Every eligible child directory must contain exactly one old and one new file;
the relative directory path becomes the stable snapshot key.
"""
GOLDEN_ROOT = Path(__file__).parents[1] / "golden" / "difftastic"
"""Stored Difftastic row payloads keyed by preset-relative path.

`DifftasticGoldenSnapshotExtension` writes only beneath this directory and
keeps fixture inputs separate from approved renderer output.
"""
BROKEN_PRESET_GROUPS: set[str] = {
    "borked",
}
"""Preset groups intentionally excluded because they do not form valid input.

Their cases still participate in broad property checks where exact approved
rows are not the contract.
"""

__all__: list[str] = []


class DifftasticGoldenSnapshotExtension(GoldenJsonSnapshotExtension):
    """Bind each real diff preset to its checked-in Difftastic row JSON.

    The inherited collector supplies source pairs and this class fixes the
    preset root, golden root, and assertion function name. Snapshots record the
    validated neutral engine rows, not rendered syntax or File payloads.
    """

    preset_root = PRESETS_ROOT
    golden_root = GOLDEN_ROOT
    snapshot_function_name = "test_difftastic_preset_rows_match_golden"


@pytest.fixture
def snapshot_json(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Bind Syrupy to the Difftastic preset and golden directory contract.

    Tests receive the configured assertion and supply only the stable preset
    key plus projected rows.
    """
    return snapshot.with_defaults(
        extension_class=DifftasticGoldenSnapshotExtension
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
def test_difftastic_preset_rows_match_golden(
    preset_dir: Path,
    snapshot_json: SnapshotAssertion,
) -> None:
    """Freeze exact projected rows for every valid two-sided diff preset.

    Each fixture must contain one old and one new file. The test runs real
    Difftastic JSON generation, projects it against those sources, and keys the
    snapshot by the human-readable preset path.

    # Parameters

    - `preset_dir`: One parametrized source-pair directory from `PRESETS_ROOT`.
    - `snapshot_json`: Difftastic-configured snapshot assertion.
    """
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
