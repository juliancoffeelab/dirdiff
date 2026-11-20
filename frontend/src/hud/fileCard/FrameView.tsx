import {
  For,
  Show,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  type Accessor,
  type JSX,
} from "solid-js";

import {
  FLATFILE_BAY_KEY,
  type BayChange,
  type BayPayload,
  type FileDiff,
  type Frame,
  type ReviewFilePair,
  type TextKindPayload,
} from "../../api/api";
import type { DiffViewMode } from "../App";
import { finishForcedChunkLayout, forceChunkLayout } from "./grids/text/rowDom";

import { ImageBayView } from "./grids/image/ImageBayView";
import { TextDiffGrid } from "./grids/text/TextDiffGrid";
import type { LinePins } from "../linePins";
import { useNavigation, type RealHunkIdentity } from "../navigation";
import { assert, expect } from "../../utils";

export type BayHunks = {
  stops: number[];
  carrier: "rows" | "bay";
};

export function composedHunks(diff: FileDiff): {
  total: number;
  bays: Map<string, BayHunks>;
} {
  const bays = new Map<string, BayHunks>();
  let total = 0;
  for (const frame of diff.frames) {
    for (const bay of frame.bays) {
      const stops: number[] = [];
      // Don't special case a kind here: whether a bay carries its own stops is
      // decided by whether it has rows with hunks in them.
      const rows = "rows" in bay.kind_data ? bay.kind_data.rows : null;
      for (const row of rows ?? []) {
        if (row.hunk_index !== null) {
          stops.push(row.hunk_index);
        }
      }
      const carrier = stops.length > 0 ? "rows" : "bay";
      // A text bay with an empty row list is an empty File side: nothing
      // changed inside it, so it has no hunk. Leaving it without a stop makes a
      // lone empty File a zero-hunk file, whose header carries the `zero`
      // pseudo-hunk instead. Rowless kinds (image, blob) and text bays whose
      // rows carry no stop (a move) keep their bay-carried stop.
      if (
        carrier === "bay" &&
        bay.change.kind !== "unchanged" &&
        !(rows !== null && rows.length === 0)
      ) {
        stops.push(0);
      }
      total += stops.length;
      bays.set(bay.bay_key, { stops, carrier });
    }
  }
  return { total, bays };
}

export type BayRenderMode = "rich" | "virtual";

export type BayRenderModes = {
  mode(bayKey: string): BayRenderMode;
  setMode(bayKey: string, mode: BayRenderMode): void;
  clearMode(bayKey: string): void;
};

type BayCost = "small" | "medium" | "large";

type RichZone = {
  enterViewports: number;
  exitViewports: number;
};

function richZone(rowCount: number): RichZone {
  assert(
    Number.isInteger(rowCount) && rowCount >= 0,
    "Virtualization requires a non-negative row count.",
  );
  const cost: BayCost =
    rowCount <= 250 ? "small" : rowCount <= 1_000 ? "medium" : "large";
  switch (cost) {
    case "small":
      return { enterViewports: 2, exitViewports: 3 };
    case "medium":
      return { enterViewports: 4, exitViewports: 6 };
    case "large":
      return { enterViewports: 8, exitViewports: 12 };
  }
}

function initialRenderMode(card: HTMLElement, rowCount: number): BayRenderMode {
  const viewportHeight = window.innerHeight;
  const rect = card.getBoundingClientRect();
  assert(
    Number.isFinite(viewportHeight) && viewportHeight > 0,
    "Initial virtualization requires a finite positive viewport height.",
  );
  assert(
    Number.isFinite(rect.top) && Number.isFinite(rect.bottom),
    "Initial virtualization requires a finite FileCard rectangle.",
  );
  assert(
    rect.width !== 0 || rect.height !== 0,
    "Initial virtualization requires measurable FileCard geometry.",
  );
  const margin = richZone(rowCount).enterViewports * viewportHeight;
  return rect.bottom >= -margin && rect.top <= viewportHeight + margin
    ? "rich"
    : "virtual";
}

