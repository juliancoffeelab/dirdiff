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
  /** Per-file selected positions keyed by manifest file index. */
  fileSelectedHunks: ReadonlyMap<number, HunkPosition>;
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
 * `root` is the mounted ChangeSet root. `onDisplayChange` receives every
 * complete immutable replacement value. The observer must never write DOM or
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
   * Accepts one complete immutable display replacement after a DOM calculation.
   *
   * The observer invokes it after the initial mounted calculation and after
   * watched structural or selected-index mutations. `display` contains no live
   * DOM and the caller stores it only for declarative presentation. The callback
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
     * Per-card facts recorded by the last complete calculation.
     *
     * `offset` is the card's stable-position prefix and `participating` its
     * displayed total; both are invariant under selection-only mutations, so
     * the selective path below can reuse them. Any structural mutation runs
     * the full walk again, which replaces this map wholesale.
     */
    type CardStats = {
      /** Immutable manifest index read from this exact FileCard. */
      fileIndex: number;
      /** Count of non-skip targets in the last complete DOM calculation. */
      participating: number;
      /** Number of all stable coordinates in preceding FileCards. */
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
        assert(
          selectedCards.length === 0,
          "Empty ChangeSet contains a selected FileCard.",
        );
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
      assert(
        selectedCards.length === 1,
        "A non-empty ChangeSet requires exactly one selected FileCard.",
      );

      let stablePositionOffset = 0;
      let participatingTotal = 0;
      let selectedCurrent: number | null = null;
      let selectedFileIndex: number | null = null;
      let hasMore = false;
      const fileSelectedHunks = new Map<number, HunkPosition>();
      const stats = new Map<HTMLElement, CardStats>();

      cards.forEach((card) => {
        const fileIndexText = card.dataset.fileIndex;
        assert(
          fileIndexText !== undefined && /^(?:0|[1-9]\d*)$/.test(fileIndexText),
          "FileCard has an invalid manifest file index.",
        );
        const fileIndex = Number(fileIndexText);
        const hunkSet = parseHunkSet(card);
        const targets = Array.from(
          card.querySelectorAll<HTMLElement>("[data-hunk-target]"),
        );
        for (const target of targets) {
          const hunkIndex = target.dataset.hunkIndex;
          assert(
            target.dataset.fileIndex === fileIndexText &&
              hunkIndex !== undefined &&
              /^(?:0|[1-9]\d*)$/.test(hunkIndex),
            `FileCard ${fileIndexText} contains a hunk with invalid coordinates.`,
          );
        }
        const participatingTargets = targets.filter(
          (target) => !target.classList.contains("skip"),
        );
        hasMore ||=
          hunkSet.kind === "husk" || hunkSet.kind === "lazy" || hunkSet.skipped;
        let localCurrent: number | null = null;
        if (card === selectedCards[0]) {
          selectedFileIndex = fileIndex;
          const selectedTarget = storedHunkTarget(card, targets);
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

      assert(
        selectedFileIndex !== null && selectedCurrent !== null,
        "Selected hunk has no calculable DOM position.",
      );
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
     * performs structural validation.
     *
     * # Returns
     *
     * - A complete display update derived from cached per-card totals and the
     *   newly selected FileCard.
     * - `null`: No complete cache exists or the selected card is absent from
     *   that cache. The observer must run the full structural calculation
     *   instead.
     */
    function calculateSelectionDisplay(): HunkDisplay | null {
      if (cardStats === null || lastDisplay === null) {
        return null;
      }
      const selectedCards = root.querySelectorAll<HTMLElement>(
        "[data-file-card][data-selected-hunk-index]",
      );
      assert(
        selectedCards.length === 1,
        "A non-empty ChangeSet requires exactly one selected FileCard.",
      );
      const card = selectedCards[0];
      if (card === undefined) {
        return null;
      }
      const stats = cardStats.get(card);
      if (stats === undefined) {
        return null;
      }
      const targets = Array.from(
        card.querySelectorAll<HTMLElement>("[data-hunk-target]"),
      );
      const selectedTarget = storedHunkTarget(card, targets);
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
     * TextDiffGrid places its imperative row targets during mount after FileCard has
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

    // The mounted snapshot writes its initial selection synchronously during
    // its own mount. This MutationObserver must therefore attach in a microtask
    // and calculate only after the FileCards and their imperative rows finish
    // mounting. It watches the three attributes that can change display facts,
    // coalesces each mutation batch through `queueDisplayCalculation`, and lives
    // only for this observer component. Cleanup flips `alive` and disconnects;
    // a queued microtask checks `alive` before reading or publishing detached DOM.
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
