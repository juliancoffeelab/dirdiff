# File meat

## File model

One manifest entry becomes one stable `FileCard` in `Husk`, `Full`, or `Lazy`
state. The article keeps the manifest file index and identity across state
replacement; header, body, hunk targets, and navigation operations change
inside that boundary. Loading and canonical backend state come from the file
lane ([`file-lane.md`](file-lane.md)); the card does not observe queries or
perform HTTP operations.

A successful backend file response becomes `FullFile` immediately. Render
admission is a separate value: before admission the card already shows its Full
header and statistics and omits the body.

Every non-terminal `FileCard` exposes at least one hunk target. Unexpected
renderer failure is terminal local damage and is the one state that exposes
none.

## Hunk targets

Every hunk target carries a file index and a hunk index, whatever its kind
(`husk`, `lazy`, `zero`, `skip`, `real`). A stale pseudo kind is never a
substitute for missing coordinates.

- A Husk exposes `husk`/`0`. An explicitly collapsed Husk keeps it and gains
  `.skip`.
- A Lazy exposes `lazy`/`0`; collapsing keeps the kind and coordinates and adds
  `.skip`.
- An expanded nonzero-hunk Full file before admission exposes one Husk target
  at hunk index zero; admission replaces it with the real hunk body.
- A zero-hunk Full file (such as an empty added or removed text File) exposes
  one `zero` target permanently, in the header; its single bay has no rows and
  no stop.
- Collapsing a nonzero Full file keeps every real coordinate in the DOM as an
  invisible `skip` target, admitted or not.

Hunk coordinates are bay-local backend coordinates, consecutive from zero
within the bay, and never renumbered file-wide. File rendering asserts this.
`ChangeSet` checks the response path against the manifest before supplying the
state.

## Headers

The Full header is sticky, file-local, and outside `FileRendererBoundary`:
unexpected body-renderer damage replaces only the body with its local error
strip. The square in the header is the sole file-collapse control; the rest of
the header is inert and selectable as text. The File-level Comment trigger
stays inside the sticky header in Husk, Full, and Lazy states.

Husk, Full, and Lazy headers close only review UI anchored inside their own
outgoing DOM when a File state replacement disposes them.

A Husk preserves manifest position without reserving future body height and
shows no statistics.

## Frames and bays

Every backend result is one composed diff: File-level metadata plus ordered
frames of ordered bays. There is no `render_kind` to switch on; `FrameView`
dispatches each bay by its `kind`. Ordinary text is one heading-less frame with
one `flatfile` bay.

A bay's `bay_key` is the sub-file coordinate line pins and review targets name.
It extends a line coordinate inside the file; it is not a second file identity
and never changes file indexes. A notebook cell's key is its `nbformat` cell id,
so it survives edits to the cell it names. A notebook composes one frame per
cell, changed or not, so unchanged cells stay where the reviewer expects them;
an unchanged source bay arrives collapsed.

Content with no rows (`image` and `blob` bays, keyed `image` and `blob`) does
not participate in rich/virtual replacement: there is nothing to virtualize,
and staying mounted lets its chrome carry its single hunk stop. They carry no
line statistics or engine warning because no engine ran. Classification is
total: bytes that are not text compose a `blob` bay, so a File never arrives as
an error `LazyFile` where a diff was expected.

Bay expansion is owned by the card, keyed by bay key, and read as the backend's
`default_expanded` until the reviewer changes it. It survives file collapse and
rich/virtual replacement and is dropped with the card. A bay collapsed by
default still writes its first hunk coordinate, and that anchor stays landable,
unlike a collapsed File's skipped hunks: a change the reviewer cannot land on
is a hidden change.

## TextDiffGrid

`TextDiffGrid` owns the row DOM and line-pin paint for one text bay. It does
not own file loading or hunk selection.

- It has one persistent root and replaces its row children atomically when a
  rendering input other than review markers changes. Review marker changes only
  update classes on mounted Comment triggers; they never replace rows, detach a
  composer anchor, or erase selected-hunk DOM.
- Every row replacement, including fold changes, first closes only the review
  composer or Thread panel whose trigger is inside that grid.
- After each render the grid reports which ordinary sides have a real line-one
  Comment trigger in mounted DOM. The File marker uses that fact to place
  `file-start` on line one or on the File header; it never infers availability
  from backend rows.
- Trigger activation on the first row of an expanded fold bypasses the fold
  action and reaches the ordinary delegated review handler.
- Clicking a Comment trigger changes no URL, loading, scroll, or hunk
  selection. Shift-click extends the draft only when File, bay, and side are
  unchanged. Line-number clicks keep line-pin behavior.
- `ChangeSetShell` owns the one document-level pointer listener that marks the
  selected old or new side; shared CSS uses it to limit native text selection.

Backend row order is authoritative. Inline view merges rows whose changed
tokens are insert-only into one presented row, but hunk identity stays attached
to the backend boundary that produced it. When the bay's
`interleave_inline_rows` is true, each changed equal row is instead one
presented row in the tool's own order: each shared stretch, then the left
deletions, then the right insertions. The shared text anchors the two sides and
a disagreement throws. Split view ignores the flag.

Parts: the rendering layer combines each engine token partition with the syntax
spans of the same row side, preserving every displayed character. Invalid
engine tokens or syntax spans fail at the backend boundary; the grid renders
the parts directly and never intersects ranges or slices source by backend
offsets. Syntax splitting never merges different move relationships.

