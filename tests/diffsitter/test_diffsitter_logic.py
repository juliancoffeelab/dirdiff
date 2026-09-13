"""Check the Diffsitter engine's local decisions, warnings, and rejections.

These tests drive the public engine and row builder. Rows for identical,
empty, absent, unsupported, and whitespace-only inputs are decided locally or
by the real executable's diagnosis; corrupted copies of real cached JSON must
be rejected rather than rendered plausibly; real Unicode coordinates must land
on the characters Diffsitter addressed.
"""

import copy
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import tool_json

from dirdiff.engines import (
    DiffSide,
    DiffsitterDiffEngine,
    DiffsitterJson,
    DirdiffError,
    build_diffsitter_rows_from_json,
)

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"
"""Source pairs whose cached Diffsitter output feeds corruption and offset tests."""

UNICODE_PRESET = "typescript/unicode-byte-offsets-are-not-character-offsets"
"""Preset whose new side is a string literal of multi-byte curly quotes."""


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
    """The real executable's unsupported diagnosis yields text rows and a warning.

    Mixed hints stay unsupported when either side lacks a grammar, and a
    missing hint is not silently given an extension.

    # Parameters

    - `old_hint`: Old-side path hint passed through unchanged.
    - `new_hint`: New-side path hint passed through unchanged.
    """
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


def test_identical_texts_never_run_the_executable() -> None:
    """Identical documents render as equal rows even with an unsupported hint.

    The unsupported hint would produce a warning if Diffsitter ran, so its
    absence proves the local shortcut. A trailing carriage return stays on its
    line and the missing final newline does not create a phantom line.
    """
    text = "one\r\ntwo\n\nthree"
    result = DiffsitterDiffEngine().render_diff(
        old=DiffSide(exists=True, text=text, path_hint="a.unsupported-dirdiff"),
        new=DiffSide(exists=True, text=text, path_hint="b.unsupported-dirdiff"),
    )
    assert "engine_warning" not in result
    assert [
        (row["status"], row["left_no"], row["right_no"], row["left_text"])
        for row in result["rows"]
    ] == [
        ("equal", 1, 1, "one\r"),
        ("equal", 2, 2, "two"),
        ("equal", 3, 3, ""),
        ("equal", 4, 4, "three"),
    ]
    assert result["summary"]["changed_lines"] == 0


@pytest.mark.parametrize(
    "old,new",
    [
        (
            DiffSide(exists=False, text=None, path_hint=None),
            DiffSide(exists=True, text="a\n\nb\n", path_hint="new.nope"),
        ),
        (
            DiffSide(exists=True, text="", path_hint="old.nope"),
            DiffSide(exists=True, text="a\n\nb\n", path_hint="new.nope"),
        ),
        (
            DiffSide(exists=True, text="a\n\nb\n", path_hint="old.nope"),
            DiffSide(exists=False, text=None, path_hint=None),
        ),
        (
            DiffSide(exists=True, text="a\n\nb\n", path_hint="old.nope"),
            DiffSide(exists=True, text="", path_hint="new.nope"),
        ),
    ],
)
def test_one_sided_documents_render_locally(
    old: DiffSide, new: DiffSide
) -> None:
    """Absent or empty sides yield one-sided rows without running Diffsitter.

    Unsupported hints would otherwise trigger the warning. An existing empty
    file and an absent file render identically: the other side is entirely
    inserted or deleted, blank lines included.

    # Parameters

    - `old`: Old side, absent or empty in half the cases.
    - `new`: New side, absent or empty in the other half.
    """
    result = DiffsitterDiffEngine().render_diff(old=old, new=new)
    assert "engine_warning" not in result
    inserted = old.text in (None, "")
    assert [
        (
            row["status"],
            row["left_no"],
            row["left_text"],
            row["right_no"],
            row["right_text"],
        )
        for row in result["rows"]
    ] == [
        ("insert", None, "", number, text)
        if inserted
        else ("delete", number, text, None, "")
        for number, text in [(1, "a"), (2, ""), (3, "b")]
    ]
    assert all(
        "left_tokens" not in row and "right_tokens" not in row
        for row in result["rows"]
    )
    assert result["summary"]["changed_lines"] == 3


