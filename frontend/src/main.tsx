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
  fetchDiff,
  fetchFileDiff,
  fetchRepoRefs,
  fetchRepos,
  type DiffEngine,
  type DiffRequest,
  type FileEntry,
  type RepoDiffPayload,
  type RepoId,
  type RepoMark,
  type RepoRefs,
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
  type BranchSource,
  type ControlsState,
  type FileGroup,
  type LinePin,
  type LoadedDiff,
  type LoadState,
  addHydratedNotebookSummary,
  appQuery,
  branchReviewRef,
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

function diffRequestParts(request: DiffRequest) {
  return [
    request.repo_id,
    request.engine,
    request.mode,
    request.left,
    request.right,
    request.base_branch,
    request.review_branch,
    request.show_untracked,
  ] as const;
}

function diffRequestIdentity(request: DiffRequest): string {
  return JSON.stringify(diffRequestParts(request));
}

function diffRequestQueryKey(request: DiffRequest) {
  return ["diff", diffRequestIdentity(request)] as const;
}

type DiffQueryPayload = {
  request: DiffRequest;
  requestIdentity: string;
  payload: RepoDiffPayload;
};

function App() {
  const [engine, setEngine] = createSignal<DiffEngine>("dirdiff");
  const [selectedRepoId, setSelectedRepoId] = createSignal<RepoId | null>(null);
  const [repoSelectionError, setRepoSelectionError] = createSignal("");
  const [repoList, setRepoList] = createSignal<RepoMark[] | null>(null);
  const [reposPending, setReposPending] = createSignal(true);
  const [reposError, setReposError] = createSignal<unknown>(null);
  const [repoRefs, setRepoRefs] = createSignal<RepoRefs | null>(null);
  const [repoRefsPending, setRepoRefsPending] = createSignal(false);
  const [repoRefsError, setRepoRefsError] = createSignal<unknown>(null);
  const [diffViewMode, setDiffViewMode] = createSignal<DiffViewMode>(
    initialDiffViewMode(),
  );
  const [controls, setControls] = createSignal<ControlsState | null>(null);
  const [activeRequest, setActiveRequest] = createSignal<DiffRequest | null>(
    null,
  );
  const [loadedDiff, setLoadedDiff] = createSignal<LoadedDiff | null>(null);
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
  let restoredLinePinKey = "";
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
  const displayFiles = createMemo(() => {
    const diff = loadedDiff();
    if (diff === null) {
      return [];
    }
    return [...diff.files, ...diff.lazyFiles].sort(
      (leftFile, rightFile) =>
        (diff.fileOrder[fileKey(leftFile)] ?? 0) -
        (diff.fileOrder[fileKey(rightFile)] ?? 0),
    );
  });
  const repoPickerRepos = createMemo(() => {
    const repos = repoList();
    if (repos === null) {
      return null;
    }
    if (selectedRepoId() !== null) {
      return null;
    }
    return repos;
  });
  createEffect(() => {
    if (forcedRichFileIds().length > 0) {
      return;
    }
    setForcedRichPreloadIds(richPreloadFileIdsForFileId(null, displayFiles()));
  });
  const hunkNav = createHunkNavigation(() => appRoot, {
    afterReconcile: () => {
      if (appRoot === undefined) {
        return;
      }
      restorePinnedLine(appRoot, restoredLinePinKey, (pinKey) => {
        restoredLinePinKey = pinKey;
      });
    },
    onSelectionChange: ({ selected }) => {
      if (selected === null) {
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
    displayFiles,
    directoryExpansion,
    fileExpansion,
    loadingFiles,
    forcedRichFileIds,
    diffViewMode,
  ]);
  hunkNav.followScroll();

  onMount(() => {
    if (appRoot === undefined || appHeader === undefined) {
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
    const value = repoRefs();
    if (value === null) {
      throw new Error("Ref choices require loaded repo refs.");
    }
    return value.ref_choices;
  };
  const resetDiffState = (nextStatus: LoadState, nextStatusText: string) => {
    batch(() => {
      setLoadedDiff(null);
      setDirectoryExpansion({});
      setFileExpansion({});
      setLoadingFiles({});
      setFileErrors({});
      setForcedRichFileIds([]);
      setActiveHunkFileId(null);
      setVirtualizedFileIds([]);
      setStatus(nextStatus);
      setStatusText(nextStatusText);
    });
  };
  const updateLoadedDiff = (updater: (current: LoadedDiff) => LoadedDiff) => {
    setLoadedDiff((current) => (current === null ? current : updater(current)));
  };
  const activeRequestIdentity = (): string | null => {
    const request = activeRequest();
    if (request === null) {
      return null;
    }
    return diffRequestIdentity(request);
  };
  const requestIsActive = (requestIdentity: string): boolean =>
    activeRequestIdentity() === requestIdentity;

  const diffQuery = createQuery(() => {
    const request = activeRequest();
    if (request === null) {
      return {
        queryKey: ["diff", "idle"] as const,
        queryFn: async (): Promise<DiffQueryPayload> => {
          throw new Error("Diff request is not active.");
        },
        enabled: false,
      };
    }
    const requestIdentity = diffRequestIdentity(request);
    return {
      queryKey: diffRequestQueryKey(request),
      queryFn: async ({ signal }): Promise<DiffQueryPayload> => ({
        request,
        requestIdentity,
        payload: await fetchDiff(request, signal),
      }),
      staleTime: 0,
    };
  });

  let appliedDiffIdentity = "";
  createEffect(() => {
    const result = diffQuery.data;
    if (result === undefined) {
      return;
    }
    if (!requestIsActive(result.requestIdentity)) {
      return;
    }
    if (appliedDiffIdentity === result.requestIdentity) {
      return;
    }
    appliedDiffIdentity = result.requestIdentity;
    applyDiffPayload(result);
  });

  createEffect(() => {
    const error = diffQuery.error;
    if (!diffQuery.isError || error === null) {
      return;
    }
    const requestIdentity = activeRequestIdentity();
    if (requestIdentity === null) {
      return;
    }
    batch(() => {
      setStatus("error");
      setStatusText(
        error instanceof Error ? error.message : "Failed to load diff.",
      );
    });
  });

  function applyDiffPayload(result: DiffQueryPayload) {
    const { request, requestIdentity, payload } = result;
    const order = Object.fromEntries(
      payload.files.map((entry, index) => [fileKey(entry), index]),
    );
    const lazyManifestFiles = payload.files.filter((entry) => entry.lazy);
    const baseStatus = statusLabel(
      request,
      payload.left_label,
      payload.right_label,
    );
    batch(() => {
      setLoadedDiff({
        request,
        files: [],
        lazyFiles: lazyManifestFiles,
        fileOrder: order,
        summary: payload.summary,
      });
      setStatusText(loadedStatusLabel(baseStatus, lazyManifestFiles.length, 0));
    });
    void hydrateManifestFiles(
      request,
      requestIdentity,
      payload.files,
      baseStatus,
      lazyManifestFiles.length,
    );
  }

  async function hydrateManifestFiles(
    request: DiffRequest,
    requestIdentity: string,
    manifestFiles: FileEntry[],
    baseStatus: string,
    initialLoadedFiles: number,
  ) {
    const pendingFiles = manifestFiles.filter((entry) => !entry.lazy);
    if (pendingFiles.length === 0) {
      if (requestIsActive(requestIdentity)) {
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
          if (!requestIsActive(requestIdentity)) {
            return;
          }
          const entry = pendingFiles[index];
          const key = fileKey(entry);
          try {
            const hydrated = await queryClient.fetchQuery({
              queryKey: fileDiffQueryKey(request, entry),
              queryFn: ({ signal }) => fetchFileDiff(request, entry, signal),
              staleTime: 0,
            });
            if (!requestIsActive(requestIdentity)) {
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
              setLoadedDiff((current) => {
                if (current === null) {
                  return current;
                }
                const withoutCurrent = current.files.filter(
                  (file) => fileKey(file) !== nextKey,
                );
                return {
                  ...current,
                  files: sortFilesByOrder(
                    [...withoutCurrent, nextEntry],
                    current.fileOrder,
                  ),
                  summary: addHydratedNotebookSummary(
                    current.summary,
                    nextEntry,
                  ),
                };
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
              setStatusText(
                loadedStatusLabel(baseStatus, loadedFiles, failedDetailFiles),
              );
            });
          } catch (error) {
            if (!requestIsActive(requestIdentity)) {
              return;
            }
            hasFailure = true;
            loadedFiles += 1;
            failedDetailFiles += 1;
            batch(() => {
              setLoadedDiff((current) => {
                if (current === null) {
                  return current;
                }
                const withoutCurrent = current.files.filter(
                  (file) => fileKey(file) !== key,
                );
                return {
                  ...current,
                  files: sortFilesByOrder(
                    [...withoutCurrent, entry],
                    current.fileOrder,
                  ),
                };
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
    if (requestIsActive(requestIdentity)) {
      setStatus(hasFailure ? "error" : "done");
      if (!hasFailure) {
        setStatusText(baseStatus);
      }
    }
  }

  const replaceUrlForRequest = (
    request: DiffRequest,
    viewMode = diffViewMode(),
  ) => {
    history.replaceState(
      {},
      "",
      `/?${appQuery(request, viewMode).toString()}${window.location.hash}`,
    );
  };

  const clearActiveRequest = () => {
    void queryClient.cancelQueries({ queryKey: ["diff"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
    setActiveRequest(null);
  };

  const startDiff = (request: DiffRequest) => {
    setActiveRequest(null);
    void queryClient.cancelQueries({ queryKey: ["diff"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
    queryClient.removeQueries({ queryKey: diffRequestQueryKey(request) });
    replaceUrlForRequest(request);
    appliedDiffIdentity = "";
    resetDiffState("loading", "Loading diff...");
    setActiveRequest(request);
  };

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
      reloadDiff();
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
    if (grid === null || side === null) {
      return;
    }
    grid.dataset.diffSelectionSide = side;
  };

  const onPointerDown = (event: PointerEvent) => {
    const target = event.target;
    if (
      !(target instanceof Element) ||
      appRoot === undefined ||
      !appRoot.contains(target)
    ) {
      setDiffSelectionSide(null, null);
      return;
    }
    const side = target.closest(".diff-side.side-left, .diff-side.side-right");
    if (side === null || !appRoot.contains(side)) {
      setDiffSelectionSide(null, null);
      return;
    }
    const grid = side.closest<HTMLElement>(".diff-grid");
    if (grid === null || !appRoot.contains(grid)) {
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
    if (!(target instanceof Element) || target.closest("button") !== null) {
      return;
    }
    const lineNo = target.closest<HTMLElement>(".line-no[data-line-pin-line]");
    if (lineNo === null || appRoot === undefined || !appRoot.contains(lineNo)) {
      return;
    }
    const pin = linePinFromElement(lineNo);
    if (pin === null) {
      return;
    }
    const pinKey = JSON.stringify(pin);
    const row = lineNo.closest<HTMLElement>(".diff-row");
    if (
      restoredLinePinKey === pinKey &&
      row?.classList.contains("pinned-line") === true
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
    const file = displayFiles().find((entry) => fileMatchesLinePin(entry, pin));
    if (file === undefined) {
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
    if (appRoot === undefined) {
      return;
    }
    const pin = getLinePinFromHash();
    setLinePin(pin);
    if (pin !== null) {
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

  const selectedRepoIdOrIdle = (): RepoId | null => {
    const repoId = selectedRepoId();
    if (repoId === null) {
      clearActiveRequest();
      resetDiffState("idle", "Choose a repo to load a diff.");
      return null;
    }
    return repoId;
  };

  const failLoad = (message: string) => {
    clearActiveRequest();
    resetDiffState("error", message);
  };

  const loadAgainstHead = (selectedEngine: DiffEngine = engine()) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    setControls((current) =>
      current === null ? current : { ...current, mode: "against-head" },
    );
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "against-head",
      left: "head",
      right: "worktree",
      base_branch: null,
      review_branch: null,
      show_untracked: true,
    });
  };

  const loadPreset = (selectedEngine: DiffEngine = engine()) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    setControls((current) =>
      current === null ? current : { ...current, mode: "preset" },
    );
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "preset",
      left: "presets",
      right: "new",
      base_branch: null,
      review_branch: null,
      show_untracked: false,
    });
  };

  const loadRefs = (
    left: string,
    right: string,
    selectedEngine: DiffEngine = engine(),
  ) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    const trimmedLeft = left.trim();
    const trimmedRight = right.trim();
    setControls((current) =>
      current === null ? current : { ...current, mode: "refs", left, right },
    );
    if (trimmedLeft.length === 0 || trimmedRight.length === 0) {
      failLoad("Enter both refs to compare them.");
      return;
    }
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "refs",
      left: trimmedLeft,
      right: trimmedRight,
      base_branch: null,
      review_branch: null,
      show_untracked: false,
    });
  };

  const loadBranchReview = (
    baseSource: BranchSource,
    baseRemote: string,
    baseBranch: string,
    branchSource: BranchSource,
    branchRemote: string,
    reviewBranch: string,
    selectedEngine: DiffEngine = engine(),
  ) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    const choices = refChoices();
    setControls((current) =>
      current === null
        ? current
        : {
            ...current,
            mode: "branch-review",
            baseSource,
            baseRemote,
            baseBranch,
            branchSource,
            branchRemote,
            reviewBranch,
          },
    );
    if (baseSource === "remote" && !baseRemote.trim()) {
      failLoad("Pick a base remote.");
      return;
    }
    if (!baseBranch.trim()) {
      failLoad("Pick a base branch.");
      return;
    }
    if (branchSource === "remote" && !branchRemote.trim()) {
      failLoad("Pick a branch remote.");
      return;
    }
    if (!reviewBranch.trim()) {
      failLoad("Pick a branch to compare against the base branch.");
      return;
    }
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "branch-review",
      left: "",
      right: "",
      base_branch: branchReviewRef(
        baseSource,
        baseRemote,
        baseBranch,
        choices.remote_names,
      ),
      review_branch: branchReviewRef(
        branchSource,
        branchRemote,
        reviewBranch,
        choices.remote_names,
      ),
      show_untracked: false,
    });
  };

  const reloadDiff = () => {
    const request = activeRequest();
    if (request === null) {
      return;
    }
    startDiff(request);
  };

  const loadEngine = (nextEngine: DiffEngine) => {
    setEngine(nextEngine);
    const request = activeRequest();
    if (request !== null) {
      startDiff({ ...request, engine: nextEngine });
    }
  };

  async function initializeRepo(repoId: RepoId) {
    setRepoRefs(null);
    setRepoRefsError(null);
    setRepoRefsPending(true);
    resetDiffState("idle", "Loading refs...");
    try {
      const refs = await fetchRepoRefs(repoId);
      if (selectedRepoId() !== repoId) {
        return;
      }
      const nextEngine = initialEngine();
      const nextControls = initialControls(refs);
      batch(() => {
        setRepoRefs(refs);
        setEngine(nextEngine);
        setControls(nextControls);
        setRepoRefsPending(false);
      });
      if (nextControls.mode === "refs") {
        loadRefs(nextControls.left, nextControls.right, nextEngine);
      } else if (nextControls.mode === "branch-review") {
        loadBranchReview(
          nextControls.baseSource,
          nextControls.baseRemote,
          nextControls.baseBranch,
          nextControls.branchSource,
          nextControls.branchRemote,
          nextControls.reviewBranch,
          nextEngine,
        );
      } else if (nextControls.mode === "preset") {
        loadPreset(nextEngine);
      } else {
        loadAgainstHead(nextEngine);
      }
    } catch (error) {
      if (selectedRepoId() !== repoId) {
        return;
      }
      batch(() => {
        setRepoRefsError(error);
        setRepoRefsPending(false);
        clearActiveRequest();
        resetDiffState(
          "error",
          error instanceof Error ? error.message : "Failed to load repo refs.",
        );
      });
    }
  }

  function repoIdFromUrl(availableRepos: RepoMark[]): RepoId | null {
    const rawRepoId = new URLSearchParams(window.location.search).get(
      "repo_id",
    );
    if (rawRepoId === null) {
      setSelectedRepoId(null);
      setRepoSelectionError("");
      resetDiffState("idle", "Choose a repo to load a diff.");
      return null;
    }
    const parsedRepoId = Number(rawRepoId);
    if (!Number.isInteger(parsedRepoId) || parsedRepoId <= 0) {
      setSelectedRepoId(null);
      setRepoSelectionError(`Invalid repo_id: ${rawRepoId}`);
      resetDiffState("idle", "Choose a repo to load a diff.");
      return null;
    }
    const repo = availableRepos.find(
      (candidate) => candidate.id === parsedRepoId,
    );
    if (repo === undefined) {
      setSelectedRepoId(null);
      setRepoSelectionError(`Invalid repo_id: ${rawRepoId}`);
      resetDiffState("idle", "Choose a repo to load a diff.");
      return null;
    }
    setSelectedRepoId(parsedRepoId);
    setRepoSelectionError("");
    return parsedRepoId;
  }

  async function loadReposFromUrl() {
    setReposPending(true);
    setReposError(null);
    try {
      const availableRepos = await fetchRepos();
      setRepoList(availableRepos);
      setReposPending(false);
      const repoId = repoIdFromUrl(availableRepos);
      if (repoId !== null) {
        await initializeRepo(repoId);
      }
    } catch (error) {
      batch(() => {
        setReposError(error);
        setReposPending(false);
      });
      return;
    }
  }

  onMount(() => {
    void loadReposFromUrl();
  });

  const selectRepo = (repo: RepoMark) => {
    const params = new URLSearchParams();
    params.set("repo_id", String(repo.id));
    history.replaceState(
      {},
      "",
      `/?${params.toString()}${window.location.hash}`,
    );
    batch(() => {
      setSelectedRepoId(repo.id);
      setRepoSelectionError("");
      setControls(null);
      setRepoRefs(null);
      clearActiveRequest();
      resetDiffState("idle", "Preparing diff...");
    });
    void initializeRepo(repo.id);
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
        if (card === null) {
          throw new Error(
            `Could not find file card for ${fileDisplayName(file)}.`,
          );
        }
        const target =
          card.querySelector<HTMLElement>(
            ".diff-row.hunk-anchor:not(.virtual-hunk-anchor)",
          ) ?? document.getElementById(fileBodyAnchorElementId(key));
        if (target === null) {
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
      if (target === null) {
        throw new Error(`Could not find directory group for ${group.label}.`);
      }
      const header = target.querySelector<HTMLElement>(
        ".directory-group-header",
      );
      if (header === null) {
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
            <Show when={repoList()}>
              {(repos) => (
                <RepoSelect
                  repos={repos()}
                  selectedRepoId={selectedRepoId()}
                  onRepoChange={selectRepo}
                />
              )}
            </Show>
            <div class="header-actions">
              <EngineSelect engine={engine()} onEngineChange={loadEngine} />
              <DiffViewSelect
                viewMode={diffViewMode()}
                onViewModeChange={(viewMode) => {
                  setDiffViewMode(viewMode);
                  const request = activeRequest();
                  if (request !== null) {
                    replaceUrlForRequest(request, viewMode);
                  }
                }}
              />
            </div>
          </div>
        </div>
        <SummaryView summary={loadedDiff()?.summary ?? emptySummary} />
      </header>

      <Show when={selectedRepoId() !== null && repoRefsPending()}>
        <p class="status">Loading refs...</p>
      </Show>

      <Show when={reposPending()}>
        <p class="status">Loading marked repos...</p>
      </Show>

      <Show when={repoRefsError()}>
        <section class="notice error">
          Failed to load refs: {String(repoRefsError())}
        </section>
      </Show>

      <Show when={reposError()}>
        <section class="notice error">
          Failed to load marked repos: {String(reposError())}
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
              onAgainstHead={loadAgainstHead}
              onPreset={loadPreset}
              onRefs={loadRefs}
              onBranchReview={loadBranchReview}
            />
            <p class={`status ${status()}`}>{statusText()}</p>
            <FileList
              files={displayFiles()}
              loadedDiff={loadedDiff()}
              activeRequestIdentity={activeRequestIdentity}
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
              updateLoadedDiff={updateLoadedDiff}
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

function RepoSelect(props: {
  repos: RepoMark[];
  selectedRepoId: RepoId | null;
  onRepoChange: (repo: RepoMark) => void;
}) {
  const handleRepoChange = (select: HTMLSelectElement) => {
    const nextRepoId = Number(select.value);
    if (!Number.isInteger(nextRepoId)) {
      return;
    }
    const repo = props.repos.find((candidate) => candidate.id === nextRepoId);
    if (repo === undefined) {
      return;
    }
    if (repo.id === props.selectedRepoId) {
      select.blur();
      return;
    }
    props.onRepoChange(repo);
    select.blur();
  };

  return (
    <label class="engine-select repo-select">
      <span>Repo</span>
      <select
        aria-label="Repo"
        value={
          props.selectedRepoId === null ? "" : String(props.selectedRepoId)
        }
        onChange={(event) => handleRepoChange(event.currentTarget)}
      >
        <option value="" disabled>
          Choose repo
        </option>
        <For each={props.repos}>
          {(repo) => <option value={repo.id}>{repo.name}</option>}
        </For>
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
