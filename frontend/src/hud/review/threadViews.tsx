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
import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";
import {
  attachmentUrl,
  threadOutdated,
  type ReviewAuthor,
  type ReviewComment,
  type ReviewId,
  type ReviewThread,
} from "../../api/api";
import { ErrorPanel } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { NewThreadDraft, ReviewDraft } from "./drafts";
import type { ActiveCommentInput, ActiveThreadPanel } from "./Review";

type TextareaEvent<E extends Event> = E & {
  currentTarget: HTMLTextAreaElement;
};

// Pasting or dropping files into a Comment textarea uploads them and inserts
// their image references. The upload finishes after the user may have typed,
// so the references go in at the selection as it stands then, and reach the
// draft through the textarea's own input handler, the one path that changes a
// body.
function createImageInsertion(
  uploadImages: (files: readonly File[]) => Promise<string | null>,
): {
  onPaste(event: TextareaEvent<ClipboardEvent>): void;
  onDragOver(event: TextareaEvent<DragEvent>): void;
  onDrop(event: TextareaEvent<DragEvent>): void;
} {
  function insert(
    event: TextareaEvent<ClipboardEvent | DragEvent>,
    files: FileList | undefined,
  ): void {
    if (files === undefined || files.length === 0) {
      return;
    }
    event.preventDefault();
    const textarea = event.currentTarget;
    // A throw here reaches the unhandled-rejection Toast.
    void uploadImages([...files]).then((markdown) => {
      if (markdown === null) {
        return;
      }
      if (!textarea.isConnected || textarea.disabled) {
        throw new Error(
          `The Comment input closed during the image upload. Paste this where the image belongs: ${markdown}`,
        );
      }
      textarea.setRangeText(
        markdown,
        textarea.selectionStart,
        textarea.selectionEnd,
        "end",
      );
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  return {
    onPaste(event) {
      insert(event, event.clipboardData?.files);
    },
    onDragOver(event) {
      // Without this the browser does not deliver a file drop to the textarea
      // and opens the file instead.
      if (event.dataTransfer?.types.includes("Files") === true) {
        event.preventDefault();
      }
    },
    onDrop(event) {
      insert(event, event.dataTransfer?.files);
    },
  };
}

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
  uploadImages(files: readonly File[]): Promise<string | null>;
}): JSX.Element {
  const draft = createMemo(() => {
    const active = props.active();
    if (active === null) {
      return null;
    }
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
  const imageInsertion = createImageInsertion(props.uploadImages);
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
                onPaste={imageInsertion.onPaste}
                onDragOver={imageInsertion.onDragOver}
                onDrop={imageInsertion.onDrop}
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
                  if (current !== null) {
                    props.onDiscard(current.draft_id);
                  }
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
  uploadImages(files: readonly File[]): Promise<string | null>;
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
        const mount = expect(
          active.anchor.codeCell.parentElement,
          "A code cell requires its row part.",
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
                        if (!multiple()) {
                          return;
                        }
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
                      uploadImages={props.uploadImages}
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

export function ThreadCard(props: {
  thread: ReviewThread;
  expanded: boolean;
  navigation:
    | {
        kind: "inline";
      }
    | {
        kind: "history";
        viewable: boolean;
        advisory: string | null;
        unavailableNote: string | null;
        onView(): void;
      };
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
  uploadImages(files: readonly File[]): Promise<string | null>;
}): JSX.Element {
  const imageInsertion = createImageInsertion(props.uploadImages);
  const origin = props.thread.origin_target;
  // A review-scoped origin has no File; its heading shows a fixed label.
  const excerptPath =
    origin.kind === "review"
      ? null
      : expect(
          origin.side === "left"
            ? origin.file.left_path
            : origin.file.right_path,
          "A review origin requires its selected-side File path.",
        );
  const excerptFileName =
    excerptPath === null
      ? "Whole review"
      : excerptPath.slice(excerptPath.lastIndexOf("/") + 1);
  // The excerpt travels inside a text origin, so the three sites that render
  // it need this arm; a retained File-level origin carries none.
  const textOrigin = origin.kind === "text" ? origin : null;
  const firstComment = () =>
    expect(
      props.thread.comments[0],
      "A review Thread requires its first Comment.",
    );
  function placementNote(): string | null {
    switch (props.thread.placement.kind) {
      case "region-kept":
      case "whole-file":
      case "review-scope":
        return null;
      case "file-absent":
        return "The reviewed file is not present in this Snapshot.";
      case "file-unreadable":
        return "The reviewed file could not be read in this Snapshot.";
      case "bay-lost":
      case "side-lost":
        return "The reviewed part of the file is gone from this Snapshot.";
      case "region-changed":
      case "region-lost":
        return "The reviewed code changed after this Thread was created.";
    }
  }
  function unlocatedNote(): string | null {
    if (
      props.navigation.kind === "history" &&
      props.navigation.unavailableNote !== null
    ) {
      return props.navigation.unavailableNote;
    }
    const kind = props.thread.placement.kind;
    return kind === "file-absent" || kind === "file-unreadable"
      ? expect(placementNote(), "An unlocated placement states its reason.")
      : null;
  }
  function authoredByCurrentProfile(comment: ReviewComment): boolean {
    const author = comment.author;
    return author.profile_id === props.profileId;
  }
  function ThreadHeading(): JSX.Element {
    return (
      <>
        <span class="review-thread-state-dot" aria-hidden="true" />
        <AuthorName author={firstComment().author} />
        <span class="review-thread-location" title={excerptPath ?? undefined}>
          {excerptFileName}
          <Show when={textOrigin} keyed>
            {(text) => <> · L{text.excerpt.selected_start_line}</>}
          </Show>
        </span>
        <Show when={placementNote()}>
          {(note) => (
            <span class="review-warning" title={note()}>
              <TriangleAlert aria-hidden="true" />
            </span>
          )}
        </Show>
      </>
    );
  }
  function intrinsicHeightEstimate(): number {
    if (!props.expanded) {
      return 120;
    }
    // The History panel is ~320px wide, so bodies wrap at roughly 40
    // characters per 18px line. Length/2.5 approximates the wrapped text
    // height; constants price the comment chrome and the code excerpt.
    const commentPixels = props.thread.comments.reduce(
      (total, comment) => total + 90 + (comment.body ?? "").length / 2.5,
      0,
    );
    return Math.round(140 + (textOrigin !== null ? 240 : 0) + commentPixels);
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
        "review-thread-outdated": threadOutdated(props.thread),
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
        {/* A whole-review Thread names no code, so it has no go-to at all. */}
        <Show
          when={
            props.navigation.kind === "history" && origin.kind !== "review"
              ? props.navigation
              : undefined
          }
          keyed
        >
          {(navigation) => (
            <Show
              when={unlocatedNote()}
              fallback={
                <button
                  type="button"
                  class="review-view review-thread-goto"
                  classList={{
                    "review-thread-goto-advisory": navigation.advisory !== null,
                  }}
                  title={
                    navigation.viewable
                      ? (navigation.advisory ?? "Go to reviewed code")
                      : "The reviewed File is not loaded."
                  }
                  aria-label={
                    navigation.viewable
                      ? (navigation.advisory ?? "Go to reviewed code")
                      : "Go to code unavailable: the reviewed File is not loaded."
                  }
                  disabled={!navigation.viewable}
                  onClick={navigation.onView}
                >
                  <LocateFixed aria-hidden="true" />
                </button>
              }
            >
              {(note) => (
                <span class="review-view-unavailable" title={note()}>
                  <button
                    type="button"
                    class="review-view review-thread-goto"
                    disabled
                    aria-label={`Go to code unavailable: ${note()}`}
                  >
                    <LocateFixed aria-hidden="true" />
                  </button>
                </span>
              )}
            </Show>
          )}
        </Show>
      </header>
      <Show when={props.expanded}>
        <Show when={unlocatedNote()}>
          {(note) => <p class="review-no-location">{note()}</p>}
        </Show>
        <Show when={textOrigin?.excerpt} keyed>
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
                  snapshotId={props.thread.snapshot_id}
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
                  uploadImages={props.uploadImages}
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
              onPaste={imageInsertion.onPaste}
              onDragOver={imageInsertion.onDragOver}
              onDrop={imageInsertion.onDrop}
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

function AuthorName(props: { author: ReviewAuthor }): JSX.Element {
  const agentLabel = (): string | null => {
    if (!props.author.is_agent) {
      return null;
    }
    const facts = [props.author.model, props.author.harness].filter(
      (fact) => fact !== null,
    );
    return facts.length === 0 ? "agent" : facts.join(" · ");
  };
  return (
    <strong
      title={
        agentLabel() === null
          ? props.author.display_name
          : `${props.author.display_name} · ${agentLabel()}`
      }
    >
      {props.author.display_name}
      <Show when={agentLabel()}>
        {(label) => <span class="review-author-agent"> · {label()}</span>}
      </Show>
    </strong>
  );
}

type MarkdownNode = ReturnType<typeof fromMarkdown>["children"][number];

// Bodies are built node by node from the parsed tree and never pass through
// HTML, so raw HTML stays literal text and no element is created that the
// allow-list below does not name.
function CommentBody(props: {
  body: string;
  snapshotId: ReviewId;
}): JSX.Element {
  const tree = createMemo(() =>
    fromMarkdown(props.body, {
      extensions: [gfm()],
      mdastExtensions: [gfmFromMarkdown()],
    }),
  );
  // Markdown from agents is open-ended: a node outside the allow-list shows
  // its source text, because dropping it would hide what someone wrote.
  function source(node: MarkdownNode): string {
    const position = expect(node.position, "Parsed Markdown has positions.");
    return props.body.slice(
      expect(position.start.offset, "Parsed Markdown has offsets."),
      expect(position.end.offset, "Parsed Markdown has offsets."),
    );
  }
  function block(node: MarkdownNode): JSX.Element {
    switch (node.type) {
      case "paragraph":
        return <p>{node.children.map((child) => inline(child, false))}</p>;
      // A real heading element would enter the page's heading outline.
      case "heading":
        return (
          <p class="review-comment-body-heading">
            {node.children.map((child) => inline(child, false))}
          </p>
        );
      case "thematicBreak":
        return <hr />;
      case "blockquote":
        return <blockquote>{node.children.map(block)}</blockquote>;
      case "code":
        return (
          <pre class="review-comment-body-code">
            <code>{node.value}</code>
          </pre>
        );
      case "list": {
        const items = node.children.map((item) =>
          typeof item.checked === "boolean" ? (
            <li class="review-comment-body-task">
              <input type="checkbox" checked={item.checked} disabled />
              {item.children.map(block)}
            </li>
          ) : (
            <li>{item.children.map(block)}</li>
          ),
        );
        return node.ordered === true ? (
          <ol start={node.start ?? undefined}>{items}</ol>
        ) : (
          <ul>{items}</ul>
        );
      }
      case "table": {
        const head = expect(node.children[0], "A table has a header row.");
        const body = node.children.slice(1);
        const columns = head.children.length;
        // The tree keeps each row's own cell count; GFM makes the header's
        // count authoritative, padding short rows and dropping surplus cells.
        const row = (
          tableRow: (typeof node.children)[number],
          header: boolean,
        ) => (
          <tr>
            {Array.from({ length: columns }, (_, column) => {
              const align = node.align?.[column] ?? undefined;
              const content = tableRow.children[column]?.children.map((child) =>
                inline(child, false),
              );
              return header ? (
                <th style={{ "text-align": align }}>{content}</th>
              ) : (
                <td style={{ "text-align": align }}>{content}</td>
              );
            })}
          </tr>
        );
        return (
          <div class="review-comment-body-table">
            <table>
              <thead>{row(head, true)}</thead>
              <tbody>{body.map((tableRow) => row(tableRow, false))}</tbody>
            </table>
          </div>
        );
      }
      default:
        return <p class="review-comment-body-source">{source(node)}</p>;
    }
  }
  // `linked` is true inside a rendered link: an image there is that link's
  // content, because a second link around it would nest one anchor in another.
  function inline(node: MarkdownNode, linked: boolean): JSX.Element {
    switch (node.type) {
      // A line ending inside a paragraph breaks the line, as in GitHub
      // comments, so plain Comments keep the layout they had before Markdown.
      // The parser leaves CR, CRLF and LF endings in text values as written.
      case "text":
        return node.value
          .split(/\r\n|\r|\n/)
          .flatMap((line, index) => (index === 0 ? [line] : [<br />, line]));
      case "emphasis":
        return <em>{node.children.map((child) => inline(child, linked))}</em>;
      case "strong":
        return (
          <strong>{node.children.map((child) => inline(child, linked))}</strong>
        );
      case "delete":
        return <del>{node.children.map((child) => inline(child, linked))}</del>;
      case "inlineCode":
        return <code>{node.value}</code>;
      case "break":
        return <br />;
      case "link": {
        if (!/^(?:https?|mailto):/i.test(node.url)) {
          return node.children.map((child) => inline(child, linked));
        }
        // Whether the rendered content gives the link an accessible name: a
        // link holding only images without a caption has none, so it is named
        // by its destination instead.
        const named = (child: MarkdownNode): boolean => {
          switch (child.type) {
            case "text":
            case "inlineCode":
              return child.value.trim() !== "";
            case "break":
              return false;
            case "image":
              return (
                !/^attachment:[0-9a-f]{64}$/.test(child.url) ||
                (child.alt ?? "").trim() !== ""
              );
            case "emphasis":
            case "strong":
            case "delete":
              return child.children.some(named);
            default:
              return true;
          }
        };
        return (
          <a
            href={node.url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={node.children.some(named) ? undefined : node.url}
          >
            {node.children.map((child) => inline(child, true))}
          </a>
        );
      }
      // Only an image uploaded to this Room is fetched: any other URL an agent
      // wrote shows as its source, because it must not cause a request from
      // the reviewer's browser. A Thread's Snapshot belongs to the Room its
      // Comments were written in, so it can read their attachments.
      case "image": {
        const digest = /^attachment:([0-9a-f]{64})$/.exec(node.url)?.[1];
        if (digest === undefined) {
          return source(node);
        }
        const url = attachmentUrl(props.snapshotId, digest);
        const image = (
          <img src={url} alt={node.alt ?? ""} title={node.title ?? undefined} />
        );
        if (linked) {
          return image;
        }
        // An image without a caption, or with only spaces in it, gives its
        // link no accessible name.
        return (
          <a
            class="review-comment-body-image"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={
              (node.alt ?? "").trim() === ""
                ? "Open the image at full size"
                : undefined
            }
          >
            {image}
          </a>
        );
      }
      default:
        return source(node);
    }
  }
  return <div class="review-comment-body">{tree().children.map(block)}</div>;
}

function ReviewCommentView(props: {
  comment: ReviewComment;
  snapshotId: ReviewId;
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
  uploadImages(files: readonly File[]): Promise<string | null>;
}): JSX.Element {
  const imageInsertion = createImageInsertion(props.uploadImages);
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
          <AuthorName author={props.comment.author} />
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
        {props.comment.body === null ? (
          <p>Comment deleted</p>
        ) : (
          <CommentBody
            body={props.comment.body}
            snapshotId={props.snapshotId}
          />
        )}
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
                  onPaste={imageInsertion.onPaste}
                  onDragOver={imageInsertion.onDragOver}
                  onDrop={imageInsertion.onDrop}
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
