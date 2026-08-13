/**
 * Implements browser-authored review Threads for one exact Snapshot.
 *
 * The module exports the application-lifetime draft boundary, the
 * Snapshot-bound Review boundary, and narrow FileCard/DiffGrid bindings. The
 * draft boundary is the sole localStorage representation so a completed write
 * can safely outlive one Snapshot view. ReviewProvider observes the canonical
 * bulk query, performs explicit Comment and Thread actions, and renders the
 * code-aligned composer and Snapshot-wide History panel.
 * It does not own Files, rendered rows, hunk selection, scrolling follow,
 * Profile state, or private Thread matching facts.
 */
import {
  For,
  Show,
  batch,
  createContext,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  untrack,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";
import {
  createInfiniteQuery,
  createMutation,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/solid-query";
import { Portal } from "solid-js/web";
import { RefreshCw } from "lucide-solid";
import { z } from "zod";
import {
  ReviewIdSchema,
  ReviewRequestError,
  ReviewTargetSchema,
  api,
  type ReviewComment,
  type ReviewFilePair,
  type ReviewId,
  type ReviewTarget,
  type ReviewTextRegion,
  type ReviewThread,
  type ReviewThreadPage,
  type ReviewThreadUpdate,
  type ThreadCodeLocation,
} from "../api/api";
import {
  ErrorPanel,
  RetryButton,
  UnexpectedErrorBoundary,
  useToasts,
} from "../comp/Toasts";
import { assert, expect } from "../utils";
import type { DiffViewMode } from "./App";
import type { StoredProfile } from "./Profile";

const REVIEW_DRAFT_STORAGE_KEY = "dirdiff:v1:review-drafts";

const NewThreadDraftSchema = z.strictObject({
  kind: z.literal("new-thread"),
  draft_id: ReviewIdSchema,
  snapshot_id: ReviewIdSchema,
  target: ReviewTargetSchema,
  profile_id: z.number().int().positive(),
  body: z.string(),
  updated_at: z.string().datetime({ offset: true }),
});
const ReplyDraftSchema = z.strictObject({
  kind: z.literal("reply"),
  draft_id: ReviewIdSchema,
  thread_id: ReviewIdSchema,
  snapshot_id: ReviewIdSchema,
  profile_id: z.number().int().positive(),
  body: z.string(),
  updated_at: z.string().datetime({ offset: true }),
});
const EditDraftSchema = z.strictObject({
  kind: z.literal("edit"),
  draft_id: ReviewIdSchema,
  thread_id: ReviewIdSchema,
  comment_id: ReviewIdSchema,
  snapshot_id: ReviewIdSchema,
  profile_id: z.number().int().positive(),
  body: z.string(),
  updated_at: z.string().datetime({ offset: true }),
});
const ReviewDraftSchema = z.discriminatedUnion("kind", [
  NewThreadDraftSchema,
  ReplyDraftSchema,
  EditDraftSchema,
]);
const StoredReviewDraftsSchema = z
  .object({
    drafts: z.array(ReviewDraftSchema),
  })
  .superRefine((document, context) => {
    const identities = new Set(document.drafts.map((draft) => draft.draft_id));
    if (identities.size !== document.drafts.length) {
      context.addIssue({
        code: "custom",
        message: "Stored review draft identities must be unique.",
      });
    }
  });
/** Retains one unfinished browser-authored review operation. */
type ReviewDraft = z.infer<typeof ReviewDraftSchema>;

/** Exposes application-lifetime drafts and their active submissions. */
type ReviewDraftContextValue = {
  drafts: Accessor<readonly ReviewDraft[]>;
  error: Accessor<Error | null>;
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  add(draft: ReviewDraft): boolean;
  replace(draft: ReviewDraft): boolean;
  remove(draftId: ReviewId): boolean;
  clear(): boolean;
  beginSubmission(draftId: ReviewId): ReviewDraft | null;
  endSubmission(draftId: ReviewId, succeeded: boolean): void;
};

const ReviewDraftContext = createContext<ReviewDraftContextValue>();

/** Returns one random lowercase 32-hex review identifier. */
export function newReviewId(): ReviewId {
  return ReviewIdSchema.parse(crypto.randomUUID().replaceAll("-", ""));
}

/**
 * Owns unfinished review drafts for application lifetime.
 *
 * Stored input is validated once when it enters the application. Every later
 * operation writes the complete typed document before publishing its Solid
 * state. A storage failure preserves the previous authoritative value and
 * permanently disables later draft writes until the application is reloaded.
 * A submitted draft is disabled until its single HTTP action settles. Success
 * removes it; failure leaves the ordinary editable draft in localStorage.
 */
export function ReviewDraftRoot(props: { children: JSX.Element }): JSX.Element {
  const toast = useToasts();
  const [drafts, setDrafts] = createSignal<readonly ReviewDraft[]>([]);
  const [error, setError] = createSignal<Error | null>(null);
  const [submittingDraftIds, setSubmittingDraftIds] = createSignal<
    ReadonlySet<ReviewId>
  >(new Set());

  onMount(() => {
    try {
      const raw = localStorage.getItem(REVIEW_DRAFT_STORAGE_KEY);
      if (raw !== null) {
        setDrafts(StoredReviewDraftsSchema.parse(JSON.parse(raw)).drafts);
      }
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught
          : new Error("Stored review drafts failed without an Error value."),
      );
    }
  });

  /** Persists one typed replacement before publishing it to consumers. */
  function store(next: readonly ReviewDraft[]): boolean {
    const existingFailure = error();
    if (existingFailure !== null) {
      toast.showError("Review drafts unavailable", existingFailure);
      return false;
    }
    try {
      const document = { drafts: next };
      localStorage.setItem(REVIEW_DRAFT_STORAGE_KEY, JSON.stringify(document));
      setDrafts(next);
      return true;
    } catch (caught) {
      const failure =
        caught instanceof Error
          ? caught
          : new Error("Review draft write failed without an Error value.");
      setError(failure);
      toast.showError("Review drafts unavailable", failure);
      return false;
    }
  }

  return (
    <ReviewDraftContext.Provider
      value={{
        drafts,
        error,
        submittingDraftIds,
        add(draft) {
          assert(
            drafts().every(
              (candidate) => candidate.draft_id !== draft.draft_id,
            ),
            "Review draft creation reused an identity.",
          );
          return store([...drafts(), draft]);
        },
        replace(replacement) {
          const matches = drafts().flatMap((draft, index) =>
            draft.draft_id === replacement.draft_id ? [index] : [],
          );
          assert(
            matches.length === 1,
            "Review draft replacement requires one exact match.",
          );
          return store(
            drafts().map((draft, index) =>
              index === matches[0] ? replacement : draft,
            ),
          );
        },
        remove(draftId) {
          const matches = drafts().filter(
            (draft) => draft.draft_id === draftId,
          );
          assert(
            matches.length === 1,
            "Review draft removal requires one exact match.",
          );
          return store(drafts().filter((draft) => draft.draft_id !== draftId));
        },
        clear() {
          try {
            localStorage.removeItem(REVIEW_DRAFT_STORAGE_KEY);
          } catch (caught) {
            const failure =
              caught instanceof Error
                ? caught
                : new Error(
                    "Review draft clearing failed without an Error value.",
                  );
            setError(failure);
            toast.showError("Review drafts unavailable", failure);
            return false;
          }
          batch(() => {
            setDrafts([]);
            setError(null);
          });
          return true;
        },
        beginSubmission(draftId) {
          const matches = drafts().filter(
            (draft) => draft.draft_id === draftId,
          );
          assert(
            matches.length === 1,
            "Review submission requires one persisted draft.",
          );
          assert(
            !submittingDraftIds().has(draftId),
            "Review draft submission is already in flight.",
          );
          setSubmittingDraftIds((current) => new Set(current).add(draftId));
          return matches[0] ?? null;
        },
        endSubmission(draftId, succeeded) {
          assert(
            submittingDraftIds().has(draftId),
            "Review draft submission completion has no matching start.",
          );
          setSubmittingDraftIds((current) => {
            const next = new Set(current);
            next.delete(draftId);
            return next;
          });
          if (succeeded) {
            const matches = drafts().filter(
              (draft) => draft.draft_id === draftId,
            );
            assert(
              matches.length === 1,
              "Confirmed review submission lost its persisted draft.",
            );
            store(drafts().filter((draft) => draft.draft_id !== draftId));
          }
        },
      }}
    >
      {props.children}
    </ReviewDraftContext.Provider>
  );
}

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

/** Describes one connected row anchor used only while a composer is visible. */
type ComposerAnchor = {
  codeCell: HTMLElement;
  trigger: HTMLButtonElement;
};

/** Describes the one active Snapshot-bound composer. */
type ActiveComposer = {
  draftId: ReviewId;
  anchor: ComposerAnchor | null;
};

/** Describes code-aligned persisted Threads opened from one marker. */
type ActiveThreadPanel = {
  threadIds: readonly ReviewId[];
  anchor: ComposerAnchor;
};

/** Reports marker facts for one rendered line without exposing review storage. */
export type ReviewMarkerState = {
  disabled: boolean;
  hasThread: boolean;
  hasDraft: boolean;
  muted: boolean;
  warning: boolean;
};

/** Exposes narrow review interactions to code renderers and FileCard. */
export type ReviewBinding = {
  snapshotId: ReviewId;
  threads: Accessor<readonly ReviewThread[]>;
  markerRevision: Accessor<ReviewMarkerRevision>;
  markerState(
    binding: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
  ): ReviewMarkerState;
  activateTextComposer(
    binding: ReviewTextGridBinding,
    side: "left" | "right",
    line: number,
    anchor: ComposerAnchor,
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
 * child File lane, and the sole permitted File-view operation. The provider may
 * observe or write review data but must not store Profile, History, navigation,
 * or File-lane state itself.
 */
type ReviewProviderProps = {
  snapshotId: ReviewId;
  view: DiffViewMode;
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
  profile: StoredProfile | null;
  children: JSX.Element;
  canViewThread(location: ThreadCodeLocation): boolean;
  viewThread(location: ThreadCodeLocation): void;
};

/**
 * Owns one exact Snapshot's review observation and composers and renders History.
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

  const draftContext = expect(
    useContext(ReviewDraftContext),
    "ReviewDraftRoot is unavailable.",
  );
  const queryClient = useQueryClient();
  const toast = useToasts();
  const review = createInfiniteQuery(() =>
    api.review.snapshot(props.snapshotId),
  );
  const createThread = createMutation(() => api.review.thread.create());
  const addComment = createMutation(() => api.review.comment.add());
  const editComment = createMutation(() => api.review.comment.edit());
  const deleteComment = createMutation(() => api.review.comment.delete());
  const resolveThread = createMutation(() =>
    api.review.thread.changeState("resolve"),
  );
  const reopenThread = createMutation(() =>
    api.review.thread.changeState("reopen"),
  );
  const deleteThread = createMutation(() =>
    api.review.thread.changeState("delete"),
  );
  const drafts = draftContext.drafts;
  const draftError = draftContext.error;
  const submittingDraftIds = draftContext.submittingDraftIds;
  const [activeComposer, setActiveComposer] =
    createSignal<ActiveComposer | null>(null);
  const [activeThreadPanel, setActiveThreadPanel] =
    createSignal<ActiveThreadPanel | null>(null);
  const [splitHistoryTop, setSplitHistoryTop] = createSignal<number | null>(
    null,
  );
  const [expandedThreads, setExpandedThreads] = createSignal<
    ReadonlyMap<ReviewId, boolean>
  >(new Map());
  const fileHeaders = new Set<HTMLElement>();
  let firstFileHeader: HTMLElement | null = null;
  let fileHeaderObserver: ResizeObserver | null = null;
  let splitHistoryFrame: number | null = null;
  let splitHistoryGeometryFailed = false;
  const currentProfileId = (): number | null => props.profile?.id ?? null;
  const reviewThreads = createMemo(() => {
    const threads: ReviewThread[] = [];
    const seen = new Set<ReviewId>();
    for (const page of review.data?.pages ?? []) {
      for (const thread of page.threads) {
        if (!seen.has(thread.thread_id)) {
          seen.add(thread.thread_id);
          threads.push(thread);
        }
      }
    }
    return threads;
  });
  const totalThreads = (): number => review.data?.pages[0]?.total_threads ?? 0;
  const reviewAvailable = createMemo(
    () => review.data !== undefined && !review.isRefetching && !review.isError,
  );
  /** Close a confirmed composer before its persisted draft is removed. */
  function endSubmission(draftId: ReviewId, succeeded: boolean): void {
    batch(() => {
      if (succeeded && activeComposer()?.draftId === draftId) {
        setActiveComposer(null);
      }
      draftContext.endSubmission(draftId, succeeded);
    });
  }
  /** Report whether the exact Comment deletion is crossing the wire. */
  function commentDeletePending(commentId: ReviewId): boolean {
    return (
      deleteComment.isPending &&
      deleteComment.variables?.commentId === commentId
    );
  }
  /** Report whether the exact Thread lifecycle action is crossing the wire. */
  function threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean {
    const mutation =
      action === "resolve"
        ? resolveThread
        : action === "reopen"
          ? reopenThread
          : deleteThread;
    return mutation.isPending && mutation.variables?.threadId === threadId;
  }
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
  const markerRevision = createMemo<ReviewMarkerRevision>(() => [
    reviewThreads(),
    draftMarkers(),
    reviewAvailable(),
  ]);
  let cachedMarkerRevision: ReviewMarkerRevision | null = null;
  let cachedMarkerIndex: ReviewMarkerIndex | null = null;

  /** Returns the selected Profile or verbally directs the user to its control. */
  function profileForWrite(): StoredProfile | null {
    const profile = props.profile;
    if (profile === null) {
      toast.showTransient(
        "Profile required",
        "Select or create a Profile from the Profile control to write review Comments.",
        5000,
      );
    }
    return profile;
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
    const index: ReviewMarkerIndex = {
      persistedAvailable: revision[2],
      lineThreads,
      lineDraftIds,
    };
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
    if (
      event?.type === "scroll" &&
      event.target instanceof Element &&
      event.target.closest(".review-history-host") !== null
    ) {
      return;
    }
    if (splitHistoryFrame !== null) {
      return;
    }
    splitHistoryFrame = requestAnimationFrame(() => {
      splitHistoryFrame = null;
      updateSplitHistoryGeometry();
    });
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
    window.addEventListener("scroll", scheduleSplitHistoryGeometry, true);
    window.addEventListener("resize", scheduleSplitHistoryGeometry);
    scheduleSplitHistoryGeometry();
    onCleanup(() => {
      if (splitHistoryFrame !== null) {
        cancelAnimationFrame(splitHistoryFrame);
        splitHistoryFrame = null;
      }
      fileHeaderObserver?.disconnect();
      fileHeaderObserver = null;
      window.removeEventListener("scroll", scheduleSplitHistoryGeometry, true);
      window.removeEventListener("resize", scheduleSplitHistoryGeometry);
    });
  });

  /** Replaces one draft only when its complete persisted document is writable. */
  function replaceDraft(replacement: ReviewDraft): boolean {
    return draftContext.replace(replacement);
  }

  /** Removes one draft only after its persisted document confirms the removal. */
  function removeDraft(draftId: ReviewId): boolean {
    let removed = false;
    batch(() => {
      removed = draftContext.remove(draftId);
      if (removed && activeComposer()?.draftId === draftId) {
        setActiveComposer(null);
      }
    });
    return removed;
  }

  /** Publish one newly created Thread into the canonical Snapshot query. */
  function acceptCreatedThread(thread: ReviewThread): void {
    const key = api.review.snapshot(thread.snapshot_id).queryKey;
    const current =
      queryClient.getQueryData<InfiniteData<ReviewThreadPage, number>>(key);
    if (current === undefined) return;
    const matches = current.pages.flatMap((page) =>
      page.threads.filter(
        (candidate) => candidate.thread_id === thread.thread_id,
      ),
    );
    assert(matches.length === 0, "Created review Thread already exists.");
    const total = (current.pages[0]?.total_threads ?? 0) + 1;
    queryClient.setQueryData<InfiniteData<ReviewThreadPage, number>>(key, {
      ...current,
      pages: current.pages.map((page, index) => ({
        ...page,
        total_threads: total,
        has_more: page.page * page.limit < total,
        threads: index === 0 ? [...page.threads, thread] : page.threads,
      })),
    });
  }

  /** Merge one bounded action result into its already loaded Thread. */
  function acceptThreadUpdate(update: ReviewThreadUpdate): void {
    const key = api.review.snapshot(update.snapshot_id).queryKey;
    const current =
      queryClient.getQueryData<InfiniteData<ReviewThreadPage, number>>(key);
    if (current === undefined) return;
    const matches = current.pages.flatMap((page) =>
      page.threads.filter(
        (candidate) => candidate.thread_id === update.thread_id,
      ),
    );
    assert(matches.length === 1, "Updated review Thread is not loaded once.");
    queryClient.setQueryData<InfiniteData<ReviewThreadPage, number>>(key, {
      ...current,
      pages: current.pages.map((page) => ({
        ...page,
        threads: page.threads.map((thread) => {
          if (thread.thread_id !== update.thread_id) return thread;
          let comments = thread.comments;
          if (update.comment !== null) {
            if (update.comment.sequence === comments.length) {
              comments = [...comments, update.comment];
            } else {
              const commentMatches = comments.filter(
                (comment) => comment.comment_id === update.comment?.comment_id,
              );
              assert(
                commentMatches.length === 1,
                "Updated review Comment is not loaded once.",
              );
              comments = comments.map((comment) =>
                comment.comment_id === update.comment?.comment_id
                  ? update.comment
                  : comment,
              );
            }
          }
          return {
            ...thread,
            state: update.state,
            state_revision: update.state_revision,
            comments,
          };
        }),
      })),
    });
  }

  /** Refreshes stale Thread state before a rejected command gate reopens. */
  async function refreshReviewAfterConflict(
    error: ReviewRequestError,
  ): Promise<void> {
    if (
      error.code === "revision_conflict" ||
      error.code === "state_conflict" ||
      error.code === "thread_not_found" ||
      error.code === "comment_not_found"
    ) {
      await review.refetch();
    }
  }

  /** Opens a new persisted Thread draft at one exact target. */
  function openNewDraft(
    profile: StoredProfile,
    target: ReviewTarget,
    anchor: ComposerAnchor | null,
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
      updated_at: new Date().toISOString(),
    };
    if (draftContext.add(draft)) {
      setActiveComposer({ draftId: draft.draft_id, anchor });
    }
  }

  const binding: ReviewBinding = {
    snapshotId: props.snapshotId,
    threads: reviewThreads,
    markerRevision,
    markerState(grid, side, line) {
      const index = markerIndex();
      const key = lineMarkerKey(grid, side, line);
      const lineDraftIds = index.lineDraftIds.get(key) ?? [];
      if (!index.persistedAvailable) {
        return {
          disabled: true,
          hasThread: false,
          hasDraft: lineDraftIds.length > 0,
          muted: false,
          warning: false,
        };
      }
      const lineThreads = index.lineThreads.get(key) ?? [];
      return {
        disabled: false,
        hasThread: lineThreads.length > 0,
        hasDraft: lineDraftIds.length > 0,
        muted:
          lineDraftIds.length === 0 &&
          lineThreads.length > 0 &&
          lineThreads.every((thread) => thread.state !== "open"),
        warning: lineThreads.some((thread) => thread.outdated_reason !== null),
      };
    },
    activateTextComposer(grid, side, line, anchor, extend) {
      assert(
        reviewAvailable(),
        "Text review activation requires persisted Threads to be available.",
      );
      assert(
        grid.snapshot_id === props.snapshotId,
        "Text composer targeted another Snapshot.",
      );
      const active = activeComposer();
      if (active !== null && submittingDraftIds().has(active.draftId)) {
        return;
      }
      if (active?.anchor?.trigger === anchor.trigger) {
        const draft = drafts().find(
          (candidate) => candidate.draft_id === active.draftId,
        );
        if (draft?.body === "") {
          removeDraft(active.draftId);
        } else {
          setActiveComposer(null);
        }
        return;
      }
      if (activeThreadPanel()?.anchor.trigger === anchor.trigger) {
        setActiveThreadPanel(null);
        return;
      }
      if (extend && active !== null) {
        const draft = drafts().find(
          (candidate) => candidate.draft_id === active.draftId,
        );
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
          replaceDraft({
            ...draft,
            target: {
              ...draft.target,
              range: {
                start_line: Math.min(draft.target.range.start_line, line),
                end_line: Math.max(draft.target.range.end_line, line),
              },
            },
            updated_at: new Date().toISOString(),
          });
          return;
        }
      }
      const index = markerIndex();
      assert(
        index.persistedAvailable,
        "Review Threads must be available before deriving line markers.",
      );
      const existing =
        index.lineThreads.get(lineMarkerKey(grid, side, line)) ?? [];
      if (existing.length > 0) {
        setActiveComposer(null);
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
        const existingDraft = expect(
          drafts().find((draft) => draft.draft_id === existingDraftId),
          "Indexed review draft lost its authoritative value.",
        );
        if (active?.draftId === existingDraft.draft_id) {
          if (existingDraft.body === "") {
            removeDraft(existingDraft.draft_id);
          } else {
            setActiveComposer(null);
          }
          return;
        }
        setActiveThreadPanel(null);
        setActiveComposer({ draftId: existingDraft.draft_id, anchor });
        return;
      }
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
      } else {
        assert(
          fileHeaders.delete(header),
          "Review File header was unmounted without a matching mount.",
        );
        fileHeaderObserver?.unobserve(header);
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
          const composer = activeComposer();
          if (
            composer?.anchor !== null &&
            composer?.anchor !== undefined &&
            (!composer.anchor.trigger.isConnected ||
              container.contains(composer.anchor.trigger))
          ) {
            setActiveComposer(null);
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

  /** Apply one explicit Thread state action. */
  async function changeThreadState(
    thread: ReviewThread,
    action: "resolve" | "reopen" | "delete",
  ): Promise<void> {
    if (!reviewAvailable()) {
      toast.showError(
        "Review Threads unavailable",
        new Error("Review Threads are loading or failed to refresh."),
      );
      return;
    }
    const profile = profileForWrite();
    if (profile === null) return;
    const mutation =
      action === "resolve"
        ? resolveThread
        : action === "reopen"
          ? reopenThread
          : deleteThread;
    try {
      const updated = await mutation.mutateAsync({
        snapshotId: props.snapshotId,
        threadId: thread.thread_id,
        body: {
          profile_id: profile.id,
        },
      });
      acceptThreadUpdate(updated);
    } catch (error) {
      if (error instanceof ReviewRequestError) {
        await refreshReviewAfterConflict(error);
      }
      // QueryProvider presented the mutation failure through its metadata.
    }
  }

  /** Opens one persisted reply draft for the selected Thread. */
  function openReplyDraft(thread: ReviewThread): void {
    if (!reviewAvailable()) {
      toast.showError(
        "Review Threads unavailable",
        new Error("Review Threads are loading or failed to refresh."),
      );
      return;
    }
    const profile = profileForWrite();
    if (profile === null) return;
    const draft: ReviewDraft = {
      kind: "reply",
      draft_id: newReviewId(),
      thread_id: thread.thread_id,
      snapshot_id: props.snapshotId,
      profile_id: profile.id,
      body: "",
      updated_at: new Date().toISOString(),
    };
    if (draftContext.add(draft)) {
      setActiveThreadPanel(null);
      setActiveComposer({ draftId: draft.draft_id, anchor: null });
    }
  }

  /** Opens one persisted replacement draft for an authored Comment. */
  function openEditDraft(thread: ReviewThread, comment: ReviewComment): void {
    if (!reviewAvailable()) {
      toast.showError(
        "Review Threads unavailable",
        new Error("Review Threads are loading or failed to refresh."),
      );
      return;
    }
    const profile = profileForWrite();
    if (profile === null) return;
    if (comment.author.profile_id !== profile.id || comment.body === null) {
      throw new Error("Only the original Profile may edit a current Comment.");
    }
    const draft: ReviewDraft = {
      kind: "edit",
      draft_id: newReviewId(),
      thread_id: thread.thread_id,
      comment_id: comment.comment_id,
      snapshot_id: props.snapshotId,
      profile_id: profile.id,
      body: comment.body,
      updated_at: new Date().toISOString(),
    };
    if (draftContext.add(draft)) {
      setActiveThreadPanel(null);
      setActiveComposer({ draftId: draft.draft_id, anchor: null });
    }
  }

  /** Tombstone one current Comment as the selected acting Profile. */
  function tombstoneComment(
    thread: ReviewThread,
    comment: ReviewComment,
  ): void {
    if (!reviewAvailable()) {
      toast.showError(
        "Review Threads unavailable",
        new Error("Review Threads are loading or failed to refresh."),
      );
      return;
    }
    const profile = profileForWrite();
    if (profile === null) return;
    void (async () => {
      try {
        const updated = await deleteComment.mutateAsync({
          snapshotId: props.snapshotId,
          threadId: thread.thread_id,
          commentId: comment.comment_id,
          body: {
            profile_id: profile.id,
          },
        });
        acceptThreadUpdate(updated);
      } catch (error) {
        if (error instanceof ReviewRequestError) {
          await refreshReviewAfterConflict(error);
        }
        // QueryProvider already presented this mutation through its metadata.
      }
    })();
  }

  const orderedThreads = createMemo(() => {
    const rank = { open: 0, resolved: 1, deleted: 2 } as const;
    return [...reviewThreads()].sort(
      (left, right) =>
        rank[left.state] - rank[right.state] ||
        left.created_at.localeCompare(right.created_at) ||
        left.thread_id.localeCompare(right.thread_id),
    );
  });

  return (
    <ReviewContext.Provider value={binding}>
      {props.children}
      <UnexpectedErrorBoundary
        title="Could not render review Threads"
        retryOnR={false}
      >
        <ReviewComposer
          snapshotId={props.snapshotId}
          drafts={drafts}
          active={activeComposer}
          profile={() => props.profile}
          draftError={draftError}
          reviewAvailable={reviewAvailable}
          submittingDraftIds={submittingDraftIds}
          onSubmissionStart={draftContext.beginSubmission}
          onSubmissionEnd={endSubmission}
          onStructuredRejection={refreshReviewAfterConflict}
          onDraftChange={replaceDraft}
          onDiscard={removeDraft}
          onClose={() => {
            const active = activeComposer();
            const draft = drafts().find(
              (candidate) => candidate.draft_id === active?.draftId,
            );
            if (draft?.body === "") {
              removeDraft(draft.draft_id);
            } else {
              setActiveComposer(null);
            }
          }}
          onThread={async (result, _submittedDraft, creation) => {
            assert(result.snapshot_id === props.snapshotId);
            if (creation) {
              assert("origin_target" in result);
              acceptCreatedThread(result);
            } else {
              assert(!("origin_target" in result));
              acceptThreadUpdate(result);
            }
          }}
          createThread={createThread.mutateAsync}
          addComment={addComment.mutateAsync}
          editComment={editComment.mutateAsync}
        />
        <InlineThreadPanel
          active={activeThreadPanel}
          threads={reviewThreads}
          profileId={currentProfileId}
          commentDeletePending={commentDeletePending}
          threadStatePending={threadStatePending}
          canView={props.canViewThread}
          onView={props.viewThread}
          onClose={() => setActiveThreadPanel(null)}
          onReply={openReplyDraft}
          onEdit={openEditDraft}
          onDeleteComment={tombstoneComment}
          onState={(thread, action) => {
            void changeThreadState(thread, action);
          }}
        />
        <aside
          class="review-history-host"
          classList={{
            "review-history-inline": props.view === "inline",
            "review-history-open": props.historyOpen,
          }}
          hidden={props.view === "split" && splitHistoryTop() === null}
          style={
            props.view === "split" && splitHistoryTop() !== null
              ? { "--review-history-top": `${splitHistoryTop()}px` }
              : undefined
          }
        >
          <Show
            when={props.historyOpen}
            fallback={
              <button
                class="review-history-toggle"
                type="button"
                onClick={() => props.onHistoryOpenChange(true)}
              >
                History <span>{totalThreads()}</span>
              </button>
            }
          >
            <section class="review-history-panel" aria-label="Review History">
              <header>
                <strong>History</strong>
                <span>
                  {orderedThreads().length} / {totalThreads()} Threads
                </span>
                <button
                  type="button"
                  class="field-icon-button metadata-refresh-button review-history-refresh"
                  aria-label="Reload Threads"
                  title="Reload Threads"
                  disabled={review.isFetching}
                  onClick={() => void review.refetch({ cancelRefetch: false })}
                >
                  <RefreshCw
                    class="field-icon"
                    classList={{ spinning: review.isFetching }}
                    aria-hidden="true"
                  />
                </button>
                <Show when={props.view === "split"}>
                  <button
                    type="button"
                    onClick={() => props.onHistoryOpenChange(false)}
                    aria-label="Close History"
                  >
                    ×
                  </button>
                </Show>
              </header>
              <Show when={review.isPending}>
                <p class="review-status">Loading Threads…</p>
              </Show>
              <Show when={review.error} keyed>
                {(error) => (
                  <ErrorPanel
                    title="Failed to load review Threads"
                    error={error}
                  >
                    <RetryButton
                      onRetry={() => review.refetch().then(() => undefined)}
                    />
                  </ErrorPanel>
                )}
              </Show>
              <Show when={draftError()} keyed>
                {(error) => (
                  <ErrorPanel title="Review drafts unavailable" error={error}>
                    <button
                      type="button"
                      disabled={submittingDraftIds().size > 0}
                      onClick={() => {
                        if (draftContext.clear()) {
                          setActiveComposer(null);
                        }
                      }}
                    >
                      Clear stored drafts
                    </button>
                  </ErrorPanel>
                )}
              </Show>
              <div class="review-history-scroll">
                <Show when={drafts().length > 0}>
                  <section class="review-drafts" aria-label="Review drafts">
                    <h3>Drafts</h3>
                    <For each={drafts()}>
                      {(draft) => (
                        <article>
                          <strong>
                            {draft.kind === "new-thread"
                              ? "New Thread"
                              : draft.kind === "reply"
                                ? "Reply"
                                : "Edit Comment"}
                          </strong>
                          <p>
                            {draft.body.length === 0
                              ? "Empty draft"
                              : draft.body}
                          </p>
                          <div class="review-actions">
                            <Show
                              when={
                                draft.snapshot_id === props.snapshotId &&
                                draft.profile_id === currentProfileId()
                              }
                            >
                              <button
                                type="button"
                                disabled={
                                  draftError() !== null ||
                                  submittingDraftIds().has(draft.draft_id)
                                }
                                onClick={() => {
                                  setActiveThreadPanel(null);
                                  setActiveComposer({
                                    draftId: draft.draft_id,
                                    anchor: null,
                                  });
                                }}
                              >
                                Open
                              </button>
                            </Show>
                            <button
                              type="button"
                              onClick={() => {
                                void navigator.clipboard
                                  .writeText(draft.body)
                                  .catch((error: unknown) =>
                                    toast.showError(
                                      "Could not copy draft",
                                      error,
                                    ),
                                  );
                              }}
                            >
                              Copy Text
                            </button>
                            <button
                              type="button"
                              disabled={
                                draftError() !== null ||
                                submittingDraftIds().has(draft.draft_id)
                              }
                              onClick={() => removeDraft(draft.draft_id)}
                            >
                              Discard
                            </button>
                          </div>
                        </article>
                      )}
                    </For>
                  </section>
                </Show>
                <For each={orderedThreads()}>
                  {(thread) => {
                    const expanded = () =>
                      expandedThreads().get(thread.thread_id) ??
                      thread.state === "open";
                    const viewable = () =>
                      thread.code_location !== null &&
                      props.canViewThread(thread.code_location);
                    return (
                      <ThreadCard
                        thread={thread}
                        expanded={expanded()}
                        viewable={viewable()}
                        onToggle={() => {
                          const next = new Map(expandedThreads());
                          next.set(thread.thread_id, !expanded());
                          setExpandedThreads(next);
                        }}
                        onView={() => {
                          if (thread.code_location !== null)
                            props.viewThread(thread.code_location);
                        }}
                        onState={(action) => {
                          void changeThreadState(thread, action);
                        }}
                        profileId={currentProfileId()}
                        commentDeletePending={commentDeletePending}
                        threadStatePending={threadStatePending}
                        onReply={() => openReplyDraft(thread)}
                        onEdit={(comment) => openEditDraft(thread, comment)}
                        onDeleteComment={(comment) =>
                          tombstoneComment(thread, comment)
                        }
                        inline={false}
                      />
                    );
                  }}
                </For>
                <Show when={review.hasNextPage}>
                  <button
                    type="button"
                    class="review-history-more"
                    disabled={review.isFetchingNextPage}
                    onClick={() => {
                      void review.fetchNextPage();
                    }}
                  >
                    {review.isFetchingNextPage
                      ? "Loading…"
                      : "Load more Threads"}
                  </button>
                </Show>
              </div>
            </section>
          </Show>
        </aside>
      </UnexpectedErrorBoundary>
    </ReviewContext.Provider>
  );
}

/**
 * Places one anchored review floater wholly inside the current viewport.
 *
 * The floater uses the anchor width when practical, chooses the side with
 * enough visible room (preferring below on ties), and scrolls its own content
 * when neither side can display it at natural height. The operation performs
 * no document scrolling and must be called again after viewport or ancestor
 * scroll changes.
 */
function placeAnchoredReviewFloater(
  floater: HTMLElement,
  anchor: HTMLElement,
): void {
  const margin = 8;
  const gap = 4;
  const anchorRect = anchor.getBoundingClientRect();
  const availableWidth = Math.max(0, window.innerWidth - margin * 2);
  const width = Math.min(
    availableWidth,
    Math.max(300, Math.min(anchorRect.width, availableWidth)),
  );
  const left = Math.min(
    Math.max(margin, anchorRect.left),
    window.innerWidth - margin - width,
  );
  const belowTop = Math.min(
    Math.max(margin, anchorRect.bottom + gap),
    window.innerHeight - margin,
  );
  const aboveBottom = Math.min(
    Math.max(margin, anchorRect.top - gap),
    window.innerHeight - margin,
  );
  const belowHeight = Math.max(0, window.innerHeight - margin - belowTop);
  const aboveHeight = Math.max(0, aboveBottom - margin);
  const placeBelow = belowHeight >= aboveHeight;
  const availableHeight = placeBelow ? belowHeight : aboveHeight;

  floater.style.left = `${left}px`;
  floater.style.width = `${width}px`;
  floater.style.maxHeight = `${availableHeight}px`;
  floater.style.top = placeBelow
    ? `${belowTop}px`
    : `${Math.max(margin, aboveBottom - floater.getBoundingClientRect().height)}px`;
}

/** Renders the active new-Thread draft at its connected code anchor. */
function ReviewComposer(props: {
  snapshotId: ReviewId;
  drafts: Accessor<readonly ReviewDraft[]>;
  active: Accessor<ActiveComposer | null>;
  profile: Accessor<StoredProfile | null>;
  draftError: Accessor<Error | null>;
  reviewAvailable: Accessor<boolean>;
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  onSubmissionStart(draftId: ReviewId): ReviewDraft | null;
  onSubmissionEnd(draftId: ReviewId, succeeded: boolean): void;
  onStructuredRejection(error: ReviewRequestError): Promise<void>;
  onDraftChange(draft: ReviewDraft): boolean;
  onDiscard(draftId: ReviewId): boolean;
  onClose(): void;
  onThread(
    thread: ReviewThread | ReviewThreadUpdate,
    draft: ReviewDraft,
    creation: boolean,
  ): Promise<void>;
  createThread(input: {
    snapshotId: ReviewId;
    body: {
      profile_id: number;
      target: ReviewTarget;
      body: string;
    };
  }): Promise<ReviewThread>;
  addComment(input: {
    snapshotId: ReviewId;
    threadId: ReviewId;
    body: { profile_id: number; body: string };
  }): Promise<ReviewThreadUpdate>;
  editComment(input: {
    snapshotId: ReviewId;
    threadId: ReviewId;
    commentId: ReviewId;
    body: {
      profile_id: number;
      body: string;
    };
  }): Promise<ReviewThreadUpdate>;
}): JSX.Element {
  const toast = useToasts();
  let floater!: HTMLDivElement;
  let frame: number | null = null;
  const draft = createMemo(() => {
    const active = props.active();
    if (active === null) return null;
    return expect(
      props.drafts().find((candidate) => candidate.draft_id === active.draftId),
      "Active review composer lost its persisted draft.",
    );
  });
  const submitting = createMemo(() => {
    const current = draft();
    return current !== null && props.submittingDraftIds().has(current.draft_id);
  });

  createEffect(() => {
    const visible = props.active();
    if (visible?.anchor === null || visible?.anchor === undefined) {
      return;
    }
    /** Positions the floater against its active connected code anchor. */
    function place(): void {
      const active = props.active();
      if (
        active?.anchor === null ||
        active?.anchor === undefined ||
        !floater?.isConnected
      ) {
        if (active?.anchor === null && floater?.isConnected) {
          floater.style.removeProperty("left");
          floater.style.removeProperty("width");
          floater.style.removeProperty("top");
          floater.style.removeProperty("max-height");
        }
        return;
      }
      if (!active.anchor.codeCell.isConnected) {
        props.onClose();
        return;
      }
      placeAnchoredReviewFloater(floater, active.anchor.codeCell);
    }
    /** Coalesces viewport events for one visible anchored Composer. */
    function schedule(): void {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        frame = null;
        place();
      });
    }
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    schedule();
    onCleanup(() => {
      if (frame !== null) {
        cancelAnimationFrame(frame);
        frame = null;
      }
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
    });
  });

  return (
    <Show when={props.active() !== null && draft() !== null}>
      <Portal mount={document.body}>
        <div
          ref={floater}
          class="comment-floater review-composer"
          classList={{
            "review-composer-centered": props.active()?.anchor === null,
          }}
        >
          <header class="comment-floater-header">
            <div class="comment-floater-heading">
              <strong>
                {draft()?.kind === "new-thread"
                  ? "New Thread"
                  : draft()?.kind === "reply"
                    ? "Reply"
                    : "Edit Comment"}
              </strong>
            </div>
            <button
              type="button"
              class="comment-floater-close"
              onClick={props.onClose}
            >
              ×
            </button>
          </header>
          <Show when={props.draftError()} keyed>
            {(error) => (
              <ErrorPanel title="Review drafts unavailable" error={error}>
                <></>
              </ErrorPanel>
            )}
          </Show>
          <form
            class="comment-floater-form"
            onSubmit={(event) => {
              event.preventDefault();
              const current = draft();
              assert(
                current !== null,
                "Review composer requires its persisted draft.",
              );
              assert(
                current.snapshot_id === props.snapshotId,
                "Review draft cannot be submitted through another Snapshot.",
              );
              if (
                props.draftError() !== null ||
                !props.reviewAvailable() ||
                props.submittingDraftIds().has(current.draft_id)
              ) {
                return;
              }
              const profile = props.profile();
              if (profile === null) {
                toast.showTransient(
                  "Profile required",
                  "Select or create a Profile from the Profile control to write review Comments.",
                  5000,
                );
                return;
              }
              if (current.profile_id !== profile.id) {
                toast.showError(
                  "Draft Profile conflict",
                  new Error(
                    "This draft belongs to another Profile. Copy or discard it explicitly.",
                  ),
                );
                return;
              }
              const command = props.onSubmissionStart(current.draft_id);
              if (command === null) {
                toast.showTransient(
                  "Thread write pending",
                  "Wait for the current Comment submission to finish.",
                  5000,
                );
                return;
              }
              void (async () => {
                let thread: ReviewThread | ReviewThreadUpdate;
                try {
                  thread =
                    command.kind === "new-thread"
                      ? await props.createThread({
                          snapshotId: props.snapshotId,
                          body: {
                            profile_id: profile.id,
                            target: command.target,
                            body: command.body,
                          },
                        })
                      : command.kind === "reply"
                        ? await props.addComment({
                            snapshotId: props.snapshotId,
                            threadId: command.thread_id,
                            body: {
                              profile_id: profile.id,
                              body: command.body,
                            },
                          })
                        : await props.editComment({
                            snapshotId: props.snapshotId,
                            threadId: command.thread_id,
                            commentId: command.comment_id,
                            body: {
                              profile_id: profile.id,
                              body: command.body,
                            },
                          });
                } catch (error) {
                  if (error instanceof ReviewRequestError) {
                    await props.onStructuredRejection(error);
                  }
                  // QueryProvider presented the failure; retain the draft.
                  props.onSubmissionEnd(command.draft_id, false);
                  return;
                }
                try {
                  await props.onThread(
                    thread,
                    command,
                    command.kind === "new-thread",
                  );
                } finally {
                  props.onSubmissionEnd(command.draft_id, true);
                }
              })();
            }}
          >
            <label class="comment-floater-field">
              <span>Comment</span>
              <textarea
                rows="5"
                autofocus
                value={draft()?.body ?? ""}
                disabled={props.draftError() !== null || submitting()}
                onInput={(event) => {
                  const current = draft();
                  assert(
                    current !== null,
                    "Review input requires its active draft.",
                  );
                  if (
                    !props.onDraftChange({
                      ...current,
                      body: event.currentTarget.value,
                      updated_at: new Date().toISOString(),
                    })
                  ) {
                    event.currentTarget.value = current.body;
                  }
                }}
              />
            </label>
            <div class="comment-floater-actions">
              <button
                type="button"
                class="comment-floater-secondary"
                disabled={props.draftError() !== null || submitting()}
                onClick={() => {
                  const current = draft();
                  if (current !== null) props.onDiscard(current.draft_id);
                }}
              >
                Discard
              </button>
              <button
                type="submit"
                class="comment-floater-primary"
                disabled={
                  props.draftError() !== null ||
                  !props.reviewAvailable() ||
                  submitting() ||
                  (draft()?.body.trim().length ?? 0) === 0
                }
              >
                {submitting() ? "Submitting…" : "Comment"}
              </button>
            </div>
          </form>
        </div>
      </Portal>
    </Show>
  );
}

