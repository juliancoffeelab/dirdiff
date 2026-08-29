/**
 * Coordinates review markers, anchored inputs, and History for one Snapshot.
 *
 * `ReviewProvider` observes the canonical Thread set, derives line-marker
 * descriptions, registers connected renderer anchors, and holds the single active
 * anchored input or Thread panel. Persisted input comes from the application draft
 * document, while discussion operations publish accepted writes back to the
 * canonical query.
 *
 * Renderers retain their own rows and Files. Review never selects hunks or follows
 * scrolling, and Profile identity remains controlled by the application.
 */
import {
  Show,
  batch,
  createContext,
  createEffect,
  createMemo,
  createSignal,
  on,
  onCleanup,
  untrack,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";
import { createQuery, useQueryClient } from "@tanstack/solid-query";
import { Portal } from "solid-js/web";
import {
  api,
  threadCodePoint,
  threadOutdated,
  type ReviewComment,
  type ReviewFilePair,
  type ReviewId,
  type ReviewTarget,
  type ReviewTextBay,
  type ReviewThread,
  type ThreadCodePoint,
} from "../../api/api";
import { UnexpectedErrorBoundary, useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import { z } from "zod";
import type { DiffViewMode } from "../App";
import type { StoredProfile } from "../Profile";
import {
  newReviewId,
  useReviewDrafts,
  type NewThreadDraft,
  type ReviewDraft,
} from "./drafts";
import { createThreadDiscussion } from "./discussion";
import { ReviewHistory } from "./History";
import { CommentInput, InlineThreadPanel } from "./threadViews";

/**
 * Identity-stable empty array used while review data is unavailable.
 *
 * `reviewThreads` returns this exact array for every empty read. Creating a new
 * array there would make `markerRevision` treat unchanged Thread state as
 * changed. This module never mutates the array.
 */
const NO_THREADS: readonly ReviewThread[] = [];

/**
 * Binds one rendered text grid to the exact Snapshot, File pair, and composed bay
 * that produced it. Renderers pass this identity back for marker reads and actions;
 * it carries no rendered content or writable review state.
 */
export type ReviewTextGridBinding = {
  /**
   * Snapshot that produced the rendered grid. Activation asserts it matches the
   * provider so a retained renderer cannot write into another review boundary.
   */
  snapshot_id: ReviewId;
  /**
   * Manifest File pair used to encode backend review targets for either side.
   * Nullable side paths remain part of marker identity and are not display labels.
   */
  file: ReviewFilePair;
  /**
   * Composed text bay whose stable key distinguishes this grid within the File.
   * The binding carries identity only, never renderer content or mutable row state.
   */
  bay: ReviewTextBay;
};

/**
 * Holds one derived marker index for the current query and draft revision.
 *
 * The provider rebuilds it from canonical Threads and persisted drafts. Renderers
 * read its grouped line facts but never update it or treat it as another authority.
 */
type ReviewMarkerIndex = {
  /**
   * Whether loaded persisted Threads may contribute enabled marker controls.
   * False retains draft markers but disables every resulting line action.
   */
  persistedAvailable: boolean;
  /**
   * Persisted Threads grouped by exact rendered line after placement. Threads with
   * no code point are intentionally absent and remain available through History.
   */
  lineThreads: ReadonlyMap<string, readonly ReviewThread[]>;
  /**
   * New-Thread draft identities grouped by their selected starting line. Ranges
   * contribute one marker at the start rather than a duplicate on every selected line.
   */
  lineDraftIds: ReadonlyMap<string, readonly ReviewId[]>;
  /**
   * Fully derived marker presentation for every line containing a Thread or draft.
   * Lines absent from this map use the availability-dependent shared default.
   */
  lineStates: ReadonlyMap<string, ReviewMarkerState>;
};

/**
 * Reduces one persisted new-Thread draft to the identity facts that affect markers.
 * Body changes leave this value equal; moving to another line or replacing the draft
 * identity invalidates the corresponding marker index.
 */
type ReviewDraftMarker = {
  /**
   * Persisted new-Thread draft represented by this derived marker input. Consumers
   * use it to reopen authoritative draft content rather than copying a body here.
   */
  draftId: ReviewId;
  /**
   * Encoded File, bay, side, and starting line where the draft reopens. It is the
   * equality key for marker invalidation, not a backend or DOM identifier.
   */
  lineKey: string;
};

/**
 * Captures the three identity-stable inputs used to decide whether markers changed.
 * The provider compares tuple members by identity or value and rebuilds the index
 * only when canonical Threads, draft locations, or availability differ.
 */
type ReviewMarkerRevision = readonly [
  readonly ReviewThread[],
  readonly ReviewDraftMarker[],
  boolean,
];

/**
 * Couples the two connected DOM elements required by code-aligned review UI.
 * The provider uses the code cell as a Portal mount and the trigger as the marker
 * identity; renderer cleanup must close uses before detaching either element.
 */
export type ReviewCodeAnchor = {
  /**
   * Connected code cell beside which anchored review presentation mounts. Its
   * parent must be the exact split side or inline row that receives the Portal.
   */
  codeCell: HTMLElement;
  /**
   * Visible marker button used for identity, toggling, and connection checks.
   * Renderer cleanup must close anchored UI before this element is discarded.
   */
  trigger: HTMLButtonElement;
};

/**
 * Describes the sole active new-Thread input and its current presentation mount.
 * It bridges a transient empty value to the persisted draft document without
 * copying meaningful text into provider state.
 */
export type ActiveCommentInput = {
  /**
   * Stable identity shared by transient and persisted input phases. Submission,
   * discard, and settlement all address the input through this value.
   */
  draftId: ReviewId;
  /**
   * Transient empty draft until meaningful text is persisted, then null. A null
   * value requires the identity to resolve exactly once in the shared draft document.
   */
  input: NewThreadDraft | null;
  /**
   * Exact diff row or Thread card element receiving the input Portal. It is placement
   * state only and does not become an authority for the draft body.
   */
  mount: HTMLElement;
  /**
   * Source line for code input, or null for an editor opened inside a Thread.
   * Only code anchors participate in renderer-driven close and same-marker toggling.
   */
  sourceAnchor: ReviewCodeAnchor | null;
};

/**
 * Keeps one marker's selected canonical Thread identities beside their code line.
 * The query remains authoritative for Thread bodies and state; this value carries
 * only panel selection and the connected anchor needed for presentation.
 */
export type ActiveThreadPanel = {
  /**
   * Canonical Thread identities selected by one state marker at the line. Each
   * identity must resolve exactly once from the current canonical query list.
   */
  threadIds: readonly ReviewId[];
  /**
   * Connected line and marker that locate and toggle the shared panel. The renderer
   * must close the panel before removing either element from its DOM subtree.
   */
  anchor: ReviewCodeAnchor;
};

/**
 * Names the line-local action represented by one marker control.
 *
 * `new` creates input, `draft` resumes saved input, and the remaining variants
 * open canonical Threads filtered to the named lifecycle state.
 */
export type ReviewMarkerKind =
  | "new"
  | "draft"
  | "open"
  | "resolved"
  | "deleted";

/**
 * Describes one marker control without copying the Threads or draft behind it.
 * The discriminant selects creation, draft continuation, or a lifecycle group;
 * only lifecycle groups carry a count and outdated-code warning.
 */
export type ReviewMarkerDescriptor =
  | {
      /**
       * Offers creation when no persisted Thread or draft occupies the line. When
       * disabled by its enclosing state, it communicates query unavailability only.
       */
      kind: "new";
    }
  | {
      /**
       * Reopens persisted new-Thread work whose selection starts at the line. Multiple
       * drafts at one line remain an explicit error resolved from History.
       */
      kind: "draft";
    }
  | {
      /**
       * Selects the lifecycle group whose Threads the marker opens. It never combines
       * states or stands for a draft/new-Thread action.
       */
      kind: "open" | "resolved" | "deleted";
      /**
       * Number of loaded Threads in this state at the exact line. Activation expects
       * at least one and opens precisely those canonical identities.
       */
      count: number;
      /**
       * Whether at least one represented Thread no longer rests on unchanged code.
       * The flag affects warning presentation but not whether the marker can open.
       */
      warning: boolean;
    };

/**
 * Returns the complete marker presentation derived for one rendered line.
 * Consumers render the ordered descriptors but must block every activation while
 * `disabled` says the canonical Thread list is unavailable.
 */
export type ReviewMarkerState = {
  /**
   * Prevents activation while persisted Thread data is not authoritative. Markers
   * remain descriptive during loading but must not derive actions from stale data.
   */
  disabled: boolean;
  /**
   * Ordered controls rendered for a draft and each populated Thread state. An empty
   * location instead receives exactly one new-Thread descriptor.
   */
  markers: readonly ReviewMarkerDescriptor[];
};

/** Shared enabled default for a loaded line with no persisted review work. */
const AVAILABLE_NEW_MARKER_STATE: ReviewMarkerState = {
  disabled: false,
  markers: [{ kind: "new" }],
};
/** Shared disabled default shown while the canonical review query is unavailable. */
const DISABLED_NEW_MARKER_STATE: ReviewMarkerState = {
  disabled: true,
  markers: [{ kind: "new" }],
};

/**
 * Exposes the Snapshot-bound review operations that renderers and FileCard need.
 *
 * Consumers may read derived markers, register connected DOM anchors, and request
 * explicit UI actions. They cannot mutate canonical Threads, persisted drafts, or
 * provider presentation state directly.
 */
export type ReviewBinding = {
  /**
   * Immutable Snapshot identity code renderers copy into their grid bindings.
   * It binds every activation and marker read to this provider's exact review.
   */
  snapshotId: ReviewId;
  /**
   * Reads the canonical loaded Threads, or the stable empty value before load.
   * Query publication feeds accepted writes back through later reactive reads;
   * renderers must not mutate the returned array.
   */
  threads: Accessor<readonly ReviewThread[]>;
  /**
   * Reads the identity-stable tuple of Thread data, draft marker inputs, and
   * availability. Grids track it to refresh markers only after one of those
   * authorities changes, never as a writable marker store.
   */
  markerRevision: Accessor<ReviewMarkerRevision>;
  /**
   * Returns the line keys whose marker state changed in the latest revision.
   *
   * `null` means no bounded change set exists (first index build or a
   * persisted-availability flip) and every rendered host must refresh. The
   * set is valid only for the current `markerRevision` value; grids whose
   * rows changed independently refresh fully regardless.
   *
   * # Returns
   *
   * - A set of serialized line keys whose complete marker state may have
   *   changed in the current revision. The set is bounded to that revision.
   * - `null`: No bounded delta exists. Every mounted marker host must repaint
   *   from `markerState`.
   */
  changedMarkerKeys(): ReadonlySet<string> | null;
  /**
   * Called while painting one line, with its grid binding, side, and positive
   * backend line. It returns the current complete marker state; no UI or data is
   * changed. Callers must track `markerRevision` and repaint using this result.
   *
   * @param binding Snapshot, File, and bay rendered by the caller.
   * @param side File side containing the marker host.
   * @param line Positive backend line represented by that host.
   */
  markerState(
    binding: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
  ): ReviewMarkerState;
  /**
   * Called only when a rendered marker is activated, with the exact grid, side,
   * line, connected anchor, represented kind, and whether Shift requested range
   * extension. It may toggle the same UI, extend compatible new-Thread work,
   * open loaded Threads or a persisted draft, or start empty input; unavailable
   * data and an in-flight input prevent activation. Accepted drafts and Threads
   * return through `markerRevision`, while anchored presentation updates before
   * this call returns. Callers must pass the marker kind currently painted at
   * that anchor and must not use this operation for navigation or selection.
   *
   * @param binding Snapshot, File, and bay rendered by the activating grid.
   * @param side File side containing the activated marker.
   * @param line Positive backend line represented by the marker.
   * @param anchor Connected code cell and exact visible button that was activated.
   * @param markerKind Current descriptor kind painted on that button.
   * @param extend Whether Shift requested extension of compatible active input.
   */
  activateTextCommentInput(
    binding: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
    anchor: ReviewCodeAnchor,
    markerKind: ReviewMarkerKind,
    extend: boolean,
  ): void;
  /**
   * Called exactly once on mount and once on cleanup for every File header, with
   * the connected element and corresponding boolean. The provider updates split
   * History geometry after registration; callers must pair identities and invoke
   * unmount before discarding the element. It returns no accepted state to render.
   *
   * @param header Connected File header identity being registered or released.
   * @param mounted True for its mount callback and false for the paired cleanup.
   */
  setFileHeaderMounted(header: HTMLElement, mounted: boolean): void;
  /**
   * Called immediately before a renderer removes or replaces nodes in `container`.
   * The provider synchronously closes anchored input or panels whose trigger is
   * inside it or already disconnected, while leaving unrelated History editors
   * intact. Callers may mutate the DOM only after this operation completes.
   */
  closeAnchoredUi(container: Node): void;
};

/** Context identity for the one Snapshot-bound renderer review interface. */
const ReviewContext = createContext<ReviewBinding>();

/**
 * Return the nearest Snapshot-bound renderer interface.
 *
 * FileCard and grid descendants call this only below ReviewProvider. Missing context
 * is an invariant violation and throws rather than returning a disabled substitute.
 */
export function useReview(): ReviewBinding {
  return expect(useContext(ReviewContext), "Review binding is unavailable.");
}

/**
 * Defines one Snapshot review boundary and its complete external presentation.
 *
 * The caller supplies the exact Snapshot, selected Profile, History visibility,
 * inline ChangeSet grid position, child File lane, and the sole permitted
 * File-view operation. The provider may observe or write review data but must
 * not store Profile, History, navigation, or File-lane state itself.
 */
type ReviewProviderProps = {
  /**
   * Immutable Snapshot shared by manifest, query, drafts, and renderers. Replacing
   * it disposes this provider rather than retargeting its anchored state.
   */
  snapshotId: ReviewId;
  /**
   * Current inline or split layout used only for History placement. Code review
   * anchors remain renderer-provided and are not transformed across view modes.
   */
  view: DiffViewMode;
  /**
   * Parent-owned History visibility retained across Snapshot renderer changes.
   * Review reads it but does not create a competing local visibility signal.
   */
  historyOpen: boolean;
  /**
   * Runs after the user requests a History open or close with the desired value.
   * The Tab state holder may store it and must feed the accepted state back through
   * `historyOpen`; Review does not change History visibility itself.
   */
  onHistoryOpenChange(open: boolean): void;
  /**
   * Live selected Profile, or null. Existing discussions remain readable in either
   * state; authorship is captured only when an explicit write begins.
   */
  profile: StoredProfile | null;
  /**
   * File lane subtree that consumes the Review binding and registers anchors.
   * It renders inside the context before provider-owned Portals and History.
   */
  children: JSX.Element;
  /**
   * Reads the mounted inline History outlet supplied by ChangeSetShell. Null
   * withholds inline History until mount; accepted outlet changes remount only
   * its keyed Portal, and split layout ignores the accessor.
   */
  inlineHistoryTarget: Accessor<HTMLElement | null>;
  /**
   * Called while presenting a located History item with its exact code point.
   * It returns whether that File is currently navigable; false disables go-to
   * and prevents `viewThread` from being called for that control.
   */
  canViewThread(point: ThreadCodePoint): boolean;
  /**
   * Called from enabled History navigation with an exact code point. The ChangeSet
   * may load and scroll to the line, then returns its connected code cell and
   * visible marker, or null when navigation was explicitly stopped. Review opens
   * anchored UI only after the promise resolves; failures are presented as Toasts.
   *
   * # Returns
   *
   * - A connected code cell and visible marker for the requested Thread point.
   * - `null`: Navigation stopped before producing an anchor. Review must leave
   *   the Thread in History instead of opening anchored UI.
   */
  viewThread(point: ThreadCodePoint): Promise<ReviewCodeAnchor | null>;
};

/**
 * Validates the serialized coordinate shared by marker producers and grid readers.
 * Nullable File paths retain added/deleted-side identity; bay, side, and positive
 * line complete the exact rendered location.
 */
export const LineMarkerKeySchema = z.tuple([
  /** Normalized old-side File path, or null when the File lacks that side. */
  z.string().nullable(),
  /** Normalized new-side File path, or null when the File lacks that side. */
  z.string().nullable(),
  /** Non-empty composed bay key containing the rendered line. */
  z.string().min(1),
  /** Captured side on which the rendered line exists. */
  z.enum(["left", "right"]),
  /** Positive one-based backend line number. */
  z.number().int().positive(),
]);

/**
 * Identifies one rendered line: File pair, composed bay key, side, and line.
 *
 * The marker index is keyed by the JSON encoding of this tuple, and TextDiffGrid
 * reads that encoding back to decide which rendered host a changed key names.
 * Encoder and reader live in different modules, so both use this single validated
 * tuple shape instead of maintaining separate positional assumptions.
 */
export type LineMarkerKey = z.infer<typeof LineMarkerKeySchema>;

/**
 * Owns one exact Snapshot's review observation and Comment inputs and renders History.
 *
 * Snapshot replacement disposes this boundary. File rendering and engine
 * replacement remain below it. It consumes the application-owned draft
 * document, so persisted drafts and backend Threads survive renderer
 * replacement. Marker indexes are memoized derivations of those two
 * authorities, not another writable store.
 */
export function ReviewProvider(props: ReviewProviderProps): JSX.Element {
  /**
   * Encodes one exact rendered line location for derived marker lookup only.
   *
   * @param grid Snapshot, File pair, and composed bay rendered by the caller.
   * @param side File side containing the rendered line.
   * @param line Positive backend line number represented by the marker host.
   */
  function lineMarkerKey(
    grid: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
  ): string {
    const key: LineMarkerKey = [
      grid.file.left_path,
      grid.file.right_path,
      grid.bay.bay_key,
      side,
      line,
    ];
    return JSON.stringify(key);
  }

  const draftContext = useReviewDrafts();
  const queryClient = useQueryClient();
  const toast = useToasts();
  const review = createQuery(() => api.review.snapshot(props.snapshotId));
  const drafts = draftContext.drafts;
  const draftError = draftContext.error;
  const submittingDraftIds = draftContext.submittingDraftIds;
  const [activeCommentInput, setActiveCommentInput] =
    createSignal<ActiveCommentInput | null>(null);
  const [activeThreadPanel, setActiveThreadPanel] =
    createSignal<ActiveThreadPanel | null>(null);
  const discussion = createThreadDiscussion({
    snapshotId: props.snapshotId,
    profile: () => props.profile,
    onSubmitted(draftId, succeeded) {
      // Close the anchored input whose submitted draft is about to leave the
      // draft document; reply and edit submissions never match it.
      if (succeeded && activeCommentInput()?.draftId === draftId) {
        setActiveCommentInput(null);
      }
    },
  });
  const {
    commentDeletePending,
    threadStatePending,
    replyDraftForThread,
    editDraftForComment,
    updateReplyDraft,
    replaceDraft,
    profileForWrite,
    changeThreadState,
    openEditDraft,
    tombstoneComment,
  } = discussion;
  const currentProfileId = discussion.profileId;
  const submitReviewDraft = discussion.submitDraft;
  // The History panel stays mounted while closed (CSS hides it), but a
  // hidden scroller's box reads scrollTop 0, so the reading position is
  // tracked from scroll events and written back when the panel opens or
  // its keyed Portal remounts on an inline/split view switch.
  const [splitHistoryTop, setSplitHistoryTop] = createSignal<number | null>(
    null,
  );
  const fileHeaders = new Set<HTMLElement>();
  let firstFileHeader: HTMLElement | null = null;
  // Split-view steady-state detector: a viewport-band IntersectionObserver at
  // the sticky offset records which File headers are currently stuck. While
  // one is, the History placement is constant, so document scroll events skip
  // the hit-tested geometry calculation entirely; band membership changes
  // (File boundaries, collapse, layout shifts) re-run the one existing
  // calculation. The observer never publishes geometry itself.
  let stickyBandObserver: IntersectionObserver | null = null;
  const stickyBandHeaders = new Set<HTMLElement>();
  let fileHeaderObserver: ResizeObserver | null = null;
  let splitHistoryFrame: number | null = null;
  let splitHistoryGeometryFailed = false;
  /** Reads canonical query data through an identity-stable empty initial value. */
  const reviewThreads = (): readonly ReviewThread[] =>
    review.data ?? NO_THREADS;
  const reviewAvailable = createMemo(
    () => review.data !== undefined && !review.isRefetching && !review.isError,
  );
  const draftMarkers = createMemo<readonly ReviewDraftMarker[]>(
    () =>
      drafts().flatMap((draft) => {
        if (
          draft.kind !== "new-thread" ||
          draft.snapshot_id !== props.snapshotId
        ) {
          return [];
        }
        return [
          {
            draftId: draft.draft_id,
            lineKey: lineMarkerKey(
              {
                snapshot_id: draft.snapshot_id,
                file: draft.target.file,
                bay: draft.target.bay,
              },
              draft.target.side,
              draft.target.range.start_line,
            ),
          },
        ];
      }),
    [],
    {
      equals: (previous, next) =>
        previous.length === next.length &&
        previous.every(
          (marker, index) =>
            marker.draftId === next[index]?.draftId &&
            marker.lineKey === next[index]?.lineKey,
        ),
    },
  );
  // Each revision element is itself identity-stable (canonical query data,
  // the equality-guarded draft markers, a boolean), so element identity is
  // content identity: when nothing changed, the memo returns the previous
  // wrapper array and the default reference equality suppresses the
  // notification a fresh array would have caused.
  let lastMarkerRevision: ReviewMarkerRevision | null = null;
  const markerRevision = createMemo<ReviewMarkerRevision>(() => {
    const next: ReviewMarkerRevision = [
      reviewThreads(),
      draftMarkers(),
      reviewAvailable(),
    ];
    if (
      lastMarkerRevision !== null &&
      lastMarkerRevision[0] === next[0] &&
      lastMarkerRevision[1] === next[1] &&
      lastMarkerRevision[2] === next[2]
    ) {
      return lastMarkerRevision;
    }
    lastMarkerRevision = next;
    return next;
  });
  let cachedMarkerRevision: ReviewMarkerRevision | null = null;
  let cachedMarkerIndex: ReviewMarkerIndex | null = null;
  // The bounded delta of the latest index rebuild: keys whose marker state
  // differs from the previous index, or null when no previous comparable
  // index exists. Grids use it to touch only changed hosts.
  let lastChangedKeys: ReadonlySet<string> | null = null;

  /**
   * Reports whether two derived marker states render identically.
   *
   * @param previous Marker presentation retained from the prior index revision.
   * @param next Marker presentation derived from the current authorities.
   */
  function markerStatesEqual(
    previous: ReviewMarkerState,
    next: ReviewMarkerState,
  ): boolean {
    return (
      previous.disabled === next.disabled &&
      previous.markers.length === next.markers.length &&
      previous.markers.every((marker, index) => {
        const other = next.markers[index];
        return (
          other !== undefined &&
          marker.kind === other.kind &&
          ("count" in marker ? marker.count : null) ===
            ("count" in other ? other.count : null) &&
          ("warning" in marker ? marker.warning : null) ===
            ("warning" in other ? other.warning : null)
        );
      })
    );
  }

  /**
   * Return the line-marker index for the current authoritative review revision.
   *
   * The revision identity combines the canonical Thread array, the
   * equality-guarded new-Thread draft markers, and persisted-data availability.
   * The first read of a new identity groups located Threads and draft starting
   * lines by exact encoded line, then derives draft and lifecycle markers for
   * each line. Threads without a current code point remain History-only.
   * Subsequent reads of that identity return the same index object for this
   * ReviewProvider lifetime.
   *
   * `lastChangedKeys` becomes `null` for the first index or an availability
   * change because even unindexed lines change their default marker state.
   * Otherwise it becomes the bounded set of added, removed, or visibly changed
   * line keys. A cached revision without its paired index is an invariant
   * failure and throws instead of hiding the missing cache entry.
   */
  function markerIndex(): ReviewMarkerIndex {
    const revision = markerRevision();
    if (revision === cachedMarkerRevision) {
      return expect(
        cachedMarkerIndex,
        "Marker revision lost its derived index.",
      );
    }
    const lineThreads = new Map<string, ReviewThread[]>();
    const lineDraftIds = new Map<string, ReviewId[]>();
    const lineStates = new Map<string, ReviewMarkerState>();

    /**
     * Appends one item to a derived marker bucket.
     *
     * @param map Mutable index being built for this marker revision.
     * @param key Encoded rendered line receiving the item.
     * @param item Thread or draft identity derived for that line.
     */
    function append<T>(map: Map<string, T[]>, key: string, item: T): void {
      const current = map.get(key);
      if (current === undefined) {
        map.set(key, [item]);
      } else {
        current.push(item);
      }
    }

    // A Thread naming no code contributes no marker: there is no bay to mark,
    // and only History presents such Threads.
    for (const thread of revision[0]) {
      const point = threadCodePoint(thread);
      if (point === null) continue;
      append(
        lineThreads,
        lineMarkerKey(
          {
            snapshot_id: props.snapshotId,
            file: point.file,
            bay: point.bay,
          },
          point.side,
          point.line,
        ),
        thread,
      );
    }
    for (const marker of revision[1]) {
      append(lineDraftIds, marker.lineKey, marker.draftId);
    }
    const lineKeys = new Set([...lineThreads.keys(), ...lineDraftIds.keys()]);
    for (const key of lineKeys) {
      const markers: ReviewMarkerDescriptor[] = [];
      if ((lineDraftIds.get(key)?.length ?? 0) > 0) {
        markers.push({ kind: "draft" });
      }
      if (revision[2]) {
        const counts = {
          open: { count: 0, warning: false },
          resolved: { count: 0, warning: false },
          deleted: { count: 0, warning: false },
        };
        for (const thread of lineThreads.get(key) ?? []) {
          const state = counts[thread.state];
          state.count += 1;
          state.warning ||= threadOutdated(thread);
        }
        for (const kind of ["open", "resolved", "deleted"] as const) {
          const state = counts[kind];
          if (state.count > 0) {
            markers.push({ kind, count: state.count, warning: state.warning });
          }
        }
      }
      if (markers.length === 0) {
        markers.push({ kind: "new" });
      }
      lineStates.set(key, { disabled: !revision[2], markers });
    }
    const index: ReviewMarkerIndex = {
      persistedAvailable: revision[2],
      lineThreads,
      lineDraftIds,
      lineStates,
    };
    const previous = cachedMarkerIndex;
    if (
      previous === null ||
      previous.persistedAvailable !== index.persistedAvailable
    ) {
      // No comparable predecessor: hosts outside lineStates also render
      // differently (availability default), so no bounded delta exists.
      lastChangedKeys = null;
    } else {
      const changed = new Set<string>();
      for (const [key, state] of lineStates) {
        const before = previous.lineStates.get(key);
        if (before === undefined || !markerStatesEqual(before, state)) {
          changed.add(key);
        }
      }
      for (const key of previous.lineStates.keys()) {
        if (!lineStates.has(key)) {
          changed.add(key);
        }
      }
      lastChangedKeys = changed;
    }
    cachedMarkerRevision = revision;
    cachedMarkerIndex = index;
    return index;
  }

  /**
   * Publish the current top edge for fixed Split History placement.
   *
   * Inline view and a previously failed geometry calculation publish `null`.
   * Without a connected File header, the calculation reads the application
   * shell's sticky-header offset. Otherwise it reads the first registered
   * header's sticky position, hit-tests the header currently occupying that
   * band, and publishes the lower edge plus one pixel through `splitHistoryTop`.
   *
   * This function runs only through the frame scheduler. A missing shell,
   * invalid CSS measurement, or unusable header geometry is contained here
   * because viewport callbacks are outside Solid's ErrorBoundary: it disables
   * Split History geometry for the rest of this provider lifetime, publishes
   * `null`, and reports the exact failure once as a Toast.
   */
  function updateSplitHistoryGeometry(): void {
    if (props.view !== "split") {
      setSplitHistoryTop(null);
      return;
    }
    if (splitHistoryGeometryFailed) {
      setSplitHistoryTop(null);
      return;
    }
    try {
      if (firstFileHeader === null || !firstFileHeader.isConnected) {
        const appShells = document.querySelectorAll(".app-shell");
        assert(
          appShells.length === 1 && appShells[0] instanceof HTMLElement,
          "Split History requires one application shell.",
        );
        const appHeaderOffset = Number.parseFloat(
          getComputedStyle(appShells[0]).getPropertyValue(
            "--app-header-sticky-offset",
          ),
        );
        assert(
          Number.isFinite(appHeaderOffset) && appHeaderOffset >= 0,
          "Split History requires a measurable application header offset.",
        );
        setSplitHistoryTop(appHeaderOffset);
        return;
      }
      const firstRect = firstFileHeader.getBoundingClientRect();
      const stickyTop = Number.parseFloat(
        getComputedStyle(firstFileHeader).top,
      );
      assert(
        Number.isFinite(stickyTop) &&
          Number.isFinite(firstRect.left) &&
          Number.isFinite(firstRect.width) &&
          firstRect.width > 0,
        "Split History requires one measurable File header.",
      );
      const hit = document.elementFromPoint(
        firstRect.left + firstRect.width / 2,
        stickyTop + 1,
      );
      const hitHeader = hit?.closest<HTMLElement>(".file-card-header") ?? null;
      const activeHeader =
        hitHeader !== null && fileHeaders.has(hitHeader)
          ? hitHeader
          : firstFileHeader;
      const activeRect = activeHeader.getBoundingClientRect();
      assert(
        Number.isFinite(activeRect.top) &&
          Number.isFinite(activeRect.height) &&
          activeRect.height > 0,
        "Split History requires a measurable sticky File header.",
      );
      setSplitHistoryTop(
        Math.max(stickyTop, activeRect.top) + activeRect.height + 1,
      );
    } catch (error) {
      // Viewport callbacks cannot be contained by Solid's ErrorBoundary. Stop
      // this smallest UI piece and present its exact invariant failure once.
      splitHistoryGeometryFailed = true;
      setSplitHistoryTop(null);
      toast.showError("Could not place Review History", error);
    }
  }

  /**
   * Schedule at most one Split History geometry calculation for the next frame.
   *
   * Calls outside Split view do nothing. Scroll events originating inside the
   * History host do not affect its placement, and document scrolls are skipped
   * while a registered File header occupies the sticky band. Resize observers,
   * header registration, sticky-band changes, and direct initial scheduling pass
   * no event and remain eligible. An already pending frame absorbs later calls;
   * its callback clears the handle before invoking the contained calculation.
   * The provider effect cancels a remaining frame on rerun or disposal.
   *
   * @param event Optional captured viewport event; omitted by observer and direct callers.
   */
  function scheduleSplitHistoryGeometry(event?: Event): void {
    if (props.view !== "split") {
      return;
    }
    if (event?.type === "scroll") {
      if (
        event.target instanceof Element &&
        event.target.closest(".review-history-host") !== null
      ) {
        return;
      }
      // A stuck header pins the placement to a constant, so mid-file scroll
      // frames need no geometry work; boundary transitions change the band
      // membership and re-run the calculation from the observer instead.
      if (stickyBandHeaders.size > 0) {
        return;
      }
    }
    if (splitHistoryFrame !== null) {
      return;
    }
    splitHistoryFrame = requestAnimationFrame(() => {
      splitHistoryFrame = null;
      updateSplitHistoryGeometry();
    });
  }

  /**
   * Replace the observer that tracks File headers occupying the sticky band.
   *
   * Every call disconnects the previous observer and clears its membership.
   * A new observer is created only when Split geometry has not failed, the first
   * header is connected, and its sticky offset is finite and nonnegative. Its
   * viewport root margins form a two-pixel band at that offset. Each callback
   * replaces membership for the reported connected HTML headers, then schedules
   * one geometry calculation; every currently registered header is observed.
   *
   * Later rebuilds dispose replaced observers. The provider effect disconnects
   * the final observer and clears membership when Split view ends or the provider
   * is disposed. A non-HTML callback target is an internal registration invariant
   * failure and throws.
   */
  function rebuildStickyBandObserver(): void {
    stickyBandObserver?.disconnect();
    stickyBandObserver = null;
    stickyBandHeaders.clear();
    if (props.view !== "split" || splitHistoryGeometryFailed) {
      return;
    }
    const sample = firstFileHeader;
    if (sample === null || !sample.isConnected) {
      return;
    }
    const stickyTop = Number.parseFloat(getComputedStyle(sample).top);
    if (!Number.isFinite(stickyTop) || stickyTop < 0) {
      return;
    }
    const bottomInset = Math.max(0, window.innerHeight - stickyTop - 2);
    stickyBandObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          assert(
            entry.target instanceof HTMLElement,
            "History header observer received a non-HTML target.",
          );
          if (entry.isIntersecting && entry.target.isConnected) {
            stickyBandHeaders.add(entry.target);
          } else {
            stickyBandHeaders.delete(entry.target);
          }
        }
        scheduleSplitHistoryGeometry();
      },
      {
        rootMargin: `${-Math.round(stickyTop)}px 0px ${-Math.round(bottomInset)}px 0px`,
      },
    );
    fileHeaders.forEach((header) => stickyBandObserver?.observe(header));
  }

  // This provider-lifetime effect tracks only `props.view`. Split layout needs
  // live DOM observers because File header sizes and viewport position change
  // outside Solid state; inline layout performs the explicit teardown instead.
  // Each rerun disposes its animation frame, ResizeObserver, band observer, and
  // window listeners before installing the split resources, and provider disposal
  // performs the same cleanup. Header membership remains in the separately paired
  // renderer callbacks so a view change does not invent another header authority.
  createEffect(() => {
    if (props.view !== "split") {
      setSplitHistoryTop(null);
      return;
    }
    // File headers are content-sized. These observers exist only for the Split
    // view lifetime and maintain only that fixed History host.
    fileHeaderObserver = new ResizeObserver(() =>
      scheduleSplitHistoryGeometry(),
    );
    fileHeaders.forEach((header) => fileHeaderObserver?.observe(header));
    /** Rebuilds the viewport-sized band before recalculating placement. */
    function handleResize(): void {
      rebuildStickyBandObserver();
      scheduleSplitHistoryGeometry();
    }
    window.addEventListener("scroll", scheduleSplitHistoryGeometry, true);
    window.addEventListener("resize", handleResize);
    rebuildStickyBandObserver();
    scheduleSplitHistoryGeometry();
    onCleanup(() => {
      if (splitHistoryFrame !== null) {
        cancelAnimationFrame(splitHistoryFrame);
        splitHistoryFrame = null;
      }
      fileHeaderObserver?.disconnect();
      fileHeaderObserver = null;
      stickyBandObserver?.disconnect();
      stickyBandObserver = null;
      stickyBandHeaders.clear();
      window.removeEventListener("scroll", scheduleSplitHistoryGeometry, true);
      window.removeEventListener("resize", handleResize);
    });
  });

  /**
   * Return the complete new-Thread draft rendered by the active input.
   *
   * No active input returns `null`. A transient input returns its inline value;
   * after persistence, the active record carries only an identity and this
   * function resolves the exact value from the shared draft document. A missing
   * persisted identity or a non-new-Thread value is an invariant failure and
   * throws rather than substituting empty input.
   *
   * # Returns
   *
   * - The active new-Thread draft, read directly for transient input or resolved
   *   from the shared draft document after persistence.
   * - `null`: No Comment input is active. Close and submission paths must do
   *   nothing that assumes a draft identity.
   */
  function activeCommentInputDraft(): NewThreadDraft | null {
    const active = activeCommentInput();
    if (active === null) return null;
    if (active.input !== null) return active.input;
    const draft = expect(
      drafts().find((draft) => draft.draft_id === active.draftId),
      "Active Comment input lost its persisted draft.",
    );
    assert(
      draft.kind === "new-thread",
      "Code input requires a new-Thread draft.",
    );
    return draft;
  }

  /**
   * Close the active Comment input without discarding meaningful persisted work.
   *
   * Absence and an in-flight submission are no-ops. Transient input closes
   * directly because meaningful text is persisted on input. A persisted blank
   * or whitespace-only draft is removed first and closes only if storage accepts
   * that removal; a nonblank draft remains stored while its Portal closes. Draft
   * identity and kind invariants are enforced by `activeCommentInputDraft`.
   */
  function closeActiveCommentInput(): void {
    const active = activeCommentInput();
    if (active === null || submittingDraftIds().has(active.draftId)) return;
    if (active.input !== null) {
      setActiveCommentInput(null);
      return;
    }
    const draft = activeCommentInputDraft();
    const hasWork = draft !== null && draft.body.trim().length > 0;
    if (draft !== null && !hasWork) {
      removeDraft(draft.draft_id);
    } else {
      setActiveCommentInput(null);
    }
  }

  /**
   * Opens and focuses one prevalidated new-Thread input at its inline mount.
   *
   * The active input is replaced with `draft`, then the function requires the
   * resulting Portal to contain exactly one Comment textarea beneath `mount`
   * and focuses it. A persisted input stores only its identity in local state;
   * the caller must ensure that exact draft remains resolvable from the shared
   * draft document whenever the input is read.
   *
   * @param draft Exact new-Thread input to render.
   * @param persisted Whether the draft already belongs to the shared document.
   * @param mount Connected diff row or Thread card receiving the Portal.
   * @param sourceAnchor Originating code marker, or null for a Thread-contained input.
   *
   * # Failures
   *
   * Missing or duplicate textarea content throws after the active input has
   * changed. Later reads of a persisted input throw if its shared draft is
   * missing or has the wrong kind.
   */
  function openCommentInput(
    draft: NewThreadDraft,
    persisted: boolean,
    mount: HTMLElement,
    sourceAnchor: ReviewCodeAnchor | null,
  ): void {
    setActiveCommentInput({
      draftId: draft.draft_id,
      input: persisted ? null : draft,
      mount,
      sourceAnchor,
    });
    // Solid applies the active input synchronously; focus the resulting Portal
    // content as part of the same explicit open action.
    const textareas = mount.querySelectorAll<HTMLTextAreaElement>(
      ".review-comment-input-line textarea",
    );
    assert(
      textareas.length === 1,
      "Opened Comment input requires one textarea.",
    );
    textareas[0].focus();
  }

  /**
   * Apply one complete active new-Thread input value to draft storage.
   *
   * The replacement must name the sole active input; absence or another identity
   * is an invariant failure. Whitespace-only transient input needs no storage
   * write and succeeds. Whitespace-only persisted input is removed, then the
   * active record is changed back to transient input in the same batch. Nonblank
   * persisted input replaces its stored value; nonblank transient input is added
   * and hands authority to storage by clearing the inline copy only after success.
   *
   * Returns false when the required add, replacement, or removal fails. In that
   * case the published active record and shared draft document remain unchanged.
   * Storage operations also assert their identity transition: add requires no
   * existing match, while replacement and removal require exactly one.
   *
   * @param replacement Complete current value carrying the active draft identity.
   */
  function updateCommentInputDraft(replacement: NewThreadDraft): boolean {
    const active = expect(
      activeCommentInput(),
      "Review input requires one active commentInput.",
    );
    assert(
      active.draftId === replacement.draft_id,
      "Review input changed another Comment input's draft.",
    );
    const stored = drafts().some(
      (draft) => draft.draft_id === replacement.draft_id,
    );
    if (replacement.body.trim().length === 0) {
      if (!stored) return true;
      let removed = false;
      batch(() => {
        removed = draftContext.remove(replacement.draft_id);
        if (removed) {
          setActiveCommentInput({ ...active, input: replacement });
        }
      });
      return removed;
    }
    if (stored) return draftContext.replace(replacement);
    if (!draftContext.add(replacement)) return false;
    setActiveCommentInput({ ...active, input: null });
    return true;
  }

  /**
   * Remove one persisted draft and close matching active input after acceptance.
   *
   * Storage removal and the conditional active-input close are batched so
   * consumers never observe a closed input with the old document. Returns false
   * on storage failure and leaves both values unchanged; removing an unrelated
   * draft does not alter active presentation. The shared document asserts that
   * `draftId` has exactly one persisted match and throws otherwise.
   *
   * @param draftId Exact persisted identity to remove.
   */
  function removeDraft(draftId: ReviewId): boolean {
    let removed = false;
    batch(() => {
      removed = draftContext.remove(draftId);
      if (removed && activeCommentInput()?.draftId === draftId) {
        setActiveCommentInput(null);
      }
    });
    return removed;
  }

  /**
   * Discard one active transient input or remove the named persisted draft.
   *
   * A matching transient input has no storage entry, so it closes immediately
   * and returns true. Every other identity is passed to `removeDraft`, which
   * closes matching persisted input only after storage accepts removal and
   * returns false without presentation changes on failure. That path requires
   * exactly one persisted match and throws for a missing or duplicate identity.
   *
   * @param draftId Exact transient or persisted draft identity to discard.
   */
  function discardCommentInputDraft(draftId: ReviewId): boolean {
    const active = activeCommentInput();
    if (active?.draftId === draftId && active.input !== null) {
      setActiveCommentInput(null);
      return true;
    }
    return removeDraft(draftId);
  }

  /**
   * Opens and focuses transient new-Thread input at one exact code target.
   *
   * Review data and draft storage must both be available. An unavailable
   * authority produces its specific Toast and leaves the current presentation
   * unchanged. Otherwise this function creates a fresh identity for the
   * selected Profile and current Snapshot and makes the empty transient input
   * active at the target row; persistence begins only after meaningful input.
   *
   * @param profile Selected author already accepted for this write action.
   * @param target Validated File, bay, side, and range for the future Thread.
   * @param anchor Connected rendered line required for the input Portal.
   *
   * # Failures
   *
   * A missing anchor, a code cell outside an exact split-side or inline diff
   * row, or the textarea invariant enforced by `openCommentInput` throws.
   */
  function openNewDraft(
    profile: StoredProfile,
    target: ReviewTarget,
    anchor: ReviewCodeAnchor | null,
  ): void {
    if (!reviewAvailable()) {
      const reviewFailure = review.error;
      toast.showError(
        "Review Threads unavailable",
        reviewFailure instanceof Error
          ? reviewFailure
          : new Error("Review Threads are still loading."),
      );
      return;
    }
    const storedDraftFailure = draftError();
    if (storedDraftFailure !== null) {
      toast.showError("Review drafts unavailable", storedDraftFailure);
      return;
    }
    const draft: ReviewDraft = {
      kind: "new-thread",
      draft_id: newReviewId(),
      snapshot_id: props.snapshotId,
      target,
      profile_id: profile.id,
      body: "",
    };
    assert(anchor !== null, "New-Thread input requires its rendered line.");
    const mount = anchor.codeCell.parentElement;
    assert(
      mount !== null &&
        (mount.classList.contains("diff-side") ||
          mount.classList.contains("inline-diff-row")),
      "New-Thread input requires its exact rendered diff row.",
    );
    openCommentInput(draft, false, mount, anchor);
  }

  /**
   * Navigate to one Thread's current code point and open its anchored panel.
   *
   * A Thread without a current code landing is a no-op. Otherwise navigation
   * runs asynchronously through the ChangeSet; an explicit null result also
   * leaves presentation unchanged. A returned anchor must contain one visible
   * marker for the Thread's current lifecycle state or the operation throws an
   * invariant failure. Success closes any active Comment input and replaces the
   * active Thread panel with this sole Thread at that exact marker. Navigation,
   * DOM, and invariant failures are caught and presented as a Toast.
   *
   * @param thread Loaded canonical Thread selected from History.
   */
  function viewThreadInCode(thread: ReviewThread): void {
    const point = threadCodePoint(thread);
    if (point === null) return;
    void (async () => {
      const anchor = await props.viewThread(point);
      if (anchor === null) return;
      const trigger = anchor.trigger.parentElement?.querySelector(
        `[data-review-marker-kind="${thread.state}"]`,
      );
      assert(
        trigger instanceof HTMLButtonElement && trigger.hidden === false,
        "Viewed Thread requires its visible state marker.",
      );
      setActiveCommentInput(null);
      setActiveThreadPanel({
        threadIds: [thread.thread_id],
        anchor: { codeCell: anchor.codeCell, trigger },
      });
    })().catch((error: unknown) =>
      toast.showError("Could not view Thread", error),
    );
  }

  /**
   * Continues one persisted new-Thread draft at its rendered code line.
   *
   * The File must first be admitted by `canViewThread`. Navigation then runs
   * asynchronously; an explicitly stopped navigation returning no anchor is a
   * no-op. Success closes the active Thread panel and opens and focuses this
   * shared draft at the exact returned diff row.
   *
   * Navigation, unavailable-File, row-mount, persisted-draft, and textarea
   * failures are contained and presented as a Toast, so this void action does
   * not expose a rejecting promise to its caller.
   *
   * @param draft Existing shared draft whose editor should reopen.
   * @param point Exact location selected by that draft's starting line.
   */
  function continueDraftInCode(
    draft: NewThreadDraft,
    point: ThreadCodePoint,
  ): void {
    void (async () => {
      if (!props.canViewThread(point)) {
        throw new Error("Load the reviewed File before continuing this draft.");
      }
      const anchor = await props.viewThread(point);
      if (anchor === null) return;
      setActiveThreadPanel(null);
      const mount = anchor.codeCell.parentElement;
      assert(
        mount !== null &&
          (mount.classList.contains("diff-side") ||
            mount.classList.contains("inline-diff-row")),
        "Continuing a new Thread requires its exact rendered row.",
      );
      openCommentInput(draft, true, mount, anchor);
    })().catch((error: unknown) =>
      toast.showError("Could not continue editing draft", error),
    );
  }

  /**
   * Close Comment input mounted inside one exact History Thread before its card changes.
   *
   * Only an input without a code source anchor whose mount is a descendant of
   * the named `data-review-history-thread-id` is affected. Returns false only
   * when that matching input is in flight, which tells History to abort its card
   * toggle. Otherwise it applies ordinary close semantics and returns true;
   * unrelated or absent input is unchanged, and failed removal of an empty
   * persisted draft may leave the matching input open while storage reports the
   * error through the shared document.
   *
   * @param threadId Exact History Thread whose descendant input may close.
   */
  function closeCommentInputInThread(threadId: ReviewId): boolean {
    const commentInput = activeCommentInput();
    if (
      commentInput?.sourceAnchor === null &&
      commentInput.mount.closest(
        `[data-review-history-thread-id="${threadId}"]`,
      ) !== null
    ) {
      if (submittingDraftIds().has(commentInput.draftId)) return false;
      closeActiveCommentInput();
    }
    return true;
  }

  /**
   * Clear the persisted draft document, then close active input on success.
   *
   * A storage failure leaves both the shared document and active presentation
   * unchanged. Successful clearing closes transient or persisted active input
   * only after the cleared document has been published.
   */
  function clearStoredDrafts(): void {
    if (draftContext.clear()) {
      setActiveCommentInput(null);
    }
  }

  const binding: ReviewBinding = {
    snapshotId: props.snapshotId,
    threads: reviewThreads,
    markerRevision,
    changedMarkerKeys() {
      markerIndex();
      return lastChangedKeys;
    },
    markerState(grid, side, line) {
      const index = markerIndex();
      const key = lineMarkerKey(grid, side, line);
      return (
        index.lineStates.get(key) ??
        (index.persistedAvailable
          ? AVAILABLE_NEW_MARKER_STATE
          : DISABLED_NEW_MARKER_STATE)
      );
    },
    activateTextCommentInput(grid, side, line, anchor, markerKind, extend) {
      assert(
        reviewAvailable(),
        "Text review activation requires persisted Threads to be available.",
      );
      assert(
        grid.snapshot_id === props.snapshotId,
        "Text Comment input targeted another Snapshot.",
      );
      const active = activeCommentInput();
      if (active !== null && submittingDraftIds().has(active.draftId)) {
        return;
      }
      if (active?.sourceAnchor?.trigger === anchor.trigger) {
        closeActiveCommentInput();
        return;
      }
      if (activeThreadPanel()?.anchor.trigger === anchor.trigger) {
        setActiveThreadPanel(null);
        return;
      }
      if (
        extend &&
        active !== null &&
        (markerKind === "new" || markerKind === "draft")
      ) {
        const draft = activeCommentInputDraft();
        if (
          draft?.kind === "new-thread" &&
          draft.target.kind === "text" &&
          draft.target.file.left_path === grid.file.left_path &&
          draft.target.file.right_path === grid.file.right_path &&
          draft.target.bay.bay_key === grid.bay.bay_key &&
          draft.target.side === side
        ) {
          const replacement: ReviewDraft = {
            ...draft,
            target: {
              ...draft.target,
              range: {
                start_line: Math.min(draft.target.range.start_line, line),
                end_line: Math.max(draft.target.range.end_line, line),
              },
            },
          };
          if (active.input !== null) {
            setActiveCommentInput({ ...active, input: replacement });
          } else {
            replaceDraft(replacement);
          }
          return;
        }
      }
      const index = markerIndex();
      assert(
        index.persistedAvailable,
        "Review Threads must be available before deriving line markers.",
      );
      const lineThreads =
        index.lineThreads.get(lineMarkerKey(grid, side, line)) ?? [];
      const existing = lineThreads.filter(
        (thread) => thread.state === markerKind,
      );
      if (
        markerKind === "open" ||
        markerKind === "resolved" ||
        markerKind === "deleted"
      ) {
        assert(
          existing.length > 0,
          "Thread marker requires at least one matching Thread.",
        );
        setActiveCommentInput(null);
        setActiveThreadPanel({
          threadIds: existing.map((thread) => thread.thread_id),
          anchor,
        });
        return;
      }
      const existingDraftIds =
        index.lineDraftIds.get(lineMarkerKey(grid, side, line)) ?? [];
      if (existingDraftIds.length > 1) {
        toast.showError(
          "Multiple review drafts at this location",
          new Error("Open the draft you want from History."),
        );
        return;
      }
      const existingDraftId = existingDraftIds[0];
      if (existingDraftId !== undefined) {
        assert(
          markerKind === "draft",
          "Persisted review draft requires its Draft marker.",
        );
        const existingDraft = expect(
          drafts().find((draft) => draft.draft_id === existingDraftId),
          "Indexed review draft lost its authoritative value.",
        );
        assert(
          existingDraft.kind === "new-thread",
          "Code draft marker requires a new-Thread draft.",
        );
        if (active?.draftId === existingDraft.draft_id) {
          if (existingDraft.body === "") {
            removeDraft(existingDraft.draft_id);
          } else {
            setActiveCommentInput(null);
          }
          return;
        }
        setActiveThreadPanel(null);
        const mount = anchor.codeCell.parentElement;
        assert(
          mount !== null &&
            (mount.classList.contains("diff-side") ||
              mount.classList.contains("inline-diff-row")),
          "Review draft requires its exact rendered diff row.",
        );
        openCommentInput(existingDraft, true, mount, anchor);
        return;
      }
      assert(
        markerKind === "new",
        "Empty review location requires its new-Comment marker.",
      );
      const profile = profileForWrite();
      if (profile === null) return;
      openNewDraft(
        profile,
        {
          kind: "text",
          file: grid.file,
          bay: grid.bay,
          side,
          range: { start_line: line, end_line: line },
        },
        anchor,
      );
    },
    setFileHeaderMounted(header, mounted) {
      if (mounted) {
        assert(
          !fileHeaders.has(header),
          "Review File header was mounted more than once.",
        );
        fileHeaders.add(header);
        if (
          firstFileHeader === null ||
          (header.compareDocumentPosition(firstFileHeader) &
            Node.DOCUMENT_POSITION_FOLLOWING) !==
            0
        ) {
          firstFileHeader = header;
        }
        fileHeaderObserver?.observe(header);
        if (stickyBandObserver === null) {
          // The band needs a mounted header to read the sticky offset from;
          // the first registration in Split view creates it.
          rebuildStickyBandObserver();
        } else {
          stickyBandObserver.observe(header);
        }
      } else {
        assert(
          fileHeaders.delete(header),
          "Review File header was unmounted without a matching mount.",
        );
        fileHeaderObserver?.unobserve(header);
        stickyBandObserver?.unobserve(header);
        stickyBandHeaders.delete(header);
        if (firstFileHeader === header) {
          firstFileHeader = null;
          for (const candidate of fileHeaders) {
            if (
              firstFileHeader === null ||
              (candidate.compareDocumentPosition(firstFileHeader) &
                Node.DOCUMENT_POSITION_FOLLOWING) !==
                0
            ) {
              firstFileHeader = candidate;
            }
          }
        }
      }
      scheduleSplitHistoryGeometry();
    },
    closeAnchoredUi(container) {
      untrack(() => {
        batch(() => {
          const commentInput = activeCommentInput();
          if (
            commentInput !== null &&
            commentInput.sourceAnchor !== null &&
            (!commentInput.sourceAnchor.trigger.isConnected ||
              container.contains(commentInput.sourceAnchor.trigger))
          ) {
            setActiveCommentInput(null);
          }
          const panel = activeThreadPanel();
          if (
            panel !== null &&
            (!panel.anchor.trigger.isConnected ||
              container.contains(panel.anchor.trigger))
          ) {
            setActiveThreadPanel(null);
          }
        });
      });
    },
  };

  return (
    <ReviewContext.Provider value={binding}>
      {props.children}
      <UnexpectedErrorBoundary
        title="Could not render review Threads"
        retryOnR={false}
      >
        <CommentInput
          drafts={drafts}
          active={activeCommentInput}
          draftError={draftError}
          reviewAvailable={reviewAvailable}
          submittingDraftIds={submittingDraftIds}
          onDraftChange={updateCommentInputDraft}
          onDiscard={discardCommentInputDraft}
          onClose={closeActiveCommentInput}
          onSubmit={submitReviewDraft}
        />
        <InlineThreadPanel
          active={activeThreadPanel}
          threads={reviewThreads}
          profileId={currentProfileId}
          submittingDraftIds={submittingDraftIds}
          commentDeletePending={commentDeletePending}
          threadStatePending={threadStatePending}
          onClose={() => {
            const commentInput = activeCommentInput();
            if (commentInput !== null && commentInput.sourceAnchor !== null) {
              if (submittingDraftIds().has(commentInput.draftId)) return;
              closeActiveCommentInput();
            }
            setActiveThreadPanel(null);
          }}
          replyDraft={replyDraftForThread}
          editDraft={editDraftForComment}
          onReplyChange={updateReplyDraft}
          onReplySubmit={async (thread) => {
            const draft = replyDraftForThread(thread.thread_id);
            return draft !== null && submitReviewDraft(draft.draft_id);
          }}
          onReplyDiscard={(thread) => {
            const draft = replyDraftForThread(thread.thread_id);
            return draft === null || removeDraft(draft.draft_id);
          }}
          onEdit={openEditDraft}
          onEditChange={replaceDraft}
          onEditSubmit={(draft) => submitReviewDraft(draft.draft_id)}
          onEditDiscard={(draft) => removeDraft(draft.draft_id)}
          onDeleteComment={tombstoneComment}
          onState={(thread, action) => {
            void changeThreadState(thread, action);
          }}
        />
        <ReviewHistory
          snapshotId={props.snapshotId}
          profile={props.profile}
          view={props.view}
          historyOpen={props.historyOpen}
          onHistoryOpenChange={props.onHistoryOpenChange}
          inlineHistoryTarget={props.inlineHistoryTarget}
          splitHistoryTop={splitHistoryTop}
          canViewThread={props.canViewThread}
          viewThreadInCode={viewThreadInCode}
          continueDraftInCode={continueDraftInCode}
          closeCommentInputInThread={closeCommentInputInThread}
          discardNewThreadDraft={removeDraft}
          clearDrafts={clearStoredDrafts}
        />
      </UnexpectedErrorBoundary>
    </ReviewContext.Provider>
  );
}
