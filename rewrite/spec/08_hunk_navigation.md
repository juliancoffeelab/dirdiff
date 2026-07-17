# Hunk navigation

## Scope and separation

Hunk navigation and line pins are separate systems.

Hunk navigation owns:

- real and pseudo hunk tokens;
- selected hunk identity;
- Next and Previous traversal;
- direct hunk navigation;
- FileTree hunk destinations;
- recognized user-scroll selection;
- local and global hunk counter calculation;
- FileTree highlighting;
- hunk scrolling;
- HintHud and DebugHud hunk information.

Line-pin behavior is outside this specification. `NavigationProvider` owns no line-pin state, parsing, listeners, timers, or restoration behavior. Line pins receive their own separate design and implementation stage.

Whole-file virtualization remains hunk-blind. The later hunk-navigation implementation may place hunk tokens in rich and virtual representations, but hunk state never influences virtualization eligibility, cost, mode, geometry, or observation.

## Hunk identity and DOM

`HunkIdentity` describes a real object constructed by the component rendering a participating hunk target:

```ts
type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};

type PseudoHunkIdentity =
  | {
      fileIndex: number;
      kind: "husk" | "lazy" | "zero";
    }
  | {
      fileIndex: number;
      kind: "skip";
      hunkIndex: number;
    };

type HunkIdentity =
  | RealHunkIdentity
  | PseudoHunkIdentity;
```

The object is local to that renderer. It is not stored in a signal, provider, store, or cache. Its fields are written directly into JSX. After rendering, the DOM is authoritative.

There is no conversion helper. Real, Husk, Lazy, zero, and skipped renderers write their concrete identity fields directly into their own JSX.

A real hunk row does this directly:

```tsx
const identity: RealHunkIdentity = {
  fileIndex: props.fileIndex,
  kind: "real",
  hunkIndex: row.hunk_index,
};

return (
  <div
    data-hunk-target
    data-hunk-kind={identity.kind}
    data-file-index={identity.fileIndex}
    data-hunk-index={identity.hunkIndex}
  >
    ...
  </div>
);
```

A HuskFile does this directly:

```tsx
const identity: PseudoHunkIdentity = {
  fileIndex: props.fileIndex,
  kind: "husk",
};

return (
  <div
    data-hunk-target
    data-hunk-kind={identity.kind}
    data-file-index={identity.fileIndex}
  >
    ...
  </div>
);
```

An expanded LazyFile’s visible explicit-load plank carries:

```tsx
const identity: PseudoHunkIdentity = {
  fileIndex: props.fileIndex,
  kind: "lazy",
};

return (
  <button
    data-hunk-target
    data-hunk-kind={identity.kind}
    data-file-index={identity.fileIndex}
    onClick={props.fetch}
  >
    ...
  </button>
);
```

Selecting or navigating to that button does not activate it. Only a direct button activation starts the fetch.

A loaded FullFile with no real hunks renders a visible zero-hunk target:

```tsx
const identity: PseudoHunkIdentity = {
  fileIndex: props.fileIndex,
  kind: "zero",
};

return (
  <div
    data-hunk-target
    data-hunk-kind={identity.kind}
    data-file-index={identity.fileIndex}
  >
    ...
  </div>
);
```

A zero target participates in navigation and counters. It does not add `+`, because the loaded file’s hunk count is exact.

## Participating targets

The complete traversal set is always:

```css
[data-hunk-target]:not(.skip)
```

Navigation reads identity attributes only from elements matching that selector.

DOM order is navigation order:

- manifest file order between files;
- backend `hunk_index` order inside loaded files.

Real targets require:

```text
data-hunk-kind="real"
data-file-index
data-hunk-index
```

Participating pseudo-targets require:

```text
data-hunk-kind="husk" | "lazy" | "zero"
data-file-index
```

Malformed participating targets are application errors.

A collapsed FullFile replaces each real target with a `skip` pseudo carrying the same `fileIndex` and `hunkIndex`:

```tsx
const identity: PseudoHunkIdentity = {
  fileIndex: props.fileIndex,
  kind: "skip",
  hunkIndex: row.hunk_index,
};

return (
  <div
    data-hunk-target
    data-hunk-kind={identity.kind}
    data-file-index={identity.fileIndex}
    data-hunk-index={identity.hunkIndex}
    class="skip"
  />
);
```

