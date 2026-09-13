"""Compare the Git row adapter with sources and real Git preset output.

The tests expose independent engine invariants: rows replay every preset in
both directions, row coordinates reproduce Git's alignment, changed-row
classification reproduces Git's controlled color facts, and generated
whitespace mutations reproduce Git's ``--ignore-space-change`` alignment.
Each oracle invokes Git separately from ``GitDiffEngine`` and never calls the
adapter's private patch parser.

This module owns no fixtures or caches.  It does not test ANSI syntax in
isolation, frontend rendering, preference persistence, or Git's choice of diff.
"""

import re
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dirdiff.engines import (
    DiffSide,
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    GitDiffEngine,
    UnpairedMove,
    git_executable,
)

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "git"
"""Readable Git source pairs used as the complete preset corpus."""

PRESETS = sorted(PRESETS_ROOT.glob("*/*"))
"""Every Git preset directory in stable pytest display order."""


@pytest.mark.parametrize(
    "algorithm", ["myers", "minimal", "patience", "histogram"]
)
@pytest.mark.parametrize("preset", PRESETS, ids=str)
def test_git_preset_diff_replays_both_directions(
    preset: Path,
    algorithm: GitAlgorithm,
) -> None:
    """Row statuses replay old into new and new into old without losing text.

    The forward walk consumes equal, deleted, and old-side moved rows from the
    old source and emits equal, inserted, and new-side moved rows.  The reverse
    walk applies the inverse operations.  Both walks use the adapter's statuses
    as the edit script rather than collecting each side independently.

    # Parameters

    - `preset`: Readable old and new source pair being replayed.
    - `algorithm`: Exact Git line-matching algorithm used for the edit script.
    """
    old_path = next(preset.glob("old.*"))
    new_path = next(preset.glob("new.*"))
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    assert old_text.endswith("\n")
    assert new_text.endswith("\n")
    rows = GitDiffEngine(
        algorithm=algorithm,
        color_moved="zebra",
    ).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=old_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=new_path.name),
    )["rows"]

    old_lines = old_text.splitlines()
    forward_cursor = 0
    replayed_new: list[str] = []
    for row in rows:
        if row["status"] == "equal":
            assert old_lines[forward_cursor] == row["left_text"]
            replayed_new.append(old_lines[forward_cursor])
            forward_cursor += 1
        elif row["status"] == "delete" or (
            row["status"] == "move" and row["left_no"] is not None
        ):
            assert old_lines[forward_cursor] == row["left_text"]
            forward_cursor += 1
        elif row["status"] == "insert" or (
            row["status"] == "move" and row["right_no"] is not None
        ):
            replayed_new.append(row["right_text"])
        else:
            raise AssertionError(row)
    assert forward_cursor == len(old_lines)
    assert "\n".join(replayed_new) + "\n" == new_text

    new_lines = new_text.splitlines()
    reverse_cursor = 0
    replayed_old: list[str] = []
    for row in rows:
        if row["status"] == "equal":
            assert new_lines[reverse_cursor] == row["right_text"]
            replayed_old.append(new_lines[reverse_cursor])
            reverse_cursor += 1
        elif row["status"] == "insert" or (
            row["status"] == "move" and row["right_no"] is not None
        ):
            assert new_lines[reverse_cursor] == row["right_text"]
            reverse_cursor += 1
        elif row["status"] == "delete" or (
            row["status"] == "move" and row["left_no"] is not None
        ):
            replayed_old.append(row["left_text"])
        else:
            raise AssertionError(row)
    assert reverse_cursor == len(new_lines)
    assert "\n".join(replayed_old) + "\n" == old_text


