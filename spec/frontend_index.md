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
│   ├── fileCard/
│   │   ├── FileCard.tsx
│   │   ├── FrameView.tsx
│   │   └── grids/
│   │       ├── image/
│   │       │   └── ImageBayView.tsx
│   │       └── text/
│   │           ├── TextDiffGrid.tsx
│   │           ├── folds.ts
│   │           └── rowDom.ts
│   ├── Profile.tsx
│   ├── review/
│   │   ├── discussion.ts
│   │   ├── drafts.tsx
│   │   ├── History.tsx
│   │   ├── Review.tsx
│   │   └── threadViews.tsx
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
- imports the application-lifetime `ReviewDraftRoot` from `hud/review/drafts.tsx`;
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
- composed file diffs: frames, the bay envelope they hold, and the `text` and
  `image` arms of its `kind_data`;
- decorated row parts, folds, and bay warnings from engines or format parsing;
- image references, and `fileMediaUrl` addressing one captured side's bytes;
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

Head, Compare Refs, and Branch Review render their repository-backed content
only behind a `repoId` gate and show `RepoGate` without one. Refs and Branch
Review gate into separate private components; Head keeps both branches inline.
The gated branch is the only place those Tabs construct or report a selection,
so no repo-backed value exists without a repository, including on Tab
reactivation.

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

### `hud/fileCard/FileCard.tsx`

Exports:

```ts
FileCard
HunkPosition
```

`FileCard.tsx` is the facade of the `hud/fileCard/` directory: code outside the
directory imports only this module. `FrameView.tsx` and the bay widgets under
`grids/` are facade-private, enforced by the `local/file-card-facade` ESLint
rule. The facade constrains who reaches in, not what the inside reaches out to,
so the grids' imports of `review/Review` marker bindings and `linePins` types
remain ordinary.

`FileCard` receives one of three file states:

- Husk;
- Full;
- Lazy.

It also receives:

- exact nullable left/right review File pair;
- manifest file index;
- expansion state;
- render admission;
- view mode;
- fold policy;
- line-pin interface;
- hunk-display data;
- explicit load, retry, and expansion callbacks.

Private state components:

- `FileCardContent`;
- `HuskFile`;
- `FullFile`;
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

`FileBody` mounts the generic frame renderer `FrameView`, which walks the
composed diff's frames and dispatches each bay to its widget by `kind`. A
flatfile is one heading-less frame holding one `flatfile` text bay, so
the text widget delegates straight to `TextDiffGrid` and the rendered DOM is
unchanged. There is no `render_kind` branch.

A FullFile exposes its line-preparation operation through its FileCard DOM
interface for `navigation.tsx`, and aggregates its mounted bays' render modes
into the card's `data-file-render`. Rich/virtual representation and the
enrichment operations belong to the individual bays inside `FrameView`.

The TextDiffGrid lives in `hud/fileCard/grids/text/`, the home of the row-shaped
bay widgets: `TextDiffGrid.tsx` is the reactive component and `rowDom.ts` is
the pure imperative row-DOM kernel.

### `hud/fileCard/grids/text/TextDiffGrid.tsx`

Exports:

```ts
TextDiffGrid
```

Private components:

- `SplitHeader`;
- `InlineHeader`;
- `ImperativeDiffLines`.

Inputs:

- exact nullable left/right review File pair;
- manifest file index;
- file display name;
- the composed bay key this grid renders;
- the bay label naming the grid's content column;
- old and new labels;
- validated diff rows;
- fold hints;
- view mode;
- fold policies;
- `LinePins`;
- nullable ordinary-line-one side reporting, present for ordinary File grids.

`TextDiffGrid` renders decorated text parts, line numbers, fold rows, hunk targets,
and line-pin coordinates.

It uses `folds.ts` to construct visible rows and uses `LinePins` to read or change line-pin URL state.

### `hud/fileCard/grids/text/rowDom.ts`

Exports:

```ts
renderSplitRowsDom
renderInlineRowsDom
forceChunkLayout
finishForcedChunkLayout
Side
```

The kernel builds one complete detached row fragment per call from validated
backend rows and fold state; every function is a pure DOM constructor with no
Solid reactivity, component state, or queries. It owns row chunking: large
renders stream rows through fixed-size content-visibility containers, and one
idle-paced module-level warm-up pass renders each new chunk once so the
browser records its real height, and `forceChunkLayout` /
`finishForcedChunkLayout` let off-screen geometry reads (navigation
enrichment, rich-to-virtual height capture) lay unwarmed chunks out
immediately instead of measuring the intrinsic estimate. It must not listen
to events, own review markers or line pins, decide view modes, or fetch
anything.

