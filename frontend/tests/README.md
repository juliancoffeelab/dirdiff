# Generated frontend fixtures

From the repository root:

```sh
uv --no-cache run python frontend/tests/generate_fixtures.py
```

The script reads every `tests/presets/gumtree/<language>/<case>/old.*` and
`new.*` pair, runs the real GumTree adapter, and applies backend display
enrichment. It writes the decorated rows to
`frontend/tests/fixtures/gumtree/<language>/<case>.json`. Names mirror source
presets; no HTTP envelope, handwritten token facts, or server response mocks
are added. GumTree must be on PATH, in `../gumtree`, or configured through
`DIRDIFF_GUMTREE_BIN`.

Run the enclosure properties with:

```sh
bun run --cwd frontend test GumTreeMoves
```

The expected enclosed characters are those carrying each move relationship in
the generated backend data, including edited or inserted descendants. Other
move identities and text with no relationship form holes in that enclosure.
Tests run the real grid and pointer handlers in both layouts, with original
parts and parts split into individual Unicode characters. Hover must change
only emphasis, leaving coverage and holes unchanged on both sides. Parent
hover emphasizes all descendants contained on the hovered side and their
counterparts; hovering a child does not select its parent.

The tests stub unrelated review bindings and supply deterministic character
geometry because jsdom does not lay out text. Actual SVG outlines, wrapping,
and colors also require browser verification.