@pytest.mark.parametrize(
    "algorithm", ["myers", "minimal", "patience", "histogram"]
)
@pytest.mark.parametrize("preset", PRESETS, ids=str)
def test_git_preset_line_alignment_matches_git_patch(
    preset: Path,
    algorithm: GitAlgorithm,
    tmp_path: Path,
) -> None:
    """Every adapter coordinate pair must equal Git's complete line alignment.

    A separate zero-context Git invocation supplies changed coordinates.  The
    test expands the gaps between its hunks into paired equal coordinates, then
    compares that independent sequence with every adapter row.

    # Parameters

    - `preset`: Readable source pair supplied to Git and the adapter.
    - `algorithm`: Exact Git line-matching algorithm used by both invocations.
    - `tmp_path`: Isolated directory containing the oracle's source files.
    """
    old_path = next(preset.glob("old.*"))
    new_path = next(preset.glob("new.*"))
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    oracle_old = tmp_path / f"old{old_path.suffix}"
    oracle_new = tmp_path / f"new{new_path.suffix}"
    oracle_old.write_text(old_text)
    oracle_new.write_text(new_text)
    completed = subprocess.run(
        [
            git_executable(),
            "-c",
            "diff.suppressBlankEmpty=false",
            "diff",
            "--no-index",
            f"--diff-algorithm={algorithm}",
            "--no-color",
            "--unified=0",
            "--no-relative",
            "--text",
            "--no-ext-diff",
            "--no-textconv",
            str(oracle_old),
            str(oracle_new),
        ],
        check=False,
        capture_output=True,
    )
    assert completed.returncode in {0, 1}, completed.stderr.decode()
    patch = completed.stdout.decode()

    old_line_count = len(old_text.splitlines())
    new_line_count = len(new_text.splitlines())
    expected: list[tuple[int | None, int | None]] = []
    old_no = 1
    new_no = 1
    in_hunk = False
    for line in patch.splitlines():
        match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
            line,
        )
        if match is not None:
            target_old = int(match[1]) + (match[2] == "0")
            target_new = int(match[3]) + (match[4] == "0")
            while old_no < target_old and new_no < target_new:
                expected.append((old_no, new_no))
                old_no += 1
                new_no += 1
            assert old_no == target_old
            assert new_no == target_new
            in_hunk = True
            continue
        if not in_hunk or line.startswith("\\"):
            continue
        if line.startswith("-"):
            expected.append((old_no, None))
            old_no += 1
        elif line.startswith("+"):
            expected.append((None, new_no))
            new_no += 1
        else:
            raise AssertionError(f"Unexpected zero-context Git row: {line!r}")
    while old_no <= old_line_count and new_no <= new_line_count:
        expected.append((old_no, new_no))
        old_no += 1
        new_no += 1
    assert old_no == old_line_count + 1
    assert new_no == new_line_count + 1

    rows = GitDiffEngine(algorithm=algorithm).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=old_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=new_path.name),
    )["rows"]
    assert [(row["left_no"], row["right_no"]) for row in rows] == expected


