/**
 * Implements one selected ChangeSet's backend observation, file lane, and presentation.
 *
 * The module exports ChangeSet. The lightweight outer ChangeSet stores FileTree
 * visibility, file expansion, and local Help/Debug state. Each mounted
 * ChangeSetShell stores its HunkDisplay signal. Active ChangeSetContent observes
 * the manifest, while ChangeSetSnapshot stores the lazy-info, file, and profile-
 * preference observers together with file-lane state.
 * Together they render Navigation, hotkeys, HUD, Portals, title, FileTree, and
 * FileCards. They must not copy backend results into Solid state, start concurrent
 * file-diff requests, store workspace or Tab selections, follow user scrolling,
 * or handle line pins.
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
  requestCallback,
  type Accessor,
  type JSX,
} from "solid-js";
import { createStore, type SetStoreFunction } from "solid-js/store";
import { Portal } from "solid-js/web";
import {
  createQueries,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { isCancelledError, type QueryKey } from "@tanstack/query-core";
import { CircleAlert, Clock3, LoaderCircle } from "lucide-solid";
import {
  api,
  isRepositoryCacheExpiration,
  type DiffParams,
  type FileDiff,
  type FileDiffTimeout,
  type LazyInfoFile,
  type Manifest,
  type ManifestDirectory,
  type ManifestFile,
  type ManifestNode,
  type ManifestSummary,
} from "../api/api";
import {
  ErrorPanel,
  RetryButton,
  UnexpectedErrorBoundary,
  useToasts,
} from "../comp/Toasts";
import type { DiffViewMode } from "./App";
import type { AppHeaderOutlets } from "./AppHeader";
import { FileCard, type HunkPosition } from "./FileCard";
import { NavigationProvider, useNavigation } from "./navigation";
import type { StoredProfile } from "./Profile";

const SLOW_FILE_THRESHOLD_MS = 8_000;

/**
 * Yields one async continuation through Solid's cooperative scheduler.
 *
 * Callers await the returned Promise to end the current rendering loop turn and
 * let the main thread process browser work before continuing. The scheduled
 * callback has no side effects and requires no cancellation lifecycle.
 */
function schedulerYield(): Promise<void> {
  return new Promise((resolve) => {
    requestCallback(resolve);
  });
}

/**
 * Defines every complete input needed to identify and activate one ChangeSet.
 *
 * `params` is a selected complete DiffParams value, `view` is the global reactive
 * renderer input, `profile` is genuine nullable profile identity, and `active`
 * controls expensive observation. The view callback reports a direct workspace
 * action. No field may represent live control input.
 */
type ChangeSetProps = {
  active: boolean;
  params: DiffParams;
  view: DiffViewMode;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  onToggleView: () => void;
};

/**
 * Contains all lightweight client state that survives inactive Tab periods.
 *
 * Expansion keys are manifest paths. Backend files, query state, progress, hunk
 * selection, and renderer rows are deliberately excluded from this store.
 */
type ChangeSetState = {
  treeOpen: boolean;
  fileExpansion: Record<string, boolean | undefined>;
};

/**
 * Describes the active work presented by the single file lane.
 *
 * `kind` distinguishes ordinary manifest progress from an explicit LazyFile
 * selection. Slow is a one-shot threshold flag rather than elapsed-time state.
 */
