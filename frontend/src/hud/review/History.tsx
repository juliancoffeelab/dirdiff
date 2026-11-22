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
import { createInfiniteQuery } from "@tanstack/solid-query";
import { Eye, MessageSquarePlus, RefreshCw } from "lucide-solid";
import {
  api,
  isRejectedThread,
  reviewThreadEntries,
  threadPlacementCodePoint,
  type AcceptedReviewActivity,
  type ReviewFilePair,
  type RejectedThread,
  type ReviewId,
  type ReviewThread,
  type ReviewThreadEntry,
  type ThreadCodePoint,
} from "../../api/api";
import { ErrorPanel, RetryButton, useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { DiffViewMode } from "../App";
import type { StoredProfile } from "../Profile";
import { createThreadDiscussion } from "./discussion";
import { useReviewDrafts, type NewThreadDraft } from "./drafts";
import { ThreadCard } from "./threadViews";

const NO_THREADS: readonly ReviewThreadEntry[] = [];

type ReviewHistoryProps = {
  snapshotId: ReviewId;
  profile: StoredProfile | null;
  acceptedActivities: Accessor<readonly AcceptedReviewActivity[]>;
  onAcceptedActivity(activity: AcceptedReviewActivity): void;
  view: DiffViewMode;
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
  inlineHistoryTarget: Accessor<HTMLElement | null>;
  splitHistoryTop: Accessor<number | null>;
  // The draft the open whole-review Comment input holds, which Drafts omits.
  reviewInputDraftId: Accessor<ReviewId | null>;
  canViewThread(point: ThreadCodePoint): boolean;
  viewThreadInCode(thread: ReviewThread): void;
  canViewFile(file: ReviewFilePair): boolean;
  viewOriginThreadInCode(
    file: ReviewFilePair,
    point: ThreadCodePoint | null,
    useFileStart: boolean,
  ): void;
  continueDraftInCode(draft: NewThreadDraft, point: ThreadCodePoint): void;
  toggleReviewThreadInput(mount: HTMLElement): void;
  closeReviewThreadInput(mount: HTMLElement): void;
  continueReviewDraft(draft: NewThreadDraft, mount: HTMLElement): void;
  // False when an in-flight submission blocks the close; the caller must abort.
  closeCommentInputInThread(threadId: ReviewId): boolean;
  discardNewThreadDraft(draftId: ReviewId): boolean;
  clearDrafts(): void;
};

export function ReviewHistory(props: ReviewHistoryProps): JSX.Element {
  const toast = useToasts();
  const draftContext = useReviewDrafts();
  const review = createInfiniteQuery(() =>
    api.review.snapshot(props.snapshotId, toast.showError),
  );
  const discussion = createThreadDiscussion({
    snapshotId: props.snapshotId,
    profile: () => props.profile,
    acceptedActivities: props.acceptedActivities,
    onAcceptedActivity: props.onAcceptedActivity,
    // Reply and edit inputs live in ThreadCard and read the draft document.
    onSubmitted() {},
  });
  const threadEntries = createMemo<readonly ReviewThreadEntry[]>(
    () => reviewThreadEntries(review.data, props.acceptedActivities()),
    NO_THREADS,
  );
  // A rejected Thread has no valid state to group or sort by, so the rejected
  // ones form their own group above Open, in backend order.
  const rejectedThreads = createMemo(() =>
    threadEntries().filter(isRejectedThread),
  );
  const reviewThreads = createMemo(() =>
    threadEntries().filter(
      (entry): entry is ReviewThread => !isRejectedThread(entry),
    ),
  );
  const totalThreads = (): number =>
    Math.max(review.data?.pages[0]?.total_threads ?? 0, threadEntries().length);

  async function reloadThreads(): Promise<void> {
    await review.refetch();
  }
  // The closed panel is display: none and reads scrollTop 0, so the last real
  // position is recorded from scroll events for restoration on open.
  let historyScrollTop = 0;
  let reviewInputSlot: HTMLElement | null = null;
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
  const listedDrafts = createMemo(() =>
    draftContext
      .drafts()
      .filter(
        (draft) =>
          draft.kind === "new-thread" &&
          draft.draft_id !== props.reviewInputDraftId(),
      ),
  );
  const groupedThreads = createMemo(() => ({
    open: orderedThreads().filter((thread) => thread.state === "open"),
    resolved: orderedThreads().filter((thread) => thread.state === "resolved"),
    deleted: orderedThreads().filter((thread) => thread.state === "deleted"),
  }));

  function HistoryThread(historyProps: { thread: ReviewThread }): JSX.Element {
    const thread = () => historyProps.thread;
    const point = threadPlacementCodePoint(historyProps.thread);
    const locationSource = historyProps.thread.location_source;
    // A review-scoped Thread has no File, so it has no File-start landing.
    const originFile =
      historyProps.thread.origin_target.kind === "review"
        ? null
        : historyProps.thread.origin_target.file;
    const originUnavailableNote =
      locationSource.kind !== "origin-snapshot"
        ? null
        : locationSource.file_state === "missing"
          ? "The reviewed File is not present in the displayed Snapshot."
          : locationSource.file_state === "unreadable"
            ? "The reviewed File could not be read in the displayed Snapshot."
            : null;
    const useFileStart =
      locationSource.kind === "origin-snapshot" &&
      originFile !== null &&
      (locationSource.file_state === "changed" || point === null);
    const navigationAdvisory =
      locationSource.kind === "origin-snapshot" &&
      locationSource.file_state === "changed" &&
      historyProps.thread.origin_target.kind === "text"
        ? "This Thread has no placement in the displayed Snapshot. Go to the File start; reload the diff to locate it accurately."
        : null;
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
          viewable:
            originUnavailableNote === null &&
            (locationSource.kind === "origin-snapshot"
              ? useFileStart
                ? props.canViewFile(originFile)
                : point !== null && props.canViewThread(point)
              : point !== null && props.canViewThread(point)),
          advisory: navigationAdvisory,
          unavailableNote: originUnavailableNote,
          onView: () => {
            if (
              originUnavailableNote !== null ||
              (point === null && !useFileStart)
            ) {
              return;
            }
            const next = new Map(expandedThreads());
            next.set(thread().thread_id, true);
            setExpandedThreads(next);
            props.onHistoryOpenChange(false);
            if (locationSource.kind === "origin-snapshot") {
              props.viewOriginThreadInCode(
                expect(
                  originFile,
                  "Origin navigation requires an origin File.",
                ),
                point,
                useFileStart,
              );
            } else {
              props.viewThreadInCode(thread());
            }
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

  // The toggle is created in whichever place the current view shows it; the
  // panel stays in the body host, so a view switch does not remount it.
  const toggle = (): JSX.Element => (
    <button
      class="review-history-toggle"
      type="button"
      onClick={() => props.onHistoryOpenChange(true)}
      aria-expanded={props.historyOpen ? "true" : "false"}
      aria-label="Open History"
    >
      <kbd>m</kbd>
      <span class="review-history-label">History ({totalThreads()})</span>
      <Eye class="review-history-icon" aria-hidden="true" />
    </button>
  );

  // The open view covers the change set from its viewport top (CSS clamps it
  // under the app header) to the viewport bottom. The document cannot scroll
  // while the view is open, so that top moves only with layout: it is measured
  // on open, on window resize and whenever the body changes size.
  const [changeSetTop, setChangeSetTop] = createSignal<number | null>(null);
  createEffect(
    on(
      () => props.historyOpen,
      (open) => {
        if (!open) {
          setChangeSetTop(null);
          return;
        }
        function measure(): void {
          const workspaces = document.querySelectorAll(".diff-workspace");
          // History is mounted only beside a Snapshot's change set, and it
          // can only be opened there.
          assert(
            workspaces.length === 1,
            "The review view requires exactly one change set.",
          );
          setChangeSetTop(workspaces[0].getBoundingClientRect().top);
        }
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(document.body);
        window.addEventListener("resize", measure);
        onCleanup(() => {
          observer.disconnect();
          window.removeEventListener("resize", measure);
        });
      },
    ),
  );

  return (
    <>
      <Show
        when={props.view === "inline" ? props.inlineHistoryTarget() : null}
        keyed
      >
        {(slot) => (
          <Portal mount={slot}>
            <div class="review-history-rail">{toggle()}</div>
          </Portal>
        )}
      </Show>
      <Portal>
        <aside
          class="review-history-host"
          classList={{ "review-history-open": props.historyOpen }}
          hidden={
            props.historyOpen
              ? changeSetTop() === null
              : props.view === "split" && props.splitHistoryTop() === null
          }
          style={{
            "--review-history-top":
              props.splitHistoryTop() === null
                ? undefined
                : `${props.splitHistoryTop()}px`,
            "--review-change-set-top":
              changeSetTop() === null ? undefined : `${changeSetTop()}px`,
          }}
        >
          {/* The split toggle and the panel both stay mounted; CSS on the
            host's review-history-open class swaps which one displays, so
            closing History cannot destroy per-Thread state, warmed heights,
            or the scroller. */}
          <Show when={props.view === "split"}>{toggle()}</Show>
          <section class="review-history-panel" aria-label="Review History">
            <header onClick={() => props.onHistoryOpenChange(false)}>
              <kbd>m</kbd>
              <strong>History</strong>
              <span>
                {threadEntries().length} / {totalThreads()} Threads
              </span>
              <button
                type="button"
                class="field-icon-button review-history-new-thread"
                aria-label="Comment on the whole review"
                title="Comment on the whole review"
                onClick={(event) => {
                  event.stopPropagation();
                  props.toggleReviewThreadInput(
                    expect(
                      reviewInputSlot,
                      "History requires its whole-review input slot.",
                    ),
                  );
                }}
              >
                <MessageSquarePlus class="field-icon" aria-hidden="true" />
              </button>
              <button
                type="button"
                class="field-icon-button metadata-refresh-button review-history-refresh"
                aria-label="Reload Threads"
                title="Reload Threads"
                disabled={review.isFetching}
                onClick={(event) => {
                  event.stopPropagation();
                  void reloadThreads();
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
                <ErrorPanel title="Failed to load review Threads" error={error}>
                  <RetryButton onRetry={reloadThreads} />
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
              // Focus target on open, so keyboard scrolling reaches this
              // scroller while the document is locked.
              tabIndex={-1}
              ref={(scroller) => {
                // The listener is never removed; it dies with the scroller.
                scroller.addEventListener(
                  "scroll",
                  () => {
                    historyScrollTop = scroller.scrollTop;
                  },
                  { passive: true },
                );
                // Tracks accepted `historyOpen`, not the click; runs on
                // creation too, so an initially open panel restores. The
                // frame is not cancelled: after disposal it writes a
                // detached scroller.
                createEffect(
                  on(
                    () => props.historyOpen,
                    (open) => {
                      if (!open) {
                        return;
                      }
                      // Writing before layout is silently clamped.
                      requestAnimationFrame(() => {
                        scroller.scrollTop = historyScrollTop;
                        scroller.focus({ preventScroll: true });
                      });
                    },
                  ),
                );
                // Warm Threads over frames after opening so estimated heights
                // are replaced by real ones (avoids jumpy scrolling) without
                // delaying open; a manual scroll anchor pins the top Thread.
                let warmFrame: number | null = null;
                const warmBatch = () => {
                  warmFrame = null;
                  // Hidden panels must not warm: warmed Threads leave
                  // content-visibility, so the next open would lay out all.
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
                    anchor === null ? 0 : anchor.offsetTop - scroller.scrollTop;
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
                // Re-arms warming on open and when Threads arrive; the
                // cleanup below cancels the pending frame.
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
              <div
                class="review-history-input-slot"
                ref={(slot) => {
                  reviewInputSlot = slot;
                  // An error-boundary retry replaces History and this slot;
                  // close the input the provider mounted here before the
                  // new slot exists, as renderers do for replaced rows.
                  onCleanup(() => props.closeReviewThreadInput(slot));
                }}
              />
              <Show when={listedDrafts().length > 0}>
                <section class="review-drafts" aria-label="Review drafts">
                  <h3>Drafts</h3>
                  <For each={listedDrafts()}>
                    {(draft) => {
                      assert(
                        draft.kind === "new-thread",
                        "History Drafts contains only new Threads.",
                      );
                      // A whole-review draft has no code; it continues in
                      // History's own input slot.
                      const target =
                        draft.target.kind === "text" ? draft.target : null;
                      const continuable = () =>
                        draft.profile_id === discussion.profileId() &&
                        draft.snapshot_id === props.snapshotId;
                      const path =
                        target === null
                          ? "Whole review"
                          : expect(
                              target.side === "left"
                                ? target.file.left_path
                                : target.file.right_path,
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
                            <Show when={target} keyed>
                              {(text) => (
                                <span>
                                  {text.side === "left" ? "old" : "new"} · L
                                  {text.range.start_line}
                                  {text.range.start_line === text.range.end_line
                                    ? ""
                                    : `–${text.range.end_line}`}
                                </span>
                              )}
                            </Show>
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
                              onClick={() => {
                                if (target === null) {
                                  props.continueReviewDraft(
                                    draft,
                                    expect(
                                      reviewInputSlot,
                                      "History requires its whole-review input slot.",
                                    ),
                                  );
                                  return;
                                }
                                props.onHistoryOpenChange(false);
                                // An unposted draft has no placement, so the
                                // code it continues at is its own target.
                                props.continueDraftInCode(draft, {
                                  file: target.file,
                                  bay: target.bay,
                                  side: target.side,
                                  line: target.range.start_line,
                                });
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
              <Show when={rejectedThreads().length > 0}>
                <section class="review-thread-group">
                  <header class="review-thread-group-heading">
                    <strong>Could not be shown</strong>
                    <span>{rejectedThreads().length}</span>
                  </header>
                  <For each={rejectedThreads()}>
                    {(rejected: RejectedThread) => (
                      <ErrorPanel
                        title="Thread could not be shown"
                        error={rejected.error}
                      >
                        <p>
                          {rejected.thread_id ?? "The Thread has no valid id."}
                        </p>
                      </ErrorPanel>
                    )}
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
    </>
  );
}
