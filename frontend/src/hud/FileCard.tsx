/**
 * Renders one stable manifest-position FileCard and its complete state branch.
 *
 * The module exports FileCard and its HunkPosition display contract. FileCard contains
 * HuskFile, LazyFile, FullFile, their distinct headers, FileBody dispatch,
 * a FullFile renderer ErrorBoundary, and the explicit lazy-load affordance.
 * Callers provide a reactive derived state, ChangeSet-owned expansion, hunk
 * display accessors, and the single-lane load command. This module must not
 * observe queries, schedule HTTP work, own ChangeSet progress, or navigate
 * hunks. File representation changes remain internal to FileCard.
 */
import {
  ErrorBoundary,
  For,
  Show,
  createSignal,
  onCleanup,
  onMount,
  type Accessor,
  type JSX,
} from "solid-js";
import { LoaderCircle } from "lucide-solid";
import type {
  DiffEngine,
  FileDiff,
  LazyInfoFile,
  ReviewFilePair,
  TextFileDiff,
} from "../api/api";
import {
  ErrorPanel,
  RetryButton,
  presentError,
  useToasts,
} from "../comp/Toasts";
import type { DiffViewMode } from "./App";
import { DiffGrid } from "./DiffGrid";
import type { LinePins, LinePinTarget, PreparedLine } from "./linePins";
import { NotebookFile } from "./NotebookFile";
import type { PseudoHunkIdentity, RealHunkIdentity } from "./navigation";
import { useReview } from "./Review";

/**
 * Classifies one hydrated text file by the cost of fully rendering its rows.
 *
 * `small` means 0–250 rows, `medium` means 251–1000 rows, and `large` means
 * 1001 or more rows. The value controls viewport lead distance and must not
 * encode hunk, selection, or global state.
 */
type FileCost = "small" | "medium" | "large";

/**
 * Defines the two viewport distances governing one text-file cost band.
 *
 * `enterViewports` is the distance at which the whole file becomes rich;
 * `exitViewports` is the larger distance beyond which it becomes virtual.
 */
type RichZone = {
  enterViewports: number;
  exitViewports: number;
};

/**
 * Represents the complete local body representation for one FullFile.
 *
 * Rich means the natural interactive renderer; virtual means complete plain
 * split text. The mode must never represent loading, file expansion, or navigation.
 */
type FileRenderMode = "rich" | "virtual";

/**
 * Describes the navigation geometry and rich-materialization operations attached
 * by FullFile.
 *
 * Every FullFile exposes both methods as one DOM interface. `waitToEnrich()` is
 * the general materialization operation. `intersectsRichEntryZone()` is valid
 * only for a virtualizable text FullFile currently exposing `.virtual-file-body`
 * and applies that file's exact row-cost policy. Neither operation changes
 * selected identity, counters, or scroll position.
 */
type EnrichableFileCard = HTMLElement & {
  intersectsRichEntryZone: (viewportTop: number) => boolean;
  waitToEnrich_impl: () => Promise<void>;
  prepareLine_impl: (
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ) => Promise<PreparedLine>;
};

/**
 * Represents selected position data within one global or file-local scope.
 *
 * `current` is the selected identity's one-based stable DOM position and is
 * null only when that scope has no selected position. `total` counts currently
 * participating targets only. A skipped selection keeps its stable position,
 * so `current` may exceed `total`. Consumers format these numbers and must not
 * use them for navigation.
 */
export type HunkPosition = {
  current: number | null;
  total: number;
};

/**
 * Defines the exact derived hunk data required by every FileCard header.
 *
 * Null access results exist only before the ChangeSet performs its initial DOM
 * calculation. The global value also reports whether more hunk targets can
 * become available. FileCard formats the values but never changes or navigates
 * from them.
 */
type HunkCounterProps = {
  globalSelectedHunk: Accessor<{
    position: HunkPosition;
    hasMore: boolean;
  } | null>;
  fileSelectedHunk: Accessor<HunkPosition | null>;
};

/**
 * Returns the specified rich-entry and virtual-exit distances for one row count.
 *
 * Callers provide the exact backend row count. Invalid counts violate the
 * hydrated text-file contract and throw instead of selecting a cost band.
 */
