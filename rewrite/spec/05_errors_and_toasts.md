## 64. Toasts and error containment

### 64.1 Purpose

Errors must be impossible to miss without allowing one damaged region to destroy unrelated UI.

The governing rules are:

1. Every real error is presented dramatically.
2. Damage stops at the (reasonably) smallest UI piece where we cannot produce a valid result, for whatever reason. Unexpected thrown failures are contained by the nearest `ErrorBoundary`.
3. The damaged boundary presents the complete error locally.
4. The same failure also produces one global Toast.
5. The program never silently retries, substitutes data, hides the failure or pretends that the operation succeeded.
6. The user may explicitly retry, reload, change inputs, switch Tabs or otherwise replace the damaged boundary.

A user-controlled retry is not automatic recovery.

```text
failure
├── global visibility
│   └── Error Toast
└── localized damage
    └── complete local ErrorPanel
        └── RetryButton
```

This ordinary recoverable-error shape does not apply to an unexpected FullFile renderer exception. That programmer failure uses the separately specified critical unrecoverable strip and has no RetryButton.

### 64.2 Non-errors

The following are not errors and do not produce Toasts:

- intentional TanStack Query cancellation;
- a result discarded because its source component, query, or mutation was intentionally replaced;
- an unknown repository `cache_id`, which is an expected snapshot-expiration indication and restarts its ChangeSet;
- ordinary input validation, such as an empty required PR URL;
- content intentionally represented by a normal LazyFile reason;
- unavailable autocomplete data while its query is still pending.

Validation remains prominently local to the relevant input or action.

Cancellation remains silent because the application intentionally requested it.

### 64.3 Exact Toast behavior

Toasts remain visually and behaviorally the same as the current implementation.

There is one global Toast queue. It contains persistent or transport-timeout Error Toasts and the explicitly temporary non-error notices required by line-pin restoration.

Every Error Toast contains:

- a title;
- a formatted primary message;
- optional expandable details containing the stack trace;
- a manual dismiss button.

Toast behavior remains:

- Toasts appear in insertion order.
- New Toasts are appended after existing Toasts.
- There is no generic success, information or warning Toast. A line-pin-unavailable notice is the only transient non-error variant and lasts for its required two seconds.
- There is no maximum Toast count.
- There is no automatic provider-level deduplication.
- The viewport is fixed at the bottom-right.
- The viewport grows upward and becomes vertically scrollable when necessary.
- Individual Toast message and detail regions remain independently scrollable.
- Every non-timeout Error Toast remains until the user dismisses it.
- A timeout Toast is automatically dismissed after 10 seconds.
- A transient line-pin notice is automatically dismissed after its required two seconds.
- Manual dismissal works for every Toast.
- The details section is closed initially.
- The viewport imposes no shared live-region urgency. Every Error Toast uses `role="alert"`; a transient line-pin notice uses `role="status"`.

```ts
export type ErrorToast = {
  id: number;
  title: string;
  message: string;
  details: string | null;
  reason: "timeout" | "other";
};

export type TransientToast = {
  id: number;
  title: string;
  message: string;
  details: null;
  reason: "transient";
  durationMs: number;
};

export type Toast = ErrorToast | TransientToast;
```

There is no generic `ToastTone`, and transient notices cannot carry Error data or recovery behavior.

### 64.4 Error formatting

The current formatting behavior remains.

Primary error formatting follows this order:

1. An object with an array-valued `issues` field displays those issues as formatted JSON.
2. An `Error` displays its message.
3. If an Error message contains valid JSON text, that JSON is formatted.
4. A string is displayed directly, unless it contains valid JSON text.
5. Other values use formatted JSON.
6. Values that cannot be JSON-serialized display “Unable to display the thrown value.” without invoking conversion hooks.

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
- global browser error listeners.

It performs immutable insertion and explicit dismissal.

Its public context contains commands only:

