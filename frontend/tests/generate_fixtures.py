"""Generate frontend GumTree fixtures from the repository's source presets.

From the repository root, run `uv run python frontend/tests/generate_fixtures.py`.
Each source pair runs through the real GumTree adapter and display enrichment.
Outputs contain only decorated rows, under fixtures/gumtree/<language>/<case>.json.
This script writes generated fixtures only; it does not change presets or snapshots.
"""

import json
from pathlib import Path

from dirdiff.engines import DiffSide, GumTreeDiffEngine
from dirdiff.rendering import enrich_rows_for_display

__all__: list[str] = []


def main() -> None:
    """Regenerate every GumTree source-pair fixture, propagating engine failures."""
    repository = Path(__file__).resolve().parents[2]
    presets = repository / "tests/presets/gumtree"
    destination = Path(__file__).resolve().parent / "fixtures/gumtree"
    for preset in sorted(presets.glob("*/*")):
        (left_path,) = preset.glob("old.*")
        (right_path,) = preset.glob("new.*")
        left = left_path.read_bytes().decode("utf-8")
        right = right_path.read_bytes().decode("utf-8")
        result = GumTreeDiffEngine().render_diff(
            old=DiffSide(exists=True, text=left, path_hint=left_path.name),
            new=DiffSide(exists=True, text=right, path_hint=right_path.name),
        )
        rows = enrich_rows_for_display(
            rows=result["rows"],
            left_text=left,
            right_text=right,
            left_path_hint=left_path.name,
            right_path_hint=right_path.name,
        )["rows"]
        output = destination / preset.relative_to(presets).with_suffix(".json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(output.relative_to(repository))


if __name__ == "__main__":
    main()
