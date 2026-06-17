import { Show, batch, createSignal, onMount } from "solid-js";
import type { RepoId, RepoMark } from "./api";
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
import { type ControlsState, emptySummary } from "./fileUtils";
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
    setLoadedDiff: ui.setLoadedDiff,
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

  const summary = () => {
    const loadedDiff = ui.loadedDiff();
    if (loadedDiff === null) {
      return emptySummary;
    }
    return loadedDiff.summary;
  };

  const navigation = createDiffNavigation({
    appRoot: () => appRoot,
    appHeader: () => appHeader,
    displayFiles: ui.displayFiles,
    directoryExpansion: ui.directoryExpansion,
    fileExpansion: ui.fileExpansion,
    loadingFiles: diff.loadingFiles,
    forcedRichFileIds: ui.forcedRichFileIds,
    diffViewMode,
    setDirectoryExpansion: ui.setDirectoryExpansion,
    setFileExpansion: ui.setFileExpansion,
    setForcedRichPreloadIds: ui.setForcedRichPreloadIds,
    forceRichFileId: ui.forceRichFileId,
    setActiveHunkFileId: ui.setActiveHunkFileId,
    reloadDiff: diff.reloadDiff,
    toggleDiffViewMode,
    setAllFilesExpanded: ui.setAllFilesExpanded,
    openFileExpansion: ui.openFileExpansion,
    openDirectoryExpansion: ui.openDirectoryExpansion,
  });

  const loadInitialDiff = (initial: InitialRepoDiff) => {
    batch(() => {
      setControls(initial.controls);
    });
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
        repos={repo.repoList()}
        selectedRepoId={repo.selectedRepoId()}
        engine={diff.engine()}
        viewMode={diffViewMode()}
        summary={summary()}
        onHeaderMount={(element) => {
          appHeader = element;
        }}
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

      <Show when={repo.repoPickerRepos() !== null}>
        <RepoPicker
          repos={repo.repoPickerRepos()!}
          error={repo.repoSelectionError()}
          onSelect={selectRepo}
        />
      </Show>

      <Show
        when={
          repo.selectedRepoId() !== null &&
          controls() !== null &&
          repo.presetCatalog() !== null
        }
      >
        <>
          <Controls
            controls={controls()!}
            refChoices={repo.refChoices()}
            presetCatalog={repo.presetCatalog()!}
            onAgainstHead={diff.loadAgainstHead}
            onPreset={diff.loadPreset}
            onRefs={diff.loadRefs}
            onBranchReview={diff.loadBranchReview}
          />
          <p class={`status ${diff.status()}`}>{diff.statusText()}</p>
          <GracefulErrorBoundary title="Could not render diff">
            <FileList
              files={ui.displayFiles()}
              loadedDiff={ui.loadedDiff()}
              currentParamsIdentity={diff.currentParamsIdentity}
              directoryExpansion={ui.directoryExpansion()}
              fileExpansion={ui.fileExpansion()}
              loadingFiles={diff.loadingFiles()}
              fileErrors={diff.fileErrors()}
              linePin={navigation.linePin()}
              forcedRichFileIds={ui.forcedRichFileIds()}
              onFileVirtualizedChange={ui.setFileVirtualized}
              diffViewMode={diffViewMode()}
              setDirectoryExpansion={ui.setDirectoryExpansion}
              setFileExpansion={ui.setFileExpansion}
              setLoadingFiles={diff.setLoadingFiles}
              setFileErrors={diff.setFileErrors}
              updateLoadedDiff={ui.updateLoadedDiff}
              onSetAllExpanded={ui.setAllFilesExpanded}
            />
          </GracefulErrorBoundary>
          <GracefulErrorBoundary title="Could not render file tree">
            <FileTreeSidebar
              files={ui.displayFiles()}
              directoryExpansion={ui.directoryExpansion()}
              fileExpansion={ui.fileExpansion()}
              activeHunkFileId={ui.activeHunkFileId()}
              virtualizedFileIds={ui.virtualizedFileIds()}
              open={navigation.fileTreeOpen()}
              onOpenChange={navigation.setFileTreeOpen}
              setDirectoryExpansion={ui.setDirectoryExpansion}
              setFileExpansion={ui.setFileExpansion}
              onScrollToDirectory={navigation.scrollToDirectory}
              onScrollToFile={navigation.scrollToFile}
            />
          </GracefulErrorBoundary>
          <HunkNav
            debugOpen={navigation.debugMenuOpen()}
            helpOpen={navigation.helpOpen()}
            hunkPosition={navigation.hunkPosition()}
            onHelpOpenChange={navigation.setHelpOpen}
            onNext={navigation.scrollNext}
            onPrev={navigation.scrollPrev}
          />
        </>
      </Show>
    </main>
  );
}
