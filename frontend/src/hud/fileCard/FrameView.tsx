/**
 * Renders one composed diff as frames of bays, each bay by its `kind`.
 *
 * This is the frontend's extension axis: `FileBody` mounts `FrameView`, which
 * walks the backend's frames in order and dispatches each bay to the widget
 * for its kind. A frame is presentational grouping only; its optional heading is
 * backend-authored, and everything a reviewer navigates to, collapses, or
 * comments on belongs to a bay.
 *
 * Callers provide one complete immutable composed `FileDiff`, the current view
 * mode, fold policy, file identity, the shared line-pin interface, and the
 * card-owned `BayExpansion` and `BayRenderModes`. This module owns no
 * fetching, navigation, selection, or backend-shape decision; it draws what
 * composition already decided, and it owns each mounted text bay's rich or
 * virtual representation: a mounting bay chooses its first mode from current
 * card geometry, its own IntersectionObservers are the sole transition
 * mechanism afterwards, and navigation reaches a bay through the enrichment
 * operations its wrapper exposes.
 *
 * A composed diff whose only bay is a flatfile's renders as a bare grid,
 * exactly the DOM a structureless text file has always produced. Bay chrome —
 * the backend-authored label and its expansion toggle — appears only for a file
 * that actually composes into several bays, because that is the only case
 * where a reviewer needs to be told which bay they are reading.
 *
 * The only bay kind today is `text`, which delegates to the established
 * `TextDiffGrid`. An `image` or `binary` kind adds its widget here beside the text
 * one; nothing about the frame walk or the envelope changes to admit it.
 */

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
  type FileDiff,
  type Frame,
  type Bay,
  type ReviewFilePair,
  type TextBay,
} from "../../api/api";
import type { DiffViewMode } from "../App";
import { finishForcedChunkLayout, forceChunkLayout } from "./grids/text/rowDom";

import { TextDiffGrid } from "./grids/text/TextDiffGrid";
import type { LinePins } from "../linePins";
import type { RealHunkIdentity } from "../navigation";

/**
 * Collects every hunk stop in one composed diff, in document order.
 *
 * A hunk is a stop for Next and Previous, so what counts as one is a navigation
 * decision and belongs here rather than on the wire. A hunk's coordinate is
 * compound — the bay key plus the bay-local `hunk_index` composition
 * published — and this walk uses those published indexes verbatim; nothing
 * renumbers them into a file-wide sequence or mutates the payload. Two rules
 * produce the stops, applied while walking frames and bays in document
 * order:
 *
 * - a text bay contributes one stop per row that begins a changed run,
 *   in row order, at that row's own `hunk_index`;
 * - a bay that reports `changed` while contributing no such row takes one
 *   stop of its own, at index zero. Re-running a notebook replaces a plot's
 *   image bytes while its rendered text still reads
 *   `<Figure size 640x480 with 1 Axes>`, so the engine sees two identical
 *   strings and marks no row. Without this rule the change would be displayed
 *   and impossible to land on. The index cannot collide with a row's, because
 *   a bay carries row stops or its one bay stop, never both.
 *
 * `total` is the File's hunk count: the number of stops Next visits.
 */
export type BayHunks = {
  /** This bay's stops, in document order, as bay-local indexes. */
  stops: number[];
  /**
   * Which element carries those stops.
   *
   * `rows` means the mounted rows carry them, so the bay writes anchors only
   * while collapsed. `bay` means no row can, so the bay writes its one
   * anchor whether it is open or shut.
   */
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
      for (const row of bay.rows) {
        if (row.hunk_index !== null) {
          stops.push(row.hunk_index);
        }
      }
      const carrier = stops.length > 0 ? "rows" : "bay";
      if (carrier === "bay" && bay.change.kind !== "unchanged") {
        stops.push(0);
      }
      total += stops.length;
      bays.set(bay.bay_key, { stops, carrier });
    }
  }
  return { total, bays };
}

/**
 * Represents the complete local body representation for one mounted text bay.
 *
 * Rich means the natural interactive grid; virtual means the bay's complete
 * plain split text. The mode must never represent loading, file or bay
 * expansion, or navigation.
 */
export type BayRenderMode = "rich" | "virtual";

/**
 * Card-owned registry of every mounted bay body's render mode.
 *
 * The card derives its whole-File render answer — the FileTree indicator and
 * the header marker — from the bays it currently mounts, so each bay wrapper
 * registers itself here for exactly its mounted lifetime. The registry is the
 * one authoritative representation of a mounted bay's mode; the bay itself
 * reads its mode back through `mode()`. It holds no unmounted bay and never
 * persists a mode across a remount: every mounting bay chooses afresh from
 * current card geometry.
 */
