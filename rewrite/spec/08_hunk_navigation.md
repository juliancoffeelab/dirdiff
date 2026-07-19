# Hunk navigation

## Scope and separation

Hunk navigation and line pins are separate systems.

Hunk navigation covers:

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

Line-pin behavior is outside this specification. `NavigationProvider` stores no line-pin state, listeners, or timers and performs no line-pin parsing or restoration. Line pins receive their own separate design and implementation stage.

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

Expansion replaces a coordinate-preserving `skip` pseudo with the real target carrying the same `fileIndex` and `hunkIndex`. Navigation matches those primitive coordinates directly. The selected FileCard identity is not rewritten, transferred, or mapped during that replacement.

A skipped target:

- remains DOM;
- is excluded from Next and Previous destinations;
- is excluded from scroll-follow;
- retains its position in `HunkDisplay`;
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

Selected identity may temporarily lack a participating matching target while representation DOM is being replaced. A selected real hunk in a collapsed file still has its coordinate-matching `skip` pseudo available as a scroll-back anchor.

Rich/virtual and inline/split replacement preserve real `fileIndex` and `hunkIndex`. Rendering does not recreate selected decoration automatically.

## File-state replacement

HuskFile contributes exactly one Husk pseudo-target. It participates unless the
file is explicitly collapsed, in which case the same target carries `.skip`.

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

In particular, an initially selected Husk identity remains `kind="husk"` on the
stable FileCard after FullFile replaces the Husk target. It does not implicitly
become real hunk zero merely because the resulting real sequence starts at zero.
The implementation must document this directly at the replacement or
selected-location boundary with a TODO to reconsider the policy; until that TODO
is explicitly redesigned, replacement performs no selection mapping.

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

Every ordinarily rendered FileCard exposes at least one hunk target. The initial presentation is
normally a Husk pseudo-target; Lazy, zero, real, and skipped targets obey the
same DOM contract. A zero-hunk FullFile satisfies this invariant with its zero
pseudo-target. Only an empty manifest has no FileCard and therefore no target.
An unexpected FullFile renderer exception is outside this ordinary navigation
contract: its critical unrecoverable strip has no hunk target, and the renderer
boundary marks its terminal DOM with `data-file-render-error`. Navigation
initialization stops when that marker exists. It does not assert a missing
target, select another file, repair selection, or promote the localized failure.

Once those initial FileCards have rendered, the ChangeSet selects the first
FileCard's first `[data-hunk-target]` exactly once. Initial selection may select a
target carrying `.skip`; selecting it does not make it participate.

The same rule applies after F5, a repository/reset boundary, different DiffParams, or recreation of disposed ChangeSet content.

A manual ChangeSet reload and repository cache-expiration replacement are destructive boundaries. They dispose the previous rendered snapshot and its selected DOM before the replacement ChangeSet selects its own first target through the same initialization rule.

> **TODO:** Investigate how destructive cache-expiration and reload should be. Until then, complete destructive replacement is permitted when required for correctness.

An empty manifest is the only ordinary state with no selected hunk. A non-empty ChangeSet containing `data-file-render-error` is the explicit unrecoverable exception: initialization may stop without a selected identity. Next and Previous require an existing selected identity whenever the manifest is non-empty and no terminal renderer marker exists.

Initial selection is an explicit ChangeSet-initialization action. File loading and later DOM replacement never repeat it.

The private selection operation receives the concrete hunk target:

```ts
function selectHunk(
  root: HTMLElement,
  target: HTMLElement,
): void;
```

It:

1. asserts that `target` matches `[data-hunk-target]` inside `root`;
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

Ordinary navigation callers still resolve only participating destinations.
Initial selection is the sole operation that may deliberately pass a skipped
target when the first FileCard is collapsed.

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

### Selected real hunk in a collapsed file

The selected FileCard retains the real `fileIndex` and `hunkIndex`. Its matching `skip` pseudo is used only for scroll-back and ordering; it is never itself selected as a destination.

After the scroll-back rule is satisfied, Next chooses the first participating target after that real identity and Previous chooses the last participating target before it.

### Selected pseudo-target in a collapsed file

A selected Husk, Lazy, or zero pseudo in a collapsed file remains in DOM with its original identity attributes and `.skip`.