function richZone(rowCount: number): RichZone {
  if (!Number.isInteger(rowCount) || rowCount < 0) {
    throw new Error("Virtualization requires a non-negative row count.");
  }
  const cost: FileCost =
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
 * Chooses the first representation from current FileCard geometry.
 *
 * A card intersecting its cost-dependent entry zone begins rich. The stable
 * Husk must provide readable geometry before FullFile chooses a representation.
 */
function initialRenderMode(
  card: HTMLElement,
  rowCount: number,
): FileRenderMode {
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
 * Describes the ordinary pre-result presentation of one manifest file.
 *
 * A Husk contains only stable manifest presentation and exact queue activity.
 * It must not pretend that per-file statistics or rendered rows are available.
 */
type HuskFileState = {
  state: "husk";
  fileIndex: number;
  name: string;
  path: string;
  activity: "queued" | "fetching";
};

/**
 * Describes a successfully loaded canonical file query result.
 *
 * The immutable FileDiff is complete and is the sole backend value accepted by
 * FileBody. Manifest or lazy metadata must not be merged into it.
 */
type FullFileState = {
  state: "full";
  fileIndex: number;
  backend_data: FileDiff;
};

/**
 * Describes either intentional delayed-file metadata or a real file failure.
 *
 * Deferred values come only from lazy-info. Error values retain the original
 * thrown Error and stable manifest path so complete local damage remains visible.
 */
type LazyFile =
  | { kind: "deferred"; info: LazyInfoFile }
  | { kind: "error"; name: string; path: string; error: Error };

/**
 * Describes a file whose content starts only through explicit user activation.
 *
 * Retry and delayed hydration use distinct ChangeSet-supplied commands because
 * their HTTP timeout policies differ. The state itself contains no query state,
 * timeout policy, or copied loading flag.
 */
type LazyFileState = {
  state: "lazy";
  fileIndex: number;
  file: LazyFile;
};

/**
 * Represents every complete presentation branch accepted by FileCard.
 *
 * The discriminant is derived by ChangeSet from canonical query state. Callers
 * must never manufacture a FullFile for loading or failure placeholders.
 */
type FileCardState = HuskFileState | FullFileState | LazyFileState;

/**
 * Defines every input required by one stable FileCard.
 *
 * Expansion remains ChangeSet-owned so it survives active-content replacement.
 * `explicitlyCollapsed` distinguishes a user/directory collapse from the
 * bodyless default Husk presentation, which still participates in navigation.
 * FileCard may only request a bounded explicit load, request an unbounded retry,
 * and change expansion. It cannot mutate state, select transport policy, or
 * begin a query independently.
 */
type FileCardProps = {
  /** Supplies the exact pair used by rendered line Thread targets. */
  reviewFile: ReviewFilePair;
  file_state: FileCardState;
  expanded: boolean;
  explicitlyCollapsed: boolean;
  /**
   * Allows this FileCard to mount its expensive FileBody after the file lane yields.
   *
   * TODO: Consider cooperative yields inside FileBody for expensive in-file rendering.
   */
  admitted: boolean;
  engine: DiffEngine;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  onExpandedChange: (expanded: boolean) => void;
  onLoad: () => void;
  onRetry: () => void;
} & HunkCounterProps;

/**
 * Renders one stable manifest-position FileCard and contains its renderer.
 *
 * Callers keep this component mounted at one manifest position and replace only
 * its reactive state. FullFile renderer failures remain inside the stable
 * article; ordinary backend failures arrive as the explicit LazyFile state.
 */
export function FileCard(props: FileCardProps): JSX.Element {
  return (
    <FileCardContent
      reviewFile={props.reviewFile}
      file_state={props.file_state}
      expanded={props.expanded}
      explicitlyCollapsed={props.explicitlyCollapsed}
      admitted={props.admitted}
      engine={props.engine}
      view={props.view}
      aggressiveFolds={props.aggressiveFolds}
      linePins={props.linePins}
      globalSelectedHunk={props.globalSelectedHunk}
      fileSelectedHunk={props.fileSelectedHunk}
      onExpandedChange={props.onExpandedChange}
      onLoad={props.onLoad}
      onRetry={props.onRetry}
    />
  );
}

/**
 * Renders one reactive state branch into stable FileCard DOM.
 *
 * The article persists for this mounted keyed manifest entry. State replacement
 * swaps complete Husk, Full, or Lazy content without moving query state into
 * the card or retaining partial content from the prior branch.
 */
function FileCardContent(props: FileCardProps): JSX.Element {
  let card!: HTMLElement;
  const review = useReview();
  /**
   * Describes this FileCard's complete semantic hunk target set.
   *
   * The value changes only with target identity or participation. Full files
   * awaiting body admission retain one Husk target, while collapsed real files
   * retain their complete coordinate-preserving skipped target count.
   */
  function hunkSet(): string {
    if (props.file_state.state === "husk") {
      return props.explicitlyCollapsed ? "husk:skip" : "husk";
    }
    if (props.file_state.state === "lazy") {
      return props.expanded ? "lazy" : "lazy:skip";
    }
    const count = props.file_state.backend_data.hunk_count;
    if (count === 0) {
      return props.expanded ? "zero" : "zero:skip";
    }
    if (!props.expanded) {
      return `real:${count}:skip`;
    }
    return props.admitted ? `real:${count}` : "husk";
  }

  return (
    <article
      ref={card}
      class="file-card"
      classList={{
        "is-collapsed": props.file_state.state === "husk" || !props.expanded,
      }}
      data-file-card
      data-file-index={props.file_state.fileIndex}
      data-file-state={props.file_state.state}
      data-hunk-set={hunkSet()}
      data-hunk-count={
        props.file_state.state === "full"
          ? props.file_state.backend_data.hunk_count
          : undefined
      }
    >
      <Show
        when={props.file_state.state === "husk" ? props.file_state : null}
        keyed
      >
        {(file_state) => (
          <HuskFile
            file_state={file_state}
            explicitlyCollapsed={props.explicitlyCollapsed}
            globalSelectedHunk={props.globalSelectedHunk}
            fileSelectedHunk={props.fileSelectedHunk}
          />
        )}
      </Show>
      <Show
        when={
          props.file_state.state === "full"
            ? props.file_state.backend_data
            : null
        }
        keyed
      >
        {(backend_data) => (
          <FullFile
            reviewFile={props.reviewFile}
            file_state={{
              state: "full",
              fileIndex: props.file_state.fileIndex,
              backend_data,
            }}
            expanded={props.expanded}
            admitted={props.admitted}
            engine={props.engine}
            view={props.view}
            aggressiveFolds={props.aggressiveFolds}
            linePins={props.linePins}
            globalSelectedHunk={props.globalSelectedHunk}
            fileSelectedHunk={props.fileSelectedHunk}
            card={() => card}
            onExpandedChange={props.onExpandedChange}
          />
        )}
      </Show>
      <Show
        when={props.file_state.state === "lazy" ? props.file_state : null}
        keyed
      >
        {(file_state) => (
          <LazyFileView
            file_state={file_state}
            expanded={props.expanded}
            globalSelectedHunk={props.globalSelectedHunk}
            fileSelectedHunk={props.fileSelectedHunk}
            onLoad={props.onLoad}
            onRetry={props.onRetry}
          />
        )}
      </Show>
    </article>
  );
}

/**
 * Contains an unexpected FullFile renderer failure at its stable FileCard.
 *
 * Callers provide the manifest path and complete renderer subtree. A failure
 * replaces only that subtree with unrecoverable critical damage. The boundary
 * does not retry, preserve failed DOM, synthesize hunks, or change selection.
 */
function FileRendererBoundary(props: {
  card: Accessor<HTMLElement>;
  path: string;
  children: JSX.Element;
}): JSX.Element {
  return (
    <ErrorBoundary
      fallback={(error) => (
        <FileRendererErrorStrip
          card={props.card}
          path={props.path}
          error={error}
        />
      )}
    >
      {props.children}
    </ErrorBoundary>
  );
}

/**
 * Presents complete unrecoverable damage for one failed file renderer.
 *
 * The mounted strip reports the original failure once through global Toasts
 * and exposes its complete local message and stack. It offers no retry and is
 * deliberately not a LazyFile or hunk target.
 */
function FileRendererErrorStrip(props: {
  card: Accessor<HTMLElement>;
  path: string;
  error: unknown;
}): JSX.Element {
  const toast = useToasts();
  const presented = presentError(props.error);

  onMount(() => {
    const card = props.card();
    card.setAttribute("data-file-render-error", "");
    toast.showError(`Could not render ${props.path}`, props.error);
    onCleanup(() => card.removeAttribute("data-file-render-error"));
  });

  return (
    <section
      class="file-render-critical-error"
      data-file-render-error
      role="alert"
    >
      <strong>Critical renderer error in {props.path}</strong>
      <pre class="render-error-message">{presented.message}</pre>
      <Show when={presented.details !== null}>
        <details class="error-traceback" open>
          <summary>Stack</summary>
          <pre>{presented.details}</pre>
        </details>
      </Show>
    </section>
  );
}

/**
 * Renders a queued or actively fetching file without reserving body height.
 *
 * The header uses only manifest path and activity. It exposes no body or
 * expansion control while no file or lazy-info result exists, and therefore
 * never reserves the eventual rendered height.
 */
function HuskFile(
  props: {
    file_state: HuskFileState;
    explicitlyCollapsed: boolean;
  } & HunkCounterProps,
): JSX.Element {
  return (
    <HuskFileHeader
      file_state={props.file_state}
      explicitlyCollapsed={props.explicitlyCollapsed}
      globalSelectedHunk={props.globalSelectedHunk}
      fileSelectedHunk={props.fileSelectedHunk}
    />
  );
}

/**
 * Renders the complete non-interactive header for a queued or fetching file.
 *
 * Callers provide stable manifest presentation and exact lane activity. The
 * header exposes neither file statistics nor expansion before content exists.
 */
function HuskFileHeader(
  props: {
    file_state: HuskFileState;
    explicitlyCollapsed: boolean;
  } & HunkCounterProps,
): JSX.Element {
  let header!: HTMLElement;
  const review = useReview();
  onMount(() => review.setFileHeaderMounted(header, true));
  onCleanup(() => {
    review.setFileHeaderMounted(header, false);
    review.closeAnchoredUi(header);
  });
  const identity: PseudoHunkIdentity = {
    fileIndex: props.file_state.fileIndex,
    kind: "husk",
    hunkIndex: 0,
  };
  return (
    <header
      ref={header}
      class="file-card-header husk-file-header"
      classList={{ skip: props.explicitlyCollapsed }}
      data-hunk-target
      data-hunk-kind={identity.kind}
      data-file-index={identity.fileIndex}
      data-hunk-index={identity.hunkIndex}
    >
      <span class="file-card-heading">
        <VisibilityIndicator visible={false} virtualized={false} />
        <span class="file-card-title-row">
          <h2>{props.file_state.path}</h2>
          <span class="file-card-status">
            {props.file_state.activity === "fetching" ? "loading" : "queued"}
          </span>
          <HunkCounterBadges
            globalSelectedHunk={props.globalSelectedHunk}
            fileSelectedHunk={props.fileSelectedHunk}
          />
        </span>
      </span>
      <LoaderCircle
        class="file-state-spinner"
        classList={{
          "is-spinning": props.file_state.activity === "fetching",
        }}
        aria-hidden="true"
      />
    </header>
  );
}

/**
 * Reserves the established global and file-local counter positions in a header.
 *
 * ChangeSet owns their exact numeric data. This component formats that data for
 * the established header labels and never reads DOM or navigation state.
 */
function HunkCounterBadges(props: HunkCounterProps): JSX.Element {
  return (
    <>
      <span
        class="file-card-hunks"
        hidden={props.globalSelectedHunk() === null}
      >
        <Show when={props.globalSelectedHunk()}>
          {(global) => (
            <>
              {global().position.current ?? "—"}/{global().position.total}
              {global().hasMore ? "+" : ""} hunks
            </>
          )}
        </Show>
      </span>
      <span
        class="file-card-file-hunks"
        hidden={props.fileSelectedHunk() === null}
      >
        <Show when={props.fileSelectedHunk()}>
          {(local) => (
            <>
              {local().current === null ? "" : `${local().current}/`}
              {local().total} in file
            </>
          )}
        </Show>
      </span>
    </>
  );
}

/**
 * Renders a complete file header and rich body from one immutable query result.
 *
 * View and aggressive-fold changes are read reactively by the renderer. They do
 * not replace query data or move global progress and navigation behavior
 * into FileBody.
 */
function FullFile(
  props: {
    reviewFile: ReviewFilePair;
    file_state: FullFileState;
    expanded: boolean;
    admitted: boolean;
    engine: DiffEngine;
    view: DiffViewMode;
    aggressiveFolds: boolean;
    linePins: LinePins;
    card: Accessor<HTMLElement>;
    onExpandedChange: (expanded: boolean) => void;
  } & HunkCounterProps,
): JSX.Element {
  const backend_data = props.file_state.backend_data;
  const [renderMode, setRenderMode] = createSignal<FileRenderMode | null>(null);

  return (
    <>
      <FullFileHeader
        file_state={props.file_state}
        expanded={props.expanded}
        virtualized={props.expanded && renderMode() === "virtual"}
        awaitingAdmission={
          props.expanded &&
          !props.admitted &&
          props.file_state.backend_data.hunk_count > 0
        }
        globalSelectedHunk={props.globalSelectedHunk}
        fileSelectedHunk={props.fileSelectedHunk}
        onExpandedChange={props.onExpandedChange}
      />
      <FileRendererBoundary card={props.card} path={backend_data.display_name}>
        <FullFileRenderer
          {...props}
          renderMode={renderMode}
          onRenderModeChange={setRenderMode}
        />
      </FileRendererBoundary>
    </>
  );
}

/**
 * Renders and operates the fallible body beneath one stable Full File header.
 *
 * FileRendererBoundary contains this complete subtree. The component may expose
 * body preparation operations on its FileCard, but it neither renders nor
 * replaces the independent header review action.
 */
function FullFileRenderer(
  props: {
    reviewFile: ReviewFilePair;
    file_state: FullFileState;
    expanded: boolean;
    admitted: boolean;
    engine: DiffEngine;
    view: DiffViewMode;
    aggressiveFolds: boolean;
    linePins: LinePins;
    card: Accessor<HTMLElement>;
    onExpandedChange: (expanded: boolean) => void;
    renderMode: Accessor<FileRenderMode | null>;
    onRenderModeChange(mode: FileRenderMode): void;
  } & HunkCounterProps,
): JSX.Element {
  const backend_data = props.file_state.backend_data;
  const textFile = "render_kind" in backend_data ? null : backend_data;
  const hunkIndices =
    "render_kind" in backend_data
      ? backend_data.cells.flatMap((cell) =>
          cell.source_rows.flatMap((row) =>
            row.hunk_index === null ? [] : [row.hunk_index],
          ),
        )
      : backend_data.rows.flatMap((row) =>
          row.hunk_index === null ? [] : [row.hunk_index],
        );
  if (hunkIndices.length !== props.file_state.backend_data.hunk_count) {
    throw new Error(
      `${props.file_state.backend_data.display_name} returned ${hunkIndices.length} hunk targets for hunk_count ${props.file_state.backend_data.hunk_count}.`,
    );
  }
  const uniqueHunkIndices = new Set(hunkIndices);
  for (
    let hunkIndex = 0;
    hunkIndex < props.file_state.backend_data.hunk_count;
    hunkIndex += 1
  ) {
    if (!uniqueHunkIndices.has(hunkIndex)) {
      throw new Error(
        `${props.file_state.backend_data.display_name} omitted hunk index ${hunkIndex}.`,
      );
    }
  }
  props.onRenderModeChange(
    textFile === null
      ? "rich"
      : initialRenderMode(props.card(), textFile.rows.length),
  );
  /** Reads the representation initialized inside this renderer boundary. */
  const renderMode: Accessor<FileRenderMode> = () => {
    const current = props.renderMode();
    if (current === null) {
      throw new Error("FullFile renderer mode was not initialized.");
    }
    return current;
  };
  const [reservedRichHeight, setReservedRichHeight] = createSignal<
    number | null
  >(null);

  /**
   * Changes only this FullFile's representation and records usable rich height.
   *
   * Observer callbacks call this operation directly. An expanded admitted file
   * must expose a measurable rich body before becoming virtual. The operation
   * performs no navigation, selected-hunk, ChangeSet, or scrolling behavior.
   */
  function changeRenderMode(mode: FileRenderMode): void {
    if (renderMode() === mode) {
      return;
    }
    if (mode === "virtual") {
      const richBody = props
        .card()
        .querySelector<HTMLElement>(".rich-file-body");
      if (richBody === null) {
        if (props.expanded && props.admitted) {
          throw new Error(
            "Expanded admitted FullFile cannot become virtual without a rich body.",
          );
        }
      } else {
        const measuredHeight = richBody.getBoundingClientRect().height;
        if (!Number.isFinite(measuredHeight) || measuredHeight <= 0) {
          throw new Error(
            "Rich FullFile body must have a finite positive height before virtualization.",
          );
        }
        setReservedRichHeight(measuredHeight);
      }
    }
    props.onRenderModeChange(mode);
    props.card().dataset.fileRender = mode;
  }

  /**
   * Materializes this FullFile's rich body for one explicit navigation action.
   *
   * Collapsed, zero-hunk, non-text, and body-awaiting FullFiles already expose
   * their complete current representation and are immediate no-ops. An admitted
   * expanded text file changes only local representation and resolves after
   * Solid has mounted rich DOM. It never expands, selects, calculates counters,
   * scrolls, or fetches.
   */
  async function waitToEnrich_impl(): Promise<void> {
    // Not expanded and not admitted files dont need enrichment
    if (!props.expanded || !props.admitted) {
      return;
    }
    const card = props.card();
    changeRenderMode("rich");
    await Promise.resolve();
    if (!card.isConnected) {
      return;
    }
    if (props.expanded && props.admitted) {
      const richBody = card.querySelector<HTMLElement>(".rich-file-body");
      if (richBody === null) {
        throw new Error("FullFile did not mount its rich body.");
      }
    }
  }

  /**
   * Prepares one exact semantic line inside this admitted FullFile.
   *
   * Navigation supplies the complete URL target and its operation AbortSignal.
   * The operation expands this FileCard, materializes rich DOM, resolves the
   * exact ordinary or notebook DiffGrid from canonical renderer order, and
   * delegates local unfolding. It does not fetch, scroll, paint, parse the URL,
   * or select a hunk.
   */
  async function prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine> {
    if (target.file !== backend_data.display_name) {
      throw new Error("Line preparation targeted the wrong FileCard.");
    }
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    if (!props.admitted) {
      throw new Error("Line preparation requires an admitted FullFile.");
    }
    if (!props.expanded) {
      props.onExpandedChange(true);
      await Promise.resolve();
    }
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    await waitToEnrich_impl();
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    const grids = Array.from(
      props.card().querySelectorAll<HTMLElement>(".diff-lines"),
    );
    let grid: HTMLElement | undefined;
    if ("render_kind" in backend_data) {
      const cellIndex = backend_data.cells.findIndex(
        (cell) => cell.cell_key === target.region,
      );
      if (cellIndex === -1) {
        return { state: "missing" };
      }
      if (grids.length !== backend_data.cells.length) {
        throw new Error(
          "Notebook FullFile must render exactly one DiffGrid per cell.",
        );
      }
      grid = grids[cellIndex];
    } else {
      if (target.region !== null) {
        return { state: "missing" };
      }
      if (grids.length !== 1) {
        throw new Error("Text FullFile must render exactly one DiffGrid.");
      }
      grid = grids[0];
    }
    if (grid === undefined) {
      throw new Error("Prepared DiffGrid disappeared.");
    }
    const prepareLine: unknown = Reflect.get(grid, "prepareLine_impl");
    if (typeof prepareLine !== "function") {
      throw new Error("DiffGrid omitted its line-preparation operation.");
    }
    const result: unknown = await Reflect.apply(prepareLine, grid, [
      target,
      abortSignal,
    ]);
    if (
      typeof result !== "object" ||
      result === null ||
      !("state" in result) ||
      (result.state !== "ready" &&
        result.state !== "missing" &&
        result.state !== "stopped")
    ) {
      throw new Error("DiffGrid returned an invalid preparation result.");
    }
    if (
      result.state === "ready" &&
      (!("row" in result) || !(result.row instanceof HTMLElement))
    ) {
      throw new Error("Ready DiffGrid preparation omitted its rendered row.");
    }
    return result as PreparedLine;
  }

  /**
   * Tests this FileCard against its rich-entry zone at a proposed scroll position.
   *
   * Navigation supplies a finite non-negative document viewport top. The result
   * applies the same row-cost threshold as the local IntersectionObserver without
   * scrolling or changing representation. It must not be used for the larger
   * rich-exit zone.
   */
  function intersectsRichEntryZone(viewportTop: number): boolean {
    if (textFile === null) {
      throw new Error("Only text FullFiles have a rich-entry zone.");
    }
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
    const rect = props.card().getBoundingClientRect();
    if (!Number.isFinite(rect.top) || !Number.isFinite(rect.bottom)) {
      throw new Error(
        "Rich-entry geometry requires a finite FileCard rectangle.",
      );
    }
    const cardTop = window.scrollY + rect.top;
    const cardBottom = window.scrollY + rect.bottom;
    const margin =
      richZone(textFile.rows.length).enterViewports * viewportHeight;
    return (
      cardBottom >= viewportTop - margin &&
      cardTop <= viewportTop + viewportHeight + margin
    );
  }

  onMount(() => {
    const card = props.card() as EnrichableFileCard;
    const observedFile = textFile;
    card.dataset.fileRender = renderMode();
    card.intersectsRichEntryZone = intersectsRichEntryZone;
    card.waitToEnrich_impl = waitToEnrich_impl;
    card.prepareLine_impl = prepareLine_impl;
    if (observedFile === null) {
      onCleanup(() => {
        delete card.dataset.fileRender;
        Reflect.deleteProperty(card, "intersectsRichEntryZone");
        Reflect.deleteProperty(card, "waitToEnrich_impl");
        Reflect.deleteProperty(card, "prepareLine_impl");
      });
      return;
    }
    const rowCount = observedFile.rows.length;
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
          // left visible files stuck virtual. Only the newest entry is the
          // card's current state.
          const entry = entries[entries.length - 1];
          if (entry === undefined) {
            throw new Error("Rich-zone observer omitted its FileCard entry.");
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
            throw new Error(
              "Virtual-zone observer omitted its FileCard entry.",
            );
          }
          if (!entry.isIntersecting) {
            changeRenderMode("virtual");
          }
        },
        {
          rootMargin: `${zone.exitViewports * window.innerHeight}px 0px`,
        },
      );
      enterObserver.observe(card);
      exitObserver.observe(card);
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
      delete card.dataset.fileRender;
      Reflect.deleteProperty(card, "intersectsRichEntryZone");
      Reflect.deleteProperty(card, "waitToEnrich_impl");
      Reflect.deleteProperty(card, "prepareLine_impl");
    });
  });

  return (
    <>
      <Show
        when={!props.expanded && props.file_state.backend_data.hunk_count > 0}
      >
        <div class="hunk-skip-anchors" aria-hidden="true">
          <For
            each={Array.from(
              { length: props.file_state.backend_data.hunk_count },
              (_, hunkIndex) => hunkIndex,
            )}
          >
            {(hunkIndex) => {
              const identity: PseudoHunkIdentity = {
                fileIndex: props.file_state.fileIndex,
                kind: "skip",
                hunkIndex,
              };
              return (
                <span
                  class="hunk-skip skip"
                  data-hunk-target
                  data-hunk-kind={identity.kind}
                  data-file-index={identity.fileIndex}
                  data-hunk-index={identity.hunkIndex}
                />
              );
            }}
          </For>
        </div>
      </Show>
      <Show when={props.expanded && props.admitted}>
        <Show
          when={textFile}
          keyed
          fallback={
            <div class="file-card-body rich-file-body" data-file-body>
              <FileBody
                reviewFile={props.reviewFile}
                fileIndex={props.file_state.fileIndex}
                backend_data={props.file_state.backend_data}
                engine={props.engine}
                view={props.view}
                aggressiveFolds={props.aggressiveFolds}
                linePins={props.linePins}
              />
            </div>
          }
        >
          {(backend_data) => (
            <Show
              when={renderMode() === "rich"}
              fallback={
                <VirtualFile
                  fileIndex={props.file_state.fileIndex}
                  backend_data={backend_data}
                  reservedRichHeight={reservedRichHeight()}
                />
              }
            >
              <div class="file-card-body rich-file-body" data-file-body>
                <FileBody
                  reviewFile={props.reviewFile}
                  fileIndex={props.file_state.fileIndex}
                  backend_data={backend_data}
                  engine={props.engine}
                  view={props.view}
                  aggressiveFolds={props.aggressiveFolds}
                  linePins={props.linePins}
                />
              </div>
            </Show>
          )}
        </Show>
      </Show>
    </>
  );
}

