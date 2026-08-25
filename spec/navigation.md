# Navigation

## Mounted DOM model

Navigation is scoped to one mounted `ChangeSet` root. Renderers write file and
hunk identity into that DOM; navigation reads the current DOM when an operation
occurs. It does not retain a parallel hunk registry or selected-hunk store.

Every hunk identity contains:

- the manifest `fileIndex`;
- for real and skipped hunks, the `bay_key` of the owning bay;
- the bay-local `hunkIndex`, exactly as the backend numbered it;
- a render kind.

Hunk indexes are bay-local: every bay numbers its hunks from zero, so
the index alone names no target and the bay key is the other half of the
coordinate. No layer renumbers hunks file-wide.

Real hunks use `kind: "real"`. File-state pseudo-hunks use `"husk"`, `"lazy"`,
or `"zero"` with `hunkIndex: 0` and no bay. Collapsed real hunks use
`"skip"` while preserving their original bay and hunk index.

Every target writes:

```text
data-hunk-target
data-file-index
data-hunk-bay   (real and skip targets only)
data-hunk-index
data-hunk-kind
```

The selected identity is stored on its `FileCard` as
`data-selected-hunk-kind` and `data-selected-hunk-index`, joined by
`data-selected-hunk-bay` when the selected target carries a bay. The
declared kind picks the resolution strategy. A file-state selection (`husk`,
`lazy`, `zero`) names the File's own stop and survives representation
replacement: a Husk selection stays written after admission swaps the husk
target for real composed DOM, and in every representation that stop resolves
to the card's first target in DOM order. A hunk selection (`real`, `skip`)
resolves strictly on bay key and bay-local hunk index; the kind stays
out of that match because collapse and expansion interconvert real and skip
targets at the same coordinates. Any other combination of kind, bay, and
index is a contract violation and throws. The exported `storedHunkTarget`
implements this resolution once, for navigation and the hunk display alike.
Initial selection and the three selection operations
also decorate the matching current target with `data-selected`, `aria-current`,
and the selected visual class.

The FileCard attribute survives representation replacement. Target decoration
does not: when Husk, Full, rich, virtual, or collapsed DOM replaces the selected
target, no renderer copies the decoration to the replacement. Decoration
returns only when `nextHunk()`, `prevHunk()`, or `scrollFollow()` actually calls
`selectHunk()` for a destination. Returning an off-screen selection to the
viewport does not redecorate it because that action deliberately does not select
again.

A participating target is any hunk target without `.skip`. Skipped targets
retain coordinates and may retain selection, but Next, Previous, FileTree
destinations, and scroll-follow do not traverse them as participants.

## Initial selection

When a non-empty snapshot mounts, `ChangeSetSnapshot` calls the exported
`writeInitialHunkSelection`, which validates the fresh unselected DOM and
selects the first `FileCard`'s first target in DOM order — which, in every
representation, is that file's first coordinate. Because the
NavigationProvider survives snapshot replacement — an engine switch replaces
the snapshot beneath it — initialization belongs to the snapshot lifetime,
and every replacement snapshot starts selected. The write itself goes through
`selectHunk()` as its one sanctioned initialization caller.

An empty manifest has no selected hunk. A terminal renderer error prevents
initialization because it exposes no valid hunk target. Every ordinary
`FileCard`, including Husk, Lazy, zero-hunk Full, and collapsed files, has the
required target, which carries a coordinate.

## Selected-hunk operation

`selectHunk()` is the single operation that changes an existing hunk selection.
It verifies the target's coordinate, removes the previous selected
decoration, writes the selected bay and hunk index to the destination
`FileCard`, and decorates that exact target.

It has exactly four direct callers:

1. `nextHunk()`;
2. `prevHunk()`;
3. `scrollFollow()`;
4. `writeInitialHunkSelection()`, the one initialization exception for a
   freshly mounted snapshot.

No renderer, FileTree operation, line pin, helper, wrapper, or shared
calculation calls it.

## Next and Previous

Next and Previous begin from the exact selected file and hunk indexes in DOM.
They never substitute the FileCard header, the first target, or another nearby
element when the selected coordinates cannot be resolved.

If the selected target is outside the main viewport, the operation first
returns to that same target and stops. A target inside a virtual bay has that
bay enriched, the exact target is resolved again after layout changes, and
then centered.
Enrichment completes only with real geometry: fresh chunks are laid out and
warmed before the operation reads heights, and a rich-to-virtual transition
measures real chunk layout before pinning its reserved height, so completed
navigation cannot be displaced by later render-mode transitions.
Selection does not advance.

