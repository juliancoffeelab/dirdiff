"""Git diff-engine row projection tests.

These tests exercise the engine-facing Git projection boundary.  Git emits
unified patches with sparse hunk context, but dirdiff engine rows must describe
the full left/right source surface so the frontend can render and virtualize a
complete file.  The assertions here keep that source-preservation contract
separate from repository loading, HTTP serialization, and syntax enrichment.
"""

import pytest

from dirdiff.engines import (
    DiffSide,
    DirdiffError,
    GitWordBoundaryCustom,
    GitWordBoundaryDefault,
    GitWordBoundaryLanguage,
)
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


def test_git_word_default_boundary_splits_at_whitespace_only() -> None:
    """Without a regex Git treats every run of non-whitespace as one word."""
    engine = GitDiffEngine(
        word_diff=True, word_boundary=GitWordBoundaryDefault(mode="default")
    )
    result = engine.render_diff(
        old=DiffSide(text="x = foo.bar(1)\n", path_hint="old.py", exists=True),
        new=DiffSide(text="x = foo.baz(12)\n", path_hint="new.py", exists=True),
    )
    tokens = [
        (token["status"], token["text"])
        for row in result["rows"]
        for token in [*row.get("left_tokens", []), *row.get("right_tokens", [])]
        if token["status"] != "unchanged"
    ]
    assert tokens == [("delete", "foo.bar(1)"), ("insert", "foo.baz(12)")]


def test_git_word_custom_boundary_matches_whole_codepoints() -> None:
    """A per-character pattern splits Unicode text at codepoints, not bytes.

    Under a byte locale `.` would match one byte of `₁`, and Git would print
    token records that are not valid UTF-8. The driver pins a UTF-8 locale so
    the subscript digit is one token.
    """
    engine = GitDiffEngine(
        word_diff=True,
        word_boundary=GitWordBoundaryCustom(mode="custom", regex="."),
    )
    result = engine.render_diff(
        old=DiffSide(
            text="αβγ = δ₁ + 1  # größe\n", path_hint="a.jl", exists=True
        ),
        new=DiffSide(
            text="αβγ = δ₂ + 1  # größe\n", path_hint="b.jl", exists=True
        ),
    )
    tokens = [
        (token["status"], token["text"])
        for row in result["rows"]
        for token in [*row.get("left_tokens", []), *row.get("right_tokens", [])]
        if token["status"] != "unchanged"
    ]
    assert tokens == [("delete", "₁"), ("insert", "₂")]


def test_git_word_language_boundary_uses_python_driver_for_py_suffix() -> None:
    """A `.py` hint selects Git's Python driver, which splits identifiers at `.`.

    The same text under the default boundary is one `foo.bar(1)` word, so a
    finer split proves the attributes file reached Git. Unicode identifiers
    still survive as whole tokens through the driver's own multibyte rule.
    """
    engine = GitDiffEngine(
        word_diff=True, word_boundary=GitWordBoundaryLanguage(mode="language")
    )
    result = engine.render_diff(
        old=DiffSide(
            text="x = foo.bar(1) + größe\n", path_hint="old.py", exists=True
        ),
        new=DiffSide(
            text="x = foo.baz(12) + größe\n", path_hint="new.py", exists=True
        ),
    )
    tokens = [
        (token["status"], token["text"])
        for row in result["rows"]
        for token in [*row.get("left_tokens", []), *row.get("right_tokens", [])]
        if token["status"] != "unchanged"
    ]
    # Left tokens precede right tokens within the single changed row.
    assert tokens == [
        ("delete", "bar"),
        ("delete", "1"),
        ("insert", "baz"),
        ("insert", "12"),
    ]


def test_git_word_language_boundary_ignores_unknown_suffix() -> None:
    """A suffix with no driver keeps Git's whitespace boundary."""
    engine = GitDiffEngine(
        word_diff=True, word_boundary=GitWordBoundaryLanguage(mode="language")
    )
    result = engine.render_diff(
        old=DiffSide(text="x = foo.bar(1)\n", path_hint="old.zzz", exists=True),
        new=DiffSide(
            text="x = foo.baz(12)\n", path_hint="new.zzz", exists=True
        ),
    )
    tokens = [
        (token["status"], token["text"])
        for row in result["rows"]
        for token in [*row.get("left_tokens", []), *row.get("right_tokens", [])]
        if token["status"] != "unchanged"
    ]
    assert tokens == [("delete", "foo.bar(1)"), ("insert", "foo.baz(12)")]