def test_whitespace_only_change_reports_empty_result() -> None:
    """Diffsitter strips whitespace, so a spacing-only edit has no entries.

    The engine must not present differing texts as equal: the unified diff
    shows the two spellings as a deletion and an insertion under the
    `diffsitter_empty_result` warning, and no tokens are invented.
    """
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


def test_configured_missing_executable_is_an_error(tmp_path: Path) -> None:
    """An explicit executable path that does not exist is never replaced by PATH.

    Identical texts would skip the executable, so the inputs differ. The
    environment is restored afterwards because it is process-global.
    """
    previous = os.environ.get("DIRDIFF_DIFFSITTER_BIN")
    os.environ["DIRDIFF_DIFFSITTER_BIN"] = str(tmp_path / "missing-diffsitter")
    try:
        with pytest.raises(DirdiffError, match="DIRDIFF_DIFFSITTER_BIN"):
            DiffsitterDiffEngine().render_diff(
                old=DiffSide(exists=True, text="a = 1\n", path_hint="old.py"),
                new=DiffSide(exists=True, text="a = 2\n", path_hint="new.py"),
            )
    finally:
        if previous is None:
            del os.environ["DIRDIFF_DIFFSITTER_BIN"]
        else:
            os.environ["DIRDIFF_DIFFSITTER_BIN"] = previous


def test_unicode_byte_columns_land_on_the_addressed_characters() -> None:
    """Real multi-byte coordinates highlight the curly quotes, not shifted bytes.

    The new literal starts with `“` (three UTF-8 bytes) right after the ASCII
    prefix; a character-offset reading of the byte columns would paint the
    wrong characters or fail to slice. The old side loses `p`, `l`, `n` of
    `plain` while `a` and `i` survive as unchanged context. The local unified
    alignment never pairs differing lines, so each side is its own equal row
    carrying the reported changed and unchanged token facts.
    """
    preset = PRESETS_ROOT / UNICODE_PRESET
    left, right = next(preset.glob("old.*")), next(preset.glob("new.*"))
    rows = build_diffsitter_rows_from_json(
        diff_json=tool_json(
            "diffsitter", f"presets/{UNICODE_PRESET}", left=left, right=right
        ),
        left_text=left.read_bytes().decode(),
        right_text=right.read_bytes().decode(),
    )
    old_row, new_row = rows
    assert (old_row["left_no"], old_row["right_no"]) == (1, None)
    assert (new_row["left_no"], new_row["right_no"]) == (None, 1)
    assert old_row["status"] == new_row["status"] == "equal"
    assert "right_tokens" not in old_row and "left_tokens" not in new_row
    assert [
        (token["status"], token["text"]) for token in old_row["left_tokens"]
    ] == [
        ("unchanged", 'const message = "'),
        ("delete", "p"),
        ("delete", "l"),
        ("unchanged", "ai"),
        ("delete", "n"),
        ("unchanged", '";'),
    ]
    right_tokens = new_row["right_tokens"]
    assert right_tokens[0] == {
        "status": "unchanged",
        "text": 'const message = "',
        "is_ws": False,
    }
    assert right_tokens[1] == {"status": "insert", "text": "“", "is_ws": False}
    assert right_tokens[-2] == {"status": "insert", "text": "”", "is_ws": False}
    assert right_tokens[-1] == {
        "status": "unchanged",
        "text": '";',
        "is_ws": False,
    }
    # Diffsitter keeps exactly the `a` and `i` of `plain` as matched graphemes
    # somewhere in the new literal; every other visible grapheme is inserted.
    assert Counter(
        token["text"] for token in right_tokens if token["status"] == "insert"
    ) == Counter(
        character
        for character in "“beware unicode quotes, they screw up "
        "byte/character offsets”"
        if not character.isspace()
    ) - Counter("ai")


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(
            lambda facts: facts["new"].__setitem__(
                "text", facts["new"]["text"].replace("beware", "Beware")
            ),
            id="echoed-text-differs",
        ),
        pytest.param(
            lambda facts: facts["hunks"][0]["New"][0].__setitem__(
                "line_index", 1
            ),
            id="line-outside-source",
        ),
        pytest.param(
            lambda facts: facts["hunks"][0]["New"][0]["entries"][0][
                "end_position"
            ].__setitem__("row", 1),
            id="entry-spans-rows",
        ),
        pytest.param(
            lambda facts: facts["hunks"][0]["New"][0]["entries"][-1][
                "end_position"
            ].__setitem__("column", 10_000),
            id="column-past-line-end",
        ),
        pytest.param(
            lambda facts: facts["hunks"][0]["New"][0]["entries"][0].update(
                {
                    "start_position": {"row": 0, "column": 18},
                    "end_position": {"row": 0, "column": 21},
                }
            ),
            id="boundary-inside-multibyte-grapheme",
        ),
        pytest.param(
            lambda facts: facts["hunks"][0]["New"][0]["entries"][0].__setitem__(
                "text", "”"
            ),
            id="entry-text-disagrees",
        ),
        pytest.param(
            lambda facts: facts["hunks"][0]["New"][0]["entries"].__setitem__(
                1, facts["hunks"][0]["New"][0]["entries"][0]
            ),
            id="entries-overlap",
        ),
        pytest.param(
            lambda facts: facts["hunks"].append(
                {
                    "New": [
                        {
                            "line_index": 0,
                            "entries": [
                                facts["hunks"][0]["New"][0]["entries"][0]
                            ],
                        }
                    ]
                }
            ),
            id="later-hunk-runs-backwards",
        ),
    ],
)
def test_corrupted_real_output_is_rejected(
    corrupt: Callable[[DiffsitterJson], None],
) -> None:
    """Coordinates or text the source cannot satisfy raise instead of rendering.

    Each mutation is applied to a deep copy of real cached output whose first
    new entry is the three-byte `“`, so a shift of one byte lands inside a
    grapheme and a duplicated entry violates the ascending-column contract.

    # Parameters

    - `corrupt`: In-place mutation of the copied Diffsitter report.
    """
    preset = PRESETS_ROOT / UNICODE_PRESET
    left, right = next(preset.glob("old.*")), next(preset.glob("new.*"))
    facts = copy.deepcopy(
        tool_json(
            "diffsitter", f"presets/{UNICODE_PRESET}", left=left, right=right
        )
    )
    assert facts["hunks"][0]["New"][0]["entries"][0] == {
        "text": "“",
        "start_position": {"row": 0, "column": 17},
        "end_position": {"row": 0, "column": 20},
        "kind_id": facts["hunks"][0]["New"][0]["entries"][0]["kind_id"],
    }
    corrupt(facts)
    with pytest.raises(DirdiffError, match="Diffsitter"):
        build_diffsitter_rows_from_json(
            diff_json=facts,
            left_text=left.read_bytes().decode(),
            right_text=right.read_bytes().decode(),
        )