That skipped pseudo is used only for scroll-back and ordering; it is never selected as a destination. After the scroll-back rule is satisfied, Next chooses the first participating target after it and Previous chooses the last participating target before it.

### Selected Husk or Lazy pseudo after FullFile appears

The stable FileCard header is the scroll-back element.

After the scroll-back rule is satisfied:

- Next enters that file at its first real target;
- Previous also enters that file at its first real target;
- a zero-hunk result selects its zero target.

This behavior belongs only to the active user navigation operation. File-state replacement performs no mapping.

## Direct hunk navigation and FileTree

> **TODO design gate — do not implement FileTree file-row navigation from this section yet.** The presentation, expansion, highlighting, and private-sidebar scrolling contract is approved in [03_file_presentation.md](03_file_presentation.md). Exact file-row target resolution, enrichment, layout stabilization, main-page scrolling, and selection remain gated until they receive their own complete design and explicit approval.

During the current chapter, FileTree file rows are inert. Directory rows invoke only the shared directory-expansion action. Collapsing or reopening a directory changes expansion and `.skip` participation without selecting, clearing, repairing, navigating, loading, or scrolling.

The approved later direction is:

- clicking a file row navigates to that file;
- an expanded file is never collapsed by navigation;
- a collapsed non-Lazy FullFile may be expanded before navigation;
- navigating to a LazyFile neither expands nor fetches it;
- only direct activation of the LazyFile plank may submit its explicit fetch.

These points do not authorize implementation of the gated interaction.

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

> **TODO design gate — do not implement scroll-follow from this section yet.** The behavior below records the current direction, but it is unreliable until the stable implementation, browser input classification, throttling, `scrollend`, layout changes, selection timing, and interaction with explicit navigation have been re-investigated together. Present a corrected complete design and obtain explicit user approval before implementation.

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

## HunkDisplay

One `HunkDisplay` signal belongs to the mounted ChangeSet shell.

It contains an exact derived mirror of the hunk state currently represented by the ChangeSet DOM:

```ts
export type HunkPosition = {
  current: number | null;
  total: number;
};

/**
 * Mirrors navigation information from the DOM.
 *
 * Must be exact, but must not be used by navigation or selection logic; those
 * continue using the DOM.
 */
type HunkDisplay = {
  /**
   * File index into the manifest, used for FileTree highlighting.
   *
   * Can be `null` only when the ChangeSet has no files.
   */
  selectedFileIndex: number | null;

  /**
   * Global selected position.
   *
   * `hasMore` indicates that more hunks can arrive later because they are still
   * loading, some files are lazy and can be expanded later by the user, or some
   * files are collapsed by the user.
   */
  globalSelectedHunk: {
    position: HunkPosition;
    hasMore: boolean;
  };

  /**
   * Same as `globalSelectedHunk`, but per file.
   *
   * The map key is the file index into the manifest.
   */
  fileSelectedHunks: ReadonlyMap<number, HunkPosition>;
};
```

`HunkPosition` is exported by `hud/FileCard.tsx`, alongside `FileCard`, because it is the required shared file-header display contract. `HunkDisplay` remains private to `hud/ChangeSet.tsx`. No separate hunk-display module exists.

`current: null` means that the represented scope has no selected position to mirror. For the global scope, that occurs when the ChangeSet is empty and has no FileCards. A local scope may have no current position when the selected identity belongs to another file.

A selected skipped target retains its position. A selected identity whose original target has been replaced also retains the position from which Next or Previous can continue. Neither case permits `current` to become `null`.

`total` counts only currently participating, non-`.skip` targets. Stable position calculation still includes skipped identities, so a selected skipped hunk keeps its exact `current` position and `current` may be greater than `total`. `hasMore` communicates that the participating denominator is incomplete.

Consumers format these numbers for their own presentation. No formatted counter strings exist in `HunkDisplay`.

This signal does not own hunk truth. Selection, identity, ordering and participation remain in DOM, and `HunkDisplay` must mirror those facts exactly after every calculation. It is derived data, not decoration and not an independent approximation of DOM state.

Navigation never reads `HunkDisplay`.

## FileCard target-set attribute

Every stable FileCard exposes one semantic attribute describing its current target set:

```text
data-hunk-set="husk"
data-hunk-set="husk:skip"
data-hunk-set="lazy"
data-hunk-set="lazy:skip"
data-hunk-set="zero"
data-hunk-set="zero:skip"
data-hunk-set="real:35"
data-hunk-set="real:35:skip"
```

