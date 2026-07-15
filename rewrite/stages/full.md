# Frontend rewrite plan

## 1. Setup

The rewritten frontend is built beside the existing frontend. Existing files under `frontend/src/` remain `v_old`, while the rewrite is `v_new` and lives under `frontend/src/new/` until final cutover.

This is not a gradual movement of `v_old` application functions into new files. `v_new` implements the architecture in `spec/frontend.md` as an independent frontend.

Visual parity is a hard rewrite requirement. At the same viewport, URL, backend data and UI state, `v_new` must be a pixel-perfect 1:1 visual copy of `v_old`, except for the three differences authorized by Appendix A. Architectural improvement does not authorize visual redesign, approximation or cleanup.

`frontend/src/main.tsx` remains the sole Vite entrypoint. It attaches Solid to the root DOM element and temporarily selects which complete frontend to mount. It owns no workspace or domain state.

The visible applications are:

```text
v_old → frontend/src/App.tsx
v_new → frontend/src/new/hud/App.tsx
```

There is no second entrypoint, `new/main.tsx`, or `new/root.tsx`.

The transitional source tree is:

```text
frontend/src/
├── App.tsx
├── Header.tsx
├── ...
├── app/
│   └── ...
├── main.tsx
└── new/
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
    ├── styles.css
    └── utils.ts
```

The migration selector chooses a complete provider tree and App, not only an App component.

`v_old` remains:

```text
current QueryClientProvider
└── current ToastProvider
    ├── current root ErrorBoundary
    │   └── current App
    └── current ToastViewport
```

`v_new` uses the rewritten composition from the specification:

```text
new ToastProvider
├── new QueryProvider
│   └── new root ErrorBoundary
│       └── new/hud/App
└── new ToastViewport
```

The two versions share only the browser document, root mount element, Python backend contract, and temporary frontend-switch operation.

They do not share QueryClient instances, query caches, Toast state, ErrorBoundaries, Solid state, Context, DOM references, event listeners, timers, observers, or application CSS.

The temporary version type is:

```ts
type FrontendVersion = "v_old" | "v_new";
```

The selected implementation is stored independently from workspace state:

```text
localStorage["dirdiff:frontend-version"] = "v_old" | "v_new"
```

`v_old` is the initial default.

The version does not live in the URL because `v_old` currently reconstructs the query string and may discard unrelated parameters. The storage value selects an implementation only; the active frontend still reconstructs its entire workspace from the current URL.

The dirdiff brand in the top-left becomes a button in both versions.

The `v_old` button retains the current visual treatment and switches to `v_new`. The `v_new` button has the same `dirdiff` text, geometry, typography and placement, but uses the green treatment authorized by Appendix A and switches back to `v_old`.

The buttons communicate their destination through their title and accessible label:

```text
v_old button → Switch to v_new
v_new button → Switch to v_old
```

Pressing either button:

1. stores the destination version;
2. leaves the current pathname, query, and hash unchanged;
3. reloads the page;
4. causes `main.tsx` to mount only the selected frontend.

Switching is an intentional complete reset boundary. No Solid state, query state, DOM state, selected hunk, input state, or pending orchestration crosses between versions. The newly mounted frontend starts from the current URL.

The hard reload also aborts outstanding requests, disposes the previous reactive graph and global listeners, clears in-memory caches, and prevents both versions’ stylesheets from remaining active.

`main.tsx` must conditionally load only the selected branch. It must not statically import both Apps and both stylesheets into the page.

The `v_old` branch dynamically loads the existing App, query client, Toast implementation, and stylesheet. The `v_new` branch dynamically loads the equivalent modules under `new/`.

Code under `frontend/src/new/` must not import application code from `v_old`.

Prohibited examples include:

```ts
import { Header } from "../Header";
import { queryClient } from "../queryClient";
import { useToasts } from "../Toasts";
import "../styles.css";
```

`v_new` may import only:

- other modules under `new/`;
- third-party packages;
- browser APIs.

Explicitly preserved renderer files such as `DiffGrid.tsx` and `folds.ts` receive `v_new`-owned copies under `new/hud/`. `v_new` does not import their `v_old` copies.

