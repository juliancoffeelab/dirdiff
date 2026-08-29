/**
 * Renders one composed File as ordered frames containing independently shown bays.
 *
 * `FrameView` preserves backend frame and bay order, dispatches each bay by its
 * validated kind, and keeps expansion and render mode in the containing card.
 * Text bays choose rich or virtual DOM from current geometry, then use mounted
 * observers for viewport transitions; Navigation may explicitly materialize a
 * virtual bay through its wrapper operation. Image bays remain fully mounted.
 *
 * Frames provide grouping and optional headings, but navigation, review, and
 * line-pin coordinates address bays. This module does not fetch File data,
 * choose backend composition, or select or scroll to a hunk.
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
import type { RealHunkIdentity } from "../navigation";
import { assert, expect } from "../../utils";

/**
 * Describes how one composed bay contributes stable hunk-navigation stops.
 *
 * A hunk is a stop for Next and Previous, so what counts as one is a navigation
 * decision and belongs here rather than on the wire. A hunk's coordinate
 * combines the bay key and the published bay-local `hunk_index`. This walk
 * uses those indexes verbatim. Nothing renumbers them into a file-wide
 * sequence or mutates the payload. Two rules
 * produce the stops while walking frames and bays in document order. The
 * record contains no file-wide position or selected state; callers compose it
 * with the bay key and stable file index when writing actual anchors.
 */
export type BayHunks = {
  /**
   * Contains this bay's navigation stops in document order.
   * Values are the exact bay-local indexes published by composition.
   */
  stops: number[];
  /**
   * Which element carries those stops.
   *
   * `rows` means the mounted rows carry them, so the bay writes anchors only
   * while its body is closed. `bay` means no row can, so the bay writes its one
   * anchor whether it is open or shut.
   */
  carrier: "rows" | "bay";
};

/**
 * Collects every hunk stop in one composed file diff, in document order.
 *
 * Text rows that begin changed runs contribute their published bay-local
 * indexes. A bay whose change is not `unchanged` and has no row stop contributes
 * index zero itself, which keeps image-only and other non-row changes reachable.
 * The returned map has exactly one entry for every composed bay and `total` is
 * the number of stops Next and Previous may visit; the operation never
 * renumbers or mutates input.
 *
 * # Returns
 *
 * - `total` is the File-wide stop count. It equals the sum of every bay's stop
 *   list, including stops carried by changed bays without row stops.
 * - `bays` maps every stable bay key to that bay's ordered local indexes and
 *   carrier. Even unchanged bays with no stops receive an entry, so callers can
 *   require an exact lookup for every composed bay.
 */