The `.skip` class, not the `kind` value, controls participation.

## Backend hunk invariants

The backend remains authoritative for:

- real hunk boundaries;
- file-local hunk indices;
- exact `hunk_count`.

The frontend does not infer hunks by grouping changed rows.

A loaded FullFile has an exact non-negative `hunk_count`. Its real identities are contiguous:

```text
0 .. hunk_count - 1
```

The participating rich or virtual representation must contain exactly one token for each real identity.

Duplicate, missing, negative, or out-of-range indices are application errors.

A FullFile may legitimately have zero hunks. Existing examples include notebooks whose metadata or outputs changed while their source did not. Pure renames, empty-file changes, and mode-only changes may also produce no rendered text hunk.

A FullFile with `hunk_count === 0` has no real hunk token and contributes exactly one participating zero pseudo-target.

## DiffGrid token placement

`DiffGrid` reads `hunk_index` directly from validated backend rows.

It does not need:

- `HunkDiffRow`;
- `isHunkAnchor`;
- `markHunkAnchors`;
- hidden hunk elements;
- inferred hunk boundaries.

When `row.hunk_index !== null`, the rendered row representing that backend hunk boundary receives the real token attributes.

When inline rendering turns one backend row into two visible rows, exactly one of those rows receives the token. It is the first visible row representing that backend hunk boundary.

Rich, virtual, inline, and split rendering must preserve the same real identities, although their token elements and geometry may differ.

## Folded lines

Folded lines and collapsed files are different concepts.

A folded-line range contains only unchanged context between hunks.

Therefore:

- folded lines cannot contain a non-null `hunk_index`;
- folding or unfolding lines does not change hunk participation;
- folding lines does not add `.skip`;
- folding lines does not remove a real hunk token;
- folding lines does not change selection;
- folding lines does not change counters;
- folding lines does not change FileTree highlighting.

Before constructing a folded-line range, the frontend asserts that no row inside it carries a hunk boundary.

If such a range contains a hunk boundary, the frontend throws an application error. It does not manufacture hidden tokens or implement behavior for the invalid shape.

`hunkSkipAnchors()` and equivalent synthetic folded-line tokens do not exist.

## Collapsed files and directories

Collapsed files do not participate in traversal.

A queued or loading HuskFile is bodyless, but that loading presentation is not an explicit file collapse for navigation. Its Husk pseudo-target participates immediately. Only explicit file collapse or containing-directory collapse adds `.skip`.

A collapsed HuskFile, LazyFile, or zero-hunk FullFile keeps its existing pseudo-target and adds `.skip`.

A collapsed FullFile with real hunks replaces every real target with a corresponding `skip` pseudo-target carrying the same `fileIndex` and `hunkIndex`.

For a file with three real hunks:

```html
<div
  data-hunk-target
  data-hunk-kind="skip"
  data-file-index="3"
  data-hunk-index="0"
  class="skip"
></div>
<div
  data-hunk-target
  data-hunk-kind="skip"
  data-file-index="3"
  data-hunk-index="1"
  class="skip"
></div>
<div
  data-hunk-target
  data-hunk-kind="skip"
  data-file-index="3"
  data-hunk-index="2"
  class="skip"
></div>
```

Each skipped pseudo preserves the real hunk's coordinates. The selected real identity on FileCard remains unchanged.

A skipped target:

- remains DOM;
- is excluded from Next and Previous destinations;
- is excluded from scroll-follow;
- is excluded from counters;
- is not a FileTree navigation destination;
- may be used as the scroll-back anchor matching a selected real hunk.

Collapsing a file directly replaces its current target representation with skipped pseudos and collapses its FileBody.

Collapsing a directory performs that same direct operation on every affected FileCard.

Neither operation:

- selects another hunk;
- clears selection;
- maps identity;
- scrolls;
- queues a later selection change;
- publishes a notification;
- writes Solid selected-hunk state.

Expanding a file or directory replaces skipped pseudos with its ordinary Husk, Lazy, zero, or real targets. It does not automatically select or decorate a target.

## Selected identity in DOM

The selected identity is retained directly on the owning FileCard:

```html
<article
  data-file-card
  data-file-index="3"
  data-selected-hunk-kind="real"
  data-selected-hunk-index="2"
>
</article>
```

For a pseudo-hunk:

