# Frontend modules and components

## 1. Purpose

This chapter defines the frontend source layout, the responsibility of every module, the components contained within each module, and the interfaces connecting them.

## 2. Source layout

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
│   ├── ChangeSet.tsx
│   ├── DiffGrid.tsx
│   ├── FileCard.tsx
│   ├── NotebookFile.tsx
│   ├── Profile.tsx
│   ├── Tabs.tsx
│   ├── folds.ts
│   ├── linePins.ts
│   └── navigation.tsx
├── main.tsx
├── styles.css
├── utils.ts
└── vite-env.d.ts
```

## 3. Directory contracts

### `api/`

Defines the complete Python-backend interface.

It contains validated backend types, HTTP operations, TanStack query definitions, mutation definitions, query keys, and the application query client.

### `comp/`

Contains domain-independent UI components.

These components receive ordinary values, elements, and callbacks. They are reusable without knowledge of repositories, diffs, tabs, files, hunks, or profiles.

### `hud/`

Contains the dirdiff interface.

It contains the application shell, tabs, ChangeSets, files, renderers, navigation, line pins, and project-specific controls.

### Root modules

Root modules mount the application, provide shared presentation, and contain the small utilities used across directories.

## 4. Root modules

### `main.tsx`

The browser entrypoint.

Public surface: none.

Private component:

- `Root` connects application-level services and renders `App`.

Direct interfaces:

- imports `QueryProvider` from `api/queryClient.tsx`;
- imports Toast components from `comp/Toasts.tsx`;
- imports `App` from `hud/App.tsx`;
- imports `styles.css`.

JavaScript module updates reload the page. CSS updates use Vite’s normal stylesheet replacement.

### `styles.css`

Defines the complete visual presentation of the frontend.

Components provide semantic classes and data attributes. This stylesheet defines their layout, geometry, typography, colors, sticky positioning, overlays, file rendering, navigation decoration, and responsive behavior.

### `utils.ts`

Exports three domain-independent operations:

```ts
assert(condition: boolean, message: string | null): asserts condition;
expect<T>(value: T | null | undefined, message: string | null): T;
clamp(value: number, min: number, max: number): number;
```

### `vite-env.d.ts`

Provides Vite’s ambient TypeScript declarations.

## 5. API modules

### `api/api.ts`

Defines the complete backend data model and exports the `api` facade.

Main exported type groups:

- repositories, profiles, preferences, refs, branches, and presets;
- `DiffParams` and its workflow-specific variants;
- manifest trees and manifest statistics;
- lazy-file information;
- text and notebook file diffs;
- rows, tokens, syntax spans, folds, and engine warnings.

The facade is:

```ts
api.changeSet.manifest(params)
api.changeSet.lazyInfo(params, cacheId)
api.changeSet.file(params, cacheId, entry, timeout)

api.repos.list()
api.repos.refs(projectId)
api.repos.defaults(projectId)
api.repos.remove()
api.repos.saveMainBranch()

api.presets.catalogs()

api.profile.preferences(profileId)
api.profile.register()
api.profile.rename()
api.profile.savePreferences()

api.pullRequest.prepare()
```

Each facade operation returns a TanStack query or mutation definition. HUD modules decide when to observe, prefetch, refetch, or execute it.

The module also exports:

```ts
isRepositoryCacheExpiration(error): boolean;
```

### `api/queryClient.tsx`

Exports:

```ts
QueryProvider
```

`QueryProvider` receives application children and an error-reporting callback. It supplies the configured TanStack Query client used by every query and mutation observer.

## 6. Domain-independent components

### `comp/AutocompleteInput.tsx`

Exports:

```ts
AutocompleteInput
```

Inputs:

- field label and placeholder;
- realtime seed value;
- realtime grouped choices;
- optional prefix and action elements;
- input visibility;
- optional edit notification;
- `onDone(value)`.

The component stores its own input, popup, focus, and highlighted-choice state. The caller receives completed user input through `onDone`.

### `comp/Select.tsx`

Exports:

```ts
Select
SelectOption
```

Inputs:

- label;
- selected value and its visible label;
- options;
- disabled state;
- optional open notification;
- optional per-option action;
- `onChange(value)`.

The component stores its own popup state. The caller stores the selected value.

### `comp/Toasts.tsx`

Exports:

```ts
ToastProvider
useToasts
presentError

