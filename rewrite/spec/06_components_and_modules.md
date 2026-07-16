## 65. Component and module architecture

I’d use a shallow, component-owner structure: a file represents a substantial owner, not every JSX component. Small supporting components remain private in their owner’s file.

### 65.1 Three viable shapes

#### A. Flat owner modules — recommended

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

`comp/` contains domain-independent interface components. They own reusable interaction behavior, but know nothing about dirdiff concepts.

`hud/` contains the project-aware dirdiff interface: the application shell, Tabs, ChangeSet, files, navigation, visible HUD widgets, and overlays. It is a source namespace, not a single visual grouping and not one runtime owner. `App`, `ChangeSet`, `FileTree`, `HintHud`, `DebugHud`, and `HelpModal` all belong to `hud/`, even though they have different visual roles and state owners.

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

Owns QueryClient construction and exports `QueryProvider`.

ChangeSet obtains the client through TanStack’s `useQueryClient()`. It does not import a global singleton.

`QueryProvider` requires error reporting as a callback rather than importing Toasts into `api/`. The private `Root()` component in `main.tsx` bridges the two providers:

```tsx
function Root() {
  const toast = useToasts();

  return (
    <QueryProvider
      onError={toast.showError}
    >
      <ErrorBoundary
        fallback={(error, retry) => (
          <ApplicationErrorPanel
            error={error}
            onRetry={retry}
          />
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
Select
SelectOption
```

It owns popup interaction, keyboard behavior and dismissal. It knows nothing about repos, engines or Tabs.

#### `comp/AutocompleteInput.tsx`

Exports `AutocompleteInput`.

It owns:

- current input;
- edited status;
- choices and filtering;
- popup interaction;
- `onEditNotification`;
- `onDone`.

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

Owns only the application shell and workspace state:

- active Tab;
- selected repo;
- engine;
- inline/split view;
- selected profile;
- reset/reconstruction commands.

It renders `AppHeader`, `TabStrip`, and `Tabs`. It does not know how any Tab constructs `DiffParams`.

#### `hud/AppHeader.tsx`

Contains:

- `AppHeader`;
- header `RepoSelect`;
- engine and view controls;
- Profile placement;
- stable Portal targets for ChangeSet status and summary;
- one stable workspace-metadata status target for compact presentations owned by Tabs and Profile.

It does not own manifest statistics, loading progress, or metadata queries. ChangeSet, Tabs, and Profile supply those presentations through Portals while retaining their logical ownership. Repo refs and defaults may project workspace warmup state from inactive eternal Tabs; Preset and Pull Request remain active-gated.

#### `hud/Tabs.tsx`

Exports:

```ts
TabId
TabStrip
Tabs
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

Owns:

- profile menu/dialog state;
- username workflow;
- preferences workflow;
- explicit local-storage updates;
- preference mutations.

The small profile storage operations can remain private here or in `hud/App.tsx`; a generic `storage.ts` is unnecessary.

#### `hud/ChangeSet.tsx`

Exports only `ChangeSet`.

It owns:

- manifest observation;
- lazy-info observation;
- the ordered file-query observer collection;
- deriving and supplying the shared per-file states used by FileTree and FileCard;
- explicit file-load and retry operations invoked by LazyFile planks;
- strict FileSequence;
- combined progress;
- expansion state;
- observing the selected profile's canonical preferences query and deriving the reactive `aggressiveFolds` renderer input;
- `ChangeSetTitle`;
- `FileTree`;
- AppHeader Portal contributions;
- mapping manifest files to `FileCard`;
- mounting the ChangeSet-scoped `NavigationProvider`;
- one private active hotkey listener;
- independent Help and Debug visibility;
- adjacent private `HintHud` and `DebugHud` components;
- a separate private `HelpModal`;
- ChangeSet reload.

`FileSequence` is a section of this owner’s implementation, not another exported abstraction or file.

Hotkeys map keys directly to Navigation, ChangeSet, workspace, Help, or Debug operations. There is no generic `Command`, command provider, command router, dispatch registry, or `commands.ts`.

`HintHud` and `DebugHud` are adjacent in source and adjacent inside the rendered `hud-stack`. `HelpModal` is defined separately and rendered outside that stack.

#### `hud/FileCard.tsx`

Exports only `FileCard`.

Private components:

```text
HuskFile
HuskFileHeader
LazyFile
LazyFileHeader
FullFile
FullFileHeader
FileBody
VirtualFile
```

It owns:

- projecting the reactive state supplied by ChangeSet into Husk/Full/Lazy presentation;
- invoking the ChangeSet-supplied explicit-load callback from the LazyFile plank;
- rich/virtual transitions;
- geometry preservation;
- local fold responsibility;
- `projectSelectedHunk`;
- responding to `waitToEnrich` requests;
- rendering DiffGrid or NotebookFile;
- FileCard-level error containment.

This is the “meat” boundary. It should be a large, deep module.

#### `hud/navigation.tsx`

Exports:

```ts
RealHunkIdentity
PseudoHunkIdentity
HunkIdentity
NavigationCommand
Navigation
NavigationProvider
useNavigation
```

`NavigationProvider` owns one stateful, disposable Navigation controller for one mounted ChangeSet. `useNavigation` returns that controller’s public operations to descendants of the nearest Provider.

It owns:

- the ChangeSet root reference;
- DOM hunk traversal;
- `selectHunk`;
- `clearHunkSelection`;
- Next/Previous;
- direct-hunk and return-to-top navigation;
- the non-reactive `idle | user | navigation` scroll-source gate;
- throttled scroll-follow;
- navigation listener and animation-frame lifecycle;
- isolated line-pin parsing, highlighting, retry and restoration.

The controller stores only ephemeral coordination state: root, scroll source, scheduled frame, listeners, and line-pin retry/stabilization bookkeeping. Selected hunk identity, target order, counters and FileTree highlighting remain in or are derived from DOM. Line-pin identity remains in the URL. Rich/virtual state and `waitToEnrich` remain FileCard-owned.

The Context exposes Navigation operations but no controller state. There is no copied global hunk index, selected-hunk signal, selected-file state, generic setter or backend data.

`NavigationCommand` is the only retained `*Command` concept. It is an explicit typed navigation instruction and the input to `navigate`:

```ts
type NavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | { kind: "hunk"; hunk: HunkIdentity }
  | { kind: "top" };