type EnrichableBay = HTMLElement & {
  intersectsRichEntryZone: (viewportTop: number) => boolean;
  waitToEnrich_impl: () => Promise<void>;
};

export function composedHunkCount(diff: FileDiff): number {
  return composedHunks(diff).total;
}

function BayWarnings(props: { bay: BayPayload }): JSX.Element {
  return (
    <For each={props.bay.warnings}>
      {(warning) => (
        <p class="composed-bay-warning" title={warning.message}>
          {warning.message}
        </p>
      )}
    </For>
  );
}

function VirtualBay(props: {
  fileIndex: number;
  bay: BayPayload;
  content: TextKindPayload;
  reservedRichHeight: number | null;
}): JSX.Element {
  const text = createMemo(() => {
    const leftLines: string[] = [];
    const rightLines: string[] = [];
    const hunkAnchors: {
      hunkIndex: number;
      rowOffset: number;
    }[] = [];
    props.content.rows.forEach((row, rowOffset) => {
      // A side a row is missing is a blank line, not an absent one: the two
      // texts stay aligned so a reader compares the same position on both.
      leftLines.push(row.left_text ?? "");
      rightLines.push(row.right_text ?? "");
      if (row.hunk_index !== null) {
        hunkAnchors.push({ hunkIndex: row.hunk_index, rowOffset });
      }
    });
    return {
      left: leftLines.join("\n"),
      right: rightLines.join("\n"),
      hunkAnchors,
    };
  });
  return (
    <div
      class="virtual-bay-body"
      style={{
        height:
          props.reservedRichHeight === null
            ? undefined
            : `${props.reservedRichHeight}px`,
      }}
    >
      <div class="plain-split-diff" aria-label="Virtualized plain split diff">
        <For each={text().hunkAnchors}>
          {(anchor) => {
            const identity: RealHunkIdentity = {
              fileIndex: props.fileIndex,
              kind: "real",
              bay: props.bay.bay_key,
              hunkIndex: anchor.hunkIndex,
            };
            return (
              <span
                class="virtual-hunk-anchor hunk-anchor"
                style={{ top: `${10 + anchor.rowOffset * 17.4}px` }}
                data-hunk-target
                data-hunk-kind={identity.kind}
                data-file-index={identity.fileIndex}
                data-hunk-bay={identity.bay}
                data-hunk-index={identity.hunkIndex}
                aria-hidden="true"
              />
            );
          }}
        </For>
        <pre>{text().left}</pre>
        <pre>{text().right}</pre>
      </div>
    </div>
  );
}