The backend remains one contract. There are no `/api/v_old` or `/api/v_new` endpoints, compatibility responses, or version-dependent backend behavior. If the backend contract changes during migration, both frontends are updated together.

Setup is implemented in this order:

1. Create the initial `frontend/src/new/` structure.
2. Create the minimal `v_new` Toast, QueryProvider, AppHeader, App, and stylesheet modules needed to mount an independent `v_new` root.
3. Turn the existing `v_old` dirdiff brand into the switch button.
4. Add the green `v_new` brand with the inverse action.
5. Change `main.tsx` into the temporary version selector.
6. Make both branches conditionally load their own modules and CSS.
7. Add an explicit version switch to both root error presentations.
8. Verify switching and isolation before adding domain functionality.

The initial `v_new` App may contain only its AppHeader and an explicit message that the rewrite is under construction. It must already use the `v_new` providers and stylesheet. It must not render `v_old` UI as a placeholder.

Setup is complete when:

- `main.tsx` remains the only Vite entrypoint;
- `v_old` is the initial default;
- both brand buttons switch to the opposite version;
- F5 preserves the selected version;
- switching preserves the exact pathname, query, and hash;
- the newly mounted frontend reconstructs state from the URL;
- only one App and one provider tree are mounted;
- only the active version’s stylesheet is loaded;
- `v_new` imports no `v_old` application modules;
- `v_old` behavior outside the switch remains unchanged;
- either root error presentation permits switching versions;
- neither version switches automatically after an error;
- `make format` and `make tscheck` pass;
- both versions work through the normal Vite-backed dirdiff session.

## 2. Top-level infrastructure

Before `v_new` implements workspace state, Tabs or backend data, it implements the complete top-level infrastructure specified in `spec/frontend.md`.

These are not temporary or reduced provider implementations. The Toast system, QueryProvider and root error behavior created here are their final `v_new` implementations.

Their visible output must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. Toasts, error panels, RetryButton, the root application error and all surrounding layout remain visually identical to `v_old`.

The `v_new` root is:

```text
ToastProvider
├── Root
│   └── QueryProvider
│       └── root ErrorBoundary
│           └── hud/App
└── ToastViewport
```

`ToastProvider` and `ToastViewport` live in:

```text
frontend/src/new/comp/Toasts.tsx
```

`QueryProvider` lives in:

```text
frontend/src/new/api/queryClient.ts
```

`Root()` is a private composition component in the `v_new` branch of the sole `frontend/src/main.tsx` entrypoint.

`hud/App.tsx` may still contain only the green `v_new` AppHeader and an explicit under-construction body. The infrastructure surrounding it may not be provisional.

`new/comp/Toasts.tsx` implements the complete domain-independent error-presentation subsystem:

- `ErrorToast`;
- `PresentedError`;
- `presentError`;
- `ToastProvider`;
- `useToasts`;
- `ToastViewport`;
- individual Toast cards;
- `ErrorPanel`;
- `RetryButton`;
- unexpected-error presentation;
- root `ApplicationErrorPanel`.

There is one global error-only Toast queue.

```ts
export type ErrorToast = {
  id: number;
  title: string;
  message: string;
  details: string | null;
  reason: "timeout" | "other";
};
```

There is no generic Toast tone and no success, information or warning Toast.

`presentError(error)` follows the exact formatting order from the specification:

1. An object with an array-valued `issues` field displays those issues as formatted JSON.
2. An `Error` displays its message.
3. Valid JSON contained in an Error message is formatted.
4. A string is displayed directly unless it contains valid JSON.
5. Other values use formatted JSON.
6. A value that cannot be serialized uses `String(value)`.

The formatter never throws while presenting the original error.

An Error stack becomes expandable details only when it differs from the primary message.

`ToastProvider` owns:

- the Toast signal;
- monotonically increasing IDs;
- immutable insertion;
- dismissal;
- browser-level error listeners.

Its public Context exposes only:

```ts
export type ToastCommands = {
  showError(
    title: string,
    error: unknown,
  ): void;
};
```

Consumers cannot read or mutate the queue, manufacture Toasts, dismiss arbitrary Toast IDs or access a Toast setter.

