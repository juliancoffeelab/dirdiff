/**
 * Renders review Comment input, anchored panels, and reusable Thread cards.
 *
 * Every component receives its Thread, draft, availability, pending state, and
 * operations through props. `ThreadCard` is used both beside code and in History,
 * while `CommentInput` portals the active new-Thread form to its registered line.
 *
 * These views retain only local disclosure and textarea mechanics. They consume
 * no context and originate no query, mutation, draft persistence, or navigation.
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
import {
  threadOutdated,
  type ReviewComment,
  type ReviewId,
  type ReviewThread,
} from "../../api/api";
import { ErrorPanel } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { NewThreadDraft, ReviewDraft } from "./drafts";
import type { ActiveCommentInput, ActiveThreadPanel } from "./Review";

/**
 * Presents the provider's one active new-Thread input at its connected mount.
 *
 * The host supplies placement, the shared draft document, availability, pending
 * state, and every action. This component derives the displayed draft and Portals
 * the form beside its source line. It stores no draft, starts no HTTP action on
 * its own, and accepts changes only when the corresponding callback returns them
 * through `active`, `drafts`, or `submittingDraftIds`.
 */
export function CommentInput(props: {
  /**
   * Shared persisted draft document used after transient input gains meaningful
   * text. The active identity must resolve exactly once whenever `active.input` is null.
   */
  drafts: Accessor<readonly ReviewDraft[]>;
  /**
   * Reads the provider-owned input placement, or null when no input is open.
   * Its connected mount is the only element this component may Portal into.
   */
  active: Accessor<ActiveCommentInput | null>;
  /**
   * Reads the storage failure that disables editing and draft actions. The exact
   * error is presented locally instead of replacing it with an empty draft state.
   */
  draftError: Accessor<Error | null>;
  /**
   * Reports whether persisted Threads are currently safe to write against.
   * False leaves saved input visible but prevents starting submission.
   */
  reviewAvailable: Accessor<boolean>;
  /**
   * Reads draft identities disabled from submission start through settlement.
   * Membership freezes this input without hiding its persisted body.
   */
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  /**
   * Called on each textarea input with the complete new-Thread draft containing
   * the typed body. The provider may persist, replace, or remove it and returns
   * whether storage accepted the value; false makes this component restore the
   * previously supplied body, while true returns accepted state through `active`
   * or `drafts` before the next render. It is not called while the input is disabled.
   */
  onDraftChange(draft: NewThreadDraft): boolean;
  /**
   * Called from the enabled Discard control with the active draft identity. The
   * provider may remove persisted work or close transient empty input and returns
   * whether it succeeded; accepted closure returns through `active`. It is not
   * called when no active draft exists.
   */
  onDiscard(draftId: ReviewId): boolean;
  /**
   * Called from the close control without a draft copy. The provider decides
   * whether empty work is removed or meaningful work merely closes, and may refuse
   * while submission is in flight; the resulting placement returns through `active`
   * after the callback. This component performs no independent close.
   */
  onClose(): void;
  /**
   * Called after valid form or shortcut submission with the active persisted draft
   * identity. The provider may perform its HTTP action and resolves true when the
   * backend accepts it, even if refreshing the local Thread query then fails; pending
   * and final state return through the draft accessors. Empty, unavailable,
   * failed-storage, or already-submitting forms do not invoke it.
   */
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

/**
 * Presents the canonical Threads selected by one active code marker.
 *
 * The host must keep every selected identity resolvable in `threads`, supply the
 * connected Portal mount, and perform all discussion actions. This component owns
 * only which card is expanded when a marker names several Threads. It neither
 * copies Thread state nor keeps a panel alive after `active` becomes null.
 */
export function InlineThreadPanel(props: {
  /**
   * Provider-owned marker selection and connected mount, or null when closed.
   * Every selected Thread identity must remain resolvable while the panel is active.
   */
  active: Accessor<ActiveThreadPanel | null>;
  /**
   * Canonical Thread list from which every selected identity must resolve once.
   * Published actions replace entries here rather than mutating card-local copies.
   */
  threads: Accessor<readonly ReviewThread[]>;
  /**
   * Reads the selected Profile identity used to expose authored Comment editing.
   * Null keeps discussions readable but makes every Comment noneditable.
   */
  profileId: Accessor<number | null>;
  /**
   * Reads reply and edit identities whose immutable drafts are in flight.
   * Matching inputs remain visible and disabled until their settlement changes props.
   */
  submittingDraftIds: Accessor<ReadonlySet<ReviewId>>;
  /**
   * Called during Comment rendering with its identity to disable only the matching
   * delete action. It returns shared mutation-cache state and never starts deletion;
   * settlement feeds false back through a reactive render.
   */
  commentDeletePending(commentId: ReviewId): boolean;
  /**
   * Called during Thread rendering with the Thread identity and exact lifecycle
   * action represented by a control. It returns shared pending state, performs no
   * transition, and becomes false through a later render after settlement.
   *
   * @param threadId Thread identity rendered by the current card.
   * @param action Exact transition represented by the pending probe.
   */
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  /**
   * Called while rendering one selected Thread with its identity. It returns the
   * current Profile's persisted reply or null without opening one; draft updates
   * feed a replacement value back on the next reactive read.
   *
   * # Returns
   *
   * - The current Profile's persisted reply draft for this Thread.
   * - `null`: No matching reply exists. The permanent reply input renders empty
   *   and offers no Discard action.
   */
  replyDraft(threadId: ReviewId): ReviewDraft | null;
  /**
   * Called while rendering a Comment with its containing Thread and Comment
   * identities. It returns the matching edit draft or null and does not open an
   * editor; accepted edit actions feed a draft back on a later render.
   *
   * @param threadId Thread expected to contain the Comment.
   * @param commentId Comment whose persisted replacement is requested.
   *
   * # Returns
   *
   * - The current Profile's persisted edit draft for this Comment.
   * - `null`: No matching edit exists. The Comment stays in read mode.
   */
  editDraft(threadId: ReviewId, commentId: ReviewId): ReviewDraft | null;
  /**
   * Called from the panel close control. The provider may preserve or remove any
   * adjacent draft according to its submission state, then clears the selected
   * panel; accepted closure returns through `active` after this call completes.
   */
  onClose(): void;
  /**
   * Called on reply input with the selected `thread` and complete typed `body`.
   * The provider returns whether persistence accepted it; false restores the
   * supplied reply body, while true feeds the changed draft back through
   * `replyDraft`. Disabled deleted or submitting inputs do not invoke it.
   *
   * @param thread Loaded selected Thread receiving the reply input.
   * @param body Complete current textarea value.
   */
  onReplyChange(thread: ReviewThread, body: string): boolean;
  /**
   * Called from a valid reply submission with the exact selected `thread`.
   * It resolves after the HTTP attempt, any local query update attempt, and draft
   * settlement. A backend-accepted write may still be followed by a local refresh
   * error; pending and final draft state return through the supplied accessors.
   * Empty or submitting forms skip it.
   */
  onReplySubmit(thread: ReviewThread): Promise<boolean>;
  /**
   * Called from an enabled reply Discard control with its selected `thread`.
   * It returns whether removal was accepted; success feeds null back through
   * `replyDraft`, and false leaves the previous text rendered.
   */
  onReplyDiscard(thread: ReviewThread): boolean;
  /**
   * Called from an eligible Comment's Edit control with its containing `thread`
   * and current `comment`. The provider may persist an edit draft; the editor
   * appears only when `editDraft` returns it after this callback completes.
   *
   * @param thread Loaded Thread containing the Comment.
   * @param comment Current editable Comment selected by the control.
   */
  onEdit(thread: ReviewThread, comment: ReviewComment): void;
  /**
   * Called on edit input with the complete replacement `draft`. It returns whether
   * persistence accepted the body; false restores the previous supplied draft,
   * and true feeds the replacement back through `editDraft` before rerender.
   */
  onEditChange(draft: ReviewDraft): boolean;
  /**
   * Called from a valid edit submission with its persisted edit `draft`. It resolves
   * after the HTTP attempt, any local query update attempt, and settlement. Backend
   * acceptance removes the draft even if the local update reports an error; pending
   * and final editor state return through the accessors. Empty, ineligible, or
   * already-submitting edits do not invoke it.
   */
  onEditSubmit(draft: ReviewDraft): Promise<boolean>;
  /**
   * Called from an enabled edit Discard control with its exact draft. It returns
   * whether storage removed it; success makes `editDraft` return null, while false
   * keeps the editor and its previous body.
   */
  onEditDiscard(draft: ReviewDraft): boolean;
  /**
   * Called from an eligible Comment delete action with its containing Thread and
   * current Comment. The provider may confirm and start deletion; accepted state
   * returns later through `threads`, and cancelled or unavailable actions do nothing.
   *
   * @param thread Loaded Thread containing the Comment.
   * @param comment Current deletable Comment selected by the control.
   */
  onDeleteComment(thread: ReviewThread, comment: ReviewComment): void;
  /**
   * Called from a Thread lifecycle control with its selected Thread and exact action.
   * The provider may confirm deletion and publish the response; state returns later
   * through `threads`, while unavailable or cancelled actions leave it unchanged.
   *
   * @param thread Loaded Thread receiving the lifecycle transition.
   * @param action Exact transition selected by the control.
   */
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
        /**
         * Reports whether this marker requires per-Thread expansion controls.
         *
         * A single visible Thread stays expanded without a toggle. With several,
         * this reactive read enables independent expansion while persisted reply
         * and edit inputs may still force their own Thread open.
         */
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
                  /**
                   * Reads this selected Thread's current Profile reply draft.
                   *
                   * Keeping the read reactive lets persistence acceptance or
                   * submission settlement update expansion, pending state, and the
                   * ThreadCard input together without retaining a second draft.
                   */
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

/**
 * Presents one canonical Thread as the card shared by History and inline panels.
 *
 * The host controls expansion, navigation, pending probes, drafts, and every
 * discussion action. This component derives eligibility and visible controls from
 * those values, but never mutates the Thread or writes a draft itself. Accepted
 * operations appear only when the host supplies replacement Thread or draft props.
 */
export function ThreadCard(props: {
  /**
   * Canonical loaded Thread whose placement, Comments, and state are shown. The
   * host replaces this complete value after accepted actions; the card never mutates it.
   */
  thread: ReviewThread;
  /**
   * Host-owned expansion. False renders only summary and header actions while
   * keeping any persisted reply or edit input available to force expansion upstream.
   */
  expanded: boolean;
  /**
   * Host kind and, only for History, the availability and action for code go-to.
   * Inline cards already occupy their target and must not represent navigation.
   */
  navigation:
    | {
        /** Marks a card already mounted beside its code, so no go-to action exists. */
        kind: "inline";
      }
    | {
        /** Marks a History card that may offer navigation back to reviewed code. */
        kind: "history";
        /** Whether the located Thread's File is currently loaded for navigation. */
        viewable: boolean;
        /**
         * Called only from an enabled History go-to control for this exact Thread.
         * The host may navigate and open anchored presentation; the card remains
         * expanded while accepted location and Thread state return through its props.
         * Unlocated or unloaded Threads do not invoke it.
         */
        onView(): void;
      };
  /**
   * Selected Profile identity used solely to expose editing of authored Comments.
   * Null or a different identity never changes immutable author attribution.
   */
  profileId: number | null;
  /**
   * Draft identities whose reply or edit inputs remain disabled until settlement.
   * The card reads membership but cannot add, remove, or infer submissions.
   */
  submittingDraftIds: ReadonlySet<ReviewId>;
  /**
   * Called while rendering a Comment with its identity. It returns whether that
   * Comment's deletion is in flight and does not mutate it; settlement returns
   * false through new props from the host.
   */
  commentDeletePending(commentId: ReviewId): boolean;
  /**
   * Called while rendering a lifecycle control with this Thread identity and the
   * control's exact action. It returns shared pending state only; completion and
   * accepted Thread state return later through `thread`.
   *
   * @param threadId Identity of the Thread rendered by this card.
   * @param action Exact transition represented by the queried control.
   */
  threadStatePending(
    threadId: ReviewId,
    action: "resolve" | "reopen" | "delete",
  ): boolean;
  /**
   * Current Profile's persisted reply, or null for an empty permanent input.
   * Deleted Threads keep an existing draft visible but prohibit creating a new one.
   */
  replyDraft: ReviewDraft | null;
  /**
   * Whether that exact reply draft is between submission start and settlement.
   * True freezes editing and duplicate form submission without hiding the body.
   */
  replySubmitting: boolean;
  /**
   * Whether the host permits the heading to change this card's expansion.
   * History always permits it; an inline panel does so only when several Threads share a line.
   */
  toggleable: boolean;
  /**
   * Called from the heading only when History or a multi-Thread inline panel renders
   * it as a toggle. The host may refuse a close that would disrupt an in-flight
   * editor, then feeds accepted expansion back through `expanded`; the card never
   * changes expansion itself and a single inline Thread does not invoke this callback.
   */
  onToggle(): void;
  /**
   * Called from an enabled lifecycle control with the exact selected action for
   * `thread`. The host may confirm and publish it; accepted state returns through
   * a replacement `thread`, while unavailable or cancelled actions change nothing.
   */
  onState(action: "resolve" | "reopen" | "delete"): void;
  /**
   * Called on reply input with the complete typed body for `thread`. It returns
   * whether persistence accepted the value; false restores `replyDraft`'s body,
   * while true returns the accepted draft through the next props. Deleted or
   * submitting inputs do not invoke it.
   */
  onReplyChange(body: string): boolean;
  /**
   * Called from a valid reply form or keyboard shortcut for `replyDraft`. It resolves
   * after the HTTP attempt, any local query update attempt, and settlement. Backend
   * acceptance removes the reply draft even if the local update fails; pending and
   * final state return through props. Empty or already-submitting replies do not
   * invoke it.
   */
  onReplySubmit(): Promise<boolean>;
  /**
   * Called from the enabled reply Discard control. It returns whether storage
   * accepted removal; success feeds null back through `replyDraft`, and false keeps
   * the supplied draft body rendered.
   */
  onReplyDiscard(): boolean;
  /**
   * Called during Comment rendering with the current Comment. It returns that
   * Comment's persisted edit draft or null without opening one; accepted edit
   * activation and changes return through later calls.
   *
   * # Returns
   *
   * - The persisted edit draft that replaces this Comment's body in the editor.
   * - `null`: No edit is open for the Comment, so the card renders its canonical
   *   body and eligible Edit control.
   */
  editDraft(comment: ReviewComment): ReviewDraft | null;
  /**
   * Called from an eligible Comment's Edit control with that current Comment. The
   * host may persist an edit draft; the editor appears only when `editDraft` returns
   * it afterward. Ineligible Comments never expose the activating control.
   */
  onEdit(comment: ReviewComment): void;
  /**
   * Called on edit input with the complete replacement draft. It returns whether
   * persistence accepted the body; false restores the previous draft, and true
   * feeds the replacement back through `editDraft` before the next render.
   */
  onEditChange(draft: ReviewDraft): boolean;
  /**
   * Called from a valid edit form with its persisted draft. It resolves after the
   * HTTP attempt, any local query update attempt, and settlement. Backend acceptance
   * removes the editor even if that local update fails; pending and final state
   * return through props. Empty, ineligible, or already-submitting edits do not
   * invoke it.
   */
  onEditSubmit(draft: ReviewDraft): Promise<boolean>;
  /**
   * Called from an enabled edit Discard control with the exact draft. It returns
   * whether removal succeeded; the editor closes only when `editDraft` later returns
   * null, and a false result retains the supplied body.
   */
  onEditDiscard(draft: ReviewDraft): boolean;
  /**
   * Called from an eligible Comment delete control with that current Comment. The
   * host may confirm and start deletion; accepted tombstone state returns later
   * through `thread`, while cancellation or unavailable data leaves it unchanged.
   */
  onDeleteComment(comment: ReviewComment): void;
}): JSX.Element {
  const origin = props.thread.origin_target;
  const excerptPath = expect(
    origin.side === "left" ? origin.file.left_path : origin.file.right_path,
    "A review origin requires its selected-side File path.",
  );
  const excerptFileName = excerptPath.slice(excerptPath.lastIndexOf("/") + 1);
  // The excerpt travels inside a text origin, so the three sites that render
  // it need this arm; a retained File-level origin carries none.
  const textOrigin = origin.kind === "text" ? origin : null;
  /**
   * Returns the required originating Comment used for attribution and summaries.
   *
   * The Thread contract requires a nonempty Comment history. Violations throw
   * instead of rendering anonymous attribution or an empty summary.
   */
  const firstComment = () =>
    expect(
      props.thread.comments[0],
      "A review Thread requires its first Comment.",
    );
  /**
   * States what this Snapshot did to the reviewed code, or `null` for nothing.
   *
   * A kept region and a retained File-level Thread both still rest where they
   * were written, and those are exactly the placements that raise no warning.
   *
   * # Returns
   *
   * - The established warning sentence for a changed, lost, absent, or
   *   unreadable placement.
   * - `null`: The region remains where written or the whole File remains
   *   present. The card must omit the placement warning.
   */
  function placementNote(): string | null {
    switch (props.thread.placement.kind) {
      case "region-kept":
      case "whole-file":
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
  /**
   * States why this Thread names no code at all, or `null` when it names some.
   *
   * Only the two File-level failures drop every coordinate: one File pair is
   * absent from this Snapshot, the other holds nothing dirdiff could read.
   * Both disable go-to and print their sentence in the expanded card. Every
   * other placement lands somewhere, however degraded, and must not print it.
   *
   * # Returns
   *
   * - The placement warning for a File that is absent or unreadable in this
   *   Snapshot.
   * - `null`: The Thread retains a code coordinate. History may offer go-to and
   *   must not render the unlocated notice.
   */
  function unlocatedNote(): string | null {
    const kind = props.thread.placement.kind;
    return kind === "file-absent" || kind === "file-unreadable"
      ? expect(placementNote(), "An unlocated placement states its reason.")
      : null;
  }
  /**
   * Reports whether the currently selected Profile authored one Comment.
   *
   * This comparison controls only edit eligibility. A missing Profile returns
   * false, and deletion and Thread lifecycle permissions remain separate.
   *
   * @param comment Current Comment whose immutable author is compared.
   */
  function authoredByCurrentProfile(comment: ReviewComment): boolean {
    const author = comment.author;
    return author.profile_id === props.profileId;
  }
  /**
   * Renders the shared Thread identity displayed by both header variants.
   *
   * The fragment shows the originating author's immutable display name and the
   * origin File's basename. Text origins also show their selected start line.
   * Placement damage adds the warning returned by `placementNote`, while kept and
   * whole-File origins add no warning. The heading starts no action and stores no
   * presentation state, so button and non-button headers use the same identity.
   *
   * # Failures
   *
   * A Thread without its required originating Comment throws when the heading is
   * evaluated instead of rendering anonymous attribution.
   */
  function ThreadHeading(): JSX.Element {
    return (
      <>
        <span class="review-thread-state-dot" aria-hidden="true" />
        <strong title={firstComment().author.display_name}>
          {firstComment().author.display_name}
        </strong>
        <span class="review-thread-location" title={excerptPath}>
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
  /** Estimates the rendered height for pre-render History scroll geometry.
   *
   * History Threads render lazily under content-visibility; before first
   * render the browser uses the intrinsic estimate, and a constant 120px is
   * about five times too short for an expanded discussion, which made History
   * scrolling jump as real heights replaced estimates. The model prices the
   * chrome, the code excerpt, and each Comment by its body length; `auto`
   * sizing replaces it with the real height after first render.
   */
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
        <Show
          when={
            props.navigation.kind === "history" ? props.navigation : undefined
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
              /**
               * Reads this Comment's current persisted replacement input.
               *
               * The accessor keeps editor visibility, pending membership, and the
               * supplied body on the same reactive draft without a local copy.
               */
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

/**
 * Presents one canonical Comment body or tombstone within its Thread card.
 *
 * The parent decides authorship eligibility, supplies the persisted edit draft,
 * and performs edit and delete actions. This row handles only textarea mechanics and
 * restores the supplied body when persistence rejects a change. It never mutates
 * the Comment, selects a Profile, or starts an action without the matching control.
 */
function ReviewCommentView(props: {
  /**
   * Current canonical Comment, including immutable author and optional tombstone.
   * Accepted edits or deletion arrive as a replacement value from the Thread query.
   */
  comment: ReviewComment;
  /**
   * Persisted replacement input for this Comment, or null when not editing.
   * A non-null value keeps the editor present even across host rerenders.
   */
  editDraft: ReviewDraft | null;
  /**
   * Whether this exact edit draft is disabled from submission start through settlement.
   * True preserves its text while preventing changes, discard, and duplicate submission.
   */
  editSubmitting: boolean;
  /**
   * Whether its author differs from the Thread's originating Comment author.
   * This is presentation metadata only and does not imply reply authorship or permission.
   */
  response: boolean;
  /**
   * Whether the selected Profile may currently replace this nondeleted Comment.
   * False removes the Edit action and prevents a retained editor from submitting.
   */
  editable: boolean;
  /**
   * Whether current Thread and Comment state permit a delete action. Authorship is
   * not required by this presentation flag; the backend evaluates the selected actor.
   */
  deletable: boolean;
  /**
   * Whether this exact Comment's delete HTTP action is in flight. True disables only
   * its delete control until canonical Comment state or the failure returns.
   */
  deletePending: boolean;
  /**
   * Called only from the rendered Edit control for `comment`. The host may persist
   * a draft seeded with its current body; accepted editor state returns through
   * `editDraft` after the callback. Noneditable Comments do not invoke it.
   */
  onEdit(): void;
  /**
   * Called on edit textarea input with the complete replacement draft. It returns
   * whether persistence accepted the body; false restores the supplied draft body,
   * while true feeds the accepted replacement back through `editDraft` next.
   * Disabled or noneditable inputs do not invoke it.
   */
  onEditChange(draft: ReviewDraft): boolean;
  /**
   * Called from a valid form or keyboard shortcut with the current persisted edit
   * draft. It resolves after the HTTP attempt, any local query update attempt, and
   * settlement. Backend acceptance closes the editor even if that local update
   * fails; pending and final state return through props. Blank, noneditable, or
   * submitting forms skip it.
   */
  onEditSubmit(draft: ReviewDraft): Promise<boolean>;
  /**
   * Called from the enabled editor Discard control with the current draft. It
   * returns whether removal succeeded; the editor closes only when `editDraft`
   * becomes null, while false preserves the previously supplied body.
   */
  onEditDiscard(draft: ReviewDraft): boolean;
  /**
   * Called only from the rendered enabled Delete control for `comment`. The host
   * may confirm and start deletion; accepted tombstone and pending state return
   * through props, while cancellation changes nothing.
   */
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
