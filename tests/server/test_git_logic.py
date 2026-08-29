"""Git diff-engine row projection tests.

These tests exercise the engine-facing Git projection boundary.  Git emits
unified patches with sparse hunk context, but dirdiff engine rows must describe
the full left/right source surface so the frontend can render and virtualize a
complete file.  The assertions here keep that source-preservation contract
separate from repository loading, HTTP serialization, and syntax enrichment.
"""

from dirdiff.engines import DiffSide
from dirdiff.engines.git import GitDiffEngine


def test_git_engine_keeps_equal_source_outside_patch_hunks() -> None:
    """Sparse unified-patch context expands back to every source line.

    Equal prefixes, inter-hunk gaps, and tails must survive in engine rows on
    both sides, preserving complete File rendering and virtualization.
    """
    old_lines = [
        "pre 1",
        "pre 2",
        "pre 3",
        "pre 4",
        "left one",
        "gap 1",
        "gap 2",
        "gap 3",
        "gap 4",
        "gap 5",
        "gap 6",
        "gap 7",
        "gap 8",
        "left two",
        "tail 1",
        "tail 2",
        "tail 3",
        "tail 4",
    ]
    new_lines = [
        "pre 1",
        "pre 2",
        "pre 3",
        "pre 4",
        "right one",
        "gap 1",
        "gap 2",
        "gap 3",
        "gap 4",
        "gap 5",
        "gap 6",
        "gap 7",
        "gap 8",
        "right two",
        "tail 1",
        "tail 2",
        "tail 3",
        "tail 4",
    ]
    old_text = "\n".join(old_lines)
    new_text = "\n".join(new_lines)

    rows = GitDiffEngine().render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint="demo.txt"),
        new=DiffSide(exists=True, text=new_text, path_hint="demo.txt"),
    )["rows"]

    assert [
        row["left_text"] for row in rows if row["left_no"] is not None
    ] == old_lines
    assert [
        row["right_text"] for row in rows if row["right_no"] is not None
    ] == new_lines
    assert {row["left_text"] for row in rows if row["status"] == "equal"} >= {
        "pre 1",
        "gap 4",
        "gap 5",
        "tail 4",
    }