/** Renders persisted Threads opened explicitly from one code marker. */
function InlineThreadPanel(props: {
  active: Accessor<ActiveThreadPanel | null>;
  threads: Accessor<readonly ReviewThread[]>;
  profileId: Accessor<number | null>;
  commentDeletePending(commentId: ReviewId): boolean;
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  canView(location: ThreadCodeLocation): boolean;
  onView(location: ThreadCodeLocation): void;
  onClose(): void;
  onReply(thread: ReviewThread): void;
  onEdit(thread: ReviewThread, comment: ReviewComment): void;
  onDeleteComment(thread: ReviewThread, comment: ReviewComment): void;
  onState(thread: ReviewThread, action: "resolve" | "reopen" | "delete"): void;
}): JSX.Element {
  let panel!: HTMLDivElement;
  let frame: number | null = null;
  const visibleThreads = createMemo(() => {
    const active = props.active();
    if (active === null) {
      return [];
    }
    return active.threadIds.map((threadId) => {
      const matches = props
        .threads()
        .filter((thread) => thread.thread_id === threadId);
      assert(
        matches.length === 1,
        "Inline Thread panel requires one exact Thread.",
      );
      return expect(matches[0], "Inline Thread disappeared.");
    });
  });

  createEffect(() => {
    if (props.active() === null) {
      return;
    }
    /** Positions the Thread panel against its connected marker row. */
    function place(): void {
      const active = props.active();
      if (active === null || !panel?.isConnected) {
        return;
      }
      if (!active.anchor.codeCell.isConnected) {
        props.onClose();
        return;
      }
      placeAnchoredReviewFloater(panel, active.anchor.codeCell);
    }
    /** Coalesces viewport events for one visible anchored Thread panel. */
    function schedule(): void {
      if (frame !== null) {
        cancelAnimationFrame(frame);
      }
      frame = requestAnimationFrame(() => {
        frame = null;
        place();
      });
    }
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    schedule();
    onCleanup(() => {
      if (frame !== null) {
        cancelAnimationFrame(frame);
        frame = null;
      }
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
    });
  });

  return (
    <Show when={props.active() !== null}>
      <Portal mount={document.body}>
        <div ref={panel} class="comment-floater review-inline-threads">
          <header class="comment-floater-header">
            <div class="comment-floater-heading">
              <strong>Threads</strong>
            </div>
            <button
              type="button"
              class="comment-floater-close"
              onClick={props.onClose}
            >
              ×
            </button>
          </header>
          <div class="review-inline-thread-scroll">
            <For each={visibleThreads()}>
              {(thread) => {
                const viewable = () =>
                  thread.code_location !== null &&
                  props.canView(thread.code_location);
                return (
                  <ThreadCard
                    thread={thread}
                    expanded={true}
                    viewable={viewable()}
                    profileId={props.profileId()}
                    commentDeletePending={props.commentDeletePending}
                    threadStatePending={props.threadStatePending}
                    onToggle={() => undefined}
                    onView={() => {
                      if (thread.code_location !== null) {
                        props.onView(thread.code_location);
                      }
                    }}
                    onState={(action) => props.onState(thread, action)}
                    onReply={() => props.onReply(thread)}
                    onEdit={(comment) => props.onEdit(thread, comment)}
                    onDeleteComment={(comment) =>
                      props.onDeleteComment(thread, comment)
                    }
                    inline={true}
                  />
                );
              }}
            </For>
          </div>
        </div>
      </Portal>
    </Show>
  );
}

