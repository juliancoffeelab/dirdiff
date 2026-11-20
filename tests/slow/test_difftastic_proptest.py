import re
from hashlib import sha256
from pathlib import Path
from typing import Literal, TypedDict

import pytest
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript
from helpers import ToolText, tool_json
from tree_sitter import Language, Node, Parser

from dirdiff.change_source import GitChangeSource, GitRepository
from dirdiff.engines import DiffEngineRow
from dirdiff.engines.difftastic.logic import difftastic_rows_from_json
from dirdiff.formats import try_decode_text
from dirdiff.util import Ok

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"
REPO_ROOT = Path(__file__).resolve().parents[2]
Side = Literal["left", "right"]


class PresetCase(TypedDict):
    old_path: Path
    new_path: Path
    cache_key: str
    allow_mismatches: bool


class WorktreeCase(TypedDict):
    display_name: str
    left_name: str
    right_name: str
    left_text: str
    right_text: str
    cache_key: str
    allow_mismatches: bool


def _preset_cases() -> list[PresetCase]:
    cases: list[PresetCase] = []
    for preset_dir in sorted(PRESETS_ROOT.glob("*/*")):
        if not preset_dir.is_dir():
            continue
        old_paths = sorted(preset_dir.glob("old.*"))
        new_paths = sorted(preset_dir.glob("new.*"))
        assert len(old_paths) == 1, preset_dir
        assert len(new_paths) == 1, preset_dir
        cases.append(
            {
                "old_path": old_paths[0],
                "new_path": new_paths[0],
                "cache_key": f"presets/{preset_dir.relative_to(PRESETS_ROOT).as_posix()}",
                "allow_mismatches": False,
            }
        )
    return cases


def _current_diff_cases() -> list[WorktreeCase]:
    head_id = GitRepository(REPO_ROOT).commit_id("HEAD", allow_unborn=True)
    source = GitChangeSource(
        REPO_ROOT,
        left=head_id,
        right="worktree",
        show_untracked=True,
        max_link_target_bytes=1024 * 1024,
    )
    cases: list[WorktreeCase] = []
    for entry in source.changes().files:
        # Absent sides replay against empty content. A side whose observation
        # or acquisition failed drops out of this changing corpus.
        loaded = source.load((entry,))[0]
        left_loaded, right_loaded = loaded.left, loaded.right
        left_content = left_loaded.map(lambda side: side.content).get_or(
            Ok(b"")
        )
        right_content = right_loaded.map(lambda side: side.content).get_or(
            Ok(b"")
        )
        if not Ok.good(left_content):
            continue
        if not Ok.good(right_content):
            continue

        left_text = try_decode_text(left_content.value)
        right_text = try_decode_text(right_content.value)
        # Content this project does not call text has no engine rows to
        # replay, exactly as composition classifies it away from a text bay.
        if not Ok.good(left_text) or not Ok.good(right_text):
            continue
        # A pure rename compares a file against its own content. Difftastic
        # rightly reports it unchanged and the engine returns zero rows, but
        # the replay invariants assert that rows cover every source
        # character, so an identical pair can only fail vacuously. Skip it:
        # there is no diff to check.
        if left_text == right_text:
            continue
        left_name = entry.left.map(lambda side: side.key.path).get_or(
            entry.display_name
        )
        right_name = entry.right.map(lambda side: side.key.path).get_or(
            entry.display_name
        )
        cases.append(
            {
                "display_name": entry.display_name,
                "left_name": left_name,
                "right_name": right_name,
                "left_text": left_text.value,
                "right_text": right_text.value,
                "cache_key": f"worktree/{sha256(entry.display_name.encode()).hexdigest()}",
                "allow_mismatches": True,
            }
        )
    return cases


def _difftastic_rows_for_preset(
    case: PresetCase,
) -> tuple[list[DiffEngineRow], str, str]:
    old_path, new_path = case["old_path"], case["new_path"]
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        case["cache_key"],
        left=old_path,
        right=new_path,
        allow_mismatches=case["allow_mismatches"],
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )
    return rows, old_text, new_text


