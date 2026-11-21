import {
  ErrorBoundary,
  For,
  Show,
  createMemo,
  onCleanup,
  onMount,
  type Accessor,
  type JSX,
} from "solid-js";
import { LoaderCircle } from "lucide-solid";
import {
  type FileDiff,
  type LazyInfoFile,
  type ReviewFilePair,
} from "../../api/api";
import {
  ErrorPanel,
  RetryButton,
  presentError,
  useToasts,
} from "../../comp/Toasts";
import type { DiffViewMode } from "../App";
import type {
  FileState,
  FullFileState,
  HuskFileState,
  LazyFileState,
} from "../changeSet/fileLane";
import type { LinePins, LinePinTarget, PreparedLine } from "../linePins";
import { createStore } from "solid-js/store";
import {
  FrameView,
  composedHunkCount,
  composedHunks,
  type BayExpansion,
  type BayHandle,
  type BayRenderMode,
  type BayRenderModes,
} from "./FrameView";
import {
  hunkSetAttrs,
  hunkTargetAttrs,
  type HunkSet,
  type PseudoHunkIdentity,
} from "../navigation";
import { useReview } from "../review/Review";
import { assert, expect } from "../../utils";

export type { BayHandle, BayRenderMode, BayRenderModes } from "./FrameView";
export type { LineCell } from "./grids/text/rowDom";

export type FileCardHandle = {
  // Expands the File and the target's bay, then reveals the line in it.
  prepareLine(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine>;
};

type MountedFullFile = {
  card: FileCardHandle;
  bays: Map<string, BayHandle>;
};

// Keyed by the persistent card element. An entry exists exactly while that
// card's admitted FullFile renderer is mounted; its bay map holds exactly the
// mounted bays.
const mountedFullFiles = new WeakMap<HTMLElement, MountedFullFile>();

// In document order.
export function fileCardsIn(root: ParentNode): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-file-card]"));
}

export function fileCardContaining(element: Element): HTMLElement | null {
  return element.closest<HTMLElement>("[data-file-card]");
}

export function fileCardIndex(card: HTMLElement): number {
  const fileIndex = card.dataset.fileIndex;
  assert(
    fileIndex !== undefined && /^(?:0|[1-9]\d*)$/.test(fileIndex),
    "FileCard has an invalid manifest file index.",
  );
  return Number(fileIndex);
}

// A File renderer that failed has already shown its damage and Toast.
export function renderDamaged(root: ParentNode): boolean {
  return root.querySelector("[data-file-render-error]") !== null;
}

