import {
  For,
  createContext,
  createSignal,
  onCleanup,
  onMount,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";

type ToastTone = "error";

type Toast = {
  id: number;
  tone: ToastTone;
  title: string;
  message: string;
};

type ToastContextValue = {
  toasts: Accessor<Toast[]>;
  addErrorToast: (title: string, error: unknown) => void;
  dismissToast: (id: number) => void;
};

const ToastContext = createContext<ToastContextValue>();

let nextToastId = 1;

export function ToastProvider(props: { children: JSX.Element }) {
  const [toasts, setToasts] = createSignal<Toast[]>([]);

  const dismissToast = (id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  };

  const addErrorToast = (title: string, error: unknown) => {
    const id = nextToastId;
    nextToastId += 1;
    setToasts((current) => [
      ...current,
      {
        id,
        tone: "error",
        title,
        message: errorMessage(error),
      },
    ]);
    window.setTimeout(() => dismissToast(id), 9000);
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
              <p>{toast.message}</p>
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
        <p>{errorMessage(props.error)}</p>
        <button type="button" onClick={props.reset}>
          Try again
        </button>
      </section>
    </main>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "string") {
    return error;
  }
  return "Unexpected error.";
}
