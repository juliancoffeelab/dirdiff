"""Property-style checks for difftastic preset projections.

The tests in this module run every non-borked difftastic preset through the
same row projector and assert broad invariants: source text is preserved,
one-sided rows are not pure unchanged context, and replacement tokens stay
paired sensibly.  Golden snapshots cover exact output; this file guards shape
and semantic consistency across the preset corpus.
"""

import re
from pathlib import Path
from typing import Any, Literal

import pytest

from dirdiff.engines.difftastic import (
    DifftasticDiffEngine,
    DifftasticRow,
)
from dirdiff.engines.difftastic.logic import (
    _difftastic_rows_from_json,
)

PRESETS_ROOT = Path(__file__).parent / "presets" / "difftastic"
Side = Literal["left", "right"]

__all__: list[str] = []


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
) -> tuple[list[DifftasticRow], str, str]:
    old_path = _single_file("old.*", preset_dir)
    new_path = _single_file("new.*", preset_dir)
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    service = DifftasticDiffEngine()
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


def _side_rendered_text(
    rows: list[DifftasticRow],
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


def _replace_atoms(tokens: object) -> list[str]:
    assert isinstance(tokens, list)
    atoms: list[str] = []
    for token in tokens:
        assert isinstance(token, dict)
        if token.get("status") != "replace":
            continue
        if token.get("is_ws") is True:
            continue
        text = token.get("text")
        assert isinstance(text, str)
        atoms.extend(_token_atoms(text))
    return atoms


def _meaningful_token_atoms(token: dict[str, Any]) -> list[str]:
    text = token.get("text")
    assert isinstance(text, str)
    return _semantic_atoms(_token_atoms(text))


def _semantic_atoms(atoms: list[str]) -> list[str]:
    return [
        atom
        for atom in atoms
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", atom)
    ]


def _unchanged_semantic_runs(tokens: object) -> list[list[str]]:
    assert isinstance(tokens, list)
    runs: list[list[str]] = []
    for token in tokens:
        assert isinstance(token, dict)
        if token.get("status") != "unchanged":
            continue
        text = token.get("text")
        assert isinstance(text, str)
        atoms = _semantic_atoms(_token_atoms(text))
        if len(atoms) >= 2:
            runs.append(atoms)
    return runs


def _row_marked_changed_atoms(row: DifftasticRow, side: Side) -> list[str]:
    tokens = row.get(_side_tokens_key(side))
    if tokens is None:
        return []
    return _changed_atoms(tokens)


def _row_marked_replace_atoms(row: DifftasticRow, side: Side) -> list[str]:
    tokens = row.get(_side_tokens_key(side))
    if tokens is None:
        return []
    return _replace_atoms(tokens)


def _row_marked_unchanged_runs(
    row: DifftasticRow, side: Side
) -> list[list[str]]:
    tokens = row.get(_side_tokens_key(side))
    if tokens is None:
        return []
    return _unchanged_semantic_runs(tokens)


def _row_is_changed_group_member(row: DifftasticRow) -> bool:
    if row.get("status") != "equal":
        return True
    if row.get("left_tokens") or row.get("right_tokens"):
        return True
    return row.get("left_no") is None or row.get("right_no") is None


def _changed_row_groups(
    rows: list[DifftasticRow],
) -> list[list[tuple[int, DifftasticRow]]]:
    groups: list[list[tuple[int, DifftasticRow]]] = []
    current: list[tuple[int, DifftasticRow]] = []
    for index, row in enumerate(rows):
        if _row_is_changed_group_member(row):
            current.append((index, row))
            continue
        if current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _marked_changed_atoms_in_group(
    group: list[tuple[int, DifftasticRow]], side: Side
) -> list[str]:
    atoms: list[str] = []
    for _, row in group:
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


def _one_sided_equal_context_run(
    row: DifftasticRow,
    *,
    side: Side,
    other_side: Side,
) -> list[str]:
    if row.get("status") != "equal":
        return []
    if row.get(_side_no_key(side)) is None:
        return []
    if row.get(_side_no_key(other_side)) is not None:
        return []

    text = row.get(_side_text_key(side))
    assert isinstance(text, str)
    return _token_atoms(text)


def _unchanged_context_leak_diagnostics(
    rows: list[DifftasticRow],
) -> list[str]:
    diagnostics: list[str] = []
    for group_index, group in enumerate(_changed_row_groups(rows)):
        _collect_unchanged_context_leak_diagnostics(
            diagnostics,
            group_index=group_index,
            group=group,
            side="left",
            other_side="right",
        )
        _collect_unchanged_context_leak_diagnostics(
            diagnostics,
            group_index=group_index,
            group=group,
            side="right",
            other_side="left",
        )
    return diagnostics


def _collect_unchanged_context_leak_diagnostics(
    diagnostics: list[str],
    *,
    group_index: int,
    group: list[tuple[int, DifftasticRow]],
    side: Side,
    other_side: Side,
) -> None:
    changed_atoms = _marked_changed_atoms_in_group(group, other_side)
    changed_semantic_atoms = _semantic_atoms(changed_atoms)

    for row_index, row in group:
        for run in _row_marked_unchanged_runs(row, side):
            if _run_is_contiguous_subsequence(
                needles=run,
                haystack=changed_semantic_atoms,
            ):
                diagnostics.append(
                    _context_leak_diagnostic(
                        group_index=group_index,
                        row_index=row_index,
                        side=side,
                        other_side=other_side,
                        text=row.get(_side_text_key(side)),
                        run=run,
                    )
                )

        context_run = _one_sided_equal_context_run(
            row, side=side, other_side=other_side
        )
        if not context_run:
            continue

        if _run_is_contiguous_subsequence(
            needles=context_run,
            haystack=changed_atoms,
        ):
            diagnostics.append(
                _context_leak_diagnostic(
                    group_index=group_index,
                    row_index=row_index,
                    side=side,
                    other_side=other_side,
                    text=row.get(_side_text_key(side)),
                    run=context_run,
                )
            )


def _context_leak_diagnostic(
    *,
    group_index: int,
    row_index: int,
    side: Side,
    other_side: Side,
    text: object,
    run: list[str],
) -> str:
    assert isinstance(text, str)
    return (
        f"group {group_index + 1}, row {row_index + 1}: "
        f"{side} context {run!r} from {text!r} is changed on {other_side}"
    )


def _unpaired_replace_token_diagnostics(
    rows: list[DifftasticRow],
) -> list[str]:
    diagnostics: list[str] = []
    for row_index, row in enumerate(rows):
        left_atoms = _row_marked_replace_atoms(row, "left")
        right_atoms = _row_marked_replace_atoms(row, "right")
        if left_atoms and not right_atoms:
            diagnostics.append(
                _unpaired_replace_token_diagnostic(
                    row_index=row_index,
                    side="left",
                    other_side="right",
                    text=row.get("left_text"),
                    atoms=left_atoms,
                )
            )
        if right_atoms and not left_atoms:
            diagnostics.append(
                _unpaired_replace_token_diagnostic(
                    row_index=row_index,
                    side="right",
                    other_side="left",
                    text=row.get("right_text"),
                    atoms=right_atoms,
                )
            )
    return diagnostics


def _unpaired_replace_token_diagnostic(
    *,
    row_index: int,
    side: Side,
    other_side: Side,
    text: object,
    atoms: list[str],
) -> str:
    assert isinstance(text, str)
    return (
        f"row {row_index + 1}: {side} replace tokens {atoms!r} "
        f"from {text!r} have no replace tokens on {other_side}"
    )


def _one_sided_change_side(row: DifftasticRow) -> Side | None:
    if row.get("left_no") is not None and row.get("right_no") is None:
        return "left"
    if row.get("left_no") is None and row.get("right_no") is not None:
        return "right"
    return None


def _pure_unchanged_one_sided_change_texts(
    rows: list[DifftasticRow],
) -> list[str]:
    broken_texts: list[str] = []
    for row in rows:
        if row.get("status") not in {"delete", "insert"}:
            continue

        side = _one_sided_change_side(row)
        if side is None:
            continue

        tokens = row.get(_side_tokens_key(side))
        if tokens is None:
            continue
        assert isinstance(tokens, list)

        meaningful_tokens = [
            token
            for token in tokens
            if isinstance(token, dict) and _meaningful_token_atoms(token)
        ]
        if not meaningful_tokens:
            continue

        if all(
            token.get("status") == "unchanged" for token in meaningful_tokens
        ):
            text = row.get(_side_text_key(side))
            assert isinstance(text, str)
            broken_texts.append(text)
    return broken_texts


def _assert_one_sided_changes_are_not_pure_unchanged_context(
    rows: list[DifftasticRow],
) -> None:
    broken_texts = _pure_unchanged_one_sided_change_texts(rows)
    assert not broken_texts, broken_texts


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_tokens_stay_in_source_order(
    preset_dir: Path,
) -> None:
    """This test verifies that for both old source and new source, the output
    on the left and on the right has all tokens in full, and in the same order
    as they were in the original sources.
    """
    rows, old_text, new_text = _preset_rows(preset_dir)
    # Difftastic can report no structured rows for changed files; the service
    # layer handles that by falling back to git-style rows, so parser-level
    # row/token invariants have nothing to check here.
    if rows == []:
        return

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

        source_atoms = _token_atoms(source_text)
        rendered_atoms = _token_atoms(_side_rendered_text(rows, side=side))
        assert rendered_atoms == source_atoms


@pytest.mark.parametrize(
    "preset_dir",
    _preset_dirs(),
    ids=str,
)
def test_difftastic_preset_unchanged_tokens_match_on_both_sides(
    preset_dir: Path,
) -> None:
    """This test verifies that context rendered as unchanged on one side is
    not rendered as changed on the other side of the same change group.
    """
    rows, _, _ = _preset_rows(preset_dir)

    diagnostics = _unchanged_context_leak_diagnostics(rows)
    assert not diagnostics, diagnostics


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_replace_tokens_are_paired_on_both_sides(
    preset_dir: Path,
) -> None:
    """This test verifies that a replacement token on one side has a
    replacement token on the other side of the same rendered row.
    """
    rows, _, _ = _preset_rows(preset_dir)

    diagnostics = _unpaired_replace_token_diagnostics(rows)
    assert not diagnostics, diagnostics


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_one_sided_changes_include_changed_tokens(
    preset_dir: Path,
) -> None:
    """This test verifies that one-sided changed rows are not made entirely
    from meaningful tokens marked unchanged.
    """
    rows, _, _ = _preset_rows(preset_dir)

    _assert_one_sided_changes_are_not_pure_unchanged_context(rows)
