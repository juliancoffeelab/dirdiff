## 25. File presentation and AppHeader integration

### 25.1 Scope

This section specifies file presentation, statistics authority, loading messages and their placement in the sticky AppHeader.

It does not specify virtualization policy or hunk-navigation mechanics. Those concerns remain in their dedicated sections. FileTree's presentation-only virtual-mode bridge and its boundary with already-approved explicit navigation are specified here because they determine FileTree's visible contract without transferring state or behavior between the two subsystems.

### 25.2 Presentation hierarchy

```text
ChangeSet
├── ChangeSetTitle
├── AppHeader contributions
│   ├── manifest statistics portal
│   └── loading/status portal
├── FileTree
└── FileCards
    └── FileCard
        └── state switch
            ├── HuskFile
            │   └── HuskFileHeader
            ├── FullFile
            │   ├── FullFileHeader
            │   └── FileBody
            └── LazyFile
                ├── LazyFileHeader
                └── explicit-fetch plank
```

`FileCard` is the stable manifest-position wrapper. It receives its reactive file state and explicit-load callback from ChangeSet; it does not observe a query. The three file states render different presentations and different headers. The LazyFile plank invokes the supplied callback so ChangeSet can submit that file to its single file-fetch lane.

Ordinary file errors produce the existing error-flavoured `LazyFile`. Repository cache expiration does not produce one file error plank; it disposes the complete expired `ChangeSetSnapshot` and restarts the ChangeSet as specified in `01_tanstack_query.md`. No other backend file-failure presentation changes.

An unexpected FullFile renderer exception is not a backend file error and is never presented as a LazyFile. The stable FileCard article remains mounted where possible, while the failed renderer subtree becomes a critical unrecoverable error strip with the complete error and one persistent Toast. It contains no RetryButton or hunk target and marks its terminal DOM with `data-file-render-error`. The renderer boundary does not preserve failed DOM, synthesize replacement hunks, remap selection, select another hunk, or attempt automatic recovery.

The ChangeSet title remains with the ChangeSet. It is not placed in AppHeader because AppHeader space is limited.

### 25.3 Manifest summary authority

The manifest remains thin even though it carries compact aggregate statistics. The backend already obtains these values while listing changed paths; they are snapshot metadata, not rendered file content.

```ts
const ManifestSummarySchema = z.strictObject({
  changed_files: z.number().int(),
  added_files: z.number().int(),
  removed_files: z.number().int(),
  updated_files: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  skipped_files: z.number().int(),
  changed_cells: z.number().int().nullable(),
  added_cells: z.number().int().nullable(),
  removed_cells: z.number().int().nullable(),
  modified_cells: z.number().int().nullable(),
});

const TextFileSummarySchema = z.strictObject({
  changed_lines: z.number().int(),
  modified_lines: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  moved_lines: z.number().int(),
  left_exists: z.boolean(),
  right_exists: z.boolean(),
});

const NotebookFileSummarySchema = TextFileSummarySchema.extend({
  changed_cells: z.number().int(),
  added_cells: z.number().int(),
  removed_cells: z.number().int(),
  modified_cells: z.number().int(),
  notebook_metadata_changed: z.boolean(),
});

export type ManifestSummary = z.infer<typeof ManifestSummarySchema>;
export type TextFileSummary = z.infer<typeof TextFileSummarySchema>;
export type NotebookFileSummary = z.infer<typeof NotebookFileSummarySchema>;
```

These schemas preserve the stable backend response fields while giving each frontend consumer one precise type. `ManifestSummary` is immutable for one manifest/cache ID and is never recomputed or progressively mutated from loaded FullFiles. Nullable aggregate notebook fields remain whatever the manifest response supplied; complete per-file notebook statistics belong to `NotebookFileSummary`.

### 25.4 File-specific headers

The three headers intentionally have different interfaces:

| File component | Header information |
|---|---|
| `HuskFile` | manifest path and queued/fetching activity |
| `FullFile` | path, complete file statistics, local/global hunk counters, file kind or engine warning, expansion controls |
| `LazyFile` | path, available lazy metadata or error information, and explicit-fetch affordance |

The display name lives primarily in FileTree. File headers display the path.