It changes only when the FileCard's target set or participation changes.

It does not change for:

- syntax highlighting;
- diff-row rendering;
- inline/split replacement with the same identities;
- rich/virtual replacement with the same identities;
- ordinary status text;
- selection decoration.

`data-hunk-set` is the semantic DOM change marker and `hasMore` input. Position and total calculation walks current hunk targets in DOM order. `HunkDisplay` does not audit redundant renderer counts, classes, duplicate identities, or navigation invariants. Navigation validates the concrete target involved in each action.

## HunkDisplay calculation

When relevant DOM truth changes, one calculation produces a complete replacement `HunkDisplay` value:

1. Read the FileCards and their current hunk targets in DOM order.
2. Count non-skipped targets for displayed totals while retaining skipped targets in stable position offsets.
3. Assert only the attributes required to perform the calculation.
4. Find the unique FileCard carrying selected identity.
5. Calculate the selected identity's position from the same DOM identity and ordering facts that allow Next or Previous to continue from it.
6. Calculate `globalSelectedHunk`, including whether more hunks can arrive later.
7. Calculate `fileSelectedHunks` for every FileCard.
8. Calculate `selectedFileIndex`.
9. Replace the complete `HunkDisplay` signal once.

`.skip` excludes a target from traversal but does not erase its position or the selected identity attached to its FileCard. Collapsed files make `globalSelectedHunk.hasMore` true because their hunks can become participating targets again when the user expands them.

When a selected Husk or Lazy target is replaced by FullFile targets, the selected identity remains unchanged as specified by the hunk contract. Its calculated position is the position of the file's first resulting target, which is where both Next and Previous continue. This calculates the existing identity's position; it does not map or replace that identity with a real hunk.

`globalSelectedHunk.hasMore` is also true while Husk targets remain or while Lazy targets can still be loaded explicitly. A zero target is exact and does not independently make `hasMore` true.

## Automatic HunkDisplay updates

One private, ChangeSet-scoped `MutationObserver` triggers `HunkDisplay` calculation when relevant DOM truth changes:

```ts
observer.observe(root, {
  subtree: true,
  attributes: true,
  attributeFilter: [
    "data-hunk-set",
    "data-selected-hunk-kind",
    "data-selected-hunk-index",
    "data-file-render-error",
  ],
});
```

The observer does not observe:

- `childList`;
- `class`;
- `data-selected`;
- hunk-target insertion;
- syntax spans;
- text;
- FileTree rows;
- counter elements.

Every renderer or explicit action that changes target identity or participation updates `data-hunk-set` in the same render. The browser filters attribute names before delivering records, and relevant synchronous attribute mutations arrive as one observer batch. The calculation runs once for that batch.

If the calculation cannot parse the required semantic DOM, the observer reports one persistent “Could not calculate hunk display” Toast directly and disconnects. It does not transfer the failure through another signal, throw from a reactive rendering branch, repair DOM, or continue producing repeated Toasts.

`data-file-render-error` is different from malformed hunk DOM: the renderer boundary already presented the critical failure and its one Toast. When that marker appears, the observer disconnects without another calculation, signal write, or Toast. The last successfully calculated display may remain visible as part of the preserved surrounding UI, but the unrecoverable renderer failure ends the ordinary exact-mirror contract for that damaged ChangeSet lifetime.

After the explicit initial-hunk selection has run:

1. attach the observer;
2. calculate the current DOM once;
3. set the initial signal.

The initial-hunk selection itself uses no `MutationObserver`. Running it before the initial calculation ensures that a non-empty ChangeSet's first `HunkDisplay` already contains its required selected position and `selectedFileIndex`.

On cleanup:

1. disconnect the observer;
2. dispose the signal with its Solid owner;
3. permit no later calculation or DOM write.

The observer and `HunkDisplay` never:

- select or clear a hunk;
- scroll;
- choose a navigation destination;
- call Navigation;
- expand or load a file;
- change `.skip`;
- change hunk identity;
- decide rich/virtual rendering;
- become an input to Navigation.

## Rendering HunkDisplay

Solid renders every consumer declaratively from the signal.

FileCard receives required accessors for its global and per-file position data. Consumers format the two numbers themselves.

FileTree compares each row's manifest index with:

