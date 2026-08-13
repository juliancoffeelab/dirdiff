# File meat

## File model

One manifest entry becomes one stable `FileCard` in `Husk`, `Full`, or `Lazy`
state.

The `FileCard` article keeps the manifest file index and identity while its
state changes. Its header, body, hunk targets, and navigation operations change
inside that stable boundary. Loading and canonical backend state come from the
file lane described in [`file-lane.md`](file-lane.md); the card does not observe
queries or perform HTTP operations.

The three states have different jobs:

| State | Meaning | Body |
| --- | --- | --- |
| `Husk` | the canonical file result is queued or fetching | path and loading state |
| `Full` | the backend file result is loaded | body appears after separate render admission |
| `Lazy` | loading is deferred, or file/lazy-info loading failed | explicit-load plank or error panel |

Every non-terminal `FileCard` exposes at least one hunk target. Unexpected
renderer failure is terminal local damage and is the one state that deliberately
exposes none.

## Husk

A `HuskFile` preserves the file’s manifest position without reserving its future
body height. Its header shows the path and whether work is queued or fetching.
It has no file statistics, expansion control, or rendered code. The shared
File-level Comment trigger remains inside this sticky header while loading.

Its pseudo-hunk identity is:

- the manifest file index;
- `kind: "husk"`;
- `hunkIndex: 0`.

An explicitly collapsed Husk keeps the same coordinates and receives `.skip`.
Loading does not create a new `FileCard`; it replaces the contents of the stable
article with the resulting `FullFile` or error `LazyFile`.

## FullFile

A successful backend file response becomes `FullFile` immediately. Render
admission is a separate value. Before admission, the stable card already shows
its Full header and statistics and omits the body. An expanded nonzero-hunk file
temporarily exposes one Husk target at hunk index zero; admission replaces that
target with the real hunk body. A zero-hunk file keeps its permanent zero target,
and a collapsed nonzero file keeps its coordinate-preserving skip targets,
whether or not the body has been admitted.

Before `ChangeSet` supplies the state to `FileCard`, it checks the response path
against the manifest. File rendering checks that real hunk indexes are
consecutive backend coordinates beginning at zero.

The `FullFileHeader` is sticky and file-local. It contains:

- the File-level Comment trigger and its persisted/draft/outdated marker state;
- the path;
- file kind or status;
- engine warning, when present;
- added, modified, removed, and moved statistics;
- global and file-local hunk positions.

The square in the header is the sole file-collapse control. The rest of the
header is inert and remains selectable as text.

The Full header is outside `FileRendererBoundary`; unexpected body-renderer
damage replaces only the body with its local error strip. Husk, Full, and Lazy
headers explicitly close only review UI anchored inside their outgoing DOM when
a File state replacement disposes them.

A zero-hunk `FullFile` exposes one pseudo-hunk with `kind: "zero"` and
`hunkIndex: 0`. A nonzero expanded `FullFile` exposes its real backend hunk
coordinates. When collapsed, each real coordinate remains in DOM as an
invisible `kind: "skip"` target with the same file and hunk indexes.

## FileBody dispatch

`FileBody` chooses the renderer from the backend `render_kind`:

- ordinary text is rendered by `DiffGrid`;
- notebooks are rendered by `NotebookFile`.

This is a rendering boundary, not a loading boundary. Both branches receive one
already validated backend result, the current split/inline view, the fold
preference, file identity, and the shared line-pin interface.

## Text rendering and DiffGrid

`DiffGrid` owns the visible row DOM for one text region. It uses one persistent
root and replaces its row children atomically when identity-bearing renderer
inputs change. Review marker changes update classes on the mounted Comment
triggers and never enter that complete-render effect, replace rows, detach a
composer anchor, or erase selected-hunk DOM.

Every explicit row replacement, including fold changes, first closes only the
review composer or inline Thread panel whose trigger is inside that grid. This
prevents a detached floater without observing the DOM or affecting review UI in
another File.

After each complete render or fold replacement, the grid reports the exact
ordinary sides whose mounted DOM contains a real line-one Comment trigger. The
File marker uses that mounted fact to place `file-start` on line one or the File
header; it does not infer render availability from backend rows.

The first row of an expanded fold retains its visible Comment triggers. Trigger
activation bypasses that row's fold action and reaches the ordinary delegated
review handler.

