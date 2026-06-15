import {
  For,
  Show,
  batch,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import { render } from "solid-js/web";
import {
  QueryClient,
  QueryClientProvider,
  createQuery,
} from "@tanstack/solid-query";
import {
  fetchDefaults,
  fetchDiff,
  fetchFileDiff,
  fetchRepos,
  type DiffEngine,
  type DiffRequest,
  type FileEntry,
  type RepoId,
  type RepoMark,
  type Summary,
} from "./api";
import { type DiffViewMode } from "./DiffGrid";
import { Controls } from "./Controls";
import { FileList, FileTreeSidebar } from "./FileViews";
import { HunkNav } from "./Hud";
import {
  createHunkNavigation,
  fileIdForHunkAnchor,
  richPreloadFileIdsForAnchor,
  richPreloadFileIdsForFileId,
  shouldIgnoreGlobalHotkeyEvent,
} from "./hunkNavigation";
import {
  clearLinePinInHash,
  getLinePinFromHash,
  highlightPinnedLine,
  linePinFromElement,
  restorePinnedLine,
  setLinePinInHash,
} from "./linePins";
import {
  type ControlsState,
  type FileGroup,
  type LinePin,
  type LoadState,
  addHydratedNotebookSummary,
  appQuery,
  buildRequest,
  directoryElementId,
  emptySummary,
  engineLabels,
  diffViewLabels,
  entryDirectoryLabel,
  fileBodyAnchorElementId,
  fileDiffQueryKey,
  fileDisplayName,
  fileElementId,
  fileKey,
  fileMatchesLinePin,
  groupFilesByLabel,
  initialControls,
  initialDiffViewMode,
  initialEngine,
  loadedStatusLabel,
  nextFileExpansion,
  sortFilesByOrder,
  statusLabel,
} from "./model";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
    },
  },
});

function stringArraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => value === right[index]);
}