@pytest.mark.parametrize(
    "color_moved_ws",
    [
        "no",
        "ignore-space-at-eol",
        "ignore-space-change",
        "ignore-all-space",
        "allow-indentation-change",
    ],
)
@pytest.mark.parametrize(
    "color_moved", ["no", "plain", "blocks", "zebra", "dimmed-zebra", "default"]
)
@pytest.mark.parametrize("preset", PRESETS, ids=str)
def test_git_preset_changed_rows_match_git_patch(
    preset: Path,
    color_moved: GitColorMoved,
    color_moved_ws: GitColorMovedWs,
    tmp_path: Path,
) -> None:
    """Every changed adapter row must reproduce one exact Git patch fact.

    A separate zero-context Git invocation uses reserved color slots as a
    machine-readable status alphabet.  Its prefix, coordinate, source text,
    move group, and dimmed bit are compared as one changed-row fact with the
    adapter result.

    # Parameters

    - `preset`: Readable source pair supplied to Git and the adapter.
    - `color_moved`: Exact Git move-classification mode used by both.
    - `color_moved_ws`: Exact move-whitespace policy used by both.
    - `tmp_path`: Isolated directory containing the oracle's source files.
    """
    old_path = next(preset.glob("old.*"))
    new_path = next(preset.glob("new.*"))
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    oracle_old = tmp_path / f"old{old_path.suffix}"
    oracle_new = tmp_path / f"new{new_path.suffix}"
    oracle_old.write_text(old_text)
    oracle_new.write_text(new_text)
    completed = subprocess.run(
        [
            git_executable(),
            *[
                argument
                for slot, color in {
                    "meta": "normal",
                    "frag": "normal",
                    "func": "normal",
                    "context": "normal",
                    "old": "31",
                    "new": "32",
                    "oldMoved": "33",
                    "newMoved": "34",
                    "oldMovedAlternative": "35",
                    "newMovedAlternative": "36",
                    "oldMovedDimmed": "37",
                    "newMovedDimmed": "38",
                    "oldMovedAlternativeDimmed": "39",
                    "newMovedAlternativeDimmed": "40",
                    "whitespace": "normal",
                }.items()
                for argument in ("-c", f"color.diff.{slot}={color}")
            ],
            "-c",
            "diff.wsErrorHighlight=none",
            "-c",
            "diff.suppressBlankEmpty=false",
            "diff",
            "--no-index",
            "--diff-algorithm=myers",
            "--color=always",
            f"--color-moved={color_moved}",
            f"--color-moved-ws={color_moved_ws}",
            "--unified=0",
            "--no-relative",
            "--text",
            "--no-ext-diff",
            "--no-textconv",
            str(oracle_old),
            str(oracle_new),
        ],
        check=False,
        capture_output=True,
    )
    assert completed.returncode in {0, 1}, completed.stderr.decode()
    patch = completed.stdout.decode()

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    expected: list[
        tuple[
            str,
            int | None,
            int | None,
            str,
            str,
            UnpairedMove | None,
        ]
    ] = []
    old_no = 1
    new_no = 1
    in_hunk = False
    move_colors: dict[str, UnpairedMove] = {
        "33": {"kind": "unpaired", "group": "primary", "dimmed": False},
        "34": {"kind": "unpaired", "group": "primary", "dimmed": False},
        "35": {
            "kind": "unpaired",
            "group": "alternative",
            "dimmed": False,
        },
        "36": {
            "kind": "unpaired",
            "group": "alternative",
            "dimmed": False,
        },
        "37": {"kind": "unpaired", "group": "primary", "dimmed": True},
        "38": {"kind": "unpaired", "group": "primary", "dimmed": True},
        "39": {
            "kind": "unpaired",
            "group": "alternative",
            "dimmed": True,
        },
        "40": {
            "kind": "unpaired",
            "group": "alternative",
            "dimmed": True,
        },
    }
    for raw_line in patch.splitlines():
        plain_line = re.sub(r"\x1b\[[0-9;]*m", "", raw_line)
        match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
            plain_line,
        )
        if match is not None:
            old_no = int(match[1]) + (match[2] == "0")
            new_no = int(match[3]) + (match[4] == "0")
            in_hunk = True
            continue
        if not in_hunk or plain_line.startswith("\\"):
            continue
        envelope = re.match(r"^\x1b\[38;5;(\d+)m([+-])", raw_line)
        assert envelope is not None, raw_line
        color = envelope[1]
        prefix = envelope[2]
        move = move_colors.get(color)
        if prefix == "-":
            expected.append(
                (
                    "move" if move is not None else "delete",
                    old_no,
                    None,
                    old_lines[old_no - 1],
                    "",
                    move,
                )
            )
            old_no += 1
        else:
            expected.append(
                (
                    "move" if move is not None else "insert",
                    None,
                    new_no,
                    "",
                    new_lines[new_no - 1],
                    move,
                )
            )
            new_no += 1

    rows = GitDiffEngine(
        color_moved=color_moved,
        color_moved_ws=color_moved_ws,
    ).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=old_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=new_path.name),
    )["rows"]
    actual = [
        (
            row["status"],
            row["left_no"],
            row["right_no"],
            row["left_text"],
            row["right_text"],
            row.get("move"),
        )
        for row in rows
        if row["status"] != "equal"
    ]
    assert actual == expected


