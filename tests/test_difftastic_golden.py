"""Golden row-output tests for difftastic presets.

Each non-broken preset source pair is run through difftastic plus our adapter,
then compared against the full expected row JSON in tests/golden/difftastic.
"""

import json
from pathlib import Path

import pytest

from dirdiff.diff import (
    DifftasticDiffService,
    PresetBackend,
    _difftastic_rows_from_json,
)

PRESETS_ROOT = Path(__file__).parent / "presets" / "difftastic"
GOLDEN_ROOT = Path(__file__).parent / "golden" / "difftastic"


def _preset_dirs() -> list[Path]:
    return [path for path in sorted(PRESETS_ROOT.iterdir()) if path.is_dir()]


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=lambda path: path.name)
def test_difftastic_preset_rows_match_golden(preset_dir: Path) -> None:
    old_files = sorted(preset_dir.glob("old.*"))
    new_files = sorted(preset_dir.glob("new.*"))
    assert len(old_files) == 1, preset_dir
    assert len(new_files) == 1, preset_dir
    old_path = old_files[0]
    new_path = new_files[0]
    service = DifftasticDiffService(PresetBackend(PRESETS_ROOT))
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

    golden_path = GOLDEN_ROOT / f"{preset_dir.name}.json"
    expected = json.loads(golden_path.read_text())
    assert rows == expected
