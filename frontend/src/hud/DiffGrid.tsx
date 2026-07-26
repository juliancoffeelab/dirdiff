/**
 * Renders immutable text-diff rows as the established split or inline grid.
 *
 * The module exports DiffGrid and its row contracts. DiffGrid contains the imperative DOM
 * kernel, fold-row construction, syntax decoration, side-selection behavior,
 * hunk anchor attributes, and the frontend-only floating comment mock for one
 * FullFile body. Callers provide fully validated backend rows and complete
 * presentation inputs. It must not fetch data, own ChangeSet state, navigate
 * hunks, virtualize files, or render notebook framing.
 */
import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  untrack,
  type JSX,
} from "solid-js";
import { createStore, produce } from "solid-js/store";
import { Portal } from "solid-js/web";
import type { DecoratedPart, DiffRow, FoldHint, RowStatus } from "../api/api";
import type { DiffViewMode } from "./App";
import { addFoldRows, isFoldRow, type FoldRow, type RenderRow } from "./folds";
import type { LinePins, LinePinTarget, PreparedLine } from "./linePins";
import type { RealHunkIdentity } from "./navigation";
import { assert } from "../utils";

const suppressedSyntaxClassPrefixes = [
  "ts-punctuation",
  "ts-operator",
  "ts-variable",
  "ts-parameter",
  "ts-field",
  "ts-local",
];

/**
 * Selects one validated side of a split diff row.
 *
 * The value controls which backend line, text, and decorated-part fields are
 * read; it does not represent a user selection or persistent application state.
 */
type Side = "left" | "right";

/**
 * Represents the single visible prefix emitted for one inline diff line.
 *
 * Every inline row must provide exactly one marker from this closed set. The
 * marker communicates presentation only and is hidden from accessibility text.
 */
type InlineMarker = " " | "-" | "+" | "*";

/**
 * Represents the CSS and token treatment available to an inline render row.
 *
 * The union excludes fold rows and maps the split-only backend statuses into
 * the complete set that inline rendering may emit.
 */
type InlineRowStatus = "equal" | "delete" | "insert" | "replace" | "move";

/**
 * Identifies one exact rendered line selected for the floating comment mock.
 *
 * File and nullable notebook region identify the rendered text region; side and
 * positive decimal line identify the visible backend coordinate. This is
 * transient presentation identity only and must not represent a persisted
 * comment, line pin, or backend entity.
 */
type CommentTarget = {
  file: string;
  region: string | null;
  side: Side;
  line: string;
};

/**
 * Represents one comment posted into the page-lifetime frontend mock.
 *
 * The UUID distinguishes records, target identifies one rendered backend line,
 * and body preserves the exact submitted text. It must not represent a backend
 * comment, unsubmitted textarea input, author identity, or delivery state.
 */
type MockComment = {
  id: string;
  target: CommentTarget;
  body: string;
};

/**
 * Represents one unfinished page-lifetime mock comment.
 *
 * Target identifies the exact rendered backend line and the non-empty body is
 * material the user may revise, submit, or clear. It must not represent a
 * posted comment, author identity, or delivery state.
 */
type MockCommentDraft = {
  target: CommentTarget;
  body: string;
};

declare global {
  /**
   * Exposes both authoritative page-lifetime comment stores for inspection.
   *
   * DiffGrid assigns these properties during module initialization to their
   * Solid store proxies. Consumers may inspect records but must not replace or
   * mutate either array outside DiffGrid's validated editing actions.
   */
  interface Window {
    /** Contains comments submitted through the mock composer. */
    COMMENTS: MockComment[];

    /** Contains unfinished comments keyed by their exact rendered line. */
    COMMENT_DRAFTS: MockCommentDraft[];
  }
}

/**
 * Holds every mock comment posted during the current browser page lifetime.
 *
 * This module-lifetime Solid store is the single authoritative representation:
 * mounted floaters react to it directly, and `window.COMMENTS` exposes the same
 * proxy for inspection. Page reload intentionally discards all records.
 */
const [mockComments, setMockComments] = createStore<MockComment[]>([]);
window.COMMENTS = mockComments;

/**
 * Holds every unfinished mock comment during the current browser page lifetime.
 *
 * This module-lifetime Solid store is authoritative for textarea contents
 * across floater and DiffGrid disposal. `window.COMMENT_DRAFTS` exposes the
 * same proxy for inspection; submitting or clearing an entry removes it.
 */
const [mockCommentDrafts, setMockCommentDrafts] = createStore<
  MockCommentDraft[]
>([]);
window.COMMENT_DRAFTS = mockCommentDrafts;

/**
 * Describes one mounted comment floater and its imperative row attachment.
 *
 * The target supplies visible context, the code cell supplies exact viewport
 * geometry, and the nested trigger receives restored focus when the user
 * dismisses the floater. The value exists only while its exact rendered row
 * remains connected and must not survive a DiffGrid row replacement.
 */
type CommentFloaterState = {
  target: CommentTarget;
  codeCell: HTMLElement;
  trigger: HTMLButtonElement;
};

/**
 * Renders one complete immutable text FileDiff using the established grid DOM.
 *
 * Callers provide the stable manifest file index, canonical display name,
 * explicit nullable notebook region, labels, validated rows and fold hints,
 * current view, and both fold policies. Every pinnable line receives its exact
 * side and line coordinates; the component supplies file and region from these
 * typed inputs. It owns its rendered row DOM, one delegated activation listener,
 * and at most one comment floater. Posted mock comments outlive the component in
 * the module store; DiffGrid performs no fetching, navigation, backend
 * persistence, or file-level state.
 */
export function DiffGrid(props: {
  fileIndex: number;
  displayName: string;
  region: string | null;
  leftLabel: string;
  rightLabel: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  viewMode: DiffViewMode;
  aggressiveFolds: boolean;
  combineInsertOnlyReplaceRows: boolean;
  linePins: LinePins;
}) {
  return (
    <div
      class="diff-grid"
      classList={{
        "diff-grid-inline": props.viewMode === "inline",
        "diff-grid-combine-insert-only-replace": Boolean(
          props.combineInsertOnlyReplaceRows,
        ),
      }}
    >
      {props.viewMode === "inline" ? (
        <InlineHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
        />
      ) : (
        <SplitHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
        />
      )}
      <ImperativeDiffLines
        fileIndex={props.fileIndex}
        displayName={props.displayName}
        region={props.region}
        rows={props.rows}
        foldHints={props.foldHints}
        leftLabel={props.leftLabel}
        rightLabel={props.rightLabel}
        viewMode={props.viewMode}
        aggressiveFolds={props.aggressiveFolds}
        combineInsertOnlyReplaceRows={props.combineInsertOnlyReplaceRows}
        linePins={props.linePins}
      />
    </div>
  );
}

/**
 * Renders the two required side labels above a split diff grid.
 *
 * Both labels are complete backend display strings and remain visible for the
 * lifetime of this header; the component owns no row or view state.
 */
