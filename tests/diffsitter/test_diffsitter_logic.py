import pytest

from dirdiff.engines import (
    DiffSide,
    DiffsitterDiffEngine,
)

__all__: list[str] = []


@pytest.mark.parametrize(
    "old_hint,new_hint",
    [
        ("old.unsupported-dirdiff", "new.unsupported-dirdiff"),
        ("old.py", "new.unsupported-dirdiff"),
        (None, None),
        ("Makefile", "Makefile"),
    ],
)
def test_unsupported_language_uses_unified_diff_with_notice(
    old_hint: str | None, new_hint: str | None
) -> None:
    result = DiffsitterDiffEngine().render_diff(
        old=DiffSide(exists=True, text="old\ncontext\n", path_hint=old_hint),
        new=DiffSide(exists=True, text="new\ncontext\n", path_hint=new_hint),
    )
    assert result["engine_warning"] == {
        "type": "diffsitter_unsupported_language",
        "message": (
            "Diffsitter does not support this file type. "
            "Showing a unified text diff."
        ),
    }
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
        ("delete", 1, None, "old", ""),
        ("insert", None, 1, "", "new"),
        ("equal", 2, 2, "context", "context"),
    ]
    assert all(
        "left_tokens" not in row and "right_tokens" not in row
        for row in result["rows"]
    )
    assert result["summary"] == {
        "changed_lines": 2,
        "modified_lines": 0,
        "added_lines": 1,
        "removed_lines": 1,
        "moved_lines": 0,
    }


def test_whitespace_only_change_reports_empty_result() -> None:
    result = DiffsitterDiffEngine().render_diff(
        old=DiffSide(exists=True, text="x = 1\ny = 2\n", path_hint="old.py"),
        new=DiffSide(exists=True, text="x  =  1\ny = 2\n", path_hint="new.py"),
    )
    assert result["engine_warning"] == {
        "type": "diffsitter_empty_result",
        "message": (
            "Diffsitter reported no edits for texts that differ. "
            "Showing a unified text diff."
        ),
    }
    assert [
        (row["status"], row["left_text"], row["right_text"])
        for row in result["rows"]
    ] == [
        ("delete", "x = 1", ""),
        ("insert", "", "x  =  1"),
        ("equal", "y = 2", "y = 2"),
    ]
    assert all("left_tokens" not in row for row in result["rows"])
