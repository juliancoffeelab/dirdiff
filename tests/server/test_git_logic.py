import pytest

from dirdiff.engines import (
    DiffSide,
    DirdiffError,
    GitWordBoundaryCustom,
    GitWordBoundaryDefault,
    GitWordBoundaryLanguage,
)
from dirdiff.engines.git import GitDiffEngine


def test_git_word_custom_boundary_matches_whole_codepoints() -> None:
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


@pytest.mark.parametrize("regex", ["(", "[[:alnum:]"])
def test_git_word_custom_boundary_surfaces_git_regex_rejection(
    regex: str,
) -> None:
    engine = GitDiffEngine(
        word_diff=True,
        word_boundary=GitWordBoundaryCustom(mode="custom", regex=regex),
    )
    with pytest.raises(DirdiffError, match="invalid regular expression"):
        engine.render_diff(
            old=DiffSide(text="a\n", path_hint="old.txt", exists=True),
            new=DiffSide(text="b\n", path_hint="new.txt", exists=True),
        )


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