`useToasts()` throws when called outside `ToastProvider`.

`ToastProvider` renders both its children and `ToastViewport`. The viewport is therefore outside the root ErrorBoundary and survives a complete `hud/App` failure.

Toast behavior remains exactly as specified:

- Toasts appear in insertion order.
- New Toasts are appended.
- There is no maximum queue length.
- There is no provider-level deduplication.
- The viewport remains fixed at the bottom-right.
- It grows upward.
- It becomes vertically scrollable when required.
- Message and detail areas remain independently scrollable.
- Every Toast uses `role="alert"`.
- The viewport uses an assertive live region.
- Stack details are collapsed initially.
- Every Toast has a manual dismiss button.
- Non-timeout Toasts persist until user dismissal.
- Timeout Toasts expire after exactly 10 seconds.

Timeout ownership belongs to the mounted Toast card. `ToastProvider` does not maintain a parallel timer map.

A timeout Toast starts its timer on mount. Manual dismissal unmounts it and clears the timer. Provider disposal clears every remaining timer through Solid cleanup. Non-timeout Toasts create no timer.

`ToastProvider` installs the final browser-level visibility boundary:

```ts
window.addEventListener(
  "error",
  onError,
);

window.addEventListener(
  "unhandledrejection",
  onUnhandledRejection,
);
```

`window.error` creates a persistent “Unexpected error” Toast.

`unhandledrejection` creates a persistent “Unhandled promise rejection” Toast.

Neither listener calls `preventDefault()`. Browser console reporting remains intact. Both listeners are removed when the Provider is disposed.

These listeners do not replace ordinary query, mutation or ErrorBoundary ownership.

`ErrorPanel` is the complete local presentation for a damaged owner. It displays:

- the title;
- complete formatted error;
- an open stack trace when available;
- caller-provided user actions.

It never hides the error, substitutes data, renders an untrusted failed owner underneath itself, retries automatically or dismisses itself automatically.

Every retry action is rendered through:

```ts
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

`RetryButton` has no default behavior. Its callback is required and is never invoked by the program.

Unexpected rendering and reactive errors mount an unexpected-error presentation that:

1. calls `toast.showError(...)` once during its mount;
2. renders the complete local `ErrorPanel`;
3. renders `RetryButton`;
4. invokes only the ErrorBoundary reset supplied by Solid.

A repeated failed retry mounts a new failed attempt and appends a new persistent Toast.

The root `ApplicationErrorPanel` preserves the specified full-page behavior:

- `hud/App` is replaced;
- “Something broke” is visible;
- the complete formatted error is visible;
- the stack is open when available;
- RetryButton is available;
- one persistent “Application error” Toast is added;
- ToastViewport remains usable.

During coexistence, it also contains the explicit user-controlled switch to the other frontend required by Chapter 1. Switching versions remains separate from Retry and is never automatic.

`new/api/queryClient.ts` implements the final `QueryProvider`.

It constructs exactly one QueryClient for each mounted `QueryProvider`. It does not export a QueryClient singleton.

Its interface is:

```ts
export function QueryProvider(props: {
  children: JSX.Element;
  onError(
    title: string,
    error: unknown,
  ): void;
}): JSX.Element;
```

`QueryProvider` imports no Toast component or Toast Context.

It configures:

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

Default structural sharing remains enabled.

There is no persisted browser query cache.

The QueryClient owns one `QueryCache` and one `MutationCache`.

Every query and mutation definition supplies:

```ts
type ErrorMeta = {
  errorTitle: string;
};
```

The cache callbacks use that metadata to report one Toast for each failed attempt.

Query cancellation produces no Toast.

A failed query observed by several components still produces one Toast because reporting occurs at the cache level rather than in each observer.

A user-controlled retry is a new attempt. A repeated failure produces a new Toast.

Query and mutation error state remains in TanStack Query. Components later use that same state to render local damage. No effect copies query errors into Toast signals or separate error signals.

The private `Root()` component bridges Toasts and TanStack Query without creating an `api → comp` dependency:

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
            onSwitchFrontend={
              switchFrontend
            }
          />
        )}
      >
        <App
          onSwitchFrontend={
            switchFrontend
          }
        />
      </ErrorBoundary>
    </QueryProvider>
  );
}
```

