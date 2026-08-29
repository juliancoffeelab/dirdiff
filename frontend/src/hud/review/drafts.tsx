/**
 * Provides the application-lifetime persisted review draft document.
 *
 * `ReviewDraftRoot` mounts once, validates the localStorage representation before
 * publishing it, writes every accepted add, replacement, removal, or clear, and
 * tracks identities whose submissions are in flight. Consumers use the checked
 * context interface so storage remains the only draft authority.
 *
 * The document stores unfinished review input only. Snapshot queries, mutations,
 * markers, and presentation remain outside this module.
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

/** Legacy whole-document key read only while importing stored drafts. */
const REVIEW_DRAFT_STORAGE_KEY = "dirdiff:v1:review-drafts";
// One localStorage entry per draft, keyed by its identity, so persisting a
// keystroke serializes only the changed draft instead of every unfinished
// text. The single-document key above remains readable as legacy input and is
// migrated to per-draft entries on load.
/** Prefix for the authoritative localStorage entry of each individual draft. */
const REVIEW_DRAFT_STORAGE_PREFIX = "dirdiff:v2:review-draft:";

/**
 * Validates persisted input for a Thread that does not exist yet.
 *
 * Snapshot, target, and Profile identity remain fixed while the body changes.
 * This shape never represents an accepted backend Thread.
 */
const NewThreadDraftSchema = z.strictObject({
  /** Discriminant selecting new-Thread submission and target requirements. */
  kind: z.literal("new-thread"),
  /** Client-generated identity used for storage and submission settlement. */
  draft_id: ReviewIdSchema,
  /** Exact Snapshot in which the eventual Thread must be created. */
  snapshot_id: ReviewIdSchema,
  /** Complete code coordinate retained for eventual Thread creation. */
  target: ReviewTargetSchema,
  /** Profile selected when this unfinished input was created. */
  profile_id: z.number().int().positive(),
  /** Current unsent text; blank values may exist transiently but are not reloaded. */
  body: z.string(),
});
/**
 * Validates persisted input for a reply to an existing Thread.
 *
 * The Thread and Profile remain fixed while the body changes. Snapshot identity
 * comes from the containing discussion and is not duplicated in this record.
 */
const ReplyDraftSchema = z.strictObject({
  /** Discriminant selecting reply submission behavior. */
  kind: z.literal("reply"),
  /** Client-generated identity used for storage and submission settlement. */
  draft_id: ReviewIdSchema,
  /** Existing Thread that will receive the reply. */
  thread_id: ReviewIdSchema,
  /** Profile selected when this unfinished reply was created. */
  profile_id: z.number().int().positive(),
  /** Current unsent reply text; blank values do not survive reload. */
  body: z.string(),
});
/**
 * Validates persisted replacement text for one existing Comment.
 *
 * Thread, Comment, and Profile identity stay fixed until submission or discard.
 * The record holds a complete replacement body, never a patch.
 */
const EditDraftSchema = z.strictObject({
  /** Discriminant selecting Comment-edit submission behavior. */
  kind: z.literal("edit"),
  /** Client-generated identity used for storage and submission settlement. */
  draft_id: ReviewIdSchema,
  /** Existing Thread containing the Comment being edited. */
  thread_id: ReviewIdSchema,
  /** Existing authored Comment whose body will be replaced. */
  comment_id: ReviewIdSchema,
  /** Profile whose authorship must permit the edit. */
  profile_id: z.number().int().positive(),
  /** Complete current replacement text; blank values do not survive reload. */
  body: z.string(),
});
/**
 * Accepts exactly the unfinished review operations persisted by the HUD.
 *
 * The `kind` discriminator fixes each operation's required identities. Accepted
 * backend entities and in-flight state remain outside this stored union.
 */
const ReviewDraftSchema = z.discriminatedUnion("kind", [
  NewThreadDraftSchema,
  ReplyDraftSchema,
  EditDraftSchema,
]);
/**
 * Validates the legacy whole-document representation during one-time import.
 *
 * Every contained record must satisfy the current draft schema, and identities
 * must be unique before entries are split into individual storage keys.
 */
