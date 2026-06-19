"""Golden row-output tests for difftastic presets."""

import json
from pathlib import Path
from typing import Any

import pytest
from syrupy.data import Snapshot, SnapshotCollection
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from dirdiff.diff import (
    DifftasticDiffService,
    PresetBackend,
    _difftastic_rows_from_json,
)

PRESETS_ROOT = Path(__file__).parent / "presets" / "difftastic"
GOLDEN_ROOT = Path(__file__).parent / "golden" / "difftastic"
BROKEN_PRESET_GROUPS: set[str] = {
    "borked",
}


class DifftasticGoldenSnapshotExtension(SingleFileSnapshotExtension):
    _write_mode = WriteMode.TEXT
    file_extension = "json"
    snapshot_function_name = "test_difftastic_preset_rows_match_golden"

    def serialize(
        self,
        data: Any,
        *,
        exclude: Any = None,
        include: Any = None,
        matcher: Any = None,
    ) -> str:
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

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
    def dirname(cls, *, test_location: Any) -> str:
        return str(GOLDEN_ROOT)

    @classmethod
    def get_snapshot_name(
        cls, *, test_location: Any, index: int | str = 0
    ) -> str:
        if isinstance(index, str):
            return test_location.testname
        return super().get_snapshot_name(
            test_location=test_location, index=index
        )

    @classmethod
    def get_location(cls, *, test_location: Any, index: int | str) -> str:
        if isinstance(index, str):
            return str(
                GOLDEN_ROOT
                / index
                / f"{test_location.basename}.{cls.file_extension}"
            )
        return super().get_location(test_location=test_location, index=index)

    def read_snapshot_collection(self, *, snapshot_location: str) -> Any:
        snapshot_collection = SnapshotCollection(location=snapshot_location)
        snapshot_collection.add(Snapshot(name=self.snapshot_function_name))
        return snapshot_collection


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

    assert (
        snapshot_json(name=preset_dir.relative_to(PRESETS_ROOT).as_posix())
        == rows
    )
