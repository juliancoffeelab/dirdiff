import { ErrorBoundary, type JSX } from "solid-js";
import { render } from "solid-js/web";
import { QueryProvider } from "./api/queryClient";
import { ApplicationErrorPanel, ToastProvider, useToasts } from "./comp/Toasts";
import { App } from "./hud/App";
import { ReviewDraftRoot } from "./hud/review/drafts";
import { expect } from "./utils";
import "./styles.css";

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

function main(): void {
  const root = expect(
    document.getElementById("root"),
    "The frontend root element #root is missing.",
  );

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