Unknown statistics are normally omitted instead of displaying four question marks.

Per-file statistics never feed back into `ManifestSummary`.

`FullFileHeader` receives both hunk counters, but does not own or derive navigation state:

```tsx
<FullFileHeader
  path={filePath(props.state.file)}
  summary={props.state.file.summary}
  fileSelectedHunk={props.fileSelectedHunk}
  globalSelectedHunk={props.globalSelectedHunk}
/>
```

The exact counter authority is the mounted ChangeSet shell's `HunkDisplay` specified in [08_hunk_navigation.md](08_hunk_navigation.md). Navigation continues using DOM directly and never reads these values.

### 25.5 FileTree

FileTree receives its tree data from ChangeSet. It owns no backend data and starts no queries.

It may derive progressively available row or directory statistics from file states supplied by ChangeSet, but those values are presentation only and never become another aggregate authority.

#### Visible contract

The small square is the visible expansion marker. “Collapse” and “Expand” exist only in accessible labels.

| File state | Marker |
|---|---|
| Collapsed | empty square |
| Expanded rich FullFile | filled square |
| Expanded virtual FullFile | `V` |
| Husk | empty square |
| Lazy, including deferred, fetching, or error | empty square |
| Lazy, error, untracked, added, removed, or renamed | existing color and border |

| Directory state | Marker |
|---|---|
| Collapsed | empty square |
| Expanded | filled square |

The FileTree displays manifest hierarchy in strict manifest order, progressive file state and statistics, calculated directory reachability, shared file expansion, FileCard-local virtual mode, the file containing the selected hunk, and that highlighted row within its own scroll container.

Every visible directory name ends with `/`. The slash is part of the selectable directory-name button; it is not part of the manifest directory name or path identity.

It may toggle a directory or individual FullFile through the corresponding square, toggle its own open state, scroll its own overflow container to reveal an already-mounted highlighted row, and invoke the approved FileTree Navigation operation through a directory or file name. It must not store separate directory-expansion state, select hunks, load files, or expand directories or files while navigating.

#### State and interface

ChangeSet remains the file-expansion authority. Directory expansion is calculated from descendant file reachability and is not stored independently:

```ts
type ChangeSetState = {
  treeOpen: boolean;
  fileExpansion: Record<string, boolean | undefined>;
};
```

The calculation walks the manifest bottom-up:

```ts
/**
 * Calculates whether each directory has any reachable descendant file.
 *
 * Explicit file expansion wins. An unresolved Husk remains reachable so the
 * directory hierarchy does not collapse while sequential loading discovers its actual default.
 * LazyFiles remain reachable because their plank is visible.
 */
function calculateDirectoryExpansion(
  nodes: readonly ManifestNode[],
  stateForFile: (file: ManifestFile) => FileTreeState,
  fileExpansion: Readonly<Record<string, boolean | undefined>>,
): ReadonlyMap<string, boolean> {
  const result = new Map<string, boolean>();

  function visit(children: readonly ManifestNode[]): boolean {
    let hasReachableFile = false;

    for (const child of children) {
      let childIsReachable: boolean;

      if (child.type === "file") {
        const explicit = fileExpansion[manifestEntryKey(child.entry)];

        if (explicit !== undefined) {
          childIsReachable = explicit;
        } else {
          const state = stateForFile(child);
          childIsReachable =
            state.state === "husk" ||
            state.state === "lazy" ||
            state.file.default_expanded;
        }
      } else {
        childIsReachable = visit(child.entries);
        result.set(child.path, childIsReachable);
      }

      hasReachableFile = childIsReachable || hasReachableFile;
    }

    return hasReachableFile;
  }

  visit(nodes);
  return result;
}
```

The sole reactive calculation is:

```ts
const directoryExpansion = createMemo(() =>
  calculateDirectoryExpansion(
    props.manifest.tree,
    stateForFile,
    props.state.fileExpansion,
  ),
);
```

Expanding one file therefore opens only the ancestor chain made reachable by that file. Collapsing a file collapses an ancestor only when no other reachable file remains beneath it. Collapsing the final reachable file may collapse several ancestors. No action blindly writes `true` to every ancestor.

