/**
 * Mounts the stable DOM and interaction frame around one active ChangeSet.
 *
 * `ChangeSetShell` keeps one root and Navigation provider mounted while snapshot
 * content changes inside it. It installs the shell-lifetime hotkey and text-side
 * listeners, derives declarative hunk display data from renderer-authored DOM,
 * and hosts the fixed Hint, Debug, and Help overlays.
 *
 * Callers supply every workspace operation and the body renderer. The shell does
 * not observe backend data, select hunks, or retain file expansion.
 */
import {
  Show,
  createMemo,
  createSignal,
  batch,
  type Setter,
  onCleanup,
  onMount,
  type Accessor,
  type JSX,
} from "solid-js";
import { useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { HunkPosition } from "../fileCard/FileCard";
import {
  NavigationProvider,
  storedHunkTarget,
  useNavigation,
} from "../navigation";

/**
 * Mirrors navigation information from the mounted ChangeSet DOM.
 *
 * The snapshot must be exact, but Navigation and selection logic must continue
 * using DOM directly. Consumers format its numeric values and may not mutate it
 * or treat it as an independent source of hunk identity. A terminal renderer
 * failure stops further snapshots rather than publishing an inexact value.
 */
export type HunkDisplay = {
  /** Manifest file index used for declarative FileTree highlighting. */
  selectedFileIndex: number | null;
  /** Global selected position and whether more targets can become available. */
  globalSelectedHunk: {
    /** Stable selected position among all coordinates and current participating total. */
    position: HunkPosition;
    /** True while Husk, Lazy, or collapsed Files can change that total. */
    hasMore: boolean;
  };
  /** Stable per-file readers of local numeric positions, keyed by manifest index. */
  fileSelectedHunks: ReadonlyMap<number, Accessor<HunkPosition>>;
};

/**
 * Defines the complete active UI operations surrounding one ChangeSet body.
 *
 * All callbacks are required and invoke their specified operations. Children are
 * rendered inside the same root served by the scoped Navigation instance.
 */
type ChangeSetShellProps = {
  /** Current local Help visibility supplied by the outer ChangeSet. */
  helpOpen: boolean;
  /** Current workspace DebugHud visibility; false unmounts its sampling loop. */
  debugOpen: boolean;
  /**
   * Runs the FileTree visibility toggle for `t` or the shell control.
   * The caller changes workspace state and supplies the accepted value to the
   * nested FileTree; no hunk or file action invokes it.
   */
  onToggleTree: () => void;
  /**
   * Runs the inline/split workspace toggle for `i`.
   * The caller stores the new mode; mounted descendants receive it only after
   * this callback completes and Solid propagates the updated prop.
   */
  onToggleView: () => void;
  /**
   * Runs the ChangeSet-local History toggle for `m`.
   * Review controls may change the same state through their separate boolean
   * callback, but navigation and display observation never invoke this one.
   */
  onToggleHistory: () => void;
  /**
   * Starts explicit snapshot replacement for `r`.
   * The caller stops the old lane, clears file expansion, and refetches; this
   * callback returns before its asynchronous replacement necessarily settles.
   */
  onReload: () => void;
  /**
   * Runs the local Help toggle for `h` or the HintHud button.
   * The caller stores the new value and returns it through `helpOpen`.
   */
  onToggleHelp: () => void;
  /**
   * Replaces Help visibility with `open` after modal backdrop or Close actions.
   * The caller stores the accepted boolean and returns it through `helpOpen`;
   * opening from HintHud uses `onToggleHelp` instead.
   */
  onHelpOpenChange: (open: boolean) => void;
  /**
   * Runs the workspace DebugHud toggle for `d`.
   * The caller stores the accepted value and returns it through `debugOpen`;
   * metric sampling and unmount cleanup never invoke this callback.
   */
  onToggleDebug: () => void;
  /**
   * Toggles the AppHeader engine options popover for `o`.
   * The native popover holds its own open state, so nothing returns through
   * props; the caller forwards to the header outlet.
   */
  onToggleOptions: () => void;
  /**
   * Renders the current ChangeSet body inside the shell's NavigationProvider.
   *
   * The shell invokes it during render with its read-only `HunkDisplay`
   * accessor. The child may use that mirror for presentation but must not
   * select or navigate from it. Snapshot replacement re-renders the body while
   * the root and accessor identity remain mounted.
   */
  children: (hunkDisplay: Accessor<HunkDisplay | null>) => JSX.Element;
};

/**
 * Binds one visible ChangeSet body, hotkey listener, and HUD to one DOM root.
 *
 * The shell survives snapshot replacement inside its body; every mounted
 * snapshot writes its own initial hunk selection. Loading and error bodies
 * retain shell operations but contain no targets. The wrapper has no layout
 * box and stores no backend state.
 */
export function ChangeSetShell(props: ChangeSetShellProps): JSX.Element {
  let root!: HTMLElement;
  const [hunkDisplay, setHunkDisplay] = createSignal<HunkDisplay | null>(null);

  // Pointerdown is a native document listener because a browser drag may start
  // anywhere inside the mounted ChangeSet and CSS needs the side marker before
  // selection continues beyond the initial cell. It reads only this shell's
  // stable root, runs for the shell lifetime, and cleanup removes the exact
  // listener so inactive Tabs cannot keep changing grid attributes.
  onMount(() => {
    /**
     * Restricts native browser text selection to the diff side under the pointer.
     *
     * The mounted ChangeSet root is the complete interaction scope. Every
     * pointer press clears its previous grid marker; a press on a left or right
     * TextDiffGrid side then marks only that grid. CSS suppresses selection on the
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
          onToggleOptions={props.onToggleOptions}
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
  /**
   * Handles an unmodified, non-editable `t` key after default is prevented.
   *
   * No argument arrives because the outer caller already knows current tree
   * visibility. It may update workspace state; the mounted shell and FileTree
   * receive the accepted value on the following reactive update. Modified keys,
   * editable targets, other key codes, and UI clicks do not invoke it here.
   */
  onToggleTree: () => void;
  /**
   * Handles an unmodified, non-editable `i` key after default is prevented.
   *
   * The callback receives no argument and may replace the caller's workspace
   * view. Accepted state returns through reactive props after it completes;
   * modified keys, editable targets, and unrelated actions do not invoke it.
   */
  onToggleView: () => void;
  /**
   * Handles an unmodified, non-editable `m` key after default is prevented.
   *
   * The caller may toggle its retained History boolean, which flows back to
   * review presentation after completion. Review's own visibility controls use
   * a separate boolean callback, and all ignored keyboard cases skip this one.
   */
  onToggleHistory: () => void;
  /**
   * Invoked only for unmodified `r` after the listener prevents its browser default.
   * The caller begins asynchronous snapshot replacement; Hotkeys does not await it.
   */
  onReload: () => void;
  /**
   * Handles an unmodified, non-editable `h` key after default is prevented.
   *
   * The caller toggles local Help state and returns the accepted value through
   * `ChangeSetShellProps.helpOpen`. Backdrop and Close actions use the explicit
   * boolean callback instead; ignored keys and editable targets do not invoke it.
   */
  onToggleHelp: () => void;
  /**
   * Handles an unmodified, non-editable `d` key after default is prevented.
   *
   * The callback may toggle workspace DebugHud state. The accepted value returns
   * through `debugOpen`, after which the sampling component mounts or unmounts;
   * metric ticks and ignored keyboard events never invoke this callback.
   */
  onToggleDebug: () => void;
  /**
   * Handles an unmodified, non-editable `o` key after default is prevented.
   *
   * The caller toggles the AppHeader engine options popover, which stores its
   * own open state natively. Ignored keys and editable targets never invoke it.
   */
  onToggleOptions: () => void;
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
  // Hotkeys require one native document listener because focus may sit anywhere
  // inside the active ChangeSet, while editable controls and modified browser
  // shortcuts must keep their defaults. The listener captures current props and
  // Navigation for this mounted Hotkeys lifetime; cleanup removes the same
  // function before another active shell can install its listener.
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
        return;
      }
      if (event.code === "KeyO") {
        event.preventDefault();
        props.onToggleOptions();
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
  /** Rounded animation-frame rate from the latest completed sample window. */
  fps: string;
  /** Last full-document element count, sampled outside the frame callback. */
  nodes: string;
  /** Last full-document span count captured with `nodes`. */
  spans: string;
};

/**
 * Defines the exact reactive hunk value displayed by DebugHud.
 *
 * Null exists only before the first DOM calculation. The HUD may format this
 * value but must not query hunk DOM, calculate navigation state, or mutate it.
 */
type DebugHudProps = {
  /**
   * Reads the latest DOM-mirrored global hunk position for display only.
   * DebugHud calls it reactively while mounted and never turns the value back
   * into navigation or selected state; null renders the pre-calculation marker.
   */
  globalSelectedHunk: Accessor<HunkDisplay["globalSelectedHunk"] | null>;
};

/**
 * Defines the independent Help operation required by the fixed HintHud.
 *
 * Navigation comes from context; this contract must not carry hunk state,
 * destinations, counters, or grouped HUD state.
 */
type HintHudProps = {
  /** Current local Help state reflected as the button's expanded value. */
  helpOpen: boolean;
  /**
   * Toggles local Help after the Help button is activated.
   * The caller stores the new state and returns it through `helpOpen`; Next and
   * Previous use Navigation directly and never invoke this callback.
   */
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

  // Debug metrics need an animation-frame loop because FPS is a property of
  // successive browser paints, not reactive application state. The loop reads
  // elapsed time and schedules low-frequency DOM counts in a zero-delay timer
  // so counting does not distort the sampled frame. Both handles exist only
  // while DebugHud is mounted and cleanup cancels them before the hidden HUD can
  // update signals.
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
      if (countTimer !== null) {
        return;
      }
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
      if (countTimer !== null) {
        window.clearTimeout(countTimer);
      }
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
function DebugMetric(props: {
  /** Short metric name placed in the left HUD column. */
  label: string;
  /** Already formatted sampled or reactive value placed beside `label`. */
  value: string;
}): JSX.Element {
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
 * `root` is the mounted ChangeSet root. `onDisplayChange` receives global display
 * values and stable local readers whenever global facts or the snapshot change. The observer must never write DOM or
 * expose data to Navigation.
 */
type HunkDisplayObserverProps = {
  /**
   * Returns the connected shell root whose FileCard attributes are authoritative.
   * HunkDisplayObserver calls it once on mount and observes that exact element
   * until cleanup. The caller must not replace it during the observer lifetime
   * or return a root belonging to another ChangeSet.
   */
  root: Accessor<HTMLElement>;
  /**
   * Accepts global display values and stable local readers after a DOM calculation.
   *
   * The observer invokes it after the initial mounted calculation and after
   * watched mutations that change global display facts. Local readers publish
   * independently when only a File's local count changes. `display` contains no
   * live DOM and the caller stores it only for declarative presentation. The callback
   * is not invoked after cleanup or after terminal renderer damage disconnects
   * observation; its completion precedes the next queued calculation.
   */
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
     *
     * # Returns
     *
     * - `kind` is the FileCard's semantic hunk-set branch. Husk and Lazy kinds
     *   may still gain targets, while Zero and Real describe stable totals.
     * - `skipped` reports whether this representation hides otherwise stable
     *   coordinates. The display calculation uses it to avoid claiming there
     *   are no more hunks merely because those coordinates are hidden.
     */
    function parseHunkSet(card: HTMLElement): {
      /** Semantic file-set branch used to determine whether more targets may appear. */
      kind: "husk" | "lazy" | "zero" | "real";
      /** Whether the current representation hides otherwise stable coordinates. */
      skipped: boolean;
    } {
      const hunkSet = expect(
        card.dataset.hunkSet,
        "Every FileCard requires data-hunk-set.",
      );
      const pseudo = /^(husk|lazy|zero)(:skip)?$/.exec(hunkSet);
      if (pseudo !== null) {
        const kind = pseudo[1];
        switch (kind) {
          case "husk":
          case "lazy":
          case "zero":
            return { kind, skipped: pseudo[2] === ":skip" };
        }
        assert(false, "FileCard has an invalid pseudo-hunk set.");
      }
      const real = expect(
        /^real:([1-9]\d*)(:skip)?$/.exec(hunkSet),
        `FileCard has invalid hunk set ${hunkSet}.`,
      );
      return { kind: "real", skipped: real[2] === ":skip" };
    }

    /**
     * Disposable scalar facts read from one stable FileCard's current DOM.
     * Positions are reactive presentation only; Navigation never reads them.
     */
    type CardStats = {
      /** Required immutable manifest coordinate. */
      fileIndex: number;
      /** Count of all coordinate-bearing targets, including skipped targets. */
      stable: number;
      /** Count of targets currently participating in navigation. */
      participating: number;
      /** Whether loading or expansion can change this card's target count. */
      more: boolean;
      /** Read this card's current local display without subscribing other cards. */
      position: Accessor<HunkPosition>;
      /** Replace local numeric presentation after a DOM calculation. */
      setPosition: Setter<HunkPosition>;
    };
    let cardStats = new Map<HTMLElement, CardStats>();
    let fileSelectedHunks = new Map<number, Accessor<HunkPosition>>();
    // A Fenwick tree stores only DOM-derived target counts. Prefix updates and
    // reads take O(log files), avoiding a scan of preceding cards on each load.
    // All caches are rebuilt when a replacement snapshot supplies new cards.
    let prefixes = new Float64Array(1);
    let participatingTotal = 0;
    let moreCards = 0;
    let selectedCard: HTMLElement | null = null;
    const selectedCards = new Set<HTMLElement>();
    let lastDisplay: HunkDisplay | null = null;
    let alive = true;
    let observer: MutationObserver | null = null;
    // Several ordered admissions can share one browser frame. Read their DOM
    // together before paint instead of rewriting every global badge per file.
    // Disposal cancels the pending frame before its snapshot can be read.
    let displayFrame: number | null = null;
    const changedCards = new Set<HTMLElement>();
    onCleanup(() => {
      alive = false;
      observer?.disconnect();
      if (displayFrame !== null) {
        cancelAnimationFrame(displayFrame);
        displayFrame = null;
      }
      changedCards.clear();
    });

    /**
     * Read changed cards and publish exact DOM-derived positions and totals.
     * Initial mount and snapshot replacement index the complete card set once;
     * later mutations reread only their cards, including selection changes.
     */
    function calculateDisplay(changed: ReadonlySet<HTMLElement>): HunkDisplay {
      const reset =
        lastDisplay === null ||
        [...changed].some((card) => !cardStats.has(card));
      let cards: Iterable<HTMLElement> = changed;
      if (reset) {
        const mounted = root.querySelectorAll<HTMLElement>("[data-file-card]");
        cardStats = new Map();
        fileSelectedHunks = new Map();
        prefixes = new Float64Array(mounted.length + 1);
        participatingTotal = 0;
        moreCards = 0;
        selectedCard = null;
        selectedCards.clear();
        mounted.forEach((card, fileIndex) => {
          assert(
            card.dataset.fileIndex === String(fileIndex),
            "FileCard DOM must retain manifest order.",
          );
          const [position, setPosition] = createSignal<HunkPosition>(
            { current: null, total: 0 },
            {
              equals: (a, b) => a.current === b.current && a.total === b.total,
            },
          );
          cardStats.set(card, {
            fileIndex,
            stable: 0,
            participating: 0,
            more: false,
            position,
            setPosition,
          });
          fileSelectedHunks.set(fileIndex, position);
        });
        cards = mounted;
      }
      const previousSelected = selectedCard;
      for (const card of cards) {
        const stats = expect(
          cardStats.get(card),
          "Hunk display requires an indexed FileCard.",
        );
        const hunkSet = parseHunkSet(card);
        const targets =
          card.querySelectorAll<HTMLElement>("[data-hunk-target]");
        let participating = 0;
        for (const target of targets) {
          const index = target.dataset.hunkIndex;
          assert(
            target.dataset.fileIndex === String(stats.fileIndex) &&
              index !== undefined &&
              /^(?:0|[1-9]\d*)$/.test(index),
            "FileCard contains a hunk with invalid coordinates.",
          );
          if (!target.classList.contains("skip")) {
            participating += 1;
          }
        }
        const delta = targets.length - stats.stable;
        for (
          let index = stats.fileIndex + 1;
          index < prefixes.length;
          index += index & -index
        ) {
          prefixes[index] += delta;
        }
        participatingTotal += participating - stats.participating;
        const more =
          hunkSet.kind === "husk" || hunkSet.kind === "lazy" || hunkSet.skipped;
        moreCards += Number(more) - Number(stats.more);
        stats.stable = targets.length;
        stats.participating = participating;
        stats.more = more;
        stats.setPosition({ current: null, total: participating });
        if (card.dataset.selectedHunkIndex !== undefined) {
          selectedCards.add(card);
        } else {
          selectedCards.delete(card);
        }
      }
      assert(
        selectedCards.size === (cardStats.size === 0 ? 0 : 1),
        "A non-empty ChangeSet requires exactly one selected FileCard.",
      );
      selectedCard = selectedCards.values().next().value ?? null;
      if (
        previousSelected !== null &&
        previousSelected !== selectedCard &&
        cardStats.has(previousSelected)
      ) {
        const previous = expect(
          cardStats.get(previousSelected),
          "Missing previous selected FileCard.",
        );
        previous.setPosition({ current: null, total: previous.participating });
      }
      let selectedCurrent: number | null = null;
      let selectedFileIndex: number | null = null;
      if (cardStats.size > 0) {
        const card = expect(
          selectedCard,
          "A non-empty ChangeSet requires a selected FileCard.",
        );
        assert(
          card.dataset.selectedHunkIndex !== undefined,
          "Selected FileCard lost its selected identity.",
        );
        const stats = expect(
          cardStats.get(card),
          "Selected FileCard is outside the mounted snapshot.",
        );
        const targets = Array.from(
          card.querySelectorAll<HTMLElement>("[data-hunk-target]"),
        );
        const selected = storedHunkTarget(card, targets);
        const localCurrent = targets.indexOf(selected) + 1;
        selectedCurrent = localCurrent;
        for (let index = stats.fileIndex; index > 0; index -= index & -index) {
          selectedCurrent += prefixes[index];
        }
        selectedFileIndex = stats.fileIndex;
        stats.setPosition({
          current: localCurrent,
          total: stats.participating,
        });
      }
      return {
        selectedFileIndex,
        globalSelectedHunk: {
          position: { current: selectedCurrent, total: participatingTotal },
          hasMore: moreCards > 0,
        },
        fileSelectedHunks,
      };
    }

    /** Coalesce renderer mutations until their complete target DOM is mounted. */
    function queueDisplayCalculation(): void {
      if (displayFrame !== null) {
        return;
      }
      displayFrame = requestAnimationFrame(() => {
        displayFrame = null;
        if (!alive) {
          return;
        }
        if (
          (lastDisplay === null &&
            root.querySelector("[data-file-render-error]") !== null) ||
          [...changedCards].some((card) =>
            card.hasAttribute("data-file-render-error"),
          )
        ) {
          observer?.disconnect();
          return;
        }
        try {
          // Local position signals and the global mirror describe the same DOM
          // batch; publish together so headers cannot see intermediate counts.
          batch(() => {
            const display = calculateDisplay(changedCards);
            changedCards.clear();
            const previous = lastDisplay;
            lastDisplay = display;
            if (
              previous === null ||
              previous.fileSelectedHunks !== display.fileSelectedHunks ||
              previous.selectedFileIndex !== display.selectedFileIndex ||
              previous.globalSelectedHunk.position.current !==
                display.globalSelectedHunk.position.current ||
              previous.globalSelectedHunk.position.total !==
                display.globalSelectedHunk.position.total ||
              previous.globalSelectedHunk.hasMore !==
                display.globalSelectedHunk.hasMore
            ) {
              props.onDisplayChange(display);
            }
          });
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

    // The mounted snapshot writes its initial selection synchronously during
    // its own mount. This MutationObserver must therefore attach in a microtask
    // and calculate only after the FileCards and their imperative rows finish
    // mounting. It watches the three attributes that can change display facts,
    // coalesces changed cards once per paint frame, and lives
    // only for this observer component. Cleanup flips `alive` and disconnects;
    // a queued calculation checks `alive` before reading or publishing detached DOM.
    queueMicrotask(() => {
      if (!alive || !root.isConnected) {
        return;
      }
      observer = new MutationObserver((records) => {
        for (const record of records) {
          assert(
            record.target instanceof HTMLElement &&
              record.target.hasAttribute("data-file-card"),
            "Hunk display mutation requires a stable FileCard.",
          );
          changedCards.add(record.target);
        }
        queueDisplayCalculation();
      });
      // data-selected-hunk-kind and data-selected-hunk-bay are deliberately
      // not watched: selectHunk() writes the index attribute on every
      // selection, so the index mutation already wakes the observer, and the
      // callback reads the whole identity from the live dataset.
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
 * Callers provide explicit visibility and an update callback. Every listed
 * hotkey is currently available; removed file-wide expansion operations are
 * absent. Backdrop and Close actions report `false` through that callback.
 */
function HelpModal(props: {
  /** Current local visibility; false unmounts the modal and backdrop. */
  open: boolean;
  /**
   * Replaces Help visibility after backdrop or Close activation.
   * Both interactions pass false. The caller stores it and returns the accepted
   * value through `open`; clicks inside the modal stop before invoking it.
   */
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
            <HotkeyHelpRow keys="o" label="Toggle engine options" />
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
  /** Visible section heading that groups the supplied rows. */
  title: string;
  /** Preconstructed Help rows rendered in caller order without transformation. */
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
function HotkeyHelpRow(props: {
  /** Exact key glyph shown in the non-interactive `kbd` element. */
  keys: string;
  /** Human-readable operation description paired with `keys`. */
  label: string;
}): JSX.Element {
  return (
    <div class="help-hud-row">
      <kbd>{props.keys}</kbd>
      <span>{props.label}</span>
    </div>
  );
}
