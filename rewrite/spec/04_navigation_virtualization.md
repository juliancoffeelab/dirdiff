# Whole-file virtualization

This topic specifies whole-file virtualization only.

Hunk targets, selection, counters, explicit navigation, scroll-follow, FileTree navigation, HUD hunk behavior, and future notebook navigation regions are specified in [08_hunk_navigation.md](08_hunk_navigation.md).

Line pins require their own later specification and are not part of virtualization.

## 26. Scope

This section specifies:

- whole-file virtualization;
- rich/virtual FileBody replacement;
- browser text-side selection;
- preservation of complete split-side text for native browser search;
- virtualization cost, entry and exit heuristics;
- height reservation and overflow containment;
- rich materialization through FileCard's `waitToEnrich()` implementation;
- notebook FullFiles remaining rich for now.

It does not revisit:

- manifest and file request definitions;
- strict sequential file fetching;
- AppHeader loading messages;
- the semantic `HuskFile`, `FullFile`, and `LazyFile` boundaries;
- notebook backend response design;
- row virtualization.

TODOs about virtualization heuristics or representation policy are explicit post-rewrite follow-ups. FileTree Navigation and scroll-follow are approved separately in their own specifications and practical chapters. The line-pin design gate remains required rewrite work rather than a virtualization TODO or optional follow-up.

## 27. Essential complexity

The system genuinely must handle:

1. A ChangeSet may contain enough rendered DOM to hurt memory, layout, and paint performance.
2. Files load over time, so a provisional global hunk sequence can change.
3. HuskFiles and LazyFiles do not yet know their real hunk structure.
4. Rich FileBody DOM may be removed and recreated.
5. Inline/split changes may replace rich row DOM.
6. Code folds replace unchanged row DOM without changing the hunk-target set.
7. A file collapsed directly or through its directory leaves traversal while retaining coordinate-preserving skipped DOM anchors.
8. Rendered hunk targets may be replaced while the selected `fileIndex` and `hunkIndex` remain on the stable FileCard DOM; every replacement target carries both coordinates.
9. User scrolling, programmatic scrolling, browser anchoring, and layout movement all produce browser scroll events.
10. Sticky AppHeader and FileHeader elements affect visible geometry.
11. FileTree, FileHeader, HUD, and rendered targets need selected-hunk calculations or decoration from the same DOM truth.
12. Notebook files contain several file-like regions inside one outer file.
13. Future raw/rich notebook modes may expose different numbers of hunks.
14. URL line pins may refer to content that has not rendered yet.
15. Browser native search must see both complete file sides while a file is virtual.

The accidental complexity currently includes:

- selected hunk identity stored in both DOM and a Solid signal;
- separately maintained `HunkPosition` state in Navigation that can diverge from DOM;
- `activeHunkFileId`;
- global forced-rich file maps;
- global virtualized-file maps;
- layout, loading, and virtualization revision signals;
- a rich preload radius;
- delayed background selection timers;
- repeated animation-frame retries during FileTree navigation;
- page-scrolling functions outside explicit navigation operations;
- Debug sampling while Debug is closed.

The rewrite preserves the essential complexity while deleting those coordination mechanisms.

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

After hunk navigation exists, programmatic hunk navigation and FileTree navigation use:

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

After Chapter 7 adds hunk targets, the virtual rendering also contains every participating real-hunk identity in the same order as RichFileBody. This is a rendering invariant only: virtualization never reads those identities or selected-hunk state when choosing eligibility, mode, observer margins, cost, or geometry.

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
- After Chapter 7, does not change the set or order of participating hunk identities.
- Does not recreate selected decoration automatically; selected identity remains on the stable FileCard DOM.
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

The virtualization, FileTree-navigation, and approved scroll-follow behavior must be exercised together in this scenario.

The virtualization and scroll-anchoring design must be tested from the least convenient starting condition:

1. Load only the manifest and begin strict sequential file fetching from the start.
2. While later files are still HuskFiles, select the first available hunk near the beginning.
3. Invoke Previous once and wrap to the final manifest target.
4. If the final target is a HuskFile, FileTree navigation remains disabled and performs no scroll because the Husk's geometry is unstable while sequential loading continues.
5. Continue sequentially loading and enriching earlier FileCards above the viewport.
6. Walk backward from the end toward the start through a changing mixture of HuskFile, LazyFile, VirtualFile and rich FullFile targets.
7. Allow selected HuskFiles to be replaced by their real hunk sets while preserving the selected `fileIndex` and `hunkIndex === 0`; every representation of that hunk carries both coordinates.
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
- correct explicit Previous behavior after a selected Husk pseudo-hunk becomes a multi-hunk FullFile: resolve the resulting hunk zero by the same `fileIndex` and `hunkIndex`, without substituting the FileCard header or another target;
- global and local counter changes as pseudo-hunks become real hunks;
- visible layout jumps during first-time virtual → rich transitions above the viewport;
- rich → virtual height preservation for previously measured files;
- native scroll anchoring behavior at and near the document end;
- scroll-follow preserving selection through VirtualFiles and selecting only visible rich participating real targets;
- browser native search inside fixed-height VirtualFiles;
- DOM node and span counts throughout the traversal;
- long tasks, dropped frames and enrichment duration for each row-count band.

The scenario should run with the intrinsic-size optimization enabled and disabled. That comparison determines whether the existing optimization helps the new architecture or merely adds a second source of provisional geometry.

So the short version is:

> Every distant hydrated text file is virtual. Row count determines how far ahead it becomes completely rich and how far away it must travel before becoming virtual again. Rich → virtual preserves height through a fixed, internally scrollable VirtualFile; RichFileBody always remains natural document content.
