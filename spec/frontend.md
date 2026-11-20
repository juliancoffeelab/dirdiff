# Frontend invariants

Rules and contracts the frontend code does not state on its own. Module
inventories live in `frontend/src`; the `local/file-card-facade` and
`select-hunk-callers` ESLint rules enforce the two lint-backed rules below.

## Layering

- `api/` is the frontend's complete backend interface; each facade operation
  returns a TanStack query or mutation definition, and HUD modules decide when
  to observe or execute it.
- `comp/` is domain-independent: it knows no repositories, diffs, tabs, files,
  hunks, or profiles.
- Backend data stays in TanStack Query results. Client state stays in the
  component whose lifetime matches it. DOM-backed navigation identity stays in
  the rendered ChangeSet DOM.
- A JavaScript module change triggers a full page reload (Solid contexts cannot
  span independently replaced module graphs, `vite.config.ts`); CSS uses Vite's
  stylesheet replacement. Packaging is in [`release.md`](release.md).

## Backend data contracts

- `DiffParams` is sent unchanged as one JSON `selection` query value for
  manifest. Its `tab` discriminator carries the Tab.
- `file_kind` is shared by manifest, lazy-info, and composed File payloads:
  `tracked` carries the VCS status; `untracked` has no status and denotes files
  outside the VCS's tracked set. Status display does not depend on the engine.
- File rendering takes a complete `EngineConfig`, and the whole value is part
  of query identity. `word_boundary.regex` travels only under mode `custom`, so
  other modes share one query identity whatever pattern is stored. Workspace
  URLs store only the engine name.
- Browser review domain failures become `ReviewRequestError`. Callers classify
  its stable `code`; presentation never interprets `message` prose.

## Preferences

- The active TanStack query is the sole reactive representation of
  preferences. localStorage holds the preference values only, with no Profile
  identity or version.
- Profile reads write the database value into the localStorage cache; anonymous
  reads use that cache.
- Fields absent from an old anonymous localStorage value take explicit defaults
  (the localStorage schema's `.default(...)`); HTTP values still require every
  field.
- `savePreferences` is the single publish path for the header and the editor.
  A save that completes after its Profile was left must not overwrite the
  current local cache.

## Engine options

- Git suppresses moved-line colors in word-diff mode, so the Moved code
  selects are disabled while Word diff is on; a disabled control keeps its
  value and a caption names the cause.
- The browser never compiles the Word-diff regex. Git does, and its rejection
  reaches the file card as the usual engine error.
- Option values are not copied into workspace state; the header reads and saves
  canonical preferences directly.

## Tabs and selection

- Each Tab retains one complete `DiffParams` and passes that same value to both
  `ChangeSet` and manifest; neither builds a second selection shape.
- Repository-backed Tabs construct or report a selection only behind the
  `repoId` gate, so no repo-backed value exists without a repository.
- Compare Refs and Branch Review create no selected value or `ChangeSet` until
  explicit Load. Once loaded they stay mounted across Tab switches.
- Pull Request base/source branch names are display facts: they are kept beside
  the `DiffParams`, required in the browser URL, and excluded from Room and
  Snapshot identity and from manifest.

## ChangeSet

- `engine` is a file-rendering choice independent of `params`; it does not
  participate in manifest or Room identity.
- A changed Git/engine configuration replaces the file lane while the manifest,
  Snapshot, and `ReviewProvider` stay mounted. Structurally equal configuration
  does not replace it, so unrelated preference changes are harmless.
- The file lane ([`file-lane.md`](file-lane.md)) owns the data lifecycle of one
  immutable snapshot and performs no presentation: no DOM, toasts, URL or
  line-pin identity, file expansion, or navigation.
- `ChangeSetShell` observes no queries, stores no backend data, and never
  selects hunks, navigates, or owns file expansion.
- `FileTree` may change only tree visibility or file expansion, scrolls only
  its own groups container, and never selects or loads files; name activation
  goes through Navigation.
- History's go-to is enabled only for indexes whose FullFile is mounted; it
  never selects a hunk or loads a File.

## Files and bays

- `FileCard.tsx` is the `hud/fileCard/` facade: outside code imports only it.
  `FrameView.tsx` and the `grids/` widgets are facade-private.
- `BayBody` is the single place a bay `kind` is examined.
- A hunk is a navigation stop, and what counts as one is decided in
  `composedHunks`, not on the wire. A hunk coordinate is the owning bay's key
  plus the bay-local index; there is no file-wide numbering. A bay with no
  row-carried stop and a `change` other than `unchanged` takes one bay-carried
  stop at index zero, except a text bay with an empty row list.
- A flatfile's bay key is the literal `flatfile`, so its line pins and review
  targets keep that identity. Only the one-frame, one-bay `flatfile` text shape
  renders without bay chrome; a blob File composes the same shape around a
  differently keyed bay and keeps its frame.
- An image bay renders no changed-line counts: zeroes beside a replaced picture
  would claim an engine looked and found nothing. A warning belongs to the
  smallest bay whose representation degraded, not to the File.
- Image bytes never arrive in the payload; pictures load from `/api/file-media`.
- `BayExpansion` and `BayRenderModes` are card-owned because the card outlives
  body collapse and rich/virtual remounts; line-pin navigation opens a bay by
  writing the state, not by reaching a mounted bay through the DOM.
- An image bay hosts one review line numbered 1 per side and writes the same
  line-host DOM contract as `TextDiffGrid`, because `FileCard` line pins and
  `navigation.tsx` line preparation resolve against it.
- `rowDom.ts` is a detached row-DOM builder with no reactivity, queries, or
  state. Its only listeners are fold toggles; it must not install review or
  line-pin listeners, retain review markers or line pins, decide view modes, or
  fetch.

## Review

- `ReviewDraftRoot` (mounted once by `main.tsx`) owns the application-lifetime
  persisted draft document; consumers touch drafts only through
  `useReviewDrafts`.
- `ReviewBinding` is the narrow renderer interface: renderers neither perform
  review HTTP operations nor store Thread data.
- `threadViews.tsx` is pure presentation: it receives data and operations
  through props and originates no query, mutation, draft write, or navigation.
- Persistence, markers, History, and HTTP behavior are in
  [`reviews.md`](reviews.md).

## Navigation

- `selectHunk` has exactly five direct callers (initialization, Next, Previous,
  scroll-follow, file navigation), enforced by lint. Do not add another.
  Initial hunk selection belongs to the mounted snapshot; line-pin navigation
  does not select hunks.
- `Navigation.activeOperations` is a nonreactive count of unfinished commands;
  `navigation-idle` is dispatched on `root` when it returns to zero, and text
  bays read the count before virtualizing and refresh their zone observers on
  that event.
- Hunk identities and FileCard operations are read from the mounted ChangeSet
  DOM; `storedHunkTarget` is the single resolver for a stored selection, used
  by navigation and the hunk display observer.
