import {
  For,
  Show,
  batch,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";
import { render } from "solid-js/web";
import {
  QueryClient,
  QueryClientProvider,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { createHunkNavigation } from "./hunkNavigation";
import {
  type Defaults,
  type DiffEngine,
  type DiffMode,
  type DiffRequest,
  type DiffRow,
  type FileEntry,
  type FileKind,
  type NotebookCellEntry,
  type NotebookSection,
  type NotebookSummary,
  type RefChoices,
  type Summary,
  fetchDefaults,
  fetchDiff,
  fetchFileDiff,
  fetchNotebookSection,
} from "./api";
import { DiffGrid, type DiffViewMode } from "./DiffGrid";
import "./styles.css";

type LoadState = "idle" | "loading" | "done" | "error";
type BranchSource = "local" | "remote";
type ControlsState = {
  mode: DiffMode;
  left: string;
  right: string;
  baseSource: BranchSource;
  baseRemote: string;
  baseBranch: string;
  branchSource: BranchSource;
  branchRemote: string;
  reviewBranch: string;
};

type AutocompleteGroup = [string, string[]];
type DebugMetrics = {
  fps: string;
  nodes: string;
  spans: string;
};
type LinePin = {
  file: string;
  side: "left" | "right";
  line: string;
};

const modeSides: Record<
  Exclude<DiffMode, "refs" | "branch-review">,
  [string, string]
> = {
  files: ["index", "worktree"],
  staged: ["head", "index"],
  "against-head": ["head", "worktree"],
};

const modeLabels: Record<DiffMode, string> = {
  files: "Diff files",
  staged: "Diff staged",
  "against-head": "Diff against HEAD",
  refs: "Compare refs",
  "branch-review": "Branch review",
};
const topLevelModes: DiffMode[] = ["against-head", "refs", "branch-review"];
const engineLabels: Record<DiffEngine, string> = {
  dirdiff: "Dirdiff",
  git: "Git",
  difftastic: "Difftastic",
};
const diffViewLabels: Record<DiffViewMode, string> = {
  split: "Split",
  inline: "Inline",
};

const builtinSides = new Set(["head", "index", "worktree"]);
const linePinHashKey = "pin";
const refSectionLabels: Record<string, string> = {
  builtins: "Built-ins",
  locals: "Local branches",
  remotes: "Remote refs",
  remote_names: "Remotes",
  remote_branches: "Remote branches",
};
const builtinRefDescriptions: Record<string, string> = {
  head: "Current commit on this branch.",
  index: "Staged snapshot, what the next commit would include.",
  worktree: "Files on disk, including unstaged changes.",
};

const emptySummary: Summary = {
  changed_files: 0,
  added_files: 0,
  removed_files: 0,
  updated_files: 0,
  changed_lines: 0,
  modified_lines: 0,
  added_lines: 0,
  removed_lines: 0,
  skipped_files: 0,
};

const emptyDebugMetrics: DebugMetrics = {
  fps: "--",
  nodes: "--",
  spans: "--",
};

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
    },
  },
});

function inferMode(
  left: string,
  right: string,
  baseBranch: string,
  reviewBranch: string,
): DiffMode {
  if (baseBranch || reviewBranch) {
    return "branch-review";
  }
  return left === "head" && right === "worktree" ? "against-head" : "refs";
}

function normalizeTopLevelMode(
  mode: DiffMode | null,
  left: string,
  right: string,
  baseBranch: string,
  reviewBranch: string,
): DiffMode {
  if (mode === "refs" || mode === "branch-review" || mode === "against-head") {
    return mode;
  }
  if (mode === "files" || mode === "staged") {
    return "against-head";
  }
  return inferMode(left, right, baseBranch, reviewBranch);
}

function initialControls(defaults: Defaults): ControlsState {
  const search = new URLSearchParams(window.location.search);
  const remoteNames = defaults.ref_choices.remote_names || [];
  const left = search.get("left") || defaults.left || "index";
  const right = search.get("right") || defaults.right || "worktree";
  const baseBranchRef = search.get("base_branch") || defaults.base_branch || "";
  const reviewBranchRef =
    search.get("review_branch") || defaults.review_branch || "";
  const baseBranchParts = splitRemoteQualifiedRef(baseBranchRef, remoteNames);
  const reviewBranchParts = splitRemoteQualifiedRef(
    reviewBranchRef,
    remoteNames,
  );
  const mode = normalizeTopLevelMode(
    (search.get("mode") as DiffMode | null) || defaults.mode || null,
    left,
    right,
    baseBranchParts.value,
    reviewBranchParts.value,
  );

  if (mode in modeSides) {
    const [modeLeft, modeRight] = modeSides[mode as keyof typeof modeSides];
    return {
      mode,
      left: modeLeft,
      right: modeRight,
      baseSource: baseBranchParts.remote ? "remote" : "local",
      baseRemote: baseBranchParts.remote,
      baseBranch: baseBranchParts.value,
      branchSource: reviewBranchParts.remote ? "remote" : "local",
      branchRemote: reviewBranchParts.remote,
      reviewBranch: reviewBranchParts.value,
    };
  }

  return {
    mode,
    left,
    right,
    baseSource: baseBranchParts.remote ? "remote" : "local",
    baseRemote: baseBranchParts.remote,
    baseBranch: baseBranchParts.value,
    branchSource: reviewBranchParts.remote ? "remote" : "local",
    branchRemote: reviewBranchParts.remote,
    reviewBranch: reviewBranchParts.value,
  };
}

function initialEngine(defaults: Defaults): DiffEngine {
  const engine = new URLSearchParams(window.location.search).get("engine");
  if (engine === "git" || engine === "dirdiff" || engine === "difftastic") {
    return engine;
  }
  return defaults.engine || "dirdiff";
}

function initialDiffViewMode(): DiffViewMode {
  const view = new URLSearchParams(window.location.search).get("view");
  if (view === "split" || view === "inline") {
    return view;
  }
  return "inline";
}

function buildRequest(
  controls: ControlsState,
  refChoices: RefChoices,
  engine: DiffEngine,
): DiffRequest | string {
  if (controls.mode === "refs") {
    if (!controls.left.trim() || !controls.right.trim()) {
      return "Enter both refs to compare them.";
    }
    return {
      engine,
      mode: controls.mode,
      left: controls.left.trim(),
      right: controls.right.trim(),
      base_branch: null,
      review_branch: null,
      show_untracked: false,
    };
  }

  if (controls.mode === "branch-review") {
    if (controls.baseSource === "remote" && !controls.baseRemote.trim()) {
      return "Pick a base remote.";
    }
    if (!controls.baseBranch.trim()) {
      return "Pick a base branch.";
    }
    if (controls.branchSource === "remote" && !controls.branchRemote.trim()) {
      return "Pick a branch remote.";
    }
    if (!controls.reviewBranch.trim()) {
      return "Pick a branch to compare against the base branch.";
    }
    return {
      engine,
      mode: controls.mode,
      left: "",
      right: "",
      base_branch: branchReviewRef(
        controls.baseSource,
        controls.baseRemote,
        controls.baseBranch,
        refChoices.remote_names,
      ),
      review_branch: branchReviewRef(
        controls.branchSource,
        controls.branchRemote,
        controls.reviewBranch,
        refChoices.remote_names,
      ),
      show_untracked: false,
    };
  }

  const [left, right] = modeSides[controls.mode];
  return {
    engine,
    mode: controls.mode,
    left,
    right,
    base_branch: null,
    review_branch: null,
    show_untracked: controls.mode === "against-head",
  };
}

function requestQuery(request: DiffRequest): URLSearchParams {
  const params = new URLSearchParams();
  params.set("engine", request.engine);
  params.set("mode", request.mode);
  if (request.left) {
    params.set("left", request.left);
  }
  if (request.right) {
    params.set("right", request.right);
  }
  if (request.base_branch) {
    params.set("base_branch", request.base_branch);
  }
  if (request.review_branch) {
    params.set("review_branch", request.review_branch);
  }
  if (request.show_untracked) {
    params.set("show_untracked", "true");
  }
  return params;
}

function appQuery(
  request: DiffRequest,
  viewMode: DiffViewMode,
): URLSearchParams {
  const params = requestQuery(request);
  params.set("view", viewMode);
  return params;
}

function statusLabel(
  request: DiffRequest,
  leftLabel?: string,
  rightLabel?: string,
): string {
  if (request.mode === "files") {
    return "Unstaged changes in working tree";
  }
  if (request.mode === "staged") {
    return "Staged changes ready to commit";
  }
  if (request.mode === "against-head") {
    return "Working tree vs HEAD";
  }
  if (request.mode === "branch-review") {
    return `${request.review_branch} vs ${request.base_branch}`;
  }
  return `${leftLabel || request.left} vs ${rightLabel || request.right}`;
}

