import {
  ErrorBoundary,
  For,
  Show,
  createContext,
  createSignal,
  onCleanup,
  onMount,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";

type ToastTone = "error";
type ErrorReason = "timeout" | "other";

type Toast = {
  id: number;
  tone: ToastTone;
  title: string;
  message: string;
  details: string | null;
};

type ToastContextValue = {
  toasts: Accessor<Toast[]>;
  addErrorToast: (title: string, error: unknown) => void;
  dismissToast: (id: number) => void;
};

const ToastContext = createContext<ToastContextValue>();
const TIMEOUT_TOAST_TTL_MS = 10_000;

let nextToastId = 1;

export function ToastProvider(props: { children: JSX.Element }) {
  const [toasts, setToasts] = createSignal<Toast[]>([]);
  const expiryTimers = new Map<number, number>();

  const dismissToast = (id: number) => {
    const timerId = expiryTimers.get(id);
    if (timerId !== undefined) {
      window.clearTimeout(timerId);
      expiryTimers.delete(id);
    }
    setToasts((current) => current.filter((toast) => toast.id !== id));
  };

  const addErrorToast = (title: string, error: unknown) => {
    const id = nextToastId;
    nextToastId += 1;
    const message = errorPrimary(error);
    setToasts((current) => [
      ...current,
      {
        id,
        tone: "error",
        title,
        message,
        details: errorDetails(error),
      },
    ]);
    if (errorReason(error) === "timeout") {
      const timerId = window.setTimeout(() => {
        dismissToast(id);
      }, TIMEOUT_TOAST_TTL_MS);
      expiryTimers.set(id, timerId);
    }
  };

  onMount(() => {
    const onError = (event: ErrorEvent) => {
      const error = event.error === undefined ? event.message : event.error;
      addErrorToast("Unexpected error", error);
    };
    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      addErrorToast("Unhandled promise rejection", event.reason);
    };

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    onCleanup(() => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
      for (const timerId of expiryTimers.values()) {
        window.clearTimeout(timerId);
      }
      expiryTimers.clear();
    });
  });

  return (
    <ToastContext.Provider value={{ toasts, addErrorToast, dismissToast }}>
      {props.children}
    </ToastContext.Provider>
  );
}

export function useToasts(): ToastContextValue {
  const context = useContext(ToastContext);
  if (context === undefined) {
    throw new Error("useToasts must be used inside ToastProvider.");
  }
  return context;
}

export function ToastViewport() {
  const { toasts, dismissToast } = useToasts();
  return (
    <div
      class="toast-viewport"
      aria-live="assertive"
      aria-relevant="additions removals"
    >
      <For each={toasts()}>
        {(toast) => (
          <section class={`toast toast-${toast.tone}`} role="alert">
            <div class="toast-copy">
              <strong>{toast.title}</strong>
              <pre class="toast-message">{toast.message}</pre>
              <Show when={toast.details !== null}>
                <details class="toast-details">
                  <summary>Stack</summary>
                  <pre>{toast.details}</pre>
                </details>
              </Show>
            </div>
            <button
              class="toast-dismiss"
              type="button"
              aria-label="Dismiss notification"
              onClick={() => dismissToast(toast.id)}
            >
              x
            </button>
          </section>
        )}
      </For>
    </div>
  );
}

export function RootErrorFallback(props: {
  error: unknown;
  reset: () => void;
}) {
  const { addErrorToast } = useToasts();

  onMount(() => {
    addErrorToast("Application error", props.error);
  });

  return (
    <main class="app-shell app-crash">
      <section class="notice error app-crash-notice">
        <h1>Something broke.</h1>
        <pre class="render-error-message">{errorPrimary(props.error)}</pre>
        <ErrorTraceback error={props.error} />
        <button type="button" onClick={props.reset}>
          Try again
        </button>
      </section>
    </main>
  );
}

export function GracefulErrorBoundary(props: {
  title: string;
  children: JSX.Element;
}) {
  return (
    <ErrorBoundary
      fallback={(error, reset) => (
        <RecoverableErrorFallback
          title={props.title}
          error={error}
          reset={reset}
        />
      )}
    >
      {props.children}
    </ErrorBoundary>
  );
}

function RecoverableErrorFallback(props: {
  title: string;
  error: unknown;
  reset: () => void;
}) {
  const { addErrorToast } = useToasts();

  onMount(() => {
    addErrorToast(props.title, props.error);
  });

  return (
    <section class="notice error recoverable-error-notice" role="alert">
      <strong>{props.title}</strong>
      <pre class="render-error-message">{errorPrimary(props.error)}</pre>
      <ErrorTraceback error={props.error} />
      <button type="button" onClick={props.reset}>
        Try again
      </button>
    </section>
  );
}

function ErrorTraceback(props: { error: unknown }) {
  const details = errorDetails(props.error);
  return (
    <Show when={details !== null}>
      <details class="error-traceback" open>
        <summary>Stack</summary>
        <pre>{details}</pre>
      </details>
    </Show>
  );
}

type ZodIssueLike = {
  path?: unknown;
  message?: unknown;
  expected?: unknown;
  received?: unknown;
};

function errorPrimary(error: unknown): string {
  const directIssues = zodIssues(error);
  if (directIssues !== null) {
    return prettyJson(directIssues);
  }
  if (error instanceof Error) {
    const primaryFromMessage = structuredErrorText(error.message);
    if (primaryFromMessage !== null) {
      return primaryFromMessage;
    }
    return error.message;
  }
  if (typeof error === "string") {
    const structuredText = structuredErrorText(error);
    if (structuredText !== null) {
      return structuredText;
    }
    return error;
  }
  return prettyUnknown(error);
}

function errorDetails(error: unknown): string | null {
  if (error instanceof Error) {
    const primary = errorPrimary(error);
    if (error.stack !== undefined && error.stack.length > 0) {
      return error.stack === primary ? null : error.stack;
    }
    return null;
  }
  return null;
}

function errorReason(error: unknown): ErrorReason {
  if (typeof error !== "object" || error === null) {
    return "other";
  }
  if (!("error_reason" in error)) {
    return "other";
  }
  const reason = error.error_reason;
  if (reason === "timeout") {
    return "timeout";
  }
  return "other";
}

function zodIssues(error: unknown): ZodIssueLike[] | null {
  if (typeof error !== "object" || error === null) {
    return null;
  }
  if (!("issues" in error)) {
    return null;
  }
  const issues = error.issues;
  if (!Array.isArray(issues)) {
    return null;
  }
  return issues;
}

function structuredErrorText(text: string): string | null {
  const parsed = parseJsonText(text);
  if (parsed === null) {
    return null;
  }
  return prettyUnknown(parsed);
}

function parseJsonText(text: string): unknown | null {
  const trimmed = text.trim();
  if (
    !trimmed.startsWith("[") &&
    !trimmed.startsWith("{") &&
    !trimmed.startsWith('"')
  ) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function prettyUnknown(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value instanceof Error) {
    return value.message;
  }
  return prettyJson(value);
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
