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
import {
  type Defaults,
  type DiffEngine,
  type DiffMode,
  type DiffRequest,
  type FileEntry,
  type NotebookCellEntry,
  type NotebookSection,
  type NotebookSummary,
  type RefChoices,
  type Summary,
  fetchDefaults,
  fetchFileDiff,
  fetchNotebookSection,
  openDiffStream,
} from "./api";
import { DiffGrid } from "./DiffGrid";
import "./styles.css";

type LoadState = "idle" | "loading" | "done" | "error";
type ControlsState = {
  mode: DiffMode;
  left: string;
  right: string;
  baseRemote: string;
  baseBranch: string;
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
const AUTO_COLLAPSE_RENDERED_ROW_LIMIT = 500;
const AUTO_COLLAPSE_FILE_COUNT_LIMIT = 50;
const engineLabels: Record<DiffEngine, string> = {
  dirdiff: "Dirdiff",
  git: "Git",
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

function legacyUrl(request: DiffRequest | null): string {
  const backendOrigin =
    import.meta.env.VITE_DIRDIFF_BACKEND_ORIGIN ?? window.location.origin;
  const query = request
    ? `?${requestQuery(request).toString()}`
    : window.location.search;
  return `${backendOrigin}/${query}`;
}

function inferMode(
  left: string,
  right: string,
  baseBranch: string,
  reviewBranch: string,
): DiffMode {
  if (baseBranch || reviewBranch) {
    return "branch-review";
  }
  if (left === "index" && right === "worktree") {
    return "files";
  }
  if (left === "head" && right === "index") {
    return "staged";
  }
  if (left === "head" && right === "worktree") {
    return "against-head";
  }
  return "refs";
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
  const mode =
    (search.get("mode") as DiffMode | null) ||
    defaults.mode ||
    inferMode(left, right, reviewBranchParts.value, baseBranchParts.value);

  if (mode in modeSides) {
    const [modeLeft, modeRight] = modeSides[mode as keyof typeof modeSides];
    return {
      mode,
      left: modeLeft,
      right: modeRight,
      baseRemote: baseBranchParts.remote,
      baseBranch: baseBranchParts.value,
      branchRemote: reviewBranchParts.remote,
      reviewBranch: reviewBranchParts.value,
    };
  }

  return {
    mode,
    left,
    right,
    baseRemote: baseBranchParts.remote,
    baseBranch: baseBranchParts.value,
    branchRemote: reviewBranchParts.remote,
    reviewBranch: reviewBranchParts.value,
  };
}

function initialEngine(defaults: Defaults): DiffEngine {
  const engine = new URLSearchParams(window.location.search).get("engine");
  if (engine === "git" || engine === "dirdiff") {
    return engine;
  }
  return defaults.engine || "dirdiff";
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
    };
  }

  if (controls.mode === "branch-review") {
    if (!(refChoices.remote_names || []).length) {
      return "Branch review needs at least one remote.";
    }
    if (!controls.baseRemote.trim()) {
      return "Pick a base remote.";
    }
    if (!controls.baseBranch.trim()) {
      return "Pick a base branch.";
    }
    if (!controls.branchRemote.trim()) {
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
      base_branch: qualifyRemoteRef(
        controls.baseRemote,
        controls.baseBranch,
        refChoices.remote_names,
      ),
      review_branch: qualifyRemoteRef(
        controls.branchRemote,
        controls.reviewBranch,
        refChoices.remote_names,
      ),
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

function App() {
  const defaults = createQuery(() => ({
    queryKey: ["defaults"],
    queryFn: fetchDefaults,
    staleTime: Infinity,
  }));
  const [engine, setEngine] = createSignal<DiffEngine>("dirdiff");
  const [controls, setControls] = createSignal<ControlsState | null>(null);
  const [request, setRequest] = createSignal<DiffRequest | null>(null);
  const [files, setFiles] = createSignal<FileEntry[]>([]);
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
  const [status, setStatus] = createSignal<LoadState>("idle");
  const [statusText, setStatusText] = createSignal("Preparing diff...");
  const [debugMenuOpen, setDebugMenuOpen] = createSignal(false);
  const [helpOpen, setHelpOpen] = createSignal(false);
  const [fileTreeOpen, setFileTreeOpen] = createSignal(false);
  const [currentHunkIndex, setCurrentHunkIndex] = createSignal(0);
  const [hunkNavigationTick, setHunkNavigationTick] = createSignal(0);
  let appRoot: HTMLElement | undefined;
  let stream: EventSource | undefined;
  let initialized = false;
  let hunkReconcileTimer = 0;
  let restoredLinePinKey = "";

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
      setDirectoryExpansion({});
      setFileExpansion({});
      setLoadingFiles({});
      setFileErrors({});
      setSummary(emptySummary);
      setStatus(nextStatus);
      setStatusText(nextStatusText);
      setCurrentHunkIndex(0);
      setHunkNavigationTick((tick) => tick + 1);
    });
  };

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

    stream?.close();
    resetDiffState("loading", "Loading diff...");
    history.replaceState(
      {},
      "",
      `/?${requestQuery(activeRequest).toString()}${window.location.hash}`,
    );

    stream = openDiffStream(
      activeRequest,
      (event) => {
        if (event.type === "init") {
          batch(() => {
            setSummary(event.payload.summary);
            setStatusText(
              `${statusLabel(activeRequest, event.payload.left_label, event.payload.right_label)} · streaming...`,
            );
          });
          return;
        }
        if (event.type === "file") {
          const key = fileKey(event.entry);
          const directory = entryDirectoryLabel(event.entry);
          const nextFiles = [...files(), event.entry];
          batch(() => {
            setFiles(() => nextFiles);
            setDirectoryExpansion((current) => ({
              ...current,
              [directory]: directoryExpansionValue(current, directory),
            }));
            setFileExpansion((current) =>
              nextFileExpansion(current, nextFiles, event.entry, key),
            );
            setSummary(event.summary);
            setStatusText(
              `${statusLabel(activeRequest)} · loaded ${event.summary.changed_files} files...`,
            );
          });
          return;
        }
        if (event.type === "done") {
          stream?.close();
          batch(() => {
            setSummary(event.summary);
            setStatus("done");
            setStatusText(statusLabel(activeRequest));
          });
          return;
        }
        stream?.close();
        batch(() => {
          setStatus("error");
          setStatusText(event.error);
        });
      },
      () => {
        if (status() === "done") {
          return;
        }
        stream?.close();
        batch(() => {
          setStatus("error");
          setStatusText("Diff stream failed.");
        });
      },
    );
  });

  onCleanup(() => stream?.close());
  onCleanup(() => clearTimeout(hunkReconcileTimer));

  const onKeyDown = (event: KeyboardEvent) => {
    if (shouldIgnoreHunkNavKeyEvent(event)) {
      return;
    }
    if (event.code === "KeyN" && !event.shiftKey) {
      event.preventDefault();
      scrollHunk(1);
      return;
    }
    if (event.code === "KeyN" && event.shiftKey) {
      event.preventDefault();
      scrollHunk(-1);
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
    if (side) {
      document.body.dataset.diffSelectionSide = side;
      return;
    }
    delete document.body.dataset.diffSelectionSide;
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
    setDiffSelectionSide(
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
      highlightPinnedLine(appRoot, null);
      return;
    }
    restoredLinePinKey = pinKey;
    setLinePinInHash(pin);
    highlightPinnedLine(appRoot, row);
  };

  const onHashChange = () => {
    if (!appRoot) {
      return;
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
      stream?.close();
      resetDiffState("error", nextRequest);
      return;
    }
    setRequest(nextRequest);
  };

  const loadEngine = (nextEngine: DiffEngine) => {
    setEngine(nextEngine);
    const currentControls = controls();
    if (currentControls) {
      loadControls(currentControls, nextEngine);
    }
  };

  createEffect(() => {
    hunkNavigationTick();
    files();
    directoryExpansion();
    fileExpansion();
    loadingFiles();
    clearTimeout(hunkReconcileTimer);
    hunkReconcileTimer = window.setTimeout(() => {
      const anchors = hunkAnchors(appRoot);
      if (!anchors.length) {
        setCurrentHunkIndex(0);
        if (appRoot) {
          restorePinnedLine(appRoot, restoredLinePinKey, (pinKey) => {
            restoredLinePinKey = pinKey;
          });
        }
        return;
      }
      setCurrentHunkIndex((index) => clamp(index, 0, anchors.length - 1));
      selectCurrentHunk(currentHunkIndex(), false, appRoot);
      if (appRoot) {
        restorePinnedLine(appRoot, restoredLinePinKey, (pinKey) => {
          restoredLinePinKey = pinKey;
        });
      }
    }, 120);
  });

  const scrollHunk = (direction: 1 | -1) => {
    const anchors = hunkAnchors(appRoot);
    if (!anchors.length) {
      console.error(
        "[dirdiff] Hunk navigation requested with no mounted hunk anchors.",
      );
      throw new Error(
        "Hunk navigation requested with no mounted hunk anchors.",
      );
    }
    const nextIndex = wrapIndex(currentHunkIndex() + direction, anchors.length);
    setCurrentHunkIndex(nextIndex);
    selectCurrentHunk(nextIndex, true, appRoot);
  };

  const scrollTop = () => {
    window.scrollTo({ top: 0, behavior: "instant" });
  };

  const scrollToFile = (file: FileEntry) => {
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
    requestAnimationFrame(() => {
      const target = document.getElementById(fileElementId(key));
      if (!target) {
        throw new Error(
          `Could not find file card for ${fileDisplayName(file)}.`,
        );
      }
      const header = target.querySelector<HTMLElement>(".file-card-header");
      if (!header) {
        throw new Error(
          `Could not find file header for ${fileDisplayName(file)}.`,
        );
      }
      header.scrollIntoView({ block: "start", behavior: "instant" });
      target.classList.remove("file-card-flash");
      void target.offsetWidth;
      target.classList.add("file-card-flash");
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
      <header class="app-header">
        <div class="app-title-block">
          <p class="eyebrow">Solid Frontend</p>
          <div class="app-title-row">
            <h1>dirdiff</h1>
            <div class="header-actions">
              <EngineSelect engine={engine()} onEngineChange={loadEngine} />
              <a class="legacy-link" href={legacyUrl(request())}>
                Legacy
              </a>
            </div>
          </div>
          <p class="subtitle">
            The new frontend is now owning app state and API IO.
          </p>
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
              files={files()}
              request={request()}
              directoryExpansion={directoryExpansion()}
              fileExpansion={fileExpansion()}
              loadingFiles={loadingFiles()}
              fileErrors={fileErrors()}
              setDirectoryExpansion={setDirectoryExpansion}
              setFileExpansion={setFileExpansion}
              setLoadingFiles={setLoadingFiles}
              setFileErrors={setFileErrors}
              setFiles={setFiles}
            />
            <FileTreeSidebar
              files={files()}
              directoryExpansion={directoryExpansion()}
              fileExpansion={fileExpansion()}
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
              onNext={() => scrollHunk(1)}
              onPrev={() => scrollHunk(-1)}
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
          if (nextEngine === "dirdiff" || nextEngine === "git") {
            props.onEngineChange(nextEngine);
          }
        }}
      >
        <option value="dirdiff">{engineLabels.dirdiff}</option>
        <option value="git">{engineLabels.git}</option>
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
          </HotkeyHelpSection>
          <HotkeyHelpSection title="Misc">
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
        <For each={Object.entries(modeLabels) as [DiffMode, string][]}>
          {([mode, label]) => (
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
              {label}
            </button>
          )}
        </For>
      </fieldset>

      <Show when={draft().mode === "refs"}>
        <AutocompleteField
          label="Left ref"
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
          label="Right ref"
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
        <AutocompleteField
          label="Base remote"
          value={draft().baseRemote}
          groups={(query) =>
            filterRefChoices(props.refChoices, query, ["remote_names"])
          }
          onValue={(baseRemote) => updateDraft({ baseRemote })}
        />
        <AutocompleteField
          label="Base branch"
          value={draft().baseBranch}
          groups={(query) => {
            const values = filterValues(
              listRemoteBranchChoices(props.refChoices, draft().baseRemote),
              query,
            );
            return values.length ? [["remote_branches", values]] : [];
          }}
          onValue={(baseBranch) => updateDraft({ baseBranch })}
        />
        <AutocompleteField
          label="Branch remote"
          value={draft().branchRemote}
          groups={(query) =>
            filterRefChoices(props.refChoices, query, ["remote_names"])
          }
          onValue={(branchRemote) => updateDraft({ branchRemote })}
        />
        <AutocompleteField
          label="Branch to review"
          value={draft().reviewBranch}
          groups={(query) => {
            const values = filterValues(
              listRemoteBranchChoices(props.refChoices, draft().branchRemote),
              query,
            );
            return values.length ? [["remote_branches", values]] : [];
          }}
          onValue={(reviewBranch) => updateDraft({ reviewBranch })}
        />
      </Show>

      <button class="load-button" type="submit">
        Load
      </button>
    </form>
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
  const [blurTimer, setBlurTimer] = createSignal<number | undefined>();
  const groups = createMemo(() => (focused() ? props.groups(props.value) : []));

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

  return (
    <label class="field autocomplete-host">
      <span>{props.label}</span>
      <input
        ref={input}
        value={props.value}
        spellcheck={false}
        autocomplete="off"
        onClick={() => setFocused(true)}
        onPointerDown={() => setFocused(true)}
        onInput={(event) => {
          props.onValue(event.currentTarget.value);
          setFocused(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setFocused(false);
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
                  {(value) => (
                    <button
                      type="button"
                      class="autocomplete-option"
                      onMouseDown={(event) => {
                        event.preventDefault();
                        props.onValue(value);
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
  return (
    <div class="summary-group">
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
  files: FileEntry[],
  newFile: FileEntry,
  newFileKey: string,
): Record<string, boolean> {
  if (files.length > AUTO_COLLAPSE_FILE_COUNT_LIMIT) {
    return Object.fromEntries(files.map((file) => [fileKey(file), false]));
  }
  if (Object.hasOwn(current, newFileKey)) {
    return current;
  }
  return {
    ...current,
    [newFileKey]: shouldExpandFileByDefault(newFile),
  };
}

function shouldExpandFileByDefault(file: FileEntry): boolean {
  if (file.lazy) {
    return false;
  }
  return renderedLineCount(file) <= AUTO_COLLAPSE_RENDERED_ROW_LIMIT;
}

function renderedLineCount(file: FileEntry): number {
  if (file.render_kind === "notebook") {
    if (!file.cells) {
      throw new Error(
        `Notebook file is missing cells for ${fileDisplayName(file)}.`,
      );
    }
    if (!file.notebook_metadata_rows) {
      throw new Error(
        `Notebook file is missing metadata rows for ${fileDisplayName(file)}.`,
      );
    }
    return file.cells.reduce(
      (total, cell) =>
        total +
        cell.source_rows.length +
        cell.metadata_rows.length +
        cell.outputs_rows.length,
      file.notebook_metadata_rows.length,
    );
  }
  if (!file.rows) {
    throw new Error(`File is missing rows for ${fileDisplayName(file)}.`);
  }
  return file.rows.length;
}

function FileList(props: {
  files: FileEntry[];
  request: DiffRequest | null;
  directoryExpansion: Record<string, boolean>;
  fileExpansion: Record<string, boolean>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
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

  const setAllExpanded = (expanded: boolean) => {
    props.setDirectoryExpansion(() =>
      Object.fromEntries(groupLabels().map((label) => [label, expanded])),
    );
    props.setFileExpansion(() =>
      Object.fromEntries(props.files.map((file) => [fileKey(file), expanded])),
    );
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
          <button type="button" onClick={() => setAllExpanded(false)}>
            Fold all
          </button>
          <button type="button" onClick={() => setAllExpanded(true)}>
            Show all
          </button>
        </div>
        <div class="directory-groups">
          <For each={groupLabels()}>
            {(label) => (
              <DirectoryGroup
                group={() => groupForLabel(label)}
                request={props.request}
                expanded={props.directoryExpansion[label] ?? true}
                fileExpansion={props.fileExpansion}
                loadingFiles={props.loadingFiles}
                fileErrors={props.fileErrors}
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
  expanded: boolean;
  fileExpansion: Record<string, boolean>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  setExpanded: (expanded: boolean) => void;
  setFileExpanded: (key: string, expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
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
          <span class="directory-collapse-indicator" aria-hidden="true">
            {props.expanded ? "▾" : "▸"}
          </span>
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
                  expanded={props.fileExpansion[key] ?? !file.lazy}
                  loading={props.loadingFiles[key] ?? false}
                  error={props.fileErrors[key] ?? ""}
                  setExpanded={(expanded) =>
                    props.setFileExpanded(key, expanded)
                  }
                  setLoadingFiles={props.setLoadingFiles}
                  setFileErrors={props.setFileErrors}
                  setFiles={props.setFiles}
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
    expansionValue(props.fileExpansion, fileKey(file), !file.lazy);

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
                        <span
                          class="file-tree-visibility-box"
                          classList={{ visible: directoryExpanded(group) }}
                          aria-hidden="true"
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
                      {(file) => (
                        <div
                          class="file-tree-file"
                          classList={{
                            added: file.change_type === "add",
                            removed: file.change_type === "delete",
                            lazy: Boolean(file.lazy),
                          }}
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
                            <span
                              class="file-tree-visibility-box"
                              classList={{ visible: fileExpanded(file) }}
                              aria-hidden="true"
                            />
                          </button>
                          <button
                            type="button"
                            class="file-tree-file-target"
                            onClick={() => props.onScrollToFile(file)}
                          >
                            {fileBasename(file)}
                          </button>
                          <TreeLineStats stats={fileLineStats(file)} />
                        </div>
                      )}
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
  expanded: boolean;
  loading: boolean;
  error: string;
  setExpanded: (expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
}) {
  const queryClient = useQueryClient();
  const key = () => fileKey(props.file);
  const summary = () =>
    props.file.summary ?? {
      added_lines: 0,
      modified_lines: 0,
      removed_lines: 0,
      changed_lines: 0,
      left_exists: Boolean(props.file.left_path),
      right_exists: Boolean(props.file.right_path),
    };
  const displayName = () => fileDisplayName(props.file);
  const lazyTitle = () => {
    if (props.file.change_type === "delete") {
      return "Load deleted file diff";
    }
    return isGeneratedLazyEntry(props.file)
      ? "Load generated diff"
      : "Load diff";
  };
  const lazyMeta = () =>
    props.file.lazy_reason ||
    `${displayName()} is folded by default. Click to fetch and open it.`;
  const canRenderRows = () =>
    !props.file.lazy &&
    props.file.render_kind !== "notebook" &&
    (props.file.rows?.length ?? 0) > 0;

  const expand = async () => {
    props.setExpanded(true);
    if (!props.file.lazy || !props.request || props.loading) {
      return;
    }
    props.setLoadingFiles((current) => ({
      ...current,
      [key()]: true,
    }));
    props.setFileErrors((current) => ({ ...current, [key()]: "" }));
    try {
      const hydrated = await queryClient.fetchQuery({
        queryKey: fileDiffQueryKey(props.request, props.file),
        queryFn: () => fetchFileDiff(props.request!, props.file),
        staleTime: Infinity,
      });
      const nextEntry = { ...props.file, ...hydrated, lazy: false };
      props.setFiles((current) =>
        current.map((entry) => (fileKey(entry) === key() ? nextEntry : entry)),
      );
    } catch (error) {
      props.setFileErrors((current) => ({
        ...current,
        [key()]:
          error instanceof Error ? error.message : "Failed to load file diff.",
      }));
    } finally {
      props.setLoadingFiles((current) => ({
        ...current,
        [key()]: false,
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
          <span class="file-collapse-indicator" aria-hidden="true">
            {props.expanded ? "▾" : "▸"}
          </span>
          <span>
            <h2>{displayName()}</h2>
            <p>
              {props.file.lazy
                ? isGeneratedLazyEntry(props.file)
                  ? "generated"
                  : "loads on expand"
                : (props.file.change_type ?? "modify")}
            </p>
          </span>
        </span>
        <span class="file-stats">
          <span class="delta added">+ {summary().added_lines}</span>
          <span class="delta changed">~ {summary().modified_lines}</span>
          <span class="delta removed">- {summary().removed_lines}</span>
        </span>
      </button>
      <Show when={props.expanded}>
        <div class="file-card-body">
          <Show when={props.loading}>
            <p class="file-placeholder">Loading file diff...</p>
          </Show>
          <Show when={props.error}>
            <p class="file-placeholder error-text">{props.error}</p>
          </Show>
          <Show when={!props.loading && !props.error}>
            <Show when={props.file.render_kind === "notebook"}>
              <NotebookFile file={props.file} request={props.request} />
            </Show>
            <Show when={props.file.render_kind !== "notebook"}>
              <Show
                when={canRenderRows()}
                fallback={<FilePlaceholder file={props.file} />}
              >
                <DiffGrid file={props.file} />
              </Show>
            </Show>
          </Show>
        </div>
      </Show>
      <Show when={!props.expanded && props.file.lazy}>
        <button type="button" class="file-lazy-load-toggle" onClick={expand}>
          <span class="file-lazy-load-toggle-title">{lazyTitle()}</span>
          <span class="file-lazy-load-toggle-meta">{lazyMeta()}</span>
        </button>
      </Show>
    </article>
  );
}

function FilePlaceholder(props: { file: FileEntry }) {
  if (props.file.lazy) {
    return (
      <p class="file-placeholder">
        {props.file.lazy_reason || "Lazy file loading is not ported yet."}
      </p>
    );
  }
  return <p class="file-placeholder">No rows for this file.</p>;
}

function NotebookFile(props: { file: FileEntry; request: DiffRequest | null }) {
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
        staleTime: Infinity,
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
}) {
  const file = (): FileEntry => ({
    change_type: "modify",
    left_path: null,
    right_path: null,
    left_label: props.leftLabel,
    right_label: props.rightLabel,
    rows: props.rows ?? [],
    fold_hints: props.foldHints ?? [],
  });

  return (
    <section class="notebook-section">
      <Show when={props.heading}>
        <p class="notebook-section-heading">{props.heading}</p>
      </Show>
      <DiffGrid file={file()} />
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
  if (!entry.display_name) {
    throw new Error("File entry is missing display_name.");
  }
  return entry.display_name;
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
  throw new Error(`File entry is missing paths for ${fileDisplayName(entry)}.`);
}

type LineStats = {
  added: number;
  modified: number;
  removed: number;
};

function emptyLineStats(): LineStats {
  return { added: 0, modified: 0, removed: 0 };
}

function addLineStats(left: LineStats, right: LineStats): LineStats {
  return {
    added: left.added + right.added,
    modified: left.modified + right.modified,
    removed: left.removed + right.removed,
  };
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
  throw new Error(
    `File entry is missing line stats for ${fileDisplayName(entry)}.`,
  );
}

function groupLineStats(group: FileGroup): LineStats {
  return group.files.reduce(
    (total, file) => addLineStats(total, fileLineStats(file)),
    emptyLineStats(),
  );
}

function TreeLineStats(props: { stats: LineStats }) {
  return (
    <span class="file-tree-line-stats">
      <span class="added">+ {props.stats.added}</span>
      <span class="changed">~ {props.stats.modified}</span>
      <span class="removed">- {props.stats.removed}</span>
    </span>
  );
}

function isGeneratedLazyEntry(entry: FileEntry): boolean {
  const path = String(entry.right_path || entry.left_path || "")
    .trim()
    .toLowerCase();
  return [
    "cargo.lock",
    "composer.lock",
    "flake.lock",
    "go.sum",
    "package-lock.json",
    "pdm.lock",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
  ].some((name) => path.endsWith(`/${name}`) || path === name);
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
  return `${entry.left_path || ""}\u0000${entry.right_path || ""}\u0000${entry.display_name || ""}\u0000${entry.change_type || ""}`;
}

function fileElementId(key: string): string {
  return hashedElementId("file", key);
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
    entry.left_path,
    entry.right_path,
    entry.display_name,
    entry.change_type,
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

function hunkAnchors(root: ParentNode = document): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(".hunk-anchor")];
}

function selectCurrentHunk(
  index: number,
  scroll: boolean,
  root: ParentNode = document,
) {
  const anchors = hunkAnchors(root);
  if (!anchors.length) {
    return;
  }
  const selected = anchors[clamp(index, 0, anchors.length - 1)];
  for (const anchor of anchors) {
    anchor.classList.remove("active-hunk");
    anchor.removeAttribute("aria-current");
  }
  selected.classList.add("active-hunk");
  selected.setAttribute("aria-current", "true");
  if (scroll) {
    selected.scrollIntoView({
      block: "center",
      behavior: "instant",
    });
  }
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

function wrapIndex(index: number, length: number): number {
  return ((index % length) + length) % length;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
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
