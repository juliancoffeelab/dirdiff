/**
 * Defines application-wide notification and fatal-error components.
 *
 * The module exports the notification ownership boundary, the single viewport
 * where notifications are presented, and the root error presentation. It does
 * not own domain errors, decide whether an operation may be retried, or perform
 * recovery without an explicit caller-provided action.
 */
import type { JSX } from "solid-js";

/**
 * Establishes notification ownership for an application subtree.
 *
 * Callers provide the complete subtree as `children` and mount one provider at
 * its root. Notification producers and the viewport belong beneath this same
 * ownership boundary.
 */
export function ToastProvider(props: { children: JSX.Element }) {
  // Notification storage is added inside this boundary when Toast behavior is
  // implemented; keeping the boundary now fixes its ownership and call site.
  return props.children;
}

/**
 * Renders the live region where application notifications are presented.
 *
 * Callers mount exactly one viewport beneath `ToastProvider` and outside the
 * root application ErrorBoundary so notifications remain visible after a root
 * rendering error. The viewport owns presentation only; notification state
 * remains with the provider.
 */
export function ToastViewport() {
  return (
    <div
      class="toast-viewport"
      aria-live="assertive"
      aria-relevant="additions removals"
    />
  );
}

/**
 * Presents an uncaught root rendering failure and an explicit retry action.
 *
 * The owning ErrorBoundary supplies the original error and its reset callback.
 * The component displays the error without transforming it and invokes `reset`
 * only when the user presses the retry button; it never retries automatically.
 */
export function RootErrorFallback(props: {
  error: unknown;
  reset: () => void;
}) {
  const message =
    props.error instanceof Error ? props.error.message : String(props.error);

  return (
    <main class="app-shell app-crash">
      <section class="notice error app-crash-notice">
        <h1>Something broke.</h1>
        <pre class="render-error-message">{message}</pre>
        <button type="button" onClick={props.reset}>
          Try again
        </button>
      </section>
    </main>
  );
}
