"""Property-style checks for the tokendiff engine over the preset corpus.

Every difftastic preset pair (and each of its sides against itself) runs
through `TokenDiffEngine`, and each test asserts one engine guarantee:
source text is reproduced exactly, lines appear once in order, tokens
partition their lines, unchanged text agrees across sides, row status
derives from tokens, and token statuses replay either document from the
other. Two transform tests then state the engine's headline behaviors on
the same corpus: an indentation shift diffs as whitespace-only equal rows,
and joining lines conserves the changed content as moved text.
"""

import re
from pathlib import Path
from typing import Any, Literal

import pytest

from dirdiff.engines import (
    DiffEngineResult,
    DiffEngineRow,
    DiffSide,
    InlineToken,
    TokenDiffEngine,
)

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "difftastic"
Side = Literal["left", "right"]

__all__: list[str] = []


def _preset_cases() -> list[Any]:
    """Collect every preset's (old text, new text) pair as one test case."""
    cases: list[Any] = []
    for preset in sorted(PRESETS_ROOT.glob("*/*")):
        if not preset.is_dir():
            continue
        old_files = list(preset.glob("old.*"))
        new_files = list(preset.glob("new.*"))
        assert len(old_files) == 1 and len(new_files) == 1, preset
        cases.append(
            pytest.param(
                old_files[0].read_text(encoding="utf-8"),
                new_files[0].read_text(encoding="utf-8"),
                id=f"{preset.parent.name}-{preset.name}",
            )
        )
    assert cases != []
    return cases


CASES = _preset_cases()


def _render(left: str, right: str) -> DiffEngineResult:
    """Render one pair through the real engine entry point."""
    return TokenDiffEngine().render_diff(
        old=DiffSide(exists=True, text=left),
        new=DiffSide(exists=True, text=right),
    )


def _side_no(row: DiffEngineRow, side: Side) -> int | None:
    """Read one side's line number with narrowing."""
    number = row.get(f"{side}_no")
    assert number is None or isinstance(number, int)
    return number


def _side_text(row: DiffEngineRow, side: Side) -> str:
    """Read one side's line text with narrowing."""
    text = row.get(f"{side}_text")
    assert isinstance(text, str)
    return text


def _side_tokens(row: DiffEngineRow, side: Side) -> list[InlineToken]:
    """Read one side's token list with narrowing."""
    tokens = row.get(f"{side}_tokens")
    assert isinstance(tokens, list)
    return tokens


def _source_line_count(text: str) -> int:
    """Count the lines rows must address, excluding the trailing phantom."""
    if text == "":
        return 0
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return len(lines)