function SplitHeader(props: { leftLabel: string; rightLabel: string }) {
  return (
    <div class="diff-header-row">
      <div class="diff-pane-header diff-side-header">{props.leftLabel}</div>
      <div class="diff-pane-header diff-side-header">{props.rightLabel}</div>
    </div>
  );
}

/**
 * Renders the fixed old/new/code labels above an inline diff grid.
 *
 * Full backend labels remain available through the two line-column tooltips;
 * the component must not abbreviate or reinterpret them elsewhere.
 */
function InlineHeader(props: { leftLabel: string; rightLabel: string }) {
  return (
    <div class="diff-header-row inline-header-row">
      <div class="diff-pane-header inline-line-header" title={props.leftLabel}>
        old
      </div>
      <div class="diff-pane-header inline-line-header" title={props.rightLabel}>
        new
      </div>
      <div class="diff-pane-header">Code</div>
    </div>
  );
}

/**
 * Renders a temporary line-comment composer outside the imperative diff DOM.
 *
 * The caller supplies one connected code cell and its row's nested line
 * trigger. The component writes exact fixed-position geometry from the code
 * cell, follows scroll and resize while mounted, autofocuses the textarea,
 * edits its exact page-lifetime draft, posts that draft into the comment store,
 * dismisses on Escape or an explicit close action, and restores trigger focus.
 * It has no backend interface.
 */
function CommentFloater(props: {
  state: CommentFloaterState;
  onClose: () => void;
}): JSX.Element {
  let floater!: HTMLDivElement;
  let textarea!: HTMLTextAreaElement;
  let placementFrame: number | null = null;

  /**
   * Returns the single page-lifetime draft for this exact rendered line.
   *
   * Null means that the user has not entered any text for the target. Store
   * identity is returned directly so edits never copy a draft into local state.
   */
  const commentDraft = createMemo<MockCommentDraft | null>(() => {
    const storedDraft = mockCommentDrafts.find(
      (candidate) =>
        candidate.target.file === props.state.target.file &&
        candidate.target.region === props.state.target.region &&
        candidate.target.side === props.state.target.side &&
        candidate.target.line === props.state.target.line,
    );
    return storedDraft === undefined ? null : storedDraft;
  });

  /**
   * Writes the floater’s complete geometry from its exact rendered code cell.
   *
   * The connected cell must have a positive border-box width. The floater
   * starts at its block end, matches its inline edges without covering the line
   * number gutter, and uses only the remaining viewport height. A cell outside
   * the viewport hides its floater.
   */
  function placeAgainstCodeCell(): void {
    assert(
      props.state.codeCell.isConnected && floater.isConnected,
      "Comment floater and code cell must remain connected during placement.",
    );
    const codeRect = props.state.codeCell.getBoundingClientRect();
    assert(
      Number.isFinite(codeRect.left) &&
        Number.isFinite(codeRect.bottom) &&
        codeRect.width > 0,
      "Comment code cell must expose finite positive viewport geometry.",
    );
    floater.style.left = `${codeRect.left}px`;
    floater.style.top = `${codeRect.bottom}px`;
    floater.style.width = `${codeRect.width}px`;
    floater.style.maxHeight = `${Math.max(0, window.innerHeight - codeRect.bottom - 12)}px`;
    floater.style.visibility =
      codeRect.bottom > 0 &&
      codeRect.top < window.innerHeight &&
      codeRect.right > 0 &&
      codeRect.left < window.innerWidth
        ? "visible"
        : "hidden";
  }

  /**
   * Coalesces scroll and resize placement into the next rendered frame.
   *
   * At most one frame is pending. Running after the originating browser event
   * lets imperative row virtualization finish before geometry is read.
   */
  function schedulePlacement(): void {
    if (placementFrame !== null) {
      return;
    }
    placementFrame = requestAnimationFrame(() => {
      placementFrame = null;
      placeAgainstCodeCell();
    });
  }

  /**
   * Dismisses this transient floater and restores its initiating control.
   *
   * Callers invoke this only for user-directed dismissal. Renderer replacement
   * clears the parent state directly because its detached trigger cannot receive
   * focus.
   */
  function close(): void {
    props.onClose();
    if (props.state.trigger.isConnected) {
      props.state.trigger.focus();
    }
  }

  onMount(() => {
    placeAgainstCodeCell();
    window.addEventListener("scroll", schedulePlacement, {
      capture: true,
      passive: true,
    });
    window.addEventListener("resize", schedulePlacement);
    queueMicrotask(() => {
      if (textarea.isConnected) {
        textarea.focus();
      }
    });
  });
  onCleanup(() => {
    window.removeEventListener("scroll", schedulePlacement, true);
    window.removeEventListener("resize", schedulePlacement);
    if (placementFrame !== null) {
      cancelAnimationFrame(placementFrame);
    }
  });

  return (
    <div
      ref={floater}
      class="comment-floater"
      role="dialog"
      aria-label={`Comment on ${props.state.target.side === "left" ? "old" : "new"} line ${props.state.target.line}`}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          close();
        }
      }}
    >
      <header class="comment-floater-header">
        <div class="comment-floater-heading">
          <strong>
            {props.state.target.side === "left" ? "Old" : "New"} line{" "}
            {props.state.target.line}
          </strong>
          <span title={props.state.target.file}>
            {props.state.target.region === null
              ? props.state.target.file
              : `${props.state.target.file} · ${props.state.target.region}`}
          </span>
        </div>
        <button
          type="button"
          class="comment-floater-close"
          aria-label="Close comment"
          title="Close comment"
          onClick={close}
        >
          ×
        </button>
      </header>
      <p class="comment-floater-notice">
        Frontend mock — page-lifetime comments and drafts
      </p>
      <ol class="comment-floater-comments" aria-label="Comments on this line">
        <For
          each={mockComments.filter(
            (comment) =>
              comment.target.file === props.state.target.file &&
              comment.target.region === props.state.target.region &&
              comment.target.side === props.state.target.side &&
              comment.target.line === props.state.target.line,
          )}
        >
          {(comment) => (
            <li>
              <p>{comment.body}</p>
            </li>
          )}
        </For>
      </ol>
      <form
        class="comment-floater-form"
        onSubmit={(event) => {
          event.preventDefault();
          const storedDraft = commentDraft();
          assert(
            storedDraft !== null && storedDraft.body.trim().length > 0,
            "A mock comment must contain non-whitespace text.",
          );
          setMockComments(mockComments.length, {
            id: crypto.randomUUID(),
            target: { ...props.state.target },
            body: storedDraft.body,
          });
          const draftIndex = mockCommentDrafts.indexOf(storedDraft);
          assert(
            draftIndex >= 0,
            "Submitted mock comment draft must remain in its store.",
          );
          setMockCommentDrafts(
            produce((drafts) => {
              drafts.splice(draftIndex, 1);
            }),
          );
          props.state.trigger.classList.remove("line-comment-trigger-draft");
          props.state.trigger.classList.add("line-comment-trigger-commented");
        }}
      >
        <label class="comment-floater-field">
          <span>Comment</span>
          <textarea
            ref={textarea}
            rows="5"
            value={commentDraft()?.body ?? ""}
            placeholder="Leave a comment on this line…"
            onInput={(event) => {
              const body = event.currentTarget.value;
              const storedDraft = commentDraft();
              if (body.length === 0) {
                if (storedDraft !== null) {
                  const draftIndex = mockCommentDrafts.indexOf(storedDraft);
                  assert(
                    draftIndex >= 0,
                    "Cleared mock comment draft must remain in its store.",
                  );
                  setMockCommentDrafts(
                    produce((drafts) => {
                      drafts.splice(draftIndex, 1);
                    }),
                  );
                }
                props.state.trigger.classList.remove(
                  "line-comment-trigger-draft",
                );
                return;
              }
              if (storedDraft === null) {
                setMockCommentDrafts(mockCommentDrafts.length, {
                  target: { ...props.state.target },
                  body,
                });
              } else {
                const draftIndex = mockCommentDrafts.indexOf(storedDraft);
                assert(
                  draftIndex >= 0,
                  "Edited mock comment draft must remain in its store.",
                );
                setMockCommentDrafts(draftIndex, "body", body);
              }
              props.state.trigger.classList.add("line-comment-trigger-draft");
            }}
          />
        </label>
        <div class="comment-floater-actions">
          <span>Comments and drafts live until this page reloads.</span>
          <button
            type="button"
            class="comment-floater-secondary"
            onClick={close}
          >
            Cancel
          </button>
          <button
            type="submit"
            class="comment-floater-primary"
            disabled={
              commentDraft() === null ||
              commentDraft()?.body.trim().length === 0
            }
          >
            Comment
          </button>
        </div>
      </form>
    </div>
  );
}

