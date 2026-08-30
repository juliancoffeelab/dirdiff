"""Check display enrichment at the neutral-row boundary.

The tests call `dirdiff.rendering` directly to verify syntax classes, decoration
weaving, and rejection of invalid engine tokens. Diff-engine behavior and HTTP
serialization remain outside this module.
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


def test_preset_highlights_use_declared_syntax_classes() -> None:
    """Check real preset captures against the complete public CSS vocabulary.

    Every configured text preset that receives highlighting contributes all
    emitted classes. The assertion prevents a bundled query from producing a
    syntactically valid but undeclared class the HUD cannot style.
    """
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
    """Reject engine tokens that cannot describe the supplied row text.

    # Parameters

    - `text`: Exact row text the generated invalid token sequence claims to cover.
    - `tokens`: Malformed partition with wrong text, emptiness, or whitespace fact.
    """
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
    """Require decoration weaving to reject malformed syntax geometry.

    The parameter cases cover empty, reversed, overlapping, and out-of-bounds
    line-local spans. None may be clipped, reordered, or silently omitted to
    make a payload appear valid.
    """
    with pytest.raises(AssertionError):
        weave_decorated_parts("abc", [], syntax)