ErrorPanel
RetryButton
ErrorPopover
UnexpectedErrorBoundary
ApplicationErrorPanel
```

Exported data contracts:

```ts
ErrorToast
TransientToast
Toast
PresentedError
ToastCommands
ErrorPopoverProps
```

Private components:

- `ToastViewport` renders active Toasts;
- `ToastCard` renders one Toast;
- `UnexpectedErrorPanel` renders a contained unexpected component failure.

Other modules report errors through `ToastCommands`. Local failure surfaces compose `ErrorPanel`, `ErrorPopover`, and `RetryButton`.

## 7. Application HUD

### `hud/App.tsx`

Exports:

```ts
App
DiffViewMode
```

Private component:

- `Workspace` stores one URL-constructed workspace.

`App` stores:

- selected profile;
- workspace reset identity.

`Workspace` stores:

- active Tab;
- selected repository ID;
- diff engine;
- split or inline view;
- FileTree visibility;
- DebugHud visibility.

Direct interfaces:

- supplies workspace values and actions to `AppHeader`;
- supplies Tab selection and workflow actions to `TabStrip` and `Tabs`;
- receives complete user selections through callbacks;
- writes canonical workspace state into the URL.

### `hud/AppHeader.tsx`

Exports:

```ts
AppHeader
AppHeaderOutlets
RepositoryState
```

Private component:

- `RepoSelect` renders the global repository selector.

`AppHeader` receives current workspace values and explicit callbacks for:

- profile selection;
- repository selection and removal;
- engine selection;
- view selection.

It observes the canonical repository-list query.

`AppHeaderOutlets` exposes the stable header destinations used by an active ChangeSet:

```ts
type AppHeaderOutlets = {
  status(): HTMLDivElement;
  summary(): HTMLDivElement;
};
```

A separate metadata destination receives repository, refs, presets, pull-request, and profile status.

### `hud/Profile.tsx`

Exports:

```ts
Profile
StoredProfile
loadStoredProfile
```

Private components:

- `ProfilePreferencesStatus`;
- `PreferencesModal`;
- `PreferencesEditor`.

`Profile` receives the selected profile and reports complete profile selection changes through:

```ts
onSelected(profile)
onForgotten()
```

It uses the `api.profile` facade for registration, rename, preferences loading, and preferences saving.

Confirmed profile identity is persisted in browser storage. Preferences remain canonical backend data.

## 8. Tabs

### `hud/Tabs.tsx`

Exports:

```ts
TabId
TabStrip
Tabs
```

`TabId` identifies the five application Tabs:

```ts
"head"
"refs"
"branch-review"
"pull-request"
"preset"
```

Public components:

- `TabStrip` displays and changes the active Tab;
- `Tabs` keeps the Tab components mounted and supplies shared workspace inputs.

Private Tab components:

- `HeadTab`;
- `RefsTab`;
- `BranchReviewTab`;
- `PullRequestTab`;
- `PresetTab`.

Private control components:

- `RefsControls`;
- `BranchReviewControls`;
- `BranchSelectionFields`.

Private repository components:

- `RepoGate`;
- `RefsWithoutRepo`;
- `RefsRepoTab`;
- `BranchReviewRepoTab`.

Private metadata components:

- `MetadataStatusPortal`;
- `MetadataRefresh`.

Each Tab stores its workflow-specific selected data. When that data is complete, the Tab constructs one complete `DiffParams` value and renders `ChangeSet`.

Shared inputs from `App` include:

- active Tab;
- repository ID;
- engine;
- view;
- selected profile;
- FileTree visibility;
- DebugHud visibility;
- header outlets;
- explicit workspace callbacks.

## 9. ChangeSet

### `hud/ChangeSet.tsx`

Exports:

```ts
ChangeSet
```

`ChangeSet` receives:

```ts
active
params
view
fileTreeOpen
debugHudOpen
profile
appHeaderOutlets
onToggleView
onFileTreeOpenChange
onDebugHudOpenChange
```

Private lifetime components:

- `ChangeSetContent` observes the manifest and controls snapshot replacement;
- `ChangeSetShell` provides the mounted ChangeSet DOM and navigation interface;
- `ChangeSetSnapshot` represents one manifest and its backend cache ID.

Private interaction components:

- `Hotkeys`;
- `HunkDisplayObserver`.

Private HUD components:

- `HintHud`;
- `DebugHud`;
- `DebugMetric`;
- `HelpModal`;
- `HotkeyHelpSection`;
- `HotkeyHelpRow`.

Private presentation components:

- `FileTree`;
- `AppHeaderFileStatus`;
- `ManifestStatistics`;
- `TreeStatistics`;
- `TreeVisibilityIndicator`.

`ChangeSet` stores per-file expansion across replacement of its mounted
snapshot.

`ChangeSetSnapshot`:

- observes lazy information and ordered file queries;
- runs sequential file loading;
- constructs the FileTree data;
- renders one stable `FileCard` per manifest entry;
- creates the snapshot’s `LinePins` interface;
- supplies header status and statistics.

## 10. Files

### `hud/FileCard.tsx`

Exports:

```ts
FileCard
HunkPosition
```

`FileCard` receives one of three file states:

- Husk;
- Full;
- Lazy.

It also receives:

- manifest file index;
- expansion state;
- render admission;
- engine and view;
- fold policy;
- line-pin interface;
- hunk-display data;
- explicit load, retry, and expansion callbacks.

Private state components:

- `FileCardContent`;
- `HuskFile`;
- `FullFile`;
- `VirtualFile`;
- `LazyFileView`.

Private header components:

- `HuskFileHeader`;
- `FullFileHeader`;
- `LazyFileHeader`;
- `HunkCounterBadges`.

Private body and failure components:

- `FileBody`;
- `FileRendererBoundary`;
- `FileRendererErrorStrip`;
- `DeferredFilePlank`.

Private statistics components:

- `FileStatistics`;
- `LazyStatistics`;
- `VisibilityIndicator`.

`FileBody` routes text files to `DiffGrid` and notebook files to `NotebookFile`.

A FullFile exposes its rich-materialization and line-preparation operations through its FileCard DOM interface for `navigation.tsx`.

### `hud/DiffGrid.tsx`

Exports:

```ts
DiffGrid
```

Private components:

- `SplitHeader`;
- `InlineHeader`;
- `ImperativeDiffLines`.

Inputs:

- manifest file index;
- file display name;
- nullable notebook region;
- old and new labels;
- validated diff rows;
- fold hints;
- view mode;
- fold policies;
- `LinePins`.

`DiffGrid` renders text rows, line numbers, syntax spans, fold rows, hunk targets, and line-pin coordinates.

It uses `folds.ts` to construct visible rows and uses `LinePins` to read or change line-pin URL state.

### `hud/NotebookFile.tsx`

Exports:

```ts
NotebookFile
```

Private component:

- `NotebookCellView`.

Inputs:

- manifest file index;
- validated notebook diff;
- view mode;
- fold policy;
- `LinePins`.

`NotebookFile` renders the notebook summary and changed cells in backend order. Each source region delegates its text rows to `DiffGrid` with a stable notebook region key.

### `hud/folds.ts`

Exports:

```ts
FoldRow
RenderRow
parseFoldHints()
addFoldRows()
isFoldRow()
```

`DiffGrid` supplies backend rows, fold hints, and fold expansion state. The module returns the visible sequence of ordinary rows and fold rows.

## 11. Navigation

### `hud/navigation.tsx`

Exports:

```ts
NavigationProvider
useNavigation