/**
 * Tracks the most recently rendered number on each inline side.
 *
 * The state is local to one fragment render and suppresses duplicate line
 * numbers created when a backend row expands into multiple inline rows.
 */
type InlineLineNumberState = {
  leftNo: number | null;
  rightNo: number | null;
};

/**
 * Describes the file-local preparation operation attached to one DiffGrid row root.
 *
 * FileCard supplies complete semantic coordinates and an AbortSignal. The
 * operation unfolds the exact local row and returns it without scrolling,
 * painting, URL mutation, file expansion, or file materialization.
 */
type PreparableDiffLines = HTMLDivElement & {
  prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine>;
};

/**
 * Synchronizes reactive DiffGrid inputs into one exclusively owned DOM root.
 *
 * The Solid effect runs initially and whenever rows, labels, view, file name,
 * or fold policies change. It clears local expanded-fold state when any such
 * renderer input changes and replaces every child of its root atomically. One
 * delegated root listener handles line pins and comment triggers across all
 * explicit row replacements. Solid renders at most one frontend-only comment
 * floater through `document.body`; renderer replacement closes it when its
 * trigger disappears. The listener and preparation operation are removed on
 * cleanup.
 */
function ImperativeDiffLines(props: {
  fileIndex: number;
  displayName: string;
  region: string | null;
  rows: DiffRow[];
  foldHints: FoldHint[];
  leftLabel: string;
  rightLabel: string;
  viewMode: DiffViewMode;
  aggressiveFolds: boolean;
  combineInsertOnlyReplaceRows: boolean;
  linePins: LinePins;
}) {
  let root!: PreparableDiffLines;
  const expandedFolds = new Set<number>();
  const [activeComment, setActiveComment] =
    createSignal<CommentFloaterState | null>(null);
  let previousDisplayName: string | undefined;
  let previousRows: DiffRow[] | undefined;
  let previousFoldHints: FoldHint[] | undefined;
  let previousLeftLabel: string | undefined;
  let previousRightLabel: string | undefined;
  let previousViewMode: DiffViewMode | undefined;
  let previousAggressiveFolds: boolean | undefined;
  let previousCombineInsertOnlyReplaceRows: boolean | undefined;

  /**
   * Resolves the unique rendered row for one exact target inside this DiffGrid.
   *
   * The caller must supply this DiffGrid's typed file and nullable region
   * identity. `null` means only that the exact side and line are not currently
   * rendered. A target from another region, duplicate coordinate, or line
   * detached from a valid row is a structural contradiction and throws.
   */
  function renderedRow(target: LinePinTarget): HTMLElement | null {
    if (target.file !== props.displayName || target.region !== props.region) {
      throw new Error("DiffGrid received a line target from another region.");
    }
    const matchingLines = Array.from(
      root.querySelectorAll<HTMLElement>(".line-no[data-line-pin-line]"),
    ).filter(
      (lineNumber) =>
        lineNumber.dataset.linePinSide === target.side &&
        lineNumber.dataset.linePinLine === target.line,
    );
    if (matchingLines.length > 1) {
      throw new Error("DiffGrid contains duplicate line-pin coordinates.");
    }
    const lineNumber = matchingLines[0];
    if (lineNumber === undefined) {
      return null;
    }
    const row = lineNumber.closest<HTMLElement>(".diff-row");
    if (row === null || !root.contains(row)) {
      throw new Error("Pinnable line has no DiffGrid row.");
    }
    return row;
  }

  /**
   * Routes direct line-number interaction to comments or the established pin.
   *
   * The delegated root listener passes every click to this operation. A comment
   * trigger toggles its exact code-aligned Solid floater; another trigger
   * switches that single floater to its line. Neither action changes the URL.
   * Other pinnable line-number clicks retain the exact existing pin behavior;
   * no path scrolls, loads a file, or selects a hunk.
   */
  function handleLineActivation(event: MouseEvent): void {
    const clicked = event.target;
    if (!(clicked instanceof HTMLElement)) {
      return;
    }
    const lineNumber = clicked.closest<HTMLElement>(
      ".line-no[data-line-pin-line]",
    );
    if (lineNumber === null || !root.contains(lineNumber)) {
      return;
    }
    event.stopPropagation();
    const commentTrigger = clicked.closest(".line-comment-trigger");
    if (commentTrigger !== null) {
      assert(
        commentTrigger instanceof HTMLButtonElement &&
          lineNumber.contains(commentTrigger),
        "Comment trigger must be a button inside its line number.",
      );
    }
    const side = lineNumber.dataset.linePinSide;
    const line = lineNumber.dataset.linePinLine;
    if (side !== "left" && side !== "right") {
      throw new Error("Pinnable line has an invalid side identity.");
    }
    if (line === undefined || !/^[1-9]\d*$/u.test(line)) {
      throw new Error("Pinnable line has an invalid backend line identity.");
    }
    if (commentTrigger !== null) {
      const comment = untrack(activeComment);
      if (comment !== null && comment.trigger === commentTrigger) {
        setActiveComment(null);
        return;
      }
      const commentRowPart = lineNumber.parentElement;
      const commentCodeCell =
        commentRowPart?.querySelector<HTMLElement>(":scope > .line-code") ??
        null;
      assert(
        commentRowPart instanceof HTMLElement &&
          (commentRowPart.classList.contains("diff-side") ||
            commentRowPart.classList.contains("inline-diff-row")) &&
          root.contains(commentRowPart) &&
          commentCodeCell !== null,
        "Comment trigger must belong to a rendered diff code cell.",
      );
      setActiveComment({
        target: {
          file: props.displayName,
          region: props.region,
          side,
          line,
        },
        codeCell: commentCodeCell,
        trigger: commentTrigger,
      });
      return;
    }
    const target: LinePinTarget = {
      file: props.displayName,
      region: props.region,
      side,
      line,
    };
    const row = renderedRow(target);
    if (row === null) {
      throw new Error("Activated line disappeared from its DiffGrid.");
    }
    const changeSetRoot = root.closest<HTMLElement>("[data-change-set-root]");
    if (changeSetRoot === null) {
      throw new Error("DiffGrid requires its ChangeSet root.");
    }
    const paintedRows =
      changeSetRoot.querySelectorAll<HTMLElement>(".pinned-line");
    if (paintedRows.length > 1) {
      throw new Error("ChangeSet contains multiple painted line pins.");
    }
    paintedRows[0]?.classList.remove("pinned-line");
    if (props.linePins.toggleUrlState(target) === "pinned") {
      row.classList.add("pinned-line");
    }
  }

  /**
   * Restores decoration for an already-routed URL pin after row rendering.
   *
   * The caller must first verify that the valid URL target belongs to this
   * DiffGrid. This operation tolerates a currently absent row and never paints
   * an expanded fold edge. It changes no URL identity and performs no loading,
   * scrolling, or hunk selection.
   */
  function restoreOldPin(): void {
    const parsed = props.linePins.parseUrl();
    // we dont validatate the pin, it must be checked elsewhere
    if (parsed.state === "valid") {
      const row = renderedRow(parsed.target);
      // we ignore missing rows too, again, must be checked elsewhere
      if (row === null) {
        return;
      }
      // row is not a fold-edge
      if (!row.classList.contains("fold-toggle-row")) {
        row.classList.add("pinned-line");
      }
    }
  }

  /**
   * Unfolds and returns one exact line from this complete immutable DiffGrid.
   *
   * FileCard has already expanded and materialized the owning FullFile. Missing
   * complete-file coordinates return `missing`; cancellation returns `stopped`;
   * duplicate or malformed renderer identity throws visibly.
   */
  async function prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine> {
    if (target.file !== props.displayName || target.region !== props.region) {
      throw new Error("DiffGrid preparation received the wrong target.");
    }
    if (abortSignal.aborted || !root.isConnected) {
      return { state: "stopped" };
    }
    const lineNumber = Number(target.line);
    const matchingRowIndexes = props.rows.flatMap((row, rowIndex) => {
      const candidate = target.side === "left" ? row.left_no : row.right_no;
      return candidate === lineNumber ? [rowIndex] : [];
    });
    if (matchingRowIndexes.length === 0) {
      return { state: "missing" };
    }
    if (matchingRowIndexes.length !== 1) {
      throw new Error("Backend rows contain duplicate line-pin coordinates.");
    }
    const targetRowIndex = matchingRowIndexes[0];
    if (targetRowIndex === undefined) {
      throw new Error("Matched line row index disappeared.");
    }
    const remaining = [
      ...addFoldRows(props.rows, props.foldHints, props.aggressiveFolds),
    ];
    while (remaining.length > 0) {
      const candidate = remaining.shift();
      if (candidate === undefined || !isFoldRow(candidate)) {
        continue;
      }
      if (
        targetRowIndex >= candidate.startRow &&
        targetRowIndex < candidate.startRow + candidate.count
      ) {
        expandedFolds.add(candidate.startRow);
      }
      remaining.unshift(...candidate.foldedRows);
    }
    render();
    await Promise.resolve();
    if (abortSignal.aborted || !root.isConnected) {
      return { state: "stopped" };
    }
    const row = renderedRow(target);
    return row === null ? { state: "missing" } : { state: "ready", row };
  }

  /**
   * Rebuilds the complete owned row subtree from current reactive inputs.
   *
   * The function resets local fold expansion only when an identity-bearing
   * renderer input changes and atomically replaces every child of `root`.
   */
  const render = () => {
    /**
     * Applies transient UI state after any complete or local row replacement.
     *
     * A floater whose imperative trigger disappeared is closed explicitly.
     * Every current comment trigger derives persistent visibility from the
     * page-lifetime comment and draft stores. Matching URL decoration is then
     * restored through the established single routing point; invalid targets
     * remain ChangeSet restoration's concern.
     */
    function afterRowsChanged(): void {
      const comment = untrack(activeComment);
      if (comment !== null && !comment.trigger.isConnected) {
        setActiveComment(null);
      }
      for (const lineNumber of root.querySelectorAll<HTMLElement>(
        ".line-no[data-line-pin-line]",
      )) {
        const trigger = lineNumber.querySelector(
          ":scope > .line-comment-trigger",
        );
        const side = lineNumber.dataset.linePinSide;
        const line = lineNumber.dataset.linePinLine;
        assert(
          trigger instanceof HTMLButtonElement &&
            (side === "left" || side === "right") &&
            line !== undefined &&
            /^[1-9]\d*$/u.test(line),
          "Rendered comment trigger must expose its exact line identity.",
        );
        // Row refresh is explicit; observing the stores here would rebuild every
        // imperative row after an edit instead of changing only its trigger.
        const storedState = untrack(() => ({
          hasComments: mockComments.some(
            (storedComment) =>
              storedComment.target.file === props.displayName &&
              storedComment.target.region === props.region &&
              storedComment.target.side === side &&
              storedComment.target.line === line,
          ),
          hasDraft: mockCommentDrafts.some(
            (storedDraft) =>
              storedDraft.target.file === props.displayName &&
              storedDraft.target.region === props.region &&
              storedDraft.target.side === side &&
              storedDraft.target.line === line,
          ),
        }));
        trigger.classList.toggle(
          "line-comment-trigger-commented",
          storedState.hasComments,
        );
        trigger.classList.toggle(
          "line-comment-trigger-draft",
          storedState.hasDraft,
        );
      }
      const parsed = props.linePins.parseUrl();
      if (
        parsed.state === "valid" &&
        parsed.target.file === props.displayName &&
        parsed.target.region === props.region
      ) {
        restoreOldPin();
      }
    }

    const inputChanged = [
      props.displayName !== previousDisplayName,
      props.rows !== previousRows,
      props.foldHints !== previousFoldHints,
      props.leftLabel !== previousLeftLabel,
      props.rightLabel !== previousRightLabel,
      props.viewMode !== previousViewMode,
      props.aggressiveFolds !== previousAggressiveFolds,
      props.combineInsertOnlyReplaceRows !==
        previousCombineInsertOnlyReplaceRows,
    ].some(Boolean);
    if (inputChanged) {
      expandedFolds.clear();
      previousDisplayName = props.displayName;
      previousRows = props.rows;
      previousFoldHints = props.foldHints;
      previousLeftLabel = props.leftLabel;
      previousRightLabel = props.rightLabel;
      previousViewMode = props.viewMode;
      previousAggressiveFolds = props.aggressiveFolds;
      previousCombineInsertOnlyReplaceRows = props.combineInsertOnlyReplaceRows;
    }

    const rows = addFoldRows(
      props.rows,
      props.foldHints,
      props.aggressiveFolds,
    );
    const fragment =
      props.viewMode === "inline" && props.combineInsertOnlyReplaceRows === true
        ? renderCombinedInlineRowsDom(
            rows,
            expandedFolds,
            props.fileIndex,
            0,
            afterRowsChanged,
          )
        : props.viewMode === "inline"
          ? renderInlineRowsDom(
              rows,
              expandedFolds,
              props.fileIndex,
              0,
              afterRowsChanged,
            )
          : renderSplitRowsDom(
              rows,
              props.leftLabel,
              props.rightLabel,
              expandedFolds,
              props.fileIndex,
              0,
              afterRowsChanged,
            );
    root.replaceChildren(fragment);
    afterRowsChanged();
  };

  createEffect(render);

  onMount(() => {
    root.prepareLine_impl = prepareLine_impl;
    root.addEventListener("click", handleLineActivation);
    onCleanup(() => {
      root.removeEventListener("click", handleLineActivation);
      Reflect.deleteProperty(root, "prepareLine_impl");
    });
  });

  return (
    <>
      <div ref={root} class="diff-lines" />
      <Show when={activeComment()} keyed>
        {(state) => (
          <Portal mount={document.body}>
            <CommentFloater
              state={state}
              onClose={() => setActiveComment(null)}
            />
          </Portal>
        )}
      </Show>
    </>
  );
}

