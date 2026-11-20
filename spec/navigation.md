# Navigation

## Mounted DOM model

Navigation is scoped to one mounted `ChangeSet` root. Renderers write file and
hunk identity into that DOM; navigation reads the current DOM when an operation
occurs. It does not retain a parallel hunk registry or selected-hunk store.

Each Navigation instance counts unfinished explicit commands. Text bays read
that count and suppress rich-to-virtual exits while it is positive. The count
is released in `finally` on completion, cancellation, or failure. Overlapping
commands keep exits paused until all settle; their competing scrolls are not
serialized. When the count reaches zero the shell root emits `navigation-idle`
so mounted bays re-evaluate exits skipped while commands ran.

## Hunk coordinates

A hunk is addressed by manifest `fileIndex`, the owning bay's `bay_key`, and
the bay-local `hunkIndex` exactly as the backend numbered it. Every bay numbers
from zero, so the index alone names no target; no layer renumbers hunks
file-wide. File-state pseudo-hunks (`husk`, `lazy`, `zero`) carry the file
index, index zero and no bay. A collapsed real hunk becomes `skip` at the same
coordinates.

A changed bay that contributes no changed row (an `image` or `blob` bay, or a
text bay whose change is a move) carries one ordinary `real` stop at index zero
on its own always-mounted chrome, so every change is reachable by Next and
Previous. An empty text bay (as for an added or removed empty File) carries no
stop; a File made only of such bays is zero-hunk and exposes the `zero`
pseudo-hunk, so Next and Previous still visit it.

The selected identity lives on its `FileCard` (`data-selected-hunk-*`).
A file-state selection survives representation replacement and resolves to the
card's first target in DOM order. A `real`/`skip` selection resolves strictly on
bay key and bay-local index, never on kind, because collapse and expansion
interconvert real and skip at the same coordinates. Any other combination
throws. `storedHunkTarget` is the one implementation of this, shared by
navigation and the hunk display.

The FileCard attribute survives representation replacement; the target
decoration (`data-selected`, `aria-current`, visual class) does not, and no
renderer copies it. Decoration returns only when `selectHunk()` runs for a
destination. Returning an off-screen selection to the viewport deliberately
does not select again, so it does not redecorate.

A participating target is any hunk target without `.skip`. Skipped targets keep
coordinates and may stay selected, but Next, Previous, FileTree destinations
and scroll-follow never traverse them.

## Selection

`selectHunk()` is the single operation that changes an existing hunk selection.
It has exactly five direct callers, enforced by the scoped frontend lint
(`select-hunk-callers`):

1. `nextHunk()`;
2. `prevHunk()`;
3. `scrollFollow()`;
4. `navigateToFile()`;
5. `writeInitialHunkSelection()`.

No renderer, FileTree component, line pin, helper or wrapper calls it.

Initial selection belongs to the mounted snapshot: `ChangeSetSnapshot` calls
`writeInitialHunkSelection` on mount. The NavigationProvider survives snapshot
replacement (an engine switch), so it cannot initialize; every replacement
snapshot selects its first card's first target itself. An empty manifest has no
selection.

FileTree name navigation selects hunks (through `navigateToFile()`). Line-pin
navigation never selects.

## Next and Previous

They start from the exact selected file and hunk indexes in DOM and never
substitute the card header, the first target or another nearby element when
those cannot be resolved.

If the selected target is outside the main viewport, the operation first
returns to that same target and stops; selection does not advance. Otherwise it
picks the next (or previous) participating target in DOM order, wrapping at the
ends. From a skipped selection, its position in the complete target order
decides what is next or previous. If no participating target exists, the
skipped selection is left unchanged.

A destination inside a virtual bay has exactly that bay enriched and is
re-resolved by file index, bay key and hunk index before selection and
scrolling. Enrichment completes only with real geometry (fresh chunks laid out
and warmed; a rich-to-virtual transition measures real chunk layout before
pinning its reserved height), so later render-mode transitions cannot displace
a completed navigation.

## Scroll-follow

Scroll-follow updates the selection only during recognized user-driven document
scrolling. A private guard (idle, input that may begin document scrolling,
active document scroll) gates it; programmatic navigation stops the guard.
Eligible scroll events coalesce into at most one `scrollFollow()` per animation
frame, and a guard stop before the frame cancels it.

The reading line is the vertical center of the viewport within the file-list
area. Only visible, rich, participating real hunk targets in the hit card count;
virtual anchors, pseudo-hunks and skipped targets are excluded. The target is
the last real hunk at or above the line, or the first one below it when none
precedes. The binary search over targets depends on `.diff-row-chunk` using
`content-visibility: auto`, never `hidden` (a zero rect would break
monotonicity silently).

`scrollFollow()` calls `selectHunk()` and does nothing else: it does not
scroll, enrich, expand or fetch.

## Hunk display