function TextBayView(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  displayName: string;
  bay: BayPayload;
  content: TextKindPayload;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  card: Accessor<HTMLElement>;
  bayRenderModes: BayRenderModes;
}): JSX.Element {
  // Held as HTMLElement: the enrichment attachment below converts it to the
  // EnrichableBay DOM interface, which is declared on the element base type.
  let wrapper!: HTMLElement;
  const navigation = useNavigation();
  const bayKey = props.bay.bay_key;
  // Registration spans the component lifetime, not onMount's: the mode is
  // read by the JSX below before mount, so the key must already exist.
  props.bayRenderModes.setMode(
    bayKey,
    initialRenderMode(props.card(), props.content.rows.length),
  );
  onCleanup(() => props.bayRenderModes.clearMode(bayKey));
  const mode = (): BayRenderMode => props.bayRenderModes.mode(bayKey);
  const [reservedRichHeight, setReservedRichHeight] = createSignal<
    number | null
  >(null);

  function changeRenderMode(next: BayRenderMode): void {
    if (mode() === next) {
      return;
    }
    if (next === "virtual") {
      // An off-screen grid with unwarmed chunks measures the intrinsic
      // estimate, not its real height; pinning that onto the virtual body
      // moves the page under the reader. Force real layout first. The grid
      // unmounts with this transition, so the visible chunks need no
      // restoration.
      forceChunkLayout(wrapper);
      const measuredHeight = wrapper.getBoundingClientRect().height;
      assert(
        Number.isFinite(measuredHeight) && measuredHeight > 0,
        "Rich bay body must have a finite positive height before virtualization.",
      );
      setReservedRichHeight(measuredHeight);
    }
    props.bayRenderModes.setMode(bayKey, next);
  }

  async function waitToEnrich_impl(): Promise<void> {
    changeRenderMode("rich");
    // `changeRenderMode` writes a store key; the grid it selects is mounted
    // by Solid's flush, not by the assignment. Yielding the microtask lets
    // that flush run so the chunks read below exist. Nothing here waits on
    // layout or paint, only on the render Solid has already scheduled.
    await Promise.resolve();
    if (!wrapper.isConnected) {
      return;
    }
    // Enrichment is complete only when its geometry is real: fresh chunks
    // still carry the intrinsic estimate, and the callers (navigation's
    // pre-enrichment and centering) read heights immediately. Lay the
    // chunks out now and give the browser one rendered frame to record
    // their remembered sizes before returning them to skippable containment.
    const freshChunks = forceChunkLayout(wrapper);
    if (freshChunks.length > 0) {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (!wrapper.isConnected) {
        return;
      }
      finishForcedChunkLayout(freshChunks);
    }
  }

  function intersectsRichEntryZone(viewportTop: number): boolean {
    assert(
      Number.isFinite(viewportTop) && viewportTop >= 0,
      "Rich-entry geometry requires a finite non-negative viewport top.",
    );
    const viewportHeight = window.innerHeight;
    assert(
      viewportHeight > 0,
      "Rich-entry geometry requires a positive viewport height.",
    );
    const rect = wrapper.getBoundingClientRect();
    assert(
      Number.isFinite(rect.top) && Number.isFinite(rect.bottom),
      "Rich-entry geometry requires a finite bay rectangle.",
    );
    const bayTop = window.scrollY + rect.top;
    const bayBottom = window.scrollY + rect.bottom;
    const margin =
      richZone(props.content.rows.length).enterViewports * viewportHeight;
    return (
      bayBottom >= viewportTop - margin &&
      bayTop <= viewportTop + viewportHeight + margin
    );
  }

  onMount(() => {
    Object.assign(wrapper, {
      intersectsRichEntryZone,
      waitToEnrich_impl,
    }) satisfies EnrichableBay;
    const rowCount = props.content.rows.length;
    let enterObserver: IntersectionObserver | null = null;
    let exitObserver: IntersectionObserver | null = null;

    function observeCurrentZones(): void {
      if (enterObserver !== null) {
        enterObserver.disconnect();
      }
      if (exitObserver !== null) {
        exitObserver.disconnect();
      }
      const zone = richZone(rowCount);
      enterObserver = new IntersectionObserver(
        (entries) => {
          // Entries queue oldest-first; a fast programmatic jump can batch
          // an out-then-in transition, and acting on the stale first entry
          // left visible bays stuck virtual. Only the newest entry is the
          // bay's current state.
          const currentEntry = expect(
            entries[entries.length - 1],
            "Rich-zone observer omitted its bay entry.",
          );
          if (currentEntry.isIntersecting) {
            changeRenderMode("rich");
          }
        },
        {
          rootMargin: `${zone.enterViewports * window.innerHeight}px 0px`,
        },
      );
      exitObserver = new IntersectionObserver(
        (entries) => {
          // Same newest-entry rule as the enter observer above.
          const currentEntry = expect(
            entries[entries.length - 1],
            "Virtual-zone observer omitted its bay entry.",
          );
          if (
            !currentEntry.isIntersecting &&
            navigation.activeOperations === 0
          ) {
            changeRenderMode("virtual");
          }
        },
        {
          rootMargin: `${zone.exitViewports * window.innerHeight}px 0px`,
        },
      );
      enterObserver.observe(wrapper);
      exitObserver.observe(wrapper);
    }

    observeCurrentZones();
    window.addEventListener("resize", observeCurrentZones);
    // Navigation emits on this shell root only after its last command settles.
    // Reusing zone setup delivers fresh entries for bays whose exit was skipped.
    const navigationRoot = navigation.root();
    navigationRoot.addEventListener("navigation-idle", observeCurrentZones);
    onCleanup(() => {
      if (enterObserver !== null) {
        enterObserver.disconnect();
      }
      if (exitObserver !== null) {
        exitObserver.disconnect();
      }
      window.removeEventListener("resize", observeCurrentZones);
      navigationRoot.removeEventListener(
        "navigation-idle",
        observeCurrentZones,
      );
      Reflect.deleteProperty(wrapper, "intersectsRichEntryZone");
      Reflect.deleteProperty(wrapper, "waitToEnrich_impl");
    });
  });

  return (
    <div
      ref={(element) => {
        wrapper = element;
      }}
      data-bay-render={mode()}
      data-bay-key={bayKey}
    >
      <Show
        when={mode() === "rich"}
        fallback={
          <VirtualBay
            fileIndex={props.fileIndex}
            bay={props.bay}
            content={props.content}
            reservedRichHeight={reservedRichHeight()}
          />
        }
      >
        <TextDiffGrid
          reviewFile={props.reviewFile}
          fileIndex={props.fileIndex}
          displayName={props.displayName}
          bayKey={props.bay.bay_key}
          contentLabel={props.bay.label}
          leftLabel={props.content.left_label}
          rightLabel={props.content.right_label}
          rows={props.content.rows}
          foldHints={props.content.fold_hints}
          interleaveInlineRows={props.content.interleave_inline_rows}
          viewMode={props.view}
          aggressiveFolds={props.aggressiveFolds}
          linePins={props.linePins}
        />
      </Show>
    </div>
  );
}

