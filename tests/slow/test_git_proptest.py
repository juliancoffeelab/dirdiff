import re
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dirdiff.engines import (
    DiffEngineResult,
    DiffSide,
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    GitDiffEngine,
    InlineTokenStatus,
    UnpairedMove,
    git_executable,
)
from dirdiff.rendering import enrich_rows_for_display

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "git"

PRESETS = sorted(PRESETS_ROOT.glob("*/*"))

WORD_DIFF_PRESETS = sorted(
    [*PRESETS, *(Path(__file__).parents[1] / "presets" / "diff").glob("*/*")]
)


def git_word_diff_body_from_result(result: DiffEngineResult) -> str:
    body: list[str] = []
    continuing_status: InlineTokenStatus | None = None
    for row in result["rows"]:
        outside_hunk = (
            row["status"] == "equal"
            and row["left_no"] is not None
            and row["right_no"] is not None
            and row["left_tokens"] == []
            and row["right_tokens"] == []
        )
        if outside_hunk:
            continuing_status = None
            continue

        left_words: list[tuple[str, str, InlineTokenStatus]] = []
        right_words: list[tuple[str, str, InlineTokenStatus]] = []
        left_trailing = ""
        right_trailing = ""
        for token in row["left_tokens"]:
            if token["is_ws"]:
                left_trailing += token["text"]
            else:
                left_words.append(
                    (left_trailing, token["text"], token["status"])
                )
                left_trailing = ""
        for token in row["right_tokens"]:
            if token["is_ws"]:
                right_trailing += token["text"]
            else:
                right_words.append(
                    (right_trailing, token["text"], token["status"])
                )
                right_trailing = ""

        pieces: list[tuple[InlineTokenStatus, str]] = []
        left_index = 0
        right_index = 0
        last_word_status: InlineTokenStatus | None = None
        while left_index < len(left_words) or right_index < len(right_words):
            left_unchanged = next(
                (
                    index
                    for index in range(left_index, len(left_words))
                    if left_words[index][2] == "unchanged"
                ),
                len(left_words),
            )
            right_unchanged = next(
                (
                    index
                    for index in range(right_index, len(right_words))
                    if right_words[index][2] == "unchanged"
                ),
                len(right_words),
            )
            deleted = left_words[left_index:left_unchanged]
            inserted = right_words[right_index:right_unchanged]
            if inserted != []:
                prefix_status = (
                    continuing_status
                    if pieces == []
                    and continuing_status in {"delete", "insert"}
                    else "unchanged"
                )
                pieces.append((prefix_status, inserted[0][0]))
            elif deleted != [] and row["right_no"] is None:
                prefix_status = (
                    "delete"
                    if pieces == [] and continuing_status == "delete"
                    else "unchanged"
                )
                pieces.append((prefix_status, deleted[0][0]))
            if deleted != []:
                pieces.append(
                    (
                        "delete",
                        deleted[0][1]
                        + "".join(
                            prefix + word for prefix, word, _ in deleted[1:]
                        ),
                    )
                )
                last_word_status = "delete"
            if inserted != []:
                pieces.append(
                    (
                        "insert",
                        inserted[0][1]
                        + "".join(
                            prefix + word for prefix, word, _ in inserted[1:]
                        ),
                    )
                )
                last_word_status = "insert"
            left_index = left_unchanged
            right_index = right_unchanged
            if left_index == len(left_words) or right_index == len(right_words):
                break
            left_word = left_words[left_index]
            right_word = right_words[right_index]
            assert left_word[1] == right_word[1]
            pieces.append(("unchanged", right_word[0] + right_word[1]))
            left_index += 1
            right_index += 1
            last_word_status = "unchanged"

        compact_pieces: list[tuple[InlineTokenStatus, str]] = []
        for status, text in pieces:
            if text == "":
                continue
            if compact_pieces != [] and compact_pieces[-1][0] == status:
                compact_pieces[-1] = (status, compact_pieces[-1][1] + text)
            else:
                compact_pieces.append((status, text))
        for status, text in compact_pieces:
            match status:
                case "delete":
                    body.append(f"\x1b[38;5;31m{text}\x1b[m")
                case "insert":
                    body.append(f"\x1b[38;5;32m{text}\x1b[m")
                case "unchanged":
                    body.append(text)
                case _:
                    raise AssertionError(status)
        body.append(
            right_trailing if row["right_no"] is not None else left_trailing
        )
        body.append("\n")
        continuing_status = (
            last_word_status
            if last_word_status in {"delete", "insert"}
            else None
        )
    return "".join(body)


