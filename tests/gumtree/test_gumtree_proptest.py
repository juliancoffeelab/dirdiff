"""Exercise GumTree's source, alignment, and move contracts adversarially.

Real presets cover executable output across languages. Generated single-line
moves check Unicode conversion, nested edits, and syntax splitting against
source coordinates independently of row building. Preset properties compare
rendered characters and move endpoints with raw executable facts.
"""

import re
from pathlib import Path

import pytest
from helpers import tool_json
from hypothesis import given
from hypothesis import strategies as st

from dirdiff.engines import (
    GumTreeJson,
    build_gumtree_rows_from_json,
)
from dirdiff.rendering import highlight_lines_for_path, weave_decorated_parts

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "gumtree"
"""Readable source pairs shared by snapshots, invariant tests, and the HUD."""


@pytest.mark.parametrize("preset", sorted(PRESETS_ROOT.glob("*/*")), ids=str)
def test_sources_survive_alignment_and_decoration(preset: Path) -> None:
    """Every source line occurs once in order and every decorated character survives.

    Real language syntax splitting must retain each character's diff status
    and move endpoints, including on updates.
    """
    left = next(preset.glob("old.*"))
    right = next(preset.glob("new.*"))
    left_text = left.read_bytes().decode()
    right_text = right.read_bytes().decode()
    rows = build_gumtree_rows_from_json(
        diff_json=tool_json(
            "gumtree",
            f"presets/{preset.relative_to(PRESETS_ROOT).as_posix()}",
            left=left,
            right=right,
        ),
        left_text=left_text,
        right_text=right_text,
        left_path_hint=left.name,
        right_path_hint=right.name,
    )
    for side, text, path in [
        ("left", left_text, left),
        ("right", right_text, right),
    ]:
        syntax = highlight_lines_for_path(path.name, text)
        numbers = [
            row["left_no"] if side == "left" else row["right_no"]
            for row in rows
        ]
        assert [number for number in numbers if number is not None] == list(
            range(1, len(text.splitlines()) + 1)
        )
        rendered = []
        for row, number in zip(rows, numbers, strict=True):
            content = row["left_text"] if side == "left" else row["right_text"]
            tokens = (
                row["left_tokens"] if side == "left" else row["right_tokens"]
            )
            assert "".join(token["text"] for token in tokens) == content
            assert all(
                token["text"] and token["is_ws"] == token["text"].isspace()
                for token in tokens
            )
            parts = weave_decorated_parts(
                content,
                tokens,
                [] if number is None or syntax is None else syntax[number - 1],
            )
            assert "".join(part["text"] for part in parts) == content
            assert [
                (part["diff_status"], part.get("move"))
                for part in parts
                for _character in part["text"]
            ] == [
                (token["status"], token.get("move"))
                for token in tokens
                for _character in token["text"]
            ]
            if number is None:
                assert content == "" and tokens == []
            else:
                rendered.append(content)
        assert rendered == text.splitlines()


@given(
    context=st.lists(
        st.text(alphabet="abc_012 ", max_size=15),
        min_size=0,
        max_size=25,
    ),
    position=st.integers(min_value=0, max_value=25),
    count=st.integers(min_value=1, max_value=5),
    delete=st.booleans(),
)
def test_one_sided_edits_do_not_displace_unchanged_lines(
    context: list[str], position: int, count: int, delete: bool
) -> None:
    """One-sided blocks leave surrounding unchanged lines paired, including repeats.

    # Parameters

    - `context`: Generated source lines, allowing repeats, blanks, and an empty file.
    - `position`: Insertion position reduced to the source line bounds.
    - `count`: Number of lines in the one-sided structural action.
    - `delete`: Whether to reverse the source pair and delete the block.
    """
    position %= len(context) + 1
    left = "".join(line + "\n" for line in context)
    added = "added\n" * count
    offset = sum(len(line) + 1 for line in context[:position])
    right = left[:offset] + added + left[offset:]
    facts: GumTreeJson = {
        "actions": [
            {
                "action": "delete-tree" if delete else "insert-tree",
                "tree": f"block [{offset},{offset + len(added) - 1}]",
            }
        ]
    }
    rows = build_gumtree_rows_from_json(
        diff_json=facts,
        left_text=right if delete else left,
        right_text=left if delete else right,
        left_path_hint="old.py",
        right_path_hint="new.py",
    )
    pairs = [
        (row["right_no"], row["left_no"])
        if delete
        else (row["left_no"], row["right_no"])
        for row in rows
        if (row["right_no"] if delete else row["left_no"]) is not None
    ]
    assert pairs == [
        (index + 1, index + 1 + (count if index >= position else 0))
        for index in range(len(context))
    ]


