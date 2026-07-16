# Corrected draft: virtualization, navigation and hunk selection

## 26. Scope

This section specifies:

- whole-file virtualization;
- DOM-owned hunk selection;
- rich/virtual FileBody replacement;
- local and global hunk counters;
- explicit and user-scroll navigation;
- HuskFile and LazyFile pseudo-hunks;
- FileTree highlighting derived from the selected hunk;
- line pins;
- browser text-side selection;
- HUD, Help, and Debug behavior;
- direct hotkeys;
- future notebook navigation regions.

It does not revisit:

- manifest and file request definitions;
- strict sequential file fetching;
- AppHeader loading messages;
- the semantic `HuskFile`, `FullFile`, and `LazyFile` boundaries;
- notebook backend response design;
- row virtualization.

Anything explicitly labelled TODO in this specification is a post-rewrite follow-up. It is not an unfinished implementation choice or an acceptance requirement for the rewrite itself.

## 27. Essential complexity

The system genuinely must handle:

1. A ChangeSet may contain enough rendered DOM to hurt memory, layout, and paint performance.
2. Files load over time, so a provisional global hunk sequence can change.
3. HuskFiles and LazyFiles do not yet know their real hunk structure.
4. Rich FileBody DOM may be removed and recreated.
5. Inline/split changes may replace rich row DOM.
6. Code folds replace unchanged row DOM without changing the hunk-target set.
7. A file folded directly or through its directory leaves navigation.
8. A structural owner may destroy the currently selected hunk.
9. User scrolling, programmatic scrolling, browser anchoring, and layout movement all produce browser scroll events.
10. Sticky AppHeader and FileHeader elements affect visible geometry.
11. FileTree, FileHeader, HUD, and rendered targets need projections of the selected hunk.
12. Notebook files contain several file-like regions inside one outer file.
13. Future raw/rich notebook modes may expose different numbers of hunks.
14. URL line pins may refer to content that has not rendered yet.
15. Browser native search must see both complete file sides while a file is virtual.

The accidental complexity currently includes:

- selected hunk identity stored in both DOM and a Solid signal;
- separately maintained `HunkPosition`;
- `activeHunkFileId`;
- global forced-rich file maps;
- global virtualized-file maps;
- layout, loading, and virtualization revision signals;
- a rich preload radius;
- delayed reconciliation timers;
- repeated animation-frame retries during FileTree navigation;
- page-scrolling functions outside the two authorized navigation systems;
- Debug sampling while Debug is closed.

The rewrite preserves the essential complexity while deleting those coordination mechanisms.

## 28. Selection model: persistent FileCard identity with replaceable targets

Hunk targets live inside the rendered FileBody.

Only the currently selected hunk identity survives outside FileBody, as DOM attributes on its stable FileCard.

```text
FileCard
├── selected-hunk identity attributes
├── FileHeader
└── FileBody switch
    ├── rich hunk targets
    └── virtual hunk targets
```

The target owns:

- its hunk identity;
- its position in current navigation order;
- visible selected decoration;
- viewport geometry;
- the scrolling destination.

The FileCard owns only the selected identity that must survive representation replacement.

There is:

- no selected-file state;
- no `data-selected-file`;
- no selected-file signal;
- no hidden marker for every hunk;
- no Solid selected-hunk signal.

FileTree highlighting is derived from the FileCard that currently owns selected-hunk attributes.

## 29. Hunk identities

The current backend already produces one file-global hunk sequence.

Real hunks therefore use:

```ts
type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};
```

HuskFile and LazyFile pseudo-hunks use:

```ts
type PseudoHunkIdentity = {
  fileIndex: number;
  kind: "husk" | "lazy";
  entryDirection: 1 | -1;
};
```

```ts
type HunkIdentity =
  | RealHunkIdentity
  | PseudoHunkIdentity;
```

`entryDirection` determines how a selected pseudo-hunk maps when its real file result arrives:

- `1` maps into the beginning;
- `-1` maps into the end;
- direct plank or FileTree activation behaves as forward entry.

Global positions such as `9/42` are derived display values. They are not identities.

## 30. DOM contract

A selected real hunk may look like:

```html
<article
  class="file-card"
  data-file-card
  data-file-index="3"
  data-file-state="full"
  data-file-render="rich"
  data-selected-hunk-kind="real"
  data-selected-hunk-index="2"
>
  <header class="full-file-header">
    <span data-hunk-counter="local"></span>
    <span data-hunk-counter="global"></span>
  </header>

  <div data-file-body>
    <div
      class="diff-row"
      data-hunk-target
      data-hunk-kind="real"
      data-file-index="3"
      data-hunk-index="2"
      data-selected
      aria-current="true"
    ></div>
  </div>
</article>
```

A pseudo-hunk target may look like:

