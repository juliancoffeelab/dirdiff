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
- individual files have no isolated query state to supply to FileCard or FileTree;
- a large result update places more pressure on the complete ChangeSet;
- retry and cancellation are too coarse.

### Option C: canonical queries plus one FileSequence

Selected.

- Manifest, lazy metadata and every file have independent query keys.
- `api.ts` defines every query.
- `ChangeSet` owns one small sequential loop using `queryClient.fetchQuery`.
- `ChangeSet` owns the ordered file-query observers and supplies their reactive results to both FileTree and FileCard.
- The loop contains no backend data and creates no second cache.

`fetchQuery` is appropriate here because it fetches and caches one canonical query while returning a Promise that can be awaited sequentially. [TanStack QueryClient reference](https://tanstack.com/query/v5/docs/reference/QueryClient)

## 4. API files and boundaries

This section records the API-specific file boundaries. Section 65 defines the complete selected frontend structure and component ownership; read that section for the full file plan.

The API portion of that structure is:

```text
frontend/src/
├── api/
│   ├── api.ts
│   └── queryClient.tsx
├── hud/
│   ├── ...
│   ├── ChangeSet.tsx
│   └── ...
└── ...
```

In that structure:

- `api/api.ts` owns schemas, API types, HTTP handlers, query definitions and mutation definitions;
- `api/queryClient.tsx` owns QueryClient construction and exports `QueryProvider`;
- `hud/ChangeSet.tsx` owns the manifest observer, FileSequence, derived file state and ChangeSet rendering.

The API facade follows these rules:

- `api/api.ts` exports the single `api = { ... }` facade and API types.
- Private HTTP handlers, Zod schemas, query keys, and query/mutation definitions live behind it.
- `api/queryClient.tsx` provides the configured client through `QueryProvider`; consumers obtain it with TanStack Query’s `useQueryClient()` rather than importing a global singleton.

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
  mode: "repo";
  left_label: string;
  right_label: string;
  summary: ManifestSummary;
  tree: ManifestNode[];
};
```

The current optional-field-heavy `FileEntry` should not survive the rewrite, but the stable backend response contract does not change. Notebook responses carry `render_kind: "notebook"`; text responses do not carry a `render_kind` field. The frontend validates the two existing response shapes as a Zod union:

```ts
const TextFileDiffResponseSchema = z.strictObject({
  // Exact required fields returned by the existing text response.
});

const NotebookFileDiffResponseSchema = z.strictObject({
  render_kind: z.literal("notebook"),
  // Exact required fields returned by the existing notebook response.
});

const FileDiffResponseSchema = z.union([
  NotebookFileDiffResponseSchema,
  TextFileDiffResponseSchema,
]);

export type TextFileDiff = z.infer<typeof TextFileDiffResponseSchema>;
export type NotebookFileDiff = z.infer<typeof NotebookFileDiffResponseSchema>;
export type FileDiff = z.infer<typeof FileDiffResponseSchema>;
```

Every field required by its renderer is required by the corresponding schema. Narrowing may check for the existing notebook discriminator; it must not require a new text discriminator or any other backend response change.

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
        staleTime: 5_000,
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

`QueryProvider` constructs one QueryClient for each mounted provider. It does not export a QueryClient singleton. Consumers obtain the mounted client through TanStack Query's `useQueryClient()`.

The constructed client uses:

```ts
defaultOptions: {
  queries: {
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  },
  mutations: {
    retry: false,
  },
}
```

Section 64.8 defines the complete `QueryProvider` construction, including its `QueryCache` and `MutationCache` error callbacks.

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

## 14. ChangeSet file-query observation

`ChangeSet` owns one ordered collection of canonical file-query observers. Neither FileTree nor FileCard creates a query observer. The ChangeSet request lane initiates both automatic and explicitly selected requests.

```ts
const fileQueries = createQueries(() => ({
  queries: orderedFiles().map((entry) => ({
    ...api.changeSet.file(
      props.params,
      manifest.data!.cache_id,
      entry,
    ),
    enabled: false,
  })),
}));
```

Normally, permanently disabled queries are discouraged because they opt out of automatic behavior. Here it is deliberate: the observers are read-only projections of the cache, while the single request lane performs canonical fetches through `fetchQuery`. [TanStack disabled-query guide](https://tanstack.com/query/latest/docs/framework/react/guides/disabling-queries)

Each observer still receives cache updates for its exact key. `ChangeSet` pairs every observer with the manifest entry at the same index and derives that entry's reactive `FileCardState` from the query result and lazy metadata. It passes those same per-file states to FileTree and FileCard; it does not copy file results into a `filesByKey` store.

FileCard receives its state and an explicit-load callback from ChangeSet:

```tsx
<FileCard
  state={fileStates[fileIndex]}
  onLoad={() => enqueueExplicitFile(entry)}
/>
```

The clickable LazyFile plank invokes `onLoad`. It does not refetch independently. `enqueueExplicitFile` submits that exact canonical file key to the ChangeSet request lane, which preserves the sequencing rules from Section 11.

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
  stopFileSequence();
  resetChangeSetState();
  await manifest.refetch();
}
```

Reload targets the active manifest observer directly. It does not invalidate the manifest cache as an indirect request trigger.

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

Toast deduplication does not require load IDs. QueryCache and MutationCache callbacks produce one global Toast for each failed attempt, even when several components observe the same query. The component that owns the failed operation renders only its complete localized ErrorPanel and does not produce another Toast.

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
10. ChangeSet owns the ordered file-query observers and supplies each derived file state to FileTree and its corresponding FileCard.
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