def _replayed(rows: list[DiffEngineRow], *, onto: Side) -> list[str]:
    """Rebuild one side's lines using only the other side's unchanged text.

    For `onto="right"`, unchanged token text must come from the left tokens
    (and vice versa), so the rebuild proves the statuses form a real edit
    script rather than two decorated columns.
    """
    source_side: Side = "left" if onto == "right" else "right"
    rebuilt: list[str] = []
    for row in rows:
        if _side_no(row, onto) is None:
            continue
        if _side_no(row, source_side) is None:
            rebuilt.append(_side_text(row, onto))
            continue
        onto_tokens = _side_tokens(row, onto)
        if onto_tokens == []:
            rebuilt.append(_side_text(row, source_side))
            continue
        unchanged = "".join(
            token["text"]
            for token in _side_tokens(row, source_side)
            if token["status"] == "unchanged"
        )
        pieces: list[str] = []
        cursor = 0
        for token in onto_tokens:
            if token["status"] == "unchanged":
                pieces.append(unchanged[cursor : cursor + len(token["text"])])
                cursor += len(token["text"])
            else:
                pieces.append(token["text"])
        assert cursor == len(unchanged)
        rebuilt.append("".join(pieces))
    return rebuilt


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_rows_reproduce_sources(left: str, right: str) -> None:
    """Row texts joined with newlines reproduce both documents exactly."""
    rows = _render(left, right)["rows"]
    sides: list[tuple[Side, str]] = [("left", left), ("right", right)]
    for side, source in sides:
        lines = [
            _side_text(row, side)
            for row in rows
            if _side_no(row, side) is not None
        ]
        joined = "\n".join(lines)
        if source.endswith("\n"):
            joined += "\n"
        assert joined == source


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_line_numbers_cover_sources_in_order(
    left: str, right: str
) -> None:
    """Each side's line numbers are exactly 1..N in row order."""
    rows = _render(left, right)["rows"]
    sides: list[tuple[Side, str]] = [("left", left), ("right", right)]
    for side, source in sides:
        numbers = [
            _side_no(row, side)
            for row in rows
            if _side_no(row, side) is not None
        ]
        assert numbers == list(range(1, _source_line_count(source) + 1))


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_tokens_partition_their_lines(left: str, right: str) -> None:
    """Non-empty token lists concatenate to their exact line text."""
    rows = _render(left, right)["rows"]
    sides: list[Side] = ["left", "right"]
    for row in rows:
        for side in sides:
            tokens = _side_tokens(row, side)
            if tokens == []:
                continue
            assert _side_no(row, side) is not None
            assert "".join(token["text"] for token in tokens) == _side_text(
                row, side
            )
            for token in tokens:
                assert token["text"] != ""
                assert token["is_ws"] == token["text"].isspace()


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_unchanged_text_agrees_across_sides(
    left: str, right: str
) -> None:
    """Per row, unchanged token text is identical on both sides.

    One-sided rows carry no tokens at all: their entire presence is the
    change, so nothing on them may claim to be shared.
    """
    rows = _render(left, right)["rows"]
    for row in rows:
        left_unchanged = "".join(
            token["text"]
            for token in row["left_tokens"]
            if token["status"] == "unchanged"
        )
        right_unchanged = "".join(
            token["text"]
            for token in row["right_tokens"]
            if token["status"] == "unchanged"
        )
        assert left_unchanged == right_unchanged
        if row["left_no"] is None or row["right_no"] is None:
            assert row["left_tokens"] == []
            assert row["right_tokens"] == []


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_row_status_derives_from_tokens(
    left: str, right: str
) -> None:
    """Row status is the pure token-status function the engine documents."""
    rows = _render(left, right)["rows"]
    for row in rows:
        if row["left_no"] is None:
            assert row["status"] == "insert"
            continue
        if row["right_no"] is None:
            assert row["status"] == "delete"
            continue
        tokens = row["left_tokens"] + row["right_tokens"]
        if tokens == []:
            assert row["left_text"] == row["right_text"]
            assert row["status"] == "equal"
            continue
        content_changed = any(
            token["status"] != "unchanged" and token["is_ws"] is False
            for token in tokens
        )
        assert row["status"] == ("replace" if content_changed else "equal")


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_replays_left_to_right(left: str, right: str) -> None:
    """Token statuses rebuild the new document from old-side data."""
    rows = _render(left, right)["rows"]
    joined = "\n".join(_replayed(rows, onto="right"))
    if right.endswith("\n"):
        joined += "\n"
    assert joined == right


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_replays_right_to_left(left: str, right: str) -> None:
    """Token statuses rebuild the old document from new-side data."""
    rows = _render(left, right)["rows"]
    joined = "\n".join(_replayed(rows, onto="left"))
    if left.endswith("\n"):
        joined += "\n"
    assert joined == left


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_identity_diff_is_all_equal(left: str, right: str) -> None:
    """A document diffed against itself has only bare equal rows."""
    for source in (left, right):
        result = _render(source, source)
        assert result["summary"] == {
            "changed_lines": 0,
            "modified_lines": 0,
            "added_lines": 0,
            "removed_lines": 0,
            "moved_lines": 0,
        }
        for row in result["rows"]:
            assert row["status"] == "equal"
            assert row["left_tokens"] == []
            assert row["right_tokens"] == []


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_summary_matches_rows(left: str, right: str) -> None:
    """The summary is a recount of the returned rows, nothing else."""
    result = _render(left, right)
    modified = sum(
        1
        for row in result["rows"]
        if row["status"] == "replace"
        or (row["status"] == "equal" and row["left_text"] != row["right_text"])
    )
    added = sum(1 for row in result["rows"] if row["status"] == "insert")
    removed = sum(1 for row in result["rows"] if row["status"] == "delete")
    assert result["summary"] == {
        "changed_lines": modified + added + removed,
        "modified_lines": modified,
        "added_lines": added,
        "removed_lines": removed,
        "moved_lines": 0,
    }


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_indent_shift_is_pure_whitespace(
    left: str, right: str
) -> None:
    """Indenting every content line diffs as whitespace-only equal rows.

    This is the behavior line-first diffs get wrong: no line may fall out
    of pairing and no non-whitespace token may be marked changed.
    """
    for source in (left, right):
        segments = source.split("\n")
        shifted = "\n".join(
            "    " + segment if segment.strip() != "" else segment
            for segment in segments
        )
        rows = _render(source, shifted)["rows"]
        for row in rows:
            assert row["left_no"] is not None
            assert row["right_no"] is not None
            assert row["status"] == "equal"
            for token in row["left_tokens"] + row["right_tokens"]:
                if token["status"] != "unchanged":
                    assert token["is_ws"] is True


@pytest.mark.parametrize(("left", "right"), CASES)
def test_tokendiff_line_join_conserves_moved_content(
    left: str, right: str
) -> None:
    """Joining line pairs diffs as movement, never as rewriting.

    All content is conserved by the transform, so the non-whitespace
    characters reported changed must be the same multiset on both sides —
    words may move between rows (and repeated words may match a different
    occurrence), but the engine may not invent or lose content.
    """
    for source in (left, right):
        segments = source.split("\n")
        merged = [
            segments[index] + " " + segments[index + 1]
            for index in range(0, len(segments) - 1, 2)
        ]
        if len(segments) % 2 == 1:
            merged.append(segments[-1])
        rows = _render(source, "\n".join(merged))["rows"]
        sides: list[Side] = ["left", "right"]
        changed: dict[str, str] = {}
        for side in sides:
            parts: list[str] = []
            for row in rows:
                if _side_no(row, side) is None:
                    continue
                tokens = _side_tokens(row, side)
                if tokens != []:
                    parts.append(
                        "".join(
                            token["text"]
                            for token in tokens
                            if token["status"] != "unchanged"
                        )
                    )
                elif row["status"] in ("delete", "insert"):
                    parts.append(_side_text(row, side))
            changed[side] = "".join(sorted(re.sub(r"\s+", "", "".join(parts))))
        assert changed["left"] == changed["right"]
