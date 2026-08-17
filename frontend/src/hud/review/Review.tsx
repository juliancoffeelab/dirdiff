/**
 * Implements browser-authored review Threads for one exact Snapshot.
 *
 * The module exports the application-lifetime draft boundary, the
 * Snapshot-bound Review boundary, and narrow FileCard/DiffGrid bindings. The
 * draft boundary is the sole localStorage representation so a completed write
 * can safely outlive one Snapshot view. ReviewProvider observes the canonical
 * bulk query, performs explicit Comment and Thread actions, and renders the
 * code-aligned new-Thread, reply, and edit Comment inputs, and
 * Snapshot-wide History panel.
 * It does not own Files, rendered rows, hunk selection, scrolling follow,
 * Profile state, or private Thread matching facts.
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
  type ReviewComment,
  type ReviewFilePair,
  type ReviewId,
  type ReviewTarget,
  type ReviewTextRegion,
  type ReviewThread,
  type ThreadCodeLocation,
} from "../../api/api";
import { UnexpectedErrorBoundary, useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
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

// The one identity-stable empty Thread list: `?? []` would mint a fresh
// array per read and defeat markerRevision's element-identity equality.
const NO_THREADS: readonly ReviewThread[] = [];

/** Identifies one exact rendered text grid for review operations. */
export type ReviewTextGridBinding = {
  snapshot_id: ReviewId;
  file: ReviewFilePair;
  region: ReviewTextRegion;
};

/** Indexes derived marker inputs without becoming another review authority. */
type ReviewMarkerIndex = {
  persistedAvailable: boolean;
  lineThreads: ReadonlyMap<string, readonly ReviewThread[]>;
  lineDraftIds: ReadonlyMap<string, readonly ReviewId[]>;
  lineStates: ReadonlyMap<string, ReviewMarkerState>;
};

/** Identifies the only draft facts that can alter a rendered marker. */
type ReviewDraftMarker = {
  draftId: ReviewId;
  lineKey: string;
};

/** Identifies the Thread, draft-marker, and availability inputs of one marker index. */
type ReviewMarkerRevision = readonly [
  readonly ReviewThread[],
  readonly ReviewDraftMarker[],
  boolean,
];

/** Identifies one connected rendered row anchor for code-aligned review UI. */
export type ReviewCodeAnchor = {
  codeCell: HTMLElement;
  trigger: HTMLButtonElement;
};

/** Places the one active Snapshot-bound Comment input at code or in its Thread. */
export type ActiveCommentInput = {
  draftId: ReviewId;
  input: NewThreadDraft | null;
  mount: HTMLElement;
  sourceAnchor: ReviewCodeAnchor | null;
};

/** Describes code-aligned persisted Threads opened from one marker. */
export type ActiveThreadPanel = {
  threadIds: readonly ReviewId[];
  anchor: ReviewCodeAnchor;
};

/** Identifies the exact line-local review action selected by one marker. */
export type ReviewMarkerKind =
  | "new"
  | "draft"
  | "open"
  | "resolved"
  | "deleted";

/** Describes one control represented at an exact rendered line. */
export type ReviewMarkerDescriptor =
  | { kind: "new" }
  | { kind: "draft" }
  | {
      kind: "open" | "resolved" | "deleted";
      count: number;
      warning: boolean;
    };

/** Reports the actual controls for one rendered line and their availability. */
export type ReviewMarkerState = {
  disabled: boolean;
  markers: readonly ReviewMarkerDescriptor[];
};

const AVAILABLE_NEW_MARKER_STATE: ReviewMarkerState = {
  disabled: false,
  markers: [{ kind: "new" }],
};
const DISABLED_NEW_MARKER_STATE: ReviewMarkerState = {
  disabled: true,
  markers: [{ kind: "new" }],
};

/** Exposes narrow review interactions to code renderers and FileCard. */
export type ReviewBinding = {
  snapshotId: ReviewId;
  threads: Accessor<readonly ReviewThread[]>;
  markerRevision: Accessor<ReviewMarkerRevision>;
  /**
   * Returns the line keys whose marker state changed in the latest revision.
   *
   * `null` means no bounded change set exists (first index build or a
   * persisted-availability flip) and every rendered host must refresh. The
   * set is valid only for the current `markerRevision` value; grids whose
   * rows changed independently refresh fully regardless.
   */
  changedMarkerKeys(): ReadonlySet<string> | null;
  markerState(
    binding: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
  ): ReviewMarkerState;
  activateTextCommentInput(
    binding: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
    anchor: ReviewCodeAnchor,
    markerKind: ReviewMarkerKind,
    extend: boolean,
  ): void;
  setFileHeaderMounted(header: HTMLElement, mounted: boolean): void;
  closeAnchoredUi(container: Node): void;
};

