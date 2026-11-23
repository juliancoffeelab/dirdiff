import pytest
from helpers import ToolText, tool_json

from dirdiff.engines import (
    DiffSide,
    DirdiffError,
    GumTreeDiffEngine,
    build_gumtree_rows_from_json,
)

__all__: list[str] = []


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
