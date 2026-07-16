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

/**
 * Returns the complete frontend tree selected by browser-local preference.
 *
 * An absent or unrecognized stored value selects the established default tree;
 * callers always receive one valid FrontendVersion.
 */
function selectedFrontendVersion(): FrontendVersion {
  const storedVersion = window.localStorage.getItem(
    FRONTEND_VERSION_STORAGE_KEY,
  );
  return storedVersion === "v_new" ? "v_new" : "v_old";
}

/**
 * Persists another complete frontend tree and reloads into its URL vocabulary.
 *
 * The destination is required. Translation is limited to browser field names at
 * this entrypoint boundary; neither mounted application receives mixed state.
 */
function switchFrontend(destination: FrontendVersion): void {
  // The temporary two-tree toggle translates browser workspace vocabulary at
  // the cutover boundary. Neither application accepts the other tree's URL.
  const search = new URLSearchParams(window.location.search);
  if (destination === "v_new") {
    const projectId = search.get("project_id");
    if (projectId !== null) {
      if (/^[1-9]\d*$/.test(projectId)) {
        search.set("repo_id", projectId);
      } else if (
        projectId === "diff" ||
        projectId === "fold" ||
        projectId === "gumtree" ||
        projectId === "scroll"
      ) {
        search.set("preset_type", projectId);
      }
    }
    search.delete("project_id");
  } else {
    const tab = search.get("tab");
    const mode = search.get("mode");
    if (tab === "preset" || mode === "preset") {
      const presetType = search.get("preset_type");
      if (presetType !== null) {
        search.set("project_id", presetType);
      }
    } else {
      const repoId = search.get("repo_id");
      if (repoId !== null) {
        search.set("project_id", repoId);
      }
    }
    search.delete("repo_id");
    search.delete("preset_type");
  }
  const query = search.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${query.length === 0 ? "" : `?${query}`}${window.location.hash}`,
  );
  window.localStorage.setItem(FRONTEND_VERSION_STORAGE_KEY, destination);
  window.location.reload();
}

/**
 * Mounts the established application as one isolated provider and style tree.
 *
 * The caller supplies the concrete root element. The returned promise resolves
 * after every tree-specific module has loaded and Solid has mounted the tree.
 */
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

/**
 * Mounts the replacement application as one isolated provider and style tree.
 *
 * The caller supplies the concrete root element. The returned promise resolves
 * after every tree-specific module has loaded and Solid has mounted the tree.
 */
async function mountNew(root: HTMLElement): Promise<void> {
  await import("./new/styles.css");
  const [solid, app, queries, toasts] = await Promise.all([
    import("solid-js"),
    import("./new/hud/App"),
    import("./new/api/queryClient"),
    import("./new/comp/Toasts"),
  ]);

  /**
   * Connects the toast owner to query failures and the root error boundary.
   *
   * The component owns provider composition only; application and toast state
   * remain in their respective descendants and ancestors.
   */
  function Root(): ReturnType<typeof app.App> {
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

/**
 * Resolves the required browser mount and starts exactly one application tree.
 *
 * A missing root is a programming error. Dynamic tree loading prevents providers
 * or styles from the unselected application from entering the mounted runtime.
 */
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
