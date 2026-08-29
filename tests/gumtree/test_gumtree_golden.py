"""Snapshot exact GumTree rows for the preset corpus.

Each non-borked preset supplies one old/new source pair and one snapshot named
by its relative preset path. These tests pin exact neutral rows and action-based
token status. Executable behavior belongs to the engine integration, and
display enrichment has separate rendering tests.
"""

from pathlib import Path

import pytest
from helpers import GoldenJsonSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from dirdiff.engines.gumtree import GumTreeDiffEngine
from dirdiff.engines.gumtree.logic import build_gumtree_rows_from_json

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "gumtree"
"""GumTree source-pair catalog used by exact row snapshots.

Each eligible case contributes one parser-selecting old/new file pair and a
relative path used as its snapshot identity.
"""
GOLDEN_ROOT = Path(__file__).parents[1] / "golden" / "gumtree"
"""Stored GumTree row payloads keyed by preset-relative path.

The custom snapshot extension confines approvals here, separate from source
fixtures and Difftastic output.
"""
BROKEN_PRESET_GROUPS: set[str] = {
    "borked",
}
"""Invalid fixture groups excluded from executable-backed snapshots.

They do not promise exact valid GumTree rows, so approving their output would
turn failure behavior into a golden contract.
"""


class GumTreeGoldenSnapshotExtension(GoldenJsonSnapshotExtension):
    """Bind each real diff preset to its checked-in GumTree row JSON.

    The extension fixes corpus and snapshot locations for the shared golden
    harness. Stored values cover GumTree's neutral rows and inline action
    token mapping, before display enrichment or File composition.
    """

    preset_root = PRESETS_ROOT
    golden_root = GOLDEN_ROOT
    snapshot_function_name = "test_gumtree_preset_rows_match_golden"


@pytest.fixture
def snapshot_json(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Bind Syrupy to the GumTree preset and golden directory contract.

    The returned assertion uses the project extension for path calculation;
    individual tests provide only their relative key and row value.
    """
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
    snapshot_json: SnapshotAssertion,
) -> None:
    """Freeze exact GumTree range mapping for every valid preset pair.

    # Parameters

    - `preset_dir`: Parametrized directory containing one old and one new file.
    - `snapshot_json`: GumTree-configured snapshot assertion.
    """
    old_files = sorted(preset_dir.glob("old.*"))
    new_files = sorted(preset_dir.glob("new.*"))
    assert len(old_files) == 1, preset_dir
    assert len(new_files) == 1, preset_dir
    old_path = old_files[0]
    new_path = new_files[0]
    service = GumTreeDiffEngine()
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