If the selected target is already on screen:

- Next chooses the following participating target in DOM order;
- Previous chooses the preceding participating target in DOM order;
- each wraps at the end of the participating sequence.

When the current selected target is skipped, its stable position in the complete
target order determines which participant comes next or previous. A real
destination inside a virtual bay has exactly that owning bay enriched and is
re-resolved by its same file index, bay key, and hunk index before selection
and scrolling; other bays keep their representation.

The operation then calls `selectHunk()` exactly once and centers the selected
target. If no participating targets exist, it leaves the current skipped
selection unchanged.

## Scroll-follow

Scroll-follow updates hunk selection only during recognized user-driven document
scrolling.

A private scroll guard distinguishes:

- idle;
- input that may begin document scrolling;
- an active document scroll.

Wheel and touch input enable the guard only when the document can move in that
direction. Keyboard scrolling remains eligible until scrolling ends. Input
expires on the next animation frame if the document never scrolls. Nested
scrolling before document movement cancels eligibility; FileTree scrolling
during an already active document scroll does not. Programmatic navigation
stops the guard.

Eligible document scroll events coalesce into at most one `scrollFollow()`
calculation per animation frame, always using the latest viewport; a guard
stop occurring before the frame cancels the pending calculation. The
calculation hit-tests the reading line at the vertical center of the viewport
within the file-list area. It considers only visible, rich, participating real
hunk targets in the hit `FileCard`, located by binary search over their
document-ordered rows rather than measuring every target. Virtual anchors,
pseudo-hunks, and skipped targets are excluded.

The target is the last real hunk at or above the reading line, or the first one
below it when none precedes the line. `scrollFollow()` calls `selectHunk()` and
does nothing else: it does not scroll, enrich, expand, fetch, or calculate
counters.

`scrollend` returns the guard to idle without another selection calculation.
Listener errors stop the guard and produce a Toast.

## Hunk display

`ChangeSet` mirrors selected-hunk display data from the authoritative DOM into
one `HunkDisplay` signal. One narrow `MutationObserver` watches only:

- `data-hunk-set`;
- `data-selected-hunk-index`;
- `data-file-render-error`.

The signal contains:

- the selected manifest file index;
- the global selected position and `hasMore`;
- the selected position within each file.

Positions are numeric data. Consumers format them independently.
`DebugHud`, `FileCard` headers, and FileTree highlighting read this signal;
navigation and selection never do.

Totals count participating targets. A selected skipped target retains its
stable position among all targets that carry a coordinate, so its current
position may be greater than the participating total. `hasMore` records that
Husk, Lazy, or collapsed content can change the visible participant set.

## FileTree model

FileTree renders the manifest hierarchy and the same current file states used by
the file list. Directory labels end in `/`.

File and directory collapse is one shared `ChangeSet` value:

- a FullFile square toggles that file;
- a directory square writes the same expansion value to all descendant files;
- Husk and Lazy squares are inert state markers;
- names do not toggle expansion.

Directory visibility is derived from descendant reachability. An explicitly
expanded file is reachable. Unresolved Husks and LazyFiles remain reachable.
Otherwise a FullFile uses its backend default expansion. A directory is expanded
when at least one descendant remains reachable. This lets expanding one file
reveal the required ancestor path without blindly expanding unrelated
directories.

FileTree progressively calculates file and directory statistics from the same
file states. Unknown descendants propagate `?`. Lazy reason colors remain
visible, and a successfully loaded FullFile keeps the color of its non-error
lazy reason.

### Highlight and private scrolling

FileTree highlight is a read-only calculation from
`HunkDisplay.selectedFileIndex`. The matching file row receives `aria-current`.
It does not select or navigate.

When the selected file index changes and its row is reachable, FileTree adjusts
only its own scroll container by the minimum distance required to reveal that
row. A hunk change within the same file does not move the tree. A collapsed
ancestor legitimately leaves the row absent; an expanded reachable path with a
missing row is an error.

FileTree never calls `scrollIntoView()` for this behavior because that could move
the main document.

### File and directory navigation

Name activation sends one explicit file-navigation operation:

- a file name targets that manifest file;
- a directory name targets its first manifest file;
- a backend-state Husk name is disabled until that file is loaded.

