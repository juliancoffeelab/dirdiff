import { batch, createMemo, type Accessor } from "solid-js";
import {
  createInfiniteQuery,
  createMutation,
  useMutationState,
} from "@tanstack/solid-query";
import {
  api,
  reviewThreadList,
  ReviewIdSchema,
  ReviewRequestError,
  type AcceptedReviewActivity,
  type ReviewComment,
  type ReviewId,
  type ReviewThread,
  type ReviewThreadUpdate,
} from "../../api/api";
import { useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { StoredProfile } from "../Profile";
import { newReviewId, useReviewDrafts, type ReviewDraft } from "./drafts";

export type ThreadDiscussionArgs = {
  snapshotId: ReviewId;
  profile: Accessor<StoredProfile | null>;
  acceptedActivities: Accessor<readonly AcceptedReviewActivity[]>;
  onAcceptedActivity(activity: AcceptedReviewActivity): void;
  onSubmitted(draftId: ReviewId, succeeded: boolean): void;
};

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

export function createThreadDiscussion(
  args: ThreadDiscussionArgs,
): ThreadDiscussion {
  const draftContext = useReviewDrafts();
  const toast = useToasts();
  const review = createInfiniteQuery(() =>
    api.review.snapshot(args.snapshotId, toast.showError),
  );
  const reviewThreads = createMemo(() =>
    reviewThreadList(review.data, args.acceptedActivities()),
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

  const reviewAvailable = createMemo(
    () => review.data !== undefined && !review.isError,
  );
  const profileId = (): number | null => args.profile()?.id ?? null;

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

  function commentDeletePending(commentId: ReviewId): boolean {
    return pendingCommentDeletes().includes(commentId);
  }

  function threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean {
    return pendingThreadStates().some(
      (pending) => pending.action === action && pending.threadId === threadId,
    );
  }

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
    if (currentProfileId === null) {
      return { replies, edits };
    }
    for (const draft of draftContext.drafts()) {
      if (draft.profile_id !== currentProfileId) {
        continue;
      }
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

  function replyDraftForThread(threadId: ReviewId): ReviewDraft | null {
    return profileDrafts().replies.get(threadId) ?? null;
  }

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

  function updateReplyDraft(thread: ReviewThread, body: string): boolean {
    const profile = profileForWrite();
    if (profile === null) {
      return false;
    }
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

  async function submitDraft(draftId: ReviewId): Promise<boolean> {
    if (draftContext.error() !== null || !reviewAvailable()) {
      return false;
    }
    const profile = profileForWrite();
    if (profile === null) {
      return false;
    }
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
      const addressedSnapshotId =
        command.kind === "new-thread"
          ? args.snapshotId
          : expect(
              reviewThreads().find(
                (candidate) => candidate.thread_id === command.thread_id,
              ),
              "A submitted Thread draft requires its loaded discussion.",
            ).snapshot_id;
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
                snapshotId: addressedSnapshotId,
                threadId: command.thread_id,
                body: {
                  profile_id: profile.id,
                  body: command.body,
                  attention: "alert",
                },
              })
            : await editComment.mutateAsync({
                snapshotId: addressedSnapshotId,
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
        args.onAcceptedActivity({ kind: "created", thread });
      } else {
        assert(!("origin_target" in thread));
        args.onAcceptedActivity({ kind: "updated", update: thread });
      }
    } catch (error) {
      toast.showError("Could not refresh review Thread", error);
    }
    settleSubmission(command.draft_id, true);
    return true;
  }

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
    if (profile === null) {
      return;
    }
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
    let updated: ReviewThreadUpdate;
    try {
      updated = await mutation.mutateAsync({
        snapshotId: thread.snapshot_id,
        threadId: thread.thread_id,
        body: {
          profile_id: profile.id,
        },
      });
    } catch (error) {
      if (error instanceof ReviewRequestError) {
        await refreshReviewAfterConflict(error);
      }
      // QueryProvider presented the mutation failure through its metadata.
      return;
    }
    args.onAcceptedActivity({ kind: "updated", update: updated });
  }

  function openEditDraft(thread: ReviewThread, comment: ReviewComment): void {
    if (!reviewAvailable()) {
      toast.showError(
        "Review Threads unavailable",
        new Error("Review Threads are loading or failed to refresh."),
      );
      return;
    }
    const profile = profileForWrite();
    if (profile === null) {
      return;
    }
    assert(
      comment.author.profile_id === profile.id && comment.body !== null,
      "Only the original Profile may edit a current Comment.",
    );
    if (editDraftForComment(thread.thread_id, comment.comment_id) !== null) {
      return;
    }
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
    if (profile === null) {
      return;
    }
    if (!window.confirm("Delete this Comment?")) {
      return;
    }
    void (async () => {
      let updated: ReviewThreadUpdate;
      try {
        updated = await deleteComment.mutateAsync({
          snapshotId: thread.snapshot_id,
          threadId: thread.thread_id,
          commentId: comment.comment_id,
          body: {
            profile_id: profile.id,
          },
        });
      } catch (error) {
        if (error instanceof ReviewRequestError) {
          await refreshReviewAfterConflict(error);
          return;
        }
        // QueryProvider already presented this mutation through its metadata.
        return;
      }
      args.onAcceptedActivity({ kind: "updated", update: updated });
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
