/**
 * Implements Thread discussion operations against their shared authorities.
 *
 * `createThreadDiscussion` is the public interface. Every operation here
 * works on exactly three application-level authorities — the persisted
 * review-draft document, the canonical Snapshot query cache, and the TanStack
 * mutation cache — so any review surface (the anchored panel host and History
 * alike) creates its own instance instead of receiving a pile of callbacks.
 * Concurrent instances stay consistent because none of them owns state: draft
 * reads and writes go through the application draft document, Thread updates
 * publish into the one canonical query entry, and pending probes read the
 * shared mutation cache rather than per-instance observer copies.
 *
 * The factory owns its mutation observers, its canonical-query observer (the
 * cache deduplicates it against every other observer of the same Snapshot),
 * and the publication protocol that keeps an in-flight refetch from reverting
 * a committed write. It must not own presentation: no DOM, no anchored-UI
 * state, no History layout. Its single outward call is the required
 * `onSubmitted` construction behavior, batched with submission settlement so
 * a host can close UI that references the settled draft before the draft
 * document drops it.
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
 * Supplies one discussion instance's identity and its single host reaction.
 *
 * `snapshotId` scopes every operation to one exact Snapshot. `profile` is the
 * live selected Profile. `onSubmitted` runs batched with submission
 * settlement for every submitted draft, before the draft document drops a
 * succeeded draft; hosts without settlement UI pass an explicitly empty
 * reaction.
 */
export type ThreadDiscussionArgs = {
  snapshotId: ReviewId;
  profile: Accessor<StoredProfile | null>;
  onSubmitted(draftId: ReviewId, succeeded: boolean): void;
};

/**
 * Exposes the complete discussion operation surface for one Snapshot.
 *
 * Draft accessors read the selected Profile's persisted work; write
 * operations validate availability and authorship, perform their single HTTP
 * action, and publish the result into the canonical query. Pending probes
 * report in-flight work from the shared mutation cache and the draft
 * document, so every surface sees the same in-flight state. The value stores
 * no Thread data and must not be kept beyond its reactive owner.
 */
export type ThreadDiscussion = {
  reviewAvailable: Accessor<boolean>;
  profileId(): number | null;
  profileForWrite(): StoredProfile | null;
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  commentDeletePending(commentId: ReviewId): boolean;
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  replyDraftForThread(threadId: ReviewId): ReviewDraft | null;
  editDraftForComment(
    threadId: ReviewId,
    commentId: ReviewId,
  ): ReviewDraft | null;
  updateReplyDraft(thread: ReviewThread, body: string): boolean;
  replaceDraft(replacement: ReviewDraft): boolean;
  removeDraft(draftId: ReviewId): boolean;
  submitDraft(draftId: ReviewId): Promise<boolean>;
  changeThreadState(
    thread: ReviewThread,
    action: "resolve" | "reopen" | "delete",
  ): Promise<void>;
  openEditDraft(thread: ReviewThread, comment: ReviewComment): void;
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
  const profileId = (): number | null => args.profile()?.id ?? null;

  /** Returns the selected Profile or verbally directs the user to its control. */
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
  const pendingCommentDeletes = useMutationState(() => ({
    filters: {
      mutationKey: api.review.comment.delete().mutationKey,
      status: "pending",
    },
    select: (mutation) =>
      (mutation.state.variables as { commentId: ReviewId } | undefined)
        ?.commentId,
  }));
  const pendingThreadStates = useMutationState(() => ({
    filters: { mutationKey: ["review", "thread"], status: "pending" },
    select: (mutation) => ({
      action: mutation.options.mutationKey?.[2],
      threadId: (mutation.state.variables as { threadId: ReviewId } | undefined)
        ?.threadId,
    }),
  }));

  /** Report whether the exact Comment deletion is crossing the wire. */
  function commentDeletePending(commentId: ReviewId): boolean {
    return pendingCommentDeletes().includes(commentId);
  }

  /** Report whether the exact Thread lifecycle action is crossing the wire. */
  function threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean {
    return pendingThreadStates().some(
      (pending) => pending.action === action && pending.threadId === threadId,
    );
  }

  /** Settles one submission and lets the host react before the draft drops. */
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