function BayBody(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  displayName: string;
  bay: BayPayload;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  card: Accessor<HTMLElement>;
  bayRenderModes: BayRenderModes;
}): JSX.Element {
  // Read once into a local so the narrowing below survives into each branch;
  // `props.bay.kind_data` is a fresh getter call and narrows nothing.
  const content = props.bay.kind_data;
  switch (content.kind) {
    case "text":
      return (
        <TextBayView
          reviewFile={props.reviewFile}
          fileIndex={props.fileIndex}
          displayName={props.displayName}
          bay={props.bay}
          content={content}
          view={props.view}
          aggressiveFolds={props.aggressiveFolds}
          linePins={props.linePins}
          card={props.card}
          bayRenderModes={props.bayRenderModes}
        />
      );
    case "image":
      return (
        <ImageBayView
          reviewFile={props.reviewFile}
          bay={props.bay}
          content={content}
          view={props.view}
          linePins={props.linePins}
        />
      );
  }
}

export type BayExpansion = {
  isExpanded(bay: BayPayload): boolean;
  setExpanded(bayKey: string, expanded: boolean): void;
};

function BayStats(props: { bay: BayPayload }): JSX.Element {
  // Counts belong to whichever kind reports them, so this asks the payload for
  // its stats instead of naming the kinds allowed to have any.
  const counts = () =>
    "stats" in props.bay.kind_data ? props.bay.kind_data.stats : null;
  return (
    <Show when={counts()}>
      {(stats) => (
        <span class="composed-bay-stats">
          <Show when={stats().added_lines > 0}>
            <span class="delta added">+{stats().added_lines}</span>
          </Show>
          <Show when={stats().modified_lines > 0}>
            <span class="delta changed">~{stats().modified_lines}</span>
          </Show>
          <Show when={stats().removed_lines > 0}>
            <span class="delta removed">-{stats().removed_lines}</span>
          </Show>
        </span>
      )}
    </Show>
  );
}