/**
 * Renders complete undecorated old/new text for one distant hydrated text file.
 *
 * The representation is always split and contains two aligned searchable text
 * nodes beneath the stable FullFileHeader. It writes one transparent real-hunk
 * target for every backend boundary, but handles no selection, navigation, syntax
 * spans, inline tokens, rich rows, or row virtualization.
 */
function VirtualFile(props: {
  fileIndex: number;
  backend_data: TextFileDiff;
  reservedRichHeight: number | null;
}): JSX.Element {
  /**
   * Renders one complete backend side as aligned searchable plain text.
   *
   * Callers choose the required old or new field. Missing text is an intentional
   * blank row, so both returned sides preserve identical backend row positions.
   */
  function sideText(side: "left_text" | "right_text"): string {
    return props.backend_data.rows.map((row) => row[side] ?? "").join("\n");
  }

  return (
    <div
      class="file-card-body virtual-file-body"
      data-file-body
      style={{
        height:
          props.reservedRichHeight === null
            ? undefined
            : `${props.reservedRichHeight}px`,
      }}
    >
      <div class="plain-split-diff" aria-label="Virtualized plain split diff">
        <For each={props.backend_data.rows}>
          {(row, rowIndex) => {
            const hunkIndex = row.hunk_index;
            if (hunkIndex === null) {
              return null;
            }
            const identity: RealHunkIdentity = {
              fileIndex: props.fileIndex,
              kind: "real",
              hunkIndex,
            };
            return (
              <span
                class="virtual-hunk-anchor hunk-anchor"
                style={{ top: `${10 + rowIndex() * 17.4}px` }}
                data-hunk-target
                data-hunk-kind={identity.kind}
                data-file-index={identity.fileIndex}
                data-hunk-index={identity.hunkIndex}
                aria-hidden="true"
              />
            );
          }}
        </For>
        <pre>{sideText("left_text")}</pre>
        <pre>{sideText("right_text")}</pre>
      </div>
    </div>
  );
}

