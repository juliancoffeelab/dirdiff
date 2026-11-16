/**
 * Renders one stable manifest position through every file-lane state.
 *
 * A card keeps its coordinate and header mounted while queued, delayed, failed,
 * loaded, collapsed, or awaiting render admission. Loaded content retains bay
 * expansion across body unmounts and reports its aggregate rich or virtual mode
 * through the card DOM for FileTree presentation.
 *
 * Callers provide canonical lane state, ChangeSet file expansion, and the lane's
 * explicit load operation. The card does not observe queries, schedule transport,
 * or introduce a hunk-navigation path.
 */
import {
  ErrorBoundary,
  For,
  Show,
  Suspense,
  lazy,
  createEffect,
  createMemo,
  createSignal,
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
  type BayRenderMode,
  type BayRenderModes,
} from "./FrameView";
import type { PseudoHunkIdentity, SkippedHunkIdentity } from "../navigation";
import { useReview } from "../review/Review";
import { assert, expect } from "../../utils";
import type structureCaptureType from "./file-structure-prototype.fixtures.json";
import structureMetadata from "./file-structure-prototype.metadata.json";
import "./file-structure-prototype.css";

/**
 * Loads genuine graph evidence only when an explicitly selected prototype body mounts.
 *
 * The normal HUD imports only tiny capture metadata. The frozen multi-megabyte
 * sem artifact is a separate development chunk, shared by the prototype cards.
 */
const CapturedFileStructure = import.meta.env.DEV
  ? lazy(async () => {
      const capture = await import("./file-structure-prototype.fixtures.json");
      return {
        default: (
          props: Omit<Parameters<typeof FileStructurePrototype>[0], "capture">,
        ) => <FileStructurePrototype {...props} capture={capture.default} />,
      };
    })
  : null;

/**
 * Names changed source files covered by the development-only frozen sem capture.
 *
 * ChangeSet uses this metadata only to offer an explicit jump into captured source.
 * Complete graph, source, and raw sem evidence remain private to FileCard.
 * Production has no prototype metadata; this constant is not backend File data.
 */
export const fileStructurePrototypePaths: readonly string[] | null = import.meta
  .env.DEV
  ? structureMetadata.paths
  : null;

/**
 * Describes the line-preparation operation attached by FullFile.
 *
 * Every mounted FullFile exposes it as one DOM interface. The operation
 * prepares one exact backend line inside this card and never changes
 * selected identity, counters, or scroll position.
 */
type PreparableFileCard = HTMLElement & {
  /**
   * Prepares the mounted rich row for one exact line-navigation target.
   *
   * Navigation may call this repeatedly with the same `target` while enriching
   * virtual bays. The target must identify this card's captured file pair; the
   * operation may expand the File and its bay, but does not scroll, paint, or
   * change URL state. Aborting `abortSignal` ends the current navigation and
   * makes any previously returned row unsafe for later work.
   *
   * @param target Complete file, bay, side, and backend line coordinates.
   * @param abortSignal Lifetime of the caller's current navigation operation.
   */
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
  /**
   * Gives the selected target's one-based stable DOM position in this scope.
   * Null means the scope currently has no selected position; a skipped target
   * can retain a stable position greater than `total`.
   */
  current: number | null;

  /**
   * Counts targets currently participating in this display scope.
   * Consumers may format the count but must not derive navigation from it.
   */
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
  /**
   * Reads the current ChangeSet-wide hunk display after DOM calculation.
   *
   * FileCard invokes the accessor reactively while rendering its header. Null
   * means the initial calculation has not supplied a display. The result also
   * says whether later file loading can add targets. The accessor must not
   * navigate, mutate selection, or retain per-call resources.
   */
  globalSelectedHunk: Accessor<{
    /**
     * Combines selected stable position with the current participating count.
     * Skipped selection may therefore place `current` beyond `total`.
     */
    position: HunkPosition;
    /**
     * Reports whether unresolved manifest files may add hunk targets later.
     * Headers append `+` to distinguish this open-ended total.
     */
    hasMore: boolean;
  } | null>;

  /**
   * Reads the current file-local hunk display after DOM calculation.
   *
   * FileCard invokes the accessor reactively while rendering its header. Null
   * means the initial calculation has not supplied a display; the accessor must
   * not navigate, mutate selection, or retain resources between reads.
   */
  fileSelectedHunk: Accessor<HunkPosition | null>;
};

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
  /**
   * Identifies the exact captured file pair used by review and line targets.
   * Both nullable paths remain identity inputs and must match `file_state`.
   */
  reviewFile: ReviewFilePair;

  /**
   * Supplies the lane-derived state for this stable manifest position.
   * Replacing its discriminated branch swaps the card's complete presentation.
   */
  file_state: FileState;

  /**
   * Reports ChangeSet-owned expansion for loaded or lazy content.
   * The card requests changes through `onExpandedChange` and never owns a copy.
   */
  expanded: boolean;

  /**
   * Distinguishes an explicit user or directory collapse from a bodyless Husk.
   * It controls skipped-target participation while the file has no body.
   */
  explicitlyCollapsed: boolean;
  /**
   * Reports whether the file lane has yielded permission to mount FileBody.
   * False keeps expensive in-file rendering unmounted without changing expansion.
   */
  admitted: boolean;
  // TODO: Consider cooperative yields inside FileBody for expensive in-file rendering.

  /**
   * Selects the Tab-wide split or inline presentation for mounted text bays.
   * The card passes it through without keeping local presentation state.
   */
  view: DiffViewMode;

  /**
   * Selects the current fold policy for every mounted text bay in the file.
   * A changed value is consumed reactively by the row renderer.
   */
  aggressiveFolds: boolean;

  /**
   * Supplies Snapshot-scoped URL line navigation and activation behavior.
   * FileCard delegates it to bays and never substitutes its own line state.
   */
  linePins: LinePins;

  /**
   * Handles an explicit expansion change for this exact manifest file.
   *
   * `expanded` is the complete requested state. FullFile invokes the callback
   * after its header control is activated and line preparation invokes it when
   * navigation must reveal a collapsed file; other rendering does not call it.
   * The caller must write the accepted value back through this prop before
   * preparation continues after Solid's flush.
   */
  onExpandedChange: (expanded: boolean) => void;

  /**
   * Handles direct activation of a deferred file's load plank.
   *
   * The callback receives no value because this card's manifest position is
   * already bound by the caller. It is not called on mount or expansion. The
   * caller must enqueue the file with bounded load policy and expose resulting
   * lane activity through `file_state`.
   */
  onLoad: () => void;

  /**
   * Handles direct activation of a failed lazy file's Retry control.
   *
   * The callback receives no value because this card's manifest position is
   * already bound by the caller. It is not called merely because damage is
   * rendered. The caller must enqueue an unbounded retry and return its state
   * through `file_state`.
   */
  onRetry: () => void;
} & HunkCounterProps;

