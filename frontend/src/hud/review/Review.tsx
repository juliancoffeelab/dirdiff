import {
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
import { createInfiniteQuery } from "@tanstack/solid-query";
import {
  api,
  reviewThreadList,
  threadPlacementCodePoint,
  threadOutdated,
  type AcceptedReviewActivity,
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

const NO_THREADS: readonly ReviewThread[] = [];

export type ReviewTextGridBinding = {
  snapshot_id: ReviewId;
  file: ReviewFilePair;
  bay: ReviewTextBay;
};

type ReviewMarkerIndex = {
  persistedAvailable: boolean;
  lineThreads: ReadonlyMap<string, readonly ReviewThread[]>;
  lineDraftIds: ReadonlyMap<string, readonly ReviewId[]>;
  lineStates: ReadonlyMap<string, ReviewMarkerState>;
};

type ReviewDraftMarker = {
  draftId: ReviewId;
  lineKey: string;
};

type ReviewMarkerRevision = readonly [
  readonly ReviewThread[],
  readonly ReviewDraftMarker[],
  boolean,
];

export type ReviewCodeAnchor = {
  codeCell: HTMLElement;
  trigger: HTMLButtonElement;
};

export type ActiveCommentInput = {
  draftId: ReviewId;
  input: NewThreadDraft | null;
  mount: HTMLElement;
  sourceAnchor: ReviewCodeAnchor | null;
};

export type ActiveThreadPanel = {
  threadIds: readonly ReviewId[];
  anchor: ReviewCodeAnchor;
};

export type ReviewMarkerKind =
  | "new"
  | "draft"
  | "open"
  | "resolved"
  | "deleted";

export type ReviewMarkerDescriptor =
  | {
      kind: "new";
    }
  | {
      kind: "draft";
    }
  | {
      kind: "open" | "resolved" | "deleted";
      count: number;
      warning: boolean;
    };

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

export type ReviewBinding = {
  snapshotId: ReviewId;
  threads: Accessor<readonly ReviewThread[]>;
  markerRevision: Accessor<ReviewMarkerRevision>;
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

export function useReview(): ReviewBinding {
  return expect(useContext(ReviewContext), "Review binding is unavailable.");
}

type ReviewProviderProps = {
  snapshotId: ReviewId;
  view: DiffViewMode;
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
  profile: StoredProfile | null;
  children: JSX.Element;
  inlineHistoryTarget: Accessor<HTMLElement | null>;
  canViewThread(point: ThreadCodePoint): boolean;
  viewThread(point: ThreadCodePoint): Promise<ReviewCodeAnchor | null>;
  canViewFile(file: ReviewFilePair): boolean;
  viewFileStart(file: ReviewFilePair): Promise<void>;
  viewOriginThread(point: ThreadCodePoint): Promise<void>;
};

export const LineMarkerKeySchema = z.tuple([
  z.string().nullable(),
  z.string().nullable(),
  z.string().min(1),
  z.enum(["left", "right"]),
  z.number().int().positive(),
]);

export type LineMarkerKey = z.infer<typeof LineMarkerKeySchema>;

export function ReviewProvider(props: ReviewProviderProps): JSX.Element {
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
  const toast = useToasts();
  const review = createInfiniteQuery(() =>
    api.review.snapshot(props.snapshotId),
  );
  // This provider is the sole driver for the shared progressive query. Each
  // successful page is already authoritative and visible to all observers
  // before the next page begins; disposal cancels the observer-owned chain.
  createEffect(
    on(
      () =>
        [
          review.data?.pages.length ?? 0,
          review.hasNextPage,
          review.isFetchingNextPage,
          review.isError,
        ] as const,
      ([loadedPages, hasNextPage, isFetchingNextPage, isError]) => {
        if (loadedPages > 0 && hasNextPage && !isFetchingNextPage && !isError) {
          void review.fetchNextPage();
        }
      },
    ),
  );
  const drafts = draftContext.drafts;
  const draftError = draftContext.error;
  const submittingDraftIds = draftContext.submittingDraftIds;
  const [activeCommentInput, setActiveCommentInput] =
    createSignal<ActiveCommentInput | null>(null);
  const [activeThreadPanel, setActiveThreadPanel] =
    createSignal<ActiveThreadPanel | null>(null);
  // The Snapshot provider retains only successful mutation responses that occur
  // after its query boundary. Disposal drops them; query pages are never copied
  // or changed, and the presentation memo ignores activity already in later pages.
  const [acceptedActivities, setAcceptedActivities] = createSignal<
    readonly AcceptedReviewActivity[]
  >([]);

  function recordAcceptedActivity(activity: AcceptedReviewActivity): void {
    setAcceptedActivities((current) => [...current, activity]);
  }

  const discussion = createThreadDiscussion({
    snapshotId: props.snapshotId,
    profile: () => props.profile,
    acceptedActivities,
    onAcceptedActivity: recordAcceptedActivity,
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
  const reviewThreads = createMemo<readonly ReviewThread[]>(
    () => reviewThreadList(review.data, acceptedActivities()),
    NO_THREADS,
  );
  const reviewAvailable = createMemo(
    () => review.data !== undefined && !review.isError,
  );
  const draftMarkers = createMemo<readonly ReviewDraftMarker[]>(
    () =>
      drafts().flatMap((draft) => {
        // A whole-review draft has no line, so it contributes no marker.
        if (
          draft.kind !== "new-thread" ||
          draft.target.kind !== "text" ||
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
  // Each revision element is itself identity-stable (presented Thread data,
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
      if (thread.location_source.kind === "origin-snapshot") {
        continue;
      }
      const point = threadPlacementCodePoint(thread);
      if (point === null) {
        continue;
      }
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

  function activeCommentInputDraft(): NewThreadDraft | null {
    const active = activeCommentInput();
    if (active === null) {
      return null;
    }
    if (active.input !== null) {
      return active.input;
    }
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

  function closeActiveCommentInput(): void {
    const active = activeCommentInput();
    if (active === null || submittingDraftIds().has(active.draftId)) {
      return;
    }
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
      if (!stored) {
        return true;
      }
      let removed = false;
      batch(() => {
        removed = draftContext.remove(replacement.draft_id);
        if (removed) {
          setActiveCommentInput({ ...active, input: replacement });
        }
      });
      return removed;
    }
    if (stored) {
      return draftContext.replace(replacement);
    }
    if (!draftContext.add(replacement)) {
      return false;
    }
    setActiveCommentInput({ ...active, input: null });
    return true;
  }

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

  function discardCommentInputDraft(draftId: ReviewId): boolean {
    const active = activeCommentInput();
    if (active?.draftId === draftId && active.input !== null) {
      setActiveCommentInput(null);
      return true;
    }
    return removeDraft(draftId);
  }

  function openNewDraft(
    profile: StoredProfile,
    target: ReviewTarget,
    mount: HTMLElement,
    sourceAnchor: ReviewCodeAnchor | null,
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
    openCommentInput(draft, false, mount, sourceAnchor);
  }

  function toggleReviewThreadInput(mount: HTMLElement): void {
    const active = activeCommentInput();
    if (active !== null && submittingDraftIds().has(active.draftId)) {
      return;
    }
    if (active?.mount === mount) {
      closeActiveCommentInput();
      return;
    }
    const profile = profileForWrite();
    if (profile === null) {
      return;
    }
    openNewDraft(profile, { kind: "review" }, mount, null);
  }

  function viewThreadInCode(thread: ReviewThread): void {
    assert(
      thread.location_source.kind === "displayed-snapshot",
      "Anchored Thread navigation requires a displayed placement.",
    );
    const point = threadPlacementCodePoint(thread);
    if (point === null) {
      return;
    }
    void (async () => {
      const anchor = await props.viewThread(point);
      if (anchor === null) {
        return;
      }
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

  function viewOriginThreadInCode(
    file: ReviewFilePair,
    point: ThreadCodePoint | null,
    useFileStart: boolean,
  ): void {
    void (
      useFileStart
        ? props.viewFileStart(file)
        : props.viewOriginThread(
            expect(point, "Exact origin navigation requires its code point."),
          )
    ).catch((error: unknown) =>
      toast.showError("Could not view Thread", error),
    );
  }

  function continueDraftInCode(
    draft: NewThreadDraft,
    point: ThreadCodePoint,
  ): void {
    void (async () => {
      if (!props.canViewThread(point)) {
        throw new Error("Load the reviewed File before continuing this draft.");
      }
      const anchor = await props.viewThread(point);
      if (anchor === null) {
        return;
      }
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

  function closeCommentInputInThread(threadId: ReviewId): boolean {
    const commentInput = activeCommentInput();
    if (
      commentInput?.sourceAnchor === null &&
      commentInput.mount.closest(
        `[data-review-history-thread-id="${threadId}"]`,
      ) !== null
    ) {
      if (submittingDraftIds().has(commentInput.draftId)) {
        return false;
      }
      closeActiveCommentInput();
    }
    return true;
  }

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
      if (profile === null) {
        return;
      }
      const mount = anchor.codeCell.parentElement;
      assert(
        mount !== null &&
          (mount.classList.contains("diff-side") ||
            mount.classList.contains("inline-diff-row")),
        "New-Thread input requires its exact rendered diff row.",
      );
      openNewDraft(
        profile,
        {
          kind: "text",
          file: grid.file,
          bay: grid.bay,
          side,
          range: { start_line: line, end_line: line },
        },
        mount,
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
              if (submittingDraftIds().has(commentInput.draftId)) {
                return;
              }
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
          acceptedActivities={acceptedActivities}
          onAcceptedActivity={recordAcceptedActivity}
          view={props.view}
          historyOpen={props.historyOpen}
          onHistoryOpenChange={props.onHistoryOpenChange}
          inlineHistoryTarget={props.inlineHistoryTarget}
          splitHistoryTop={splitHistoryTop}
          canViewThread={props.canViewThread}
          viewThreadInCode={viewThreadInCode}
          canViewFile={props.canViewFile}
          viewOriginThreadInCode={viewOriginThreadInCode}
          continueDraftInCode={continueDraftInCode}
          toggleReviewThreadInput={toggleReviewThreadInput}
          closeReviewThreadInput={(mount) => {
            // Ordinary close keeps meaningful text as a draft and drops an
            // empty input; an in-flight submission keeps it until it settles.
            if (activeCommentInput()?.mount === mount) {
              closeActiveCommentInput();
            }
          }}
          continueReviewDraft={(draft, mount) =>
            openCommentInput(draft, true, mount, null)
          }
          closeCommentInputInThread={closeCommentInputInThread}
          discardNewThreadDraft={removeDraft}
          clearDrafts={clearStoredDrafts}
        />
      </UnexpectedErrorBoundary>
    </ReviewContext.Provider>
  );
}
