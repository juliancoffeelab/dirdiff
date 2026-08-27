"""Property-style checks for difftastic preset projections.

The tests in this module run every difftastic preset, including `borked`
fixtures, through the same row projector and assert broad invariants: source
text is preserved, one-sided rows are not pure unchanged context, and
replacement tokens stay paired sensibly.  Golden snapshots cover exact output
for non-borked presets; this file guards shape and token consistency across
the full preset corpus.
"""

import re
from pathlib import Path
from typing import Literal

import pytest
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from dirdiff.backend import GitBackend
from dirdiff.engines import DiffSide, DirdiffError
from dirdiff.engines.difftastic import DifftasticDiffEngine, DifftasticRow
from dirdiff.engines.difftastic.logic import _difftastic_rows_from_json
from dirdiff.formats import TextRejection, try_decode_text

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"
REPO_ROOT = Path(__file__).resolve().parents[2]
Side = Literal["left", "right"]


__all__: list[str] = []


def _preset_dirs() -> list[Path]:
    """
    Used as pytest parametrizer to run tests over preset directories
    """
    return [path for path in sorted(PRESETS_ROOT.glob("*/*")) if path.is_dir()]


def _current_diff_cases() -> list[tuple[str, str, str, str, str]]:
    """
    Used as pytest parametrizer to run tests over current diff
    """
    backend = GitBackend.discover(cwd=REPO_ROOT)
    cases: list[tuple[str, str, str, str, str]] = []
    for entry in backend.repo_diff(
        left="HEAD",
        right="worktree",
        show_untracked=True,
    ).paths:
        # An absent path is an addition or a deletion, which replays against
        # empty content. `load_versions` answers with the concrete failure for
        # a side it cannot read instead of raising, so a File that vanished
        # between listing and loading drops out of the corpus here.
        left_content = (
            b""
            if entry.left_path is None
            else backend.load_versions(((entry.left_path, "HEAD"),))[0]
        )
        right_content = (
            b""
            if entry.right_path is None
            else backend.load_versions(((entry.right_path, "worktree"),))[0]
        )
        if isinstance(left_content, DirdiffError):
            continue
        if isinstance(right_content, DirdiffError):
            continue

        left_text = try_decode_text(left_content)
        right_text = try_decode_text(right_content)
        # Content this project does not call text has no row projection to
        # replay, exactly as composition classifies it away from a text bay.
        if isinstance(left_text, TextRejection) or isinstance(
            right_text, TextRejection
        ):
            continue
        # A pure rename compares a file against its own content. Difftastic
        # rightly reports it unchanged and the engine returns zero rows, but
        # the replay invariants assert that rows cover every source
        # character, so an identical pair can only fail vacuously. Skip it:
        # there is no diff to check.
        if left_text == right_text:
            continue
        left_name = entry.left_path or entry.display_name
        right_name = entry.right_path or entry.display_name
        cases.append(
            (entry.display_name, left_name, right_name, left_text, right_text)
        )
    return cases


def _preset_paths(preset_dir: Path) -> tuple[Path, Path]:
    """Return the preset's sole old and new files, in that order.

    The preset directory must contain exactly one `old.*` file and exactly one
    `new.*` file.
    """
    old_paths = sorted(preset_dir.glob("old.*"))
    new_paths = sorted(preset_dir.glob("new.*"))
    assert len(old_paths) == 1, preset_dir
    assert len(new_paths) == 1, preset_dir
    return old_paths[0], new_paths[0]


def _difftastic_rows_for_preset(
    preset_dir: Path,
) -> tuple[list[DifftasticRow], str, str]:
    """
    Returns private-ish difftastic parser result, without handling
    bad cases like fallback to unified_diff, if difftastic fails
    """
    old_path, new_path = _preset_paths(preset_dir)
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