Moves: moved rows may carry an unpaired `move` record independent of structural
move endpoints; each old-only or new-only row counts once toward moved lines,
with no paired hover or counterpart navigation. Descendants of a hovered move
are determined by range containment on the hovered side; their origins may lie
outside the parent on the other side. Hover never adds enclosure coverage or
fills holes. Outline geometry is rebuilt after row replacement and on grid
resize; its observer and listeners end with the grid. Clicking a move navigates
to its counterpart through the shared line action, changes no line pins or hunk
selection, is suppressed by text selection, and an unfinished jump is cancelled
by a newer move click or grid disposal.

## Folded lines

The backend marks hunk boundaries and accepts foldable ranges from the same
canonical engine-row change classification, so fold discovery never compares
row text independently or discards rows such as trailing blanks. A fold range
containing a hunk boundary is a backend contract violation and throws.
`FrameView` numbers the boundaries into the File's sequence.

`TextDiffGrid` stores expanded folds locally. Expansion changes only that
grid's row DOM; it selects no hunk and neither navigates, fetches, nor changes
file collapse. Fold edges are not pinnable line numbers. Changing any rendering
input other than review markers rebuilds the grid and resets expanded folds;
preserving them across split/inline replacement is an open follow-up.

## Rich and virtual bays

Every expanded text bay alternates between rich and virtual representation,
per bay: a notebook's cells transition independently. The wrapper carries
`data-bay-render` (`"rich"` or `"virtual"`) and `data-bay-key`. Rich/virtual
replacement changes representation only; it does not change file state,
expansion, selection, counters, or URL.

The bay's row count selects its cost band:

| Cost | Rows | Rich-entry distance | Virtual-exit distance |
| --- | ---: | ---: | ---: |
| small | 0–250 | 2 viewports | 3 viewports |
| medium | 251–1000 | 4 viewports | 6 viewports |
| large | 1001+ | 8 viewports | 12 viewports |

The different distances are hysteresis: a bay is rich before it reaches the
viewport and virtual only after it moves farther away.

- A mounting bay chooses its first representation from the stable card's
  rectangle (its own wrapper does not exist yet). Render admission mounts card
  bodies top-down, so cards above already hold their real height. A bay in view
  therefore paints rich on its first frame.
- The exit observer suppresses virtualization while Navigation has unfinished
  commands.
- Zones are viewport multiples, so each mounted bay rebuilds both observers on
  window `resize` and on the Navigation root's `navigation-idle` event (fresh
  entries apply current geometry when an exit was skipped during navigation).
  The listener is per bay because the distances are the bay's own cost band.
  Both listeners go away when the bay unmounts.
- Rich to virtual: the bay is measured first and the virtual body uses that
  exact height, so the page does not jump. A bay never rich uses its natural
  virtual height.
- `VirtualBay` holds the complete undecorated old and new text in split form so
  native browser search finds both sides. A bay whose hunk anchor lives in its
  chrome keeps it in both representations.
- `data-file-render` on the card aggregates mounted bays: absent with none
  mounted, `virtual` when all are, otherwise `rich`. The header indicator and
  FileTree read only this aggregate.

## LazyFile

Lazy covers a manifest-deferred file, an ordinary file-query error, and an
ordinary lazy-info failure presented through each manifest-lazy file.

A deferred Lazy body is one colored, clickable plank, the sole explicit load
action; its color records the lazy reason, which FileTree retains after
loading. A deferred header shows known added/removed values and `?` for
unavailable modified/moved values, never invented zeroes. An error Lazy body
shows the local `ErrorPanel` with a `RetryButton`; retry has no timeout and
re-enters the shared file lane. An error header has no statistics.

## Renderer failures

An unexpected `FullFile` renderer exception reports a Toast, marks the article
as a terminal renderer error, shows a critical unrecoverable strip, and exposes
no hunk target or Retry. It is not a backend `LazyFile` and never invents file
content.

## Expansion and disposal

File expansion is stored by `ChangeSet` and shared by the file list and
FileTree; state replacement creates no other expansion value.

Disposing a `FileCard` disposes its bays: each text bay disconnects its
observers and resize listener, removes the operations on its wrapper, and
clears its registered render mode. The card removes the operations `FullFile` or
`TextDiffGrid` attached, drops `data-file-render`, and drops local fold and bay
expansion state. The card does not outlive its `ChangeSetSnapshot`; query
cancellation and observer disposal remain the lane's responsibility.

## Operations exposed to navigation

Navigation discovers these through the DOM, and each exists exactly for the
mounted lifetime of its element. File presentation never calls `selectHunk()`.

- Card: `prepareLine_impl(target, abortSignal)` expands the file and the
  target's bay, enriches that bay, unfolds the line, and returns the row or a
  precise missing/stopped result. Calling it before admission is a contract
  error.
- Text bay wrapper: `waitToEnrich_impl()` makes the bay rich and resolves once
  the rich grid exists; `intersectsRichEntryZone(viewportTop)` answers whether
  the bay's entry zone intersects a hypothetical viewport without changing
  representation.
- `TextDiffGrid`: its own `prepareLine_impl` unfolds containing folds and
  returns the exact row. It does not scroll, paint a pin, select a hunk, or
  fetch.