/**
 * Renders an ordered `RenderRow` range into split-view DOM.
 *
 * `startRow` is the required source-row offset for stable row identity. Folded
 * ranges advance by their represented count while ordinary rows advance once.
 */
function renderSplitRowsDom(
  rows: RenderRow[],
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  startRow: number,
  onRowsChanged: () => void,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      fragment.append(
        renderSplitFoldDom(
          row,
          rowIndex,
          leftLabel,
          rightLabel,
          expandedFolds,
          fileIndex,
          onRowsChanged,
        ),
      );
      cursor += row.count;
    } else {
      fragment.append(renderSplitDiffRowDom(row, rowIndex, fileIndex));
      cursor += 1;
    }
  });
  return fragment;
}

/**
 * Renders an ordered `RenderRow` range into ordinary inline-view DOM.
 *
 * `startRow` is the required source-row offset. One backend row may emit two
 * visible rows, while shared line-number state suppresses duplicate numbers.
 */
function renderInlineRowsDom(
  rows: RenderRow[],
  expandedFolds: Set<number>,
  fileIndex: number,
  startRow: number,
  onRowsChanged: () => void,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const lineNumberState: InlineLineNumberState = {
    leftNo: null,
    rightNo: null,
  };
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      fragment.append(
        renderInlineFoldDom(
          row,
          rowIndex,
          expandedFolds,
          fileIndex,
          false,
          onRowsChanged,
        ),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      fragment.append(
        renderInlineDiffRowsDom(row, rowIndex, fileIndex, lineNumberState),
      );
      cursor += 1;
    }
  });
  return fragment;
}

