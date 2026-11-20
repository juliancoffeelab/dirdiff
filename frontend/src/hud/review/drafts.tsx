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
export type ReviewDraft = z.infer<typeof ReviewDraftSchema>;
export type NewThreadDraft = z.infer<typeof NewThreadDraftSchema>;

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

export function newReviewId(): ReviewId {
  return ReviewIdSchema.parse(crypto.randomUUID().replaceAll("-", ""));
}

export function ReviewDraftRoot(props: { children: JSX.Element }): JSX.Element {
  const toast = useToasts();
  const [drafts, setDrafts] = createSignal<readonly ReviewDraft[]>([]);
  const [error, setError] = createSignal<Error | null>(null);
  const [submittingDraftIds, setSubmittingDraftIds] = createSignal<
    ReadonlySet<ReviewId>
  >(new Set());

  function draftStorageKey(draftId: ReviewId): string {
    return `${REVIEW_DRAFT_STORAGE_PREFIX}${draftId}`;
  }

  // This is mount work rather than an effect because it reads no reactive input
  // and must import browser storage exactly once for the application-lifetime
  // provider. It migrates the legacy document, validates each current entry,
  // removes blank input, sorts by stable identity, and then publishes one Solid
  // value. The synchronous operation installs no listener and needs no cleanup.
  // If any read, parse, assertion, or storage write fails, the provider publishes
  // the failure and no draft state; writes completed before that failure remain
  // in localStorage and are examined again on the next application mount.
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

  function reportUnavailable(): boolean {
    const existingFailure = error();
    if (existingFailure !== null) {
      toast.showError("Review drafts unavailable", existingFailure);
      return true;
    }
    return false;
  }

  function storeFailed(caught: unknown): false {
    const failure =
      caught instanceof Error
        ? caught
        : new Error("Review draft write failed without an Error value.");
    setError(failure);
    toast.showError("Review drafts unavailable", failure);
    return false;
  }

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

  function add(draft: ReviewDraft): boolean {
    assert(
      drafts().every((candidate) => candidate.draft_id !== draft.draft_id),
      "Review draft creation reused an identity.",
    );
    return persistDraft(draft, [...drafts(), draft]);
  }

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

export function useReviewDrafts(): ReviewDraftContextValue {
  return expect(
    useContext(ReviewDraftContext),
    "Review requires the application draft document.",
  );
}
