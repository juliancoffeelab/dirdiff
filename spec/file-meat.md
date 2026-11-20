# File meat

## File model

One manifest entry becomes one stable `FileCard` in `Husk`, `Full`, or `Lazy`
state. The article keeps the manifest file index and identity across state
replacement; header, body, hunk targets, and navigation operations change
inside that boundary. Loading and canonical backend state come from the file
lane ([`file-lane.md`](file-lane.md)). `FileCard.tsx` is the `hud/fileCard/`
facade: outside code imports only it (`local/file-card-facade` lint).

A successful backend file response becomes `FullFile` immediately. Render
admission is a separate value: before admission the card already shows its Full
header and statistics and omits the body.

Every non-terminal `FileCard` exposes at least one hunk target, carrying a file
index and a hunk index whatever its kind. A stale pseudo kind is never a
substitute for missing coordinates. Collapsing a file keeps every coordinate in
the DOM as a `skip` target. Unexpected renderer failure is terminal local
damage and is the one state that exposes none: it reports a Toast, shows an
unrecoverable strip, offers no Retry, and is not a backend `LazyFile`.

The Full header is outside `FileRendererBoundary`, so body-renderer damage
replaces only the body. File headers close only review UI anchored inside their
own outgoing DOM when a state replacement disposes them.

A Lazy header never invents values: unavailable statistics show `?`, never
zeroes.

## Bays

Bay expansion and render modes (`BayExpansion`, `BayRenderModes`) are owned by
the card, keyed by bay key, because the card outlives body collapse and
rich/virtual remounts. Expansion reads the backend's `default_expanded` until
the reviewer changes it and is dropped with the card. Line-pin navigation opens
a bay by writing that state, not by reaching a mounted bay through the DOM.

A bay collapsed by default still writes its first hunk coordinate, and that
anchor stays landable, unlike a collapsed File's skipped hunks: a change the
reviewer cannot land on is a hidden change.

Content with no rows (`image` and `blob` bays) does not participate in
rich/virtual replacement and stays mounted, so its chrome carries its single
hunk stop. An image bay hosts one review line per side and writes the same
line-host DOM contract as `TextDiffGrid`, because line pins and navigation
resolve against it. Only the one-frame, one-bay `flatfile` text shape renders
without bay chrome.

## TextDiffGrid

`TextDiffGrid` owns the row DOM and line-pin paint for one text bay. It does
not own file loading or hunk selection.

- It has one persistent root and replaces its row children atomically when a
  rendering input other than review markers changes. Review marker changes only
  update classes on mounted Comment triggers; they never replace rows, detach a
  composer anchor, or erase selected-hunk DOM.
- Every row replacement, including fold changes, first closes only the review
  composer or Thread panel whose trigger is inside that grid.
- After each render the grid reports which sides have a real line-one Comment
  trigger in mounted DOM; the File marker uses that fact and never infers
  availability from backend rows.
- `rowDom.ts` is a detached row-DOM builder with no reactivity, queries, or
  state. Its only listeners are fold toggles; it installs no review or line-pin
  listeners, retains no markers or pins, decides no view mode, and never
  fetches.

Backend row order is authoritative. Inline view may merge presented rows, but
hunk identity stays attached to the backend boundary that produced it. When the
bay's `interleave_inline_rows` is true, each changed equal row is one presented
row in the tool's own order: each shared stretch, then the left deletions, then
the right insertions. The shared text anchors the two sides and a disagreement
throws. Split view ignores the flag.

The rendering layer combines each engine token partition with the syntax spans
of the same row side, preserving every displayed character. Invalid engine
tokens or syntax spans fail at the backend boundary; the grid renders the parts
directly and never intersects ranges or slices source by backend offsets.
Syntax splitting never merges different move relationships.

Move outline geometry is rebuilt after row replacement and on grid resize; its
observer and listeners end with the grid. A move click changes no line pins or
hunk selection, and an unfinished jump is cancelled by a newer move click or
grid disposal.

## Folded lines

The backend marks hunk boundaries and accepts foldable ranges from the same
canonical engine-row change classification, so fold discovery never compares
row text independently or discards rows such as trailing blanks. A fold range
containing a hunk boundary is a backend contract violation and throws.

`TextDiffGrid` stores expanded folds locally. Expansion changes only that
grid's row DOM; it selects no hunk and neither navigates, fetches, nor changes
file collapse.

## Rich and virtual bays

Every expanded text bay alternates between rich and virtual representation,
per bay. Rich/virtual replacement changes representation only; it does not
change file state, expansion, selection, counters, or URL. Entry and exit
distances differ (hysteresis): a bay is rich before it reaches the viewport and
virtual only after it moves farther away.

- A mounting bay chooses its first representation from the stable card's
  rectangle (its own wrapper does not exist yet). Render admission mounts card
  bodies top-down, so cards above already hold their real height and a bay in
  view paints rich on its first frame.
- The exit observer suppresses virtualization while Navigation has unfinished
  commands.
- Zones are viewport multiples, so each mounted bay rebuilds its observers on
  window `resize` and on `navigation-idle`. The listener is per bay because the
  distances are the bay's own cost band. Both listeners go away when the bay
  unmounts.
- Rich to virtual: the bay is measured first and the virtual body uses that
  exact height, so the page does not jump.
- `VirtualBay` holds the complete undecorated old and new text so native browser
  search finds both sides.

## Disposal

File expansion is stored by `ChangeSet` and shared by the file list and
FileTree; state replacement creates no other expansion value.

Disposing a `FileCard` disposes its bays: each text bay disconnects its
observers and resize listener, removes the operations on its wrapper, and
clears its registered render mode. The card removes the operations `FullFile` or
`TextDiffGrid` attached and drops local fold and bay expansion state. The card
does not outlive its `ChangeSetSnapshot`; query cancellation and observer
disposal remain the lane's responsibility.

## Operations exposed to navigation

Navigation discovers these through the DOM, and each exists exactly for the
mounted lifetime of its element. File presentation never calls `selectHunk()`.

- Card: `prepareLine_impl(target, abortSignal)` expands the file and the
  target's bay, enriches that bay, unfolds the line, and returns the row or a
  precise missing/stopped result. Calling it before admission is a contract
  error.
- Text bay wrapper: `waitToEnrich_impl()` makes the bay rich and resolves once
  the rich grid exists; `intersectsRichEntryZone(viewportTop)` answers without
  changing representation.
- `TextDiffGrid`: its own `prepareLine_impl` unfolds containing folds and
  returns the exact row. It does not scroll, paint a pin, select a hunk, or
  fetch.