The grid renders:

- split or inline column headers;
- old and new line numbers;
- backend-woven text parts carrying syntax and inline diff decoration;
- backend hunk boundaries;
- unchanged fold rows;
- the selected old/new pointer side;
- line-pin decoration for the exact current URL target.
- one Comment trigger on every real line number, with persisted, draft, and
  outdated marker state from the Snapshot review boundary.

Backend row order is authoritative. Difftastic insert-only replacement rows may
be combined for presentation, but hunk identity remains attached to the backend
boundary that produced it.

The rendering layer combines each engine token partition with the syntax spans
for the same row side. The resulting ordered parts preserve every source
character and carry `syntax_classes`, `diff_status`, whitespace status, and
leading-whitespace status. Invalid engine tokens or syntax spans fail at this
backend boundary. `DiffGrid` renders these parts directly; it does not intersect
parallel token and syntax ranges or slice source text by backend offsets.

Each real hunk target contains all of:

- `data-file-index`;
- `data-hunk-index`;
- `data-hunk-kind="real"`;
- `data-hunk-target`.

Line-number elements contain only their rendered side and backend line number.
The surrounding `DiffGrid` supplies file and notebook-region identity.

Clicking the Comment trigger opens the one code-aligned composer without
changing URL, File loading, scrolling, or hunk selection. Shift-click extends
the active draft only when File, rendered region, and side are unchanged.
Ordinary line-number clicks retain line-pin behavior.

`ChangeSetShell` owns one document-level pointer listener for side selection. A
pointer-down inside a grid clears the previous grid marker and marks the current
grid as old- or new-side selection. DiffGrid’s semantic DOM and shared CSS use
that marker to limit native text selection to the chosen side.

## Folded lines

`folds.ts` converts backend fold hints into validated nested ranges. A fold
range containing a hunk boundary is a backend contract violation and throws.
The backend assigns hunk indexes and accepts foldable ranges from the same
canonical engine-row change classification. Fold discovery does not compare row
text independently or discard rows such as trailing blanks before deciding that
a range is unchanged.

`DiffGrid` stores expanded line folds locally. Clicking a fold edge explicitly
replaces that edge with its rows; folding replaces the rows with the fold edge
again. Fold edges are not pinnable line numbers.

Fold expansion changes only this grid’s row DOM. It does not select a hunk,
navigate, fetch, or change file collapse.

Changing identity-bearing rendering inputs rebuilds the grid and resets its
local expanded folds. Preserving expanded folds across split/inline replacement
remains a follow-up rather than a hidden reconstruction path.

## Rich and virtual FullFiles

Only hydrated text files alternate between rich and virtual representation.
Notebook files remain rich.

The row count selects one cost band:

| Cost | Rows | Rich-entry distance | Virtual-exit distance |
| --- | ---: | ---: | ---: |
| small | 0–250 | 2 viewports | 3 viewports |
| medium | 251–1000 | 4 viewports | 6 viewports |
| large | 1001+ | 8 viewports | 12 viewports |

The different entry and exit distances provide hysteresis. A text file becomes
fully rich before it reaches the viewport and returns to virtual only after it
moves farther away.

The initial representation is chosen from the mounted `FileCard` geometry. A
distant file may begin virtual before any rich height exists. When an expanded
rich body later becomes virtual, it is measured first and the virtual body uses
that exact height, contains overflow internally, and prevents the surrounding
page from jumping. A file that begins virtual uses its natural virtual height
until it has had a measurable rich body.

`VirtualFile` contains complete undecorated old and new text in split form so
native browser search can find both sides. It omits decorated parts, fold
interaction, and rich rows. Transparent real hunk anchors preserve every
backend hunk coordinate.

Rich/virtual replacement changes representation only. It does not change file
state, expansion, selected identity, counters, or URL state.

## Notebook rendering

`NotebookFile` renders the notebook summary and every backend cell in backend
order. Every cell has a unique, stable, non-empty backend `cell_key`, including
a cell whose source itself is unchanged.

Each cell gets its own `DiffGrid`; its line-pin region is that cell key.
Ordinary text uses a null region. Notebook metadata and outputs retain their
current presentation, while richer raw and interactive output modes remain
outside the current renderer.

