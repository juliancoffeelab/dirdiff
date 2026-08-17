/**
 * Owns the application-lifetime persisted review-draft document.
 *
 * The module exports ReviewDraftRoot, the draft value types, the checked
 * `useReviewDrafts` accessor, and `newReviewId`. ReviewDraftRoot is mounted
 * once by `main.tsx` and owns the localStorage representation: it validates
 * stored drafts on mount, persists every accepted change, and tracks
 * in-flight submissions. Consumers read and write drafts only through the
 * provided context value; the module must not know Snapshots, queries,
 * mutations, markers, or any review presentation.
 */
import {
  batch,
  createContext,
  createSignal,
  onMount,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";
import { z } from "zod";
import {
  ReviewIdSchema,
  ReviewTargetSchema,
  type ReviewId,
} from "../../api/api";
import { useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";

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
export type ReviewDraft = z.infer<typeof ReviewDraftSchema>;
/** Retains unfinished input for a Thread that does not exist yet. */
export type NewThreadDraft = z.infer<typeof NewThreadDraftSchema>;

/** Exposes application-lifetime drafts and their active submissions. */
export type ReviewDraftContextValue = {
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

/** Returns the application's persisted review-draft document. */
export function useReviewDrafts(): ReviewDraftContextValue {
  return expect(
    useContext(ReviewDraftContext),
    "Review requires the application draft document.",
  );
}