```html
<button
  data-hunk-target
  data-hunk-kind="husk"
  data-file-index="4"
></button>
```

or:

```html
<button
  data-hunk-target
  data-hunk-kind="lazy"
  data-file-index="5"
></button>
```

At most one FileCard contains selected-hunk attributes.

When the selected target is mounted, exactly one matching target carries:

```html
data-selected
aria-current="true"
```

## 31. Hunk production

The backend remains authoritative for:

- real hunk boundaries;
- real file-local hunk indices;
- exact `hunk_count`.

The frontend does not infer hunks by grouping changed rows.

Rich and virtual representations of the same FullFile structure must expose the same participating real-hunk identities.

Code folds contain only unchanged rows between hunks. They never exclude a real hunk from participation. A fold range that contains a hunk target violates the backend/fold invariant rather than defining a supported navigation state.

Pseudo-hunks are frontend navigation entities:

- one for every HuskFile;
- one for every expanded LazyFile.

They are not reported as backend hunks.

## 32. File states and pseudo-hunks

### HuskFile

`HuskFile` contributes one provisional pseudo-hunk.

Its target may be its compact loading body or another stable target inside the card.

The Husk pseudo-hunk:

- participates in next/previous navigation;
- participates in the provisional global counter;
- is a FileTree destination;
- may be selected and scrolled to;
- never changes strict sequential request order;
- never starts a separate request.

When the automatic file response arrives, it is replaced with the resulting FullFile structure.

### LazyFile

An expanded `LazyFile` contributes one provisional pseudo-hunk through its colored plank.

It:

- participates in explicit navigation;
- participates in the provisional global counter;
- is a FileTree destination;
- may be selected and scrolled to;
- does not fetch merely because it was selected.

Only activating the colored plank starts its canonical file request.

A folded LazyFile contributes no target.

### FullFile

An expanded `FullFile` contributes its current participating real-hunk targets.

A folded FullFile contributes no targets.

A FullFile with zero hunks contributes no target unless a later explicit product requirement adds a separate non-hunk target.

## 33. Destructive structural transitions

Rich ↔ virtual is representation replacement. The same identity survives.

The following are structural transitions:

- HuskFile → FullFile;
- LazyFile → FullFile;
- folding a file;
- folding a directory containing files;
- future notebook raw/rich structure changes;
- replacing the complete ChangeSet snapshot.

A structural owner that is about to remove the currently selected hunk is responsible for repairing selection before completing its destruction.

This is one explicitly permitted non-user selection path.

### Pseudo-hunk replacement

If a selected Husk or Lazy pseudo-hunk becomes a FullFile:

- forward entry selects the first resulting real hunk;
- backward entry selects the last resulting real hunk;
- zero resulting hunks select the next target after that file;
- if no later target exists, select the previous target;
- if no target exists, clear hunk selection.

### Folding a file or directory

Before removing a selected hunk:

1. find the first target after the folded subtree;
2. otherwise find the last target before it;
3. call `selectHunk`;
4. only then remove the folded targets;
5. clear selection if the entire sequence disappears.

The folding operation never scrolls merely because it repaired selection.

### Code folds

Code folds contain unchanged lines between hunks. Expanding or collapsing one changes only unchanged row presentation. It never changes hunk participation, selected identity, local or global counters, or FileTree highlighting, and it never invokes structural selection repair.

The frontend must reject or expose as an invariant violation any fold range that contains a real hunk target. It must not implement navigation behavior for that impossible shape.

### Representation replacement

Rich ↔ virtual and inline ↔ split do not perform structural repair.

They preserve the selected identity and only project it onto the replacement target.

Here is the complete candidate heuristic. The architecture is settled; only the numeric thresholds should be tuned through browser testing.

## 34. Eligibility

Only hydrated text `FullFile`s participate.

- `HuskFile`: never virtualized.
- `LazyFile`: never virtualized.
- Notebook `FullFile`: always rich for now.
- Collapsed file: renders no body, so mode is irrelevant.
- Expanded text `FullFile`: either rich or virtual.
- No row-level virtualization.

## 35. Cost

Cost is simply the backend-provided row count:

```ts
const rowCount = file.rows.length;
```

Initial bands:

| Row count | Cost |
|---:|---|
| `0–250` | small |
| `251–1000` | medium |
| `1001+` | large |

```ts
type FileCost = "small" | "medium" | "large";
```

These thresholds are tuning constants, not API contracts.

## 36. Rich zones

More expensive files begin enriching earlier.

Initial candidate distances:

| Cost | Become rich within | Become virtual beyond |
|---|---:|---:|
| Small | 2 viewport heights | 3 viewport heights |
| Medium | 4 viewport heights | 6 viewport heights |
| Large | 8 viewport heights | 12 viewport heights |