@pytest.mark.parametrize(
    "algorithm", ["myers", "minimal", "patience", "histogram"]
)
@pytest.mark.parametrize("preset", PRESETS, ids=str)
def test_git_preset_diff_replays_both_directions(
    preset: Path,
    algorithm: GitAlgorithm,
) -> None:
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

    operations: list[tuple[str, str, str]] = []
    for row in rows:
        if row["status"] == "equal":
            assert row["left_no"] is not None
            assert row["right_no"] is not None
            assert row["left_text"] == row["right_text"]
            operations.append(("keep", row["left_text"], row["right_text"]))
        elif row["status"] == "delete" or (
            row["status"] == "move" and row["left_no"] is not None
        ):
            assert row["left_no"] is not None
            assert row["right_no"] is None
            operations.append(("remove", row["left_text"], ""))
        elif row["status"] == "insert" or (
            row["status"] == "move" and row["right_no"] is not None
        ):
            assert row["left_no"] is None
            assert row["right_no"] is not None
            operations.append(("insert", "", row["right_text"]))
        else:
            raise AssertionError(row)

    old_lines = old_text.splitlines()
    forward_cursor = 0
    replayed_new: list[str] = []
    for operation, left_text, right_text in operations:
        if operation == "insert":
            replayed_new.append(right_text)
            continue
        assert old_lines[forward_cursor] == left_text
        forward_cursor += 1
        if operation == "keep":
            replayed_new.append(right_text)
        else:
            assert operation == "remove"

    assert forward_cursor == len(old_lines)
    assert "\n".join(replayed_new) + "\n" == new_text

    new_lines = new_text.splitlines()
    reverse_cursor = 0
    replayed_old: list[str] = []
    for operation, left_text, right_text in operations:
        if operation == "remove":
            replayed_old.append(left_text)
            continue
        assert new_lines[reverse_cursor] == right_text
        reverse_cursor += 1
        if operation == "keep":
            replayed_old.append(left_text)
        else:
            assert operation == "insert"

    assert reverse_cursor == len(new_lines)
    assert "\n".join(replayed_old) + "\n" == old_text


@pytest.mark.parametrize(
    "algorithm", ["myers", "minimal", "patience", "histogram"]
)
@pytest.mark.parametrize("preset", PRESETS, ids=str)
def test_git_preset_word_diff_replays_both_directions(
    preset: Path,
    algorithm: GitAlgorithm,
) -> None:
    old_path = next(preset.glob("old.*"))
    new_path = next(preset.glob("new.*"))
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    assert old_text.endswith("\n")
    assert new_text.endswith("\n")
    rows = GitDiffEngine(algorithm=algorithm, word_diff=True).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=old_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=new_path.name),
    )["rows"]
    display = enrich_rows_for_display(
        rows=rows,
        left_text=old_text,
        right_text=new_text,
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )
    assert len(display["rows"]) == len(rows)
    for row, display_row in zip(rows, display["rows"], strict=True):
        assert (
            "".join(part["text"] for part in display_row["left_parts"])
            == row["left_text"]
        )
        assert (
            "".join(part["text"] for part in display_row["right_parts"])
            == row["right_text"]
        )

    actual_left_words: list[str] = []
    actual_right_words: list[str] = []
    for row in rows:
        if row["left_tokens"] != []:
            actual_left_words.extend(
                word
                for token in row["left_tokens"]
                for word in token["text"].split()
            )
        elif row["left_no"] is not None:
            actual_left_words.extend(row["left_text"].split())
        if row["right_tokens"] != []:
            actual_right_words.extend(
                word
                for token in row["right_tokens"]
                for word in token["text"].split()
            )
        elif row["right_no"] is not None:
            actual_right_words.extend(row["right_text"].split())

    assert actual_left_words == old_text.split()
    assert actual_right_words == new_text.split()


