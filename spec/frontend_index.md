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
│   ├── Review.tsx
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

- `Root` connects application-level providers and renders `App`.

Direct interfaces:

- imports `QueryProvider` from `api/queryClient.tsx`;
- imports Toast components from `comp/Toasts.tsx`;
- imports `App` from `hud/App.tsx`;
- imports the application-lifetime `ReviewDraftRoot` from `hud/Review.tsx`;
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
- `ChangeSetShell` provides the mounted ChangeSet DOM and navigation interface;
- `ReviewSnapshotBoundary` keeps Snapshot review state mounted across engine replacement;
- `ChangeSetSnapshot` represents one manifest and its opaque `snapshot_id`.

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

`params` is the complete selected Tab value used by manifest. `engine` is a
separate file-rendering choice and does not participate in manifest or Room
identity. `ChangeSet` stores per-file expansion across replacement of its mounted
snapshot.

`ChangeSetSnapshot`:

- observes lazy information and ordered file queries;
- runs sequential file loading;
- constructs the FileTree data;
- renders one stable `FileCard` per manifest entry;
- creates the snapshot’s `LinePins` interface;
- supplies header status and statistics.

`ReviewSnapshotBoundary` wraps the engine-keyed File lane in `ReviewProvider`
and passes the exact selected Profile through as browser review authorship.
History's explicit `View` action finds the exact manifest File pair and invokes
ordinary File navigation; it does not select a hunk or address a line. The File
lane publishes which manifest indexes satisfy the same non-Husk caller contract
as FileTree, so History keeps View disabled until ordinary navigation is valid.

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
```

`ReviewDraftRoot` owns the application-lifetime strict persisted draft document
and the set of drafts whose single HTTP action is in flight. Draft IDs are local
only; new-Thread drafts allocate no server entity or operation identity.
`ReviewProvider` observes explicitly loaded bounded Thread pages for one exact
Snapshot, receives the selected `StoredProfile` and ChangeSet-owned History
visibility, owns the one active composer, and renders History. With no Profile,
reads remain available and an attempted write
presents verbal direction to the existing Profile control without opening,
focusing, clicking, or dispatching to it. History loads one later page only
through its explicit control and shows loaded and total counts; it never drains
pages eagerly. A confirmed write updates the canonical Snapshot pages when they
still exist. After provider disposal
it creates no substitute.
A draft document is published to Solid only
after its synchronous localStorage write succeeds. Write failure leaves the
previous document authoritative, presents a local failure, and disables draft
editing, removal, and submission without damaging Files or persisted Threads.
Stored input is validated when it enters the application; later typed draft
operations are serialized directly rather than reparsing the complete document.
Pending or failed bulk Thread reads likewise disable code and File review
activation, including a refetch with retained data; renderer bindings never
substitute an empty Thread set.

`ReviewBinding` is the narrow renderer interface. FileCard reads File marker
state and activates File-level composers. DiffGrid reads line marker state and
activates text composers with exact File, public region, side, and line
coordinates. A trigger is a toggle: activating its visible composer or Thread
panel closes that UI, while activating a persisted draft at the same exact
target reopens it instead of creating another draft. Closing discards a draft
only when its body is exactly empty; any entered body remains persisted.
Marker-only changes update mounted trigger classes in place;
DiffGrid's complete row renderer does not observe them. Neither renderer
performs review HTTP operations. One memo derives exact File and rendered-line
indexes from the canonical query and marker-relevant draft identity and location,
so mounted triggers use keyed reads without becoming another review store. Draft
body and timestamp changes do not publish a marker revision or wake mounted
DiffGrids. A File-marker invariant
failure substitutes only its trigger; a line-marker failure disables only that
grid's decorations and presents a sibling review error without replacing valid
rows. The failed state remains for that DiffGrid lifetime and disables triggers
created by every later fold or renderer replacement. DiffGrid reports which ordinary line-one sides
its current mounted DOM actually supplies after every render and fold change so
`file-start` binds to exactly one trigger. FileCard puts its one File trigger
inside every Husk, Full, and Lazy sticky header. DiffGrid explicitly closes
review UI anchored inside its row root immediately before replacing or
disposing that root. Expanded fold edges keep their Comment trigger visible and
exclude trigger activation from the row's fold action. Each File header also
closes its own anchored UI before state replacement; the stable Full header sits
outside the fallible body-renderer boundary.

Inline History is a fixed right column that starts open. Split History starts as
a closed control or expands into an overlay in one stable right-side host below
the sticky File header. The panel and `m` hotkey update the same ChangeSet-local
visibility state in both modes. Its compact refresh control explicitly refetches
only the bounded pages already loaded; it does not drain later pages or use the
agent activity boundary. Every expanded original excerpt identifies its
selected-side File path and line range above the retained source.
The host measures the current content-sized sticky header; its internal scroll
and displayed content remain independent of File-lane navigation. Without a
File header it uses the application-header offset instead of disappearing.
Thread rows fold locally; each located Comment row and the Thread header expose
explicit `View`. Those
controls use one immutable exact-pair manifest index and the same File-only
action, and remain disabled while the File is a Husk.

The code-aligned composer and inline Thread panel share one viewport placement
operation. It chooses above or below from measured room, clamps the floater to
the viewport, and limits its own scroll without navigating or scrolling the
File lane. A failed submission leaves its ordinary editable draft; lifecycle
and deletion controls retain no replay command.

One focused unexpected-error boundary contains the review composer, inline
Thread panel, and History presentation. Marker-local boundaries separately
contain the review triggers and imperative decorations embedded in File DOM.
The File lane remains outside those damage regions, so review derivation damage
cannot replace rendered Files.

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
| `main.tsx` | `Review.tsx` | application-lifetime persisted draft boundary |
| `App.tsx` | `AppHeader.tsx` | workspace values and explicit selection callbacks |
| `App.tsx` | `Tabs.tsx` | shared workspace values and workflow callbacks |
| `Tabs.tsx` | `ChangeSet.tsx` | complete selected `DiffParams`, separate engine, and shared display state |
| `ChangeSet.tsx` | `api.ts` | manifest, lazy-info, file, and preferences definitions |
| `ChangeSet.tsx` | `FileCard.tsx` | one manifest-position file state and explicit file actions |
| `ChangeSet.tsx` | `navigation.tsx` | mounted ChangeSet root and navigation operations |
| `ChangeSet.tsx` | `linePins.ts` | one line-pin interface per snapshot |
| `ChangeSet.tsx` | `Review.tsx` | one exact Snapshot review boundary and explicit File jump |
| `Review.tsx` | `api.ts` | bulk Snapshot review query and Profile-authored Thread and Comment mutations |
| `FileCard.tsx` | `Review.tsx` | File marker state and File composer activation |
| `DiffGrid.tsx` | `Review.tsx` | line marker state and text composer activation |
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