```ts
type RichZone = {
  enterViewports: number;
  exitViewports: number;
};
```

The exit distance is always larger than the enter distance. Between the two boundaries, the file retains its current mode.

The zones are symmetrical above and below the viewport. Direction-aware zones are a post-rewrite TODO, not part of this rewrite.

## 37. Initial mode

The outer `FileCard` already exists as a Husk before file data arrives, so its geometry is available.

When the file result arrives:

- If its FileCard intersects its cost-dependent enter zone, start rich.
- Otherwise, start virtual.
- If geometry cannot yet be read, start virtual and let the observer correct it.

This avoids initially materializing every loaded file as rich.

## 38. Automatic transitions

Each text `FullFile` owns:

```ts
type FileRenderMode = "rich" | "virtual";

const [renderMode, setRenderMode] =
  createSignal<FileRenderMode>(initialRenderMode);
```

Intersection observers implement hysteresis:

```text
File intersects enter zone
        → rich

File leaves exit zone
        → virtual

File is between boundaries
        → retain current mode
```

Transitions may happen during active scrolling. They are not queued until `scrollend`.

Any file intersecting the actual viewport must become entirely rich. This includes gigantic files: there is no partial-rich representation.

The observer may be shared infrastructure, but every resulting mode remains `FullFile`-local state.

## 39. Explicit enrichment

Programmatic hunk navigation, FileTree navigation, and line-pin restoration use:

```ts
async function waitToEnrich(
  fileCard: HTMLElement,
): Promise<void>;
```

It locally changes the target FullFile to rich and resolves after the rich body mounts.

The caller then re-resolves its DOM target and scrolls.

There are no:

- Forced-rich file sets.
- Selected-file preload neighbourhoods.
- Permanently rich first/last files.
- Virtualization revisions.
- ChangeSet-wide virtualization state.

Once the explicitly enriched file enters its normal rich zone, ordinary observer policy resumes naturally.

## 40. Rich → virtual geometry

Immediately before replacing a rich body:

1. Measure `RichFileBody` height.
2. Store it locally as `reservedRichHeight`.
3. Replace the body with `VirtualFile`.
4. Give only `.virtual-file-body` that exact height.
5. Allow internal overflow without displaying a scrollbar.

```css
.virtual-file-body {
  height: var(--reserved-rich-height);
  overflow: auto;
  overscroll-behavior: auto;
  scrollbar-width: none;
}

.virtual-file-body::-webkit-scrollbar {
  display: none;
}
```

This makes rich → virtual preserve the FileBody’s outer height.

If the file has never been rich, `VirtualFile` uses its natural height. There is no fabricated rich-height estimate.

## 41. Virtual → rich geometry

`RichFileBody` always uses its natural document height. It is never constrained and never receives an internal scrollbar.

Normally, cost-dependent lead distance makes the file rich before it enters the viewport, so any height correction happens before the user sees the file.

Direct browser-find jumps and extremely fast jumps remain exceptional cases that require browser testing. We do not add a fixed height to RichFileBody to conceal them.

## 42. VirtualFile contract

`VirtualFile` always contains:

- Complete old-side text.
- Complete new-side text.
- A split presentation, regardless of global inline/split mode.
- Every participating real-hunk identity in the same order as RichFileBody.
- Local projection of the selected hunk, if this FileCard owns it.

It does not contain:

- Syntax spans.
- Inline-token spans.
- Rich row DOM.
- Decorations.
- Row virtualization.

Native browser search can search the overflowed text and scroll `.virtual-file-body` internally to reveal its match.

Whether the browser preserves the active highlighted match when VirtualFile is replaced by RichFileBody is an explicit browser-test case.

## 43. Other state changes

Inline/split change:

- Reconstructs rich DOM.
- Does not affect virtual DOM.
- Does not change render mode.
- VirtualFile remains split.

Engine change:

- Produces new file query results.
- Recalculates the row-count band.
- Re-registers the relevant observer margins.
- May reuse the previous height as a provisional geometry hint for the same FileCard, but it is not treated as authoritative.

File collapse:

- Unmounts the body.
- Retains harmless local measurements.
- Removes the file from hunk navigation.
- Re-evaluates proximity when expanded.

## 44. Representation invariants

Rich ↔ virtual replacement:

- Does not select or clear a hunk.
- Does not update counters.
- Does not update FileTree highlighting.
- Does not fetch.
- Does not intentionally scroll the page.
- Does not publish layout or virtualization revisions.
- Does not change the set or order of participating hunk identities.
- Reprojects selected decoration locally.
- May occur while the user is scrolling.

Scroll-follow may subsequently observe normal DOM geometry through its one existing throttled path. Virtualization itself never invokes selection.

