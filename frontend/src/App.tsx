import { Show, batch, createSignal, onMount } from "solid-js";
import {
  fetchPreferences,
  type Preferences,
  type RepoId,
  type RepoMark,
} from "./api";
import type { DiffViewMode } from "./DiffGrid";
import { Header } from "./Header";
import { Controls } from "./Controls";
import { FileList, FileTreeSidebar } from "./FileViews";
import { HunkNav } from "./Hud";
import { RepoPicker } from "./RepoPicker";
import { createDiffNavigation } from "./app/createDiffNavigation";
import { createDiffResources } from "./app/createDiffResources";
import { createDiffUiState } from "./app/createDiffUiState";
import {
  type InitialRepoDiff,
  createRepoResources,
} from "./app/createRepoResources";
import { type ControlsState } from "./fileUtils";
import { GracefulErrorBoundary, useToasts } from "./Toasts";
import "./styles.css";

function initialDiffViewMode(): DiffViewMode {
  const view = new URLSearchParams(window.location.search).get("view");
  if (view === "split" || view === "inline") {
    return view;
  }
  return "inline";
}

export function App() {
  const [diffViewMode, setDiffViewMode] = createSignal<DiffViewMode>(
    initialDiffViewMode(),
  );
  const [controls, setControls] = createSignal<ControlsState | null>(null);
  const [preferences, setPreferences] = createSignal<Preferences | null>(null);
  const [preferencesPending, setPreferencesPending] = createSignal(true);
  const [preferencesError, setPreferencesError] = createSignal<string | null>(
    null,
  );
  let appRoot: HTMLElement | undefined;
  let appHeader: HTMLElement | undefined;
  const { addErrorToast } = useToasts();

  const repo = createRepoResources({ addErrorToast });
  const ui = createDiffUiState();
  const diff = createDiffResources({
    selectedRepoId: repo.selectedRepoId,
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
    refChoices: repo.refChoices,
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

  const loadPreferences = async () => {
    setPreferencesPending(true);
    try {
      const loadedPreferences = await fetchPreferences();
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
        diff.resetDiffState("error", "Failed to reload presets.");
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

  const loadInitialDiff = (initial: InitialRepoDiff) => {
    batch(() => {
      setControls(initial.controls);
    });
    if (initial.controls.mode === "preset") {
      void repo.loadPresetCatalogs();
      if (initial.controls.preset.length === 0) {
        diff.resetDiffState("idle", "Choose a preset to load a diff.");
        return;
      }
    }
    diff.loadInitialControls(initial.controls, initial.engine);
  };

  const initializeRepo = async (repoId: RepoId) => {
    diff.resetDiffState("idle", "Loading refs...");
    try {
      const initial = await repo.initializeRepo(repoId);
      if (initial !== null) {
        loadInitialDiff(initial);
      }
    } catch (error) {
      addErrorToast("Failed to load repo refs", error);
      batch(() => {
        diff.clearCurrentParams();
        diff.resetDiffState(
          "error",
          error instanceof Error ? error.message : "Failed to load repo refs.",
        );
      });
    }
  };

  const selectRepo = (repoMark: RepoMark) => {
    repo.selectRepo(repoMark);
    batch(() => {
      setControls(null);
      diff.clearCurrentParams();
      diff.resetDiffState("idle", "Preparing diff...");
    });
    void initializeRepo(repoMark.id);
  };

  onMount(() => {
    void loadPreferences();
    void (async () => {
      const repoId = await repo.loadReposFromUrl();
      if (repoId === null) {
        diff.resetDiffState("idle", "Choose a repo to load a diff.");
        return;
      }
      await initializeRepo(repoId);
    })();
  });

  return (
    <main ref={appRoot} class="app-shell">
      <Header
        preferences={preferences()}
        preferencesPending={preferencesPending()}
        preferencesError={preferencesError()}
        onPreferencesSaved={setPreferences}
        onReloadPreferences={loadPreferences}
        repos={repo.repoList()}
        selectedRepoId={repo.selectedRepoId()}
        engine={diff.engine()}
        viewMode={diffViewMode()}
        summary={summary()}
        onHeaderMount={(element) => {
          appHeader = element;
        }}
        onRepoListOpen={repo.refreshRepos}
        onRepoChange={selectRepo}
        onEngineChange={diff.loadEngine}
        onViewModeChange={setViewMode}
      />

      <Show when={repo.selectedRepoId() !== null && repo.repoRefsPending()}>
        <p class="status">Loading refs...</p>
      </Show>

      <Show when={repo.reposPending()}>
        <p class="status">Loading marked repos...</p>
      </Show>

      <Show when={preferencesPending()}>
        <p class="status">Loading preferences...</p>
      </Show>

      <Show when={repo.repoRefsError() !== null}>
        <section class="notice error">
          Failed to load refs: {String(repo.repoRefsError())}
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

      <Show when={repo.repoPickerRepos() !== null}>
        <RepoPicker
          repos={repo.repoPickerRepos()!}
          error={repo.repoSelectionError()}
          onSelect={selectRepo}
        />
      </Show>

      <Show when={repo.selectedRepoId() !== null && controls() !== null}>
        <>
          <Controls
            controls={controls()!}
            refChoices={repo.refChoices()}
            presetCatalogs={repo.presetCatalogs()}
            presetCatalogsPending={repo.presetCatalogsPending()}
            presetCatalogsError={repo.presetCatalogsError()}
            onPresetMode={repo.loadPresetCatalogs}
            onAgainstHead={diff.loadAgainstHead}
            onPreset={diff.loadPreset}
            onRefs={diff.loadRefs}
            onBranchReview={diff.loadBranchReview}
            mainBranchSaving={repo.mainBranchSaving()}
            onSaveMainBranch={repo.saveMainBranch}
          />
          <p class={`status ${diff.status()}`}>{diff.statusText()}</p>
          <Show when={preferences() !== null}>
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
                  fileExpansion={ui.fileExpansion}
                  loadingFiles={diff.loadingFiles}
                  fileErrors={diff.fileErrors}
                  linePin={navigation.linePin()}
                  isForcedRichFileId={ui.isForcedRichFileId}
                  aggressiveFolds={preferences()!.aggressive_folds}
                  onFileVirtualizedChange={ui.setFileVirtualized}
                  onHydrateFile={diff.hydrateFile}
                  diffViewMode={diffViewMode()}
                  setFileExpansion={ui.setFileExpansion}
                />
              </GracefulErrorBoundary>
            </div>
            <HunkNav
              debugOpen={navigation.debugMenuOpen()}
              helpOpen={navigation.helpOpen()}
              hunkPosition={navigation.hunkPosition()}
              onHelpOpenChange={navigation.setHelpOpen}
              onNext={navigation.scrollNext}
              onPrev={navigation.scrollPrev}
            />
          </Show>
        </>
      </Show>
    </main>
  );
}
