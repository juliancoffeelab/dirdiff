/**
 * Mounts the complete browser application and its top-level providers.
 *
 * This is the sole Vite entrypoint. It composes Toast, TanStack Query,
 * application-lifetime persisted-review-draft/write boundary,
 * root error handling, and App around the required document mount. JavaScript
 * updates reload the complete URL-backed application; CSS retains Vite's normal
 * stylesheet replacement.
 */
import { ErrorBoundary, type JSX } from "solid-js";
import { render } from "solid-js/web";
import { QueryProvider } from "./api/queryClient";
import { ApplicationErrorPanel, ToastProvider, useToasts } from "./comp/Toasts";
import { App } from "./hud/App";
import { ReviewDraftRoot } from "./hud/review/drafts";
import "./styles.css";

/**
 * Connects Toast reporting to query failures and the root error boundary.
 *
 * ToastProvider is mounted above this component, so `useToasts()` resolves the
 * single application Toast interface. QueryProvider receives that reporter,
 * while App receives no provider-composition details.
 */
function Root(): JSX.Element {
  const toast = useToasts();

  return (
    <QueryProvider onError={toast.showError}>
      <ErrorBoundary
        fallback={(error, reset) => (
          <ApplicationErrorPanel error={error} onRetry={reset} />
        )}
      >
        <ReviewDraftRoot>
          <App />
        </ReviewDraftRoot>
      </ErrorBoundary>
    </QueryProvider>
  );
}

/**
 * Resolves the required document mount and renders one application tree.
 *
 * A missing `#root` is an invalid host document and throws immediately.
 */
function main(): void {
  const root = document.getElementById("root");
  if (root === null) {
    throw new Error("The frontend root element #root is missing.");
  }

  render(
    () => (
      <ToastProvider>
        <Root />
      </ToastProvider>
    ),
    root,
  );
}

main();
