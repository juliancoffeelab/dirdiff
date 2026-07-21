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
 * this entrypoint boundary; neither mounted application receives mixed state. An
 * empty source query remains empty, while nonempty state that cannot be translated
 * completely is discarded rather than partially retained or inferred.
 */
function switchFrontend(destination: FrontendVersion): void {
  // The temporary two-tree toggle translates browser workspace vocabulary at
  // the cutover boundary. Neither application accepts the other tree's URL.
  let search = new URLSearchParams(window.location.search);
  let translationFailed = false;
  const sourceQueryEmpty = search.size === 0;
  if (destination === "v_new") {
    const mode = search.get("mode");
    const tab = search.get("tab");
    const projectId = search.get("project_id");

    if (sourceQueryEmpty) {
      // Both frontends define their own behavior for a genuinely empty URL.
    } else if (search.has("repo_id") || search.has("preset_type")) {
      translationFailed = true;
    } else if (mode === null || search.has("cache_id")) {
      translationFailed = true;
    } else if (tab !== null && tab !== "pull-request") {
      translationFailed = true;
    } else if (tab === "pull-request" && mode !== "branch-review") {
      translationFailed = true;
    } else if (
      mode !== null &&
      mode !== "head" &&
      mode !== "refs" &&
      mode !== "branch-review" &&
      mode !== "preset"
    ) {
      translationFailed = true;
    } else if (mode === "preset") {
      if (
        projectId === "diff" ||
        projectId === "fold" ||
        projectId === "gumtree" ||
        projectId === "scroll"
      ) {
        search.set("tab", "preset");
        search.set("preset_type", projectId);
      } else {
        translationFailed = true;
      }
    } else {
      search.set("tab", tab === "pull-request" ? "pull-request" : mode);
      if (projectId !== null) {
        if (/^[1-9]\d*$/.test(projectId)) {
          search.set("repo_id", projectId);
        } else {
          translationFailed = true;
        }
      }
    }

    if (!translationFailed) {
      search.delete("mode");
      search.delete("project_id");
      search.delete("show_untracked");
    }
  } else {
    const tab = search.get("tab");
    const repoId = search.get("repo_id");
    const presetType = search.get("preset_type");

    if (sourceQueryEmpty) {
      // Both frontends define their own behavior for a genuinely empty URL.
    } else if (
      search.has("mode") ||
      search.has("project_id") ||
      search.has("cache_id") ||
      search.has("show_untracked")
    ) {
      translationFailed = true;
    } else if (tab === null) {
      translationFailed = true;
    } else if (
      tab !== null &&
      tab !== "head" &&
      tab !== "refs" &&
      tab !== "branch-review" &&
      tab !== "pull-request" &&
      tab !== "preset"
    ) {
      translationFailed = true;
    } else if (tab === "preset") {
      if (
        presetType === "diff" ||
        presetType === "fold" ||
        presetType === "gumtree" ||
        presetType === "scroll"
      ) {
        search.set("mode", "preset");
        search.set("project_id", presetType);
        search.delete("tab");
      } else {
        translationFailed = true;
      }
    } else if (presetType !== null) {
      translationFailed = true;
    } else if (repoId !== null && !/^[1-9]\d*$/.test(repoId)) {
      translationFailed = true;
    } else {
      search.set("mode", tab === "pull-request" ? "branch-review" : tab);
      if (tab === "pull-request") {
        search.set("tab", "pull-request");
      } else {
        search.delete("tab");
      }
      if (repoId !== null) {
        search.set("project_id", repoId);
      }
    }

    if (!translationFailed) {
      search.delete("repo_id");
      search.delete("preset_type");
    }
  }

  if (translationFailed) {
    // An invalid or contradictory source URL has no conservative translation.
    search = new URLSearchParams();
  }
  const query = search.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${query.length === 0 ? "" : `?${query}`}${translationFailed ? "" : window.location.hash}`,
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
