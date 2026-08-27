"""Display-enrichment behavior tests.

These tests exercise `dirdiff.rendering` directly, without invoking a diff
engine or assembling an HTTP response. They verify focused syntax, decoration,
and engine-row boundary contracts.
"""

from pathlib import Path
from typing import get_args

import pytest

from dirdiff.engines import InlineToken
from dirdiff.formats import TextRejection, try_decode_text
from dirdiff.rendering import (
    SyntaxClass,
    SyntaxSpan,
    highlight_lines_for_path,
    weave_decorated_parts,
)

__all__: list[str] = []


def test_preset_highlights_use_declared_syntax_classes() -> None:
    """Require every syntax class emitted for a preset to be declared."""
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
        # The corpus holds image and blob fixtures too. A highlighter has
        # nothing to say about bytes, so the same rule composition classifies
        # by decides what this walk can read.
        text = try_decode_text(path.read_bytes())
        if isinstance(text, TextRejection):
            continue
        highlighted_lines = highlight_lines_for_path(str(path), text)
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
    """Reject engine tokens that cannot describe the supplied row text."""
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
    """Reject syntax spans that do not form valid ordered source ranges."""
    with pytest.raises(AssertionError):
        weave_decorated_parts("abc", [], syntax)
