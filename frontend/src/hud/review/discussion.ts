/**
 * Applies Thread and Comment operations to shared draft and query data.
 *
 * Each `createThreadDiscussion` instance observes one Snapshot's canonical Thread
 * query, uses the application draft document, and reads shared mutation state.
 * Accepted backend writes update the canonical query without letting an older
 * in-flight refetch replace them. Submission settlement notifies the host before
 * a successful draft is removed in the same Solid batch.
 *
 * Anchored review and History may create separate instances because neither keeps
 * a private copy of Threads, drafts, or pending state. DOM and presentation state
 * remain with those callers.
 */
import { batch, createMemo, type Accessor } from "solid-js";
import {
  createMutation,
  createQuery,
  useMutationState,
  useQueryClient,
} from "@tanstack/solid-query";
import {
  api,
  ReviewIdSchema,
  ReviewRequestError,
  type ReviewComment,
  type ReviewId,
  type ReviewThread,
  type ReviewThreadUpdate,
} from "../../api/api";
import { useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { StoredProfile } from "../Profile";
import { newReviewId, useReviewDrafts, type ReviewDraft } from "./drafts";

/**
 * Supplies the live authorities and settlement behavior for one discussion instance.
 *
 * The factory reads Snapshot identity and Profile selection from these values. It
 * reports every begun draft submission through `onSubmitted` before the shared
 * draft document applies the matching settlement.
 */
export type ThreadDiscussionArgs = {
  /**
   * Immutable Snapshot whose canonical query and HTTP routes this instance uses.
   * New-Thread submissions must carry the same identity; other draft kinds inherit it.
   */
  snapshotId: ReviewId;
  /**
   * Reads the currently selected Profile whenever an action needs authorship.
   * The accessor may return null, in which case write actions stop and present
   * the Profile control; selection changes feed back through the next read.
   */
  profile: Accessor<StoredProfile | null>;
  /**
   * Runs once for every begun draft submission when it settles, including a Profile
   * conflict detected before any HTTP action. The host receives the persisted draft
   * identity and whether the backend accepted the write. For accepted writes, this
   * runs after the local query update was attempted, even when that attempt raised
   * and was reported. The host may synchronously close UI referring to the draft;
   * then, in the same Solid batch, accepted settlement removes it while rejection
   * restores ordinary editing. It is not called when submission never begins, or
   * for Thread state and Comment-delete actions.
   *
   * @param draftId Persisted draft whose begun submission attempt settled.
   * @param succeeded Whether the backend accepted the draft's write action.
   */
  onSubmitted(draftId: ReviewId, succeeded: boolean): void;
};

/**
 * Exposes the complete discussion interface for one Snapshot.
 *
 * Draft accessors read the selected Profile's persisted work; write
 * operations validate availability and authorship, perform at most one HTTP
 * action after validation, and attempt to publish an accepted result into the
 * canonical query. Pending probes report in-flight work from the shared mutation
 * cache and the draft document, so Review and History see the same in-flight state.
 * The value stores no Thread data and must not be kept beyond its reactive owner.
 */
export type ThreadDiscussion = {
  /**
   * True only while the canonical Thread list is loaded and neither failing nor
   * refetching. Write controls must not treat stale visible data as available.
   */
  reviewAvailable: Accessor<boolean>;
  /**
   * Reads the selected Profile identity, or null when no Profile is selected.
   * Consumers use the returned identity only for presentation and draft matching;
   * a later Profile selection appears on the next call.
   *
   * # Returns
   *
   * - `number`: The live selected Profile identity used for authorship and draft
   *   filtering.
   * - `null`: No Profile is selected. Consumers keep discussions readable but
   *   expose no Profile-specific drafts or editing rights.
   */
  profileId(): number | null;
  /**
   * Reads the selected Profile for an immediate write. When absent, presents the
   * required-Profile notice and returns null, so callers must stop the action;
   * it does not select or create a Profile.
   *
   * # Returns
   *
   * - `StoredProfile`: The Profile whose identity must author this write.
   * - `null`: No Profile is selected. The requirement Toast has been presented,
   *   and the caller must stop before creating a draft or starting a mutation.
   */
  profileForWrite(): StoredProfile | null;
  /**
   * Draft identities disabled from submission start through settlement. The set
   * is shared by anchored and History surfaces through the draft authority.
   */
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  /**
   * Reports whether deletion of `commentId` is in the shared mutation cache.
   * Presentation calls it during rendering; completion returns false through a
   * later reactive render, and this probe never starts or cancels deletion.
   */
  commentDeletePending(commentId: ReviewId): boolean;
  /**
   * Reports whether the exact lifecycle `action` for `threadId` is in flight.
   * It observes actions started by every discussion instance and becomes false
   * after settlement; it does not infer pending state from the loaded Thread.
   *
   * @param threadId Thread identity carried by the shared mutation entry.
   * @param action Exact transition represented by the querying control.
   */
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  /**
   * Returns the selected Profile's sole persisted reply for `threadId`, or null.
   * Draft-document changes are visible on the next reactive call; no draft is
   * created merely by reading the permanent reply input.
   *
   * # Returns
   *
   * - The selected Profile's persisted reply draft for this Thread.
   * - `null`: No selected Profile has such a reply. The permanent reply input
   *   remains blank and no submission is available.
   */
  replyDraftForThread(threadId: ReviewId): ReviewDraft | null;
  /**
   * Returns the selected Profile's sole edit for `commentId`, asserting that it
   * belongs to `threadId`, or null. The lookup never opens an editor or changes
   * which Comment is being edited.
   *
   * @param threadId Current Thread expected to contain the Comment.
   * @param commentId Comment whose persisted replacement is requested.
   *
   * # Returns
   *
   * - The selected Profile's persisted edit draft for this Comment, already
   *   checked against `threadId`.
   * - `null`: No selected Profile has such an edit. The Comment renders in
   *   ordinary read mode instead of opening an editor.
   */
  editDraftForComment(
    threadId: ReviewId,
    commentId: ReviewId,
  ): ReviewDraft | null;
  /**
   * Accepts the complete text currently typed for `thread`; meaningful text is
   * added or replaces its reply draft and blank text removes it. Returns whether
   * persistence accepted the change, after which the updated accessor value feeds
   * the input; on false the input must restore its previously supplied body.
   *
   * @param thread Loaded Thread receiving the reply input.
   * @param body Complete current textarea value, including blank removal input.
   */
  updateReplyDraft(thread: ReviewThread, body: string): boolean;
  /**
   * Persists the complete replacement draft and returns whether it was accepted.
   * Callers feed the next draft-document value back to their editor and restore
   * the old value on false; this operation does not submit the draft.
   */
  replaceDraft(replacement: ReviewDraft): boolean;
  /**
   * Removes the persisted `draftId` and returns whether storage accepted removal.
   * Callers close or clear their editor only on true; in-flight drafts remain the
   * caller's responsibility and are not implicitly cancelled.
   */
  removeDraft(draftId: ReviewId): boolean;
  /**
   * Begins submission of the persisted `draftId` and performs its kind-specific
   * HTTP action only after availability, Profile, and draft validation. Returns true
   * when the backend accepts the write, after the local query update has been
   * attempted and successful settlement removes the draft. A query-update failure
   * is reported but does not turn an accepted write into failure. Returns false and
   * retains the draft for a rejected HTTP action or Profile conflict; unavailable
   * review data or Profile selection prevents a start. Concurrent calls for the same
   * draft are invalid. If a conflict-triggered corrective refetch itself fails, that
   * error propagates before submission settlement and the draft remains in flight.
   */
  submitDraft(draftId: ReviewId): Promise<boolean>;
  /**
   * Starts `action` for the loaded `thread` after optional delete confirmation.
   * It does nothing when review data or a writing Profile is unavailable, or
   * deletion is cancelled. An accepted update is published to the canonical
   * query before resolution; ordinary mutation failure is presented by the
   * query layer and resolves without a result.
   *
   * A typed conflict first refetches the authoritative review query. Failure of
   * that corrective refetch rejects this operation instead of being treated as
   * a handled mutation failure.
   *
   * @param thread Loaded canonical Thread receiving the transition.
   * @param action Exact lifecycle transition selected by the user.
   */
  changeThreadState(
    thread: ReviewThread,
    action: "resolve" | "reopen" | "delete",
  ): Promise<void>;
  /**
   * Opens editing for `comment` in `thread` by persisting its current body as an
   * edit draft. It does nothing when one already exists or prerequisites fail;
   * the editor appears only when that draft returns through the draft document.
   *
   * @param thread Loaded Thread containing the current Comment.
   * @param comment Authored nondeleted Comment whose body seeds the editor.
   */
  openEditDraft(thread: ReviewThread, comment: ReviewComment): void;
  /**
   * After explicit confirmation, deletes `comment` from `thread` as the selected
   * Profile and publishes the tombstone to the canonical query. It returns before
   * the HTTP action settles and does nothing when confirmation or prerequisites
   * fail. Typed conflicts start a corrective query refetch. Because this
   * callable deliberately does not expose the detached task, a failure of that
   * refetch occurs after return and cannot be awaited by its caller.
   *
   * @param thread Loaded Thread containing the target Comment.
   * @param comment Current Comment identity offered for deletion.
   */
  tombstoneComment(thread: ReviewThread, comment: ReviewComment): void;
};

/**
 * Creates one Snapshot-scoped discussion instance under the current owner.
 *
 * The caller must run within `ReviewDraftRoot` and the application query
 * client. Observers and derived state live in the calling component's
 * reactive owner and die with it.
 */
export function createThreadDiscussion(
  args: ThreadDiscussionArgs,
): ThreadDiscussion {
  const draftContext = useReviewDrafts();
  const queryClient = useQueryClient();
  const toast = useToasts();
  const review = createQuery(() => api.review.snapshot(args.snapshotId));
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

  const reviewAvailable = createMemo(
    () => review.data !== undefined && !review.isRefetching && !review.isError,
  );
  /**
   * Reads the live selected Profile identity without presenting a requirement.
   *
   * # Returns
   *
   * - `number`: The selected Profile identity used to filter persisted drafts
   *   and compare Comment authorship.
   * - `null`: No Profile is selected. Reactive draft indexing returns empty and
   *   discussions remain read-only.
   */
  const profileId = (): number | null => args.profile()?.id ?? null;

  /**
   * Return the selected Profile required for a browser-authored review write.
   *
   * # Usage
   *
   * Every write gate calls this before constructing mutation input. When no
   * Profile is selected, the function appends one five-second transient Toast
   * directing the user to the existing Profile control and returns null. It
   * never opens that control, selects a Profile, or starts a mutation.
   *
   * # Returns
   *
   * - `StoredProfile`: The live selected Profile that must author the pending
   *   browser write.
   * - `null`: No Profile is selected. The function has shown the requirement
   *   Toast, and the write gate must return without changing drafts or backend
   *   state.
   */
  function profileForWrite(): StoredProfile | null {
    const profile = args.profile();
    if (profile === null) {
      toast.showTransient(
        "Profile required",
        "Select or create a Profile from the Profile control to write review Comments.",
        5000,
      );
    }
    return profile;
  }

  // Pending probes read the shared mutation cache, not this instance's
  // observers, so concurrent discussion instances (the anchored host and
  // History) see one another's in-flight work and disable the same controls.
  /**
   * Reads one required review identity from TanStack's untyped mutation state.
   *
   * This is the validation boundary between TanStack's `unknown` variables and
   * pending-state selectors. It returns only an identity accepted by
   * `ReviewIdSchema`.
   *
   * @param variables Mutation variables whose shape was established by the API action.
   * @param key Identity field required by the pending-state probe.
   *
   * # Failures
   *
   * A non-object value or missing requested key fails the mutation-variable
   * invariant. A present value with an invalid review identity throws from
   * schema parsing.
   */
  function reviewIdVariable(
    variables: unknown,
    key: "commentId" | "threadId",
  ): ReviewId {
    assert(
      typeof variables === "object" && variables !== null && key in variables,
      `Pending review mutation omitted ${key}.`,
    );
    return ReviewIdSchema.parse(Reflect.get(variables, key));
  }

  const pendingCommentDeletes = useMutationState(() => ({
    filters: {
      mutationKey: api.review.comment.delete().mutationKey,
      status: "pending",
    },
    select: (mutation) =>
      reviewIdVariable(mutation.state.variables, "commentId"),
  }));
  const pendingThreadStates = useMutationState(() => ({
    filters: {
      mutationKey: ["review", "thread"],
      status: "pending",
      // Creation has no Thread identity until the backend returns it. These
      // controls observe only lifecycle writes against existing Threads.
      predicate: (mutation) => {
        const action = mutation.options.mutationKey?.[2];
        return (
          action === "resolve" || action === "reopen" || action === "delete"
        );
      },
    },
    select: (mutation) => ({
      action: mutation.options.mutationKey?.[2],
      threadId: reviewIdVariable(mutation.state.variables, "threadId"),
    }),
  }));

  /** Report whether the exact Comment deletion is crossing the wire. */
  function commentDeletePending(commentId: ReviewId): boolean {
    return pendingCommentDeletes().includes(commentId);
  }

  /**
   * Reports whether the exact Thread lifecycle action is crossing the wire.
   *
   * @param threadId Thread identity carried by the HTTP mutation variables.
   * @param action Specific state transition whose control should be disabled.
   */
  function threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean {
    return pendingThreadStates().some(
      (pending) => pending.action === action && pending.threadId === threadId,
    );
  }

  /**
   * Settles one submission and lets the host react before the draft drops.
   *
   * @param draftId Persisted input whose submission just settled.
   * @param succeeded Whether the backend accepted the draft's write action.
   */
  function settleSubmission(draftId: ReviewId, succeeded: boolean): void {
    batch(() => {
      args.onSubmitted(draftId, succeeded);
      draftContext.endSubmission(draftId, succeeded);
    });
  }

  const profileDrafts = createMemo(() => {
    const currentProfileId = profileId();
    const replies = new Map<ReviewId, ReviewDraft>();
    const edits = new Map<ReviewId, ReviewDraft>();
    if (currentProfileId === null) return { replies, edits };
    for (const draft of draftContext.drafts()) {
      if (draft.profile_id !== currentProfileId) continue;
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

  /**
   * Returns the selected Profile's sole reply draft for one current Thread.
   *
   * # Returns
   *
   * - The indexed reply draft for `threadId` under the selected Profile.
   * - `null`: No Profile is selected or that Profile has no reply for the
   *   Thread. The caller keeps the permanent reply input blank.
   */
  function replyDraftForThread(threadId: ReviewId): ReviewDraft | null {
    return profileDrafts().replies.get(threadId) ?? null;
  }

  /**
   * Returns the selected Profile's sole edit draft for one current Comment.
   *
   * @param threadId Current Thread expected to contain the Comment.
   * @param commentId Comment whose replacement input is requested.
   *
   * # Returns
   *
   * - The indexed edit draft for `commentId`, validated to belong to
   *   `threadId`.
   * - `null`: No Profile is selected or that Profile has no edit for the
   *   Comment. The caller renders the Comment outside edit mode.
   */
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

  /**
   * Persists meaningful text from one Thread's permanent reply input.
   *
   * A selected Profile is required; if absent, the shared Profile Toast is
   * shown and the function returns false without changing storage. Blank input
   * removes an existing reply draft and otherwise needs no write. Meaningful
   * input replaces the selected Profile's existing draft or creates a fresh
   * one. The result reports whether the required storage transition was
   * accepted, so the input caller can retain its prior value after failure.
   *
   * @param thread Loaded Thread receiving the reply.
   * @param body Complete current input text, where blank text removes the draft.
   */
  function updateReplyDraft(thread: ReviewThread, body: string): boolean {
    const profile = profileForWrite();
    if (profile === null) return false;
    const existing = replyDraftForThread(thread.thread_id);
    if (body.trim().length === 0) {
      return existing === null || draftContext.remove(existing.draft_id);
    }
    if (existing !== null) {
      return draftContext.replace({
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

  /**
   * Publish one committed Thread without letting an older refetch erase it.
   *
   * @param thread Complete Thread returned by the successful create mutation.
   *
   * # Usage
   *
   * `submitDraft` calls this after the create mutation succeeds. It cancels the
   * Snapshot query before writing. If the cache already contains the Thread,
   * the equal-or-newer discussion revision wins; a disposed cache remains
   * absent instead of being reconstructed here.
   *
   * # Failures
   *
   * Query cancellation errors propagate. Duplicate cached identities violate
   * the canonical-query contract and throw.
   */
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

  /**
   * Apply one contiguous action result to its authoritative loaded Thread.
   *
   * @param update Accepted backend action result for one loaded Thread.
   *
   * # Usage
   *
   * Existing-Thread mutations call this only with the backend's accepted
   * update. It cancels an older refetch first. A missing Thread or skipped
   * discussion revision triggers a complete query refetch instead of publishing
   * a partial history. A changed existing Comment must match one loaded identity.
   *
   * If disposal removed the query data, the function returns without creating
   * another cache entry.
   *
   * # Failures
   *
   * Query cancellation or corrective refetch errors propagate. Duplicate
   * Thread or Comment identities throw because the loaded query is not
   * authoritative enough to update safely.
   */
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

  /**
   * Refresh stale Thread state after a conflict invalidates the loaded query.
   *
   * @param error Typed backend rejection from one review mutation.
   *
   * # Usage
   *
   * Mutation failure handlers pass only a parsed `ReviewRequestError`. Revision
   * and state conflicts, or missing Thread and Comment identities, refetch the
   * complete Snapshot query before the caller settles. Other typed failures do
   * not imply stale query state and require no read.
   *
   * # Failures
   *
   * A corrective refetch failure propagates to the mutation handler.
   */
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

  /**
   * Submit one persisted draft through its operation-specific HTTP action.
   *
   * @param draftId Persisted draft identity selected by the review UI.
   *
   * # Usage
   *
   * Review surfaces pass an identity from the shared draft document. Submission
   * starts only when the Snapshot query and selected Profile are available.
   * `beginSubmission` freezes that exact draft until this function settles it.
   *
   * A backend rejection retains the editable draft and returns false. Backend
   * acceptance returns true and removes the draft even if local query
   * publication fails; that later failure is shown as a Toast because the
   * server write cannot be undone. Settlement calls `onSubmitted` and updates
   * the draft document in one Solid batch.
   *
   * # Failures
   *
   * Missing, duplicate, or already-submitting draft state throws before a start.
   * A cross-Snapshot new-Thread draft throws after the start and remains in
   * flight. HTTP failures are presented by the shared query boundary and return
   * false. A corrective refetch failure also propagates before settlement,
   * leaving the draft marked in flight. Result-shape and query-publication
   * failures after backend acceptance show a Toast, settle success, and return
   * true.
   */
  async function submitDraft(draftId: ReviewId): Promise<boolean> {
    if (draftContext.error() !== null || !reviewAvailable()) return false;
    const profile = profileForWrite();
    if (profile === null) return false;
    const command = draftContext.beginSubmission(draftId);
    if (command.kind === "new-thread") {
      assert(
        command.snapshot_id === args.snapshotId,
        "A new-Thread draft cannot be submitted through another Snapshot.",
      );
    }
    if (command.profile_id !== profile.id) {
      settleSubmission(command.draft_id, false);
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
              snapshotId: args.snapshotId,
              body: {
                profile_id: profile.id,
                target: command.target,
                body: command.body,
              },
            })
          : command.kind === "reply"
            ? await addComment.mutateAsync({
                snapshotId: args.snapshotId,
                threadId: command.thread_id,
                body: {
                  profile_id: profile.id,
                  body: command.body,
                  attention: "alert",
                },
              })
            : await editComment.mutateAsync({
                snapshotId: args.snapshotId,
                threadId: command.thread_id,
                commentId: command.comment_id,
                body: { profile_id: profile.id, body: command.body },
              });
    } catch (error) {
      if (error instanceof ReviewRequestError) {
        await refreshReviewAfterConflict(error);
      }
      // QueryProvider presented the failure; retain the draft.
      settleSubmission(command.draft_id, false);
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
    settleSubmission(command.draft_id, true);
    return true;
  }

  /**
   * Applies one explicit Thread state action to the canonical review query.
   *
   * Unavailable review data shows a Toast; a missing Profile shows the shared
   * Profile Toast; cancelling delete leaves both backend and query unchanged.
   * An accepted mutation publishes the returned Thread update before this
   * promise resolves. Ordinary mutation failure is already presented by the
   * query layer and resolves without changing local state.
   *
   * @param thread Loaded canonical Thread receiving the transition.
   * @param action Exact transition selected by the user.
   *
   * # Failures
   *
   * A typed conflict triggers a corrective refetch. If that refetch fails, its
   * error propagates from this promise rather than being handled as the original
   * mutation failure. Canonical-query invariant failures during publication
   * also propagate.
   */
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
        snapshotId: args.snapshotId,
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

  /**
   * Activates one persisted replacement draft for an authored current Comment.
   *
   * Unavailable review data or Profile selection presents the corresponding
   * Toast and performs no storage write. The Comment must belong to the selected
   * Profile and retain a body. An existing edit draft is a no-op; otherwise the
   * current body seeds a fresh draft, and the editor appears only if storage
   * accepts it and publishes it through the shared document.
   *
   * @param thread Loaded Thread that contains the Comment.
   * @param comment Current authored Comment whose body seeds the draft.
   *
   * # Failures
   *
   * Wrong authorship or a deleted Comment throws. Storage rejection returns
   * from this void action without opening an editor and records the storage
   * failure in the shared draft document.
   */
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
    assert(
      comment.author.profile_id === profile.id && comment.body !== null,
      "Only the original Profile may edit a current Comment.",
    );
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

  /**
   * Tombstones one current Comment as the selected acting Profile.
   *
   * Unavailable review data, a missing Profile, or cancelled confirmation is a
   * no-op after presenting any applicable Toast. An accepted action starts a
   * detached mutation, returns immediately, and later publishes the returned
   * Thread update. Ordinary mutation failure is presented by the query layer.
   *
   * @param thread Loaded Thread containing the target Comment.
   * @param comment Current Comment identity to delete after confirmation.
   *
   * # Failures
   *
   * Typed conflicts start a corrective refetch. If that refetch rejects, the
   * detached task rejects after this function has returned; callers cannot
   * await or contain that failure through this void interface. Canonical-query
   * invariant failures have the same detached lifetime.
   */
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
          snapshotId: args.snapshotId,
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

  return {
    reviewAvailable,
    profileId,
    profileForWrite,
    submittingDraftIds: draftContext.submittingDraftIds,
    commentDeletePending,
    threadStatePending,
    replyDraftForThread,
    editDraftForComment,
    updateReplyDraft,
    replaceDraft: draftContext.replace,
    removeDraft: draftContext.remove,
    submitDraft,
    changeThreadState,
    openEditDraft,
    tombstoneComment,
  };
}
