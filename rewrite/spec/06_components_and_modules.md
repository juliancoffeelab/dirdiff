## 65. Component and module architecture

I’d use a shallow component-module structure: a file represents a substantial component or subsystem, not every JSX component. Small supporting components remain private in that component's file.

### 65.1 Three viable shapes

#### A. Flat component modules — recommended

```text
frontend/src/
├── api/
│   ├── api.ts
│   └── queryClient.tsx
├── comp/
│   ├── AutocompleteInput.tsx
│   ├── Select.tsx
│   └── Toasts.tsx
├── hud/
│   ├── App.tsx
│   ├── AppHeader.tsx
│   ├── Tabs.tsx
│   ├── Profile.tsx
│   ├── ChangeSet.tsx
│   ├── FileCard.tsx
│   ├── linePins.ts
│   ├── navigation.tsx
│   ├── DiffGrid.tsx
│   ├── NotebookFile.tsx
│   └── folds.ts
├── main.tsx
├── utils.ts
├── styles.css
└── vite-env.d.ts
```

This gives each difficult subsystem one obvious home without introducing `state/`, `hooks/`, `services/`, `helpers/`, or `types/` dumping grounds.

`comp/` contains domain-independent interface components. They implement reusable interaction behavior but know nothing about dirdiff concepts.

`hud/` contains the dirdiff-aware interface: the application shell, Tabs, ChangeSet, files, navigation, visible HUD widgets, and overlays. It is a source namespace, not a single visual grouping or runtime structure. `App`, `ChangeSet`, `FileTree`, `HintHud`, `DebugHud`, and `HelpModal` all belong to `hud/`, even though they have different visual roles and store different state.

#### B. Nested feature packages

```text
hud/
├── tabs/
│   ├── HeadTab.tsx
│   ├── RefsTab.tsx
│   ├── BranchReviewTab.tsx
│   ├── PullRequestTab.tsx
│   └── PresetTab.tsx
├── changeSet/
│   ├── ChangeSet.tsx
│   ├── FileTree.tsx
│   ├── FileSequence.ts
│   └── FileCard.tsx
└── navigation/
    ├── NavigationProvider.tsx
    ├── selection.ts
    ├── scrollFollow.ts
    └── linePins.ts
```

This looks conventionally “organized,” but recreates the current problem: understanding one feature requires opening several files whose interfaces collectively expose almost all their implementation.

I would reject it for now.

#### C. Minimal large modules

```text
hud/
├── App.tsx
├── Tabs.tsx
├── ChangeSet.tsx
├── Rendering.tsx
└── navigation.tsx
```

Very easy tree, but `Rendering.tsx` would mix FileCard state, DiffGrid, notebooks, virtualization and headers. Stable renderer code would constantly overlap with orchestration changes.

Better than fragmentation, but Option A provides stronger boundaries.

### 65.2 Recommended component tree

```text
main
└── ToastProvider
    ├── Root
    │   └── QueryProvider
    │       └── root ErrorBoundary
    │           └── App
    │               ├── AppHeader
    │               │   ├── RepoSelect
    │               │   ├── engine Select
    │               │   ├── view Select
    │               │   ├── Profile
    │               │   └── ChangeSet portal targets
    │               ├── TabStrip
    │               └── Tabs
    │                   └── active Tab
    │                       ├── Tab-specific Controls
    │                       └── ChangeSet
    │                           └── NavigationProvider
    │                               ├── Hotkeys
    │                               ├── ChangeSetTitle
    │                               ├── FileTree
    │                               ├── FileCards
    │                               │   └── FileCard
    │                               │       ├── HuskFile
    │                               │       ├── LazyFile
    │                               │       └── FullFile
    │                               │           └── FileBody
    │                               │               ├── DiffGrid
    │                               │               └── NotebookFile
    │                               ├── hud-stack
    │                               │   ├── DebugHud
    │                               │   └── HintHud
    │                               └── HelpModal
    └── ToastViewport
```

### 65.3 Exact file responsibilities

#### `api/api.ts`

The complete Python boundary:

- Zod schemas;
- backend response types;
- `DiffParams`;
- private HTTP request functions;
- query definitions;
- mutation definitions;
- exported `api = { ... }` facade.

It does not export request functions, query keys, or generic HTTP helpers.

#### `api/queryClient.tsx`

Contains QueryClient construction and exports `QueryProvider`.

ChangeSet obtains the client through TanStack’s `useQueryClient()`. It does not import a global singleton.

`QueryProvider` requires error reporting as a callback rather than importing Toasts into `api/`. The private `Root()` component in `main.tsx` bridges the two providers:

```tsx
function Root() {
  const toast = useToasts();

  return (
    <QueryProvider onError={toast.showError}>
      <ErrorBoundary
        fallback={(error, retry) => (
          <ApplicationErrorPanel error={error} onRetry={retry} />
        )}
      >
        <App />
      </ErrorBoundary>
    </QueryProvider>
  );
}
```

That keeps the dependency direction clean:

```text
main → api
main → comp
api  ↛ comp
```

#### `comp/Select.tsx`

Exports:

```ts
Select;
SelectOption;
```

It stores popup visibility and implements keyboard interaction and dismissal. It knows nothing about repos, engines or Tabs.

#### `comp/AutocompleteInput.tsx`

Exports `AutocompleteInput`.

It stores:

- current input;
- edited status;
- popup visibility;
- highlighted choice.

It receives realtime choices, implements filtering and popup interaction, and invokes the caller-supplied `onEditNotification` and `onDone` callbacks at their specified interactions.

A plain `<input>` does not need its own wrapper. We should not create an `Input.tsx` until there is actual shared behavior to hide.

#### `comp/Toasts.tsx`

Contains the complete domain-independent error presentation system:

- `ToastProvider`;
- `useToasts`;
- `ToastViewport`;
- `ErrorPanel`;
- `ErrorPopover`;
- `RetryButton`;
- the reusable unexpected-error boundary.

These concepts share formatting and failure presentation, so splitting them into `Errors.tsx`, `RetryButton.tsx`, and `ToastContext.ts` would only scatter one subsystem.

#### `hud/App.tsx`

`App` stores selected profile and workspace-reset identity. `Workspace` stores:

- active Tab;
- selected repo;
- engine;
- inline/split view.

The components implement explicit URL update, reset, and reconstruction operations.

App renders `AppHeader`, `TabStrip`, and `Tabs`. It does not know how any Tab constructs `DiffParams`.

#### `hud/AppHeader.tsx`

Contains:

- `AppHeader`;
- header `RepoSelect`;
- engine and view controls;
- Profile placement;
- stable Portal targets for ChangeSet status and summary;
- one stable workspace-metadata status target for compact presentations supplied by Tabs and Profile.

It does not store manifest statistics, loading progress, or metadata queries. ChangeSet, Tabs, and Profile supply those presentations through Portals while retaining their state and query observers. Repo refs and defaults may present workspace warmup state from inactive eternal Tabs; Preset and Pull Request remain active-gated.

#### `hud/Tabs.tsx`

Exports:

```ts
TabId;
TabStrip;
Tabs;
```

Private components remain in the same file:

```text
HeadTab
RefsTab
BranchReviewTab
PullRequestTab
PresetTab
RepoGate
HeadControls
RefsControls
BranchReviewControls
PullRequestControls
PresetControls
```

This is one cohesive subsystem: turn user selections into a Tab-owned selected value, derive `DiffParams`, and render `ChangeSet`.

One component per Tab does not imply one file per Tab.

#### `hud/Profile.tsx`

`Profile` stores:

- profile menu/dialog state;
- username input state;
- preference editor state.

It observes canonical preference data and implements the username, preference, and explicit local-storage workflows.

The small profile storage operations can remain private here or in `hud/App.tsx`; a generic `storage.ts` is unnecessary.

#### `hud/ChangeSet.tsx`

Exports only `ChangeSet`.

Its private state and resource boundaries are:

```text
ChangeSet
└── ChangeSetContent
    └── ChangeSetSnapshot
```

- `Workspace` owns the global FileTree-open and DebugHud-open booleans shared by every Tab. `ChangeSet` owns file-expansion state and local Help state.
- `ChangeSetShell` stores the `HunkDisplay` signal and mounts ChangeSet-scoped Navigation for its active content lifetime.
- `ChangeSetContent` owns the manifest observer for one immutable complete `DiffParams` and performs manifest loading, error presentation, reload, and repository-cache-expiration restart.
- `ChangeSetSnapshot` owns the lazy-info observer, the ordered file-query observer collection, FileSequence state, combined progress, and admission state. It traverses the immutable manifest, performs explicit file loading, and renders `ChangeSetTitle`, `FileTree`, AppHeader Portal contributions, and `FileCard` content.

`ChangeSetSnapshot` also observes the selected profile's canonical preferences query and derives the reactive `aggressiveFolds` renderer input. `ChangeSetShell` mounts the ChangeSet-scoped `NavigationProvider`, one private active hotkey listener, adjacent private `HintHud` and `DebugHud` components, and a separate private `HelpModal`. The outer `ChangeSet` stores Help visibility; Workspace supplies the global DebugHud and FileTree visibility values.

