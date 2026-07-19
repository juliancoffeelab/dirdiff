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
| ChangeSet state | ChangeSet | tree visibility, file expansion |
| Controls state | Tab-specific Controls | selected field values and field-to-field workflow |
| Input and selection state | `Input`, `AutocompleteInput` or `Select` | live user input, open popup, highlighted or selected choice |
| Component state | Component | profile dialog, other self-contained HUD state |
| Derived data | `createMemo` | filtered choices, selected repo name, realtime initial and default values, directory reachability |
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

Workspace stores one small global workspace entity:

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

The repository list is not workspace state. It remains backend state in the TanStack query entry defined by:

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

Browser workspace URLs and Python API URLs use distinct vocabulary:

```text
browser workspace URL
├── repo_id=<numeric selected repository>
├── preset_type=diff|fold|gumtree|scroll
└── never uses project_id

Python API URL
└── project_id=<numeric repository ID or preset project kind>
```

`repo_id` preserves the global selected repository independently of the active
Tab. `preset_type` preserves the Preset Tab kind. When a Tab constructs complete
`DiffParams`, it translates browser state into the stable backend contract:

- repository-backed Tabs map numeric `repo_id` to numeric `project_id`;
- Preset maps `preset_type` to its string `project_id` and leaves the selected
  repository out of `PresetDiffParams`.

This distinction applies only to browser workspace serialization. It does not
rename or otherwise change the Python API contract.

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
- calculated directory reachability;
- hunk state;
- rendered DOM registrations.

### 24.8 AutocompleteInput and selected values

`AutocompleteInput` is an explicit state-owning part of the client data model. It is not a controlled text box whose live value is mirrored into Tab state.

It owns:

- the current user input;
- whether the user has edited the supplied value;
- its open popup;
- its highlighted choice.

It implements autocomplete interaction, choice presentation, and filtering.

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

Engine is stored by Workspace. It is selected in the shared Header and applies across Tabs.

Changing an engine:

- updates the workspace engine;
- causes every Tab with a selection to derive new immutable `DiffParams`;
- recreates the active `ChangeSetContent` for those complete `DiffParams`;
- disposes the previous manifest observer and `ChangeSetSnapshot`;
- preserves mounted Tabs, Controls, Inputs and the outer `ChangeSet` instance;
- preserves ChangeSet-owned layout state, such as tree visibility and path-based expansion, where it remains valid;
- never presents old-engine file results as results of the new engine.

The API contract continues to use complete `DiffParams` for manifest, lazy-info and file queries. Although the current Python manifest handler does not use engine, the frontend does not introduce a second parameter contract to special-case that fact. An engine change recreates the active `ChangeSetContent`; once the replacement manifest succeeds, its new `ChangeSetSnapshot` restarts strict sequential file loading.

An engine change is therefore much more expensive than a view change, but it is not a reason to discard unrelated client state or disrupt the page layout.

Inline/split view is stored by Workspace because it is presentation state shared across Tabs.

Changing view:

- does not change `DiffParams`;
- does not request another ChangeSet;
- does not reset component-owned input;
- does not reset ChangeSet expansion;
- may require hunk/layout work specified in the later DOM-state section.

### 24.10 Tab and ChangeSet lifetime

All lightweight Tabs, Controls and Inputs remain mounted until the workspace reaches an explicit reset boundary.

`App` renders one shared AppHeader outside Tabs. Tabs never render their own Header. Only the active Tab displays its Controls; cheap Controls remain mounted under the HTML `hidden` attribute so their component-owned input survives ordinary Tab switches. The active ChangeSet may contribute status and summary presentation to AppHeader through its specified Portal outlets. Tabs and Profile retain ownership of their canonical metadata queries and Portal only compact pending/error presentation into AppHeader's stable workspace-metadata status target. Repo refs and defaults present background warmup state even while their owning Tab is inactive; Preset and Pull Request remain active-gated.

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

The keyed boundary recreates the `ChangeSet` whenever the Tab receives a new selection. Changing the global engine changes `params()` but not `state.selected`, so the outer `ChangeSet` remains mounted and retains its client-owned layout state while its active `ChangeSetContent` is recreated.