Query success changes the state to Full before render admission. During that
short interval the name is enabled. File navigation rejects the temporary Husk
target of an expanded nonzero-hunk FullFile; a zero-hunk or collapsed FullFile
already has its stable zero or skip target and remains navigable.

File navigation scrolls to the destination’s first target in its current
representation. It does not select, fetch, or change expansion.

Before the one final centered scroll, Navigation computes the destination’s
hypothetical viewport. Any mounted virtual bay whose rich-entry zone
intersects that viewport is enriched at most once during the operation.
Navigation recalculates geometry after each resulting layout change, resolves
the exact destination coordinates again, then performs one scroll and a
temporary destination flash.

A Lazy destination scrolls to its plank or collapsed skip target. A zero-hunk,
real, or collapsed FullFile uses its exact first target; for an expanded rich
FullFile that is its first bay's hunk zero. Navigation never expands a
collapsed file.

## Hotkeys and HUD

Hotkeys are attached to the mounted ChangeSet shell:

| Key | Operation |
| --- | --- |
| `n` | Next hunk |
| `N` | Previous hunk |
| `p` | Top of page |
| `t` | Toggle FileTree |
| `i` | Toggle inline/split view |
| `m` | Toggle review History |
| `r` | Reload the ChangeSet snapshot |
| `d` | Toggle DebugHud |
| `h` | Toggle HelpModal |

Ctrl, Meta, and Alt combinations are ignored. ChangeSet hotkeys are ignored while
editing an input, textarea, select, or content-editable element.

FileTree and DebugHud visibility are workspace-global, so switching Tabs or
presets does not reset them. `HintHud` contains Next, Previous, and Help.
`DebugHud` displays FPS, node count, span count, and the global hunk position.

## Line pins

Line pins connect URL identity, the file lane, file preparation, TextDiffGrid
decoration, and one final Navigation scroll. They never select a hunk.

### URL identity

Each `ChangeSetSnapshot` creates one `LinePins` instance and passes it to every
`TextDiffGrid`.

The URL hash contains at most one `pin` JSON value:

```ts
{
  file: string;
  bay: string;
  side: "left" | "right";
  line: string;
}
```

`file` is the canonical `FileDiff.display_name`. Repository snapshots derive it
from the manifest path pair; preset snapshots use the fixture’s new-side path,
which is also the backend file response name. `bay` is the non-empty composed
bay key — the same universal sub-file coordinate review targets use — so a
flatfile carries `FLATFILE_BAY_KEY` rather than an absent field, and notebook
source carries its backend cell key. `line` is a positive decimal backend line
number.

The URL is the sole retained pin identity. `LinePins` does not observe browser
history and no Solid signal or DOM attribute duplicates that identity.

`parseUrl()` reads and validates the current hash. It does not repair malformed
data. `toggleUrlState(target)` cancels any active restoration, removes the pin
when the exact target is already present, or replaces it otherwise. It preserves
the path, query, and unrelated hash fields through `history.replaceState`;
changing the URL does not itself start restoration.

### Direct TextDiffGrid activation

`TextDiffGrid` has one delegated click listener on its persistent root. An ordinary
line-number click combines:

- the grid’s file path and its composed bay key;
- the clicked side;
- the clicked backend line.

It passes that complete target to `toggleUrlState()`, removes the previous
`.pinned-line` decoration from this ChangeSet, and paints the clicked complete
row when the result is pinned.

Fold-edge rows are not pinnable and retain their fold interaction. No direct
click invokes Navigation or file loading because the clicked row is already
rendered.

### Decoration during rendering

TextDiffGrid owns all `.pinned-line` paint. Initial rendering, explicit fold
replacement, rich rendering after virtualization, inline/split replacement,
and notebook source rendering each read `LinePins.parseUrl()` and paint the
matching ordinary row when it is present in that grid.

Decoration follows rendered rows and may disappear when a file collapses, a
line folds, or rich content becomes virtual. The URL identity remains unchanged.
There is no history listener, MutationObserver, decoration signal, revision
counter, or retry loop for paint.

### Snapshot and lane preparation

Before the lane starts, `ChangeSet` parses the URL once.

- A malformed pin produces a two-second Toast and remains untouched.
- A valid pin that names no manifest file produces a two-second Toast and is
  removed if it is still the current exact target.
- More than one matching manifest file is an error.
- One exact match becomes the lane’s current line target.

