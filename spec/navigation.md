# Navigation

## Mounted DOM model

Navigation is scoped to one mounted `ChangeSet` root. Renderers write file and
hunk identity into that DOM; navigation reads the current DOM when an operation
occurs. It does not retain a parallel hunk registry or selected-hunk store.

Each Navigation instance counts unfinished explicit commands
(`activeOperations`, nonreactive). Text bays read that count and suppress
rich-to-virtual exits while it is positive. The count is released in `finally`
on completion, cancellation, or failure. Overlapping commands keep exits paused
until all settle; their competing scrolls are not serialized. When the count
reaches zero the shell root emits `navigation-idle` so mounted bays re-evaluate
exits skipped while commands ran.

## Hunk coordinates

A hunk is addressed by manifest `fileIndex`, the owning bay's `bay_key`, and
the bay-local `hunkIndex`. Every bay numbers from zero, so the index alone
names no target; no layer renumbers hunks file-wide. File-state pseudo-hunks
(`husk`, `lazy`, `zero`) carry the file index, index zero and no bay. A
collapsed real hunk becomes `skip` at the same coordinates. Which bays carry a
stop is in [`formats.md`](formats.md#bays).

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
destination.

A participating target is any hunk target without `.skip`. Skipped targets keep
coordinates and may stay selected, but Next, Previous, FileTree destinations
and scroll-follow never traverse them.

## Selection

`selectHunk()` is the single operation that changes an existing hunk selection.
Its five direct callers are fixed and enforced by the `select-hunk-callers`
lint: `nextHunk()`, `prevHunk()`, `scrollFollow()`, `navigateToFile()` and
`writeInitialHunkSelection()`.

Initial selection belongs to the mounted snapshot: `ChangeSetSnapshot` calls
`writeInitialHunkSelection` on mount. The NavigationProvider survives snapshot
replacement (an engine switch), so it cannot initialize.

FileTree name navigation selects hunks (through `navigateToFile()`). Line-pin
navigation never selects.

## Enrichment before scrolling

A destination inside a virtual bay has exactly that bay enriched and is
re-resolved by file index, bay key and hunk index before selection and
scrolling. Enrichment completes only with real geometry (fresh chunks laid out
and warmed; a rich-to-virtual transition measures real chunk layout before
pinning its reserved height), so later render-mode transitions cannot displace
a completed navigation.

Programmatic file and line navigation center their destination after layout
preparation and re-center until nearby chunk rendering stops moving it.

## Scroll-follow

Scroll-follow updates the selection only during recognized user-driven document
scrolling. A private guard gates it; programmatic navigation stops the guard.
Eligible scroll events coalesce into at most one `scrollFollow()` per animation
frame, and a guard stop before the frame cancels it.

Only visible, rich, participating real hunk targets count; virtual anchors,
pseudo-hunks and skipped targets are excluded. `scrollFollow()` calls
`selectHunk()` and does nothing else: it does not scroll, enrich, expand or
fetch.

## Hunk display

`ChangeSet` mirrors selected-hunk display data from the authoritative DOM into
one `HunkDisplay` signal through one `MutationObserver`. `DebugHud`, `FileCard`
headers and FileTree highlighting read it; navigation and selection never do.

## FileTree

File and directory collapse is one shared `ChangeSet` value. `FileTree` changes
only tree visibility or file expansion and never selects or loads files; name
activation goes through `navigateToFile()`, which never fetches and never
changes expansion.

Highlight is a read-only calculation from `HunkDisplay`. On a file change the
tree adjusts only its own scroll container and never calls `scrollIntoView()`,
which could move the main document.

## Line pins

Line pins connect URL identity, the file lane, file preparation, grid
decoration and one final Navigation scroll. They never select a hunk.

The URL hash holds at most one `pin` JSON value `{file, bay, side, line}`. The
URL is the sole retained pin identity: `LinePins` does not observe history, and
no signal or DOM attribute duplicates it. `parseUrl()` validates and never
repairs. `toggleUrlState()` aborts any active restoration, then changes the pin
through `history.replaceState`; changing the URL does not itself start
restoration.

Ownership: `LinePins` owns parsing, URL toggling and restoration cancellation;
the lane owns loading; Navigation owns the final scroll; bay widgets own
decoration.

- Each bay widget paints `.pinned-line` inside its own bay whenever it renders
  rows. Paint follows rendered rows and may vanish on collapse, fold or
  virtualization while the URL stays. There is no history listener, observer,
  signal or retry loop for paint.
- A line-number click toggles the URL and repaints directly; it never invokes
  Navigation or loading, because the row is already rendered.
- `ChangeSet` parses the URL once before the lane starts. The lane loads through
  the target, explicitly loads a lazy target, waits for admission, then awaits
  `LinePins.restore()` before later files.
- `LinePins.restore()` retains one `AbortController`, checks the URL still holds
  the same target, and asks Navigation to navigate. Navigation calls
  `prepareLine()` on the target File's `FileCardHandle` (`fileCardAt()`), which
  returns `ready` with the line's `LineCell`, `missing` or `stopped` and does
  not fetch, paint, select or scroll. A completed line navigation returns that
  `LineCell`, and only while its row is still mounted after the final scroll
  settles: a view switch or File collapse meanwhile makes it `stopped`, so no
  review UI anchors to a detached row.
- `toggleUrlState()` and snapshot disposal abort restoration; Navigation checks
  the signal immediately before its final scroll. A pin is removed
  automatically only if the URL still holds that target. Structural failures
  stay exceptions for the nearest error boundary.
