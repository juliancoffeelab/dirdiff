## 2. Top-level infrastructure

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Before `v_new` implements workspace state, Tabs or backend data, it implements the complete top-level infrastructure specified in `../spec/01_tanstack_query.md`, `../spec/05_errors_and_toasts.md`, and `../spec/06_components_and_modules.md`.

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
frontend/src/new/api/queryClient.tsx
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

`new/api/queryClient.tsx` implements the final `QueryProvider`.

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