function loadedStatusLabel(
  baseStatus: string,
  loadedFiles: number,
  failedDetailFiles: number,
): string {
  const fileWord = loadedFiles === 1 ? "file" : "files";
  const failureText =
    failedDetailFiles > 0 ? `, failed details ${failedDetailFiles}` : "";
  return `${baseStatus} · loaded ${loadedFiles} ${fileWord}${failureText}`;
}

function App() {
  const defaults = createQuery(() => ({
    queryKey: ["defaults"],
    queryFn: fetchDefaults,
    staleTime: 0,
  }));
  const [engine, setEngine] = createSignal<DiffEngine>("dirdiff");
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

  const refChoices = () =>
    defaults.data?.ref_choices ?? {
      builtins: [],
      locals: [],
      remotes: [],
      remote_names: [],
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
    if (!value || initialized) {
      return;
    }
    initialized = true;
    const nextEngine = initialEngine(value);
    const nextControls = initialControls(value);
    setEngine(nextEngine);
    setControls(nextControls);
    const nextRequest = buildRequest(nextControls, refChoices(), nextEngine);
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
    if (shouldIgnoreHunkNavKeyEvent(event)) {
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

  const setDiffSelectionSide = (side: "left" | "right" | null) => {
    appRoot
      ?.querySelector<HTMLElement>(".diff-grid[data-diff-selection-side]")
      ?.removeAttribute("data-diff-selection-side");
    if (!side) {
      return;
    }
  };

  const onPointerDown = (event: PointerEvent) => {
    const target = event.target;
    if (!(target instanceof Element) || !appRoot?.contains(target)) {
      setDiffSelectionSide(null);
      return;
    }
    const side = target.closest(".diff-side.side-left, .diff-side.side-right");
    if (!side || !appRoot.contains(side)) {
      setDiffSelectionSide(null);
      return;
    }
    const grid = side.closest<HTMLElement>(".diff-grid");
    if (!grid || !appRoot.contains(grid)) {
      setDiffSelectionSide(null);
      return;
    }
    setDiffSelectionSide(
      side.classList.contains("side-left") ? "left" : "right",
    );
    grid.dataset.diffSelectionSide = side.classList.contains("side-left")
      ? "left"
      : "right";
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
    setDiffSelectionSide(null);
  });

  const loadControls = (
    nextControls: ControlsState,
    selectedEngine: DiffEngine = engine(),
  ) => {
    setControls(nextControls);
    const nextRequest = buildRequest(
      nextControls,
      refChoices(),
      selectedEngine,
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

      <Show when={defaults.isPending}>
        <p class="status">Loading defaults...</p>
      </Show>

      <Show when={defaults.error}>
        <section class="notice error">
          Failed to load defaults: {String(defaults.error)}
        </section>
      </Show>

      <Show when={controls()}>
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

function DebugHud(props: { open: boolean }) {
  const [metrics, setMetrics] = createSignal<DebugMetrics>(emptyDebugMetrics);
  let frame = 0;
  let sampleStartedAt = performance.now();
  let sampleFrames = 0;
  let displayUpdatedAt = sampleStartedAt;
  let currentFps = 0;

  const formatCount = (value: number) => value.toLocaleString();

  const updateMetrics = () => {
    setMetrics({
      fps: currentFps ? String(Math.round(currentFps)) : "--",
      nodes: formatCount(document.querySelectorAll("*").length),
      spans: formatCount(document.querySelectorAll("span").length),
    });
  };

  const tick = (now: number) => {
    sampleFrames += 1;
    const sampleElapsed = now - sampleStartedAt;
    if (sampleElapsed >= 400) {
      currentFps = (sampleFrames * 1000) / sampleElapsed;
      sampleStartedAt = now;
      sampleFrames = 0;
    }
    if (props.open && now - displayUpdatedAt >= 900) {
      updateMetrics();
      displayUpdatedAt = now;
    }
    frame = requestAnimationFrame(tick);
  };

  onMount(() => {
    frame = requestAnimationFrame(tick);
    onCleanup(() => {
      cancelAnimationFrame(frame);
    });
  });

  createEffect(() => {
    if (props.open) {
      updateMetrics();
    }
  });

  return (
    <Show when={props.open}>
      <div class="debug-hud" aria-label="Developer metrics">
        <DebugMetric label="FPS" value={metrics().fps} />
        <DebugMetric label="Nodes" value={metrics().nodes} />
        <DebugMetric label="Spans" value={metrics().spans} />
      </div>
    </Show>
  );
}

function DebugMetric(props: { label: string; value: string }) {
  return (
    <div class="debug-metric">
      <span class="debug-metric-label">{props.label}</span>
      <strong class="debug-metric-value">{props.value}</strong>
    </div>
  );
}

function HunkNav(props: {
  debugOpen: boolean;
  helpOpen: boolean;
  onHelpOpenChange: (open: boolean) => void;
  onNext: () => void;
  onPrev: () => void;
}) {
  return (
    <div class="hud-stack">
      <DebugHud open={props.debugOpen} />
      <HelpModal open={props.helpOpen} onOpenChange={props.onHelpOpenChange} />
      <nav class="hunk-nav" aria-label="Hunk navigation">
        <button type="button" onClick={props.onNext} title="Next hunk (n)">
          Next <kbd>n</kbd>
        </button>
        <button type="button" onClick={props.onPrev} title="Previous hunk (N)">
          Prev <kbd>N</kbd>
        </button>
        <button
          type="button"
          onClick={() => props.onHelpOpenChange(!props.helpOpen)}
          aria-expanded={props.helpOpen}
          title="Hotkey help (h)"
        >
          Help <kbd>h</kbd>
        </button>
      </nav>
    </div>
  );
}

function HelpModal(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Show when={props.open}>
      <div
        class="help-modal-backdrop"
        onClick={() => props.onOpenChange(false)}
      >
        <section
          class="help-modal"
          aria-label="Hotkey help"
          onClick={(event) => event.stopPropagation()}
        >
          <div class="help-modal-header">
            <strong>Hotkeys</strong>
            <button type="button" onClick={() => props.onOpenChange(false)}>
              Close
            </button>
          </div>
          <HotkeyHelpSection title="Navigation">
            <HotkeyHelpRow keys="n" label="Go to the next hunk" />
            <HotkeyHelpRow keys="N" label="Go to the previous hunk" />
            <HotkeyHelpRow keys="p" label="Go to the top" />
          </HotkeyHelpSection>
          <HotkeyHelpSection title="UI">
            <HotkeyHelpRow keys="t" label="Toggle the file tree" />
            <HotkeyHelpRow keys="i" label="Toggle inline diff view" />
            <HotkeyHelpRow keys="s" label="Show all files" />
            <HotkeyHelpRow keys="f" label="Fold all files" />
          </HotkeyHelpSection>
          <HotkeyHelpSection title="Misc">
            <HotkeyHelpRow keys="r" label="Reload the current diff" />
            <HotkeyHelpRow keys="d" label="Toggle developer metrics" />
            <HotkeyHelpRow keys="h" label="Toggle this help panel" />
          </HotkeyHelpSection>
        </section>
      </div>
    </Show>
  );
}

function HotkeyHelpSection(props: { title: string; children: JSX.Element }) {
  return (
    <section class="help-modal-section">
      <h2>{props.title}</h2>
      <div class="help-modal-grid">{props.children}</div>
    </section>
  );
}

function HotkeyHelpRow(props: { keys: string; label: string }) {
  return (
    <div class="help-hud-row">
      <kbd>{props.keys}</kbd>
      <span>{props.label}</span>
    </div>
  );
}

function Controls(props: {
  controls: ControlsState;
  refChoices: RefChoices;
  onLoad: (controls: ControlsState) => void;
}) {
  const [draft, setDraft] = createSignal<ControlsState>(props.controls);
  createEffect(() => setDraft(props.controls));

  const updateDraft = (patch: Partial<ControlsState>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };

  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    props.onLoad(draft());
  };

  return (
    <form class="controls" onSubmit={submit}>
      <fieldset class="mode-tabs">
        <legend>View</legend>
        <For each={topLevelModes}>
          {(mode) => (
            <button
              type="button"
              classList={{ "is-active": draft().mode === mode }}
              aria-pressed={draft().mode === mode}
              onClick={() => {
                const nextDraft = { ...draft(), mode };
                setDraft(nextDraft);
                props.onLoad(nextDraft);
              }}
            >
              {modeLabels[mode]}
            </button>
          )}
        </For>
      </fieldset>

      <Show when={draft().mode === "refs"}>
        <AutocompleteField
          label="Old ref"
          value={draft().left}
          groups={(query) =>
            filterRefChoices(props.refChoices, query, [
              "builtins",
              "locals",
              "remotes",
            ])
          }
          onValue={(left) => updateDraft({ left })}
        />
        <AutocompleteField
          label="New ref"
          value={draft().right}
          groups={(query) =>
            filterRefChoices(props.refChoices, query, [
              "builtins",
              "locals",
              "remotes",
            ])
          }
          onValue={(right) => updateDraft({ right })}
        />
      </Show>

      <Show when={draft().mode === "branch-review"}>
        <BranchSourceField
          label="Base remote"
          source={draft().baseSource}
          remote={draft().baseRemote}
          remoteChoices={props.refChoices.remote_names || []}
          onSource={(baseSource) =>
            updateDraft({
              baseSource,
              baseRemote:
                baseSource === "remote" && !draft().baseRemote
                  ? (props.refChoices.remote_names || [])[0] || ""
                  : draft().baseRemote,
            })
          }
          onRemote={(baseRemote) => updateDraft({ baseRemote })}
        />
        <AutocompleteField
          label="Base branch"
          value={draft().baseBranch}
          groups={(query) =>
            filterBranchChoices(
              props.refChoices,
              draft().baseSource,
              draft().baseRemote,
              query,
            )
          }
          onValue={(baseBranch) => updateDraft({ baseBranch })}
        />
        <BranchSourceField
          label="Branch remote"
          source={draft().branchSource}
          remote={draft().branchRemote}
          remoteChoices={props.refChoices.remote_names || []}
          onSource={(branchSource) =>
            updateDraft({
              branchSource,
              branchRemote:
                branchSource === "remote" && !draft().branchRemote
                  ? (props.refChoices.remote_names || [])[0] || ""
                  : draft().branchRemote,
            })
          }
          onRemote={(branchRemote) => updateDraft({ branchRemote })}
        />
        <AutocompleteField
          label="Branch to review"
          value={draft().reviewBranch}
          groups={(query) =>
            filterBranchChoices(
              props.refChoices,
              draft().branchSource,
              draft().branchRemote,
              query,
            )
          }
          onValue={(reviewBranch) => updateDraft({ reviewBranch })}
        />
      </Show>

      <button class="load-button" type="submit">
        Load
      </button>
    </form>
  );
}

function BranchSourceField(props: {
  label: string;
  source: BranchSource;
  remote: string;
  remoteChoices: string[];
  onSource: (source: BranchSource) => void;
  onRemote: (remote: string) => void;
}) {
  let input: HTMLInputElement | undefined;
  const [focused, setFocused] = createSignal(false);
  const [blurTimer, setBlurTimer] = createSignal<number | undefined>();
  const groups = createMemo(() => {
    if (!focused() || props.source !== "remote") {
      return [];
    }
    const values = filterValues(props.remoteChoices, props.remote);
    return values.length ? [["remote_names", values] as AutocompleteGroup] : [];
  });

  onMount(() => {
    if (!input) {
      return;
    }
    const open = () => setFocused(true);
    input.addEventListener("focus", open);
    input.addEventListener("blur", closeSoon);
    onCleanup(() => {
      input?.removeEventListener("focus", open);
      input?.removeEventListener("blur", closeSoon);
    });
  });

  onCleanup(() => {
    const timer = blurTimer();
    if (timer) {
      clearTimeout(timer);
    }
  });

  const closeSoon = () => {
    setBlurTimer(window.setTimeout(() => setFocused(false), 120));
  };

  const keepOpen = () => {
    const timer = blurTimer();
    if (timer) {
      clearTimeout(timer);
      setBlurTimer(undefined);
    }
  };

  const toggleSource = () => {
    props.onSource(props.source === "local" ? "remote" : "local");
    setFocused(props.source === "local");
  };

  return (
    <div class="field branch-source-field autocomplete-host">
      <span>{props.label}</span>
      <div
        classList={{
          "branch-source-control": true,
          "is-remote": props.source === "remote",
        }}
      >
        <button
          type="button"
          class="branch-source-toggle"
          aria-pressed={props.source === "remote"}
          onClick={toggleSource}
        >
          {props.source === "remote" ? "Remote" : "Local"}
        </button>
        <Show when={props.source === "remote"}>
          <input
            ref={input}
            class="branch-source-remote"
            value={props.remote}
            aria-label={props.label}
            placeholder="remote"
            spellcheck={false}
            autocomplete="off"
            onClick={() => setFocused(true)}
            onPointerDown={() => setFocused(true)}
            onInput={(event) => {
              props.onRemote(event.currentTarget.value);
              setFocused(true);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setFocused(false);
              }
            }}
          />
        </Show>
      </div>
      <Show when={groups().length > 0}>
        <div class="autocomplete-panel" onMouseDown={keepOpen}>
          <For each={groups()}>
            {([section, values]) => (
              <div class="autocomplete-section">
                <div class="autocomplete-section-label">
                  {refSectionLabels[section] || section}
                </div>
                <For each={values}>
                  {(value) => (
                    <button
                      type="button"
                      class="autocomplete-option"
                      onMouseDown={(event) => {
                        event.preventDefault();
                        props.onRemote(value);
                        setFocused(false);
                      }}
                    >
                      {value}
                    </button>
                  )}
                </For>
              </div>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}

function AutocompleteField(props: {
  label: string;
  value: string;
  groups: (query: string) => AutocompleteGroup[];
  onValue: (value: string) => void;
}) {
  let input: HTMLInputElement | undefined;
  const [focused, setFocused] = createSignal(false);
  const [query, setQuery] = createSignal("");
  const [blurTimer, setBlurTimer] = createSignal<number | undefined>();
  const groups = createMemo(() => (focused() ? props.groups(query()) : []));

  onMount(() => {
    if (!input) {
      return;
    }
    const open = () => {
      setQuery("");
      setFocused(true);
    };
    input.addEventListener("focus", open);
    input.addEventListener("blur", closeSoon);
    onCleanup(() => {
      input?.removeEventListener("focus", open);
      input?.removeEventListener("blur", closeSoon);
    });
  });

  onCleanup(() => {
    const timer = blurTimer();
    if (timer) {
      clearTimeout(timer);
    }
  });

  const closeSoon = () => {
    setBlurTimer(
      window.setTimeout(() => {
        setFocused(false);
        setQuery("");
      }, 120),
    );
  };

  const keepOpen = () => {
    const timer = blurTimer();
    if (timer) {
      clearTimeout(timer);
      setBlurTimer(undefined);
    }
  };

  return (
    <label class="field autocomplete-host">
      <span>{props.label}</span>
      <input
        ref={input}
        value={props.value}
        spellcheck={false}
        autocomplete="off"
        onClick={() => {
          setQuery("");
          setFocused(true);
        }}
        onPointerDown={() => {
          setQuery("");
          setFocused(true);
        }}
        onInput={(event) => {
          props.onValue(event.currentTarget.value);
          setQuery(event.currentTarget.value);
          setFocused(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setFocused(false);
            setQuery("");
          }
        }}
      />
      <Show when={groups().length > 0}>
        <div class="autocomplete-panel" onMouseDown={keepOpen}>
          <For each={groups()}>
            {([section, values]) => (
              <div class="autocomplete-section">
                <div class="autocomplete-section-label">
                  {refSectionLabels[section] || section}
                </div>
                <For each={values}>
                  {(value) => {
                    const description = autocompleteOptionDescription(
                      section,
                      value,
                    );
                    return (
                      <button
                        type="button"
                        class="autocomplete-option"
                        onMouseDown={(event) => {
                          event.preventDefault();
                          props.onValue(value);
                          setFocused(false);
                          setQuery("");
                        }}
                      >
                        <span class="autocomplete-option-label">{value}</span>
                        <Show when={description}>
                          <span class="autocomplete-option-description">
                            {description}
                          </span>
                        </Show>
                      </button>
                    );
                  }}
                </For>
              </div>
            )}
          </For>
        </div>
      </Show>
    </label>
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

type FileGroup = {
  label: string;
  files: FileEntry[];
};

type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;
type FilesSetter = (updater: (current: FileEntry[]) => FileEntry[]) => void;
type SummarySetter = (updater: (current: Summary) => Summary) => void;
type StringMapSetter = (
  updater: (current: Record<string, string>) => Record<string, string>,
) => void;

function directoryExpansionValue(
  current: Record<string, boolean>,
  directory: string,
): boolean {
  return expansionValue(current, directory, true);
}

function expansionValue(
  current: Record<string, boolean>,
  key: string,
  defaultValue: boolean,
): boolean {
  if (Object.hasOwn(current, key)) {
    return current[key];
  }
  return defaultValue;
}

function nextFileExpansion(
  current: Record<string, boolean>,
  newFile: FileEntry,
  newFileKey: string,
): Record<string, boolean> {
  if (Object.hasOwn(current, newFileKey)) {
    return current;
  }
  return {
    ...current,
    [newFileKey]: newFile.default_expanded ?? false,
  };
}

function FileList(props: {
  files: FileEntry[];
  request: DiffRequest | null;
  requestVersion: () => number;
  fileOrder: Record<string, number>;
  diffViewMode: DiffViewMode;
  directoryExpansion: Record<string, boolean>;
  fileExpansion: Record<string, boolean>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
  setLazyFiles: FilesSetter;
  setSummary: SummarySetter;
  onSetAllExpanded: (expanded: boolean) => void;
}) {
  const groupsByLabel = createMemo(() => groupFilesByLabel(props.files));
  const groupLabels = createMemo(() => [...groupsByLabel().keys()]);
  const groupForLabel = (label: string) => {
    const group = groupsByLabel().get(label);
    if (!group) {
      throw new Error(`Could not find directory group ${label}.`);
    }
    return group;
  };

  const setDirectoryExpanded = (label: string, expanded: boolean) => {
    const group = groupForLabel(label);
    props.setDirectoryExpansion((current) => ({
      ...current,
      [label]: expanded,
    }));
    props.setFileExpansion((current) => ({
      ...current,
      ...Object.fromEntries(
        group.files.map((file) => [fileKey(file), expanded]),
      ),
    }));
  };

  return (
    <section class="file-list" aria-label="Changed files">
      <Show
        when={props.files.length > 0}
        fallback={<p class="empty">No files loaded yet.</p>}
      >
        <div class="repo-fold-controls">
          <button type="button" onClick={() => props.onSetAllExpanded(false)}>
            Fold all
          </button>
          <button type="button" onClick={() => props.onSetAllExpanded(true)}>
            Show all
          </button>
        </div>
        <div class="directory-groups">
          <For each={groupLabels()}>
            {(label) => (
              <DirectoryGroup
                group={() => groupForLabel(label)}
                request={props.request}
                requestVersion={props.requestVersion}
                expanded={props.directoryExpansion[label] ?? true}
                fileExpansion={props.fileExpansion}
                fileOrder={props.fileOrder}
                loadingFiles={props.loadingFiles}
                fileErrors={props.fileErrors}
                linePin={props.linePin}
                forcedRichFileIds={props.forcedRichFileIds}
                onFileVirtualizedChange={props.onFileVirtualizedChange}
                diffViewMode={props.diffViewMode}
                setExpanded={(expanded) =>
                  setDirectoryExpanded(label, expanded)
                }
                setFileExpanded={(key, expanded) =>
                  props.setFileExpansion((current) => ({
                    ...current,
                    [key]: expanded,
                  }))
                }
                setLoadingFiles={props.setLoadingFiles}
                setFileErrors={props.setFileErrors}
                setFiles={props.setFiles}
                setLazyFiles={props.setLazyFiles}
                setSummary={props.setSummary}
              />
            )}
          </For>
        </div>
      </Show>
    </section>
  );
}

function DirectoryGroup(props: {
  group: () => FileGroup;
  request: DiffRequest | null;
  requestVersion: () => number;
  diffViewMode: DiffViewMode;
  expanded: boolean;
  fileExpansion: Record<string, boolean>;
  fileOrder: Record<string, number>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setExpanded: (expanded: boolean) => void;
  setFileExpanded: (key: string, expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
  setLazyFiles: FilesSetter;
  setSummary: SummarySetter;
}) {
  const group = () => props.group();

  return (
    <section
      id={directoryElementId(group().label)}
      class="directory-group"
      classList={{ "is-collapsed": !props.expanded }}
    >
      <button
        type="button"
        class="directory-group-header"
        onClick={() => props.setExpanded(!props.expanded)}
      >
        <span class="directory-group-heading">
          <VisibilityIndicator size="large" visible={props.expanded} />
          <span class="directory-group-title">{group().label}</span>
        </span>
        <span class="badge badge-neutral">
          {group().files.length} file
          {group().files.length === 1 ? "" : "s"}
        </span>
      </button>
      <Show when={props.expanded}>
        <div class="directory-group-body">
          <For each={group().files}>
            {(file) => {
              const key = fileKey(file);
              return (
                <FileCard
                  file={file}
                  request={props.request}
                  requestVersion={props.requestVersion}
                  expanded={
                    props.fileExpansion[key] ?? file.default_expanded ?? false
                  }
                  loading={props.loadingFiles[key] ?? false}
                  error={props.fileErrors[key] ?? ""}
                  linePin={props.linePin}
                  forcedRichFileIds={props.forcedRichFileIds}
                  onFileVirtualizedChange={props.onFileVirtualizedChange}
                  diffViewMode={props.diffViewMode}
                  fileOrder={props.fileOrder}
                  setExpanded={(expanded) =>
                    props.setFileExpanded(key, expanded)
                  }
                  setLoadingFiles={props.setLoadingFiles}
                  setFileErrors={props.setFileErrors}
                  setFiles={props.setFiles}
                  setLazyFiles={props.setLazyFiles}
                  setSummary={props.setSummary}
                />
              );
            }}
          </For>
        </div>
      </Show>
    </section>
  );
}

function FileTreeSidebar(props: {
  files: FileEntry[];
  directoryExpansion: Record<string, boolean>;
  fileExpansion: Record<string, boolean>;
  activeHunkFileId: string | null;
  virtualizedFileIds: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  onScrollToDirectory: (group: FileGroup) => void;
  onScrollToFile: (file: FileEntry) => void;
}) {
  const groups = createMemo(() => [...groupFilesByLabel(props.files).values()]);
  const lineStats = createMemo(() =>
    props.files.reduce(
      (total, file) => addLineStats(total, fileLineStats(file)),
      emptyLineStats(),
    ),
  );
  const directoryExpanded = (group: FileGroup) =>
    expansionValue(props.directoryExpansion, group.label, true);
  const fileExpanded = (file: FileEntry) =>
    expansionValue(
      props.fileExpansion,
      fileKey(file),
      file.default_expanded ?? false,
    );

  const setDirectoryExpanded = (group: FileGroup, expanded: boolean) => {
    props.setDirectoryExpansion((current) => ({
      ...current,
      [group.label]: expanded,
    }));
    props.setFileExpansion((current) => ({
      ...current,
      ...Object.fromEntries(
        group.files.map((file) => [fileKey(file), expanded]),
      ),
    }));
  };

  const setFileExpanded = (file: FileEntry, expanded: boolean) => {
    props.setFileExpansion((current) => ({
      ...current,
      [fileKey(file)]: expanded,
    }));
  };
  const fileIsActiveHunkFile = (file: FileEntry) =>
    props.activeHunkFileId === fileElementId(fileKey(file));
  const fileIsVirtualized = (file: FileEntry) =>
    props.virtualizedFileIds.includes(fileElementId(fileKey(file)));

  createEffect(() => {
    if (!props.open || props.activeHunkFileId === null) {
      return;
    }
    requestAnimationFrame(() => {
      const activeRow = document.querySelector<HTMLElement>(
        `[data-file-tree-file-id="${props.activeHunkFileId}"]`,
      );
      activeRow?.scrollIntoView({ block: "nearest", behavior: "instant" });
    });
  });

  return (
    <Show when={props.files.length > 0}>
      <div class="file-tree-shell" classList={{ open: props.open }}>
        <Show when={props.open}>
          <aside
            id="fileTreeSidebar"
            class="file-tree-sidebar"
            aria-label="Changed file tree"
          >
            <div class="file-tree-groups">
              <For each={groups()}>
                {(group) => (
                  <section class="file-tree-group">
                    <div class="file-tree-directory">
                      <button
                        type="button"
                        class="file-tree-visibility-toggle"
                        onClick={() =>
                          setDirectoryExpanded(group, !directoryExpanded(group))
                        }
                        aria-label={
                          directoryExpanded(group)
                            ? `Fold ${group.label}`
                            : `Show ${group.label}`
                        }
                      >
                        <VisibilityIndicator
                          size="small"
                          visible={directoryExpanded(group)}
                        />
                      </button>
                      <button
                        type="button"
                        class="file-tree-directory-target"
                        onClick={() => props.onScrollToDirectory(group)}
                      >
                        {group.label}
                      </button>
                      <TreeLineStats stats={groupLineStats(group)} />
                    </div>
                    <For each={group.files}>
                      {(file) => {
                        const virtualized = () => fileIsVirtualized(file);
                        return (
                          <div
                            class="file-tree-file"
                            data-file-tree-file-id={fileElementId(
                              fileKey(file),
                            )}
                            classList={{
                              added: fileKindStatus(file.file_kind) === "added",
                              removed:
                                fileKindStatus(file.file_kind) === "deleted",
                              lazy: Boolean(file.lazy),
                              "active-hunk-file": fileIsActiveHunkFile(file),
                            }}
                            aria-current={
                              fileIsActiveHunkFile(file) ? "true" : undefined
                            }
                            title={fileDisplayName(file)}
                          >
                            <button
                              type="button"
                              class="file-tree-visibility-toggle"
                              onClick={() =>
                                setFileExpanded(file, !fileExpanded(file))
                              }
                              aria-label={
                                fileExpanded(file)
                                  ? `Fold ${fileDisplayName(file)}`
                                  : `Show ${fileDisplayName(file)}`
                              }
                            >
                              <VisibilityIndicator
                                size="small"
                                visible={fileExpanded(file)}
                                virtualized={virtualized()}
                              />
                            </button>
                            <button
                              type="button"
                              class="file-tree-file-target"
                              aria-current={
                                fileIsActiveHunkFile(file) ? "true" : undefined
                              }
                              onClick={() => props.onScrollToFile(file)}
                            >
                              <span class="file-tree-file-name">
                                {fileBasename(file)}
                              </span>
                              <TreeLineStats stats={fileLineStats(file)} />
                            </button>
                          </div>
                        );
                      }}
                    </For>
                  </section>
                )}
              </For>
            </div>
          </aside>
        </Show>
        <button
          type="button"
          class="file-tree-toggle"
          onClick={() => props.onOpenChange(!props.open)}
          aria-expanded={props.open}
          aria-controls="fileTreeSidebar"
          aria-label={props.open ? "Close file tree" : "Open file tree"}
        >
          <span class="file-tree-icon" aria-hidden="true">
            ▦
          </span>
          <Show when={props.open}>
            <span class="file-tree-label">Files</span>
            <TreeLineStats stats={lineStats()} />
          </Show>
          <kbd>t</kbd>
        </button>
      </div>
    </Show>
  );
}

function FileCard(props: {
  file: FileEntry;
  request: DiffRequest | null;
  requestVersion: () => number;
  diffViewMode: DiffViewMode;
  fileOrder: Record<string, number>;
  expanded: boolean;
  loading: boolean;
  error: string;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setExpanded: (expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
  setLazyFiles: FilesSetter;
  setSummary: SummarySetter;
}) {
  const queryClient = useQueryClient();
  let bodyViewport: HTMLDivElement | undefined;
  const [nearViewport, setNearViewport] = createSignal(false);
  const key = () => fileKey(props.file);
  const lineStats = () => fileLineStats(props.file);
  const displayName = () => fileDisplayName(props.file);
  const needsHydration = () => !fileEntryIsHydrated(props.file);
  const isPinnedFile = () =>
    props.linePin !== null && fileMatchesLinePin(props.file, props.linePin);
  const isForcedRichFile = () =>
    props.forcedRichFileIds.includes(fileElementId(key()));
  const canVirtualizeBody = () =>
    props.file.render_kind !== "notebook" && canRenderRows();
  const shouldRenderRichBody = () =>
    !canVirtualizeBody() ||
    nearViewport() ||
    isPinnedFile() ||
    isForcedRichFile();
  const isVirtualizedBody = () =>
    props.expanded && canRenderRows() && !shouldRenderRichBody();
  createEffect(() => {
    const fileId = fileElementId(key());
    props.onFileVirtualizedChange(fileId, isVirtualizedBody());
    onCleanup(() => props.onFileVirtualizedChange(fileId, false));
  });
  const lazyTitle = () => {
    switch (props.file.lazy) {
      case "deleted":
        return "Load deleted file diff";
      case "generated":
        return "Load generated diff";
      case "too_big":
        return "Load large diff";
      case "untracked":
        return "Load untracked file";
      case "pure_renamed":
        return "Load renamed file diff";
      default:
        return "Load diff";
    }
  };
  const lazyMeta = () => {
    switch (props.file.lazy) {
      case "deleted":
        return `${displayName()} is deleted. Click to fetch and open it.`;
      case "generated":
        return `${displayName()} looks generated. Click to fetch and open it.`;
      case "too_big":
        return `${displayName()} is large. Click to fetch and open it.`;
      case "untracked":
        return `${displayName()} is untracked. Click to fetch and open it.`;
      case "pure_renamed":
        return `${displayName()} was renamed without content changes. Click to fetch and open it.`;
      default:
        return `${displayName()} is folded by default. Click to fetch and open it.`;
    }
  };
  const canRenderRows = () =>
    fileEntryIsHydrated(props.file) &&
    props.file.render_kind !== "notebook" &&
    (props.file.rows?.length ?? 0) > 0;

  createEffect(() => {
    props.expanded;
    props.file;
    if (!bodyViewport || !props.expanded || !canVirtualizeBody()) {
      setNearViewport(false);
      return;
    }
    if (!("IntersectionObserver" in window)) {
      setNearViewport(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(entry?.isIntersecting ?? false),
      { rootMargin: "1500px 0px" },
    );
    observer.observe(bodyViewport);
    onCleanup(() => observer.disconnect());
  });

  const expand = async () => {
    props.setExpanded(true);
    const activeRequest = props.request;
    const activeVersion = props.requestVersion();
    const activeKey = key();
    if (!needsHydration() || !activeRequest || props.loading) {
      return;
    }
    props.setLoadingFiles((current) => ({
      ...current,
      [activeKey]: true,
    }));
    props.setFileErrors((current) => ({ ...current, [activeKey]: "" }));
    try {
      const hydrated = await queryClient.fetchQuery({
        queryKey: fileDiffQueryKey(activeRequest, props.file),
        queryFn: () => fetchFileDiff(activeRequest, props.file),
        staleTime: 0,
      });
      if (props.requestVersion() !== activeVersion) {
        return;
      }
      const nextEntry = { ...props.file, ...hydrated, lazy: null };
      const nextKey = fileKey(nextEntry);
      props.setFiles((current) => {
        const withoutCurrent = current.filter(
          (entry) => fileKey(entry) !== nextKey,
        );
        return sortFilesByOrder(
          [...withoutCurrent, nextEntry],
          props.fileOrder,
        );
      });
      props.setLazyFiles((current) =>
        current.filter((entry) => fileKey(entry) !== activeKey),
      );
      props.setSummary((current) =>
        addHydratedNotebookSummary(current, nextEntry),
      );
    } catch (error) {
      if (props.requestVersion() !== activeVersion) {
        return;
      }
      props.setFileErrors((current) => ({
        ...current,
        [activeKey]:
          error instanceof Error ? error.message : "Failed to load file diff.",
      }));
    } finally {
      if (props.requestVersion() !== activeVersion) {
        return;
      }
      props.setLoadingFiles((current) => ({
        ...current,
        [activeKey]: false,
      }));
    }
  };

  const toggle = () => {
    if (props.expanded) {
      props.setExpanded(false);
      return;
    }
    void expand();
  };

  return (
    <article
      id={fileElementId(key())}
      class="file-card"
      classList={{
        "is-collapsed": !props.expanded,
        "file-card-lazy-generated": Boolean(props.file.lazy),
      }}
    >
      <button
        type="button"
        class="file-card-header"
        onClick={toggle}
        aria-expanded={props.expanded}
      >
        <span class="file-card-heading">
          <VisibilityIndicator
            size="large"
            visible={props.expanded && !isVirtualizedBody()}
            virtualized={isVirtualizedBody()}
          />
          <span class="file-card-title-row">
            <h2>{displayName()}</h2>
            <span class="file-card-status">
              {fileKindLabel(props.file.file_kind)}
            </span>
          </span>
        </span>
        <span class="file-stats">
          <span class="delta added">+ {formatLineStat(lineStats().added)}</span>
          <span class="delta changed">
            ~ {formatLineStat(lineStats().modified)}
          </span>
          <span class="delta removed">
            - {formatLineStat(lineStats().removed)}
          </span>
        </span>
      </button>
      <div
        id={fileBodyAnchorElementId(key())}
        class="file-card-scroll-target"
        aria-hidden="true"
      />
      <Show when={!props.expanded && canRenderRows()}>
        <HunkSkipAnchors file={props.file} />
      </Show>
      <Show
        when={
          props.expanded && (!needsHydration() || props.loading || props.error)
        }
      >
        <div ref={bodyViewport} class="file-card-body">
          <Show when={props.loading}>
            <p class="file-placeholder">Loading file diff...</p>
          </Show>
          <Show when={props.error}>
            <p class="file-placeholder error-text">{props.error}</p>
          </Show>
          <Show when={!props.loading && !props.error}>
            <Show when={props.file.render_kind === "notebook"}>
              <NotebookFile
                file={props.file}
                request={props.request}
                diffViewMode={props.diffViewMode}
              />
            </Show>
            <Show when={props.file.render_kind !== "notebook"}>
              <Show
                when={canRenderRows()}
                fallback={<FilePlaceholder file={props.file} />}
              >
                <Show
                  when={shouldRenderRichBody()}
                  fallback={<PlainSplitFileDiff file={props.file} />}
                >
                  <DiffGrid
                    file={props.file}
                    viewMode={props.diffViewMode}
                    semanticReplaceRows={props.request?.engine === "difftastic"}
                  />
                </Show>
              </Show>
            </Show>
          </Show>
        </div>
      </Show>
      <Show when={needsHydration() && props.file.lazy && !props.loading}>
        <button
          type="button"
          class="file-lazy-load-toggle"
          classList={{
            "is-untracked": props.file.lazy === "untracked",
            "is-generated": props.file.lazy === "generated",
            "is-deleted": props.file.lazy === "deleted",
            "is-too-big": props.file.lazy === "too_big",
            "is-pure-renamed": props.file.lazy === "pure_renamed",
          }}
          onClick={expand}
        >
          <span class="file-lazy-load-toggle-title">{lazyTitle()}</span>
          <span class="file-lazy-load-toggle-meta">{lazyMeta()}</span>
        </button>
      </Show>
    </article>
  );
}

function FilePlaceholder(props: { file: FileEntry }) {
  if (!fileEntryIsHydrated(props.file)) {
    return (
      <p class="file-placeholder">
        {props.file.lazy
          ? "Click Load diff to fetch this file."
          : "Loading file diff..."}
      </p>
    );
  }
  return <p class="file-placeholder">No rows for this file.</p>;
}

function PlainSplitFileDiff(props: { file: FileEntry }) {
  const text = () => plainSplitText(props.file.rows ?? []);
  const hunkAnchors = () => virtualHunkAnchors(props.file.rows ?? []);

  return (
    <div class="plain-split-diff" aria-label="Virtualized plain split diff">
      <For each={hunkAnchors()}>
        {(anchor) => (
          <span
            class="diff-row hunk-anchor virtual-hunk-anchor"
            style={{ top: `${virtualHunkAnchorTop(anchor.rowIndex)}px` }}
            aria-hidden="true"
          />
        )}
      </For>
      <pre>{text().left}</pre>
      <pre>{text().right}</pre>
    </div>
  );
}

function HunkSkipAnchors(props: { file: FileEntry }) {
  const hunkAnchors = () => virtualHunkAnchors(props.file.rows ?? []);

  return (
    <div class="hunk-skip-anchors" aria-hidden="true">
      <For each={hunkAnchors()}>
        {() => <span class="diff-row hunk-anchor hunk-skip" />}
      </For>
    </div>
  );
}

const RICH_PRELOAD_FILE_RADIUS = 2;

function richPreloadFileIdsForAnchor(
  anchor: HTMLElement,
  files: FileEntry[],
): string[] {
  return richPreloadFileIdsForFileId(fileIdForHunkAnchor(anchor), files);
}

function fileIdForHunkAnchor(anchor: HTMLElement): string | null {
  return anchor.closest<HTMLElement>(".file-card")?.id ?? null;
}

function richPreloadFileIdsForFileId(
  activeFileId: string | null,
  files: FileEntry[],
): string[] {
  const changedFileIds = files
    .filter(fileCanHaveDomHunks)
    .map((file) => fileElementId(fileKey(file)));
  if (!changedFileIds.length) {
    return [];
  }

  const forced = new Set<string>();
  forced.add(changedFileIds[0]);
  forced.add(changedFileIds[changedFileIds.length - 1]);

  const activeIndex =
    activeFileId === null ? -1 : changedFileIds.indexOf(activeFileId);
  if (activeIndex !== -1) {
    const start = Math.max(0, activeIndex - RICH_PRELOAD_FILE_RADIUS);
    const end = Math.min(
      changedFileIds.length - 1,
      activeIndex + RICH_PRELOAD_FILE_RADIUS,
    );
    for (let index = start; index <= end; index += 1) {
      forced.add(changedFileIds[index]);
    }
  }

  return [...forced];
}

function stringArraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => value === right[index]);
}

function fileCanHaveDomHunks(file: FileEntry): boolean {
  return (
    fileEntryIsHydrated(file) &&
    file.render_kind !== "notebook" &&
    (file.rows?.length ?? 0) > 0 &&
    fileHasChangedRows(file)
  );
}

function virtualHunkAnchors(rows: DiffRow[]): { rowIndex: number }[] {
  let previousChanged = false;
  const anchors: { rowIndex: number }[] = [];
  rows.forEach((row, rowIndex) => {
    if (row.status === "fold") {
      previousChanged = false;
      return;
    }

    const changed = isChangedDiffRowStatus(row.status);
    if (changed && !previousChanged) {
      anchors.push({ rowIndex });
    }
    previousChanged = changed;
  });
  return anchors;
}

function virtualHunkAnchorTop(rowIndex: number): number {
  return 10 + rowIndex * 17.4;
}

function isChangedDiffRowStatus(status: DiffRow["status"]): boolean {
  return status === "replace" || status === "insert" || status === "delete";
}

function plainSplitText(rows: DiffRow[]): { left: string; right: string } {
  const left: string[] = [];
  const right: string[] = [];

  for (const row of rows) {
    left.push(plainSideText(row, "left"));
    right.push(plainSideText(row, "right"));
  }

  return {
    left: left.join("\n"),
    right: right.join("\n"),
  };
}

function plainSideText(row: DiffRow, side: "left" | "right"): string {
  if (row.status === "fold") {
    return row.label ?? `... ${row.count ?? 0} lines`;
  }
  return (side === "left" ? row.left_text : row.right_text) ?? "";
}

function fileHasChangedRows(file: FileEntry): boolean {
  return (file.rows ?? []).some(
    (row) =>
      row.status === "insert" ||
      row.status === "delete" ||
      row.status === "replace",
  );
}

function NotebookFile(props: {
  file: FileEntry;
  request: DiffRequest | null;
  diffViewMode: DiffViewMode;
}) {
  const notebookSummary = () =>
    isNotebookSummary(props.file.summary) ? props.file.summary : null;
  const summary = () => props.file.summary;
  const changedCells = () => notebookSummary()?.changed_cells ?? 0;
  const cells = () => props.file.cells ?? [];

  return (
    <div class="notebook-file">
      <div class="notebook-summary">
        <span class="badge badge-neutral">
          {summary()?.left_exists ? "left exists" : "left missing"}
        </span>
        <span class="badge badge-neutral">
          {summary()?.right_exists ? "right exists" : "right missing"}
        </span>
        <span class="badge badge-neutral">
          {changedCells()} changed cell{changedCells() === 1 ? "" : "s"}
        </span>
        <Show when={notebookSummary()?.notebook_metadata_changed}>
          <span class="badge badge-neutral">notebook metadata changed</span>
        </Show>
      </div>

      <Show when={notebookSummary()?.notebook_metadata_changed}>
        <NotebookDetails
          file={props.file}
          request={props.request}
          title={notebookSectionSummary("Notebook metadata diff", {
            renderMode: props.file.notebook_metadata_render_mode ?? null,
            truncatedRows: props.file.notebook_metadata_truncated_rows ?? 0,
          })}
          section="notebook-metadata"
          leftLabel="Left notebook metadata"
          rightLabel="Right notebook metadata"
          diffViewMode={props.diffViewMode}
        />
      </Show>

      <div class="notebook-cells">
        <Show
          when={cells().length > 0}
          fallback={
            <p class="file-placeholder">
              No changed cells detected for the selected notebook sides.
            </p>
          }
        >
          <For each={cells()}>
            {(cell) => (
              <NotebookCell
                file={props.file}
                request={props.request}
                cell={cell}
                diffViewMode={props.diffViewMode}
              />
            )}
          </For>
        </Show>
      </div>
    </div>
  );
}

function NotebookCell(props: {
  file: FileEntry;
  request: DiffRequest | null;
  cell: NotebookCellEntry;
  diffViewMode: DiffViewMode;
}) {
  const cell = () => props.cell;
  const leftIndex = () => cell().left_index ?? "—";
  const rightIndex = () => cell().right_index ?? "—";

  return (
    <article class="notebook-cell-card">
      <header class="notebook-cell-header">
        <div>
          <h3>
            {cell().kind.toUpperCase()} {cell().cell_type} cell
          </h3>
          <p>
            Cell ID: {cell().cell_id ?? "missing"} · left #{leftIndex()} · right
            #{rightIndex()}
          </p>
        </div>
        <div class="badge-row">
          <span class={`badge ${notebookCellKindBadgeClass(cell().kind)}`}>
            {cell().kind}
          </span>
          <Show when={cell().metadata_changed}>
            <span class="badge badge-neutral">metadata changed</span>
          </Show>
          <Show when={cell().outputs_changed}>
            <span class="badge badge-neutral">outputs changed</span>
          </Show>
          <Show when={!cell().source_changed}>
            <span class="badge badge-neutral">source unchanged</span>
          </Show>
        </div>
      </header>

      <NotebookSectionView
        heading="Cell source"
        rows={cell().source_rows}
        foldHints={cell().source_fold_hints}
        leftLabel="Left source"
        rightLabel="Right source"
        renderMode={cell().source_render_mode}
        truncatedRows={cell().source_truncated_rows ?? 0}
        diffViewMode={props.diffViewMode}
      />

      <Show when={cell().metadata_changed}>
        <NotebookDetails
          file={props.file}
          request={props.request}
          title={notebookSectionSummary("Cell metadata diff", {
            renderMode: cell().metadata_render_mode,
            truncatedRows: cell().metadata_truncated_rows ?? 0,
          })}
          section="cell-metadata"
          cellKey={cell().cell_key}
          leftLabel="Left metadata"
          rightLabel="Right metadata"
          diffViewMode={props.diffViewMode}
        />
      </Show>

      <Show when={cell().outputs_changed}>
        <NotebookDetails
          file={props.file}
          request={props.request}
          title={notebookSectionSummary("Cell outputs diff", {
            renderMode: cell().outputs_render_mode,
            truncatedRows: cell().outputs_truncated_rows ?? 0,
          })}
          section="cell-outputs"
          cellKey={cell().cell_key}
          leftLabel="Left outputs"
          rightLabel="Right outputs"
          diffViewMode={props.diffViewMode}
        />
      </Show>
    </article>
  );
}

function NotebookDetails(props: {
  file: FileEntry;
  request: DiffRequest | null;
  title: string;
  section: string;
  cellKey?: string;
  leftLabel: string;
  rightLabel: string;
  diffViewMode: DiffViewMode;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [section, setSection] = createSignal<NotebookSection | null>(null);

  const load = async () => {
    if (!props.request || section() || loading()) {
      return;
    }
    setOpen(true);
    setLoading(true);
    setError("");
    try {
      const payload = await queryClient.fetchQuery({
        queryKey: notebookSectionQueryKey(
          props.request,
          props.file,
          props.section,
          props.cellKey ?? null,
        ),
        queryFn: () =>
          fetchNotebookSection(props.request!, props.file, {
            section: props.section,
            cellKey: props.cellKey,
          }),
        staleTime: 0,
      });
      setSection(payload);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Failed to load notebook section.",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <details
      class="notebook-details"
      open={open()}
      onToggle={(event) => {
        const nextOpen = event.currentTarget.open;
        setOpen(nextOpen);
        if (nextOpen) {
          void load();
        }
      }}
    >
      <summary>{props.title}</summary>
      <Show when={loading()}>
        <p class="notebook-details-message">Loading...</p>
      </Show>
      <Show when={error()}>
        <p class="file-placeholder error-text">{error()}</p>
      </Show>
      <Show when={section()}>
        {(payload) => (
          <NotebookSectionView
            rows={payload().rows}
            foldHints={payload().fold_hints}
            leftLabel={props.leftLabel}
            rightLabel={props.rightLabel}
            renderMode={payload().render_mode}
            truncatedRows={payload().truncated_rows}
            diffViewMode={props.diffViewMode}
          />
        )}
      </Show>
    </details>
  );
}

function NotebookSectionView(props: {
  heading?: string;
  rows: FileEntry["rows"];
  foldHints: FileEntry["fold_hints"];
  leftLabel: string;
  rightLabel: string;
  renderMode?: "plain" | null;
  truncatedRows?: number | null;
  diffViewMode: DiffViewMode;
}) {
  const file = (): FileEntry => ({
    file_kind: { type: "git", status: "modified" },
    left_path: null,
    right_path: null,
    left_label: props.leftLabel,
    right_label: props.rightLabel,
    rows: props.rows ?? [],
    fold_hints: props.foldHints ?? [],
    default_expanded: true,
  });

  return (
    <section class="notebook-section">
      <Show when={props.heading}>
        <p class="notebook-section-heading">{props.heading}</p>
      </Show>
      <DiffGrid file={file()} viewMode={props.diffViewMode} />
      <Show when={props.renderMode === "plain" || (props.truncatedRows ?? 0)}>
        <p class="notebook-section-note">
          {props.renderMode === "plain" ? "plain render" : ""}
          {props.renderMode === "plain" && (props.truncatedRows ?? 0)
            ? " · "
            : ""}
          {(props.truncatedRows ?? 0) ? `truncated ${props.truncatedRows}` : ""}
        </p>
      </Show>
    </section>
  );
}

function entryDirectoryPath(entry: FileEntry): string {
  const path = fileTreePath(entry);
  const lastSlash = path.lastIndexOf("/");
  return lastSlash >= 0 ? path.slice(0, lastSlash) : "";
}

function fileDisplayName(entry: FileEntry): string {
  return entry.display_name ?? fileTreePath(entry);
}

function fileBasename(entry: FileEntry): string {
  const path = fileTreePath(entry);
  const basename = path.split("/").at(-1);
  if (!basename) {
    throw new Error(`Could not derive file basename from ${path}.`);
  }
  return basename;
}

function fileTreePath(entry: FileEntry): string {
  if (entry.right_path) {
    return entry.right_path;
  }
  if (entry.left_path) {
    return entry.left_path;
  }
  throw new Error("File entry is missing paths.");
}

type LineStats = {
  added: number | null;
  modified: number | null;
  removed: number | null;
};

function emptyLineStats(): LineStats {
  return { added: 0, modified: 0, removed: 0 };
}

function addLineStats(left: LineStats, right: LineStats): LineStats {
  return {
    added: addLineStat(left.added, right.added),
    modified: addLineStat(left.modified, right.modified),
    removed: addLineStat(left.removed, right.removed),
  };
}

function addLineStat(left: number | null, right: number | null): number | null {
  if (left === null || right === null) {
    return null;
  }
  return left + right;
}

function unknownLineStats(): LineStats {
  return { added: null, modified: null, removed: null };
}

function fileLineStats(entry: FileEntry): LineStats {
  if (entry.summary) {
    return {
      added: entry.summary.added_lines,
      modified: entry.summary.modified_lines,
      removed: entry.summary.removed_lines,
    };
  }
  if (
    entry.lazy &&
    typeof entry.added_lines === "number" &&
    typeof entry.removed_lines === "number"
  ) {
    return {
      added: entry.added_lines,
      modified: 0,
      removed: entry.removed_lines,
    };
  }
  return unknownLineStats();
}

function formatLineStat(value: number | null): string {
  return value === null ? "?" : String(value);
}

function fileEntryIsHydrated(entry: FileEntry): boolean {
  return entry.render_kind === "notebook" || entry.rows !== undefined;
}

function addHydratedNotebookSummary(
  current: Summary,
  entry: FileEntry,
): Summary {
  const entrySummary = entry.summary;
  if (!entrySummary || !("changed_cells" in entrySummary)) {
    return current;
  }
  const notebookSummary = entrySummary as NotebookSummary;
  return {
    ...current,
    changed_cells: (current.changed_cells ?? 0) + notebookSummary.changed_cells,
    added_cells: (current.added_cells ?? 0) + notebookSummary.added_cells,
    modified_cells:
      (current.modified_cells ?? 0) + notebookSummary.modified_cells,
    removed_cells: (current.removed_cells ?? 0) + notebookSummary.removed_cells,
  };
}

function groupLineStats(group: FileGroup): LineStats {
  return group.files.reduce(
    (total, file) => addLineStats(total, fileLineStats(file)),
    emptyLineStats(),
  );
}

function VisibilityIndicator(props: {
  size: "small" | "large";
  visible: boolean;
  virtualized?: boolean;
}) {
  return (
    <span
      class="visibility-indicator"
      classList={{
        large: props.size === "large",
        small: props.size === "small",
        visible: props.visible,
        virtualized: props.virtualized ?? false,
      }}
      aria-hidden="true"
    >
      {props.virtualized ? "V" : ""}
    </span>
  );
}

function TreeLineStats(props: { stats: LineStats }) {
  return (
    <span class="file-tree-line-stats">
      <span class="added">+ {formatLineStat(props.stats.added)}</span>
      <span class="changed">~ {formatLineStat(props.stats.modified)}</span>
      <span class="removed">- {formatLineStat(props.stats.removed)}</span>
    </span>
  );
}

function fileKindStatus(fileKind: FileKind): string {
  return fileKind.type === "git" ? fileKind.status : "untracked";
}

function fileKindLabel(fileKind: FileKind): string {
  return fileKindStatus(fileKind);
}

function fileKindKey(fileKind: FileKind): string {
  if (fileKind.type === "untracked") {
    return "untracked";
  }
  return `git:${fileKind.status}`;
}

function isNotebookSummary(
  summary: FileEntry["summary"],
): summary is NotebookSummary {
  return Boolean(summary && "changed_cells" in summary);
}

function notebookSectionSummary(
  label: string,
  details: { renderMode?: "plain" | null; truncatedRows?: number | null },
): string {
  const parts = [label];
  if (details.renderMode === "plain") {
    parts.push("plain render");
  }
  if (details.truncatedRows) {
    parts.push(`truncated ${details.truncatedRows}`);
  }
  return parts.join(" · ");
}

function notebookCellKindBadgeClass(kind: NotebookCellEntry["kind"]): string {
  if (kind === "added") {
    return "badge-added";
  }
  if (kind === "removed") {
    return "badge-removed";
  }
  return "badge-modified";
}

function splitRemoteQualifiedRef(
  ref: string,
  remoteNames: string[],
): { remote: string; value: string } {
  const normalizedRef = (ref || "").trim();
  for (const remoteName of [...remoteNames].sort(
    (left, right) => right.length - left.length,
  )) {
    const prefix = `${remoteName}/`;
    if (normalizedRef.startsWith(prefix)) {
      return {
        remote: remoteName,
        value: normalizedRef.slice(prefix.length),
      };
    }
  }
  return {
    remote: "",
    value: normalizedRef,
  };
}

function qualifyRemoteRef(
  remote: string,
  ref: string,
  remoteNames: string[],
): string {
  const normalizedRemote = (remote || "").trim();
  const normalizedRef = (ref || "").trim();
  if (!normalizedRemote || !normalizedRef) {
    return normalizedRef;
  }
  if (
    normalizedRef.startsWith("refs/") ||
    builtinSides.has(normalizedRef) ||
    /^[0-9a-f]{7,40}$/i.test(normalizedRef) ||
    normalizedRef.includes(":") ||
    normalizedRef.includes("^") ||
    normalizedRef.includes("~") ||
    remoteNames.some(
      (name) => normalizedRef === name || normalizedRef.startsWith(`${name}/`),
    )
  ) {
    return normalizedRef;
  }
  return `${normalizedRemote}/${normalizedRef}`;
}

function branchReviewRef(
  source: BranchSource,
  remote: string,
  branch: string,
  remoteNames: string[],
): string {
  if (source === "local") {
    return branch.trim();
  }
  return qualifyRemoteRef(remote, branch, remoteNames);
}

function filterRefChoices(
  refChoices: RefChoices,
  query: string,
  sections: (keyof RefChoices)[],
): AutocompleteGroup[] {
  const needle = query.trim().toLowerCase();
  const filtered: AutocompleteGroup[] = [];
  for (const section of sections) {
    const values = (refChoices[section] || []).filter((value) => {
      if (!needle) {
        return true;
      }
      return value.toLowerCase().includes(needle);
    });
    if (values.length) {
      filtered.push([section, values]);
    }
  }
  return filtered;
}

function filterBranchChoices(
  refChoices: RefChoices,
  source: BranchSource,
  remoteName: string,
  query: string,
): AutocompleteGroup[] {
  if (source === "local") {
    return filterRefChoices(refChoices, query, ["locals"]);
  }
  const values = filterValues(
    listRemoteBranchChoices(refChoices, remoteName),
    query,
  );
  return values.length ? [["remote_branches", values]] : [];
}

function autocompleteOptionDescription(section: string, value: string): string {
  if (section !== "builtins") {
    return "";
  }
  return builtinRefDescriptions[value] || "";
}

function listRemoteBranchChoices(
  refChoices: RefChoices,
  remoteName: string,
): string[] {
  const normalizedRemote = remoteName.trim();
  if (!normalizedRemote) {
    return [];
  }
  const prefix = `${normalizedRemote}/`;
  return [
    ...new Set(
      (refChoices.remotes || [])
        .filter((value) => value.startsWith(prefix))
        .map((value) => value.slice(prefix.length))
        .filter(Boolean),
    ),
  ].sort();
}

function filterValues(values: string[], query: string): string[] {
  const needle = query.trim().toLowerCase();
  return values.filter((value) => {
    if (!needle) {
      return true;
    }
    return value.toLowerCase().includes(needle);
  });
}

function entryDirectoryLabel(entry: FileEntry): string {
  return entryDirectoryPath(entry) || "root files";
}

function fileKey(entry: FileEntry): string {
  const leftPath = entry.left_path || "";
  const rightPath = entry.right_path || "";
  const displayName = leftPath || rightPath ? "" : entry.display_name || "";
  return `${leftPath}\u0000${rightPath}\u0000${displayName}\u0000${fileKindKey(entry.file_kind)}`;
}

function sortFilesByOrder(
  files: FileEntry[],
  order: Record<string, number>,
): FileEntry[] {
  return [...files].sort(
    (leftFile, rightFile) =>
      (order[fileKey(leftFile)] ?? 0) - (order[fileKey(rightFile)] ?? 0),
  );
}

function fileElementId(key: string): string {
  return hashedElementId("file", key);
}

function fileBodyAnchorElementId(key: string): string {
  return hashedElementId("file-body", key);
}

function directoryElementId(label: string): string {
  return hashedElementId("directory", label);
}

function hashedElementId(prefix: string, value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index);
  }
  return `${prefix}-${(hash >>> 0).toString(36)}`;
}

function fileDiffQueryKey(request: DiffRequest, entry: FileEntry) {
  return [
    "file-diff",
    request.engine,
    request.mode,
    request.left,
    request.right,
    request.base_branch,
    request.review_branch,
    request.show_untracked,
    entry.left_path,
    entry.right_path,
    entry.display_name,
    fileKindKey(entry.file_kind),
  ] as const;
}

function notebookSectionQueryKey(
  request: DiffRequest,
  entry: FileEntry,
  section: string,
  cellKey: string | null,
) {
  return [
    "notebook-section",
    request.engine,
    request.mode,
    request.left,
    request.right,
    request.base_branch,
    request.review_branch,
    request.show_untracked,
    entry.left_path,
    entry.right_path,
    section,
    cellKey,
  ] as const;
}

function groupFilesByLabel(files: FileEntry[]): Map<string, FileGroup> {
  const groups = new Map<string, FileEntry[]>();
  for (const file of files) {
    const label = entryDirectoryLabel(file);
    const groupFiles = groups.get(label);
    if (groupFiles) {
      groupFiles.push(file);
    } else {
      groups.set(label, [file]);
    }
  }
  return new Map(
    [...groups].map(([label, groupFiles]) => [
      label,
      { label, files: groupFiles },
    ]),
  );
}

function getLinePinFromHash(): LinePin | null {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const rawPin = params.get(linePinHashKey);
  if (!rawPin) {
    return null;
  }
  try {
    const pin = JSON.parse(rawPin) as Partial<LinePin>;
    if (
      pin &&
      typeof pin.file === "string" &&
      (pin.side === "left" || pin.side === "right") &&
      typeof pin.line === "string" &&
      pin.line
    ) {
      return {
        file: pin.file,
        side: pin.side,
        line: pin.line,
      };
    }
  } catch {
    return null;
  }
  return null;
}

function setLinePinInHash(pin: LinePin) {
  const params = new URLSearchParams(window.location.hash.slice(1));
  params.set(linePinHashKey, JSON.stringify(pin));
  history.replaceState(
    {},
    "",
    `${window.location.pathname}${window.location.search}#${params.toString()}`,
  );
}

function clearLinePinInHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  params.delete(linePinHashKey);
  const hash = params.toString();
  history.replaceState(
    {},
    "",
    `${window.location.pathname}${window.location.search}${hash ? `#${hash}` : ""}`,
  );
}

function linePinFromElement(lineNo: HTMLElement): LinePin | null {
  const file = lineNo.dataset.linePinFile;
  const side = lineNo.dataset.linePinSide;
  const line = lineNo.dataset.linePinLine;
  if (!file || (side !== "left" && side !== "right") || !line) {
    return null;
  }
  return { file, side, line };
}

function fileMatchesLinePin(file: FileEntry, pin: LinePin): boolean {
  return fileDisplayName(file) === pin.file;
}

function findPinnedLine(root: ParentNode, pin: LinePin): HTMLElement | null {
  for (const lineNo of root.querySelectorAll<HTMLElement>(
    ".line-no[data-line-pin-line]",
  )) {
    if (
      lineNo.dataset.linePinFile === pin.file &&
      lineNo.dataset.linePinSide === pin.side &&
      lineNo.dataset.linePinLine === pin.line
    ) {
      return lineNo;
    }
  }
  return null;
}

function highlightPinnedLine(root: ParentNode, row: HTMLElement | null) {
  for (const node of root.querySelectorAll(".pinned-line")) {
    node.classList.remove("pinned-line");
  }
  row?.classList.add("pinned-line");
}

function restorePinnedLine(
  root: ParentNode,
  restoredLinePinKey: string,
  setRestoredLinePinKey: (pinKey: string) => void,
) {
  const pin = getLinePinFromHash();
  if (!pin) {
    highlightPinnedLine(root, null);
    setRestoredLinePinKey("");
    return;
  }
  const pinKey = JSON.stringify(pin);
  const lineNo = findPinnedLine(root, pin);
  if (!lineNo) {
    highlightPinnedLine(root, null);
    return;
  }
  const row = lineNo.closest<HTMLElement>(".diff-row");
  if (restoredLinePinKey === pinKey && row?.classList.contains("pinned-line")) {
    return;
  }
  setRestoredLinePinKey(pinKey);
  highlightPinnedLine(root, row);
  row?.scrollIntoView({ block: "center", behavior: "instant" });
}

function shouldIgnoreHunkNavKeyEvent(event: KeyboardEvent): boolean {
  if (
    event.defaultPrevented ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey
  ) {
    return true;
  }
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return (
    target.isContentEditable ||
    Boolean(target.closest("input, textarea, select, [contenteditable='true']"))
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