@given(
    prefix=st.text(alphabet="abc ї😀", max_size=20),
    reverse=st.booleans(),
    suffix=st.text(alphabet="xyzé🦊", max_size=20),
)
def test_edited_moves_keep_exact_unicode_endpoints(
    prefix: str, suffix: str, reverse: bool
) -> None:
    """Nested updates retain the surrounding move after Unicode and syntax splitting.

    Synthetic single-line actions isolate the adapter contract from matcher
    heuristics: exactly one character changes inside one containing move.

    # Parameters

    - `prefix`: Unicode source preceding the changed character.
    - `suffix`: Unicode source following it; may make the update cover the move.
    - `reverse`: Whether to reverse action order to expose precedence mistakes.
    """
    left, right = prefix + "1" + suffix, prefix + "2" + suffix
    byte_start = len(prefix.encode())
    byte_end = len(left.encode())
    tree = f"block [0,{byte_end}]"
    changed = f"integer [{byte_start},{byte_start + 1}]"
    # tool-json: synthetic a move to the same coordinate isolates nested action precedence
    facts: GumTreeJson = {
        "matches": [
            {"src": tree, "dest": tree},
            {"src": changed, "dest": changed},
        ],
        "actions": [
            {"action": "move-tree", "tree": tree},
            {"action": "update-node", "tree": changed},
        ],
    }
    if reverse:
        facts["actions"].reverse()
    rows = build_gumtree_rows_from_json(
        diff_json=facts,
        left_text=left,
        right_text=right,
        left_path_hint="old.py",
        right_path_hint="new.py",
    )
    assert len(rows) == 1
    expected_span = {
        "start_line": 1,
        "start_column": 0,
        "end_line": 1,
        "end_column": len(left),
    }
    for tokens, text in [
        (rows[0]["left_tokens"], left),
        (rows[0]["right_tokens"], right),
    ]:
        assert (
            "".join(
                token["text"]
                for token in tokens
                if token["status"] == "replace"
            )
            == text[len(prefix)]
        )
        assert all(
            token["move"]
            == {
                "kind": "paired",
                "left": expected_span,
                "right": expected_span,
            }
            for token in tokens
        )
        parts = weave_decorated_parts(
            text,
            tokens,
            [
                {
                    "start": len(prefix),
                    "end": len(prefix) + 1,
                    "classes": ["ts-number"],
                }
            ],
        )
        assert "".join(part["text"] for part in parts) == text
        assert all(
            part["move"]
            == {
                "kind": "paired",
                "left": expected_span,
                "right": expected_span,
            }
            for part in parts
        )