The `v_new` branch mounts it as:

```tsx
<ToastProvider>
  <Root />
</ToastProvider>
```

This establishes the dependency direction:

```text
main → new/api
main → new/comp
new/api ↛ new/comp
```

Top-level infrastructure is implemented in this order:

1. Implement `presentError` and its exact formatting behavior.
2. Implement `ErrorToast`, the immutable Toast queue and Toast Context.
3. Implement ToastViewport and mounted Toast-card expiration.
4. Install and clean up the two browser-level error listeners.
5. Implement ErrorPanel and RetryButton.
6. Implement unexpected-error and root-application error presentation.
7. Implement QueryProvider, its QueryClient and both cache callbacks.
8. Implement private `Root()` in the `v_new` branch of `main.tsx`.
9. Mount the minimal `hud/App` beneath the completed infrastructure.
10. Implement the pixel-perfect `v_old` Toast and error styling inside `new/styles.css`.

## 3. Application without navigation

Everything in this chapter must be implemented according to `spec/frontend.md`. This chapter defines implementation order and the temporary no-navigation boundary; it does not define alternative behavior or architecture.

Every visible component implemented in this chapter must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. New ownership, state and component boundaries must not change layout, dimensions, spacing, typography, colors, borders, shadows, sticky behavior, overflow, responsive behavior or control states.

Implemented in this order:

1. Complete `api/api.ts`

   Schemas, backend types, HTTP handlers, query/mutation definitions, and the `api` facade.

2. Domain-independent components

   `Select`, `AutocompleteInput`, and their local interaction state.

3. Workspace shell

   `hud/App`, `AppHeader`, global repo/engine/view state, TabStrip, eternal Tabs, and reset-from-URL behavior.

4. Metadata workflows

   Repositories, refs, defaults, presets, profiles, preferences, warmups, explicit refetches, and stale-time policies.

5. Tab workflows

   Head, Refs, Branch Review, Pull Request, and Preset controls. Each produces complete `DiffParams`.

6. ChangeSet loading

   Manifest query, lazy metadata, strict FileSequence, canonical file queries, progress, cancellation, and reload.

7. File presentation

   FileTree, ChangeSetTitle, HuskFile, FullFile, LazyFile, their separate headers, FileBody, DiffGrid, folds, notebooks, Portals, and localized boundaries.

8. Rich-only rendering

   Every loaded text file remains rich temporarily. There is no temporary virtualization mechanism.

Explicitly absent until the next chapter:

- `NavigationProvider`;
- selected hunk;
- hunk counters;
- Next/Previous/Top;
- pseudo-hunk navigation behavior;
- FileTree selected-hunk highlighting;
- line-pin restoration;
- scroll-follow;
- navigation hotkeys;
- HintHud;
- DebugHud hunk projection;
- whole-file virtualization.

At the end of Chapter 3, `v_new` can load and display real ChangeSets through every Tab, but it cannot navigate hunks yet.

Then Chapter 4 can implement navigation, selection, virtualization, HintHud, DebugHud, HelpModal, and direct hotkeys as one interconnected subsystem.

## 4. Navigation and completion

Everything in this chapter must be implemented according to `spec/frontend.md`. This chapter defines implementation order; it does not redefine navigation or virtualization behavior.

Every visible navigation, selection, virtualization, HUD and hotkey result must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. DOM replacement and virtualization must not introduce visual differences while their corresponding `v_old` content is visible.

Implemented in this order:

1. DOM identity and selection

   Add real and pseudo-hunk targets, FileCard-owned selected-hunk identity, DOM projection, counters, and structural selection repair.

2. Navigation controller

   Implement `navigation.tsx`, `NavigationProvider`, `useNavigation`, `NavigationCommand`, Next, Previous, wrapping, direct-hunk navigation, and Top.

3. File integration

   Connect HuskFile, LazyFile, FullFile, folding, FileTree targets, FileTree highlighting, headers, counters, and `waitToEnrich` to Navigation.

