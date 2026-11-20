import {
  ErrorBoundary,
  For,
  Show,
  createContext,
  createSignal,
  createUniqueId,
  onCleanup,
  onMount,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";
import { assert, expect } from "../utils";

const TIMEOUT_TOAST_TTL_MS = 10_000;

const UNDISPLAYABLE_THROWN_VALUE_MESSAGE =
  "Unable to display the thrown value.";

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

export type PresentedError = {
  message: string;
  details: string | null;
  reason: "timeout" | "other";
};

export type ToastCommands = {
  showError(title: string, error: unknown): void;
  showTransient(title: string, message: string, durationMs: number): void;
};

export type ErrorPopoverProps = {
  title: string;
  error: unknown;
  onRetry: () => void;
  trigger: JSX.Element;
  triggerClass: string;
  triggerLabel: string;
};

type ToastViewportProps = {
  toasts: Accessor<Toast[]>;
  onDismiss(id: number): void;
};

type ErrorWithReason = {
  error_reason: unknown;
};

function isErrorWithReason(error: object): error is ErrorWithReason {
  return "error_reason" in error;
}

type ErrorWithIssues = {
  issues: unknown;
};

function isErrorWithIssues(error: object): error is ErrorWithIssues {
  return "issues" in error;
}

type ParsedJson =
  | {
      parsed: false;
    }
  | {
      parsed: true;
      value: unknown;
    };

const ToastContext = createContext<ToastCommands>();

export function presentError(error: unknown): PresentedError {
  function errorReason(error: unknown): "timeout" | "other" {
    if (!isObject(error) || !isErrorWithReason(error)) {
      return "other";
    }

    return error.error_reason === "timeout" ? "timeout" : "other";
  }

  try {
    const message = primaryMessage(error);
    return {
      message,
      details: errorDetails(error, message),
      reason: errorReason(error),
    };
  } catch {
    return {
      message: UNDISPLAYABLE_THROWN_VALUE_MESSAGE,
      details: null,
      reason: "other",
    };
  }
}

export function ToastProvider(props: { children: JSX.Element }): JSX.Element {
  const [toasts, setToasts] = createSignal<Toast[]>([]);
  let nextToastId = 1;

  function showError(title: string, error: unknown): void {
    const presented = presentError(error);
    const toast: ErrorToast = {
      id: nextToastId,
      title,
      ...presented,
    };
    nextToastId += 1;
    setToasts((current) => [...current, toast]);
  }

  function showTransient(
    title: string,
    message: string,
    durationMs: number,
  ): void {
    assert(
      title.length > 0 && message.length > 0,
      "Transient Toasts require visible title and message text.",
    );
    assert(
      Number.isFinite(durationMs) && durationMs > 0,
      "Transient Toasts require a positive finite duration.",
    );
    const toast: TransientToast = {
      id: nextToastId,
      title,
      message,
      details: null,
      reason: "transient",
      durationMs,
    };
    nextToastId += 1;
    setToasts((current) => [...current, toast]);
  }

  function onWindowError(event: ErrorEvent): void {
    // Runtime errors provide the thrown value and its stack, while resource and
    // cross-origin failures may provide only the browser's message.
    const error = event.error == null ? event.message : event.error;
    showError("Unexpected error", error);
  }

  function onUnhandledRejection(event: PromiseRejectionEvent): void {
    showError("Unhandled promise rejection", event.reason);
  }

  onMount(() => {
    window.addEventListener("error", onWindowError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    onCleanup(() => {
      window.removeEventListener("error", onWindowError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    });
  });

  const commands: ToastCommands = { showError, showTransient };

  return (
    <ToastContext.Provider value={commands}>
      {props.children}
      <ToastViewport
        toasts={toasts}
        onDismiss={(id) => {
          // Dismissal removes the exact ID and is safe to repeat.
          setToasts((current) => current.filter((toast) => toast.id !== id));
        }}
      />
    </ToastContext.Provider>
  );
}

export function useToasts(): ToastCommands {
  return expect(useContext(ToastContext), "useToasts requires ToastProvider.");
}

export function ErrorPanel(props: {
  title: string;
  error: unknown;
  children: JSX.Element;
}): JSX.Element {
  const presented = () => presentError(props.error);

  return (
    <section class="notice error recoverable-error-notice" role="alert">
      <strong>{props.title}</strong>
      <pre class="render-error-message">{presented().message}</pre>
      <Show when={presented().details !== null}>
        <details class="error-traceback" open>
          <summary>Stack</summary>
          <pre>{presented().details}</pre>
        </details>
      </Show>
      {props.children}
    </section>
  );
}

export function RetryButton(props: { onRetry: () => void }): JSX.Element {
  return (
    <button type="button" onClick={props.onRetry}>
      Try again
    </button>
  );
}

export function ErrorPopover(props: ErrorPopoverProps): JSX.Element {
  const popoverId = createUniqueId();

  return (
    <>
      <button
        type="button"
        class={props.triggerClass}
        aria-label={props.triggerLabel}
        title={props.triggerLabel}
        popovertarget={popoverId}
      >
        {props.trigger}
      </button>
      <div id={popoverId} class="error-popover" popover="auto">
        <ErrorPanel title={props.title} error={props.error}>
          <RetryButton onRetry={props.onRetry} />
        </ErrorPanel>
      </div>
    </>
  );
}

export function UnexpectedErrorBoundary(props: {
  title: string;
  retryOnR: boolean;
  children: JSX.Element;
}): JSX.Element {
  return (
    <ErrorBoundary
      fallback={(error, reset) => (
        <UnexpectedErrorPanel
          title={props.title}
          error={error}
          onRetry={reset}
          retryOnR={props.retryOnR}
        />
      )}
    >
      {props.children}
    </ErrorBoundary>
  );
}

export function ApplicationErrorPanel(props: {
  error: unknown;
  onRetry: () => void;
}): JSX.Element {
  const toast = useToasts();

  onMount(() => {
    toast.showError("Application error", props.error);
  });

  return (
    <main class="app-shell app-crash">
      <div class="app-crash-notice">
        <ErrorPanel title="Something broke." error={props.error}>
          <RetryButton onRetry={props.onRetry} />
        </ErrorPanel>
      </div>
    </main>
  );
}

function ToastViewport(props: ToastViewportProps): JSX.Element {
  return (
    <div class="toast-viewport">
      <For each={props.toasts()}>
        {(toast) => (
          <ToastCard
            toast={toast}
            onDismiss={() => props.onDismiss(toast.id)}
          />
        )}
      </For>
    </div>
  );
}

function ToastCard(props: {
  toast: Toast;
  onDismiss: () => void;
}): JSX.Element {
  onMount(() => {
    if (props.toast.reason === "other") {
      return;
    }

    const durationMs =
      props.toast.reason === "transient"
        ? props.toast.durationMs
        : TIMEOUT_TOAST_TTL_MS;
    const timer = window.setTimeout(props.onDismiss, durationMs);
    onCleanup(() => {
      window.clearTimeout(timer);
    });
  });

  return (
    <section
      class="toast"
      classList={{
        "toast-error": props.toast.reason !== "transient",
        "toast-transient": props.toast.reason === "transient",
      }}
      role={props.toast.reason === "transient" ? "status" : "alert"}
    >
      <div class="toast-copy">
        <strong>{props.toast.title}</strong>
        <pre class="toast-message">{props.toast.message}</pre>
        <Show when={props.toast.details !== null}>
          <details class="toast-details">
            <summary>Stack</summary>
            <pre>{props.toast.details}</pre>
          </details>
        </Show>
      </div>
      <button
        class="toast-dismiss"
        type="button"
        aria-label="Dismiss notification"
        onClick={props.onDismiss}
      >
        x
      </button>
    </section>
  );
}

function UnexpectedErrorPanel(props: {
  title: string;
  error: unknown;
  onRetry: () => void;
  retryOnR: boolean;
}): JSX.Element {
  const toast = useToasts();

  onMount(() => {
    toast.showError(props.title, props.error);
    if (!props.retryOnR) {
      return;
    }

    function retryFromKeyboard(event: KeyboardEvent): void {
      if (
        event.defaultPrevented ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.code !== "KeyR"
      ) {
        return;
      }
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target instanceof HTMLInputElement ||
          target instanceof HTMLTextAreaElement ||
          target instanceof HTMLSelectElement)
      ) {
        return;
      }
      event.preventDefault();
      props.onRetry();
    }

    document.addEventListener("keydown", retryFromKeyboard);
    onCleanup(() => {
      document.removeEventListener("keydown", retryFromKeyboard);
    });
  });

  return (
    <ErrorPanel title={props.title} error={props.error}>
      <RetryButton onRetry={props.onRetry} />
    </ErrorPanel>
  );
}

function primaryMessage(error: unknown): string {
  function errorIssues(error: unknown): unknown[] | null {
    if (!isObject(error) || !isErrorWithIssues(error)) {
      return null;
    }

    return Array.isArray(error.issues) ? error.issues : null;
  }

  function parseJson(text: string): ParsedJson {
    try {
      return { parsed: true, value: JSON.parse(text) };
    } catch {
      return { parsed: false };
    }
  }

  function prettyJson(value: unknown): string {
    try {
      const formatted = JSON.stringify(value, null, 2);
      return formatted === undefined
        ? UNDISPLAYABLE_THROWN_VALUE_MESSAGE
        : formatted;
    } catch {
      return UNDISPLAYABLE_THROWN_VALUE_MESSAGE;
    }
  }

  function formattedText(text: string): string {
    const parsed = parseJson(text);
    return parsed.parsed ? prettyJson(parsed.value) : text;
  }

  const issues = errorIssues(error);
  if (issues !== null) {
    return prettyJson(issues);
  }

  if (error instanceof Error) {
    return formattedText(error.message);
  }

  if (typeof error === "string") {
    return formattedText(error);
  }

  return prettyJson(error);
}

function errorDetails(error: unknown, message: string): string | null {
  if (!(error instanceof Error)) {
    return null;
  }

  const stack = error.stack;
  if (stack === undefined || stack.length === 0 || stack === message) {
    return null;
  }
  return stack;
}

function isObject(value: unknown): value is object {
  return typeof value === "object" && value !== null;
}
