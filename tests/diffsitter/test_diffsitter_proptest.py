from pathlib import Path

import pytest
from helpers import tool_json
from hypothesis import given
from hypothesis import strategies as st

from dirdiff.engines import DiffsitterJson, build_diffsitter_rows_from_json
from dirdiff.rendering import highlight_lines_for_path, weave_decorated_parts

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"

SUPPORTED_SUFFIXES = {".py", ".rs", ".ts", ".tsx", ".ml"}

SUPPORTED_PRESETS = sorted(
    preset
    for preset in PRESETS_ROOT.glob("*/*")
    if next(preset.glob("old.*")).suffix in SUPPORTED_SUFFIXES
)


@pytest.mark.parametrize("preset", SUPPORTED_PRESETS, ids=str)
def test_sources_survive_alignment_and_decoration(preset: Path) -> None:
    left = next(preset.glob("old.*"))
    right = next(preset.glob("new.*"))
    left_text = left.read_bytes().decode()
    right_text = right.read_bytes().decode()
    rows = build_diffsitter_rows_from_json(
        diff_json=tool_json(
            "diffsitter",
            f"presets/{preset.relative_to(PRESETS_ROOT).as_posix()}",
            left=left,
            right=right,
        ),
        left_text=left_text,
        right_text=right_text,
    )
    assert all("move" not in row for row in rows)
    for side, text, path in [
        ("left", left_text, left),
        ("right", right_text, right),
    ]:
        syntax = highlight_lines_for_path(path.name, text)
        numbers = [
            row["left_no"] if side == "left" else row["right_no"]
            for row in rows
        ]
        present = [number for number in numbers if number is not None]
        assert present == list(range(1, len(present) + 1))
        rendered = []
        for row, number in zip(rows, numbers, strict=True):
            content = row["left_text"] if side == "left" else row["right_text"]
            if number is None:
                assert content == ""
                assert (
                    "left_tokens" if side == "left" else "right_tokens"
                ) not in row
                continue
            tokens = (
                row["left_tokens"] if side == "left" else row["right_tokens"]
            )
            # A line Diffsitter never mentioned carries no tokens at all; a
            # mentioned line is partitioned completely.
            assert tokens == [] or (
                "".join(token["text"] for token in tokens) == content
            )
            assert all(
                token["text"] != ""
                and token["is_ws"] == token["text"].isspace()
                and token["status"] != "replace"
                and "move" not in token
                for token in tokens
            )
            parts = weave_decorated_parts(
                content,
                tokens,
                [] if syntax is None else syntax[number - 1],
            )
            assert "".join(part["text"] for part in parts) == content
            assert [
                part["diff_status"] for part in parts for _ in part["text"]
            ] == (
                ["unchanged"] * len(content)
                if tokens == []
                else [
                    token["status"] for token in tokens for _ in token["text"]
                ]
            )
            rendered.append(content)
        assert (
            "\n".join(rendered) + ("\n" if text.endswith("\n") else "") == text
        ), (preset, side)


@pytest.mark.parametrize("preset", SUPPORTED_PRESETS, ids=str)
def test_preset_tokens_replay_every_diffsitter_entry(preset: Path) -> None:
    left = next(preset.glob("old.*"))
    right = next(preset.glob("new.*"))
    facts = tool_json(
        "diffsitter",
        f"presets/{preset.relative_to(PRESETS_ROOT).as_posix()}",
        left=left,
        right=right,
    )
    rows = build_diffsitter_rows_from_json(
        diff_json=facts,
        left_text=left.read_bytes().decode(),
        right_text=right.read_bytes().decode(),
    )
    reported = 0
    for old_side in (True, False):
        status = "delete" if old_side else "insert"
        expected = sorted(
            (
                line["line_index"],
                entry["start_position"]["column"],
                entry["text"],
            )
            for hunk in facts["hunks"]
            for line in (
                hunk.get("Old", []) if old_side else hunk.get("New", [])
            )
            for entry in line["entries"]
        )
        reported += len(expected)
        assert all(not text.isspace() for _, _, text in expected)
        actual: list[tuple[int, int, str]] = []
        for row in rows:
            number = row["left_no"] if old_side else row["right_no"]
            if number is None:
                continue
            byte_offset = 0
            for token in (
                row["left_tokens"] if old_side else row["right_tokens"]
            ):
                if token["status"] != "unchanged":
                    assert token["status"] == status, (preset, row)
                    actual.append((number - 1, byte_offset, token["text"]))
                byte_offset += len(token["text"].encode())
        assert actual == expected, (preset, status)
    # Every corpus pair differs visibly, so the engine would never have taken
    # the empty-result path for it.
    assert reported > 0, preset
    # Only a nonempty partition made entirely from one side's status changes
    # the row status. Every other reported token mixture remains equal.
    for row in rows:
        tokens = row.get("left_tokens", []) + row.get("right_tokens", [])
        if tokens != [] and all(
            token["status"] == "delete" for token in tokens
        ):
            expected_status = "delete"
        elif tokens != [] and all(
            token["status"] == "insert" for token in tokens
        ):
            expected_status = "insert"
        elif (
            tokens == []
            and row["left_no"] is not None
            and row["right_no"] is None
            and row["left_text"].strip() == ""
        ):
            expected_status = "delete"
        elif (
            tokens == []
            and row["right_no"] is not None
            and row["left_no"] is None
            and row["right_text"].strip() == ""
        ):
            expected_status = "insert"
        else:
            expected_status = "equal"
        assert row["status"] == expected_status, (preset, row)


