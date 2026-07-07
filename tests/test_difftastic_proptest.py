"""Property-style checks for difftastic preset projections.

The tests in this module run every difftastic preset, including `borked`
fixtures, through the same row projector and assert broad invariants: source
text is preserved, one-sided rows are not pure unchanged context, and
replacement tokens stay paired sensibly.  Golden snapshots cover exact output
for non-borked presets; this file guards shape and semantic consistency across
the full preset corpus.
"""

import re
from pathlib import Path
from typing import Any, Literal

import pytest
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

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
    left_tokens = row.get("left_tokens")
    right_tokens = row.get("right_tokens")
    if left_tokens != [] and left_tokens is not None:
        return True
    if right_tokens != [] and right_tokens is not None:
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
        if current != []:
            groups.append(current)
            current = []
    if current != []:
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
        if left_atoms != [] and right_atoms == []:
            diagnostics.append(
                _unpaired_replace_token_diagnostic(
                    row_index=row_index,
                    side="left",
                    other_side="right",
                    text=row.get("left_text"),
                    atoms=left_atoms,
                )
            )
        if right_atoms != [] and left_atoms == []:
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


def _text_without_difftastic_ignored_trailing_commas(
    source_path: Path, source_text: str
) -> str:
    """Remove punctuation that difftastic deliberately omits from JSON.

    Difftastic marks language-specific trailing commas as ignorable before
    emitting change positions.  The replay invariant compares concrete text,
    so it must normalize the same punctuation away before it asks whether the
    remaining status stream can reconstruct the target.
    """
    match source_path.suffix:
        case ".py":
            language = Language(tree_sitter_python.language())
            ignored_parents = {
                "dictionary",
                "list",
                "set",
                "argument_list",
                "parameters",
            }
        case ".rs":
            language = Language(tree_sitter_rust.language())
            ignored_parents = {
                "arguments",
                "parameters",
                "type_parameters",
                "field_declaration_list",
                "token_tree",
            }
        case ".js" | ".jsx":
            language = Language(tree_sitter_javascript.language())
            ignored_parents = {
                "object",
                "array",
                "arguments",
                "formal_parameters",
            }
        case ".ts":
            language = Language(tree_sitter_typescript.language_typescript())
            ignored_parents = {
                "object",
                "array",
                "arguments",
                "formal_parameters",
            }
        case ".tsx":
            language = Language(tree_sitter_typescript.language_tsx())
            ignored_parents = {
                "object",
                "array",
                "arguments",
                "formal_parameters",
            }
        case _:
            return source_text

    parser = Parser(language)
    source_bytes = source_text.encode()
    tree = parser.parse(source_bytes)
    ranges_to_remove: list[tuple[int, int]] = []
    stack: list[Node] = [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in ignored_parents:
            continue

        children = [child for child in node.children if not child.is_extra]
        if len(children) < 2:
            continue

        candidate = children[-2]
        if children[-1].type == ",":
            candidate = children[-1]
        if candidate.type != ",":
            continue

        comma_text = source_bytes[candidate.start_byte : candidate.end_byte]
        assert comma_text == b","
        ranges_to_remove.append((candidate.start_byte, candidate.end_byte))

    normalized_pieces: list[str] = []
    cursor = 0
    for start, end in sorted(ranges_to_remove):
        normalized_pieces.append(source_bytes[cursor:start].decode())
        cursor = end
    normalized_pieces.append(source_bytes[cursor:].decode())
    return "".join(normalized_pieces)


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
def test_difftastic_preset_diff_replays_left_to_right(
    preset_dir: Path,
) -> None:
    """Token statuses should replay the old file into the new file."""
    if preset_dir.name in {
        "create-app-runtime-config-collapses-service-block",
        "rust-quest-resolve-chain-wraps-poorly",
        "typescript-repo-fold-controls-show-placeholder-aligns-poorly",
    }:
        pytest.xfail("difftastic emits non-replayable status streams")

    old_path = _single_file("old.*", preset_dir)
    new_path = _single_file("new.*", preset_dir)
    source_texts = {
        "left": old_path.read_text(),
        "right": new_path.read_text(),
    }
    source_paths = {"left": old_path, "right": new_path}
    normalized_texts: dict[Side, str] = {"left": "", "right": ""}
    sides: tuple[Side, Side] = ("left", "right")

    # Stage 0: remove the trailing commas that difftastic intentionally
    # ignores before it emits JSON change positions.
    for side in sides:
        normalized_texts[side] = (
            _text_without_difftastic_ignored_trailing_commas(
                source_paths[side],
                source_texts[side],
            )
        )

    service = DifftasticDiffEngine()
    diff_json = service._run_difftastic_json(
        left_text=normalized_texts["left"],
        right_text=normalized_texts["right"],
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )
    rows = _difftastic_rows_from_json(
        diff_json,
        left_text=normalized_texts["left"],
        right_text=normalized_texts["right"],
    )

    # Keep the replay stages in this test in sync with the right-to-left test.

    # Stage 1: collect difftastic statuses as non-whitespace characters.
    side_parts: dict[Side, list[tuple[str, str]]] = {"left": [], "right": []}
    for side in sides:
        for row in rows:
            if row.get(_side_no_key(side)) is None:
                continue
            tokens = row.get(_side_tokens_key(side))
            if tokens is None or tokens == []:
                row_text = row.get(_side_text_key(side))
                assert isinstance(row_text, str)
                row_status = row.get("status")
                assert isinstance(row_status, str)
                if row_status == "equal":
                    token_status = "unchanged"
                elif side == "left" and row.get("right_no") is None:
                    assert row_status == "delete"
                    token_status = "delete"
                elif side == "right" and row.get("left_no") is None:
                    assert row_status == "insert"
                    token_status = "insert"
                else:
                    token_status = "unchanged"
                for char in row_text:
                    if char.isspace():
                        continue
                    side_parts[side].append((token_status, char))
                continue
            assert isinstance(tokens, list)
            for token in tokens:
                assert isinstance(token, dict)
                token_text = token.get("text")
                assert isinstance(token_text, str)
                listed_token_status = token.get("status")
                assert isinstance(listed_token_status, str)
                for char in token_text:
                    if char.isspace():
                        continue
                    side_parts[side].append((listed_token_status, char))

    old_parts = side_parts["left"]
    new_parts = side_parts["right"]
    operations: list[tuple[str, str]] = []
    old_cursor = 0
    new_cursor = 0

    # Stage 2: walk source and target status streams into edit operations.
    while old_cursor < len(old_parts) or new_cursor < len(new_parts):
        if old_cursor >= len(old_parts):
            status, text = new_parts[new_cursor]
            assert status in {"insert", "replace"}
            operations.append(("insert", text))
            new_cursor += 1
            continue
        if new_cursor >= len(new_parts):
            status, text = old_parts[old_cursor]
            assert status in {"delete", "replace"}
            operations.append(("remove", text))
            old_cursor += 1
            continue

        old_status, old_text = old_parts[old_cursor]
        new_status, new_text = new_parts[new_cursor]
        if old_status == "unchanged":
            while new_status != "unchanged" or new_text != old_text:
                assert new_status in {"insert", "replace"}
                operations.append(("insert", new_text))
                new_cursor += 1
                assert new_cursor < len(new_parts)
                new_status, new_text = new_parts[new_cursor]
            operations.append(("keep", old_text))
            old_cursor += 1
            new_cursor += 1
            continue

        assert old_status in {"delete", "replace"}
        operations.append(("remove", old_text))
        old_cursor += 1

    replayed: list[str] = []
    old_cursor = 0

    # Stage 3: apply collected operations to source and compare with target.
    for operation, text in operations:
        if operation == "insert":
            replayed.append(text)
            continue
        assert old_cursor < len(old_parts)
        assert old_parts[old_cursor][1] == text
        if operation == "keep":
            replayed.append(text)
        old_cursor += 1

    assert "".join(replayed) == "".join(text for _, text in new_parts)


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_diff_replays_right_to_left(
    preset_dir: Path,
) -> None:
    """Token statuses should replay the new file back into the old file."""
    if preset_dir.name in {
        "create-app-runtime-config-collapses-service-block",
        "rust-quest-resolve-chain-wraps-poorly",
        "typescript-repo-fold-controls-show-placeholder-aligns-poorly",
    }:
        pytest.xfail("difftastic emits non-replayable status streams")

    old_path = _single_file("old.*", preset_dir)
    new_path = _single_file("new.*", preset_dir)
    source_texts = {
        "left": old_path.read_text(),
        "right": new_path.read_text(),
    }
    source_paths = {"left": old_path, "right": new_path}
    normalized_texts: dict[Side, str] = {"left": "", "right": ""}
    sides: tuple[Side, Side] = ("left", "right")

    # Stage 0: remove the trailing commas that difftastic intentionally
    # ignores before it emits JSON change positions.
    for side in sides:
        normalized_texts[side] = (
            _text_without_difftastic_ignored_trailing_commas(
                source_paths[side],
                source_texts[side],
            )
        )

    service = DifftasticDiffEngine()
    diff_json = service._run_difftastic_json(
        left_text=normalized_texts["left"],
        right_text=normalized_texts["right"],
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )
    rows = _difftastic_rows_from_json(
        diff_json,
        left_text=normalized_texts["left"],
        right_text=normalized_texts["right"],
    )

    # Keep the replay stages in this test in sync with the left-to-right test.

    # Stage 1: collect difftastic statuses as non-whitespace characters.
    side_parts: dict[Side, list[tuple[str, str]]] = {"left": [], "right": []}
    for side in sides:
        for row in rows:
            if row.get(_side_no_key(side)) is None:
                continue
            tokens = row.get(_side_tokens_key(side))
            if tokens is None or tokens == []:
                row_text = row.get(_side_text_key(side))
                assert isinstance(row_text, str)
                row_status = row.get("status")
                assert isinstance(row_status, str)
                if row_status == "equal":
                    token_status = "unchanged"
                elif side == "left" and row.get("right_no") is None:
                    assert row_status == "delete"
                    token_status = "delete"
                elif side == "right" and row.get("left_no") is None:
                    assert row_status == "insert"
                    token_status = "insert"
                else:
                    token_status = "unchanged"
                for char in row_text:
                    if char.isspace():
                        continue
                    side_parts[side].append((token_status, char))
                continue
            assert isinstance(tokens, list)
            for token in tokens:
                assert isinstance(token, dict)
                token_text = token.get("text")
                assert isinstance(token_text, str)
                listed_token_status = token.get("status")
                assert isinstance(listed_token_status, str)
                for char in token_text:
                    if char.isspace():
                        continue
                    side_parts[side].append((listed_token_status, char))

    new_parts = side_parts["right"]
    old_parts = side_parts["left"]
    operations: list[tuple[str, str]] = []
    new_cursor = 0
    old_cursor = 0

    # Stage 2: walk source and target status streams into edit operations.
    while new_cursor < len(new_parts) or old_cursor < len(old_parts):
        if new_cursor >= len(new_parts):
            status, text = old_parts[old_cursor]
            assert status in {"delete", "replace"}
            operations.append(("insert", text))
            old_cursor += 1
            continue
        if old_cursor >= len(old_parts):
            status, text = new_parts[new_cursor]
            assert status in {"insert", "replace"}
            operations.append(("remove", text))
            new_cursor += 1
            continue

        new_status, new_text = new_parts[new_cursor]
        old_status, old_text = old_parts[old_cursor]
        if new_status == "unchanged":
            while old_status != "unchanged" or old_text != new_text:
                assert old_status in {"delete", "replace"}
                operations.append(("insert", old_text))
                old_cursor += 1
                assert old_cursor < len(old_parts)
                old_status, old_text = old_parts[old_cursor]
            operations.append(("keep", new_text))
            new_cursor += 1
            old_cursor += 1
            continue

        assert new_status in {"insert", "replace"}
        operations.append(("remove", new_text))
        new_cursor += 1

    replayed: list[str] = []
    new_cursor = 0

    # Stage 3: apply collected operations to source and compare with target.
    for operation, text in operations:
        if operation == "insert":
            replayed.append(text)
            continue
        assert new_cursor < len(new_parts)
        assert new_parts[new_cursor][1] == text
        if operation == "keep":
            replayed.append(text)
        new_cursor += 1

    assert "".join(replayed) == "".join(text for _, text in old_parts)


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
