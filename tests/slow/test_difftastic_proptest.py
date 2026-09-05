"""Property checks for Difftastic rows over the complete preset corpus.

The tests in this module run every difftastic preset, including `borked`
fixtures, through the same row builder and assert broad invariants: source
text is preserved, one-sided rows are not pure unchanged context, and
replacement tokens stay paired sensibly.  Golden snapshots cover exact output
for non-borked presets; this file guards shape and token consistency across
the full preset corpus.
"""

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

from dirdiff.backend import GitBackend
from dirdiff.engines import DiffEngineRow, DirdiffError
from dirdiff.engines.difftastic.logic import difftastic_rows_from_json
from dirdiff.formats import TextRejection, try_decode_text

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"
"""Complete Difftastic source-pair corpus, including intentionally hard cases.

Unlike golden tests, these invariants include the `borked` group because exact
appearance is irrelevant to source preservation and token consistency.
"""
REPO_ROOT = Path(__file__).resolve().parents[2]
"""Current checkout used to replay the worktree patch through preset invariants.

The dynamic corpus is additional coverage for code under active development;
it does not replace stable checked-in fixtures.
"""
Side = Literal["left", "right"]
"""Difftastic source side inspected by replay and span-order assertions.

The value selects coordinated row and token fields only. It does not describe
which side changed or whether a row is paired.
"""


class PresetCase(TypedDict):
    """Required source paths and explicit cache identity for preset invariants.

    Stable fixtures forbid changed inputs. The worktree test supplies its
    materialized pair to the same checks with explicit replacement permission.
    """

    old_path: Path
    """Old source file, read exactly with its basename as parser hint."""
    new_path: Path
    """New source file under the same exact-text contract."""
    cache_key: str
    """Stable identity for this input pair, independent of temporary paths."""
    allow_mismatches: bool
    """Whether changed inputs may replace an existing valid cache entry."""


class WorktreeCase(TypedDict):
    """Captured worktree source pair and its required changing-input policy.

    Git capture supplies exact source and path facts. Repeated runs may replace
    stale inputs for the same changed File, without hiding malformed entries.
    """

    display_name: str
    """Backend File name used as the readable pytest case identity."""
    left_name: str
    """Old parser hint, using the display name when that side is absent."""
    right_name: str
    """New parser hint, using the display name when that side is absent."""
    left_text: str
    """Exact decoded old content; an absent side contributes empty text."""
    right_text: str
    """Exact decoded new content; an absent side contributes empty text."""
    cache_key: str
    """Stable identity for this input pair, independent of temporary paths."""
    allow_mismatches: bool
    """Whether changed inputs may replace an existing valid cache entry."""


def _preset_cases() -> list[PresetCase]:
    """Return every unambiguous preset pair with a stable shared cache key.

    Both paths must exist exactly once. Stable fixtures forbid hash mismatches;
    their key is shared with golden and focused checks of the same source pair.

    # Usage

    Use as the parameter list for properties that need every checked-in preset
    pair, including fixtures Difftastic cannot compare structurally.

    # Failures

    Raises `AssertionError` when a preset contains an ambiguous number of old or
    new files.
    """
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
    """Build replay cases from changed text Files in the current worktree.

    The helper compares HEAD with worktree including untracked Files, loads an
    absent side as empty bytes, and skips capture failures, non-text content,
    and equal pairs such as pure renames. Each result contains display name,
    left and right parser names, then decoded left and right text, in backend
    manifest order. An empty current diff returns an empty parameter list.

    # Usage

    Use as an additional property-test parameter source when local changed Files
    should be checked without replacing the stable preset corpus.

    # Returns

    - `Cases`: Required source texts, parser names, display name and cache policy.
    - `Selection and order`: Cases retain backend manifest order; equal,
      non-text, capture-failed, and unreadable pairs are absent.
    """
    backend = GitBackend.discover(cwd=REPO_ROOT)
    cases: list[WorktreeCase] = []
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
        # Content this project does not call text has no engine rows to
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
            {
                "display_name": entry.display_name,
                "left_name": left_name,
                "right_name": right_name,
                "left_text": left_text,
                "right_text": right_text,
                "cache_key": f"worktree/{sha256(entry.display_name.encode()).hexdigest()}",
                "allow_mismatches": True,
            }
        )
    return cases


