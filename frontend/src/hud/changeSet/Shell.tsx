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

  // Pointerdown is a native document listener because a browser drag may start
  // anywhere inside the mounted ChangeSet and CSS needs the side marker before
  // selection continues beyond the initial cell. It reads only this shell's
  // stable root, runs for the shell lifetime, and cleanup removes the exact
  // listener so inactive Tabs cannot keep changing grid attributes.
  onMount(() => {
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

type HotkeysProps = {
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
  // Hotkeys require one native document listener because focus may sit anywhere
  // inside the active ChangeSet, while editable controls and modified browser
  // shortcuts must keep their defaults. The listener captures current props and
  // Navigation for this mounted Hotkeys lifetime; cleanup removes the same
  // function before another active shell can install its listener.
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

  // Debug metrics need an animation-frame loop because FPS is a property of
  // successive browser paints, not reactive application state. The loop reads
  // elapsed time and schedules low-frequency DOM counts in a zero-delay timer
  // so counting does not distort the sampled frame. Both handles exist only
  // while DebugHud is mounted and cleanup cancels them before the hidden HUD can
  // update signals.
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

    function parseHunkSet(card: HTMLElement): {
      kind: "husk" | "lazy" | "zero" | "real";
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

    type CardStats = {
      fileIndex: number;
      participating: number;
      offset: number;
    };
    let cardStats: Map<HTMLElement, CardStats> | null = null;
    let lastDisplay: HunkDisplay | null = null;

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