`ChangeSet` mirrors selected-hunk display data from the authoritative DOM into
one `HunkDisplay` signal through one `MutationObserver` on `data-hunk-set`,
`data-selected-hunk-index` and `data-file-render-error`. `DebugHud`, `FileCard`
headers and FileTree highlighting read it; navigation and selection never do.

Totals count participating targets. A selected skipped target keeps its stable
position among all coordinate-carrying targets, so its position may exceed the
participating total. `hasMore` records that Husk, Lazy or collapsed content can
change the participant set.

## FileTree

File and directory collapse is one shared `ChangeSet` value. Names never toggle
expansion; Husk and Lazy squares are inert markers. A directory is expanded
when at least one descendant is reachable: explicitly expanded files, and
unresolved Husks and LazyFiles, are reachable; any other FullFile uses its
backend default. Expanding one file therefore reveals its ancestor path without
expanding unrelated directories.

Highlight is a read-only calculation from `HunkDisplay.selectedFileIndex`; it
never selects or navigates. On a file change it adjusts only the tree's own
scroll container, and never calls `scrollIntoView()`, which could move the main
document. A hunk change within the same file does not move the tree. A
collapsed ancestor legitimately leaves the row absent; a missing row on an
expanded reachable path is an error.

### File navigation

A name sends one explicit file-navigation operation: a file name targets that
file, a directory name its first manifest file. A backend-state Husk name is
disabled until loaded. Query success changes the state to Full before render
admission, and in that interval the name is enabled; `navigateToFile()` rejects
the temporary Husk target of an expanded nonzero-hunk FullFile, while a
zero-hunk or collapsed FullFile has its stable zero or skip target and stays
navigable.

It selects the destination's first target in its current representation and
scrolls once. It never fetches and never changes expansion, so a collapsed file
is reached at its skip target. Before the final centered scroll, any mounted
virtual bay whose rich-entry zone intersects the destination's hypothetical
viewport is enriched at most once; geometry is recalculated and the destination
re-resolved after each layout change.

## Line pins

Line pins connect URL identity, the file lane, file preparation, grid
decoration and one final Navigation scroll. They never select a hunk.

The URL hash holds at most one `pin` JSON value `{file, bay, side, line}`:
`file` is the manifest `{left_path, right_path}` pair, `bay` is `{bay_key}`
(a flatfile carries `FLATFILE_BAY_KEY`, notebook source its cell key), `side`
names a side whose path exists, and `line` is a positive decimal backend line
number. The URL is the sole retained pin identity: `LinePins` does not observe
history, and no signal or DOM attribute duplicates it. `parseUrl()` validates
and never repairs. `toggleUrlState()` aborts any active restoration, then adds,
replaces or removes the pin through `history.replaceState`, keeping unrelated
hash fields; changing the URL does not itself start restoration.

Pin ownership:

- Each bay widget owns the `.pinned-line` paint inside its own bay (initial
  rendering, fold replacement, rich rendering after virtualization,
  inline/split replacement, notebook source). Paint follows rendered rows and
  may vanish on collapse, fold or virtualization while the URL stays. There is
  no history listener, observer, signal or retry loop for paint.
- A line-number click toggles the URL and repaints directly; it never invokes
  Navigation or loading, because the row is already rendered. Fold-edge rows
  are not pinnable. A media bay has one pseudo-line per captured side, line `1`.
- `ChangeSet` parses the URL once before the lane starts. A malformed pin, or a
  valid pin naming no manifest file, produces a two-second Toast (the latter
  also removes the pin if still current); more than one matching file is an
  error. The lane loads through the target, explicitly loads a lazy target,
  waits for admission, then awaits `LinePins.restore()` before later files. An
  ordinary target-file failure stays an error `LazyFile` and can resume after
  Retry.
- `LinePins.restore()` retains one `AbortController`, checks the URL still holds
  the same target, and asks Navigation to navigate. Navigation calls the target
  `FullFile`'s `prepareLine_impl()` (expand file, expand and enrich the bay,
  find the bay by its bay key, let its line host expand folds) which returns
  `ready`, `missing` or `stopped` and does not fetch, paint, select or scroll.
- For a ready row Navigation uses the same rich-entry-zone process as file
  navigation, checks cancellation, stops scroll-follow and does one centered
  scroll.
- Cancellation: `toggleUrlState()` and snapshot disposal abort restoration;
  Navigation checks the signal immediately before its final scroll. A missing
  exact line shows a two-second Toast and removes the pin only if the URL still
  holds that target. A stopped restoration does nothing visible. Structural
  failures stay exceptions for the nearest error boundary.

## Invariants

- Programmatic file and line navigation center their destination after layout
  preparation and re-center until nearby chunk rendering stops moving it.
- Scroll-follow selects only rich participating real hunks and never scrolls.
- LinePins owns parsing, URL toggling and restoration cancellation; the lane
  owns loading; Navigation owns the final scroll; bay widgets own decoration.