export type BayRenderModes = {
  /**
   * Reads one registered bay's current mode.
   *
   * Only a mounted, registered bay may be read; an unregistered key is a
   * lifecycle violation and throws.
   */
  mode(bayKey: string): BayRenderMode;
  /** Registers or changes one mounted bay's mode under its `bay_key`. */
  setMode(bayKey: string, mode: BayRenderMode): void;
  /** Removes one bay's registration when its wrapper unmounts. */
  clearMode(bayKey: string): void;
};

/**
 * Classifies one mounted text bay by the cost of fully rendering its rows.
 *
 * `small` means 0–250 rows, `medium` means 251–1000 rows, and `large` means
 * 1001 or more rows. The value controls viewport lead distance and must not
 * encode hunk, selection, or global state.
 */
type BayCost = "small" | "medium" | "large";

/**
 * Defines the two viewport distances governing one bay cost band.
 *
 * `enterViewports` is the distance at which the bay becomes rich;
 * `exitViewports` is the larger distance beyond which it becomes virtual.
 */
type RichZone = {
  enterViewports: number;
  exitViewports: number;
};

/**
 * Returns the specified rich-entry and virtual-exit distances for one row count.
 *
 * Callers provide the exact backend row count. Invalid counts violate the
 * composed text-bay contract and throw instead of selecting a cost band.
 */
function richZone(rowCount: number): RichZone {
  if (!Number.isInteger(rowCount) || rowCount < 0) {
    throw new Error("Virtualization requires a non-negative row count.");
  }
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

/**
 * Chooses one bay's first representation from current FileCard geometry.
 *
 * A bay whose card intersects the bay's cost-dependent entry zone begins
 * rich; the rest begin virtual and enrich through their own observers. The
 * stable card is the only readable geometry at this moment: the bay's own
 * wrapper does not exist until the choice is made. Cards without measurable
 * geometry violate the mounted-card contract and throw.
 */
function initialRenderMode(card: HTMLElement, rowCount: number): BayRenderMode {
  const viewportHeight = window.innerHeight;
  const rect = card.getBoundingClientRect();
  if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) {
    throw new Error(
      "Initial virtualization requires a finite positive viewport height.",
    );
  }
  if (!Number.isFinite(rect.top) || !Number.isFinite(rect.bottom)) {
    throw new Error(
      "Initial virtualization requires a finite FileCard rectangle.",
    );
  }
  if (rect.width === 0 && rect.height === 0) {
    throw new Error(
      "Initial virtualization requires measurable FileCard geometry.",
    );
  }
  const margin = richZone(rowCount).enterViewports * viewportHeight;
  return rect.bottom >= -margin && rect.top <= viewportHeight + margin
    ? "rich"
    : "virtual";
}

/**
 * Describes the navigation geometry and rich-materialization operations
 * attached by every mounted bay wrapper.
 *
 * The wrapper is the element carrying `data-bay-render`. `waitToEnrich_impl()`
 * is the general materialization operation. `intersectsRichEntryZone()`
 * applies the bay's exact row-cost policy at a proposed scroll position.
 * Neither operation changes selected identity, counters, or scroll position.
 */
type EnrichableBay = HTMLElement & {
  intersectsRichEntryZone: (viewportTop: number) => boolean;
  waitToEnrich_impl: () => Promise<void>;
};

/**
 * Returns how many hunks one composed diff has: the stops Next visits.
 */
export function composedHunkCount(diff: FileDiff): number {
  return composedHunks(diff).total;
}

/**
 * Renders the engine warning belonging to one bay, when it has one.
 *
 * A warning is the engine's report that it gave up matching *these* rows, so it
 * belongs beside the rows it describes rather than on the File: one composed
 * File holds many bays and only some of them carry a warning. It renders
 * whether or not the bay is expanded, because a reviewer needs to know the
 * rows are unreliable before deciding to open them.
 */
function BayWarning(props: { bay: Bay }): JSX.Element {
  return (
    <Show when={props.bay.engine_warning}>
      {(warning) => (
        <p class="composed-bay-warning" title={warning().message}>
          {warning().message}
        </p>
      )}
    </Show>
  );
}