def _text_without_difftastic_ignored_trailing_commas(
    source_path: Path, source_text: str
) -> str:
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


@pytest.mark.parametrize(
    "case", _preset_cases(), ids=lambda case: case["cache_key"]
)
def test_difftastic_preset_tokens_stay_in_source_order(
    case: PresetCase,
) -> None:

    def _side_rendered_text(
        rows: list[DiffEngineRow],
        *,
        side: Side,
    ) -> str:
        if side == "left":
            return "\n".join(
                row["left_text"] for row in rows if row["left_no"] is not None
            )
        return "\n".join(
            row["right_text"] for row in rows if row["right_no"] is not None
        )

    def _token_atoms(text: str) -> list[str]:
        return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|\S", text)

    rows, old_text, new_text = _difftastic_rows_for_preset(case)
    # Difftastic can report no structured rows for changed files; the service
    # layer handles that by falling back to unified-diff rows, so parser-level
    # row/token invariants have nothing to check here.
    if rows == []:
        return

    sides: tuple[tuple[Side, str], ...] = (
        ("left", old_text),
        ("right", new_text),
    )
    for side, source_text in sides:
        for row in rows:
            if side == "left":
                tokens = row.get("left_tokens")
                text = row["left_text"]
            else:
                tokens = row.get("right_tokens")
                text = row["right_text"]
            if tokens is None:
                continue
            if tokens == []:
                continue
            pieces = [token["text"] for token in tokens]
            assert "".join(pieces) == text

        source_atoms = _token_atoms(source_text)
        rendered_atoms = _token_atoms(_side_rendered_text(rows, side=side))
        assert rendered_atoms == source_atoms


@pytest.mark.parametrize(
    "case", _preset_cases(), ids=lambda case: case["cache_key"]
)
def test_difftastic_preset_token_spans_match_difftastic_json(
    case: PresetCase,
) -> None:
    old_path, new_path = case["old_path"], case["new_path"]
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        case["cache_key"],
        left=old_path,
        right=new_path,
        allow_mismatches=case["allow_mismatches"],
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )
    source_lines = {
        "left": old_text.splitlines(),
        "right": new_text.splitlines(),
    }

    expected: dict[Side, dict[int, set[int]]] = {"left": {}, "right": {}}
    for chunk in diff_json.get("chunks", []):
        for entry in chunk:
            for side in ("left", "right"):
                side_data = (
                    entry.get("lhs") if side == "left" else entry.get("rhs")
                )
                if side_data is None:
                    continue
                line_number = side_data["line_number"]
                changes = side_data["changes"]
                line_spans = expected[side].setdefault(line_number, set())
                for change in changes:
                    start = change["start"]
                    end = change["end"]
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
    for row_index, row in enumerate(rows):
        for side in sides:
            if side == "left":
                line_no = row["left_no"]
                text = row["left_text"]
                tokens = row.get("left_tokens")
            else:
                line_no = row["right_no"]
                text = row["right_text"]
                tokens = row.get("right_tokens")
            if line_no is None:
                continue
            if tokens is None:
                continue
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
                token_text = token["text"]
                status = token["status"]
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


@pytest.mark.parametrize(
    "case", _preset_cases(), ids=lambda case: case["cache_key"]
)
def test_difftastic_preset_line_status_matches_token_statuses(
    case: PresetCase,
) -> None:
    rows, _, _ = _difftastic_rows_for_preset(case)

    diagnostics: list[tuple[int, str, str, list[str]]] = []
    for row_index, row in enumerate(rows, start=1):
        has_token_data = False
        token_statuses: list[str] = []
        for side in ("left", "right"):
            tokens = (
                row.get("left_tokens")
                if side == "left"
                else row.get("right_tokens")
            )
            if tokens is None:
                continue
            if tokens != []:
                has_token_data = True
            for token in tokens:
                status = token["status"]
                token_statuses.append(status)
        if token_statuses == [] and not has_token_data:
            continue

        if all(status == "delete" for status in token_statuses):
            expected = "delete"
        elif all(status == "insert" for status in token_statuses):
            expected = "insert"
        else:
            expected = "equal"

        actual = row["status"]
        if actual != expected:
            diagnostics.append((row_index, actual, expected, token_statuses))

    assert diagnostics == []