/**
 * Renders inline rows with the engine-specific insert-only replacement combination.
 *
 * The required source-row offset and fold set retain the same identity rules as
 * ordinary inline rendering; rows outside the narrow combination predicate remain
 * byte-for-byte equivalent in presentation structure.
 */
function renderCombinedInlineRowsDom(
  rows: RenderRow[],
  expandedFolds: Set<number>,
  fileIndex: number,
  startRow: number,
  onRowsChanged: () => void,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const lineNumberState: InlineLineNumberState = {
    leftNo: null,
    rightNo: null,
  };
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      fragment.append(
        renderInlineFoldDom(
          row,
          rowIndex,
          expandedFolds,
          fileIndex,
          true,
          onRowsChanged,
        ),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      fragment.append(
        renderCombinedInlineDiffRowsDom(
          row,
          rowIndex,
          fileIndex,
          lineNumberState,
        ),
      );
      cursor += 1;
    }
  });
  return fragment;
}

/**
 * Creates one stateful split-view fold subtree around an immutable FoldRow.
 *
 * Expansion lives only in the owning DiffGrid set. Toggling replaces this
 * wrapper's children; validated folded context contains no hunk boundaries.
 */
function renderSplitFoldDom(
  row: FoldRow,
  rowIndex: number,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  onRowsChanged: () => void,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  /**
   * Toggles this source-row range in the owning local expansion set.
   *
   * The mutation is DiffGrid-local and immediately rebuilds only this fold
   * wrapper; it never changes file or ChangeSet expansion.
   */
  const toggle = () => {
    if (expandedFolds.has(rowIndex)) {
      expandedFolds.delete(rowIndex);
    } else {
      expandedFolds.add(rowIndex);
    }
    renderFold();
    onRowsChanged();
  };

  /**
   * Replaces this split fold wrapper with its bar or complete expanded rows.
   *
   * Expanded rows preserve source offsets and receive one fold affordance;
   * folded rows render only the backend-provided fold bar.
   */
  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const fragment = renderSplitRowsDom(
        row.foldedRows,
        leftLabel,
        rightLabel,
        expandedFolds,
        fileIndex,
        row.startRow,
        onRowsChanged,
      );
      attachExpandedFoldToggle(fragment, toggle);
      wrapper.replaceChildren(fragment);
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "diff-row fold-bar";
    button.dataset.rowIndex = String(rowIndex);
    button.title = "Expand folded rows";
    button.addEventListener("click", toggle);
    button.append(
      createFoldSideDom(row.count, row.label, leftLabel),
      createFoldSideDom(row.count, row.label, rightLabel),
    );
    wrapper.replaceChildren(button);
  };

  renderFold();
  return wrapper;
}

/**
 * Creates one stateful inline-view fold subtree around an immutable FoldRow.
 *
 * The required row-combination policy is propagated into expanded nested rows. The
 * wrapper owns only its current DOM children and local toggle listeners.
 */