Directory-square activation remains a bulk file operation. It writes the requested value to every descendant file, after which the same calculation determines that directory and all of its ancestors:

```ts
batch(() => {
  for (const file of manifestFilesInOrder(directory.entries)) {
    props.setState(
      "fileExpansion",
      manifestEntryKey(file.entry),
      expanded,
    );
  }
});
```

FileTree receives required reactive inputs and explicit actions:

```ts
type FileTreeProps = {
  changeSetRoot: Accessor<HTMLElement>;
  tree: readonly ManifestNode[];
  states: Accessor<readonly FileTreeState[]>;
  open: boolean;
  view: DiffViewMode;
  selectedFileIndex: Accessor<number | null>;
  directoryExpansion: Accessor<ReadonlyMap<string, boolean>>;
  fileExpansion: Accessor<
    Readonly<Record<string, boolean | undefined>>
  >;
  onOpenChange: (open: boolean) => void;
  onDirectoryExpandedChange: (
    directory: ManifestDirectory,
    expanded: boolean,
  ) => void;
  onFileExpandedChange: (
    file: ManifestFile,
    expanded: boolean,
  ) => void;
};
```

There is no `onDirectoryReveal`, because sidebar scrolling never changes expansion.

#### Reactive rows and directory interaction

Directory and file rows receive accessors and call those accessors while rendering. They never retain a plain boolean or file state captured by a long-lived recursive renderer. Directory statistics similarly read current descendant file states through a memo.

The directory square is the sole directory-expansion button. It exposes the calculated state through `aria-expanded` and performs the bulk descendant-file action above. The directory name is a separate selectable button ending with `/`. Activating it finds the directory's first file in manifest order and invokes that file's Navigation operation without changing expansion.

The FullFile square is the sole individual file-expansion button in both FileTree and the main FileCard. Both squares call the same ChangeSet file-expansion action. The FileTree file name is a separate selectable button that invokes the file's Navigation operation. The remainder of the main FileHeader—including path, counters, and statistics—is inert selectable content rather than part of the expansion button.

Husk and Lazy squares remain inert and empty because they have no expandable rendered body. Their presence does not change the reachability calculation: unresolved Husks keep their directory stable during sequential loading, and a LazyFile's visible plank keeps its directory reachable unless it was explicitly collapsed. The Lazy plank remains the only individual explicit-load action. A Husk file-name button is disabled because later replacement has unstable geometry. A Lazy file-name button may scroll to its existing file-level target, but it never loads or expands the file. A directory-name button is likewise disabled while its first manifest file is a Husk.

No square activation performs selection, repair, navigation, loading, or scrolling. No name activation toggles expansion.

Collapsing a directory or file preserves the selected hunk identity and marks its hunk representation skipped according to [08_hunk_navigation.md](08_hunk_navigation.md). It never chooses another hunk. Reopening changes only expansion; any still-selected descendant row mounts highlighted again.

#### File interaction and Lazy lifecycle

For a FullFile, the square displays state and is the only expansion control. The file name invokes the FileTree Navigation operation and uses a pointer cursor. Individual FullFile expansion from either FileTree or FileHeader uses the same ChangeSet action.

LazyFile has a distinct presentation lifecycle:

1. FileTree always displays a LazyFile as collapsed with an empty square, including while its explicit fetch is running and after an ordinary localized failure.
2. The LazyFile plank remains the only explicit fetch action while its FileCard is expanded.
3. Activating the plank submits the file to the ChangeSet fetch lane without changing expansion.
4. Failure leaves an error-flavoured LazyFile and its FileTree marker collapsed.
5. Success first replaces LazyFile with FullFile and only then expands that FullFile.
6. The resulting FullFile retains the color of its Lazy reason when that reason was not an error.

FileTree therefore derives `expanded` as `false` for every Lazy state regardless of stale file-expansion data. FileCard continues to own whether the Lazy plank is physically present; an explicit file or containing-directory collapse may hide it. Automatic non-Lazy files continue using their backend/default expansion rules.

