import re
from pathlib import Path
from typing import Any, Literal

import pytest

from dirdiff.diff import (
    DifftasticDiffService,
    PresetBackend,
    _difftastic_rows_from_json,
)

PRESETS_ROOT = Path(__file__).parent / "presets" / "difftastic"
Side = Literal["left", "right"]


def _preset_dirs() -> list[Path]:
    return [path for path in sorted(PRESETS_ROOT.glob("*/*")) if path.is_dir()]


def _side_text_key(side: Side) -> str:
    if side == "left":
        return "left_text"
    return "right_text"


def _side_no_key(side: Side) -> str:
    if side == "left":
        return "left_no"
    return "right_no"


def _side_tokens_key(side: Side) -> str:
    if side == "left":
        return "left_tokens"
    return "right_tokens"


def _single_file(pattern: str, preset_dir: Path) -> Path:
    files = sorted(preset_dir.glob(pattern))
    assert len(files) == 1, preset_dir
    return files[0]


def _preset_rows(
    preset_dir: Path,
) -> tuple[list[dict[str, Any]], str, str]:
    old_path = _single_file("old.*", preset_dir)
    new_path = _single_file("new.*", preset_dir)
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    service = DifftasticDiffService(PresetBackend(PRESETS_ROOT))
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
    return rows, old_text, new_text


def _token_text(tokens: object) -> str:
    assert isinstance(tokens, list)
    pieces: list[str] = []
    for token in tokens:
        assert isinstance(token, dict)
        text = token.get("text")
        assert isinstance(text, str)
        pieces.append(text)
    return "".join(pieces)