Navigation
NavigationCommand
NavigationResult

RealHunkIdentity
PseudoHunkIdentity
HunkIdentity
```

`NavigationCommand` supports:

```ts
{ kind: "next-hunk" }
{ kind: "previous-hunk" }
{ kind: "file"; fileIndex }
{ kind: "line"; fileIndex; target; abortSignal }
{ kind: "top" }
```

`Navigation` exposes:

```ts
navigate(command): Promise<NavigationResult>;
```

The module reads hunk identities and FileCard operations from the mounted ChangeSet DOM.

Its direct consumers are:

- ChangeSet hotkeys and HintHud;
- FileTree navigation;
- line-pin restoration.

### `hud/linePins.ts`

Exports:

```ts
linePins
LinePins
LinePinTarget
ParsedLinePin
LinePinToggleResult
LinePinRestoration
PreparedLine
```

A `LinePinTarget` contains:

```ts
file
region
side
line
```

`linePins()` creates one interface for one ChangeSet snapshot:

```ts
type LinePins = {
  parseUrl(): ParsedLinePin;
  toggleUrlState(target: LinePinTarget): LinePinToggleResult;
  restore(
    target: LinePinTarget,
    fileIndex: number,
    changeSetAbortSignal: AbortSignal,
  ): Promise<LinePinRestoration>;
};
```

`DiffGrid` uses `parseUrl()` and `toggleUrlState()`.

`ChangeSetSnapshot` uses `restore()` after parsing the initial URL target.

`LinePins.restore()` sends the final line-navigation operation through `Navigation`.

## 12. Direct module interfaces

| Caller | Callee | Interface |
|---|---|---|
| `main.tsx` | `App.tsx` | `App` |
| `App.tsx` | `AppHeader.tsx` | workspace values and explicit selection callbacks |
| `App.tsx` | `Tabs.tsx` | shared workspace values and workflow callbacks |
| `Tabs.tsx` | `ChangeSet.tsx` | complete `DiffParams` and shared display state |
| `ChangeSet.tsx` | `api.ts` | manifest, lazy-info, file, and preferences definitions |
| `ChangeSet.tsx` | `FileCard.tsx` | one manifest-position file state and explicit file actions |
| `ChangeSet.tsx` | `navigation.tsx` | mounted ChangeSet root and navigation operations |
| `ChangeSet.tsx` | `linePins.ts` | one line-pin interface per snapshot |
| `FileCard.tsx` | `DiffGrid.tsx` | complete text-file rendering inputs |
| `FileCard.tsx` | `NotebookFile.tsx` | complete notebook rendering inputs |
| `NotebookFile.tsx` | `DiffGrid.tsx` | one notebook source region |
| `DiffGrid.tsx` | `folds.ts` | rows, fold hints, and expansion |
| `DiffGrid.tsx` | `linePins.ts` | URL parsing and direct pin toggling |
| `linePins.ts` | `navigation.tsx` | exact line navigation |
| `AppHeader.tsx` | `Select.tsx` | engine, view, and repository controls |
| `Tabs.tsx` | `AutocompleteInput.tsx` | refs and branch input |
| HUD modules | `Toasts.tsx` | Toast commands and local error presentation |
| HUD modules | `api.ts` | validated backend query and mutation definitions |

## 13. Module invariants

- `api/` is the frontend’s complete backend interface.
- `comp/` components are domain-independent.
- `hud/` contains the dirdiff-specific interface.
- Public types live with the interface that introduces them.
- Supporting components remain private inside their subsystem module.
- Backend data remains in TanStack Query results.
- Client state remains in the component whose lifetime matches that state.
- DOM-backed navigation identity remains in the rendered ChangeSet DOM.