@pytest.mark.parametrize("preset", sorted(PRESETS_ROOT.glob("*/*")), ids=str)
def test_preset_actions_and_moves_match_gumtree_facts(preset: Path) -> None:
    """Every rendered character must agree with real action and destination facts.

    The oracle encodes source positions into GumTree coordinates, the reverse
    of the adapter's conversion. It checks action coverage without using the
    adapter's range parser or sweep, including nested and edited moves. When
    both sides retain the same unchanged-line sequence, every line must pair
    with its counterpart despite intervening changes or moves.
    """
    left, right = next(preset.glob("old.*")), next(preset.glob("new.*"))
    texts = [left.read_bytes().decode(), right.read_bytes().decode()]
    facts = tool_json(
        "gumtree",
        f"presets/{preset.relative_to(PRESETS_ROOT).as_posix()}",
        left=left,
        right=right,
    )
    rows = build_gumtree_rows_from_json(
        diff_json=facts,
        left_text=texts[0],
        right_text=texts[1],
        left_path_hint=left.name,
        right_path_hint=right.name,
    )

    # Enumerate forward coordinates from source, independently of displayed
    # tokens. Each entry includes the exclusive end-of-content boundary.
    boundaries: list[list[list[int]]] = []
    for path, text in zip((left, right), texts, strict=True):
        source_lines = text.splitlines(keepends=True)
        side_boundaries: list[list[int]] = []
        for index, line in enumerate(text.splitlines()):
            prefix = "".join(source_lines[:index])
            if path.suffix in {".yaml", ".yml"}:
                positions = [
                    len(prefix) + column for column in range(len(line) + 1)
                ]
            elif path.suffix == ".java":
                positions = [
                    len((prefix + line[:column]).encode("utf-16-le")) // 2
                    for column in range(len(line) + 1)
                ]
            else:
                # These corpus languages use tree-sitter-ng. Adding another
                # generator requires stating its coordinate convention here.
                assert path.suffix in {".py", ".rs", ".ts", ".tsx", ".ml"}, path
                prefix = prefix.replace("\r\n", "\n").replace("\r", "\n")
                base = len(prefix.encode("utf-16-le")) // 2
                positions = [
                    base + len(line[:column].encode("utf-8"))
                    for column in range(len(line) + 1)
                ]
            side_boundaries.append(positions)
        boundaries.append(side_boundaries)

    destinations = {
        match["src"]: match["dest"] for match in facts.get("matches", [])
    }
    # Side-specific raw intervals retain action order and the complete paired
    # move identity. This oracle deliberately scans them per character.
    actions: list[
        list[tuple[int, int, int, str, tuple[int, int, int, int] | None]]
    ] = [[], []]
    for ordinal, action in enumerate(facts.get("actions", [])):
        operation = action["action"].split("-")[0]
        assert operation in {"insert", "delete", "update", "move"}, action
        status = "replace" if operation == "update" else operation
        tree = action["tree"]
        trees = (
            [None, tree]
            if operation == "insert"
            else [tree, None]
            if operation == "delete"
            else [tree, destinations[tree]]
        )
        ranges: list[tuple[int, int] | None] = []
        for description in trees:
            if description is None:
                ranges.append(None)
            else:
                match = re.search(r"\[(\d+),(\d+)\]\s*$", description)
                assert match is not None, description
                ranges.append((int(match[1]), int(match[2])))
        move = None
        if operation == "move":
            old_range, new_range = ranges
            assert old_range is not None and new_range is not None
            move = (*old_range, *new_range)
        for side, source_range in enumerate(ranges):
            if source_range is not None:
                actions[side].append((*source_range, ordinal, status, move))

    unchanged: list[list[tuple[int, str]]] = [[], []]
    for row in rows:
        for side, (number, content, tokens) in enumerate(
            [
                (row["left_no"], row["left_text"], row["left_tokens"]),
                (row["right_no"], row["right_text"], row["right_tokens"]),
            ]
        ):
            if number is None:
                continue
            positions = boundaries[side][number - 1]
            cursor = 0
            line_unchanged = True
            for token in tokens:
                actual_move = None
                if "move" in token:
                    endpoints: list[int] = []
                    for endpoint_side, span in enumerate(
                        (token["move"]["left"], token["move"]["right"])
                    ):
                        for line_number, column in [
                            (span["start_line"], span["start_column"]),
                            (span["end_line"], span["end_column"]),
                        ]:
                            assert (
                                1
                                <= line_number
                                <= len(boundaries[endpoint_side])
                            ), (preset, span)
                            assert (
                                0
                                <= column
                                < len(
                                    boundaries[endpoint_side][line_number - 1]
                                )
                            ), (preset, span)
                            endpoints.append(
                                boundaries[endpoint_side][line_number - 1][
                                    column
                                ]
                            )
                    actual_move = tuple(endpoints)
                for column in range(cursor, cursor + len(token["text"])):
                    covering = [
                        entry
                        for entry in actions[side]
                        if entry[0] <= positions[column]
                        and positions[column + 1] <= entry[1]
                    ]
                    expected_status = "unchanged"
                    if covering != []:
                        expected_status = min(
                            covering,
                            key=lambda entry: (
                                entry[1] - entry[0],
                                entry[3] == "move",
                                entry[2],
                            ),
                        )[3]
                    containing_moves = [
                        entry for entry in covering if entry[4] is not None
                    ]
                    expected_move = (
                        None
                        if containing_moves == []
                        else min(
                            containing_moves,
                            key=lambda entry: (entry[1] - entry[0], entry[2]),
                        )[4]
                    )
                    diagnostic = (
                        preset,
                        "left" if side == 0 else "right",
                        number,
                        column,
                        content[column],
                    )
                    assert token["status"] == expected_status, diagnostic
                    assert actual_move == expected_move, diagnostic
                    line_unchanged = (
                        line_unchanged and expected_status == "unchanged"
                    )
                cursor += len(token["text"])
            assert cursor == len(content), (preset, side, number)
            if line_unchanged:
                unchanged[side].append((number, content))

    if [text for _, text in unchanged[0]] == [text for _, text in unchanged[1]]:
        pairs = {(row["left_no"], row["right_no"]) for row in rows}
        for (old_number, _), (new_number, _) in zip(*unchanged, strict=True):
            assert (old_number, new_number) in pairs, (
                preset,
                old_number,
                new_number,
            )