@pytest.mark.parametrize("preset", WORD_DIFF_PRESETS, ids=str)
def test_git_preset_word_diff_round_trips_color_body(
    preset: Path,
    tmp_path: Path,
) -> None:
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
            "color.diff.meta=normal",
            "-c",
            "color.diff.frag=36",
            "-c",
            "color.diff.func=normal",
            "-c",
            "color.diff.context=normal",
            "-c",
            "color.diff.old=31",
            "-c",
            "color.diff.new=32",
            "-c",
            "color.diff.whitespace=normal",
            "-c",
            "diff.suppressBlankEmpty=false",
            "diff",
            "--no-index",
            "--diff-algorithm=myers",
            "--word-diff=color",
            "--word-diff-regex=[^[:space:]]+",
            "--color=always",
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

    expected_body: list[str] = []
    in_hunk = False
    for line in completed.stdout.decode().splitlines(keepends=True):
        plain_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if plain_line.startswith("@@ "):
            in_hunk = True
            continue
        if in_hunk:
            expected_body.append(line)

    result = GitDiffEngine(word_diff=True).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=old_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=new_path.name),
    )
    assert git_word_diff_body_from_result(result) == "".join(expected_body)


@pytest.mark.parametrize(
    "algorithm", ["myers", "minimal", "patience", "histogram"]
)
@pytest.mark.parametrize("preset", PRESETS, ids=str)
def test_git_preset_word_diff_row_status_preserves_complete_token_facts(
    preset: Path,
    algorithm: GitAlgorithm,
) -> None:
    old_path = next(preset.glob("old.*"))
    new_path = next(preset.glob("new.*"))
    result = GitDiffEngine(algorithm=algorithm, word_diff=True).render_diff(
        old=DiffSide(
            exists=True,
            text=old_path.read_text(),
            path_hint=old_path.name,
        ),
        new=DiffSide(
            exists=True,
            text=new_path.read_text(),
            path_hint=new_path.name,
        ),
    )
    rows = result["rows"]

    expected: list[str] = []
    for row in rows:
        statuses = [
            token["status"]
            for token in [*row["left_tokens"], *row["right_tokens"]]
        ]
        if statuses != [] and all(status == "insert" for status in statuses):
            expected_status = "insert"
        elif statuses != [] and all(status == "delete" for status in statuses):
            expected_status = "delete"
        else:
            expected_status = "equal"
        expected.append(expected_status)

    actual = [row["status"] for row in rows]

    assert actual == expected, preset
    added = expected.count("insert")
    removed = expected.count("delete")
    modified = sum(
        1
        for status, row in zip(expected, rows, strict=True)
        if status == "equal"
        and any(
            token["status"] != "unchanged"
            for token in [*row["left_tokens"], *row["right_tokens"]]
        )
    )
    assert result["summary"] == {
        "changed_lines": added + removed + modified,
        "modified_lines": modified,
        "added_lines": added,
        "removed_lines": removed,
        "moved_lines": 0,
    }


@pytest.mark.parametrize(
    "algorithm", ["myers", "minimal", "patience", "histogram"]
)
@pytest.mark.parametrize("preset", PRESETS, ids=str)
def test_git_preset_line_alignment_matches_git_patch(
    preset: Path,
    algorithm: GitAlgorithm,
    tmp_path: Path,
) -> None:
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
    # Stage 0: generate ignored whitespace changes plus one inserted line.
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