def test_git_word_custom_boundary_without_pattern_passes_no_regex() -> None:
    """Custom with no entered pattern behaves like the default boundary.

    `None` must not become a `--word-diff-regex` argument on its way to Git;
    the flag is omitted and whitespace splitting applies.
    """
    engine = GitDiffEngine(
        word_diff=True,
        word_boundary=GitWordBoundaryCustom(mode="custom", regex=None),
    )
    result = engine.render_diff(
        old=DiffSide(text="x = foo.bar(1)\n", path_hint="old.py", exists=True),
        new=DiffSide(text="x = foo.baz(12)\n", path_hint="new.py", exists=True),
    )
    tokens = [
        (token["status"], token["text"])
        for row in result["rows"]
        for token in [*row.get("left_tokens", []), *row.get("right_tokens", [])]
        if token["status"] != "unchanged"
    ]
    assert tokens == [("delete", "foo.bar(1)"), ("insert", "foo.baz(12)")]


@pytest.mark.parametrize("regex", ["(", "[[:alnum:]"])
def test_git_word_custom_boundary_surfaces_git_regex_rejection(
    regex: str,
) -> None:
    """Git's own rejection of a malformed pattern reaches the caller."""
    engine = GitDiffEngine(
        word_diff=True,
        word_boundary=GitWordBoundaryCustom(mode="custom", regex=regex),
    )
    with pytest.raises(DirdiffError, match="invalid regular expression"):
        engine.render_diff(
            old=DiffSide(text="a\n", path_hint="old.txt", exists=True),
            new=DiffSide(text="b\n", path_hint="new.txt", exists=True),
        )


def test_git_word_rows_place_fused_lines_on_their_first_source_line() -> None:
    """A row fusing several source lines reports the line its text starts on.

    Git's porcelain keeps only the new side's newlines inside unchanged text,
    so old lines 1 and 2 fuse into one row while the numbering still follows
    each side's own source.
    """
    engine = GitDiffEngine(word_diff=True)
    result = engine.render_diff(
        old=DiffSide(
            text="alpha beta\ngamma\n\ndelta\n",
            path_hint="old.txt",
            exists=True,
        ),
        new=DiffSide(
            text="alpha beta gamma\n\ndelta epsilon\nzeta\n",
            path_hint="new.txt",
            exists=True,
        ),
    )
    assert [
        (row["status"], row["left_no"], row["right_no"], row["left_text"])
        for row in result["rows"]
    ] == [
        ("equal", 1, 1, "alpha beta gamma"),
        ("equal", 3, 2, ""),
        ("equal", 4, 3, "delta "),
        ("insert", None, 4, ""),
    ]


@pytest.mark.parametrize(
    "word_boundary",
    [
        GitWordBoundaryDefault(mode="default"),
        GitWordBoundaryLanguage(mode="language"),
        GitWordBoundaryCustom(
            mode="custom", regex="[[:alnum:]_]+|[^[:space:]]"
        ),
    ],
    ids=["default", "language", "custom"],
)
def test_git_word_rows_round_trip_source_under_every_boundary(
    word_boundary: GitWordBoundaryDefault
    | GitWordBoundaryLanguage
    | GitWordBoundaryCustom,
) -> None:
    """Row text reproduces each side's non-whitespace source in order.

    Whatever boundary Git split words at, concatenating a side's row text
    yields the source's non-whitespace characters, and every row's number is
    the source line holding its first non-whitespace character. This is the
    property the engine trusts Git for instead of checking per request.
    """
    old_text = (
        "def total(items):\n"
        "    return sum(item.price * item.qty\n"
        "               for item in items)\n"
        "\n"
        "größe = total([])  # αβγ\n"
    )
    new_text = (
        "def total(items, tax):\n"
        "    subtotal = sum(item.price * item.qty for item in items)\n"
        "    return subtotal * (1 + tax)\n"
        "\n"
        "größe = total([], 0.2)  # αβγ δ\n"
    )
    engine = GitDiffEngine(word_diff=True, word_boundary=word_boundary)
    result = engine.render_diff(
        old=DiffSide(text=old_text, path_hint="old.py", exists=True),
        new=DiffSide(text=new_text, path_hint="new.py", exists=True),
    )
    assert any(
        token["status"] != "unchanged"
        for row in result["rows"]
        for token in [*row.get("left_tokens", []), *row.get("right_tokens", [])]
    )
    for text, side_rows in (
        (
            old_text,
            [(row["left_no"], row["left_text"]) for row in result["rows"]],
        ),
        (
            new_text,
            [(row["right_no"], row["right_text"]) for row in result["rows"]],
        ),
    ):
        source_chars = [
            (char, line_no)
            for line_no, line in enumerate(text.splitlines(), start=1)
            for char in line
            if not char.isspace()
        ]
        consumed = 0
        for row_no, row_text in side_rows:
            row_chars = [char for char in row_text if not char.isspace()]
            if row_chars == []:
                continue
            assert row_no == source_chars[consumed][1]
            assert row_chars == [
                char
                for char, _ in source_chars[
                    consumed : consumed + len(row_chars)
                ]
            ]
            consumed += len(row_chars)
        assert consumed == len(source_chars)