4. Scrolling and line pins

   Implement the scroll-source gate, throttled scroll-follow, navigation scrolling, and independent line-pin restoration.

5. Whole-file virtualization

   Implement row-count cost, rich zones, VirtualFile, geometry preservation, rich/virtual identity preservation, and enrichment before navigation.

6. HUD and hotkeys

   Implement HintHud, DebugHud, HelpModal, their required placement, direct hotkeys, and removal of Show All/Fold All behavior.

7. Remaining DOM behavior

   Preserve browser text-side selection and keep notebook navigation extensible without implementing the post-rewrite region-key TODO.

8. Final integration

   Remove the temporary rich-only limitation from Chapter 3 and connect the complete navigation and virtualization subsystem to the finished application.

At the end of Chapter 4, `v_new` is a complete working frontend with hunk selection, counters, Next/Previous navigation, wrapping, FileTree projection, folded-target exclusion, line pins, whole-file virtualization, HintHud, DebugHud, HelpModal, and direct hotkeys.

`v_old` remains available until final cutover is explicitly authorized.

## 5. Review and correction

After Chapter 4, `v_new` remains available for continued review, correction and direct user feedback.

Every visual difference from `v_old` that is not explicitly authorized by Appendix A is a defect. “Similar,” “close,” “equivalent,” or “improved” is not sufficient; review compares the two implementations at matching viewport, URL, backend data and UI state.

Review covers the complete frontend, including:

- application structure;
- provider behavior;
- backend requests and TanStack Query ownership;
- workspace and Tab state;
- metadata freshness;
- controls and input behavior;
- ChangeSet loading;
- strict file ordering;
- FileTree;
- FileCard states;
- rendering;
- errors and Toasts;
- headers and Portals;
- navigation;
- selection;
- counters;
- scrolling;
- line pins;
- virtualization;
- folding;
- notebooks;
- HUD behavior;
- hotkeys;
- styling;
- layout;
- pixel-perfect visual parity;
- performance;
- missing behavior;
- behavior that technically works but remains confusing or unpleasant.

`spec/frontend.md` remains the authority for intended frontend behavior and architecture.

`spec/rewrite.md` remains the authority for rewrite order and coexistence with `v_old`.

When implementation disagrees with the specification, correct the implementation.

When user feedback changes an agreed requirement, present the proposed specification correction and wait for explicit permission before editing the specification.

Corrections remain inside `v_new`. They must not introduce imports from `v_old`, copied `v_old` state, compatibility providers, compatibility API responses, or alternate code paths that bypass the new architecture.

Review and correction continue in this order:

1. Present the current `v_new` behavior to the user.
2. Investigate every reported problem against the implementation and both specifications.
3. Explain the cause and proposed correction.
4. Apply only the correction authorized by the user.
5. Recheck the affected behavior in the browser.
6. Continue responding to feedback until the user explicitly accepts the rewritten frontend.

`v_old` remains available as the stable alternative throughout this chapter.

This chapter does not authorize:

- deleting `v_old`;
- moving `new/` into the root of `frontend/src/`;
- removing the frontend switch;
- changing the default frontend;
- removing migration storage;
- treating `v_new` as accepted merely because Chapters 1–4 were implemented.

Deletion of `v_old` and final cutover happen only in a separate, explicitly authorized follow-up.

## Appendix A. Permitted visual differences

Only the following visual differences between `v_old` and `v_new` are authorized:

1. Show All and Fold All are removed.

   Their ChangeSet title controls and corresponding Help rows do not exist in `v_new`. No replacement controls are added.

2. The `v_new` dirdiff plank is green.

   It retains the same `dirdiff` text, dimensions, typography, spacing, border, placement and interaction footprint as `v_old`. Only its color treatment changes to make the active `v_new` implementation immediately visible.

3. File-loading status is more compact.

   Status shown while files are being loaded may use the compact AppHeader presentation specified in `spec/frontend.md`. This exception applies only to file-loading progress, failure and long-running-file status. It does not authorize unrelated Header, status, summary or layout changes.

No other visual difference is permitted. Everything not listed above must remain a pixel-perfect 1:1 copy of `v_old`.