function BayView(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  displayName: string;
  bay: BayPayload;
  hunks: BayHunks;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  card: Accessor<HTMLElement>;
  bayExpansion: BayExpansion;
  bayRenderModes: BayRenderModes;
}): JSX.Element {
  function changeTone(change: BayChange): string {
    return `composed-bay-${change.kind}`;
  }

  const expanded = (): boolean => props.bayExpansion.isExpanded(props.bay);

  const shown = (): boolean => !props.bay.collapsible || expanded();
  // The bay writes anchors when it carries the stops itself, and when it is
  // closed and its rows are not mounted to carry them. Which of the two it
  // is does not change what gets written, only whether anything does.
  const anchors = (): number[] =>
    props.hunks.carrier === "bay" || !shown() ? props.hunks.stops : [];

  return (
    <div
      class="composed-bay"
      classList={{ "composed-bay-body": !props.bay.collapsible }}
    >
      <Show when={props.bay.collapsible}>
        <button
          type="button"
          class="composed-bay-header"
          title={props.bay.detail ?? undefined}
          aria-expanded={expanded()}
          onClick={() =>
            props.bayExpansion.setExpanded(props.bay.bay_key, !expanded())
          }
        >
          <span class="composed-bay-disclosure" aria-hidden="true">
            {expanded() ? "▾" : "▸"}
          </span>
          <span
            class="composed-bay-label"
            classList={{
              // An untouched bay may still have a disclosure, and colouring it would
              // claim something happened there.
              [changeTone(props.bay.change)]:
                props.bay.change.kind !== "unchanged",
            }}
          >
            {props.bay.label}
          </span>
          <BayStats bay={props.bay} />
        </button>
      </Show>
      <Show when={props.bay.detail}>
        {(detail) => <p class="composed-bay-detail">{detail()}</p>}
      </Show>
      <BayWarnings bay={props.bay} />
      <For each={anchors()}>
        {(hunkIndex, position) => {
          const identity: RealHunkIdentity = {
            fileIndex: props.fileIndex,
            kind: "real",
            bay: props.bay.bay_key,
            hunkIndex,
          };
          return (
            <span
              class="composed-bay-anchor"
              // The first anchor is where Next lands; the rest hold their
              // coordinates without being traversed.
              classList={{ skip: position() > 0 }}
              data-hunk-target
              data-hunk-kind={identity.kind}
              data-file-index={identity.fileIndex}
              data-hunk-bay={identity.bay}
              data-hunk-index={identity.hunkIndex}
              aria-hidden="true"
            />
          );
        }}
      </For>
      <Show when={shown()}>
        <BayBody
          reviewFile={props.reviewFile}
          fileIndex={props.fileIndex}
          displayName={props.displayName}
          bay={props.bay}
          view={props.view}
          aggressiveFolds={props.aggressiveFolds}
          linePins={props.linePins}
          card={props.card}
          bayRenderModes={props.bayRenderModes}
        />
      </Show>
    </div>
  );
}

