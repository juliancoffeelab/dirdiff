"""Property checks for diff and syntax display enrichment.

These tests exercise broad invariants rather than individual examples. Generated
partitions verify that decoration weaving preserves both independent inputs,
while the preset corpus verifies that native engine rows and real syntax
highlighters remain compatible at the rendering boundary.
"""

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from dirdiff.engines import (
    DiffSide,
    InlineToken,
    InlineTokenStatus,
    TextDiffEngine,
    text_diff_summary,
)
from dirdiff.formats import text_content_or_none
from dirdiff.rendering import (
    SyntaxClass,
    SyntaxSpan,
    enrich_rows_for_display,
    weave_decorated_parts,
)

__all__: list[str] = []


@given(
    data=st.data(),
    text=st.text(min_size=1, max_size=60),
)
def test_decorated_parts_preserve_diff_and_syntax_per_character(
    data: st.DataObject,
    text: str,
) -> None:
    """Recover either input decoration after discarding the other one."""
    token_cut_points = (
        data.draw(
            st.lists(
                st.integers(min_value=1, max_value=len(text) - 1),
                unique=True,
            ),
            label="token cut points",
        )
        if len(text) > 1
        else []
    )
    token_boundaries = [0, *sorted(token_cut_points), len(text)]
    status_choices: list[InlineTokenStatus] = [
        "unchanged",
        "replace",
        "insert",
        "delete",
        "move",
    ]
    statuses = data.draw(
        st.lists(
            st.sampled_from(status_choices),
            min_size=len(token_boundaries) - 1,
            max_size=len(token_boundaries) - 1,
        ),
        label="diff statuses",
    )
    tokens: list[InlineToken] = []
    for token_index in range(len(token_boundaries) - 1):
        token_text = text[
            token_boundaries[token_index] : token_boundaries[token_index + 1]
        ]
        tokens.append(
            {
                "text": token_text,
                "is_ws": token_text.isspace(),
                "status": statuses[token_index],
            }
        )

    syntax_cut_points = (
        data.draw(
            st.lists(
                st.integers(min_value=1, max_value=len(text) - 1),
                unique=True,
            ),
            label="syntax cut points",
        )
        if len(text) > 1
        else []
    )
    syntax_boundaries = [0, *sorted(syntax_cut_points), len(text)]
    decorated_segments = data.draw(
        st.lists(
            st.booleans(),
            min_size=len(syntax_boundaries) - 1,
            max_size=len(syntax_boundaries) - 1,
        ),
        label="decorated syntax segments",
    )
    syntax_class_choices: list[SyntaxClass] = [
        "ts-keyword",
        "ts-string",
        "ts-function",
    ]
    syntax: list[SyntaxSpan] = []
    for syntax_index, decorated in enumerate(decorated_segments):
        if not decorated:
            continue
        syntax.append(
            {
                "start": syntax_boundaries[syntax_index],
                "end": syntax_boundaries[syntax_index + 1],
                "classes": data.draw(
                    st.lists(
                        st.sampled_from(syntax_class_choices),
                        min_size=1,
                        max_size=len(syntax_class_choices),
                        unique=True,
                    ),
                    label=f"syntax classes {syntax_index}",
                ),
            }
        )

    parts = weave_decorated_parts(text, tokens, syntax)

    assert "".join(part["text"] for part in parts) == text
    expected_diff: list[tuple[InlineTokenStatus, bool, bool]] = []
    for token_index, token in enumerate(tokens):
        expected_diff.extend(
            [
                (
                    token["status"],
                    token["is_ws"],
                    token_index == 0 and token["is_ws"],
                )
            ]
            * len(token["text"])
        )
    actual_diff: list[tuple[InlineTokenStatus, bool, bool]] = []
    for part in parts:
        actual_diff.extend(
            [
                (
                    part["diff_status"],
                    part["is_whitespace"],
                    part["is_leading_whitespace"],
                )
            ]
            * len(part["text"])
        )
    assert actual_diff == expected_diff

    expected_syntax: list[tuple[SyntaxClass, ...]] = [() for _ in text]
    for span in syntax:
        for character_index in range(span["start"], span["end"]):
            expected_syntax[character_index] = tuple(span["classes"])
    actual_syntax: list[tuple[SyntaxClass, ...]] = []
    for part in parts:
        actual_syntax.extend(
            [tuple(part["syntax_classes"])] * len(part["text"])
        )
    assert actual_syntax == expected_syntax


def test_native_engine_and_highlighter_weave_every_preset_pair() -> None:
    """Weave native diff tokens and syntax for every real preset pair."""
    preset_root = Path(__file__).parents[1] / "presets"
    old_paths = sorted(preset_root.glob("**/old.*"))
    assert old_paths != []

    rendered_pairs = 0
    for old_path in old_paths:
        new_candidates = list(old_path.parent.glob("new.*"))
        if len(new_candidates) != 1:
            continue
        new_path = new_candidates[0]
        # The corpus holds image and blob fixtures too, and the text engine
        # is not what composes those. The same rule composition classifies by
        # decides which pairs this walk can render.
        old_text = text_content_or_none(old_path.read_bytes())
        new_text = text_content_or_none(new_path.read_bytes())
        if old_text is None or new_text is None:
            continue
        rendered = TextDiffEngine().render_diff(
            old=DiffSide(
                exists=True,
                text=old_text,
                path_hint=str(old_path),
            ),
            new=DiffSide(
                exists=True,
                text=new_text,
                path_hint=str(new_path),
            ),
        )
        display = enrich_rows_for_display(
            rows=[dict(row) for row in rendered["rows"]],
            left_text=old_text,
            right_text=new_text,
            left_path_hint=str(old_path),
            right_path_hint=str(new_path),
        )

        assert len(display["rows"]) == len(rendered["rows"])
        for engine_row, display_row in zip(
            rendered["rows"], display["rows"], strict=True
        ):
            assert "".join(
                part["text"] for part in display_row["left_parts"]
            ) == (
                ""
                if engine_row["left_text"] is None
                else engine_row["left_text"]
            )
            assert "".join(
                part["text"] for part in display_row["right_parts"]
            ) == (
                ""
                if engine_row["right_text"] is None
                else engine_row["right_text"]
            )
        rendered_pairs += 1

    assert rendered_pairs > 0


def test_text_diff_summary_matches_render_diff_counts() -> None:
    """The token-free summary path must count exactly as render_diff does."""
    cases = [
        # whitespace-only change: equal-status paired row that still counts
        ("    indented\nsame\n", "  indented\nsame\n"),
        # aligned replace plus surrounding inserts/deletes
        (
            "alpha one\nremoved line\nshared\n",
            "alpha two\nshared\nadded line\n",
        ),
        # unalignable blocks fall apart into inserts and deletes
        ("aaaa\nbbbb\n", "zzzz\nyyyy\nxxxx\n"),
        # one side absent
        ("", "only right\nlines here\n"),
        ("only left\n", ""),
        # identical inputs
        ("same\ntext\n", "same\ntext\n"),
        # giant single-line surfaces (the notebook secondary shape)
        ("x" * 50_000 + "\n", "y" * 50_000 + "\n"),
    ]
    for left_text, right_text in cases:
        rendered = TextDiffEngine().render_diff(
            old=DiffSide(exists=True, text=left_text, path_hint=None),
            new=DiffSide(exists=True, text=right_text, path_hint=None),
        )
        assert (
            text_diff_summary(left_text, right_text) == rendered["summary"]
        ), (
            left_text[:40],
            right_text[:40],
        )