## 45. Post-rewrite TODO: syntax-highlighting optimization

After the rewrite, disabling syntax highlighting outside the viewport may introduce:

```text
VirtualFile
    → structural rich body without syntax spans
    → fully decorated RichFileBody
```

That is not part of this rewrite. For the rewrite, rich means completely rich.

## 46. Post-rewrite TODO: intrinsic-size investigation

The current FileBody CSS contains:

```css
.file-card-body {
  content-visibility: auto;
  contain-intrinsic-size: 180px;
}
```

This optimization may predate whole-file virtualization. It may still provide useful performance by allowing the browser to skip work for distant content, but its fixed `180px` substitute size may also conflict with VirtualFile’s natural or measured document geometry and amplify backward-scroll corrections when the browser replaces the substitute size with the real height.

The rewrite may initially preserve it, but must not declare it permanently required or permanently removable by assumption. After the rewrite, it requires a browser investigation against the new rich/virtual design.

The investigation must compare the same representative ChangeSets with the optimization enabled and disabled, and determine:

- whether it still materially reduces layout, rendering or initial-load cost after distant files already render as VirtualFile;
- whether it prevents the browser from using `reservedRichHeight` as the effective document geometry;
- whether its fixed substitute height causes visible movement while scrolling forward or backward;
- how it interacts with native scroll anchoring;
- how it interacts with IntersectionObserver rich-zone boundaries;
- how it interacts with browser native search entering distant VirtualFiles.

Possible outcomes are:

- remove it because VirtualFile already supplies the required offscreen reduction;
- retain it only where measurements demonstrate an additional benefit without destabilizing geometry;
- replace the fixed `180px` substitute with a per-file value derived from the same measured geometry used by VirtualFile.

The outcome is a measured post-rewrite decision, not an architectural assumption.

## 47. Required wrapped-Previous reverse-traversal stress test

The virtualization and scroll-anchoring design must be tested from the least convenient starting condition:

1. Load only the manifest and begin strict sequential file fetching from the start.
2. While later files are still HuskFiles, select the first available hunk near the beginning.
3. Invoke Previous once and wrap to the final manifest target.
4. If the final target is a HuskFile, navigate immediately to its pseudo-hunk without waiting for its file request.
5. Continue sequentially loading and enriching earlier FileCards above the viewport.
6. Walk backward from the end toward the start through a changing mixture of HuskFile, LazyFile, VirtualFile and rich FullFile targets.
7. Allow selected HuskFiles to be destructively replaced by their real hunk sets while preserving the backward entry direction.
8. Exercise ordinary backward wheel, touch and keyboard scrolling between explicit Previous commands.

This must use an elaborate preset derived from one or more real diffs rather than a tiny synthetic list. The preset should contain:

- enough files that the final manifest target remains unloaded when Previous wraps;
- small, medium and large text files;
- files whose rich and virtual heights differ substantially;
- files with many syntax spans and hunks;
- folded code and collapsed files;
- LazyFiles mixed between eager files;
- at least one selected HuskFile that becomes a multi-hunk FullFile;
- enough sequential loading time for several earlier FileCards to change height while the user remains near the end;
- file ordering and content shapes taken from real repository diffs that have previously stressed layout or enrichment.

The test must observe:

- the selected target’s viewport position while earlier FileCards hydrate above it;
- unexpected changes to selected hunk identity;
- correct backward repair from a selected Husk pseudo-hunk to the new file’s last participating real hunk;
- global and local counter changes as pseudo-hunks become real hunks;
- visible layout jumps during first-time virtual → rich transitions above the viewport;
- rich → virtual height preservation for previously measured files;
- native scroll anchoring behavior at and near the document end;
- scroll-follow behavior while representation and structural transitions occur;
- browser native search inside fixed-height VirtualFiles;
- DOM node and span counts throughout the traversal;
- long tasks, dropped frames and enrichment duration for each row-count band.

The scenario should run with the intrinsic-size optimization enabled and disabled. That comparison determines whether the existing optimization helps the new architecture or merely adds a second source of provisional geometry.

So the short version is:

> Every distant hydrated text file is virtual. Row count determines how far ahead it becomes completely rich and how far away it must travel before becoming virtual again. Rich → virtual preserves height through a fixed, internally scrollable VirtualFile; RichFileBody always remains natural document content.
## 48. Selection projection

When rich/virtual or inline/split replacement destroys visible targets, selected identity remains on FileCard.

One narrow local operation restores decoration:

```ts
function projectSelectedHunk(
  fileCard: HTMLElement,
): void;
```

It:

1. reads selected-hunk attributes from FileCard;
2. finds the unique matching target in current FileBody;
3. removes stale selected decoration within that FileCard;
4. marks the matching target;
5. asserts if representation-only replacement unexpectedly lost the identity.

