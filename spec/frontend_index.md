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
│   ├── changeSet/
│   │   ├── ChangeSet.tsx
│   │   ├── fileLane.ts
│   │   ├── FileTree.tsx
│   │   └── Shell.tsx
│   ├── diffGrid/
│   │   ├── DiffGrid.tsx
│   │   ├── folds.ts
│   │   └── rowDom.ts
│   ├── FileCard.tsx
│   ├── NotebookFile.tsx
│   ├── Profile.tsx
│   ├── Review.tsx
│   ├── Tabs.tsx
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

- `Root` connects application-level providers and renders `App`.

Direct interfaces:

- imports `QueryProvider` from `api/queryClient.tsx`;
- imports Toast components from `comp/Toasts.tsx`;
- imports `App` from `hud/App.tsx`;
- imports the application-lifetime `ReviewDraftRoot` from `hud/Review.tsx`;
- imports `styles.css`.

The Vite server turns JavaScript module changes into a full page reload before
module replacement begins. CSS updates use Vite's normal stylesheet
replacement.

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
- `DiffParams` and its Tab-specific variants, whose `tab` discriminator and
  complete parameters are sent unchanged to manifest;
- manifest trees and manifest statistics;
- lazy-file information;
- text and notebook file diffs;
- decorated row parts, folds, and engine warnings;
- review authors, targets, Threads, Comments, and structured review
  failure codes.

The facade is:

```ts
api.changeSet.manifest(params)
api.changeSet.lazyInfo(snapshotId)
api.changeSet.file(engine, snapshotId, entry, timeout)

api.review.snapshot(snapshotId)
api.review.thread.create()
api.review.thread.changeState(action)
api.review.comment.add()
api.review.comment.edit()
api.review.comment.delete()

api.repos.list()
api.repos.refs(projectId)
api.repos.defaults(projectId)
api.repos.remove()
api.repos.saveMainBranch()

api.presets.catalogs()

api.profile.preferences(profileId)
api.profile.login()
api.profile.register()
api.profile.rename()
api.profile.savePreferences()

api.pullRequest.prepare()
```

Each facade operation returns a TanStack query or mutation definition. HUD modules decide when to observe, prefetch, refetch, or execute it.

Browser review domain failures validate the direct `{ code, message }` body and
become `ReviewRequestError`. Callers classify its stable `code`; presentation
never interprets `message` prose.

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

It uses the `api.profile` facade for exact-name login, registration, rename,
preferences loading, and preferences saving. With no selected Profile, login is
the primary action and creation is a separate explicit action. A selected
Profile may log in as another Profile, rename itself, edit preferences, or log
out. Login never creates a missing Profile.

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

Each Tab retains one complete selected `DiffParams` value and passes that same
value to `ChangeSet`. `ChangeSet` does not switch over the Tab to reconstruct it.
The Pull Request value contains its URL and the two commits returned by
preparation; it contains no Branch Review selections. Engine is independent of
the selected value and is supplied separately for file rendering.

Compare Refs and Branch Review seed their controls from URL and repository
metadata but create no selected value or `ChangeSet` until explicit Load. Once
loaded, their selected value and `ChangeSet` remain mounted across Tab switches.

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

The ChangeSet lives in the `hud/changeSet/` directory: `ChangeSet.tsx` is the
lifetime and snapshot orchestration, `Shell.tsx` is the mounted frame, and
`FileTree.tsx` is the sidebar.

### `hud/changeSet/ChangeSet.tsx`

Exports:

```ts
ChangeSet
```

`ChangeSet` receives:

```ts
active
params
engine
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
- `ReviewSnapshotBoundary` keeps Snapshot review state mounted across engine replacement;
- `ChangeSetSnapshot` represents one manifest and its opaque `snapshot_id`.

Private presentation components:

- `AppHeaderFileStatus`;
- `ManifestStatistics`.

`params` is the complete selected Tab value used by manifest. `engine` is a
separate file-rendering choice and does not participate in manifest or Room
identity. `ChangeSet` stores per-file expansion across replacement of its mounted
snapshot.

`ChangeSetSnapshot`:

- resolves the URL line pin and creates the snapshot's one file lane;
- writes the snapshot's initial hunk selection after its FileCards mount;
- constructs the FileTree data from the lane's canonical states;
- renders one stable `FileCard` per manifest entry;
- creates the snapshot’s `LinePins` interface;
- supplies header status and statistics from the lane's progress.

### `hud/changeSet/Shell.tsx`

Exports:

```ts
ChangeSetShell
HunkDisplay
```

`ChangeSetShell` mounts one ChangeSet frame: the root element with its
Navigation provider, the side-scoped text-selection behavior, the direct
hotkey listener, the `HunkDisplayObserver` DOM mirror, and the fixed
overlays. Callers supply every UI operation as an explicit callback and
render the ChangeSet body through the children render prop, which receives
the shell's `HunkDisplay` accessor. The shell observes no queries, stores no
backend data, and never selects hunks, navigates, or owns file expansion.

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

### `hud/changeSet/FileTree.tsx`

Exports:

```ts
FileTree
calculateDirectoryExpansion
fileExpanded
```

`FileTree` renders the sidebar over the lane's canonical file states through
the documented narrow props contract: the immutable manifest tree, the states
accessor, expansion values, and callbacks that may change only tree
visibility or file expansion. `calculateDirectoryExpansion` derives directory
reachability and `fileExpanded` resolves one file's expansion policy; both
are shared with `ChangeSetSnapshot`, which owns the expansion state itself.
The tree may scroll only its own groups container and never changes hunk
selection, loads files, or moves the main page.

Private presentation components:

- `FileTreeDirectory`, `FileTreeFile`, `FileTreeNode`, `FileTreeContent`;
- `TreeStatistics`;
- `TreeVisibilityIndicator`.

### `hud/changeSet/fileLane.ts`

Exports:

```ts
createFileLane
manifestEntryKey
fileDisplayName
manifestFilesInOrder
FileLane
FileState (with HuskFileState, FullFileState, LazyFile, LazyFileState)
FileLaneActivity
FileLaneLineTarget
```

`createFileLane` builds the canonical data lifecycle of one immutable
snapshot, described by [`file-lane.md`](file-lane.md). The lane owns the
lazy-info observer, the per-index file-query view signals and payload slots,
render admission, progress, and idempotent cancellation. Its inputs are plain
data (engine, `snapshot_id`, the validated manifest-order file list and its
canonical response names) plus two host behaviors: an optional line-target
restoration gate and the explicit-load notification the host uses for its
expansion policy. The lane performs no presentation: no DOM, toasts, URL or
line-pin identity, file expansion, or navigation.

`ReviewSnapshotBoundary` wraps the engine-keyed File lane in `ReviewProvider`
and passes the exact selected Profile through as browser review authorship.
Each History Thread's explicit go-to action finds the exact manifest File pair and invokes
exact-line navigation for an already loaded FullFile; it does not select a hunk
or load a File. The File lane publishes only indexes whose FullFile is mounted,
so History keeps go-to disabled for Lazy and Husk entries. Drafts expose one
`Continue editing` action for new Threads. Reply and edit drafts render directly
in their complete canonical Thread and Comment.

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

- exact nullable left/right review File pair;
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

The DiffGrid lives in the `hud/diffGrid/` directory: `DiffGrid.tsx` is the
reactive component and `rowDom.ts` is the pure imperative row-DOM kernel.

### `hud/diffGrid/DiffGrid.tsx`

Exports:

```ts
DiffGrid
```

Private components:

- `SplitHeader`;
- `InlineHeader`;
- `ImperativeDiffLines`.

Inputs:

- exact nullable left/right review File pair;
- manifest file index;
- file display name;
- nullable notebook region;
- old and new labels;
- validated diff rows;
- fold hints;
- view mode;
- fold policies;
- `LinePins`;
- nullable ordinary-line-one side reporting, present for ordinary File grids.

`DiffGrid` renders decorated text parts, line numbers, fold rows, hunk targets,
and line-pin coordinates.

It uses `folds.ts` to construct visible rows and uses `LinePins` to read or change line-pin URL state.

### `hud/diffGrid/rowDom.ts`

Exports:

```ts
renderSplitRowsDom
renderInlineRowsDom
renderCombinedInlineRowsDom
Side
```

The kernel builds one complete detached row fragment per call from validated
backend rows and fold state; every function is a pure DOM constructor with no
Solid reactivity, component state, or queries. It owns row chunking: large
renders stream rows through fixed-size content-visibility containers, and one
idle-paced module-level warm-up pass renders each new chunk once so the
browser records its real height. It must not listen to events, own review
markers or line pins, decide view modes, or fetch anything.

### `hud/NotebookFile.tsx`

Exports:

```ts
NotebookFile
```

Private component:

- `NotebookCellView`.

Inputs:

- exact nullable left/right review File pair;
- manifest file index;
- validated notebook diff;
- view mode;
- fold policy;
- `LinePins`.

`NotebookFile` renders the notebook summary and changed cells in backend order. Each source region delegates its text rows to `DiffGrid` with a stable notebook region key.

### `hud/Review.tsx`

Exports:

```ts
ReviewDraftRoot
ReviewProvider
useReview
newReviewId
ReviewBinding
ReviewMarkerState
ReviewTextGridBinding
ReviewCodeAnchor
```

`ReviewDraftRoot` owns the application-lifetime strict persisted draft document
and the set of drafts whose single HTTP action is in flight.
`ReviewProvider` observes the complete canonical Thread set for one exact
Snapshot, receives the selected `StoredProfile` and ChangeSet-owned History
visibility, performs explicit Thread and Comment actions, owns active Comment
input and inline-Thread presentation, and renders History. HTTP pagination is a
private transport detail of the canonical Snapshot query; every page uses the
append-only activity boundary returned by its first page.

`ReviewBinding` is the narrow renderer interface. FileCard reports mounted File
headers used by History placement and File-start targets. DiffGrid reads compact
line-marker descriptors and activates exact File, public-region, side, and line
actions. Neither renderer performs review HTTP operations or stores Thread data.

`ReviewMarkerState` contains only the controls actually represented on a line.
`ReviewTextGridBinding` identifies one immutable text grid.
`ReviewCodeAnchor` identifies one connected code cell and its selected marker.

The complete review persistence, marker, History, Comment-input, navigation,
error, and browser/agent HTTP behavior is specified once in
[`reviews.md`](reviews.md).

### `hud/diffGrid/folds.ts`

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
writeInitialHunkSelection

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
| `main.tsx` | `Review.tsx` | application-lifetime persisted draft boundary |
| `App.tsx` | `AppHeader.tsx` | workspace values and explicit selection callbacks |
| `App.tsx` | `Tabs.tsx` | shared workspace values and workflow callbacks |
| `Tabs.tsx` | `changeSet/ChangeSet.tsx` | complete selected `DiffParams`, separate engine, and shared display state |
| `changeSet/ChangeSet.tsx` | `changeSet/Shell.tsx` | mounted frame, UI-operation callbacks, and the HunkDisplay accessor |
| `changeSet/ChangeSet.tsx` | `changeSet/FileTree.tsx` | manifest tree, canonical states, and shared expansion policy |
| `changeSet/ChangeSet.tsx` | `api.ts` | manifest and preferences definitions |
| `changeSet/ChangeSet.tsx` | `changeSet/fileLane.ts` | one file lane per snapshot and its canonical file states |
| `changeSet/fileLane.ts` | `api.ts` | lazy-info and file query definitions |
| `changeSet/ChangeSet.tsx` | `FileCard.tsx` | one manifest-position file state and explicit file actions |
| `changeSet/*` | `navigation.tsx` | mounted ChangeSet root and navigation operations |
| `changeSet/ChangeSet.tsx` | `linePins.ts` | one line-pin interface per snapshot |
| `changeSet/ChangeSet.tsx` | `Review.tsx` | one exact Snapshot review boundary and explicit File jump |
| `Review.tsx` | `api.ts` | bulk Snapshot review query and Profile-authored Thread and Comment mutations |
| `FileCard.tsx` | `Review.tsx` | File marker state and File Comment-input activation |
| `diffGrid/DiffGrid.tsx` | `Review.tsx` | line marker state and text Comment-input activation |
| `FileCard.tsx` | `diffGrid/DiffGrid.tsx` | complete text-file rendering inputs |
| `FileCard.tsx` | `NotebookFile.tsx` | complete notebook rendering inputs |
| `NotebookFile.tsx` | `diffGrid/DiffGrid.tsx` | one notebook source region |
| `diffGrid/DiffGrid.tsx` | `diffGrid/rowDom.ts` | validated rows, fold state, and the two render callbacks |
| `diffGrid/*` | `diffGrid/folds.ts` | rows, fold hints, and expansion |
| `diffGrid/DiffGrid.tsx` | `linePins.ts` | URL parsing and direct pin toggling |
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
