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
  createMutation,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { Portal } from "solid-js/web";
import {
  Eye,
  LocateFixed,
  Pencil,
  RefreshCw,
  Trash2,
  TriangleAlert,
} from "lucide-solid";
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

// The one identity-stable empty Thread list: `?? []` would mint a fresh
// array per read and defeat markerRevision's element-identity equality.
const NO_THREADS: readonly ReviewThread[] = [];

const REVIEW_DRAFT_STORAGE_KEY = "dirdiff:v1:review-drafts";
// One localStorage entry per draft, keyed by its identity, so persisting a
// keystroke serializes only the changed draft instead of every unfinished
// text. The single-document key above remains readable as legacy input and is
// migrated to per-draft entries on load.
const REVIEW_DRAFT_STORAGE_PREFIX = "dirdiff:v2:review-draft:";

const NewThreadDraftSchema = z.strictObject({
  kind: z.literal("new-thread"),
  draft_id: ReviewIdSchema,
  snapshot_id: ReviewIdSchema,
  target: ReviewTargetSchema,
  profile_id: z.number().int().positive(),
  body: z.string(),
});
const ReplyDraftSchema = z.strictObject({
  kind: z.literal("reply"),
  draft_id: ReviewIdSchema,
  thread_id: ReviewIdSchema,
  profile_id: z.number().int().positive(),
  body: z.string(),
});
const EditDraftSchema = z.strictObject({
  kind: z.literal("edit"),
  draft_id: ReviewIdSchema,
  thread_id: ReviewIdSchema,
  comment_id: ReviewIdSchema,
  profile_id: z.number().int().positive(),
  body: z.string(),
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
/** Retains unfinished input for a Thread that does not exist yet. */
type NewThreadDraft = z.infer<typeof NewThreadDraftSchema>;

/** Exposes application-lifetime drafts and their active submissions. */
type ReviewDraftContextValue = {
  drafts: Accessor<readonly ReviewDraft[]>;
  error: Accessor<Error | null>;
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  add(draft: ReviewDraft): boolean;
  replace(draft: ReviewDraft): boolean;
  remove(draftId: ReviewId): boolean;
  clear(): boolean;
  beginSubmission(draftId: ReviewId): ReviewDraft;
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
 * Stored input is validated once when it enters the application. Each draft
 * persists as its own localStorage entry, so every later operation writes or
 * removes exactly the one changed draft before publishing its Solid state;
 * typing cost does not grow with other unfinished text. A storage failure
 * preserves the previous authoritative value and permanently disables later
 * draft writes until the application is reloaded. A submitted draft is
 * disabled until its single HTTP action settles. Success removes it; failure
 * leaves the ordinary editable draft in localStorage.
 */
export function ReviewDraftRoot(props: { children: JSX.Element }): JSX.Element {
  const toast = useToasts();
  const [drafts, setDrafts] = createSignal<readonly ReviewDraft[]>([]);
  const [error, setError] = createSignal<Error | null>(null);
  const [submittingDraftIds, setSubmittingDraftIds] = createSignal<
    ReadonlySet<ReviewId>
  >(new Set());

  /** Names the localStorage entry persisting one exact draft. */
  function draftStorageKey(draftId: ReviewId): string {
    return `${REVIEW_DRAFT_STORAGE_PREFIX}${draftId}`;
  }

  onMount(() => {
    try {
      const loaded: ReviewDraft[] = [];
      // Import the legacy single-document representation once, splitting it
      // into per-draft entries; later sessions read only those.
      const raw = localStorage.getItem(REVIEW_DRAFT_STORAGE_KEY);
      if (raw !== null) {
        for (const draft of StoredReviewDraftsSchema.parse(JSON.parse(raw))
          .drafts) {
          if (draft.body.trim().length > 0) {
            localStorage.setItem(
              draftStorageKey(draft.draft_id),
              JSON.stringify(draft),
            );
          }
        }
        localStorage.removeItem(REVIEW_DRAFT_STORAGE_KEY);
      }
      const storedKeys: string[] = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key !== null && key.startsWith(REVIEW_DRAFT_STORAGE_PREFIX)) {
          storedKeys.push(key);
        }
      }
      for (const key of storedKeys) {
        const value = localStorage.getItem(key);
        assert(value !== null, "Enumerated draft entry disappeared.");
        const draft = ReviewDraftSchema.parse(JSON.parse(value));
        assert(
          key === draftStorageKey(draft.draft_id),
          "Stored review draft entry key must match its identity.",
        );
        // Empty input is not revisable material and must never enter the
        // application as a persisted draft.
        if (draft.body.trim().length > 0) {
          loaded.push(draft);
        } else {
          localStorage.removeItem(key);
        }
      }
      // Entry enumeration order is arbitrary; sort for a deterministic
      // published order (draft consumers key by identity, not position).
      loaded.sort((a, b) => (a.draft_id < b.draft_id ? -1 : 1));
      setDrafts(loaded);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught
          : new Error("Stored review drafts failed without an Error value."),
      );
    }
  });

  /** Reports the storage failure that disables draft writes, if any. */
  function reportUnavailable(): boolean {
    const existingFailure = error();
    if (existingFailure !== null) {
      toast.showError("Review drafts unavailable", existingFailure);
      return true;
    }
    return false;
  }

  /** Converts one storage failure into the permanent disabled state. */
  function storeFailed(caught: unknown): false {
    const failure =
      caught instanceof Error
        ? caught
        : new Error("Review draft write failed without an Error value.");
    setError(failure);
    toast.showError("Review drafts unavailable", failure);
    return false;
  }

  /** Persists one changed draft entry before publishing the next state. */
  function persistDraft(
    draft: ReviewDraft,
    next: readonly ReviewDraft[],
  ): boolean {
    if (reportUnavailable()) {
      return false;
    }
    try {
      localStorage.setItem(
        draftStorageKey(draft.draft_id),
        JSON.stringify(draft),
      );
      setDrafts(next);
      return true;
    } catch (caught) {
      return storeFailed(caught);
    }
  }

  /** Adds one new draft identity to persistent storage. */
  function add(draft: ReviewDraft): boolean {
    assert(
      drafts().every((candidate) => candidate.draft_id !== draft.draft_id),
      "Review draft creation reused an identity.",
    );
    return persistDraft(draft, [...drafts(), draft]);
  }

  /** Replaces the one persisted draft with the same identity. */
  function replace(replacement: ReviewDraft): boolean {
    const matches = drafts().flatMap((draft, index) =>
      draft.draft_id === replacement.draft_id ? [index] : [],
    );
    assert(
      matches.length === 1,
      "Review draft replacement requires one exact match.",
    );
    return persistDraft(
      replacement,
      drafts().map((draft, index) =>
        index === matches[0] ? replacement : draft,
      ),
    );
  }

  /** Removes the one persisted draft with the requested identity. */
  function remove(draftId: ReviewId): boolean {
    const matches = drafts().filter((draft) => draft.draft_id === draftId);
    assert(
      matches.length === 1,
      "Review draft removal requires one exact match.",
    );
    if (reportUnavailable()) {
      return false;
    }
    try {
      localStorage.removeItem(draftStorageKey(draftId));
      setDrafts(drafts().filter((draft) => draft.draft_id !== draftId));
      return true;
    } catch (caught) {
      return storeFailed(caught);
    }
  }

  /** Clears every persisted draft entry after a storage failure. */
  function clear(): boolean {
    try {
      localStorage.removeItem(REVIEW_DRAFT_STORAGE_KEY);
      const storedKeys: string[] = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key !== null && key.startsWith(REVIEW_DRAFT_STORAGE_PREFIX)) {
          storedKeys.push(key);
        }
      }
      for (const key of storedKeys) {
        localStorage.removeItem(key);
      }
    } catch (caught) {
      const failure =
        caught instanceof Error
          ? caught
          : new Error("Review draft clearing failed without an Error value.");
      setError(failure);
      toast.showError("Review drafts unavailable", failure);
      return false;
    }
    batch(() => {
      setDrafts([]);
      setError(null);
    });
    return true;
  }

  /** Marks one persisted draft as the sole input to an in-flight write. */
  function beginSubmission(draftId: ReviewId): ReviewDraft {
    const matches = drafts().filter((draft) => draft.draft_id === draftId);
    assert(
      matches.length === 1,
      "Review submission requires one persisted draft.",
    );
    assert(
      !submittingDraftIds().has(draftId),
      "Review draft submission is already in flight.",
    );
    setSubmittingDraftIds((current) => new Set(current).add(draftId));
    return matches[0];
  }

  /** Ends one draft write and removes its input only after success. */
  function endSubmission(draftId: ReviewId, succeeded: boolean): void {
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
      const matches = drafts().filter((draft) => draft.draft_id === draftId);
      assert(
        matches.length === 1,
        "Confirmed review submission lost its persisted draft.",
      );
      remove(draftId);
    }
  }

  return (
    <ReviewDraftContext.Provider
      value={{
        drafts,
        error,
        submittingDraftIds,
        add,
        replace,
        remove,
        clear,
        beginSubmission,
        endSubmission,
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
type ActiveCommentInput = {
  draftId: ReviewId;
  input: NewThreadDraft | null;
  mount: HTMLElement;
  sourceAnchor: ReviewCodeAnchor | null;
};

/** Describes code-aligned persisted Threads opened from one marker. */
type ActiveThreadPanel = {
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

  const draftContext = expect(
    useContext(ReviewDraftContext),
    "ReviewDraftRoot is unavailable.",
  );
  const queryClient = useQueryClient();
  const toast = useToasts();
  const review = createQuery(() => api.review.snapshot(props.snapshotId));
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
  const [activeCommentInput, setActiveCommentInput] =
    createSignal<ActiveCommentInput | null>(null);
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
  const currentProfileId = (): number | null => props.profile?.id ?? null;
  const reviewThreads = (): readonly ReviewThread[] =>
    review.data ?? NO_THREADS;
  const totalThreads = (): number => review.data?.length ?? 0;
  const reviewAvailable = createMemo(
    () => review.data !== undefined && !review.isRefetching && !review.isError,
  );
  /** Close a confirmed Comment input before its persisted draft is removed. */
  function endSubmission(draftId: ReviewId, succeeded: boolean): void {
    batch(() => {
      if (succeeded && activeCommentInput()?.draftId === draftId) {
        setActiveCommentInput(null);
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
  const profileDrafts = createMemo(() => {
    const profileId = currentProfileId();
    const replies = new Map<ReviewId, ReviewDraft>();
    const edits = new Map<ReviewId, ReviewDraft>();
    if (profileId === null) return { replies, edits };
    for (const draft of drafts()) {
      if (draft.profile_id !== profileId) continue;
      if (draft.kind === "reply") {
        assert(
          !replies.has(draft.thread_id),
          "A Thread has multiple matching reply drafts.",
        );
        replies.set(draft.thread_id, draft);
      } else if (draft.kind === "edit") {
        assert(
          !edits.has(draft.comment_id),
          "A Comment has multiple matching edit drafts.",
        );
        edits.set(draft.comment_id, draft);
      }
    }
    return { replies, edits };
  });
  /** Returns the selected Profile's sole reply draft for one current Thread. */
  function replyDraftForThread(threadId: ReviewId): ReviewDraft | null {
    return profileDrafts().replies.get(threadId) ?? null;
  }

  /** Returns the selected Profile's sole edit draft for one current Comment. */
  function editDraftForComment(
    threadId: ReviewId,
    commentId: ReviewId,
  ): ReviewDraft | null {
    const draft = profileDrafts().edits.get(commentId) ?? null;
    assert(
      draft === null || (draft.kind === "edit" && draft.thread_id === threadId),
      "An edit draft belongs to another Thread.",
    );
    return draft;
  }

  /** Persists meaningful text from one Thread's permanent reply input. */
  function updateReplyDraft(thread: ReviewThread, body: string): boolean {
    const profile = profileForWrite();
    if (profile === null) return false;
    const existing = replyDraftForThread(thread.thread_id);
    if (body.trim().length === 0) {
      return existing === null || removeDraft(existing.draft_id);
    }
    if (existing !== null) {
      return replaceDraft({
        ...existing,
        body,
      });
    }
    return draftContext.add({
      kind: "reply",
      draft_id: newReviewId(),
      thread_id: thread.thread_id,
      profile_id: profile.id,
      body,
    });
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

  /** Replaces one draft only when its complete persisted document is writable. */
  function replaceDraft(replacement: ReviewDraft): boolean {
    return draftContext.replace(replacement);
  }

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

  /** Publish one newly created Thread into the canonical Snapshot query. */
  async function acceptCreatedThread(thread: ReviewThread): Promise<void> {
    const key = api.review.snapshot(thread.snapshot_id).queryKey;
    // A refetch begun before the backend committed could resolve after this
    // publication and erase the new Thread with its stale result; cancel the
    // in-flight read so the committed response publishes last.
    await queryClient.cancelQueries({ queryKey: key });
    queryClient.setQueryData<ReviewThread[]>(key, (current) => {
      if (current === undefined) return current;
      const matches = current.filter(
        (candidate) => candidate.thread_id === thread.thread_id,
      );
      if (matches.length === 0) return [...current, thread];
      // A concurrent Reload already fetched the committed Thread: ordinary
      // concurrency, not an error. Keep the equal-or-newer loaded entry.
      assert(
        matches.length === 1,
        "Created review Thread requires one loaded entry.",
      );
      const existing = expect(matches[0], "Created review Thread vanished.");
      if (existing.discussion_revision >= thread.discussion_revision) {
        return current;
      }
      return current.map((candidate) =>
        candidate.thread_id === thread.thread_id ? thread : candidate,
      );
    });
  }

  /** Applies one contiguous action result to its authoritative loaded Thread. */
  async function acceptThreadUpdate(update: ReviewThreadUpdate): Promise<void> {
    const key = api.review.snapshot(update.snapshot_id).queryKey;
    // Same publication ordering as acceptCreatedThread: a refetch begun
    // before the backend committed this action must not resolve afterward
    // and silently revert the applied update.
    await queryClient.cancelQueries({ queryKey: key });
    const current = queryClient.getQueryData<ReviewThread[]>(key);
    if (current === undefined) return;
    const matches = current.filter(
      (candidate) => candidate.thread_id === update.thread_id,
    );
    if (matches.length === 0) {
      await review.refetch();
      return;
    }
    assert(
      matches.length === 1,
      "Updated review Thread requires one authoritative loaded entry.",
    );
    const existing = expect(matches[0], "Updated review Thread disappeared.");
    if (update.discussion_revision !== existing.discussion_revision + 1) {
      await review.refetch();
      return;
    }
    if (
      update.comment !== null &&
      update.comment.sequence !== existing.comments.length
    ) {
      const commentMatches = existing.comments.filter(
        (comment) => comment.comment_id === update.comment?.comment_id,
      );
      assert(
        commentMatches.length === 1,
        "Updated review Comment requires one authoritative loaded entry.",
      );
    }
    let comments = existing.comments;
    if (update.comment !== null) {
      comments =
        update.comment.sequence === comments.length
          ? [...comments, update.comment]
          : comments.map((comment) =>
              comment.comment_id === update.comment?.comment_id
                ? update.comment
                : comment,
            );
    }
    const updatedThread: ReviewThread = {
      ...existing,
      state: update.state,
      attention: update.attention,
      discussion_revision: update.discussion_revision,
      comments,
    };
    queryClient.setQueryData<ReviewThread[]>(
      key,
      current.map((thread) =>
        thread.thread_id === update.thread_id ? updatedThread : thread,
      ),
    );
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

  /** Submits one persisted draft through its single operation-specific action. */
  async function submitReviewDraft(draftId: ReviewId): Promise<boolean> {
    if (draftError() !== null || !reviewAvailable()) return false;
    const profile = profileForWrite();
    if (profile === null) return false;
    const command = draftContext.beginSubmission(draftId);
    if (command.kind === "new-thread") {
      assert(
        command.snapshot_id === props.snapshotId,
        "A new-Thread draft cannot be submitted through another Snapshot.",
      );
    }
    if (command.profile_id !== profile.id) {
      endSubmission(command.draft_id, false);
      toast.showError(
        "Draft Profile conflict",
        new Error(
          "This draft belongs to another Profile. Copy or discard it explicitly.",
        ),
      );
      return false;
    }
    let thread: ReviewThread | ReviewThreadUpdate;
    try {
      thread =
        command.kind === "new-thread"
          ? await createThread.mutateAsync({
              snapshotId: props.snapshotId,
              body: {
                profile_id: profile.id,
                target: command.target,
                body: command.body,
              },
            })
          : command.kind === "reply"
            ? await addComment.mutateAsync({
                snapshotId: props.snapshotId,
                threadId: command.thread_id,
                body: {
                  profile_id: profile.id,
                  body: command.body,
                  attention: "alert",
                },
              })
            : await editComment.mutateAsync({
                snapshotId: props.snapshotId,
                threadId: command.thread_id,
                commentId: command.comment_id,
                body: { profile_id: profile.id, body: command.body },
              });
    } catch (error) {
      if (error instanceof ReviewRequestError) {
        await refreshReviewAfterConflict(error);
      }
      // QueryProvider presented the failure; retain the draft.
      endSubmission(command.draft_id, false);
      return false;
    }
    try {
      if (command.kind === "new-thread") {
        assert("origin_target" in thread);
        await acceptCreatedThread(thread);
      } else {
        assert(!("origin_target" in thread));
        await acceptThreadUpdate(thread);
      }
    } catch (error) {
      toast.showError("Could not refresh review Thread", error);
    }
    endSubmission(command.draft_id, true);
    return true;
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
    if (
      action === "delete" &&
      !window.confirm(
        "Delete this Thread? Its discussion will remain in History as deleted.",
      )
    ) {
      return;
    }
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
      await acceptThreadUpdate(updated);
    } catch (error) {
      if (error instanceof ReviewRequestError) {
        await refreshReviewAfterConflict(error);
      }
      // QueryProvider presented the mutation failure through its metadata.
    }
  }

  /** Activates the one persisted replacement draft for an authored Comment. */
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
    if (editDraftForComment(thread.thread_id, comment.comment_id) !== null)
      return;
    const draft: ReviewDraft = {
      kind: "edit",
      draft_id: newReviewId(),
      thread_id: thread.thread_id,
      comment_id: comment.comment_id,
      profile_id: profile.id,
      body: comment.body,
    };
    draftContext.add(draft);
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
    if (!window.confirm("Delete this Comment?")) return;
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
        await acceptThreadUpdate(updated);
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
  const groupedThreads = createMemo(() => ({
    open: orderedThreads().filter((thread) => thread.state === "open"),
    resolved: orderedThreads().filter((thread) => thread.state === "resolved"),
    deleted: orderedThreads().filter((thread) => thread.state === "deleted"),
  }));

  /** Renders one loaded History Thread with its Snapshot-bound actions. */
  function HistoryThread(historyProps: { thread: ReviewThread }): JSX.Element {
    const thread = () => historyProps.thread;
    const location = historyProps.thread.code_location;
    const replyDraft = () => replyDraftForThread(thread().thread_id);
    const hasEditDraft = () =>
      thread().comments.some(
        (comment) =>
          editDraftForComment(thread().thread_id, comment.comment_id) !== null,
      );
    const expanded = () =>
      expandedThreads().get(thread().thread_id) ??
      (thread().state === "open" || replyDraft() !== null || hasEditDraft());
    return (
      <ThreadCard
        thread={thread()}
        expanded={expanded()}
        navigation={{
          kind: "history",
          viewable: location !== null && props.canViewThread(location),
          onView: () => {
            if (location === null) return;
            const next = new Map(expandedThreads());
            next.set(thread().thread_id, true);
            setExpandedThreads(next);
            void (async () => {
              const anchor = await props.viewThread(location);
              if (anchor === null) return;
              const trigger = anchor.trigger.parentElement?.querySelector(
                `[data-review-marker-kind="${thread().state}"]`,
              );
              assert(
                trigger instanceof HTMLButtonElement && !trigger.hidden,
                "Viewed Thread requires its visible state marker.",
              );
              setActiveCommentInput(null);
              setActiveThreadPanel({
                threadIds: [thread().thread_id],
                anchor: { codeCell: anchor.codeCell, trigger },
              });
            })().catch((error: unknown) =>
              toast.showError("Could not view Thread", error),
            );
          },
        }}
        onToggle={() => {
          const commentInput = activeCommentInput();
          if (
            commentInput?.sourceAnchor === null &&
            commentInput.mount.closest(
              `[data-review-history-thread-id="${thread().thread_id}"]`,
            ) !== null
          ) {
            if (submittingDraftIds().has(commentInput.draftId)) {
              return;
            }
            closeActiveCommentInput();
          }
          const next = new Map(expandedThreads());
          next.set(thread().thread_id, !expanded());
          setExpandedThreads(next);
        }}
        onState={(action) => {
          void changeThreadState(thread(), action);
        }}
        profileId={currentProfileId()}
        submittingDraftIds={submittingDraftIds()}
        commentDeletePending={commentDeletePending}
        threadStatePending={threadStatePending}
        replyDraft={replyDraft()}
        replySubmitting={
          replyDraft() !== null &&
          submittingDraftIds().has(
            expect(replyDraft(), "Submitting reply requires its draft.")
              .draft_id,
          )
        }
        toggleable={true}
        onReplyChange={(body) => updateReplyDraft(thread(), body)}
        onReplySubmit={async () => {
          const draft = replyDraftForThread(thread().thread_id);
          return draft !== null && submitReviewDraft(draft.draft_id);
        }}
        onReplyDiscard={() => {
          const draft = replyDraftForThread(thread().thread_id);
          return draft === null || removeDraft(draft.draft_id);
        }}
        editDraft={(comment) =>
          editDraftForComment(thread().thread_id, comment.comment_id)
        }
        onEdit={(comment) => openEditDraft(thread(), comment)}
        onEditChange={replaceDraft}
        onEditSubmit={(draft) => submitReviewDraft(draft.draft_id)}
        onEditDiscard={(draft) => removeDraft(draft.draft_id)}
        onDeleteComment={(comment) => tombstoneComment(thread(), comment)}
      />
    );
  }

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
        <Show
          when={
            props.view === "inline"
              ? props.inlineHistoryTarget()
              : document.body
          }
          keyed
        >
          {(historyTarget) => (
            <Portal mount={historyTarget}>
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
                      aria-expanded="false"
                      aria-label="Open History"
                    >
                      <kbd>m</kbd>
                      <span class="review-history-label">
                        History ({totalThreads()})
                      </span>
                      <Eye class="review-history-icon" aria-hidden="true" />
                    </button>
                  }
                >
                  <section
                    class="review-history-panel"
                    aria-label="Review History"
                  >
                    <header onClick={() => props.onHistoryOpenChange(false)}>
                      <kbd>m</kbd>
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
                        onClick={(event) => {
                          event.stopPropagation();
                          void review.refetch({ cancelRefetch: false });
                        }}
                      >
                        <RefreshCw
                          class="field-icon"
                          classList={{ spinning: review.isFetching }}
                          aria-hidden="true"
                        />
                      </button>
                      <button
                        type="button"
                        class="review-history-collapse"
                        aria-label="Close History"
                        title="Close History"
                      >
                        <Eye class="review-history-icon" aria-hidden="true" />
                      </button>
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
                            onRetry={() =>
                              review.refetch().then(() => undefined)
                            }
                          />
                        </ErrorPanel>
                      )}
                    </Show>
                    <Show when={draftError()} keyed>
                      {(error) => (
                        <ErrorPanel
                          title="Review drafts unavailable"
                          error={error}
                        >
                          <button
                            type="button"
                            disabled={submittingDraftIds().size > 0}
                            onClick={() => {
                              if (draftContext.clear()) {
                                setActiveCommentInput(null);
                              }
                            }}
                          >
                            Clear stored drafts
                          </button>
                        </ErrorPanel>
                      )}
                    </Show>
                    <div class="review-history-scroll">
                      <Show
                        when={drafts().some(
                          (draft) => draft.kind === "new-thread",
                        )}
                      >
                        <section
                          class="review-drafts"
                          aria-label="Review drafts"
                        >
                          <h3>Drafts</h3>
                          <For
                            each={drafts().filter(
                              (draft) => draft.kind === "new-thread",
                            )}
                          >
                            {(draft) => {
                              assert(
                                draft.kind === "new-thread",
                                "History Drafts contains only new Threads.",
                              );
                              const location: ThreadCodeLocation = {
                                kind: "range",
                                file: draft.target.file,
                                region: draft.target.region,
                                side: draft.target.side,
                                range: draft.target.range,
                              };
                              const continuable = () =>
                                draft.profile_id === currentProfileId() &&
                                draft.snapshot_id === props.snapshotId;
                              const path = expect(
                                location.side === "left"
                                  ? location.file.left_path
                                  : location.file.right_path,
                                "A new Thread draft requires its selected-side File path.",
                              );
                              return (
                                <article class="review-draft">
                                  <div class="review-draft-heading">
                                    <strong>New Thread</strong>
                                    <span>Saved</span>
                                  </div>
                                  <p class="review-draft-location">
                                    <strong>{path}</strong>
                                    <span>
                                      {location.side === "left" ? "old" : "new"}{" "}
                                      · L{location.range.start_line}
                                      {location.range.start_line ===
                                      location.range.end_line
                                        ? ""
                                        : `–${location.range.end_line}`}
                                    </span>
                                  </p>
                                  <Show when={!continuable()}>
                                    <p class="review-draft-unavailable">
                                      {draft.profile_id !== currentProfileId()
                                        ? "Log in as the draft's Profile to continue editing."
                                        : "A new Thread draft remains bound to the review where it was written."}
                                    </p>
                                  </Show>
                                  <p class="review-draft-body">{draft.body}</p>
                                  <div class="review-actions">
                                    <button
                                      type="button"
                                      class="review-action-primary"
                                      disabled={
                                        !continuable() ||
                                        draftError() !== null ||
                                        submittingDraftIds().has(draft.draft_id)
                                      }
                                      onClick={() => {
                                        void (async () => {
                                          if (!props.canViewThread(location)) {
                                            throw new Error(
                                              "Load the reviewed File before continuing this draft.",
                                            );
                                          }
                                          const anchor =
                                            await props.viewThread(location);
                                          if (anchor === null) return;
                                          setActiveThreadPanel(null);
                                          const mount =
                                            anchor.codeCell.parentElement;
                                          assert(
                                            mount !== null &&
                                              (mount.classList.contains(
                                                "diff-side",
                                              ) ||
                                                mount.classList.contains(
                                                  "inline-diff-row",
                                                )),
                                            "Continuing a new Thread requires its exact rendered row.",
                                          );
                                          openCommentInput(
                                            draft,
                                            true,
                                            mount,
                                            anchor,
                                          );
                                        })().catch((error: unknown) =>
                                          toast.showError(
                                            "Could not continue editing draft",
                                            error,
                                          ),
                                        );
                                      }}
                                    >
                                      Continue editing
                                    </button>
                                    <Show when={!continuable()}>
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
                                    </Show>
                                    <button
                                      type="button"
                                      class="review-action-danger"
                                      disabled={
                                        draftError() !== null ||
                                        submittingDraftIds().has(draft.draft_id)
                                      }
                                      onClick={() =>
                                        removeDraft(draft.draft_id)
                                      }
                                    >
                                      Discard
                                    </button>
                                  </div>
                                </article>
                              );
                            }}
                          </For>
                        </section>
                      </Show>
                      <For each={groupedThreads().open}>
                        {(thread) => <HistoryThread thread={thread} />}
                      </For>
                      <Show when={groupedThreads().resolved.length > 0}>
                        <section class="review-thread-group review-thread-group-resolved">
                          <header class="review-thread-group-heading">
                            <strong>Resolved</strong>
                            <span>{groupedThreads().resolved.length}</span>
                          </header>
                          <For each={groupedThreads().resolved}>
                            {(thread) => <HistoryThread thread={thread} />}
                          </For>
                        </section>
                      </Show>
                      <Show when={groupedThreads().deleted.length > 0}>
                        <details class="review-thread-group review-thread-group-deleted">
                          <summary class="review-thread-group-heading">
                            <strong>Deleted</strong>
                            <span>{groupedThreads().deleted.length}</span>
                          </summary>
                          <For each={groupedThreads().deleted}>
                            {(thread) => <HistoryThread thread={thread} />}
                          </For>
                        </details>
                      </Show>
                    </div>
                  </section>
                </Show>
              </aside>
            </Portal>
          )}
        </Show>
      </UnexpectedErrorBoundary>
    </ReviewContext.Provider>
  );
}

/** Renders the one active new-Thread draft at its final inline mount. */
function CommentInput(props: {
  drafts: Accessor<readonly ReviewDraft[]>;
  active: Accessor<ActiveCommentInput | null>;
  draftError: Accessor<Error | null>;
  reviewAvailable: Accessor<boolean>;
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  onDraftChange(draft: NewThreadDraft): boolean;
  onDiscard(draftId: ReviewId): boolean;
  onClose(): void;
  onSubmit(draftId: ReviewId): Promise<boolean>;
}): JSX.Element {
  const draft = createMemo(() => {
    const active = props.active();
    if (active === null) return null;
    const current =
      active.input ??
      expect(
        props
          .drafts()
          .find((candidate) => candidate.draft_id === active.draftId),
        "Active Comment input lost its persisted draft.",
      );
    assert(
      current.kind === "new-thread",
      "Code input requires a new-Thread draft.",
    );
    return current;
  });
  const submitting = createMemo(() => {
    const current = draft();
    return current !== null && props.submittingDraftIds().has(current.draft_id);
  });
  const mount = createMemo(() => {
    const active = props.active();
    return active?.mount ?? document.body;
  });

  return (
    <Show when={props.active() !== null && draft() !== null}>
      <Portal
        mount={mount()}
        // The Portal wrapper contributes no box (`display: contents` via this
        // class); styling it by class keeps the row free of `:has()` rules
        // that would be evaluated against every child of every diff row.
        ref={(host) => host.classList.add("review-portal-host")}
      >
        <div
          class="review-comment-input review-comment-input-inline"
          classList={{
            "review-comment-input-line": draft()?.kind === "new-thread",
          }}
        >
          <header class="comment-floater-header">
            <div class="comment-floater-heading">
              <strong>New Thread</strong>
              <Show when={props.active()?.input === null}>
                <span>Saved</span>
              </Show>
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
                "Comment input requires its persisted draft.",
              );
              if (
                props.draftError() !== null ||
                !props.reviewAvailable() ||
                props.submittingDraftIds().has(current.draft_id)
              ) {
                return;
              }
              void props.onSubmit(current.draft_id);
            }}
          >
            <label class="comment-floater-field">
              <span>Comment</span>
              <textarea
                rows="5"
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
                    })
                  ) {
                    event.currentTarget.value = current.body;
                  }
                }}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    (event.metaKey || event.ctrlKey) &&
                    !event.shiftKey &&
                    !event.altKey
                  ) {
                    event.preventDefault();
                    const form = event.currentTarget.form;
                    assert(
                      form !== null,
                      "Comment input requires its submission form.",
                    );
                    form.requestSubmit();
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
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  commentDeletePending(commentId: ReviewId): boolean;
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  replyDraft(threadId: ReviewId): ReviewDraft | null;
  editDraft(threadId: ReviewId, commentId: ReviewId): ReviewDraft | null;
  onClose(): void;
  onReplyChange(thread: ReviewThread, body: string): boolean;
  onReplySubmit(thread: ReviewThread): Promise<boolean>;
  onReplyDiscard(thread: ReviewThread): boolean;
  onEdit(thread: ReviewThread, comment: ReviewComment): void;
  onEditChange(draft: ReviewDraft): boolean;
  onEditSubmit(draft: ReviewDraft): Promise<boolean>;
  onEditDiscard(draft: ReviewDraft): boolean;
  onDeleteComment(thread: ReviewThread, comment: ReviewComment): void;
  onState(thread: ReviewThread, action: "resolve" | "reopen" | "delete"): void;
}): JSX.Element {
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

  return (
    <Show when={props.active()} keyed>
      {(active) => {
        const [expandedThreadIds, setExpandedThreadIds] = createSignal<
          ReadonlySet<ReviewId>
        >(new Set());
        const multiple = () => visibleThreads().length > 1;
        const mount = active.anchor.codeCell.parentElement;
        assert(
          mount !== null &&
            (mount.classList.contains("diff-side") ||
              mount.classList.contains("inline-diff-row")),
          "Inline Threads require their exact rendered diff row.",
        );
        return (
          <Portal
            mount={mount}
            // Same boxless-wrapper contract as the Comment input's Portal.
            ref={(host) => host.classList.add("review-portal-host")}
          >
            <div class="comment-floater review-inline-threads">
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
              <For each={visibleThreads()}>
                {(thread) => {
                  const replyDraft = () => props.replyDraft(thread.thread_id);
                  return (
                    <ThreadCard
                      thread={thread}
                      expanded={
                        !multiple() ||
                        expandedThreadIds().has(thread.thread_id) ||
                        replyDraft() !== null ||
                        thread.comments.some(
                          (comment) =>
                            props.editDraft(
                              thread.thread_id,
                              comment.comment_id,
                            ) !== null,
                        )
                      }
                      navigation={{ kind: "inline" }}
                      profileId={props.profileId()}
                      submittingDraftIds={props.submittingDraftIds()}
                      commentDeletePending={props.commentDeletePending}
                      threadStatePending={props.threadStatePending}
                      replyDraft={replyDraft()}
                      replySubmitting={
                        replyDraft() !== null &&
                        props
                          .submittingDraftIds()
                          .has(
                            expect(
                              replyDraft(),
                              "Submitting reply requires its draft.",
                            ).draft_id,
                          )
                      }
                      toggleable={multiple()}
                      onToggle={() => {
                        if (!multiple()) return;
                        const next = new Set(expandedThreadIds());
                        if (next.has(thread.thread_id)) {
                          next.delete(thread.thread_id);
                        } else {
                          next.add(thread.thread_id);
                        }
                        setExpandedThreadIds(next);
                      }}
                      onState={(action) => props.onState(thread, action)}
                      onReplyChange={(body) =>
                        props.onReplyChange(thread, body)
                      }
                      onReplySubmit={() => props.onReplySubmit(thread)}
                      onReplyDiscard={() => props.onReplyDiscard(thread)}
                      editDraft={(comment) =>
                        props.editDraft(thread.thread_id, comment.comment_id)
                      }
                      onEdit={(comment) => props.onEdit(thread, comment)}
                      onEditChange={props.onEditChange}
                      onEditSubmit={props.onEditSubmit}
                      onEditDiscard={props.onEditDiscard}
                      onDeleteComment={(comment) =>
                        props.onDeleteComment(thread, comment)
                      }
                    />
                  );
                }}
              </For>
            </div>
          </Portal>
        );
      }}
    </Show>
  );
}