function App() {
  const [engine, setEngine] = createSignal<DiffEngine>("dirdiff");
  const [selectedRepoId, setSelectedRepoId] = createSignal<RepoId | null>(null);
  const [repoSelectionError, setRepoSelectionError] = createSignal("");
  const repos = createQuery(() => ({
    queryKey: ["repos"],
    queryFn: fetchRepos,
    staleTime: 0,
  }));
  const defaults = createQuery(() => ({
    queryKey: ["defaults", selectedRepoId()],
    queryFn: () => {
      const repoId = selectedRepoId();
      if (repoId === null) {
        throw new Error("repo_id is required.");
      }
      return fetchDefaults(repoId);
    },
    enabled: selectedRepoId() !== null,
    staleTime: 0,
  }));
  const [diffViewMode, setDiffViewMode] = createSignal<DiffViewMode>(
    initialDiffViewMode(),
  );
  const [controls, setControls] = createSignal<ControlsState | null>(null);
  const [request, setRequest] = createSignal<DiffRequest | null>(null);
  const [files, setFiles] = createSignal<FileEntry[]>([]);
  const [lazyFiles, setLazyFiles] = createSignal<FileEntry[]>([]);
  const [fileOrder, setFileOrder] = createSignal<Record<string, number>>({});
  const [directoryExpansion, setDirectoryExpansion] = createSignal<
    Record<string, boolean>
  >({});
  const [fileExpansion, setFileExpansion] = createSignal<
    Record<string, boolean>
  >({});
  const [loadingFiles, setLoadingFiles] = createSignal<Record<string, boolean>>(
    {},
  );
  const [fileErrors, setFileErrors] = createSignal<Record<string, string>>({});
  const [summary, setSummary] = createSignal<Summary>(emptySummary);
  const [linePin, setLinePin] = createSignal<LinePin | null>(
    getLinePinFromHash(),
  );
  const [forcedRichFileIds, setForcedRichFileIds] = createSignal<string[]>([]);
  const [activeHunkFileId, setActiveHunkFileId] = createSignal<string | null>(
    null,
  );
  const [virtualizedFileIds, setVirtualizedFileIds] = createSignal<string[]>(
    [],
  );
  const [status, setStatus] = createSignal<LoadState>("idle");
  const [statusText, setStatusText] = createSignal("Preparing diff...");
  const [debugMenuOpen, setDebugMenuOpen] = createSignal(false);
  const [helpOpen, setHelpOpen] = createSignal(false);
  const [fileTreeOpen, setFileTreeOpen] = createSignal(false);
  let appRoot: HTMLElement | undefined;
  let appHeader: HTMLElement | undefined;
  let initialized = false;
  let restoredLinePinKey = "";
  let requestVersion = 0;
  const currentRequestVersion = () => requestVersion;
  const setForcedRichPreloadIds = (nextIds: string[]) => {
    setForcedRichFileIds((currentIds) =>
      stringArraysEqual(currentIds, nextIds) ? currentIds : nextIds,
    );
  };
  const forceRichFileId = (fileId: string) => {
    setForcedRichFileIds((currentIds) =>
      currentIds.includes(fileId) ? currentIds : [...currentIds, fileId],
    );
  };
  const setFileVirtualized = (fileId: string, virtualized: boolean) => {
    setVirtualizedFileIds((currentIds) => {
      if (virtualized) {
        return currentIds.includes(fileId)
          ? currentIds
          : [...currentIds, fileId];
      }
      return currentIds.filter((currentId) => currentId !== fileId);
    });
  };
  const displayFiles = createMemo(() =>
    [...files(), ...lazyFiles()].sort(
      (leftFile, rightFile) =>
        (fileOrder()[fileKey(leftFile)] ?? 0) -
        (fileOrder()[fileKey(rightFile)] ?? 0),
    ),
  );
  const selectedRepo = createMemo(() => {
    const repoId = selectedRepoId();
    const repoList = repos.data;
    if (repoId === null) {
      return null;
    }
    if (!repoList) {
      return null;
    }
    const repo = repoList.find((candidate) => candidate.id === repoId);
    if (!repo) {
      return null;
    }
    return repo;
  });
  const repoPickerRepos = createMemo(() => {
    const repoList = repos.data;
    if (!repoList) {
      return null;
    }
    if (selectedRepoId() !== null) {
      return null;
    }
    return repoList;
  });
  createEffect(() => {
    if (forcedRichFileIds().length > 0) {
      return;
    }
    setForcedRichPreloadIds(richPreloadFileIdsForFileId(null, displayFiles()));
  });
  const hunkNav = createHunkNavigation(() => appRoot, {
    afterReconcile: () => {
      if (!appRoot) {
        return;
      }
      restorePinnedLine(appRoot, restoredLinePinKey, (pinKey) => {
        restoredLinePinKey = pinKey;
      });
    },
    onSelectionChange: ({ selected }) => {
      if (!selected) {
        setActiveHunkFileId(null);
        return;
      }
      setActiveHunkFileId(fileIdForHunkAnchor(selected));
      setForcedRichPreloadIds(
        richPreloadFileIdsForAnchor(selected, displayFiles()),
      );
    },
  });

  hunkNav.reconcileWhen([
    files,
    directoryExpansion,
    fileExpansion,
    loadingFiles,
    forcedRichFileIds,
    diffViewMode,
  ]);
  hunkNav.followScroll();

  onMount(() => {
    if (!appRoot || !appHeader) {
      return;
    }

    const updateStickyHeaderOffset = () => {
      appRoot.style.setProperty(
        "--app-header-sticky-offset",
        `${appHeader?.offsetHeight ?? 0}px`,
      );
    };

    updateStickyHeaderOffset();
    const observer = new ResizeObserver(updateStickyHeaderOffset);
    observer.observe(appHeader);
    window.addEventListener("resize", updateStickyHeaderOffset);
    onCleanup(() => {
      observer.disconnect();
      window.removeEventListener("resize", updateStickyHeaderOffset);
    });
  });

  const refChoices = () => {
    const value = defaults.data;
    if (!value) {
      throw new Error("Ref choices require loaded defaults.");
    }
    return value.ref_choices;
  };
  const resetDiffState = (nextStatus: LoadState, nextStatusText: string) => {
    batch(() => {
      setFiles([]);
      setLazyFiles([]);
      setFileOrder({});
      setDirectoryExpansion({});
      setFileExpansion({});
      setLoadingFiles({});
      setFileErrors({});
      setForcedRichFileIds([]);
      setActiveHunkFileId(null);
      setVirtualizedFileIds([]);
      setSummary(emptySummary);
      setStatus(nextStatus);
      setStatusText(nextStatusText);
    });
  };

  createEffect(() => {
    const repoList = repos.data;
    if (!repoList) {
      return;
    }
    const rawRepoId = new URLSearchParams(window.location.search).get(
      "repo_id",
    );
    if (rawRepoId === null) {
      setSelectedRepoId(null);
      setRepoSelectionError("");
      resetDiffState("idle", "Choose a repo to load a diff.");
      return;
    }
    const parsedRepoId = Number(rawRepoId);
    if (!Number.isInteger(parsedRepoId)) {
      setSelectedRepoId(null);
      setRepoSelectionError(`Invalid repo_id: ${rawRepoId}`);
      resetDiffState("idle", "Choose a repo to load a diff.");
      return;
    }
    if (parsedRepoId <= 0) {
      setSelectedRepoId(null);
      setRepoSelectionError(`Invalid repo_id: ${rawRepoId}`);
      resetDiffState("idle", "Choose a repo to load a diff.");
      return;
    }
    const repo = repoList.find((candidate) => candidate.id === parsedRepoId);
    if (!repo) {
      setSelectedRepoId(null);
      setRepoSelectionError(`Invalid repo_id: ${rawRepoId}`);
      resetDiffState("idle", "Choose a repo to load a diff.");
      return;
    }
    setSelectedRepoId(parsedRepoId);
    setRepoSelectionError("");
  });

  async function loadDiff(
    activeRequest: DiffRequest,
    signal: AbortSignal,
    version: number,
  ) {
    try {
      const payload = await fetchDiff(activeRequest, signal);
      if (version !== requestVersion) {
        return;
      }
      const order = Object.fromEntries(
        payload.files.map((entry, index) => [fileKey(entry), index]),
      );
      const lazyManifestFiles = payload.files.filter((entry) => entry.lazy);
      const baseStatus = statusLabel(
        activeRequest,
        payload.left_label,
        payload.right_label,
      );
      batch(() => {
        setLazyFiles(lazyManifestFiles);
        setFileOrder(order);
        setSummary(payload.summary);
        setStatusText(
          loadedStatusLabel(baseStatus, lazyManifestFiles.length, 0),
        );
      });
      void hydrateManifestFiles(
        activeRequest,
        payload.files,
        version,
        baseStatus,
        lazyManifestFiles.length,
      );
    } catch (error) {
      if (signal.aborted || version !== requestVersion) {
        return;
      }
      batch(() => {
        setStatus("error");
        setStatusText(
          error instanceof Error ? error.message : "Failed to load diff.",
        );
      });
    }
  }

  async function hydrateManifestFiles(
    activeRequest: DiffRequest,
    manifestFiles: FileEntry[],
    version: number,
    baseStatus: string,
    initialLoadedFiles: number,
  ) {
    const pendingFiles = manifestFiles.filter((entry) => !entry.lazy);
    if (pendingFiles.length === 0) {
      if (version === requestVersion) {
        setStatus("done");
        setStatusText(baseStatus);
      }
      return;
    }
    const pin = getLinePinFromHash();
    let hasFailure = false;
    let loadedFiles = initialLoadedFiles;
    let failedDetailFiles = 0;

    const workers = Array.from(
      { length: Math.min(4, pendingFiles.length) },
      async (_, workerIndex) => {
        for (let index = workerIndex; index < pendingFiles.length; index += 4) {
          if (version !== requestVersion) {
            return;
          }
          const entry = pendingFiles[index];
          const key = fileKey(entry);
          try {
            const hydrated = await queryClient.fetchQuery({
              queryKey: fileDiffQueryKey(activeRequest, entry),
              queryFn: () => fetchFileDiff(activeRequest, entry),
              staleTime: 0,
            });
            if (version !== requestVersion) {
              return;
            }
            const nextEntry = {
              ...entry,
              ...hydrated,
              lazy: null,
              default_expanded: hydrated.default_expanded,
            };
            const nextKey = fileKey(nextEntry);
            const shouldOpenPinnedFile =
              pin !== null && fileMatchesLinePin(nextEntry, pin);
            loadedFiles += 1;
            batch(() => {
              setFiles((current) => {
                const withoutCurrent = current.filter(
                  (file) => fileKey(file) !== nextKey,
                );
                return sortFilesByOrder(
                  [...withoutCurrent, nextEntry],
                  fileOrder(),
                );
              });
              setDirectoryExpansion((current) => {
                const directory = entryDirectoryLabel(nextEntry);
                if (shouldOpenPinnedFile) {
                  return { ...current, [directory]: true };
                }
                if (Object.hasOwn(current, directory)) {
                  return current;
                }
                return { ...current, [directory]: true };
              });
              setFileExpansion((current) =>
                nextFileExpansion(
                  shouldOpenPinnedFile
                    ? { ...current, [nextKey]: true }
                    : current,
                  nextEntry,
                  nextKey,
                ),
              );
              setSummary((current) =>
                addHydratedNotebookSummary(current, nextEntry),
              );
              setStatusText(
                loadedStatusLabel(baseStatus, loadedFiles, failedDetailFiles),
              );
            });
          } catch (error) {
            if (version !== requestVersion) {
              return;
            }
            hasFailure = true;
            loadedFiles += 1;
            failedDetailFiles += 1;
            batch(() => {
              setFiles((current) => {
                const withoutCurrent = current.filter(
                  (file) => fileKey(file) !== key,
                );
                return sortFilesByOrder(
                  [...withoutCurrent, entry],
                  fileOrder(),
                );
              });
              setDirectoryExpansion((current) => {
                const directory = entryDirectoryLabel(entry);
                if (Object.hasOwn(current, directory)) {
                  return current;
                }
                return { ...current, [directory]: true };
              });
              setFileExpansion((current) => ({ ...current, [key]: true }));
              setFileErrors((current) => ({
                ...current,
                [key]:
                  error instanceof Error
                    ? error.message
                    : "Failed to load file diff.",
              }));
              setStatus("error");
              setStatusText(
                loadedStatusLabel(baseStatus, loadedFiles, failedDetailFiles),
              );
            });
          }
        }
      },
    );
    await Promise.all(workers);
    if (version === requestVersion) {
      setStatus(hasFailure ? "error" : "done");
      if (!hasFailure) {
        setStatusText(baseStatus);
      }
    }
  }

  createEffect(() => {
    const value = defaults.data;
    const repoId = selectedRepoId();
    if (!value) {
      return;
    }
    if (repoId === null) {
      return;
    }
    if (initialized) {
      return;
    }
    initialized = true;
    const nextEngine = initialEngine(value);
    const nextControls = initialControls(value);
    setEngine(nextEngine);
    setControls(nextControls);
    const nextRequest = buildRequest(
      nextControls,
      refChoices(),
      nextEngine,
      repoId,
    );
    if (typeof nextRequest === "string") {
      setStatus("error");
      setStatusText(nextRequest);
      return;
    }
    setRequest(nextRequest);
  });

  createEffect(() => {
    const activeRequest = request();
    if (!activeRequest) {
      return;
    }

    const controller = new AbortController();
    const version = ++requestVersion;
    resetDiffState("loading", "Loading diff...");
    void loadDiff(activeRequest, controller.signal, version);
    onCleanup(() => controller.abort());
  });

  createEffect(() => {
    const activeRequest = request();
    if (!activeRequest) {
      return;
    }
    history.replaceState(
      {},
      "",
      `/?${appQuery(activeRequest, diffViewMode()).toString()}${window.location.hash}`,
    );
  });

  const toggleDiffViewMode = () => {
    setDiffViewMode((mode) => (mode === "split" ? "inline" : "split"));
  };

  const setAllFilesExpanded = (expanded: boolean) => {
    const currentFiles = displayFiles();
    const groups = [...groupFilesByLabel(currentFiles).keys()];
    batch(() => {
      setDirectoryExpansion(() =>
        Object.fromEntries(groups.map((label) => [label, expanded])),
      );
      setFileExpansion(() =>
        Object.fromEntries(
          currentFiles.map((file) => [fileKey(file), expanded]),
        ),
      );
    });
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (shouldIgnoreGlobalHotkeyEvent(event)) {
      return;
    }
    if (event.code === "KeyN" && !event.shiftKey) {
      event.preventDefault();
      hunkNav.scrollNext();
      return;
    }
    if (event.code === "KeyN" && event.shiftKey) {
      event.preventDefault();
      hunkNav.scrollPrev();
      return;
    }
    if (event.code === "KeyP") {
      event.preventDefault();
      scrollTop();
      return;
    }
    if (event.code === "KeyT") {
      event.preventDefault();
      setFileTreeOpen((open) => !open);
      return;
    }
    if (event.code === "KeyI") {
      event.preventDefault();
      toggleDiffViewMode();
      return;
    }
    if (event.code === "KeyS") {
      event.preventDefault();
      setAllFilesExpanded(true);
      return;
    }
    if (event.code === "KeyF") {
      event.preventDefault();
      setAllFilesExpanded(false);
      return;
    }
    if (event.code === "KeyR") {
      event.preventDefault();
      reloadControls();
      return;
    }
    if (event.code === "KeyD") {
      event.preventDefault();
      setDebugMenuOpen((open) => !open);
      return;
    }
    if (event.code === "KeyH") {
      event.preventDefault();
      setHelpOpen((open) => !open);
      return;
    }
  };

  window.addEventListener("keydown", onKeyDown);
  onCleanup(() => window.removeEventListener("keydown", onKeyDown));

  const setDiffSelectionSide = (
    grid: HTMLElement | null,
    side: "left" | "right" | null,
  ) => {
    appRoot
      ?.querySelector<HTMLElement>(".diff-grid[data-diff-selection-side]")
      ?.removeAttribute("data-diff-selection-side");
    if (!grid || !side) {
      return;
    }
    grid.dataset.diffSelectionSide = side;
  };

  const onPointerDown = (event: PointerEvent) => {
    const target = event.target;
    if (!(target instanceof Element) || !appRoot?.contains(target)) {
      setDiffSelectionSide(null, null);
      return;
    }
    const side = target.closest(".diff-side.side-left, .diff-side.side-right");
    if (!side || !appRoot.contains(side)) {
      setDiffSelectionSide(null, null);
      return;
    }
    const grid = side.closest<HTMLElement>(".diff-grid");
    if (!grid || !appRoot.contains(grid)) {
      setDiffSelectionSide(null, null);
      return;
    }
    setDiffSelectionSide(
      grid,
      side.classList.contains("side-left") ? "left" : "right",
    );
  };

  const onLinePinClick = (event: MouseEvent) => {
    const target = event.target;
    if (!(target instanceof Element) || target.closest("button")) {
      return;
    }
    const lineNo = target.closest<HTMLElement>(".line-no[data-line-pin-line]");
    if (!lineNo || !appRoot?.contains(lineNo)) {
      return;
    }
    const pin = linePinFromElement(lineNo);
    if (!pin) {
      return;
    }
    const pinKey = JSON.stringify(pin);
    const row = lineNo.closest<HTMLElement>(".diff-row");
    if (
      restoredLinePinKey === pinKey &&
      row?.classList.contains("pinned-line")
    ) {
      restoredLinePinKey = "";
      clearLinePinInHash();
      setLinePin(null);
      highlightPinnedLine(appRoot, null);
      return;
    }
    restoredLinePinKey = pinKey;
    setLinePinInHash(pin);
    setLinePin(pin);
    highlightPinnedLine(appRoot, row);
  };

  const openPinnedFile = (pin: LinePin) => {
    const file = files().find((entry) => fileMatchesLinePin(entry, pin));
    if (!file) {
      return;
    }
    const directory = entryDirectoryLabel(file);
    const key = fileKey(file);
    batch(() => {
      setDirectoryExpansion((current) => ({
        ...current,
        [directory]: true,
      }));
      setFileExpansion((current) => ({
        ...current,
        [key]: true,
      }));
    });
  };

  const onHashChange = () => {
    if (!appRoot) {
      return;
    }
    const pin = getLinePinFromHash();
    setLinePin(pin);
    if (pin) {
      openPinnedFile(pin);
    }
    restorePinnedLine(appRoot, restoredLinePinKey, (pinKey) => {
      restoredLinePinKey = pinKey;
    });
  };

  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("click", onLinePinClick);
  window.addEventListener("hashchange", onHashChange);
  onCleanup(() => {
    document.removeEventListener("pointerdown", onPointerDown);
    document.removeEventListener("click", onLinePinClick);
    window.removeEventListener("hashchange", onHashChange);
    setDiffSelectionSide(null, null);
  });

  const loadControls = (
    nextControls: ControlsState,
    selectedEngine: DiffEngine = engine(),
  ) => {
    const repoId = selectedRepoId();
    if (repoId === null) {
      setRequest(null);
      resetDiffState("idle", "Choose a repo to load a diff.");
      return;
    }
    setControls(nextControls);
    const nextRequest = buildRequest(
      nextControls,
      refChoices(),
      selectedEngine,
      repoId,
    );
    if (typeof nextRequest === "string") {
      setRequest(null);
      resetDiffState("error", nextRequest);
      return;
    }
    setRequest(nextRequest);
  };

  const reloadControls = () => {
    const currentControls = controls();
    if (!currentControls) {
      return;
    }
    loadControls(currentControls);
  };

  const loadEngine = (nextEngine: DiffEngine) => {
    setEngine(nextEngine);
    const currentControls = controls();
    if (currentControls) {
      loadControls(currentControls, nextEngine);
    }
  };

  const selectRepo = (repo: RepoMark) => {
    const params = new URLSearchParams(window.location.search);
    params.set("repo_id", String(repo.id));
    history.replaceState(
      {},
      "",
      `/?${params.toString()}${window.location.hash}`,
    );
    initialized = false;
    batch(() => {
      setSelectedRepoId(repo.id);
      setRepoSelectionError("");
      setControls(null);
      setRequest(null);
      resetDiffState("idle", "Preparing diff...");
    });
  };

  const scrollTop = () => {
    window.scrollTo({ top: 0, behavior: "instant" });
  };

  const scrollToFile = (file: FileEntry) => {
    const directory = entryDirectoryLabel(file);
    const key = fileKey(file);
    const fileId = fileElementId(key);
    batch(() => {
      setDirectoryExpansion((current) => ({
        ...current,
        [directory]: true,
      }));
      setFileExpansion((current) => ({
        ...current,
        [key]: true,
      }));
      forceRichFileId(fileId);
    });
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const card = document.getElementById(fileId);
        if (!card) {
          throw new Error(
            `Could not find file card for ${fileDisplayName(file)}.`,
          );
        }
        const target =
          card.querySelector<HTMLElement>(
            ".diff-row.hunk-anchor:not(.virtual-hunk-anchor)",
          ) ?? document.getElementById(fileBodyAnchorElementId(key));
        if (!target) {
          throw new Error(
            `Could not find file scroll target for ${fileDisplayName(file)}.`,
          );
        }
        target.scrollIntoView({ block: "center", behavior: "instant" });
        card.classList.remove("file-card-flash");
        void card.offsetWidth;
        card.classList.add("file-card-flash");
      });
    });
  };

  const scrollToDirectory = (group: FileGroup) => {
    batch(() => {
      setDirectoryExpansion((current) => ({
        ...current,
        [group.label]: true,
      }));
      setFileExpansion((current) => ({
        ...current,
        ...Object.fromEntries(group.files.map((file) => [fileKey(file), true])),
      }));
    });
    requestAnimationFrame(() => {
      const target = document.getElementById(directoryElementId(group.label));
      if (!target) {
        throw new Error(`Could not find directory group for ${group.label}.`);
      }
      const header = target.querySelector<HTMLElement>(
        ".directory-group-header",
      );
      if (!header) {
        throw new Error(`Could not find directory header for ${group.label}.`);
      }
      header.scrollIntoView({ block: "start", behavior: "instant" });
    });
  };

  return (
    <main ref={appRoot} class="app-shell">
      <header ref={appHeader} class="app-header">
        <div class="app-title-block">
          <div class="app-title-row">
            <h1>dirdiff</h1>
            <Show when={selectedRepo()}>
              {(repo) => <span class="repo-context">{repo().name}</span>}
            </Show>
            <div class="header-actions">
              <EngineSelect engine={engine()} onEngineChange={loadEngine} />
              <DiffViewSelect
                viewMode={diffViewMode()}
                onViewModeChange={setDiffViewMode}
              />
            </div>
          </div>
        </div>
        <SummaryView summary={summary()} />
      </header>

      <Show when={selectedRepoId() !== null && defaults.isPending}>
        <p class="status">Loading defaults...</p>
      </Show>

      <Show when={repos.isPending}>
        <p class="status">Loading marked repos...</p>
      </Show>

      <Show when={defaults.error}>
        <section class="notice error">
          Failed to load defaults: {String(defaults.error)}
        </section>
      </Show>

      <Show when={repos.error}>
        <section class="notice error">
          Failed to load marked repos: {String(repos.error)}
        </section>
      </Show>

      <Show when={repoPickerRepos()}>
        {(repoList) => (
          <RepoPicker
            repos={repoList()}
            error={repoSelectionError()}
            onSelect={selectRepo}
          />
        )}
      </Show>

      <Show when={selectedRepoId() !== null && controls()}>
        {(value) => (
          <>
            <Controls
              controls={value()}
              refChoices={refChoices()}
              onLoad={loadControls}
            />
            <p class={`status ${status()}`}>{statusText()}</p>
            <FileList
              files={displayFiles()}
              request={request()}
              requestVersion={currentRequestVersion}
              fileOrder={fileOrder()}
              directoryExpansion={directoryExpansion()}
              fileExpansion={fileExpansion()}
              loadingFiles={loadingFiles()}
              fileErrors={fileErrors()}
              linePin={linePin()}
              forcedRichFileIds={forcedRichFileIds()}
              onFileVirtualizedChange={setFileVirtualized}
              diffViewMode={diffViewMode()}
              setDirectoryExpansion={setDirectoryExpansion}
              setFileExpansion={setFileExpansion}
              setLoadingFiles={setLoadingFiles}
              setFileErrors={setFileErrors}
              setFiles={setFiles}
              setLazyFiles={setLazyFiles}
              setSummary={setSummary}
              onSetAllExpanded={setAllFilesExpanded}
            />
            <FileTreeSidebar
              files={displayFiles()}
              directoryExpansion={directoryExpansion()}
              fileExpansion={fileExpansion()}
              activeHunkFileId={activeHunkFileId()}
              virtualizedFileIds={virtualizedFileIds()}
              open={fileTreeOpen()}
              onOpenChange={setFileTreeOpen}
              setDirectoryExpansion={setDirectoryExpansion}
              setFileExpansion={setFileExpansion}
              onScrollToDirectory={scrollToDirectory}
              onScrollToFile={scrollToFile}
            />
            <HunkNav
              debugOpen={debugMenuOpen()}
              helpOpen={helpOpen()}
              onHelpOpenChange={setHelpOpen}
              onNext={hunkNav.scrollNext}
              onPrev={hunkNav.scrollPrev}
            />
          </>
        )}
      </Show>
    </main>
  );
}