const ReviewContext = createContext<ReviewBinding>();

/** Returns the required Snapshot-bound review interface. */
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
  snapshotId: ReviewId;
  view: DiffViewMode;
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
  profile: StoredProfile | null;
  children: JSX.Element;
  inlineHistoryTarget: Accessor<HTMLElement | null>;
  canViewThread(location: ThreadCodeLocation): boolean;
  viewThread(location: ThreadCodeLocation): Promise<ReviewCodeAnchor | null>;
};

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
  /** Encodes one exact rendered line location for derived marker lookup only. */
  function lineMarkerKey(
    grid: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
  ): string {
    return JSON.stringify([
      grid.file.left_path,
      grid.file.right_path,
      grid.region.kind,
      grid.region.kind === "notebook-cell-source" ? grid.region.cell_key : null,
      side,
      line,
    ]);
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
                region: draft.target.region,
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

  /** Reports whether two derived marker states render identically. */
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

  /** Builds the marker index once per exact query/draft revision on first use. */
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

    /** Appends one item to a derived marker bucket. */
    function append<T>(map: Map<string, T[]>, key: string, item: T): void {
      const current = map.get(key);
      if (current === undefined) {
        map.set(key, [item]);
      } else {
        current.push(item);
      }
    }

    for (const thread of revision[0]) {
      const location = thread.code_location;
      if (location?.kind === "range") {
        append(
          lineThreads,
          lineMarkerKey(
            {
              snapshot_id: props.snapshotId,
              file: location.file,
              region: location.region,
            },
            location.side,
            location.range.start_line,
          ),
          thread,
        );
      } else if (location?.kind === "file-start") {
        append(
          lineThreads,
          lineMarkerKey(
            {
              snapshot_id: props.snapshotId,
              file: location.file,
              region: { kind: "ordinary" },
            },
            location.side,
            1,
          ),
          thread,
        );
      }
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
          state.warning ||= thread.outdated_reason !== null;
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

  /** Places split History beneath the sticky File header using one hit-tested header. */
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

  /** Coalesces viewport geometry work and ignores History's own scrolling. */
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

  /** Rebuilds the sticky-band observer for the current viewport and offset. */
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
          if (entry.isIntersecting && entry.target.isConnected) {
            stickyBandHeaders.add(entry.target as HTMLElement);
          } else {
            stickyBandHeaders.delete(entry.target as HTMLElement);
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

  /** Returns the active persisted draft or the transient empty input opening it. */
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

  /** Closes the active input and discards only empty or unchanged work. */
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

  /** Opens one prevalidated draft at its final inline mount. */
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

  /** Persists meaningful Comment input and removes it when emptied again. */
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

  /** Removes one draft only after its persisted document confirms the removal. */
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

  /** Discards either transient input or its persisted draft. */
  function discardCommentInputDraft(draftId: ReviewId): boolean {
    const active = activeCommentInput();
    if (active?.draftId === draftId && active.input !== null) {
      setActiveCommentInput(null);
      return true;
    }
    return removeDraft(draftId);
  }

  /** Opens transient new-Thread input at one exact target. */
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

  /** Opens the viewed Thread's discussion panel at its rendered code line. */
  function viewThreadInCode(thread: ReviewThread): void {
    const location = thread.code_location;
    if (location === null) return;
    void (async () => {
      const anchor = await props.viewThread(location);
      if (anchor === null) return;
      const trigger = anchor.trigger.parentElement?.querySelector(
        `[data-review-marker-kind="${thread.state}"]`,
      );
      assert(
        trigger instanceof HTMLButtonElement && !trigger.hidden,
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

  /** Continues one persisted new-Thread draft at its rendered code line. */
  function continueDraftInCode(
    draft: NewThreadDraft,
    location: ThreadCodeLocation,
  ): void {
    void (async () => {
      if (!props.canViewThread(location)) {
        throw new Error("Load the reviewed File before continuing this draft.");
      }
      const anchor = await props.viewThread(location);
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

  /** Closes an input mounted inside one History Thread; false blocks the caller. */
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

  /** Clears the persisted draft document and any anchored input rendering it. */
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
          draft.target.region.kind === grid.region.kind &&
          (draft.target.region.kind === "ordinary" ||
            (grid.region.kind === "notebook-cell-source" &&
              draft.target.region.cell_key === grid.region.cell_key)) &&
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
          region: grid.region,
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