def _difftastic_rows_for_preset(
    case: PresetCase,
) -> tuple[list[DiffEngineRow], str, str]:
    """Build one fixture through raw Difftastic without textual degradation.

    Empty or degraded structural output remains visible to the property test;
    this helper deliberately does not substitute unified text rows.

    # Parameters

    - `case`: Required source paths, cache key, and mismatch policy.

    # Usage

    Property tests call this when they need to distinguish Difftastic's raw
    structural result from the engine's textual degraded result.

    # Returns

    - `First`: Raw Difftastic rows before the engine substitutes a textual
      degraded result.
    - `Second`: Exact old fixture text supplied to Difftastic.
    - `Third`: Exact new fixture text supplied to Difftastic.
    """
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
    """Remove punctuation that difftastic deliberately omits from JSON.

    Difftastic marks language-specific trailing commas as ignorable before
    emitting change positions.  The replay invariant compares concrete text,
    so it must normalize the same punctuation away before it asks whether the
    remaining status stream can reconstruct the target.

    Sad but true.

    # Parameters

    - `source_path`: Parser-selecting fixture path for language-specific rules.
    - `source_text`: Exact text from which ignored comma bytes are removed.

    # Usage

    Replay checks apply this to the exact source before comparing it with
    Difftastic token text for languages whose parser omits trailing commas.

    # Failures

    Raises `AssertionError` when a Difftastic-reported byte position does not
    address the comma expected in the supplied source.
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


@pytest.mark.parametrize(
    "case", _preset_cases(), ids=lambda case: case["cache_key"]
)
def test_difftastic_preset_tokens_stay_in_source_order(
    case: PresetCase,
) -> None:
    """This test verifies that for both old source and new source, the output
    on the left and on the right has all tokens in full, and in the same order
    as they were in the original sources.

    # Parameters

    - `case`: Required source paths, cache key, and mismatch policy.
    """

    def _side_rendered_text(
        rows: list[DiffEngineRow],
        *,
        side: Side,
    ) -> str:
        """Join source-side row text in rendered order.

        # Parameters

        - `rows`: Complete projected row sequence.
        - `side`: Source side whose present rows are joined.

        # Usage

        The enclosing property test calls this for each side before comparing
        the rendered line sequence with the original source.
        """
        if side == "left":
            return "\n".join(
                row["left_text"] for row in rows if row["left_no"] is not None
            )
        return "\n".join(
            row["right_text"] for row in rows if row["right_no"] is not None
        )

    def _token_atoms(text: str) -> list[str]:
        """Reduce exact token text to atoms safe for subsequence comparison.

        Whitespace is deliberately absent because Difftastic may partition it
        differently. Identifier, numeric, and punctuation order must still be a
        subsequence of the corresponding original source.
        """
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
    """Match every projected changed character to Difftastic's byte ranges.

    The original source converts external byte columns to character offsets;
    diagnostics identify any missing or extra changed character by line.

    # Parameters

    - `case`: Required source paths, cache key, and mismatch policy.
    """
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
    """Derive each projected row status from its actual token classifications.

    # Parameters

    - `case`: Required source paths, cache key, and mismatch policy.
    """
    rows, _, _ = _difftastic_rows_for_preset(case)

    diagnostics: list[tuple[int, str, str, list[str]]] = []
    for row_index, row in enumerate(rows, start=1):
        has_token_data = False
        token_statuses: list[str] = []
        changed_tokens_are_ws = True
        has_unchanged_text = False
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
                if status == "unchanged" and not token["is_ws"]:
                    has_unchanged_text = True
                if status != "unchanged" and not token["is_ws"]:
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
    """Preserve Difftastic's complete line alignment after phantom removal.

    # Parameters

    - `case`: Required source paths, cache key, and mismatch policy.
    """
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
    """Token statuses must replay either source file into the other.

    # Parameters

    - `case`: Required source paths, cache key, and mismatch policy.
    """
    old_path, new_path = case["old_path"], case["new_path"]

    def replay(
        source_side: Side,
        target_side: Side,
        *,
        source_changed: set[str],
        target_changed: set[str],
        direction: str,
    ) -> None:
        """Run the complete replay invariant in one direction.

        # Parameters

        - `source_side`: Side providing shared token text.
        - `target_side`: Side reconstructed by the edit script.
        - `source_changed`: Statuses discarded from the source during replay.
        - `target_changed`: Statuses copied from the target during replay.
        - `direction`: Human-readable direction included in failures.

        # Usage

        The bidirectional property calls this once per direction with the
        corresponding changed-status vocabulary.

        # Failures

        Raises `AssertionError` when token status cannot replay the target or
        when shared text disagrees between sides.
        """

        # Stage 1: collect difftastic statuses as non-whitespace characters.
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

        # Stage 2: walk source and target status streams into edit operations.
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

        # Stage 3: apply collected operations to source.
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
    """Run every preset invariant against each readable current File change.

    The test materializes temporary old/new files so existing helpers exercise
    their real path-based parser selection without altering the checkout.

    # Parameters

    - `case`: Display name, side names, and decoded source texts from Git.
    - `tmp_path`: Pytest directory used for the materialized source pair.
    """
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
