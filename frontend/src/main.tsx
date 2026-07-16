// During the rewrite, the sole Vite entrypoint selects and mounts one complete
// frontend tree; all providers, application state, and CSS stay branch-owned.
import { render } from "solid-js/web";

/**
 * Identifies one complete frontend tree that the browser entrypoint may mount.
 *
 * The entrypoint persists exactly one of these values and uses it only to select
 * a root tree. It is not workspace, domain, or component state.
 */
type FrontendVersion = "v_old" | "v_new";

const FRONTEND_VERSION_STORAGE_KEY = "dirdiff:frontend-version";

function selectedFrontendVersion(): FrontendVersion {
  const storedVersion = window.localStorage.getItem(
    FRONTEND_VERSION_STORAGE_KEY,
  );
  return storedVersion === "v_new" ? "v_new" : "v_old";
}

function switchFrontend(destination: FrontendVersion): void {
  window.localStorage.setItem(FRONTEND_VERSION_STORAGE_KEY, destination);
  window.location.reload();
}

async function mountOld(root: HTMLElement): Promise<void> {
  await import("./styles.css");
  const [solid, query, app, queryClientModule, toasts] = await Promise.all([
    import("solid-js"),
    import("@tanstack/solid-query"),
    import("./App"),
    import("./queryClient"),
    import("./Toasts"),
  ]);

  render(
    () => (
      <query.QueryClientProvider client={queryClientModule.queryClient}>
        <toasts.ToastProvider>
          <solid.ErrorBoundary
            fallback={(error, reset) => (
              <toasts.RootErrorFallback error={error} reset={reset} />
            )}
          >
            <app.App onSwitchFrontend={() => switchFrontend("v_new")} />
          </solid.ErrorBoundary>
          <toasts.ToastViewport />
        </toasts.ToastProvider>
      </query.QueryClientProvider>
    ),
    root,
  );
}

async function mountNew(root: HTMLElement): Promise<void> {
  await import("./new/styles.css");
  const [solid, app, queries, toasts] = await Promise.all([
    import("solid-js"),
    import("./new/hud/App"),
    import("./new/api/queryClient"),
    import("./new/comp/Toasts"),
  ]);

  function Root() {
    const toast = toasts.useToasts();

    return (
      <queries.QueryProvider onError={toast.showError}>
        <solid.ErrorBoundary
          fallback={(error, reset) => (
            <toasts.ApplicationErrorPanel error={error} onRetry={reset} />
          )}
        >
          <app.App onSwitchFrontend={() => switchFrontend("v_old")} />
        </solid.ErrorBoundary>
      </queries.QueryProvider>
    );
  }

  render(
    () => (
      <toasts.ToastProvider>
        <Root />
      </toasts.ToastProvider>
    ),
    root,
  );
}

async function main(): Promise<void> {
  const root = document.getElementById("root");
  if (root === null) {
    throw new Error("The frontend root element #root is missing.");
  }

  switch (selectedFrontendVersion()) {
    case "v_old":
      await mountOld(root);
      return;
    case "v_new":
      await mountNew(root);
      return;
  }
}

void main();