// CSS keeps a drag's text selection on the split side where the pointer went
// down. The marker moves to that side's grid, or is cleared outside any side.
export function markSelectionSide(
  root: HTMLElement,
  target: EventTarget | null,
): void {
  root
    .querySelector<HTMLElement>(".diff-grid[data-diff-selection-side]")
    ?.removeAttribute("data-diff-selection-side");
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

export function fileCardAt(
  root: ParentNode,
  fileIndex: number,
): FileCardHandle {
  const cards = root.querySelectorAll<HTMLElement>(
    `[data-file-card][data-file-index="${fileIndex}"]`,
  );
  assert(
    cards.length === 1,
    `Navigation requires one FileCard at index ${fileIndex}.`,
  );
  return expect(
    mountedFullFiles.get(
      expect(cards.item(0), "Indexed FileCard disappeared."),
    ),
    "Line navigation requires a mounted FullFile.",
  ).card;
}

export function bayContaining(element: Element): BayHandle | null {
  const wrapper = element.closest<HTMLElement>("[data-bay-key]");
  if (wrapper === null) {
    return null;
  }
  const card = expect(
    wrapper.closest<HTMLElement>("[data-file-card]"),
    "A bay requires its FileCard.",
  );
  const bayKey = expect(wrapper.dataset.bayKey, "A bay wrapper names its bay.");
  return expect(
    expect(
      mountedFullFiles.get(card),
      "A bay requires its mounted FullFile.",
    ).bays.get(bayKey),
    `Bay ${bayKey} is mounted without its handle.`,
  );
}

// In document order.
export function virtualBays(root: ParentNode): BayHandle[] {
  return Array.from(root.querySelectorAll("[data-bay-key]"), (wrapper) =>
    expect(bayContaining(wrapper), "A bay wrapper contains its own bay."),
  ).filter((bay) => bay.mode() === "virtual");
}

export type HunkPosition = {
  // One-based stable DOM position of the selection. A skipped selection keeps
  // its position, so this may exceed `total`; display only, never navigate by
  // it.
  current: number | null;
  total: number;
};

type HunkCounterProps = {
  globalSelectedHunk: Accessor<{
    position: HunkPosition;
    hasMore: boolean;
  } | null>;
  fileSelectedHunk: Accessor<HunkPosition | null>;
};

type FileCardProps = {
  reviewFile: ReviewFilePair;
  file_state: FileState;
  expanded: boolean;
  explicitlyCollapsed: boolean;
  admitted: boolean;
  // TODO: Consider cooperative yields inside FileBody for expensive in-file
  // rendering.
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
  // The mounted bays of this File register their modes here.
  bayRenderModes: BayRenderModes;
  // The aggregate of the modes above: null while no bay is mounted, virtual
  // only while every mounted bay is.
  renderMode: BayRenderMode | null;
  onExpandedChange: (expanded: boolean) => void;
  onLoad: () => void;
  onRetry: () => void;
} & HunkCounterProps;

export function FileCard(props: FileCardProps): JSX.Element {
  let card!: HTMLElement;
  // Derived from the composed diff rather than sent with it, and memoized so
  // the card and its header share one count instead of walking per read. The
  // renderers below run their own walks: they need the per-bay stop map, which
  // this count discards.
  const hunkCount = createMemo(() =>
    props.file_state.state === "full"
      ? composedHunkCount(props.file_state.backend_data)
      : 0,
  );
  function hunkSet(): HunkSet {
    if (props.file_state.state === "husk") {
      return { kind: "husk", skipped: props.explicitlyCollapsed };
    }
    if (props.file_state.state === "lazy") {
      return { kind: "lazy", skipped: !props.expanded };
    }
    const count = hunkCount();
    if (count === 0) {
      return { kind: "zero", skipped: !props.expanded };
    }
    if (!props.expanded) {
      return { kind: "real", count, skipped: true };
    }
    return props.admitted
      ? { kind: "real", count, skipped: false }
      : { kind: "husk", skipped: false };
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
      {...hunkSetAttrs(hunkSet())}
      data-hunk-count={
        props.file_state.state === "full" ? hunkCount() : undefined
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
            view={props.view}
            aggressiveFolds={props.aggressiveFolds}
            linePins={props.linePins}
            bayRenderModes={props.bayRenderModes}
            renderMode={props.renderMode}
            hunkCount={hunkCount}
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

function FileRendererErrorStrip(props: {
  card: Accessor<HTMLElement>;
  path: string;
  error: unknown;
}): JSX.Element {
  const toast = useToasts();
  const presented = presentError(props.error);

  // The card attribute is the renderer's terminal-damage marker consumed by
  // HunkDisplayObserver. Set it and report once for this failed mount, then
  // remove only that attribute when the strip is disposed.
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

function HuskFileHeader(
  props: {
    file_state: HuskFileState;
    explicitlyCollapsed: boolean;
  } & HunkCounterProps,
): JSX.Element {
  let header!: HTMLElement;
  const review = useReview();
  // Review tracks which persistent File header can host anchored UI. Register
  // this Husk header for its mounted lifetime and close any anchored UI before
  // the header disappears.
  onMount(() => {
    review.setFileHeaderMounted(header, true);
    onCleanup(() => {
      review.setFileHeaderMounted(header, false);
      review.closeAnchoredUi(header);
    });
  });
  return (
    <header
      ref={header}
      class="file-card-header husk-file-header"
      classList={{ skip: props.explicitlyCollapsed }}
      {...hunkTargetAttrs({
        fileIndex: props.file_state.fileIndex,
        kind: "husk",
        hunkIndex: 0,
      })}
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

function FullFile(
  props: {
    reviewFile: ReviewFilePair;
    file_state: FullFileState;
    expanded: boolean;
    admitted: boolean;
    view: DiffViewMode;
    aggressiveFolds: boolean;
    linePins: LinePins;
    bayRenderModes: BayRenderModes;
    renderMode: BayRenderMode | null;
    hunkCount: Accessor<number>;
    card: Accessor<HTMLElement>;
    onExpandedChange: (expanded: boolean) => void;
  } & HunkCounterProps,
): JSX.Element {
  const backend_data = props.file_state.backend_data;
  // Bay expansion must outlive the body: collapsing the File unmounts every
  // bay, and remounting shows the bays as the reviewer left them. A key
  // absent from the store is at its backend default.
  const [expandedBays, setExpandedBays] = createStore<Record<string, boolean>>(
    {},
  );
  const bayExpansion: BayExpansion = {
    isExpanded: (bay) => expandedBays[bay.bay_key] ?? bay.default_expanded,
    setExpanded: (bayKey, expanded) => setExpandedBays(bayKey, expanded),
  };
  return (
    <>
      <FullFileHeader
        file_state={props.file_state}
        expanded={props.expanded}
        virtualized={props.expanded && props.renderMode === "virtual"}
        awaitingAdmission={
          props.expanded && !props.admitted && props.hunkCount() > 0
        }
        hunkCount={props.hunkCount}
        globalSelectedHunk={props.globalSelectedHunk}
        fileSelectedHunk={props.fileSelectedHunk}
        onExpandedChange={props.onExpandedChange}
      />
      <FileRendererBoundary card={props.card} path={backend_data.display_name}>
        <FullFileRenderer {...props} bayExpansion={bayExpansion} />
      </FileRendererBoundary>
    </>
  );
}

function FullFileRenderer(
  props: {
    reviewFile: ReviewFilePair;
    file_state: FullFileState;
    expanded: boolean;
    admitted: boolean;
    view: DiffViewMode;
    aggressiveFolds: boolean;
    linePins: LinePins;
    card: Accessor<HTMLElement>;
    onExpandedChange: (expanded: boolean) => void;
    bayExpansion: BayExpansion;
    bayRenderModes: BayRenderModes;
  } & HunkCounterProps,
): JSX.Element {
  const backend_data = props.file_state.backend_data;
  // Hunk coordinates are bay-local and arrive on the wire, so the check is
  // the wire contract itself: a bay whose rows carry the stops must number
  // them zero through n-1 in row order. A mismatch means the backend numbered
  // hunks by a different rule than the renderers anchor them by.
  const { total: hunkTotal, bays: bayHunks } = composedHunks(backend_data);
  const hunkStops = backend_data.frames.flatMap((frame) =>
    frame.bays.flatMap((bay) => {
      const hunks = bayHunks.get(bay.bay_key);
      const presentHunks = expect(
        hunks,
        `${backend_data.display_name} bay ${bay.bay_key} is absent from its File's hunk stops.`,
      );
      if (presentHunks.carrier === "rows") {
        presentHunks.stops.forEach((stop, position) => {
          assert(
            stop === position,
            `${backend_data.display_name} bay ${bay.bay_key} numbers hunk ${stop} at position ${position}.`,
          );
        });
      }
      return presentHunks.stops.map((hunkIndex) => ({
        bay: bay.bay_key,
        hunkIndex,
      }));
    }),
  );
  assert(
    hunkStops.length === hunkTotal,
    `${backend_data.display_name} wrote ${hunkStops.length} hunk targets for ${hunkTotal} counted hunks.`,
  );

  const bays = new Map<string, BayHandle>();

  async function prepareLine(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine> {
    assert(
      target.file.left_path === props.reviewFile.left_path &&
        target.file.right_path === props.reviewFile.right_path,
      "Line preparation targeted the wrong FileCard.",
    );
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    assert(props.admitted, "Line preparation requires an admitted FullFile.");
    if (!props.expanded) {
      props.onExpandedChange(true);
      // Expansion is a signal write, so the File body does not exist until
      // Solid flushes. The yield lets that happen before the bay is searched.
      await Promise.resolve();
    }
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    // A pin names the bay it was taken in, always by the key composition
    // gave it. The bay may be closed, so its card-held expansion is written
    // before its handle is looked up; a key this composed diff does not
    // contain expands nothing and is answered by the missing handle below.
    const bayKey = target.bay.bay_key;
    props.bayExpansion.setExpanded(bayKey, true);
    // The expansion is a store write, so a closed bay is mounted by the
    // resulting flush. The yield lets that flush run before the handle below
    // is looked up.
    await Promise.resolve();
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    const bay = bays.get(bayKey);
    if (bay === undefined) {
      return { state: "missing" };
    }
    return await bay.prepareLine(target, abortSignal);
  }

  onMount(() => {
    const card = props.card();
    assert(!mountedFullFiles.has(card), "A FileCard mounts one FullFile.");
    mountedFullFiles.set(card, { card: { prepareLine }, bays });
    onCleanup(() => {
      mountedFullFiles.delete(card);
    });
  });

  return (
    <>
      <Show when={!props.expanded && hunkStops.length > 0}>
        {/* A collapsed File writes the coordinates its bays own, not a range
            rebuilt from how many there are. The list is the same one the checks
            above ran against. */}
        <div class="hunk-skip-anchors" aria-hidden="true">
          <For each={hunkStops}>
            {(stop) => {
              return (
                <span
                  class="hunk-skip skip"
                  {...hunkTargetAttrs({
                    fileIndex: props.file_state.fileIndex,
                    kind: "skip",
                    bay: stop.bay,
                    hunkIndex: stop.hunkIndex,
                  })}
                />
              );
            }}
          </For>
        </div>
      </Show>
      <Show when={props.expanded && props.admitted}>
        <div class="file-card-body" data-file-body>
          {/* The frame renderer walks the backend's frames and bays and draws
              each bay by its kind; a plain text file is one heading-less frame
              holding one text bay. Rows retain the exact backend labels and
              hints. This boundary does not subscribe to progress, headers,
              other files, or navigation state. */}
          <FrameView
            reviewFile={props.reviewFile}
            fileIndex={props.file_state.fileIndex}
            backend_data={props.file_state.backend_data}
            view={props.view}
            aggressiveFolds={props.aggressiveFolds}
            linePins={props.linePins}
            card={props.card}
            bayExpansion={props.bayExpansion}
            bayRenderModes={props.bayRenderModes}
            bays={bays}
          />
        </div>
      </Show>
    </>
  );
}

function FullFileHeader(
  props: {
    file_state: FullFileState;
    expanded: boolean;
    virtualized: boolean;
    awaitingAdmission: boolean;
    hunkCount: Accessor<number>;
    onExpandedChange: (expanded: boolean) => void;
  } & HunkCounterProps,
): JSX.Element {
  let header!: HTMLElement;
  const review = useReview();
  // Review anchors to this loaded File header only while the header is mounted.
  // Disposal unregisters it and closes UI that would otherwise retain its DOM.
  onMount(() => {
    review.setFileHeaderMounted(header, true);
    onCleanup(() => {
      review.setFileHeaderMounted(header, false);
      review.closeAnchoredUi(header);
    });
  });
  const zeroHunkFile = (): boolean => props.hunkCount() === 0;

  function targetIdentity(): PseudoHunkIdentity | null {
    if (zeroHunkFile()) {
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
  const targetAttrs = () => {
    const identity = targetIdentity();
    return identity === null ? {} : hunkTargetAttrs(identity);
  };

  return (
    <header
      ref={header}
      class="file-card-header full-file-header"
      classList={{ skip: zeroHunkFile() && !props.expanded }}
      {...targetAttrs()}
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
            {props.file_state.backend_data.file_kind.type === "tracked"
              ? props.file_state.backend_data.file_kind.status
              : "untracked"}
          </span>
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
          <span class="hunk-skip skip" {...hunkTargetAttrs(identity)} />
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
              {...hunkTargetAttrs(identity)}
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

function LazyFileHeader(
  props: {
    file_state: LazyFileState;
  } & HunkCounterProps,
): JSX.Element {
  let header!: HTMLElement;
  const review = useReview();
  // Review anchors to this LazyFile header only while the header is mounted.
  // Disposal unregisters it and closes UI before its DOM disappears.
  onMount(() => {
    review.setFileHeaderMounted(header, true);
    onCleanup(() => {
      review.setFileHeaderMounted(header, false);
      review.closeAnchoredUi(header);
    });
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
        assert(false, "LazyFile metadata requires a lazy reason.");
    }
  };
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
        assert(false, "LazyFile metadata requires a lazy reason.");
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
      {...hunkTargetAttrs(identity)}
      onClick={props.onLoad}
    >
      <span class="file-lazy-load-toggle-title">{title()}</span>
      <span class="file-lazy-load-toggle-meta">{explanation()}</span>
    </button>
  );
}

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