`ChangeSetContent` is recreated when complete `DiffParams` changes. `ChangeSetSnapshot` is recreated when manifest data changes. No manifest-dependent observation, sequencing, or rendering lives above `ChangeSetSnapshot`.

`HunkDisplay` is private to `hud/ChangeSet.tsx`. It is the exact derived DOM mirror specified in `08_hunk_navigation.md`; it is not exported and is never Navigation state.

All three boundaries remain private to `hud/ChangeSet.tsx`; no new module is required.

`FileSequence` is a section of `ChangeSetSnapshot`'s implementation, not another exported abstraction or file.

Hotkeys map keys directly to Navigation, ChangeSet, workspace, Help, or Debug operations. There is no generic `Command`, command provider, command router, dispatch registry, or `commands.ts`.

`HintHud` and `DebugHud` are adjacent in source and adjacent inside the rendered `hud-stack`. `HelpModal` is defined separately and rendered outside that stack.

#### `hud/FileCard.tsx`

Exports `FileCard` and the `HunkPosition` type. `ChangeSet.tsx` imports `HunkPosition` from this child component contract when declaring its private `HunkDisplay`; no separate hunk-display module is introduced.

Private components:

```text
HuskFile
HuskFileHeader
LazyFile
LazyFileHeader
FullFile
FullFileHeader
FileRendererBoundary
FileRendererErrorStrip
FileBody
VirtualFile
```

It stores FileCard-local rich/virtual mode and geometry measurements. It also performs:

- rendering the reactive state supplied by ChangeSet as Husk/Full/Lazy presentation;
- invoking the ChangeSet-supplied explicit-load callback from the LazyFile plank;
- rich/virtual transitions;
- geometry preservation;
- local fold responsibility;
- direct rendering of the approved real and pseudo hunk identity attributes once Chapter 7 integrates hunk navigation;
- responding to `waitToEnrich` calls;
- rendering DiffGrid or NotebookFile;
- a FullFile renderer ErrorBoundary that replaces a failed renderer with a critical unrecoverable strip, preserves the stable FileCard article where possible, and performs no retry, hunk synthesis, selection repair, or automatic recovery.

This is the “meat” boundary. It should be a large, deep module.

#### `hud/navigation.tsx`

Exports:

```ts
RealHunkIdentity;
PseudoHunkIdentity;
HunkIdentity;
NavigationCommand;
NavigationResult;
Navigation;
NavigationProvider;
useNavigation;
```

`NavigationProvider` owns one stateful, disposable Navigation controller for one mounted ChangeSet. `useNavigation` returns that controller’s public operations to descendants of the nearest Provider.

FileTree Navigation is an approved scroll-only operation in `08_hunk_navigation.md`. It never calls `selectHunk` directly or indirectly.

The controller owns:

- the ChangeSet root reference;
- the non-reactive `"idle" | "input" | "document"` scroll-follow state;
- the private scroll guard, touch controller, and navigation listeners.

It performs DOM hunk traversal, `selectHunk`, Next/Previous, file, return-to-top, and approved user-scroll navigation. Exactly `nextHunk`, `prevHunk`, and `scrollFollow` call `selectHunk` directly. Scroll-follow considers only visible rich participating real targets and never selects virtual, pseudo, or skipped targets.

The controller stores only ephemeral browser-work state: root, one private scroll guard closure, one private touch controller closure, and listeners. Selected hunk identity and target order remain in DOM. Counters and FileTree highlighting render from `ChangeSetShell`'s `HunkDisplay` signal, which is an exact calculation from DOM and is never Navigation state. Rich/virtual state remains FileCard-local, and FileCard implements `waitToEnrich`.

Line pins are a separate system specified in [09_line_pins.md](09_line_pins.md). Navigation stores no line-pin identity, timers, or listener resources and performs no parsing, decoration, file loading, or restoration lifecycle. It accepts one coordinate-bearing `line` operation only after the file sequence reports browser layout and performs rich preparation plus the single final scroll.

The Context exposes Navigation operations but no controller state. There is no copied global hunk index, selected-hunk signal, independently owned selected-file state, generic setter or backend data.

`NavigationCommand` is the only retained `*Command` concept. It is an explicit typed navigation instruction and the input to `navigate`:

```ts
type NavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | { kind: "file"; fileIndex: number }
  | {
      kind: "line";
      fileIndex: number;
      target: LinePinTarget;
      abortSignal: AbortSignal;
    }
  | { kind: "top" };

type NavigationResult =
  | { state: "complete" }
  | { state: "missing" }
  | { state: "stopped" };

type Navigation = {
  navigate(command: NavigationCommand): Promise<NavigationResult>;
};
```