@pytest.mark.parametrize(
    "case", _preset_cases(), ids=lambda case: case["cache_key"]
)
def test_difftastic_preset_line_alignment_matches_difftastic(
    case: PresetCase,
) -> None:
    old_path, new_path = case["old_path"], case["new_path"]
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        case["cache_key"],
        left=old_path,
        right=new_path,
        allow_mismatches=case["allow_mismatches"],
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )
    old_line_count = len(old_text.splitlines())
    new_line_count = len(new_text.splitlines())
    expected: list[tuple[int | None, int | None]] = []
    for pair in diff_json.get("aligned_lines", []):
        assert len(pair) == 2
        left_raw, right_raw = pair
        left_no = None if left_raw is None else left_raw + 1
        right_no = None if right_raw is None else right_raw + 1
        if left_no is not None and left_no > old_line_count:
            continue
        if right_no is not None and right_no > new_line_count:
            continue
        expected.append((left_no, right_no))

    actual = [(row["left_no"], row["right_no"]) for row in rows]
    assert actual == expected


@pytest.mark.parametrize(
    "case", _preset_cases(), ids=lambda case: case["cache_key"]
)
def test_difftastic_preset_diff_replays_both_directions(
    case: PresetCase,
) -> None:
    old_path, new_path = case["old_path"], case["new_path"]

    def replay(
        source_side: Side,
        target_side: Side,
        *,
        source_changed: set[str],
        target_changed: set[str],
        direction: str,
    ) -> None:

        side_parts: dict[Side, list[tuple[str, str]]] = {
            "left": [],
            "right": [],
        }
        for side in sides:
            for row in rows:
                if side == "left":
                    line_no = row["left_no"]
                    row_text = row["left_text"]
                    tokens = row.get("left_tokens", [])
                else:
                    line_no = row["right_no"]
                    row_text = row["right_text"]
                    tokens = row.get("right_tokens", [])
                if line_no is None:
                    continue
                assert isinstance(row_text, str)
                if tokens == []:
                    row_status = row["status"]
                    if row_status == "equal":
                        token_status = "unchanged"
                    elif side == "left" and row["right_no"] is None:
                        assert row_status == "delete"
                        token_status = "delete"
                    elif side == "right" and row["left_no"] is None:
                        assert row_status == "insert"
                        token_status = "insert"
                    else:
                        token_status = "unchanged"
                    for char in row_text:
                        if char.isspace():
                            continue
                        side_parts[side].append((token_status, char))
                    continue
                for token in tokens:
                    token_text = token["text"]
                    listed_token_status = token["status"]
                    for char in token_text:
                        if char.isspace():
                            continue
                        side_parts[side].append((listed_token_status, char))

        source_parts = side_parts[source_side]
        target_parts = side_parts[target_side]
        source_chars = [
            char for char in normalized_texts[source_side] if not char.isspace()
        ]
        target_chars = [
            char for char in normalized_texts[target_side] if not char.isspace()
        ]
        assert [text for _, text in source_parts] == source_chars, direction
        assert [text for _, text in target_parts] == target_chars, direction

        operations: list[tuple[str, str]] = []
        source_cursor = 0
        target_cursor = 0

        while source_cursor < len(source_parts) or target_cursor < len(
            target_parts
        ):
            if source_cursor >= len(source_parts):
                status, text = target_parts[target_cursor]
                assert status in target_changed, direction
                operations.append(("insert", text))
                target_cursor += 1
                continue
            if target_cursor >= len(target_parts):
                status, text = source_parts[source_cursor]
                assert status in source_changed, direction
                operations.append(("remove", text))
                source_cursor += 1
                continue

            source_status, source_text = source_parts[source_cursor]
            target_status, target_text = target_parts[target_cursor]
            if source_status == "unchanged":
                while (
                    target_status != "unchanged" or target_text != source_text
                ):
                    assert target_status in target_changed, direction
                    operations.append(("insert", target_text))
                    target_cursor += 1
                    assert target_cursor < len(target_parts), direction
                    target_status, target_text = target_parts[target_cursor]
                operations.append(("keep", source_text))
                source_cursor += 1
                target_cursor += 1
                continue

            assert source_status in source_changed, direction
            operations.append(("remove", source_text))
            source_cursor += 1

        replayed: list[str] = []
        source_cursor = 0

        for operation, text in operations:
            if operation == "insert":
                replayed.append(text)
                continue
            assert source_cursor < len(source_parts), direction
            assert source_parts[source_cursor][1] == text, direction
            if operation == "keep":
                replayed.append(text)
            source_cursor += 1

        assert replayed == target_chars, direction

    source_texts = {
        "left": old_path.read_bytes().decode("utf-8"),
        "right": new_path.read_bytes().decode("utf-8"),
    }
    source_paths = {"left": old_path, "right": new_path}
    normalized_texts: dict[Side, str] = {"left": "", "right": ""}
    sides: tuple[Side, Side] = ("left", "right")

    # Stage 0: remove the trailing commas that difftastic intentionally
    # ignores before it emits JSON change positions.
    #
    # That makes test a bit lower in fidelity, but well, what can you do
    for side in sides:
        normalized_texts[side] = (
            _text_without_difftastic_ignored_trailing_commas(
                source_paths[side],
                source_texts[side],
            )
        )

    diff_json = tool_json(
        "difftastic",
        f"{case['cache_key']}/normalized",
        left=ToolText(normalized_texts["left"], old_path.name),
        right=ToolText(normalized_texts["right"], new_path.name),
        allow_mismatches=case["allow_mismatches"],
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=normalized_texts["left"],
        right_text=normalized_texts["right"],
    )
    # Difftastic can report no structural rows for changed files. The retained
    # renderer test covers its textual substitution; there is no structural
    # token stream for this parser-level replay invariant to inspect.
    if rows == []:
        return

    # Replay them in both directions
    replay(
        "left",
        "right",
        source_changed={"delete", "replace"},
        target_changed={"insert", "replace"},
        direction="left-to-right",
    )
    replay(
        "right",
        "left",
        source_changed={"insert", "replace"},
        target_changed={"delete", "replace"},
        direction="right-to-left",
    )