/**
 * Renders the complete interactive header for one successfully loaded file.
 *
 * Callers provide the canonical FullFile state and ChangeSet-owned expansion.
 * The header shows only file-local statistics and warnings. Its square is the
 * sole expansion button; the remaining header content stays inert and selectable.
 */
function FullFileHeader(
  props: {
    file_state: FullFileState;
    expanded: boolean;
    virtualized: boolean;
    awaitingAdmission: boolean;
    onExpandedChange: (expanded: boolean) => void;
  } & HunkCounterProps,
): JSX.Element {
  let header!: HTMLElement;
  const review = useReview();
  onMount(() => review.setFileHeaderMounted(header, true));
  onCleanup(() => {
    review.setFileHeaderMounted(header, false);
    review.closeAnchoredUi(header);
  });
  const zeroHunkFile = props.file_state.backend_data.hunk_count === 0;

  /**
   * Constructs the indexed pseudo-hunk currently placed by this header.
   *
   * Zero files retain their permanent identity. A loaded file awaiting body
   * admission temporarily exposes a Husk identity; an admitted nonzero file
   * leaves all real identities to its body renderer.
   */
  function targetIdentity(): PseudoHunkIdentity | null {
    if (zeroHunkFile) {
      return {
        fileIndex: props.file_state.fileIndex,
        kind: "zero",
        hunkIndex: 0,
      };
    }
    return props.awaitingAdmission
      ? {
          fileIndex: props.file_state.fileIndex,
          kind: "husk",
          hunkIndex: 0,
        }
      : null;
  }

  return (
    <header
      ref={header}
      class="file-card-header full-file-header"
      classList={{ skip: zeroHunkFile && !props.expanded }}
      data-hunk-target={targetIdentity() === null ? undefined : ""}
      data-hunk-kind={targetIdentity()?.kind}
      data-file-index={targetIdentity()?.fileIndex}
      data-hunk-index={targetIdentity()?.hunkIndex}
    >
      <span class="file-card-heading">
        <button
          type="button"
          class="file-card-visibility-control"
          aria-expanded={props.expanded}
          aria-label={
            props.expanded
              ? `Collapse ${props.file_state.backend_data.display_name}`
              : `Expand ${props.file_state.backend_data.display_name}`
          }
          onClick={() => props.onExpandedChange(!props.expanded)}
        >
          <VisibilityIndicator
            visible={props.expanded && !props.virtualized}
            virtualized={props.virtualized}
          />
        </button>
        <span class="file-card-title-row">
          <h2>{props.file_state.backend_data.display_name}</h2>
          <span class="file-card-status">
            {props.file_state.backend_data.file_kind.type === "git"
              ? props.file_state.backend_data.file_kind.status
              : "untracked"}
          </span>
          <Show
            when={
              "engine_warning" in props.file_state.backend_data
                ? props.file_state.backend_data.engine_warning
                : null
            }
            keyed
          >
            {(warning) => (
              <span class="file-card-engine-warning" title={warning.message}>
                {warning.message}
              </span>
            )}
          </Show>
          <HunkCounterBadges
            globalSelectedHunk={props.globalSelectedHunk}
            fileSelectedHunk={props.fileSelectedHunk}
          />
        </span>
      </span>
      <FileStatistics summary={props.file_state.backend_data.summary} />
    </header>
  );
}