/**
 * Renders one bay's undecorated old/new text while the bay is virtual.
 *
 * The two `pre`s print the bay's rows as plain text, one line per row and
 * aligned with each other so a row absent from a side is a blank line rather
 * than a shift. Rows that begin a changed run write transparent real-hunk
 * targets at their row offsets, because plain text carries no coordinate of
 * its own; a bay whose one stop is carried by the bay itself writes nothing
 * here — its anchor lives in the bay chrome whether the body is rich or
 * virtual. The body takes the measured rich height when one exists, so a
 * rich-to-virtual replacement cannot move the page under the reader; a
 * never-rich bay keeps its natural text height. It handles no selection,
 * navigation, syntax spans, inline tokens, or rich rows.
 */
function VirtualBay(props: {
  fileIndex: number;
  bay: TextBay;
  reservedRichHeight: number | null;
}): JSX.Element {
  const text = createMemo(() => {
    const leftLines: string[] = [];
    const rightLines: string[] = [];
    const hunkAnchors: { hunkIndex: number; rowOffset: number }[] = [];
    props.bay.rows.forEach((row, rowOffset) => {
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

/**
 * Renders one `text` bay's body and owns its rich or virtual representation.
 *
 * The bay key is the sub-file coordinate, passed through verbatim to the
 * grid so line pins and review targets keep their identity. The backend bay
 * label names the grid's content column, so an inline grid over a notebook
 * output is not labelled as code.
 *
 * The persistent wrapper element carries `data-bay-render` and
 * `data-bay-key`, registers its mode in the card's `BayRenderModes` for
 * exactly its mounted lifetime, and exposes the `EnrichableBay` operations as
 * its DOM interface. A mounting bay chooses its first mode through
 * `initialRenderMode` from the card's current rectangle, so a bay inside its
 * own entry zone paints rich on its very first frame; the two
 * IntersectionObservers below are the only transition mechanism after that
 * choice. Leaving rich pins the measured grid
 * height onto the virtual body solely so the replacement cannot move the
 * page under the reader; the pinned value approximates nothing else, and the
 * viewport-sized hysteresis bands tolerate it going stale.
 */
function TextBayView(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  displayName: string;
  bay: TextBay;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  card: Accessor<HTMLElement>;
  bayRenderModes: BayRenderModes;
}): JSX.Element {
  // Held as HTMLElement: the enrichment attachment below converts it to the
  // EnrichableBay DOM interface, which is declared on the element base type.
  let wrapper!: HTMLElement;
  const bayKey = props.bay.bay_key;
  // Registration spans the component lifetime, not onMount's: the mode is
  // read by the JSX below before mount, so the key must already exist.
  props.bayRenderModes.setMode(
    bayKey,
    initialRenderMode(props.card(), props.bay.rows.length),
  );
  onCleanup(() => props.bayRenderModes.clearMode(bayKey));
  const mode = (): BayRenderMode => props.bayRenderModes.mode(bayKey);
  const [reservedRichHeight, setReservedRichHeight] = createSignal<
    number | null
  >(null);

  /**
   * Changes only this bay's representation and records usable rich height.
   *
   * Observer callbacks call this operation directly. A mounted bay always has
   * a body, so leaving rich requires measurable grid geometry. The operation
   * performs no navigation, selected-hunk, ChangeSet, or scrolling behavior.
   */
  function changeRenderMode(next: BayRenderMode): void {
    if (mode() === next) {
      return;
    }
    if (next === "virtual") {
      // An off-screen grid with unwarmed chunks measures the intrinsic
      // estimate, not its real height; pinning that onto the virtual body
      // moves the page under the reader. Force real layout first — the grid
      // unmounts with this transition, so the visible chunks need no
      // restoration.
      forceChunkLayout(wrapper);
      const measuredHeight = wrapper.getBoundingClientRect().height;
      if (!Number.isFinite(measuredHeight) || measuredHeight <= 0) {
        throw new Error(
          "Rich bay body must have a finite positive height before virtualization.",
        );
      }
      setReservedRichHeight(measuredHeight);
    }
    props.bayRenderModes.setMode(bayKey, next);
  }

  /**
   * Materializes this bay's grid for one explicit navigation action.
   *
   * The operation changes only this bay's representation and resolves after
   * Solid has mounted the grid and its chunks report real geometry. It never
   * expands, selects, calculates counters, scrolls, or fetches.
   */
  async function waitToEnrich_impl(): Promise<void> {
    changeRenderMode("rich");
    // `changeRenderMode` writes a store key; the grid it selects is mounted
    // by Solid's flush, not by the assignment. Yielding the microtask lets
    // that flush run so the chunks read below exist. Nothing here waits on
    // layout or paint — only on the render Solid has already scheduled.
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

  /**
   * Tests this bay against its rich-entry zone at a proposed scroll position.
   *
   * Navigation supplies a finite non-negative document viewport top. The
   * result applies the same row-cost threshold as the local
   * IntersectionObserver without scrolling or changing representation. It
   * must not be used for the larger rich-exit zone.
   */
  function intersectsRichEntryZone(viewportTop: number): boolean {
    if (!Number.isFinite(viewportTop) || viewportTop < 0) {
      throw new Error(
        "Rich-entry geometry requires a finite non-negative viewport top.",
      );
    }
    const viewportHeight = window.innerHeight;
    if (viewportHeight <= 0) {
      throw new Error(
        "Rich-entry geometry requires a positive viewport height.",
      );
    }
    const rect = wrapper.getBoundingClientRect();
    if (!Number.isFinite(rect.top) || !Number.isFinite(rect.bottom)) {
      throw new Error("Rich-entry geometry requires a finite bay rectangle.");
    }
    const bayTop = window.scrollY + rect.top;
    const bayBottom = window.scrollY + rect.bottom;
    const margin =
      richZone(props.bay.rows.length).enterViewports * viewportHeight;
    return (
      bayBottom >= viewportTop - margin &&
      bayTop <= viewportTop + viewportHeight + margin
    );
  }

  onMount(() => {
    const enrichableBay = wrapper as EnrichableBay;
    enrichableBay.intersectsRichEntryZone = intersectsRichEntryZone;
    enrichableBay.waitToEnrich_impl = waitToEnrich_impl;
    const rowCount = props.bay.rows.length;
    let enterObserver: IntersectionObserver | null = null;
    let exitObserver: IntersectionObserver | null = null;

    /**
     * Rebuilds both cost-zone observers from the current viewport height.
     *
     * The mount lifecycle calls this initially and after each window resize.
     * Existing observers are disconnected before their replacements attach.
     */
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
          const entry = entries[entries.length - 1];
          if (entry === undefined) {
            throw new Error("Rich-zone observer omitted its bay entry.");
          }
          if (entry.isIntersecting) {
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
          const entry = entries[entries.length - 1];
          if (entry === undefined) {
            throw new Error("Virtual-zone observer omitted its bay entry.");
          }
          if (!entry.isIntersecting) {
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
    onCleanup(() => {
      if (enterObserver !== null) {
        enterObserver.disconnect();
      }
      if (exitObserver !== null) {
        exitObserver.disconnect();
      }
      window.removeEventListener("resize", observeCurrentZones);
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
          leftLabel={props.bay.left_label}
          rightLabel={props.bay.right_label}
          rows={props.bay.rows}
          foldHints={props.bay.fold_hints}
          viewMode={props.view}
          aggressiveFolds={props.aggressiveFolds}
          linePins={props.linePins}
        />
      </Show>
    </div>
  );
}

/**
 * Dispatches one bay to the widget for its `kind`.
 *
 * The switch is exhaustive over the bay union. A new bay kind adds a
 * branch here and its widget module; there is no other dispatch point.
 */
function BayBody(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  displayName: string;
  bay: Bay;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  card: Accessor<HTMLElement>;
  bayRenderModes: BayRenderModes;
}): JSX.Element {
  switch (props.bay.kind) {
    case "text":
      return (
        <TextBayView
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
      );
  }
}

/**
 * Card-owned expansion state for the bays of one composed diff.
 *
 * A bay body unmounts whenever its File collapses or turns virtual, so
 * expansion kept inside the bay component would reset to the backend
 * default on every remount. The card outlives those replacements — the same
 * ownership the File's own `expanded` has — so it holds the state, and each
 * bay reads and writes only its own key through this interface. Line-pin
 * preparation writes it directly instead of reaching a mounted bay through
 * the DOM.
 */
export type BayExpansion = {
  /**
   * Whether the bay is expanded: its recorded choice, or its backend
   * `default_expanded` while none has been made.
   */
  isExpanded(bay: Bay): boolean;
  /** Records one bay's expansion choice under its `bay_key`. */
  setExpanded(bayKey: string, expanded: boolean): void;
};

/**
 * Renders one bay's own changed-line counts beside its label.
 *
 * A collapsed bay shows no rows, so without this a reviewer would have to
 * expand it to learn whether it is worth expanding. Counts come from the
 * bay's backend stats; a count of zero is omitted rather than shown as zero,
 * so the summary stays readable at a glance.
 */
function BayStats(props: { bay: Bay }): JSX.Element {
  const counts = (): { added: number; modified: number; removed: number } => ({
    added: props.bay.stats.added_lines,
    modified: props.bay.stats.modified_lines,
    removed: props.bay.stats.removed_lines,
  });
  return (
    <span class="composed-bay-stats">
      <Show when={counts().added > 0}>
        <span class="delta added">+{counts().added}</span>
      </Show>
      <Show when={counts().modified > 0}>
        <span class="delta changed">~{counts().modified}</span>
      </Show>
      <Show when={counts().removed > 0}>
        <span class="delta removed">-{counts().removed}</span>
      </Show>
    </span>
  );
}

/**
 * Renders one bay with its backend label and its own expansion state.
 *
 * Expansion starts at the backend's `default_expanded`; the reviewer changes
 * only this bay's expansion from its header, and that decision affects no
 * other bay, the File, or any selection. The state itself lives in the
 * card's `BayExpansion`, so it survives this component unmounting with a
 * collapsed or virtual File.
 *
 * A bay the backend marks non-collapsible is the frame's body — the thing
 * the frame is, not one more thing hanging off it — so it renders
 * bare, with no header and no toggle. Everything attached to it carries the
 * label and the disclosure control instead.
 *
 * A bay writes its own anchors when it carries its stops itself, and when it
 * is collapsed so its rows are not mounted to carry them. The first anchor is
 * where Next lands: a change the reviewer cannot land on is a hidden change, and
 * a bay collapsed by default is exactly where that would happen. The rest
 * stay in the DOM as skipped targets, keeping their coordinates without being
 * traversed until the reviewer expands the bay.
 */
function BayView(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  displayName: string;
  bay: Bay;
  hunks: BayHunks;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  card: Accessor<HTMLElement>;
  bayExpansion: BayExpansion;
  bayRenderModes: BayRenderModes;
}): JSX.Element {
  /**
   * Names the palette entry for one bay's backend-authored `change`.
   *
   * The five kinds are the composer's own vocabulary, so the frontend picks a
   * colour and infers nothing. Whether a cell moved, was inserted, or was
   * edited is a fact about the document that only the format builder can
   * determine; two of those cases produce rows that are identical on both
   * sides.
   */
  function changeTone(change: BayChange): string {
    return `composed-bay-${change.kind}`;
  }

  const expanded = (): boolean => props.bayExpansion.isExpanded(props.bay);
  const shown = (): boolean => !props.bay.collapsible || expanded();
  // The bay writes anchors when it carries the stops itself, and when it is
  // collapsed and its rows are not mounted to carry them. Which of the two it
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
              // An untouched bay is collapsible too, and colouring it would
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
      <BayWarning bay={props.bay} />
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

/**
 * Renders every frame and bay of one composed diff in backend order.
 *
 * Frames appear in document order; a frame with a heading renders it above its
 * bays. The rendered rows keep the exact backend order, labels, and hints.
 */
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
  /**
   * Names the palette entry for one frame, from its body bay's `change`.
   *
   * A frame is coloured by what happened to the thing it frames. A frame whose
   * body the reviewer may hide is one nothing happened to, which the builder
   * already said by composing it `unchanged`.
   */
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

  /**
   * Returns the stops for one bay, which the walk always produced.
   */
  const bayHunks = (bay: Bay): BayHunks => {
    const value = hunks().get(bay.bay_key);
    if (value === undefined) {
      throw new Error(
        `Bay ${bay.bay_key} is absent from its File's hunk stops.`,
      );
    }
    return value;
  };

  /**
   * Returns this File's single bare text bay, or null when it has chrome.
   *
   * A plain text File composes into one frame holding one non-collapsible text
   * bay, and must render as one bare grid with no heading and no bay
   * header. Any other shape renders as frames.
   */
  const bareTextBay = (): TextBay | null => {
    const diff = props.backend_data;
    if (diff.frames.length !== 1) {
      return null;
    }
    const frame = diff.frames[0];
    if (frame.bays.length !== 1) {
      return null;
    }
    const bay = frame.bays[0];
    if (bay.kind !== "text" || bay.bay_key !== FLATFILE_BAY_KEY) {
      return null;
    }
    return bay;
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
                // name at an end — a notebook's prose cells carry no
                // prompt — has nothing to point at, and says only that it
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
      {(bay) => (
        <>
          {/* A bare text File has no bay chrome to hang a warning on, so it
              renders directly above the grid it describes. */}
          <BayWarning bay={bay} />
          <TextBayView
            reviewFile={props.reviewFile}
            fileIndex={props.fileIndex}
            displayName={props.backend_data.display_name}
            bay={bay}
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
