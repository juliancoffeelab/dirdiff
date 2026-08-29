/**
 * Keeps one text bay's reactive inputs synchronized with imperative row DOM.
 *
 * Reactive changes rebuild the grid's exclusive row subtree through `rowDom.ts`.
 * A delegated listener routes line activation to the shared pin and review
 * interfaces, marker refreshes track canonical review revisions, and the root
 * exposes the line-preparation operation used by FileCard.
 *
 * The grid retains local expanded-fold state and renderer DOM only. It does not
 * fetch File data, retain ChangeSet state, navigate hunks, or decide whether its
 * containing bay is rich or virtual.
 */
import {
  ErrorBoundary,
  Show,
  createEffect,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";
import {
  type DiffRow,
  type FoldHint,
  type ReviewFilePair,
} from "../../../../api/api";
import type { DiffViewMode } from "../../../App";
import { addFoldRows, isFoldRow } from "./folds";
import type { LinePins, LinePinTarget, PreparedLine } from "../../../linePins";
import { assert, expect } from "../../../../utils";
import { presentError } from "../../../../comp/Toasts";
import {
  LineMarkerKeySchema,
  useReview,
  type ReviewMarkerKind,
  type ReviewTextGridBinding,
} from "../../../review/Review";
import { renderInlineRowsDom, renderSplitRowsDom, type Side } from "./rowDom";

/**
 * Renders one complete immutable text FileDiff using the established grid DOM.
 *
 * Callers provide the stable manifest file index, canonical display name,
 * the composed bay key this grid renders, labels, validated rows and fold hints,
 * current view, and both fold policies. Every pinnable line receives its exact
 * side and line coordinates; the component supplies file and bay from these
 * typed inputs. It owns its rendered row DOM, one delegated activation listener,
 * and routes review activation through its Snapshot-bound Review binding. A
 * marker-only child observes indexed review facts beside, never around, valid
 * row DOM.
 */
export function TextDiffGrid(props: {
  /**
   * Identifies the exact captured file pair used by review and line pins.
   * Both paths remain part of the identity even when one captured side is null.
   */
  reviewFile: ReviewFilePair;

  /**
   * Supplies the file's stable coordinate in the mounted ChangeSet manifest.
   * The grid writes it only to real hunk anchors for navigation enrichment.
   */
  fileIndex: number;

  /**
   * Names the file represented by these rows for renderer identity.
   * A changed name resets renderer-local fold expansion with the other inputs.
   */
  displayName: string;

  /**
   * Identifies the exact physical bay whose rows are rendered here.
   * It composes with file and line coordinates for review and navigation.
   */
  bayKey: string;

  /**
   * Provides the backend's display name for this bay's inline content column.
   * Callers must not substitute a generic code label for non-code bays.
   */
  contentLabel: string;

  /**
   * Provides the complete old-side label for split display and inline tooltip.
   * It is presentation text, not file or bay identity.
   */
  leftLabel: string;

  /**
   * Provides the complete new-side label for split display and inline tooltip.
   * It is presentation text, not file or bay identity.
   */
  rightLabel: string;

  /**
   * Contains validated backend diff rows in source order for this bay.
   * The component treats the array as immutable and replaces its DOM on change.
   */
  rows: DiffRow[];

  /**
   * Contains backend fold candidates whose coordinates refer to `rows`.
   * Invalid overlaps are rejected by the fold builder rather than repaired.
   */
  foldHints: FoldHint[];

  /**
   * Selects the current Tab-wide split or inline text presentation.
   * Changing it rebuilds the row DOM and clears local fold expansion.
   */
  viewMode: DiffViewMode;

  /**
   * Selects whether every valid fold hint initially becomes a folded range.
   * False retains context-sensitive folding; a change resets local expansion.
   */
  aggressiveFolds: boolean;

  /**
   * Supplies Snapshot-scoped URL line identity and pin activation behavior.
   * The grid paints only rows that this service reports as currently pinned.
   */
  linePins: LinePins;
}) {
  return (
    <div
      class="diff-grid"
      data-review-bay={props.bayKey}
      classList={{
        "diff-grid-inline": props.viewMode === "inline",
      }}
    >
      {props.viewMode === "inline" ? (
        <InlineHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
          contentLabel={props.contentLabel}
        />
      ) : (
        <SplitHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
        />
      )}
      <ImperativeDiffLines
        reviewFile={props.reviewFile}
        fileIndex={props.fileIndex}
        displayName={props.displayName}
        bayKey={props.bayKey}
        rows={props.rows}
        foldHints={props.foldHints}
        leftLabel={props.leftLabel}
        rightLabel={props.rightLabel}
        viewMode={props.viewMode}
        aggressiveFolds={props.aggressiveFolds}
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
function SplitHeader(props: {
  /**
   * Provides the complete visible label for the left diff side.
   * The header presents it unchanged for the lifetime of this render.
   */
  leftLabel: string;
  /**
   * Provides the complete visible label for the right diff side.
   * The header presents it unchanged for the lifetime of this render.
   */
  rightLabel: string;
}) {
  return (
    <div class="diff-header-row">
      <div class="diff-pane-header diff-side-header">{props.leftLabel}</div>
      <div class="diff-pane-header diff-side-header">{props.rightLabel}</div>
    </div>
  );
}

/**
 * Renders the old/new line columns and the backend-named content column.
 *
 * `contentLabel` is the backend's name for the bay this grid renders, so an
 * inline grid holding a notebook output or cell metadata says so instead of
 * calling every bay "Code". Full backend side labels remain available
 * through the two line-column tooltips; the component must not abbreviate or
 * reinterpret them elsewhere.
 */
function InlineHeader(props: {
  /**
   * Provides the complete old-side text exposed by the old-column tooltip.
   * The visible column heading remains the established abbreviated `old`.
   */
  leftLabel: string;
  /**
   * Provides the complete new-side text exposed by the new-column tooltip.
   * The visible column heading remains the established abbreviated `new`.
   */
  rightLabel: string;
  /**
   * Provides the backend name displayed over the shared content column.
   * It distinguishes code from other frame bays without local reinterpretation.
   */
  contentLabel: string;
}) {
  return (
    <div class="diff-header-row inline-header-row">
      <div class="diff-pane-header inline-line-header" title={props.leftLabel}>
        old
      </div>
      <div class="diff-pane-header inline-line-header" title={props.rightLabel}>
        new
      </div>
      <div class="diff-pane-header">{props.contentLabel}</div>
    </div>
  );
}

/**
 * Describes the file-local preparation operation attached to one TextDiffGrid row root.
 *
 * FileCard supplies complete semantic coordinates and an AbortSignal. The
 * operation unfolds the exact local row and returns it without scrolling,
 * painting, URL mutation, file expansion, or file materialization.
 */
type PreparableDiffLines = HTMLDivElement & {
  /**
   * Prepares one exact line target within this mounted text grid.
   *
   * `target` must carry this grid's file and bay identity. The operation may
   * expand a local folded range, but it does not scroll, paint, or update URL
   * state. It returns `stopped` if `abortSignal` is already aborted or becomes
   * aborted before the newly rendered row is available; callers must not use a
   * returned row after aborting their navigation operation.
   *
   * @param target Complete snapshot file, bay, side, and line coordinates.
   * @param abortSignal Lifetime of the caller's current line preparation.
   */
  prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine>;
};

/**
 * Synchronizes reactive TextDiffGrid inputs into one exclusively owned DOM root.
 *
 * The Solid effect runs initially and whenever rows, labels, view, file name,
 * or fold policies change. It clears local expanded-fold state when any such
 * renderer input changes and replaces every child of its root atomically. One
 * delegated root listener handles line pins and comment triggers across all
 * explicit row replacements. The Snapshot review boundary renders at most one
 * persistent Comment input through `document.body`; renderer replacement
 * closes it when its trigger disappears. The listener and preparation
 * operation are removed on cleanup.
 */
function ImperativeDiffLines(props: {
  /**
   * Identifies the exact captured file pair for review and line-pin validation.
   * Both paths must match every target routed into this row root.
   */
  reviewFile: ReviewFilePair;
  /**
   * Supplies the file's stable coordinate in the mounted ChangeSet manifest.
   * It is written to real hunk anchors but does not select any hunk.
   */
  fileIndex: number;
  /**
   * Names the file represented by this renderer instance.
   * A changed name invalidates its local expanded-fold set with other inputs.
   */
  displayName: string;
  /**
   * Identifies the exact physical bay represented by this row root.
   * Review, hunk, and line identities written below must retain this key.
   */
  bayKey: string;
  /**
   * Contains validated backend rows in source order for this one bay.
   * The renderer treats the array as immutable and replaces all DOM on change.
   */
  rows: DiffRow[];
  /**
   * Contains backend fold candidates whose coordinates refer to `rows`.
   * The fold builder validates their ordering before imperative rendering.
   */
  foldHints: FoldHint[];
  /**
   * Provides the complete left-side label to split rows and fold bars.
   * Inline row content does not use this as identity.
   */
  leftLabel: string;
  /**
   * Provides the complete right-side label to split rows and fold bars.
   * Inline row content does not use this as identity.
   */
  rightLabel: string;
  /**
   * Selects the current Tab-wide split or inline text presentation.
   * A change rebuilds the complete row subtree and clears expanded folds.
   */
  viewMode: DiffViewMode;
  /**
   * Selects whether every valid hint begins as a folded range.
   * A change rebuilds rows and clears the renderer-local expansion set.
   */
  aggressiveFolds: boolean;
  /**
   * Supplies Snapshot-scoped parsing and updates for URL line identity.
   * This row root uses it only after validating the target belongs here.
   */
  linePins: LinePins;
}) {
  let root!: PreparableDiffLines;
  const review = useReview();
  const [rowRevision, setRowRevision] = createSignal(0);
  let reviewMarkersFailed = false;
  const reviewBinding: ReviewTextGridBinding = {
    snapshot_id: review.snapshotId,
    file: props.reviewFile,
    bay: { bay_key: props.bayKey },
  };
  const expandedFolds = new Set<number>();
  let previousDisplayName: string | undefined;
  let previousRows: DiffRow[] | undefined;
  let previousFoldHints: FoldHint[] | undefined;
  let previousLeftLabel: string | undefined;
  let previousRightLabel: string | undefined;
  let previousViewMode: DiffViewMode | undefined;
  let previousAggressiveFolds: boolean | undefined;

  /**
   * Reads the required marker discriminator from one rendered Comment control.
   *
   * Marker buttons are created only by this grid. A missing or unknown dataset
   * value therefore indicates corrupted renderer-owned DOM and throws rather
   * than being treated as an undecorated marker.
   */
  function reviewMarkerKind(trigger: HTMLButtonElement): ReviewMarkerKind {
    const markerKind = trigger.dataset.reviewMarkerKind;
    assert(
      markerKind === "new" ||
        markerKind === "draft" ||
        markerKind === "open" ||
        markerKind === "resolved" ||
        markerKind === "deleted",
      "Comment trigger has an invalid marker kind.",
    );
    return markerKind;
  }

  /**
   * Refreshes review decorations without making complete row rendering reactive.
   *
   * The child subscribes to review marker revisions and the row revision
   * published after imperative replacements. It either refreshes all mounted
   * lines or the exact changed marker keys. Any failure disables every marker
   * in this grid before reaching the nearest marker-only ErrorBoundary.
   */
  function ReviewMarkerRefresh(): JSX.Element {
    let appliedRowRevision = -1;
    // Keep marker buttons synchronized with review facts and imperative row
    // replacements for this child's mounted lifetime. The effect reads the
    // review revision, changed-key set, and rowRevision signal; Solid disposes
    // the subscription on unmount, while onCleanup removes marker behavior from
    // DOM that may outlive a failed refresh.
    createEffect(() => {
      review.markerRevision();
      const rows = rowRevision();
      try {
        if (rows !== appliedRowRevision) {
          // New or replaced rows carry no decoration yet: refresh them all.
          appliedRowRevision = rows;
          refreshReviewMarkers();
          return;
        }
        const changed = review.changedMarkerKeys();
        if (changed === null) {
          refreshReviewMarkers();
        } else if (changed.size > 0) {
          refreshChangedReviewMarkers(changed);
        }
      } catch (error) {
        reviewMarkersFailed = true;
        disableReviewMarkers();
        throw error;
      }
    });
    onCleanup(disableReviewMarkers);
    return <></>;
  }

  /**
   * Resolves the unique rendered row for one exact target inside this TextDiffGrid.
   *
   * The caller must supply this TextDiffGrid's typed file and nullable bay
   * identity. `null` means only that the exact side and line are not currently
   * rendered. A target from another bay, duplicate coordinate, or line
   * detached from a valid row is a structural contradiction and throws.
   *
   * # Returns
   *
   * - The unique rendered row containing the exact line-pin coordinate.
   * - `null`: The exact side and line are not currently rendered. Pin operations
   *   must avoid painting or scrolling to a substitute row.
   */
  function renderedRow(target: LinePinTarget): HTMLElement | null {
    assert(
      target.file.left_path === props.reviewFile.left_path &&
        target.file.right_path === props.reviewFile.right_path &&
        target.bay.bay_key === props.bayKey,
      "TextDiffGrid received a line target from another bay.",
    );
    const matchingLines = root.querySelectorAll<HTMLElement>(
      `.line-no[data-line-pin-side="${target.side}"][data-line-pin-line="${target.line}"]`,
    );
    assert(
      matchingLines.length <= 1,
      "TextDiffGrid contains duplicate line-pin coordinates.",
    );
    const lineNumber = matchingLines[0];
    if (lineNumber === undefined) {
      return null;
    }
    const row = lineNumber.closest<HTMLElement>(".diff-row");
    assert(
      row !== null && root.contains(row),
      "Pinnable line has no TextDiffGrid row.",
    );
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
    assert(
      side === "left" || side === "right",
      "Pinnable line has an invalid side identity.",
    );
    assert(
      line !== undefined && /^[1-9]\d*$/u.test(line),
      "Pinnable line has an invalid backend line identity.",
    );
    if (commentTrigger !== null) {
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
      review.activateTextCommentInput(
        reviewBinding,
        side,
        Number(line),
        { codeCell: commentCodeCell, trigger: commentTrigger },
        reviewMarkerKind(commentTrigger),
        event.shiftKey,
      );
      return;
    }
    const target: LinePinTarget = {
      file: props.reviewFile,
      bay: { bay_key: props.bayKey },
      side,
      line,
    };
    const row = expect(
      renderedRow(target),
      "Activated line disappeared from its TextDiffGrid.",
    );
    const changeSetRoot = expect(
      root.closest<HTMLElement>("[data-change-set-root]"),
      "TextDiffGrid requires its ChangeSet root.",
    );
    const paintedRows =
      changeSetRoot.querySelectorAll<HTMLElement>(".pinned-line");
    assert(
      paintedRows.length <= 1,
      "ChangeSet contains multiple painted line pins.",
    );
    paintedRows[0]?.classList.remove("pinned-line");
    if (props.linePins.toggleUrlState(target) === "pinned") {
      row.classList.add("pinned-line");
    }
  }

  /**
   * Restores decoration for an already-routed URL pin after row rendering.
   *
   * The caller must first verify that the valid URL target belongs to this
   * TextDiffGrid. This operation tolerates a currently absent row and never paints
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
   * Unfolds and returns one exact line from this complete immutable TextDiffGrid.
   *
   * FileCard has already expanded and materialized the owning FullFile. Missing
   * complete-file coordinates return `missing`; cancellation returns `stopped`;
   * duplicate or malformed renderer identity throws visibly.
   *
   * @param target Exact file, bay, side, and line coordinates to reveal.
   * @param abortSignal Lifetime of the FileCard navigation operation.
   */
  async function prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine> {
    assert(
      target.file.left_path === props.reviewFile.left_path &&
        target.file.right_path === props.reviewFile.right_path &&
        target.bay.bay_key === props.bayKey,
      "TextDiffGrid preparation received the wrong target.",
    );
    if (abortSignal.aborted || !root.isConnected) {
      return { state: "stopped" };
    }
    const existing = renderedRow(target);
    if (existing !== null) {
      return { state: "ready", row: existing };
    }
    const lineNumber = Number(target.line);
    const matchingRowIndexes = props.rows.flatMap((row, rowIndex) => {
      const candidate = target.side === "left" ? row.left_no : row.right_no;
      return candidate === lineNumber ? [rowIndex] : [];
    });
    if (matchingRowIndexes.length === 0) {
      return { state: "missing" };
    }
    assert(
      matchingRowIndexes.length === 1,
      "Backend rows contain duplicate line-pin coordinates.",
    );
    const targetRowIndex = expect(
      matchingRowIndexes[0],
      "Matched line row index disappeared.",
    );
    let expanded = false;
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
        if (!expandedFolds.has(candidate.startRow)) {
          expandedFolds.add(candidate.startRow);
          expanded = true;
        }
      }
      remaining.unshift(...candidate.foldedRows);
    }
    assert(
      expanded,
      "A known unrendered line must belong to a collapsed fold.",
    );
    render();
    // `render()` reopens the fold through a signal write, so the rows it
    // reveals are mounted by Solid's flush rather than by the call. The yield
    // lets that flush run before the reopened row is looked up.
    await Promise.resolve();
    if (abortSignal.aborted || !root.isConnected) {
      return { state: "stopped" };
    }
    const row = renderedRow(target);
    return row === null ? { state: "missing" } : { state: "ready", row };
  }

  /**
   * Applies the derived marker state to one rendered line-number host.
   *
   * It mutates only that host's trigger elements; it does not render rows or
   * change folds, line pins, or hunk selection.
   */
  function applyMarkerState(lineNumber: HTMLElement): void {
    /**
     * Applies one exact marker kind to an existing Comment trigger.
     *
     * The button retains its identity so an active anchored Comment input is not
     * disconnected. This operation updates only kind classes and the dataset;
     * labels, counts, warning state, and disabled state remain the caller's job.
     *
     * @param trigger Renderer-owned button whose identity must be preserved.
     * @param markerKind Current review-derived state for that trigger.
     */
    function decorateTriggerKind(
      trigger: HTMLButtonElement,
      markerKind: ReviewMarkerKind,
    ): void {
      trigger.dataset.reviewMarkerKind = markerKind;
      trigger.classList.toggle(
        "line-comment-trigger-commented",
        markerKind === "open" ||
          markerKind === "resolved" ||
          markerKind === "deleted",
      );
      trigger.classList.toggle(
        "line-comment-trigger-draft",
        markerKind === "draft",
      );
      trigger.classList.toggle(
        "line-comment-trigger-open",
        markerKind === "open",
      );
      trigger.classList.toggle(
        "line-comment-trigger-resolved",
        markerKind === "resolved",
      );
      trigger.classList.toggle(
        "line-comment-trigger-deleted",
        markerKind === "deleted",
      );
    }

    /**
     * Creates one Comment control for a marker represented on this exact line.
     *
     * The returned button has marker identity and stable child elements, but no
     * listener: the grid's delegated root listener handles activation. The
     * caller must finish its visible label, title, warning, and disabled state.
     *
     * @param markerKind Review-derived state initially applied to the button.
     * @param side Exact backend side exposed in its accessible label.
     * @param line Validated positive backend line identity as decimal text.
     */
    function createTrigger(
      markerKind: ReviewMarkerKind,
      side: Side,
      line: string,
    ): HTMLButtonElement {
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "line-comment-trigger";
      decorateTriggerKind(trigger, markerKind);
      const icon = document.createElement("span");
      icon.className = "line-comment-trigger-icon";
      icon.ariaHidden = "true";
      const label = document.createElement("span");
      label.className = "line-comment-trigger-label";
      trigger.append(icon, label);
      trigger.ariaLabel = `Review action on ${side === "left" ? "old" : "new"} line ${line}`;
      return trigger;
    }

    const host = lineNumber.querySelector(":scope > .line-comment-triggers");
    const side = lineNumber.dataset.linePinSide;
    const line = lineNumber.dataset.linePinLine;
    assert(
      host instanceof HTMLSpanElement &&
        (side === "left" || side === "right") &&
        line !== undefined &&
        /^[1-9]\d*$/u.test(line),
      "Rendered Comment marker must expose its exact line identity.",
    );
    const storedState = review.markerState(reviewBinding, side, Number(line));
    storedState.markers.forEach((marker, index) => {
      const current = host.children.item(index);
      let trigger: HTMLButtonElement;
      if (current instanceof HTMLButtonElement) {
        // A kind change re-decorates the existing button instead of replacing
        // it: the button may anchor the active Comment input, and replacing it
        // disconnects that anchor, which closes the input on the next
        // anchored-UI sweep (e.g. any FileCard header unmount while the file
        // lane is still loading).
        trigger = current;
        if (reviewMarkerKind(trigger) !== marker.kind) {
          decorateTriggerKind(trigger, marker.kind);
        }
      } else {
        trigger = createTrigger(marker.kind, side, line);
        if (current === null) {
          host.append(trigger);
        } else {
          current.replaceWith(trigger);
        }
      }
      const label = trigger.lastElementChild;
      assert(
        label instanceof HTMLSpanElement,
        "Rendered Comment trigger requires its visible label.",
      );
      let actionLabel: string;
      if (marker.kind === "draft") {
        actionLabel = "Draft";
      } else if (marker.kind === "new") {
        actionLabel = "Add comment";
      } else {
        actionLabel = `${marker.count} ${marker.kind === "resolved" ? "Resolved" : marker.kind === "deleted" ? "Deleted" : "Open"} Thread${marker.count === 1 ? "" : "s"}`;
      }
      const labelText =
        marker.kind === "open" ||
        marker.kind === "resolved" ||
        marker.kind === "deleted"
          ? String(marker.count)
          : actionLabel;
      if (label.textContent !== labelText) label.textContent = labelText;
      if (trigger.title !== actionLabel) trigger.title = actionLabel;
      const ariaLabel = `${actionLabel} on ${side === "left" ? "old" : "new"} line ${line}`;
      if (trigger.ariaLabel !== ariaLabel) trigger.ariaLabel = ariaLabel;
      const warning = "warning" in marker && marker.warning;
      if (
        trigger.classList.contains("line-comment-trigger-warning") !== warning
      ) {
        trigger.classList.toggle("line-comment-trigger-warning", warning);
      }
      if (trigger.disabled !== storedState.disabled) {
        trigger.disabled = storedState.disabled;
      }
    });
    while (host.children.length > storedState.markers.length) {
      host.lastElementChild?.remove();
    }
  }

  /**
   * Refreshes Comment-trigger labels and state on every rendered line.
   *
   * ReviewMarkerRefresh calls this when rows were replaced or no bounded
   * change set exists for the current marker revision.
   */
  function refreshReviewMarkers(): void {
    for (const lineNumber of root.querySelectorAll<HTMLElement>(
      ".line-no[data-line-pin-line]",
    )) {
      applyMarkerState(lineNumber);
    }
  }

  /**
   * Refreshes only the rendered hosts named by changed marker line keys.
   *
   * Keys encode grid identity, so entries for other grids are skipped, as are
   * lines this grid does not currently render (folded or absent). The caller
   * guarantees rows are unchanged since the last complete refresh, so every
   * untouched host already displays current state.
   */
  function refreshChangedReviewMarkers(keys: ReadonlySet<string>): void {
    for (const key of keys) {
      // The key's shape is declared once beside its encoder, so this reader
      // cannot drift from it: a changed encoding fails here as a parse error
      // rather than as a stale hand-written arity check.
      const [leftPath, rightPath, bayKey, side, line] =
        LineMarkerKeySchema.parse(JSON.parse(key));
      if (
        leftPath !== reviewBinding.file.left_path ||
        rightPath !== reviewBinding.file.right_path ||
        bayKey !== reviewBinding.bay.bay_key
      ) {
        continue;
      }
      const matchingLines = root.querySelectorAll<HTMLElement>(
        `.line-no[data-line-pin-side="${side}"][data-line-pin-line="${line}"]`,
      );
      assert(
        matchingLines.length <= 1,
        "TextDiffGrid contains duplicate line-pin coordinates.",
      );
      const lineNumber = matchingLines[0];
      if (lineNumber === undefined) {
        continue;
      }
      applyMarkerState(lineNumber);
    }
  }

  /**
   * Disables possibly partial review decoration while preserving every row.
   *
   * This is the local damage boundary for marker failures. It removes all
   * review-state classes and disables existing Comment triggers, but leaves
   * their stable DOM identities and line-pin behavior intact.
   */
  function disableReviewMarkers(): void {
    for (const trigger of root.querySelectorAll<HTMLButtonElement>(
      ".line-comment-trigger",
    )) {
      trigger.classList.remove(
        "line-comment-trigger-commented",
        "line-comment-trigger-draft",
        "line-comment-trigger-open",
        "line-comment-trigger-resolved",
        "line-comment-trigger-deleted",
        "line-comment-trigger-warning",
      );
      trigger.disabled = true;
    }
  }

  /**
   * Restores transient marker and URL state after a renderer-owned row change.
   *
   * The row renderer calls this only after a complete or folded-range DOM
   * replacement. It publishes only a row-DOM revision; the marker-local effect
   * reads review state without making the complete renderer reactive.
   */
  function afterRowsChanged(): void {
    setRowRevision((current) => current + 1);
    if (reviewMarkersFailed) {
      disableReviewMarkers();
    }
    const parsed = props.linePins.parseUrl();
    if (
      parsed.state === "valid" &&
      parsed.target.file.left_path === props.reviewFile.left_path &&
      parsed.target.file.right_path === props.reviewFile.right_path &&
      parsed.target.bay.bay_key === props.bayKey
    ) {
      restoreOldPin();
    }
  }

  /**
   * Rebuilds the complete owned row subtree from current reactive inputs.
   *
   * The function resets local fold expansion only when a rendering input
   * other than review markers changes, and atomically replaces every child
   * of `root`.
   */
  const render = () => {
    const inputChanged = [
      props.displayName !== previousDisplayName,
      props.rows !== previousRows,
      props.foldHints !== previousFoldHints,
      props.leftLabel !== previousLeftLabel,
      props.rightLabel !== previousRightLabel,
      props.viewMode !== previousViewMode,
      props.aggressiveFolds !== previousAggressiveFolds,
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
    }

    const rows = addFoldRows(
      props.rows,
      props.foldHints,
      props.aggressiveFolds,
    );
    const fragment =
      props.viewMode === "inline"
        ? renderInlineRowsDom(
            rows,
            expandedFolds,
            props.fileIndex,
            props.bayKey,
            0,
            review.closeAnchoredUi,
            afterRowsChanged,
          )
        : renderSplitRowsDom(
            rows,
            props.leftLabel,
            props.rightLabel,
            expandedFolds,
            props.fileIndex,
            props.bayKey,
            0,
            review.closeAnchoredUi,
            afterRowsChanged,
          );
    review.closeAnchoredUi(root);
    root.replaceChildren(fragment);
    afterRowsChanged();
  };

  // Rebuild the exclusively owned row subtree when the renderer inputs read by
  // render() change. The effect lives with this component, owns no asynchronous
  // work, and needs no cleanup because each run replaces the prior DOM after
  // closing anchored review UI; component disposal removes the root itself.
  createEffect(render);
  onMount(() => {
    root.prepareLine_impl = prepareLine_impl;
    root.addEventListener("click", handleLineActivation);
    onCleanup(() => {
      review.closeAnchoredUi(root);
      root.removeEventListener("click", handleLineActivation);
      Reflect.deleteProperty(root, "prepareLine_impl");
    });
  });

  return (
    <>
      <div ref={root} class="diff-lines" />
      <ErrorBoundary
        fallback={(error) => (
          <div class="review-marker-error" role="alert">
            Review markers unavailable: {presentError(error).message}
          </div>
        )}
      >
        <ReviewMarkerRefresh />
      </ErrorBoundary>
    </>
  );
}