/**
 * Renders intentionally delayed content or complete localized query damage.
 *
 * Deferred planks submit exactly one explicit load command. Error planks retain
 * the complete message, open stack, and RetryButton; neither branch invokes the
 * command without direct user activation.
 */
function LazyFileView(
  props: {
    file_state: LazyFileState;
    expanded: boolean;
    onLoad: () => void;
    onRetry: () => void;
  } & HunkCounterProps,
): JSX.Element {
  const identity: PseudoHunkIdentity = {
    fileIndex: props.file_state.fileIndex,
    kind: "lazy",
    hunkIndex: 0,
  };
  return (
    <>
      <LazyFileHeader
        file_state={props.file_state}
        globalSelectedHunk={props.globalSelectedHunk}
        fileSelectedHunk={props.fileSelectedHunk}
      />
      <Show when={!props.expanded}>
        <div class="hunk-skip-anchors" aria-hidden="true">
          <span
            class="hunk-skip skip"
            data-hunk-target
            data-hunk-kind={identity.kind}
            data-file-index={identity.fileIndex}
            data-hunk-index={identity.hunkIndex}
          />
        </div>
      </Show>
      <Show when={props.expanded}>
        <Show
          when={
            props.file_state.file.kind === "deferred"
              ? props.file_state.file
              : null
          }
          keyed
        >
          {(deferred) => (
            <DeferredFilePlank
              fileIndex={props.file_state.fileIndex}
              info={deferred.info}
              onLoad={props.onLoad}
            />
          )}
        </Show>
        <Show
          when={
            props.file_state.file.kind === "error"
              ? props.file_state.file
              : null
          }
          keyed
        >
          {(failure) => (
            <div
              class="file-lazy-error-panel is-error lazy-hunk-anchor"
              data-hunk-target
              data-hunk-kind={identity.kind}
              data-file-index={identity.fileIndex}
              data-hunk-index={identity.hunkIndex}
            >
              <ErrorPanel
                title={`Failed to load ${failure.path}`}
                error={failure.error}
              >
                <RetryButton onRetry={props.onRetry} />
              </ErrorPanel>
            </div>
          )}
        </Show>
      </Show>
    </>
  );
}