### `hud/fileCard/FrameView.tsx`

Exports:

```ts
FrameView
composedHunks
composedHunkCount
```

Exported types: `BayHunks`, one bay's hunk stops and which carrier —
rows or the bay itself — supplies them; `BayRenderMode`, one bay's current
representation; `BayRenderModes`, the card-owned registry of mounted bays'
modes that `FullFile` aggregates into `data-file-render`.

Private components:

- `BayView`;
- `BayBody`;
- `BayStats`;
- `BayWarning`;
- `TextBayView`;
- `VirtualBay`.

Private helpers: `changeTone` and `frameTone` map a bay's backend-authored
`change` onto a palette class. Neither decides what happened; the composer
already did.

Exported type: `BayExpansion`, the card-owned expansion state bays read
and write. The card outlives the body's collapse and rich/virtual unmounts, so
bays keep the reviewer's expansion across remounts, and line-pin navigation
opens a collapsed bay by writing the state instead of reaching a mounted
bay through the DOM.

Inputs:

- exact nullable left/right review File pair;
- manifest file index;
- validated composed diff;
- view mode;
- fold policy;
- `LinePins`;
- the card element accessor;
- `BayExpansion`;
- `BayRenderModes`.

`FrameView` walks the composed diff's frames in backend order, renders a frame's
optional backend-authored heading, and dispatches each bay to the widget for
its `kind_data`. `BayBody` is the single dispatch point and the only place a
kind is examined: `text` delegates to `TextDiffGrid`, `image` to
`ImageBayView`. Each widget receives the whole `BayPayload`, for identity and
label, and its own already-narrowed arm of `kind_data`, for content. Every grid
takes a `bayKey` string; a flatfile's is the literal
`flatfile`, so line pins and review targets keep their existing flatfile
identity through the same coordinate every other bay uses. `FrameView` nests
`bareTextBay`, which recognises the one-frame one-bay flatfile shape that
must render as a bare grid with no bay chrome. A blob File composes the same
one-frame one-bay shape around a differently keyed bay and is deliberately not
bare: it keeps its frame, because the frame is what states what happened to
those bytes.

`BayStats` renders a bay's own changed-line counts, which only a bay holding
lines has; an image bay renders none, because printing three zeroes beside a
replaced picture would claim an engine looked and found nothing. `BayWarnings`
renders every warning on the bay envelope, whether an engine or a format
builder reported it.

`composedHunks` collects each bay's hunk stops. A hunk is a stop for Next
and Previous, so what counts as one is a navigation decision and it is made
here, not on the wire. It walks frames and bays in document order: a text
bay's stops are the wire's own bay-local `hunk_index` values verbatim,
and a bay whose `change` is not `unchanged`, contributing no such row,
takes one stop of its own at index zero. There is no file-wide numbering: a
hunk coordinate is the owning bay's key plus the bay-local index, and
`TextDiffGrid` takes the bay key so `rowDom` writes both halves untranslated.
`composedHunkCount` is the File total, replacing the `hunk_count` the payload
used to carry.

`TextBayView` owns one text bay's rich/virtual representation: it registers
the bay's mode in the card's `BayRenderModes`, chooses its initial mode from
the card's geometry against its entry zone, flips on its own
IntersectionObservers afterwards, pins the measured rich height across a
rich-to-virtual replacement, and attaches the bay's enrichment operations to
its `data-bay-render` wrapper. `VirtualBay` renders what a distant bay shows
instead of rows: the bay's two plain texts and transparent anchors for its
row-carried hunk stops. It never sees a decorated `DiffRow` part; a bay-root
stop stays with the bay chrome, which is mounted in both representations.

`BayWarnings` renders a bay's warnings in both layouts: inside the header block
for a bay with chrome, and directly above the grid for a bare flatfile. A
warning belongs to the smallest bay whose engine or format representation
degraded, not to the File.

### `hud/fileCard/grids/image/ImageBayView.tsx`

Exports:

```ts
ImageBayView
```

Private components:

- `ImageSideView`;
- `CommentTrigger`.

Inputs:

- exact nullable left/right review File pair;
- the whole bay envelope;
- its narrowed `image` content;
- view mode;
- `LinePins`.

`ImageBayView` renders one bay whose content is a captured picture: for each
side, the side's name and the picture itself. A side the File was not captured
on says so in words rather than showing an empty pane. Bytes never arrive in
the payload — a side's picture is an `<img>` whose source is the
`/api/file-media` address for that Snapshot, File pair, and side — and a
picture the browser refuses to decode raises a Toast rather than failing
silently. The facts about those bytes are not this widget's business: an image
File composes a second, collapsible `image-facts` text bay, and the ordinary
text grid diffs the type, size, and digest there.

