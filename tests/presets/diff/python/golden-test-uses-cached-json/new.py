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

    Each fixture must contain one old and one new file. The test loads cached real
    Difftastic JSON, projects it against those sources, and keys the
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
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")

    diff_json = tool_json(
        "difftastic",
        f"presets/{preset_dir.relative_to(PRESETS_ROOT).as_posix()}",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert (
        snapshot_json(name=preset_dir.relative_to(PRESETS_ROOT).as_posix())
        == rows
    )