@pytest.mark.parametrize("preset", sorted(PRESETS_ROOT.glob("*/*")), ids=str)
def test_preset_row_status_matches_token_semantics(preset: Path) -> None:
    """Row classification must preserve semantic changes regardless of alignment gaps.

    As in Difftastic's corpus invariant, context-only and whitespace-only rows
    are equal; insertion or deletion requires no unchanged non-whitespace text.
    GumTree additionally permits a pure move row. Mixed changes or changes
    sharing a row with context are replacements. Exact token classifications
    are checked independently against raw GumTree actions by the span property.
    """
    left, right = next(preset.glob("old.*")), next(preset.glob("new.*"))
    rows = build_gumtree_rows_from_json(
        diff_json=tool_json(
            "gumtree",
            f"presets/{preset.relative_to(PRESETS_ROOT).as_posix()}",
            left=left,
            right=right,
        ),
        left_text=left.read_bytes().decode(),
        right_text=right.read_bytes().decode(),
        left_path_hint=left.name,
        right_path_hint=right.name,
    )
    mismatches = []
    for row in rows:
        tokens = row["left_tokens"] + row["right_tokens"]
        changes = {
            token["status"]
            for token in tokens
            if token["status"] != "unchanged"
        }
        has_changed_text = any(
            token["status"] != "unchanged" and not token["is_ws"]
            for token in tokens
        )
        has_context = any(
            token["status"] == "unchanged" and not token["is_ws"]
            for token in tokens
        )
        if not has_changed_text:
            expected = "equal"
        elif not has_context and changes in ({"insert"}, {"delete"}, {"move"}):
            expected = next(iter(changes))
        else:
            expected = "replace"
        if row["status"] != expected:
            mismatches.append(
                (row["left_no"], row["right_no"], row["status"], expected)
            )
    assert mismatches == [], (
        f"{preset}: (old line, new line, actual, expected): {mismatches}"
    )