The widget mounts no rich/virtual representation. It has no rows to virtualize,
its two sides are one row, and it is always mounted, which is what lets the bay
chrome carry its single hunk stop.

It hosts exactly one review line, numbered 1, on each captured side, matching
the single pseudo-line the backend exposes for a non-text bay. That line is the
host `FileCard` resolves for line pins and `navigation.tsx` line preparation, so
the widget writes the same line-host DOM contract `TextDiffGrid` does: a
`data-bay-key` wrapper carrying the enrichment operation, a `data-review-bay`
grid, a line container carrying the preparation operation, and per side a
`.line-no` carrying the pin coordinate beside its `.line-code`.

The review subsystem lives in the `hud/review/` directory: `Review.tsx` is
the Snapshot review boundary, `drafts.tsx` is the application-lifetime draft
document, and `threadViews.tsx` is the shared discussion presentation.

### `hud/review/Review.tsx`

Exports:

```ts
ReviewProvider
useReview
ReviewBinding
ReviewMarkerState
ReviewTextGridBinding
ReviewCodeAnchor
ActiveCommentInput
ActiveThreadPanel
```

`ReviewProvider` observes the complete canonical Thread set for one exact
Snapshot, receives the selected `StoredProfile` and ChangeSet-owned History
visibility, owns the anchored Comment input and inline-Thread presentation
with their discussion instance, computes split-History placement geometry,
and mounts `ReviewHistory` with its host facts and the anchored-UI behaviors
only the provider can perform. HTTP pagination is a private transport detail
of the canonical Snapshot query; every page uses the append-only activity
boundary returned by its first page.

`ReviewBinding` is the narrow renderer interface. FileCard reports mounted File
headers used by History placement and File-start targets. TextDiffGrid reads compact
line-marker descriptors and activates exact File, public-bay, side, and line
actions. Neither renderer performs review HTTP operations or stores Thread data.

`ReviewMarkerState` contains only the controls actually represented on a line.
`ReviewTextGridBinding` identifies one immutable text grid.
`ReviewCodeAnchor` identifies one connected code cell and its selected marker.

### `hud/review/drafts.tsx`

Exports:

```ts
ReviewDraftRoot
useReviewDrafts
newReviewId
ReviewDraft
NewThreadDraft
ReviewDraftContextValue
```

`ReviewDraftRoot` owns the application-lifetime strict persisted draft document
and the set of drafts whose single HTTP action is in flight. It is mounted once
by `main.tsx` and owns the localStorage representation; consumers read and
write drafts only through `useReviewDrafts`. The module knows no Snapshots,
queries, mutations, markers, or review presentation.

### `hud/review/discussion.ts`

Exports:

```ts
createThreadDiscussion
ThreadDiscussion
ThreadDiscussionArgs
```

`createThreadDiscussion` builds one Snapshot-scoped discussion instance over
the three shared authorities: the persisted draft document, the canonical
Snapshot query cache, and the TanStack mutation cache. It owns its own
mutation and query observers (the cache deduplicates the query against every
other observer of the same Snapshot), the cancel-then-publish protocol that
keeps an in-flight refetch from reverting a committed write, and pending
probes read from the shared mutation cache, so concurrent instances see one
another's in-flight work. The single outward call is the required
`onSubmitted` construction behavior, batched with submission settlement.
The module owns no presentation, anchored-UI state, or Thread data.

### `hud/review/History.tsx`

Exports:

```ts
ReviewHistory
```

History is an independent consumer of the shared authorities: it observes the
canonical Snapshot review query itself (deduplicated by the cache), reads the
application draft document, and creates its own Thread discussion instance,
so no action callbacks cross its boundary. Its props are host facts —
Snapshot identity, selected Profile, view and visibility, mount targets,
split placement — plus the anchored-UI behaviors only the review provider
can perform: viewing a Thread or continuing a draft at its rendered line,
closing a Comment input mounted in a History card, discarding a new-Thread
draft, and clearing the draft document. It owns per-Thread expansion, the
keep-mounted reading position, and idle warming.

### `hud/review/threadViews.tsx`

Exports:

```ts
CommentInput
InlineThreadPanel
ThreadCard
```

Pure discussion presentation: every component receives all data and operations
through props, consumes no context, and originates no query, mutation, draft
write, or navigation. `ThreadCard` is the shared discussion card rendered by
both the anchored panel and History.

The complete review persistence, marker, History, Comment-input, navigation,
error, and browser/agent HTTP behavior is specified once in
[`reviews.md`](reviews.md).

### `hud/fileCard/grids/text/folds.ts`

Exports:

```ts
FoldRow
RenderRow
parseFoldHints()
addFoldRows()
isFoldRow()
```

