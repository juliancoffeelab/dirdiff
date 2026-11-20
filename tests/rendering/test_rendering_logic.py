from pathlib import Path
from typing import get_args

import pytest

from dirdiff.engines import InlineToken
from dirdiff.formats import try_decode_text
from dirdiff.rendering import (
    SyntaxClass,
    SyntaxSpan,
    highlight_lines_for_path,
    weave_decorated_parts,
)
from dirdiff.util import Ok


def test_preset_highlights_use_declared_syntax_classes() -> None:
    declared_classes = set(get_args(SyntaxClass.__value__))
    preset_root = Path(__file__).parents[1] / "presets"
    preset_files = sorted(
        [
            *preset_root.glob("**/old.*"),
            *preset_root.glob("**/new.*"),
        ]
    )

    assert preset_files != []
    for path in preset_files:
        # Link sides compose from readlink bytes and final captured content,
        # never from filesystem-followed bytes under their `.link` suffix.
        if path.is_symlink():
            continue
        # The corpus holds image and blob fixtures too. A highlighter has
        # nothing to say about bytes, so text decoding decides what remains.
        text = try_decode_text(path.read_bytes())
        if not Ok.good(text):
            continue
        highlighted_lines = highlight_lines_for_path(str(path), text.value)
        if highlighted_lines is None:
            continue
        emitted_classes = {
            syntax_class
            for line in highlighted_lines
            for span in line
            for syntax_class in span["classes"]
        }
        assert emitted_classes <= declared_classes, (
            path,
            emitted_classes - declared_classes,
        )


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        (
            "abc",
            [{"text": "abd", "is_ws": False, "status": "replace"}],
        ),
        (
            "abc",
            [{"text": "", "is_ws": False, "status": "unchanged"}],
        ),
        (
            " ",
            [{"text": " ", "is_ws": False, "status": "unchanged"}],
        ),
    ],
)
def test_decorated_parts_reject_invalid_inline_tokens(
    text: str,
    tokens: list[InlineToken],
) -> None:
    with pytest.raises(AssertionError):
        weave_decorated_parts(text, tokens, [])


@pytest.mark.parametrize(
    "syntax",
    [
        [{"start": -1, "end": 1, "classes": ["ts-keyword"]}],
        [{"start": 1, "end": 1, "classes": ["ts-keyword"]}],
        [{"start": 0, "end": 4, "classes": ["ts-keyword"]}],
        [{"start": 0, "end": 1, "classes": []}],
        [
            {"start": 1, "end": 2, "classes": ["ts-keyword"]},
            {"start": 0, "end": 1, "classes": ["ts-string"]},
        ],
        [
            {"start": 0, "end": 2, "classes": ["ts-keyword"]},
            {"start": 1, "end": 3, "classes": ["ts-string"]},
        ],
    ],
)
def test_decorated_parts_reject_invalid_syntax_spans(
    syntax: list[SyntaxSpan],
) -> None:
    with pytest.raises(AssertionError):
        weave_decorated_parts("abc", [], syntax)
