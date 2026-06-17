import {
  Show,
  createEffect,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";

type DebugMetrics = {
  fps: string;
  nodes: string;
  spans: string;
  hunks: string;
};

const emptyDebugMetrics: DebugMetrics = {
  fps: "--",
  nodes: "--",
  spans: "--",
  hunks: "--/--",
};

function DebugHud(props: {
  open: boolean;
  hunkPosition: { current: number; total: number };
}) {
  const [metrics, setMetrics] = createSignal<DebugMetrics>(emptyDebugMetrics);
  let frame = 0;
  let sampleStartedAt = performance.now();
  let sampleFrames = 0;
  let displayUpdatedAt = sampleStartedAt;
  let currentFps = 0;

  const formatCount = (value: number) => value.toLocaleString();

  const updateMetrics = () => {
    const hunkPosition = props.hunkPosition;
    setMetrics({
      fps: currentFps ? String(Math.round(currentFps)) : "--",
      nodes: formatCount(document.querySelectorAll("*").length),
      spans: formatCount(document.querySelectorAll("span").length),
      hunks:
        hunkPosition.total === 0
          ? "--/--"
          : `${hunkPosition.current}/${hunkPosition.total}`,
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
        <DebugMetric label="Hunks" value={metrics().hunks} />
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

export function HunkNav(props: {
  debugOpen: boolean;
  helpOpen: boolean;
  hunkPosition: { current: number; total: number };
  onHelpOpenChange: (open: boolean) => void;
  onNext: () => void;
  onPrev: () => void;
}) {
  return (
    <div class="hud-stack">
      <DebugHud open={props.debugOpen} hunkPosition={props.hunkPosition} />
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