`TextDiffGrid` supplies backend rows, fold hints, and fold expansion state. The module returns the visible sequence of ordinary rows and fold rows.

## 11. Navigation

### `hud/navigation.tsx`

Exports:

```ts
NavigationProvider
useNavigation
writeInitialHunkSelection
storedHunkTarget

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
root: Accessor<HTMLElement>;
```

`root` returns the mounted ChangeSet root the instance serves, so consumers
that must locate navigated DOM afterwards query inside the same root the
navigation itself used, in every view.

The module reads hunk identities and FileCard operations from the mounted ChangeSet DOM. `storedHunkTarget` resolves one FileCard's stored selected identity to its current hunk target by the declared kind; navigation and the hunk display observer in `changeSet/Shell.tsx` both resolve through it.

Its direct consumers are:

- ChangeSet hotkeys and HintHud;
- FileTree navigation;
- line-pin restoration;
- review Thread navigation in `changeSet/ChangeSet.tsx`.

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
bay
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

`TextDiffGrid` uses `parseUrl()` and `toggleUrlState()`.

`ChangeSetSnapshot` uses `restore()` after parsing the initial URL target.

`LinePins.restore()` sends the final line-navigation operation through `Navigation`.

## 12. Direct module interfaces

| Caller | Callee | Interface |
|---|---|---|
| `main.tsx` | `App.tsx` | `App` |
| `main.tsx` | `review/drafts.tsx` | application-lifetime persisted draft boundary |
| `App.tsx` | `AppHeader.tsx` | workspace values and explicit selection callbacks |
| `App.tsx` | `Tabs.tsx` | shared workspace values and workflow callbacks |
| `Tabs.tsx` | `changeSet/ChangeSet.tsx` | complete selected `DiffParams`, separate engine, and shared display state |
| `changeSet/ChangeSet.tsx` | `changeSet/Shell.tsx` | mounted frame, UI-operation callbacks, and the HunkDisplay accessor |
| `changeSet/ChangeSet.tsx` | `changeSet/FileTree.tsx` | manifest tree, canonical states, and shared expansion policy |
| `changeSet/ChangeSet.tsx` | `api.ts` | manifest and preferences definitions |
| `changeSet/ChangeSet.tsx` | `changeSet/fileLane.ts` | one file lane per snapshot and its canonical file states |
| `changeSet/fileLane.ts` | `api.ts` | lazy-info and file query definitions |
| `changeSet/ChangeSet.tsx` | `fileCard/FileCard.tsx` | one manifest-position file state and explicit file actions |
| `changeSet/*` | `navigation.tsx` | mounted ChangeSet root and navigation operations |
| `changeSet/ChangeSet.tsx` | `linePins.ts` | one line-pin interface per snapshot |
| `changeSet/ChangeSet.tsx` | `review/Review.tsx` | one exact Snapshot review boundary and explicit File jump |
| `review/Review.tsx` | `review/History.tsx` | host facts, split placement, and the provider's anchored-UI behaviors |
| `review/Review.tsx` | `review/discussion.ts` | one Snapshot-scoped discussion instance and its submission reaction |
| `review/History.tsx` | `review/discussion.ts` | History's own Snapshot-scoped discussion instance |
| `review/discussion.ts` | `api.ts` | canonical Snapshot review query and Profile-authored Thread and Comment mutations |
| `fileCard/FileCard.tsx` | `review/Review.tsx` | File marker state and File Comment-input activation |
| `fileCard/grids/text/TextDiffGrid.tsx` | `review/Review.tsx` | line marker state and text Comment-input activation |
| `fileCard/FileCard.tsx` | `fileCard/FrameView.tsx` | complete composed-diff rendering inputs |
| `fileCard/FrameView.tsx` | `fileCard/grids/text/TextDiffGrid.tsx` | one text bay's rows, hints, and bay key |
| `fileCard/FrameView.tsx` | `fileCard/grids/image/ImageBayView.tsx` | one `image` bay and its two picture references |
| `fileCard/grids/image/ImageBayView.tsx` | `review/Review.tsx` | line marker state and image Comment-input activation |
| `fileCard/grids/image/ImageBayView.tsx` | `linePins.ts` | URL parsing and direct pin toggling |
| `fileCard/grids/text/TextDiffGrid.tsx` | `fileCard/grids/text/rowDom.ts` | validated rows, fold state, and the two render callbacks |
| `fileCard/grids/text/*` | `fileCard/grids/text/folds.ts` | rows, fold hints, and expansion |
| `fileCard/grids/text/TextDiffGrid.tsx` | `linePins.ts` | URL parsing and direct pin toggling |
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