It never:

- selects another hunk;
- clears selection;
- updates counters;
- updates FileTree;
- scrolls;
- fetches;
- enriches;
- expands anything.

This is a local DOM projection, not selection reconciliation.

The implementation may use a FileBody-ready callback, ref, or scoped DOM event. It must not use revision signals, delayed timers, or repeated animation frames.

## 49. Rich materialization

Exact hunk and line navigation may require rich geometry.

The operation is named:

```ts
async function waitToEnrich(
  fileCard: HTMLElement,
): Promise<void>;
```

If already rich, it returns immediately.

Otherwise:

```text
request local rich mode
        │
        ▼
FullFile changes renderMode
        │
        ▼
rich FileBody mounts
        │
        ▼
selected decoration is projected
        │
        ▼
waitToEnrich resolves
```

`waitToEnrich`:

- changes only FullFile-local rendering;
- does not select a hunk;
- does not update counters;
- does not scroll;
- does not fetch.

Navigation re-resolves its target after `waitToEnrich`.

## 50. Hunk selection

There is no file-selection operation.

The central primitive is:

```ts
function selectHunk(
  root: HTMLElement,
  hunk: HunkIdentity,
): void;
```

It:

1. resolves the current target for the identity;
2. removes selected identity from the previous FileCard;
3. removes previous target decoration;
4. writes the new identity on the owning FileCard;
5. decorates the resolved target;
6. updates hunk counters;
7. updates FileTree highlighting from the owning FileCard;
8. does not scroll.

A separate operation clears selection:

```ts
function clearHunkSelection(
  root: HTMLElement,
): void;
```

It:

- removes selected identity;
- removes selected-target decoration;
- clears FileTree highlighting;
- updates counters;
- does not select a replacement;
- does not scroll.

Only these paths may select or reselect a hunk:

1. Next/Previous navigation.
2. FileTree navigation to a file’s first hunk.
3. Recognized user-scroll following.
4. Husk or Lazy plank activation.
5. A destructive structural owner repairing selection before removal.
6. Explicit line-pin behavior, only if later specified to select a hunk.

Rendering, virtualization, Debug, counters, and ordinary reconciliation never select.

## 51. Hunk counters

Every specialized FileHeader provides space for local and global hunk information appropriate to its state.

### FullFileHeader

```text
Local —/5    Global —/42+
```

When selected:

```text
Local 2/5    Global 9/42+
```

### HuskFileHeader

The exact local count is unknown:

```text
Local pending    Global 9/42+
```

### LazyFileHeader

The exact local count is deferred:

```text
Local deferred    Global 9/42+
```

The global sequence includes:

- one pseudo-hunk for every HuskFile;
- one pseudo-hunk for every expanded LazyFile;
- participating real hunks in expanded FullFiles.

It excludes:

- folded files;

Code-line folds exclude no real hunk targets.

The `+` suffix means one or more pseudo-hunks may later become a different number of real hunks.

The local FullFile position comes from the backend file-local hunk index and exact `hunk_count`.

The global position comes from current participating target order.

Counters are imperative DOM projections. There is no `HunkPosition` Solid signal.

Counters update only when:

- `selectHunk` runs;
- `clearHunkSelection` runs;
- a destructive structural transition changes the target sequence.

Rich ↔ virtual and inline ↔ split do not update counters.

The Debug HUD reads the same derived counter.

## 52. Notebook behavior and future region keys

Current notebook behavior already provides one file-global source-hunk sequence.

Today:

- one outer notebook FileCard renders several cell cards;
- each changed cell source renders through its own DiffGrid;
- the backend offsets cell-local source hunk indices into one file-wide sequence;
- every cell source uses the same outer file index;
- notebook metadata is summary-only;
- cell metadata is summary-only;
- outputs are summary-only;
- metadata and outputs contribute no hunks;
- notebooks remain rich and are not virtualized.

Therefore current selection remains:

```ts
type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};
```

### Post-rewrite TODO: notebook navigation regions

The architecture must not assume forever that one file body is one grid.

A future notebook may remain one outer FullFile while containing:

```text
FullFile: notebook.ipynb
├── notebook metadata
├── cell A
│   ├── source
│   ├── metadata
│   ├── output 0 raw JSON
│   └── output 0 rich Plotly
├── cell B
│   ├── source
│   └── output 0 image
└── cell C
    └── source
```

When metadata and output navigation are implemented, identity may extend to:

```ts
type RegionHunkIdentity = {
  fileIndex: number;
  regionKey: string;
  itemKey: string;
};
```

Examples:

```text
regionKey = "cell:abc123:source"
itemKey   = "hunk:1"

regionKey = "cell:abc123:output:0:raw"
itemKey   = "hunk:4"

regionKey = "cell:abc123:output:0:rich"
itemKey   = "plot"
```