/**
 * Renders the complete inert header for a delayed or failed file.
 *
 * Deferred values show only available lazy metadata; failures show their local
 * status without hiding the full body error. Its empty square is presentation
 * only; the explicit plank remains the sole individual LazyFile action.
 */
function LazyFileHeader(
  props: {
    file_state: LazyFileState;
  } & HunkCounterProps,
): JSX.Element {
  let header!: HTMLElement;
  const review = useReview();
  onMount(() => review.setFileHeaderMounted(header, true));
  onCleanup(() => {
    review.setFileHeaderMounted(header, false);
    review.closeAnchoredUi(header);
  });
  return (
    <header ref={header} class="file-card-header lazy-file-header">
      <span class="file-card-heading">
        <VisibilityIndicator visible={false} virtualized={false} />
        <span class="file-card-title-row">
          <h2>
            {props.file_state.file.kind === "deferred"
              ? props.file_state.file.info.display_name
              : props.file_state.file.path}
          </h2>
          <span class="file-card-status">
            {props.file_state.file.kind === "error"
              ? "failed"
              : props.file_state.file.info.lazy}
          </span>
          <HunkCounterBadges
            globalSelectedHunk={props.globalSelectedHunk}
            fileSelectedHunk={props.fileSelectedHunk}
          />
        </span>
      </span>
      <Show
        when={
          props.file_state.file.kind === "deferred"
            ? props.file_state.file
            : null
        }
        keyed
      >
        {(deferred) => <LazyStatistics info={deferred.info} />}
      </Show>
    </header>
  );
}