function renderInlineFoldDom(
  row: FoldRow,
  rowIndex: number,
  expandedFolds: Set<number>,
  fileIndex: number,
  combineInsertOnlyReplaceRows: boolean,
  onRowsChanged: () => void,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  /**
   * Toggles this source-row range in the owning local expansion set.
   *
   * The mutation is DiffGrid-local and immediately rebuilds only this fold
   * wrapper; it never changes file or ChangeSet expansion.
   */
  const toggle = () => {
    if (expandedFolds.has(rowIndex)) {
      expandedFolds.delete(rowIndex);
    } else {
      expandedFolds.add(rowIndex);
    }
    renderFold();
    onRowsChanged();
  };

  /**
   * Replaces this inline fold wrapper with its bar or complete expanded rows.
   *
   * Expanded rows retain the required row-combination policy and source offsets;
   * folded rows render only the backend-provided fold bar.
   */
  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const rows = row.foldedRows;
      const fragment =
        combineInsertOnlyReplaceRows === true
          ? renderCombinedInlineRowsDom(
              rows,
              expandedFolds,
              fileIndex,
              row.startRow,
              onRowsChanged,
            )
          : renderInlineRowsDom(
              rows,
              expandedFolds,
              fileIndex,
              row.startRow,
              onRowsChanged,
            );
      attachExpandedFoldToggle(fragment, toggle);
      wrapper.replaceChildren(fragment);
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "diff-row inline-diff-row inline-fold-bar";
    button.dataset.rowIndex = String(rowIndex);
    button.title = "Expand folded rows";
    button.addEventListener("click", toggle);
    const lineText = foldLineText(row.count);
    const label =
      row.label.length > 0
        ? `... ${lineText} in ${row.label}`
        : `... ${lineText}`;
    button.append(
      createPlainLineNumberDom(".."),
      createPlainLineNumberDom(".."),
      createElementWithClass("div", "fold-label inline-fold-label", label),
    );
    wrapper.replaceChildren(button);
  };

  renderFold();
  return wrapper;
}

/**
 * Describes the local interactive control attached to an expanded fold row.
 *
 * `expanded` determines visual treatment and `onToggle` mutates only the owning
 * DiffGrid's local fold set; it is not file-expansion or application state.
 */
type FoldToggle = { expanded: boolean; onToggle: () => void };

/**
 * Attaches the fold affordance to the first visible row of an expanded fold.
 *
 * An empty fragment is accepted and produces no affordance. The listener is
 * owned by the supplied fragment and disappears when its DOM is replaced. The
 * complete first row is the expanded fold edge, so its activation never reaches
 * delegated line-pin handling even though it displays backend line numbers.
 */
function attachExpandedFoldToggle(
  fragment: DocumentFragment,
  onToggle: () => void,
) {
  const row = fragment.querySelector(
    ".diff-row:not(.fold-bar):not(.inline-fold-bar)",
  );
  if (!(row instanceof HTMLElement)) {
    return;
  }
  row.classList.add("fold-toggle-row", "fold-expanded");
  row.title = "Fold rows";
  row.addEventListener("click", (event) => {
    event.stopPropagation();
    onToggle();
  });

  const lineNumber = row.querySelector(".line-no");
  if (lineNumber instanceof HTMLElement) {
    lineNumber.prepend(createFoldToggleButtonDom({ expanded: true, onToggle }));
  }
}

/**
 * Renders one ordinary backend row as a two-sided split-view element.
 *
 * The required source index and file index become stable DOM identity. Fold
 * disclosure is attached separately to the first expanded row.
 */
function renderSplitDiffRowDom(
  row: DiffRow,
  rowIndex: number,
  fileIndex: number,
): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(row.status, row, "");
  element.dataset.rowIndex = String(rowIndex);
  if (row.hunk_index !== null) {
    const identity: RealHunkIdentity = {
      fileIndex,
      kind: "real",
      hunkIndex: row.hunk_index,
    };
    element.dataset.hunkTarget = "";
    element.dataset.hunkKind = identity.kind;
    element.dataset.fileIndex = String(identity.fileIndex);
    element.dataset.hunkIndex = String(identity.hunkIndex);
  }
  element.append(
    createDiffSideDom(row, "left"),
    createDiffSideDom(row, "right"),
  );
  return element;
}

/**
 * Renders one backend row as the one or two elements required by inline view.
 *
 * Equal, insert, and delete rows emit one element; replace and move rows may
 * emit both sides while transferring hunk identity to the first visible side.
 * Unsupported backend statuses throw exhaustively.
 */
function renderInlineDiffRowsDom(
  row: DiffRow,
  rowIndex: number,
  fileIndex: number,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  /**
   * Reports whether one inline side has a line identity or visible text.
   *
   * Null plus empty text is the only absent-side representation; zero and an
   * empty line with a number remain present.
   */
  function inlineSideExists(lineNo: number | null, text: string): boolean {
    return lineNo !== null || text.length > 0;
  }

  const rightText = sideText(row, "right");
  const leftText = sideText(row, "left");
  // The resulting side supplies shared inline content when it is non-empty.
  const sharedText = rightText.length > 0 ? rightText : leftText;
  const sharedParts =
    row.right_parts.length > 0 ? row.right_parts : row.left_parts;

  switch (row.status) {
    case "equal":
      return renderInlineDiffRowDom({
        status: "equal",
        marker: " ",
        leftNo: row.left_no,
        rightNo: row.right_no,
        text: sharedText,
        parts: sharedParts,
        rowIndex,
        fileIndex,
        sourceRow: row,
        lineNumberState,
        tokenRowStatus: null,
      });
    case "delete":
      return renderInlineDiffRowDom({
        status: "delete",
        marker: "-",
        leftNo: row.left_no,
        rightNo: null,
        text: leftText,
        parts: row.left_parts,
        rowIndex,
        fileIndex,
        sourceRow: row,
        lineNumberState,
        tokenRowStatus: null,
      });
    case "insert":
      return renderInlineDiffRowDom({
        status: "insert",
        marker: "+",
        leftNo: null,
        rightNo: row.right_no,
        text: rightText,
        parts: row.right_parts,
        rowIndex,
        fileIndex,
        sourceRow: row,
        lineNumberState,
        tokenRowStatus: null,
      });
    case "replace": {
      const fragment = document.createDocumentFragment();
      const hasLeftSide = inlineSideExists(row.left_no, leftText);
      const hasRightSide = inlineSideExists(row.right_no, rightText);
      if (hasLeftSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "delete",
            marker: "-",
            leftNo: row.left_no,
            rightNo: null,
            text: leftText,
            parts: row.left_parts,
            rowIndex,
            fileIndex,
            sourceRow: row,
            lineNumberState,
            tokenRowStatus: "replace",
          }),
        );
      }
      if (hasRightSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "insert",
            marker: "+",
            leftNo: null,
            rightNo: row.right_no,
            text: rightText,
            parts: row.right_parts,
            rowIndex,
            fileIndex,
            sourceRow: {
              ...row,
              hunk_index: hasLeftSide ? null : row.hunk_index,
            },
            lineNumberState,
            tokenRowStatus: "replace",
          }),
        );
      }
      return fragment;
    }
    case "move": {
      const fragment = document.createDocumentFragment();
      const hasLeftSide = inlineSideExists(row.left_no, leftText);
      const hasRightSide = inlineSideExists(row.right_no, rightText);
      if (hasLeftSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "move",
            marker: "*",
            leftNo: row.left_no,
            rightNo: null,
            text: leftText,
            parts: row.left_parts,
            rowIndex,
            fileIndex,
            sourceRow: row,
            lineNumberState,
            tokenRowStatus: null,
          }),
        );
      }
      if (hasRightSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "move",
            marker: "*",
            leftNo: null,
            rightNo: row.right_no,
            text: rightText,
            parts: row.right_parts,
            rowIndex,
            fileIndex,
            sourceRow: {
              ...row,
              hunk_index: hasLeftSide ? null : row.hunk_index,
            },
            lineNumberState,
            tokenRowStatus: null,
          }),
        );
      }
      return fragment;
    }
    default: {
      // Keep this assignment exhaustive when the backend RowStatus union grows.
      const unhandledStatus: never = row.status;
      throw new Error(`Unhandled diff row status: ${String(unhandledStatus)}.`);
    }
  }
}

