import { QueryClientProvider } from "@tanstack/solid-query";
import { ErrorBoundary } from "solid-js";
import { render } from "solid-js/web";
import { App } from "./App";
import { queryClient } from "./queryClient";
import { RootErrorFallback, ToastProvider, ToastViewport } from "./Toasts";

render(
  () => (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ErrorBoundary
          fallback={(error, reset) => (
            <RootErrorFallback error={error} reset={reset} />
          )}
        >
          <App />
        </ErrorBoundary>
        <ToastViewport />
      </ToastProvider>
    </QueryClientProvider>
  ),
  document.getElementById("root")!,
);
