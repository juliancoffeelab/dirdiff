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
import { assert } from "../../utils";
import {
  fileCardIndex,
  fileCardsIn,
  markSelectionSide,
  renderDamaged,
  type HunkPosition,
} from "../fileCard/FileCard";
import {
  NavigationProvider,
  hunkParticipates,
  hunkTargetsOf,
  readHunkIdentity,
  readHunkSet,
  selectedFileCard,
  storedHunkTarget,
  useNavigation,
} from "../navigation";

export type HunkDisplay = {
  selectedFileIndex: number | null;
  globalSelectedHunk: {
    position: HunkPosition;
    hasMore: boolean;
  };
  fileSelectedHunks: ReadonlyMap<number, HunkPosition>;
};

type ChangeSetShellProps = {
  helpOpen: boolean;
  debugOpen: boolean;
  historyOpen: boolean;
  onToggleTree: () => void;
  onToggleView: () => void;
  onToggleHistory: () => void;
  onReload: () => void;
  onToggleHelp: () => void;
  onHelpOpenChange: (open: boolean) => void;
  onToggleDebug: () => void;
  onToggleOptions: () => void;
  children: (hunkDisplay: Accessor<HunkDisplay | null>) => JSX.Element;
};

export function ChangeSetShell(props: ChangeSetShellProps): JSX.Element {
  let root!: HTMLElement;
  const [hunkDisplay, setHunkDisplay] = createSignal<HunkDisplay | null>(null);

  // Native document listener: a drag may start anywhere in the ChangeSet and
  // CSS
  // needs the side marker before selection leaves the initial cell. Removed on
  // cleanup so inactive Tabs stop changing grid attributes.
  onMount(() => {
    function selectDiffSide(event: PointerEvent): void {
      markSelectionSide(root, event.target);
    }

    document.addEventListener("pointerdown", selectDiffSide);
    onCleanup(() => {
      document.removeEventListener("pointerdown", selectDiffSide);
    });
  });

  return (
    <section
      ref={root}
      class="change-set-root"
      data-change-set-root
      // The review view covers this section without removing its layout, so
      // its controls would still take focus, and focus scrolls the locked
      // document.
      inert={props.historyOpen}
    >
      <NavigationProvider root={() => root}>
        <Hotkeys
          historyOpen={props.historyOpen}
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

type HotkeysProps = {
  historyOpen: boolean;
  onToggleTree: () => void;
  onToggleView: () => void;
  onToggleHistory: () => void;
  onReload: () => void;
  onToggleHelp: () => void;
  onToggleDebug: () => void;
  onToggleOptions: () => void;
};

function Hotkeys(props: HotkeysProps): null {
  const navigation = useNavigation();
  const toast = useToasts();
  // Focus may sit anywhere in the ChangeSet, so this is a document listener.
  // It must be removed before another active shell installs its own.
  onMount(() => {
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

      // The review view covers the code, the file tree and the developer
      // metrics, so these keys would change things the reader cannot see.
      if (
        props.historyOpen &&
        (event.code === "KeyN" ||
          event.code === "KeyP" ||
          event.code === "KeyT" ||
          event.code === "KeyD")
      ) {
        return;
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

type DebugMetrics = {
  fps: string;
  nodes: string;
  spans: string;
};

type DebugHudProps = {
  globalSelectedHunk: Accessor<HunkDisplay["globalSelectedHunk"] | null>;
};

type HintHudProps = {
  helpOpen: boolean;
  onToggleHelp: () => void;
};

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

  // FPS needs an animation-frame loop. The full-document counts are slow, so
  // they run at most every five seconds in their own macrotask; during a
  // sustained scroll they go stale rather than steal time from the FPS sample.
  onMount(() => {
    function updateFps(): void {
      setMetrics((current) => ({
        ...current,
        fps: currentFps ? String(Math.round(currentFps)) : "--",
      }));
    }

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

function DebugMetric(props: { label: string; value: string }): JSX.Element {
  return (
    <div class="debug-metric">
      <span class="debug-metric-label">{props.label}</span>
      <strong class="debug-metric-value">{props.value}</strong>
    </div>
  );
}

type HunkDisplayObserverProps = {
  root: Accessor<HTMLElement>;
  onDisplayChange: (display: HunkDisplay) => void;
};

function HunkDisplayObserver(props: HunkDisplayObserverProps): null {
  const toast = useToasts();
  onMount(() => {
    const root = props.root();

    // Per-card facts from the last full calculation. `offset` and
    // `participating` do not change on selection-only mutations, so the
    // selective path reuses them; a full walk replaces the map wholesale.
    type CardStats = {
      fileIndex: number;
      participating: number;
      offset: number;
    };
    let cardStats: Map<HTMLElement, CardStats> | null = null;
    let lastDisplay: HunkDisplay | null = null;

    function calculateDisplay(): HunkDisplay {
      const cards = fileCardsIn(root);
      const selectedCard = selectedFileCard(root);
      if (cards.length === 0) {
        assert(
          selectedCard === null,
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
        selectedCard !== null,
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
        const fileIndex = fileCardIndex(card);
        const hunkSet = readHunkSet(card);
        const targets = hunkTargetsOf(card);
        for (const target of targets) {
          assert(
            readHunkIdentity(target).fileIndex === fileIndex,
            `FileCard ${fileIndex} contains a hunk with invalid coordinates.`,
          );
        }
        const participatingTargets = targets.filter(hunkParticipates);
        hasMore ||=
          hunkSet.kind === "husk" || hunkSet.kind === "lazy" || hunkSet.skipped;
        let localCurrent: number | null = null;
        if (card === selectedCard) {
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

    function calculateSelectionDisplay(): HunkDisplay | null {
      if (cardStats === null || lastDisplay === null) {
        return null;
      }
      const card = selectedFileCard(root);
      assert(
        card !== null,
        "A non-empty ChangeSet requires exactly one selected FileCard.",
      );
      const stats = cardStats.get(card);
      if (stats === undefined) {
        return null;
      }
      const targets = hunkTargetsOf(card);
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

    // TextDiffGrid places its row targets during mount, after FileCard updates
    // `data-hunk-set`; the microtask waits for that to finish. A queued full
    // calculation subsumes a selective one, never the reverse.
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
        if (renderDamaged(root)) {
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

    // The snapshot writes its initial selection synchronously during its mount,
    // so attach in a microtask, after FileCards and their rows have mounted.
    // `alive` guards queued microtasks against reading detached DOM.
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

function HotkeyHelpRow(props: { keys: string; label: string }): JSX.Element {
  return (
    <div class="help-hud-row">
      <kbd>{props.keys}</kbd>
      <span>{props.label}</span>
    </div>
  );
}