@pytest.mark.parametrize("source_side", ["old", "new"])
@pytest.mark.parametrize("preset", PRESETS, ids=str)
@given(
    data=st.data(),
    line_position=st.integers(min_value=0),
    character_position=st.integers(min_value=0),
    reverse=st.booleans(),
    algorithm=st.sampled_from(("myers", "minimal", "patience", "histogram")),
)
@settings(max_examples=20, deadline=None)
def test_git_preset_word_diff_ignore_space_change_replays_whitespace_mutation(
    preset: Path,
    source_side: str,
    data: st.DataObject,
    line_position: int,
    character_position: int,
    reverse: bool,
    algorithm: GitAlgorithm,
) -> None:
    # Stage 0: generate ignored whitespace changes plus one real word edit.
    source_path = next(preset.glob(f"{source_side}.*"))
    source_text = source_path.read_text()
    assert source_text.endswith("\n")
    source_lines = source_text.splitlines()
    assert source_lines != []
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

    sentinel = "__DIRDIFF_WORD_PROPERTY_EDIT__"
    assert sentinel not in source_text
    target_line = line_position % len(mutated_lines)
    target_character = character_position % (
        len(mutated_lines[target_line]) + 1
    )
    mutated_lines[target_line] = (
        mutated_lines[target_line][:target_character]
        + sentinel
        + mutated_lines[target_line][target_character:]
    )
    mutated_text = "\n".join(mutated_lines) + "\n"
    if reverse:
        old_text, new_text = mutated_text, source_text
    else:
        old_text, new_text = source_text, mutated_text

    rows = GitDiffEngine(
        algorithm=algorithm,
        ignore_space_change=True,
        word_diff=True,
    ).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=source_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=source_path.name),
    )["rows"]

    actual_left_words: list[str] = []
    actual_right_words: list[str] = []
    for row in rows:
        if row["left_tokens"] != []:
            actual_left_words.extend(
                word
                for token in row["left_tokens"]
                for word in token["text"].split()
            )
        elif row["left_no"] is not None:
            actual_left_words.extend(row["left_text"].split())
        if row["right_tokens"] != []:
            actual_right_words.extend(
                word
                for token in row["right_tokens"]
                for word in token["text"].split()
            )
        elif row["right_no"] is not None:
            actual_right_words.extend(row["right_text"].split())

    assert actual_left_words == old_text.split()
    assert actual_right_words == new_text.split()