@pytest.mark.parametrize("source_side", ["old", "new"])
@pytest.mark.parametrize("preset", PRESETS, ids=str)
@given(
    data=st.data(),
    insertion_position=st.integers(min_value=0),
    reverse=st.booleans(),
    algorithm=st.sampled_from(("myers", "minimal", "patience", "histogram")),
)
@settings(max_examples=20, deadline=None)
def test_git_preset_ignore_space_change_matches_whitespace_mutation(
    preset: Path,
    source_side: str,
    data: st.DataObject,
    insertion_position: int,
    reverse: bool,
    algorithm: GitAlgorithm,
) -> None:
    """Git ``-b`` ignores generated whitespace while preserving both spellings.

    Every existing horizontal-whitespace run in one preset source receives a
    different nonempty spelling, and lines without trailing whitespace may
    receive it.  One fixed non-whitespace sentinel, placed and directed by
    Hypothesis, forces a real hunk.  A separate Git invocation determines the
    complete alignment; the adapter must return that alignment with exact text
    from both generated sources and no other changed row.

    # Parameters

    - `preset`: Readable pair supplying the source corpus.
    - `source_side`: Old or new preset File independently used as the source.
    - `data`: Hypothesis choices for every source-local whitespace mutation.
    - `insertion_position`: Generated sentinel position reduced to source bounds.
    - `reverse`: Whether the sentinel and mutations appear on the old side.
    - `algorithm`: Git line-matching algorithm used by both invocations.
    """
    source_path = next(preset.glob(f"{source_side}.*"))
    source_text = source_path.read_text()
    assert source_text.endswith("\n")
    source_lines = source_text.splitlines()
    mutated_lines: list[str] = []
    replacements = (" ", "  ", "\t", " \t ")
    for line_index, line in enumerate(source_lines):
        pieces: list[str] = []
        cursor = 0
        for run_index, match in enumerate(re.finditer(r"[ \t]+", line)):
            pieces.append(line[cursor : match.start()])
            choices = tuple(
                replacement
                for replacement in replacements
                if replacement != match[0]
            )
            pieces.append(
                data.draw(
                    st.sampled_from(choices),
                    label=f"line_{line_index}_run_{run_index}",
                )
            )
            cursor = match.end()
        pieces.append(line[cursor:])
        mutated = "".join(pieces)
        if re.search(r"[ \t]$", line) is None:
            mutated += data.draw(
                st.sampled_from(("", " ", "\t", "  \t")),
                label=f"line_{line_index}_trailing",
            )
        mutated_lines.append(mutated)

    sentinel = "__DIRDIFF_PROPERTY_INSERTION__"
    assert sentinel not in source_lines
    position = insertion_position % (len(mutated_lines) + 1)
    mutated_lines.insert(position, sentinel)
    mutated_text = "\n".join(mutated_lines) + "\n"
    if reverse:
        old_text, new_text = mutated_text, source_text
    else:
        old_text, new_text = source_text, mutated_text

    with tempfile.TemporaryDirectory(prefix="dirdiff-git-proptest-") as raw_tmp:
        tmp = Path(raw_tmp)
        oracle_old = tmp / f"old{source_path.suffix}"
        oracle_new = tmp / f"new{source_path.suffix}"
        oracle_old.write_text(old_text)
        oracle_new.write_text(new_text)
        completed = subprocess.run(
            [
                git_executable(),
                "-c",
                "diff.suppressBlankEmpty=false",
                "diff",
                "--no-index",
                f"--diff-algorithm={algorithm}",
                "--ignore-space-change",
                "--no-color",
                "--unified=0",
                "--no-relative",
                "--text",
                "--no-ext-diff",
                "--no-textconv",
                str(oracle_old),
                str(oracle_new),
            ],
            check=False,
            capture_output=True,
        )
        assert completed.returncode == 1, completed.stderr.decode()
        patch = completed.stdout.decode()

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    expected: list[tuple[str, int | None, int | None, str, str]] = []
    old_no = 1
    new_no = 1
    in_hunk = False
    for line in patch.splitlines():
        mutation_hunk_match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
            line,
        )
        if mutation_hunk_match is not None:
            target_old = int(mutation_hunk_match[1]) + (
                mutation_hunk_match[2] == "0"
            )
            target_new = int(mutation_hunk_match[3]) + (
                mutation_hunk_match[4] == "0"
            )
            while old_no < target_old and new_no < target_new:
                expected.append(
                    (
                        "equal",
                        old_no,
                        new_no,
                        old_lines[old_no - 1],
                        new_lines[new_no - 1],
                    )
                )
                old_no += 1
                new_no += 1
            assert old_no == target_old
            assert new_no == target_new
            in_hunk = True
            continue
        if in_hunk is False or line.startswith("\\") is True:
            continue
        if line.startswith("-") is True:
            expected.append(("delete", old_no, None, old_lines[old_no - 1], ""))
            old_no += 1
        elif line.startswith("+") is True:
            expected.append(("insert", None, new_no, "", new_lines[new_no - 1]))
            new_no += 1
        else:
            raise AssertionError(f"Unexpected zero-context Git row: {line!r}")
    while old_no <= len(old_lines) and new_no <= len(new_lines):
        expected.append(
            (
                "equal",
                old_no,
                new_no,
                old_lines[old_no - 1],
                new_lines[new_no - 1],
            )
        )
        old_no += 1
        new_no += 1
    assert old_no == len(old_lines) + 1
    assert new_no == len(new_lines) + 1
    changed = [row for row in expected if row[0] != "equal"]
    assert changed == [
        (
            "delete" if reverse else "insert",
            position + 1 if reverse else None,
            None if reverse else position + 1,
            sentinel if reverse else "",
            "" if reverse else sentinel,
        )
    ]

    rows = GitDiffEngine(
        algorithm=algorithm,
        ignore_space_change=True,
    ).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=source_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=source_path.name),
    )["rows"]
    actual = [
        (
            row["status"],
            row["left_no"],
            row["right_no"],
            row["left_text"],
            row["right_text"],
        )
        for row in rows
    ]
    assert actual == expected
