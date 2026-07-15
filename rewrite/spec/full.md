# Frontend specification: server state and TanStack Query

Status: proposed for approval.

## 1. Terminology

- `Tab` is a UI mode selected by the user.
- `DiffParams` is the complete set of parameters required to request a diff.
- `ChangeSet` is the complete interactive result displayed by a Tab.
- `Manifest` is the ordered description of a ChangeSet returned by the backend.
- `FileCard` is the stable DOM-resident representation of one manifest entry.
- `HuskFile` is the small queued or fetching representation of a file.
- `FullFile` is a successfully loaded file with its complete header and `FileBody`.
- `LazyFile` is an intentionally delayed or failed file with an explicit-fetch plank.
- `FileBody` is the expensive rendered content of a `FullFile`.
- `FileSequence` is the single file-request lane: normal files retain manifest order, while explicitly selected lazy files may run after the active request.
- `FileDiff` is the backend result for one rendered file.

“Comparison” is not an architectural term.

`Diff*` remains correct for diff parameters, engines, rows, view modes, and actual per-file diffs.

`commit` and `committed` are reserved for Git commits. Client-side choices use `select`, `selected` and `selection`; they must not use commit terminology.

## 2. Core ownership rule

TanStack Query owns all state received from the Python backend:

- manifest data;
- lazy-file metadata;
- individual file diffs;
- repository lists;
- refs and repository defaults;
- presets;
- profiles and preferences;
- query loading, success and error states.

Solid owns client-only state:

- workspace state: current Tab, selected repo, engine and inline/split view;
- component-owned live input and selection interaction;
- each Tab’s local selection and derived `DiffParams`;
- file and directory expansion;
- DOM references;
- hunk navigation;
- virtualization decisions;
- transient self-contained HUD state such as an open profile dialog.

Backend data must not be copied from TanStack Query into another Solid store.

```text
App
├── workspace                      Solid state
└── Tab
    ├── Controls and Inputs        Solid component state
    ├── selected values            Solid state
    └── ChangeSet
        ├── Manifest               TanStack Query
        ├── lazy metadata          TanStack Query
        ├── file queries           TanStack Query
        ├── FileTree               derived from Manifest
        ├── FileCards              derived from Manifest
        ├── expansion              Solid state
        ├── FileSequence           ChangeSet-local orchestration
        └── HunkNavigation         DOM/local state
```

