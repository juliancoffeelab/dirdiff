/**
 * Renders the History panel as an independent consumer of shared authorities.
 *
 * The module exports ReviewHistory. History observes the canonical Snapshot
 * review query itself (the cache deduplicates it against the provider's
 * observer), reads the application draft document, and creates its own
 * Thread discussion instance, so no action callbacks cross its boundary. Its
 * props are host facts — Snapshot identity, the selected Profile, view and
 * visibility, mount targets, split placement — plus the anchored-UI
 * behaviors only the review provider can perform: viewing a Thread or
 * continuing a draft at its rendered line, closing a Comment input mounted
 * in a History card, discarding a new-Thread draft, and clearing the draft
 * document. It owns per-Thread expansion, the keep-mounted reading position,
 * and idle warming; it must not own anchored-UI state, markers, or geometry.
 */
import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  on,
  onCleanup,
  type Accessor,
  type JSX,
} from "solid-js";
import { Portal } from "solid-js/web";
import { createQuery } from "@tanstack/solid-query";
import { Eye, RefreshCw } from "lucide-solid";
import {
  api,
  threadCodePoint,
  type ReviewId,
  type ReviewThread,
  type ThreadCodePoint,
} from "../../api/api";
import { ErrorPanel, RetryButton, useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { DiffViewMode } from "../App";
import type { StoredProfile } from "../Profile";
import { createThreadDiscussion } from "./discussion";
import { useReviewDrafts, type NewThreadDraft } from "./drafts";
import { ThreadCard } from "./threadViews";

const NO_THREADS: readonly ReviewThread[] = [];

/**
 * Defines History's host facts and the provider-owned anchored-UI behaviors.
 *
 * Data and discussion operations are deliberately absent: History reaches
 * the canonical query, the draft document, and its own discussion instance
 * directly. `closeCommentInputInThread` returns `false` when an in-flight
 * submission blocks the close, and the caller must then abort its action.
 */
type ReviewHistoryProps = {
  snapshotId: ReviewId;
  profile: StoredProfile | null;
  view: DiffViewMode;
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
  inlineHistoryTarget: Accessor<HTMLElement | null>;
  splitHistoryTop: Accessor<number | null>;
  canViewThread(point: ThreadCodePoint): boolean;
  viewThreadInCode(thread: ReviewThread): void;
  continueDraftInCode(draft: NewThreadDraft, point: ThreadCodePoint): void;
  closeCommentInputInThread(threadId: ReviewId): boolean;
  discardNewThreadDraft(draftId: ReviewId): boolean;
  clearDrafts(): void;
};

/**
 * Renders the keep-mounted History host, toggle, and grouped Thread panel.
 *
 * One instance exists per Snapshot review boundary and survives open/close;
 * an inline/split view switch remounts only the keyed Portal. Every write it
 * performs goes through its own discussion instance against the shared
 * authorities.
 */
export function ReviewHistory(props: ReviewHistoryProps): JSX.Element {
  const toast = useToasts();
  const draftContext = useReviewDrafts();
  const review = createQuery(() => api.review.snapshot(props.snapshotId));
  const discussion = createThreadDiscussion({
    snapshotId: props.snapshotId,
    profile: () => props.profile,
    // History has no settlement UI of its own: reply and edit inputs render
    // inside their ThreadCard and read the draft document directly.
    onSubmitted() {},
  });
  const reviewThreads = (): readonly ReviewThread[] =>
    review.data ?? NO_THREADS;
  const totalThreads = (): number => review.data?.length ?? 0;
  // History keeps its reading position itself: the closed panel is
  // display: none, whose box reads scrollTop 0, so the scroll listener in
  // the panel ref records the last real position for restoration on open.
  let historyScrollTop = 0;
  const [expandedThreads, setExpandedThreads] = createSignal<
    ReadonlyMap<ReviewId, boolean>
  >(new Map());

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
    const point = threadCodePoint(historyProps.thread);
    const replyDraft = () => discussion.replyDraftForThread(thread().thread_id);
    const hasEditDraft = () =>
      thread().comments.some(
        (comment) =>
          discussion.editDraftForComment(
            thread().thread_id,
            comment.comment_id,
          ) !== null,
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
          viewable: point !== null && props.canViewThread(point),
          onView: () => {
            if (point === null) return;
            const next = new Map(expandedThreads());
            next.set(thread().thread_id, true);
            setExpandedThreads(next);
            props.viewThreadInCode(thread());
          },
        }}
        onToggle={() => {
          if (!props.closeCommentInputInThread(thread().thread_id)) {
            return;
          }
          const next = new Map(expandedThreads());
          next.set(thread().thread_id, !expanded());
          setExpandedThreads(next);
        }}
        onState={(action) => {
          void discussion.changeThreadState(thread(), action);
        }}
        profileId={discussion.profileId()}
        submittingDraftIds={discussion.submittingDraftIds()}
        commentDeletePending={discussion.commentDeletePending}
        threadStatePending={discussion.threadStatePending}
        replyDraft={replyDraft()}
        replySubmitting={
          replyDraft() !== null &&
          discussion
            .submittingDraftIds()
            .has(
              expect(replyDraft(), "Submitting reply requires its draft.")
                .draft_id,
            )
        }
        toggleable={true}
        onReplyChange={(body) => discussion.updateReplyDraft(thread(), body)}
        onReplySubmit={async () => {
          const draft = discussion.replyDraftForThread(thread().thread_id);
          return draft !== null && discussion.submitDraft(draft.draft_id);
        }}
        onReplyDiscard={() => {
          const draft = discussion.replyDraftForThread(thread().thread_id);
          return draft === null || discussion.removeDraft(draft.draft_id);
        }}
        editDraft={(comment) =>
          discussion.editDraftForComment(thread().thread_id, comment.comment_id)
        }
        onEdit={(comment) => discussion.openEditDraft(thread(), comment)}
        onEditChange={discussion.replaceDraft}
        onEditSubmit={(draft) => discussion.submitDraft(draft.draft_id)}
        onEditDiscard={(draft) => discussion.removeDraft(draft.draft_id)}
        onDeleteComment={(comment) =>
          discussion.tombstoneComment(thread(), comment)
        }
      />
    );
  }

  return (
    <Show
      when={
        props.view === "inline" ? props.inlineHistoryTarget() : document.body
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
            hidden={props.view === "split" && props.splitHistoryTop() === null}
            style={
              props.view === "split" && props.splitHistoryTop() !== null
                ? { "--review-history-top": `${props.splitHistoryTop()}px` }
                : undefined
            }
          >
            {/* Toggle and panel both stay mounted; CSS on the host's
                review-history-open class swaps which one displays, so
                closing History cannot destroy per-Thread state, warmed
                heights, or the scroller. */}
            <button
              class="review-history-toggle"
              type="button"
              onClick={() => props.onHistoryOpenChange(true)}
              aria-expanded={props.historyOpen ? "true" : "false"}
              aria-label="Open History"
            >
              <kbd>m</kbd>
              <span class="review-history-label">
                History ({totalThreads()})
              </span>
              <Eye class="review-history-icon" aria-hidden="true" />
            </button>
            <section class="review-history-panel" aria-label="Review History">
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
                      onRetry={() => review.refetch().then(() => undefined)}
                    />
                  </ErrorPanel>
                )}
              </Show>
              <Show when={draftContext.error()} keyed>
                {(error) => (
                  <ErrorPanel title="Review drafts unavailable" error={error}>
                    <button
                      type="button"
                      disabled={discussion.submittingDraftIds().size > 0}
                      onClick={() => props.clearDrafts()}
                    >
                      Clear stored drafts
                    </button>
                  </ErrorPanel>
                )}
              </Show>
              <div
                class="review-history-scroll"
                ref={(scroller) => {
                  // This ref runs only when the keyed Portal remounts
                  // on an inline/split view switch; open/close leaves
                  // the panel mounted. Track the reading position from
                  // scroll events: the closed panel is display: none,
                  // whose box reads scrollTop 0, so capturing at close
                  // time would record nothing.
                  scroller.addEventListener(
                    "scroll",
                    () => {
                      historyScrollTop = scroller.scrollTop;
                    },
                    { passive: true },
                  );
                  createEffect(
                    on(
                      () => props.historyOpen,
                      (open) => {
                        if (!open) {
                          return;
                        }
                        // Restore after the browser lays the panel
                        // out; writing before layout is silently
                        // clamped.
                        requestAnimationFrame(() => {
                          scroller.scrollTop = historyScrollTop;
                        });
                      },
                    ),
                  );
                  // Warm every Thread over the frames after opening: a
                  // warmed Thread has rendered once, its height is
                  // real, and the estimate-replacement shifts that made
                  // History scrolling jumpy become impossible. Opening
                  // stays instant because warming is spread across
                  // frames, and a manual scroll anchor keeps the
                  // Thread at the top of the viewport pinned while
                  // heights above it change.
                  let warmFrame: number | null = null;
                  const warmBatch = () => {
                    warmFrame = null;
                    // A hidden panel must not warm: warmed Threads
                    // leave the content-visibility regime, so warming
                    // without rendering would make the next open lay
                    // out every Thread at once.
                    if (!scroller.isConnected || !props.historyOpen) {
                      return;
                    }
                    const pending = scroller.querySelectorAll(
                      ".review-thread[data-review-history-thread-id]:not(.review-thread-warmed)",
                    );
                    if (pending.length === 0) {
                      return;
                    }
                    const threads = scroller.querySelectorAll<HTMLElement>(
                      ".review-thread[data-review-history-thread-id]",
                    );
                    let anchor: HTMLElement | null = null;
                    for (const thread of threads) {
                      if (
                        thread.offsetTop + thread.offsetHeight >
                        scroller.scrollTop
                      ) {
                        anchor = thread;
                        break;
                      }
                    }
                    const anchorOffset =
                      anchor === null
                        ? 0
                        : anchor.offsetTop - scroller.scrollTop;
                    for (
                      let index = 0;
                      index < 8 && index < pending.length;
                      index += 1
                    ) {
                      pending[index]?.classList.add("review-thread-warmed");
                    }
                    if (anchor !== null) {
                      scroller.scrollTop = anchor.offsetTop - anchorOffset;
                    }
                    if (pending.length > 8) {
                      warmFrame = requestAnimationFrame(warmBatch);
                    }
                  };
                  // (Re)arm warming when the panel opens and when new
                  // Threads arrive while it is open; already-warmed
                  // Threads keep their class across close and reopen.
                  createEffect(() => {
                    if (!props.historyOpen) {
                      return;
                    }
                    orderedThreads();
                    if (warmFrame === null) {
                      warmFrame = requestAnimationFrame(warmBatch);
                    }
                  });
                  onCleanup(() => {
                    if (warmFrame !== null) {
                      cancelAnimationFrame(warmFrame);
                    }
                  });
                }}
              >
                <Show
                  when={draftContext
                    .drafts()
                    .some((draft) => draft.kind === "new-thread")}
                >
                  <section class="review-drafts" aria-label="Review drafts">
                    <h3>Drafts</h3>
                    <For
                      each={draftContext
                        .drafts()
                        .filter((draft) => draft.kind === "new-thread")}
                    >
                      {(draft) => {
                        assert(
                          draft.kind === "new-thread",
                          "History Drafts contains only new Threads.",
                        );
                        // An unposted draft has no placement, so the code it
                        // continues at is its own selected target.
                        const point: ThreadCodePoint = {
                          file: draft.target.file,
                          bay: draft.target.bay,
                          side: draft.target.side,
                          line: draft.target.range.start_line,
                        };
                        const continuable = () =>
                          draft.profile_id === discussion.profileId() &&
                          draft.snapshot_id === props.snapshotId;
                        const path = expect(
                          draft.target.side === "left"
                            ? draft.target.file.left_path
                            : draft.target.file.right_path,
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
                                {draft.target.side === "left" ? "old" : "new"} ·
                                L{draft.target.range.start_line}
                                {draft.target.range.start_line ===
                                draft.target.range.end_line
                                  ? ""
                                  : `–${draft.target.range.end_line}`}
                              </span>
                            </p>
                            <Show when={!continuable()}>
                              <p class="review-draft-unavailable">
                                {draft.profile_id !== discussion.profileId()
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
                                  draftContext.error() !== null ||
                                  discussion
                                    .submittingDraftIds()
                                    .has(draft.draft_id)
                                }
                                onClick={() =>
                                  props.continueDraftInCode(draft, point)
                                }
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
                                  draftContext.error() !== null ||
                                  discussion
                                    .submittingDraftIds()
                                    .has(draft.draft_id)
                                }
                                onClick={() =>
                                  props.discardNewThreadDraft(draft.draft_id)
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
          </aside>
        </Portal>
      )}
    </Show>
  );
}