```html
<article
  data-file-card
  data-file-index="3"
  data-selected-hunk-kind="husk"
>
</article>
```

`selectHunk()` copies the target’s concrete dataset fields directly onto its FileCard. It does not call an identity reader, writer, serializer, or conversion helper.

The currently selected target additionally carries:

```html
data-selected
aria-current="true"
```

If rendering removes that target, selected identity remains on the stable FileCard.

Selected identity may temporarily lack a participating matching target while representation DOM is being replaced. A collapsed selected real hunk still has its coordinate-matching `skip` pseudo available as a scroll-back anchor.

Rich/virtual and inline/split replacement preserve real `fileIndex` and `hunkIndex`. Rendering does not recreate selected decoration automatically.

## File-state replacement

HuskFile contributes exactly one participating Husk pseudo-target.

An expanded LazyFile, including its localized error presentation, contributes exactly one participating Lazy pseudo-target. Collapsing it adds `.skip`; selecting or traversing it never fetches it.

An expanded FullFile with real hunks contributes exactly its backend-produced real targets.

An expanded FullFile with `hunk_count === 0` contributes exactly one zero pseudo-target.

When HuskFile or LazyFile becomes FullFile:

- its old pseudo-target disappears;
- FullFile places either real targets or one zero target;
- selected identity on FileCard is not rewritten;
- no target is selected automatically;
- no scrolling occurs.

A later explicit Next, Previous, FileTree, or recognized user-scroll action may select a resulting target. Rendering itself performs no mapping.

## Navigation module and Provider

Navigation lives in:

```text
frontend/src/new/hud/navigation.tsx
```

It exports the identity contracts, `NavigationCommand`, `Navigation`, `NavigationProvider`, and `useNavigation`.

```ts
export type NavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | { kind: "hunk"; target: HTMLElement }
  | { kind: "top" };

export type Navigation = {
  navigate(command: NavigationCommand): Promise<void>;
};
```

A direct hunk command receives the concrete participating DOM target. Navigation validates that the target belongs to its ChangeSet root. It does not reconstruct a second registry of identities.

`NavigationProvider` owns one disposable controller for one mounted active ChangeSet. Context only delivers that same instance to Hotkeys, HintHud, FileTree, and other consumers. It does not make navigation global or move truth out of DOM.

```ts
export type NavigationProviderProps = {
  root: Accessor<HTMLElement>;
  children: JSX.Element;
};
```

The root is required. The Provider never falls back to `document`.

The controller may retain only ephemeral browser-work state:

```ts
type NavigationScrollSource =
  | "idle"
  | "user"
  | "navigation";
```

It may also retain one scheduled scroll-follow frame and the listener or observer handles required for cleanup. It does not retain selected identity, a hunk registry, counters, FileTree selection, FullFile render mode, or line-pin state.

Line pins are a separate system with a separate design and lifecycle.

On cleanup, Navigation removes every listener, observer, and scheduled frame it owns and permits no later DOM write or scroll.

```ts
const NavigationContext =
  createContext<Navigation>();
```

`useNavigation()` returns the nearest ChangeSet instance and throws when used outside `NavigationProvider`. It never constructs a controller or subscribes to navigation state.

## Initial selection and the selection operation

Once a non-empty ChangeSet has mounted its manifest FileCards and participating targets, it selects the first participating target exactly once.

The same rule applies after F5, a repository/reset boundary, different DiffParams, or recreation of disposed ChangeSet content.

An empty manifest is the only ordinary state with no selected hunk. Next and Previous therefore require an existing selected identity whenever the manifest is non-empty.

Initial selection is an explicit ChangeSet-initialization action. File loading and later DOM replacement never repeat it.

The private selection operation receives the concrete participating target:

```ts
function selectHunk(
  root: HTMLElement,
  target: HTMLElement,
): void;
```

It:

1. asserts that `target` matches `[data-hunk-target]:not(.skip)` inside `root`;
2. removes selected identity from the previous FileCard;
3. removes previous visible selected decoration;
4. copies the target's concrete `data-hunk-kind` and `data-hunk-index`, where present, directly onto the owning FileCard's selected attributes;
5. adds `data-selected` and `aria-current="true"` to `target`.

It does not call an identity reader, writer, serializer, conversion helper, or registry.

It also does not:

- calculate counters;
- update FileTree highlighting;
- scroll;
- enrich;
- expand;
- fetch;
- change participation.