TanStack Query is specifically intended to own, cache, synchronize, and expose asynchronous server state. Solid signals and stores remain appropriate for client-owned state. [TanStack Solid Query overview](https://tanstack.com/query/v5/docs/framework/solid/overview)

## 3. Selected query architecture

Three possible designs were considered.

### Option A: each FileCard starts its own query

Every FileCard uses an enabled query once the previous file finishes.

Rejected because:

- sequencing becomes distributed across components;
- error and delayed-file handling complicates the dependency chain;
- reordering or mounting shells can accidentally affect request order;
- the loading policy becomes difficult to inspect.

### Option B: one query fetches the entire ChangeSet

One query fetches the manifest and then every file.

Rejected because:

- individual files have no independent cache entry;
- one file error affects the whole query;
- individual FileCards cannot observe isolated state;
- a large result update places more pressure on the complete ChangeSet;
- retry and cancellation are too coarse.

### Option C: canonical queries plus one FileSequence

Selected.

- Manifest, lazy metadata and every file have independent query keys.
- `api.ts` defines every query.
- `ChangeSet` owns one small sequential loop using `queryClient.fetchQuery`.
- Each FileCard subscribes only to its own query entry.
- The loop contains no backend data and creates no second cache.

`fetchQuery` is appropriate here because it fetches and caches one canonical query while returning a Promise that can be awaited sequentially. [TanStack QueryClient reference](https://tanstack.com/query/v5/docs/reference/QueryClient)

## 4. API files and boundaries

This section records the API-specific file boundaries. Section 65 defines the complete selected frontend structure and component ownership; read that section for the full file plan.

The API portion of that structure is:

```text
frontend/src/
├── api/
│   ├── api.ts
│   └── queryClient.ts
├── hud/
│   ├── ...
│   ├── ChangeSet.tsx
│   └── ...
└── ...
```

In that structure:

- `api/api.ts` owns schemas, API types, HTTP handlers, query definitions and mutation definitions;
- `api/queryClient.ts` owns QueryClient construction and exports `QueryProvider`;
- `hud/ChangeSet.tsx` owns the manifest observer, FileSequence, derived file state and ChangeSet rendering.

The API facade follows these rules:

- `api/api.ts` exports the single `api = { ... }` facade and API types.
- Private HTTP handlers, Zod schemas, query keys, and query/mutation definitions live behind it.
- `api/queryClient.ts` provides the configured client through `QueryProvider`; consumers obtain it with TanStack Query’s `useQueryClient()` rather than importing a global singleton.

Other frontend code should normally interact with the backend through `api`, not import its internal HTTP handlers directly. If `api.ts` eventually becomes genuinely enormous, we can reconsider—but shouldn’t pre-split it now.

There will be no:

- `queries.ts`;
- `queryKeys.ts`;
- `createDiffResources.ts`;
- `createChangeSetResources.ts`;
- separate file-loading resource;
- application-specific `create*` abstraction.

Framework functions such as Solid’s `createMemo` and `createEffect` retain their framework names.

## 5. API types

`DiffParams` remains exactly `DiffParams`.

It is a complete immutable backend-request value. A Tab derives it from its local selection and the workspace-owned engine. Live control input is not `DiffParams`; every required field must exist before the value is constructed.

```ts
export type DiffParams =
  | HeadDiffParams
  | RefsDiffParams
  | BranchReviewDiffParams
  | PresetDiffParams;
```

Manifest entries remain complete file handles:

```ts
export type ManifestEntry = {
  file_kind: FileKind;
  left_path: string | null;
  right_path: string | null;
  lazy: LazyReason | null;
};

export type ManifestFile = {
  type: "file";
  name: string;
  entry: ManifestEntry;
};

export type ManifestDirectory = {
  type: "directory";
  name: string;
  path: string;
  entries: ManifestNode[];
};

export type ManifestNode = ManifestFile | ManifestDirectory;

export type Manifest = {
  cache_id: string;
  display_name: string;
  left_label: string;
  right_label: string;
  summary: Summary;
  tree: ManifestNode[];
};
```

The current optional-field-heavy `FileEntry` should not survive the rewrite. The backend response should be a proper discriminated union:

```ts
export type FileDiff = TextFileDiff | NotebookFileDiff;

export type TextFileDiff = {
  render_kind: "text";
  // Required text-file response fields.
};

export type NotebookFileDiff = {
  render_kind: "notebook";
  // Required notebook response fields.
};
```

Every field required by its renderer must be required by that variant. The Python response must add `render_kind: "text"` for text files so the frontend does not infer the variant from the presence or absence of optional fields.

Zod schemas in `api.ts` remain the runtime authority:

```ts
export type FileDiff = z.infer<typeof FileDiffResponseSchema>;
```

## 6. HTTP naming

Only functions that actually perform HTTP requests may use `request` terminology.

These handlers are private to `api.ts`:

```ts
function requestManifest(
  params: DiffParams,
  signal: AbortSignal,
): Promise<Manifest>;

function requestLazyInfo(
  params: DiffParams,
  cacheId: string,
  signal: AbortSignal,
): Promise<LazyInfo>;

function requestFileDiff(
  params: DiffParams,
  cacheId: string,
  entry: ManifestEntry,
  signal: AbortSignal,
): Promise<FileDiff>;
```

There will be no `DiffRequest`, `ManifestRequest`, or `FileRequest` name for ordinary parameter or state objects.

Every HTTP response is validated inside its request handler before being returned.

## 7. Query definitions in `api.ts`

Query keys and query functions must be colocated through `queryOptions`. This preserves type inference and allows the same definition to be used by components and `queryClient.fetchQuery`. [TanStack query-options guide](https://tanstack.com/query/v5/docs/framework/solid/guides/query-options)

```ts
import {
  mutationOptions,
  queryOptions,
} from "@tanstack/solid-query";

const snapshotQuery = {
  staleTime: Infinity,
  retry: false,
} as const;

export const api = {
  changeSet: {
    key: ["change-set"] as const,

    manifest(params: DiffParams) {
      return queryOptions({
        queryKey: ["change-set", "manifest", params] as const,
        queryFn: ({ signal }) => requestManifest(params, signal),
        ...snapshotQuery,
      });
    },

    lazyInfo(params: DiffParams, cacheId: string) {
      return queryOptions({
        queryKey: [
          "change-set",
          "lazy-info",
          params,
          cacheId,
        ] as const,
        queryFn: ({ signal }) =>
          requestLazyInfo(params, cacheId, signal),
        ...snapshotQuery,
      });
    },

    file(
      params: DiffParams,
      cacheId: string,
      entry: ManifestEntry,
    ) {
      const locator = {
        left_path: entry.left_path,
        right_path: entry.right_path,
      };

      return queryOptions({
        queryKey: [
          "change-set",
          "file",
          params,
          cacheId,
          locator,
        ] as const,
        queryFn: ({ signal }) =>
          requestFileDiff(params, cacheId, entry, signal),
        ...snapshotQuery,
      });
    },
  },

  repos: {
    list() {
      return queryOptions({
        queryKey: ["repos"] as const,
        queryFn: ({ signal }) => requestRepos(signal),
      });
    },

    refs(projectId: ProjectId) {
      return queryOptions({
        queryKey: ["repos", projectId, "refs"] as const,
        queryFn: ({ signal }) =>
          requestRepoRefs(projectId, signal),
      });
    },

    defaults(projectId: ProjectId) {
      return queryOptions({
        queryKey: ["repos", projectId, "defaults"] as const,
        queryFn: ({ signal }) =>
          requestRepoDefaults(projectId, signal),
      });
    },

    remove() {
      return mutationOptions({
        mutationKey: ["repos", "remove"] as const,
        mutationFn: requestRemoveRepo,
      });
    },

    saveMainBranch() {
      return mutationOptions({
        mutationKey: ["repos", "save-main-branch"] as const,
        mutationFn: requestSaveMainBranch,
      });
    },
  },

  presets: {
    catalogs() {
      return queryOptions({
        queryKey: ["presets"] as const,
        queryFn: ({ signal }) => requestPresets(signal),
        staleTime: Infinity,
      });
    },
  },

  profile: {
    preferences(profileId: number) {
      return queryOptions({
        queryKey: ["profile", profileId, "preferences"] as const,
        queryFn: ({ signal }) =>
          requestPreferences(profileId, signal),
      });
    },

    register() {
      return mutationOptions({
        mutationKey: ["profile", "register"] as const,
        mutationFn: requestRegisterProfile,
      });
    },

    rename() {
      return mutationOptions({
        mutationKey: ["profile", "rename"] as const,
        mutationFn: requestRenameProfile,
      });
    },

    savePreferences() {
      return mutationOptions({
        mutationKey: ["profile", "save-preferences"] as const,
        mutationFn: requestSavePreferences,
      });
    },
  },

  pullRequest: {
    prepare() {
      return mutationOptions({
        mutationKey: ["pull-request", "prepare"] as const,
        mutationFn: requestPreparePullRequest,
      });
    },
  },
};
```

Query keys contain every value used by their query function. TanStack Query supports serializable objects in keys and hashes object keys deterministically, so a separate JSON identity string is unnecessary. [TanStack query-key guide](https://tanstack.com/query/v5/docs/framework/solid/guides/query-keys)

Consequently, `diffParamsIdentity` and `currentParamsIdentity` are removed.

## 8. QueryClient configuration

```ts
import { QueryClient } from "@tanstack/solid-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
    mutations: {
      retry: false,
    },
  },
});
```

Rationale:

- The backend is local.
- Automatic retries make sequential loading appear stuck on one file.
- Window focus must not silently replace a ChangeSet snapshot.
- ChangeSet snapshot queries set `staleTime: Infinity`.
- Reloading is an explicit user operation.
- Default structural sharing remains enabled because the responses are JSON-compatible and stable references reduce unnecessary reactive updates. [TanStack important defaults](https://tanstack.com/query/v5/docs/framework/solid/guides/important-defaults)

No persisted browser query cache is required.

## 9. ChangeSet lifecycle

A Tab derives complete `DiffParams` from its local selection and the workspace-owned engine:

```tsx
<ChangeSet params={params()} />
```

`ChangeSet` starts exactly one manifest query:

```ts
const manifest = useQuery(() =>
  api.changeSet.manifest(props.params),
);
```

Changing `DiffParams` changes the manifest, lazy-info and file query keys. The Tab’s keyed boundary—not `DiffParams` object identity—determines whether the outer `ChangeSet` is recreated.

There is no:

- `startDiff`;
- `loadDiff`;
- `refreshDiff`;
- `switchDiffEngine`;
- manual parameter identity;
- load ID;
- revision counter.

Selecting a different global engine derives new complete `DiffParams`. The existing API contract includes engine in the manifest request, so this selects a new manifest query and then new sequential file queries. The outer `ChangeSet` remains mounted and preserves its client-owned layout state.

## 10. Manifest behavior

Before the manifest resolves:

- no FileTree exists;
- no FileCard exists;
- the ChangeSet displays manifest loading or error state.

After the manifest resolves:

- its tree is rendered directly;
- a depth-first traversal produces the canonical ordered file list;
- every manifest entry receives a FileCard immediately;
- FileTree navigation targets those shells;
- loading begins at manifest index zero.

The manifest is never copied into a Solid store.

Derived maps, flattened arrays and directory labels may use `createMemo`. A memo is a derived view, not another authoritative store.

## 11. Strict FileSequence

File entries are visited strictly from first to last in manifest order.

There is never more than one `/api/file-diff` request in flight.

Lazy entries are visited in their correct position but do not start a file request.

The same request lane also accepts explicit `LazyFile` selections. An explicit selection:

1. waits for the currently active request;
2. becomes the next request;
3. pauses but does not reorder the remaining automatic entries;
4. uses the same canonical TanStack query;
5. allows the automatic sequence to resume afterward.

Multiple explicit selections are deduplicated by canonical file key and processed in selection order. The automatic files retain their relative manifest order.

```ts
async function loadFilesInOrder(
  params: DiffParams,
  manifest: Manifest,
  stopped: () => boolean,
  setActiveKey: (key: readonly unknown[] | null) => void,
): Promise<void> {
  const entries = manifestFilesInOrder(manifest.tree);

  for (const entry of entries) {
    if (stopped()) {
      return;
    }

    if (entry.lazy !== null) {
      continue;
    }

    const options = api.changeSet.file(
      params,
      manifest.cache_id,
      entry,
    );

    setActiveKey(options.queryKey);

    try {
      await queryClient.fetchQuery(options);
    } catch (error) {
      if (stopped() || isCancelledError(error)) {
        return;
      }

      // The query retains its own error.
      // Continue with the next manifest entry.
    }
  }

  setActiveKey(null);
}
```

The sequence must not:

- filter the manifest into a second reordered list;
- use `Promise.all`;
- use `useQueries` to start all requests;
- reprioritize based on viewport position;
- reprioritize when the user clicks the tree;
- start an explicit file request concurrently with the active request;
- copy successful file data elsewhere;
- manufacture fake `FileDiff` objects for failures.

The sequence owns only cancellation bookkeeping, its automatic cursor, the explicit selection queue and one combined progress value. Backend data and file errors remain in the query cache.

## 12. Cancellation

Every query function passes TanStack Query’s `AbortSignal` into the actual HTTP request.

TanStack Query supplies this signal specifically so query cancellation can abort the underlying fetch and revert the query to its previous state. [TanStack cancellation guide](https://tanstack.com/query/latest/docs/framework/solid/guides/query-cancellation)

When a ChangeSet is replaced or unmounted, or when its engine changes:

1. its local sequence is marked stopped;
2. the exact currently active file query is cancelled;
3. no subsequent file query starts.

```ts
createEffect(() => {
  const data = manifest.data;
  if (data === undefined) {
    return;
  }

  let stopped = false;
  let activeKey: readonly unknown[] | null = null;

  onCleanup(() => {
    stopped = true;

    if (activeKey !== null) {
      void queryClient.cancelQueries({
        queryKey: activeKey,
        exact: true,
      });
    }
  });

  void loadFilesInOrder(
    props.params,
    data,
    () => stopped,
    (key) => {
      activeKey = key;
    },
  );
});
```

No `activeLoadId` is required. Solid cleanup determines whether the sequence still belongs to the mounted ChangeSet.

## 13. Lazy metadata and LazyFile

After the manifest loads, `ChangeSet` starts `lazyInfo` only if at least one manifest entry is lazy.

```ts
const lazyInfo = useQuery(() => ({
  ...api.changeSet.lazyInfo(
    props.params,
    manifest.data!.cache_id,
  ),
  enabled:
    manifest.data !== undefined &&
    manifestContainsLazyFiles(manifest.data.tree),
}));
```

The lazy metadata query may run concurrently with the normal FileSequence because:

- it is one metadata request, not an individual file-render request;
- it cannot alter manifest order;
- it cannot start a FileBody render;
- it only describes intentionally delayed files.

A lazy-info failure leaves the FileCards present and produces error-flavoured `LazyFile` states. It does not stop normal file loading.

`LazyFile` renders a colored clickable plank. Selecting the plank submits an explicit file request to the single request lane. The `LazyFile` becomes a fetching `HuskFile`, then becomes `FullFile` on success or an error-flavoured `LazyFile` on failure.

`LazyFile` never calls a private HTTP handler directly and never starts a concurrent request.

## 14. FileCard query observation

A FileCard subscribes to its own canonical file query but does not initiate automatic fetching. The ChangeSet request lane initiates both automatic and explicitly selected requests.

```ts
const file = useQuery(() => ({
  ...api.changeSet.file(
    props.params,
    props.cacheId,
    props.entry,
  ),
  enabled: false,
}));
```

Normally, permanently disabled queries are discouraged because they opt out of automatic behavior. Here it is deliberate: the observer is read-only, while the single request lane performs the canonical fetch through `fetchQuery`. [TanStack disabled-query guide](https://tanstack.com/query/latest/docs/framework/react/guides/disabling-queries)

The query observer still receives cache updates for its exact key.

Therefore loading one file updates its own FileCard without replacing a ChangeSet-wide `filesByKey` object.

## 15. Derived file state

```ts
export type FileCardState =
  | HuskFileState
  | FullFileState
  | LazyFileState;

export type HuskFileState = {
  state: "husk";
  fileIndex: number;
  name: string;
  path: string;
  activity: "queued" | "fetching";
};

export type FullFileState = {
  state: "full";
  fileIndex: number;
  file: FileDiff;
};

export type LazyFileState = {
  state: "lazy";
  fileIndex: number;
  file: LazyFile;
};

export type LazyFile =
  | { kind: "deferred"; info: LazyInfoFile }
  | { kind: "error"; name: string; path: string; error: Error };
```

The state is derived from the manifest handle, the canonical file query and lazy metadata. It is not stored separately. An active fetch takes precedence so retrying an error or explicitly loading a delayed file immediately produces a fetching `HuskFile`.

```ts
function fileCardState(
  manifestFile: ManifestFile,
  fileIndex: number,
  fileQuery: UseQueryResult<FileDiff, Error>,
  lazyInfo: LazyInfoState,
): FileCardState {
  if (fileQuery.fetchStatus === "fetching") {
    return fileHusk(manifestFile, fileIndex, "fetching");
  }

  if (fileQuery.isSuccess) {
    return { state: "full", fileIndex, file: fileQuery.data };
  }

  if (fileQuery.isError) {
    return {
      state: "lazy",
      fileIndex,
      file: {
        kind: "error",
        name: manifestFile.name,
        path: manifestFilePath(manifestFile),
        error: fileQuery.error,
      },
    };
  }

  if (manifestFile.entry.lazy !== null) {
    return lazyFileState(manifestFile, fileIndex, lazyInfo);
  }

  return fileHusk(manifestFile, fileIndex, "queued");
}
```

TanStack distinguishes data status from fetch status: `status` describes whether data exists, while `fetchStatus` describes whether the query function is currently running. [TanStack query guide](https://tanstack.com/query/v5/docs/framework/solid/guides/queries)

## 16. Sequence progress

One combined client entity describes the request lane:

```ts
export type FileSequenceState =
  | {
      state: "loading";
      processed: number;
      automaticTotal: number;
      failed: number;
      active: ActiveFileRequest;
    }
  | {
      state: "ready";
      processed: number;
      automaticTotal: number;
      failed: number;
    };

export type ActiveFileRequest = {
  kind: "sequence" | "selected";
  fileIndex: number;
  path: string;
  slow: boolean;
};
```

`automaticTotal` counts only files included in the automatic sequence. Manifest-lazy files therefore do not leave progress permanently incomplete.

When a request begins, one timeout may mark its existing active value as `slow: true`. The timeout is cleared when the request settles. There is no elapsed-seconds interval and no status string rewritten every second.

There are no separate `loadedFiles`, `failedFiles`, `loadingRevision`, placement or mutable status-string signals.

## 17. FileBody rendering

A FileCard is a stable manifest-position wrapper whose state switch renders one of three complete presentations:

```text
FileCard
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

`HuskFile` has only manifest name/path and queued/fetching activity. It does not reserve the eventual body height.

`LazyFile` represents either backend-described delayed content or an error. Its colored plank is the explicit fetch action.

`FullFile` has the complete per-file header and mounts `FileBody` with the exact immutable query result:

```tsx
<Show when={state().state === "full"}>
  <FullFile state={state()} />
</Show>
```

FileBody must not receive:

- AppHeader status or progress;
- local or global hunk counters;
- unrelated Tab state;
- complete manifest data;
- complete FileTree state;
- other files’ query results.

This boundary protects expensive file rendering from unrelated updates.

`FileBody` dispatches the discriminated `FileDiff` union to the existing text or notebook renderer. `DiffGrid` remains untouched during this architectural rewrite. Preserving fold expansion across inline/split changes is an explicit post-rewrite TODO.

## 18. Deferred file-adjacent behavior

Once the manifest exists, every entry has a stable FileCard. This section does not design:

- virtualization or rich/plain transitions;
- hunk selection authority;
- next/previous navigation;
- scroll-follow;
- forced-rich files;
- line-pin scrolling;
- navigation to folded or unloaded hunks.

Those concerns belong to the later navigation section. The only retained hunk presentation requirement is that `FullFileHeader` visibly contains both local and global counters.

## 19. Reloading a ChangeSet

Reload is explicit:

```ts
async function reloadChangeSet(): Promise<void> {
  await manifest.refetch();
}
```

A new manifest normally produces a new `cache_id`.

Because file query keys contain `cache_id`:

- a new snapshot receives new file cache entries automatically;
- old file responses cannot appear in the new ChangeSet;
- no manual file cache clearing is required.

If the backend returns the same `cache_id`, it is declaring the snapshot unchanged, and existing file results may be reused.

Routine reloads must not call `removeQueries`.

## 20. Error policy

### Manifest failure

- No ChangeSet content is available.
- Display the manifest query error.
- No file sequence starts.

### Lazy-info failure

- FileCards remain.
- Affected entries become error-flavoured `LazyFile` values.
- Normal file loading continues.

### File failure

- That file query stays in the error state.
- Its FileCard derives an error-flavoured `LazyFile` with an explicit retry plank.
- The FileSequence continues to the next entry.
- No fake error `FileDiff` is inserted.

### Cancellation

- Cancellation is not presented as an error.
- The old sequence stops silently.

Toast deduplication should not require load IDs. A query error belongs to one key and can be presented at the component boundary that owns that query.

## 21. Ordinary queries and commands

Read-only operations use queries:

| Backend data | Definition |
|---|---|
| repositories | `api.repos.list()` |
| repository refs | `api.repos.refs(projectId)` |
| repository defaults | `api.repos.defaults(projectId)` |
| preset catalogs | `api.presets.catalogs()` |
| preferences | `api.profile.preferences(profileId)` |
| manifest | `api.changeSet.manifest(params)` |
| lazy metadata | `api.changeSet.lazyInfo(params, cacheId)` |
| file diff | `api.changeSet.file(...)` |

Server-changing operations use mutations:

| Command | Definition | Cache consequence |
|---|---|---|
| remove repository | `api.repos.remove()` | invalidate repository list |
| save main branch | `api.repos.saveMainBranch()` | invalidate that repository’s defaults |
| register profile | `api.profile.register()` | store returned selected profile |
| rename profile | `api.profile.rename()` | replace stored profile with response |
| save preferences | `api.profile.savePreferences()` | place returned preferences in exact query cache |
| prepare pull request | `api.pullRequest.prepare()` | invalidate refs for returned `project_id` |

Example:

```ts
const removeRepo = useMutation(() => ({
  ...api.repos.remove(),

  onSuccess: async () => {
    await queryClient.invalidateQueries({
      queryKey: api.repos.list().queryKey,
    });
  },
}));
```

Queries describe backend data. Mutations describe commands that change backend state.

## 22. Removed concepts

The rewrite removes rather than renames:

- `createDiffResources`;
- `DiffResources`;
- `createDiffUiState`;
- copied `diffData`;
- copied manifest tree;
- `filesByKey`;
- `applyManifest`;
- `upsertFile`;
- `upsertFiles`;
- `sourceParams`;
- `sourceParamsIdentity`;
- `sourceLoadId`;
- `activeLoadId`;
- `currentParamsIdentity`;
- manual query-key identity strings;
- `loadingFiles`;
- `fileErrors`;
- `loadingRevision`;
- separate manual aggregate status counters and mutable status strings;
- routine `removeQueries`;
- fake `FileEntry` error placeholders;
- direct click-triggered HTTP paths outside the single file-request lane;
- application-level `create*` abstractions.

## 23. Acceptance criteria

The implementation conforms to this specification when:

1. Every backend read has one canonical query definition in `api.ts`.
2. Every backend-changing action uses a mutation definition in `api.ts`.
3. `DiffParams` is the complete parameter type.
4. A Tab displays a `ChangeSet`.
5. Automatic file requests retain manifest order.
6. At most one file request is active.
7. Lazy entries are visited in order but are not fetched automatically.
8. Selecting a LazyFile may run it after the active request without reordering the remaining automatic files.
9. Every manifest entry gets a DOM-resident FileCard.
10. Each FileCard observes only its own file query.
11. File data is never copied into another store.
12. File failures remain query errors, derive error-flavoured LazyFiles and do not stop the sequence.
13. Changing snapshots is isolated through `cache_id`.
14. Expensive FileBody rendering is isolated from unrelated state.
15. `HuskFile`, `FullFile` and `LazyFile` own distinct headers.
16. FullFileHeader contains local and global hunk counters, but navigation behavior remains deferred.
17. No application-level `create*` files or abstractions remain.

The two most material proposed corrections beyond the earlier draft are:

- `FileEntry` becomes a real discriminated `FileDiff` union.
- Lazy metadata may load concurrently, but actual file-diff requests remain strictly sequential.

## 24. Client-side state and metadata freshness

### 24.1 Scope

This section covers ordinary client-side state:

- the active Tab;
- the globally selected repository;
- the globally selected diff engine;
- Tab-owned selections and derived `DiffParams`;
- component-owned input;
- control selection workflow;
- inline/split view;
- ChangeSet expansion;
- profiles and transient component state;
- backend data used by controls.

It does not specify:

- hunk selection;
- DOM hunk registration;
- scroll-following;
- measured layout;
- virtualization;
- forced rich rendering.

Those require a separate design because their lifetime is tied to rendered DOM.

### 24.2 State categories

Every value belongs to one category:

| Category | Owner | Examples |
|---|---|---|
| Backend data | TanStack Query | repositories, refs, defaults, presets, preferences |
| Workspace state | App | active Tab, selected repo, engine, inline/split view |
| Tab state | Individual Tab | selected values required to construct its `DiffParams` |
| ChangeSet state | ChangeSet | tree visibility, directory expansion, file expansion |
| Controls state | Tab-specific Controls | selected field values and field-to-field workflow |
| Input and selection state | `Input`, `AutocompleteInput` or `Select` | live user input, open popup, highlighted or selected choice |
| Component state | Component | profile dialog, other self-contained HUD state |
| Derived data | `createMemo` | filtered choices, selected repo name, realtime fallback values |
| DOM state | Deferred | hunk elements, measurements, scroll targets |

Backend data must never be copied into a Solid signal or store merely to make it available to components.

### 24.3 Solid primitives

Use:

- `createSignal` for one small independent value;
- `createStore` for one cohesive state entity;
- `createMemo` for pure derived values;
- `createEffect` only for actual external synchronization.

Effects must not:

- copy props into local signals;
- copy query data into stores;
- maintain values that can be derived;
- synchronize duplicate input state.

Solid components execute once and reactive consumers update independently. State must be placed at the component lifetime where it belongs. [Solid component basics](https://docs.solidjs.com/concepts/components/basics), [Solid stores](https://docs.solidjs.com/concepts/stores)

### 24.4 Workspace state

The application owns one small global workspace entity:

```ts
export type RepoSelection =
  | { state: "missing" }
  | { state: "selected"; projectId: ProjectId };

export type WorkspaceState = {
  activeTab: TabId;
  repo: RepoSelection;
  engine: DiffEngine;
  view: DiffViewMode;
};
```

```ts
const [workspace, setWorkspace] =
  createStore<WorkspaceState>(initialWorkspace);
```

The workspace exposes explicit commands:

```ts
selectTab(tab: TabId): void;
selectRepo(projectId: ProjectId): void;
setEngine(engine: DiffEngine): void;
setView(view: DiffViewMode): void;
```

Components do not receive a generic workspace setter.

The selected repository is global because Head, Refs, Branch Review and prepared PRs must operate on the same repository. Preset ignores it.

Engine and inline/split view are global because their controls live in the shared Header and apply consistently across Tabs. They differ in consequence: engine participates in backend `DiffParams`, while view changes only presentation.

The repository list is not workspace state. It remains backend state owned by:

```ts
api.repos.list()
```

The selected repo stores only the selected `ProjectId`. Its name, path and other backend fields are derived from the repository-list query.

### 24.5 Global repository invariant and reset boundary

Every displayed non-preset `ChangeSet` must belong to the globally selected repository:

```ts
params.project_id === workspace.repo.projectId
```

The Header `RepoSelect` and Tab `RepoGate` both call the same `selectRepo` command.

Changing the global repository is a complete workspace reset boundary:

```text
select repository
├── construct a canonical URL for the new repository
├── remove URL fields belonging to the previous workspace
├── replace the current URL
├── discard all mounted workspace state
└── reconstruct the workspace from the new URL
```

The recreated workspace restores only state represented by an explicit persistence source:

| Source | Restored state |
|---|---|
| URL | repo, active Tab, engine, view, selected parameters, explicit input values |
| Local storage | selected profile |
| TanStack cache | backend data, until garbage-collected |
| Nothing | current input, in-progress field-selection workflow, ChangeSet expansion, open Help |

The reset clears:

- all Tab state;
- all Controls workflow state;
- all Input and AutocompleteInput state;
- all Tab selections and derived `DiffParams` not represented by the new URL;
- all ChangeSets and their internal state;
- transient workspace HUD state.

It does not manually clear:

- the selected profile stored in local storage;
- the TanStack query cache;
- backend data;
- global provider infrastructure.

F5 additionally creates a new QueryClient and therefore starts with an empty in-memory query cache.

### 24.6 Pull request transition

The PR Tab does not require a selected repository before preparation.

Successful preparation produces enough canonical URL state to reconstruct the PR workspace:

```text
prepare PR
├── receive authoritative project_id
├── invalidate refs for that project
├── write the project, PR URL and branch selections into the URL
├── recreate the workspace from that URL
└── construct and display BranchReviewDiffParams
```

The global repository and PR parameters change through one URL-backed reset. The UI must never temporarily display a PR `ChangeSet` under another selected repository.

### 24.7 Tab ownership

Each Tab owns only the selected domain values returned by its Controls. Engine is supplied by the workspace when the Tab derives complete `DiffParams`.

```ts
type RefsSelection = {
  left: string;
  right: string;
};

type RefsTabState = {
  selected: RefsSelection | null;
};
```

These are local Tab and Controls types, not another exported API parameter family. Each Tab defines only the selection its own workflow returns. The complete immutable `DiffParams` is derived from that selection, the selected repository and the current workspace engine. Live input is not Tab state. URL values, repository defaults and autocomplete choices are supplied directly to the components that consume them.

Tabs do not own:

- manifest data;
- file data;
- tree visibility;
- file expansion;
- directory expansion;
- hunk state;
- rendered DOM registrations.

### 24.8 AutocompleteInput and selected values

`AutocompleteInput` is an explicit state-owning part of the client data model. It is not a controlled text box whose live value is mirrored into Tab state.

It owns:

- the current user input;
- whether the user has edited the supplied value;
- its open popup;
- its current autocomplete interaction;
- the presentation and filtering of choices.

Its architectural inputs are:

- a realtime initial or default value;
- realtime autocomplete choices;
- an optional `onEditNotification` callback for interaction-triggered warmups;
- an `onDone` callback returning the selected or entered value.

The exact TypeScript interface is intentionally postponed. In particular, this section does not decide whether the realtime value is named `initialInput`, `defaultInput`, `seed`, or represented by a tagged value.

The required behavior is:

```text
realtime initial/default value
        │
        ├── no user edit ──► may update displayed input
        └── user edited  ──► must not overwrite user input

realtime choices
        └── may update autocomplete suggestions at any time

user confirms input
        └── onDone(value)

user edits input
        └── onEditNotification() may warm stale backend choices
```

An empty user edit is still an edit. Clearing the field must prevent a later default from silently restoring text.

`onEditNotification` does not transfer the input value to the caller and does not make the input controlled. It only reports that relevant user interaction occurred, allowing the caller to ask TanStack Query to warm stale autocomplete data. Calling it for each edit is acceptable because query freshness and in-flight deduplication govern whether a backend request is actually made.

`onDone` transfers the selected or entered value. The owning Controls must react by advancing or finishing its workflow: for example, focus the next field, return the resulting selection, or trigger the Tab action. The exact focus scheduling and DOM implementation are intentionally unspecified.

The caller may retain an `onDone` result because it is a meaningful value needed for the current workflow. It must not retain it merely to restore an eternally mounted input later.

For example, tab-specific Controls may use values returned by `onDone` to move between fields or return one complete selection:

```text
AutocompleteInput
├── owns live input
└── onDone(left)
        │
        ▼
RefsControls
├── retains selected left for the current workflow
├── waits for selected right
└── onDone({ left, right })
        │
        ▼
RefsTab
├── validates the selected control values
├── stores the refs selection
└── derives immutable RefsDiffParams with the workspace engine
```

There is no universal `ControlsState`, `controls.tab`, or `controls.mode`.

There is no universal `Controls` component. Each Tab has Controls appropriate to its own selection workflow.

The exact focus transitions, keyboard gestures, and a possible shared `Form` primitive are postponed. They are local interaction design, not part of this state-ownership interface, as long as `AutocompleteInput` emits the notifications and Controls react to `onDone` with the appropriate workflow transition.

### 24.9 Engine and view ownership

Engine is workspace-owned. It is selected in the shared Header and applies across Tabs.

Changing an engine:

- updates the workspace engine;
- causes every Tab with a selection to derive new immutable `DiffParams`;
- starts a new backend diff generation for the active `ChangeSet`;
- preserves mounted Tabs, Controls, Inputs and the outer `ChangeSet` instance;
- preserves ChangeSet-owned layout state, such as tree visibility and path-based expansion, where it remains valid;
- never presents old-engine file results as results of the new engine.

The API contract continues to use complete `DiffParams` for manifest, lazy-info and file queries. Although the current Python manifest handler does not use engine, the frontend does not introduce a second parameter contract to special-case that fact. An engine change therefore selects a new manifest query and then restarts strict sequential file queries.

An engine change is therefore much more expensive than a view change, but it is not a reason to discard unrelated client state or collapse the page layout.

Inline/split view is workspace-owned because it is presentation state shared across Tabs.

Changing view:

- does not change `DiffParams`;
- does not request another ChangeSet;
- does not reset component-owned input;
- does not reset ChangeSet expansion;
- may require hunk/layout work specified in the later DOM-state section.

### 24.10 Tab and ChangeSet lifetime

All lightweight Tabs, Controls and Inputs remain mounted until the workspace reaches an explicit reset boundary.

Only the active Tab displays its Header and Controls. Cheap Controls remain mounted under the HTML `hidden` attribute so their component-owned input survives ordinary Tab switches.

The Tab’s `ChangeSet` instance remains mounted while its Tab is inactive so that the ChangeSet can preserve its own small client state. Its expensive content is only mounted while active.

```tsx
function RefsTab(props: RefsTabProps) {
  const [state, setState] =
    createStore<RefsTabState>(initialRefsTabState);

  const params = createMemo<RefsDiffParams | null>(() => {
    const selected = state.selected;
    if (selected === null) {
      return null;
    }

    return {
      project_id: props.projectId,
      mode: "refs",
      engine: props.engine,
      left: selected.left,
      right: selected.right,
    };
  });

  function showRefs(inputs: {
    left: string;
    right: string;
  }): void {
    setState("selected", {
      left: inputs.left,
      right: inputs.right,
    });
  }

  return (
    <>
      <Show when={props.active}>
        <Header
          engine={props.engine}
          view={props.view}
          repo={props.repo}
        />

      </Show>

      <section hidden={!props.active}>
        <RefsControls
          active={props.active}
          projectId={props.projectId}
          onDone={showRefs}
        />
      </section>

      <Show when={state.selected} keyed>
        {() => (
          <ChangeSet
            active={props.active}
            params={params()!}
          />
        )}
      </Show>
    </>
  );
}
```

The keyed boundary recreates the `ChangeSet` whenever the Tab receives a new selection. Changing the global engine changes `params()` but not `state.selected`, so the outer `ChangeSet` remains mounted and retains its client-owned layout state while its manifest and file query generation changes.

Keeping Controls mounted is not restoration caching. Their state continues to exist because their owning components continue to exist. When the workspace is recreated, the Controls and all their local input are destroyed and reconstructed from URL state, static defaults and realtime query data.

### 24.11 ChangeSet ownership

`ChangeSet` owns all state internal to one displayed result:

```ts
export type ChangeSetState = {
  treeOpen: boolean;
  directoryExpansion: Record<string, boolean>;
  fileExpansion: Record<string, boolean>;
};
```

It also owns:

- the manifest observer;
- derived manifest traversal;
- the FileSequence;
- file query observers;
- lazy metadata;
- FileTree rendering;
- FileCard rendering and its derived HuskFile, FullFile and LazyFile states;
- reload behavior;
- later hunk and DOM state.

The public `ChangeSet` boundary stays mounted while inactive. Its internal active content mounts only while the Tab is active:

```tsx
function ChangeSet(props: {
  active: boolean;
  params: DiffParams;
}) {
  const [state, setState] =
    createStore<ChangeSetState>(initialChangeSetState);

  return (
    <Show when={props.active}>
      <ChangeSetContent
        params={props.params}
        state={state}
        setState={setState}
      />
    </Show>
  );
}
```

`ChangeSetContent` is a private component boundary inside the same module. It exists so inactive ChangeSets retain lightweight client state without retaining:

- rendered file DOM;
- active manifest observers;
- active file observers;
- a running FileSequence.

It is not a separate application abstraction or separate resource owner.

Consequently:

- switching Tabs preserves ChangeSet expansion;
- switching Tabs removes expensive ChangeSet DOM;
- switching Tabs stops the inactive FileSequence;
- returning may reuse TanStack-cached backend data;
- selecting new Tab values recreates the ChangeSet and its state;
- changing engine preserves the outer ChangeSet and layout state while replacing its manifest and file query generation;
- recreating the workspace destroys every ChangeSet;
- explicit reload resets state from inside the ChangeSet.

The Tab never updates ChangeSet expansion directly.

### 24.12 ChangeSet reload

Reload belongs to `ChangeSet`, not to the Tab.

Reloading:

1. resets the ChangeSet-owned tree and expansion state;
2. invalidates the manifest query for the current `DiffParams`;
3. allows the new manifest to produce its new `cache_id`;
4. restarts strict manifest-order loading.

The reload control may be visually placed in shared HUD, but its command targets the active `ChangeSet`.

### 24.13 Backend-data lifecycle

TanStack Query owns the lifecycle of every backend entity:

```text
not needed
    -> pending
    -> available | error

available
    -> stale or refreshing, while old data remains usable
    -> updated | error with old data still available
```

The application must not turn those independent lifecycles into one global loading gate. Each consumer defines what it can do before its own data is available:

| Missing backend data | Required UI behavior |
|---|---|
| selected repository | Only the repo-dependent action shows `RepoGate`; the rest of the Tab remains alive |
| repository list | Header selector or `RepoGate` shows its own pending/error state |
| refs, branches and remotes | Inputs remain usable as free-form inputs; autocomplete choices wait locally |
| repository defaults | Inputs render without the realtime default; an untouched input may adopt it when it arrives |
| preset catalogs | Preset controls wait locally; the Preset Tab itself remains alive |
| manifest | The owning `ChangeSet` shows its own pending/error state |
| rendered file | The owning `FileCard` derives its own HuskFile, FullFile or LazyFile presentation |
| preferences | Only the Profile preferences UI waits or reports the error |

The rule is:

> Missing data gates the smallest component or action that actually requires it.

When a repository becomes known, including when it is reconstructed from the URL, the workspace immediately starts the cheap repo-scoped metadata that later controls are likely to need:

```ts
function warmRepository(projectId: ProjectId): void {
  void queryClient.prefetchQuery(
    api.repos.refs(projectId),
  );
  void queryClient.prefetchQuery(
    api.repos.defaults(projectId),
  );
}
```

Tabs do not wait for these promises. A later `useQuery` using the same definition observes the existing pending, available or error state. The result is never copied into an App, Tab or Controls signal.

For `AutocompleteInput`, both the initial/default value and the choices are realtime inputs. Query results may therefore arrive or refresh while the component is mounted. Realtime data may update choices immediately and may fill an untouched input, but it must never overwrite input the user has edited, including an intentional empty value.

### 24.14 Metadata query definitions

Query definitions and freshness policy live in `api.ts`.

```ts
api.repos.list();
api.repos.refs(projectId);
api.repos.defaults(projectId);
api.presets.catalogs();
```

Provisional freshness policy:

| Query | `staleTime` |
|---|---:|
| repositories | 5 seconds |
| refs, branches and remotes | 30 seconds |
| preset catalogs | 5 seconds |
| repository defaults | `Infinity` |

These values should be adjusted after measuring the real endpoints.

`staleTime` does not schedule a request. It decides whether a cached result may be reused when some trigger occurs. [TanStack `useQuery` reference](https://tanstack.com/query/latest/docs/framework/solid/reference/useQuery)

The global QueryClient policy remains:

```ts
queries: {
  retry: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
}
```

There is no polling.

### 24.15 Repository-list query

Consumers:

- Header `RepoSelect`;
- Tab `RepoGate`.

Possible requests occur when:

- the Header must resolve a selected repo ID;
- the Header repo selector opens;
- the RepoGate is displayed;
- a repository mutation invalidates the list.

The Header and gate observe the same canonical query:

```ts
api.repos.list()
```

They do not own separate repository resources.

There is no repository refresh button.

### 24.16 Refs, branches and remotes query

Consumers:

- Refs Tab autocomplete;
- Branch Review branch autocomplete;
- Branch Review remote selection.

All consumers share:

```ts
api.repos.refs(projectId)
```

Possible requests occur when:

- a repository is selected or reconstructed from the URL and the workspace prefetches the query;
- Refs or Branch Review becomes active and the query is stale;
- the global repo changes, producing another query key;
- the user edits a refs-backed autocomplete while the query is stale;
- the user clicks the explicit refresh button;
- PR preparation invalidates refs for its returned project.

The mounted Controls observe the query only while their Tab is active. Activation therefore gives TanStack the normal opportunity to fetch missing data or refresh stale data without keeping inactive query observers alive:

```ts
const refsOptions = () =>
  api.repos.refs(props.projectId);

const refs = useQuery(() => ({
  ...refsOptions(),
  enabled: props.active,
}));
```

Typing filters the currently available choices locally and emits `onEditNotification`. The caller may use that notification to prefetch the canonical refs query:

```ts
function warmRefs(): void {
  void queryClient.prefetchQuery(
    api.repos.refs(props.projectId),
  );
}
```

`AutocompleteInput` does not provide its current text to `warmRefs`, and the caller does not store it. The backend query still fetches the complete refs entity; filtering remains local.

Both repository-selection and interaction-triggered prefetches respect the query definition’s `staleTime`. Fresh data causes no backend request, and an already-running request is shared rather than duplicated. [TanStack QueryClient reference](https://tanstack.com/query/latest/docs/reference/QueryClient)

### 24.17 Preset-catalog query

Consumer:

- Preset Tab.

Possible requests occur when:

- the Preset Tab becomes active and catalogs are stale;
- the explicit preset refresh button is clicked.

The catalog choices are supplied to the active preset controls in realtime. Until they arrive, only the catalog-dependent control waits; the Tab remains mounted.

The current endpoint may continue returning all four catalogs:

```ts
api.presets.catalogs()
```

It does not load complete preset file trees or file contents. It lists preset directories and validates their fixture pairs.

Splitting the endpoint by preset type is unnecessary unless measurement shows an actual problem.

### 24.18 Repository-defaults query

Consumer:

- Branch Review’s effective base and review selections.

Possible requests occur when:

- a repository is selected or reconstructed from the URL and the workspace prefetches the query;
- Branch Review becomes active and observes a missing or invalidated query;
- the global repository changes, producing another query key;
- F5 starts a new QueryClient with an empty cache;
- saving the main branch explicitly invalidates the defaults query.

Defaults use:

```ts
staleTime: Infinity
```

They have no refresh button.

Defaults remain query data. They are supplied to `AutocompleteInput` as realtime initial/default values rather than copied into Tab or Controls state. An arriving default may fill an untouched input; it must not replace user-edited input.

### 24.19 Explicit metadata refresh buttons

Only these controls receive metadata refresh buttons:

| Location | Action |
|---|---|
| Refs Tab | refresh refs |
| Branch Review | refresh branches and remotes |
| Preset Tab | refresh preset catalogs |

The buttons call the exact observer’s `refetch()`:

```tsx
<button
  type="button"
  aria-label="Refresh branches and remotes"
  title="Refresh branches and remotes"
  disabled={refs.isFetching}
  onClick={() =>
    void refs.refetch({
      cancelRefetch: false,
    })
  }
>
  <RefreshCw
    aria-hidden="true"
    classList={{ spinning: refs.isFetching }}
  />
</button>
```

`cancelRefetch: false` prevents repeated clicks from cancelling and restarting an existing request.

A metadata refresh button must be visually and semantically distinct from reloading the current `ChangeSet`.

There are no metadata refresh buttons for:

- Head;
- the repo list;
- repository defaults;
- PR preparation.

### 24.20 Mutation consequences

Known backend changes update or invalidate the queries they affect:

| Command | Cache consequence |
|---|---|
| remove repository | invalidate repository list |
| save main branch | invalidate that repository’s defaults |
| save preferences | replace the exact preferences cache entry |
| prepare pull request | invalidate refs for returned `project_id` |

Selecting a repository is client state and does not invalidate backend data. It changes the query key used by repo-dependent controls.

PR preparation does not invalidate the repository list because it selects an already-marked repository rather than creating one.

### 24.21 Profiles and preferences

The selected profile is client state initialized from `localStorage`.

Saving or forgetting a selected profile updates `localStorage` explicitly. An effect does not mirror profile state into storage.

Preferences remain backend state:

```ts
api.profile.preferences(profileId)
```

Changing the selected profile changes the preferences query key.

Saving preferences places the mutation response into the exact preferences cache entry. Preferences are not copied into App signals.

Profile UI state is local and tagged:

```ts
export type ProfileUiState =
  | { view: "closed" }
  | { view: "menu" }
  | {
      view: "username";
      input: string;
    }
  | {
      view: "preferences";
      aggressiveFolds: boolean;
    };
```

Mutation pending and error states come from TanStack mutations.

### 24.22 Local component state

A domain-independent `Select` belongs to the same state category as `Input` and `AutocompleteInput`. It owns local selection interaction state, such as whether its popup is open:

```ts
const [open, setOpen] = createSignal(false);
```

It does not own the selected domain value.

`AutocompleteInput` owns its live input, edited status, popup and focus state. It receives realtime initial/default data and realtime choices, may notify its caller of editing for stale-data warmups, and reports the selected or entered value through `onDone`. Its caller does not mirror every edit.

Controls may retain values returned by `onDone` only for their current mounted workflow, such as moving from a selected left ref to the right-ref field. When the workflow has all required values, the Tab receives the selection or parameters it needs. This is not a restoration cache.

Filtered autocomplete choices use `createMemo`.

Pending repository removal is derived from the repository-removal mutation rather than stored in a separate `removingProjectId` signal.

Notices are derived from query and mutation states rather than stored independently.

Toasts remain a global provider.

### 24.23 Help and Debug

Help and Debug are independent.

They must not be represented as variants of one mutually exclusive union.

```ts
const [helpOpen, setHelpOpen] =
  createSignal(false);

const [debugEnabled, setDebugEnabled] =
  createSignal(false);
```

Both may be true simultaneously.

Signal reduction only combines values that form one entity or share an invariant. Values are not combined merely because they are visually adjacent or currently implemented in the same file.

### 24.24 URL and browser storage

The URL is the source of reconstructible workspace state at every intentional reset boundary.

On initial load, F5, repository change or another explicit reset:

1. canonicalize the URL first;
2. remove fields that do not apply to the new workspace or repository;
3. discard the mounted workspace and all of its local client state;
4. reconstruct from the canonical URL, static defaults and asynchronous query data.

Explicit commands update the URL when user-visible navigation state changes:

- selecting a Tab;
- selecting a repository;
- changing view;
- showing a ChangeSet;
- successfully preparing a PR.

An effect does not continuously serialize the complete application state into the URL.

Profile selection is persisted explicitly through profile actions.

TanStack query data is not persisted in browser storage.

Live `AutocompleteInput` text and Controls workflow values are not persisted merely because they exist. They become reconstructible only when an explicit command uses them to update navigation or `DiffParams` state in the URL.

### 24.25 Reset matrix

| Event | Result |
|---|---|
| Edit an input | Change only that `AutocompleteInput` |
| Complete an input | Give the value to the owning Controls workflow through `onDone` |
| Complete a Controls workflow | Validate and give the complete entity or parameters to the Tab |
| Switch Tab | Preserve mounted Tab, Controls, input and ChangeSet-owned lightweight state |
| Select new Tab values | Recreate that Tab’s outer ChangeSet |
| Change global engine | Derive new `DiffParams`, start new manifest and sequential file queries, and preserve Controls, Inputs and ChangeSet-owned layout state |
| Change inline/split view | Preserve Tab and ChangeSet state |
| Change global repo | Canonicalize the URL, discard the complete mounted workspace and reconstruct every Tab from the URL |
| Prepare PR | Write the authoritative repo and prepared result to the canonical URL, then reconstruct the workspace |
| Explicit workspace reset | Rewrite/canonicalize the URL, discard all workspace-local state and reconstruct it |
| Reload ChangeSet | ChangeSet resets its own state and queries |
| F5 | Create a new QueryClient and reconstruct client state from URL and explicit browser storage |

### 24.26 Removed client-state concepts

The rewrite removes:

- universal `ControlsState`;
- universal `Controls`;
- duplicated local Controls draft;
- Tab-level draft or live input state;
- `BranchDraft` and input-origin bookkeeping;
- parent-controlled `AutocompleteInput` text;
- restoration caches for input or `onDone` values;
- treating `onEditNotification` as input-state synchronization;
- unguarded per-keystroke backend requests;
- selective per-Tab reset behavior on repository change;
- `controls.tab`;
- `controls.mode`;
- `baseSelectionDirty`;
- `reviewSelectionDirty`;
- Tab-owned ChangeSet expansion;
- copied repository data;
- copied refs data;
- copied defaults data;
- copied preset data;
- copied preferences data;
- manual promise deduplication;
- manual loading/error signals for queries and mutations;
- prop-to-signal synchronization effects;
- mutually exclusive Help/Debug state;
- stored values that can be derived with `createMemo`;
- application-level request counters;
- application-specific `create*` state abstractions.

### 24.27 Acceptance criteria

The client-state design conforms to this specification when:

1. The selected repo is global.
2. Every displayed non-preset ChangeSet matches the selected repo.
3. Preset ignores the selected repo.
4. Successful PR preparation selects its authoritative repo.
5. Repository change, F5 and explicit reset reconstruct the complete workspace from canonical URL state.
6. Engine is workspace-owned; each Tab owns only its selected values or result.
7. `AutocompleteInput` owns live input; `onEditNotification` may warm stale data without transferring input ownership, and `onDone` transfers the selected or entered value.
8. Initial/default values and choices reach `AutocompleteInput` in realtime without overwriting edited input.
9. Controls retain selected values only for their current mounted workflow and give Tabs the resulting selection or parameters.
10. ChangeSet owns its internal layout and reload state.
11. Inactive Tabs do not retain expensive rendered ChangeSet DOM.
12. Switching Tabs preserves mounted lightweight Tab, Controls, input and ChangeSet state.
13. Changing engine derives new `DiffParams` and queries without remounting Controls, Inputs or the outer ChangeSet layout state.
14. Backend data exists only in TanStack Query.
15. Missing backend data gates only the smallest dependent component or action.
16. Refs and defaults are prefetched when a repository becomes known without blocking Tab rendering.
17. Refs and Branch Review share one refs query per repo.
18. Typing filters locally and may request a stale-time-guarded warmup of the complete refs query.
19. Only Refs, Branch Review and Preset have metadata refresh buttons.
20. Help and Debug remain independent.
21. Controls react to `onDone` by advancing, refocusing or triggering the appropriate action, while exact focus, keyboard and possible `Form` behavior remain postponed.
22. Hunk and DOM state remain deferred to their dedicated section.

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

`FileCard` is the stable manifest-position wrapper. The three file states own different presentations and different headers.

The ChangeSet title remains with the ChangeSet. It is not placed in AppHeader because AppHeader space is limited.

### 25.3 Manifest summary authority

The manifest remains thin even though it carries compact aggregate statistics. The backend already obtains these values while listing changed paths; they are snapshot metadata, not rendered file content.

```ts
export type ManifestSummary = {
  changed_files: number;
  added_files: number;
  removed_files: number;
  updated_files: number;
  added_lines: number;
  removed_lines: number;
  skipped_files: number;
};
```

The summary is immutable for one manifest/cache ID and is never recomputed from loaded FullFiles.

The generic optional-field-heavy `Summary` concept must become distinct types:

```ts
export type ManifestSummary = {
  // Aggregate snapshot information.
};

export type TextFileSummary = {
  // Complete text-file statistics.
};

export type NotebookFileSummary = {
  // Complete notebook-file statistics.
};
```

Notebook cell totals do not progressively mutate `ManifestSummary`. They remain in the notebook FullFile presentation for now. If aggregate notebook statistics are needed later, they require either a separate backend summary or an explicitly partial metric.

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

# Corrected draft: virtualization, navigation and hunk selection

## 26. Scope

This section specifies:

- whole-file virtualization;
- DOM-owned hunk selection;
- rich/virtual FileBody replacement;
- local and global hunk counters;
- explicit and user-scroll navigation;
- HuskFile and LazyFile pseudo-hunks;
- FileTree highlighting derived from the selected hunk;
- line pins;
- browser text-side selection;
- HUD, Help, and Debug behavior;
- direct hotkeys;
- future notebook navigation regions.

It does not revisit:

- manifest and file request definitions;
- strict sequential file fetching;
- AppHeader loading messages;
- the semantic `HuskFile`, `FullFile`, and `LazyFile` boundaries;
- notebook backend response design;
- row virtualization.

Anything explicitly labelled TODO in this specification is a post-rewrite follow-up. It is not an unfinished implementation choice or an acceptance requirement for the rewrite itself.

## 27. Essential complexity

The system genuinely must handle:

1. A ChangeSet may contain enough rendered DOM to hurt memory, layout, and paint performance.
2. Files load over time, so a provisional global hunk sequence can change.
3. HuskFiles and LazyFiles do not yet know their real hunk structure.
4. Rich FileBody DOM may be removed and recreated.
5. Inline/split changes may replace rich row DOM.
6. Code folds may hide individual hunk targets.
7. A file folded directly or through its directory leaves navigation.
8. A structural owner may destroy the currently selected hunk.
9. User scrolling, programmatic scrolling, browser anchoring, and layout movement all produce browser scroll events.
10. Sticky AppHeader and FileHeader elements affect visible geometry.
11. FileTree, FileHeader, HUD, and rendered targets need projections of the selected hunk.
12. Notebook files contain several file-like regions inside one outer file.
13. Future raw/rich notebook modes may expose different numbers of hunks.
14. URL line pins may refer to content that has not rendered yet.
15. Browser native search must see both complete file sides while a file is virtual.

The accidental complexity currently includes:

- selected hunk identity stored in both DOM and a Solid signal;
- separately maintained `HunkPosition`;
- `activeHunkFileId`;
- global forced-rich file maps;
- global virtualized-file maps;
- layout, loading, and virtualization revision signals;
- a rich preload radius;
- delayed reconciliation timers;
- repeated animation-frame retries during FileTree navigation;
- page-scrolling functions outside the two authorized navigation systems;
- Debug sampling while Debug is closed.

The rewrite preserves the essential complexity while deleting those coordination mechanisms.

## 28. Selection model: persistent FileCard identity with replaceable targets

Hunk targets live inside the rendered FileBody.

Only the currently selected hunk identity survives outside FileBody, as DOM attributes on its stable FileCard.

```text
FileCard
├── selected-hunk identity attributes
├── FileHeader
└── FileBody switch
    ├── rich hunk targets
    └── virtual hunk targets
```

The target owns:

- its hunk identity;
- its position in current navigation order;
- visible selected decoration;
- viewport geometry;
- the scrolling destination.

The FileCard owns only the selected identity that must survive representation replacement.

There is:

- no selected-file state;
- no `data-selected-file`;
- no selected-file signal;
- no hidden marker for every hunk;
- no Solid selected-hunk signal.

FileTree highlighting is derived from the FileCard that currently owns selected-hunk attributes.

## 29. Hunk identities

The current backend already produces one file-global hunk sequence.

Real hunks therefore use:

```ts
type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};
```

HuskFile and LazyFile pseudo-hunks use:

```ts
type PseudoHunkIdentity = {
  fileIndex: number;
  kind: "husk" | "lazy";
  entryDirection: 1 | -1;
};
```

```ts
type HunkIdentity =
  | RealHunkIdentity
  | PseudoHunkIdentity;
```

`entryDirection` determines how a selected pseudo-hunk maps when its real file result arrives:

- `1` maps into the beginning;
- `-1` maps into the end;
- direct plank or FileTree activation behaves as forward entry.

Global positions such as `9/42` are derived display values. They are not identities.

## 30. DOM contract

A selected real hunk may look like:

```html
<article
  class="file-card"
  data-file-card
  data-file-index="3"
  data-file-state="full"
  data-file-render="rich"
  data-selected-hunk-kind="real"
  data-selected-hunk-index="2"
>
  <header class="full-file-header">
    <span data-hunk-counter="local"></span>
    <span data-hunk-counter="global"></span>
  </header>

  <div data-file-body>
    <div
      class="diff-row"
      data-hunk-target
      data-hunk-kind="real"
      data-file-index="3"
      data-hunk-index="2"
      data-selected
      aria-current="true"
    ></div>
  </div>
</article>
```

A pseudo-hunk target may look like:

```html
<button
  data-hunk-target
  data-hunk-kind="husk"
  data-file-index="4"
></button>
```

or:

```html
<button
  data-hunk-target
  data-hunk-kind="lazy"
  data-file-index="5"
></button>
```

At most one FileCard contains selected-hunk attributes.

When the selected target is mounted, exactly one matching target carries:

```html
data-selected
aria-current="true"
```

## 31. Hunk production

The backend remains authoritative for:

- real hunk boundaries;
- real file-local hunk indices;
- exact `hunk_count`.

The frontend does not infer hunks by grouping changed rows.

Rich and virtual representations of the same FullFile structure must expose the same participating real-hunk identities.

Code folds may intentionally exclude hidden hunks from participation.

Pseudo-hunks are frontend navigation entities:

- one for every HuskFile;
- one for every expanded LazyFile.

They are not reported as backend hunks.

## 32. File states and pseudo-hunks

### HuskFile

`HuskFile` contributes one provisional pseudo-hunk.

Its target may be its compact loading body or another stable target inside the card.

The Husk pseudo-hunk:

- participates in next/previous navigation;
- participates in the provisional global counter;
- is a FileTree destination;
- may be selected and scrolled to;
- never changes strict sequential request order;
- never starts a separate request.

When the automatic file response arrives, it is replaced with the resulting FullFile structure.

### LazyFile

An expanded `LazyFile` contributes one provisional pseudo-hunk through its colored plank.

It:

- participates in explicit navigation;
- participates in the provisional global counter;
- is a FileTree destination;
- may be selected and scrolled to;
- does not fetch merely because it was selected.

Only activating the colored plank starts its canonical file request.

A folded LazyFile contributes no target.

### FullFile

An expanded `FullFile` contributes its current participating real-hunk targets.

A folded FullFile contributes no targets.

A FullFile with zero hunks contributes no target unless a later explicit product requirement adds a separate non-hunk target.

## 33. Destructive structural transitions

Rich ↔ virtual is representation replacement. The same identity survives.

The following are structural transitions:

- HuskFile → FullFile;
- LazyFile → FullFile;
- folding a file;
- folding a directory containing files;
- changing code-fold participation;
- future notebook raw/rich structure changes;
- replacing the complete ChangeSet snapshot.

A structural owner that is about to remove the currently selected hunk is responsible for repairing selection before completing its destruction.

This is one explicitly permitted non-user selection path.

### Pseudo-hunk replacement

If a selected Husk or Lazy pseudo-hunk becomes a FullFile:

- forward entry selects the first resulting real hunk;
- backward entry selects the last resulting real hunk;
- zero resulting hunks select the next target after that file;
- if no later target exists, select the previous target;
- if no target exists, clear hunk selection.

### Folding a file or directory

Before removing a selected hunk:

1. find the first target after the folded subtree;
2. otherwise find the last target before it;
3. call `selectHunk`;
4. only then remove the folded targets;
5. clear selection if the entire sequence disappears.

The folding operation never scrolls merely because it repaired selection.

### Code folds

The initial architecture keeps current behavior:

- hidden hunks do not participate;
- navigation skips them;
- user-scroll following cannot select them;
- explicit hunk navigation does not expand a code fold.

If collapsing a code fold removes the selected hunk, the fold owner applies the same adjacent-target repair before removing it.

### Representation replacement

Rich ↔ virtual and inline ↔ split do not perform structural repair.

They preserve the selected identity and only project it onto the replacement target.

Here is the complete candidate heuristic. The architecture is settled; only the numeric thresholds should be tuned through browser testing.

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

Programmatic hunk navigation, FileTree navigation, and line-pin restoration use:

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
- Every participating real-hunk identity in the same order as RichFileBody.
- Local projection of the selected hunk, if this FileCard owns it.

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
- Does not change the set or order of participating hunk identities.
- Reprojects selected decoration locally.
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

The virtualization and scroll-anchoring design must be tested from the least convenient starting condition:

1. Load only the manifest and begin strict sequential file fetching from the start.
2. While later files are still HuskFiles, select the first available hunk near the beginning.
3. Invoke Previous once and wrap to the final manifest target.
4. If the final target is a HuskFile, navigate immediately to its pseudo-hunk without waiting for its file request.
5. Continue sequentially loading and enriching earlier FileCards above the viewport.
6. Walk backward from the end toward the start through a changing mixture of HuskFile, LazyFile, VirtualFile and rich FullFile targets.
7. Allow selected HuskFiles to be destructively replaced by their real hunk sets while preserving the backward entry direction.
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
- correct backward repair from a selected Husk pseudo-hunk to the new file’s last participating real hunk;
- global and local counter changes as pseudo-hunks become real hunks;
- visible layout jumps during first-time virtual → rich transitions above the viewport;
- rich → virtual height preservation for previously measured files;
- native scroll anchoring behavior at and near the document end;
- scroll-follow behavior while representation and structural transitions occur;
- browser native search inside fixed-height VirtualFiles;
- DOM node and span counts throughout the traversal;
- long tasks, dropped frames and enrichment duration for each row-count band.

The scenario should run with the intrinsic-size optimization enabled and disabled. That comparison determines whether the existing optimization helps the new architecture or merely adds a second source of provisional geometry.

So the short version is:

> Every distant hydrated text file is virtual. Row count determines how far ahead it becomes completely rich and how far away it must travel before becoming virtual again. Rich → virtual preserves height through a fixed, internally scrollable VirtualFile; RichFileBody always remains natural document content.
## 48. Selection projection

When rich/virtual or inline/split replacement destroys visible targets, selected identity remains on FileCard.

One narrow local operation restores decoration:

```ts
function projectSelectedHunk(
  fileCard: HTMLElement,
): void;
```

It:

1. reads selected-hunk attributes from FileCard;
2. finds the unique matching target in current FileBody;
3. removes stale selected decoration within that FileCard;
4. marks the matching target;
5. asserts if representation-only replacement unexpectedly lost the identity.

It never:

- selects another hunk;
- clears selection;
- updates counters;
- updates FileTree;
- scrolls;
- fetches;
- enriches;
- expands anything.

This is a local DOM projection, not selection reconciliation.

The implementation may use a FileBody-ready callback, ref, or scoped DOM event. It must not use revision signals, delayed timers, or repeated animation frames.

## 49. Rich materialization

Exact hunk and line navigation may require rich geometry.

The operation is named:

```ts
async function waitToEnrich(
  fileCard: HTMLElement,
): Promise<void>;
```

If already rich, it returns immediately.

Otherwise:

```text
request local rich mode
        │
        ▼
FullFile changes renderMode
        │
        ▼
rich FileBody mounts
        │
        ▼
selected decoration is projected
        │
        ▼
waitToEnrich resolves
```

`waitToEnrich`:

- changes only FullFile-local rendering;
- does not select a hunk;
- does not update counters;
- does not scroll;
- does not fetch.

Navigation re-resolves its target after `waitToEnrich`.

## 50. Hunk selection

There is no file-selection operation.

The central primitive is:

```ts
function selectHunk(
  root: HTMLElement,
  hunk: HunkIdentity,
): void;
```

It:

1. resolves the current target for the identity;
2. removes selected identity from the previous FileCard;
3. removes previous target decoration;
4. writes the new identity on the owning FileCard;
5. decorates the resolved target;
6. updates hunk counters;
7. updates FileTree highlighting from the owning FileCard;
8. does not scroll.

A separate operation clears selection:

```ts
function clearHunkSelection(
  root: HTMLElement,
): void;
```

It:

- removes selected identity;
- removes selected-target decoration;
- clears FileTree highlighting;
- updates counters;
- does not select a replacement;
- does not scroll.

Only these paths may select or reselect a hunk:

1. Next/Previous navigation.
2. FileTree navigation to a file’s first hunk.
3. Recognized user-scroll following.
4. Husk or Lazy plank activation.
5. A destructive structural owner repairing selection before removal.
6. Explicit line-pin behavior, only if later specified to select a hunk.

Rendering, virtualization, Debug, counters, and ordinary reconciliation never select.

## 51. Hunk counters

Every specialized FileHeader provides space for local and global hunk information appropriate to its state.

### FullFileHeader

```text
Local —/5    Global —/42+
```

When selected:

```text
Local 2/5    Global 9/42+
```

### HuskFileHeader

The exact local count is unknown:

```text
Local pending    Global 9/42+
```

### LazyFileHeader

The exact local count is deferred:

```text
Local deferred    Global 9/42+
```

The global sequence includes:

- one pseudo-hunk for every HuskFile;
- one pseudo-hunk for every expanded LazyFile;
- participating real hunks in expanded FullFiles.

It excludes:

- folded files;
- real hunks hidden inside code folds.

The `+` suffix means one or more pseudo-hunks may later become a different number of real hunks.

The local FullFile position comes from the backend file-local hunk index and exact `hunk_count`.

The global position comes from current participating target order.

Counters are imperative DOM projections. There is no `HunkPosition` Solid signal.

Counters update only when:

- `selectHunk` runs;
- `clearHunkSelection` runs;
- a destructive structural transition changes the target sequence.

Rich ↔ virtual and inline ↔ split do not update counters.

The Debug HUD reads the same derived counter.

## 52. Notebook behavior and future region keys

Current notebook behavior already provides one file-global source-hunk sequence.

Today:

- one outer notebook FileCard renders several cell cards;
- each changed cell source renders through its own DiffGrid;
- the backend offsets cell-local source hunk indices into one file-wide sequence;
- every cell source uses the same outer file index;
- notebook metadata is summary-only;
- cell metadata is summary-only;
- outputs are summary-only;
- metadata and outputs contribute no hunks;
- notebooks remain rich and are not virtualized.

Therefore current selection remains:

```ts
type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};
```

### Post-rewrite TODO: notebook navigation regions

The architecture must not assume forever that one file body is one grid.

A future notebook may remain one outer FullFile while containing:

```text
FullFile: notebook.ipynb
├── notebook metadata
├── cell A
│   ├── source
│   ├── metadata
│   ├── output 0 raw JSON
│   └── output 0 rich Plotly
├── cell B
│   ├── source
│   └── output 0 image
└── cell C
    └── source
```

When metadata and output navigation are implemented, identity may extend to:

```ts
type RegionHunkIdentity = {
  fileIndex: number;
  regionKey: string;
  itemKey: string;
};
```

Examples:

```text
regionKey = "cell:abc123:source"
itemKey   = "hunk:1"

regionKey = "cell:abc123:output:0:raw"
itemKey   = "hunk:4"

regionKey = "cell:abc123:output:0:rich"
itemKey   = "plot"
```

The actual global `9/42` remains derived from current target order. It is not identity.

Raw/rich output changes may replace N hunks with M hunks. That is a destructive structural transition, not representation-only virtualization.

Its owner must repair a selected identity before destroying it.

Region keys are a future extension, not part of the initial rewrite.

## 53. Main-page hunk navigation gateway

One interface owns ordinary hunk, FileTree-target, and return-to-top movement:

```ts
type PageNavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | { kind: "hunk"; hunk: HunkIdentity }
  | { kind: "top" };

async function navigate(
  root: HTMLElement,
  command: PageNavigationCommand,
): Promise<void>;
```

There are no file or directory navigation commands.

Outside line-pin restoration, no other code may call main-page:

- `window.scrollTo`;
- `scrollIntoView`;
- `scrollBy`.

Line-pin restoration is an explicit second authorized viewport-moving system because it must repeatedly restore a target while asynchronous file rendering and layout continue. It is specified separately and is not forced through this one-shot gateway.

### Next and previous

```text
read selected identity from FileCard DOM
        │
        ▼
query participating targets in DOM order
        │
        ▼
choose adjacent target
        │
        ▼
selectHunk
        │
        ▼
waitToEnrich when real target needs rich geometry
        │
        ▼
resolve matching rich target
        │
        ▼
perform one scroll
```

Selecting a Husk or Lazy pseudo-hunk does not enrich or fetch it.

### Direct hunk navigation

FileTree and other callers may resolve a concrete hunk and dispatch:

```ts
{ kind: "hunk", hunk }
```

It uses the same selection, enrichment, and scrolling sequence.

### FileTree target

A FileTree file target is not file navigation.

It:

1. explicitly unfolds the file if needed;
2. resolves the file’s first participating hunk;
3. dispatches ordinary hunk navigation.

For HuskFile or LazyFile, that first hunk is its pseudo-hunk.

A FullFile with no hunks has no hunk-navigation destination.

Directories do not navigate the page.

### Top

Return-to-top scrolls to zero and preserves selected hunk identity.

## 54. Wrapping and provisional targets

Because every manifest file has either:

- a Husk pseudo-hunk;
- a Lazy pseudo-hunk;
- zero or more real hunks;

the provisional sequence can represent unloaded files without fetching them out of order.

Next and Previous may traverse:

- real hunks;
- Husk pseudo-hunks;
- expanded Lazy pseudo-hunks.

They may wrap through the current provisional sequence.

As pseudo-hunks become real structures:

- the target count may change;
- the `+` suffix remains while any pseudo-hunk exists;
- structural handoff preserves or repairs selection;
- fetch order remains strict manifest order.

Hunk navigation never changes request order.

## 55. Scroll-source gate and throttled scroll-follow

```ts
type PageScrollSource =
  | "idle"
  | "user"
  | "command";
```

This is a local non-reactive controller variable.

It is not application state.

### Entering user scroll

Wheel, touch movement, and native scrolling keys may set:

```text
idle → user
```

Input at the corresponding document boundary does not arm user scrolling.

### Command scrolling

The navigation gateway sets:

```text
idle → command
```

before moving the viewport.

Rich/virtual transitions operate independently of this scroll-source gate and may occur while either source is active.

### Throttling

The `scroll` listener does not walk DOM directly for every event.

```ts
let followFrame: number | null = null;

function scheduleScrollFollow() {
  if (followFrame !== null) {
    return;
  }

  followFrame = requestAnimationFrame(() => {
    followFrame = null;
    followScrollNow();
  });
}
```

This permits at most one geometry walk per animation frame.

### Completion

On `scrollend`:

1. perform or schedule one final scroll-follow sample;
2. complete that sample;
3. set the source to `idle`.

Instant command navigation may return to `idle` after its final settled animation frame if no longer-running scroll sequence exists.

No user/command source remains active indefinitely.

### User-scroll selection

During recognized user scrolling:

1. find the visible real hunk target at the reading line;
2. call `selectHunk` if it changed;
3. otherwise preserve current selection.

User-scroll following does not automatically select Husk or Lazy pseudo-hunks merely because their card crosses the reading line.

It never:

- scrolls;
- enriches;
- expands;
- fetches;
- updates virtualization state.

Layout movement while the source is `idle` never changes selection.

## 56. FileTree highlighting and targets

FileTree highlighting is derived from the selected hunk’s owning FileCard.

```text
selected hunk target
        │
        ▼
closest FileCard
        │
        ▼
matching FileTree row gets aria-current
```

There is no:

- selected-file state;
- `activeHunkFileId`;
- file-selection command.

When `selectHunk` changes ownership, it updates the derived FileTree projection.

When a destructive transition clears the last selection, FileTree has no highlighted file.

When FileTree opens:

1. find the FileCard containing selected-hunk attributes;
2. find the matching FileTree row;
3. apply `aria-current`;
4. reveal that row inside the FileTree scroll container.

Sidebar auto-reveal moves only the sidebar.

Clicking a FileTree file target:

1. unfolds it if necessary;
2. resolves its first hunk or pseudo-hunk;
3. dispatches ordinary hunk navigation.

Directory rows only control directory expansion. They do not navigate the page.

## 57. Line pins and restoration

Line-pin restoration keeps its current behavioral and implementation shape.

There is no standard browser API that reliably keeps one exact line at one viewport position while its file loads, enriches, folds, and repeatedly changes layout.

Native fragment navigation and `scrollIntoView` are one-shot operations. CSS scroll anchoring lets the browser choose an anchor and may help opportunistically, but it does not let the application nominate the pinned line or guarantee continued restoration.

Therefore line pins retain:

- the current URL pin representation;
- the current pin-highlighting behavior;
- repeated restoration as files load, enrich, and change layout;
- the ability to re-scroll until the rendered target stabilizes;
- their own restoration controller;
- their own authorized viewport-moving path.

Line-pin restoration is intentionally not forced through the one-shot hunk navigation gateway.

It remains isolated from hunk selection:

- restoring a pin never selects or reselects a hunk;
- pin retries never update hunk counters;
- pin retries never update FileTree highlighting;
- hunk reconciliation never calls pin restoration.

The initial frontend rewrite may mechanically adapt pin restoration to new component names and `waitToEnrich`, but it must not redesign or simplify away its polling, retry, or stabilization behavior without a separate investigation demonstrating equivalent real-world behavior.

Line-pin restoration may be revisited only if:

- browsers expose a genuinely controllable persistent-anchor API; or
- a separate, browser-verified stabilization design proves at least as reliable as the current implementation.

CSS scroll anchoring remains relevant to the rich/virtual heuristic, but it is not a replacement for line-pin restoration.

## 58. Browser text-side selection

The existing side-selection behavior remains visually and functionally intact.

```html
<div
  class="diff-grid"
  data-diff-selection-side="left"
></div>
```

One delegated pointer handler:

- determines whether pointer-down occurred on the left or right;
- records the side on that DiffGrid;
- removes the previous side marker;
- clears it when selection begins outside a diff side.

No Solid signal is needed.

This state is independent from:

- hunk selection;
- line pins;
- inline/split workspace view.

## 59. Hint HUD, Help, and Debug

The Hint HUD, Help modal, and Debug HUD remain visually exactly as they are.

This includes:

- the existing Hint HUD buttons and labels;
- the existing Help modal layout and content, except for removal of the Show All and Fold All rows;
- the existing Debug HUD layout;
- FPS;
- whole-document node count;
- whole-document span count;
- the hunk counter.

The Show All and Fold All controls in the ChangeSet title area are removed. No other visual redesign is part of this architecture.

### Debug implementation

Debug retains:

```ts
type DebugMetrics = {
  fps: string;
  nodes: string;
  spans: string;
  hunks: string;
};
```

It continues to calculate:

```ts
document.querySelectorAll("*").length;
document.querySelectorAll("span").length;
```

The implementation improvement is lifecycle-only:

```tsx
<Show when={debugOpen()}>
  <DebugHud />
</Show>
```

Mounting starts its sampler.

Unmounting cancels it.

When Debug is closed:

- no RAF runs;
- no DOM counts run;
- no metric signals update.

Its hunk value is read from the DOM-derived counter rather than a `HunkPosition` signal.

Debug observes. It never:

- selects;
- scrolls;
- enriches;
- fetches;
- repairs.

## 60. Hotkeys

One private `Hotkeys` lifecycle component is mounted only for the active Tab. It owns the application’s single hotkey listener and calls concrete owner operations directly.

There is no generic hotkey command, parser, router, dispatch function, registry, or grouped owner interface. `NavigationCommand` remains the explicit typed input to `navigation.navigate(...)`; it does not form a generic application command system.

The mappings are:

| Key | Operation |
|---|---|
| `n` | navigate to the next hunk |
| `N` | navigate to the previous hunk |
| `p` | navigate to the top |
| `t` | toggle the active ChangeSet FileTree |
| `i` | toggle the workspace inline/split view |
| `r` | reload the active ChangeSet |
| `d` | toggle Debug |
| `h` | toggle Help |

The `s` and `f` hotkeys do not exist in the rewrite. Show All and Fold All are removed rather than routed elsewhere.

Editable targets, modified shortcuts, and already-prevented events retain their native behavior. A recognized hotkey calls `preventDefault()` before invoking its concrete operation.

Inactive Tabs retain their DOM but do not mount a hotkey listener. Buttons call their actual owner operations directly. Sections 66.24–66.26 specify the exact lifecycle and ignored-input behavior.

## 61. Ownership summary

| Concern | Authority |
|---|---|
| File order | Manifest order reflected by FileCard DOM |
| Real hunk identity | Backend file/hunk index on DOM target |
| Husk pseudo-hunk | HuskFile DOM target |
| Lazy pseudo-hunk | LazyFile plank DOM target |
| Navigation order | Participating hunk-target DOM order |
| Selected hunk identity | Attributes on its owning FileCard |
| Visible selection | Matching current hunk target |
| FileTree highlight | Projection from selected hunk’s FileCard |
| Local/global counters | DOM-derived imperative header projections |
| Rich/virtual mode | FullFile-local Solid signal |
| Virtualization trigger | Local/shared IntersectionObserver heuristic |
| Measured height | FullFile-local DOM measurement |
| Hunk, FileTree-target, and top scrolling | Main navigation gateway |
| Scroll source | Navigation-local ephemeral variable |
| Line pin | Current URL representation, rendered projection, and restoration controller |
| Text-selection side | DiffGrid DOM attribute |
| Help visibility | Existing local HUD signal |
| Debug visibility | Existing local HUD signal |
| Debug metrics | Existing visual model, sampled only while open |
| Keyboard mapping | Pure command parser |
| Keyboard execution | Delegation to actual owners |

## 62. Concepts removed

The rewrite removes:

- `currentIdentity` as a Solid selection authority;
- `HunkPosition` application state;
- selected-file state;
- `activeHunkFileId`;
- `forcedRichFileIds`;
- `virtualizedFileIds`;
- rich preload radius;
- layout revision;
- virtualization revision;
- loading revision as a navigation dependency;
- delayed hunk reconciliation timers;
- FileTree animation-frame stabilization loops;
- file navigation commands;
- directory navigation commands;
- always-running Debug RAF;
- application-level counters updated by virtualization.

The rewrite retains:

- one HuskFile pseudo-hunk;
- one expanded LazyFile pseudo-hunk;
- DOM-selected hunk state;
- destructive structural selection repair;
- throttled user-scroll following;
- a small scroll-source gate;
- `waitToEnrich`;
- local selected-decoration projection;
- the current HUD, Help, and Debug visuals;
- current browser text-side selection.

## 63. Required invariants

1. FileCard DOM order equals manifest order.
2. Every HuskFile exposes exactly one pseudo-hunk.
3. Every expanded LazyFile exposes exactly one pseudo-hunk.
4. Every folded file exposes no hunk target.
5. Every expanded FullFile exposes its participating real-hunk targets.
6. Rich and virtual representations expose identical real-hunk identities.
7. Inline and split rich representations expose identical real-hunk identities.
8. VirtualFile always contains complete old and new text in split form.
9. VirtualFile does not depend on global inline/split view.
10. At most one FileCard contains selected-hunk identity.
11. When mounted, exactly one target matches and projects selected identity.
12. No independent selected-file state exists.
13. FileTree highlighting derives only from selected hunk ownership.
14. Rich ↔ virtual changes no non-local state.
15. Rich ↔ virtual does not update counters.
16. Representation projection never selects or scrolls.
17. A destructive owner repairs selected-hunk state before removing its target.
18. Structural repair uses `selectHunk` or clears selection.
19. Folding never leaves selection pointing at a removed target.
20. Hunk navigation never changes backend request order.
21. Selecting a pseudo-hunk never starts its request.
22. Only explicit LazyFile activation starts lazy hydration.
23. Only the main navigation gateway and line-pin restoration controller intentionally move the page viewport.
24. FileTree navigation resolves to ordinary first-hunk navigation.
25. Directory rows do not navigate the page.
26. Scroll-follow performs at most one DOM walk per animation frame.
27. The final scroll-follow sample completes before returning to `idle`.
28. Automatic rich/virtual transitions may run during active scrolling, but virtualization never invokes selection or scrolling.
29. Layout changes while scroll source is `idle` never alter selection.
30. Browser scroll anchoring is preserved but not treated as a correctness guarantee.
31. Height preservation is best effort.
32. Counters change only through selection or structural target changes.
33. Debug retains FPS, Nodes, Spans, and Hunks visually.
34. Closed Debug performs no sampling.
35. Hint HUD and Debug HUD remain visually unchanged. Help remains visually unchanged except for removal of the Show All and Fold All rows.
36. Browser text-side selection remains intact.
37. Repository changes, F5, and other workspace replacement boundaries clear selection by replacing ChangeSet DOM.
38. Future notebook region keys extend identity without making global counter position part of identity.

## 64. Toasts and error containment

### 64.1 Purpose

Errors must be impossible to miss without allowing one damaged region to destroy unrelated UI.

The governing rules are:

1. Every real error is presented dramatically.
2. Damage stops at the smallest owner whose correctness can no longer be trusted.
3. The damaged owner presents the complete error locally.
4. The same failure also produces one global Toast.
5. The program never silently retries, substitutes data, hides the failure or pretends that the operation succeeded.
6. The user may explicitly retry, reload, change inputs, switch Tabs or otherwise replace the damaged owner.

A user-controlled retry is not automatic recovery.

```text
failure
├── global visibility
│   └── Error Toast
└── localized damage
    └── complete local ErrorPanel
        └── RetryButton
```

### 64.2 Non-errors

The following are not errors and do not produce Toasts:

- intentional TanStack Query cancellation;
- a result discarded because its owner was intentionally replaced;
- ordinary input validation, such as an empty required PR URL;
- content intentionally represented by a normal LazyFile reason;
- unavailable autocomplete data while its query is still pending.

Validation remains prominently local to the relevant input or action.

Cancellation remains silent because the application intentionally requested it.

### 64.3 Exact Toast behavior

Toasts remain visually and behaviorally the same as the current implementation.

There is one global error-only Toast queue.

Every Error Toast contains:

- a title;
- a formatted primary message;
- optional expandable details containing the stack trace;
- a manual dismiss button.

Toast behavior remains:

- Toasts appear in insertion order.
- New Toasts are appended after existing Toasts.
- There is no success, information or warning Toast.
- There is no maximum Toast count.
- There is no automatic provider-level deduplication.
- The viewport is fixed at the bottom-right.
- The viewport grows upward and becomes vertically scrollable when necessary.
- Individual Toast message and detail regions remain independently scrollable.
- Every non-timeout Toast remains until the user dismisses it.
- A timeout Toast is automatically dismissed after 10 seconds.
- Manual dismissal works for every Toast.
- The details section is collapsed initially.
- The Toast viewport uses an assertive live region.
- Every Toast uses `role="alert"`.

```ts
export type ErrorToast = {
  id: number;
  title: string;
  message: string;
  details: string | null;
  reason: "timeout" | "other";
};
```

There is no generic `ToastTone`. Every Toast is an error.

### 64.4 Error formatting

The current formatting behavior remains.

Primary error formatting follows this order:

1. An object with an array-valued `issues` field displays those issues as formatted JSON.
2. An `Error` displays its message.
3. If an Error message contains valid JSON text, that JSON is formatted.
4. A string is displayed directly, unless it contains valid JSON text.
5. Other values use formatted JSON.
6. Values that cannot be JSON-serialized use `String(value)`.

Details are:

- `error.stack` for an `Error` when the stack differs from the primary message;
- otherwise absent.

Formatting functions are pure:

```ts
export type PresentedError = {
  message: string;
  details: string | null;
  reason: "timeout" | "other";
};

export function presentError(
  error: unknown,
): PresentedError;
```

Formatting must never throw while trying to present the original error.

### 64.5 Toast ownership

`ToastProvider` is global infrastructure.

It owns:

- the Toast queue;
- monotonically increasing Toast IDs;
- insertion;
- dismissal;
- global browser error listeners.

Its public context contains commands only:

```ts
export type ToastCommands = {
  showError(
    title: string,
    error: unknown,
  ): void;
};
```

Consumers do not receive:

- the Toast signal;
- the Toast setter;
- `dismissToast`;
- generic queue mutation;
- generic Toast construction.

```ts
const ToastContext =
  createContext<ToastCommands>();

export function useToasts(): ToastCommands {
  const value = useContext(ToastContext);

  if (value === undefined) {
    throw new Error(
      "useToasts requires ToastProvider.",
    );
  }

  return value;
}
```

A global Context is appropriate because error reporting is application-wide infrastructure used throughout the component tree. Throwing from `useToasts` when the Provider is missing follows Solid’s documented Context pattern and prevents a missing Provider from being silently ignored. [Solid Context documentation](https://docs.solidjs.com/concepts/context)

### 64.6 Provider composition

`ToastProvider` contains both the application children and `ToastViewport`.

```tsx
export function ToastProvider(props: {
  children: JSX.Element;
}) {
  const [toasts, setToasts] =
    createSignal<ErrorToast[]>([]);

  let nextToastId = 1;

  function showError(
    title: string,
    error: unknown,
  ): void {
    const presented = presentError(error);

    setToasts((current) => [
      ...current,
      {
        id: nextToastId++,
        title,
        ...presented,
      },
    ]);
  }

  function dismissToast(id: number): void {
    setToasts((current) =>
      current.filter((toast) => toast.id !== id),
    );
  }

  return (
    <ToastContext.Provider value={{ showError }}>
      {props.children}
      <ToastViewport
        toasts={toasts}
        onDismiss={dismissToast}
      />
    </ToastContext.Provider>
  );
}
```

A signal is sufficient because the queue is replaced as one immutable array. A Solid store is unnecessary.

The Toast viewport remains outside the application’s root ErrorBoundary so it survives a root application error:

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

<ToastProvider>
  <Root />
</ToastProvider>
```

`Root` is a private composition component in `main.tsx`. It exists because `useToasts()` must run below `ToastProvider`, while `QueryProvider` must receive error reporting without importing `comp/Toasts.tsx` into `api/queryClient.ts`.

`fallback` above is the name of Solid’s `ErrorBoundary` prop. No project component is named ErrorFallback or RecoverableErrorFallback.

### 64.7 Toast expiration

A timeout belongs to the rendered Toast that expires.

The provider does not maintain a parallel timer map.

```tsx
function ToastCard(props: {
  toast: ErrorToast;
  onDismiss: () => void;
}) {
  onMount(() => {
    if (props.toast.reason !== "timeout") {
      return;
    }

    const timer = window.setTimeout(
      props.onDismiss,
      10_000,
    );

    onCleanup(() => {
      window.clearTimeout(timer);
    });
  });

  return (
    // Existing Toast markup.
  );
}
```

This preserves the exact timeout behavior:

- mounting a timeout Toast starts its timer;
- manual dismissal unmounts it and clears the timer;
- provider disposal clears every mounted Toast timer;
- non-timeout Toasts create no timer.

`onCleanup` binds the external timer to the lifetime of the Toast component. [Solid `onCleanup` documentation](https://docs.solidjs.com/reference/lifecycle/on-cleanup)

### 64.8 Query and mutation errors

TanStack Query remains the authority for backend query and mutation error state.

Every query and mutation definition provides an error title:

```ts
type ErrorMeta = {
  errorTitle: string;
};
```

```ts
queryOptions({
  queryKey: ["repos", projectId, "refs"],
  queryFn: ({ signal }) =>
    requestRepoRefs(projectId, signal),
  meta: {
    errorTitle: "Failed to load refs",
  } satisfies ErrorMeta,
});
```

The QueryClient uses `QueryCache` and `MutationCache` error callbacks to produce one Toast for each failed query or mutation attempt:

```tsx
export function QueryProvider(props: {
  children: JSX.Element;
  onError(
    title: string,
    error: unknown,
  ): void;
}) {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({
      onError(error, query) {
        if (isCancelledError(error)) {
          return;
        }

        props.onError(
          errorTitle(query.meta),
          error,
        );
      },
    }),

    mutationCache: new MutationCache({
      onError(error, _variables, _result, mutation) {
        props.onError(
          errorTitle(mutation.meta),
          error,
        );
      },
    }),

    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      mutations: {
        retry: false,
      },
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      {props.children}
    </QueryClientProvider>
  );
}
```

`QueryProvider` does not import or call `useToasts`. Its required callback is supplied by the private `Root()` component in `main.tsx`.

Cache-level error callbacks are used because one backend query may have several observers. A cache callback reports the failed query once rather than requiring every observer to synchronize the same error into Toast state. TanStack exposes `meta` on query definitions and global error callbacks on `QueryCache` and `MutationCache` for this purpose. [Solid Query options](https://tanstack.com/query/latest/docs/framework/solid/reference/useQuery), [QueryCache](https://tanstack.com/query/latest/docs/reference/QueryCache), [MutationCache](https://tanstack.com/query/latest/docs/reference/MutationCache)

Components do not use `createEffect` merely to copy query errors into Toasts.

Components continue to read query or mutation error state to render their local damage.

A failed attempt creates exactly one Toast even if several components observe the same query.

A user-triggered retry is a new attempt. If it fails, it creates a new Toast.

### 64.9 Local query damage

A query failure damages only the query’s actual owner.

| Failure | Local result |
|---|---|
| repository list | RepoSelect and RepoGate show their error state |
| refs | affected autocomplete remains usable as free-form input and shows the refs error |
| repository defaults | affected default-dependent controls show the error without overwriting user input |
| preset catalogs | Preset controls show their error |
| preferences | Preferences UI shows its complete error |
| manifest | owning ChangeSet shows ErrorPanel; no FileSequence starts |
| lazy metadata | affected entries become error-flavoured LazyFiles; normal file loading continues |
| file query | that FileCard becomes an error-flavoured LazyFile; later files continue |
| mutation | triggering component displays its mutation error |

A FileSequence never stops because one file failed.

Every failed FileCard remains represented at its manifest position.

An error-flavoured LazyFile displays:

- its path;
- the complete formatted error;
- an open local stack trace when available;
- a RetryButton;
- its error styling.

It does not display partial FileBody content as if loading succeeded.

A refetch error with previously available data may retain that data only if the owner also displays an unmistakable error state. Old data must never make a failed refresh appear successful.

### 64.10 ErrorPanel

`ErrorPanel` is the complete local presentation of a failed owner.

```tsx
export function ErrorPanel(props: {
  title: string;
  error: unknown;
  children: JSX.Element;
}) {
  const presented = () =>
    presentError(props.error);

  return (
    <section class="notice error" role="alert">
      <strong>{props.title}</strong>

      <pre class="render-error-message">
        {presented().message}
      </pre>

      <Show when={presented().details}>
        {(details) => (
          <details class="error-traceback" open>
            <summary>Stack</summary>
            <pre>{details()}</pre>
          </details>
        )}
      </Show>

      {props.children}
    </section>
  );
}
```

Unlike the Toast details, the local ErrorPanel stack is open initially.

The ErrorPanel never:

- hides the error;
- substitutes empty data;
- renders the failed owner underneath itself;
- retries automatically;
- dismisses itself automatically.

### 64.11 RetryButton

Every user-controlled retry uses the explicit `RetryButton` component.

```tsx
export function RetryButton(props: {
  onRetry: () => void;
}) {
  return (
    <button
      type="button"
      onClick={props.onRetry}
    >
      Try again
    </button>
  );
}
```

`RetryButton` may invoke:

- `query.refetch()` for a query error;
- the same mutation with the same owner-held variables;
- `ErrorBoundary`’s `reset` function for an unexpected rendering error;
- a ChangeSet reload command where reload is the real user action.

The callback is always supplied. RetryButton has no generic default behavior.

The program never invokes `onRetry` itself.

### 64.12 Unexpected rendering and reactive errors

Solid ErrorBoundary contains unexpected errors thrown while rendering or reactively updating its subtree. It does not catch event-handler errors or unrelated scheduled callbacks. [Solid ErrorBoundary documentation](https://docs.solidjs.com/reference/components/error-boundary)

An unexpected error mounts `UnexpectedErrorPanel`:

```tsx
function UnexpectedErrorPanel(props: {
  title: string;
  error: unknown;
  onRetry: () => void;
}) {
  const toast = useToasts();

  onMount(() => {
    toast.showError(
      props.title,
      props.error,
    );
  });

  return (
    <ErrorPanel
      title={props.title}
      error={props.error}
    >
      <RetryButton
        onRetry={props.onRetry}
      />
    </ErrorPanel>
  );
}
```

`onMount` produces one Toast for that mounted failed attempt. It is not a synchronization effect and does not rerun because unrelated reactive values changed. [Solid `onMount` documentation](https://docs.solidjs.com/reference/lifecycle/on-mount)

If the user retries and the owner fails again:

1. the new attempt fails;
2. UnexpectedErrorPanel mounts again;
3. a new persistent Toast is appended;
4. the complete local error remains visible.

### 64.13 ErrorBoundary placement

Boundaries follow meaningful damage ownership.

```text
ToastProvider
├── Root
│   └── QueryProvider
│       └── Root ErrorBoundary
│           └── App
│               ├── AppHeader
│               ├── TabStrip
│               └── Tabs
│                   └── Tab ErrorBoundary
│                       ├── Controls
│                       └── ChangeSet ErrorBoundary
│                           ├── FileTree ErrorBoundary
│                           └── FileCards
│                               └── FileCard ErrorBoundary
└── ToastViewport
```

The nearest boundary owns the damage:

- A FileBody or FileHeader exception replaces only that FileCard.
- A FileTree exception replaces only FileTree.
- A ChangeSet-wide exception replaces only that ChangeSet.
- A Tab workflow exception replaces only that Tab’s content.
- An App or workspace exception replaces the App.
- TabStrip remains available when one Tab fails.
- Other Tabs remain available when one Tab fails.
- Other FileCards remain available when one FileCard fails.
- ToastViewport remains available when the App fails.

There is no boundary inside FileBody merely to preserve a partially rendered file. FileCard is the smallest trustworthy file-rendering unit.

Portalled AppHeader contributions remain logically owned by ChangeSet and are caught by the ChangeSet boundary despite their physical DOM location.

### 64.14 Root application error

A root application error preserves the current full-page presentation:

- the App is replaced;
- the page shows “Something broke”;
- the complete formatted error is visible;
- the stack is open when available;
- RetryButton is available;
- a persistent “Application error” Toast is added;
- ToastViewport remains usable.

No other application UI is trusted after a root error.

### 64.15 Browser-level errors

ToastProvider retains:

```ts
window.addEventListener("error", onError);
window.addEventListener(
  "unhandledrejection",
  onUnhandledRejection,
);
```

Behavior remains:

- `window.error` produces “Unexpected error”;
- `unhandledrejection` produces “Unhandled promise rejection”;
- both create persistent Error Toasts;
- neither event is suppressed with `preventDefault`;
- browser console reporting remains intact;
- listeners are removed when ToastProvider is disposed.

These listeners are the final visibility boundary for errors outside Solid’s rendering and reactive-update ownership.

They are not the normal path for query or mutation failures.

Every `mutateAsync` or other intentionally awaited Promise must be handled by its owner. Allowing a handled mutation failure to reach `unhandledrejection` and create a duplicate Toast is a bug.

### 64.16 Prohibited error handling

The rewrite must not contain programmer-controlled recovery such as:

```ts
try {
  return await requestData();
} catch {
  return emptyData;
}
```

It must not:

- catch an error and only log it;
- catch an error and return `null` as if nothing failed;
- replace invalid backend data with defaults;
- continue rendering a FileBody after its required data failed validation;
- automatically call RetryButton actions;
- automatically reset an ErrorBoundary;
- automatically retry queries or mutations;
- show a success state while hiding a refetch error;
- maintain copied error signals outside the actual query, mutation or damaged UI owner;
- Toast the same failed attempt once from TanStack Query and again from an observer;
- throw a handled mutation rejection into the global unhandled-rejection listener.

Every `catch` must do at least one of:

- recognize intentional cancellation;
- convert invalid user input into an explicit validation result;
- place the actual owner into an explicit error state;
- rethrow to the nearest meaningful ErrorBoundary.

### 64.17 Required invariants

1. Every real error is visible locally or terminates its local owner.
2. Every real error produces exactly one Error Toast per failed attempt.
3. Cancellation produces no Toast.
4. Input validation is local and is not represented as an application error.
5. Timeout Toasts expire after 10 seconds.
6. Non-timeout Toasts persist until user dismissal.
7. ToastViewport survives a root App error.
8. A FileCard error does not remove other FileCards.
9. A FileTree error does not remove FileCards.
10. A ChangeSet error does not remove other Tabs.
11. A Tab error does not remove TabStrip or other Tabs.
12. A root error replaces the App but not ToastViewport.
13. Query and mutation errors remain owned by TanStack Query.
14. Cache-level callbacks prevent duplicate Toasts from multiple query observers.
15. Local components render query and mutation error state without copying it.
16. ErrorPanel displays the complete formatted error.
17. Local stack details are open initially.
18. Toast stack details are collapsed initially.
19. Every user retry is rendered through RetryButton.
20. RetryButton is never invoked automatically.
21. ErrorBoundary reset occurs only through explicit user action.
22. A repeated failed retry produces a new Toast.
23. No programmer-controlled default or placeholder conceals an error.
24. Global browser error listeners remain installed while ToastProvider is mounted.
25. Global browser listeners do not replace normal query, mutation or boundary ownership.

## 65. Component and module architecture

I’d use a shallow, component-owner structure: a file represents a substantial owner, not every JSX component. Small supporting components remain private in their owner’s file.

### 65.1 Three viable shapes

#### A. Flat owner modules — recommended

```text
frontend/src/
├── api/
│   ├── api.ts
│   └── queryClient.ts
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

#### `api/queryClient.ts`

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

This slightly refines the earlier hypothetical claim that `queryClient.ts` exports only a configured singleton.

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
- stable Portal targets for ChangeSet status and summary.

It does not own manifest statistics or loading progress. ChangeSet supplies those through Portals.

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
- strict FileSequence;
- combined progress;
- expansion state;
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

- observing its canonical file query;
- deriving Husk/Full/Lazy state;
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
| `queryClient.ts` at root | `api/queryClient.ts` |
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

## 66. Navigation and hotkeys clarification

### 66.1 Terminology

`navigation` means the subsystem responsible for:

- hunk selection;
- hunk traversal;
- main-page scrolling;
- user-scroll following;
- FileTree hunk destinations;
- line pins and their repeated restoration.

`NavigationCommand` is the explicit typed input to the main navigation gateway.

Hotkeys are direct keyboard bindings. They are not commands, a command system, or a generic dispatch framework.

There will be no:

- generic `Command` type;
- `CommandProvider`;
- `CommandRouter`;
- `dispatchCommand`;
- command registry;
- owner registration;
- `HudActions`;
- command metadata framework;
- `commands.ts`.

`hud/` remains the directory containing project-aware interface code. It is not a shared runtime owner.

### 66.2 Files

Navigation lives in:

```text
frontend/src/hud/navigation.tsx
```

The `.tsx` extension is required because the module contains `NavigationProvider`.

There will be no:

```text
hud/HunkNavigation.tsx
hud/Hud.tsx
hud/commands.ts
```

`navigation.tsx` exports:

```ts
export type RealHunkIdentity;
export type PseudoHunkIdentity;
export type HunkIdentity;
export type NavigationCommand;
export type Navigation;

export function NavigationProvider(
  props: NavigationProviderProps,
): JSX.Element;

export function useNavigation(): Navigation;
```

Hotkey handling remains private to `ChangeSet.tsx`.

`HintHud` and `DebugHud` are private components in `ChangeSet.tsx`. Their definitions are adjacent, and their rendered elements are adjacent inside the HUD stack.

`HelpModal` is also private to `ChangeSet.tsx`, but it is defined separately and rendered outside the HUD stack. It is not interspersed between `HintHud` and `DebugHud` in either source or rendered TSX.

### 66.3 Hunk identities

Real hunks retain the backend-produced file-local identity:

```ts
export type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};
```

HuskFile and LazyFile pseudo-hunks retain their provisional identity:

```ts
export type PseudoHunkIdentity = {
  fileIndex: number;
  kind: "husk" | "lazy";
  entryDirection: 1 | -1;
};
```

```ts
export type HunkIdentity =
  | RealHunkIdentity
  | PseudoHunkIdentity;
```

`entryDirection` determines how a selected pseudo-hunk maps when its resulting FullFile becomes available:

- `1` maps to the first participating real hunk;
- `-1` maps to the last participating real hunk;
- direct FileTree or plank entry uses `1`.

Global positions such as `9/42` remain derived display values. They are never identity.

### 66.4 NavigationCommand

The only application-level use of the word `Command` is:

```ts
export type NavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | {
      kind: "hunk";
      hunk: HunkIdentity;
    }
  | { kind: "top" };
```

`NavigationCommand` exists because it is the explicit typed argument to the single ordinary-navigation gateway:

```ts
await navigation.navigate({
  kind: "next-hunk",
});
```

It does not represent:

- hotkeys;
- view changes;
- Help;
- Debug;
- reload;
- tree visibility;
- file expansion;
- backend work.

There are no file or directory navigation commands.

### 66.5 Public Navigation interface

```ts
export type Navigation = {
  navigate(
    command: NavigationCommand,
  ): Promise<void>;

  selectHunk(
    hunk: HunkIdentity,
  ): void;

  clearHunkSelection(): void;
};
```

`navigate` is the only ordinary hunk-navigation operation that moves the main page viewport.

`selectHunk` changes DOM selection and projections without scrolling.

`clearHunkSelection` clears DOM selection and projections without scrolling.

The interface does not expose:

- the ChangeSet root;
- controller state;
- scroll-source state;
- listener handles;
- hunk targets;
- selected identity;
- counters;
- line-pin timers;
- generic setters.

### 66.6 NavigationProvider purpose

`NavigationProvider` owns one stateful, disposable navigation controller for one mounted ChangeSet.

It gives descendants:

1. the same navigation instance;
2. operations already bound to the correct ChangeSet root;
3. one shared scroll-follow and line-pin lifecycle.

Without Context, ChangeSet would pass the same instance explicitly:

```tsx
<FileTree navigation={navigation} />
<FileCards navigation={navigation} />
<HintHud navigation={navigation} />
<Hotkeys navigation={navigation} />
```

With Context:

```tsx
<NavigationProvider root={() => root}>
  <FileTree />
  <FileCards />
  <HintHud />
  <Hotkeys />
</NavigationProvider>
```

Each consumer receives the nearest ChangeSet navigation instance:

```ts
const navigation = useNavigation();
```

Context changes delivery only. It does not make navigation global and does not move navigation truth out of the DOM.

### 66.7 Provider props

```ts
export type NavigationProviderProps = {
  root: Accessor<HTMLElement>;
  children: JSX.Element;
};
```

The root is required.

`NavigationProvider` must assert that the root exists when mounting its controller. It must not silently operate against `document` when the ChangeSet root is unavailable.

The Provider accepts no:

- workspace operations;
- Help operations;
- Debug operations;
- ChangeSet operations;
- hotkey definitions;
- backend data;
- optional handlers.

### 66.8 Navigation controller state

The controller is stateful.

Its state is imperative and ephemeral rather than reactive application state.

Conceptually:

```ts
type NavigationScrollSource =
  | "idle"
  | "user"
  | "navigation";
```

```ts
type PinRestorationState = {
  retryTimer: number | null;
  restoredKey: string;
};
```

```ts
type NavigationControllerState = {
  root: HTMLElement;

  scrollSource: NavigationScrollSource;

  followFrame: number | null;

  pinRestoration: PinRestorationState;
};
```

The actual line-pin implementation may require additional retry or stabilization handles. Those remain private controller fields.

The controller also owns the browser listener and observer cleanup associated with that state.

It does not use Solid signals for:

- `scrollSource`;
- `followFrame`;
- retry timers;
- the selected hunk;
- hunk counters.

These values do not drive ordinary JSX rendering.

### 66.9 Why the controller state is shared

Programmatic navigation and user-scroll following must coordinate through the same `scrollSource`.

Programmatic navigation:

```text
navigate(...)
    │
    ▼
scrollSource = "navigation"
    │
    ▼
select target
    │
    ▼
possibly waitToEnrich
    │
    ▼
scroll viewport
    │
    ▼
scroll events occur
    │
    ▼
scroll-follow recognizes navigation-owned movement
    │
    ▼
final settled frame
    │
    ▼
scrollSource = "idle"
```

Natural user scrolling:

```text
wheel, touch or native scrolling key
    │
    ▼
scrollSource = "user"
    │
    ▼
many scroll events occur
    │
    ▼
one shared followFrame throttles DOM traversal
    │
    ▼
scrollend
    │
    ▼
final follow sample
    │
    ▼
scrollSource = "idle"
```

Without one controller instance, these paths would still require shared mutable state somewhere.

The Provider gives that state one explicit ChangeSet-scoped lifetime.

### 66.10 Navigation truth remains in DOM and URL

The controller does not become a navigation store.

| Information | Authority |
|---|---|
| selected hunk identity | attributes on the owning FileCard |
| participating target order | current DOM order |
| visible selected decoration | current target DOM |
| local/global hunk counters | imperative DOM projection |
| FileTree highlight | projection from selected FileCard |
| rich/virtual mode | FullFile-local Solid state |
| FileCard expansion | ChangeSet-owned Solid state |
| line-pin identity | URL |
| line-pin visual highlight | rendered DOM |
| scroll coordination | Navigation controller |
| line-pin retries | Navigation controller |

The controller may temporarily hold DOM references during one operation. It must not retain a second authoritative hunk registry.

### 66.11 NavigationProvider lifecycle

The Provider is mounted only with active ChangeSet content.

On mount it:

1. resolves and asserts the ChangeSet root;
2. constructs one controller;
3. attaches navigation-related browser listeners;
4. starts line-pin restoration when a URL pin exists;
5. exposes the controller’s public Navigation operations through Context.

On cleanup it:

- cancels the scheduled scroll-follow frame;
- cancels every line-pin timer and retry;
- removes wheel, touch, scroll, scrollend, hash and pointer listeners;
- disconnects navigation-owned observers;
- marks the controller disposed;
- permits no later scheduled callback to mutate DOM or scroll.

Inactive ChangeSets retain their small outer component state, but their expensive active content and NavigationProvider are unmounted.

Therefore there is exactly one active:

- navigation controller;
- user-scroll follower;
- line-pin restoration controller;
- hotkey listener.

### 66.12 Context accessor

```ts
const NavigationContext =
  createContext<Navigation>();
```

```ts
export function useNavigation(): Navigation {
  const navigation =
    useContext(NavigationContext);

  if (navigation === undefined) {
    throw new Error(
      "useNavigation requires NavigationProvider.",
    );
  }

  return navigation;
}
```

`useNavigation()` is a checked Solid Context accessor.

It does not:

- create another controller;
- subscribe to navigation state;
- return a setter;
- copy DOM state into Solid;
- install listeners.

### 66.13 Hunk selection

The Provider-bound operation is equivalent to:

```ts
function selectHunk(
  root: HTMLElement,
  hunk: HunkIdentity,
): void;
```

It:

1. resolves the current target for the identity;
2. removes selected identity from the previous FileCard;
3. removes previous target decoration;
4. writes selected identity onto the new owning FileCard;
5. decorates the resolved target;
6. updates local and global counters;
7. updates FileTree highlighting;
8. does not scroll.

```ts
function clearHunkSelection(
  root: HTMLElement,
): void;
```

It:

- removes selected identity;
- removes selected decoration;
- clears FileTree highlighting;
- updates counters;
- does not choose a replacement;
- does not scroll.

Only the previously approved paths may select or reselect a hunk:

1. Next/Previous navigation.
2. FileTree navigation to a file’s first hunk.
3. recognized user-scroll following;
4. Husk or Lazy plank activation;
5. destructive structural repair;
6. explicitly approved future line-pin selection behavior.

Line-pin restoration currently does not select.

### 66.14 Main navigation gateway

The Provider-bound `navigate` operation is equivalent to:

```ts
async function navigate(
  root: HTMLElement,
  command: NavigationCommand,
): Promise<void>;
```

For Next and Previous:

```text
read selected identity from FileCard DOM
    │
    ▼
query participating targets in DOM order
    │
    ▼
choose adjacent target with wrapping
    │
    ▼
selectHunk
    │
    ▼
waitToEnrich when real geometry is required
    │
    ▼
resolve the target again
    │
    ▼
perform one main-page scroll
```

For a direct hunk:

```text
resolve supplied HunkIdentity
    │
    ▼
selectHunk
    │
    ▼
waitToEnrich when required
    │
    ▼
resolve again
    │
    ▼
perform one main-page scroll
```

For Top:

```text
preserve selected identity
    │
    ▼
scroll main viewport to zero
```

Outside line-pin restoration, no other code may call main-page:

- `window.scrollTo`;
- `scrollIntoView`;
- `scrollBy`.

### 66.15 Pseudo-hunks

Next and Previous traverse the current provisional target sequence:

- real hunks;
- Husk pseudo-hunks;
- expanded Lazy pseudo-hunks.

Selecting a Husk or Lazy pseudo-hunk:

- updates selection and counters;
- may scroll to that pseudo-hunk;
- does not fetch;
- does not enrich;
- does not alter FileSequence order.

Only activating a LazyFile plank starts its explicit canonical file request.

### 66.16 FileTree interaction

FileTree obtains Navigation through Context:

```ts
const navigation = useNavigation();
```

Clicking a file row:

1. unfolds the file when required;
2. resolves that file’s first participating hunk;
3. invokes ordinary navigation.

```ts
void navigation.navigate({
  kind: "hunk",
  hunk,
});
```

FileTree does not ask Navigation to navigate to a file.

Directories only change directory expansion.

Opening FileTree:

1. reads the selected FileCard from DOM;
2. finds the corresponding FileTree row;
3. applies `aria-current`;
4. reveals it inside the FileTree’s own scroll container.

That sidebar scroll does not move the main page.

### 66.17 FileCard interaction

FileCard obtains Navigation only where structural behavior requires it.

A structural owner may call:

```ts
navigation.selectHunk(replacement);
```

or:

```ts
navigation.clearHunkSelection();
```

before removing the selected target.

This applies to:

- selected HuskFile becoming FullFile;
- selected LazyFile becoming FullFile;
- folding a selected file;
- folding a directory containing selection;
- code-fold participation removing selection;
- future notebook region replacement.

Representation-only rich/virtual or split/inline replacement does not call Navigation.

### 66.18 FileCard-local representation operations

These remain FileCard-owned:

```ts
function projectSelectedHunk(
  fileCard: HTMLElement,
): void;
```

```ts
async function waitToEnrich(
  fileCard: HTMLElement,
): Promise<void>;
```

`projectSelectedHunk` restores decoration for an identity that remains on the FileCard.

It never:

- selects;
- clears;
- updates counters;
- updates FileTree;
- scrolls;
- fetches.

`waitToEnrich` changes only FullFile-local rendering and resolves after rich FileBody materialization and projection.

Navigation may request `waitToEnrich` for a particular FileCard through the approved scoped DOM event or callback capability.

Navigation does not receive the FullFile render-mode signal.

### 66.19 Line pins

Line-pin functionality belongs in `navigation.tsx` because it is the second authorized main-page viewport-moving system.

It remains internally separate from `NavigationCommand` and `navigate`.

```text
NavigationCommand
    → one-shot hunk navigation
    → may select a hunk

line-pin restoration
    → repeated viewport stabilization
    → never selects a hunk
```

Line-pin identity remains encoded in the URL.

The controller retains:

- current URL parsing and writing;
- pin highlighting;
- repeated restoration while files load and enrich;
- retry and stabilization timers;
- the ability to move the viewport repeatedly until stable.

Line-pin restoration never:

- calls `selectHunk`;
- changes hunk counters;
- changes FileTree highlighting;
- changes FileSequence order;
- creates a `NavigationCommand`.

### 66.20 Browser text-side selection

Browser left/right text selection remains outside Navigation.

It belongs with DiffGrid interaction:

```html
<div
  class="diff-grid"
  data-diff-selection-side="left"
></div>
```

It uses one delegated pointer handler and no Solid signal.

It is independent from:

- hunk selection;
- line pins;
- inline/split workspace view.

### 66.21 HintHud and DebugHud source placement

`HintHud` and `DebugHud` are defined beside each other in `ChangeSet.tsx`:

```text
function HintHud(...)
function DebugHud(...)
function DebugMetric(...)

...

function HelpModal(...)
```

`HelpModal` may use its own private supporting components after its definition. It does not split the two HUD component definitions.

### 66.22 HintHud

The existing three-button visual component remains:

```tsx
type HintHudProps = {
  onToggleHelp: () => void;
};
```

```tsx
function HintHud(
  props: HintHudProps,
) {
  const navigation = useNavigation();

  return (
    <nav
      class="hunk-nav"
      aria-label="Hunk navigation"
    >
      <button
        type="button"
        onClick={() =>
          void navigation.navigate({
            kind: "next-hunk",
          })
        }
        title="Next hunk (n)"
      >
        Next <kbd>n</kbd>
      </button>

      <button
        type="button"
        onClick={() =>
          void navigation.navigate({
            kind: "previous-hunk",
          })
        }
        title="Previous hunk (N)"
      >
        Prev <kbd>N</kbd>
      </button>

      <button
        type="button"
        onClick={props.onToggleHelp}
        title="Hotkey help (h)"
      >
        Help <kbd>h</kbd>
      </button>
    </nav>
  );
}
```

Next and Previous use Navigation.

Help remains an explicit callback because Help visibility is not navigation.

### 66.23 Help and Debug state

Help and Debug remain independent ChangeSet-owned values:

```ts
const [helpOpen, setHelpOpen] =
  createSignal(false);
```

```ts
const [debugOpen, setDebugOpen] =
  createSignal(false);
```

They are not variants of one union and are not grouped under a HUD owner.

Debug sampling remains owned by `DebugHud` lifetime:

```tsx
<Show when={debugOpen()}>
  <DebugHud />
</Show>
```

Closed Debug performs no RAF sampling or DOM counting.

Help remains an overlay under `hud/`.

### 66.24 Hotkeys

Hotkeys are direct browser input bindings.

There is no intermediate `Command` or `Hotkey` union.

A private lifecycle component in `ChangeSet.tsx` owns the single active hotkey listener:

```ts
type HotkeysProps = {
  onToggleTree: () => void;
  onToggleView: () => void;
  onReload: () => void;
  onToggleHelp: () => void;
  onToggleDebug: () => void;
};
```

It receives concrete callbacks rather than grouped owner interfaces.

```tsx
function Hotkeys(
  props: HotkeysProps,
) {
  const navigation = useNavigation();

  onMount(() => {
    function onKeyDown(
      event: KeyboardEvent,
    ): void {
      if (shouldIgnoreHotkey(event)) {
        return;
      }

      if (
        event.code === "KeyN" &&
        !event.shiftKey
      ) {
        event.preventDefault();

        void navigation.navigate({
          kind: "next-hunk",
        });

        return;
      }

      if (
        event.code === "KeyN" &&
        event.shiftKey
      ) {
        event.preventDefault();

        void navigation.navigate({
          kind: "previous-hunk",
        });

        return;
      }

      if (event.code === "KeyP") {
        event.preventDefault();

        void navigation.navigate({
          kind: "top",
        });

        return;
      }

      if (event.code === "KeyT") {
        event.preventDefault();
        props.onToggleTree();
        return;
      }

      if (event.code === "KeyI") {
        event.preventDefault();
        props.onToggleView();
        return;
      }

      if (event.code === "KeyR") {
        event.preventDefault();
        props.onReload();
        return;
      }

      if (event.code === "KeyD") {
        event.preventDefault();
        props.onToggleDebug();
        return;
      }

      if (event.code === "KeyH") {
        event.preventDefault();
        props.onToggleHelp();
      }
    }

    document.addEventListener(
      "keydown",
      onKeyDown,
    );

    onCleanup(() => {
      document.removeEventListener(
        "keydown",
        onKeyDown,
      );
    });
  });

  return null;
}
```

This code is intentionally direct.

The mappings are:

| Key | Operation |
|---|---|
| `n` | `navigation.navigate({ kind: "next-hunk" })` |
| `N` | `navigation.navigate({ kind: "previous-hunk" })` |
| `p` | `navigation.navigate({ kind: "top" })` |
| `t` | toggle ChangeSet FileTree |
| `i` | toggle workspace inline/split view |
| `r` | reload ChangeSet |
| `d` | toggle Debug |
| `h` | toggle Help |

### 66.25 Ignored hotkeys

One predicate protects ordinary input behavior:

```ts
function shouldIgnoreHotkey(
  event: KeyboardEvent,
): boolean {
  if (
    event.defaultPrevented ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey
  ) {
    return true;
  }

  const target = event.target;

  if (!(target instanceof HTMLElement)) {
    return false;
  }

  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}
```

Shift is not rejected because `N` uses it.

The hotkey handler calls `preventDefault()` only after recognizing a supported hotkey.

Navigation may separately observe native browser scrolling keys to identify user scroll intent. That observer does not map application hotkeys and does not prevent their default behavior.

### 66.26 No generic hotkey dispatch

Buttons call their actual owner directly:

```text
HintHud Next
    → navigation.navigate

FileTree row
    → navigation.navigate

Header view control
    → workspace view setter

Reload button
    → ChangeSet reload

Help button
    → setHelpOpen

Debug button
    → setDebugOpen
```

The keyboard listener calls the same operations.

There is no central bus between the user interaction and the actual owner.

### 66.27 ChangeSet composition

```tsx
export function ChangeSet(
  props: ChangeSetProps,
) {
  const [helpOpen, setHelpOpen] =
    createSignal(false);

  const [debugOpen, setDebugOpen] =
    createSignal(false);

  let root!: HTMLElement;

  function toggleHelp(): void {
    setHelpOpen((open) => !open);
  }

  function toggleDebug(): void {
    setDebugOpen((open) => !open);
  }

  return (
    <section
      ref={root}
      data-change-set-root
    >
      <NavigationProvider
        root={() => root}
      >
        <Hotkeys
          onToggleTree={toggleTree}
          onToggleView={props.onToggleView}
          onReload={reload}
          onToggleHelp={toggleHelp}
          onToggleDebug={toggleDebug}
        />

        <ChangeSetTitle />

        <FileTree />

        <FileCards />

        <div class="hud-stack">
          <Show when={debugOpen()}>
            <DebugHud />
          </Show>

          <HintHud
            onToggleHelp={toggleHelp}
          />
        </div>

        <HelpModal
          open={helpOpen()}
          onClose={() =>
            setHelpOpen(false)
          }
        />
      </NavigationProvider>
    </section>
  );
}
```

The actual ChangeSet also renders the previously specified Header Portal contributions and error boundaries. They are omitted from this example because they do not interact with Navigation.

### 66.28 Show-all and fold-all removal

The rewrite removes the current `s` and `f` whole-file hotkeys:

```text
s → Show all files
f → Fold all files
```

It also removes the corresponding `ChangeSetTitle` controls and Help rows. The three-button `HintHud` remains visually unchanged.

Those aggregate operations do not follow naturally from the new FileCard/ChangeSet ownership model and do not survive merely for compatibility.

There are no replacement callbacks, dead key branches, compatibility handlers, unused variants, or invisible retained behavior.

### 66.29 Specification terminology corrections

Rename:

```ts
PageNavigationCommand
```

to:

```ts
NavigationCommand
```

Rename the scroll-source member:

```ts
"command"
```

to:

```ts
"navigation"
```

Section 60 is named:

```text
Hotkeys
```

There is no:

```ts
type Command;
type NavigationCommands;
type ChangeSetCommands;
type WorkspaceCommands;
type HudCommands;

function commandForKey(...);
function dispatchCommand(...);
```

The only remaining `Command` terminology is `NavigationCommand`.

### 66.30 Required invariants

1. Every active ChangeSet has exactly one Navigation controller.
2. NavigationController state is ephemeral and non-reactive.
3. Selected-hunk identity remains in FileCard DOM.
4. Participating navigation order remains current DOM order.
5. Line-pin identity remains in the URL.
6. `NavigationProvider` exposes operations but no controller state.
7. `useNavigation()` never constructs another controller.
8. `navigate` is the only ordinary hunk-navigation viewport mover.
9. Line-pin restoration is the only second authorized main-page viewport mover.
10. Line-pin restoration never selects a hunk.
11. `selectHunk` and `clearHunkSelection` never scroll.
12. Rich/virtual replacement never invokes Navigation.
13. `projectSelectedHunk` never selects, scrolls, enriches, or updates counters.
14. `waitToEnrich` remains FileCard-owned.
15. Navigation re-resolves a target after `waitToEnrich`.
16. FileTree navigation resolves a hunk before invoking Navigation.
17. There are no file or directory navigation commands.
18. Selecting a pseudo-hunk never starts a backend request.
19. Navigation never changes FileSequence order.
20. Exactly one application hotkey listener is mounted.
21. Inactive ChangeSets have no hotkey listener.
22. Hotkeys contain no generic command or dispatch abstraction.
23. Hotkeys ignored inside editable controls preserve native behavior.
24. Recognized hotkeys call `preventDefault()` before invoking their operation.
25. Help and Debug remain independent state values.
26. Closed Debug performs no sampling.
27. Provider cleanup removes every listener, frame, timer, retry and observer.
28. A disposed controller performs no later DOM mutation or scrolling.
29. Browser text-side selection remains independent from Navigation.
30. `NavigationCommand` is the only surviving application command type.
31. `HintHud` and `DebugHud` definitions remain adjacent in source.
32. `HintHud` and `DebugHud` remain adjacent inside the rendered HUD stack.
33. `HelpModal` remains outside the HUD stack and never separates `HintHud` from `DebugHud`.
34. `ChangeSetTitle` contains no Show All or Fold All controls, and Help contains no corresponding rows.
35. `s` and `f` are not application hotkeys.

This is the complete corrected navigation-and-hotkeys plan for approval.
