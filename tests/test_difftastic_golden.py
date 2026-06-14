"""Golden row-output tests for difftastic presets."""

import json
from pathlib import Path

import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from dirdiff.diff import (
    DifftasticDiffService,
    PresetBackend,
    _difftastic_rows_from_json,
)

PRESETS_ROOT = Path(__file__).parent / "presets" / "difftastic"
GOLDEN_ROOT = Path(__file__).parent / "golden" / "difftastic"
BROKEN_PRESET_NAMES: set[str] = set()


class DifftasticGoldenSnapshotExtension(SingleFileSnapshotExtension):
    _write_mode = WriteMode.TEXT
    file_extension = "json"

    def serialize(
        self,
        data,
        *,
        exclude=None,
        include=None,
        matcher=None,
    ) -> str:
        return json.dumps(data, indent=2) + "\n"

    def matches(
        self,
        *,
        serialized_data,
        snapshot_data,
    ) -> bool:
        return json.loads(serialized_data) == json.loads(snapshot_data)

    @classmethod
    def dirname(cls, *, test_location) -> str:
        return str(GOLDEN_ROOT)

    @classmethod
    def get_snapshot_name(cls, *, test_location, index=0) -> str:
        if isinstance(index, str):
            return index
        return super().get_snapshot_name(
            test_location=test_location, index=index
        )


def _preset_dirs() -> list[Path]:
    return [
        path
        for path in sorted(PRESETS_ROOT.iterdir())
        if path.is_dir() and path.name not in BROKEN_PRESET_NAMES
    ]


@pytest.fixture
def snapshot_json(snapshot):
    return snapshot.with_defaults(
        extension_class=DifftasticGoldenSnapshotExtension
    )


@pytest.mark.parametrize(
    "preset_dir", _preset_dirs(), ids=lambda path: path.name
)
def test_difftastic_preset_rows_match_golden(
    preset_dir: Path,
    snapshot_json,
) -> None:
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

    assert snapshot_json(name=preset_dir.name) == rows