type FileLaneActivity = {
  kind: "sequence" | "selected";
  fileIndex: number;
  path: string;
  slow: boolean;
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
 * outer layout and local HUD state survive inactive periods and are destroyed
 * with the selected Tab value or workspace reset.
 */
export function ChangeSet(props: ChangeSetProps): JSX.Element {
  const [helpOpen, setHelpOpen] = createSignal(false);
  const [debugOpen, setDebugOpen] = createSignal(false);
  const [state, setState] = createStore<ChangeSetState>({
    treeOpen: false,
    fileExpansion: {},
  });
  // JSX may preserve the params object's identity while making its fields
  // reactive. Materialize every identity field so complete DiffParams changes,
  // including engine and nested branch selections, replace active content.
  const params = createMemo<DiffParams>(() => {
    const current = props.params;
    switch (current.mode) {
      case "head":
        return {
          project_id: current.project_id,
          engine: current.engine,
          mode: current.mode,
          left: current.left,
          right: current.right,
          show_untracked: current.show_untracked,
        };
      case "refs":
        return {
          project_id: current.project_id,
          engine: current.engine,
          mode: current.mode,
          left: current.left,
          right: current.right,
        };
      case "branch-review":
        return {
          project_id: current.project_id,
          engine: current.engine,
          mode: current.mode,
          base_selection:
            current.base_selection.source === "local"
              ? {
                  source: current.base_selection.source,
                  branch: current.base_selection.branch,
                }
              : {
                  source: current.base_selection.source,
                  remote: current.base_selection.remote,
                  branch: current.base_selection.branch,
                },
          review_selection:
            current.review_selection.source === "local"
              ? {
                  source: current.review_selection.source,
                  branch: current.review_selection.branch,
                }
              : {
                  source: current.review_selection.source,
                  remote: current.review_selection.remote,
                  branch: current.review_selection.branch,
                },
        };
      case "preset":
        return {
          project_id: current.project_id,
          engine: current.engine,
          mode: current.mode,
          preset_subset: current.preset_subset,
        };
    }
  });

  return (
    <Show when={props.active ? params() : null} keyed>
      {(activeParams) => (
        <UnexpectedErrorBoundary title="Could not render ChangeSet">
          <ChangeSetContent
            params={activeParams}
            view={props.view}
            profile={props.profile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
            helpOpen={helpOpen()}
            onHelpOpenChange={setHelpOpen}
            debugOpen={debugOpen()}
            onDebugOpenChange={setDebugOpen}
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
  view: DiffViewMode;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  onToggleView: () => void;
  helpOpen: boolean;
  onHelpOpenChange: (open: boolean) => void;
  debugOpen: boolean;
  onDebugOpenChange: (open: boolean) => void;
  state: ChangeSetState;
  setState: SetStoreFunction<ChangeSetState>;
};

/**
 * Observes one active manifest and replaces its complete rendered snapshot.
 *
 * The component exists only for one immutable DiffParams value. It mounts no
 * manifest-dependent observer before manifest success, disposes the current
 * snapshot before reload or cache-expiration refetch, and never retains an old
 * manifest merely because TanStack still exposes its previous data.
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
   * the manifest refetch begins. Concurrent expiration indications share this exact
   * operation and cannot start parallel replacement manifests.
   */
  async function replaceSnapshot(resetLayout: boolean): Promise<void> {
    if (replacement !== null) {
      await replacement;
      return;
    }
    const stopPromise =
      stopFileSequence === null ? Promise.resolve() : stopFileSequence();
    setReplacingSnapshot(true);
    if (resetLayout) {
      props.setState({
        treeOpen: false,
        fileExpansion: {},
      });
    }
    const currentReplacement = (async () => {
      await stopPromise;
      await manifest.refetch();
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
            debugOpen={props.debugOpen}
            onToggleTree={() => {
              // FileTree visibility belongs to the lightweight outer ChangeSet store.
              props.setState("treeOpen", (current) => !current);
            }}
            onToggleView={props.onToggleView}
            onReload={() => {
              // Reload replaces the complete snapshot and resets outer layout state.
              void replaceSnapshot(true);
            }}
            onToggleHelp={() => props.onHelpOpenChange(!props.helpOpen)}
            onHelpOpenChange={props.onHelpOpenChange}
            onToggleDebug={() => props.onDebugOpenChange(!props.debugOpen)}
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
                        <RetryButton onRetry={() => replaceSnapshot(true)} />
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
            debugOpen={props.debugOpen}
            onToggleTree={() => {
              props.setState("treeOpen", (current) => !current);
            }}
            onToggleView={props.onToggleView}
            onReload={() => {
              void replaceSnapshot(true);
            }}
            onToggleHelp={() => props.onHelpOpenChange(!props.helpOpen)}
            onHelpOpenChange={props.onHelpOpenChange}
            onToggleDebug={() => props.onDebugOpenChange(!props.debugOpen)}
          >
            {(hunkDisplay) => (
              <UnexpectedErrorBoundary title="Could not render ChangeSet snapshot">
                <ChangeSetSnapshot
                  params={props.params}
                  manifest={snapshot}
                  view={props.view}
                  profile={props.profile}
                  appHeaderOutlets={props.appHeaderOutlets}
                  hunkDisplay={hunkDisplay}
                  state={props.state}
                  setState={props.setState}
                  onRepositoryCacheExpiration={() => {
                    void replaceSnapshot(false);
                  }}
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
  return (
    <section ref={root} class="change-set-root" data-change-set-root>
      <NavigationProvider root={() => root}>
        <Hotkeys
          onToggleTree={props.onToggleTree}
          onToggleView={props.onToggleView}
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
  let frame = 0;
  let sampleStartedAt = performance.now();
  let sampleFrames = 0;
  let displayUpdatedAt = sampleStartedAt;
  let currentFps = 0;

  onMount(() => {
    /**
     * Samples current visible document metrics for the open Debug HUD.
     *
     * The active animation-frame loop calls this at its display cadence. It
     * replaces only Debug HUD metrics and neither changes ChangeSet behavior nor
     * retains DOM nodes between samples.
     */
    function updateMetrics(): void {
      setMetrics({
        fps: currentFps ? String(Math.round(currentFps)) : "--",
        nodes: document.querySelectorAll("*").length.toLocaleString(),
        spans: document.querySelectorAll("span").length.toLocaleString(),
      });
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
        updateMetrics();
        displayUpdatedAt = now;
      }
      frame = requestAnimationFrame(tick);
    }

    updateMetrics();
    frame = requestAnimationFrame(tick);
    onCleanup(() => cancelAnimationFrame(frame));
  });

  return (
    <div class="debug-hud" aria-label="Developer metrics">
      <DebugMetric label="FPS" value={metrics().fps} />
      <DebugMetric label="Nodes" value={metrics().nodes} />
      <DebugMetric label="Spans" value={metrics().spans} />
      <DebugMetric
        label="Hunks"
        value={
          props.globalSelectedHunk() === null
            ? "--/--"
            : `${props.globalSelectedHunk()?.position.current ?? "—"}/${
                props.globalSelectedHunk()?.position.total ?? 0
              }${props.globalSelectedHunk()?.hasMore === true ? "+" : ""}`
        }
      />
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
        stablePositionOffset += targets.length;
        participatingTotal += localTotal;
      });

      if (selectedFileIndex === null || selectedCurrent === null) {
        throw new Error("Selected hunk has no calculable DOM position.");
      }
      return {
        selectedFileIndex,
        globalSelectedHunk: {
          position: { current: selectedCurrent, total: participatingTotal },
          hasMore,
        },
        fileSelectedHunks,
      };
    }

    let alive = true;
    let observer: MutationObserver | null = null;
    let calculationQueued = false;
    onCleanup(() => {
      alive = false;
      observer?.disconnect();
    });

    /**
     * Coalesces one complete display calculation after renderer mount work.
     *
     * DiffGrid places its imperative row targets during mount after FileCard has
     * updated `data-hunk-set`. The extra microtask observes the completed renderer
     * operation instead of interpreting that internal transition as invalid DOM.
     */
    function queueDisplayCalculation(): void {
      if (calculationQueued) {
        return;
      }
      calculationQueued = true;
      queueMicrotask(() => {
        calculationQueued = false;
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
          props.onDisplayChange(calculateDisplay());
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
      observer = new MutationObserver(() => {
        queueDisplayCalculation();
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
      queueDisplayCalculation();
    });
  });
  return null;
}

/**
 * Renders the ChangeSet hotkey reference as the established modal overlay.
 *
 * Callers provide explicit visibility and an update callback. Available hunk and
 * Debug operations are enabled, unavailable operations are disabled, and removed
 * file-wide expansion operations are absent. Backdrop and Close actions report
 * `false` through that callback.
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
            <HotkeyHelpRow
              keys="n"
              label="Go to the next hunk"
              disabled={false}
            />
            <HotkeyHelpRow
              keys="N"
              label="Go to the previous hunk"
              disabled={false}
            />
            <HotkeyHelpRow keys="p" label="Go to the top" disabled={false} />
          </HotkeyHelpSection>
          <HotkeyHelpSection title="UI">
            <HotkeyHelpRow
              keys="t"
              label="Toggle the file tree"
              disabled={false}
            />
            <HotkeyHelpRow
              keys="i"
              label="Toggle inline diff view"
              disabled={false}
            />
          </HotkeyHelpSection>
          <HotkeyHelpSection title="Misc">
            <HotkeyHelpRow
              keys="r"
              label="Reload the current diff"
              disabled={false}
            />
            <HotkeyHelpRow
              keys="d"
              label="Toggle developer metrics"
              disabled={false}
            />
            <HotkeyHelpRow
              keys="h"
              label="Toggle this help panel"
              disabled={false}
            />
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
 * Presents one hotkey and label with explicit availability.
 *
 * Disabled rows remain visible, gray, and semantically disabled. The row is
 * descriptive rather than interactive and never invokes the represented action.
 */
function HotkeyHelpRow(props: {
  keys: string;
  label: string;
  disabled: boolean;
}): JSX.Element {
  return (
    <div
      class="help-hud-row"
      classList={{ "is-disabled": props.disabled }}
      aria-disabled={props.disabled}
    >
      <kbd>{props.keys}</kbd>
      <span>{props.label}</span>
    </div>
  );
}

/**
 * Defines every immutable backend input and reactive presentation input for one snapshot.
 *
 * Params and manifest never change during this component lifetime. View, profile,
 * and durable outer layout state remain reactive without retargeting backend work.
 * The callbacks expose only the two lifecycle actions performed by ChangeSetContent.
 */
type ChangeSetSnapshotProps = {
  params: DiffParams;
  manifest: Manifest;
  view: DiffViewMode;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  hunkDisplay: Accessor<HunkDisplay | null>;
  state: ChangeSetState;
  setState: SetStoreFunction<ChangeSetState>;
  onRepositoryCacheExpiration(): void;
  onFileSequenceChange(stop: (() => Promise<void>) | null): void;
};

/**
 * Owns every observer, lane value, derivation, and rendered node for one manifest.
 *
 * All query definitions use the same immutable params and manifest cache ID. The
 * component reports cache expiration to ChangeSetContent, while ordinary file failures
 * remain localized. Disposal cancels the lane and releases every snapshot query.
 */
function ChangeSetSnapshot(props: ChangeSetSnapshotProps): JSX.Element {
  const queryClient = useQueryClient();
  const [processed, setProcessed] = createSignal(0);
  const [laneActivity, setLaneActivity] = createSignal<FileLaneActivity | null>(
    null,
  );
  const [laneError, setLaneError] = createSignal<Error | null>(null);
  let changeSetRoot!: HTMLDivElement;
  let enqueueSelectedFile:
    | ((fileIndex: number, timeout: FileDiffTimeout) => void)
    | null = null;
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
  const automaticTotal = createMemo(
    () => orderedFiles.filter((file) => file.entry.lazy === null).length,
  );

  // The manifest is immutable for this component, so this setup-time branch
  // creates exactly one lazy-info observer when the entity exists and none when
  // it does not. It is not a reactive zero-or-one observer collection.
  const lazyInfo = manifestContainsLazyFiles(props.manifest.tree)
    ? createQuery(() =>
        api.changeSet.lazyInfo(props.params, props.manifest.cache_id),
      )
    : null;

  if (lazyInfo !== null) {
    // This effect has one lifecycle purpose: translate the asynchronous backend
    // expiration indication into disposal of this immutable snapshot. It neither
    // copies query data nor repairs file state, and dies with the snapshot.
    createEffect(() => {
      if (isRepositoryCacheExpiration(lazyInfo.error)) {
        props.onRepositoryCacheExpiration();
      }
    });
  }

  const fileQueries = createQueries(() => ({
    queries: orderedFiles.map((file) => ({
      ...api.changeSet.file(
        props.params,
        props.manifest.cache_id,
        file.entry,
        "bounded",
      ),
      enabled: false,
    })),
  }));

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

  const lazyInfoByKey = createMemo(() => {
    const result = new Map<string, LazyInfoFile>();
    const query = lazyInfo;
    if (query === null || query.data === undefined) {
      return result;
    }
    const expected = new Map<string, ManifestFile>();
    for (const file of orderedFiles) {
      if (file.entry.lazy === null) {
        continue;
      }
      const key = manifestEntryKey(file.entry);
      if (expected.has(key)) {
        throw new Error(
          `Manifest returned duplicate lazy file ${fileDisplayName(file.entry)}.`,
        );
      }
      expected.set(key, file);
    }
    for (const info of query.data.files) {
      const key = manifestEntryKey(info);
      if (!expected.has(key)) {
        throw new Error(
          `Lazy info returned unexpected file ${fileDisplayName(info)}.`,
        );
      }
      if (result.has(key)) {
        throw new Error(
          `Lazy info returned duplicate file ${fileDisplayName(info)}.`,
        );
      }
      if (info.lazy === null) {
        throw new Error(
          `Lazy info omitted the reason for ${fileDisplayName(info)}.`,
        );
      }
      result.set(key, info);
    }
    for (const [key, file] of expected) {
      if (!result.has(key)) {
        throw new Error(
          `Lazy info omitted manifest file ${fileDisplayName(file.entry)}.`,
        );
      }
    }
    return result;
  });

  const fileStates = createMemo(() =>
    orderedFiles.map((manifestFile, fileIndex) => {
      const query = fileQueries[fileIndex];
      const path = fileDisplayName(manifestFile.entry);
      if (query === undefined) {
        return {
          state: "husk" as const,
          fileIndex,
          name: manifestFile.name,
          path,
          activity: "queued" as const,
        };
      }
      if (query.fetchStatus === "fetching") {
        return {
          state: "husk" as const,
          fileIndex,
          name: manifestFile.name,
          path,
          activity: "fetching" as const,
        };
      }
      if (query.isSuccess) {
        return {
          state: "full" as const,
          fileIndex,
          backend_data: query.data,
        };
      }
      if (query.isError) {
        if (isRepositoryCacheExpiration(query.error)) {
          return {
            state: "husk" as const,
            fileIndex,
            name: manifestFile.name,
            path,
            activity: "queued" as const,
          };
        }
        if (!(query.error instanceof Error)) {
          throw new Error(`File query ${path} failed without an Error value.`);
        }
        return {
          state: "lazy" as const,
          fileIndex,
          file: {
            kind: "error" as const,
            name: manifestFile.name,
            path,
            error: query.error,
          },
        };
      }
      if (manifestFile.entry.lazy !== null) {
        const lazyInfoQuery = lazyInfo;
        if (lazyInfoQuery !== null && lazyInfoQuery.isError) {
          if (isRepositoryCacheExpiration(lazyInfoQuery.error)) {
            return {
              state: "husk" as const,
              fileIndex,
              name: manifestFile.name,
              path,
              activity: "queued" as const,
            };
          }
          if (!(lazyInfoQuery.error instanceof Error)) {
            throw new Error(
              `Lazy-info query for ${path} failed without an Error value.`,
            );
          }
          return {
            state: "lazy" as const,
            fileIndex,
            file: {
              kind: "error" as const,
              name: manifestFile.name,
              path,
              error: lazyInfoQuery.error,
            },
          };
        }
        const info = lazyInfoByKey().get(manifestEntryKey(manifestFile.entry));
        if (info !== undefined) {
          return {
            state: "lazy" as const,
            fileIndex,
            file: { kind: "deferred" as const, info },
          };
        }
      }
      return {
        state: "husk" as const,
        fileIndex,
        name: manifestFile.name,
        path,
        activity: "queued" as const,
      };
    }),
  );

  /**
   * Resolves one manifest file to its current canonical presentation state.
   *
   * Directory reachability consumes the same strict manifest/state ordering as
   * FileTree. Missing or duplicate identities violate the immutable snapshot
   * contract and throw instead of inventing a directory-expansion default.
   */
  function stateForManifestFile(file: ManifestFile): FileTreeState {
    const fileIndex = fileIndexByKey.get(manifestEntryKey(file.entry));
    if (fileIndex === undefined) {
      throw new Error(
        `ChangeSet cannot index ${fileDisplayName(file.entry)} for directory reachability.`,
      );
    }
    const state = fileStates()[fileIndex];
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
      fileStates().filter(
        (state) => state.state === "lazy" && state.file.kind === "error",
      ).length,
  );
  const sequenceState = createMemo<FileSequenceState>(() => {
    const active = laneActivity();
    if (active !== null) {
      return {
        state: "loading",
        processed: processed(),
        automaticTotal: automaticTotal(),
        failed: failedFiles(),
        active,
      };
    }
    return {
      state: "ready",
      processed: processed(),
      automaticTotal: automaticTotal(),
      failed: failedFiles(),
    };
  });

  // Synthetic backpressure set to break the rendering loop and let the main thread breathe.
  const [admittedFiles, setAdmittedFiles] = createStore<Record<number, true>>(
    {},
  );

  // This imperative lane is born with one immutable snapshot and dies with it.
  // No effect can retarget its closures to later params, manifests, or queries.
  {
    const params = props.params;
    const snapshot = props.manifest;
    const files = orderedFiles;
    const selectedQueue: Array<{
      fileIndex: number;
      timeout: FileDiffTimeout;
    }> = [];
    const selectedSet = new Set<number>();
    let automaticCursor = 0;
    let activeIndex: number | null = null;
    let activeKey: QueryKey | null = null;
    let running = false;
    let stopped = false;
    let stopPromise: Promise<void> | null = null;

    setProcessed(0);
    setLaneActivity(null);
    setLaneError(null);

    /**
     * Routes an unexpected async lane rejection into a Solid error signal.
     *
     * Promise rejections may legally contain unknown JavaScript values. Real
     * Errors retain their identity and stack; other values become an Error with
     * the original rejection preserved as its cause.
     */
    function reportLaneFailure(error: unknown): void {
      setLaneError(
        error instanceof Error
          ? error
          : new Error("File lane rejected without an Error value.", {
              cause: error,
            }),
      );
    }

    /**
     * Drains explicit selections before resuming strict automatic manifest order.
     *
     * The closure belongs to this exact immutable snapshot. It catches only
     * query-owned failure/cancellation so the canonical observer retains damage;
     * its launch callback routes unexpected orchestration errors into Solid.
     */
    async function runLane(): Promise<void> {
      if (running || stopped) {
        return;
      }
      running = true;
      try {
        while (!stopped) {
          let kind: FileLaneActivity["kind"] = "selected";
          let timeout: FileDiffTimeout = "bounded";
          const selection = selectedQueue.shift();
          let fileIndex: number;
          if (selection !== undefined) {
            fileIndex = selection.fileIndex;
            timeout = selection.timeout;
            selectedSet.delete(fileIndex);
          } else {
            kind = "sequence";
            while (automaticCursor < files.length) {
              const candidate = files[automaticCursor];
              if (candidate === undefined) {
                throw new Error(
                  `File lane lost manifest index ${automaticCursor}.`,
                );
              }
              if (candidate.entry.lazy === null) {
                break;
              }
              automaticCursor += 1;
            }
            if (automaticCursor >= files.length) {
              break;
            }
            fileIndex = automaticCursor;
            automaticCursor += 1;
          }

          const file = files[fileIndex];
          if (file === undefined) {
            throw new Error(`File lane selected invalid index ${fileIndex}.`);
          }
          const options = api.changeSet.file(
            params,
            snapshot.cache_id,
            file.entry,
            timeout,
          );
          const activity: FileLaneActivity = {
            kind,
            fileIndex,
            path: fileDisplayName(file.entry),
            slow: false,
          };
          activeIndex = fileIndex;
          activeKey = options.queryKey;
          setLaneActivity(activity);
          const slowTimer = window.setTimeout(() => {
            if (!stopped && activeIndex === fileIndex) {
              setLaneActivity({ ...activity, slow: true });
            }
          }, SLOW_FILE_THRESHOLD_MS);

          try {
            await queryClient.fetchQuery(options);
            if (stopped) {
              return;
            }
            if (
              kind === "selected" &&
              props.state.fileExpansion[manifestEntryKey(file.entry)] !== false
            ) {
              // The query result has replaced LazyFile with FullFile. Expand it
              // now, but never override a collapse performed while it loaded.
              props.setState(
                "fileExpansion",
                manifestEntryKey(file.entry),
                true,
              );
            }
            await schedulerYield();
            if (stopped) {
              return;
            }
            setAdmittedFiles(fileIndex, true);
          } catch (error) {
            if (stopped || isCancelledError(error)) {
              return;
            }
            if (isRepositoryCacheExpiration(error)) {
              props.onRepositoryCacheExpiration();
              return;
            }
            // The canonical query owns and presents this failed attempt. The lane
            // intentionally proceeds so one file cannot damage later files.
          } finally {
            window.clearTimeout(slowTimer);
            activeIndex = null;
            activeKey = null;
            setLaneActivity(null);
            if (kind === "sequence" && !stopped) {
              setProcessed((current) => current + 1);
            }
          }
        }
      } finally {
        running = false;
      }
    }

    /**
     * Stops this exact immutable snapshot and cancels its active canonical query.
     *
     * The operation is idempotent and returns the same Promise to concurrent
     * callers. It prevents further scheduling synchronously, then waits for the
     * active cancellation before an explicit manifest reload may proceed.
     */
    function stopLane(): Promise<void> {
      if (stopPromise !== null) {
        return stopPromise;
      }
      stopped = true;
      enqueueSelectedFile = null;
      setLaneActivity(null);
      const queryKey = activeKey;
      stopPromise =
        queryKey === null
          ? Promise.resolve()
          : queryClient.cancelQueries({ queryKey, exact: true });
      return stopPromise;
    }

    props.onFileSequenceChange(stopLane);

    enqueueSelectedFile = (fileIndex, timeout) => {
      const file = files[fileIndex];
      if (file === undefined) {
        throw new Error(`Cannot load unknown file index ${fileIndex}.`);
      }
      const query = fileQueries[fileIndex];
      if (
        (query !== undefined && query.isSuccess) ||
        activeIndex === fileIndex
      ) {
        return;
      }
      if (!selectedSet.has(fileIndex)) {
        selectedSet.add(fileIndex);
        selectedQueue.push({ fileIndex, timeout });
      }
      void runLane().catch(reportLaneFailure);
    };

    void runLane().catch(reportLaneFailure);

    onCleanup(() => {
      props.onFileSequenceChange(null);
      void stopLane().catch(reportLaneFailure);
    });
  }

  /**
   * Submits one LazyFile or failed FileCard to the current single file-fetch lane.
   *
   * A mounted snapshot lane and explicit timeout policy are required. The
   * callback never calls refetch or transport directly and cannot bypass
   * sequencing or alter canonical query identity.
   */
  function loadSelectedFile(fileIndex: number, timeout: FileDiffTimeout): void {
    if (enqueueSelectedFile === null) {
      throw new Error("Cannot load a file before its manifest lane exists.");
    }
    enqueueSelectedFile(fileIndex, timeout);
  }

  return (
    <>
      <Show when={laneError()} keyed>
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
          "diff-workspace-tree-open": props.state.treeOpen,
        }}
      >
        <UnexpectedErrorBoundary title="Could not render file tree">
          <FileTree
            changeSetRoot={() => changeSetRoot}
            tree={props.manifest.tree}
            states={fileStates}
            open={props.state.treeOpen}
            view={props.view}
            selectedFileIndex={() =>
              props.hunkDisplay()?.selectedFileIndex ?? null
            }
            directoryExpansion={directoryExpansion}
            fileExpansion={() => props.state.fileExpansion}
            onOpenChange={(open) => props.setState("treeOpen", open)}
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
                    <Show when={fileStates()[fileIndex()]}>
                      {(currentState) => (
                        <FileCard
                          file_state={currentState()}
                          expanded={fileExpanded(
                            file,
                            currentState(),
                            props.state.fileExpansion,
                          )}
                          explicitlyCollapsed={
                            props.state.fileExpansion[
                              manifestEntryKey(file.entry)
                            ] === false
                          }
                          admitted={admittedFiles[fileIndex()] === true}
                          engine={props.params.engine}
                          view={props.view}
                          aggressiveFolds={aggressiveFolds()}
                          globalSelectedHunk={() =>
                            props.hunkDisplay()?.globalSelectedHunk ?? null
                          }
                          fileSelectedHunk={() =>
                            props
                              .hunkDisplay()
                              ?.fileSelectedHunks.get(fileIndex()) ?? null
                          }
                          onExpandedChange={(expanded) =>
                            props.setState(
                              "fileExpansion",
                              manifestEntryKey(file.entry),
                              expanded,
                            )
                          }
                          onLoad={() => {
                            loadSelectedFile(fileIndex(), "bounded");
                          }}
                          onRetry={() => {
                            loadSelectedFile(fileIndex(), "unbounded");
                          }}
                        />
                      )}
                    </Show>
                  );
                }}
              </For>
            </div>
          </Show>
        </section>
      </div>
    </>
  );
}

/**
 * Defines the structural state shape FileTree consumes from ChangeSet.
 *
 * The tree reads presentation only. Query objects, backend fetch controls, and
 * mutable aggregates are excluded from this contract.
 */
type FileTreeState =
  | {
      state: "husk";
      fileIndex: number;
      name: string;
      path: string;
      activity: "queued" | "fetching";
    }
  | { state: "full"; fileIndex: number; backend_data: FileDiff }
  | {
      state: "lazy";
      fileIndex: number;
      file:
        | { kind: "deferred"; info: LazyInfoFile }
        | { kind: "error"; name: string; path: string; error: Error };
    };

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
  stateForFile: (file: ManifestFile) => FileTreeState,
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
 * ChangeSet DOM root, calculated directory reachability, and ChangeSet-owned
 * file expansion. Its callbacks may change only tree visibility or file
 * expansion. FileTree stores no query, backend data, hunk selection, navigation,
 * or independent expansion authority.
 */
type FileTreeProps = {
  changeSetRoot: Accessor<HTMLElement>;
  tree: readonly ManifestNode[];
  states: Accessor<readonly FileTreeState[]>;
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
  const stateForFile = (file: ManifestFile): FileTreeState => {
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
    const fileStatus =
      rowProps.file.entry.file_kind.type === "git"
        ? rowProps.file.entry.file_kind.status
        : "untracked";
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
          <Show when={props.open}>
            <span class="file-tree-label">Files</span>
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
        <span class="delta added">+ {props.summary.added_lines}</span>
        <span class="delta removed">- {props.summary.removed_lines}</span>
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
 * Reports whether a manifest tree contains at least one intentionally lazy file.
 *
 * The predicate preserves traversal semantics and starts no query itself.
 */
function manifestContainsLazyFiles(nodes: ManifestNode[]): boolean {
  for (const node of nodes) {
    if (node.type === "file") {
      if (node.entry.lazy !== null) {
        return true;
      }
    } else if (manifestContainsLazyFiles(node.entries)) {
      return true;
    }
  }
  return false;
}

/**
 * Returns the required visible name for one manifest or lazy-info handle.
 *
 * Renames retain both paths with the established arrow, while side-only entries
 * use their existing path. API validation guarantees that present paths are
 * non-empty; a handle with neither path violates the file identity contract.
 */
function fileDisplayName(entry: {
  left_path: string | null;
  right_path: string | null;
}): string {
  const leftPath = entry.left_path;
  const rightPath = entry.right_path;
  if (leftPath !== null) {
    if (rightPath !== null && rightPath !== leftPath) {
      return `${leftPath} -> ${rightPath}`;
    }
    return leftPath;
  }
  if (rightPath !== null) {
    return rightPath;
  }
  throw new Error("File entry requires a left or right path.");
}

/**
 * Produces one stable manifest-local key from the two canonical file paths.
 *
 * The key distinguishes renames and side-only entries without adding display
 * names or mutable query state. It is used only for expansion and calculations.
 */
function manifestEntryKey(entry: {
  left_path: string | null;
  right_path: string | null;
}): string {
  return `${entry.left_path ?? ""}\u0000${entry.right_path ?? ""}`;
}

/**
 * Derives the exact ChangeSet title used by the established frontend.
 *
 * Complete selected DiffParams choose product wording while manifest labels
 * provide authoritative ref display. The function never mutates URL or requests.
 */
function changeSetTitle(params: DiffParams, manifest: Manifest): string {
  switch (params.mode) {
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
  state: FileTreeState,
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
function treeStatistics(state: FileTreeState): TreeLineStats {
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
      modified:
        state.file.info.added_lines !== null &&
        state.file.info.removed_lines !== null
          ? 0
          : null,
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
function sumTreeStatistics(states: readonly FileTreeState[]): TreeLineStats {
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