/**
 * Renders the colored explicit-fetch plank for one backend delay reason.
 *
 * The complete LazyInfoFile is required. Activation reports only the supplied
 * callback, leaving file-fetch ordering and loading presentation with ChangeSet.
 */
function DeferredFilePlank(props: {
  fileIndex: number;
  info: LazyInfoFile;
  onLoad: () => void;
}): JSX.Element {
  const identity: PseudoHunkIdentity = {
    fileIndex: props.fileIndex,
    kind: "lazy",
    hunkIndex: 0,
  };
  /**
   * Returns the complete action label for the required backend delay reason.
   *
   * Null is forbidden for a LazyFile plank and throws as a response-contract
   * violation rather than inventing a generic load operation.
   */
  const title = () => {
    switch (props.info.lazy) {
      case "deleted":
        return "Load deleted file diff";
      case "generated":
        return "Load generated diff";
      case "too_big":
        return "Load large diff";
      case "untracked":
        return "Load untracked file";
      case "pure_renamed":
        return "Load renamed file diff";
      case null:
        throw new Error("LazyFile metadata requires a lazy reason.");
    }
  };
  /**
   * Returns the complete visible explanation for the backend delay reason.
   *
   * The backend display name is preserved verbatim. Null is an invalid
   * LazyFile reason and throws instead of substituting an explanation.
   */
  const explanation = () => {
    switch (props.info.lazy) {
      case "deleted":
        return `${props.info.display_name} is deleted. Click to fetch and open it.`;
      case "generated":
        return `${props.info.display_name} looks generated. Click to fetch and open it.`;
      case "too_big":
        return `${props.info.display_name} is large. Click to fetch and open it.`;
      case "untracked":
        return `${props.info.display_name} is untracked. Click to fetch and open it.`;
      case "pure_renamed":
        return `${props.info.display_name} was renamed without content changes. Click to fetch and open it.`;
      case null:
        throw new Error("LazyFile metadata requires a lazy reason.");
    }
  };

  return (
    <button
      type="button"
      class="file-lazy-load-toggle lazy-hunk-anchor"
      classList={{
        "is-untracked": props.info.lazy === "untracked",
        "is-generated": props.info.lazy === "generated",
        "is-deleted": props.info.lazy === "deleted",
        "is-too-big": props.info.lazy === "too_big",
        "is-pure-renamed": props.info.lazy === "pure_renamed",
      }}
      data-hunk-target
      data-hunk-kind={identity.kind}
      data-file-index={identity.fileIndex}
      data-hunk-index={identity.hunkIndex}
      onClick={props.onLoad}
    >
      <span class="file-lazy-load-toggle-title">{title()}</span>
      <span class="file-lazy-load-toggle-meta">{explanation()}</span>
    </button>
  );
}