@given(
    left=st.lists(st.sampled_from(["a", "b", "", " ", "a\r"]), max_size=12),
    right=st.lists(st.sampled_from(["a", "b", "", " ", "a\r"]), max_size=12),
)
def test_empty_reports_align_every_line_once_without_reordering(
    left: list[str], right: list[str]
) -> None:
    left_text = "".join(line + "\n" for line in left)
    right_text = "".join(line + "\n" for line in right)
    # tool-json: synthetic an entry-free report isolates the alignment step
    facts: DiffsitterJson = {
        "hunks": [],
        "old": {"filename": "old.py", "text": left_text},
        "new": {"filename": "new.py", "text": right_text},
    }
    rows = build_diffsitter_rows_from_json(
        diff_json=facts, left_text=left_text, right_text=right_text
    )
    assert [row["left_no"] for row in rows if row["left_no"] is not None] == (
        list(range(1, len(left) + 1))
    )
    assert [
        row["right_no"] for row in rows if row["right_no"] is not None
    ] == list(range(1, len(right) + 1))
    for row in rows:
        assert row["left_no"] is not None or row["right_no"] is not None
        if row["left_no"] is not None:
            assert row["left_text"] == left[row["left_no"] - 1]
            assert row["left_tokens"] == []
        if row["right_no"] is not None:
            assert row["right_text"] == right[row["right_no"] - 1]
            assert row["right_tokens"] == []
        if row["left_no"] is not None and row["right_no"] is not None:
            assert row["left_text"] == row["right_text"]
            assert row["status"] == "equal"
        elif row["left_no"] is not None:
            assert row["status"] == (
                "delete" if row["left_text"].strip() == "" else "equal"
            )
        else:
            assert row["status"] == (
                "insert" if row["right_text"].strip() == "" else "equal"
            )
    if left == right:
        assert len(rows) == len(left)


@given(
    line=st.text(alphabet="ab ї😀é\r\t", min_size=1, max_size=16),
    picks=st.lists(st.booleans(), min_size=16, max_size=16),
    old_side=st.booleans(),
)
def test_byte_columns_slice_unicode_graphemes_exactly(
    line: str, picks: list[bool], old_side: bool
) -> None:
    chosen = [
        (index, character)
        for index, (character, pick) in enumerate(
            zip(line, picks, strict=False)
        )
        if pick and not character.isspace()
    ]
    text = "x\n" + line + "\n"
    other = "x\ny\n"
    # tool-json: synthetic hand-placed byte columns check slicing, not Diffsitter
    facts: DiffsitterJson = {
        "hunks": [
            {"Old": [{"line_index": 1, "entries": []}]},
            {"Old": [{"line_index": 1, "entries": []}]},
        ]
        if old_side
        else [
            {"New": [{"line_index": 1, "entries": []}]},
            {"New": [{"line_index": 1, "entries": []}]},
        ],
        "old": {"filename": "old.py", "text": text if old_side else other},
        "new": {"filename": "new.py", "text": other if old_side else text},
    }
    for ordinal, (index, character) in enumerate(chosen):
        # The second half of the entries goes into the second hunk.
        hunk = facts["hunks"][0 if ordinal < len(chosen) // 2 else 1]
        (record,) = hunk["Old"] if old_side else hunk["New"]
        record["entries"].append(
            {
                "text": character,
                "start_position": {
                    "row": 1,
                    "column": len(line[:index].encode()),
                },
                "end_position": {
                    "row": 1,
                    "column": len(line[: index + 1].encode()),
                },
                "kind_id": 1,
            }
        )
    rows = build_diffsitter_rows_from_json(
        diff_json=facts,
        left_text=facts["old"]["text"],
        right_text=facts["new"]["text"],
    )
    (row,) = [
        row
        for row in rows
        if (row["left_no"] if old_side else row["right_no"]) == 2
    ]
    tokens = row["left_tokens"] if old_side else row["right_tokens"]
    changed_status = "delete" if old_side else "insert"
    if chosen == []:
        # An unmentioned one-sided line is context unless it is blank, whose
        # presence alone is the change Diffsitter cannot report.
        assert tokens == []
        expected_status = changed_status if line.strip() == "" else "equal"
        assert row["status"] == expected_status
        return
    assert "".join(token["text"] for token in tokens) == line
    assert [
        token["text"] for token in tokens if token["status"] != "unchanged"
    ] == [character for _, character in chosen]
    assert all(
        token["status"] == changed_status
        for token in tokens
        if token["status"] != "unchanged"
    )
    # Adjacent chosen characters stay separate tokens; gaps merge into one.
    assert len(tokens) == len(chosen) + sum(
        1
        for previous, current in zip(
            [-1, *(index for index, _ in chosen)],
            [*(index for index, _ in chosen), len(line)],
            strict=True,
        )
        if current - previous > 1
    )
    expected_status = (
        changed_status
        if all(token["status"] == changed_status for token in tokens)
        else "equal"
    )
    assert row["status"] == expected_status