The actual global `9/42` remains derived from current target order. It is not identity.

Raw/rich output changes may replace N hunks with M hunks. That is a destructive structural transition, not representation-only virtualization.

Its owner must repair a selected identity before destroying it.

Region keys are a future extension, not part of the initial rewrite.

## 53. Main-page hunk navigation gateway

One interface owns ordinary hunk, FileTree-target, and return-to-top movement:

```ts
type PageNavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | { kind: "hunk"; hunk: HunkIdentity }
  | { kind: "top" };

async function navigate(
  root: HTMLElement,
  command: PageNavigationCommand,
): Promise<void>;
```

There are no file or directory navigation commands.

Outside line-pin restoration, no other code may call main-page:

- `window.scrollTo`;
- `scrollIntoView`;
- `scrollBy`.

Line-pin restoration is an explicit second authorized viewport-moving system because it must repeatedly restore a target while asynchronous file rendering and layout continue. It is specified separately and is not forced through this one-shot gateway.

### Next and previous

```text
read selected identity from FileCard DOM
        │
        ▼
query participating targets in DOM order
        │
        ▼
choose adjacent target
        │
        ▼
selectHunk
        │
        ▼
waitToEnrich when real target needs rich geometry
        │
        ▼
resolve matching rich target
        │
        ▼
perform one scroll
```

Selecting a Husk or Lazy pseudo-hunk does not enrich or fetch it.

### Direct hunk navigation

FileTree and other callers may resolve a concrete hunk and dispatch:

```ts
{ kind: "hunk", hunk }
```

It uses the same selection, enrichment, and scrolling sequence.

### FileTree target

A FileTree file target is not file navigation.

It:

1. explicitly unfolds the file if needed;
2. resolves the file’s first participating hunk;
3. dispatches ordinary hunk navigation.

For HuskFile or LazyFile, that first hunk is its pseudo-hunk.

A FullFile with no hunks has no hunk-navigation destination.

Directories do not navigate the page.

### Top

Return-to-top scrolls to zero and preserves selected hunk identity.

## 54. Wrapping and provisional targets

Because every manifest file has either:

- a Husk pseudo-hunk;
- a Lazy pseudo-hunk;
- zero or more real hunks;

the provisional sequence can represent unloaded files without fetching them out of order.

Next and Previous may traverse:

- real hunks;
- Husk pseudo-hunks;
- expanded Lazy pseudo-hunks.

They may wrap through the current provisional sequence.

As pseudo-hunks become real structures:

- the target count may change;
- the `+` suffix remains while any pseudo-hunk exists;
- structural handoff preserves or repairs selection;
- fetch order remains strict manifest order.

Hunk navigation never changes request order.

## 55. Scroll-source gate and throttled scroll-follow

```ts
type PageScrollSource =
  | "idle"
  | "user"
  | "command";
```

This is a local non-reactive controller variable.

It is not application state.

### Entering user scroll

Wheel, touch movement, and native scrolling keys may set:

```text
idle → user
```

Input at the corresponding document boundary does not arm user scrolling.

### Command scrolling

The navigation gateway sets:

```text
idle → command
```

before moving the viewport.

Rich/virtual transitions operate independently of this scroll-source gate and may occur while either source is active.

### Throttling

The `scroll` listener does not walk DOM directly for every event.

```ts
let followFrame: number | null = null;

function scheduleScrollFollow() {
  if (followFrame !== null) {
    return;
  }

  followFrame = requestAnimationFrame(() => {
    followFrame = null;
    followScrollNow();
  });
}
```

This permits at most one geometry walk per animation frame.

### Completion

On `scrollend`:

1. perform or schedule one final scroll-follow sample;
2. complete that sample;
3. set the source to `idle`.

Instant command navigation may return to `idle` after its final settled animation frame if no longer-running scroll sequence exists.

No user/command source remains active indefinitely.

### User-scroll selection

During recognized user scrolling:

1. find the visible real hunk target at the reading line;
2. call `selectHunk` if it changed;
3. otherwise preserve current selection.

User-scroll following does not automatically select Husk or Lazy pseudo-hunks merely because their card crosses the reading line.

It never:

- scrolls;
- enriches;
- expands;
- fetches;
- updates virtualization state.

Layout movement while the source is `idle` never changes selection.

## 56. FileTree highlighting and targets

FileTree highlighting is derived from the selected hunk’s owning FileCard.

```text
selected hunk target
        │
        ▼
closest FileCard
        │
        ▼
matching FileTree row gets aria-current
```

There is no:

- selected-file state;
- `activeHunkFileId`;
- file-selection command.

When `selectHunk` changes ownership, it updates the derived FileTree projection.