```ts
export type ToastCommands = {
  showError(
    title: string,
    error: unknown,
  ): void;
  showTransient(
    title: string,
    message: string,
    durationMs: number,
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

`Root` is a private composition component in `main.tsx`. It exists because `useToasts()` must run below `ToastProvider`, while `QueryProvider` must receive error reporting without importing `comp/Toasts.tsx` into `api/queryClient.tsx`.

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

Every application query and mutation definition provides a specific user-visible error title:

```ts
type ErrorMeta = {
  errorTitle: string;
};
```

```ts
queryOptions({
  queryKey: ["repos", projectId, "refs"],
  queryFn: ({ signal: abortSignal }) =>
    requestRepoRefs(projectId, abortSignal),
  meta: {
    errorTitle: "Failed to load refs",
  } satisfies ErrorMeta,
});
```

TanStack's underlying option permits metadata to be absent, but the application contract does not. The QueryClient asserts metadata presence at the cache boundary and uses its specific title. `QueryCache` and `MutationCache` error callbacks produce one Toast for each failed query or mutation attempt:

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
        if (
          isCancelledError(error) ||
          isRepositoryCacheExpiration(error)
        ) {
          return;
        }

        if (query.meta === undefined) {
          throw new Error(
            "Failed query requires error-title metadata.",
          );
        }

        props.onError(query.meta.errorTitle, error);
      },
    }),

    mutationCache: new MutationCache({
      onError(error, _variables, _result, mutation) {
        if (mutation.meta === undefined) {
          throw new Error(
            "Failed mutation requires error-title metadata.",
          );
        }

        props.onError(mutation.meta.errorTitle, error);
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

Cache-level error callbacks are used because one backend query may have several observers. A cache callback reports the failed query once rather than requiring every observer to synchronize the same error into Toast state. TanStack exposes optional `meta` on query definitions and global error callbacks on `QueryCache` and `MutationCache` for this purpose. [Solid Query options](https://tanstack.com/query/latest/docs/framework/solid/reference/useQuery), [QueryCache](https://tanstack.com/query/latest/docs/reference/QueryCache), [MutationCache](https://tanstack.com/query/latest/docs/reference/MutationCache)

The QueryCache callback recognizes an unknown repository `cache_id` as the snapshot-expiration indication defined in `01_tanstack_query.md` and does not present it as an error or produce a Toast. Its `ChangeSetContent` replaces the expired snapshot. A failure to obtain the replacement manifest is a new manifest failure and follows the normal manifest error path.

Components do not use `createEffect` merely to copy query errors into Toasts.

Components continue to read query or mutation error state to render their local damage.

A failed attempt creates exactly one Toast even if several components observe the same query.

A user-triggered retry is a new attempt. If it fails, it creates a new Toast.

### 64.9 Local query damage

A query failure damages only the component that presents that query.

| Failure | Local result |
|---|---|
| repository list | RepoSelect and RepoGate show their compact error state with complete details available through ErrorPopover |
| refs | affected autocomplete remains usable as free-form input and shows the compact refs error with complete details available through ErrorPopover |
| repository defaults | affected default-dependent controls show the compact error without overwriting user input; complete details remain available through ErrorPopover |
| preset catalogs | Preset controls show their compact error with complete details available through ErrorPopover |
| preferences | Preferences UI preserves its compact constrained error presentation with complete details available through ErrorPopover |
| manifest | ChangeSet shows ErrorPanel; no FileSequence starts |
| lazy metadata | affected entries become error-flavoured LazyFiles; normal file loading continues |
| file query | that FileCard becomes an error-flavoured LazyFile; later files continue |
| mutation | triggering component displays its mutation error |

A FileSequence never stops because one file failed.

Every failed FileCard remains represented at its manifest position.

An unexpected FullFile renderer exception is different from an HTTP file failure. It leaves the stable FileCard article mounted where possible and replaces only the failed renderer subtree with a critical unrecoverable strip. The strip presents the complete error, marks its terminal DOM with `data-file-render-error`, and produces one persistent Toast, but it is not a LazyFile, has no hunk target, and offers no RetryButton. Navigation initialization and HunkDisplay observation stop when they encounter that marker; they do not emit another Toast or promote the localized failure. The boundary does not preserve failed DOM, synthesize replacement hunks, remap selection, choose another hunk, or attempt automatic recovery.

Repository cache expiration is not a file failure. It disposes the complete expired `ChangeSetSnapshot`, uses the existing compact ChangeSet loading presentation while the replacement manifest loads, and never produces an error-flavoured LazyFile for the unknown cache response.

An error-flavoured LazyFile displays:

- its path;
- the complete formatted error;
- an open local stack trace when available;
- a RetryButton;
- its error styling.

It does not display partial FileBody content as if loading succeeded.

A refetch error with previously available data may retain that data only if the presenting component also displays an unmistakable error state. Old data must never make a failed refresh appear successful.

### 64.10 ErrorPanel

`ErrorPanel` is the complete local presentation of a failed region.

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

Shell metadata components with constrained layout do not place the complete ErrorPanel inline. They use `ErrorPopover`, which renders a caller-supplied compact trigger and places the complete ErrorPanel in the browser top layer:

```tsx
export function ErrorPopover(props: {
  title: string;
  error: unknown;
  onRetry: () => void;
  trigger: JSX.Element;
  triggerClass: string;
  triggerLabel: string;
}) {
  const id = createUniqueId();

  return (
    <>
      <button
        type="button"
        class={props.triggerClass}
        aria-label={props.triggerLabel}
        popovertarget={id}
      >
        {props.trigger}
      </button>

      <div
        id={id}
        class="error-popover"
        popover="auto"
      >
        <ErrorPanel
          title={props.title}
          error={props.error}
        >
          <RetryButton onRetry={props.onRetry} />
        </ErrorPanel>
      </div>
    </>
  );
}
```

The compact trigger preserves the previous control's layout footprint. It is keyboard and click accessible and remains visibly red while its operation remains failed. The popover consumes no document layout space, light-dismisses through the browser’s native popover behavior, and contains the complete message, initially open stack when available, and explicit RetryButton.

For a failed metadata refresh control, the red refresh icon becomes the ErrorPopover trigger. Activating the failed icon opens details; retry occurs only through RetryButton inside the popover. During an ordinary or successful state, the same icon retains its direct refresh behavior.

The ErrorPanel never:

- hides the error;
- substitutes empty data;
- renders failed content underneath itself;
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
- the same mutation with the same inputs;
- `ErrorBoundary`’s `reset` function for an unexpected rendering error;
- a ChangeSet reload command where reload is the real user action.

Unexpected FullFile renderer failures are the explicit exception: their critical strip is unrecoverable and therefore contains no RetryButton or ErrorBoundary reset action.

The callback is always supplied. RetryButton has no generic default behavior.

The program invokes `onRetry` only from direct user activation of the visible
RetryButton or an explicitly specified keyboard equivalent. The outer ChangeSet
error panel's `r` hotkey is one such equivalent and invokes the same callback.

`RetryButton` remains presentation-only and does not choose HTTP policy. For an ordinary file-query failure, ChangeSet explicitly enqueues an unbounded attempt through the existing sequential file-fetch lane. Automatic file attempts and first explicit deferred-file loads remain bounded. Both policies use the same canonical file query key because timeout is execution policy, not data identity.

### 64.12 Unexpected rendering and reactive errors

Solid ErrorBoundary contains unexpected errors thrown while rendering or reactively updating its subtree. It does not catch event-handler errors or unrelated scheduled callbacks. [Solid ErrorBoundary documentation](https://docs.solidjs.com/reference/components/error-boundary)

Unexpected errors outside the FullFile renderer boundary mount `UnexpectedErrorPanel`:

```tsx
function UnexpectedErrorPanel(props: {
  title: string;
  error: unknown;
  onRetry: () => void;
  retryOnR: boolean;
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

The outer ChangeSet boundary sets `retryOnR` because its failed subtree contained
the ordinary ChangeSet hotkey listener. While that panel is mounted, an
unmodified `r` outside editable controls invokes the exact same callback as its
visible RetryButton. Boundaries rendered inside `ChangeSetShell` set it to
`false`; the surviving ChangeSet hotkey already handles `r` there.

If the user retries and the boundary fails again:

1. the new attempt fails;
2. UnexpectedErrorPanel mounts again;
3. a new persistent Toast is appended;
4. the complete local error remains visible.

A FullFile renderer exception instead mounts its critical unrecoverable strip inside the stable FileCard article where possible. It produces one Toast but exposes no reset action, hunk target, selection repair, or automatic recovery.

### 64.13 ErrorBoundary placement

Boundaries follow meaningful damage scope.

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
│                           └── ChangeSetSnapshot ErrorBoundary
│                               ├── FileTree ErrorBoundary
│                               └── FileCards
│                                   └── FullFile renderer ErrorBoundary
└── ToastViewport
```

The nearest boundary contains the damage:

- A FullFile renderer or header exception replaces only that FileCard's rendered presentation with its critical unrecoverable strip.
- A FileTree exception replaces only FileTree.
- A ChangeSet-wide exception replaces only that ChangeSet.
- A Tab workflow exception replaces only that Tab’s content.
- An App or workspace exception replaces the App.
- TabStrip remains available when one Tab fails.
- Other Tabs remain available when one Tab fails.
- Other FileCards remain available when one FileCard fails.
- ToastViewport remains available when the App fails.

There is no boundary inside DiffGrid, NotebookFile, or another renderer merely to preserve partially rendered content. The FullFile renderer subtree is the smallest contained renderer unit. Its stable outer FileCard article remains where possible, but the failed renderer DOM is not cached or repaired.

Portalled AppHeader contributions remain in the ChangeSet reactive subtree and are caught by the ChangeSet boundary despite their physical DOM location.

The snapshot rendering ErrorBoundary belongs to its `ChangeSetSnapshot` and is disposed with it. An unexpected rendering error from an expired or replaced snapshot cannot remain mounted over its replacement.

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

These listeners are the final visibility boundary for errors outside Solid’s rendering and reactive-update handling.

They are not the normal path for query or mutation failures.

Every `mutateAsync` or other intentionally awaited Promise must be handled at its call site. Ordinary Navigation callers catch a rejected `navigation.navigate(...)`, show one persistent “Navigation failed” Toast, and stop that operation. Line-pin Navigation is already awaited inside the ChangeSet file lane: an unexpected rejection follows that lane's existing failure path into the nearest `ChangeSetSnapshot` ErrorBoundary, which presents the local damage and produces exactly one persistent Toast. LinePins and Navigation do not Toast it separately. Allowing either rejection to reach `unhandledrejection` and create a duplicate Toast is a bug.

### 64.16 Prohibited error handling

The rewrite must not contain programmer-controlled recovery such as:

```ts
try {
  return await requestData();
} catch {
  return emptyData;
}
```

Restarting a ChangeSet after an unknown repository `cache_id` is the specified response to a non-error cache-expiration indication. It is not programmer-controlled error recovery or an automatic retry of the failed file query.

It must not:

- catch an error and only log it;
- catch an error and return `null` as if nothing failed;
- replace invalid backend data with defaults;
- continue rendering a FileBody after its required data failed validation;
- automatically call RetryButton actions;
- automatically reset an ErrorBoundary;
- automatically retry queries or mutations;
- show a success state while hiding a refetch error;
- maintain copied error signals outside the actual query, mutation, or damaged UI boundary;
- Toast the same failed attempt once from TanStack Query and again from an observer;
- throw a handled mutation rejection into the global unhandled-rejection listener.

Every `catch` must do at least one of:

- recognize intentional cancellation;
- convert invalid user input into an explicit validation result;
- place the actual component or query into an explicit error state;
- rethrow to the nearest meaningful ErrorBoundary.

### 64.17 Required invariants

1. Every real error is visible locally or terminates its local boundary.
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
18. Toast stack details are closed initially.
19. Every user retry is rendered through RetryButton.
20. RetryButton is never invoked automatically.
21. ErrorBoundary reset occurs only through explicit user action.
22. A repeated failed retry produces a new Toast.
23. No programmer-controlled default or placeholder conceals an error.
24. Global browser error listeners remain installed while ToastProvider is mounted.
25. Global browser listeners do not replace normal query, mutation, or boundary handling.
26. Repository cache expiration produces no error presentation and restarts the complete ChangeSet snapshot.
27. A file `RetryButton` attempt has no HTTP timeout; automatic and initial file attempts retain their bounded timeout.