def test_engine_result_matches_builder_rows_and_counts() -> None:
    """The public engine reproduces the builder's rows and counts them faithfully.

    The rust preset wraps a call over several lines, so the local alignment
    leaves one-sided new rows beside rows that carry tokens; the ones Diffsitter
    never mentioned stay equal. The engine adds nothing beyond the builder's
    rows plus a summary derived independently from reported token facts.
    """
    preset = (
        PRESETS_ROOT / "rust/rust-range-tail-after-wrap-should-stay-right-only"
    )
    left, right = next(preset.glob("old.*")), next(preset.glob("new.*"))
    left_text, right_text = (
        left.read_bytes().decode(),
        right.read_bytes().decode(),
    )
    result = DiffsitterDiffEngine().render_diff(
        old=DiffSide(exists=True, text=left_text, path_hint=left.name),
        new=DiffSide(exists=True, text=right_text, path_hint=right.name),
    )
    rows = build_diffsitter_rows_from_json(
        diff_json=tool_json(
            "diffsitter",
            "presets/rust/rust-range-tail-after-wrap-should-stay-right-only",
            left=left,
            right=right,
        ),
        left_text=left_text,
        right_text=right_text,
    )
    assert "engine_warning" not in result
    assert result["rows"] == rows
    statuses = [row["status"] for row in rows]
    assert "replace" not in statuses
    assert any(
        row["status"] == "equal" and row["left_no"] is None for row in rows
    )
    assert result["summary"] == {
        "changed_lines": 3,
        "modified_lines": 3,
        "added_lines": 0,
        "removed_lines": 0,
        "moved_lines": 0,
    }