When a destructive transition clears the last selection, FileTree has no highlighted file.

When FileTree opens:

1. find the FileCard containing selected-hunk attributes;
2. find the matching FileTree row;
3. apply `aria-current`;
4. reveal that row inside the FileTree scroll container.

Sidebar auto-reveal moves only the sidebar.

Clicking a FileTree file target:

1. unfolds it if necessary;
2. resolves its first hunk or pseudo-hunk;
3. dispatches ordinary hunk navigation.

Directory rows only control directory expansion. They do not navigate the page.

## 57. Line pins and restoration

Line-pin restoration keeps its current behavioral and implementation shape.

There is no standard browser API that reliably keeps one exact line at one viewport position while its file loads, enriches, folds, and repeatedly changes layout.

Native fragment navigation and `scrollIntoView` are one-shot operations. CSS scroll anchoring lets the browser choose an anchor and may help opportunistically, but it does not let the application nominate the pinned line or guarantee continued restoration.

Therefore line pins retain:

- the current URL pin representation;
- the current pin-highlighting behavior;
- repeated restoration as files load, enrich, and change layout;
- the ability to re-scroll until the rendered target stabilizes;
- their own restoration controller;
- their own authorized viewport-moving path.

Line-pin restoration is intentionally not forced through the one-shot hunk navigation gateway.

It remains isolated from hunk selection:

- restoring a pin never selects or reselects a hunk;
- pin retries never update hunk counters;
- pin retries never update FileTree highlighting;
- hunk reconciliation never calls pin restoration.

The initial frontend rewrite may mechanically adapt pin restoration to new component names and `waitToEnrich`, but it must not redesign or simplify away its polling, retry, or stabilization behavior without a separate investigation demonstrating equivalent real-world behavior.

Line-pin restoration may be revisited only if:

- browsers expose a genuinely controllable persistent-anchor API; or
- a separate, browser-verified stabilization design proves at least as reliable as the current implementation.

CSS scroll anchoring remains relevant to the rich/virtual heuristic, but it is not a replacement for line-pin restoration.

## 58. Browser text-side selection

The existing side-selection behavior remains visually and functionally intact.

```html
<div
  class="diff-grid"
  data-diff-selection-side="left"
></div>
```

One delegated pointer handler:

- determines whether pointer-down occurred on the left or right;
- records the side on that DiffGrid;
- removes the previous side marker;
- clears it when selection begins outside a diff side.

No Solid signal is needed.

This state is independent from:

- hunk selection;
- line pins;
- inline/split workspace view.

## 59. Hint HUD, Help, and Debug

The Hint HUD, Help modal, and Debug HUD remain visually exactly as they are.

This includes:

- the existing Hint HUD buttons and labels;
- the existing Help modal layout and content, except for removal of the Show All and Fold All rows;
- the existing Debug HUD layout;
- FPS;
- whole-document node count;
- whole-document span count;
- the hunk counter.

The Show All and Fold All controls in the ChangeSet title area are removed. No other visual redesign is part of this architecture.

### Debug implementation

Debug retains:

```ts
type DebugMetrics = {
  fps: string;
  nodes: string;
  spans: string;
  hunks: string;
};
```

It continues to calculate:

```ts
document.querySelectorAll("*").length;
document.querySelectorAll("span").length;
```

The implementation improvement is lifecycle-only:

```tsx
<Show when={debugOpen()}>
  <DebugHud />
</Show>
```

Mounting starts its sampler.

Unmounting cancels it.

When Debug is closed:

- no RAF runs;
- no DOM counts run;
- no metric signals update.

Its hunk value is read from the DOM-derived counter rather than a `HunkPosition` signal.

Debug observes. It never:

- selects;
- scrolls;
- enriches;
- fetches;
- repairs.

## 60. Hotkeys

One private `Hotkeys` lifecycle component is mounted only for the active Tab. It owns the application’s single hotkey listener and calls concrete owner operations directly.

There is no generic hotkey command, parser, router, dispatch function, registry, or grouped owner interface. `NavigationCommand` remains the explicit typed input to `navigation.navigate(...)`; it does not form a generic application command system.

The mappings are:

| Key | Operation |
|---|---|
| `n` | navigate to the next hunk |
| `N` | navigate to the previous hunk |
| `p` | navigate to the top |
| `t` | toggle the active ChangeSet FileTree |
| `i` | toggle the workspace inline/split view |
| `r` | reload the active ChangeSet |
| `d` | toggle Debug |
| `h` | toggle Help |

The `s` and `f` hotkeys do not exist in the rewrite. Show All and Fold All are removed rather than routed elsewhere.

Editable targets, modified shortcuts, and already-prevented events retain their native behavior. A recognized hotkey calls `preventDefault()` before invoking its concrete operation.