FileTree Navigation changes neither file nor directory expansion. A FullFile name scrolls to hunk zero in its current rich, virtual, or skipped representation. A LazyFile name scrolls to its visible plank or collapsed skipped target without expanding or fetching it. A Husk name is disabled, and a file command that encounters a transient Husk target is a no-op. A zero-hunk FullFile name scrolls to its zero pseudo-target. Navigation never changes selected hunk identity; future scroll-follow design may decide how ordinary viewport following reacts after the programmatic scroll completes.

#### Virtual-mode display

FullFile retains local ownership of `"rich" | "virtual"`; ChangeSet must not regain a global virtualization map. FileTree maintains only this disposable presentation calculation:

```ts
type FileTreeRenderModes = ReadonlyMap<number, "rich" | "virtual">;
```

While FileTree is open, its mounted content:

1. scans stable FileCards for `data-file-index` and `data-file-render`;
2. starts one `MutationObserver` filtered to `data-file-render`;
3. updates only the FileTree-local display map;
4. disconnects and discards that map when FileTree closes or unmounts.

FileCard DOM remains authoritative. Navigation and virtualization cannot read the map, the map cannot change render mode, and reopening FileTree reconstructs it from current DOM. `V` appears only for an expanded file whose current FileCard DOM says `data-file-render="virtual"`. A collapsed virtual file displays an empty square.

#### Highlighting and private scrolling

Highlight remains declarative from `HunkDisplay.selectedFileIndex`; FileTree never writes highlight classes or `aria-current` imperatively. If a selected file belongs to a collapsed directory, its absent row is legitimate. Highlighting performs no selection, repair, navigation, or expansion. When that directory reopens, the row mounts with the existing highlight.

The actual private scroll container is `.file-tree-groups`. A memo of `selectedFileIndex` prevents hunk changes inside one file from triggering another sidebar scroll. The scrolling effect observes whether FileTree is open, the highlighted file index, and the expansion of that file’s directory ancestors.

If an ancestor is collapsed, the effect stops because the row is legitimately absent. If every ancestor is expanded but the manifest row is absent, that is an invariant failure. Otherwise, the effect compares the row and container rectangles and changes only the container’s `scrollTop` by the minimum amount needed to reveal the row.

Already visible rows do not move. Rows above reveal their top and rows below reveal their bottom. FileTree never uses `scrollIntoView()`, expands an ancestor, queues microtasks, retries through animation frames, or moves the main page.

#### Navigation boundary

If explicit Next or Previous is invoked while the currently selected target is outside the main viewport, Navigation resolves its FileCard, calls `waitToEnrich(fileCard)`, resolves the selected target again, scrolls back to that target, and stops without advancing selection.

`waitToEnrich()` enriches an expanded virtual FullFile, is an immediate no-op for expanded rich FullFile and Husk/Lazy/zero representations, and is an immediate no-op for a collapsed file. It never expands anything. This main-page Navigation behavior is separate from FileTree’s private sidebar scrolling.

### 25.6 AppHeader responsibility

AppHeader remains the globally sticky header. It contains workspace controls and two ChangeSet contribution regions.

The active ChangeSet contributes only:

- immutable manifest statistics;
- automatic loading progress;
- failure count;
- a compact slow-file indicator.

It does not contribute the ChangeSet title.

FileHeader remains sticky below AppHeader and contains file-local information only. Global loading messages must not consume FileHeader space.

### 25.7 Solid Portal outlets

`Portal` is a Solid feature imported from `solid-js/web`; it is not a native HTML element. It renders ordinary DOM into another mount node while preserving component ancestry and Solid's reactive Owner hierarchy.

AppHeader provides two explicit physical outlets:

```tsx
<header class="app-header">
  <WorkspaceControls />

  <div class="app-header-status-outlet" />
  <div class="app-header-summary-outlet" />
</header>
```

Two outlets and two portals are preferred over distributing one portal with clever grid or `display: contents` behavior. Loading status and summary occupy different AppHeader regions and should have explicit mount points.

The active ChangeSet renders both contributions:

```tsx
<Show when={props.active}>
  <Portal mount={outlets.status()}>
    <AppHeaderFileStatus state={sequenceState()} />
  </Portal>

  <Show when={manifest.isSuccess}>
    <Portal mount={outlets.summary()}>
      <ManifestStatistics summary={manifest.data.summary} />
    </Portal>
  </Show>
</Show>
```