def _text_without_difftastic_ignored_trailing_commas(
    source_path: Path, source_text: str
) -> str:
    """Remove punctuation that difftastic deliberately omits from JSON.

    Difftastic marks language-specific trailing commas as ignorable before
    emitting change positions.  The replay invariant compares concrete text,
    so it must normalize the same punctuation away before it asks whether the
    remaining status stream can reconstruct the target.

    Sad but true.
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
    while len(stack) != 0:
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

    def _side_rendered_text(
        rows: list[DifftasticRow],
        *,
        side: Side,
    ) -> str:
        text_key = "left_text" if side == "left" else "right_text"
        no_key = "left_no" if side == "left" else "right_no"
        pieces: list[str] = []
        for row in rows:
            line_no = row.get(no_key)
            if line_no is None:
                continue
            text = row.get(text_key)
            assert isinstance(text, str)
            pieces.append(text)
        return "\n".join(pieces)

    def _token_atoms(text: str) -> list[str]:
        return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|\S", text)

    rows, old_text, new_text = _difftastic_rows_for_preset(preset_dir)
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
        text_key = "left_text" if side == "left" else "right_text"
        tokens_key = "left_tokens" if side == "left" else "right_tokens"
        for row in rows:
            tokens = row.get(tokens_key)
            if tokens is None:
                continue
            assert isinstance(tokens, list)
            if tokens == []:
                continue
            text = row.get(text_key)
            assert isinstance(text, str)
            pieces: list[str] = []
            for token in tokens:
                assert isinstance(token, dict)
                token_text = token.get("text")
                assert isinstance(token_text, str)
                pieces.append(token_text)
            assert "".join(pieces) == text

        source_atoms = _token_atoms(source_text)
        rendered_atoms = _token_atoms(_side_rendered_text(rows, side=side))
        assert rendered_atoms == source_atoms


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_token_spans_match_difftastic_json(
    preset_dir: Path,
) -> None:
    old_path, new_path = _preset_paths(preset_dir)
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
    source_lines = {
        "left": old_text.splitlines(),
        "right": new_text.splitlines(),
    }

    expected: dict[Side, dict[int, set[int]]] = {"left": {}, "right": {}}
    json_sides: tuple[tuple[str, Side], ...] = (
        ("lhs", "left"),
        ("rhs", "right"),
    )
    for chunk in diff_json.get("chunks", []):
        assert isinstance(chunk, list)
        for entry in chunk:
            assert isinstance(entry, dict)
            for json_side, side in json_sides:
                side_data = entry.get(json_side)
                if side_data is None:
                    continue
                assert isinstance(side_data, dict)
                line_number = side_data.get("line_number")
                assert isinstance(line_number, int)
                changes = side_data.get("changes")
                assert isinstance(changes, list)
                line_spans = expected[side].setdefault(line_number, set())
                for change in changes:
                    assert isinstance(change, dict)
                    start = change.get("start")
                    end = change.get("end")
                    assert isinstance(start, int)
                    assert isinstance(end, int)
                    source_bytes = source_lines[side][line_number].encode(
                        "utf-8"
                    )
                    character_start = len(source_bytes[:start].decode("utf-8"))
                    character_end = len(source_bytes[:end].decode("utf-8"))
                    line_spans.update(range(character_start, character_end))

    actual: dict[Side, dict[int, set[int]]] = {"left": {}, "right": {}}
    cursors: dict[Side, dict[int, int]] = {"left": {}, "right": {}}
    diagnostics: list[str] = []
    sides: tuple[Side, Side] = ("left", "right")
    side_fields: tuple[tuple[Side, str, str, str], ...] = (
        ("left", "left_no", "left_text", "left_tokens"),
        ("right", "right_no", "right_text", "right_tokens"),
    )
    for row_index, row in enumerate(rows):
        for side, no_key, text_key, tokens_key in side_fields:
            line_no = row.get(no_key)
            if line_no is None:
                continue
            assert isinstance(line_no, int)
            tokens = row.get(tokens_key)
            if tokens is None:
                continue
            assert isinstance(tokens, list)
            text = row.get(text_key)
            assert isinstance(text, str)
            source_line = source_lines[side][line_no - 1]
            cursor = cursors[side].get(line_no - 1, 0)
            if source_line.startswith(text, cursor):
                start_offset = cursor
            else:
                start_offset = source_line.find(text, cursor)
            if start_offset < 0:
                diagnostics.append(
                    f"row {row_index + 1} {side} line {line_no}: "
                    f"{text!r} is not a source slice after offset {cursor}"
                )
                continue
            token_offset = start_offset
            line_spans = actual[side].setdefault(line_no - 1, set())
            for token in tokens:
                assert isinstance(token, dict)
                token_text = token.get("text")
                assert isinstance(token_text, str)
                status = token.get("status")
                assert isinstance(status, str)
                if status != "unchanged":
                    line_spans.update(
                        range(token_offset, token_offset + len(token_text))
                    )
                token_offset += len(token_text)
            cursors[side][line_no - 1] = start_offset + len(text)

    for side in sides:
        for line_number in sorted(
            set(expected[side].keys()) | set(actual[side].keys())
        ):
            if expected[side].get(line_number, set()) == actual[side].get(
                line_number, set()
            ):
                continue
            diagnostics.append(
                f"{side} line {line_number + 1}: expected changed offsets "
                f"{sorted(expected[side].get(line_number, set()))}, got "
                f"{sorted(actual[side].get(line_number, set()))}"
            )

    assert diagnostics == []


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_line_status_matches_token_statuses(
    preset_dir: Path,
) -> None:
    rows, _, _ = _difftastic_rows_for_preset(preset_dir)

    diagnostics: list[tuple[int, str, str, list[str]]] = []
    for row_index, row in enumerate(rows, start=1):
        has_token_data = False
        token_statuses: list[str] = []
        changed_tokens_are_ws = True
        has_unchanged_text = False
        for side in ("left", "right"):
            tokens_key = "left_tokens" if side == "left" else "right_tokens"
            tokens = row.get(tokens_key)
            if tokens is None:
                continue
            assert isinstance(tokens, list)
            if tokens != []:
                has_token_data = True
            for token in tokens:
                assert isinstance(token, dict)
                status = token.get("status")
                assert isinstance(status, str)
                token_statuses.append(status)
                if status == "unchanged" and token.get("is_ws") is not True:
                    has_unchanged_text = True
                if status != "unchanged" and token.get("is_ws") is not True:
                    changed_tokens_are_ws = False
        if token_statuses == [] and not has_token_data:
            continue

        changed_statuses = {
            status for status in token_statuses if status != "unchanged"
        }
        if changed_statuses == set() or changed_tokens_are_ws:
            expected = "equal"
        elif changed_statuses == {"delete"} and not has_unchanged_text:
            expected = "delete"
        elif changed_statuses == {"insert"} and not has_unchanged_text:
            expected = "insert"
        else:
            expected = "replace"

        actual = row.get("status")
        assert isinstance(actual, str)
        if actual != expected:
            diagnostics.append((row_index, actual, expected, token_statuses))

    assert diagnostics == []


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_line_alignment_matches_difftastic(
    preset_dir: Path,
) -> None:
    old_path, new_path = _preset_paths(preset_dir)
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
    old_line_count = len(old_text.splitlines())
    new_line_count = len(new_text.splitlines())
    expected: list[tuple[int | None, int | None]] = []
    for pair in diff_json.get("aligned_lines", []):
        assert isinstance(pair, list)
        assert len(pair) == 2
        left_raw, right_raw = pair
        assert left_raw is None or isinstance(left_raw, int)
        assert right_raw is None or isinstance(right_raw, int)
        left_no = None if left_raw is None else left_raw + 1
        right_no = None if right_raw is None else right_raw + 1
        if left_no is not None and left_no > old_line_count:
            continue
        if right_no is not None and right_no > new_line_count:
            continue
        expected.append((left_no, right_no))

    actual = [(row.get("left_no"), row.get("right_no")) for row in rows]
    assert actual == expected


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_diff_replays_left_to_right(
    preset_dir: Path,
) -> None:
    """Token statuses should replay the old file into the new file."""
    old_path, new_path = _preset_paths(preset_dir)
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

    rows = DifftasticDiffEngine().render_diff(
        old=DiffSide(
            exists=True,
            text=normalized_texts["left"],
            path_hint=old_path.name,
        ),
        new=DiffSide(
            exists=True,
            text=normalized_texts["right"],
            path_hint=new_path.name,
        ),
    )["rows"]

    # Keep the replay stages in this test in sync with the right-to-left test.

    # Stage 1: collect difftastic statuses as non-whitespace characters.
    side_parts: dict[Side, list[tuple[str, str]]] = {"left": [], "right": []}
    side_fields: tuple[tuple[Side, str, str, str], ...] = (
        ("left", "left_no", "left_text", "left_tokens"),
        ("right", "right_no", "right_text", "right_tokens"),
    )
    for side, no_key, text_key, tokens_key in side_fields:
        for row in rows:
            if row.get(no_key) is None:
                continue
            tokens = row.get(tokens_key)
            if tokens is None or tokens == []:
                row_text = row.get(text_key)
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
    old_source_chars = [
        char for char in normalized_texts["left"] if not char.isspace()
    ]
    new_source_chars = [
        char for char in normalized_texts["right"] if not char.isspace()
    ]
    assert [text for _, text in old_parts] == old_source_chars
    assert [text for _, text in new_parts] == new_source_chars
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

    assert replayed == new_source_chars


@pytest.mark.parametrize("preset_dir", _preset_dirs(), ids=str)
def test_difftastic_preset_diff_replays_right_to_left(
    preset_dir: Path,
) -> None:
    """Token statuses should replay the new file back into the old file."""
    old_path, new_path = _preset_paths(preset_dir)
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

    rows = DifftasticDiffEngine().render_diff(
        old=DiffSide(
            exists=True,
            text=normalized_texts["left"],
            path_hint=old_path.name,
        ),
        new=DiffSide(
            exists=True,
            text=normalized_texts["right"],
            path_hint=new_path.name,
        ),
    )["rows"]

    # Keep the replay stages in this test in sync with the left-to-right test.

    # Stage 1: collect difftastic statuses as non-whitespace characters.
    side_parts: dict[Side, list[tuple[str, str]]] = {"left": [], "right": []}
    side_fields: tuple[tuple[Side, str, str, str], ...] = (
        ("left", "left_no", "left_text", "left_tokens"),
        ("right", "right_no", "right_text", "right_tokens"),
    )
    for side, no_key, text_key, tokens_key in side_fields:
        for row in rows:
            if row.get(no_key) is None:
                continue
            tokens = row.get(tokens_key)
            if tokens is None or tokens == []:
                row_text = row.get(text_key)
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
    new_source_chars = [
        char for char in normalized_texts["right"] if not char.isspace()
    ]
    old_source_chars = [
        char for char in normalized_texts["left"] if not char.isspace()
    ]
    assert [text for _, text in new_parts] == new_source_chars
    assert [text for _, text in old_parts] == old_source_chars
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

    assert replayed == old_source_chars


@pytest.mark.parametrize(
    "case", _current_diff_cases(), ids=lambda case: case[0]
)
def test_difftastic_current_diff_matches_preset_invariants(
    case: tuple[str, str, str, str, str],
    tmp_path: Path,
) -> None:
    display_name, left_name, right_name, left_text, right_text = case
    suffix = Path(right_name).suffix or Path(left_name).suffix or ".txt"
    current_case = tmp_path / display_name.replace("/", "__")
    current_case.mkdir()
    (current_case / f"old{suffix}").write_text(left_text)
    (current_case / f"new{suffix}").write_text(right_text)

    test_difftastic_preset_tokens_stay_in_source_order(current_case)
    test_difftastic_preset_token_spans_match_difftastic_json(current_case)
    test_difftastic_preset_line_status_matches_token_statuses(current_case)
    test_difftastic_preset_line_alignment_matches_difftastic(current_case)
    test_difftastic_preset_diff_replays_left_to_right(current_case)
    test_difftastic_preset_diff_replays_right_to_left(current_case)