/**
 * Renders one inline row with the narrow insert-only replacement optimization.
 *
 * Rows failing the exact combination predicate delegate to ordinary inline
 * rendering. Combined rows retain both backend line numbers and hunk identity.
 */
function renderCombinedInlineDiffRowsDom(
  row: DiffRow,
  rowIndex: number,
  fileIndex: number,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  if (!canCombineInsertOnlyReplaceRow(row)) {
    return renderInlineDiffRowsDom(row, rowIndex, fileIndex, lineNumberState);
  }

  const rightText = sideText(row, "right");
  return renderInlineDiffRowDom({
    status: "replace",
    marker: " ",
    leftNo: row.left_no,
    rightNo: row.right_no,
    text: rightText,
    parts: row.right_parts,
    rowIndex,
    fileIndex,
    sourceRow: row,
    lineNumberState,
    tokenRowStatus: null,
  });
}

/**
 * Recognizes replacement rows whose changed tokens exist only as insertions.
 *
 * The predicate is deliberately strict: any changed old-side token or any
 * non-insert new-side token preserves the ordinary two-line representation.
 */
function canCombineInsertOnlyReplaceRow(row: DiffRow): boolean {
  if (row.status !== "replace") {
    return false;
  }
  const oldSideHasChanges = row.left_parts.some(
    (part) => part.diff_status !== "unchanged",
  );
  const rightChangedStatuses = row.right_parts
    .filter((part) => part.diff_status !== "unchanged")
    .map((part) => part.diff_status);
  return (
    !oldSideHasChanges &&
    rightChangedStatuses.length > 0 &&
    rightChangedStatuses.every((status) => status === "insert")
  );
}

/**
 * Creates one concrete inline row element from a complete render description.
 *
 * Callers provide both line numbers, decorated text inputs, stable source-row
 * identity, and explicit token-row override state. Missing sides and absent
 * overrides use null, never undefined; only the new DOM element is mutated.
 */
function renderInlineDiffRowDom(props: {
  status: InlineRowStatus;
  marker: InlineMarker;
  leftNo: number | null;
  rightNo: number | null;
  text: string;
  parts: DecoratedPart[];
  rowIndex: number;
  fileIndex: number;
  sourceRow: DiffRow;
  lineNumberState: InlineLineNumberState;
  tokenRowStatus: InlineRowStatus | null;
}): HTMLElement {
  const element = document.createElement("div");
  const displayedLeftNo = inlineDisplayLineNo(
    props.leftNo,
    "left",
    props.lineNumberState,
  );
  const displayedRightNo = inlineDisplayLineNo(
    props.rightNo,
    "right",
    props.lineNumberState,
  );
  element.className = diffRowClass(
    props.status,
    props.sourceRow,
    "inline-diff-row",
  );
  element.dataset.rowIndex = String(props.rowIndex);
  if (props.sourceRow.hunk_index !== null) {
    const identity: RealHunkIdentity = {
      fileIndex: props.fileIndex,
      kind: "real",
      hunkIndex: props.sourceRow.hunk_index,
    };
    element.dataset.hunkTarget = "";
    element.dataset.hunkKind = identity.kind;
    element.dataset.fileIndex = String(identity.fileIndex);
    element.dataset.hunkIndex = String(identity.hunkIndex);
  }
  element.append(
    createLineNumberDom(displayedLeftNo, "left"),
    createLineNumberDom(displayedRightNo, "right"),
    createInlineLineCodeDom(
      props.marker,
      props.text,
      props.parts,
      props.tokenRowStatus === null ? props.status : props.tokenRowStatus,
    ),
  );
  return element;
}

/**
 * Suppresses immediately repeated inline line numbers on one rendered side.
 *
 * A missing local state intentionally disables suppression. Null is a real
 * absent-side value and never changes the remembered number.
 */
function inlineDisplayLineNo(
  lineNo: number | null,
  side: Side,
  state: InlineLineNumberState,
): number | null {
  if (lineNo === null) {
    return lineNo;
  }
  const previousLineNo = side === "left" ? state.leftNo : state.rightNo;
  if (side === "left") {
    state.leftNo = lineNo;
  } else {
    state.rightNo = lineNo;
  }
  return previousLineNo === lineNo ? null : lineNo;
}

/**
 * Builds the complete established class list for one rendered diff row.
 *
 * The required extra class may be empty and affects presentation only;
 * whitespace and hunk classes derive from validated row data.
 */
function diffRowClass(
  status: string,
  row: DiffRow,
  extraClass: string,
): string {
  const classes = ["diff-row"];
  if (extraClass.length > 0) {
    classes.push(extraClass);
  }
  classes.push(status);
  if (row.hunk_index !== null) {
    classes.push("hunk-anchor");
  }
  if (["replace", "insert", "delete", "move"].includes(status)) {
    const changedParts = [...row.left_parts, ...row.right_parts].filter(
      (part) => part.diff_status !== "unchanged",
    );
    if (
      changedParts.length > 0 &&
      changedParts.every((part) => part.is_whitespace)
    ) {
      classes.push("whitespace-only-change");
    }
  }
  return classes.join(" ");
}

/**
 * Creates one split-view side with line identity and decorated code content.
 *
 * Callers supply a validated backend row and exact side. Null line numbers and
 * empty text produce the established empty-side treatment rather than a husk.
 */
function createDiffSideDom(row: DiffRow, side: Side): HTMLElement {
  const lineNo = side === "left" ? row.left_no : row.right_no;
  const text = sideText(row, side);
  const parts = side === "left" ? row.left_parts : row.right_parts;
  const element = document.createElement("div");
  element.className = `diff-side side-${side}${
    lineNo === null && text === "" ? " empty-side" : ""
  }`;
  const codeElement = document.createElement("code");
  codeElement.className = "line-code";
  appendDecoratedText(codeElement, text, parts, row.status);
  element.append(createLineNumberDom(lineNo, side), codeElement);
  return element;
}