Notebook structure does not change file indexes. Region identity extends a line
coordinate inside the file; it is not a second file identity.

## LazyFile

A `LazyFile` represents either:

- a manifest-deferred file with lazy metadata; or
- an ordinary file-query error; or
- an ordinary lazy-info failure presented through each manifest-lazy file.

Both use the ordinary Lazy header and one pseudo-hunk with the file index,
`kind: "lazy"`, and `hunkIndex: 0`. Collapsing keeps `kind: "lazy"` and the same
coordinates, moves the target into the collapsed anchor container, and adds
`.skip`. The shared File-level Comment trigger remains inside the sticky Lazy
header in deferred and failed states.

A deferred Lazy body is one colored, clickable plank. The plank is the sole
explicit load action. Its color records the lazy reason. FileTree retains the
non-error reason color after successful loading; the resulting `FullFile` card
uses ordinary FullFile presentation.

An error Lazy body shows the complete local `ErrorPanel` and a `RetryButton`.
Retry has no timeout and re-enters the shared file lane. Its header has no file
statistics because the error state contains no completed file or lazy-info
summary. A deferred Lazy header shows known added and removed values and `?` for
unavailable modified or moved values rather than inventing zeroes.

## Renderer failures

`FullFile` rendering is contained inside the stable `FileCard` article. An
unexpected renderer exception:

- reports a Toast;
- marks the article as a terminal renderer error;
- shows a critical unrecoverable strip;
- exposes no hunk target or Retry action.

This state does not pretend to be a backend `LazyFile`. It preserves the
smallest stable visual boundary that can still be rendered, but it does not
invent valid file content after an unexpected renderer failure.

## Expansion and state replacement

File expansion is stored by `ChangeSet` and shared by the file list and
FileTree. State replacement does not create another expansion value.

The important replacements are:

- queued Husk → fetching Husk;
- Husk → Full awaiting admission → admitted Full body;
- Husk → error Lazy;
- deferred Lazy → fetching Husk → Full or error Lazy;
- rich Full ↔ virtual Full;
- expanded Full/Lazy/Husk ↔ its collapsed presentation.

The article and manifest file index remain stable across each replacement.
Hunk kind may change, but every hunk target always carries a file index and hunk
index. A stale pseudo kind is never used as a substitute for missing
coordinates.

## Disposal

Disposing a `FileCard` disconnects its intersection observers and window resize
listener, removes DOM methods attached by `FullFile` or `DiffGrid`, and drops
local fold and render-representation state.

The card does not outlive its `ChangeSetSnapshot`. Query cancellation and
query-observer disposal remain the lane’s responsibility.

## Operations exposed to navigation

Every mounted `FullFile` attaches these operations to its stable article,
including the interval before body admission:

- `waitToEnrich_impl()` makes an admitted expanded text file rich and resolves
  after the rich body exists. It does nothing for collapsed or unadmitted
  content.
- `intersectsRichEntryZone(viewportTop)` answers whether the file’s cost-specific
  entry zone intersects a hypothetical destination viewport. It does not change
  representation.
- `prepareLine_impl(target, abortSignal)` expands the file, makes it rich,
  locates the exact ordinary or notebook `DiffGrid`, unfolds the requested line,
  and returns that row or a precise missing/stopped result. Calling it before
  admission is a contract error.

Each `DiffGrid` exposes its own `prepareLine_impl(target, abortSignal)`, which
expands containing line folds and returns the exact row. It does not scroll,
paint a pin, select a hunk, or fetch.

Navigation reads the card and hunk DOM contracts described above. File
presentation does not call `selectHunk()`.

## File-rendering invariants

- One manifest entry has one stable `FileCard` and one stable file index.
- `Husk`, `Lazy`, `zero`, `skip`, and real hunks all carry both file index and
  hunk index.
- Folded lines cannot contain hunk boundaries.
- Collapsing a file preserves real hunk coordinates as skipped targets.
- Only text `FullFile` bodies are virtualized; virtualization is whole-file.
- Virtual text retains both sides for native browser search. After a
  rich-to-virtual transition, it also retains the measured rich page height.
- Rich/virtual replacement does not change selection or application state.
- DiffGrid owns row DOM and line-pin paint; it does not own file loading or
  hunk selection.
- Unexpected renderer failure is not converted into a backend file error.
