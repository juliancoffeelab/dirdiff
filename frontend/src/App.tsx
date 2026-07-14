import { Show, batch, createMemo, createSignal, onMount } from "solid-js";
import {
  fetchPreferences,
  preparePullRequest,
  type Preferences,
  type RepoDefaults,
  type ProjectId,
  type RepoMark,
} from "./api";
import type { DiffViewMode } from "./DiffGrid";
import {
  Header,
  type AppNotice,
  type InlineDiffNotice,
  type LoadingAppNotice,
} from "./Header";
import {
  Controls,
  type PresetCatalogsStatus,
  type RefChoicesStatus,
  type RepoSelectionStatus,
} from "./Controls";
import { FileList, FileTreeSidebar } from "./FileViews";
import { HunkNav } from "./Hud";
import { createDiffNavigation } from "./app/createDiffNavigation";
import { createDiffResources } from "./app/createDiffResources";
import { createDiffUiState } from "./app/createDiffUiState";
import {
  createRepoResources,
  initialControlsFromUrl,
} from "./app/createRepoResources";
import { type ControlsState, type RepoListStatus } from "./fileUtils";
import { GracefulErrorBoundary, useToasts } from "./Toasts";
import { loadStoredProfile, type StoredProfile } from "./storage";
import "./styles.css";

function initialDiffViewMode(): DiffViewMode {
  const view = new URLSearchParams(window.location.search).get("view");
  if (view === "split" || view === "inline") {
    return view;
  }
  return "inline";
}

function isInlineDiffNotice(notice: AppNotice): notice is InlineDiffNotice {
  return notice.id === "diff" && notice.placement === "inline";
}

function pullRequestUrlFromSearch(): string {
  const search = new URLSearchParams(window.location.search);
  if (search.get("tab") !== "pull-request") {
    return "";
  }
  return search.get("pull_request_url") ?? "";
}