/**
 * Renders one stable manifest-position FileCard and contains its renderer.
 *
 * Callers keep this component mounted at one manifest position and replace only
 * its reactive state: the article persists for its keyed manifest entry, and
 * state replacement swaps complete Husk, Full, or Lazy content without moving
 * query state into the card or retaining partial content from the prior
 * branch. FullFile renderer failures remain inside the stable article;
 * ordinary backend failures arrive as the explicit LazyFile state.
 */
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
    const count = hunkCount();
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

/**
 * Contains an unexpected FullFile renderer failure at its stable FileCard.
 *
 * Callers provide the manifest path and complete renderer subtree. A failure
 * replaces only that subtree with unrecoverable critical damage. The boundary
 * does not retry, preserve failed DOM, synthesize hunks, or change selection.
 */
function FileRendererBoundary(props: {
  /**
   * Reads the stable FileCard article that contains this renderer subtree.
   * The accessor must return the same mounted element while the boundary lives.
   */
  card: Accessor<HTMLElement>;

  /**
   * Names the failed file in the boundary's visible local damage report.
   * The caller supplies the canonical backend display path unchanged.
   */
  path: string;

  /**
   * Contains the complete fallible FullFile renderer subtree.
   * A thrown failure replaces only these children, never the stable article.
   */
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
  /**
   * Reads the stable FileCard marked as damaged for this strip's lifetime.
   * The accessor must still resolve during mount and cleanup.
   */
  card: Accessor<HTMLElement>;

  /**
   * Names the damaged file in both the local strip and global Toast.
   * The canonical backend display path is presented without reinterpretation.
   */
  path: string;

  /**
   * Carries the original thrown renderer value into both presentations.
   * Unknown values remain intact so `presentError` can expose their real form.
   */
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

/**
 * Renders a queued or actively fetching file without reserving body height.
 *
 * The header uses only manifest path and activity. It exposes no body or
 * expansion control while no file or lazy-info result exists, and therefore
 * never reserves the eventual rendered height.
 */
function HuskFile(
  props: {
    /**
     * Supplies queued or fetching activity for this stable manifest position.
     * Husk rendering reads it without beginning or observing transport itself.
     */
    file_state: HuskFileState;

    /**
     * Distinguishes an explicit collapse from a merely bodyless queued file.
     * True makes the Husk pseudo-hunk a skipped target.
     */
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
    /**
     * Supplies stable path, manifest coordinate, and current queue activity.
     * The header presents this lane state without retaining a copy.
     */
    file_state: HuskFileState;

    /**
     * Selects whether this header's Husk pseudo-hunk is marked skipped.
     * The value represents explicit collapse, not ordinary queue state.
     */
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
 * into the body.
 */
function FullFile(
  props: {
    /**
     * Identifies the exact captured file pair for review and line navigation.
     * Both nullable paths must correspond to `file_state.backend_data`.
     */
    reviewFile: ReviewFilePair;
    /**
     * Supplies the successful immutable result for this manifest position.
     * Its backend data feeds headers, frame composition, and hunk calculation.
     */
    file_state: FullFileState;
    /**
     * Reports ChangeSet-owned visibility for the renderer body.
     * Collapsing preserves card-lifetime bay expansion but unmounts bay modes.
     */
    expanded: boolean;
    /**
     * Reports whether the lane has admitted this file's expensive body.
     * A visible nonzero file remains a Husk navigation target until true.
     */
    admitted: boolean;
    /**
     * Selects the Tab-wide split or inline text presentation.
     * FullFile passes it to bays without owning another view value.
     */
    view: DiffViewMode;
    /**
     * Selects the current fold policy for every mounted text bay.
     * The value is reactive and is not persisted by FileCard.
     */
    aggressiveFolds: boolean;
    /**
     * Supplies Snapshot-scoped URL line identity and activation behavior.
     * FullFile delegates it through FrameView without copying its state.
     */
    linePins: LinePins;
    /**
     * Reads FileCard's memoized whole-file hunk count.
     * The header and body share this accessor so neither repeats composition.
     */
    hunkCount: Accessor<number>;
    /**
     * Reads the stable containing FileCard article for DOM interfaces and state.
     * It must return the same mounted element throughout this branch's lifetime.
     */
    card: Accessor<HTMLElement>;

    /**
     * Handles a requested expansion state for this exact loaded file.
     *
     * The header calls it after direct activation and line preparation calls it
     * when navigation needs the body. The caller must feed the accepted boolean
     * back through `expanded`; no other state is changed by this callback.
     */
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
  // The mode registry, by contrast, holds only mounted bays: each bay wrapper
  // registers itself for exactly its mounted lifetime and every bay mounts
  // virtual again, so nothing here persists across a body unmount. The card
  // holds the store because the whole-File aggregate below is derived from it.
  const [bayModes, setBayModes] = createStore<
    Record<string, BayRenderMode | undefined>
  >({});
  const bayRenderModes: BayRenderModes = {
    mode: (bayKey) => {
      return expect(
        bayModes[bayKey],
        `Bay ${bayKey} read its render mode unregistered.`,
      );
    },
    setMode: (bayKey, mode) => setBayModes(bayKey, mode),
    clearMode: (bayKey) => setBayModes(bayKey, undefined),
  };

  /**
   * Derives this File's whole-card render answer from its mounted bays.
   *
   * The FileTree indicator and the header marker present one answer per File,
   * so the card aggregates three outcomes. It reports virtual only while at
   * least one bay is mounted and every mounted bay is virtual, and rich while
   * any mounted bay is rich. It reports null while no bay body is mounted.
   * Collapsed, unadmitted, and bodyless Files have no representation to report.
   */
  const fileRenderMode = createMemo((): BayRenderMode | null => {
    const modes = Object.values(bayModes).filter(
      (mode): mode is BayRenderMode => mode !== undefined,
    );
    if (modes.length === 0) {
      return null;
    }
    return modes.every((mode) => mode === "virtual") ? "virtual" : "rich";
  });
  // This must be an effect under the current component boundary. Mounted bays
  // can change `fileRenderMode` after FullFile renders, but FileCard renders the
  // target card, so FullFile has no JSX attribute it can bind to that changing
  // value. The effect performs the required reactive write for FileTree's
  // attribute observer. FullFile removes the attribute when it unmounts so the
  // persistent card cannot retain the departed body's mode.
  //
  // TODO: Move bay render-mode state to FileCard and bind `data-file-render`
  // in its JSX, eliminating this effect and its cleanup.
  createEffect(() => {
    const mode = fileRenderMode();
    if (mode === null) {
      // props.card() returns an HTMLElement, which filetree would observe
      delete props.card().dataset.fileRender;
    } else {
      props.card().dataset.fileRender = mode;
    }
  });
  onCleanup(() => {
    delete props.card().dataset.fileRender;
  });

  return (
    <>
      <FullFileHeader
        file_state={props.file_state}
        expanded={props.expanded}
        virtualized={props.expanded && fileRenderMode() === "virtual"}
        awaitingAdmission={
          props.expanded && !props.admitted && props.hunkCount() > 0
        }
        hunkCount={props.hunkCount}
        globalSelectedHunk={props.globalSelectedHunk}
        fileSelectedHunk={props.fileSelectedHunk}
        onExpandedChange={props.onExpandedChange}
      />
      <FileRendererBoundary card={props.card} path={backend_data.display_name}>
        <FullFileRenderer
          {...props}
          bayExpansion={bayExpansion}
          bayRenderModes={bayRenderModes}
        />
      </FileRendererBoundary>
    </>
  );
}

/**
 * Renders and operates the fallible body beneath one stable Full File header.
 *
 * FileRendererBoundary contains this complete subtree. The component attaches
 * the line-preparation operation to its FileCard for the mounted lifetime,
 * but it neither renders nor replaces the independent header review action.
 * Rich/virtual representation belongs to the individual bays inside the body.
 */
function FullFileRenderer(
  props: {
    /**
     * Identifies the captured file pair every prepared target must match.
     * A target from another pair is a structural contradiction and throws.
     */
    reviewFile: ReviewFilePair;
    /**
     * Supplies complete backend frames plus this file's manifest coordinate.
     * The renderer treats the successful result as immutable.
     */
    file_state: FullFileState;
    /**
     * Controls whether FrameView or coordinate-preserving skip anchors render.
     * The value remains authoritative in ChangeSet.
     */
    expanded: boolean;
    /**
     * Reports whether the file lane permits mounting expensive FrameView work.
     * Preparation requires true and asserts if called before admission.
     */
    admitted: boolean;
    /**
     * Selects the Tab-wide split or inline presentation delegated to text bays.
     * Non-text bay rendering does not reinterpret this value.
     */
    view: DiffViewMode;
    /**
     * Selects the current folding policy delegated to each text grid.
     * This renderer retains no second fold-policy state.
     */
    aggressiveFolds: boolean;
    /**
     * Supplies Snapshot-scoped URL line behavior for rendered grids.
     * It remains the single line-navigation representation throughout the file.
     */
    linePins: LinePins;
    /**
     * Reads the stable containing FileCard for preparation and bay lookup.
     * It must remain connected unless this renderer is being disposed.
     */
    card: Accessor<HTMLElement>;

    /**
     * Handles expansion required by line preparation.
     *
     * The renderer calls it with `true` only when a target must reveal a
     * collapsed File. The caller must publish that accepted value through
     * `expanded` before the renderer resumes after Solid's flush.
     */
    onExpandedChange: (expanded: boolean) => void;

    /**
     * Supplies card-lifetime bay expansion state used during navigation.
     * Preparation writes the exact target bay before searching for its wrapper.
     */
    bayExpansion: BayExpansion;

    /**
     * Supplies the mounted-bay render-mode registry shared with the File header.
     * Entries exist only while their corresponding bay wrapper is mounted.
     */
    bayRenderModes: BayRenderModes;
  } & HunkCounterProps,
): JSX.Element {
  const backend_data = props.file_state.backend_data;
  // This throwaway mode replaces exact changed-code bodies in the captured repository.
  // The canonical backend result continues to supply headers and hunk stops.
  const prototypeSearch = new URLSearchParams(window.location.search);
  const structureFixture =
    import.meta.env.DEV &&
    prototypeSearch.get("prototype") === "file-structure" &&
    prototypeSearch.get("tab") === "head" &&
    prototypeSearch.get("repo_id") === String(structureMetadata.projectId)
      ? structureMetadata.paths.find(
          (path) => path === backend_data.display_name,
        )
      : undefined;
  const [prototypeSource, setPrototypeSource] = createSignal(false);
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

  /**
   * Prepares one exact semantic line inside this admitted FullFile.
   *
   * Navigation supplies the complete URL target and its operation AbortSignal.
   * The operation expands this FileCard, expands and enriches the one bay the
   * pin names, resolves that bay's line host, and delegates the rest to it. It
   * does not fetch, scroll, paint, parse the URL, or select a hunk.
   *
   * @param target Complete captured-file, bay, side, and line coordinates.
   * @param abortSignal Lifetime of the active navigation operation.
   */
  async function prepareLine_impl(
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
    // Pins and review jumps prepare actual engine source, never fixture rows.
    // The explicit switch restores the ordinary bay interface before lookup.
    if (structureFixture !== undefined && !prototypeSource()) {
      setPrototypeSource(true);
      await Promise.resolve();
    }
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
    // gave it. The bay may be closed, so its card-owned expansion is
    // written before its wrapper is searched; a key this composed diff does
    // not contain expands nothing and is answered by the missing wrapper
    // below.
    const bayKey = target.bay.bay_key;
    props.bayExpansion.setExpanded(bayKey, true);
    // The expansion is a store write, so a closed bay's wrapper is mounted
    // by the resulting flush. The yield lets that flush run before the
    // wrapper below is queried.
    await Promise.resolve();
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    // A text bay's grid exists only while that bay is rich, so the pin is
    // answered through the bay wrapper every kind mounts. Enrich that one bay,
    // then search inside it. The wrapper is addressed by its bay key alone.
    // `data-bay-render` names a text bay's rich-or-virtual mode, which a bay
    // holding captured bytes has no equivalent of.
    const wrapper = props
      .card()
      .querySelector<HTMLElement>(`[data-bay-key="${CSS.escape(bayKey)}"]`);
    if (wrapper === null) {
      return { state: "missing" };
    }
    const waitToEnrich: unknown = Reflect.get(wrapper, "waitToEnrich_impl");
    assert(
      typeof waitToEnrich === "function",
      "Bay omitted its enrichment operation.",
    );
    await Reflect.apply(waitToEnrich, wrapper, []);
    if (abortSignal.aborted || !props.card().isConnected) {
      return { state: "stopped" };
    }
    const gridRoot = expect(
      wrapper.querySelector<HTMLElement>(
        `.diff-grid[data-review-bay="${CSS.escape(bayKey)}"]`,
      ),
      "Enriched bay did not mount its grid.",
    );
    const grid = expect(
      gridRoot.querySelector<HTMLElement>(".diff-lines"),
      "Prepared bay grid disappeared.",
    );
    const prepareLine: unknown = Reflect.get(grid, "prepareLine_impl");
    assert(
      typeof prepareLine === "function",
      "Bay line host omitted its preparation operation.",
    );
    const result: unknown = await Reflect.apply(prepareLine, grid, [
      target,
      abortSignal,
    ]);
    assert(
      typeof result === "object" &&
        result !== null &&
        "state" in result &&
        (result.state === "ready" ||
          result.state === "missing" ||
          result.state === "stopped"),
      "Bay line host returned an invalid preparation result.",
    );
    if (result.state === "ready") {
      assert(
        "row" in result && result.row instanceof HTMLElement,
        "Ready line preparation omitted its rendered row.",
      );
      return { state: "ready", row: result.row };
    }
    return { state: result.state };
  }

  // Navigation discovers line preparation through the stable FileCard DOM.
  // Publish the operation only while this admitted FullFile renderer is mounted
  // and remove it before a replacement renderer can expose another one.
  onMount(() => {
    const card = Object.assign(props.card(), {
      prepareLine_impl,
    }) satisfies PreparableFileCard;
    onCleanup(() => {
      Reflect.deleteProperty(card, "prepareLine_impl");
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
              const identity: SkippedHunkIdentity = {
                fileIndex: props.file_state.fileIndex,
                kind: "skip",
                bay: stop.bay,
                hunkIndex: stop.hunkIndex,
              };
              return (
                <span
                  class="hunk-skip skip"
                  data-hunk-target
                  data-hunk-kind={identity.kind}
                  data-file-index={identity.fileIndex}
                  data-hunk-bay={identity.bay}
                  data-hunk-index={identity.hunkIndex}
                />
              );
            }}
          </For>
        </div>
      </Show>
      <Show when={props.expanded && props.admitted}>
        <div class="file-card-body" data-file-body>
          <Show when={structureFixture !== undefined}>
            <div class="fsp-toolbar">
              <span>
                Frozen sem capture · genuine before/after graph relationships
              </span>
              <div>
                <button
                  type="button"
                  aria-pressed={!prototypeSource()}
                  onClick={() => setPrototypeSource(false)}
                >
                  Structure
                </button>
                <button
                  type="button"
                  aria-pressed={prototypeSource()}
                  onClick={() => setPrototypeSource(true)}
                >
                  Source diff
                </button>
              </div>
            </div>
          </Show>
          <Show
            when={structureFixture !== undefined && !prototypeSource()}
            fallback={
              <>
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
                />
              </>
            }
          >
            <Suspense
              fallback={
                <div class="fsp-loading">
                  <p>Loading frozen sem graph…</p>
                  <For each={hunkStops}>
                    {(stop) => (
                      <span
                        data-hunk-target
                        data-hunk-kind="real"
                        data-file-index={props.file_state.fileIndex}
                        data-hunk-bay={stop.bay}
                        data-hunk-index={stop.hunkIndex}
                      >
                        Source change {stop.hunkIndex + 1}
                      </span>
                    )}
                  </For>
                </div>
              }
            >
              <Show when={CapturedFileStructure} keyed>
                {(Prototype) => (
                  <Prototype
                    fixturePath={expect(
                      structureFixture,
                      "The structural body requires a matching fixture.",
                    )}
                    backendData={backend_data}
                    fileIndex={props.file_state.fileIndex}
                    hunkStops={hunkStops}
                    onSource={() => setPrototypeSource(true)}
                  />
                )}
              </Show>
            </Suspense>
          </Show>
        </div>
      </Show>
    </>
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
    /**
     * Supplies successful backend identity, file kind, and line statistics.
     * The header treats this lane result as immutable presentation input.
     */
    file_state: FullFileState;
    /**
     * Reports ChangeSet-owned visibility for the File body.
     * The sole header control requests its inverse through the callback.
     */
    expanded: boolean;
    /**
     * Reports whether every currently mounted bay uses virtual presentation.
     * The value changes only the visibility indicator, not expansion itself.
     */
    virtualized: boolean;
    /**
     * Reports whether a nonzero expanded file still awaits lane admission.
     * While true, the header exposes the temporary Husk navigation identity.
     */
    awaitingAdmission: boolean;
    /**
     * Reads FileCard's memoized whole-file hunk count.
     * The value selects zero-file identity without repeating bay composition.
     */
    hunkCount: Accessor<number>;

    /**
     * Handles direct activation of this header's visibility control.
     *
     * The callback receives the inverse of the current `expanded` value. It is
     * not called by mounting or counter updates. The caller must accept the
     * choice by passing it back through `expanded` for the body to change.
     */
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
  /**
   * Reports whether the composed file currently contains no real hunk targets.
   *
   * The answer comes from FileCard's memoized composition and selects the
   * permanent zero pseudo-hunk; it does not inspect DOM or lane activity.
   */
  const zeroHunkFile = (): boolean => props.hunkCount() === 0;

  /**
   * Constructs the indexed pseudo-hunk currently placed by this header.
   *
   * Zero files retain their permanent identity. A loaded file awaiting body
   * admission temporarily exposes a Husk identity; an admitted nonzero file
   * leaves all real identities to its body renderer.
   *
   * # Returns
   *
   * - The indexed `zero` or temporary `husk` identity carried by this header.
   * - `null`: The admitted nonzero File exposes its hunk identities in the body,
   *   so the header must omit all hunk-target attributes.
   */
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

  return (
    <header
      ref={header}
      class="file-card-header full-file-header"
      classList={{ skip: zeroHunkFile() && !props.expanded }}
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

/**
 * Renders intentionally delayed content or complete localized query damage.
 *
 * Deferred planks submit exactly one explicit load command. Error planks retain
 * the complete message, open stack, and RetryButton; neither branch invokes the
 * command without direct user activation.
 */
function LazyFileView(
  props: {
    /**
     * Supplies deferred metadata or localized failure for this manifest entry.
     * The discriminated result selects exactly one explicit body action.
     */
    file_state: LazyFileState;
    /**
     * Reports ChangeSet-owned visibility of the load or failure plank.
     * Collapsed lazy files retain only their coordinate-preserving skip target.
     */
    expanded: boolean;

    /**
     * Handles direct activation of the deferred-file load plank.
     *
     * It receives no value because the caller has bound this manifest position.
     * The callback is not invoked by expansion or mount; it must enqueue the
     * bounded load and publish activity through a later `file_state` value.
     */
    onLoad: () => void;

    /**
     * Handles direct activation of the failed-file Retry control.
     *
     * It receives no value because the caller has bound this manifest position.
     * Rendering damage does not invoke it; it must enqueue an unbounded retry
     * and publish activity through a later `file_state` value.
     */
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
    /**
     * Supplies deferred metadata or localized failure identity for the header.
     * The header reads it without triggering a load or retry.
     */
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

/**
 * Renders the colored explicit-fetch plank for one backend delay reason.
 *
 * The complete LazyInfoFile is required. Activation reports only the supplied
 * callback, leaving file-fetch ordering and loading presentation with ChangeSet.
 */
function DeferredFilePlank(props: {
  /**
   * Supplies the stable manifest coordinate for this lazy pseudo-hunk.
   * It identifies navigation only; the callback already binds the file action.
   */
  fileIndex: number;
  /**
   * Supplies backend display metadata and the exact reason content was deferred.
   * A null reason is invalid for this plank and throws during label selection.
   */
  info: LazyInfoFile;

  /**
   * Handles direct activation of this exact deferred-file plank.
   *
   * It receives no value because `fileIndex` is already bound by the caller.
   * Mounting, labeling, and expansion do not invoke it. The caller must enqueue
   * the bounded load and replace the lane state as activity changes.
   */
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
        assert(false, "LazyFile metadata requires a lazy reason.");
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
 * Renders exact per-file line statistics supplied by a FullFile response.
 *
 * The summary is complete. These values remain file-local and never contribute
 * to manifest totals or sequence progress.
 */
function FileStatistics(props: {
  /**
   * Contains complete backend line totals for one successfully rendered file.
   * Values are displayed unchanged and do not contribute to manifest totals.
   */
  summary: FileDiff["summary"];
}): JSX.Element {
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
function LazyStatistics(props: {
  /**
   * Contains the limited statistics available before deferred content loads.
   * Null added or removed counts remain visibly unknown rather than becoming 0.
   */
  info: LazyInfoFile;
}): JSX.Element {
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
 * The indicator reflects ChangeSet-owned expansion and the card's aggregated
 * bay render answer. A fully virtual File uses the established V marker;
 * ordinary rich and lazy content retain the existing expansion presentation.
 */
function VisibilityIndicator(props: {
  /**
   * Reports whether rich file content is mounted and visible.
   * Virtualized content deliberately leaves this false.
   */
  visible: boolean;

  /**
   * Reports whether all currently mounted file bays are virtual.
   * This state takes visual precedence over ordinary visible presentation.
   */
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

/**
 * Renders one captured changed-code declaration tree inside its real FileCard.
 *
 * Source and declaration facts come from frozen current-worktree files and sem output.
 * Present declarations retain new-source order within their parent. Removed
 * siblings retain their old position before the next surviving sibling, or
 * after their preceding sibling when no successor survives. Status never sorts
 * declarations; the captured sem parent chains supply every known relationship path.
 * Every arrow retains its raw sem refType and before/after evidence, including false matches.
 * Local disclosures live only for this body mount. Backend hunk coordinates
 * remain authoritative; fixture source has no review, pin, or engine identity.
 */
function FileStructurePrototype(props: {
  /** Exact changed-code FileCard identity covered by the frozen capture. */
  fixturePath: string;
  /** Genuine sem output and exact captured source, loaded only in this prototype. */
  capture: typeof structureCaptureType;
  /** Actual composed diff used exclusively to locate original engine hunk source. */
  backendData: FileDiff;
  /** Stable manifest coordinate passed unchanged to hunk targets. */
  fileIndex: number;
  /** Complete original hunk coordinates, in canonical backend order. */
  hunkStops: {
    /** Original composed bay identity, never a fixture node identity. */
    bay: string;
    /** Original bay-local engine hunk coordinate. */
    hunkIndex: number;
  }[];
  /** Explicitly restores the real source renderer for unrepresented changes. */
  onSource: () => void;
}): JSX.Element {
  const structureCapture = props.capture;
  const fixture = expect(
    structureCapture.files.find((file) => file.path === props.fixturePath),
    "A sem body requires its exact captured file.",
  );
  const [selected, setSelected] = createSignal<string | null>(null);
  const [sourceNodes, setSourceNodes] = createSignal<Set<string>>(new Set());
  const [allCalls, setAllCalls] = createSignal<Set<string>>(new Set());
  const [allIncoming, setAllIncoming] = createSignal<Set<string>>(new Set());
  const nodes = fixture.nodes;
  const capturedNodes = structureCapture.files.flatMap<
    (typeof structureCapture.files)[number]["nodes"][number]
  >((file) => file.nodes);
  const capturedIds = new Set<string>(capturedNodes.map((node) => node.id));
  const capturedParentIds = new Set<string | null>(
    capturedNodes.map((node) => node.parentId),
  );
  // An explicit relation activation reveals captured source locally. Foreign
  // unchanged files remain read-only metadata; there is no pretend destination.
  const [reference, setReference] = createSignal<{
    /** Declaration row that hosts this explicit source peek. */
    row: string;
    /** Actual sem entity identity whose captured source is displayed. */
    target: string;
  } | null>(null);
  const referenceNode = createMemo(() => {
    const active = reference();
    return active === null
      ? null
      : expect(
          capturedNodes.find((node) => node.id === active.target),
          "A source peek requires a declaration in a captured changed file.",
        );
  });

  /**
   * Labels a known declaration by its actual source-side file and full ancestry.
   *
   * Removed edges use the old snapshot. Other edges use the new snapshot.
   * Ancestry comes from sem parentId chains, never from splitting an entity id.
   *
   * @param node Known fixture declaration with ancestry on the displayed source side.
   * @param status Exact added, removed, or unchanged relationship state selecting that side.
   */
  function declarationPath(
    node: (typeof structureCapture.entities)[number],
    status: string,
  ): string {
    assert(
      status === "added" || status === "removed" || status === "unchanged",
      "A call path requires a known relationship status.",
    );
    const ancestry = expect(
      status === "removed" ? node.oldAncestry : node.newAncestry,
      "A known relationship declaration requires ancestry on its displayed side.",
    );
    return (
      [
        status === "removed"
          ? expect(
              node.oldPath,
              "A before-side reference requires its captured file path.",
            )
          : node.path,
        ...ancestry,
      ].join(" → ") +
      (node.kind === "function" || node.kind === "method" ? "()" : "")
    );
  }
  /**
   * Describes the call change independently of whether its caller was created.
   *
   * @param caller Actual declaration making the call in the fixture.
   * @param status Exact added, removed, or unchanged relationship state.
   * @param refType Native sem relationship kind, displayed without translation.
   */
  function callChange(
    caller: (typeof structureCapture.entities)[number],
    status: string,
    refType: string,
  ): string {
    if (status === "added") {
      return refType === "calls"
        ? caller.status === "added"
          ? "New caller calls"
          : "Started calling"
        : caller.status === "added"
          ? `New declaration · ${refType}`
          : `Added ${refType}`;
    }
    if (status === "removed") {
      return refType === "calls"
        ? caller.status === "removed"
          ? "Removed caller called"
          : "Stopped calling"
        : caller.status === "removed"
          ? `Removed declaration · ${refType}`
          : `Removed ${refType}`;
    }
    assert(
      status === "unchanged",
      "A call description requires a known relationship status.",
    );
    return refType === "calls" ? "Existing call" : `Existing ${refType}`;
  }

  // Each original stop is labelled using its actual row's source coordinate.
  // Keep this strip in backend order: tree order cannot renumber engine hunks.
  const stops = props.hunkStops.map((stop) => {
    const bay = expect(
      props.backendData.frames
        .flatMap((frame) => frame.bays)
        .find((candidate) => candidate.bay_key === stop.bay),
      "Prototype hunk bay is missing.",
    );
    const row =
      "rows" in bay.kind_data
        ? bay.kind_data.rows.find(
            (candidate) => candidate.hunk_index === stop.hunkIndex,
          )
        : undefined;
    const node =
      row === undefined
        ? undefined
        : nodes.find(
            (candidate) =>
              (row.right_no !== null &&
                candidate.newRange !== null &&
                row.right_no >= candidate.newRange[0]! &&
                row.right_no <= candidate.newRange[1]!) ||
              (row.left_no !== null &&
                candidate.oldRange !== null &&
                row.left_no >= candidate.oldRange[0]! &&
                row.left_no <= candidate.oldRange[1]!),
          );
    return { ...stop, node };
  });

  return (
    <div class="fsp-body">
      <div class="fsp-provenance">
        {structureCapture.provenance.semVersion} · captured{" "}
        {structureCapture.provenance.capturedAt} · HEAD{" "}
        {structureCapture.provenance.head.slice(0, 8)} · scope{" "}
        {structureCapture.provenance.extensions.join(", ")}. This is a frozen
        tool capture; reported targets may be incorrect.
      </div>
      <div class="fsp-legend">
        <span class="fsp-state added">+ added</span>
        <span class="fsp-state removed">− removed</span>
        <span class="fsp-state modified">~ modified declaration</span>
        <span class="fsp-state unchanged">unchanged</span>
        <span>Arrows: relationship changes. Labels: declaration changes.</span>
      </div>
      <Show when={stops.length > 0}>
        <div class="fsp-hunks">
          <span>Source changes</span>
          <For each={stops}>
            {(stop) => (
              <button
                type="button"
                data-hunk-target
                data-hunk-kind="real"
                data-file-index={props.fileIndex}
                data-hunk-bay={stop.bay}
                data-hunk-index={stop.hunkIndex}
                onClick={() => {
                  if (stop.node === undefined) {
                    props.onSource();
                    return;
                  }
                  setSelected(stop.node.id);
                  setSourceNodes(
                    (current) => new Set([...current, stop.node!.id]),
                  );
                }}
              >
                {stop.node === undefined
                  ? "Module source / imports"
                  : stop.node.name}
              </button>
            )}
          </For>
        </div>
      </Show>
      <div class="fsp-column-labels">
        <span>Incoming · sem reports</span>
        <span>Declarations</span>
        <span>Outgoing · sem reports calls / typeref</span>
      </div>
      <For each={nodes}>
        {(node) => {
          // Include every present call-change category in the compact sample.
          // Remaining edges use one explicit disclosure, with no inferred data.
          const initialCalls = ["unchanged", "added", "removed"].flatMap(
            (status) => {
              const edge = node.outgoing.find(
                (candidate) => candidate.status === status,
              );
              return edge === undefined ? [] : [edge];
            },
          );
          for (const edge of node.outgoing) {
            if (initialCalls.length >= 5) {
              break;
            }
            if (!initialCalls.includes(edge)) {
              initialCalls.push(edge);
            }
          }
          /** Reads the explicitly disclosed outgoing edges for this node. */
          const visibleCalls = () =>
            allCalls().has(node.id) ? node.outgoing : initialCalls;
          const incoming = node.incoming.map((edge) => ({
            ...edge,
            caller: expect(
              structureCapture.entities.find(
                (candidate) => candidate.id === edge.nodeId,
              ),
              "An incoming sem edge requires its raw caller entity.",
            ),
          }));
          // Incoming density uses its own disclosure and keeps every delta category visible.
          const initialIncoming = ["unchanged", "added", "removed"].flatMap(
            (status) => {
              const edge = incoming.find(
                (candidate) => candidate.status === status,
              );
              return edge === undefined ? [] : [edge];
            },
          );
          for (const edge of incoming) {
            if (initialIncoming.length >= 5) {
              break;
            }
            if (!initialIncoming.includes(edge)) {
              initialIncoming.push(edge);
            }
          }
          /** Reads this node's explicitly disclosed incoming relationships. */
          const visibleIncoming = () =>
            allIncoming().has(node.id) ? incoming : initialIncoming;
          const hasChildren = capturedParentIds.has(node.id);
          return (
            <section
              class="fsp-node"
              style={{ "--fsp-source-indent": `${(node.depth + 1) * 22}px` }}
              data-node-id={node.id}
              classList={{
                "fsp-selected": selected() === node.id,
                "fsp-child": node.depth > 0,
                "fsp-last-child": node.depth > 0 && node.lastSibling,
              }}
            >
              <div
                class="fsp-node-row"
                style={{ "--fsp-indent": `${node.depth * 22}px` }}
              >
                <span class="fsp-tree-guides" aria-hidden="true">
                  <For each={node.guides}>
                    {(guide) => (
                      <span
                        classList={{ "fsp-guide-half": guide.half }}
                        style={{ left: `${guide.level * 22 - 15}px` }}
                      />
                    )}
                  </For>
                  <Show when={hasChildren}>
                    <span
                      class="fsp-guide-start"
                      style={{ left: `${(node.depth + 1) * 22 - 15}px` }}
                    />
                  </Show>
                  <Show when={node.depth > 0}>
                    <i style={{ left: `${node.depth * 22 - 15}px` }} />
                  </Show>
                </span>
                <div
                  class="fsp-relations fsp-incoming"
                  classList={{
                    "fsp-all-added":
                      visibleIncoming().length > 0 &&
                      visibleIncoming().every(
                        (edge) => edge.status === "added",
                      ),
                    "fsp-all-removed":
                      visibleIncoming().length > 0 &&
                      visibleIncoming().every(
                        (edge) => edge.status === "removed",
                      ),
                  }}
                >
                  <For each={visibleIncoming()}>
                    {(edge) => (
                      <button
                        type="button"
                        class={`fsp-edge ${edge.status}`}
                        data-ref-type={edge.refType}
                        data-raw-before-indices={edge.rawBeforeIndices.join(
                          ",",
                        )}
                        data-raw-after-indices={edge.rawAfterIndices.join(",")}
                        data-edge-direction="incoming"
                        data-edge-status={edge.status}
                        data-reference-status={edge.caller.status}
                        data-caller-status={edge.caller.status}
                        title={`${callChange(edge.caller, edge.status, edge.refType)}: ${declarationPath(edge.caller, edge.status)}`}
                        disabled={!capturedIds.has(edge.caller.id)}
                        onClick={() => {
                          if (edge.caller.path === fixture.path) {
                            setSelected(edge.caller.id);
                          }
                          setReference({
                            row: node.id,
                            target: edge.caller.id,
                          });
                        }}
                      >
                        <span class="fsp-edge-label">
                          <span class={`fsp-reference ${edge.caller.status}`}>
                            {declarationPath(edge.caller, edge.status)}
                          </span>
                          <span class="fsp-call-change">
                            {callChange(edge.caller, edge.status, edge.refType)}
                          </span>
                          <Show when={!capturedIds.has(edge.caller.id)}>
                            <small>Outside current diff · read-only</small>
                          </Show>
                          <Show when={edge.issue !== null}>
                            <small class="fsp-tool-issue">
                              Known sem resolution issue: {edge.issue}
                            </small>
                          </Show>
                        </span>
                        <span class="fsp-arrow" aria-hidden="true" />
                      </button>
                    )}
                  </For>
                </div>
                <button
                  type="button"
                  title={`sem entity kind ${node.kind}. ${node.hierarchyNote === null ? "Hierarchy follows the tool's parentId." : node.hierarchyNote}`}
                  class={`fsp-declaration ${node.status}`}
                  style={{ "margin-left": `${node.depth * 22}px` }}
                  aria-expanded={sourceNodes().has(node.id)}
                  onClick={() => {
                    setSelected(node.id);
                    setSourceNodes((current) => {
                      const next = new Set(current);
                      if (next.has(node.id)) {
                        next.delete(node.id);
                      } else {
                        next.add(node.id);
                      }
                      return next;
                    });
                  }}
                >
                  <span class="fsp-disclosure">
                    {sourceNodes().has(node.id) ? "▾" : "▸"}
                  </span>
                  <span class="fsp-kind">
                    {node.kind === "orphan" ? "source" : node.kind}
                  </span>
                  <strong>
                    {node.kind === "orphan"
                      ? `module-level change · ${node.oldRange !== null ? `old ${node.oldRange[0]}–${node.oldRange[1]}` : `new ${expect(node.newRange, "A sem source change requires its reported range.")[0]}–${expect(node.newRange, "A sem source change requires its reported range.")[1]}`}`
                      : node.name}
                    {node.kind === "function" || node.kind === "method"
                      ? "()"
                      : ""}
                  </strong>
                  <span class="fsp-state">
                    {node.status === "added"
                      ? "+ added"
                      : node.status === "removed"
                        ? "− removed"
                        : node.status === "modified"
                          ? "~ modified"
                          : node.status === "unclassified"
                            ? "sem change unclassified"
                            : ""}
                  </span>
                </button>
                <div
                  class="fsp-relations fsp-outgoing"
                  classList={{
                    "fsp-all-added":
                      visibleCalls().length > 0 &&
                      visibleCalls().every((edge) => edge.status === "added"),
                    "fsp-all-removed":
                      visibleCalls().length > 0 &&
                      visibleCalls().every((edge) => edge.status === "removed"),
                  }}
                >
                  <For each={visibleCalls()}>
                    {(edge) => {
                      const target = expect(
                        structureCapture.entities.find(
                          (candidate) => candidate.id === edge.nodeId,
                        ),
                        "A sem edge requires its raw target entity.",
                      );
                      return (
                        <button
                          type="button"
                          class={`fsp-edge ${edge.status}`}
                          data-ref-type={edge.refType}
                          data-raw-before-indices={edge.rawBeforeIndices.join(
                            ",",
                          )}
                          data-raw-after-indices={edge.rawAfterIndices.join(
                            ",",
                          )}
                          data-edge-direction="outgoing"
                          data-edge-status={edge.status}
                          data-reference-status={target.status}
                          data-caller-status={node.status}
                          disabled={!capturedIds.has(target.id)}
                          title={`${callChange(node, edge.status, edge.refType)}. ${capturedIds.has(target.id) ? "Open actual captured reference source." : "Outside current diff; read-only sem metadata."}`}
                          onClick={() => {
                            const actual = expect(
                              target,
                              "A sem relationship requires its actual endpoint.",
                            );
                            if (actual.path === fixture.path) {
                              setSelected(actual.id);
                            }
                            setReference({ row: node.id, target: actual.id });
                          }}
                        >
                          <span class="fsp-arrow" aria-hidden="true" />
                          <span class="fsp-edge-label">
                            <span class={`fsp-reference ${target.status}`}>
                              {declarationPath(target, edge.status)}
                            </span>
                            <span class="fsp-call-change">
                              {callChange(node, edge.status, edge.refType)}
                            </span>
                            <Show when={!capturedIds.has(target.id)}>
                              <small>Outside current diff · read-only</small>
                            </Show>
                            <Show when={edge.issue !== null}>
                              <small class="fsp-tool-issue">
                                Known sem resolution issue: {edge.issue}
                              </small>
                            </Show>
                          </span>
                        </button>
                      );
                    }}
                  </For>
                </div>
              </div>
              <div class="fsp-node-details">
                <span class="fsp-tree-guides" aria-hidden="true">
                  <For
                    each={node.guides.filter((guide) => guide.half === false)}
                  >
                    {(guide) => (
                      <span style={{ left: `${guide.level * 22 - 15}px` }} />
                    )}
                  </For>
                  <Show when={hasChildren}>
                    <span style={{ left: `${(node.depth + 1) * 22 - 15}px` }} />
                  </Show>
                </span>
                <Show when={incoming.length > initialIncoming.length}>
                  <button
                    type="button"
                    class="fsp-more fsp-more-incoming"
                    onClick={() =>
                      setAllIncoming((current) => {
                        const next = new Set(current);
                        if (next.has(node.id)) {
                          next.delete(node.id);
                        } else {
                          next.add(node.id);
                        }
                        return next;
                      })
                    }
                  >
                    {allIncoming().has(node.id)
                      ? "Fewer incoming relationships"
                      : `Show all ${incoming.length} incoming relationships`}
                  </button>
                </Show>
                <Show when={node.outgoing.length > initialCalls.length}>
                  <button
                    type="button"
                    class="fsp-more"
                    onClick={() =>
                      setAllCalls((current) => {
                        const next = new Set(current);
                        if (next.has(node.id)) {
                          next.delete(node.id);
                        } else {
                          next.add(node.id);
                        }
                        return next;
                      })
                    }
                  >
                    {allCalls().has(node.id)
                      ? "Fewer relationships"
                      : `Show all ${node.outgoing.length} relationships`}
                  </button>
                </Show>
                <Show
                  when={reference()?.row === node.id && referenceNode()}
                  keyed
                >
                  {(target) => (
                    <div class="fsp-source fsp-reference-source">
                      <div class="fsp-source-heading">
                        <span>
                          Captured reference · {target.path} · {target.name}
                        </span>
                        <button
                          type="button"
                          onClick={() => setReference(null)}
                        >
                          Close
                        </button>
                      </div>
                      <div class="fsp-source-heading fsp-reference-columns">
                        <span>Before</span>
                        <span>After</span>
                      </div>
                      <For each={target.rows}>
                        {(row) => (
                          <div class={`fsp-source-row ${row.status}`}>
                            <span class="fsp-source-side">
                              <span>{row.old?.line}</span>
                              <code>{row.old?.text}</code>
                            </span>
                            <span class="fsp-source-side">
                              <span>{row.new?.line}</span>
                              <code>{row.new?.text}</code>
                            </span>
                          </div>
                        )}
                      </For>
                    </div>
                  )}
                </Show>
                <Show when={sourceNodes().has(node.id)}>
                  <div class="fsp-source">
                    <div class="fsp-source-heading">
                      <span>Before</span>
                      <span>After</span>
                    </div>
                    <For each={node.rows}>
                      {(row) => (
                        <div class={`fsp-source-row ${row.status}`}>
                          <span class="fsp-source-side">
                            <span>{row.old?.line}</span>
                            <code>{row.old?.text}</code>
                          </span>
                          <span class="fsp-source-side">
                            <span>{row.new?.line}</span>
                            <code>{row.new?.text}</code>
                          </span>
                        </div>
                      )}
                    </For>
                    <button
                      type="button"
                      class="fsp-full-source"
                      onClick={props.onSource}
                    >
                      Open actual source diff for line pins and comments
                    </button>
                  </div>
                </Show>
              </div>
            </section>
          );
        }}
      </For>
      <details class="fsp-state-details">
        <summary>Capture evidence and local state</summary>
        <pre>
          {JSON.stringify(
            {
              selected: selected(),
              inlineSource: [...sourceNodes()],
              expandedCalls: [...allCalls()],
              expandedIncoming: [...allIncoming()],
              reference: reference(),
              provenance: structureCapture.provenance,
              fixture,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}