The lane loads in manifest order through the target, explicitly loads a lazy
target, and waits for admission. An ordinary target-file failure remains an
error `LazyFile`; the target becomes dormant and can resume after Retry.

Once the target file is admitted, the lane calls `LinePins.restore()` and waits
for it to finish before continuing with later files.

### File and row preparation

`LinePins.restore()` retains one `AbortController` for the active restoration,
verifies that the URL still contains the same exact target, and asks Navigation
to navigate to the line’s manifest index and coordinates.

Navigation calls the target `FullFile`’s `prepareLine_impl()`:

1. expand the target file when collapsed;
2. expand the target's bay and enrich it;
3. locate the exact ordinary or notebook `TextDiffGrid` inside that bay;
4. ask that grid to expand every line fold containing the target;
5. return the exact complete row.

Preparation returns `ready`, `missing`, or `stopped`. It does not fetch, paint,
select a hunk, or scroll.

### Final scroll

For a ready row, Navigation computes its hypothetical centered viewport and
uses the same rich-entry-zone process as FileTree navigation. Intersecting
mounted virtual bays are enriched one at a time, the target is prepared
again after each layout change, and its geometry is recalculated.

Immediately before one final centered scroll, Navigation checks cancellation
and stops scroll-follow. It does not select a hunk.

### Cancellation, missing targets, and failures

`toggleUrlState()` aborts an older restoration before changing the URL.
Snapshot disposal aborts restoration through the snapshot lifetime. Navigation
also checks the abort signal immediately before its final scroll.

Manual scrolling does not cancel restoration. A slow valid pin may move the
user when its target eventually becomes ready; that delayed movement is the
purpose of restoration.

If the complete current file no longer contains the exact line, restoration
shows a two-second Toast and removes the pin only when the URL still contains
that exact target. A stopped restoration performs no Toast, URL change, paint,
or scroll.

Unexpected structural failures remain exceptions and are handled by the
snapshot’s nearest error boundary. They are not converted into missing
coordinates or file failures.

## History File jumps

History is static with respect to the main File lane. It never follows scroll,
changes selection, ordering, open state, or content in response to scrolling,
and never calls scroll-follow. In Split view only, one coalesced geometry
observer hit-tests the current sticky File header so the fixed History host
remains directly beneath it; scrolling inside History is explicitly ignored.
A located Thread exposes one explicit
go-to action in its Thread header; individual Comments carry none. ChangeSet
maps the Thread's exact nullable File pair to one manifest index and sends
`kind: "line"` navigation to the Thread's exact selected-side line (notebook
Threads address their cell bay), then expands the Thread and opens the
code-aligned Thread panel anchored at that line. The control is disabled while
the destination is a Husk, matching FileTree's caller contract without loading
it. The action follows line navigation's layout preparation and final scroll
behavior, and never selects a hunk. Unlocated Threads have no go-to action.

## Navigation invariants

- File index, bay key, and bay-local hunk index in the mounted DOM are
  the authoritative selected-hunk coordinates.
- Every real or skipped hunk carries all three coordinates; file-state
  pseudo-hunks carry the file index and index zero without a bay.
- `selectHunk()` has exactly four direct callers: `nextHunk()`, `prevHunk()`,
  `scrollFollow()`, and the initialization-only `writeInitialHunkSelection()`.
- Initial selection belongs to the mounted snapshot; FileTree and line pins
  never select.
- Skipped targets preserve coordinates but are excluded from traversal.
- FileTree navigation scrolls only and never loads, selects, or changes
  expansion.
- Scroll-follow selects only rich participating real hunks and never scrolls.
- Programmatic file and line navigation center their destination after layout
  preparation and re-center until nearby chunk rendering stops moving it. An
  idle warm-up pass renders every chunk once so skipped-chunk geometry becomes
  exact (TextDiffGrid's `warmPendingChunk`); after it completes the re-centering
  loop converges immediately.
- The URL is the sole line-pin identity.
- TextDiffGrid owns pin decoration; LinePins owns parsing, URL toggling, and active
  restoration cancellation; the lane owns loading; Navigation owns the final
  scroll.
- Line pins never call `selectHunk()`.
- The History Thread go-to is enabled only for a non-Husk destination,
  performs exact-line navigation that opens the code-aligned Thread panel,
  and never calls `selectHunk()` or `scrollFollow()`.