export function App() {
  const [diffViewMode, setDiffViewMode] = createSignal<DiffViewMode>(
    initialDiffViewMode(),
  );
  const [controls, setControls] = createSignal<ControlsState | null>(
    initialControlsFromUrl(null),
  );
  const [baseSelectionDirty, setBaseSelectionDirty] = createSignal(false);
  const [reviewSelectionDirty, setReviewSelectionDirty] = createSignal(false);
  const [storedProfile, setStoredProfile] = createSignal<StoredProfile | null>(
    loadStoredProfile(),
  );
  const [preferences, setPreferences] = createSignal<Preferences | null>(null);
  const [preferencesPending, setPreferencesPending] = createSignal(false);
  const [preferencesError, setPreferencesError] = createSignal<string | null>(
    null,
  );
  let appRoot: HTMLElement | undefined;
  let appHeader: HTMLElement | undefined;
  const { addErrorToast } = useToasts();

  const repo = createRepoResources({ addErrorToast });
  const ui = createDiffUiState();
  const diff = createDiffResources({
    selectedProjectId: repo.selectedProjectId,
    controls,
    setControls,
    diffViewMode,
    resetViewState: ui.resetViewState,
    clearLoadedDiff: ui.clearLoadedDiff,
    applyManifest: ui.applyManifest,
    upsertFile: ui.upsertFile,
    upsertFiles: ui.upsertFiles,
    currentHydratedLazyKeys: ui.currentHydratedLazyKeys,
    directoryLabelForFileKey: ui.directoryLabelForFileKey,
    setDirectoryExpansion: ui.setDirectoryExpansion,
    setFileExpansion: ui.setFileExpansion,
    addErrorToast,
  });

  const setViewMode = (viewMode: DiffViewMode) => {
    setDiffViewMode(viewMode);
    diff.replaceUrlForCurrentParams(viewMode);
  };

  const toggleDiffViewMode = () => {
    switch (diffViewMode()) {
      case "split":
        setViewMode("inline");
        return;
      case "inline":
        setViewMode("split");
        return;
      default:
        setViewMode("split");
    }
  };

  const summary = () => ui.summary();
  const notices = createMemo<AppNotice[]>(() => {
    const nextNotices: AppNotice[] = [];
    const pushLoadingNotice = (id: LoadingAppNotice["id"]): void => {
      nextNotices.push({
        id,
        placement: "top",
        state: "loading",
      });
    };
    if (repo.selectedProjectId() !== null && repo.repoDefaultsPending()) {
      pushLoadingNotice("repo-defaults");
    }
    if (repo.selectedProjectId() !== null && repo.repoRefsPending()) {
      pushLoadingNotice("repo-refs");
    }
    if (repo.reposPending()) {
      pushLoadingNotice("marked-repos");
    }
    if (preferencesPending()) {
      pushLoadingNotice("preferences");
    }
    if (repo.presetCatalogsPending()) {
      pushLoadingNotice("presets");
    }
    const diffStatus = diff.status();
    nextNotices.push({
      id: "diff",
      placement: diffStatus.placement,
      state: diffStatus.state,
      text: diffStatus.text,
    });
    return nextNotices;
  });
  // App owns nullable resource signals because fetches can be pending, failed,
  // or intentionally not started. Component boundaries receive tagged statuses
  // instead, so rendering code has to name the state it is handling.
  const repoSelectionStatus = createMemo<RepoSelectionStatus>(() => {
    const projectId = repo.selectedProjectId();
    if (projectId === null) {
      return { state: "missing" };
    }
    return { state: "selected", projectId };
  });
  const refChoicesStatus = createMemo<RefChoicesStatus>(() => {
    const refs = repo.repoRefs();
    if (refs === null) {
      return { state: "missing" };
    }
    return { state: "loaded", value: refs.ref_choices };
  });
  const repoListStatus = createMemo<RepoListStatus>(() => {
    const repos = repo.repoList();
    if (repos === null) {
      return { state: "missing" };
    }
    return { state: "loaded", repos };
  });
  const presetCatalogsStatus = createMemo<PresetCatalogsStatus>(() => {
    const catalogs = repo.presetCatalogs();
    if (catalogs === null) {
      return { state: "missing" };
    }
    return { state: "loaded", value: catalogs };
  });
  const inlineDiffNotice = createMemo<InlineDiffNotice | null>(
    () => notices().find(isInlineDiffNotice) ?? null,
  );

  const loadPreferences = async (
    profile: StoredProfile | null = storedProfile(),
  ) => {
    if (profile === null) {
      batch(() => {
        setPreferences(null);
        setPreferencesError(null);
        setPreferencesPending(false);
      });
      return;
    }
    setPreferencesPending(true);
    try {
      const loadedPreferences = await fetchPreferences(profile.id);
      setPreferences(loadedPreferences);
      setPreferencesError(null);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to load preferences.";
      setPreferencesError(message);
      addErrorToast("Failed to load preferences", error);
    } finally {
      setPreferencesPending(false);
    }
  };

  const saveStoredProfileState = (profile: StoredProfile) => {
    setStoredProfile(profile);
    void loadPreferences(profile);
  };

  const forgetStoredProfileState = () => {
    batch(() => {
      setStoredProfile(null);
      setPreferences(null);
      setPreferencesError(null);
      setPreferencesPending(false);
    });
  };

  const aggressiveFolds = () => preferences()?.aggressive_folds ?? true;

  const reloadDiff = async () => {
    const diffParams = diff.currentParams();
    const paramsIdentity = diff.currentParamsIdentity();
    if (
      diffParams !== null &&
      paramsIdentity !== null &&
      diffParams.mode === "preset"
    ) {
      try {
        await repo.reloadPresetCatalogs();
      } catch {
        diff.resetDiffState("error", "Failed to reload presets.", "inline");
        return;
      }
      if (diff.currentParamsIdentity() !== paramsIdentity) {
        return;
      }
    }
    diff.reloadDiff();
  };

  const navigation = createDiffNavigation({
    appRoot: () => appRoot,
    appHeader: () => appHeader,
    displayFiles: ui.displayFiles,
    manifestFileCount: ui.manifestFileCount,
    diffRevision: ui.diffRevision,
    isFileVirtualized: ui.isFileVirtualized,
    layoutRevision: ui.layoutRevision,
    virtualizationRevision: ui.virtualizationRevision,
    loadingRevision: diff.loadingRevision,
    diffViewMode,
    setDirectoryExpansion: ui.setDirectoryExpansion,
    setFileExpansion: ui.setFileExpansion,
    setForcedRichPreloadIds: ui.setForcedRichPreloadIds,
    forceRichFileId: ui.forceRichFileId,
    setActiveHunkFileId: ui.setActiveHunkFileId,
    reloadDiff,
    toggleDiffViewMode,
    setAllFilesExpanded: ui.setAllFilesExpanded,
    openFileExpansion: ui.openFileExpansion,
    openTreeDirectoryExpansion: ui.openTreeDirectoryExpansion,
    directoryLabelForFileKey: ui.directoryLabelForFileKey,
  });

  const applyRepoDefaults = (defaults: RepoDefaults): ControlsState | null => {
    let nextControls: ControlsState | null = null;
    setControls((current) => {
      if (current === null || current.tab === "pull-request") {
        // PR preparation already knows the exact base/review branches. Repo
        // defaults loaded in parallel are only metadata for later editing.
        nextControls = current;
        return current;
      }
      const next = {
        ...current,
        // Late defaults may arrive after the user has already typed. These
        // guards are the invariant: repo metadata may fill only genuinely
        // missing branch-review draft fields, never replace user intent.
        baseSelection:
          current.baseSelection.state === "missing" && !baseSelectionDirty()
            ? initialControlsFromUrl(defaults).baseSelection
            : current.baseSelection,
        reviewSelection:
          current.reviewSelection.state === "missing" && !reviewSelectionDirty()
            ? initialControlsFromUrl(defaults).reviewSelection
            : current.reviewSelection,
      };
      nextControls = next;
      return next;
    });
    return nextControls;
  };

  const loadInitialControlsIfReady = (
    nextControls: ControlsState | null,
    engine = diff.engine(),
  ) => {
    if (nextControls === null) {
      return;
    }
    if (
      nextControls.tab === "pull-request" &&
      nextControls.pullRequestUrl.length > 0
    ) {
      // PR URLs are self-contained: they can prepare/select a repo without an
      // already-selected header repo, so startup should not wait for /api/repos.
      void loadPullRequest(nextControls.pullRequestUrl);
      return;
    }
    if (
      nextControls.mode === "branch-review" &&
      (nextControls.baseSelection.state === "missing" ||
        nextControls.reviewSelection.state === "missing")
    ) {
      diff.resetDiffState(
        "idle",
        "Choose branches to load a review diff.",
        "inline",
      );
      return;
    }
    if (nextControls.mode === "preset") {
      // Preset catalogs are intentionally lazy. The preset tab can render before
      // this request; loading is needed only when startup must execute a preset.
      void repo.loadPresetCatalogs();
      if (nextControls.preset.length === 0) {
        diff.resetDiffState(
          "idle",
          "Choose a preset to load a diff.",
          "inline",
        );
        return;
      }
    }
    diff.loadInitialControls(nextControls, engine);
  };

  const loadRepoMetadata = (
    projectId: ProjectId,
    loadBranchReviewAfterDefaults: boolean,
  ) => {
    // Refs and defaults are independent metadata. Start both, but only defaults
    // can unblock an automatic branch-review load because refs are suggestions.
    void repo.loadRepoRefs(projectId);
    void repo.loadRepoDefaults(projectId).then((defaults) => {
      if (defaults === null) {
        return;
      }
      const nextControls = applyRepoDefaults(defaults);
      if (loadBranchReviewAfterDefaults) {
        loadInitialControlsIfReady(nextControls);
      }
    });
  };

  const selectRepo = (repoMark: RepoMark) => {
    repo.selectRepo(repoMark);
    batch(() => {
      setBaseSelectionDirty(false);
      setReviewSelectionDirty(false);
      // Repo defaults are repo-scoped. When switching repositories, existing
      // branch-review selections become unknown until the new repo's defaults
      // arrive or the user types explicit replacements.
      setControls((current) =>
        current === null
          ? current
          : {
              ...current,
              baseSelection: { state: "missing" },
              reviewSelection: { state: "missing" },
            },
      );
      diff.clearCurrentParams();
      diff.resetDiffState("idle", "Preparing diff...", "top");
    });
    // Use the current draft after selectRepo's synchronous reset. This lets a
    // user choose a repo from any tab without rebuilding controls from URL.
    loadRepoMetadata(repoMark.id, controls()?.mode === "branch-review");
    loadInitialControlsIfReady(controls());
  };

  const removeRepo = async (repoMark: RepoMark) => {
    diff.resetDiffState("loading", "Removing marked repo...", "top");
    try {
      await repo.removeRepo(repoMark.id);
      diff.resetDiffState("idle", "Choose a repo.", "top");
    } catch (error) {
      diff.resetDiffState(
        "error",
        error instanceof Error
          ? error.message
          : "Failed to remove marked repo.",
        "inline",
      );
    }
  };

  const loadPullRequest = async (url: string) => {
    const pullRequestUrl = url.trim();
    if (pullRequestUrl.length === 0) {
      diff.resetDiffState("error", "Enter a pull request URL.", "inline");
      return;
    }
    diff.resetDiffState("loading", "Preparing pull request...", "top");
    try {
      const prepared = await preparePullRequest(pullRequestUrl);
      repo.selectProjectId(prepared.project_id);
      // PR preparation returns authoritative branch selections. Repo metadata is
      // still useful for subsequent autocomplete, but not required to load.
      loadRepoMetadata(prepared.project_id, false);
      setControls({
        ...initialControlsFromUrl(null),
        tab: "pull-request",
        mode: "branch-review",
        baseSelection: {
          state: "selected",
          value: {
            source: "remote",
            remote: prepared.base_branch.remote,
            branch: prepared.base_branch.branch,
          },
        },
        reviewSelection: {
          state: "selected",
          value: {
            source: "remote",
            remote: prepared.review_branch.remote,
            branch: prepared.review_branch.branch,
          },
        },
        pullRequestUrl: prepared.pull_request_url,
      });
      diff.loadPullRequest(prepared, diff.engine());
    } catch (error) {
      addErrorToast("Failed to prepare pull request", error);
      diff.resetDiffState(
        "error",
        error instanceof Error
          ? error.message
          : "Failed to prepare pull request.",
        "inline",
      );
    }
  };

  onMount(() => {
    void loadPreferences();
    const pullRequestUrl = pullRequestUrlFromSearch();
    if (pullRequestUrl.length > 0) {
      void loadPullRequest(pullRequestUrl);
    } else {
      diff.resetDiffState("idle", "Choose a repo to load a diff.", "inline");
      const currentControls = controls();
      if (
        currentControls?.mode === "preset" &&
        currentControls.preset.length > 0
      ) {
        // Preset URLs are self-contained like PR URLs, but they do not need a
        // prepare step or a selected repo. Start them before /api/repos resolves
        // so a missing or invalid header repo cannot block fixture-backed diffs.
        loadInitialControlsIfReady(currentControls);
      }
    }
    void repo.loadReposFromUrl().then((validatedProjectId) => {
      if (pullRequestUrl.length > 0) {
        // The PR path selected its repo via preparePullRequest. Let that flow own
        // metadata and diff loading instead of racing /api/repos validation.
        return;
      }
      if (validatedProjectId === null) {
        return;
      }
      const currentControls = controls();
      // `/api/repos` may resolve after the user has already edited the visible
      // draft. Startup must continue from that current draft, not from a stale
      // snapshot taken during mount.
      loadRepoMetadata(
        validatedProjectId,
        currentControls?.mode === "branch-review",
      );
      if (
        currentControls?.mode !== "branch-review" &&
        currentControls?.mode !== "preset"
      ) {
        loadInitialControlsIfReady(currentControls);
      }
    });
  });

  return (
    <main ref={appRoot} class="app-shell">
      <Header
        storedProfile={storedProfile()}
        preferences={preferences()}
        preferencesPending={preferencesPending()}
        preferencesError={preferencesError()}
        onProfileSaved={saveStoredProfileState}
        onProfileForgotten={forgetStoredProfileState}
        onPreferencesSaved={setPreferences}
        onReloadPreferences={loadPreferences}
        repos={repoListStatus()}
        selectedProjectId={repo.selectedProjectId()}
        engine={diff.engine()}
        viewMode={diffViewMode()}
        summary={summary()}
        loadedFilesStatus={diff.status().loadedFiles}
        notices={notices()}
        onHeaderMount={(element) => {
          appHeader = element;
        }}
        onRepoListOpen={repo.refreshRepos}
        onRepoChange={selectRepo}
        onRepoRemove={removeRepo}
        onEngineChange={diff.loadEngine}
        onViewModeChange={setViewMode}
      />

      <Show when={repo.repoRefsError() !== null}>
        <section class="notice error">
          Failed to load refs: {String(repo.repoRefsError())}
        </section>
      </Show>

      <Show when={repo.repoDefaultsError() !== null}>
        <section class="notice error">
          Failed to load repo defaults: {String(repo.repoDefaultsError())}
        </section>
      </Show>

      <Show when={repo.reposError() !== null}>
        <section class="notice error">
          Failed to load marked repos: {String(repo.reposError())}
        </section>
      </Show>

      <Show when={preferencesError() !== null}>
        <section class="notice error">
          Failed to load preferences: {preferencesError()}
        </section>
      </Show>

      <Show when={controls()}>
        {(currentControls) => (
          <>
            <Controls
              controls={currentControls()}
              repoSelection={repoSelectionStatus()}
              repos={repoListStatus()}
              repoSelectionError={repo.repoSelectionError()}
              refChoices={refChoicesStatus()}
              presetCatalogs={presetCatalogsStatus()}
              presetCatalogsError={repo.presetCatalogsError()}
              onPresetMode={repo.loadPresetCatalogs}
              onAgainstHead={diff.loadAgainstHead}
              onPreset={diff.loadPreset}
              onRefs={diff.loadRefs}
              onPullRequest={loadPullRequest}
              onBranchReview={diff.loadBranchReview}
              mainBranchSaving={repo.mainBranchSaving()}
              onSaveMainBranch={repo.saveMainBranch}
              onBranchSelectionEdit={(slot) => {
                if (slot === "base") {
                  setBaseSelectionDirty(true);
                  return;
                }
                setReviewSelectionDirty(true);
              }}
              onControlsDraftChange={setControls}
              onRepoSelect={selectRepo}
              onRepoRemove={removeRepo}
              onRefsMode={() => {
                const projectId = repo.selectedProjectId();
                if (projectId !== null && repo.repoRefs() === null) {
                  void repo.loadRepoRefs(projectId);
                }
              }}
              onBranchReviewMode={() => {
                const projectId = repo.selectedProjectId();
                if (projectId !== null) {
                  if (repo.repoDefaults() === null) {
                    void repo.loadRepoDefaults(projectId).then((defaults) => {
                      if (defaults !== null) {
                        applyRepoDefaults(defaults);
                      }
                    });
                  }
                  if (repo.repoRefs() === null) {
                    void repo.loadRepoRefs(projectId);
                  }
                }
              }}
            />
            <Show when={inlineDiffNotice()}>
              {(notice) => (
                <p class={`status ${notice().state}`}>{notice().text}</p>
              )}
            </Show>
            <div class="repo-fold-controls">
              <Show
                when={ui.displayFiles().length > 0}
                fallback={
                  <>
                    <button type="button" disabled>
                      Fold all
                    </button>
                    <button type="button" disabled>
                      Show all
                    </button>
                  </>
                }
              >
                <button
                  type="button"
                  onClick={() => ui.setAllFilesExpanded(false)}
                >
                  Fold all
                </button>
                <button
                  type="button"
                  onClick={() => ui.setAllFilesExpanded(true)}
                >
                  Show all
                </button>
              </Show>
            </div>
            {/* FileTreeSidebar and FileList must stay behind the same readiness
          boundary. They share scroll/layout state; rendering one without the
          other has broken split-view layout in previous UI iterations. */}
            <Show when={ui.displayFiles().length > 0}>
              <div
                class="diff-workspace"
                classList={{
                  "diff-workspace-inline": diffViewMode() === "inline",
                  "diff-workspace-tree-open": navigation.fileTreeOpen(),
                }}
              >
                <GracefulErrorBoundary title="Could not render file tree">
                  <FileTreeSidebar
                    files={ui.displayFiles()}
                    tree={ui.displayFileTree()}
                    directoryExpansion={ui.directoryExpansion}
                    fileExpansion={ui.fileExpansion}
                    activeHunkFileId={ui.activeHunkFileId()}
                    isActiveHunkFileId={ui.isActiveHunkFileId}
                    isFileVirtualized={ui.isFileVirtualized}
                    viewMode={diffViewMode()}
                    open={navigation.fileTreeOpen()}
                    onOpenChange={navigation.setFileTreeOpen}
                    setDirectoryExpansion={ui.setDirectoryExpansion}
                    setFileExpansion={ui.setFileExpansion}
                    onScrollToDirectory={navigation.scrollToTreeDirectory}
                    onScrollToFile={navigation.scrollToFile}
                  />
                </GracefulErrorBoundary>
                <GracefulErrorBoundary title="Could not render diff">
                  <FileList
                    files={ui.displayFiles()}
                    cacheId={diff.cacheId()}
                    hunkPosition={navigation.hunkPosition()}
                    fileExpansion={ui.fileExpansion}
                    loadingFiles={diff.loadingFiles}
                    fileErrors={diff.fileErrors}
                    linePin={navigation.linePin()}
                    isForcedRichFileId={ui.isForcedRichFileId}
                    aggressiveFolds={aggressiveFolds()}
                    onFileVirtualizedChange={ui.setFileVirtualized}
                    onHydrateFile={diff.hydrateFile}
                    diffViewMode={diffViewMode()}
                    setFileExpansion={ui.setFileExpansion}
                  />
                </GracefulErrorBoundary>
              </div>
            </Show>
            <HunkNav
              debugOpen={navigation.debugMenuOpen()}
              helpOpen={navigation.helpOpen()}
              hunkPosition={navigation.hunkPosition()}
              onHelpOpenChange={navigation.setHelpOpen}
              onNext={navigation.scrollNext}
              onPrev={navigation.scrollPrev}
            />
          </>
        )}
      </Show>
    </main>
  );
}
