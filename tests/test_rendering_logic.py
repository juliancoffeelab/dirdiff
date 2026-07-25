"""Display-enrichment behavior tests.

These tests exercise `dirdiff.rendering` directly, without invoking a diff
engine or assembling an HTTP response. They verify syntax, fold, and hunk
metadata contracts against focused inputs and the existing preset corpus.
"""

from pathlib import Path
from typing import get_args

from dirdiff.rendering import SyntaxClass, highlight_lines_for_path

__all__: list[str] = []


def test_preset_highlights_use_declared_syntax_classes() -> None:
    """Require every syntax class emitted for a preset to be declared."""
    declared_classes = set(get_args(SyntaxClass.__value__))
    preset_root = Path(__file__).parent / "presets"
    preset_files = sorted(
        [
            *preset_root.glob("**/old.*"),
            *preset_root.glob("**/new.*"),
        ]
    )

    assert preset_files != []
    for path in preset_files:
        highlighted_lines = highlight_lines_for_path(
            str(path),
            path.read_text(),
        )
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