export function composedHunks(diff: FileDiff): {
  /**
   * Counts stops Next and Previous may visit across the complete file.
   * The value is the sum of every entry in `bays`, without pseudo-hunks.
   */
  total: number;
  /**
   * Maps every stable composed `bay_key` to its exact stop contract.
   * Even unchanged bays with no stops receive an entry.
   */
  bays: Map<string, BayHunks>;
} {
  const bays = new Map<string, BayHunks>();
  let total = 0;
  for (const frame of diff.frames) {
    for (const bay of frame.bays) {
      const stops: number[] = [];
      // Don't special case a kind here: whether a bay carries its own stops is
      // decided by whether it has rows with hunks in them.
      const rows = "rows" in bay.kind_data ? bay.kind_data.rows : [];
      for (const row of rows) {
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
 * The card derives its whole-File render answer for the FileTree indicator and
 * header marker from the bays it currently mounts, so each bay wrapper
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
   *
   * @param bayKey Stable key registered by the mounted bay wrapper.
   */
  mode(bayKey: string): BayRenderMode;

  /**
   * Registers or changes one mounted bay's representation.
   *
   * A wrapper calls this before reading its own mode and whenever observer
   * policy changes it. `bayKey` is the exact mounted bay identity and `mode` is
   * its complete new representation; the registry must publish the value
   * synchronously so the same render can read it.
   *
   * @param bayKey Stable key of the mounted bay being registered or changed.
   * @param mode Complete rich or virtual representation to publish.
   */
  setMode(bayKey: string, mode: BayRenderMode): void;

  /**
   * Removes one bay's registration when its wrapper unmounts.
   *
   * Cleanup calls this exactly once for the key registered by that wrapper.
   * The registry must stop reporting the bay immediately; it must not preserve
   * this mode for a later remount.
   *
   * @param bayKey Stable key whose mounted registration has ended.
   */
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
  /**
   * Gives the viewport-distance margin at which a virtual bay becomes rich.
   * The value is non-negative and always smaller than `exitViewports`.
   */
  enterViewports: number;

  /**
   * Gives the viewport-distance margin beyond which a rich bay becomes virtual.
   * Its larger value supplies hysteresis and prevents boundary oscillation.
   */
  exitViewports: number;
};

/**
 * Returns the specified rich-entry and virtual-exit distances for one row count.
 *
 * Callers provide the exact backend row count. Invalid counts violate the
 * composed text-bay contract and throw instead of selecting a cost band.
 *
 * @param rowCount Non-negative integer count of backend rows in this text bay.
 */
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

/**
 * Chooses one bay's first representation from current FileCard geometry.
 *
 * A bay whose card intersects the bay's cost-dependent entry zone begins
 * rich; the rest begin virtual and enrich through their own observers. The
 * stable card is the only readable geometry at this moment: the bay's own
 * wrapper does not exist until the choice is made. Cards without measurable
 * geometry violate the mounted-card contract and throw.
 *
 * @param card Mounted stable FileCard whose geometry stands in for the bay.
 * @param rowCount Non-negative backend row count selecting the entry distance.
 */
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
  /**
   * Tests whether this bay intersects its rich-entry zone at a proposed scroll.
   *
   * Navigation may call this repeatedly with a finite, non-negative document
   * `viewportTop` while looking for virtual bays that would become visible.
   * The callback returns geometry only: it must not scroll, enrich, select, or
   * retain resources between calls.
   */
  intersectsRichEntryZone: (viewportTop: number) => boolean;

  /**
   * Materializes this exact bay's rich body for navigation.
   *
   * FileCard line preparation calls it on the mounted target bay whether that
   * bay is virtual or already rich; navigation pre-enrichment calls it for
   * virtual candidates. It resolves after Solid mounts any required rich DOM
   * and lazy row chunks have real geometry. Unmounting may make it resolve with
   * no mounted body, so callers must re-check their own operation lifetime.
   */
  waitToEnrich_impl: () => Promise<void>;
};

/**
 * Returns how many hunks one composed diff has: the stops Next visits.
 *
 * This is the count-only view of `composedHunks`. It includes row-carried stops
 * and one bay-carried stop for a changed bay without row hunks. It excludes File
 * pseudo-hunks and never mutates or renumbers the composed payload.
 *
 * @param diff Complete validated File payload whose frames remain in document order.
 */
export function composedHunkCount(diff: FileDiff): number {
  return composedHunks(diff).total;
}

/**
 * Renders every non-fatal warning belonging to one bay.
 *
 * Engine and format damage both stop at the affected bay. Warnings render even
 * when the bay is closed, so a reviewer sees degraded content before
 * deciding whether to open it.
 */
function BayWarnings(props: {
  /**
   * Supplies the complete bay whose backend and engine warnings are rendered.
   * Warning order and messages are preserved even while the bay is closed.
   */
  bay: BayPayload;
}): JSX.Element {
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

/**
 * Renders one bay's undecorated old/new text while the bay is virtual.
 *
 * The two `pre`s print the bay's rows as plain text, one line per row and
 * aligned with each other so a row absent from a side is a blank line rather
 * than a shift. Rows that begin a changed run write transparent real-hunk
 * targets at their row offsets, because plain text carries no coordinate of
 * its own; a bay whose one stop is carried by the bay itself writes nothing
 * here. Its anchor lives in the bay chrome whether the body is rich or
 * virtual. The body takes the measured rich height when one exists, so a
 * rich-to-virtual replacement cannot move the page under the reader; a
 * never-rich bay keeps its natural text height. It handles no selection,
 * navigation, syntax spans, inline tokens, or rich rows.
 */
function VirtualBay(props: {
  /**
   * Supplies the stable ChangeSet file coordinate for virtual hunk anchors.
   * It composes with `bay.bay_key` and each published bay-local hunk index.
   */
  fileIndex: number;

  /**
   * Supplies stable bay identity and change metadata for this virtual body.
   * Its `kind_data` must be the same text arm passed separately as `content`.
   */
  bay: BayPayload;

  /**
   * Supplies the already-narrowed text rows rendered without syntax decoration.
   * Row order and nullable sides are preserved exactly in the two plain texts.
   */
  content: TextKindPayload;

  /**
   * Preserves the last measured rich-body height across virtualization.
   * Null means the bay has never supplied a rich measurement and uses natural
   * plain-text height rather than an invented estimate.
   */
  reservedRichHeight: number | null;
}): JSX.Element {
  const text = createMemo(() => {
    const leftLines: string[] = [];
    const rightLines: string[] = [];
    const hunkAnchors: {
      /**
       * Retains the exact bay-local hunk identity published by the backend row.
       * Virtual rendering never renumbers it into a file-wide sequence.
       */
      hunkIndex: number;
      /**
       * Locates the source row within virtual plain-text geometry.
       * The zero-based offset is presentation data, not navigation identity.
       */
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

/**
 * Renders one `text` bay's body and owns its rich or virtual representation.
 *
 * It takes the whole bay, for the identity and label every widget needs, and
 * its already-narrowed `text` content, for the rows. The bay key is the
 * sub-file coordinate, passed through verbatim to the grid so line pins and
 * review targets keep their identity. The backend bay label names the grid's
 * content column, so an inline grid over a notebook output is not labelled as
 * code.
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
  /**
   * Identifies the exact captured file pair used by review and line targets.
   * Both nullable paths remain part of target identity.
   */
  reviewFile: ReviewFilePair;
  /**
   * Supplies the stable ChangeSet file coordinate for real hunk anchors.
   * It composes with this bay key and each bay-local hunk index.
   */
  fileIndex: number;
  /**
   * Names the containing file for TextDiffGrid renderer identity.
   * A changed canonical name invalidates renderer-local fold expansion.
   */
  displayName: string;
  /**
   * Carries stable bay key, backend label, and change metadata.
   * Its `kind_data` must be the same text arm supplied as `content`.
   */
  bay: BayPayload;
  /**
   * Contains the already-narrowed text rows, labels, and fold hints.
   * BayBody proves this arm before mounting TextBayView.
   */
  content: TextKindPayload;
  /**
   * Selects the current Tab-wide split or inline text presentation.
   * Virtual plain text remains split because it is a lightweight substitute.
   */
  view: DiffViewMode;
  /**
   * Selects which valid fold hints begin folded in the rich grid.
   * Representation changes do not create another fold-policy value.
   */
  aggressiveFolds: boolean;
  /**
   * Supplies Snapshot-scoped URL line behavior for the rich TextDiffGrid.
   * Virtual presentation exposes anchors but does not copy this state.
   */
  linePins: LinePins;
  /**
   * Reads the stable FileCard used for initial geometry and navigation lifetime.
   * It must return the same mounted element throughout this bay wrapper's life.
   */
  card: Accessor<HTMLElement>;
  /**
   * Provides the mounted-only registry holding this wrapper's representation.
   * The component registers before its first read and clears on unmount.
   */
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
    initialRenderMode(props.card(), props.content.rows.length),
  );
  onCleanup(() => props.bayRenderModes.clearMode(bayKey));
  /**
   * Reads this mounted bay's authoritative rich or virtual representation.
   *
   * Registration occurs before the first read and cleanup removes the key only
   * after the wrapper stops rendering, so an absent value is always a lifecycle
   * violation in the registry rather than a default mode.
   */
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

  /**
   * Tests this bay against its rich-entry zone at a proposed scroll position.
   *
   * Navigation supplies a finite non-negative document viewport top. The
   * result applies the same row-cost threshold as the local
   * IntersectionObserver without scrolling or changing representation. It
   * must not be used for the larger rich-exit zone.
   */
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

  /**
   * Publishes this mounted bay's navigation operations and manages its rich zones.
   *
   * Mount is the first point at which `wrapper` is connected and can supply
   * viewport geometry. The two observers use the current row cost and viewport
   * height to enter rich rendering near the viewport and return distant bays to
   * virtual rendering. A resize replaces both observers so their fixed root
   * margins continue to describe the current viewport.
   *
   * The operations, observers, and resize listener live only as long as this
   * wrapper. Cleanup disconnects both observers, removes the listener, and
   * removes the published DOM operations so navigation cannot call a detached
   * bay.
   */
  onMount(() => {
    Object.assign(wrapper, {
      intersectsRichEntryZone,
      waitToEnrich_impl,
    }) satisfies EnrichableBay;
    const rowCount = props.content.rows.length;
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
          if (!currentEntry.isIntersecting) {
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
          viewMode={props.view}
          aggressiveFolds={props.aggressiveFolds}
          linePins={props.linePins}
        />
      </Show>
    </div>
  );
}

/**
 * Dispatches one bay to the widget for its `kind_data`.
 *
 * The switch is exhaustive over the kind union, and it is the only place either
 * kind is examined. Each widget receives the whole bay for identity and label
 * and its own narrowed arm for content, so no widget reads a `kind` of its own.
 * A new bay kind adds a branch here and its widget module; there is no other
 * dispatch point.
 */
function BayBody(props: {
  /**
   * Identifies the captured file pair delegated to review-capable widgets.
   * Dispatch does not reinterpret either nullable path.
   */
  reviewFile: ReviewFilePair;
  /**
   * Supplies the stable ChangeSet file coordinate to widgets that write hunk,
   * review, and line-pin coordinates.
   * It has meaning only when composed with this bay's local identities.
   */
  fileIndex: number;
  /**
   * Provides the canonical file name used by text renderer identity.
   * Image rendering does not consume or substitute it.
   */
  displayName: string;
  /**
   * Carries the complete envelope whose discriminant selects one widget.
   * The same envelope supplies identity and label after its content is narrowed.
   */
  bay: BayPayload;
  /**
   * Selects Tab-wide split or inline presentation for text content.
   * Image content receives it only for its established side presentation.
   */
  view: DiffViewMode;
  /**
   * Selects the current fold policy for text content.
   * Non-text widgets neither inspect nor persist the value.
   */
  aggressiveFolds: boolean;
  /**
   * Supplies Snapshot-scoped URL line behavior to the selected widget.
   * Dispatch retains no second navigation representation.
   */
  linePins: LinePins;
  /**
   * Reads the stable FileCard used by text-bay geometry and lifecycle checks.
   * The image branch does not consume it, but dispatch keeps one complete shape.
   */
  card: Accessor<HTMLElement>;
  /**
   * Provides the mounted-only representation registry to text widgets.
   * Non-text widgets do not register a text render mode.
   */
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

/**
 * Card-owned expansion state for the bays of one composed diff.
 *
 * A bay body unmounts whenever its File collapses or turns virtual, so
 * expansion kept inside the bay component would reset to the backend
 * default on every remount. The card outlives those replacements and stores
 * expansion just as it stores the File's own `expanded` value. Each
 * bay reads and writes only its own key through this interface. Line-pin
 * preparation writes it directly instead of reaching a mounted bay through
 * the DOM.
 */
export type BayExpansion = {
  /**
   * Whether the bay is expanded: its recorded choice, or its backend
   * `default_expanded` while none has been made.
   *
   * @param bay Complete bay whose stable key and default determine the answer.
   */
  isExpanded(bay: BayPayload): boolean;

  /**
   * Records one explicit expansion choice for an exact bay.
   *
   * The bay header calls this after direct activation, while line preparation
   * calls it with `true` before looking for a closed target bay. `bayKey` is
   * the exact composed key and `expanded` is the complete accepted state. The
   * card must publish the choice synchronously so remounts retain it.
   *
   * @param bayKey Stable composed identity of the bay being changed.
   * @param expanded Complete expansion state to retain at FileCard lifetime.
   */
  setExpanded(bayKey: string, expanded: boolean): void;
};

/**
 * Renders one bay's own changed-line counts beside its label.
 *
 * A closed bay shows no rows, so without this a reviewer would have to
 * expand it to learn whether it is worth expanding. Counts come from the
 * bay's backend stats; a count of zero is omitted rather than shown as zero,
 * so the summary stays readable at a glance.
 *
 * A bay with no lines has no such counts and renders nothing here. Printing
 * three zeroes for a changed image would claim the engine looked and found
 * nothing, when the truth is that lines are the wrong unit for it; the bay's
 * tint and its frame's status already say what happened.
 */
function BayStats(props: {
  /**
   * Supplies one complete bay whose kind may expose line statistics.
   * Kinds without statistics render no placeholders or invented zero counts.
   */
  bay: BayPayload;
}): JSX.Element {
  // Counts belong to whichever kind reports them, so this asks the payload for
  // its stats instead of naming the kinds allowed to have any.
  /**
   * Reads line statistics only from bay kinds that actually report them.
   * Null means lines are not a valid metric for this kind, not that all counts
   * are zero.
   */
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

/**
 * Renders one bay with its backend label and its own expansion state.
 *
 * Expansion starts at the backend's `default_expanded`; the reviewer changes
 * only this bay's expansion from its header, and that decision affects no
 * other bay, the File, or any selection. The state itself lives in the
 * card's `BayExpansion`, so it survives this component unmounting with a
 * collapsed or virtual File.
 *
 * A bay with `collapsible=false` is the frame's body, not an additional item
 * inside it. It therefore renders bare, with no header or toggle. Other bays
 * carry the label and disclosure control themselves.
 *
 * A bay writes its own anchors when it carries its stops itself, and when it
 * is closed so its rows are not mounted to carry them. The first anchor is
 * where Next lands: a change the reviewer cannot land on is a hidden change, and
 * a bay closed by default is exactly where that would happen. The rest
 * stay in the DOM as skipped targets, keeping their coordinates without being
 * traversed until the reviewer expands the bay.
 */
function BayView(props: {
  /**
   * Identifies the exact captured file pair for review-capable bay widgets.
   * It remains unchanged while the bay closes and remounts.
   */
  reviewFile: ReviewFilePair;
  /**
   * Supplies the stable ChangeSet coordinate for this bay's hunk anchors.
   * The bay key and local hunk index complete each identity.
   */
  fileIndex: number;
  /**
   * Provides the canonical containing-file name to text renderer identity.
   * Bay chrome presents its own backend label instead.
   */
  displayName: string;
  /**
   * Contains this bay's stable identity, chrome facts, and kind payload.
   * Expansion and hunk records must refer to this exact `bay_key`.
   */
  bay: BayPayload;
  /**
   * Contains navigation stops derived for this exact bay payload.
   * BayView chooses whether chrome or mounted rows carry them.
   */
  hunks: BayHunks;
  /**
   * Selects Tab-wide split or inline presentation for mounted text content.
   * It does not affect chrome expansion or hunk identity.
   */
  view: DiffViewMode;
  /**
   * Selects initial folding policy for mounted text content.
   * Bay visibility remains a separate card-lifetime decision.
   */
  aggressiveFolds: boolean;
  /**
   * Supplies Snapshot-scoped URL line behavior to the mounted widget.
   * BayView itself only controls bay visibility and anchors.
   */
  linePins: LinePins;
  /**
   * Reads the stable FileCard used by mounted text-bay virtualization.
   * It must retain element identity while this bay remains mounted.
   */
  card: Accessor<HTMLElement>;
  /**
   * Provides card-lifetime expansion state for this exact bay.
   * Header activation reads and writes only `bay.bay_key` through it.
   */
  bayExpansion: BayExpansion;
  /**
   * Provides the mounted-only text representation registry to BayBody.
   * BayView does not create entries for non-text content.
   */
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

  /**
   * Reads this bay's card-lifetime expansion decision or backend default.
   * Bays whose API payload has `collapsible=false` may still have a stored
   * value, but `shown` ignores it.
   */
  const expanded = (): boolean => props.bayExpansion.isExpanded(props.bay);

  /**
   * Reports whether the bay body must be mounted in the current render.
   * Frame bodies with `collapsible=false` are always shown; other bays follow
   * expansion.
   */
  const shown = (): boolean => !props.bay.collapsible || expanded();
  // The bay writes anchors when it carries the stops itself, and when it is
  // closed and its rows are not mounted to carry them. Which of the two it
  // is does not change what gets written, only whether anything does.
  /**
   * Selects bay-local hunk indexes that chrome must carry in this state.
   * Row carriers take over while shown; bay carriers and closed bays retain
   * their coordinates here without renumbering.
   */
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

/**
 * Renders every frame and bay of one composed diff in backend order.
 *
 * Frames appear in document order; a frame with a heading renders it above its
 * bays. The rendered rows keep the exact backend order, labels, and hints.
 */
export function FrameView(props: {
  /**
   * Identifies the exact captured file pair for review-capable bay widgets.
   * It is shared unchanged by every bay in this composed file.
   */
  reviewFile: ReviewFilePair;
  /**
   * Supplies the stable ChangeSet coordinate for every real hunk anchor.
   * Bay key and local hunk index complete each compound identity.
   */
  fileIndex: number;
  /**
   * Contains the complete immutable composed file to render in backend order.
   * Its frame and bay structure is presented without frontend reshaping.
   */
  backend_data: FileDiff;
  /**
   * Selects Tab-wide split or inline presentation for every text bay.
   * Frame and bay chrome remain independent of this choice.
   */
  view: DiffViewMode;
  /**
   * Selects initial folding policy for every mounted text grid.
   * It does not control bay visibility or file collapse.
   */
  aggressiveFolds: boolean;
  /**
   * Supplies the one Snapshot-scoped URL line interface shared by all widgets.
   * FrameView neither parses nor copies its navigation state.
   */
  linePins: LinePins;
  /**
   * Reads the stable FileCard used by text-bay geometry and lifecycle checks.
   * Every mounted text bay must observe the same element identity.
   */
  card: Accessor<HTMLElement>;
  /**
   * Provides card-lifetime expansion choices shared across bay remounts.
   * Each BayView reads and writes only its own stable key.
   */
  bayExpansion: BayExpansion;
  /**
   * Provides the mounted-only registry of current text-bay representations.
   * FullFile aggregates its entries into one File-level indicator.
   */
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
  const bayHunks = (bay: BayPayload): BayHunks => {
    return expect(
      hunks().get(bay.bay_key),
      `Bay ${bay.bay_key} is absent from its File's hunk stops.`,
    );
  };

  /**
   * Returns this File's single bare text bay, or null when it has chrome.
   *
   * A plain text File composes into one frame holding one text bay with
   * `collapsible=false`, and must render as one bare grid with no heading or bay
   * header. Any other shape renders as frames.
   *
   * # Returns
   *
   * - The sole bay envelope paired with its narrowed text payload when the File
   *   has the exact flat-file shape.
   * - `null`: The File has multiple frames or bays, a non-text payload, or a
   *   non-flat key. The caller renders the ordinary framed representation.
   */
  const bareTextBay = (): {
    /**
     * Contains the sole flat-file bay envelope passed without chrome.
     * Its key is proven to be the established flat-file key.
     */
    bay: BayPayload;
    /**
     * Contains the already-narrowed text arm of the same `bay`.
     * The shape check rejects every other kind before returning it.
     */
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