/** Renders one History Thread in complete or folded form. */
function ThreadCard(props: {
  thread: ReviewThread;
  expanded: boolean;
  navigation:
    | { kind: "inline" }
    | { kind: "history"; viewable: boolean; onView(): void };
  profileId: number | null;
  submittingDraftIds: ReadonlySet<ReviewId>;
  commentDeletePending(commentId: ReviewId): boolean;
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  replyDraft: ReviewDraft | null;
  replySubmitting: boolean;
  toggleable: boolean;
  onToggle(): void;
  onState(action: "resolve" | "reopen" | "delete"): void;
  onReplyChange(body: string): boolean;
  onReplySubmit(): Promise<boolean>;
  onReplyDiscard(): boolean;
  editDraft(comment: ReviewComment): ReviewDraft | null;
  onEdit(comment: ReviewComment): void;
  onEditChange(draft: ReviewDraft): boolean;
  onEditSubmit(draft: ReviewDraft): Promise<boolean>;
  onEditDiscard(draft: ReviewDraft): boolean;
  onDeleteComment(comment: ReviewComment): void;
}): JSX.Element {
  const origin = props.thread.origin_target;
  const excerptPath = expect(
    origin.side === "left" ? origin.file.left_path : origin.file.right_path,
    "A review origin requires its selected-side File path.",
  );
  const excerptFileName = excerptPath.slice(excerptPath.lastIndexOf("/") + 1);
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
  /** Renders the shared Thread identity displayed by both header variants. */
  function ThreadHeading(): JSX.Element {
    return (
      <>
        <span class="review-thread-state-dot" aria-hidden="true" />
        <strong title={firstComment().author.display_name}>
          {firstComment().author.display_name}
        </strong>
        <span class="review-thread-location" title={excerptPath}>
          {excerptFileName}
          <Show when={props.thread.original_excerpt} keyed>
            {(excerpt) => <> · L{excerpt.selected_start_line}</>}
          </Show>
        </span>
        <Show when={props.thread.outdated_reason !== null}>
          <span
            class="review-warning"
            title={
              props.thread.code_location === null
                ? "The reviewed file is not present in this Snapshot."
                : "The reviewed code changed after this Thread was created."
            }
          >
            <TriangleAlert aria-hidden="true" />
          </span>
        </Show>
      </>
    );
  }
  return (
    <article
      class="review-thread"
      data-review-history-thread-id={
        props.navigation.kind === "inline" ? undefined : props.thread.thread_id
      }
      classList={{
        "review-thread-expanded": props.expanded,
        "review-thread-resolved": props.thread.state === "resolved",
        "review-thread-deleted": props.thread.state === "deleted",
        "review-thread-outdated": props.thread.outdated_reason !== null,
      }}
    >
      <header>
        <Show
          when={props.navigation.kind === "history" || props.toggleable}
          fallback={
            <div class="review-thread-heading">
              <ThreadHeading />
            </div>
          }
        >
          <button
            type="button"
            class="review-thread-heading review-thread-toggle"
            aria-expanded={props.expanded}
            onClick={props.onToggle}
          >
            <ThreadHeading />
            <Show when={!props.expanded}>
              <span class="review-thread-summary">
                {firstComment().body ?? "Comment deleted"} ·{" "}
                {props.thread.comments.length}
              </span>
            </Show>
          </button>
        </Show>
        <Show
          when={
            props.navigation.kind === "history" ? props.navigation : undefined
          }
          keyed
        >
          {(navigation) => (
            <Show
              when={props.thread.code_location !== null}
              fallback={
                <span
                  class="review-view-unavailable"
                  title="The reviewed file is not present in this Snapshot."
                >
                  <button
                    type="button"
                    class="review-view review-thread-goto"
                    disabled
                    aria-label="Go to code unavailable: the reviewed file is not present in this Snapshot."
                  >
                    <LocateFixed aria-hidden="true" />
                  </button>
                </span>
              }
            >
              <button
                type="button"
                class="review-view review-thread-goto"
                title={
                  navigation.viewable
                    ? "Go to reviewed code"
                    : "The reviewed File is not loaded."
                }
                aria-label={
                  navigation.viewable
                    ? "Go to reviewed code"
                    : "Go to code unavailable: the reviewed File is not loaded."
                }
                disabled={!navigation.viewable}
                onClick={navigation.onView}
              >
                <LocateFixed aria-hidden="true" />
              </button>
            </Show>
          )}
        </Show>
      </header>
      <Show when={props.expanded}>
        <Show when={props.thread.code_location === null}>
          <p class="review-no-location">
            The reviewed file is not present in this Snapshot.
          </p>
        </Show>
        <Show when={props.thread.original_excerpt} keyed>
          {(excerpt) => (
            <pre class="review-excerpt" data-side={excerpt.side}>
              <For each={excerpt.lines}>
                {(line, index) => {
                  const lineNumber = excerpt.start_line + index();
                  const selected =
                    excerpt.selected_start_line <= lineNumber &&
                    lineNumber <= excerpt.selected_end_line;
                  return (
                    <span
                      class="review-excerpt-line"
                      classList={{ "review-excerpt-selected": selected }}
                    >
                      <span class="review-excerpt-line-number">
                        {lineNumber}
                      </span>
                      <span class="review-excerpt-code">{line}</span>
                    </span>
                  );
                }}
              </For>
            </pre>
          )}
        </Show>
        <ol class="review-comments">
          <For each={props.thread.comments}>
            {(comment) => {
              const editDraft = () => props.editDraft(comment);
              return (
                <ReviewCommentView
                  comment={comment}
                  editDraft={editDraft()}
                  editSubmitting={
                    editDraft() !== null &&
                    props.submittingDraftIds.has(
                      expect(editDraft(), "Submitting edit requires its draft.")
                        .draft_id,
                    )
                  }
                  response={
                    comment.author.profile_id !==
                    firstComment().author.profile_id
                  }
                  editable={
                    props.thread.state !== "deleted" &&
                    !comment.deleted &&
                    authoredByCurrentProfile(comment)
                  }
                  deletable={
                    props.thread.state !== "deleted" && !comment.deleted
                  }
                  deletePending={props.commentDeletePending(comment.comment_id)}
                  onEdit={() => props.onEdit(comment)}
                  onEditChange={props.onEditChange}
                  onEditSubmit={props.onEditSubmit}
                  onEditDiscard={props.onEditDiscard}
                  onDelete={() => props.onDeleteComment(comment)}
                />
              );
            }}
          </For>
        </ol>
        <Show
          when={props.thread.state !== "deleted" || props.replyDraft !== null}
        >
          <form
            class="review-reply-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (
                (props.replyDraft?.body.trim().length ?? 0) === 0 ||
                props.replySubmitting
              ) {
                return;
              }
              void props.onReplySubmit();
            }}
          >
            <textarea
              class="review-reply-input"
              rows="2"
              aria-label="Reply"
              placeholder="Write a reply…"
              value={props.replyDraft?.body ?? ""}
              disabled={
                props.thread.state === "deleted" || props.replySubmitting
              }
              onInput={(event) => {
                const body = event.currentTarget.value;
                if (!props.onReplyChange(body)) {
                  event.currentTarget.value = props.replyDraft?.body ?? "";
                }
              }}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  (event.metaKey || event.ctrlKey) &&
                  !event.shiftKey &&
                  !event.altKey
                ) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <div class="review-reply-actions">
              <Show when={props.replyDraft !== null}>
                <button
                  type="button"
                  class="comment-floater-secondary"
                  disabled={props.replySubmitting}
                  onClick={props.onReplyDiscard}
                >
                  Discard
                </button>
              </Show>
              <Show when={props.thread.state !== "deleted"}>
                <button
                  type="submit"
                  class="comment-floater-primary"
                  disabled={
                    props.replySubmitting ||
                    (props.replyDraft?.body.trim().length ?? 0) === 0
                  }
                >
                  {props.replySubmitting ? "Submitting…" : "Reply"}
                </button>
              </Show>
            </div>
          </form>
        </Show>
        <Show when={props.thread.state !== "deleted"}>
          <div class="review-actions">
            <Show
              when={props.thread.state === "open"}
              fallback={
                <button
                  type="button"
                  class="review-action-state"
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
                class="review-action-state"
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
              class="review-action-danger"
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
  editDraft: ReviewDraft | null;
  editSubmitting: boolean;
  response: boolean;
  editable: boolean;
  deletable: boolean;
  deletePending: boolean;
  onEdit(): void;
  onEditChange(draft: ReviewDraft): boolean;
  onEditSubmit(draft: ReviewDraft): Promise<boolean>;
  onEditDiscard(draft: ReviewDraft): boolean;
  onDelete(): void;
}): JSX.Element {
  return (
    <li
      data-review-comment-id={props.comment.comment_id}
      classList={{
        "review-comment-deleted": props.comment.deleted,
        "review-comment-response": props.response,
      }}
    >
      <span class="review-comment-avatar" aria-hidden="true">
        {props.comment.author.display_name.slice(0, 1).toUpperCase()}
      </span>
      <div class="review-comment-bubble">
        <div class="review-comment-heading">
          <strong title={props.comment.author.display_name}>
            {props.comment.author.display_name}
          </strong>
          <Show when={props.editable || props.deletable}>
            <div class="review-comment-actions">
              <Show when={props.editable}>
                <button
                  type="button"
                  aria-label="Edit Comment"
                  title="Edit Comment"
                  onClick={props.onEdit}
                >
                  <Pencil aria-hidden="true" />
                </button>
              </Show>
              <Show when={props.deletable}>
                <button
                  type="button"
                  class="review-action-danger"
                  aria-label="Delete Comment"
                  title="Delete Comment"
                  disabled={props.deletePending}
                  onClick={props.onDelete}
                >
                  <Trash2 aria-hidden="true" />
                </button>
              </Show>
            </div>
          </Show>
        </div>
        <p>{props.comment.body ?? "Comment deleted"}</p>
        <Show when={props.editDraft}>
          {(draft) => {
            assert(
              draft().kind === "edit",
              "Comment editor requires an edit draft.",
            );
            return (
              <form
                class="review-comment-edit-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (
                    props.editable &&
                    draft().body.trim().length > 0 &&
                    !props.editSubmitting
                  ) {
                    void props.onEditSubmit(draft());
                  }
                }}
              >
                <textarea
                  rows="3"
                  aria-label="Edit Comment"
                  value={draft().body}
                  disabled={props.editSubmitting || !props.editable}
                  onInput={(event) => {
                    const replacement = {
                      ...draft(),
                      body: event.currentTarget.value,
                    };
                    if (!props.onEditChange(replacement)) {
                      event.currentTarget.value = draft().body;
                    }
                  }}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      (event.metaKey || event.ctrlKey) &&
                      !event.shiftKey &&
                      !event.altKey
                    ) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                />
                <div class="review-reply-actions">
                  <button
                    type="button"
                    class="comment-floater-secondary"
                    disabled={props.editSubmitting}
                    onClick={() => props.onEditDiscard(draft())}
                  >
                    Discard
                  </button>
                  <button
                    type="submit"
                    class="comment-floater-primary"
                    disabled={
                      !props.editable ||
                      props.editSubmitting ||
                      draft().body.trim().length === 0
                    }
                  >
                    {props.editSubmitting ? "Submitting…" : "Save"}
                  </button>
                </div>
              </form>
            );
          }}
        </Show>
      </div>
    </li>
  );
}