@pytest.mark.parametrize(
    "case", _current_diff_cases(), ids=lambda case: case["display_name"]
)
def test_difftastic_current_diff_matches_preset_invariants(
    case: WorktreeCase,
    tmp_path: Path,
) -> None:
    display_name = case["display_name"]
    left_name, right_name = case["left_name"], case["right_name"]
    left_text, right_text = case["left_text"], case["right_text"]
    suffix = Path(right_name).suffix or Path(left_name).suffix or ".txt"
    current_case = tmp_path / display_name.replace("/", "__")
    current_case.mkdir()
    old_path = current_case / f"old{suffix}"
    new_path = current_case / f"new{suffix}"
    old_path.write_text(left_text)
    new_path.write_text(right_text)

    preset_case: PresetCase = {
        "old_path": old_path,
        "new_path": new_path,
        "cache_key": case["cache_key"],
        "allow_mismatches": case["allow_mismatches"],
    }
    test_difftastic_preset_tokens_stay_in_source_order(preset_case)
    test_difftastic_preset_token_spans_match_difftastic_json(preset_case)
    test_difftastic_preset_line_status_matches_token_statuses(preset_case)
    test_difftastic_preset_line_alignment_matches_difftastic(preset_case)
    test_difftastic_preset_diff_replays_both_directions(preset_case)