Keeping Controls mounted is not restoration caching. Their state continues to exist because their owning components continue to exist. When the workspace is recreated, the Controls and all their local input are destroyed and reconstructed from URL state, static defaults and realtime query data.

### 24.11 ChangeSet ownership

`ChangeSet` owns the lightweight layout state internal to one displayed result:

```ts
export type ChangeSetState = {
  treeOpen: boolean;
  fileExpansion: Record<string, boolean | undefined>;
};
```

Directory expansion is not stored client state. `ChangeSetSnapshot` calculates it bottom-up from current descendant file reachability as specified in Section 25.5 of [03_file_presentation.md](03_file_presentation.md). This prevents directory and file expansion from becoming contradictory authorities.

Its private active boundaries store backend observers and ChangeSet state:

- `ChangeSetContent` owns exactly one manifest observer for immutable complete `DiffParams`;
- `ChangeSetSnapshot` owns the immutable manifest, lazy metadata observer, ordered file-query observer collection, FileSequence state, and progress; it traverses the manifest, performs explicit file loading, and renders FileTree, FileCards, and file DOM;
- replacing complete `DiffParams` recreates `ChangeSetContent`;
- replacing manifest data recreates `ChangeSetSnapshot`.

The public `ChangeSet` boundary stays mounted while inactive. Its internal active content mounts only while the Tab is active and is recreated whenever complete `DiffParams` changes:

```tsx
function ChangeSet(props: {
  active: boolean;
  params: DiffParams;
}) {
  const [state, setState] =
    createStore<ChangeSetState>(initialChangeSetState);

  return (
    <Show
      when={props.active ? props.params : null}
      keyed
    >
      {(params) => (
        <ChangeSetContent
          params={params}
          state={state}
          setState={setState}
        />
      )}
    </Show>
  );
}
```

`ChangeSetContent` and `ChangeSetSnapshot` are private component boundaries inside the same module. They ensure that inactive ChangeSets retain lightweight client state without retaining:

- rendered file DOM;
- active manifest observers;
- active lazy-info or file observers;
- a running FileSequence.

`ChangeSetContent` owns the manifest observer. Its keyed manifest result mounts `ChangeSetSnapshot`, which owns all manifest-dependent observers and state and performs sequencing and rendering. No manifest-dependent query or derivation lives above that snapshot boundary.

Consequently:

- switching Tabs preserves ChangeSet expansion;
- switching Tabs removes expensive ChangeSet DOM;
- switching Tabs stops the inactive FileSequence;
- returning mounts new active ChangeSet content and obtains a current manifest;
- selecting new Tab values recreates the ChangeSet and its state;
- changing engine preserves the outer ChangeSet and layout state while recreating active `ChangeSetContent`;
- changing manifest data disposes the previous `ChangeSetSnapshot` before mounting its replacement;
- recreating the workspace destroys every ChangeSet;
- explicit reload resets state from inside the ChangeSet.

The Tab never updates ChangeSet expansion directly.

### 24.12 ChangeSet reload

`ChangeSet` implements reload; the Tab does not.

Reloading:

1. stops the current FileSequence;
2. calls `refetch()` on the active manifest observer for the current immutable `DiffParams`;
3. lets the keyed manifest-result boundary replace `ChangeSetSnapshot`;
4. applies the existing outer ChangeSet tree and expansion reset policy.

The replacement snapshot restarts strict manifest-order loading from its own manifest.

The explicit reload does not invalidate the manifest cache. Invalidation is reserved for cases where an external operation makes cached data untrustworthy; here ChangeSet directly requests a fresh snapshot.

ChangeSet reload has no visible button. The existing `R` hotkey targets the active ChangeSet directly.

### 24.13 Backend-data lifecycle