export function FrameView(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  backend_data: FileDiff;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  card: Accessor<HTMLElement>;
  bayExpansion: BayExpansion;
  bayRenderModes: BayRenderModes;
}): JSX.Element {
  function frameTone(frame: Frame): string {
    return `composed-frame-${frame.bays[0].change.kind}`;
  }

  // One stop list for the whole File, read by every bay below. Memoized so the
  // walk runs once per composed diff instead of once per bay read, and never
  // mutated into. `FullFileRenderer` walks separately to check the wire
  // contract once at construction; this memo is the reactive one the bays read.
  const hunks = createMemo(
    (): Map<string, BayHunks> => composedHunks(props.backend_data).bays,
  );

  const bayHunks = (bay: BayPayload): BayHunks => {
    return expect(
      hunks().get(bay.bay_key),
      `Bay ${bay.bay_key} is absent from its File's hunk stops.`,
    );
  };

  const bareTextBay = (): {
    bay: BayPayload;
    content: TextKindPayload;
  } | null => {
    const diff = props.backend_data;
    if (diff.frames.length !== 1) {
      return null;
    }
    const frame = diff.frames[0];
    if (frame.bays.length !== 1) {
      return null;
    }
    const bay = frame.bays[0];
    const content = bay.kind_data;
    if (content.kind !== "text" || bay.bay_key !== FLATFILE_BAY_KEY) {
      return null;
    }
    return { bay, content };
  };

  return (
    <Show
      when={bareTextBay()}
      keyed
      fallback={
        <For each={props.backend_data.frames}>
          {(frame) => {
            // The status spells the same backend-authored `change` the frame's
            // tint shows: the word itself, or the two names a moved bay wore at
            // its old and its new place. An unchanged frame stays silent,
            // matching the tint that lets it recede; a nameless frame (prose has
            // no prompt) still wears it.
            const change = frame.bays[0].change;
            let status: string | null;
            switch (change.kind) {
              case "unchanged":
                status = null;
                break;
              case "added":
              case "removed":
              case "changed":
                status = change.kind;
                break;
              case "moved":
                // The two ends are named the way their frames are headed,
                // so both can be found on screen. A bay the builder cannot
                // name at an end. A notebook's prose cell carries no prompt,
                // so the name has nothing to point at and says only that it
                // moved.
                status =
                  change.from_heading === null || change.to_heading === null
                    ? "moved"
                    : `moved: ${change.from_heading} -> ${change.to_heading}`;
                break;
            }
            return (
              <div
                class={`composed-frame ${frameTone(frame)}`}
                data-frame-key={frame.frame_key}
              >
                <Show when={frame.heading !== null || status !== null}>
                  <h3 class="composed-frame-heading">
                    {frame.heading}
                    <Show when={status} keyed>
                      {(word) => (
                        <span class="composed-frame-status">{word}</span>
                      )}
                    </Show>
                  </h3>
                </Show>
                <For each={frame.bays}>
                  {(bay) => (
                    <BayView
                      reviewFile={props.reviewFile}
                      hunks={bayHunks(bay)}
                      fileIndex={props.fileIndex}
                      displayName={props.backend_data.display_name}
                      bay={bay}
                      view={props.view}
                      aggressiveFolds={props.aggressiveFolds}
                      linePins={props.linePins}
                      card={props.card}
                      bayExpansion={props.bayExpansion}
                      bayRenderModes={props.bayRenderModes}
                    />
                  )}
                </For>
              </div>
            );
          }}
        </For>
      }
    >
      {(bare) => (
        <>
          {/* A changed flat file can have no row hunks after whitespace is
              ignored. Its bay stop remains mounted outside the grid. */}
          <For
            each={
              bayHunks(bare.bay).carrier === "bay"
                ? bayHunks(bare.bay).stops
                : []
            }
          >
            {(hunkIndex) => (
              <span
                class="composed-bay-anchor"
                data-hunk-target
                data-hunk-kind="real"
                data-file-index={props.fileIndex}
                data-hunk-bay={bare.bay.bay_key}
                data-hunk-index={hunkIndex}
                aria-hidden="true"
              />
            )}
          </For>
          {/* A bare text File has no bay chrome to hang a warning on, so it
              renders directly above the grid it describes. */}
          <BayWarnings bay={bare.bay} />
          <TextBayView
            reviewFile={props.reviewFile}
            fileIndex={props.fileIndex}
            displayName={props.backend_data.display_name}
            bay={bare.bay}
            content={bare.content}
            view={props.view}
            aggressiveFolds={props.aggressiveFolds}
            linePins={props.linePins}
            card={props.card}
            bayRenderModes={props.bayRenderModes}
          />
        </>
      )}
    </Show>
  );
}