/**
 * Normalizes nullable backend text for one selected side to a render string.
 *
 * Backend null means that side is absent and becomes the empty string; no other
 * text normalization or whitespace trimming is performed.
 */
function sideText(row: DiffRow, side: Side): string {
  const text = side === "left" ? row.left_text : row.right_text;
  if (text === null) {
    return "";
  }
  return text;
}

/**
 * Creates one half of a split fold bar with its side-specific accessible label.
 *
 * The count must be the positive size of the represented FoldRow. The returned
 * element owns no toggle listener; its parent fold bar handles activation.
 */
function createFoldSideDom(
  count: number,
  label: string,
  sideLabel: string,
): HTMLElement {
  const element = document.createElement("div");
  element.className = "diff-side fold-side";
  element.dataset.sideLabel = sideLabel;
  element.append(
    createPlainLineNumberDom(".."),
    createElementWithClass(
      "div",
      "fold-label",
      label
        ? `... ${foldLineText(count)} in ${label}`
        : `... ${foldLineText(count)}`,
    ),
  );
  return element;
}

/**
 * Formats a validated fold-row count with the correct singular or plural noun.
 *
 * Callers provide the FoldRow count; this helper does not validate ranges or
 * add context such as the fold label.
 */
function foldLineText(count: number): string {
  return `${count} line${count === 1 ? "" : "s"}`;
}

/**
 * Creates one line-number cell and its exact line-local interaction coordinates.
 *
 * A visible line number contributes its exact side and backend line coordinate;
 * the enclosing DiffGrid contributes file and region identity during pin or
 * comment activation. Its nested comment button has no listener of its own and
 * is routed by the grid's delegated listener. An absent or duplicate-suppressed
 * number is null and therefore neither pinnable nor commentable.
 */
function createLineNumberDom(lineNo: number | null, side: Side): HTMLElement {
  const element = document.createElement("div");
  element.className = "line-no";
  if (lineNo !== null) {
    element.dataset.linePinSide = side;
    element.dataset.linePinLine = String(lineNo);
    element.title = "Pin line";
    const commentTrigger = document.createElement("button");
    commentTrigger.type = "button";
    commentTrigger.className = "line-comment-trigger";
    commentTrigger.ariaLabel = `Comment on ${side === "left" ? "old" : "new"} line ${lineNo}`;
    commentTrigger.title = "Add comment";
    commentTrigger.textContent = "+";
    element.append(commentTrigger);
  }
  element.append(lineNo === null ? "" : String(lineNo));
  return element;
}

/**
 * Creates a non-interactive line-number cell for fixed fold placeholders.
 *
 * The caller supplies complete visible text; no pin or fold metadata is added.
 */
function createPlainLineNumberDom(text: string): HTMLElement {
  return createElementWithClass("div", "line-no", text);
}

/**
 * Creates the accessible disclosure control attached to an expanded fold row.
 *
 * Activation stops row propagation and invokes exactly the supplied local
 * toggle callback; it does not mutate ChangeSet or browser state.
 */
function createFoldToggleButtonDom(foldToggle: FoldToggle): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "inline-fold-toggle";
  button.ariaLabel = foldToggle.expanded ? "Fold rows" : "Expand folded rows";
  button.textContent = foldToggle.expanded ? "▾" : "▸";
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    foldToggle.onToggle();
  });
  return button;
}

/**
 * Creates one inline-view code cell including its visible change marker.
 *
 * The marker is aria-hidden, while all supplied text remains native searchable
 * content. Token decoration uses the inline status mapped to backend semantics.
 */
function createInlineLineCodeDom(
  marker: InlineMarker,
  text: string,
  parts: DecoratedPart[],
  rowStatus: InlineRowStatus,
): HTMLElement {
  const element = document.createElement("code");
  element.className = "line-code inline-line-code";
  const markerElement = document.createElement("span");
  markerElement.className = "inline-marker";
  markerElement.ariaHidden = "true";
  markerElement.textContent = marker;
  element.append(markerElement);
  const inlineTokenRowStatus =
    rowStatus === "insert" || rowStatus === "delete" ? "replace" : rowStatus;
  appendDecoratedText(element, text, parts, inlineTokenRowStatus);
  return element;
}

/**
 * Appends native-searchable text from its backend-woven decoration parts.
 *
 * The parts must reconstruct `text` exactly. The function suppresses redundant
 * diff coloring already expressed by the row and mutates only `element`.
 */
function appendDecoratedText(
  element: HTMLElement,
  text: string,
  parts: DecoratedPart[],
  rowStatus: RowStatus,
) {
  /**
   * Recognizes tree-sitter syntax classes hidden by the established diff theme.
   *
   * Exact prefix names and their hyphenated descendants are suppressed;
   * unrelated class names remain visible.
   */
  function isSuppressedSyntaxClass(className: string): boolean {
    return suppressedSyntaxClassPrefixes.some(
      (prefix) => className === prefix || className.startsWith(`${prefix}-`),
    );
  }

  /**
   * Removes syntax decoration when every class is intentionally suppressed.
   *
   * Mixed visible/suppressed class lists pass through unchanged so established
   * token styling can choose the visible class; the input array is not mutated.
   */
  function visibleSyntaxClasses(classes: string[]): string[] {
    if (!classes.length || classes.every(isSuppressedSyntaxClass)) {
      return [];
    }
    return classes;
  }

  assert(
    parts.map((part) => part.text).join("") === text,
    "Decorated parts must reconstruct their complete row text.",
  );
  for (const part of parts) {
    const tokenChanged = part.diff_status !== "unchanged";
    const syntaxClasses = visibleSyntaxClasses(part.syntax_classes);
    if (syntaxClasses.length === 0 && !tokenChanged) {
      element.append(part.text);
      continue;
    }
    const span = document.createElement("span");
    const classes = syntaxClasses.length
      ? ["ts-token", ...new Set(syntaxClasses)]
      : [];
    const rowAlreadyShowsTokenChange =
      (rowStatus === "insert" || rowStatus === "delete") &&
      rowStatus === part.diff_status;
    const showTokenChange = tokenChanged && !rowAlreadyShowsTokenChange;
    if (showTokenChange) {
      classes.push("token-changed", `token-${part.diff_status}`);
    }
    if (showTokenChange && part.is_whitespace) {
      classes.push("whitespace");
    }
    if (showTokenChange && part.is_leading_whitespace) {
      classes.push("whitespace-leading");
    }
    span.className = classes.join(" ");
    if (showTokenChange && part.is_whitespace) {
      span.title = "Whitespace changed";
    }
    span.textContent = part.text;
    element.append(span);
  }
}

/**
 * Creates one typed HTML element with complete class and text content.
 *
 * The tag must be a standard HTMLElement tag. The returned element has no
 * event listeners, data attributes, or retained application state.
 */
function createElementWithClass<K extends keyof HTMLElementTagNameMap>(
  tagName: K,
  className: string,
  text: string,
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tagName);
  element.className = className;
  element.textContent = text;
  return element;
}