`complete` means the documented operation reached its destination, including a valid hunk-navigation no-op. `missing` is reserved for an absent exact line coordinate. `stopped` means cancellation or disposal prevented the operation's final action. Cancellation must not be represented as `missing`.

Navigation does not handle hotkeys, Help, Debug, tree visibility, view changes, reload, file expansion or backend work. Hotkeys merely call `navigation.navigate(...)` for `n`, `N` and `p`.

Provider cleanup removes every listener stored by the controller and calls `scrollGuard.stop()`. A pending wheel/touch expiry callback may only repeat the private `"idle"` assignment. A disposed controller performs no later DOM mutation or scrolling.

#### `hud/DiffGrid.tsx` and `hud/folds.ts`

Retained renderer kernel.

`DiffGrid.tsx` contains the imperative text-diff renderer. `folds.ts` remains separate because fold construction is a substantial pure algorithm with an independently testable contract.

The split/inline fold-reset issue remains an explicit post-rewrite TODO as agreed.

#### `hud/NotebookFile.tsx`

Renders notebook-specific content and preserves the bridge toward cell/output region identities.

Keeping it separate prevents provisional notebook behavior from complicating ordinary `FileCard` and `DiffGrid`.

#### `hud/linePins.ts`

Exports `LinePinTarget`, `ParsedLinePin`, `LinePinToggleResult`, `LinePinRestoration`, the per-`ChangeSetSnapshot` `LinePins` interface, and `linePins(): LinePins`.

`ChangeSetSnapshot` calls `linePins()` exactly once beneath the existing Navigation and Toast providers. The function obtains those required scoped interfaces and returns the retained instance. The instance validates and writes exact URL identity, owns cancellation for its active asynchronous restoration, and routes an already-ready semantic target through Navigation. ChangeSet calls `parseUrl()` before its initial file lane. DiffGrid calls `toggleUrlState()` directly and receives `"pinned"` or `"unpinned"` for its exact row. The module renders no component, stores no authoritative identity, inspects no DOM, paints no row, loads no file, observes no query, selects no hunk, and owns no history listener, timer, MutationObserver, or retry lifecycle.

#### `utils.ts`

Only genuinely domain-independent pure functions such as `wrapIndex` and `clamp`.

No file-tree helpers, hunk helpers, API helpers, or diff helpers belong here.

### 65.4 Current files that disappear

| Current file/concept          | Destination                                                                                           |
| ----------------------------- | ----------------------------------------------------------------------------------------------------- |
| `app/createDiffUiState.ts`    | actual owning components                                                                              |
| `app/createDiffNavigation.ts` | `hud/navigation.tsx` plus private hotkeys in `hud/ChangeSet.tsx`                                      |
| `app/createRepoResources.ts`  | TanStack queries in `api/api.ts`, workspace state in `hud/App.tsx`, and consumption in `hud/Tabs.tsx` |
| `app/diffParams.ts`           | removed; query definitions use `DiffParams` directly                                                  |
| `Controls.tsx`                | rewritten as private Tab components in `hud/Tabs.tsx`                                                 |
| `FileViews.tsx`               | split into `hud/ChangeSet.tsx` and `hud/FileCard.tsx`                                                 |
| `Header.tsx`                  | `hud/AppHeader.tsx`                                                                                   |
| `Hud.tsx`                     | private adjacent `HintHud` and `DebugHud`, plus separate `HelpModal`, in `hud/ChangeSet.tsx`          |
| `RepoPicker.tsx`              | `RepoSelect` in `hud/AppHeader.tsx` and `RepoGate` in `hud/Tabs.tsx`                                  |
| `fileUtils.ts`                | functions colocated with the components that use them                                                 |
| `hunkNavigation.ts`           | `hud/navigation.tsx`                                                                                  |
| `linePins.ts`                 | rewritten as `hud/linePins.ts` under [09_line_pins.md](09_line_pins.md)                               |
| `storage.ts`                  | private profile/workspace persistence                                                                 |
| `queryClient.ts` at root      | `api/queryClient.tsx`                                                                                 |
| entire `app/` directory       | removed                                                                                               |

### 65.5 Module rule

The important rule is:

> A component boundary is not automatically a file boundary.

Create a file only when it hides a substantial subsystem behind a small interface. Otherwise, keep the component private beside the component that uses it.

I would explicitly prohibit generic architectural buckets:

```text
hooks/
state/
stores/
services/
resources/
helpers/
types/
contexts/
```

The recommended Option A is the structure I’d put into the specification. `NavigationProvider` is a deliberate ChangeSet-local Context for one stateful imperative controller; it is not another Solid application store. No implementation files were changed.