@pytest.mark.parametrize("source_side", ["old", "new"])
@pytest.mark.parametrize("preset", PRESETS, ids=str)
@given(
    data=st.data(),
    line_position=st.integers(min_value=0),
    character_position=st.integers(min_value=0),
    reverse=st.booleans(),
    algorithm=st.sampled_from(("myers", "minimal", "patience", "histogram")),
)
@settings(max_examples=20, deadline=None)
def test_git_preset_word_diff_ignore_space_change_reproduces_color_word_stream(
    preset: Path,
    source_side: str,
    data: st.DataObject,
    line_position: int,
    character_position: int,
    reverse: bool,
    algorithm: GitAlgorithm,
) -> None:
    # Stage 0: generate ignored whitespace changes plus one real word edit.
    source_path = next(preset.glob(f"{source_side}.*"))
    source_text = source_path.read_text()
    assert source_text.endswith("\n")
    source_lines = source_text.splitlines()
    assert source_lines != []
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

    sentinel = "__DIRDIFF_WORD_PROPERTY_EDIT__"
    assert sentinel not in source_text
    target_line = line_position % len(mutated_lines)
    target_character = character_position % (
        len(mutated_lines[target_line]) + 1
    )
    mutated_lines[target_line] = (
        mutated_lines[target_line][:target_character]
        + sentinel
        + mutated_lines[target_line][target_character:]
    )
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
                "color.diff.meta=normal",
                "-c",
                "color.diff.frag=36",
                "-c",
                "color.diff.func=normal",
                "-c",
                "color.diff.context=normal",
                "-c",
                "color.diff.old=31",
                "-c",
                "color.diff.new=32",
                "-c",
                "color.diff.whitespace=normal",
                "-c",
                "diff.suppressBlankEmpty=false",
                "diff",
                "--no-index",
                f"--diff-algorithm={algorithm}",
                "--ignore-space-change",
                "--word-diff=color",
                "--word-diff-regex=[^[:space:]]+",
                "--color=always",
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

    expected_events: list[tuple[str, InlineTokenStatus]] = []
    in_hunk = False
    for raw_line in completed.stdout.decode().splitlines():
        plain_line = re.sub(r"\x1b\[[0-9;]*m", "", raw_line)
        if plain_line.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        status: InlineTokenStatus = "unchanged"
        for part in re.split(r"(\x1b\[[0-9;]*m)", raw_line):
            if part.startswith("\x1b[") is True:
                final_color = part[2:-1].split(";")[-1]
                match final_color:
                    case "31":
                        status = "delete"
                    case "32":
                        status = "insert"
                    case _:
                        status = "unchanged"
                continue
            expected_events.extend(
                (word, status) for word in re.findall(r"\S+", part)
            )

    rows = GitDiffEngine(
        algorithm=algorithm,
        ignore_space_change=True,
        word_diff=True,
    ).render_diff(
        old=DiffSide(exists=True, text=old_text, path_hint=source_path.name),
        new=DiffSide(exists=True, text=new_text, path_hint=source_path.name),
    )["rows"]
    left_pending: list[list[tuple[str, InlineTokenStatus]]] = []
    right_pending: list[list[tuple[str, InlineTokenStatus]]] = []
    actual_events: list[tuple[str, InlineTokenStatus]] = []
    for row in rows:
        left_words = [
            (word, token["status"])
            for token in row["left_tokens"]
            if not token["is_ws"]
            for word in re.findall(r"\S+", token["text"])
        ]
        right_words = [
            (word, token["status"])
            for token in row["right_tokens"]
            if not token["is_ws"]
            for word in re.findall(r"\S+", token["text"])
        ]
        if left_words != []:
            left_pending.append(left_words)
        if right_words != []:
            right_pending.append(right_words)
        while True:
            while left_pending != [] and left_pending[0] == []:
                left_pending.pop(0)
            while right_pending != [] and right_pending[0] == []:
                right_pending.pop(0)
            left_word = None if left_pending == [] else left_pending[0][0]
            right_word = None if right_pending == [] else right_pending[0][0]
            if (
                left_word is not None
                and right_word is not None
                and left_word[1] == "unchanged"
                and right_word[1] == "unchanged"
            ):
                assert left_word == right_word
                actual_events.append(left_word)
                left_pending[0].pop(0)
                right_pending[0].pop(0)
            elif left_word is not None and left_word[1] == "delete":
                actual_events.append(left_word)
                left_pending[0].pop(0)
            elif right_word is not None and right_word[1] == "insert":
                actual_events.append(right_word)
                right_pending[0].pop(0)
            else:
                break
        assert len(left_pending) <= 1
        assert len(right_pending) <= 1
    while left_pending != [] and left_pending[0] == []:
        left_pending.pop(0)
    while right_pending != [] and right_pending[0] == []:
        right_pending.pop(0)
    assert left_pending == []
    assert right_pending == []

    expected: list[tuple[tuple[str, ...], tuple[str, ...], str | None]] = []
    deleted: list[str] = []
    inserted: list[str] = []
    for word, status in expected_events:
        match status:
            case "delete":
                deleted.append(word)
            case "insert":
                inserted.append(word)
            case "unchanged":
                expected.append((tuple(deleted), tuple(inserted), word))
                deleted = []
                inserted = []
            case _:
                raise AssertionError(status)
    expected.append((tuple(deleted), tuple(inserted), None))

    actual: list[tuple[tuple[str, ...], tuple[str, ...], str | None]] = []
    deleted = []
    inserted = []
    for word, status in actual_events:
        match status:
            case "delete":
                deleted.append(word)
            case "insert":
                inserted.append(word)
            case "unchanged":
                actual.append((tuple(deleted), tuple(inserted), word))
                deleted = []
                inserted = []
            case _:
                raise AssertionError(status)
    actual.append((tuple(deleted), tuple(inserted), None))

    assert actual == expected
