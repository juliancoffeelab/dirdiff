/**
 * Renders review discussion presentation: Comment input, panels, and cards.
 *
 * The module exports CommentInput, InlineThreadPanel, ThreadCard, and
 * ReviewCommentView. Every component is pure presentation: all data and
 * operations arrive through props, no context is consumed, and no query,
 * mutation, draft write, or navigation originates here. ThreadCard is the
 * shared discussion card rendered by both the anchored panel and History.
 */
import {
  For,
  Show,
  createMemo,
  createSignal,
  type Accessor,
  type JSX,
} from "solid-js";
import { Portal } from "solid-js/web";
import { LocateFixed, Pencil, Trash2, TriangleAlert } from "lucide-solid";
import type { ReviewComment, ReviewId, ReviewThread } from "../../api/api";
import { ErrorPanel } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { NewThreadDraft, ReviewDraft } from "./drafts";
import type { ActiveCommentInput, ActiveThreadPanel } from "./Review";

/** Renders the one active new-Thread draft at its final inline mount. */
export function CommentInput(props: {
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
export function InlineThreadPanel(props: {
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
export function ThreadCard(props: {
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
                : props.thread.outdated_reason === "bay_not_found"
                  ? "The reviewed part of the file is gone from this Snapshot."
                  : "The reviewed code changed after this Thread was created."
            }
          >
            <TriangleAlert aria-hidden="true" />
          </span>
        </Show>
      </>
    );
  }
  /** Estimates the rendered height for pre-render History scroll geometry.
   *
   * History Threads render lazily under content-visibility; before first
   * render the browser uses the intrinsic estimate, and a constant 120px is
   * about five times short for an expanded discussion, which made History
   * scrolling jump as real heights replaced estimates. The model prices the
   * chrome, the code excerpt, and each Comment by its body length; `auto`
   * sizing replaces it with the real height after first render.
   */
  function intrinsicHeightEstimate(): number {
    if (!props.expanded) {
      return 120;
    }
    // The History panel is ~320px wide, so bodies wrap at roughly 40
    // characters per 18px line — length/2.5 approximates the wrapped text
    // height; constants price the comment chrome and the code excerpt.
    const commentPixels = props.thread.comments.reduce(
      (total, comment) => total + 90 + (comment.body ?? "").length / 2.5,
      0,
    );
    return Math.round(
      140 + (props.thread.original_excerpt !== null ? 240 : 0) + commentPixels,
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
      style={
        props.navigation.kind === "history"
          ? {
              "contain-intrinsic-height": `auto ${intrinsicHeightEstimate()}px`,
            }
          : undefined
      }
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