/**
 * Dispatches one complete FileDiff to its established rich renderer.
 *
 * The notebook discriminator is the only variant test. Text rows retain the
 * exact backend labels, hints, and Difftastic row-combination policy; this boundary does
 * not subscribe to progress, headers, other files, or navigation state.
 */
function FileBody(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  backend_data: FileDiff;
  engine: DiffEngine;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
}): JSX.Element {
  if ("render_kind" in props.backend_data) {
    return (
      <NotebookFile
        reviewFile={props.reviewFile}
        fileIndex={props.fileIndex}
        backend_data={props.backend_data}
        view={props.view}
        aggressiveFolds={props.aggressiveFolds}
        linePins={props.linePins}
      />
    );
  }
  return (
    <DiffGrid
      reviewFile={props.reviewFile}
      fileIndex={props.fileIndex}
      displayName={props.backend_data.display_name}
      region={null}
      leftLabel={props.backend_data.left_label}
      rightLabel={props.backend_data.right_label}
      rows={props.backend_data.rows}
      foldHints={props.backend_data.fold_hints}
      viewMode={props.view}
      aggressiveFolds={props.aggressiveFolds}
      combineInsertOnlyReplaceRows={props.engine === "difftastic"}
      linePins={props.linePins}
    />
  );
}

/**
 * Renders exact per-file line statistics supplied by a FullFile response.
 *
 * The summary is complete. These values remain file-local and never contribute
 * to manifest totals or sequence progress.
 */
function FileStatistics(props: { summary: FileDiff["summary"] }): JSX.Element {
  return (
    <span class="file-stats">
      <span class="delta added">+ {props.summary.added_lines}</span>
      <span class="delta changed">~ {props.summary.modified_lines}</span>
      <span class="delta removed">- {props.summary.removed_lines}</span>
      <span class="delta moved">* {props.summary.moved_lines}</span>
    </span>
  );
}

/**
 * Renders the stable four-cell statistic shape for one LazyFile header.
 *
 * Added and removed values come from lazy-info. Lazy metadata does not contain
 * modified or moved counts, so those unavailable metrics remain question marks.
 */
function LazyStatistics(props: { info: LazyInfoFile }): JSX.Element {
  return (
    <span class="file-stats">
      <span class="delta added">
        + {props.info.added_lines === null ? "?" : props.info.added_lines}
      </span>
      <span class="delta changed">~ ?</span>
      <span class="delta removed">
        - {props.info.removed_lines === null ? "?" : props.info.removed_lines}
      </span>
      <span class="delta moved">* ?</span>
    </span>
  );
}

/**
 * Renders the established card expansion indicator for one file header.
 *
 * The indicator reflects ChangeSet-owned expansion and FileCard-local mode.
 * Virtual text uses the established V marker without exporting render mode;
 * ordinary rich and lazy content retain the existing expansion presentation.
 */
function VisibilityIndicator(props: {
  visible: boolean;
  virtualized: boolean;
}): JSX.Element {
  return (
    <span
      class="visibility-indicator large"
      classList={{
        visible: props.visible,
        virtualized: props.virtualized,
      }}
      aria-hidden="true"
    >
      {props.virtualized ? "V" : ""}
    </span>
  );
}