After initialization, rendering, file loading, file collapse, file expansion, virtualization, inline/split replacement, counters, Debug, and line pins never select or clear a hunk.

## Next and Previous

Next and Previous first locate the FileCard carrying selected identity.

They resolve the current scroll-back element in this order:

1. the matching participating target;
2. the matching skipped target: the coordinate-matching `skip` pseudo for a selected real hunk, or the same Husk, Lazy, or zero pseudo carrying `.skip`;
3. the stable owning FileCard header when a selected Husk or Lazy pseudo has already been replaced.

If that element is outside the main viewport, the operation scrolls to it and stops. Selection does not change.

Only when the selected location is already on screen does the operation choose a destination.

It then:

1. collects current participating targets in DOM order;
2. resolves the next or previous destination with wrapping;
3. calls `waitToEnrich()` when the destination belongs to a virtual FullFile;
4. resolves the rich destination again by the primitive identity attributes read from the original target for this operation;
5. selects the final target;
6. scrolls to it.

Next and Previous do not calculate counters or update FileTree directly.

### Existing participating target

Next chooses the following participating target. Previous chooses the preceding participating target. Traversal wraps at both ends.

### Selected collapsed real hunk

The selected FileCard retains the real `fileIndex` and `hunkIndex`. Its matching `skip` pseudo is used only for scroll-back and ordering; it is never itself selected as a destination.

After the scroll-back rule is satisfied, Next chooses the first participating target after that real identity and Previous chooses the last participating target before it.

### Selected collapsed pseudo-target

A collapsed selected Husk, Lazy, or zero pseudo remains in DOM with its original identity attributes and `.skip`.

That skipped pseudo is used only for scroll-back and ordering; it is never selected as a destination. After the scroll-back rule is satisfied, Next chooses the first participating target after it and Previous chooses the last participating target before it.

### Selected Husk or Lazy pseudo after FullFile appears

The stable FileCard header is the scroll-back element.

After the scroll-back rule is satisfied:

- Next enters that file at its first real target;
- Previous enters that file at its last real target;
- a zero-hunk result selects its zero target.

This behavior belongs only to the active user navigation operation. File-state replacement performs no mapping.

## Direct hunk navigation and FileTree

Direct navigation resolves one concrete participating hunk token and uses the same enrichment, selection, and scrolling path as Next and Previous.

FileTree does not navigate to files or directories.

Clicking a FileTree file row:

1. expands the relevant directory and file when required;
2. resolves that file’s first participating token;
3. waits for enrichment if required;
4. selects the resolved token;
5. scrolls to it.

For HuskFile, expanded LazyFile, or a zero-hunk FullFile, the destination is its pseudo-target.

For a FullFile with real hunks, the destination is its first real target.

FileTree navigation may expand a LazyFile and select its pseudo-token, but it never submits that file to the fetch lane. Only direct activation of the LazyFile explicit-fetch plank may do so.

A FullFile with zero hunks uses its zero pseudo-target.

Directory rows only change directory expansion. They do not move the page.

LazyFile plank activation retains its explicit-load behavior. It does not implicitly select a hunk.

## Rich materialization

Exact navigation to a virtual FullFile uses:

```ts
async function waitToEnrich(
  fileCard: HTMLElement,
): Promise<void>;
```

Navigation calls it directly:

```ts
await waitToEnrich(fileCard);
```

`waitToEnrich` belongs to FileCard.

It:

- changes only that FullFile’s render mode;
- resolves after the rich FileBody has mounted;
- does not select;
- does not calculate counters;
- does not update FileTree;
- does not scroll;
- does not fetch.

After it resolves, navigation finds the destination token again because enrichment replaced the previous DOM.

The correct navigation order is:

```text
resolve identity
    ↓
waitToEnrich
    ↓
resolve rich token
    ↓
selectHunk
    ↓
scroll
```

Navigation does not need access to FileCard’s render-mode signal.

## User-scroll following

User-scroll following retains the stable implementation’s selection heuristic.

Only recognized user input enables it:

- vertical wheel movement;
- touch movement;
- native page-scrolling keys.

Input at the corresponding document boundary does not enable following.

Programmatic hunk navigation disables user-scroll following before moving the page.

The reading line is:

```ts
window.innerHeight * 0.5
```

For each allowed sample:

1. find the FileCard intersecting the reading line;
2. find visible, participating real hunk tokens in that FileCard;
3. consider the tokens at or above the reading line;
4. if any exist, select the last such token;
5. otherwise select the first visible token below the reading line;
6. if that FileCard has no visible participating real token, preserve current selection.

User-scroll following:

- changes selection only;
- never scrolls;
- never enriches;
- never expands;
- never fetches;
- never selects pseudo-targets;
- never calculates counters directly;
- never updates FileTree directly.

The scroll listener schedules at most one DOM calculation per animation frame.

On `scrollend`:

1. perform one final allowed selection sample;
2. finish that sample;
3. return the scroll source to idle.

Layout changes while user-scroll following is idle never change selection.

## Hunk counter calculation

Hunk counters are a separate read-only calculation over the current DOM.

Navigation operations do not calculate counters.

The calculation reads:

- participating hunk tokens;
- selected identity attributes;
- backend `hunk_count` rendered on FullFile DOM;
- currently mounted Husk, Lazy, and zero pseudo-targets.

It calculates:

- exact local position for a selected real hunk;
- current global position when the selected identity has a participating token;
- current participating total;
- whether the total remains provisional.

The global total is:

```ts
root.querySelectorAll(
  "[data-hunk-target]:not(.skip)",
).length;
```

The total includes:

- participating real tokens;
- participating Husk pseudo-tokens;
- participating Lazy pseudo-targets;
- participating zero pseudo-targets.

The total excludes:

- skipped tokens;
- collapsed-file tokens;
- collapsed LazyFile pseudo-tokens.

The `+` suffix is present whenever at least one participating Husk or Lazy pseudo-target remains. A zero target is exact and does not add `+`.

Global positions and totals are display-only. Navigation never reads counter text or calculated counter values.

If selected identity has no matching participating token, the global numerator is unavailable:

```text
Global —/42+
```

For a selected real identity whose FullFile data remains known, the local counter may remain exact even when its token is collapsed:

```text
Local 2/5
```

For a selected stale pseudo-identity after FullFile appears, no real local hunk has been selected:

```text
Local —/5
```

## Automatic counter updates

One private, ChangeSet-scoped `MutationObserver` runs hunk counter calculation when relevant DOM truth changes.

It observes only changes that can alter calculated values:

- hunk tokens added or removed;
- `.skip` added or removed from a hunk token;
- selected-hunk identity attributes added, changed, or removed;
- FileHeader and counter elements mounted or replaced.

The observer does not treat every DOM mutation as relevant. Syntax highlighting, ordinary row text, line selection, Toasts, and unrelated class changes do not run hunk counter calculation.

Relevant synchronous mutations are received as one observer batch. The calculation runs once for that batch.

Conceptually:

```ts
const hunkDisplayObserver =
  new MutationObserver((records) => {
    if (!records.some(hunkCalculationChanged)) {
      return;
    }

    const counters =
      calculateHunkCounters(root);

    renderHunkCounters(root, counters);
    updateFileTreeHighlight(root);
  });
```

The implementation must ignore mutations produced by writing counter text or FileTree highlighting, so its own output cannot schedule itself indefinitely.

On mount:

1. attach the observer to the active ChangeSet root;
2. run the initial counter calculation;
3. update the initial FileTree highlight.

On cleanup:

1. disconnect the observer;
2. allow no later counter or FileTree DOM writes.

The observer:

- never selects or clears a hunk;
- never scrolls;
- never calls Navigation;
- never expands a file;
- never changes `.skip`;
- never changes hunk identity;
- never owns a Solid selection signal;
- never exposes calculated counters as navigation state.

## FileTree highlighting calculation

FileTree highlighting is calculated independently from selected identity stored on FileCard.

It does not require selected-file state.

When selected identity attributes change, or when FileTree DOM is mounted:

1. find the unique FileCard containing selected identity;
2. read its `data-file-index`;
3. find the corresponding FileTree row;
4. apply `aria-current` to that row;
5. remove stale `aria-current` from the previous row.

If no FileCard contains selected identity, no FileTree row is highlighted.

If the selected token is collapsed, skipped, absent, or being replaced, the FileTree may remain highlighted because the stable selected identity remains on its FileCard.

Opening FileTree additionally reveals the highlighted row inside the FileTree’s own scroll container. That sidebar movement does not move the main page.

FileTree highlighting never changes selection.