function EngineSelect(props: {
  engine: DiffEngine;
  onEngineChange: (engine: DiffEngine) => void;
}) {
  return (
    <label class="engine-select">
      <span>Engine</span>
      <select
        value={props.engine}
        onChange={(event) => {
          const nextEngine = event.currentTarget.value as DiffEngine;
          if (
            nextEngine === "dirdiff" ||
            nextEngine === "git" ||
            nextEngine === "difftastic"
          ) {
            props.onEngineChange(nextEngine);
            event.currentTarget.blur();
          }
        }}
      >
        <option value="dirdiff">{engineLabels.dirdiff}</option>
        <option value="git">{engineLabels.git}</option>
        <option value="difftastic">{engineLabels.difftastic}</option>
      </select>
    </label>
  );
}

function DiffViewSelect(props: {
  viewMode: DiffViewMode;
  onViewModeChange: (viewMode: DiffViewMode) => void;
}) {
  return (
    <label class="engine-select">
      <span>View</span>
      <select
        value={props.viewMode}
        onChange={(event) => {
          props.onViewModeChange(event.currentTarget.value as DiffViewMode);
          event.currentTarget.blur();
        }}
      >
        <option value="split">{diffViewLabels.split}</option>
        <option value="inline">{diffViewLabels.inline}</option>
      </select>
    </label>
  );
}