```ts
hunkDisplay().selectedFileIndex
```

FileTree applies its highlight class and `aria-current` declaratively. Newly mounted FileTree rows immediately render from the existing signal and do not require another calculation.

If the selected target's file is collapsed, or the target is skipped, absent, or being replaced, the FileTree remains highlighted because selected identity remains on its stable FileCard and `selectedFileIndex` continues to mirror that FileCard.

Opening FileTree additionally reveals the highlighted row inside `.file-tree-groups` when all of its directory ancestors are expanded. A row beneath a collapsed directory is legitimately absent and is not revealed by changing expansion. The private sidebar movement changes only the container's `scrollTop` and never moves the main page.

FileTree highlighting never changes selection.

There are no imperative writes to counter `textContent`, counter `hidden`, FileTree highlight classes, or FileTree `aria-current`.

## HintHud and DebugHud

HintHud remains the existing three-button visual component. Its Next and Previous buttons invoke ordinary Navigation operations. It does not read `HunkDisplay`, calculate destinations, or maintain selected state.

DebugHud retains its existing visible contents:

- FPS;
- Nodes;
- Spans;
- Hunks.

Its Hunk value reads `globalSelectedHunk`. It does not perform a separate hunk DOM count. DebugHud's existing FPS, node, and span sampling remains active only while DebugHud is open.

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

A future notebook may add raw and rich metadata or output regions. Their identity may extend to stable `regionKey` and `itemKey` fields. Global positions remain calculations from current DOM identity and ordering facts and never become identity.

Raw/rich output replacement may change N targets into M targets. The renderer places the new targets but does not automatically select, map, or scroll.

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
| HunkDisplay calculation trigger | Filtered ChangeSet `MutationObserver` |
| Exact calculated hunk snapshot | `HunkDisplay` signal stored by the mounted ChangeSet shell |
| Counter text | Solid rendering from `HunkDisplay` numbers |
| FileTree highlight | Solid rendering from `HunkDisplay.selectedFileIndex` |
| Rich/virtual mode | FullFile-local Solid state |
| Enrichment | FileCard `waitToEnrich` |
| Hunk scrolling | Navigation |
| Line pins | Separate line-pin system |

## Required invariants

1. Every real hunk boundary comes from backend `hunk_index`.
2. The frontend never infers hunk boundaries from changed rows.
3. Every ordinarily rendered FileCard exposes at least one hunk target; an unexpected renderer's critical unrecoverable strip is the explicit failure exception.
4. Every participating real identity has exactly one participating target.
5. Every expanded HuskFile has exactly one Husk pseudo-target.
6. Every expanded LazyFile has exactly one Lazy pseudo-target.
7. Every zero-hunk FullFile has exactly one zero pseudo-target.
8. Every real target from a collapsed file has exactly one coordinate-preserving `skip` pseudo.
9. `.skip` alone controls participation.
10. Skipped targets may retain or receive selection and retain their `HunkDisplay` position, but are excluded from traversal, FileTree destinations, and scroll-follow.
11. Folded-line ranges contain no hunk boundary.
12. Invalid folded-line ranges throw instead of producing hidden folded-line targets.
13. Every non-empty ready ChangeSet without `data-file-render-error` selects its first FileCard's first hunk target exactly once, even when that target carries `.skip`; a terminal renderer marker stops initialization without selection or repair.
14. An empty manifest is the only ordinary no-selection state.
15. File and directory collapse never select, clear, map, or scroll.
16. File-state replacement never selects, clears, maps, or scrolls.
17. Rich/virtual and inline/split replacement never changes selected identity.
18. Next and Previous scroll back to an off-screen selected location before changing selection.
19. Navigation wraps through current participating DOM order.
20. Navigation never reads counter text or `HunkDisplay`.
21. `HunkDisplay` calculation never changes selection or scrolling.
22. FileTree highlighting never changes selection.
23. User-scroll following changes selection only.
24. Selecting or traversing a LazyFile never loads it.
25. Only direct activation of the LazyFile plank starts its explicit fetch.
26. FileCard continues to implement `waitToEnrich`.
27. Navigation resolves its final target again after enrichment.
28. Virtualization decisions never depend on hunk selection.
29. Hunk navigation never changes strict file-fetch order.
30. NavigationProvider owns no line-pin state.
31. Line pins remain an entirely separate system.