The `active` guard is mandatory. Tabs remain mounted, and portal DOM is physically outside the hidden Tab DOM; hiding an inactive Tab does not hide an ungated portal.

The outlet accessors may be provided through a small context. The context contains DOM outlet accessors only, not ChangeSet data or copied status:

```ts
export type AppHeaderOutlets = {
  status: () => HTMLDivElement;
  summary: () => HTMLDivElement;
};
```

Access outside the provider or before the required outlet exists is an application error and must throw.

There is no:

- `setAppHeaderStatus` callback;
- status event bus;
- App-owned copy of ChangeSet status;
- duplicate manifest observer in AppHeader;
- effect that synchronizes ChangeSet state upward.

### 25.8 Portal styling

Portal component ancestry remains logical, but CSS layout and inheritance follow the physical DOM. Portalled contributions are ordinary AppHeader children and use AppHeader styles and variables. CSS scoped only below `.change-set` does not reach them.

Solid creates a wrapper element when mounting a Portal into a normal DOM node, so outlet CSS may style that wrapper explicitly:

```css
.app-header-status-outlet > div,
.app-header-summary-outlet > div {
  display: flex;
  align-items: center;
  gap: 6px;
}
```

### 25.9 Compact status presentation

The status region is compact and fixed-height:

```text
[spinner 7/19] [! 2] [clock]
```

- the spinner and fraction show automatic progress;
- the failure icon shows the number of files needing attention;
- the clock appears only after the active file load crosses the slow threshold;
- the slow-file path is available through the clock's native `title` tooltip and matching accessible label.

`AppHeaderFileStatus` mounts its bordered status group only when at least one of those three indicators is visible. An explicit file attempt with no automatic progress, no failure count and no slow marker renders no status group; active work alone must not create an empty square.

The slow-file indicator does not show a live seconds counter. The file-fetch lane uses one timeout to set `slow: true`, then clears it when the file load settles.

```tsx
<Show when={slowFile()}>
  {(file) => (
    <button
      type="button"
      class="app-header-slow-file"
      aria-label={`${file().path} is taking longer than expected`}
      title={`${file().path} is taking longer than expected`}
    >
      <ClockIcon />
    </button>
  )}
</Show>
```

The clock uses the browser's native `title` tooltip, matching the failure indicator. There is no custom tooltip element, positioning logic or tooltip CSS, so generic AppHeader text rules cannot make its content unreadable.

Only the changing message is an `aria-live="polite"` status. Manifest statistics are not repeatedly announced on every progress update.

### 25.10 Stable sticky layout

AppHeader has a CSS-defined height contract rather than a runtime-measured height:

```css
.app-shell {
  --app-header-height: 48px;
}

.app-header {
  position: sticky;
  top: 0;
  height: var(--app-header-height);
}

.file-header {
  position: sticky;
  top: var(--app-header-height);
}
```

The exact value may differ by responsive breakpoint, but every breakpoint defines it in CSS. At a given breakpoint, AppHeader content must remain one line, truncate or hide long text, use compact icons and place explanations in overlay tooltips.

No `ResizeObserver`, header-measurement signal or special `createEffect` is required.

### 25.11 Solid state rules

- TanStack Query owns manifest, lazy-info and file results.
- `createMemo` derives FileCard and AppHeader presentation state.
- Effects do not synchronize ChangeSet status upward into App.
- Portals render ChangeSet presentation directly into AppHeader.
- Context, if used, contains only the two outlet accessors.
- Props are read reactively rather than destructured at initialization.
- Only the active Tab renders portals.
- FileBody does not subscribe to AppHeader progress, hunk counters or unrelated state.

### 25.12 Deferred concerns

This section does not design:

- virtualization;
- rich/plain transitions;
- hunk selection authority;
- next/previous navigation;
- scroll-follow;
- forced-rich files;
- line-pin scrolling;
- navigation to hunks in collapsed files or to unloaded hunk targets.

The only retained hunk presentation requirement is that FullFileHeader visibly contains both local and global counters.