TanStack Query owns backend entity state while Solid component boundaries own which observers exist:

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
| selected repository | Head shows `RepoGate` in place of Load; Refs keeps both free-form inputs and shows `RepoGate` in place of Load; Branch Review shows only `RepoGate` and does not mount its four controls; Pull Request and Preset do not require a preselected repo |
| repository list | Header selector or `RepoGate` shows its own pending/error state |
| refs, branches and remotes | Once their required repo exists, inputs remain usable as free-form inputs while autocomplete choices wait locally |
| repository defaults | Inputs render without the realtime default; an untouched input may adopt it when it arrives |
| preset catalogs | Preset controls wait locally; the Preset Tab itself remains alive |
| manifest | The owning `ChangeSet` shows its own pending/error state |
| rendered file | The owning `FileCard` derives its own HuskFile, FullFile or LazyFile presentation |
| preferences | Profile waits or reports the error; ChangeSet continues with `aggressive_folds: true` until its canonical preferences observer has data |

The general rule is:

> Missing data gates the smallest component or action that actually requires it.

Branch Review has one explicit selected-repository boundary: its complete four-control workflow depends on a concrete repo for structured branch sources and defaults. Without a selected repo, the Tab renders only `RepoGate`. Selecting a repo reconstructs the workspace and mounts Branch Review controls with that concrete project identity. Refs is intentionally different: its two git-ref inputs remain useful as free-form text without repo metadata, so only its Load action becomes `RepoGate`.

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
| Refs autocomplete suggestion panel | refresh refs |
| Branch Review autocomplete suggestion panel | refresh branches and remotes |
| Beside the Preset kind tabs | refresh preset catalogs |

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

The Refs and Branch Review controls appear at the top-right of the open autocomplete suggestion panel. Activating one keeps the panel open, preserves input focus and user text, and leaves existing choices visible while refetching. The Preset control appears beside the preset-kind tabs; activating it preserves the active kind and selected preset.

All three controls have the same visible lifecycle:

- idle uses the ordinary refresh treatment;
- fetching disables the button and continuously rotates the refresh icon;
- failure stops rotation and changes the control to the established error red;
- the failed red control remains enabled so the user can retry;
- only a successful refresh returns the control to its ordinary treatment.

The global Error Toast exposes the complete failure. The red refresh control is the persistent local indication and must not be cleared merely by closing an autocomplete suggestion panel, changing input text, or switching Tabs.

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

App routes only the selected profile identity through Tabs into each ChangeSet. ChangeSet observes `api.profile.preferences(profileId)` under the same canonical query key as Profile and derives `aggressiveFolds` directly from that query result. It does not receive or copy a preferences entity from Profile or App.

A missing selected profile is a real absence and selects the literal default `aggressiveFolds: true`; it is never passed to `api.profile.preferences`. Pending or failed preference data also leaves that default active while the existing Profile status and global Toast paths expose the failure. When preference data arrives, or saving preferences replaces the exact cache entry, renderer calculations that consume `aggressiveFolds` update reactively. Manifest and file HTTP work, ChangeSet expansion, and FileSequence do not reset.

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
- duplicated local Controls input;
- Tab-level live input state;
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
6. Workspace stores engine; each Tab owns only its selected values or result.
7. `AutocompleteInput` owns live input; `onEditNotification` may warm stale data without transferring input ownership, and `onDone` transfers the selected or entered value.
8. Initial/default values and choices reach `AutocompleteInput` in realtime without overwriting edited input.
9. Controls retain selected values only for their current mounted workflow and give Tabs the resulting selection or parameters.
10. ChangeSet owns its internal layout and reload state.
11. Inactive Tabs do not retain expensive rendered ChangeSet DOM.
12. Switching Tabs preserves mounted lightweight Tab, Controls, input and ChangeSet state.
13. Changing engine derives new `DiffParams` and queries without remounting Controls, Inputs or the outer ChangeSet layout state.
14. Backend data exists only in TanStack Query.
15. Missing backend data gates the boundary specified in Section 24.13: Refs retains its two free-form inputs without a repo, while Branch Review displays only `RepoGate` until a repo is selected.
16. Refs and defaults are prefetched when a repository becomes known without blocking Tab rendering.
17. Refs and Branch Review share one refs query per repo.
18. Typing filters locally and may request a stale-time-guarded warmup of the complete refs query.
19. Only Refs, Branch Review and Preset have metadata refresh buttons.
20. Help and Debug remain independent.
21. Controls react to `onDone` by advancing, refocusing or triggering the appropriate action, while exact focus, keyboard and possible `Form` behavior remain postponed.
22. Hunk and DOM state remain deferred to their dedicated section.