Inactive Tabs retain their DOM but do not mount a hotkey listener. Buttons call their actual owner operations directly. Sections 66.24–66.26 specify the exact lifecycle and ignored-input behavior.

## 61. Ownership summary

| Concern | Authority |
|---|---|
| File order | Manifest order reflected by FileCard DOM |
| Real hunk identity | Backend file/hunk index on DOM target |
| Husk pseudo-hunk | HuskFile DOM target |
| Lazy pseudo-hunk | LazyFile plank DOM target |
| Navigation order | Participating hunk-target DOM order |
| Selected hunk identity | Attributes on its owning FileCard |
| Visible selection | Matching current hunk target |
| FileTree highlight | Projection from selected hunk’s FileCard |
| Local/global counters | DOM-derived imperative header projections |
| Rich/virtual mode | FullFile-local Solid signal |
| Virtualization trigger | Local/shared IntersectionObserver heuristic |
| Measured height | FullFile-local DOM measurement |
| Hunk, FileTree-target, and top scrolling | Main navigation gateway |
| Scroll source | Navigation-local ephemeral variable |
| Line pin | Current URL representation, rendered projection, and restoration controller |
| Text-selection side | DiffGrid DOM attribute |
| Help visibility | Existing local HUD signal |
| Debug visibility | Existing local HUD signal |
| Debug metrics | Existing visual model, sampled only while open |
| Keyboard mapping | Pure command parser |
| Keyboard execution | Delegation to actual owners |

## 62. Concepts removed

The rewrite removes:

- `currentIdentity` as a Solid selection authority;
- `HunkPosition` application state;
- selected-file state;
- `activeHunkFileId`;
- `forcedRichFileIds`;
- `virtualizedFileIds`;
- rich preload radius;
- layout revision;
- virtualization revision;
- loading revision as a navigation dependency;
- delayed hunk reconciliation timers;
- FileTree animation-frame stabilization loops;
- file navigation commands;
- directory navigation commands;
- always-running Debug RAF;
- application-level counters updated by virtualization.

The rewrite retains:

- one HuskFile pseudo-hunk;
- one expanded LazyFile pseudo-hunk;
- DOM-selected hunk state;
- destructive structural selection repair;
- throttled user-scroll following;
- a small scroll-source gate;
- `waitToEnrich`;
- local selected-decoration projection;
- the current HUD, Help, and Debug visuals;
- current browser text-side selection.

## 63. Required invariants

1. FileCard DOM order equals manifest order.
2. Every HuskFile exposes exactly one pseudo-hunk.
3. Every expanded LazyFile exposes exactly one pseudo-hunk.
4. Every folded file exposes no hunk target.
5. Every expanded FullFile exposes all of its real-hunk targets; code-line folds do not change that set.
6. Rich and virtual representations expose identical real-hunk identities.
7. Inline and split rich representations expose identical real-hunk identities.
8. VirtualFile always contains complete old and new text in split form.
9. VirtualFile does not depend on global inline/split view.
10. At most one FileCard contains selected-hunk identity.
11. When mounted, exactly one target matches and projects selected identity.
12. No independent selected-file state exists.
13. FileTree highlighting derives only from selected hunk ownership.
14. Rich ↔ virtual changes no non-local state.
15. Rich ↔ virtual does not update counters.
16. Representation projection never selects or scrolls.
17. A destructive owner repairs selected-hunk state before removing its target.
18. Structural repair uses `selectHunk` or clears selection.
19. Folding never leaves selection pointing at a removed target.
20. Hunk navigation never changes backend request order.
21. Selecting a pseudo-hunk never starts its request.
22. Only explicit LazyFile activation starts lazy hydration.
23. Only the main navigation gateway and line-pin restoration controller intentionally move the page viewport.
24. FileTree navigation resolves to ordinary first-hunk navigation.
25. Directory rows do not navigate the page.
26. Scroll-follow performs at most one DOM walk per animation frame.
27. The final scroll-follow sample completes before returning to `idle`.
28. Automatic rich/virtual transitions may run during active scrolling, but virtualization never invokes selection or scrolling.
29. Layout changes while scroll source is `idle` never alter selection.
30. Browser scroll anchoring is preserved but not treated as a correctness guarantee.
31. Height preservation is best effort.
32. Counters change only through selection or structural target changes.
33. Debug retains FPS, Nodes, Spans, and Hunks visually.
34. Closed Debug performs no sampling.
35. Hint HUD and Debug HUD remain visually unchanged. Help remains visually unchanged except for removal of the Show All and Fold All rows.
36. Browser text-side selection remains intact.
37. Repository changes, F5, and other workspace replacement boundaries clear selection by replacing ChangeSet DOM.
38. Future notebook region keys extend identity without making global counter position part of identity.