function RepoPicker(props: {
  repos: RepoMark[];
  error: string;
  onSelect: (repo: RepoMark) => void;
}) {
  return (
    <section class="repo-picker" aria-label="Marked repositories">
      <div class="repo-picker-heading">
        <h2>Choose a repo</h2>
        <p>Select a marked repository before loading repo-backed diffs.</p>
      </div>
      <Show when={props.error}>
        <p class="repo-picker-error">{props.error}</p>
      </Show>
      <div class="repo-list">
        <For each={props.repos}>
          {(repo) => (
            <button
              type="button"
              class="repo-option"
              onClick={() => props.onSelect(repo)}
            >
              <span class="repo-option-name">{repo.name}</span>
              <span class="repo-option-path">{repo.path}</span>
            </button>
          )}
        </For>
      </div>
    </section>
  );
}

function SummaryView(props: { summary: Summary }) {
  const hasNotebookCells = () =>
    typeof props.summary.changed_cells === "number";

  return (
    <section class="summary" aria-label="Diff summary">
      <SummaryMetric
        label="Files"
        added={props.summary.added_files}
        changed={props.summary.updated_files}
        removed={props.summary.removed_files}
      />
      <SummaryMetric
        label="Lines"
        added={props.summary.added_lines}
        changed={props.summary.modified_lines}
        removed={props.summary.removed_lines}
      />
      <Show when={hasNotebookCells()}>
        <SummaryMetric
          label="Cells"
          added={props.summary.added_cells ?? 0}
          changed={props.summary.modified_cells ?? 0}
          removed={props.summary.removed_cells ?? 0}
        />
      </Show>
    </section>
  );
}

function SummaryMetric(props: {
  label: string;
  added: number;
  changed: number;
  removed: number;
}) {
  const metricClass = () => props.label.toLowerCase();

  return (
    <div class={`summary-group summary-group-${metricClass()}`}>
      <strong>{props.label}</strong>
      <span class="delta added">+ {props.added}</span>
      <span class="delta changed">~ {props.changed}</span>
      <span class="delta removed">- {props.removed}</span>
    </div>
  );
}

render(
  () => (
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  ),
  document.getElementById("root")!,
);