const StoredReviewDraftsSchema = z
  .object({
    /** Complete legacy draft entries before blank-body filtering and migration. */
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
/**
 * Retains one unfinished browser-authored review operation.
 * Its discriminant fixes the identities needed for eventual submission; it is
 * never a backend Thread or Comment and blank bodies do not survive reload.
 */
export type ReviewDraft = z.infer<typeof ReviewDraftSchema>;
/**
 * Retains unfinished input for a Thread that does not exist yet.
 *
 * This narrower arm is used by marker and History code that requires a code
 * target before any backend Thread identity exists.
 */
export type NewThreadDraft = z.infer<typeof NewThreadDraftSchema>;

/**
 * Exposes the authoritative persisted draft document and its in-flight identities.
 *
 * Writes publish only after localStorage succeeds. Callers must restore supplied
 * input on false and pair every begun submission with one settlement.
 */
export type ReviewDraftContextValue = {
  /**
   * Reads the last successfully persisted drafts. Initial load orders them by
   * stable identity; later consumers must match by identity rather than position.
   */
  drafts: Accessor<readonly ReviewDraft[]>;
  /**
   * Reads the first storage failure that made the published document read-only.
   * It remains non-null until explicit clearing succeeds or the application reloads.
   */
  error: Accessor<Error | null>;
  /**
   * Reads identities whose exact persisted values are currently being submitted.
   * Controls must treat membership as immutable input and wait for settlement.
   */
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  /**
   * Persists a previously unseen `draft` before publishing it to consumers.
   * Returns false after a storage failure; callers must retain or restore their
   * prior input because no accepted value will be fed back through `drafts`.
   */
  add(draft: ReviewDraft): boolean;
  /**
   * Persists `draft` over the sole existing value with the same identity.
   * Returns whether storage accepted it; a false result leaves `drafts`
   * unchanged, so an input must restore the value it received from the accessor.
   */
  replace(draft: ReviewDraft): boolean;
  /**
   * Removes the sole persisted draft named by `draftId`, then publishes its absence.
   * Returns false when storage is unavailable; callers close draft UI only after
   * true makes the removal observable through `drafts`.
   */
  remove(draftId: ReviewId): boolean;
  /**
   * Removes every draft entry and clears a prior storage error as one published change.
   * Returns false without changing the document when clearing storage itself fails.
   */
  clear(): boolean;
  /**
   * Marks the persisted `draftId` in flight and returns that exact submission value.
   * Callers invoke it once per begun submission attempt and must pair it with
   * `endSubmission`, including when validation prevents an HTTP action. A missing
   * draft or duplicate start is an invariant violation.
   */
  beginSubmission(draftId: ReviewId): ReviewDraft;
  /**
   * Settles the in-flight `draftId`; success removes it after clearing its pending
   * state, while failure leaves it editable. Callers invoke this exactly once for
   * each successful `beginSubmission`, after the HTTP attempt and any local query
   * update attempt finish. A backend-accepted write uses success even if that local
   * update failed and the caller reported the error separately.
   *
   * @param draftId Identity previously started and disabled for submission.
   * @param succeeded Whether the backend accepted the draft's write action.
   */
  endSubmission(draftId: ReviewId, succeeded: boolean): void;
};

/** Context identity for the single application-lifetime persisted draft document. */
const ReviewDraftContext = createContext<ReviewDraftContextValue>();

/**
 * Create a fresh lowercase 32-hex identity for a draft or review operation.
 *
 * The value comes from `crypto.randomUUID` and is validated against the shared API
 * shape. This function does not inspect localStorage or the backend for collisions.
 */
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
 * preserves the previous authoritative value and disables later draft writes
 * until explicit clearing succeeds or the application reloads. A submitted draft
 * is disabled until its submission attempt settles. Backend acceptance attempts
 * to remove it; rejection, pre-HTTP validation failure, or failed storage removal
 * leaves the ordinary editable draft in localStorage.
 */
export function ReviewDraftRoot(props: {
  /**
   * Application subtree that may read or mutate the shared draft document.
   * It renders only after the context is installed and shares this root's lifetime.
   */
  children: JSX.Element;
}): JSX.Element {
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

  /**
   * Report whether an earlier storage failure has disabled draft writes.
   *
   * # Usage
   *
   * Persistence and removal helpers call this before touching localStorage. A
   * true result also presents the retained failure through an error Toast, so
   * the attempted operation must stop without changing Solid state.
   */
  function reportUnavailable(): boolean {
    const existingFailure = error();
    if (existingFailure !== null) {
      toast.showError("Review drafts unavailable", existingFailure);
      return true;
    }
    return false;
  }

  /**
   * Convert one storage failure into this mount's disabled state.
   *
   * @param caught Value thrown by the failed browser storage operation.
   *
   * # Usage
   *
   * Call only from a caught localStorage write or removal failure. The helper
   * retains an `Error` until clear or reload, presents it once for this attempt,
   * and returns false in the shape expected by draft mutation functions.
   */
  function storeFailed(caught: unknown): false {
    const failure =
      caught instanceof Error
        ? caught
        : new Error("Review draft write failed without an Error value.");
    setError(failure);
    toast.showError("Review drafts unavailable", failure);
    return false;
  }

  /**
   * Persists one changed draft entry before publishing the next state.
   *
   * @param draft Exact entry written to its identity-derived storage key.
   * @param next Complete document published only after that write succeeds.
   *
   * # Usage
   *
   * `add` and `replace` provide the complete next document. This helper writes
   * only the changed entry, then publishes `next`; a blocked or failed write
   * returns false and preserves the previous Solid document.
   *
   * # Failures
   *
   * Browser serialization and storage failures become the retained disabled
   * state and an error Toast. They do not throw through this boundary.
   */
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

  /**
   * Add one previously unseen draft after persisting its exact entry.
   *
   * @param draft Complete new draft with a freshly generated identity.
   *
   * # Usage
   *
   * Draft producers call this once when meaningful input first enters the
   * document. True means both localStorage and the accessor contain the new
   * value; false leaves the caller responsible for its input.
   *
   * # Failures
   *
   * Reusing an existing identity throws. Storage failures return false through
   * `persistDraft` and disable later writes for this mount.
   */
  function add(draft: ReviewDraft): boolean {
    assert(
      drafts().every((candidate) => candidate.draft_id !== draft.draft_id),
      "Review draft creation reused an identity.",
    );
    return persistDraft(draft, [...drafts(), draft]);
  }

  /**
   * Replace the sole persisted draft with the same identity.
   *
   * @param replacement Complete next value for one existing draft identity.
   *
   * # Usage
   *
   * Editors pass a complete replacement derived from their current draft. True
   * feeds the new value back through `drafts`; false requires the editor to
   * restore its prior supplied value.
   *
   * # Failures
   *
   * A missing or duplicate identity throws. Storage failures return false and
   * leave the published document unchanged.
   */
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

  /**
   * Remove one exact draft from storage before publishing its absence.
   *
   * @param draftId Sole persisted draft identity to remove.
   *
   * # Usage
   *
   * Discard and successful submission paths call this only for an identity in
   * `drafts`. The caller closes its editor only when true confirms the storage
   * removal and accessor update.
   *
   * # Failures
   *
   * A missing or duplicate identity throws. A blocked or failed storage removal
   * returns false, retains the draft, and presents the retained failure.
   */
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

  /**
   * Clear every current and legacy draft entry and reset the disabled state.
   *
   * # Usage
   *
   * The explicit clear action may call this even after ordinary writes are
   * disabled. Success removes all prefixed entries, publishes an empty document,
   * and clears the retained error in one batch.
   *
   * # Failures
   *
   * A storage failure returns false, retains the Solid document, replaces the
   * retained error, and shows a Toast. Entries removed before that failure are
   * not restored.
   */
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

  /**
   * Mark one persisted draft as the exact input to an in-flight write.
   *
   * @param draftId Persisted identity selected for submission.
   *
   * # Usage
   *
   * `submitDraft` calls this once after its availability checks. The returned
   * value is the frozen command input; the caller must pair every successful
   * start with one `endSubmission` call.
   *
   * # Failures
   *
   * A missing, duplicate, or already-submitting identity throws before changing
   * the in-flight set.
   */
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

  /**
   * Ends one draft write and removes its input only after success.
   *
   * @param draftId Identity previously returned by `beginSubmission`.
   * @param succeeded Whether the backend accepted the draft's write action.
   *
   * # Usage
   *
   * Call exactly once for every completed `beginSubmission`. Failure settlement
   * only re-enables the draft. Success first clears in-flight state and then
   * asks `remove` to persist its absence.
   *
   * # Failures
   *
   * Settlement without a matching start throws. On successful settlement, a
   * lost or duplicate persisted draft throws after in-flight state is cleared.
   * If accepted backend work cannot be removed from storage, `remove` retains
   * the draft, disables later writes, and presents the storage failure.
   */
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

/**
 * Return the application-lifetime persisted draft document.
 *
 * Review surfaces call this only below ReviewDraftRoot. Missing context is an
 * invariant violation and throws rather than constructing an empty document.
 */
export function useReviewDrafts(): ReviewDraftContextValue {
  return expect(
    useContext(ReviewDraftContext),
    "Review requires the application draft document.",
  );
}