def _token_atoms(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|\S", text)


def _assert_subsequence(
    *,
    needles: list[str],
    haystack: list[str],
) -> None:
    cursor = 0
    for needle in needles:
        for index in range(cursor, len(haystack)):
            if haystack[index] == needle:
                cursor = index + 1
                break
        else:
            raise AssertionError((needle, needles, haystack))


def _side_rendered_text(
    rows: list[dict[str, Any]],
    *,
    side: Side,
) -> str:
    text_key = _side_text_key(side)
    pieces: list[str] = []
    for row in rows:
        line_no = row.get(_side_no_key(side))
        if line_no is None:
            continue
        text = row.get(text_key)
        assert isinstance(text, str)
        pieces.append(text)
    return "\n".join(pieces)


def _unchanged_atoms(tokens: object) -> list[str]:
    assert isinstance(tokens, list)
    atoms: list[str] = []
    for token in tokens:
        assert isinstance(token, dict)
        if token.get("status") != "unchanged":
            continue
        text = token.get("text")
        assert isinstance(text, str)
        atoms.extend(_token_atoms(text))
    return atoms


def _changed_atoms(tokens: object) -> list[str]:
    assert isinstance(tokens, list)
    atoms: list[str] = []
    for token in tokens:
        assert isinstance(token, dict)
        if token.get("status") == "unchanged":
            continue
        text = token.get("text")
        assert isinstance(text, str)
        atoms.extend(_token_atoms(text))
    return atoms


def _semantic_atoms(atoms: list[str]) -> list[str]:
    return [
        atom
        for atom in atoms
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", atom)
    ]


def _token_semantic_runs(
    tokens: object, *, status: Literal["changed", "unchanged"]
) -> list[list[str]]:
    assert isinstance(tokens, list)
    runs: list[list[str]] = []
    for token in tokens:
        assert isinstance(token, dict)
        is_unchanged = token.get("status") == "unchanged"
        if status == "unchanged" and not is_unchanged:
            continue
        if status == "changed" and is_unchanged:
            continue
        text = token.get("text")
        assert isinstance(text, str)
        atoms = _semantic_atoms(_token_atoms(text))
        if len(atoms) >= 2:
            runs.append(atoms)
    return runs


def _row_marked_unchanged_atoms(row: dict[str, Any], side: Side) -> list[str]:
    tokens = row.get(_side_tokens_key(side))
    if tokens is None:
        return []
    return _unchanged_atoms(tokens)


def _row_marked_changed_atoms(row: dict[str, Any], side: Side) -> list[str]:
    tokens = row.get(_side_tokens_key(side))
    if tokens is None:
        return []
    return _changed_atoms(tokens)


def _row_marked_unchanged_runs(
    row: dict[str, Any], side: Side
) -> list[list[str]]:
    tokens = row.get(_side_tokens_key(side))
    if tokens is None:
        return []
    return _token_semantic_runs(tokens, status="unchanged")


def _row_marked_changed_runs(
    row: dict[str, Any], side: Side
) -> list[list[str]]:
    tokens = row.get(_side_tokens_key(side))
    if tokens is None:
        return []
    return _token_semantic_runs(tokens, status="changed")


def _row_is_changed_group_member(row: dict[str, Any]) -> bool:
    if row.get("status") != "equal":
        return True
    if row.get("left_tokens") or row.get("right_tokens"):
        return True
    return row.get("left_no") is None or row.get("right_no") is None


def _changed_row_groups(
    rows: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if _row_is_changed_group_member(row):
            current.append(row)
            continue
        if current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _marked_unchanged_atoms_in_group(
    group: list[dict[str, Any]], side: Side
) -> list[str]:
    atoms: list[str] = []
    for row in group:
        atoms.extend(_row_marked_unchanged_atoms(row, side))
    return atoms


def _marked_unchanged_runs_in_group(
    group: list[dict[str, Any]], side: Side
) -> list[list[str]]:
    runs: list[list[str]] = []
    for row in group:
        runs.extend(_row_marked_unchanged_runs(row, side))
    return runs


def _marked_changed_atoms_in_group(
    group: list[dict[str, Any]], side: Side
) -> list[str]:
    atoms: list[str] = []
    for row in group:
        atoms.extend(_row_marked_changed_atoms(row, side))
    return atoms


def _run_is_contiguous_subsequence(
    *, needles: list[str], haystack: list[str]
) -> bool:
    if len(needles) > len(haystack):
        return False
    last_start = len(haystack) - len(needles)
    return any(
        haystack[start : start + len(needles)] == needles
        for start in range(last_start + 1)
    )


def _mismatched_unchanged_runs(
    group: list[dict[str, Any]], side: Side, other_side: Side
) -> list[str]:
    changed_atoms = _semantic_atoms(
        _marked_changed_atoms_in_group(group, other_side)
    )
    return [
        " ".join(run)
        for run in _marked_unchanged_runs_in_group(group, side)
        if _run_is_contiguous_subsequence(needles=run, haystack=changed_atoms)
    ]


def _assert_unchanged_tokens_exist_on_other_side(
    *,
    rows: list[dict[str, Any]],
    side: Side,
    other_side: Side,
) -> None:
    for group in _changed_row_groups(rows):
        mismatched_runs = _mismatched_unchanged_runs(group, side, other_side)
        assert not mismatched_runs, mismatched_runs


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_tokens_stay_in_source_order(
    preset_dir: Path,
) -> None:
    rows, old_text, new_text = _preset_rows(preset_dir)

    sides: tuple[tuple[Side, str], ...] = (
        ("left", old_text),
        ("right", new_text),
    )
    for side, source_text in sides:
        text_key = _side_text_key(side)
        tokens_key = _side_tokens_key(side)
        for row in rows:
            tokens = row.get(tokens_key)
            if tokens is None:
                continue
            assert isinstance(tokens, list)
            if not tokens:
                continue
            text = row.get(text_key)
            assert isinstance(text, str)
            assert _token_text(tokens) == text

        _assert_subsequence(
            needles=_token_atoms(source_text),
            haystack=_token_atoms(_side_rendered_text(rows, side=side)),
        )


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_unchanged_tokens_match_on_both_sides(
    preset_dir: Path,
) -> None:
    rows, _, _ = _preset_rows(preset_dir)

    _assert_unchanged_tokens_exist_on_other_side(
        rows=rows,
        side="left",
        other_side="right",
    )
    _assert_unchanged_tokens_exist_on_other_side(
        rows=rows,
        side="right",
        other_side="left",
    )
