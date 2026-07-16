## 25. File presentation and AppHeader integration

### 25.1 Scope

This section specifies file presentation, statistics ownership, loading messages and their placement in the sticky AppHeader.

It deliberately does not specify virtualization or navigation. Those concerns are deferred to their dedicated section.

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

`FileCard` is the stable manifest-position wrapper. It receives its reactive file state and explicit-load callback from ChangeSet; it does not observe a query. The three file states own different presentations and different headers. The LazyFile plank invokes the supplied callback so ChangeSet can submit that file to its single request lane.

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
  localHunkPosition={props.localHunkPosition}
  globalHunkPosition={props.globalHunkPosition}
/>
```

The exact counter authority and navigation behavior remain deferred.

### 25.5 FileTree

FileTree receives its tree projection from ChangeSet. It owns no backend data and starts no queries.

It may derive progressively available row or directory statistics from file states supplied by ChangeSet, but those values are presentation only and never become another aggregate authority.

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

`Portal` is a Solid feature imported from `solid-js/web`; it is not a native HTML element. It renders ordinary DOM into another mount node while preserving the component and reactive ownership hierarchy.

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

Portal ownership is logical, but CSS layout and inheritance follow the physical DOM. Portalled contributions are ordinary AppHeader children and use AppHeader styles and variables. CSS scoped only below `.change-set` does not reach them.

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
- the clock appears only after the active request crosses the slow threshold;
- long paths appear only in hover/focus tooltips.

The slow-file indicator does not show a live seconds counter. The request lane uses one timeout to set `slow: true`, then clears it when the request settles.

```tsx
<Show when={slowRequest()}>
  {(request) => (
    <button
      type="button"
      class="app-header-slow-file"
      aria-label={`${request().path} is taking longer than expected`}
    >
      <ClockIcon />
      <Tooltip>
        {request().path} is taking longer than expected
      </Tooltip>
    </button>
  )}
</Show>
```

The tooltip must appear on both hover and keyboard focus. It is positioned outside normal layout and cannot resize AppHeader.

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
- Portals render ChangeSet-owned presentation directly into AppHeader.
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
- navigation to folded or unloaded hunks.

The only retained hunk presentation requirement is that FullFileHeader visibly contains both local and global counters.
