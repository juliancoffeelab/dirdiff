from pathlib import Path

import pytest
from helpers import ToolText, tool_json

from dirdiff.engines import (
    DiffSide,
    DirdiffError,
    GumTreeDiffEngine,
    GumTreeJson,
    build_gumtree_rows_from_json,
)

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "gumtree"


@pytest.mark.parametrize(
    "facts",
    [
        {"actions": [{"action": "move-tree", "tree": "node [0,1]"}]},
        {"actions": [{"action": "delete-tree", "tree": "node [2,1]"}]},
        {"actions": [{"action": "insert-tree", "tree": "node [0,99]"}]},
        {"actions": [{"action": "future-action", "tree": "node [0,1]"}]},
        {
            "matches": [
                {"src": "a [0,1]", "dest": "b [0,1]"},
                {"src": "a [0,1]", "dest": "c [1,2]"},
            ]
        },
        {
            "matches": [
                {"src": "a [0,1]", "dest": "b [0,1]"},
                {"src": "c [1,2]", "dest": "b [0,1]"},
            ]
        },
    ],
)
def test_invalid_engine_facts_are_rejected(facts: GumTreeJson) -> None:
    with pytest.raises(AssertionError):
        build_gumtree_rows_from_json(
            diff_json=facts,
            left_text="ab",
            right_text="cd",
            left_path_hint="old.py",
            right_path_hint="new.py",
        )


def test_unsupported_language_uses_unified_diff_with_notice() -> None:
    result = GumTreeDiffEngine(matcher="gumtree-simple").render_diff(
        old=DiffSide(
            exists=True,
            text="old\ncontext\n",
            path_hint="old.unsupported-dirdiff",
        ),
        new=DiffSide(
            exists=True,
            text="new\ncontext\n",
            path_hint="new.unsupported-dirdiff",
        ),
    )
    assert result["engine_warning"]["type"] == "gumtree_unsupported_language"
    assert "unified text diff" in result["engine_warning"]["message"]
    assert [
        (row["left_text"], row["right_text"]) for row in result["rows"]
    ] == [("old", ""), ("", "new"), ("context", "context")]


@pytest.mark.parametrize(
    "language,old_value,new_value",
    [("python", "3", "4"), ("java", "1", "2"), ("yaml", "1", "2")],
)
def test_unicode_offsets_address_the_changed_literal(
    language: str, old_value: str, new_value: str
) -> None:
    preset = PRESETS_ROOT / language / "unicode-before-edits"
    left, right = next(preset.glob("old.*")), next(preset.glob("new.*"))
    rows = build_gumtree_rows_from_json(
        diff_json=tool_json(
            "gumtree",
            f"presets/{language}/unicode-before-edits",
            left=left,
            right=right,
        ),
        left_text=left.read_bytes().decode(),
        right_text=right.read_bytes().decode(),
        left_path_hint=left.name,
        right_path_hint=right.name,
    )
    assert (
        "".join(
            token["text"]
            for row in rows
            for token in row["left_tokens"]
            if token["status"] == "replace"
        )
        == old_value
    )
    assert (
        "".join(
            token["text"]
            for row in rows
            for token in row["right_tokens"]
            if token["status"] == "replace"
        )
        == new_value
    )


def test_ambiguous_unicode_offsets_are_not_guessed() -> None:
    left = 'label = "ї😀"; value = 1\nnext_value = 3\n'
    right = 'label = "ї😀"; value = 2\nnext_value = 4\n'
    facts = tool_json(
        "gumtree",
        "focused/ambiguous-unicode-offsets",
        left=ToolText(left, "old.py"),
        right=ToolText(right, "new.py"),
    )
    with pytest.raises(DirdiffError, match="ambiguous Unicode offsets"):
        build_gumtree_rows_from_json(
            diff_json=facts,
            left_text=left,
            right_text=right,
            left_path_hint="old.py",
            right_path_hint="new.py",
        )

    result = GumTreeDiffEngine(matcher="gumtree-simple").render_diff(
        old=DiffSide(exists=True, text=left, path_hint="old.py"),
        new=DiffSide(exists=True, text=right, path_hint="new.py"),
    )
    assert result["engine_warning"]["type"] == "gumtree_ambiguous_offsets"
    assert "ambiguous Unicode offsets" in result["engine_warning"]["message"]
    assert "Showing unified diff instead" in result["engine_warning"]["message"]
    assert [
        (
            row["status"],
            row["left_no"],
            row["right_no"],
            row["left_text"],
            row["right_text"],
        )
        for row in result["rows"]
    ] == [
        ("delete", 1, None, left.splitlines()[0], ""),
        ("delete", 2, None, left.splitlines()[1], ""),
        ("insert", None, 1, "", right.splitlines()[0]),
        ("insert", None, 2, "", right.splitlines()[1]),
    ]
    assert result["summary"]["changed_lines"] == 4


@pytest.mark.parametrize("deleted", [False, True])
def test_missing_side_produces_complete_textual_rows(deleted: bool) -> None:
    present = DiffSide(exists=True, text="first\n\nlast\n", path_hint=None)
    absent = DiffSide(exists=False, text=None, path_hint=None)
    result = GumTreeDiffEngine(matcher="gumtree-simple").render_diff(
        old=present if deleted else absent,
        new=absent if deleted else present,
    )
    assert "engine_warning" not in result
    assert [row["status"] for row in result["rows"]] == [
        "delete" if deleted else "insert"
    ] * 3
    assert [
        row["left_text"] if deleted else row["right_text"]
        for row in result["rows"]
    ] == ["first", "", "last"]
    assert result["summary"]["changed_lines"] == 3


def test_unsupported_identical_sources_keep_context() -> None:
    side = DiffSide(
        exists=True,
        text="first\n\nlast\n",
        path_hint="file.unsupported-dirdiff",
    )
    result = GumTreeDiffEngine(matcher="gumtree-simple").render_diff(
        old=side, new=side
    )
    assert result["engine_warning"]["type"] == "gumtree_unsupported_language"
    assert [
        (row["left_no"], row["right_no"], row["status"])
        for row in result["rows"]
    ] == [(1, 1, "equal"), (2, 2, "equal"), (3, 3, "equal")]
    assert result["summary"]["changed_lines"] == 0