## HintHud and DebugHud

HintHud reads the counter text produced by hunk counter calculation.

Its Next and Previous buttons invoke ordinary Navigation operations.

HintHud does not calculate destinations or maintain selected state.

DebugHud retains its existing visible contents:

- FPS;
- Nodes;
- Spans;
- Hunks.

While DebugHud is open, its Hunk value reads current participating hunk-token count from DOM.

While DebugHud is closed, it performs no sampling.

Neither HUD may select, scroll, enrich, expand, or fetch.

## Notebook behavior and future regions

Current notebook behavior uses one outer FileCard and one file-global backend source-hunk sequence across its cell DiffGrids.

Today:

- every changed cell source uses the same outer `fileIndex`;
- the backend offsets cell-local source indices into one file-wide `hunkIndex`;
- notebook metadata, cell metadata, and outputs are summary-only;
- metadata and outputs contribute no hunk targets;
- notebooks remain rich and are not virtualized.

The design must not assume forever that one file body is one grid.

A future notebook may add raw and rich metadata or output regions. Their identity may extend to stable `regionKey` and `itemKey` fields. Global positions remain calculations from current participating DOM order and never become identity.

Raw/rich output replacement may change N targets into M targets. Its owner places the new targets but does not automatically select, map, or scroll.

Notebook regions remain a post-rewrite TODO.

## Ownership

| Concern | Owner |
|---|---|
| Real hunk identity | Backend `hunk_index` |
| Real token placement | DiffGrid and VirtualFile rendering |
| Husk pseudo-target | HuskFile |
| Lazy pseudo-target | Expanded LazyFile |
| Zero pseudo-target | Zero-hunk FullFile |
| Skip pseudo-targets | Collapsed FullFile |
| Token participation | `.skip` and current DOM presence |
| File order | Manifest-stable `fileIndex` |
| Hunk order within a file | Backend-stable `hunkIndex` |
| Selected identity | Stable FileCard DOM attributes |
| Visible selected decoration | Selected current token |
| Next/Previous destination | Navigation operation |
| Direct hunk destination | Navigation operation |
| User-scroll selection | User-scroll following |
| FileTree destination | FileTree activation followed by Navigation |
| Local/global counter values | DOM counter calculation |
| Counter text | Counter renderer |
| FileTree highlight | DOM-based FileTree highlighting calculation |
| Rich/virtual mode | FullFile-local Solid state |
| Enrichment | FileCard `waitToEnrich` |
| Hunk scrolling | Navigation |
| Line pins | Separate line-pin system |

## Required invariants

1. Every real hunk boundary comes from backend `hunk_index`.
2. The frontend never infers hunk boundaries from changed rows.
3. Every participating real identity has exactly one participating target.
4. Every expanded HuskFile has exactly one Husk pseudo-target.
5. Every expanded LazyFile has exactly one Lazy pseudo-target.
6. Every zero-hunk FullFile has exactly one zero pseudo-target.
7. Every collapsed real target has exactly one coordinate-preserving `skip` pseudo.
8. `.skip` alone controls participation.
9. Skipped targets are excluded from traversal, counters, FileTree destinations, and scroll-follow.
10. Folded-line ranges contain no hunk boundary.
11. Invalid folded-line ranges throw instead of producing hidden folded-line targets.
12. Every non-empty ready ChangeSet selects its first participating target exactly once.
13. An empty manifest is the only ordinary no-selection state.
14. File and directory collapse never select, clear, map, or scroll.
15. File-state replacement never selects, clears, maps, or scrolls.
16. Rich/virtual and inline/split replacement never changes selected identity.
17. Next and Previous scroll back to an off-screen selected location before changing selection.
18. Navigation wraps through current participating DOM order.
19. Navigation never reads counter text or counter calculations.
20. Counter calculation never changes selection or scrolling.
21. FileTree highlighting never changes selection.
22. User-scroll following changes selection only.
23. Selecting or traversing a LazyFile never loads it.
24. Only direct activation of the LazyFile plank starts its explicit fetch.
25. `waitToEnrich` remains FileCard-owned.
26. Navigation resolves its final target again after enrichment.
27. Virtualization decisions never depend on hunk selection.
28. Hunk navigation never changes strict file-fetch order.
29. NavigationProvider owns no line-pin state.
30. Line pins remain an entirely separate system.