```

Navigation does not own hotkeys, Help, Debug, tree visibility, view changes, reload, file expansion or backend work. Hotkeys merely call `navigation.navigate(...)` for `n`, `N` and `p`.

Provider cleanup cancels every navigation-owned listener, frame, timer, retry and observer. A disposed controller performs no later DOM mutation or scrolling.

#### `hud/DiffGrid.tsx` and `hud/folds.ts`

Retained renderer kernel.

`DiffGrid.tsx` owns the imperative text-diff renderer. `folds.ts` remains separate because fold construction is a substantial pure algorithm with an independently testable contract.

The split/inline fold-reset issue remains an explicit post-rewrite TODO as agreed.

#### `hud/NotebookFile.tsx`

Owns notebook-specific rendering and preserves the bridge toward cell/output region identities.

Keeping it separate prevents provisional notebook behavior from complicating ordinary `FileCard` and `DiffGrid`.

#### `utils.ts`

Only genuinely domain-independent pure functions such as `wrapIndex` and `clamp`.

No file-tree helpers, hunk helpers, API helpers, or diff helpers belong here.

### 65.4 Current files that disappear

| Current file/concept | Destination |
|---|---|
| `app/createDiffResources.ts` | queries in `api/api.ts`; sequence in `hud/ChangeSet.tsx` |
| `app/createDiffUiState.ts` | actual owning components |
| `app/createDiffNavigation.ts` | `hud/navigation.tsx` plus private hotkeys in `hud/ChangeSet.tsx` |
| `app/createRepoResources.ts` | TanStack queries in `api/api.ts`, workspace ownership in `hud/App.tsx`, and consumption in `hud/Tabs.tsx` |
| `app/diffParams.ts` | removed; query definitions use `DiffParams` directly |
| `Controls.tsx` | rewritten as private Tab components in `hud/Tabs.tsx` |
| `FileViews.tsx` | split into `hud/ChangeSet.tsx` and `hud/FileCard.tsx` |
| `Header.tsx` | `hud/AppHeader.tsx` |
| `Hud.tsx` | private adjacent `HintHud` and `DebugHud`, plus separate `HelpModal`, in `hud/ChangeSet.tsx` |
| `RepoPicker.tsx` | `RepoSelect` in `hud/AppHeader.tsx` and `RepoGate` in `hud/Tabs.tsx` |
| `fileUtils.ts` | functions colocated with their actual owners |
| `hunkNavigation.ts` | `hud/navigation.tsx` |
| `linePins.ts` | `hud/navigation.tsx` |
| `storage.ts` | private profile/workspace persistence |
| `queryClient.ts` at root | `api/queryClient.tsx` |
| entire `app/` directory | removed |

### 65.5 Module rule

The important rule is:

> A component boundary is not automatically a file boundary.

Create a file only when it hides a substantial subsystem behind a small interface. Otherwise, keep the component private beside its owner.

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