/** Renders one History Thread in complete or folded form. */
function ThreadCard(props: {
  thread: ReviewThread;
  expanded: boolean;
  viewable: boolean;
  profileId: number | null;
  commentDeletePending(commentId: ReviewId): boolean;
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  onToggle(): void;
  onView(): void;
  onState(action: "resolve" | "reopen" | "delete"): void;
  onReply(): void;
  onEdit(comment: ReviewComment): void;
  onDeleteComment(comment: ReviewComment): void;
  inline: boolean;
}): JSX.Element {
  const origin = props.thread.origin_target;
  const excerptPath = expect(
    origin.side === "left" ? origin.file.left_path : origin.file.right_path,
    "A review origin requires its selected-side File path.",
  );
  const firstComment = () =>
    expect(
      props.thread.comments[0],
      "A review Thread requires its first Comment.",
    );
  /** Reports whether the currently selected Profile authored one Comment. */
  function authoredByCurrentProfile(comment: ReviewComment): boolean {
    const author = comment.author;
    return author.profile_id === props.profileId;
  }
  return (
    <article
      class="review-thread"
      classList={{
        "review-thread-resolved": props.thread.state === "resolved",
        "review-thread-deleted": props.thread.state === "deleted",
        "review-thread-outdated": props.thread.outdated_reason !== null,
      }}
    >
      <header onClick={props.inline ? undefined : props.onToggle}>
        <Show when={!props.inline}>
          <button
            type="button"
            class="review-thread-fold"
            aria-expanded={props.expanded}
          >
            {props.expanded ? "▾" : "▸"}
          </button>
        </Show>
        <strong>{firstComment().author.display_name}</strong>
        <span>{props.thread.state}</span>
        <Show when={props.thread.outdated_reason !== null}>
          <span class="review-warning">outdated</span>
        </Show>
        <Show when={props.thread.code_location !== null}>
          <button
            type="button"
            class="review-view"
            disabled={!props.viewable}
            onClick={(event) => {
              event.stopPropagation();
              props.onView();
            }}
          >
            View
          </button>
        </Show>
      </header>
      <Show when={!props.expanded}>
        <p class="review-thread-summary">
          {firstComment().body ?? "Comment deleted"} ·{" "}
          {props.thread.comments.length}
        </p>
      </Show>
      <Show when={props.expanded}>
        <Show when={props.thread.code_location === null}>
          <p class="review-no-location">No current location</p>
        </Show>
        <div class="review-excerpt-heading">
          <strong title={excerptPath}>{excerptPath}</strong>
          <span>
            {props.thread.original_excerpt.selected_start_line ===
            props.thread.original_excerpt.selected_end_line
              ? `L${props.thread.original_excerpt.selected_start_line}`
              : `L${props.thread.original_excerpt.selected_start_line}–${props.thread.original_excerpt.selected_end_line}`}
          </span>
        </div>
        <pre
          class="review-excerpt"
          data-side={props.thread.original_excerpt.side}
        >
          <For each={props.thread.original_excerpt.lines}>
            {(line, index) => {
              const lineNumber =
                props.thread.original_excerpt.start_line + index();
              const selected =
                props.thread.original_excerpt.selected_start_line <=
                  lineNumber &&
                lineNumber <= props.thread.original_excerpt.selected_end_line;
              return (
                <span
                  class="review-excerpt-line"
                  classList={{ "review-excerpt-selected": selected }}
                >
                  <span class="review-excerpt-line-number">{lineNumber}</span>
                  <span class="review-excerpt-code">{line}</span>
                </span>
              );
            }}
          </For>
        </pre>
        <ol class="review-comments">
          <For each={props.thread.comments}>
            {(comment) => (
              <ReviewCommentView
                comment={comment}
                hasLocation={props.thread.code_location !== null}
                viewable={props.viewable}
                editable={
                  props.thread.state !== "deleted" &&
                  !comment.deleted &&
                  authoredByCurrentProfile(comment)
                }
                deletable={props.thread.state !== "deleted" && !comment.deleted}
                deletePending={props.commentDeletePending(comment.comment_id)}
                onView={props.onView}
                onEdit={() => props.onEdit(comment)}
                onDelete={() => props.onDeleteComment(comment)}
              />
            )}
          </For>
        </ol>
        <Show when={props.thread.state !== "deleted"}>
          <div class="review-actions">
            <button type="button" onClick={props.onReply}>
              Reply
            </button>
            <Show
              when={props.thread.state === "open"}
              fallback={
                <button
                  type="button"
                  disabled={props.threadStatePending(
                    props.thread.thread_id,
                    "reopen",
                  )}
                  onClick={() => props.onState("reopen")}
                >
                  Reopen
                </button>
              }
            >
              <button
                type="button"
                disabled={props.threadStatePending(
                  props.thread.thread_id,
                  "resolve",
                )}
                onClick={() => props.onState("resolve")}
              >
                Resolve
              </button>
            </Show>
            <button
              type="button"
              disabled={props.threadStatePending(
                props.thread.thread_id,
                "delete",
              )}
              onClick={() => props.onState("delete")}
            >
              Delete Thread
            </button>
          </div>
        </Show>
      </Show>
    </article>
  );
}

/** Renders immutable author attribution and one current body or tombstone. */
function ReviewCommentView(props: {
  comment: ReviewComment;
  hasLocation: boolean;
  viewable: boolean;
  editable: boolean;
  deletable: boolean;
  deletePending: boolean;
  onView(): void;
  onEdit(): void;
  onDelete(): void;
}): JSX.Element {
  return (
    <li classList={{ "review-comment-deleted": props.comment.deleted }}>
      <strong>{props.comment.author.display_name}</strong>
      <p>{props.comment.body ?? "Comment deleted"}</p>
      <Show when={props.hasLocation || props.editable || props.deletable}>
        <div class="review-actions">
          <Show when={props.hasLocation}>
            <button
              type="button"
              class="review-view"
              disabled={!props.viewable}
              onClick={props.onView}
            >
              View
            </button>
          </Show>
          <Show when={props.editable}>
            <button type="button" onClick={props.onEdit}>
              Edit
            </button>
          </Show>
          <Show when={props.deletable}>
            <button
              type="button"
              disabled={props.deletePending}
              onClick={props.onDelete}
            >
              Delete
            </button>
          </Show>
        </div>
      </Show>
    </li>
  );
}
