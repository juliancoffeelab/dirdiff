/**
 * Implements one selected ChangeSet's backend observation and presentation.
 *
 * The module exports ChangeSet. The lightweight outer ChangeSet stores file
 * expansion, local Help state, and local History visibility while receiving
 * workspace-wide FileTree and DebugHud visibility. Each mounted ChangeSetShell
 * stores its HunkDisplay signal.
 * Active ChangeSetContent observes the manifest, while ChangeSetSnapshot owns
 * the profile-preference observer, resolves the URL line pin, and creates its
 * one file lane (`fileLane.ts`), which owns the lazy-info and file queries and
 * every canonical file state. Together they render Navigation, hotkeys, HUD,
 * Portals, title, FileTree, and FileCards. They must not copy backend results
 * into Solid state, start file-diff requests outside the lane, store workspace
 * or Tab selections, or follow user scrolling.
 * Line-pin URL identity and decoration remain in LinePins; the file lane
 * accepts only its resolved target index and restoration gate.
 */
import {
  For,
  Show,
  batch,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  type Accessor,
  type JSX,
} from "solid-js";
import { createStore, type SetStoreFunction } from "solid-js/store";
import { Portal } from "solid-js/web";
import {
  createQueries,
  createMutation,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { CircleAlert, Clock3, LoaderCircle } from "lucide-solid";
import {
  api,
  type DiffEngine,
  type DiffParams,
  type Manifest,
  type ManifestDirectory,
  type ManifestFile,
  type ManifestNode,
  type ManifestSummary,
  type ThreadCodeLocation,
} from "../api/api";
import {
  ErrorPanel,
  RetryButton,
  UnexpectedErrorBoundary,
  useToasts,
} from "../comp/Toasts";
import { assert, expect } from "../utils";
import type { DiffViewMode } from "./App";
import type { AppHeaderOutlets } from "./AppHeader";
import { FileCard, type HunkPosition } from "./FileCard";
import {
  createFileLane,
  fileDisplayName,
  manifestEntryKey,
  type FileLaneActivity,
  type FileLaneLineTarget,
  type FileState,
} from "./fileLane";
import { linePins } from "./linePins";
import { NavigationProvider, useNavigation } from "./navigation";
import type { StoredProfile } from "./Profile";
import { ReviewProvider, type ReviewCodeAnchor } from "./Review";

/**
 * Defines every complete input needed to identify and activate one ChangeSet.
 *
 * `params` is one immutable selected Tab value. `engine` selects file rendering
 * without participating in manifest identity. View, FileTree visibility, and
 * DebugHud visibility are global reactive workspace inputs; `profile` is genuine
 * nullable profile identity; and `active` controls expensive observation. Required
 * callbacks report direct workspace actions. No field represents live control input.
 */
type ChangeSetProps = {
  active: boolean;
  params: DiffParams;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  onToggleView: () => void;
  onFileTreeOpenChange: (open: boolean) => void;
  onDebugHudOpenChange: (open: boolean) => void;
};

/**
 * Contains ChangeSet-local client state that survives inactive Tab periods.
 *
 * Expansion keys are manifest paths. Workspace-wide FileTree and DebugHud
 * visibility, backend files, query state, progress, hunk selection, and renderer
 * rows are deliberately excluded from this store.
 */
type ChangeSetState = {
  fileExpansion: Record<string, boolean | undefined>;
};

/**
 * Describes the complete compact state of one ChangeSet file lane.
 *
 * Processed and total count only automatic non-lazy entries. Failure count is
 * derived from current file presentations so a successful explicit retry clears
 * it without maintaining another error store.
 */
type FileSequenceState =
  | {
      state: "loading";
      processed: number;
      automaticTotal: number;
      failed: number;
      active: FileLaneActivity;
    }
  | {
      state: "ready";
      processed: number;
      automaticTotal: number;
      failed: number;
    };

/**
 * Describes the line statistics FileTree can progressively display.
 *
 * Null means the current file state genuinely lacks that statistic. The tree
 * renders this absence explicitly and never treats it as zero.
 */
type TreeLineStats = {
  added: number | null;
  modified: number | null;
  removed: number | null;
  moved: number | null;
};

/**
 * Mirrors navigation information from the mounted ChangeSet DOM.
 *
 * The snapshot must be exact, but Navigation and selection logic must continue
 * using DOM directly. Consumers format its numeric values and may not mutate it
 * or treat it as an independent source of hunk identity. A terminal renderer
 * failure stops further snapshots rather than publishing an inexact value.
 */
type HunkDisplay = {
  /** Manifest file index used for declarative FileTree highlighting. */
  selectedFileIndex: number | null;
  /** Global selected position and whether more targets can become available. */
  globalSelectedHunk: {
    position: HunkPosition;
    hasMore: boolean;
  };
  /** Per-file selected positions keyed by manifest file index. */
  fileSelectedHunks: ReadonlyMap<number, HunkPosition>;
};

/**
 * Establishes one stable ChangeSet lifetime for a Tab's selected parameters.
 *
 * Callers keep this boundary mounted across Tab switches and global view/engine
 * changes. Only active content observes queries and renders expensive file DOM;
 * file expansion, local Help state, and History visibility survive inactive
 * periods. Workspace-wide FileTree and DebugHud visibility survive switching
 * Tabs or selected ChangeSets.
 */
export function ChangeSet(props: ChangeSetProps): JSX.Element {
  const [helpOpen, setHelpOpen] = createSignal(false);
  const [historyOpen, setHistoryOpen] = createSignal(false);
  const [state, setState] = createStore<ChangeSetState>({
    fileExpansion: {},
  });
  return (
    <Show when={props.active ? props.params : null} keyed>
      {(activeParams) => (
        <UnexpectedErrorBoundary
          title="Could not render ChangeSet"
          retryOnR={true}
        >
          <ChangeSetContent
            params={activeParams}
            engine={props.engine}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            profile={props.profile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
            historyOpen={historyOpen()}
            onHistoryOpenChange={setHistoryOpen}
            helpOpen={helpOpen()}
            onHelpOpenChange={setHelpOpen}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
            state={state}
            setState={setState}
          />
        </UnexpectedErrorBoundary>
      )}
    </Show>
  );
}

/**
 * Defines the complete inputs for one active ChangeSet content lifetime.
 *
 * ChangeSetContent observes the manifest, mounts direct hotkeys and HUD presentation,
 * and performs external async work. It is disposed whenever the Tab hides; durable
 * client and HUD state remain in the outer ChangeSet.
 */
type ChangeSetContentProps = {
  params: DiffParams;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  onToggleView: () => void;
  historyOpen: boolean;
  onHistoryOpenChange: (open: boolean) => void;
  helpOpen: boolean;
  onHelpOpenChange: (open: boolean) => void;
  onFileTreeOpenChange: (open: boolean) => void;
  onDebugHudOpenChange: (open: boolean) => void;
  state: ChangeSetState;
  setState: SetStoreFunction<ChangeSetState>;
};

/**
 * Observes one active manifest and replaces its complete rendered snapshot.
 *
 * The component exists only for one immutable DiffParams value. It mounts no
 * manifest-dependent observer before manifest success, disposes the current
 * snapshot before reload and never retains an old manifest merely because
 * TanStack still exposes its previous data.
 */
function ChangeSetContent(props: ChangeSetContentProps): JSX.Element {
  const [replacingSnapshot, setReplacingSnapshot] = createSignal(false);
  const manifest = createQuery(() => api.changeSet.manifest(props.params));
  const visibleManifest = createMemo(() => {
    if (replacingSnapshot() || manifest.isError) {
      return undefined;
    }
    return manifest.data;
  });
  let stopFileSequence: (() => Promise<void>) | null = null;
  let replacement: Promise<void> | null = null;

  /**
   * Replaces the complete manifest-dependent lifetime through one refetch.
   *
   * Calling the registered stop operation synchronously closes the current lane.
   * The keyed snapshot is then disposed before cancellation settles and before
   * the manifest refetch begins. Concurrent reload actions share this exact
   * operation and cannot start parallel replacement manifests.
   */
  async function reloadSnapshot(): Promise<void> {
    if (replacement !== null) {
      await replacement;
      return;
    }
    const stopPromise =
      stopFileSequence === null ? Promise.resolve() : stopFileSequence();
    setReplacingSnapshot(true);
    props.setState("fileExpansion", {});
    const currentReplacement = (async () => {
      await stopPromise;
      await manifest.refetch({ cancelRefetch: false });
    })();
    replacement = currentReplacement;
    try {
      await currentReplacement;
    } finally {
      if (replacement === currentReplacement) {
        replacement = null;
        setReplacingSnapshot(false);
      }
    }
  }

  return (
    <>
      <Show
        when={visibleManifest()}
        keyed
        fallback={
          <ChangeSetShell
            helpOpen={props.helpOpen}
            debugOpen={props.debugHudOpen}
            onToggleTree={() => props.onFileTreeOpenChange(!props.fileTreeOpen)}
            onToggleView={props.onToggleView}
            onToggleHistory={() =>
              props.onHistoryOpenChange(!props.historyOpen)
            }
            onReload={() => {
              // Reload replaces the snapshot and resets only its file expansion.
              void reloadSnapshot();
            }}
            onToggleHelp={() => props.onHelpOpenChange(!props.helpOpen)}
            onHelpOpenChange={props.onHelpOpenChange}
            onToggleDebug={() =>
              props.onDebugHudOpenChange(!props.debugHudOpen)
            }
          >
            {() => (
              <>
                <Show when={manifest.isPending || replacingSnapshot()}>
                  <p class="status change-set-title">Loading ChangeSet...</p>
                </Show>
                <Show when={!replacingSnapshot() && manifest.error} keyed>
                  {(error) => (
                    <div class="change-set-error">
                      <ErrorPanel
                        title="Failed to load ChangeSet"
                        error={error}
                      >
                        <RetryButton onRetry={reloadSnapshot} />
                      </ErrorPanel>
                    </div>
                  )}
                </Show>
              </>
            )}
          </ChangeSetShell>
        }
      >
        {(snapshot) => (
          <ChangeSetShell
            helpOpen={props.helpOpen}
            debugOpen={props.debugHudOpen}
            onToggleTree={() => props.onFileTreeOpenChange(!props.fileTreeOpen)}
            onToggleView={props.onToggleView}
            onToggleHistory={() =>
              props.onHistoryOpenChange(!props.historyOpen)
            }
            onReload={() => {
              void reloadSnapshot();
            }}
            onToggleHelp={() => props.onHelpOpenChange(!props.helpOpen)}
            onHelpOpenChange={props.onHelpOpenChange}
            onToggleDebug={() =>
              props.onDebugHudOpenChange(!props.debugHudOpen)
            }
          >
            {(hunkDisplay) => (
              <UnexpectedErrorBoundary
                title="Could not render ChangeSet snapshot"
                retryOnR={false}
              >
                <ReviewSnapshotBoundary
                  params={props.params}
                  engine={props.engine}
                  manifest={snapshot}
                  view={props.view}
                  historyOpen={props.historyOpen}
                  onHistoryOpenChange={props.onHistoryOpenChange}
                  fileTreeOpen={props.fileTreeOpen}
                  profile={props.profile}
                  appHeaderOutlets={props.appHeaderOutlets}
                  hunkDisplay={hunkDisplay}
                  state={props.state}
                  setState={props.setState}
                  onFileTreeOpenChange={props.onFileTreeOpenChange}
                  onFileSequenceChange={(stop) => {
                    stopFileSequence = stop;
                  }}
                />
              </UnexpectedErrorBoundary>
            )}
          </ChangeSetShell>
        )}
      </Show>
    </>
  );
}

/**
 * Defines the complete active UI operations surrounding one ChangeSet body.
 *
 * All callbacks are required and invoke their specified operations. Children are
 * rendered inside the same root served by the scoped Navigation instance.
 */
type ChangeSetShellProps = {
  helpOpen: boolean;
  debugOpen: boolean;
  onToggleTree: () => void;
  onToggleView: () => void;
  onToggleHistory: () => void;
  onReload: () => void;
  onToggleHelp: () => void;
  onHelpOpenChange: (open: boolean) => void;
  onToggleDebug: () => void;
  children: (hunkDisplay: Accessor<HunkDisplay | null>) => JSX.Element;
};

/**
 * Binds one visible ChangeSet body, hotkey listener, and HUD to one DOM root.
 *
 * A ready snapshot receives a fresh Provider and therefore one initial hunk
 * selection. Loading and error bodies retain shell operations but contain no
 * targets. The wrapper has no layout box and stores no backend state.
 */
function ChangeSetShell(props: ChangeSetShellProps): JSX.Element {
  let root!: HTMLElement;
  const [hunkDisplay, setHunkDisplay] = createSignal<HunkDisplay | null>(null);

  onMount(() => {
    /**
     * Restricts native browser text selection to the diff side under the pointer.
     *
     * The mounted ChangeSet root is the complete interaction scope. Every
     * pointer press clears its previous grid marker; a press on a left or right
     * DiffGrid side then marks only that grid. CSS suppresses selection on the
     * opposite side without introducing Solid state or changing hunk selection.
     */
    function selectDiffSide(event: PointerEvent): void {
      root
        .querySelector<HTMLElement>(".diff-grid[data-diff-selection-side]")
        ?.removeAttribute("data-diff-selection-side");

      const target = event.target;
      if (!(target instanceof Element) || !root.contains(target)) {
        return;
      }
      const side = target.closest<HTMLElement>(
        ".diff-side.side-left, .diff-side.side-right",
      );
      if (side === null || !root.contains(side)) {
        return;
      }
      const grid = side.closest<HTMLElement>(".diff-grid");
      if (grid === null || !root.contains(grid)) {
        return;
      }
      grid.dataset.diffSelectionSide = side.classList.contains("side-left")
        ? "left"
        : "right";
    }

    document.addEventListener("pointerdown", selectDiffSide);
    onCleanup(() => {
      document.removeEventListener("pointerdown", selectDiffSide);
    });
  });

  return (
    <section ref={root} class="change-set-root" data-change-set-root>
      <NavigationProvider root={() => root}>
        <Hotkeys
          onToggleTree={props.onToggleTree}
          onToggleView={props.onToggleView}
          onToggleHistory={props.onToggleHistory}
          onReload={props.onReload}
          onToggleHelp={props.onToggleHelp}
          onToggleDebug={props.onToggleDebug}
        />
        {props.children(hunkDisplay)}
        <HunkDisplayObserver
          root={() => root}
          onDisplayChange={(display) => setHunkDisplay(display)}
        />
        <div class="hud-stack">
          <Show when={props.debugOpen}>
            <DebugHud
              globalSelectedHunk={() =>
                hunkDisplay()?.globalSelectedHunk ?? null
              }
            />
          </Show>
          <HintHud
            helpOpen={props.helpOpen}
            onToggleHelp={props.onToggleHelp}
          />
        </div>
        <HelpModal
          open={props.helpOpen}
          onOpenChange={props.onHelpOpenChange}
        />
      </NavigationProvider>
    </section>
  );
}

/**
 * Defines the direct operations available to the active ChangeSet hotkey listener.
 *
 * Every callback is required and invokes one explicit operation. Hunk and
 * Top operations are obtained from the enclosing ChangeSet Navigation instance.
 */
type HotkeysProps = {
  onToggleTree: () => void;
  onToggleView: () => void;
  onToggleHistory: () => void;
  onReload: () => void;
  onToggleHelp: () => void;
  onToggleDebug: () => void;
};

/**
 * Installs the single direct keyboard listener for one active ChangeSet lifetime.
 *
 * Callers provide concrete operations. Editable controls and modified browser
 * shortcuts retain native behavior; recognized keys prevent their default only
 * before invoking the corresponding operation. Cleanup removes the listener.
 */
function Hotkeys(props: HotkeysProps): null {
  const navigation = useNavigation();
  const toast = useToasts();
  onMount(() => {
    /**
     * Routes one unmodified non-editable key event to its concrete action.
     *
     * Unsupported keys remain untouched. Shift is deliberately not treated as a
     * global modifier exclusion and therefore does not suppress recognized keys.
     */
    function onKeyDown(event: KeyboardEvent): void {
      if (event.defaultPrevented) {
        return;
      }
      if (event.metaKey || event.ctrlKey) {
        return;
      }
      if (event.altKey) {
        return;
      }
      const target = event.target;
      // Let editable controls handle their own keystrokes instead of treating
      // user input as a ChangeSet hotkey.
      if (target instanceof HTMLElement) {
        if (target.isContentEditable) {
          return;
        }
        if (
          target instanceof HTMLInputElement ||
          target instanceof HTMLTextAreaElement
        ) {
          return;
        }
        if (target instanceof HTMLSelectElement) {
          return;
        }
      }

      if (event.code === "KeyN" && event.shiftKey) {
        event.preventDefault();
        void navigation
          .navigate({ kind: "previous-hunk" })
          .catch((error: unknown) =>
            toast.showError("Navigation failed", error),
          );
        return;
      }
      if (event.code === "KeyN") {
        event.preventDefault();
        void navigation
          .navigate({ kind: "next-hunk" })
          .catch((error: unknown) =>
            toast.showError("Navigation failed", error),
          );
        return;
      }
      if (event.code === "KeyP") {
        event.preventDefault();
        void navigation
          .navigate({ kind: "top" })
          .catch((error: unknown) =>
            toast.showError("Navigation failed", error),
          );
        return;
      }
      if (event.code === "KeyT") {
        event.preventDefault();
        props.onToggleTree();
        return;
      }
      if (event.code === "KeyI") {
        event.preventDefault();
        props.onToggleView();
        return;
      }
      if (event.code === "KeyM") {
        event.preventDefault();
        props.onToggleHistory();
        return;
      }
      if (event.code === "KeyR") {
        event.preventDefault();
        props.onReload();
        return;
      }
      if (event.code === "KeyD") {
        event.preventDefault();
        props.onToggleDebug();
        return;
      }
      if (event.code === "KeyH") {
        event.preventDefault();
        props.onToggleHelp();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    onCleanup(() => document.removeEventListener("keydown", onKeyDown));
  });

  return null;
}

/**
 * Describes the sampled values shown by the established developer HUD.
 *
 * Values are presentation strings sampled only while DebugHud is mounted. Hunk
 * position is reactive `HunkDisplay` data and is deliberately not sampled.
 */
type DebugMetrics = {
  fps: string;
  nodes: string;
  spans: string;
};

/**
 * Defines the exact reactive hunk value displayed by DebugHud.
 *
 * Null exists only before the first DOM calculation. The HUD may format this
 * value but must not query hunk DOM, calculate navigation state, or mutate it.
 */
type DebugHudProps = {
  globalSelectedHunk: Accessor<HunkDisplay["globalSelectedHunk"] | null>;
};

/**
 * Defines the independent Help operation required by the fixed HintHud.
 *
 * Navigation comes from context; this contract must not carry hunk state,
 * destinations, counters, or grouped HUD state.
 */
type HintHudProps = {
  helpOpen: boolean;
  onToggleHelp: () => void;
};

/**
 * Renders the established three-button explicit-navigation HUD.
 *
 * Next and Previous call the enclosing ChangeSet Navigation instance. Help
 * remains an independent UI operation handled by the outer ChangeSet.
 */
function HintHud(props: HintHudProps): JSX.Element {
  const navigation = useNavigation();
  const toast = useToasts();
  return (
    <nav class="hint-hud" aria-label="Hunk navigation">
      <button
        type="button"
        onClick={() =>
          void navigation
            .navigate({ kind: "next-hunk" })
            .catch((error: unknown) =>
              toast.showError("Navigation failed", error),
            )
        }
        title="Next hunk (n)"
      >
        Next <kbd>n</kbd>
      </button>
      <button
        type="button"
        onClick={() =>
          void navigation
            .navigate({ kind: "previous-hunk" })
            .catch((error: unknown) =>
              toast.showError("Navigation failed", error),
            )
        }
        title="Previous hunk (N)"
      >
        Prev <kbd>N</kbd>
      </button>
      <button
        type="button"
        aria-expanded={props.helpOpen}
        onClick={props.onToggleHelp}
        title="Hotkey help (h)"
      >
        Help <kbd>h</kbd>
      </button>
    </nav>
  );
}

/**
 * Renders the established developer metrics panel while it is mounted.
 *
 * The component samples frame rate and document element counts. Its hunk value
 * formats the exact reactive mirror calculated from the ChangeSet DOM. Cleanup
 * cancels its animation frame and it performs no navigation or selected-identity changes.
 */
function DebugHud(props: DebugHudProps): JSX.Element {
  const [metrics, setMetrics] = createSignal<DebugMetrics>({
    fps: "--",
    nodes: "--",
    spans: "--",
  });
  const hunkMetric = createMemo(() => {
    const hunk = props.globalSelectedHunk();
    if (hunk === null) {
      return "--/--";
    }
    return `${hunk.position.current ?? "—"}/${hunk.position.total}${
      hunk.hasMore ? "+" : ""
    }`;
  });
  let frame = 0;
  let sampleStartedAt = performance.now();
  let sampleFrames = 0;
  let displayUpdatedAt = sampleStartedAt;
  let currentFps = 0;

  let nodesCountedAt = sampleStartedAt;
  let countTimer: number | null = null;

  onMount(() => {
    /**
     * Refreshes the displayed frame rate for the open Debug HUD.
     *
     * The active animation-frame loop calls this at its display cadence. It
     * replaces only the FPS metric; document counting is scheduled separately
     * because its cost would perturb the frame rate it is displayed beside.
     */
    function updateFps(): void {
      setMetrics((current) => ({
        ...current,
        fps: currentFps ? String(Math.round(currentFps)) : "--",
      }));
    }

    /**
     * Counts document nodes outside the animation-frame callback.
     *
     * The full-document walk costs frame budget on a loaded branch tab, so it
     * runs at most every five seconds and in its own macrotask; during a
     * sustained scroll the counts go momentarily stale instead of stealing
     * time from the FPS sample.
     */
    function scheduleNodeCount(): void {
      if (countTimer !== null) return;
      countTimer = window.setTimeout(() => {
        countTimer = null;
        setMetrics((current) => ({
          ...current,
          nodes: document.querySelectorAll("*").length.toLocaleString(),
          spans: document.querySelectorAll("span").length.toLocaleString(),
        }));
      }, 0);
    }

    /**
     * Advances the frame sample and refreshes visible text at the stable cadence.
     *
     * The callback schedules exactly one successor until component cleanup.
     */
    function tick(now: number): void {
      sampleFrames += 1;
      const sampleElapsed = now - sampleStartedAt;
      if (sampleElapsed >= 400) {
        currentFps = (sampleFrames * 1000) / sampleElapsed;
        sampleStartedAt = now;
        sampleFrames = 0;
      }
      if (now - displayUpdatedAt >= 900) {
        updateFps();
        displayUpdatedAt = now;
      }
      if (now - nodesCountedAt >= 5000) {
        nodesCountedAt = now;
        scheduleNodeCount();
      }
      frame = requestAnimationFrame(tick);
    }

    updateFps();
    scheduleNodeCount();
    frame = requestAnimationFrame(tick);
    onCleanup(() => {
      cancelAnimationFrame(frame);
      if (countTimer !== null) window.clearTimeout(countTimer);
    });
  });

  return (
    <div class="debug-hud" aria-label="Developer metrics">
      <DebugMetric label="FPS" value={metrics().fps} />
      <DebugMetric label="Nodes" value={metrics().nodes} />
      <DebugMetric label="Spans" value={metrics().spans} />
      <DebugMetric label="Hunks" value={hunkMetric()} />
    </div>
  );
}

/**
 * Renders one label/value pair inside the established developer HUD grid.
 *
 * Callers provide final display strings. The component performs no sampling and stores no
 * state and preserves the exact existing visual structure.
 */
function DebugMetric(props: { label: string; value: string }): JSX.Element {
  return (
    <div class="debug-metric">
      <span class="debug-metric-label">{props.label}</span>
      <strong class="debug-metric-value">{props.value}</strong>
    </div>
  );
}

/**
 * Defines the one-way DOM mirror callback supplied by ChangeSetShell.
 *
 * `root` is the mounted ChangeSet root. `onDisplayChange` receives every
 * complete immutable replacement value. The observer must never write DOM or
 * expose data to Navigation.
 */
type HunkDisplayObserverProps = {
  root: Accessor<HTMLElement>;
  onDisplayChange: (display: HunkDisplay) => void;
};

/**
 * Mirrors exact hunk display data from primitive FileCard DOM attributes.
 *
 * One subtree observer watches only `data-hunk-set` and selected identity. It
 * performs no selection, navigation, scrolling, enrichment, expansion, fetch,
 * counter write, or FileTree write. Its first calculation runs after the
 * Navigation provider has synchronously selected the initial target. A terminal
 * file-render marker disconnects observation and retains only the last exact
 * snapshot without producing another failure notification.
 */
function HunkDisplayObserver(props: HunkDisplayObserverProps): null {
  const toast = useToasts();
  onMount(() => {
    const root = props.root();

    /**
     * Parses one FileCard's complete semantic hunk set.
     *
     * The parsed kind and participation state determine only `hasMore`.
     * Concrete position and total calculation continues to walk hunk targets.
     */
    function parseHunkSet(card: HTMLElement): {
      kind: "husk" | "lazy" | "zero" | "real";
      skipped: boolean;
    } {
      const hunkSet = card.dataset.hunkSet;
      if (hunkSet === undefined) {
        throw new Error("Every FileCard requires data-hunk-set.");
      }
      const pseudo = /^(husk|lazy|zero)(:skip)?$/.exec(hunkSet);
      if (pseudo !== null) {
        const kind = pseudo[1];
        switch (kind) {
          case "husk":
          case "lazy":
          case "zero":
            return { kind, skipped: pseudo[2] === ":skip" };
        }
        throw new Error("FileCard has an invalid pseudo-hunk set.");
      }
      const real = /^real:([1-9]\d*)(:skip)?$/.exec(hunkSet);
      if (real === null) {
        throw new Error(`FileCard has invalid hunk set ${hunkSet}.`);
      }
      return { kind: "real", skipped: real[2] === ":skip" };
    }

    /**
     * Per-card facts recorded by the last complete calculation.
     *
     * `offset` is the card's stable-position prefix and `participating` its
     * displayed total; both are invariant under selection-only mutations, so
     * the selective path below can reuse them. Any structural mutation runs
     * the full walk again, which replaces this map wholesale.
     */
    type CardStats = {
      fileIndex: number;
      participating: number;
      offset: number;
    };
    let cardStats: Map<HTMLElement, CardStats> | null = null;
    let lastDisplay: HunkDisplay | null = null;

    /**
     * Calculates the complete exact display mirror from current FileCard DOM.
     *
     * The calculation walks concrete targets in DOM order and reads semantic
     * hunk sets only for `hasMore`. It does not audit redundant renderer counts,
     * classes, or target invariants; Navigation validates the target involved in
     * each action. Stable positions include skipped identities, while displayed
     * totals count participating targets only.
     */
    function calculateDisplay(): HunkDisplay {
      const cards = Array.from(
        root.querySelectorAll<HTMLElement>("[data-file-card]"),
      );
      const selectedCards = cards.filter(
        (card) => card.dataset.selectedHunkIndex !== undefined,
      );
      if (cards.length === 0) {
        if (selectedCards.length !== 0) {
          throw new Error("Empty ChangeSet contains a selected FileCard.");
        }
        cardStats = new Map();
        return {
          selectedFileIndex: null,
          globalSelectedHunk: {
            position: { current: null, total: 0 },
            hasMore: false,
          },
          fileSelectedHunks: new Map(),
        };
      }
      if (selectedCards.length !== 1) {
        throw new Error(
          "A non-empty ChangeSet requires exactly one selected FileCard.",
        );
      }

      let stablePositionOffset = 0;
      let participatingTotal = 0;
      let selectedCurrent: number | null = null;
      let selectedFileIndex: number | null = null;
      let hasMore = false;
      const fileSelectedHunks = new Map<number, HunkPosition>();
      const stats = new Map<HTMLElement, CardStats>();

      cards.forEach((card) => {
        const fileIndexText = card.dataset.fileIndex;
        if (
          fileIndexText === undefined ||
          !/^(?:0|[1-9]\d*)$/.test(fileIndexText)
        ) {
          throw new Error("FileCard has an invalid manifest file index.");
        }
        const fileIndex = Number(fileIndexText);
        const hunkSet = parseHunkSet(card);
        const targets = Array.from(
          card.querySelectorAll<HTMLElement>("[data-hunk-target]"),
        );
        for (const target of targets) {
          const hunkIndex = target.dataset.hunkIndex;
          if (
            target.dataset.fileIndex !== fileIndexText ||
            hunkIndex === undefined ||
            !/^(?:0|[1-9]\d*)$/.test(hunkIndex)
          ) {
            throw new Error(
              `FileCard ${fileIndexText} contains a hunk with invalid coordinates.`,
            );
          }
        }
        const participatingTargets = targets.filter(
          (target) => !target.classList.contains("skip"),
        );
        hasMore ||=
          hunkSet.kind === "husk" || hunkSet.kind === "lazy" || hunkSet.skipped;
        let localCurrent: number | null = null;
        if (card === selectedCards[0]) {
          selectedFileIndex = fileIndex;
          const selectedIndexText = card.dataset.selectedHunkIndex;
          if (selectedIndexText === undefined) {
            throw new Error("Selected FileCard has no hunk index.");
          }
          const matchingTargets = targets.filter(
            (target) =>
              target.dataset.fileIndex === String(fileIndex) &&
              target.dataset.hunkIndex === selectedIndexText,
          );
          if (matchingTargets.length !== 1) {
            throw new Error(
              `Selected hunk (${fileIndex}, ${selectedIndexText}) requires exactly one DOM target.`,
            );
          }
          const selectedTarget = matchingTargets[0];
          if (selectedTarget === undefined) {
            throw new Error("Selected hunk target disappeared.");
          }
          localCurrent = targets.indexOf(selectedTarget) + 1;
          selectedCurrent = stablePositionOffset + localCurrent;
        }
        const localTotal = participatingTargets.length;
        fileSelectedHunks.set(fileIndex, {
          current: localCurrent,
          total: localTotal,
        });
        stats.set(card, {
          fileIndex,
          participating: localTotal,
          offset: stablePositionOffset,
        });
        stablePositionOffset += targets.length;
        participatingTotal += localTotal;
      });

      if (selectedFileIndex === null || selectedCurrent === null) {
        throw new Error("Selected hunk has no calculable DOM position.");
      }
      cardStats = stats;
      return {
        selectedFileIndex,
        globalSelectedHunk: {
          position: { current: selectedCurrent, total: participatingTotal },
          hasMore,
        },
        fileSelectedHunks,
      };
    }

    /**
     * Recomputes the display for a selection-only mutation batch.
     *
     * Scroll-follow changes selection on every eligible scroll, so this path
     * must not walk every mounted target. It reuses the per-card facts of the
     * last complete calculation and walks only the newly selected FileCard.
     * `null` reports a failed precondition (no prior complete calculation, or
     * an unknown selected card); the caller then runs the full walk, which
     * owns structural validation.
     */
    function calculateSelectionDisplay(): HunkDisplay | null {
      if (cardStats === null || lastDisplay === null) {
        return null;
      }
      const selectedCards = root.querySelectorAll<HTMLElement>(
        "[data-file-card][data-selected-hunk-index]",
      );
      if (selectedCards.length !== 1) {
        throw new Error(
          "A non-empty ChangeSet requires exactly one selected FileCard.",
        );
      }
      const card = selectedCards[0];
      if (card === undefined) {
        return null;
      }
      const stats = cardStats.get(card);
      if (stats === undefined) {
        return null;
      }
      const selectedIndexText = card.dataset.selectedHunkIndex;
      if (selectedIndexText === undefined) {
        throw new Error("Selected FileCard has no hunk index.");
      }
      const targets = Array.from(
        card.querySelectorAll<HTMLElement>("[data-hunk-target]"),
      );
      const matchingTargets = targets.filter(
        (target) =>
          target.dataset.fileIndex === String(stats.fileIndex) &&
          target.dataset.hunkIndex === selectedIndexText,
      );
      if (matchingTargets.length !== 1) {
        throw new Error(
          `Selected hunk (${stats.fileIndex}, ${selectedIndexText}) requires exactly one DOM target.`,
        );
      }
      const selectedTarget = matchingTargets[0];
      if (selectedTarget === undefined) {
        return null;
      }
      const localCurrent = targets.indexOf(selectedTarget) + 1;
      const fileSelectedHunks = new Map(lastDisplay.fileSelectedHunks);
      const previousIndex = lastDisplay.selectedFileIndex;
      if (previousIndex !== null && previousIndex !== stats.fileIndex) {
        const previous = fileSelectedHunks.get(previousIndex);
        if (previous !== undefined) {
          fileSelectedHunks.set(previousIndex, {
            current: null,
            total: previous.total,
          });
        }
      }
      fileSelectedHunks.set(stats.fileIndex, {
        current: localCurrent,
        total: stats.participating,
      });
      return {
        selectedFileIndex: stats.fileIndex,
        globalSelectedHunk: {
          position: {
            current: stats.offset + localCurrent,
            total: lastDisplay.globalSelectedHunk.position.total,
          },
          hasMore: lastDisplay.globalSelectedHunk.hasMore,
        },
        fileSelectedHunks,
      };
    }

    let alive = true;
    let observer: MutationObserver | null = null;
    let queuedKind: "selective" | "full" | null = null;
    onCleanup(() => {
      alive = false;
      observer?.disconnect();
    });

    /**
     * Coalesces one display calculation after renderer mount work.
     *
     * DiffGrid places its imperative row targets during mount after FileCard has
     * updated `data-hunk-set`. The extra microtask observes the completed renderer
     * operation instead of interpreting that internal transition as invalid DOM.
     * A queued full calculation subsumes a selective one, never the reverse.
     */
    function queueDisplayCalculation(kind: "selective" | "full"): void {
      if (queuedKind !== null) {
        if (kind === "full") {
          queuedKind = "full";
        }
        return;
      }
      queuedKind = kind;
      queueMicrotask(() => {
        const runKind = queuedKind;
        queuedKind = null;
        if (!alive) {
          return;
        }
        if (root.querySelector("[data-file-render-error]") !== null) {
          // The renderer already exposed terminal local damage and its Toast.
          // Preserve the last successful display without repair or duplication.
          observer?.disconnect();
          return;
        }
        try {
          const display =
            runKind === "selective"
              ? (calculateSelectionDisplay() ?? calculateDisplay())
              : calculateDisplay();
          lastDisplay = display;
          props.onDisplayChange(display);
        } catch (error) {
          observer?.disconnect();
          toast.showError(
            "Could not calculate hunk display",
            error instanceof Error
              ? error
              : new Error("Hunk display calculation threw a non-Error value."),
          );
        }
      });
    }

    // Navigation selects the first target synchronously during mount. Deferring
    // observer attachment one microtask makes the first mirror include it.
    queueMicrotask(() => {
      if (!alive || !root.isConnected) {
        return;
      }
      observer = new MutationObserver((records) => {
        queueDisplayCalculation(
          records.every(
            (record) => record.attributeName === "data-selected-hunk-index",
          )
            ? "selective"
            : "full",
        );
      });
      observer.observe(root, {
        subtree: true,
        attributes: true,
        attributeFilter: [
          "data-hunk-set",
          "data-selected-hunk-index",
          "data-file-render-error",
        ],
      });
      queueDisplayCalculation("full");
    });
  });
  return null;
}

/**
 * Renders the ChangeSet hotkey reference as the established modal overlay.
 *
 * Callers provide explicit visibility and an update callback. Every listed
 * hotkey is currently available; removed file-wide expansion operations are
 * absent. Backdrop and Close actions report `false` through that callback.
 */
function HelpModal(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): JSX.Element {
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
            <HotkeyHelpRow keys="m" label="Toggle review History" />
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

/**
 * Groups one titled set of Help rows using the established modal geometry.
 *
 * Callers provide visible row content only. The component stores no hotkey state,
 * enablement policy, or interaction and preserves child order exactly.
 */
function HotkeyHelpSection(props: {
  title: string;
  children: JSX.Element;
}): JSX.Element {
  return (
    <section class="help-modal-section">
      <h2>{props.title}</h2>
      <div class="help-modal-grid">{props.children}</div>
    </section>
  );
}

/**
 * Presents one hotkey and its label.
 *
 * The row is descriptive rather than interactive and never invokes the
 * represented action.
 */
function HotkeyHelpRow(props: { keys: string; label: string }): JSX.Element {
  return (
    <div class="help-hud-row">
      <kbd>{props.keys}</kbd>
      <span>{props.label}</span>
    </div>
  );
}

/**
 * Defines every immutable backend input and reactive presentation input for one snapshot.
 *
 * Params and manifest never change during this component lifetime. View, profile,
 * and workspace presentation state remain reactive without retargeting backend work.
 * The callbacks expose only the two lifecycle actions performed by ChangeSetContent.
 */
type ChangeSetSnapshotProps = {
  params: DiffParams;
  engine: DiffEngine;
  manifest: Manifest;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  hunkDisplay: Accessor<HunkDisplay | null>;
  state: ChangeSetState;
  setState: SetStoreFunction<ChangeSetState>;
  onFileTreeOpenChange(open: boolean): void;
  onFileSequenceChange(stop: (() => Promise<void>) | null): void;
};

/**
 * Supplies ChangeSet-owned History visibility to one Snapshot review boundary.
 *
 * `historyOpen` is the complete current presentation state shared by the global
 * hotkey and History controls. The callback replaces only that state and never
 * performs review transport, navigation, or File-lane work.
 */
type ReviewSnapshotBoundaryProps = ChangeSetSnapshotProps & {
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
};

/**
 * Supplies the engine-bound File lane's current scroll-only destinations and
 * inline History grid position.
 *
 * The callback publishes manifest indexes whose current state is a loaded FullFile. It
 * must not load, expand, select, or navigate a File. The History callback
 * publishes only the concrete third grid child mounted beside the File lane.
 */
type ChangeSetFileLaneProps = ChangeSetSnapshotProps & {
  onFileNavigabilityChange(indexes: ReadonlySet<number>): void;
  onInlineHistoryTargetChange(target: HTMLElement): void;
};

/**
 * Keeps Snapshot review state mounted while engine changes replace the file lane.
 *
 * The manifest and Snapshot identity are immutable for this lifetime. History's
 * explicit View action uses the exact File pair and ordinary FileTree navigation;
 * the nested keyed branch replaces only engine-dependent File rendering.
 */
function ReviewSnapshotBoundary(
  props: ReviewSnapshotBoundaryProps,
): JSX.Element {
  const navigation = useNavigation();
  // History navigation shares this exact Snapshot boundary lifetime; disposal
  // cancels any line preparation before it can scroll or return a detached row.
  const reviewNavigationAbort = new AbortController();
  onCleanup(() => reviewNavigationAbort.abort());
  const files = manifestFilesInOrder(props.manifest.tree);
  const reviewFileIndexes = new Map<string, number>();
  files.forEach((file, fileIndex) => {
    const key = JSON.stringify([file.entry.left_path, file.entry.right_path]);
    assert(
      !reviewFileIndexes.has(key),
      "Review navigation requires unique manifest File pairs.",
    );
    reviewFileIndexes.set(key, fileIndex);
  });
  const [navigableFileIndexes, setNavigableFileIndexes] = createSignal<
    ReadonlySet<number>
  >(new Set());
  // Inline History is rendered by the surrounding review boundary into this
  // exact third ChangeSet grid child; Split History keeps its fixed host.
  const [inlineHistoryTarget, setInlineHistoryTarget] =
    createSignal<HTMLElement | null>(null);

  /** Resolves one located Thread to its unique immutable manifest index. */
  function reviewFileIndex(location: ThreadCodeLocation): number {
    const fileIndex = reviewFileIndexes.get(
      JSON.stringify([location.file.left_path, location.file.right_path]),
    );
    if (fileIndex === undefined) {
      throw new Error(
        "A located review Thread requires one exact manifest File.",
      );
    }
    return fileIndex;
  }

  /** Reports whether exact line navigation currently accepts the Thread. */
  function canViewReviewThread(location: ThreadCodeLocation): boolean {
    return navigableFileIndexes().has(reviewFileIndex(location));
  }

  /** Navigates to one loaded Thread line and returns its mounted review anchor. */
  async function viewReviewThread(
    location: ThreadCodeLocation,
  ): Promise<ReviewCodeAnchor | null> {
    const fileIndex = reviewFileIndex(location);
    if (!navigableFileIndexes().has(fileIndex)) {
      throw new Error("Review navigation requires a loaded File.");
    }
    const selectedPath = expect(
      location.side === "left"
        ? location.file.left_path
        : location.file.right_path,
      "Located review Thread requires its selected-side File path.",
    );
    const line = location.kind === "range" ? location.range.start_line : 1;
    const result = await navigation.navigate({
      kind: "line",
      fileIndex,
      target: {
        file: selectedPath,
        region:
          location.kind === "range" &&
          location.region.kind === "notebook-cell-source"
            ? location.region.cell_key
            : null,
        side: location.side,
        line: String(line),
      },
      abortSignal: reviewNavigationAbort.signal,
    });
    if (result.state === "stopped") return null;
    if (result.state === "missing") {
      throw new Error(
        "The exact reviewed line is absent from the loaded File.",
      );
    }
    assert(
      result.state === "complete",
      "Review line navigation did not finish.",
    );
    const changeSetRoot = inlineHistoryTarget()?.closest<HTMLElement>(
      "[data-change-set-root]",
    );
    assert(
      changeSetRoot !== null && changeSetRoot !== undefined,
      "Review navigation requires its mounted ChangeSet.",
    );
    const card = changeSetRoot.querySelector<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    assert(card !== null, "Review navigation lost its loaded FileCard.");
    const region =
      location.kind === "range" &&
      location.region.kind === "notebook-cell-source"
        ? location.region.cell_key
        : "";
    const matchingGrids = [
      ...card.querySelectorAll<HTMLElement>(".diff-grid"),
    ].filter((grid) => grid.dataset.reviewRegion === region);
    assert(
      matchingGrids.length === 1,
      "Review navigation requires one exact rendered region.",
    );
    const matchingLines = expect(
      matchingGrids[0],
      "Reviewed region disappeared after navigation.",
    ).querySelectorAll<HTMLElement>(
      `.line-no[data-line-pin-side="${location.side}"][data-line-pin-line="${line}"]`,
    );
    assert(
      matchingLines.length === 1,
      "Review navigation requires one exact line in its prepared row.",
    );
    const lineNumber = expect(
      matchingLines[0],
      "Reviewed line disappeared after navigation.",
    );
    const rowPart = lineNumber.parentElement;
    const codeCell = rowPart?.querySelector<HTMLElement>(":scope > .line-code");
    const trigger = lineNumber.querySelector<HTMLButtonElement>(
      ".line-comment-trigger:not([hidden])",
    );
    assert(
      codeCell !== null && codeCell !== undefined && trigger !== null,
      "Reviewed line requires its rendered code and marker.",
    );
    return { codeCell, trigger };
  }

  return (
    <ReviewProvider
      snapshotId={props.manifest.snapshot_id}
      view={props.view}
      historyOpen={props.historyOpen}
      onHistoryOpenChange={props.onHistoryOpenChange}
      profile={props.profile}
      inlineHistoryTarget={inlineHistoryTarget}
      canViewThread={canViewReviewThread}
      viewThread={viewReviewThread}
    >
      <Show when={props.engine} keyed>
        {(engine) => (
          <ChangeSetSnapshot
            params={props.params}
            engine={engine}
            manifest={props.manifest}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            profile={props.profile}
            appHeaderOutlets={props.appHeaderOutlets}
            hunkDisplay={props.hunkDisplay}
            state={props.state}
            setState={props.setState}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onFileSequenceChange={props.onFileSequenceChange}
            onFileNavigabilityChange={setNavigableFileIndexes}
            onInlineHistoryTargetChange={setInlineHistoryTarget}
          />
        )}
      </Show>
    </ReviewProvider>
  );
}

/**
 * Owns every observer, lane value, derivation, and rendered node for one manifest.
 *
 * All query definitions use the same immutable params and opaque `snapshot_id`.
 * File failures remain localized. Disposal cancels the lane and releases every
 * snapshot query.
 */
function ChangeSetSnapshot(props: ChangeSetFileLaneProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();
  const pins = linePins();
  let changeSetRoot!: HTMLDivElement;
  const orderedFiles = manifestFilesInOrder(props.manifest.tree);
  const fileIndexByKey = new Map<string, number>();
  for (const [fileIndex, file] of orderedFiles.entries()) {
    const key = manifestEntryKey(file.entry);
    if (fileIndexByKey.has(key)) {
      throw new Error(
        `Manifest returned duplicate file ${fileDisplayName(file.entry)}.`,
      );
    }
    fileIndexByKey.set(key, fileIndex);
  }

  // The exact FileDiff `display_name` per manifest position: repository
  // snapshots use the backend's path-pair label, while preset backends
  // deliberately name their old/new fixture pair by its new-side path. The
  // lane asserts file responses against these names, and line-pin identity
  // resolves through them.
  const canonicalNames = orderedFiles.map((file) =>
    props.params.tab !== "preset"
      ? fileDisplayName(file.entry)
      : expect(
          file.entry.right_path,
          "Preset manifest file requires its canonical new-side path.",
        ),
  );

  const parsedLinePin = pins.parseUrl();
  let lineTarget: FileLaneLineTarget | null = null;
  if (parsedLinePin.state === "invalid") {
    toast.showTransient(
      "Line pin unavailable",
      "The URL contains an invalid line pin.",
      2_000,
    );
  } else if (parsedLinePin.state === "valid") {
    const matches = canonicalNames.flatMap((name, fileIndex) =>
      name === parsedLinePin.target.file ? [fileIndex] : [],
    );
    if (matches.length > 1) {
      throw new Error(
        `Line target path ${parsedLinePin.target.file} is ambiguous.`,
      );
    }
    const match = matches[0];
    if (match === undefined) {
      toast.showTransient(
        "Line pin unavailable",
        `${parsedLinePin.target.file} is not present in this ChangeSet.`,
        2_000,
      );
      const toggleResult = pins.toggleUrlState(parsedLinePin.target);
      if (toggleResult !== "unpinned") {
        throw new Error("Missing line-pin file changed its current target.");
      }
    } else {
      lineTarget = {
        fileIndex: match,
        restore: (signal) => pins.restore(parsedLinePin.target, match, signal),
      };
    }
  }

  const lane = createFileLane({
    engine: props.engine,
    snapshotId: props.manifest.snapshot_id,
    files: orderedFiles,
    canonicalNames,
    queryClient,
    lineTarget,
    onExplicitLoad: (fileIndex) => {
      const file = expect(
        orderedFiles[fileIndex],
        "Explicit load reported an invalid manifest index.",
      );
      const key = manifestEntryKey(file.entry);
      // The query result has replaced LazyFile with FullFile. Expand it
      // now, but never override a collapse performed while it loaded.
      if (props.state.fileExpansion[key] !== false) {
        props.setState("fileExpansion", key, true);
      }
    },
  });
  props.onFileSequenceChange(lane.stop);
  onCleanup(() => props.onFileSequenceChange(null));

  const preferenceQueries = createQueries<
    Array<ReturnType<typeof api.profile.preferences>>
  >(() => ({
    queries:
      props.profile === null ? [] : [api.profile.preferences(props.profile.id)],
  }));
  const aggressiveFolds = createMemo(() => {
    const query = preferenceQueries[0];
    if (query === undefined || query.data === undefined) {
      return true;
    }
    return query.data.aggressive_folds;
  });

  createEffect(() => {
    const navigable = new Set<number>();
    for (const [fileIndex, state] of lane.fileStates().entries()) {
      if (state.state === "full") {
        navigable.add(fileIndex);
      }
    }
    props.onFileNavigabilityChange(navigable);
  });
  onCleanup(() => props.onFileNavigabilityChange(new Set()));

  /**
   * Resolves one manifest file to its current canonical presentation state.
   *
   * Directory reachability consumes the same strict manifest/state ordering as
   * FileTree. Missing or duplicate identities violate the immutable snapshot
   * contract and throw instead of inventing a directory-expansion default.
   */
  function stateForManifestFile(file: ManifestFile): FileState {
    const fileIndex = fileIndexByKey.get(manifestEntryKey(file.entry));
    if (fileIndex === undefined) {
      throw new Error(
        `ChangeSet cannot index ${fileDisplayName(file.entry)} for directory reachability.`,
      );
    }
    const state = lane.fileStates()[fileIndex];
    if (state === undefined) {
      throw new Error(
        `ChangeSet is missing state for ${fileDisplayName(file.entry)}.`,
      );
    }
    return state;
  }

  const directoryExpansion = createMemo(() =>
    calculateDirectoryExpansion(
      props.manifest.tree,
      stateForManifestFile,
      props.state.fileExpansion,
    ),
  );

  const failedFiles = createMemo(
    () =>
      lane
        .fileStates()
        .filter(
          (state) => state.state === "lazy" && state.file.kind === "error",
        ).length,
  );
  const sequenceState = createMemo<FileSequenceState>(() => {
    const active = lane.activity();
    if (active !== null) {
      return {
        state: "loading",
        processed: lane.processed(),
        automaticTotal: lane.automaticTotal,
        failed: failedFiles(),
        active,
      };
    }
    return {
      state: "ready",
      processed: lane.processed(),
      automaticTotal: lane.automaticTotal,
      failed: failedFiles(),
    };
  });

  return (
    <>
      <Show when={lane.error()} keyed>
        {(error) => {
          throw error;
        }}
      </Show>
      <Portal mount={props.appHeaderOutlets.status()}>
        <AppHeaderFileStatus state={sequenceState()} />
      </Portal>
      <Portal mount={props.appHeaderOutlets.summary()}>
        <ManifestStatistics summary={props.manifest.summary} />
      </Portal>
      <p class="status change-set-title">
        {changeSetTitle(props.params, props.manifest)}
      </p>
      <div
        ref={changeSetRoot}
        class="diff-workspace"
        classList={{
          "diff-workspace-inline": props.view === "inline",
          "diff-workspace-tree-open": props.fileTreeOpen,
        }}
      >
        <UnexpectedErrorBoundary
          title="Could not render file tree"
          retryOnR={false}
        >
          <FileTree
            changeSetRoot={() => changeSetRoot}
            tree={props.manifest.tree}
            states={lane.fileStates}
            open={props.fileTreeOpen}
            view={props.view}
            selectedFileIndex={() =>
              props.hunkDisplay()?.selectedFileIndex ?? null
            }
            directoryExpansion={directoryExpansion}
            fileExpansion={() => props.state.fileExpansion}
            onOpenChange={props.onFileTreeOpenChange}
            onDirectoryExpandedChange={(directory, expanded) =>
              batch(() => {
                for (const file of manifestFilesInOrder(directory.entries)) {
                  props.setState(
                    "fileExpansion",
                    manifestEntryKey(file.entry),
                    expanded,
                  );
                }
              })
            }
            onFileExpandedChange={(file, expanded) =>
              props.setState(
                "fileExpansion",
                manifestEntryKey(file.entry),
                expanded,
              )
            }
          />
        </UnexpectedErrorBoundary>
        <section class="file-list" aria-label="Changed files">
          <Show
            when={orderedFiles.length > 0}
            fallback={
              <div class="directory-groups">
                <section
                  class="directory-group file-list-empty-shell"
                  aria-label="No changed files"
                >
                  <p class="empty file-list-empty">No files loaded yet.</p>
                </section>
              </div>
            }
          >
            <div class="directory-groups">
              <For each={orderedFiles}>
                {(file, fileIndex) => {
                  return (
                    <FileCard
                      reviewFile={{
                        left_path: file.entry.left_path,
                        right_path: file.entry.right_path,
                      }}
                      file_state={lane.fileState(fileIndex())}
                      expanded={fileExpanded(
                        file,
                        lane.fileState(fileIndex()),
                        props.state.fileExpansion,
                      )}
                      explicitlyCollapsed={
                        props.state.fileExpansion[
                          manifestEntryKey(file.entry)
                        ] === false
                      }
                      admitted={lane.admitted(fileIndex())}
                      engine={props.engine}
                      view={props.view}
                      aggressiveFolds={aggressiveFolds()}
                      linePins={pins}
                      globalSelectedHunk={() =>
                        props.hunkDisplay()?.globalSelectedHunk ?? null
                      }
                      fileSelectedHunk={() => {
                        const display = props.hunkDisplay();
                        if (display === null) {
                          return null;
                        }
                        return expect(
                          display.fileSelectedHunks.get(fileIndex()),
                          `Missing hunk position for file ${fileIndex()}.`,
                        );
                      }}
                      onExpandedChange={(expanded) =>
                        props.setState(
                          "fileExpansion",
                          manifestEntryKey(file.entry),
                          expanded,
                        )
                      }
                      onLoad={() => {
                        lane.enqueue(fileIndex(), "bounded");
                      }}
                      onRetry={() => {
                        lane.enqueue(fileIndex(), "unbounded");
                      }}
                    />
                  );
                }}
              </For>
            </div>
          </Show>
        </section>
        <Show when={props.view === "inline"}>
          <div
            ref={props.onInlineHistoryTargetChange}
            class="review-history-slot"
          />
        </Show>
      </div>
    </>
  );
}

/**
 * Calculates directory expansion from current descendant file reachability.
 *
 * Explicit file expansion is authoritative. Unresolved HuskFiles remain
 * reachable so sequential loading cannot collapse and reopen the directory hierarchy, while
 * LazyFiles remain reachable for their visible plank unless explicitly collapsed.
 * Every directory receives one result, including an empty directory.
 */
function calculateDirectoryExpansion(
  nodes: readonly ManifestNode[],
  stateForFile: (file: ManifestFile) => FileState,
  fileExpansion: Readonly<Record<string, boolean | undefined>>,
): ReadonlyMap<string, boolean> {
  const result = new Map<string, boolean>();

  /**
   * Visits one ordered sibling collection and reports subtree reachability.
   *
   * The traversal evaluates every child even after finding a reachable file so
   * nested directory entries are always complete in the returned map.
   */
  function visit(children: readonly ManifestNode[]): boolean {
    let hasReachableFile = false;
    for (const child of children) {
      let childIsReachable: boolean;
      if (child.type === "file") {
        const explicit = fileExpansion[manifestEntryKey(child.entry)];
        if (explicit !== undefined) {
          childIsReachable = explicit;
        } else {
          const state = stateForFile(child);
          childIsReachable =
            state.state === "husk" ||
            state.state === "lazy" ||
            state.backend_data.default_expanded;
        }
      } else {
        childIsReachable = visit(child.entries);
        result.set(child.path, childIsReachable);
      }
      hasReachableFile = childIsReachable || hasReachableFile;
    }
    return hasReachableFile;
  }

  visit(nodes);
  return result;
}

/**
 * Describes FileCard-local render modes calculated solely for FileTree markers.
 *
 * Keys are immutable manifest file indices and values mirror the current
 * `data-file-render` attributes. The map is disposable presentation data: it
 * must not control virtualization, navigation, selection, or ChangeSet state.
 */
type FileTreeRenderModes = ReadonlyMap<number, "rich" | "virtual">;

/**
 * Defines all reactive presentation and expansion inputs for the private FileTree.
 *
 * The tree receives one immutable manifest, current FileCard states, the stable
 * ChangeSet DOM root, calculated directory reachability, workspace FileTree
 * visibility, and ChangeSet-owned file expansion. Its callbacks may change only
 * tree visibility or file expansion. FileTree stores no query, backend data,
 * hunk selection, navigation, or independent expansion authority.
 */
type FileTreeProps = {
  changeSetRoot: Accessor<HTMLElement>;
  tree: readonly ManifestNode[];
  states: Accessor<readonly FileState[]>;
  open: boolean;
  view: DiffViewMode;
  selectedFileIndex: Accessor<number | null>;
  directoryExpansion: Accessor<ReadonlyMap<string, boolean>>;
  fileExpansion: Accessor<Readonly<Record<string, boolean | undefined>>>;
  onOpenChange: (open: boolean) => void;
  onDirectoryExpandedChange: (
    directory: ManifestDirectory,
    expanded: boolean,
  ) => void;
  onFileExpandedChange: (file: ManifestFile, expanded: boolean) => void;
};

/**
 * Renders the manifest tree, current shared expansion, and private highlighted-row scroll.
 *
 * Directory squares bulk-update descendant file expansion and FullFile squares
 * update one file. Name buttons invoke the enclosing scroll-only FileTree
 * Navigation operation. The component may calculate current FileCard render
 * modes and scroll its own
 * `.file-tree-groups`, but it never changes hunk selection, loads files, expands
 * a row for visibility, or moves the main page.
 */
function FileTree(props: FileTreeProps): JSX.Element {
  const navigation = useNavigation();
  const toast = useToasts();
  const files = manifestFilesInOrder(props.tree);
  const indexByKey = new Map(
    files.map((file, index) => [manifestEntryKey(file.entry), index]),
  );

  /**
   * Resolves one manifest file to its required manifest-order file index.
   *
   * FileTree highlighting and progressive state lookup share this exact index.
   * Missing identity violates the immutable manifest ordering and throws.
   */
  const indexForFile = (file: ManifestFile): number => {
    const index = indexByKey.get(manifestEntryKey(file.entry));
    if (index === undefined) {
      throw new Error(`FileTree cannot index ${fileDisplayName(file.entry)}.`);
    }
    return index;
  };

  /**
   * Sends one manifest file to the enclosing scroll-only Navigation operation.
   *
   * File and directory name buttons share this path. Rejection becomes the
   * ordinary dramatic Toast while Navigation remains the only code that moves
   * the main page; this function never selects, expands, collapses, or fetches.
   */
  function navigateToFile(file: ManifestFile): void {
    void navigation
      .navigate({ kind: "file", fileIndex: indexForFile(file) })
      .catch((error: unknown) =>
        toast.showError("File navigation failed", error),
      );
  }
  /**
   * Resolves one manifest file to the exact shared ChangeSet file state.
   *
   * Missing indices or states violate the required parallel manifest/state
   * ordering and throw rather than producing an incomplete tree row.
   */
  const stateForFile = (file: ManifestFile): FileState => {
    const index = indexForFile(file);
    const state = props.states()[index];
    if (state === undefined) {
      throw new Error(
        `FileTree is missing state for ${fileDisplayName(file.entry)}.`,
      );
    }
    return state;
  };

  const ancestorPathsByFileIndex = new Map<number, readonly string[]>();

  /**
   * Indexes the immutable directory chain containing every manifest file.
   *
   * Paths retain outermost-to-innermost order. The private sidebar-scroll effect
   * uses them only to distinguish a legitimately absent collapsed row from a
   * missing row that violates the manifest-rendering contract.
   */
  function indexAncestorPaths(
    nodes: readonly ManifestNode[],
    ancestors: readonly string[],
  ): void {
    for (const node of nodes) {
      if (node.type === "file") {
        ancestorPathsByFileIndex.set(indexForFile(node), ancestors);
        continue;
      }
      indexAncestorPaths(node.entries, [...ancestors, node.path]);
    }
  }
  indexAncestorPaths(props.tree, []);

  /**
   * Renders one reactive directory row and its currently expanded descendants.
   *
   * The square is the sole expansion button and invokes the shared ChangeSet
   * bulk file action. The separate name button navigates to the directory's
   * first manifest file without selecting, loading, or changing expansion, and
   * remains disabled while that first file is a Husk because a Husk (and most
   * importantly adjacent Husks) does not have stable layout.
   */
  function FileTreeDirectory(rowProps: {
    directory: ManifestDirectory;
    depth: number;
    renderModes: Accessor<FileTreeRenderModes>;
  }): JSX.Element {
    /**
     * Reads the one shared directory-expansion value used by tree and FileCards.
     *
     * An absent entry means initially expanded. The accessor never writes a
     * default into ChangeSet state or retains a second directory authority.
     */
    const expanded = () => {
      const current = props.directoryExpansion().get(rowProps.directory.path);
      if (current === undefined) {
        throw new Error(
          `FileTree is missing reachability for ${rowProps.directory.path}.`,
        );
      }
      return current;
    };
    const directoryFiles = manifestFilesInOrder(rowProps.directory.entries);
    const firstFile = directoryFiles[0];
    if (firstFile === undefined) {
      throw new Error(
        `FileTree directory ${rowProps.directory.path} contains no files.`,
      );
    }
    const statistics = createMemo(() =>
      sumTreeStatistics(directoryFiles.map(stateForFile)),
    );
    return (
      <section class="file-tree-group">
        <div
          class="file-tree-directory"
          style={{ "--file-tree-depth": String(rowProps.depth) }}
        >
          <button
            type="button"
            class="file-tree-visibility-control"
            aria-expanded={expanded()}
            aria-label={
              expanded()
                ? `Collapse ${rowProps.directory.path}`
                : `Expand ${rowProps.directory.path}`
            }
            onClick={() =>
              props.onDirectoryExpandedChange(rowProps.directory, !expanded())
            }
          >
            <TreeVisibilityIndicator visible={expanded()} virtualized={false} />
          </button>
          <button
            type="button"
            class="file-tree-directory-target"
            aria-label={`Go to first file in ${rowProps.directory.path}`}
            disabled={stateForFile(firstFile).state === "husk"}
            onClick={() => navigateToFile(firstFile)}
          >
            {rowProps.directory.name}/
          </button>
          <TreeStatistics stats={statistics()} />
        </div>
        <Show when={expanded()}>
          <div
            class="file-tree-children"
            style={{ "--file-tree-depth": String(rowProps.depth) }}
          >
            <For each={rowProps.directory.entries}>
              {(child) => (
                <FileTreeNode
                  node={child}
                  depth={rowProps.depth + 1}
                  renderModes={rowProps.renderModes}
                />
              )}
            </For>
          </div>
        </Show>
      </section>
    );
  }

  /**
   * Renders one file row from current FileCard and ChangeSet presentation.
   *
   * The row exposes selected-file highlighting and current statistics. A
   * FullFile square invokes the shared file-expansion action; Husk and Lazy
   * markers remain inert. The separate name button invokes scroll-only file
   * navigation and remains disabled while this file is a Husk because a Husk
   * (and most importantly adjacent Husks) does not have stable layout. An
   * expanded FullFile in virtual DOM render mode must display `V` instead of the
   * filled visibility marker.
   */
  function FileTreeFile(rowProps: {
    file: ManifestFile;
    depth: number;
    renderModes: Accessor<FileTreeRenderModes>;
  }): JSX.Element {
    const fileIndex = indexForFile(rowProps.file);
    /**
     * Reads the current FileCard presentation at this immutable manifest index.
     *
     * The accessor preserves ChangeSet's canonical state ordering and must not
     * cache a Husk, Lazy, or Full result across reactive query transitions.
     */
    const state = () => stateForFile(rowProps.file);
    /**
     * Calculates the marker's current rich-body visibility.
     *
     * Husk and Lazy rows always display an empty FileTree marker. FullFile reads
     * the shared expansion authority; this calculation never changes FileCard.
     */
    const expanded = () => {
      const current = state();
      if (current.state !== "full") {
        return false;
      }
      return fileExpanded(rowProps.file, current, props.fileExpansion());
    };
    /**
     * Reports whether an expanded FullFile currently exposes virtual DOM.
     *
     * The value comes only from FileTree's disposable DOM calculation and must
     * not be used to choose or change the owning FileCard's render mode.
     */
    const virtualized = () =>
      expanded() && rowProps.renderModes().get(fileIndex) === "virtual";
    const highlighted = createMemo(
      () => props.selectedFileIndex() === fileIndex,
    );
    const fileKind = rowProps.file.entry.file_kind;
    // This IIFE exists so TypeScript infers the exhaustive switch's result union.
    const fileStatus = (() => {
      switch (fileKind.type) {
        case "git":
          return fileKind.status;
        case "untracked":
          return "untracked";
        default: {
          const unsupported: never = fileKind;
          throw new Error(
            `Unsupported file kind ${JSON.stringify(unsupported)}.`,
          );
        }
      }
    })();
    /**
     * Reports the localized error presentation that must override reason colors.
     *
     * Ordinary Husk and Full states are never error-flavoured by this accessor.
     */
    const isError = () => {
      const current = state();
      return current.state === "lazy" && current.file.kind === "error";
    };
    /**
     * Preserves LazyFile's established border until explicit fetching completes.
     *
     * Manifest-lazy files temporarily render as Husk while their query fetches;
     * they remain visually Lazy in FileTree. A hydrated FullFile deliberately
     * drops the Lazy border while retaining only its approved reason color.
     */
    const styledAsLazy = () => {
      const current = state();
      return (
        current.state === "lazy" ||
        (current.state === "husk" && rowProps.file.entry.lazy !== null)
      );
    };
    /**
     * Resolves the non-error Lazy reason whose color survives FullFile hydration.
     *
     * Error-flavoured LazyFile deliberately returns null so critical error color
     * wins. Full and fetching states fall back to immutable manifest metadata.
     */
    const lazyReason = () => {
      const current = state();
      if (current.state === "lazy" && current.file.kind === "error") {
        return null;
      }
      if (current.state === "lazy" && current.file.kind === "deferred") {
        return current.file.info.lazy;
      }
      return rowProps.file.entry.lazy;
    };
    return (
      <div
        class="file-tree-file"
        data-file-tree-index={fileIndex}
        aria-current={highlighted() ? "true" : undefined}
        classList={{
          "active-hunk-file": highlighted(),
          added: fileStatus === "added",
          removed: fileStatus === "deleted",
          renamed: fileStatus === "renamed",
          untracked: fileStatus === "untracked",
          lazy: styledAsLazy(),
          "lazy-error": isError(),
          "lazy-generated": lazyReason() === "generated",
          "lazy-too-big": lazyReason() === "too_big",
        }}
        style={{ "--file-tree-depth": String(rowProps.depth) }}
        title={fileDisplayName(rowProps.file.entry)}
      >
        <Show
          when={state().state === "full"}
          fallback={
            <span class="file-tree-visibility-marker">
              <TreeVisibilityIndicator visible={false} virtualized={false} />
            </span>
          }
        >
          <button
            type="button"
            class="file-tree-visibility-control"
            aria-expanded={expanded()}
            aria-label={
              expanded()
                ? `Collapse ${fileDisplayName(rowProps.file.entry)}`
                : `Expand ${fileDisplayName(rowProps.file.entry)}`
            }
            onClick={() =>
              props.onFileExpandedChange(rowProps.file, !expanded())
            }
          >
            <TreeVisibilityIndicator
              visible={expanded() && !virtualized()}
              virtualized={virtualized()}
            />
          </button>
        </Show>
        <button
          type="button"
          class="file-tree-file-target"
          aria-label={`Go to ${fileDisplayName(rowProps.file.entry)}`}
          disabled={state().state === "husk"}
          onClick={() => navigateToFile(rowProps.file)}
        >
          <span class="file-tree-file-name">{rowProps.file.name}</span>
          <TreeStatistics stats={treeStatistics(state())} />
        </button>
      </div>
    );
  }

  /**
   * Dispatches one immutable manifest node to its reactive row component.
   *
   * Recursion preserves exact backend order and directory depth. The dispatcher
   * stores no state and exists only as the structural boundary shared by the root
   * and nested directory lists.
   */
  function FileTreeNode(nodeProps: {
    node: ManifestNode;
    depth: number;
    renderModes: Accessor<FileTreeRenderModes>;
  }): JSX.Element {
    if (nodeProps.node.type === "directory") {
      return (
        <FileTreeDirectory
          directory={nodeProps.node}
          depth={nodeProps.depth}
          renderModes={nodeProps.renderModes}
        />
      );
    }
    return (
      <FileTreeFile
        file={nodeProps.node}
        depth={nodeProps.depth}
        renderModes={nodeProps.renderModes}
      />
    );
  }

  /**
   * Maintains the open FileTree DOM calculation and private highlighted-row scroll.
   *
   * Mounting scans the authoritative stable FileCards and observes only local
   * render-mode attribute changes. Disposal disconnects the observer and drops
   * the map. The selection effect changes only this component's scroll container
   * and treats rows below collapsed ancestors as legitimately absent.
   */
  function FileTreeContent(): JSX.Element {
    let groups!: HTMLDivElement;
    const [renderModes, setRenderModes] = createSignal<FileTreeRenderModes>(
      new Map(),
    );
    const highlightedFileIndex = createMemo(() => props.selectedFileIndex());

    /**
     * Reports whether the highlighted row currently has every ancestor mounted.
     *
     * This memo deliberately reduces the complete directory-expansion map to
     * one boolean for the highlighted file. Unrelated Husk-to-Full state changes
     * may replace that map, but an unchanged boolean must not make the private
     * scrolling effect overwrite the user's manual FileTree scroll position.
     */
    const highlightedRowReachable = createMemo(() => {
      const fileIndex = highlightedFileIndex();
      if (fileIndex === null) {
        return false;
      }
      if (!Number.isInteger(fileIndex) || fileIndex < 0) {
        throw new Error("Selected hunk display has an invalid file index.");
      }
      const ancestorPaths = ancestorPathsByFileIndex.get(fileIndex);
      if (ancestorPaths === undefined) {
        throw new Error("FileTree is missing the selected manifest file.");
      }
      const expansion = props.directoryExpansion();
      for (const path of ancestorPaths) {
        const expanded = expansion.get(path);
        if (expanded === undefined) {
          throw new Error(`FileTree is missing reachability for ${path}.`);
        }
        if (!expanded) {
          return false;
        }
      }
      return true;
    });

    onMount(() => {
      const root = props.changeSetRoot();
      if (!root.isConnected) {
        throw new Error("FileTree requires a mounted ChangeSet root.");
      }

      /**
       * Reads current FullFile render modes from authoritative stable FileCards.
       *
       * Missing mode attributes are valid for Husk, Lazy, and failed renderer
       * states. Present attributes and indices must be exact; malformed or
       * duplicate values are DOM-contract violations and throw immediately.
       */
      function readRenderModes(): FileTreeRenderModes {
        const modes = new Map<number, "rich" | "virtual">();
        for (const card of root.querySelectorAll<HTMLElement>(
          "[data-file-card][data-file-index]",
        )) {
          const mode = card.dataset.fileRender;
          if (mode === undefined) {
            continue;
          }
          if (mode !== "rich" && mode !== "virtual") {
            throw new Error(`FileCard exposed invalid render mode ${mode}.`);
          }
          const indexText = card.dataset.fileIndex;
          if (indexText === undefined || !/^\d+$/.test(indexText)) {
            throw new Error("FileCard exposed an invalid manifest index.");
          }
          const fileIndex = Number(indexText);
          if (fileIndex >= files.length) {
            throw new Error("FileCard render mode is outside the manifest.");
          }
          if (modes.has(fileIndex)) {
            throw new Error("FileTree found duplicate stable FileCards.");
          }
          modes.set(fileIndex, mode);
        }
        return modes;
      }

      setRenderModes(readRenderModes());
      const observer = new MutationObserver(() => {
        try {
          setRenderModes(readRenderModes());
        } catch (error) {
          observer.disconnect();
          toast.showError(
            "Could not update file tree render modes",
            error instanceof Error
              ? error
              : new Error(
                  "FileTree render-mode calculation threw a non-Error value.",
                ),
          );
        }
      });
      observer.observe(root, {
        subtree: true,
        attributes: true,
        attributeFilter: ["data-file-render"],
      });
      onCleanup(() => observer.disconnect());
    });

    // This effect exists only for mounted, open FileTreeContent. Its memos keep
    // same-file hunk and unrelated directory-state changes from rerunning it;
    // selected-ancestor reachability changing to true reveals the remounted row.
    // It changes only `.file-tree-groups.scrollTop` and dies when the open
    // content unmounts.
    createEffect(() => {
      if (!props.open) {
        return;
      }
      const fileIndex = highlightedFileIndex();
      if (fileIndex === null) {
        return;
      }
      if (!highlightedRowReachable()) {
        return;
      }
      const row = groups.querySelector<HTMLElement>(
        `[data-file-tree-index="${fileIndex}"]`,
      );
      if (row === null) {
        throw new Error("FileTree did not render the selected manifest file.");
      }
      const containerRect = groups.getBoundingClientRect();
      const rowRect = row.getBoundingClientRect();
      if (rowRect.top < containerRect.top) {
        groups.scrollTop -= containerRect.top - rowRect.top;
      } else if (rowRect.bottom > containerRect.bottom) {
        groups.scrollTop += rowRect.bottom - containerRect.bottom;
      }
    });

    return (
      <aside
        id="fileTreeSidebar"
        class="file-tree-sidebar"
        aria-label="Changed file tree"
      >
        <div ref={groups} class="file-tree-groups">
          <For each={props.tree}>
            {(node) => (
              <FileTreeNode node={node} depth={0} renderModes={renderModes} />
            )}
          </For>
        </div>
      </aside>
    );
  }

  return (
    <Show when={files.length > 0 || props.view === "inline"}>
      <div
        class="file-tree-shell"
        classList={{
          open: props.open,
          "file-tree-shell-inline": props.view === "inline",
        }}
      >
        <Show when={props.open}>
          <FileTreeContent />
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
          <span class="file-tree-label">Files</span>
          <Show when={props.open}>
            <TreeStatistics stats={sumTreeStatistics(props.states())} />
          </Show>
          <kbd>t</kbd>
        </button>
      </div>
    </Show>
  );
}

/**
 * Renders current automatic progress, current failures, and one slow indicator.
 *
 * The compact region contributes no title or long path to AppHeader layout. Once
 * automatic work succeeds with no failures it renders nothing and relinquishes
 * its physical space.
 */
function AppHeaderFileStatus(props: { state: FileSequenceState }): JSX.Element {
  /**
   * Reports whether compact file-lane status currently has visible information.
   *
   * Visible automatic progress, localized failures, or an active slow marker
   * keep the region mounted. Activity without one of those children must not
   * create an empty bordered status group.
   */
  const visible = () => {
    if (props.state.processed < props.state.automaticTotal) {
      return true;
    }
    if (props.state.failed > 0) {
      return true;
    }
    return props.state.state === "loading" && props.state.active.slow;
  };
  return (
    <Show when={visible()}>
      <div
        class="summary-group summary-group-loaded-files change-set-file-status"
        aria-live="polite"
      >
        <Show when={props.state.processed < props.state.automaticTotal}>
          <span class="file-progress">
            <LoaderCircle
              class="file-state-spinner is-spinning"
              aria-hidden="true"
            />
            {props.state.processed}/{props.state.automaticTotal}
          </span>
        </Show>
        <Show when={props.state.failed > 0}>
          <span
            class="file-failure-count"
            aria-label={`${props.state.failed} files failed to load`}
            title={`${props.state.failed} files failed to load`}
          >
            <CircleAlert aria-hidden="true" /> {props.state.failed}
          </span>
        </Show>
        <Show
          when={
            props.state.state === "loading" && props.state.active.slow
              ? props.state.active
              : null
          }
          keyed
        >
          {(slowFile) => (
            <button
              type="button"
              class="app-header-slow-file"
              aria-label={`${slowFile.path} is taking longer than expected`}
              title={`${slowFile.path} is taking longer than expected`}
            >
              <Clock3 aria-hidden="true" />
            </button>
          )}
        </Show>
      </div>
    </Show>
  );
}

/**
 * Renders immutable manifest-level file, line, and optional cell aggregates.
 *
 * The component reads only ManifestSummary. It never accumulates loaded
 * FullFile summaries or announces itself as changing sequence progress.
 */
function ManifestStatistics(props: { summary: ManifestSummary }): JSX.Element {
  /**
   * Returns one required cell aggregate when the cell summary is present.
   *
   * The backend schema permits each field to be independently null, but a
   * visible Cells group requires all three metrics. Missing data is therefore a
   * response-contract error rather than an empty label.
   */
  function cellMetric(
    key: "added_cells" | "modified_cells" | "removed_cells",
  ): number {
    const value = props.summary[key];
    if (value === null) {
      throw new Error(`Manifest summary is missing ${key}.`);
    }
    return value;
  }

  return (
    <>
      <div class="summary-group summary-group-files">
        <strong>Files</strong>
        <span class="delta added">+ {props.summary.added_files}</span>
        <span class="delta changed">~ {props.summary.updated_files}</span>
        <span class="delta removed">- {props.summary.removed_files}</span>
      </div>
      <div class="summary-group summary-group-lines">
        <strong>Lines</strong>
        <span class="delta added">
          +{" "}
          {props.summary.added_lines === null ? "?" : props.summary.added_lines}
        </span>
        <span class="delta removed">
          -{" "}
          {props.summary.removed_lines === null
            ? "?"
            : props.summary.removed_lines}
        </span>
      </div>
      <Show when={props.summary.changed_cells !== null}>
        <div class="summary-group summary-group-cells">
          <strong>Cells</strong>
          <span class="delta added">+ {cellMetric("added_cells")}</span>
          <span class="delta changed">~ {cellMetric("modified_cells")}</span>
          <span class="delta removed">- {cellMetric("removed_cells")}</span>
        </div>
      </Show>
    </>
  );
}

/**
 * Returns manifest leaves in exact depth-first backend order.
 *
 * The returned array is a derived calculation. It contains original ManifestFile
 * objects and must not be sorted, mutated, or retained as another authority.
 */
function manifestFilesInOrder(nodes: readonly ManifestNode[]): ManifestFile[] {
  const files: ManifestFile[] = [];
  for (const node of nodes) {
    if (node.type === "file") {
      files.push(node);
    } else {
      files.push(...manifestFilesInOrder(node.entries));
    }
  }
  return files;
}

/**
 * Derives the exact ChangeSet title used by the established frontend.
 *
 * Complete selected DiffParams choose product wording while manifest labels
 * provide authoritative ref display. The function never mutates URL or requests.
 */
function changeSetTitle(params: DiffParams, manifest: Manifest): string {
  switch (params.tab) {
    case "head":
      return "Working tree vs HEAD";
    case "refs":
      return `${manifest.left_label} vs ${manifest.right_label}`;
    case "branch-review": {
      const base =
        params.base_selection.source === "remote"
          ? `${params.base_selection.remote}/${params.base_selection.branch}`
          : params.base_selection.branch;
      const review =
        params.review_selection.source === "remote"
          ? `${params.review_selection.remote}/${params.review_selection.branch}`
          : params.review_selection.branch;
      return `${review} vs ${base}`;
    }
    case "pull-request":
      return `${manifest.right_label} vs ${manifest.left_label}`;
    case "preset": {
      const kind =
        params.project_id === "gumtree"
          ? "GumTree"
          : `${params.project_id.charAt(0).toUpperCase()}${params.project_id.slice(1)}`;
      return `${kind} preset ${params.preset_subset}`;
    }
  }
}

/**
 * Resolves one file's expansion from explicit ChangeSet state or FullFile data.
 *
 * Explicit user state always wins. LazyFiles begin expanded because their body
 * contains the only explicit-load affordance. A first FullFile result supplies
 * its backend default expansion; queued HuskFiles remain collapsed.
 */
function fileExpanded(
  file: ManifestFile,
  state: FileState,
  expansion: Readonly<Record<string, boolean | undefined>>,
): boolean {
  const selected = expansion[manifestEntryKey(file.entry)];
  if (selected !== undefined) {
    return selected;
  }
  if (state.state === "lazy") {
    return true;
  }
  if (state.state === "full") {
    return state.backend_data.default_expanded;
  }
  return false;
}

/**
 * Derives FileTree line statistics from one shared FileCard state.
 *
 * Full and deferred values expose only their actual backend fields. Husk and
 * error states remain unknown rather than manufacturing zeros.
 */
function treeStatistics(state: FileState): TreeLineStats {
  if (state.state === "full") {
    return {
      added: state.backend_data.summary.added_lines,
      modified: state.backend_data.summary.modified_lines,
      removed: state.backend_data.summary.removed_lines,
      moved: state.backend_data.summary.moved_lines,
    };
  }
  if (state.state === "lazy" && state.file.kind === "deferred") {
    return {
      added: state.file.info.added_lines,
      modified: null,
      removed: state.file.info.removed_lines,
      moved: null,
    };
  }
  return { added: null, modified: null, removed: null, moved: null };
}

/**
 * Adds progressive FileTree statistics without converting unknowns to numbers.
 *
 * Each metric is null when any participating file lacks it; otherwise exact
 * values are summed. The aggregate is presentation-only and never cached.
 */
function sumTreeStatistics(states: readonly FileState[]): TreeLineStats {
  const stats = states.map(treeStatistics);
  /**
   * Sums one statistic only when every contributing file knows its value.
   *
   * Null propagates as unknown instead of becoming zero, preserving progressive
   * FileTree semantics for queued and failed files.
   */
  const sum = (values: (number | null)[]): number | null => {
    let total = 0;
    for (const value of values) {
      if (value === null) {
        return null;
      }
      total += value;
    }
    return total;
  };
  return {
    added: sum(stats.map((value) => value.added)),
    modified: sum(stats.map((value) => value.modified)),
    removed: sum(stats.map((value) => value.removed)),
    moved: sum(stats.map((value) => value.moved)),
  };
}

/**
 * Renders the four established progressive statistics in one FileTree row.
 *
 * Unknown values remain question marks in the tree only. File headers use their
 * own stricter omission rules and do not call this component.
 */
function TreeStatistics(props: { stats: TreeLineStats }): JSX.Element {
  return (
    <span class="file-tree-line-stats">
      <span class="added">+ {props.stats.added ?? "?"}</span>
      <span class="changed">~ {props.stats.modified ?? "?"}</span>
      <span class="removed">- {props.stats.removed ?? "?"}</span>
      <span class="moved">* {props.stats.moved ?? "?"}</span>
    </span>
  );
}

/**
 * Renders one inert FileTree expansion or local virtualization marker.
 *
 * `visible` produces the established filled square and `virtualized` produces
 * `V`. Callers must not set both. The marker has no interaction, accessible
 * name, expansion state, or virtualization decision.
 */
function TreeVisibilityIndicator(props: {
  visible: boolean;
  virtualized: boolean;
}): JSX.Element {
  const virtualized = createMemo(() => {
    if (props.visible && props.virtualized) {
      throw new Error("FileTree marker cannot be both rich and virtual.");
    }
    return props.virtualized;
  });
  return (
    <span
      class="visibility-indicator small"
      classList={{
        visible: props.visible,
        virtualized: virtualized(),
      }}
      aria-hidden="true"
    >
      {virtualized() ? "V" : ""}
    </span>
  );
}
